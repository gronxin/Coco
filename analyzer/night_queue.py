"""Local, quota-gated Codex task queue. Never reads authentication files."""
import json
import queue
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .quota import _JsonLineReader, _codex_command

SHANGHAI = ZoneInfo('Asia/Shanghai')
DEFAULTS = {'enabled': False, 'default_model': '', 'default_effort': ''}


def now():
    return datetime.now(timezone.utc).isoformat()


def _send(proc, value):
    proc.stdin.write((json.dumps(value, ensure_ascii=False) + '\n').encode())
    proc.stdin.flush()


class AppServerError(RuntimeError):
    def __init__(self, payload):
        self.payload = payload if isinstance(payload, dict) else {}
        super().__init__(str(self.payload.get('message') or 'Codex 请求失败')[:300])


def _start_server():
    proc = subprocess.Popen(_codex_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, bufsize=0)
    reader = _JsonLineReader(proc.stdout)
    try:
        _send(proc, {'id': 1, 'method': 'initialize', 'params': {
            'clientInfo': {'name': 'codex-token-analyzer-queue', 'title': 'Codex Token 任务队列', 'version': '1.0'}}})
        reply = reader.read(1, 10)
        if reply.get('error'):
            raise RuntimeError('Codex app-server 初始化失败')
        _send(proc, {'method': 'initialized', 'params': {}})
        return proc, reader
    except Exception:
        proc.kill()
        raise


def _reply(reader, request_id, timeout=15, events=None):
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Codex app-server 响应超时')
        try:
            reply = reader.values.get(timeout=remaining)
        except queue.Empty:
            raise TimeoutError('Codex app-server 响应超时')
        if reply is None:
            raise RuntimeError('Codex app-server 意外退出')
        if reply.get('id') == request_id:
            break
        if events is not None:
            events.append(reply)
    if reply.get('error'):
        raise AppServerError(reply['error'])
    return reply.get('result') or {}


def available_models():
    proc, reader = _start_server()
    try:
        models, cursor = [], None
        for index in range(10):
            params = {'limit': 100, 'includeHidden': False}
            if cursor:
                params['cursor'] = cursor
            request_id = 10 + index
            _send(proc, {'id': request_id, 'method': 'model/list', 'params': params})
            result = _reply(reader, request_id)
            models.extend({'id': row.get('model') or row.get('id'),
                           'name': row.get('displayName') or row.get('model') or row.get('id'),
                           'default_effort': row.get('defaultReasoningEffort'),
                           'efforts': [effort['reasoningEffort'] for effort in row.get('supportedReasoningEfforts') or []
                                       if effort.get('reasoningEffort')]}
                          for row in result.get('data') or [] if row.get('model') or row.get('id'))
            cursor = result.get('nextCursor')
            if not cursor:
                break
        return models
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()


def quota_gate(snapshot):
    if snapshot.get('status') != 'ok':
        return '额度读取失败', None
    try:
        fetched = datetime.fromisoformat(snapshot['fetched_at'].replace('Z', '+00:00'))
        if (datetime.now(timezone.utc) - fetched).total_seconds() > 120:
            return '额度数据已过期', None
    except (KeyError, ValueError, TypeError):
        return '额度时间未知', None
    limits = [x for x in snapshot.get('limits') or [] if x.get('limit_id') == 'codex']
    if len(limits) != 1:
        return 'Codex 额度归属未知', None
    windows = [w for name in ('primary', 'secondary') if isinstance((w := limits[0].get(name)), dict)]
    for duration, label in ((300, '5 小时'), (10080, '周')):
        match = next((w for w in windows if w.get('windowDurationMins') == duration), None)
        if not match or not isinstance(match.get('usedPercent'), (int, float)) or not isinstance(match.get('resetsAt'), (int, float)):
            return f'{label}额度数据缺失', None
        if match['resetsAt'] <= time.time():
            return f'{label}额度快照已过期', None
        if match['usedPercent'] >= 100:
            return f'等待{label}额度重置', match['resetsAt']
    return '可派发', None


def is_usage_limit_error(error):
    """Only the structured Codex usage-limit signal permits automatic continuation."""
    info = (error or {}).get('codexErrorInfo') if isinstance(error, dict) else None
    if isinstance(info, str):
        return info.lower() == 'usagelimitexceeded'
    if isinstance(info, dict):
        return str(info.get('type') or '').lower() == 'usagelimitexceeded' or any(
            str(key).lower() == 'usagelimitexceeded' for key in info)
    return False


def _latest_turn(proc, reader, thread_id, request_id):
    _send(proc, {'id': request_id, 'method': 'thread/turns/list', 'params': {
        'threadId': thread_id, 'limit': 1, 'sortDirection': 'desc', 'itemsView': 'notLoaded'}})
    return ((_reply(reader, request_id, timeout=15).get('data') or [{}])[0])


def read_thread_snapshot(thread_id):
    """Read only thread and latest-turn metadata; do not hydrate conversation items."""
    proc, reader = _start_server()
    try:
        _send(proc, {'id': 2, 'method': 'thread/read', 'params': {
            'threadId': thread_id, 'includeTurns': False}})
        thread = (_reply(reader, 2, timeout=15).get('thread') or {})
        last = _latest_turn(proc, reader, thread_id, 3)
        return {'id': thread.get('id'), 'title': thread.get('name') or '',
                'cwd': thread.get('cwd'), 'model': thread.get('model'),
                'effort': thread.get('reasoningEffort'),
                'archived': bool(thread.get('archived')), 'parent_id': thread.get('parentThreadId'),
                'turn_id': last.get('id'), 'turn_status': last.get('status'),
                'quota_exhausted': is_usage_limit_error(last.get('error'))}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()


def discover_quota_failed_threads(since=None, excluded=()):
    """Scan non-archived primary conversations using metadata-only turn pages."""
    proc, reader = _start_server()
    candidates = []
    latest_update = since or 0
    excluded = set(excluded)
    try:
        cursor = None
        request_id = 10
        for _ in range(1000):
            params = {'limit': 100, 'archived': False, 'sortKey': 'updated_at',
                      'sortDirection': 'desc'}
            if cursor:
                params['cursor'] = cursor
            _send(proc, {'id': request_id, 'method': 'thread/list', 'params': params})
            result = _reply(reader, request_id, timeout=30)
            request_id += 1
            past_cutoff = False
            for thread in result.get('data') or []:
                updated = thread.get('updatedAt') or 0
                latest_update = max(latest_update, updated)
                if since is not None and updated < since - 120:
                    past_cutoff = True
                    break
                thread_id = thread.get('id')
                if not thread_id or thread_id in excluded or thread.get('parentThreadId'):
                    continue
                turn = _latest_turn(proc, reader, thread_id, request_id)
                request_id += 1
                if turn.get('status') != 'failed' or not is_usage_limit_error(turn.get('error')):
                    continue
                candidates.append({'id': thread_id, 'title': thread.get('name') or '',
                                   'cwd': thread.get('cwd'), 'model': thread.get('model'),
                                   'effort': thread.get('reasoningEffort'),
                                   'turn_id': turn.get('id'), 'turn_status': 'failed',
                                   'quota_exhausted': True, 'updated_at': updated})
            cursor = result.get('nextCursor')
            if past_cutoff or not cursor:
                return candidates, latest_update
        raise RuntimeError('Codex 对话列表分页过多，自动发现已暂停')
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()


def quota_resume_at(snapshot):
    """Wait past the exhausted account window; never infer a reset from a failure message."""
    if snapshot.get('status') != 'ok':
        snapshot = snapshot.get('last_success') or {}
    for limit in snapshot.get('limits') or []:
        if limit.get('limit_id') != 'codex':
            continue
        windows = [limit.get(name) for name in ('primary', 'secondary')]
        windows = [w for w in windows if isinstance(w, dict)]
        five = next((w for w in windows if w.get('windowDurationMins') == 300), None)
        if not five or not isinstance(five.get('resetsAt'), (int, float)):
            return None
        return max([five['resetsAt']] + [w['resetsAt'] for w in windows
                    if w.get('windowDurationMins') == 10080 and
                    isinstance(w.get('usedPercent'), (int, float)) and w['usedPercent'] >= 100 and
                    isinstance(w.get('resetsAt'), (int, float))])
    return None


class AppServerRunner:
    def run(self, item, started):
        proc, reader = _start_server()
        try:
            early_events = []
            resuming = bool(item.get('thread_id'))
            followup = item.get('source') == 'followup' and (
                item.get('status') != 'waiting_quota' or item.get('turn_id') == item.get('anchor_turn_id'))
            if resuming:
                _send(proc, {'id': 4, 'method': 'thread/read', 'params': {
                    'threadId': item['thread_id'], 'includeTurns': False}})
                saved = (_reply(reader, 4, events=early_events).get('thread') or {})
                previous = _latest_turn(proc, reader, item['thread_id'], 5)
                if not previous.get('id') or previous['id'] != item.get('turn_id'):
                    return 'needs_attention', '原对话出现新的轮次或无法核对中断轮次，请人工核查'
                runtime = saved.get('status') or {}
                if followup and (previous.get('status') in ('inProgress', 'in_progress') or
                                 isinstance(runtime, dict) and runtime.get('type') == 'active'):
                    return 'deferred', '目标对话仍在执行，完成后再发送后续任务'
                if isinstance(runtime, dict) and runtime.get('type') == 'active':
                    return 'needs_attention', '原对话仍在执行，请人工核查'
                if followup and previous.get('status') == 'failed' and is_usage_limit_error(previous.get('error')):
                    return 'deferred', '目标对话额度耗尽，等待原轮次优先续接'
                if followup and previous.get('status') != 'completed':
                    return 'needs_attention', '目标对话上次轮次未正常完成，请人工核查'
                if not followup and previous.get('status') == 'completed':
                    return 'completed', ''
                if not followup and previous.get('status') not in ('failed', 'interrupted'):
                    return 'needs_attention', '上次轮次状态不明确，请人工核查'
                if not followup and not is_usage_limit_error(previous.get('error')):
                    return 'needs_attention', '原对话没有明确的额度耗尽记录，已停止自动续接'
                _send(proc, {'id': 2, 'method': 'thread/resume', 'params': {
                    'threadId': item['thread_id'], 'cwd': item['cwd'], 'excludeTurns': True}})
            else:
                _send(proc, {'id': 2, 'method': 'thread/start', 'params': {
                    'model': item['model'], 'cwd': item['cwd'], 'serviceName': 'codex_token_analyzer_queue'}})
            thread_id = (_reply(reader, 2, events=early_events).get('thread') or {}).get('id')
            if not thread_id:
                raise RuntimeError('Codex 未返回任务 ID')
            if resuming and thread_id != item['thread_id']:
                raise RuntimeError('Codex 返回了不同的对话 ID，已停止自动续接')
            started(thread_id, None)
            prompt = ('请继续当前任务。先检查本对话中已经完成的步骤、工作目录中的实际文件状态和仍未完成的要求；'
                      '只完成剩余工作，不要重复已经完成的修改或外部操作。原任务要求仍以上文为准。') if resuming and not followup else item['prompt']
            _send(proc, {'id': 3, 'method': 'turn/start', 'params': {
                'threadId': thread_id, 'input': [{'type': 'text', 'text': prompt}],
                'cwd': item['cwd'], 'model': item['model'], 'effort': item['effort']}})
            try:
                result = _reply(reader, 3, events=early_events)
            except AppServerError as exc:
                if is_usage_limit_error(exc.payload) or is_usage_limit_error(exc.payload.get('data')):
                    return 'quota_exhausted', str(exc)
                raise
            turn_id = (result.get('turn') or {}).get('id')
            if not turn_id:
                raise RuntimeError('Codex 未返回轮次 ID')
            started(thread_id, turn_id)
            deadline = time.monotonic() + 4 * 3600
            last_turn_error = None
            while time.monotonic() < deadline:
                try:
                    message = early_events.pop(0) if early_events else reader.values.get(timeout=5)
                except queue.Empty:
                    continue
                if message is None:
                    raise RuntimeError('Codex app-server 意外退出')
                if message.get('method') == 'error':
                    params = message.get('params') or {}
                    if params.get('turnId') in (None, turn_id):
                        last_turn_error = params.get('error')
                if message.get('method') == 'turn/completed':
                    turn = (message.get('params') or {}).get('turn') or {}
                    if turn.get('id') == turn_id:
                        status = turn.get('status')
                        error = turn.get('error') or last_turn_error or {}
                        if status == 'completed':
                            return 'completed', ''
                        if status == 'failed' and is_usage_limit_error(error):
                            return 'quota_exhausted', str(error.get('message') or 'Codex 额度已用尽')[:300]
                        return 'needs_attention', str(error.get('message') or status or '')[:300]
                if 'id' in message and message.get('method'):
                    return 'needs_attention', 'Codex 请求人工审批或输入，请打开任务处理'
            return 'needs_attention', '任务运行超过 4 小时，请人工核查'
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()


class NightQueue:
    def __init__(self, store, runner=None, model_provider=None, thread_provider=None,
                 discovery_provider=None):
        self.store = store
        self.runner = runner or AppServerRunner()
        self.model_provider = model_provider or available_models
        self.thread_provider = thread_provider or read_thread_snapshot
        self.discovery_provider = discovery_provider or (discover_quota_failed_threads if runner is None else lambda **_: ([], 0))
        self._discovery_since = None
        self.last_discovery_at = None
        self.last_discovery_count = 0
        self.wake = threading.Event()
        self.active = None
        self.restart_requested = False
        self.last_error = None
        self._lock = threading.Lock()
        with store.connect() as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS night_queue_config(id INTEGER PRIMARY KEY CHECK(id=1),data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS night_queue_items(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, position INTEGER NOT NULL, title TEXT NOT NULL,
                    prompt TEXT NOT NULL, cwd TEXT NOT NULL, model TEXT NOT NULL, effort TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', thread_id TEXT, turn_id TEXT,
                    anchor_turn_id TEXT,
                    actual_model TEXT, actual_effort TEXT, resume_after REAL, resume_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, message TEXT,
                    source TEXT NOT NULL DEFAULT 'queue');
                CREATE INDEX IF NOT EXISTS night_queue_order ON night_queue_items(status,position,id);
                CREATE TABLE IF NOT EXISTS night_queue_ignored_threads(
                    thread_id TEXT PRIMARY KEY, turn_id TEXT NOT NULL);
            ''')
            columns = {row['name'] for row in con.execute('PRAGMA table_info(night_queue_items)')}
            for name in ('actual_model', 'actual_effort'):
                if name not in columns:
                    con.execute(f'ALTER TABLE night_queue_items ADD COLUMN {name} TEXT')
            if 'resume_after' not in columns:
                con.execute('ALTER TABLE night_queue_items ADD COLUMN resume_after REAL')
            if 'resume_count' not in columns:
                con.execute('ALTER TABLE night_queue_items ADD COLUMN resume_count INTEGER NOT NULL DEFAULT 0')
            if 'source' not in columns:
                con.execute("ALTER TABLE night_queue_items ADD COLUMN source TEXT NOT NULL DEFAULT 'queue'")
            if 'anchor_turn_id' not in columns:
                con.execute('ALTER TABLE night_queue_items ADD COLUMN anchor_turn_id TEXT')
            con.execute('DROP INDEX IF EXISTS night_queue_thread_id')
            con.execute("CREATE UNIQUE INDEX night_queue_thread_id ON night_queue_items(thread_id) WHERE thread_id IS NOT NULL AND source IN ('existing','auto','queue')")
            con.execute('INSERT OR IGNORE INTO night_queue_config(id,data) VALUES (1,?)', (json.dumps(DEFAULTS),))
            old_config = json.loads(con.execute('SELECT data FROM night_queue_config WHERE id=1').fetchone()[0])
            new_config = {key: old_config.get(key, value) for key, value in DEFAULTS.items()}
            if old_config != new_config:
                con.execute('UPDATE night_queue_config SET data=? WHERE id=1', (json.dumps(new_config),))
            # A process crash must never cause an uncertain task to be sent again.
            con.execute("UPDATE night_queue_items SET status='needs_attention',message='服务重启后需人工核对是否已执行' WHERE status IN ('claimed','running','resuming')")

    def config(self):
        with self.store.connect() as con:
            saved = json.loads(con.execute('SELECT data FROM night_queue_config WHERE id=1').fetchone()[0])
            return {key: saved.get(key, value) for key, value in DEFAULTS.items()}

    def update_config(self, changes):
        config = {**self.config(), **{key: value for key, value in changes.items() if key in DEFAULTS}}
        with self.store.connect() as con:
            con.execute('UPDATE night_queue_config SET data=? WHERE id=1', (json.dumps(config),))
        self.wake.set()
        return config

    def prepare_restart(self):
        """Block new claims while the service supervisor replaces this process."""
        with self._lock:
            if ((self.active and self.active.is_alive()) or
                    any(item['status'] in ('claimed', 'running', 'resuming') for item in self.items())):
                return False
            self.restart_requested = True
            return True

    def items(self):
        with self.store.connect() as con:
            return [dict(row) for row in con.execute('SELECT * FROM night_queue_items ORDER BY position,id')]

    def _followup_target(self, data):
        thread_id = data.get('thread_id')
        if not thread_id:
            return None
        snapshot = self.thread_provider(thread_id)
        if (snapshot.get('id') != thread_id or not snapshot.get('turn_id') or
                snapshot.get('archived') or snapshot.get('parent_id')):
            raise ValueError('目标任务窗口不存在、已归档或不是主任务')
        if not snapshot.get('cwd') or Path(snapshot['cwd']).resolve() != Path(data['cwd']).resolve():
            raise ValueError('目标任务的工作目录与所选项目不一致')
        if snapshot.get('turn_status') == 'failed' and not snapshot.get('quota_exhausted'):
            raise ValueError('目标任务上次轮次失败，需先人工核查')
        return snapshot

    def add(self, data):
        target = self._followup_target(data)
        with self.store.connect() as con:
            position = con.execute('SELECT COALESCE(MAX(position),0)+1 FROM night_queue_items').fetchone()[0]
            key = con.execute('''INSERT INTO night_queue_items
                (position,title,prompt,cwd,model,effort,thread_id,turn_id,anchor_turn_id,source,created_at,message)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                (position, data['title'], data['prompt'], data['cwd'], data['model'], data['effort'],
                 data.get('thread_id'), target.get('turn_id') if target else None,
                 target.get('turn_id') if target else None,
                 'followup' if target else 'queue', now(),
                 '等待向指定对话发送后续任务' if target else None)).lastrowid
        self.wake.set()
        return key

    def watch_thread(self, thread_id):
        """Explicitly enroll one existing Codex conversation without replaying its prompt."""
        snapshot = self.thread_provider(thread_id)
        if snapshot.get('id') != thread_id or not snapshot.get('turn_id'):
            raise ValueError('无法核对已有对话及其轮次')
        cwd = snapshot.get('cwd')
        model = snapshot.get('model')
        effort = snapshot.get('effort')
        if not cwd or not Path(cwd).is_dir() or not model or not effort:
            raise ValueError('已有对话缺少工作目录、模型或思考深度，不能安全续接')
        status = snapshot.get('turn_status')
        if status == 'failed' and snapshot.get('quota_exhausted'):
            item_status = 'waiting_quota'
            message = '原对话因额度耗尽中断；持续读取实时额度，恢复后优先续接'
        elif status == 'failed':
            item_status = 'needs_attention'
            message = '原对话失败，但不是明确的额度耗尽；请人工核查'
        else:
            item_status = 'watching'
            message = '监控原对话；仅在明确额度耗尽后自动续接'
        with self.store.connect() as con:
            if con.execute("SELECT 1 FROM night_queue_items WHERE thread_id=? AND source IN ('existing','auto','queue')",
                           (thread_id,)).fetchone():
                raise ValueError('该对话已在队列中')
            position = con.execute('SELECT COALESCE(MAX(position),0)+1 FROM night_queue_items').fetchone()[0]
            try:
                key = con.execute('''INSERT INTO night_queue_items
                    (position,title,prompt,cwd,model,effort,status,thread_id,turn_id,
                     actual_model,actual_effort,source,created_at,started_at,message)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (position, (snapshot.get('title') or f'已有任务 {thread_id[:8]}')[:200], '', cwd,
                     model, effort, item_status, thread_id, snapshot['turn_id'], model, effort,
                     'existing', now(), now(), message)).lastrowid
            except sqlite3.IntegrityError as exc:
                raise ValueError('该对话已在队列中') from exc
        self.wake.set()
        return key

    def _poll_watches(self, items):
        for item in items:
            if item['source'] not in ('existing', 'auto') or item['status'] != 'watching':
                continue
            snapshot = self.thread_provider(item['thread_id'])
            if snapshot.get('id') != item['thread_id'] or not snapshot.get('turn_id'):
                continue
            status = snapshot.get('turn_status')
            if status == 'failed' and snapshot.get('quota_exhausted'):
                new_status = 'waiting_quota'
                message = '原对话因额度耗尽中断；持续读取实时额度，恢复后优先续接'
            elif status == 'failed':
                new_status = 'needs_attention'
                message = '原对话失败，但不是明确的额度耗尽；请人工核查'
            else:
                new_status = 'watching'
                message = '监控原对话；仅在明确额度耗尽后自动续接'
            with self.store.connect() as con:
                con.execute('''UPDATE night_queue_items SET turn_id=?,status=?,message=?
                    WHERE id=? AND status='watching' ''',
                    (snapshot['turn_id'], new_status, message, item['id']))

    def _discover(self, items):
        """Register newly exhausted top-level threads before considering new queue work."""
        candidates, watermark = self.discovery_provider(since=self._discovery_since, excluded=())
        count = 0
        with self.store.connect() as con:
            for snapshot in sorted(candidates, key=lambda row: row.get('updated_at') or 0):
                thread_id, turn_id = snapshot.get('id'), snapshot.get('turn_id')
                if not thread_id or not turn_id:
                    continue
                if con.execute("""SELECT 1 FROM night_queue_items WHERE thread_id=?
                    AND source='followup' AND status IN ('claimed','running','resuming','waiting_quota')""",
                               (thread_id,)).fetchone():
                    continue
                existing = con.execute("""SELECT id,status,turn_id,message FROM night_queue_items
                    WHERE thread_id=? AND source IN ('existing','auto','queue') LIMIT 1""",
                                       (thread_id,)).fetchone()
                if existing:
                    if existing['turn_id'] != turn_id and existing['status'] not in ('claimed', 'running', 'resuming'):
                        con.execute('''UPDATE night_queue_items SET turn_id=?,status='waiting_quota',
                            message='检测到此对话的新轮次因额度耗尽；额度恢复后优先续接',resume_after=NULL
                            WHERE id=?''', (turn_id, existing['id']))
                        count += 1
                    elif (existing['status'] == 'needs_attention' and existing['turn_id'] == turn_id and
                          existing['message'] == '服务重启后需人工核对是否已执行'):
                        con.execute('''UPDATE night_queue_items SET status='waiting_quota',
                            message='已核实原轮次因额度耗尽；额度恢复后优先续接' WHERE id=?''',
                            (existing['id'],))
                        count += 1
                    continue
                ignored = con.execute('SELECT turn_id FROM night_queue_ignored_threads WHERE thread_id=?',
                                      (thread_id,)).fetchone()
                if ignored and ignored['turn_id'] == turn_id:
                    continue
                cwd, model, effort = snapshot.get('cwd'), snapshot.get('model'), snapshot.get('effort')
                safe = bool(cwd and Path(cwd).is_dir() and model and effort)
                status = 'waiting_quota' if safe else 'needs_attention'
                message = ('自动发现额度耗尽的原对话；额度恢复后优先续接' if safe else
                           '自动发现额度耗尽，但工作目录、模型或思考深度缺失；需人工核查')
                position = con.execute('SELECT COALESCE(MAX(position),0)+1 FROM night_queue_items').fetchone()[0]
                try:
                    con.execute('''INSERT INTO night_queue_items
                        (position,title,prompt,cwd,model,effort,status,thread_id,turn_id,
                         actual_model,actual_effort,source,created_at,message)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (position, (snapshot.get('title') or f'原任务 {thread_id[:8]}')[:200], '',
                         cwd or '', model or '', effort or '', status, thread_id, turn_id,
                         model or '', effort or '', 'auto', now(), message))
                    count += 1
                except sqlite3.IntegrityError:
                    pass
        self._discovery_since = watermark
        self.last_discovery_at = now()
        self.last_discovery_count = count

    def update_item(self, key, changes):
        allowed = {'title', 'prompt', 'cwd', 'model', 'effort', 'position'}
        target = self._followup_target(changes)
        with self.store.connect() as con:
            row = con.execute('SELECT status,message,thread_id FROM night_queue_items WHERE id=?', (key,)).fetchone()
            if not row:
                raise KeyError(key)
            repairable = row['status'] == 'needs_attention' and not row['thread_id'] and row['message'] == '模型或思考深度已不可用，请修改配置'
            if row['status'] != 'pending' and not repairable:
                raise ValueError('只能编辑待执行任务')
            for field, value in changes.items():
                if field in allowed:
                    con.execute(f'UPDATE night_queue_items SET {field}=? WHERE id=?', (value, key))
            con.execute('''UPDATE night_queue_items SET thread_id=?,turn_id=?,anchor_turn_id=?,source=? WHERE id=?''',
                        (changes.get('thread_id'), target.get('turn_id') if target else None,
                         target.get('turn_id') if target else None,
                         'followup' if target else 'queue', key))
            if repairable:
                con.execute("UPDATE night_queue_items SET status='pending',message=NULL WHERE id=?", (key,))
        self.wake.set()

    def move(self, key, position):
        with self.store.connect() as con:
            rows = con.execute("SELECT id,status FROM night_queue_items WHERE status='pending' ORDER BY position,id").fetchall()
            ids = [row['id'] for row in rows]
            if key not in ids:
                raise ValueError('只能移动待执行任务')
            ids.remove(key)
            ids.insert(max(0, min(position - 1, len(ids))), key)
            for index, item_id in enumerate(ids, 1):
                con.execute('UPDATE night_queue_items SET position=? WHERE id=?', (index, item_id))
        self.wake.set()

    def remove(self, key):
        with self.store.connect() as con:
            row = con.execute('SELECT status,thread_id,turn_id,source FROM night_queue_items WHERE id=?', (key,)).fetchone()
            if not row:
                raise KeyError(key)
            if row['status'] not in ('pending', 'watching', 'waiting_quota', 'completed', 'failed', 'needs_attention'):
                raise ValueError('运行中的任务不能删除')
            if row['source'] in ('existing', 'auto') and row['thread_id'] and row['turn_id']:
                con.execute('''INSERT INTO night_queue_ignored_threads(thread_id,turn_id) VALUES (?,?)
                    ON CONFLICT(thread_id) DO UPDATE SET turn_id=excluded.turn_id''',
                    (row['thread_id'], row['turn_id']))
            con.execute('DELETE FROM night_queue_items WHERE id=?', (key,))

    def snapshot(self):
        config = self.config()
        items = self.items()
        quota = self.store.quota_status()
        reason, reset = quota_gate(quota)
        if not config['enabled']:
            reason = '已关闭'
        elif any(x['status'] in ('claimed', 'running', 'resuming') for x in items):
            reason = '任务运行中'
        elif any(x['status'] == 'waiting_quota' for x in items):
            reason = '等待原任务额度重置并续接'
        elif any(x['status'] == 'needs_attention' for x in items):
            reason = '需要人工处理'
        elif any(x['status'] == 'watching' for x in items):
            reason = '监控已有对话'
        elif not any(x['status'] == 'pending' for x in items):
            reason = '队列为空'
        elif self.last_error:
            reason = '派发检查失败：' + self.last_error
        return {'config': config, 'items': items, 'state': reason, 'next_reset_at': reset,
                'quota_fetched_at': quota.get('fetched_at'), 'discovery': {
                    'last_scan_at': self.last_discovery_at, 'new_threads': self.last_discovery_count,
                    'error': self.last_error}}

    def _run(self, item):
        key = item['id']
        def started(thread_id, turn_id):
            with self.store.connect() as con:
                con.execute("UPDATE night_queue_items SET status='running',thread_id=?,turn_id=COALESCE(?,turn_id),actual_model=?,actual_effort=? WHERE id=?",
                            (thread_id, turn_id, item['model'], item['effort'], key))
        try:
            status, message = self.runner.run(item, started)
        except Exception as exc:
            status, message = 'needs_attention', str(exc)[:300]
        resume_after = None
        deferred = status == 'deferred'
        if deferred:
            status = 'waiting_quota' if item['status'] == 'waiting_quota' else 'pending'
        completed = status == 'completed'
        if status == 'quota_exhausted':
            try:
                resume_after = quota_resume_at(self.store.refresh_quota())
            except Exception:
                pass
            status = 'waiting_quota'
            if resume_after is not None:
                message = f'额度已用尽，等待重置后继续原任务；预计 {datetime.fromtimestamp(resume_after, SHANGHAI).strftime("%m-%d %H:%M")}'
            else:
                message = '额度已用尽；重置时间暂不可用，继续检查实时额度并优先续接原任务'
        elif status == 'completed' and item.get('source') in ('existing', 'auto'):
            status = 'watching'
            message = '本轮已完成；继续监控该对话之后的轮次'
        with self.store.connect() as con:
            con.execute('UPDATE night_queue_items SET status=?,finished_at=?,message=?,resume_after=? WHERE id=?',
                        (status, now() if status not in ('waiting_quota', 'watching', 'pending') else None,
                         message, resume_after, key))
            if completed and item.get('thread_id'):
                completed_turn = con.execute('SELECT turn_id FROM night_queue_items WHERE id=?', (key,)).fetchone()[0]
                baseline = item.get('anchor_turn_id') or item.get('turn_id')
                if completed_turn and baseline and completed_turn != baseline:
                    con.execute('''UPDATE night_queue_items SET turn_id=?,anchor_turn_id=?
                        WHERE thread_id=? AND source='followup' AND status='pending'
                        AND anchor_turn_id=? AND id!=?''',
                        (completed_turn, completed_turn, item['thread_id'], baseline, key))
        with self._lock:
            self.active = None
        if not deferred:
            self.wake.set()

    def tick(self):
        if self.restart_requested:
            return
        config = self.config()
        if not config['enabled']:
            return
        with self._lock:
            if self.restart_requested:
                return
            if self.active and self.active.is_alive():
                return
            items = self.items()
            try:
                self._discover(items)
                self.last_error = None
            except Exception as exc:
                self.last_error = '自动发现失败：' + str(exc)[:150]
                return
            items = self.items()
            self._poll_watches(items)
            items = self.items()
            if any(x['status'] in ('claimed', 'running', 'resuming') for x in items):
                return
            waiting = next((x for x in items if x['status'] == 'waiting_quota'), None)
            if not waiting and any(x['status'] == 'needs_attention' for x in items):
                return
            pending = waiting or next((x for x in items if x['status'] == 'pending'), None)
            if not pending:
                return
            quota = self.store.refresh_quota()
            reason, reset = quota_gate(quota)
            if waiting:
                with self.store.connect() as con:
                    con.execute("UPDATE night_queue_items SET resume_after=? WHERE id=? AND status='waiting_quota'",
                                (reset, waiting['id']))
            if reason != '可派发':
                return
            models = {x['id']: x for x in self.model_provider()}
            self.last_error = None
            model = pending['actual_model'] if waiting else (pending['model'] or config['default_model'])
            candidate = models.get(model)
            effort = (pending['actual_effort'] if waiting else
                      (pending['effort'] or (config['default_effort'] if not pending['model'] else '') or
                       (candidate or {}).get('default_effort')))
            if not candidate or not effort or effort not in candidate['efforts']:
                with self.store.connect() as con:
                    con.execute("UPDATE night_queue_items SET status='needs_attention',message='模型或思考深度已不可用，请修改配置' WHERE id=?", (pending['id'],))
                return
            if not Path(pending['cwd']).is_dir():
                with self.store.connect() as con:
                    con.execute("UPDATE night_queue_items SET status='needs_attention',message='工作目录不存在' WHERE id=?", (pending['id'],))
                return
            # The user may turn the queue off while discovery, quota, or model reads are in flight.
            if not self.config()['enabled']:
                return
            with self.store.connect() as con:
                if waiting:
                    updated = con.execute("UPDATE night_queue_items SET status='resuming',resume_count=resume_count+1,message='正在续接原任务' WHERE id=? AND status='waiting_quota'",
                                          (pending['id'],)).rowcount
                else:
                    updated = con.execute("UPDATE night_queue_items SET status='claimed',started_at=? WHERE id=? AND status='pending'",
                                          (now(), pending['id'])).rowcount
            if updated:
                item = {**pending, 'model': model, 'effort': effort}
                self.active = threading.Thread(target=self._run, args=(item,), daemon=True)
                self.active.start()

    def loop(self, stop):
        while not stop.is_set():
            try:
                self.tick()
            except Exception as exc:
                # Failed quota/model reads are retried; no task is claimed on failure.
                self.last_error = str(exc)[:180]
            self.wake.wait(60)
            self.wake.clear()
