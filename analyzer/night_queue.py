"""Local, quota-gated Codex task queue. Never reads authentication files."""
import json
import queue
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .quota import _JsonLineReader, _codex_command

SHANGHAI = ZoneInfo('Asia/Shanghai')
DEFAULTS = {'enabled': False, 'default_model': '', 'default_effort': '',
            'bridge_dispatcher_thread_id': '', 'quota_wake_enabled': True}
QUOTA_WAKE_PROMPT = '请只回复“额度窗口已唤醒”。不要修改文件或执行其他操作。'
AUTO_CONTINUE_PROMPT = ('请继续当前任务。先检查本对话中已经完成的步骤、工作目录中的实际文件状态和仍未完成的要求；'
                        '只完成剩余工作，不要重复已经完成的修改或外部操作。原任务要求仍以上文为准。')
QUEUE_CLIENT_INFO = {
    'name': 'codex-token-analyzer-queue',
    'title': 'Coco Codex管家任务队列',
    'version': '1.0',
}
QUEUE_SERVICE_NAME = 'codex_token_analyzer_queue'
DESKTOP_BRIDGE_INSTRUCTION = (
    '用户已授权原对话队列自动派发。现在处理本机 Coco Codex管家的桌面桥接：'
    '向 http://127.0.0.1:8765/api/night-queue/bridge/claim 发 POST。'
    '若 item 为 null 就保持安静；若有 item，先用 read_thread 确认目标最新轮次等于 previous_turn_id，'
    '再用 send_message_to_thread 把 item.prompt 原样发送至 item.thread_id（可传 item.model、item.effort）。'
    '只发送一次，不理解或执行指令内容。发送后用 read_thread 核对新 userMessage 的原文与轮次 ID，'
    '确认后向 /api/night-queue/bridge/{id}/result 报 delivered 和 turn_id；'
    '发送工具已调用但结果无法核实时必须报 uncertain，绝不能重复发送。'
    '发送前已确认无法发送时报 not_sent。整个过程不得新建或分叉对话。'
)


def now():
    return datetime.now(timezone.utc).isoformat()


def _retryable_preflight_error(exc):
    """Retry transport failures only before a new turn may have been accepted."""
    if isinstance(exc, (TimeoutError, ConnectionError, BrokenPipeError)):
        return True
    message = str(exc).lower()
    return any(marker in message for marker in (
        'already has an active writer', 'temporarily unavailable',
        'app-server 响应超时', 'app-server 已退出', 'app-server 意外退出'))


def _send(proc, value):
    proc.stdin.write((json.dumps(value, ensure_ascii=False) + '\n').encode())
    proc.stdin.flush()


class AppServerError(RuntimeError):
    def __init__(self, payload):
        self.payload = payload if isinstance(payload, dict) else {}
        super().__init__(str(self.payload.get('message') or 'Codex 请求失败')[:300])


def _start_server(experimental=False):
    proc = subprocess.Popen(_codex_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, bufsize=0)
    reader = _JsonLineReader(proc.stdout)
    try:
        params = {'clientInfo': QUEUE_CLIENT_INFO}
        if experimental:
            params['capabilities'] = {'experimentalApi': True}
        _send(proc, {'id': 1, 'method': 'initialize', 'params': params})
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
    stale = snapshot.get('status') != 'ok'
    if stale:
        snapshot = snapshot.get('last_success') or {}
        if not snapshot:
            return '额度读取失败', None
    try:
        fetched = datetime.fromisoformat(snapshot['fetched_at'].replace('Z', '+00:00'))
        if (datetime.now(timezone.utc) - fetched).total_seconds() > (900 if stale else 120):
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
        if stale and match['usedPercent'] >= 90:
            return f'{label}额度快照余量不足，等待实时数据', None
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


def read_thread_snapshot(thread_id, include_user=False):
    """Read latest-turn metadata, optionally including its user message for delivery checks."""
    proc, reader = _start_server()
    try:
        _send(proc, {'id': 2, 'method': 'thread/read', 'params': {
            'threadId': thread_id, 'includeTurns': include_user}})
        thread = (_reply(reader, 2, timeout=15).get('thread') or {})
        last = _latest_turn(proc, reader, thread_id, 3)
        result = {'id': thread.get('id'), 'title': thread.get('name') or '',
                'cwd': thread.get('cwd'), 'model': thread.get('model'),
                'effort': thread.get('reasoningEffort'),
                'archived': bool(thread.get('archived')), 'parent_id': thread.get('parentThreadId'),
                'turn_id': last.get('id'), 'turn_status': last.get('status'),
                'quota_exhausted': is_usage_limit_error(last.get('error'))}
        if include_user:
            matching = next((turn for turn in thread.get('turns') or []
                             if turn.get('id') == last.get('id')), {})
            message = next((entry for entry in matching.get('items') or []
                            if entry.get('type') == 'userMessage'), {})
            result['user_prompt'] = ''.join(part.get('text') or '' for part in message.get('content') or []
                                            if part.get('type') == 'text')
            result['native_send_marker'] = any(
                entry.get('type') == 'functionCallOutput' and
                entry.get('name') == 'send_message_to_thread'
                for entry in matching.get('items') or [])
            result['turn_started_at'] = matching.get('startedAt') or last.get('startedAt')
        return result
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()


def native_queued_submissions(thread_id):
    """Read Codex's pending inputs before clearing an obsolete auto continuation."""
    proc, reader = _start_server(experimental=True)
    try:
        _send(proc, {'id': 2, 'method': 'thread/queue/list', 'params': {'threadId': thread_id}})
        response = _reply(reader, 2)
        if response.get('nextCursor'):
            raise RuntimeError('原对话队列分页未读完，不能确认续接消息')
        return response.get('data') or []
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()


def delete_native_queued_submission(thread_id, submission_id):
    proc, reader = _start_server(experimental=True)
    try:
        _send(proc, {'id': 2, 'method': 'thread/queue/delete', 'params': {
            'threadId': thread_id, 'queuedSubmissionId': submission_id}})
        if not _reply(reader, 2).get('deleted'):
            raise RuntimeError('未能移除过期的原对话续接消息')
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


QUOTA_PROBE_INTERVAL = 300


def quota_dispatch_gate(snapshot, item=None):
    """After a reset boundary, let one real queued request start the new window."""
    reason, reset = quota_gate(snapshot)
    if reason == '可派发':
        return reason, reset
    boundary = quota_resume_at(snapshot)
    if boundary is None:
        return reason, reset
    source = snapshot if snapshot.get('status') == 'ok' else snapshot.get('last_success') or {}
    limits = [x for x in source.get('limits') or [] if x.get('limit_id') == 'codex']
    if len(limits) != 1:
        return reason, reset
    windows = [w for name in ('primary', 'secondary') if isinstance((w := limits[0].get(name)), dict)]
    if any(not any(w.get('windowDurationMins') == duration and
                   isinstance(w.get('usedPercent'), (int, float)) and
                   isinstance(w.get('resetsAt'), (int, float)) for w in windows)
           for duration in (300, 10080)):
        return reason, reset
    if boundary > time.time():
        return reason, boundary
    if item and item.get('resume_after') and item['resume_after'] > time.time():
        return reason, item['resume_after']
    if reason in ('额度数据已过期', '5 小时额度快照已过期', '周额度快照已过期',
                  '5 小时额度快照余量不足，等待实时数据', '周额度快照余量不足，等待实时数据',
                  '等待5 小时额度重置', '等待周额度重置'):
        return '重置后试发', None
    return reason, reset


class AppServerRunner:
    def run(self, item, started):
        proc, reader = _start_server()
        try:
            early_events = []
            resuming = bool(item.get('thread_id'))
            blank_queue_thread = resuming and item.get('source') == 'queue' and not item.get('turn_id')
            followup = item.get('source') == 'followup' and (
                item.get('status') != 'waiting_quota' or item.get('turn_id') == item.get('anchor_turn_id'))
            if resuming:
                _send(proc, {'id': 4, 'method': 'thread/read', 'params': {
                    'threadId': item['thread_id'], 'includeTurns': False}})
                saved = (_reply(reader, 4, events=early_events).get('thread') or {})
                previous = _latest_turn(proc, reader, item['thread_id'], 5)
                runtime = saved.get('status') or {}
                if blank_queue_thread:
                    if previous.get('id'):
                        return 'needs_attention', '新建对话已有轮次，请核查是否已发送任务指令'
                    if isinstance(runtime, dict) and runtime.get('type') == 'active':
                        return 'deferred', '新建对话仍被占用；空闲后自动重试'
                elif not previous.get('id'):
                    return 'needs_attention', '原对话无法核对最新轮次，请人工核查'
                elif previous['id'] != item.get('turn_id') and not followup:
                    return 'needs_attention', '原对话出现新的轮次或无法核对中断轮次，请人工核查'
                # A follow-up belongs after the conversation, rather than after
                # one immutable turn. A user may add turns while it waits in the
                # queue. The later status check still requires a completed turn.
                if not blank_queue_thread and followup and (previous.get('status') in ('inProgress', 'in_progress') or
                                 isinstance(runtime, dict) and runtime.get('type') == 'active'):
                    return 'deferred', '目标对话仍在执行，完成后再发送后续任务'
                if not blank_queue_thread and isinstance(runtime, dict) and runtime.get('type') == 'active':
                    return 'needs_attention', '原对话仍在执行，请人工核查'
                if not blank_queue_thread and followup and previous.get('status') == 'failed' and is_usage_limit_error(previous.get('error')):
                    return 'deferred', '目标对话额度耗尽，等待原轮次优先续接'
                if not blank_queue_thread and followup and previous.get('status') not in ('completed', 'interrupted'):
                    return 'needs_attention', '目标对话上次轮次未正常完成，请人工核查'
                if not blank_queue_thread and not followup and previous.get('status') == 'completed':
                    return 'completed', ''
                if not blank_queue_thread and not followup and previous.get('status') not in ('failed', 'interrupted'):
                    return 'needs_attention', '上次轮次状态不明确，请人工核查'
                if not blank_queue_thread and not followup and not is_usage_limit_error(previous.get('error')):
                    return 'needs_attention', '原对话没有明确的额度耗尽记录，已停止自动续接'
                _send(proc, {'id': 2, 'method': 'thread/resume', 'params': {
                    'threadId': item['thread_id'], 'cwd': item['cwd'], 'excludeTurns': True}})
                resumed = _reply(reader, 2, events=early_events)
                thread_id = (resumed.get('thread') or {}).get('id')
            else:
                _send(proc, {'id': 2, 'method': 'thread/start', 'params': {
                    'model': item['model'], 'cwd': item['cwd'], 'serviceName': QUEUE_SERVICE_NAME}})
                thread_id = (_reply(reader, 2, events=early_events).get('thread') or {}).get('id')
            if not thread_id:
                raise RuntimeError('Codex 未返回任务 ID')
            if resuming and thread_id != item['thread_id']:
                raise RuntimeError('Codex 返回了不同的对话 ID，已停止自动续接')
            started(thread_id, None)
            prompt = ('请继续当前任务。先检查本对话中已经完成的步骤、工作目录中的实际文件状态和仍未完成的要求；'
                      '只完成剩余工作，不要重复已经完成的修改或外部操作。原任务要求仍以上文为准。') if resuming and not followup and not blank_queue_thread else item['prompt']
            _send(proc, {'id': 3, 'method': 'turn/start', 'params': {
                'threadId': thread_id, 'input': [{'type': 'text', 'text': prompt}],
                'cwd': item['cwd'], 'model': item['model'], 'effort': item['effort']}})
            try:
                result = _reply(reader, 3, events=early_events)
            except AppServerError as exc:
                if is_usage_limit_error(exc.payload) or is_usage_limit_error(exc.payload.get('data')):
                    return 'quota_exhausted', str(exc)
                if 'already has an active writer' in str(exc).lower():
                    return 'deferred', '目标对话已有活动写入者；空闲后自动重试'
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
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    bridge_signal_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, message TEXT,
                    source TEXT NOT NULL DEFAULT 'queue');
                CREATE INDEX IF NOT EXISTS night_queue_order ON night_queue_items(status,position,id);
                CREATE TABLE IF NOT EXISTS night_queue_ignored_threads(
                    thread_id TEXT PRIMARY KEY, turn_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS quota_wake_state(
                    id INTEGER PRIMARY KEY CHECK(id=1), window_reset_at REAL,
                    status TEXT NOT NULL DEFAULT 'idle', thread_id TEXT, turn_id TEXT,
                    previous_turn_id TEXT, started_at REAL, next_attempt_at REAL,
                    message TEXT, observed_reset_at REAL, observed_used_percent REAL);
                CREATE TABLE IF NOT EXISTS quota_wake_threads(
                    thread_id TEXT PRIMARY KEY);
            ''')
            con.execute("INSERT OR IGNORE INTO quota_wake_state(id,status) VALUES (1,'idle')")
            wake_columns = {row['name'] for row in con.execute('PRAGMA table_info(quota_wake_state)')}
            for name in ('observed_reset_at', 'observed_used_percent'):
                if name not in wake_columns:
                    con.execute(f'ALTER TABLE quota_wake_state ADD COLUMN {name} REAL')
            columns = {row['name'] for row in con.execute('PRAGMA table_info(night_queue_items)')}
            for name in ('actual_model', 'actual_effort'):
                if name not in columns:
                    con.execute(f'ALTER TABLE night_queue_items ADD COLUMN {name} TEXT')
            if 'resume_after' not in columns:
                con.execute('ALTER TABLE night_queue_items ADD COLUMN resume_after REAL')
            if 'resume_count' not in columns:
                con.execute('ALTER TABLE night_queue_items ADD COLUMN resume_count INTEGER NOT NULL DEFAULT 0')
            if 'retry_count' not in columns:
                con.execute('ALTER TABLE night_queue_items ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0')
            if 'source' not in columns:
                con.execute("ALTER TABLE night_queue_items ADD COLUMN source TEXT NOT NULL DEFAULT 'queue'")
            if 'anchor_turn_id' not in columns:
                con.execute('ALTER TABLE night_queue_items ADD COLUMN anchor_turn_id TEXT')
            if 'bridge_signal_at' not in columns:
                con.execute('ALTER TABLE night_queue_items ADD COLUMN bridge_signal_at REAL')
            if 'bridge_signal_sent' not in columns:
                con.execute('ALTER TABLE night_queue_items ADD COLUMN bridge_signal_sent INTEGER NOT NULL DEFAULT 0')
            if 'bridge_signal_count' not in columns:
                con.execute('ALTER TABLE night_queue_items ADD COLUMN bridge_signal_count INTEGER NOT NULL DEFAULT 0')
            con.execute('DROP INDEX IF EXISTS night_queue_thread_id')
            con.execute("CREATE UNIQUE INDEX night_queue_thread_id ON night_queue_items(thread_id) WHERE thread_id IS NOT NULL AND source IN ('existing','auto','queue')")
            con.execute('INSERT OR IGNORE INTO night_queue_config(id,data) VALUES (1,?)', (json.dumps(DEFAULTS),))
            old_config = json.loads(con.execute('SELECT data FROM night_queue_config WHERE id=1').fetchone()[0])
            new_config = {key: old_config.get(key, value) for key, value in DEFAULTS.items()}
            if old_config != new_config:
                con.execute('UPDATE night_queue_config SET data=? WHERE id=1', (json.dumps(new_config),))
            # A process crash must never cause an uncertain task to be sent again.
            con.execute("UPDATE night_queue_items SET status='needs_attention',message='服务重启后需人工核对是否已执行' WHERE status IN ('claimed','running','resuming','bridge_claimed')")
            con.execute("""UPDATE night_queue_items SET status='bridge_pending',message='等待 Codex 桌面派发桥接'
                WHERE source='followup' AND status='needs_attention'
                AND message LIKE '原对话持续被 Codex 桌面进程持有%'""")

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
                    any(item['status'] in ('claimed', 'running', 'resuming', 'bridge_claimed', 'bridge_running') for item in self.items())):
                return False
            self.restart_requested = True
            return True

    def items(self):
        with self.store.connect() as con:
            return [dict(row) for row in con.execute('SELECT * FROM night_queue_items ORDER BY position,id')]

    def quota_wake_state(self):
        with self.store.connect() as con:
            return dict(con.execute('SELECT * FROM quota_wake_state WHERE id=1').fetchone())

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
            try:
                self._poll_watch_item(item)
            except Exception as exc:
                self.last_error = f'监控 #{item["id"]} 读取失败：{str(exc)[:130]}'

    def _poll_watch_item(self, item):
        snapshot = self.thread_provider(item['thread_id'])
        if snapshot.get('id') != item['thread_id'] or not snapshot.get('turn_id'):
            return
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

    def _reconcile_waiting(self, items):
        """An intervening user turn supersedes a failed auto-watch turn."""
        for item in items:
            if item['status'] != 'waiting_quota' or item['source'] not in ('auto', 'existing'):
                continue
            try:
                self._reconcile_waiting_item(item)
            except Exception as exc:
                self.last_error = f'待续接 #{item["id"]} 核对失败：{str(exc)[:130]}'

    def _reconcile_waiting_item(self, item):
        snapshot = self.thread_provider(item['thread_id'])
        if snapshot.get('id') != item['thread_id'] or not snapshot.get('turn_id'):
            return
        if snapshot['turn_id'] == item['turn_id']:
            return
        if snapshot.get('turn_status') == 'failed' and snapshot.get('quota_exhausted'):
            status, message = 'waiting_quota', '新轮次也因额度耗尽；恢复后续接最新轮次'
        elif snapshot.get('turn_status') == 'failed':
            status, message = 'needs_attention', '原对话新轮次失败，请核查'
        else:
            status, message = 'watching', '原对话已有新轮次；继续监控'
        with self.store.connect() as con:
            con.execute('''UPDATE night_queue_items SET status=?,turn_id=?,anchor_turn_id=?,
                message=?,resume_after=NULL WHERE id=? AND status='waiting_quota' ''',
                (status, snapshot['turn_id'], snapshot['turn_id'], message, item['id']))

    def _reconcile_restart(self, items):
        """Recover known turns after a service crash without resending prompts."""
        for item in items:
            if (item['status'] != 'needs_attention' or
                    item['message'] != '服务重启后需人工核对是否已执行' or
                    not item['thread_id'] or not item['turn_id']):
                continue
            try:
                snapshot = self.thread_provider(item['thread_id'])
            except Exception as exc:
                self.last_error = f'重启恢复 #{item["id"]} 核对失败：{str(exc)[:130]}'
                continue
            if snapshot.get('id') != item['thread_id'] or snapshot.get('turn_id') != item['turn_id']:
                continue
            if snapshot.get('turn_status') == 'completed':
                status = 'watching' if item['source'] in ('auto', 'existing') else 'completed'
                message = '已核实服务中断期间原轮次完成'
            elif snapshot.get('turn_status') == 'failed' and snapshot.get('quota_exhausted'):
                status, message = 'waiting_quota', '已核实原轮次额度耗尽；恢复后续接'
            elif snapshot.get('turn_status') in ('inProgress', 'in_progress'):
                status, message = 'bridge_running', '已核实原轮次仍在执行；继续跟踪'
            else:
                continue
            with self.store.connect() as con:
                con.execute('''UPDATE night_queue_items SET status=?,message=?,
                    finished_at=CASE WHEN ?='completed' THEN ? ELSE NULL END
                    WHERE id=? AND status='needs_attention' AND message='服务重启后需人工核对是否已执行' ''',
                    (status, message, status, now(), item['id']))

    def _discover(self, items):
        """Register newly exhausted top-level threads before considering new queue work."""
        with self.store.connect() as con:
            excluded = tuple(row['thread_id'] for row in con.execute('SELECT thread_id FROM quota_wake_threads'))
        candidates, watermark = self.discovery_provider(since=self._discovery_since, excluded=excluded)
        count = 0
        with self.store.connect() as con:
            for snapshot in sorted(candidates, key=lambda row: row.get('updated_at') or 0):
                thread_id, turn_id = snapshot.get('id'), snapshot.get('turn_id')
                if not thread_id or not turn_id:
                    continue
                if thread_id in excluded:
                    continue
                if con.execute("""SELECT 1 FROM night_queue_items WHERE thread_id=?
                    AND source='followup' AND status IN ('claimed','running','resuming','waiting_quota','bridge_pending','bridge_claimed','bridge_queued','bridge_running')""",
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
            con.execute('''UPDATE night_queue_items SET resume_after=NULL,retry_count=0,
                           message=NULL,started_at=NULL,finished_at=NULL WHERE id=?''', (key,))
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
            if row['status'] not in ('pending', 'watching', 'waiting_quota', 'completed', 'dispatched', 'failed', 'needs_attention', 'bridge_pending'):
                raise ValueError('运行中的任务不能删除')
            if row['source'] in ('existing', 'auto') and row['thread_id'] and row['turn_id']:
                con.execute('''INSERT INTO night_queue_ignored_threads(thread_id,turn_id) VALUES (?,?)
                    ON CONFLICT(thread_id) DO UPDATE SET turn_id=excluded.turn_id''',
                    (row['thread_id'], row['turn_id']))
            con.execute('DELETE FROM night_queue_items WHERE id=?', (key,))

    def retry(self, key):
        """Manually retry only when Codex proves the prompt has not started."""
        with self.store.connect() as con:
            row = con.execute('SELECT * FROM night_queue_items WHERE id=?', (key,)).fetchone()
        if not row:
            raise KeyError(key)
        item = dict(row)
        if item['status'] != 'needs_attention':
            raise ValueError('只能重试需要处理的任务')
        latest_turn_id = None
        if item['source'] == 'followup':
            if '不确定' in (item['message'] or '') or '核对派发结果' in (item['message'] or ''):
                raise ValueError('派发结果不确定，请先核对原对话，避免重复发送')
            snapshot = read_thread_snapshot(item['thread_id'], include_user=True)
            if (snapshot.get('id') != item['thread_id'] or snapshot.get('archived') or
                    snapshot.get('parent_id') or not snapshot.get('turn_id') or
                    snapshot.get('turn_status') not in ('completed', 'interrupted') or
                    not snapshot.get('cwd') or Path(snapshot['cwd']).resolve() != Path(item['cwd']).resolve()):
                raise ValueError('目标对话已有新轮次、仍在执行或已不可用，请先核查，避免重复发送')
            if snapshot.get('user_prompt', '').strip() == item['prompt'].strip():
                raise ValueError('原对话已有相同任务指令，请先核对，避免重复发送')
            latest_turn_id = snapshot['turn_id']
        elif item['source'] != 'queue' or item['thread_id']:
            raise ValueError('无法确认任务指令是否已经发送，请先核查原对话，避免重复执行')
        with self.store.connect() as con:
            updated = con.execute("""UPDATE night_queue_items
                SET status='pending',message=NULL,started_at=NULL,finished_at=NULL,
                    resume_after=NULL,retry_count=0,anchor_turn_id=COALESCE(?,anchor_turn_id),
                    turn_id=COALESCE(?,turn_id)
                WHERE id=? AND status='needs_attention'""", (latest_turn_id, latest_turn_id, key)).rowcount
        if not updated:
            raise ValueError('任务状态已变化，请刷新后再试')
        self.wake.set()

    def snapshot(self):
        config = self.config()
        items = self.items()
        quota = self.store.quota_status()
        reason, reset = quota_gate(quota)
        if not config['enabled']:
            reason = '已关闭'
        elif any(x['status'] in ('claimed', 'running', 'resuming', 'bridge_claimed', 'bridge_queued', 'bridge_running') for x in items):
            reason = '任务运行中'
        elif any(x['status'] == 'bridge_pending' for x in items):
            reason = '等待 Codex 桌面派发到原对话'
        elif any(x['status'] == 'waiting_quota' for x in items):
            reason = '等待原任务额度重置并续接'
        elif self.last_error and any(x['status'] == 'pending' for x in items):
            reason = '派发检查失败：' + self.last_error
        elif any(x['status'] == 'needs_attention' for x in items):
            reason = ('部分任务需要处理，其他任务继续派发' if any(x['status'] == 'pending' for x in items)
                      else '需要人工处理')
        elif any(x['status'] == 'pending' for x in items):
            reason = '待派发'
        elif any(x['status'] == 'watching' for x in items):
            reason = '监控已有对话'
        elif not any(x['status'] == 'pending' for x in items):
            reason = '队列为空'
        return {'config': config, 'items': items, 'state': reason, 'next_reset_at': reset,
                'quota_wake': self.quota_wake_state(),
                'quota_fetched_at': quota.get('fetched_at'), 'discovery': {
                    'last_scan_at': self.last_discovery_at, 'new_threads': self.last_discovery_count,
                    'error': self.last_error}}

    def bridge_claim(self):
        """Reserve one locked conversation for the app-owned dispatcher."""
        if not self.config()['enabled']:
            return None
        items = self.items()
        item = next((row for row in items if row['status'] == 'bridge_pending'), None)
        if item is None:
            return None
        snapshot = self.thread_provider(item['thread_id'])
        if (item['source'] in ('auto', 'existing') and
                snapshot.get('id') == item['thread_id'] and
                snapshot.get('turn_id') and
                (snapshot['turn_id'] != item['turn_id'] or
                 snapshot.get('turn_status') == 'completed')):
            with self.store.connect() as con:
                con.execute('''UPDATE night_queue_items SET status='watching',
                    turn_id=?,anchor_turn_id=?,message='原对话已有新轮次；继续监控',
                    bridge_signal_sent=0,bridge_signal_at=NULL,bridge_signal_count=0
                    WHERE id=? AND status='bridge_pending' ''',
                    (snapshot['turn_id'], snapshot['turn_id'], item['id']))
            self.wake.set()
            return None
        reason, retry_at = quota_dispatch_gate(self.store.refresh_quota(), item)
        if reason not in ('可派发', '重置后试发'):
            with self.store.connect() as con:
                con.execute('''UPDATE night_queue_items SET bridge_signal_sent=0,
                    resume_after=?,message=? WHERE id=? AND status='bridge_pending' ''',
                    (retry_at or time.time() + QUOTA_PROBE_INTERVAL,
                     f'{reason}；等待后再次尝试桌面派发', item['id']))
            return None
        valid = (snapshot.get('id') == item['thread_id'] and not snapshot.get('archived') and
                 not snapshot.get('parent_id') and snapshot.get('cwd') and
                 Path(snapshot['cwd']).resolve() == Path(item['cwd']).resolve())
        initial_followup = item['source'] == 'followup' and item['turn_id'] == item['anchor_turn_id']
        if initial_followup:
            if valid and snapshot.get('turn_status') in ('inProgress', 'in_progress'):
                return None
            valid = valid and snapshot.get('turn_id') and snapshot.get('turn_status') in ('completed', 'interrupted')
            prompt = item['prompt']
        else:
            valid = (valid and snapshot.get('turn_id') == item['turn_id'] and
                     snapshot.get('turn_status') in ('failed', 'interrupted') and
                     snapshot.get('quota_exhausted'))
            prompt = AUTO_CONTINUE_PROMPT
        if not valid:
            with self.store.connect() as con:
                con.execute("UPDATE night_queue_items SET status='needs_attention',message='目标对话状态已变化；为避免重复派发，请人工核查' WHERE id=? AND status='bridge_pending'", (item['id'],))
            return None
        with self.store.connect() as con:
            updated = con.execute("""UPDATE night_queue_items SET status='bridge_claimed',
                anchor_turn_id=?,started_at=?,resume_after=?,message='Codex 桌面正在派发到原对话'
                WHERE id=? AND status='bridge_pending'""",
                (snapshot['turn_id'], now(), time.time() + QUOTA_PROBE_INTERVAL, item['id'])).rowcount
        if not updated:
            return None
        return {'id': item['id'], 'thread_id': item['thread_id'], 'prompt': prompt,
                'model': item['actual_model'] or item['model'],
                'effort': item['actual_effort'] or item['effort'],
                'previous_turn_id': snapshot['turn_id']}

    def bridge_result(self, key, outcome, turn_id=None):
        with self.store.connect() as con:
            row = con.execute('SELECT * FROM night_queue_items WHERE id=?', (key,)).fetchone()
        if not row or row['status'] != 'bridge_claimed':
            raise ValueError('桥接任务未处于已认领状态')
        item = dict(row)
        if outcome == 'not_sent':
            status, message = 'bridge_pending', '桌面派发未开始；稍后自动重试'
        elif outcome == 'uncertain':
            status, message = 'needs_attention', '桌面派发结果不确定；请核对原对话，避免重复发送'
        elif outcome == 'delivered':
            if not turn_id or turn_id == item['anchor_turn_id']:
                raise ValueError('未核实到新的 Codex 轮次')
            snapshot = self.thread_provider(item['thread_id'])
            if snapshot.get('id') != item['thread_id'] or snapshot.get('turn_id') != turn_id:
                raise ValueError('目标对话轮次与派发回执不一致')
            status, message = 'bridge_running', '已发送到原对话，等待轮次完成'
        else:
            raise ValueError('未知的桥接结果')
        with self.store.connect() as con:
            updated = con.execute('''UPDATE night_queue_items SET status=?,message=?,turn_id=COALESCE(?,turn_id),
                bridge_signal_sent=CASE WHEN ?='not_sent' THEN 0 ELSE bridge_signal_sent END,
                bridge_signal_at=CASE WHEN ?='not_sent' THEN NULL ELSE bridge_signal_at END,
                resume_after=CASE WHEN ?='not_sent' THEN ? ELSE resume_after END
                WHERE id=? AND status='bridge_claimed' ''',
                (status, message, turn_id if outcome == 'delivered' else None,
                 outcome, outcome, outcome, time.time() + QUOTA_PROBE_INTERVAL, key)).rowcount
        if not updated:
            raise ValueError('桥接任务状态已变化')
        self.wake.set()

    def _poll_bridge_running(self, items):
        for item in items:
            if item['status'] != 'bridge_running':
                continue
            snapshot = self.thread_provider(item['thread_id'])
            if snapshot.get('turn_id') != item['turn_id']:
                status, message = 'needs_attention', '原对话出现其他新轮次；请核对桥接任务结果'
            elif snapshot.get('turn_status') == 'completed':
                if item['source'] in ('existing', 'auto'):
                    status, message = 'watching', '本轮已在原对话完成；继续监控后续轮次'
                else:
                    status, message = 'completed', '已在原对话完成'
            elif snapshot.get('turn_status') in ('failed', 'interrupted'):
                if snapshot.get('quota_exhausted'):
                    status, message = 'waiting_quota', '原对话额度耗尽，等待恢复后续接'
                else:
                    status, message = 'needs_attention', '原对话轮次未完成，请人工核查'
            else:
                continue
            with self.store.connect() as con:
                con.execute('''UPDATE night_queue_items SET status=?,message=?,finished_at=?,
                    resume_after=CASE WHEN ?='waiting_quota' THEN ? ELSE resume_after END
                    WHERE id=? AND status='bridge_running' ''',
                    (status, message, now() if status in ('completed', 'needs_attention') else None,
                     status, time.time() + QUOTA_PROBE_INTERVAL, item['id']))
                if status in ('completed', 'watching'):
                    con.execute('''UPDATE night_queue_items SET anchor_turn_id=?,turn_id=?
                        WHERE thread_id=? AND source='followup' AND status='pending'
                        AND anchor_turn_id=? AND id!=?''',
                        (item['turn_id'], item['turn_id'], item['thread_id'], item['anchor_turn_id'], item['id']))

    def _expire_bridge_claims(self, items):
        for item in items:
            if item['status'] != 'bridge_claimed' or not item['started_at']:
                continue
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(item['started_at'])).total_seconds()
            except ValueError:
                age = 601
            if age < 600:
                continue
            with self.store.connect() as con:
                con.execute("""UPDATE night_queue_items SET status='needs_attention',
                    message='桌面桥接超过 10 分钟未确认结果；请核对原对话后再处理，避免重复发送',finished_at=?
                    WHERE id=? AND status='bridge_claimed'""", (now(), item['id']))

    def _queue_on_dispatcher_thread(self, item):
        """The dispatcher cannot send to itself while its own turn is running."""
        snapshot = self.thread_provider(item['thread_id'])
        if snapshot.get('id') != item['thread_id'] or snapshot.get('archived') or snapshot.get('parent_id'):
            return
        if not snapshot.get('cwd') or Path(snapshot['cwd']).resolve() != Path(item['cwd']).resolve():
            return
        if (item['source'] in ('auto', 'existing') and snapshot.get('turn_id') and
                (snapshot['turn_id'] != item['turn_id'] or
                 snapshot.get('turn_status') == 'completed')):
            with self.store.connect() as con:
                con.execute('''UPDATE night_queue_items SET status='watching',turn_id=?,
                    anchor_turn_id=?,message='原对话已有新轮次；继续监控',
                    bridge_signal_sent=0,bridge_signal_at=NULL,bridge_signal_count=0,resume_after=NULL
                    WHERE id=? AND status='bridge_pending' ''',
                    (snapshot['turn_id'], snapshot['turn_id'], item['id']))
            self.wake.set()
            return
        if snapshot.get('turn_status') not in ('completed', 'interrupted'):
            return
        reason, retry_at = quota_dispatch_gate(self.store.refresh_quota(), item)
        if reason not in ('可派发', '重置后试发'):
            if retry_at:
                with self.store.connect() as con:
                    con.execute("UPDATE night_queue_items SET resume_after=? WHERE id=? AND status='bridge_pending'",
                                (retry_at, item['id']))
            return
        prompt = item['prompt'] if item['source'] == 'followup' else AUTO_CONTINUE_PROMPT
        with self.store.connect() as con:
            claimed = con.execute('''UPDATE night_queue_items SET status='bridge_claimed',
                anchor_turn_id=?,started_at=?,message='正在排入原对话桌面队列'
                WHERE id=? AND status='bridge_pending' ''',
                (snapshot['turn_id'], now(), item['id'])).rowcount
        if not claimed:
            return
        try:
            result = subprocess.run([_codex_command()[0], 'queue', '--thread', item['thread_id'],
                                     '--message', prompt], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                    timeout=20, check=False)
        except Exception as exc:
            error = str(exc)[:180]
            result = None
        else:
            error = result.stderr.decode(errors='replace')[-180:]
        with self.store.connect() as con:
            if result is not None and result.returncode == 0:
                con.execute('''UPDATE night_queue_items SET status='bridge_queued',
                    bridge_signal_sent=1,bridge_signal_at=?,message='已排入原对话；等待 Codex 桌面执行'
                    WHERE id=? AND status='bridge_claimed' ''', (time.time(), item['id']))
            else:
                con.execute('''UPDATE night_queue_items SET status='needs_attention',finished_at=?,
                    message=? WHERE id=? AND status='bridge_claimed' ''',
                    (now(), '无法确认是否已排入原对话；请核查后再重试：' + error, item['id']))
                return
        if snapshot.get('turn_status') == 'completed':
            self._open_dispatcher_thread(item['thread_id'], item['id'])

    def _open_dispatcher_thread(self, thread_id, item_id):
        link = 'codex://threads/' + quote(thread_id, safe='')
        opened = subprocess.run(['/usr/bin/open', '-g', link], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                timeout=10, check=False)
        if opened.returncode != 0:
            self.last_error = '无法打开 Codex 桌面对话：' + opened.stderr.decode(errors='replace')[-140:]
        else:
            with self.store.connect() as con:
                con.execute('''UPDATE night_queue_items SET bridge_signal_at=?
                    WHERE id=? AND status IN ('bridge_pending','bridge_queued') ''',
                    (time.time(), item_id))

    def _poll_bridge_queued(self, items):
        for item in items:
            if item['status'] != 'bridge_queued':
                continue
            snapshot = read_thread_snapshot(item['thread_id'], include_user=True)
            expected = item['prompt'] if item['source'] == 'followup' else AUTO_CONTINUE_PROMPT
            if (item['source'] in ('auto', 'existing') and
                    snapshot.get('turn_id') and snapshot['turn_id'] != item['anchor_turn_id'] and
                    snapshot.get('user_prompt', '').strip() != expected.strip()):
                try:
                    queued = native_queued_submissions(item['thread_id'])
                    for submission in queued:
                        inputs = submission.get('input') or []
                        if not all(part.get('type') == 'text' and isinstance(part.get('text'), str)
                                   for part in inputs):
                            raise RuntimeError('原对话存在无法识别的排队输入')
                        if ''.join(part['text'] for part in inputs).strip() == expected.strip():
                            delete_native_queued_submission(item['thread_id'], submission['id'])
                except Exception as exc:
                    with self.store.connect() as con:
                        con.execute('''UPDATE night_queue_items SET status='needs_attention',
                            message=? WHERE id=? AND status='bridge_queued' ''',
                            ('无法确认或撤销过期自动续接；请核对原对话：' + str(exc)[:140], item['id']))
                    continue
                with self.store.connect() as con:
                    con.execute('''UPDATE night_queue_items SET status='watching',turn_id=?,
                        anchor_turn_id=?,message='原对话已有新轮次；过期自动续接已取消',
                        bridge_signal_sent=0,bridge_signal_at=NULL,bridge_signal_count=0,resume_after=NULL
                        WHERE id=? AND status='bridge_queued' ''',
                        (snapshot.get('turn_id'), snapshot.get('turn_id'), item['id']))
                continue
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(item['started_at'])).total_seconds()
            except (TypeError, ValueError):
                age = 0
            if age >= 120 and snapshot.get('turn_id') == item['anchor_turn_id']:
                queued = native_queued_submissions(item['thread_id'])
                if not queued:
                    with self.store.connect() as con:
                        con.execute('''UPDATE night_queue_items SET status='needs_attention',finished_at=?,
                            message='桌面队列未发现已接受的派发消息；已释放队列阻塞，未自动重发以避免重复'
                            WHERE id=? AND status='bridge_queued' ''', (now(), item['id']))
                    continue
            if age >= 1800 and snapshot.get('user_prompt', '').strip() != expected.strip():
                with self.store.connect() as con:
                    con.execute('''UPDATE night_queue_items SET status='needs_attention',finished_at=?,
                        message='已排入原对话 30 分钟仍未确认执行；暂停同对话后续项以避免乱序，继续自动核查'
                        WHERE id=? AND status='bridge_queued' ''', (now(), item['id']))
                continue
            if snapshot.get('turn_id') == item['anchor_turn_id']:
                if not item['bridge_signal_at'] or time.time() - item['bridge_signal_at'] >= 300:
                    self._open_dispatcher_thread(item['thread_id'], item['id'])
                continue
            matched = snapshot.get('user_prompt', '').strip() == expected.strip()
            with self.store.connect() as con:
                if matched:
                    con.execute('''UPDATE night_queue_items SET status='bridge_running',turn_id=?,
                        message='已在原对话开始执行' WHERE id=? AND status='bridge_queued' ''',
                        (snapshot['turn_id'], item['id']))
                else:
                    # A queued control or user turn may precede this item. The
                    # prompt was already accepted by Codex; never enqueue it twice.
                    con.execute('''UPDATE night_queue_items SET anchor_turn_id=?,
                        message='原对话有其他轮次；继续等待已排入的任务'
                        WHERE id=? AND status='bridge_queued' ''',
                        (snapshot['turn_id'], item['id']))

    def _poll_uncertain_deliveries(self, items):
        for item in items:
            if item['status'] != 'needs_attention' or not item['bridge_signal_sent'] or not item['thread_id']:
                continue
            snapshot = read_thread_snapshot(item['thread_id'], include_user=True)
            exact_followup = (item['source'] == 'followup' and
                              snapshot.get('user_prompt', '').strip() == item['prompt'].strip())
            native_send = (snapshot.get('native_send_marker') and
                           snapshot.get('turn_id') != item['anchor_turn_id'] and
                           isinstance(snapshot.get('turn_started_at'), (int, float)) and
                           item.get('started_at') and
                           snapshot['turn_started_at'] >= datetime.fromisoformat(item['started_at']).timestamp())
            if not exact_followup and not native_send:
                continue
            with self.store.connect() as con:
                con.execute('''UPDATE night_queue_items SET status='bridge_running',turn_id=?,
                    finished_at=NULL,message='已在原对话确认桌面派发，恢复执行跟踪'
                    WHERE id=? AND status='needs_attention' ''',
                    (snapshot['turn_id'], item['id']))

    def _notify_bridge_pending(self, items):
        item = next((row for row in items if row['status'] == 'bridge_pending'), None)
        if item is None:
            return
        dispatcher = self.config().get('bridge_dispatcher_thread_id') or ''
        if not dispatcher:
            self.last_error = '未配置 Codex 桌面派发任务 ID'
            return
        if dispatcher == item['thread_id']:
            self._queue_on_dispatcher_thread(item)
            return
        stamp = time.time()
        if item.get('resume_after') and item['resume_after'] > stamp:
            return
        if item['bridge_signal_sent']:
            if item['bridge_signal_at'] and stamp - item['bridge_signal_at'] < 300:
                return
            if int(item.get('bridge_signal_count') or 0) >= 3:
                with self.store.connect() as con:
                    con.execute('''UPDATE night_queue_items SET status='needs_attention',
                        message='Codex 桌面连续三次未领取派发桥接；已释放队列阻塞，请核查桌面服务',
                        finished_at=? WHERE id=? AND status='bridge_pending' ''',
                        (now(), item['id']))
                self.wake.set()
                return
            # The earlier control message may have been consumed before the reset.
            # Requeue only when the original item is eligible for another attempt.
            reason, retry_at = quota_dispatch_gate(self.store.refresh_quota(), item)
            if reason not in ('可派发', '重置后试发'):
                if retry_at:
                    with self.store.connect() as con:
                        con.execute("UPDATE night_queue_items SET resume_after=? WHERE id=? AND status='bridge_pending'",
                                    (retry_at, item['id']))
                return
            with self.store.connect() as con:
                con.execute("UPDATE night_queue_items SET bridge_signal_sent=0 WHERE id=? AND status='bridge_pending'",
                            (item['id'],))
            item = {**item, 'bridge_signal_sent': 0}
        if not item['bridge_signal_sent']:
            command = [_codex_command()[0], 'queue', '--thread', dispatcher,
                       '--message', DESKTOP_BRIDGE_INSTRUCTION]
            completed = subprocess.run(command, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                       timeout=20, check=False)
            if completed.returncode != 0:
                self.last_error = '无法唤醒 Codex 桌面派发任务：' + completed.stderr.decode(errors='replace')[-140:]
                return
            with self.store.connect() as con:
                con.execute('''UPDATE night_queue_items SET bridge_signal_sent=1,bridge_signal_at=?,
                    bridge_signal_count=bridge_signal_count+1
                    WHERE id=? AND status='bridge_pending' ''', (stamp, item['id']))
        # Opening the existing dispatcher task loads its persisted queue in the
        # desktop app. -g asks macOS to keep the user's current app in front.
        dispatcher_state = self.thread_provider(dispatcher)
        if dispatcher_state.get('turn_status') not in ('inProgress', 'in_progress'):
            self._open_dispatcher_thread(dispatcher, item['id'])

    def _run(self, item):
        key = item['id']
        has_started = False
        def started(thread_id, turn_id):
            nonlocal has_started
            has_started = True
            with self.store.connect() as con:
                con.execute("""UPDATE night_queue_items SET status='running',thread_id=?,
                    turn_id=COALESCE(?,turn_id),
                    actual_model=?,actual_effort=? WHERE id=?""",
                    (thread_id, turn_id, item['model'], item['effort'], key))
        try:
            status, message = self.runner.run(item, started)
        except Exception as exc:
            status = 'deferred' if not has_started and _retryable_preflight_error(exc) else 'needs_attention'
            message = str(exc)[:300]
        resume_after = None
        deferred = status == 'deferred'
        retry_count = int(item.get('retry_count') or 0)
        if deferred:
            status = 'waiting_quota' if item['status'] == 'waiting_quota' else 'pending'
            retry_count += 1
            writer_locked = 'active writer' in message.lower()
            if writer_locked and item.get('thread_id'):
                status = 'bridge_pending'
                resume_after = None
                message = '桌面 Codex 持有原对话写入锁；等待桌面派发桥接'
            elif status == 'pending':
                delay = min(30 * 2 ** min(retry_count - 1, 4), 300)
                resume_after = time.time() + delay
                message = f'{message}；约 {delay} 秒后自动重试（第 {retry_count} 次）'[:300]
        completed = status == 'completed'
        if status == 'quota_exhausted':
            try:
                resume_after = quota_resume_at(self.store.refresh_quota())
            except Exception:
                pass
            if resume_after is None or resume_after <= time.time():
                resume_after = time.time() + QUOTA_PROBE_INTERVAL
            status = 'waiting_quota'
            message = f'额度未恢复；将在 {datetime.fromtimestamp(resume_after, SHANGHAI).strftime("%m-%d %H:%M")} 尝试续接原任务'
        elif status == 'completed' and item.get('source') in ('existing', 'auto'):
            status = 'watching'
            message = '本轮已完成；继续监控该对话之后的轮次'
        with self.store.connect() as con:
            con.execute('UPDATE night_queue_items SET status=?,finished_at=?,message=?,resume_after=?,retry_count=? WHERE id=?',
                        (status, now() if status not in ('waiting_quota', 'watching', 'pending', 'bridge_pending') else None,
                         message, resume_after, retry_count, key))
            if completed and item.get('thread_id'):
                current = con.execute('SELECT thread_id,turn_id FROM night_queue_items WHERE id=?', (key,)).fetchone()
                completed_thread, completed_turn = current['thread_id'], current['turn_id']
                baseline = item.get('anchor_turn_id') or item.get('turn_id')
                if completed_thread and completed_turn and baseline and completed_turn != baseline:
                    con.execute('''UPDATE night_queue_items SET thread_id=?,turn_id=?,anchor_turn_id=?
                        WHERE thread_id=? AND source='followup' AND status='pending'
                        AND anchor_turn_id=? AND id!=?''',
                        (completed_thread, completed_turn, completed_turn,
                         item['thread_id'], baseline, key))
        with self._lock:
            self.active = None
        if not deferred or status == 'bridge_pending':
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
            errors = []
            self.last_error = None
            try:
                self._discover(items)
            except Exception as exc:
                # Discovery is observational. A failed scan must not stop an
                # already queued task from using a newly available quota window.
                errors.append('自动发现失败：' + str(exc)[:150])
            items = self.items()
            for label, operation in (
                ('服务重启恢复', self._reconcile_restart),
                ('原对话监控', self._poll_watches),
                ('待续接轮次核对', self._reconcile_waiting),
                ('桥接执行核对', self._poll_bridge_running),
                ('桥接排队核对', self._poll_bridge_queued),
                ('不确定派发核对', self._poll_uncertain_deliveries),
                ('桥接超时核对', self._expire_bridge_claims),
            ):
                try:
                    operation(items)
                except Exception as exc:
                    errors.append(f'{label}失败：' + str(exc)[:130])
            try:
                self._notify_bridge_pending(self.items())
            except Exception as exc:
                errors.append('桌面桥接通知失败：' + str(exc)[:130])
            if self.last_error:
                errors.append(self.last_error)
            self.last_error = '；'.join(errors)[:300] if errors else None
            # Older queue items treated a transient app-server writer lock as a
            # permanent failure. This specific rejection means the turn was not
            # accepted, so it is safe to retry once the target thread is idle.
            with self.store.connect() as con:
                con.execute("""UPDATE night_queue_items
                    SET status='pending',message=NULL,started_at=NULL,finished_at=NULL
                    WHERE status='needs_attention' AND lower(message) LIKE '%already has an active writer%'""")
            items = self.items()
            if any(x['status'] in ('bridge_pending', 'bridge_claimed', 'bridge_queued', 'bridge_running') for x in items):
                return
            if any(x['status'] in ('claimed', 'running', 'resuming') for x in items):
                return
            waiting = next((x for x in items if x['status'] == 'waiting_quota'), None)
            pending = None
            blocked_threads = set()
            for candidate in items:
                if candidate['status'] not in ('pending', 'needs_attention'):
                    continue
                thread_id = candidate['thread_id']
                if thread_id and thread_id in blocked_threads:
                    continue
                if (candidate['status'] == 'needs_attention' or
                        candidate['resume_after'] and candidate['resume_after'] > time.time()):
                    if thread_id:
                        blocked_threads.add(thread_id)
                    continue
                pending = candidate
                break
            pending = waiting or pending
            if not pending:
                return
            quota = self.store.refresh_quota()
            reason, reset = quota_dispatch_gate(quota, pending)
            if reason not in ('可派发', '重置后试发'):
                if waiting and reset and (not waiting['resume_after'] or reset != waiting['resume_after']):
                    with self.store.connect() as con:
                        con.execute("UPDATE night_queue_items SET resume_after=? WHERE id=? AND status='waiting_quota'",
                                    (reset, waiting['id']))
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
                self.wake.set()
                return
            if not Path(pending['cwd']).is_dir():
                with self.store.connect() as con:
                    con.execute("UPDATE night_queue_items SET status='needs_attention',message='工作目录不存在' WHERE id=?", (pending['id'],))
                self.wake.set()
                return
            # The user may turn the queue off while discovery, quota, or model reads are in flight.
            if not self.config()['enabled']:
                return
            with self.store.connect() as con:
                if waiting:
                    updated = con.execute("UPDATE night_queue_items SET status='resuming',resume_count=resume_count+1,message='正在续接原任务' WHERE id=? AND status='waiting_quota'",
                                          (pending['id'],)).rowcount
                else:
                    updated = con.execute("UPDATE night_queue_items SET status='claimed',started_at=?,message=NULL,finished_at=NULL,resume_after=NULL,actual_model=?,actual_effort=? WHERE id=? AND status='pending'",
                                          (now(), model, effort, pending['id'])).rowcount
            if updated:
                item = {**pending, 'model': model, 'effort': effort}
                self.active = threading.Thread(target=self._run, args=(item,), daemon=True)
                self.active.start()

    def _reconcile_quota_wake(self, state):
        """After a service restart, verify whether the wake message was accepted."""
        if not state['thread_id']:
            if time.time() - (state['started_at'] or 0) >= 120:
                with self.store.connect() as con:
                    con.execute("UPDATE quota_wake_state SET status='uncertain',message='服务中断时无法确认唤醒消息是否发送' WHERE id=1 AND status='running'")
            return
        snapshot = read_thread_snapshot(state['thread_id'], include_user=True)
        accepted = (snapshot.get('turn_id') and
                    snapshot['turn_id'] != state['previous_turn_id'] and
                    snapshot.get('user_prompt', '').strip() == QUOTA_WAKE_PROMPT)
        with self.store.connect() as con:
            if accepted:
                if snapshot.get('quota_exhausted'):
                    con.execute('''UPDATE quota_wake_state SET status='retry',thread_id=NULL,
                        turn_id=NULL,next_attempt_at=?,message='额度仍受限；稍后重试' WHERE id=1 AND status='running' ''',
                        (time.time() + QUOTA_PROBE_INTERVAL,))
                elif snapshot.get('turn_status') in ('inProgress', 'in_progress'):
                    return
                elif snapshot.get('turn_status') != 'completed':
                    con.execute("UPDATE quota_wake_state SET status='uncertain',message='唤醒轮次未正常完成；停止自动重发' WHERE id=1 AND status='running'")
                else:
                    con.execute('''UPDATE quota_wake_state SET status='done',turn_id=?,
                        next_attempt_at=NULL,message='已发送唤醒消息，等待额度快照更新'
                        WHERE id=1 AND status='running' ''', (snapshot['turn_id'],))
            elif time.time() - (state['started_at'] or 0) >= 120:
                if snapshot.get('turn_id') == state['previous_turn_id'] or not snapshot.get('turn_id'):
                    con.execute('''UPDATE quota_wake_state SET status='retry',thread_id=NULL,
                        turn_id=NULL,next_attempt_at=?,message='未发现已发送轮次；稍后重试'
                        WHERE id=1 AND status='running' ''', (time.time() + QUOTA_PROBE_INTERVAL,))
                else:
                    con.execute("UPDATE quota_wake_state SET status='uncertain',message='原对话出现其他轮次；停止自动重发' WHERE id=1 AND status='running'")

    def _run_quota_wake(self, state, model, effort, cwd):
        def started(thread_id, turn_id):
            with self.store.connect() as con:
                con.execute('INSERT OR IGNORE INTO quota_wake_threads(thread_id) VALUES (?)', (thread_id,))
                con.execute('''UPDATE quota_wake_state SET thread_id=?,turn_id=COALESCE(?,turn_id)
                    WHERE id=1 AND status='running' ''', (thread_id, turn_id))
        item = {'source': 'followup', 'status': 'pending', 'thread_id': state['thread_id'],
                'turn_id': state['turn_id'], 'anchor_turn_id': state['turn_id'],
                'cwd': cwd, 'model': model, 'effort': effort, 'prompt': QUOTA_WAKE_PROMPT}
        try:
            status, message = self.runner.run(item, started)
        except Exception as exc:
            status, message = 'reconcile', str(exc)[:180]
        with self.store.connect() as con:
            if status == 'completed':
                con.execute('''UPDATE quota_wake_state SET status='done',next_attempt_at=NULL,
                    message='已发送唤醒消息，等待额度快照更新' WHERE id=1 AND status='running' ''')
            elif status in ('quota_exhausted', 'deferred'):
                con.execute('''UPDATE quota_wake_state SET status='retry',thread_id=NULL,
                    turn_id=NULL,next_attempt_at=?,message=? WHERE id=1 AND status='running' ''',
                    (time.time() + QUOTA_PROBE_INTERVAL, message[:180]))
            elif status == 'reconcile':
                con.execute("UPDATE quota_wake_state SET message=? WHERE id=1 AND status='running'",
                            ('正在核对唤醒轮次：' + message[:140],))
            else:
                con.execute('''UPDATE quota_wake_state SET status='uncertain',message=?
                    WHERE id=1 AND status='running' ''', (message[:180],))
        if status == 'completed':
            self.store.refresh_quota()
        with self._lock:
            self.active = None
        self.wake.set()

    def _quota_wake_tick(self):
        """Start each new five-hour window even when the business queue is empty."""
        if self.restart_requested or not self.config()['quota_wake_enabled']:
            return
        with self._lock:
            if self.active and self.active.is_alive():
                return
            state = self.quota_wake_state()
            if state['status'] == 'running':
                self._reconcile_quota_wake(state)
                return
            quota = self.store.quota_status()
            source = quota if quota.get('status') == 'ok' else quota.get('last_success') or {}
            limits = [x for x in source.get('limits') or [] if x.get('limit_id') == 'codex']
            if len(limits) != 1:
                return
            windows = [w for name in ('primary', 'secondary') if isinstance((w := limits[0].get(name)), dict)]
            five = next((w for w in windows if w.get('windowDurationMins') == 300), None)
            week = next((w for w in windows if w.get('windowDurationMins') == 10080), None)
            if not five or not week or not isinstance(five.get('resetsAt'), (int, float)) or not isinstance(week.get('usedPercent'), (int, float)) or not isinstance(week.get('resetsAt'), (int, float)):
                return
            boundary = five['resetsAt']
            used = five.get('usedPercent')
            if quota.get('status') == 'ok' and isinstance(used, (int, float)):
                previous_boundary = state.get('observed_reset_at')
                previous_used = state.get('observed_used_percent')
                with self.store.connect() as con:
                    con.execute('''UPDATE quota_wake_state SET observed_reset_at=?,observed_used_percent=?
                        WHERE id=1''', (boundary, used))
                    if (previous_boundary and
                            isinstance(previous_used, (int, float)) and previous_used >= 100 and
                            used < 100 and state['status'] != 'running'):
                        con.execute('''UPDATE quota_wake_state SET window_reset_at=?,status='done',
                            next_attempt_at=NULL,message='额度已自动刷新，无需发送唤醒消息'
                            WHERE id=1''', (previous_boundary,))
                        state = {**state, 'window_reset_at': previous_boundary, 'status': 'done'}
            if boundary + 5 > time.time() or (week['usedPercent'] >= 100 and week['resetsAt'] > time.time()):
                return
            if state['window_reset_at'] == boundary:
                if state['status'] in ('done', 'uncertain') or (state['next_attempt_at'] and state['next_attempt_at'] > time.time()):
                    return
            models = {row['id']: row for row in self.model_provider()}
            model = 'gpt-6-luna' if 'gpt-6-luna' in models else self.config()['default_model']
            candidate = models.get(model)
            if not candidate:
                return
            effort = 'low' if 'low' in candidate['efforts'] else candidate['default_effort']
            if not effort or effort not in candidate['efforts']:
                return
            cwd = str(Path(__file__).resolve().parent.parent)
            thread_id = state['thread_id'] if state['status'] == 'done' else None
            turn_id = state['turn_id'] if thread_id else None
            with self.store.connect() as con:
                con.execute('''UPDATE quota_wake_state SET window_reset_at=?,status='running',
                    thread_id=?,turn_id=?,previous_turn_id=?,started_at=?,next_attempt_at=NULL,
                    message='正在发送额度窗口唤醒消息' WHERE id=1''',
                    (boundary, thread_id, turn_id, turn_id, time.time()))
            item = {'thread_id': thread_id, 'turn_id': turn_id}
            self.active = threading.Thread(target=self._run_quota_wake,
                                           args=(item, model, effort, cwd), daemon=True)
            self.active.start()

    def loop(self, stop):
        while not stop.is_set():
            try:
                self.tick()
            except Exception as exc:
                # Failed quota/model reads are retried; no task is claimed on failure.
                self.last_error = str(exc)[:180]
            try:
                self._quota_wake_tick()
            except Exception as exc:
                self.last_error = '额度唤醒检查失败：' + str(exc)[:150]
            self.wake.wait(60)
            self.wake.clear()
