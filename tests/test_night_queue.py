import time
import threading
import json
import queue as thread_queue
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from analyzer.app import create_app
from analyzer.night_queue import AppServerRunner, NightQueue, is_usage_limit_error, quota_gate, quota_resume_at


def quota(used_five=20, used_week=30, status='ok'):
    return {'status': status, 'fetched_at': datetime.now(timezone.utc).isoformat(), 'limits': [
        {'limit_id': 'codex', 'primary': {'windowDurationMins': 300, 'usedPercent': used_five,
                                         'resetsAt': time.time() + 3600},
         'secondary': {'windowDurationMins': 10080, 'usedPercent': used_week,
                       'resetsAt': time.time() + 86400}}]}


class FakeRunner:
    def __init__(self):
        self.calls = []

    def run(self, item, started):
        self.calls.append((item['id'], item['model'], item['effort']))
        started('thread-' + str(item['id']), 'turn-' + str(item['id']))
        return 'completed', ''


def test_quota_gate_uses_remaining_quota_until_exhausted():
    assert quota_gate(quota())[0] == '可派发'
    assert quota_gate(quota(99.9, 99.9))[0] == '可派发'
    assert quota_gate(quota(100, 30))[0] == '等待5 小时额度重置'
    assert quota_gate(quota(20, 100))[0] == '等待周额度重置'
    assert quota_gate(quota(status='unavailable'))[0] == '额度读取失败'


def test_queue_dispatches_serially_with_each_task_model(fixture_store, tmp_path):
    store, _ = fixture_store
    runner = FakeRunner()
    queue = NightQueue(store, runner, lambda: [
        {'id': 'model-a', 'efforts': ['low'], 'default_effort': 'low'},
        {'id': 'model-b', 'efforts': ['high'], 'default_effort': 'high'}])
    store.refresh_quota = lambda: quota()
    a = queue.add({'title': 'A', 'prompt': 'First', 'cwd': str(tmp_path), 'model': 'model-a', 'effort': 'low'})
    b = queue.add({'title': 'B', 'prompt': 'Second', 'cwd': str(tmp_path), 'model': 'model-b', 'effort': 'high'})
    queue.update_config({'enabled': True})
    queue.tick()
    queue.active.join(2)
    assert runner.calls == [(a, 'model-a', 'low')]
    queue.tick()
    queue.active.join(2)
    assert runner.calls == [(a, 'model-a', 'low'), (b, 'model-b', 'high')]
    assert [x['status'] for x in queue.items()] == ['completed', 'completed']
    queue.tick()
    assert len(runner.calls) == 2


def test_queue_does_not_send_when_disabled_or_quota_low_and_survives_restart(fixture_store, tmp_path):
    store, _ = fixture_store
    runner = FakeRunner()
    queue = NightQueue(store, runner, lambda: [{'id': 'm', 'efforts': ['medium'], 'default_effort': 'medium'}])
    store.refresh_quota = lambda: quota(used_five=100)
    key = queue.add({'title': 'A', 'prompt': 'First', 'cwd': str(tmp_path), 'model': 'm', 'effort': 'medium'})
    queue.tick()
    assert not runner.calls
    queue.update_config({'enabled': True})
    queue.tick()
    assert not runner.calls and queue.items()[0]['status'] == 'pending'
    with store.connect() as con:
        con.execute("UPDATE night_queue_items SET status='claimed' WHERE id=?", (key,))
    recovered = NightQueue(store, runner, queue.model_provider)
    assert recovered.items()[0]['status'] == 'needs_attention'
    store.refresh_quota = lambda: quota()
    recovered.tick()
    assert not runner.calls


def test_transient_failure_retries_without_blocking_later_work(fixture_store, tmp_path):
    store, _ = fixture_store
    calls = []

    class FlakyRunner:
        def run(self, item, started):
            calls.append(item['title'])
            if item['title'] == 'first' and calls.count('first') == 1:
                raise TimeoutError('app-server 响应超时')
            started('thread-' + str(item['id']), 'turn-' + str(item['id']))
            return 'completed', ''

    queue = NightQueue(store, FlakyRunner(), lambda: [
        {'id': 'm', 'efforts': ['medium'], 'default_effort': 'medium'}])
    store.refresh_quota = lambda: quota()
    first = queue.add({'title': 'first', 'prompt': 'A', 'cwd': str(tmp_path), 'model': 'm', 'effort': 'medium'})
    queue.add({'title': 'second', 'prompt': 'B', 'cwd': str(tmp_path), 'model': 'm', 'effort': 'medium'})
    queue.update_config({'enabled': True})
    queue.tick()
    queue.active.join(2)
    assert queue.items()[0]['status'] == 'pending'
    assert queue.items()[0]['retry_count'] == 1
    assert queue.items()[0]['resume_after'] > time.time()
    queue.tick()
    queue.active.join(2)
    assert calls == ['first', 'second']
    with store.connect() as con:
        con.execute('UPDATE night_queue_items SET resume_after=? WHERE id=?', (time.time() - 1, first))
    queue.tick()
    queue.active.join(2)
    assert calls == ['first', 'second', 'first']
    assert queue.items()[0]['status'] == 'completed'


def test_attention_item_does_not_block_other_pending_items(fixture_store, tmp_path):
    store, _ = fixture_store
    calls = []

    class FailingRunner:
        def run(self, item, started):
            calls.append(item['title'])
            if item['title'] == 'first':
                raise RuntimeError('需要人工核查')
            started('thread-second', 'turn-second')
            return 'completed', ''

    queue = NightQueue(store, FailingRunner(), lambda: [
        {'id': 'm', 'efforts': ['medium'], 'default_effort': 'medium'}])
    store.refresh_quota = lambda: quota()
    queue.add({'title': 'first', 'prompt': 'A', 'cwd': str(tmp_path), 'model': 'm', 'effort': 'medium'})
    queue.add({'title': 'second', 'prompt': 'B', 'cwd': str(tmp_path), 'model': 'm', 'effort': 'medium'})
    queue.update_config({'enabled': True})
    queue.tick()
    queue.active.join(2)
    assert queue.items()[0]['status'] == 'needs_attention'
    queue.tick()
    queue.active.join(2)
    assert calls == ['first', 'second']
    assert queue.items()[1]['status'] == 'completed'


def test_deferred_followup_preserves_order_within_target_thread(fixture_store, tmp_path):
    store, _ = fixture_store
    runner = FakeRunner()
    thread_id = '11111111-1111-1111-1111-111111111111'
    queue = NightQueue(store, runner, lambda: [
        {'id': 'm', 'efforts': ['medium'], 'default_effort': 'medium'}])
    queue.thread_provider = lambda _: {'id': thread_id, 'cwd': str(tmp_path),
                                       'turn_id': 'original-turn', 'turn_status': 'completed',
                                       'archived': False, 'parent_id': None}
    store.refresh_quota = lambda: quota()
    first = queue.add({'title': 'first', 'prompt': 'A', 'cwd': str(tmp_path), 'model': 'm',
                       'effort': 'medium', 'thread_id': thread_id})
    queue.add({'title': 'second', 'prompt': 'B', 'cwd': str(tmp_path), 'model': 'm',
               'effort': 'medium', 'thread_id': thread_id})
    independent = queue.add({'title': 'independent', 'prompt': 'C', 'cwd': str(tmp_path),
                             'model': 'm', 'effort': 'medium'})
    with store.connect() as con:
        con.execute('UPDATE night_queue_items SET resume_after=? WHERE id=?', (time.time() + 300, first))
    queue.update_config({'enabled': True})
    queue.tick()
    queue.active.join(2)
    assert runner.calls == [(independent, 'm', 'medium')]


def test_manual_retry_requires_unchanged_followup_target(fixture_store, tmp_path):
    store, _ = fixture_store
    thread_id = '11111111-1111-1111-1111-111111111111'
    snapshot = {'id': thread_id, 'cwd': str(tmp_path), 'turn_id': 'original-turn',
                'turn_status': 'completed', 'archived': False, 'parent_id': None}
    queue = NightQueue(store, FakeRunner(), lambda: [])
    queue.thread_provider = lambda _: snapshot
    key = queue.add({'title': 'followup', 'prompt': 'Do work', 'cwd': str(tmp_path),
                     'model': 'm', 'effort': 'medium', 'thread_id': thread_id})
    with store.connect() as con:
        con.execute("UPDATE night_queue_items SET status='needs_attention' WHERE id=?", (key,))
    queue.thread_provider = lambda _: {**snapshot, 'turn_id': 'new-turn'}
    with pytest.raises(ValueError, match='避免重复发送'):
        queue.retry(key)
    assert queue.items()[0]['status'] == 'needs_attention'
    queue.thread_provider = lambda _: snapshot
    queue.retry(key)
    assert queue.items()[0]['status'] == 'pending'


def test_edit_can_convert_locked_followup_to_new_thread(fixture_store, tmp_path):
    store, _ = fixture_store
    thread_id = '11111111-1111-1111-1111-111111111111'
    queue = NightQueue(store, FakeRunner(), lambda: [])
    queue.thread_provider = lambda _: {'id': thread_id, 'cwd': str(tmp_path),
                                       'turn_id': 'original-turn', 'turn_status': 'completed',
                                       'archived': False, 'parent_id': None}
    item = {'title': 'followup', 'prompt': 'Do work', 'cwd': str(tmp_path),
            'model': 'm', 'effort': 'medium', 'thread_id': thread_id}
    key = queue.add(item)
    with store.connect() as con:
        con.execute("UPDATE night_queue_items SET retry_count=3,resume_after=?,message='writer busy' WHERE id=?",
                    (time.time() + 300, key))
    queue.update_item(key, {**item, 'thread_id': None})
    updated = queue.items()[0]
    assert (updated['source'], updated['thread_id'], updated['status']) == ('queue', None, 'pending')
    assert updated['retry_count'] == 0 and updated['resume_after'] is None and updated['message'] is None


def test_queue_api_crud_and_model_catalog(fixture_store, tmp_path):
    store, _ = fixture_store
    app = create_app(store, background=False)
    app.state.night_queue.model_provider = lambda: [{'id': 'm', 'name': 'Model M', 'efforts': ['medium'], 'default_effort': 'medium'}]
    with TestClient(app) as client:
        assert client.get('/api/night-queue/models').json()['items'][0]['id'] == 'm'
        body = {'title': 'A', 'prompt': 'Do work', 'cwd': str(tmp_path), 'model': 'm', 'effort': 'medium'}
        key = client.post('/api/night-queue/items', json=body).json()['id']
        assert client.get('/api/night-queue').json()['items'][0]['id'] == key
        assert client.put(f'/api/night-queue/items/{key}', json={**body, 'title': 'Edited'}).status_code == 200
        assert client.get('/api/night-queue').json()['items'][0]['title'] == 'Edited'
        assert client.put('/api/night-queue/config', json={'enabled': False}).status_code == 200
        continuous = client.put('/api/night-queue/config', json={'enabled': True}).json()
        assert continuous['enabled'] is True
        assert 'end_at' not in continuous and 'five_hour_reserve' not in continuous
        assert client.put('/api/night-queue/config', json={'end_at': None}).status_code == 422
        assert client.put('/api/night-queue/config', json={'weekly_reserve': 5}).status_code == 422
        assert client.delete(f'/api/night-queue/items/{key}').status_code == 204


def test_recent_queue_projects_use_latest_interaction_per_existing_directory(fixture_store, tmp_path):
    store, _ = fixture_store
    projects = [tmp_path / f'project-{number}' for number in range(7)]
    for project in projects:
        project.mkdir()
    tasks = [
        {'project': str(project), 'codex_project': '工作项目',
         'updated_at': f'2026-10-10T0{number}:00:00+00:00'}
        for number, project in enumerate(projects)
    ]
    tasks += [
        {'project': str(projects[0]), 'codex_project': '工作项目',
         'updated_at': '2026-10-10T12:00:00+00:00'},
        {'project': str(tmp_path / 'missing'), 'updated_at': '2026-10-10T13:00:00+00:00'},
        {'project': '未归属项目', 'updated_at': '2026-10-10T14:00:00+00:00'},
    ]
    with store.connect() as con:
        con.executemany('INSERT INTO tasks(id,data) VALUES (?,?)',
                        [(str(index), json.dumps(task)) for index, task in enumerate(tasks)])
    with TestClient(create_app(store, background=False)) as client:
        result = client.get('/api/night-queue/recent-projects')
    assert result.status_code == 200
    items = result.json()['items']
    assert [item['path'] for item in items] == [str(projects[index]) for index in (0, 6, 5, 4, 3, 2, 1)]
    assert items[0]['name'] == 'project-0'
    assert items[0]['codex_project'] == '工作项目'


def test_select_specific_thread_in_same_project(fixture_store, tmp_path):
    store, _ = fixture_store
    thread_ids = ['11111111-1111-1111-1111-111111111111',
                  '22222222-2222-2222-2222-222222222222']
    with store.connect() as con:
        con.executemany('INSERT INTO tasks(id,data) VALUES (?,?)', [
            (thread_id, json.dumps({'id': thread_id, 'project': str(tmp_path),
             'is_window_root': True, 'window_name': f'任务 {index}',
             'updated_at': f'2026-10-10T0{index}:00:00+00:00'}))
            for index, thread_id in enumerate(thread_ids)])
    app = create_app(store, background=False)
    app.state.night_queue.thread_provider = lambda thread_id: {
        'id': thread_id, 'cwd': str(tmp_path), 'turn_id': f'turn-{thread_id[:1]}',
        'turn_status': 'completed', 'archived': False, 'parent_id': None}
    app.state.night_queue.update_config({'default_model': 'm'})
    with TestClient(app) as client:
        listed = client.get('/api/night-queue/project-threads', params={'project': str(tmp_path)})
        assert [item['id'] for item in listed.json()['items']] == thread_ids[::-1]
        body = {'title': '后续', 'prompt': '在原任务里继续', 'cwd': str(tmp_path),
                'model': 'm', 'effort': 'medium', 'thread_id': thread_ids[1]}
        created = client.post('/api/night-queue/items', json=body)
        assert created.status_code == 201
        item = client.get('/api/night-queue').json()['items'][0]
        assert (item['source'], item['thread_id'], item['turn_id']) == (
            'followup', thread_ids[1], 'turn-2')
        other = client.post('/api/night-queue/items', json={**body, 'thread_id': thread_ids[0]})
        assert other.status_code == 201
        app.state.night_queue.thread_provider = lambda thread_id: {
            'id': thread_id, 'cwd': str(tmp_path / 'other'), 'turn_id': 'turn'}
        assert client.post('/api/night-queue/items', json=body).status_code == 409


def test_followup_runner_sends_prompt_to_selected_thread_and_waits_for_active_turn(monkeypatch, tmp_path):
    import analyzer.night_queue as module
    sent = []
    class Process:
        def terminate(self): pass
        def wait(self, timeout=None): pass
    class Reader:
        def __init__(self): self.values = thread_queue.Queue()
    reader = Reader()
    monkeypatch.setattr(module, '_start_server', lambda: (Process(), reader))
    monkeypatch.setattr(module, '_send', lambda _process, request: sent.append(request))
    monkeypatch.setattr(module, '_reply', lambda _reader, request_id, **_: {
        4: {'thread': {'status': {'type': 'idle'}}},
        2: {'thread': {'id': 'selected-thread'}},
        3: {'turn': {'id': 'new-turn'}},
    }[request_id])
    monkeypatch.setattr(module, '_latest_turn', lambda *_: {'id': 'old-turn', 'status': 'completed'})
    reader.values.put({'method': 'turn/completed', 'params': {'turn': {'id': 'new-turn', 'status': 'completed'}}})
    item = {'source': 'followup', 'status': 'pending', 'thread_id': 'selected-thread',
            'turn_id': 'old-turn', 'anchor_turn_id': 'old-turn', 'cwd': str(tmp_path),
            'model': 'm', 'effort': 'medium', 'prompt': '后续具体工作'}
    assert AppServerRunner().run(item, lambda *_: None) == ('completed', '')
    assert [request['method'] for request in sent if 'method' in request] == [
        'thread/read', 'thread/resume', 'turn/start']
    assert sent[-1]['params']['threadId'] == 'selected-thread'
    assert sent[-1]['params']['input'][0]['text'] == '后续具体工作'
    sent.clear()
    monkeypatch.setattr(module, '_latest_turn', lambda *_: {'id': 'old-turn', 'status': 'inProgress'})
    monkeypatch.setattr(module, '_reply', lambda _reader, request_id, **_: {
        4: {'thread': {'status': {'type': 'active'}}},
    }[request_id])
    assert AppServerRunner().run(item, lambda *_: None)[0] == 'deferred'
    assert not any(request.get('method') == 'turn/start' for request in sent)
    sent.clear()
    monkeypatch.setattr(module, '_latest_turn', lambda *_: {'id': 'old-turn', 'status': 'completed'})
    def busy_reply(_reader, request_id, **_):
        if request_id == 3:
            raise module.AppServerError({'message': 'thread selected-thread already has an active writer'})
        return {4: {'thread': {'status': {'type': 'idle'}}},
                2: {'thread': {'id': 'selected-thread'}}}[request_id]
    monkeypatch.setattr(module, '_reply', busy_reply)
    assert AppServerRunner().run(item, lambda *_: None)[0] == 'deferred'
    assert sent[-1]['method'] == 'turn/start'


def test_blank_queue_thread_retries_original_prompt_after_writer_conflict(monkeypatch, tmp_path):
    import analyzer.night_queue as module
    sent = []
    busy = [True]

    class Process:
        def terminate(self): pass
        def wait(self, timeout=None): pass

    class Reader:
        def __init__(self):
            self.values = thread_queue.Queue()
            self.values.put({'method': 'turn/completed', 'params': {
                'turn': {'id': 'new-turn', 'status': 'completed'}}})

    def reply(_reader, request_id, **_):
        if request_id == 3 and busy[0]:
            raise module.AppServerError({'message': 'thread new-thread already has an active writer'})
        return {4: {'thread': {'status': {'type': 'idle'}}},
                2: {'thread': {'id': 'new-thread'}},
                3: {'turn': {'id': 'new-turn'}}}[request_id]

    monkeypatch.setattr(module, '_start_server', lambda: (Process(), Reader()))
    monkeypatch.setattr(module, '_send', lambda _proc, request: sent.append(request))
    monkeypatch.setattr(module, '_reply', reply)
    monkeypatch.setattr(module, '_latest_turn', lambda *_: {})
    item = {'source': 'queue', 'status': 'pending', 'thread_id': 'new-thread', 'turn_id': None,
            'cwd': str(tmp_path), 'model': 'm', 'effort': 'medium', 'prompt': 'original prompt'}
    assert AppServerRunner().run(item, lambda *_: None)[0] == 'deferred'
    busy[0] = False
    assert AppServerRunner().run(item, lambda *_: None)[0] == 'completed'
    assert sent[-1]['params']['input'][0]['text'] == 'original prompt'


def test_followups_to_same_thread_rebase_after_previous_completion(fixture_store, tmp_path):
    store, _ = fixture_store
    class Runner:
        def __init__(self): self.seen = []
        def run(self, item, started):
            self.seen.append(item['turn_id'])
            started(item['thread_id'], f'new-turn-{len(self.seen)}')
            return 'completed', ''
    runner = Runner()
    queue = NightQueue(store, runner, lambda: [
        {'id': 'm', 'efforts': ['medium'], 'default_effort': 'medium'}],
        lambda thread_id: {'id': thread_id, 'cwd': str(tmp_path), 'turn_id': 'old-turn',
                           'turn_status': 'completed'})
    store.refresh_quota = lambda: quota()
    queue.update_config({'enabled': True})
    body = {'title': '后续', 'prompt': '继续', 'cwd': str(tmp_path), 'model': 'm',
            'effort': 'medium', 'thread_id': 'selected-thread'}
    queue.add(body)
    queue.add(body)
    queue.tick()
    first = queue.active
    first.join(2)
    queue.tick()
    second = queue.active
    second.join(2)
    assert runner.seen == ['old-turn', 'new-turn-1']


def test_service_restart_requires_supervisor_and_idle_queue(fixture_store, tmp_path, monkeypatch):
    store, _ = fixture_store
    app = create_app(store, background=False)
    scheduled = []
    class FakeTimer:
        def __init__(self, delay, callback):
            scheduled.append((delay, callback))
            self.daemon = False
        def start(self): pass
    monkeypatch.setattr('analyzer.app.threading.Timer', FakeTimer)
    with TestClient(app) as client:
        assert client.get('/api/service/status').json()['managed'] is False
        assert client.post('/api/service/restart').status_code == 409
        monkeypatch.setenv('CODEX_ANALYZER_MANAGED', '1')
        assert client.get('/api/service/status').json()['managed'] is True
        with store.connect() as con:
            con.execute('''INSERT INTO night_queue_items(position,title,prompt,cwd,model,effort,
                         status,created_at) VALUES (?,?,?,?,?,?,?,?)''',
                        (1, 'Active', 'work', str(tmp_path), 'm', 'medium', 'running',
                         datetime.now(timezone.utc).isoformat()))
        assert client.post('/api/service/restart').status_code == 409
        with store.connect() as con:
            con.execute("UPDATE night_queue_items SET status='completed'")
        assert client.post('/api/service/restart').status_code == 202
        assert len(scheduled) == 1
        assert app.state.night_queue.restart_requested is True


def test_queue_default_model_and_invalid_combination_pause(fixture_store, tmp_path):
    store, _ = fixture_store
    runner = FakeRunner()
    provider = lambda: [{'id': 'm', 'efforts': ['high'], 'default_effort': 'high'}]
    queue = NightQueue(store, runner, provider)
    store.refresh_quota = lambda: quota()
    queue.update_config({'enabled': True, 'default_model': 'm', 'default_effort': 'high'})
    key = queue.add({'title': 'A', 'prompt': 'First', 'cwd': str(tmp_path), 'model': '', 'effort': ''})
    queue.tick()
    queue.active.join(2)
    assert runner.calls == [(key, 'm', 'high')]
    second = queue.add({'title': 'B', 'prompt': 'Second', 'cwd': str(tmp_path), 'model': 'm', 'effort': 'ultra'})
    queue.tick()
    assert queue.items()[-1]['status'] == 'needs_attention'
    assert len(runner.calls) == 1
    queue.update_item(second, {'effort': 'high'})
    queue.tick()
    queue.active.join(2)
    assert runner.calls[-1] == (second, 'm', 'high')


def test_usage_limit_error_requires_structured_signal():
    assert is_usage_limit_error({'codexErrorInfo': 'UsageLimitExceeded'})
    assert is_usage_limit_error({'codexErrorInfo': 'usageLimitExceeded'})
    assert is_usage_limit_error({'codexErrorInfo': {'type': 'UsageLimitExceeded'}})
    assert not is_usage_limit_error({'message': '429 rate limit exceeded'})
    assert not is_usage_limit_error({'codexErrorInfo': {'type': 'HttpConnectionFailed', 'httpStatusCode': 429}})
    current = quota(100, 40)
    assert quota_resume_at({'status': 'unavailable', 'last_success': current}) == current['limits'][0]['primary']['resetsAt']


def test_existing_thread_waits_for_live_quota_then_resumes_before_pending(fixture_store, tmp_path):
    store, _ = fixture_store
    thread_id = '01a1246e-94b8-75b3-ac1d-285876ccf3aa'
    state = {'turn_id': 'turn-original', 'turn_status': 'inProgress', 'quota_exhausted': False}
    def read_thread(_thread_id):
        assert _thread_id == thread_id
        return {'id': thread_id, 'title': 'Existing work', 'cwd': str(tmp_path),
                'model': 'm', 'effort': 'medium', **state}

    class ResumeRunner:
        def __init__(self): self.calls = []
        def run(self, item, started):
            self.calls.append((item['source'], item.get('thread_id'), item.get('turn_id')))
            next_turn = f'turn-resumed-{len(self.calls)}'
            started(item.get('thread_id') or 'new-thread', next_turn)
            state.update(turn_id=next_turn, turn_status='completed', quota_exhausted=False)
            return 'completed', ''

    runner = ResumeRunner()
    queue = NightQueue(store, runner, lambda: [
        {'id': 'm', 'efforts': ['medium'], 'default_effort': 'medium'}], read_thread)
    key = queue.watch_thread(thread_id)
    blocked = queue.add({'title': 'Approval blocked', 'prompt': 'Do work',
                         'cwd': str(tmp_path), 'model': 'm', 'effort': 'medium'})
    with store.connect() as con:
        con.execute("UPDATE night_queue_items SET status='needs_attention' WHERE id=?", (blocked,))
    assert queue.items()[0]['status'] == 'watching'
    assert queue.items()[0]['prompt'] == ''
    try:
        queue.watch_thread(thread_id)
        assert False, 'duplicate watch was accepted'
    except ValueError:
        pass
    queue.update_config({'enabled': True})
    store.refresh_quota = lambda: quota(used_five=100)
    queue.tick()
    assert runner.calls == []
    state.update(turn_status='failed', quota_exhausted=True)
    queue.tick()
    assert queue.items()[0]['status'] == 'waiting_quota'
    assert runner.calls == []
    queue = NightQueue(store, runner, queue.model_provider, read_thread)
    assert queue.items()[0]['status'] == 'waiting_quota'
    old_reset = queue.items()[0]['resume_after']
    def changed_quota():
        value = quota(used_five=100)
        value['limits'][0]['primary']['resetsAt'] = time.time() + 7200
        return value
    store.refresh_quota = changed_quota
    queue.tick()
    assert queue.items()[0]['resume_after'] > old_reset
    assert runner.calls == []
    store.refresh_quota = lambda: quota(used_five=20)
    queue.tick()
    queue.active.join(2)
    assert runner.calls == [('existing', thread_id, 'turn-original')]
    assert queue.items()[0]['status'] == 'watching'
    assert queue.items()[0]['turn_id'] == 'turn-resumed-1'
    queue.tick()
    assert len(runner.calls) == 1
    state.update(turn_id='later-manual-turn', turn_status='failed', quota_exhausted=True)
    store.refresh_quota = lambda: quota(used_five=100)
    queue.tick()
    assert queue.items()[0]['status'] == 'waiting_quota'
    store.refresh_quota = lambda: quota(used_five=20)
    queue.tick()
    queue.active.join(2)
    assert runner.calls[-1] == ('existing', thread_id, 'later-manual-turn')
    assert queue.items()[0]['status'] == 'watching'
    queue.remove(key)


def test_watch_api_and_nonquota_failure_do_not_resume(fixture_store, tmp_path):
    store, _ = fixture_store
    app = create_app(store, background=False)
    thread_id = '01a1246e-94b8-75b3-ac1d-285876ccf3aa'
    app.state.night_queue.thread_provider = lambda _: {
        'id': thread_id, 'title': 'Failed work', 'cwd': str(tmp_path),
        'model': 'm', 'effort': 'low', 'turn_id': 'failed-turn',
        'turn_status': 'failed', 'quota_exhausted': False}
    with TestClient(app) as client:
        response = client.post('/api/night-queue/watch', json={'thread_id': thread_id})
        assert response.status_code == 201
        item = client.get('/api/night-queue').json()['items'][0]
        assert item['source'] == 'existing' and item['status'] == 'needs_attention'
        assert client.post('/api/night-queue/watch', json={'thread_id': thread_id}).status_code == 409
        assert client.delete(f"/api/night-queue/items/{item['id']}").status_code == 204


def test_exhausted_task_resumes_before_next_task_and_survives_restart(fixture_store, tmp_path):
    store, _ = fixture_store

    class InterruptedRunner:
        def __init__(self):
            self.calls = []

        def run(self, item, started):
            self.calls.append((item['id'], item.get('thread_id'), item['model'], item['effort']))
            started(item.get('thread_id') or f"thread-{item['id']}", f"turn-{len(self.calls)}")
            return ('quota_exhausted', '5 小时额度已用尽') if len(self.calls) == 1 else ('completed', '')

    runner = InterruptedRunner()
    queue = NightQueue(store, runner, lambda: [
        {'id': 'm', 'efforts': ['medium'], 'default_effort': 'medium'},
        {'id': 'other', 'efforts': ['low'], 'default_effort': 'low'}])
    snapshots = [quota(), quota(used_five=100)]
    store.refresh_quota = lambda: snapshots.pop(0) if snapshots else quota(used_five=100)
    queue.update_config({'default_model': 'm', 'default_effort': 'medium'})
    a = queue.add({'title': 'A', 'prompt': 'First', 'cwd': str(tmp_path), 'model': '', 'effort': ''})
    b = queue.add({'title': 'B', 'prompt': 'Second', 'cwd': str(tmp_path), 'model': 'm', 'effort': 'medium'})
    queue.update_config({'enabled': True})
    queue.tick()
    queue.active.join(2)
    assert queue.items()[0]['status'] == 'waiting_quota'
    assert runner.calls == [(a, None, 'm', 'medium')]
    queue.tick()
    assert runner.calls == [(a, None, 'm', 'medium')]

    recovered = NightQueue(store, runner, queue.model_provider)
    assert recovered.items()[0]['status'] == 'waiting_quota'
    recovered.update_config({'default_model': 'other', 'default_effort': 'low'})
    store.refresh_quota = lambda: quota()
    with store.connect() as con:
        con.execute('UPDATE night_queue_items SET resume_after=? WHERE id=?', (time.time()-1, a))
    recovered.tick()
    recovered.active.join(2)
    assert runner.calls == [(a, None, 'm', 'medium'), (a, f'thread-{a}', 'm', 'medium')]
    assert [x['status'] for x in recovered.items()] == ['completed', 'pending']
    recovered.tick()
    recovered.active.join(2)
    assert runner.calls[-1] == (b, None, 'm', 'medium')


def test_app_server_runner_resumes_existing_thread_without_replaying_prompt(monkeypatch):
    import queue as queues
    import analyzer.night_queue as module

    class Process:
        def terminate(self): pass
        def wait(self, timeout): pass
        def kill(self): pass

    class Reader:
        def __init__(self):
            self.values = queues.Queue()
            for message in [
                {'id': 4, 'result': {'thread': {'id': 'thread-1', 'status': {'type': 'idle'},
                                                'turns': []}}},
                {'id': 5, 'result': {'data': [{'id': 'old-turn', 'status': 'failed',
                                              'error': {'codexErrorInfo': 'usageLimitExceeded'}}]}},
                {'id': 2, 'result': {'thread': {'id': 'thread-1'}}},
                {'id': 3, 'result': {'turn': {'id': 'new-turn'}}},
                {'method': 'turn/completed', 'params': {'turn': {'id': 'new-turn', 'status': 'completed'}}},
            ]:
                self.values.put(message)

    sent = []
    monkeypatch.setattr(module, '_start_server', lambda: (Process(), Reader()))
    monkeypatch.setattr(module, '_send', lambda _proc, value: sent.append(value))
    updates = []
    status, _ = AppServerRunner().run({'thread_id': 'thread-1', 'turn_id': 'old-turn',
                                       'model': 'm', 'effort': 'medium', 'cwd': '/project',
                                       'prompt': 'original prompt', 'source': 'auto'},
                                      lambda *args: updates.append(args))
    assert status == 'completed'
    assert [x['method'] for x in sent] == ['thread/read', 'thread/turns/list', 'thread/resume', 'turn/start']
    assert sent[0]['params']['includeTurns'] is False
    assert sent[1]['params']['itemsView'] == 'notLoaded'
    assert sent[2]['params']['excludeTurns'] is True
    assert sent[-1]['params']['threadId'] == 'thread-1'
    assert sent[-1]['params']['input'][0]['text'] != 'original prompt'
    assert updates[-1] == ('thread-1', 'new-turn')


def test_runner_recognizes_usage_limit_from_error_event(monkeypatch):
    import queue as queues
    import analyzer.night_queue as module

    class Process:
        def terminate(self): pass
        def wait(self, timeout): pass
        def kill(self): pass

    class Reader:
        def __init__(self):
            self.values = queues.Queue()
            for value in [
                {'id': 2, 'result': {'thread': {'id': 't'}}},
                {'id': 3, 'result': {'turn': {'id': 'r'}}},
                {'method': 'error', 'params': {'turnId': 'r', 'error': {
                    'message': 'limit reached', 'codexErrorInfo': 'usageLimitExceeded'}}},
                {'method': 'turn/completed', 'params': {'turn': {'id': 'r', 'status': 'failed'}}},
            ]:
                self.values.put(value)

    monkeypatch.setattr(module, '_start_server', lambda: (Process(), Reader()))
    monkeypatch.setattr(module, '_send', lambda *_: None)
    result = AppServerRunner().run({'model': 'm', 'effort': 'medium', 'cwd': '/project',
                                    'prompt': 'work'}, lambda *_: None)
    assert result[0] == 'quota_exhausted'


def test_auto_discovers_all_exhausted_threads_and_resumes_before_new_work(fixture_store, tmp_path):
    store, _ = fixture_store
    ids = ['thread-a', 'thread-b']
    state = {thread_id: {'turn_id': 'failed-turn', 'turn_status': 'failed',
                         'quota_exhausted': True} for thread_id in ids}
    scans = []

    def discovery(**kwargs):
        scans.append(kwargs)
        return ([{'id': thread_id, 'title': thread_id, 'cwd': str(tmp_path),
                  'model': 'm', 'effort': 'medium', 'updated_at': 10 + index,
                  **state[thread_id]} for index, thread_id in enumerate(ids)
                 if thread_id not in kwargs['excluded'] and state[thread_id]['quota_exhausted']], 12)

    def read_thread(thread_id):
        return {'id': thread_id, 'title': thread_id, 'cwd': str(tmp_path),
                'model': 'm', 'effort': 'medium', **state[thread_id]}

    class ResumeRunner:
        def __init__(self): self.calls = []
        def run(self, item, started):
            self.calls.append((item['source'], item.get('thread_id')))
            thread_id = item.get('thread_id') or 'new-thread'
            started(thread_id, 'resumed-turn')
            if thread_id in state:
                state[thread_id] = {'turn_id': 'resumed-turn', 'turn_status': 'completed',
                                    'quota_exhausted': False}
            return 'completed', ''

    runner = ResumeRunner()
    models = lambda: [{'id': 'm', 'efforts': ['medium'], 'default_effort': 'medium'}]
    queue = NightQueue(store, runner, models, read_thread, discovery)
    queue.add({'title': 'New', 'prompt': 'new work', 'cwd': str(tmp_path),
               'model': 'm', 'effort': 'medium'})
    queue.update_config({'enabled': True})
    store.refresh_quota = lambda: quota(used_five=100)
    queue.tick()
    assert [item['status'] for item in queue.items()] == ['pending', 'waiting_quota', 'waiting_quota']
    assert runner.calls == []
    store.refresh_quota = lambda: quota()
    queue.tick(); queue.active.join(2)
    queue.tick(); queue.active.join(2)
    assert runner.calls == [('auto', 'thread-a'), ('auto', 'thread-b')]
    queue.tick(); queue.active.join(2)
    assert runner.calls[-1] == ('queue', None)
    assert scans[0]['since'] is None and scans[1]['since'] == 12
    assert len(queue.items()) == 3


def test_auto_discovery_is_idempotent_and_deleted_turn_stays_ignored(fixture_store, tmp_path):
    store, _ = fixture_store
    turn = {'id': 'thread-a', 'title': 'Old task', 'cwd': str(tmp_path),
            'model': 'm', 'effort': 'medium', 'turn_id': 'failed-turn',
            'turn_status': 'failed', 'quota_exhausted': True, 'updated_at': 10}
    def discovery(**kwargs): return ([turn.copy()], 10)
    runner = FakeRunner()
    queue = NightQueue(store, runner, lambda: [], discovery_provider=discovery)
    queue.update_config({'enabled': True})
    store.refresh_quota = lambda: quota(used_five=100)
    queue.tick(); queue.tick()
    assert len(queue.items()) == 1
    key = queue.items()[0]['id']
    queue.remove(key)
    queue.tick()
    assert queue.items() == []
    restarted = NightQueue(store, runner, lambda: [], discovery_provider=discovery)
    restarted.tick()
    assert restarted.items() == []
    turn['turn_id'] = 'new-failed-turn'
    restarted.tick()
    assert len(restarted.items()) == 1
    assert restarted.items()[0]['turn_id'] == 'new-failed-turn'


def test_discovery_failure_blocks_new_dispatch_and_reports_error(fixture_store, tmp_path):
    store, _ = fixture_store
    runner = FakeRunner()
    def broken(**_): raise RuntimeError('app-server unavailable')
    queue = NightQueue(store, runner, lambda: [], discovery_provider=broken)
    queue.add({'title': 'New', 'prompt': 'work', 'cwd': str(tmp_path),
               'model': 'm', 'effort': 'medium'})
    queue.update_config({'enabled': True})
    store.refresh_quota = lambda: quota()
    queue.tick()
    assert not runner.calls
    assert queue.items()[0]['status'] == 'pending'
    assert '自动发现失败' in queue.snapshot()['discovery']['error']


def test_old_reserve_and_auto_close_config_is_removed_on_restart(fixture_store, tmp_path):
    store, _ = fixture_store
    runner = FakeRunner()
    models = lambda: [{'id': 'm', 'efforts': ['medium'], 'default_effort': 'medium'}]
    NightQueue(store, runner, models)
    with store.connect() as con:
        con.execute('''UPDATE night_queue_config SET data=? WHERE id=1''',
                    ('{"enabled":true,"end_at":"2000-01-01T00:00:00+00:00",'
                     '"five_hour_reserve":15,"weekly_reserve":5,'
                     '"default_model":"m","default_effort":"medium"}',))
    recovered = NightQueue(store, runner, models)
    assert recovered.config() == {'enabled': True, 'default_model': 'm', 'default_effort': 'medium'}
    recovered.add({'title': 'A', 'prompt': 'work', 'cwd': str(tmp_path), 'model': 'm', 'effort': 'medium'})
    store.refresh_quota = lambda: quota(99.9, 99.9)
    recovered.tick(); recovered.active.join(2)
    assert len(runner.calls) == 1


def test_turning_queue_off_during_quota_read_prevents_new_dispatch(fixture_store, tmp_path):
    store, _ = fixture_store
    runner = FakeRunner()
    queue = NightQueue(store, runner, lambda: [{'id': 'm', 'efforts': ['medium'],
                                                'default_effort': 'medium'}])
    queue.add({'title': 'A', 'prompt': 'work', 'cwd': str(tmp_path), 'model': 'm', 'effort': 'medium'})
    queue.update_config({'enabled': True})
    def disable_during_read():
        queue.update_config({'enabled': False})
        return quota()
    store.refresh_quota = disable_during_read
    queue.tick()
    assert runner.calls == []
    assert queue.items()[0]['status'] == 'pending'


def test_turning_queue_off_lets_current_task_finish_but_stops_next(fixture_store, tmp_path):
    store, _ = fixture_store
    started_event, release = threading.Event(), threading.Event()
    class BlockingRunner:
        def __init__(self): self.calls = []
        def run(self, item, started):
            self.calls.append(item['id'])
            started('thread-' + str(item['id']), 'turn-' + str(item['id']))
            started_event.set()
            assert release.wait(2)
            return 'completed', ''
    runner = BlockingRunner()
    queue = NightQueue(store, runner, lambda: [{'id': 'm', 'efforts': ['medium'],
                                                'default_effort': 'medium'}])
    for name in ('A', 'B'):
        queue.add({'title': name, 'prompt': 'work', 'cwd': str(tmp_path),
                   'model': 'm', 'effort': 'medium'})
    queue.update_config({'enabled': True})
    store.refresh_quota = lambda: quota()
    queue.tick()
    assert started_event.wait(2)
    worker = queue.active
    queue.update_config({'enabled': False})
    release.set()
    worker.join(2)
    queue.tick()
    assert len(runner.calls) == 1
    assert [item['status'] for item in queue.items()] == ['completed', 'pending']


def test_exhausted_task_waits_even_when_reset_time_is_unknown(fixture_store, tmp_path):
    store, _ = fixture_store
    class ExhaustedRunner:
        def __init__(self): self.calls = 0
        def run(self, item, started):
            self.calls += 1
            started(item.get('thread_id') or 'original-thread', f'turn-{self.calls}')
            return ('quota_exhausted', 'limit') if self.calls == 1 else ('completed', '')
    runner = ExhaustedRunner()
    queue = NightQueue(store, runner, lambda: [{'id': 'm', 'efforts': ['medium'],
                                                'default_effort': 'medium'}])
    queue.add({'title': 'A', 'prompt': 'work', 'cwd': str(tmp_path), 'model': 'm', 'effort': 'medium'})
    queue.update_config({'enabled': True})
    answers = [quota(), {'status': 'unavailable'}]
    store.refresh_quota = lambda: answers.pop(0) if answers else quota(100)
    queue.tick(); queue.active.join(2)
    assert queue.items()[0]['status'] == 'waiting_quota'
    assert queue.items()[0]['resume_after'] is None
    queue.tick()
    assert runner.calls == 1
    store.refresh_quota = lambda: quota()
    queue.tick(); queue.active.join(2)
    assert runner.calls == 2


def test_auto_discovery_reuses_existing_queue_record_for_later_failed_turn(fixture_store, tmp_path):
    store, _ = fixture_store
    runner = FakeRunner()
    candidate = {'id': 'thread-existing', 'title': 'Original', 'cwd': str(tmp_path),
                 'model': 'm', 'effort': 'medium', 'turn_id': 'new-failed-turn',
                 'turn_status': 'failed', 'quota_exhausted': True, 'updated_at': 20}
    queue = NightQueue(store, runner, lambda: [], discovery_provider=lambda **_: ([candidate], 20))
    with store.connect() as con:
        con.execute('''INSERT INTO night_queue_items(position,title,prompt,cwd,model,effort,status,
                     thread_id,turn_id,source,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                    (1, 'Original', 'old prompt', str(tmp_path), 'm', 'medium', 'completed',
                     'thread-existing', 'old-completed-turn', 'queue', datetime.now(timezone.utc).isoformat()))
    queue.update_config({'enabled': True})
    store.refresh_quota = lambda: quota(used_five=100)
    queue.tick()
    assert len(queue.items()) == 1
    assert queue.items()[0]['status'] == 'waiting_quota'
    assert queue.items()[0]['turn_id'] == 'new-failed-turn'
