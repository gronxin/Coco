"""Read user-authored prompts from local Codex sessions, outside the usage ledger."""
import hashlib
import json
import re
import sqlite3
import threading
from pathlib import Path

from .parser import timestamp


INTERNAL_SOURCES = {'subagent', 'guardian_review'}
USER_CONTENT_KINDS = {'user.text', 'user.image', 'user.file', 'user.audio',
                      'user.video', 'user.attachment'}
CONTROL_TAG = re.compile(r'^</?[a-z][a-z0-9_-]*(?:\s[^<>]*)?>', re.IGNORECASE)
QUEUE_ATTACHMENT_MARKER = '\n\n[Coco Codex管家附件]'


def _without_queue_attachment_paths(value):
    return value.split(QUEUE_ATTACHMENT_MARKER, 1)[0].strip()


def _user_text(value):
    """Extract visible user text, rejecting ambiguous machine envelopes."""
    stripped = value.strip()
    # Codex can place generated context inside a user.text item. Peel off only
    # known preambles; fail closed on incomplete or unfamiliar control markup.
    for _ in range(5):
        if stripped.startswith('# Files mentioned by the user:'):
            marker = '## My request:'
            end = stripped.find(marker)
            if end < 0:
                return '', 0
            stripped = stripped[end + len(marker):].strip()
        elif stripped.startswith('<in-app-browser-context'):
            closing = '</in-app-browser-context>'
            end = stripped.find(closing)
            if end < 0:
                return '', 0
            stripped = stripped[end + len(closing):].strip()
        elif stripped.startswith(('<environment_context>', 'environment_context>')):
            closing = '</environment_context>'
            end = stripped.find(closing)
            if end < 0:
                return '', 0
            stripped = stripped[end + len(closing):].strip()
        elif stripped.startswith('## My request:'):
            stripped = stripped[len('## My request:'):].strip()
        else:
            break
    if not stripped:
        return '', 0
    if stripped.startswith('<send_user_message_question_reply>'):
        opening, closing = '<send_user_message_question_reply>', '</send_user_message_question_reply>'
        if not stripped.endswith(closing):
            return '', 0
        try:
            answers = json.loads(stripped[len(opening):-len(closing)])
        except (ValueError, TypeError):
            return '', 0
        return '\n'.join(str(row.get('answer') or '') for row in answers
                         if isinstance(row, dict) and row.get('answer')).strip(), 0
    if stripped.startswith('<image name='):
        return '', 0
    if CONTROL_TAG.match(stripped):
        return '', 0
    return _without_queue_attachment_paths(stripped), 0


class PromptHistory:
    def __init__(self, store):
        self.store = store
        self._cache = {}
        self._lock = threading.Lock()

    @staticmethod
    def _read_file(path):
        meta = {}
        prompts = []
        active_turn = None
        with path.open(errors='replace') as stream:
            for raw in stream:
                try:
                    entry = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                payload = entry.get('payload') if isinstance(entry, dict) else None
                if not isinstance(payload, dict):
                    continue
                kind = entry.get('type')
                if kind == 'session_meta':
                    meta = {'id': payload.get('id'), 'cwd': payload.get('cwd'),
                            'source': payload.get('thread_source'),
                            'subagent': bool((payload.get('source') or {}).get('subagent'))
                            if isinstance(payload.get('source'), dict) else False,
                            'created_at': timestamp(entry.get('timestamp'))}
                elif kind == 'turn_context':
                    active_turn = payload.get('turn_id') or active_turn
                elif kind == 'event_msg' and payload.get('type') == 'task_started':
                    active_turn = payload.get('turn_id') or active_turn
                elif kind == 'response_item' and payload.get('type') == 'message' and payload.get('role') == 'user':
                    pieces = []
                    attachments = 0
                    content_parts = payload.get('content') or []
                    if not isinstance(content_parts, list):
                        continue
                    passthrough = payload.get('internal_chat_message_metadata_passthrough') or {}
                    kinds = passthrough.get('content_item_kinds') if isinstance(passthrough, dict) else None
                    if not isinstance(kinds, list) or len(kinds) != len(content_parts):
                        continue  # Origin must be explicit and aligned with each part.
                    for index, part in enumerate(content_parts):
                        origin = kinds[index]
                        if origin not in USER_CONTENT_KINDS:
                            continue
                        if origin != 'user.text':
                            attachments += 1
                            continue
                        if isinstance(part, dict):
                            value = part.get('text') or part.get('input_text')
                            if isinstance(value, str) and value:
                                clean, extra_attachments = _user_text(value)
                                attachments += extra_attachments
                                if clean:
                                    pieces.append(clean)
                        elif isinstance(part, str):
                            clean, extra_attachments = _user_text(part)
                            attachments += extra_attachments
                            if clean:
                                pieces.append(clean)
                    content = '\n'.join(pieces).strip()
                    if not content and not attachments:
                        continue
                    at = timestamp(entry.get('timestamp'))
                    if meta.get('created_at') and at and at < meta['created_at']:
                        continue  # Forked sessions can contain copied earlier turns.
                    key = str(payload.get('id') or hashlib.sha256(
                        f'{active_turn}\0{at}\0{content}'.encode()).hexdigest())
                    prompts.append({'id': key, 'turn_id': active_turn, 'at': at,
                                    'text': content, 'attachments': attachments,
                                    'characters': len(content), 'source': 'conversation'})
        return {'meta': meta, 'prompts': prompts}

    def _sessions(self):
        files = self.store.discover()
        current = set(files)
        with self._lock:
            for path in set(self._cache) - current:
                del self._cache[path]
            output = []
            for path in files:
                stat = path.stat()
                signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
                cached = self._cache.get(path)
                if cached is None or cached[0] != signature:
                    cached = (signature, self._read_file(path))
                    self._cache[path] = cached
                output.append(cached[1])
        return output

    def snapshot(self):
        if self.store.privacy_mode:
            return {'disabled': True, 'projects': [], 'total_tasks': 0, 'total_prompts': 0,
                    'total_characters': 0}
        try:
            metadata, _ = self.store._metadata()
        except (OSError, ValueError, sqlite3.Error):
            metadata = {}
        threads = {}
        seen = set()
        for session in self._sessions():
            meta = session['meta']
            thread_id = meta.get('id')
            if not thread_id:
                continue
            info = metadata.get(thread_id) or {}
            if (meta.get('source') in INTERNAL_SOURCES or info.get('thread_source') in INTERNAL_SOURCES or
                    meta.get('subagent') or info.get('agent_role') in ('guardian_review', 'subagent')):
                continue
            task = threads.setdefault(thread_id, {
                'id': thread_id, 'title': info.get('name') or info.get('title') or f'任务 {thread_id[:8]}',
                'project': info.get('cwd') or meta.get('cwd') or '未归属项目',
                'codex_project': info.get('codex_project_name') or '', 'prompts': []})
            for prompt in session['prompts']:
                identity = (thread_id, prompt['id'])
                if identity not in seen:
                    task['prompts'].append(prompt)
                    seen.add(identity)

        # Desktop bridge sends may not appear as userMessage in the session log.
        with self.store.connect() as con:
            queue_rows = con.execute('''SELECT thread_id,turn_id,title,prompt,cwd,started_at,status,source
                FROM night_queue_items WHERE source IN ('queue','followup') AND prompt!=''
                AND thread_id IS NOT NULL AND turn_id IS NOT NULL
                AND status IN ('running','resuming','bridge_running','completed','watching')''').fetchall()
        for row in queue_rows:
            thread_id = row['thread_id']
            info = metadata.get(thread_id) or {}
            if info.get('thread_source') in INTERNAL_SOURCES:
                continue
            task = threads.setdefault(thread_id, {
                'id': thread_id, 'title': info.get('name') or info.get('title') or row['title'],
                'project': info.get('cwd') or row['cwd'] or '未归属项目',
                'codex_project': info.get('codex_project_name') or '', 'prompts': []})
            user_prompt = _without_queue_attachment_paths(row['prompt'])
            if any(p['turn_id'] == row['turn_id'] and p['text'].strip() == user_prompt
                   for p in task['prompts']):
                continue
            task['prompts'].append({
                'id': f'queue-{thread_id}-{row["turn_id"]}', 'turn_id': row['turn_id'],
                'at': row['started_at'], 'text': user_prompt,
                'attachments': row['prompt'].split(QUEUE_ATTACHMENT_MARKER, 1)[1].count('\n- ')
                if QUEUE_ATTACHMENT_MARKER in row['prompt'] else 0,
                'characters': len(user_prompt), 'source': 'queue'})

        projects = {}
        for task in threads.values():
            if not task['prompts']:
                continue
            task['prompts'].sort(key=lambda item: (item['at'] or '', item['id']))
            task['prompt_count'] = len(task['prompts'])
            task['character_count'] = sum(item['characters'] for item in task['prompts'])
            task['last_at'] = max((item['at'] or '' for item in task['prompts']), default='')
            project = projects.setdefault(task['project'], {
                'path': task['project'], 'name': Path(task['project']).name,
                'codex_project': task['codex_project'], 'tasks': [],
                'prompt_count': 0, 'character_count': 0, 'last_at': ''})
            project['tasks'].append(task)
            project['prompt_count'] += task['prompt_count']
            project['character_count'] += task['character_count']
            project['last_at'] = max(project['last_at'], task['last_at'])
        result = list(projects.values())
        for project in result:
            project['tasks'].sort(key=lambda item: (item['last_at'], item['id']), reverse=True)
            project['task_count'] = len(project['tasks'])
        result.sort(key=lambda item: (item['last_at'], item['path']), reverse=True)
        return {'disabled': False, 'projects': result, 'total_tasks': sum(p['task_count'] for p in result),
                'total_prompts': sum(p['prompt_count'] for p in result),
                'total_characters': sum(p['character_count'] for p in result)}
