"""Transactional incremental importer. SQLite writes stay in the analyzer directory."""
import hashlib
import json
import os
import sqlite3
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .parser import FIELDS, annotate, reconcile, sanitize
from .activity import read_turn_activity
from .quota import read_quota, quota_forecast

LOCAL_TZ = ZoneInfo('Asia/Shanghai')


def _safe_title(value, task_id):
    title = (value or '').strip()
    lowered = title.lower()
    generated_prefixes = (
        'the following is the codex agent history',
        '<auto-review',
        'you are reviewing the work of another codex agent',
    )
    if not title or lowered.startswith(generated_prefixes):
        return '任务 ' + task_id[:8]
    return (title[:157] + '…') if len(title) > 160 else title


def _project_for_cwd(cwd, direct_project_id, projects, roots):
    """Resolve Codex sidebar projects; current Codex links them through project_roots."""
    if direct_project_id in projects:
        return projects[direct_project_id]
    if not cwd:
        return None
    target = os.path.normcase(os.path.normpath(cwd))
    matches = []
    for project_id, root in roots:
        normalized = os.path.normcase(os.path.normpath(root))
        try:
            if os.path.commonpath((target, normalized)) == normalized:
                matches.append((len(normalized), projects.get(project_id)))
        except ValueError:
            continue
    matches = [item for item in matches if item[1]]
    return max(matches, default=(0, None))[1]


def _fallback_task_title(meta, task_id):
    if meta.get('thread_source') == 'guardian_review' or meta.get('subagent_type') == 'guardian':
        return '权限审批审查'
    if meta.get('subagent_type') == 'thread_spawn':
        return '子代理任务 ' + task_id[:8]
    return '任务 ' + task_id[:8]


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, database, source_root, privacy_mode=False):
        self.path = Path(database).resolve()
        self.privacy_mode = bool(privacy_mode)
        root = Path(source_root).expanduser().resolve()
        if root == self.path or root in self.path.parents:
            raise ValueError('分析数据库必须位于 Codex 数据目录之外')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.state = dict(syncing=False, last_sync=None, last_error=None, progress={'done': 0, 'total': 0})
        with self.connect() as con:
            con.executescript('''
            CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY,identity TEXT,offset INTEGER,line INTEGER,head TEXT,tail TEXT,mtime INTEGER);
            CREATE TABLE IF NOT EXISTS events(path TEXT,line INTEGER,data TEXT,PRIMARY KEY(path,line));
            CREATE TABLE IF NOT EXISTS parse_issues(path TEXT,line INTEGER,code TEXT,message TEXT);
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,data TEXT);
            CREATE TABLE IF NOT EXISTS ledger(id TEXT PRIMARY KEY,task_id TEXT,data TEXT);
            CREATE INDEX IF NOT EXISTS ledger_task ON ledger(task_id);
            CREATE TABLE IF NOT EXISTS issues(task_id TEXT,path TEXT,line INTEGER,code TEXT,message TEXT);
            CREATE TABLE IF NOT EXISTS prices(id INTEGER PRIMARY KEY,data TEXT);
            CREATE TABLE IF NOT EXISTS subscriptions(month TEXT PRIMARY KEY,data TEXT);
            CREATE TABLE IF NOT EXISTS activity_events(id TEXT PRIMARY KEY,task_id TEXT,turn_id TEXT,timestamp TEXT,type TEXT,tool TEXT,status TEXT,failed INTEGER,source_path TEXT,source_line INTEGER,data TEXT);
            CREATE INDEX IF NOT EXISTS activity_task_turn ON activity_events(task_id,turn_id,source_line);
            CREATE TABLE IF NOT EXISTS budgets(id INTEGER PRIMARY KEY,data TEXT);
            CREATE TABLE IF NOT EXISTS quota(id INTEGER PRIMARY KEY CHECK(id=1),data TEXT);
            ''')
            con.execute('INSERT OR IGNORE INTO config VALUES (?,?)', ('source_root', str(root)))
            con.execute('INSERT OR IGNORE INTO config VALUES (?,?)', ('data_revision', '0'))

    def connect(self):
        con = sqlite3.connect(str(self.path), timeout=30)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA journal_mode=WAL')
        return con

    @property
    def source_roots(self):
        with self.connect() as con:
            row=con.execute("SELECT value FROM config WHERE key='source_roots'").fetchone()
            if row:
                return [Path(value) for value in json.loads(row[0])]
            return [Path(con.execute("SELECT value FROM config WHERE key='source_root'").fetchone()[0])]

    @property
    def source_root(self):
        return self.source_roots[0]

    def set_root(self, root):
        self.set_roots([root])

    def set_roots(self, roots):
        resolved=[]
        for value in roots:
            root=Path(value).expanduser().resolve()
            if root in resolved: continue
            if not root.is_dir() or not (root/'sessions').is_dir():
                raise ValueError(f'数据目录不可用或缺少 sessions：{root}')
            if root == self.path or root in self.path.parents:
                raise ValueError('数据目录不能包含分析数据库')
            resolved.append(root)
        if not resolved:
            raise ValueError('至少需要一个 Codex 数据目录')
        if not self.lock.acquire(blocking=False):
            raise ValueError('同步中，请稍后更换目录')
        try:
            with self.connect() as con:
                con.execute('UPDATE config SET value=? WHERE key=?', (str(resolved[0]), 'source_root'))
                con.execute('INSERT OR REPLACE INTO config VALUES (?,?)',('source_roots',json.dumps([str(v) for v in resolved])))
                # Explicit source changes replace the imported dataset, keeping user prices.
                for table in ('files', 'events', 'parse_issues', 'tasks', 'ledger', 'issues', 'activity_events'):
                    con.execute('DELETE FROM ' + table)
        finally:
            self.lock.release()

    def discover(self):
        paths = []
        for root in self.source_roots:
            for name in ('sessions', 'archived_sessions'):
                base = root / name
                if base.is_dir():
                    paths.extend(p for p in base.rglob('*.jsonl') if p.is_file() and not p.is_symlink() and root in p.resolve().parents)
        return sorted(paths)

    def sync(self):
        if not self.lock.acquire(blocking=False):
            return False
        self.state.update(syncing=True, last_error=None)
        try:
            if not all((root/'sessions').is_dir() for root in self.source_roots):
                raise ValueError('至少一个 Codex sessions 目录不可用；已保留上次采集结果。')
            paths = self.discover()
            self.state['progress'] = {'done': 0, 'total': len(paths)}
            with self.connect() as con:
                for i, path in enumerate(paths):
                    self._import(con, path)
                    self.state['progress'] = {'done': i + 1, 'total': len(paths)}
                # Removed sources are retained for offline access; surfaced as missing.
                before = self._data_digest(con)
                self._rebuild(con)
                after = self._data_digest(con)
                if before != after:
                    con.execute("UPDATE config SET value=CAST(value AS INTEGER)+1 WHERE key='data_revision'")
            self.state['last_sync'] = now()
            return True
        except Exception as exc:
            self.state['last_error'] = str(exc)
            return False
        finally:
            self.state['syncing'] = False
            self.lock.release()

    def _import(self, con, path):
        name = str(path)
        stat = path.stat()
        old = con.execute('SELECT * FROM files WHERE path=?', (name,)).fetchone()
        identity = '%s:%s' % (stat.st_dev, stat.st_ino)
        offset, line = (old['offset'], old['line']) if old else (0, 0)
        with path.open('rb') as stream:
            # Hash only already-ingested bytes: appending to a small file is not a replacement.
            head = hashlib.sha256(stream.read(min(offset, 4096))).hexdigest()
            stream.seek(max(0, offset - 4096))
            tail = hashlib.sha256(stream.read(min(offset, 4096))).hexdigest()
            reset = old and (old['identity'] != identity or stat.st_size < offset or head != old['head'] or tail != old['tail'] or (stat.st_size == offset and stat.st_mtime_ns != old['mtime']))
            if reset:
                con.execute('DELETE FROM events WHERE path=?', (name,))
                con.execute('DELETE FROM parse_issues WHERE path=?', (name,))
                offset, line = 0, 0
            stream.seek(offset)
            while True:
                raw = stream.readline()
                if not raw or not raw.endswith(b'\n'):
                    break
                offset += len(raw)
                line += 1
                try:
                    clean = sanitize(json.loads(raw))
                    if clean:
                        clean['line'] = line
                        con.execute('INSERT OR REPLACE INTO events VALUES (?,?,?)', (name, line, json.dumps(clean)))
                except (ValueError, TypeError, KeyError, AttributeError):
                    con.execute('INSERT INTO parse_issues VALUES (?,?,?,?)', (name, line, 'invalid_json', '无法解析此行；未保存原文。'))
            stream.seek(0)
            head = hashlib.sha256(stream.read(min(offset, 4096))).hexdigest()
            stream.seek(max(0, offset - 4096))
            tail = hashlib.sha256(stream.read(min(offset, 4096))).hexdigest()
        con.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?)', (name, identity, offset, line, head, tail, stat.st_mtime_ns))

    def _metadata(self):
        tasks, edges = {}, {}
        candidates = sorted((p for root in self.source_roots for p in root.glob('state_*.sqlite')), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            return tasks, edges
        # Read every source database in read-only mode; newer metadata wins.
        for candidate in reversed(candidates):
            con = sqlite3.connect(candidate.as_uri() + '?mode=ro', uri=True, timeout=1)
            con.row_factory = sqlite3.Row
            try:
                con.execute('PRAGMA query_only=ON')
                cols = {r[1] for r in con.execute('PRAGMA table_info(threads)')}
                names = [n for n in ('id', 'title', 'name', 'cwd', 'updated_at', 'archived', 'project_id', 'thread_source', 'originator', 'agent_nickname', 'agent_role') if n in cols]
                project_cols = {r[1] for r in con.execute('PRAGMA table_info(projects)')}
                projects = {}
                if {'id','name'} <= project_cols:
                    projects = dict(con.execute('SELECT id,name FROM projects'))
                root_cols = {r[1] for r in con.execute('PRAGMA table_info(project_roots)')}
                roots = list(con.execute('SELECT project_id,path FROM project_roots')) if {'project_id','path'} <= root_cols else []
                if 'id' in names:
                    for row in con.execute('SELECT ' + ','.join(names) + ' FROM threads'):
                        item = dict(row)
                        item['codex_project_name'] = _project_for_cwd(item.get('cwd'), item.get('project_id'), projects, roots)
                        tasks[item['id']] = item
                if con.execute("SELECT 1 FROM sqlite_master WHERE name='thread_spawn_edges'").fetchone():
                    edges.update(dict(con.execute('SELECT child_thread_id,parent_thread_id FROM thread_spawn_edges')))
            finally:
                con.close()
        return tasks, edges

    def _rebuild(self, con):
        sources = defaultdict(list)
        for r in con.execute('SELECT * FROM events ORDER BY path,line'):
            sources[r['path']].append(json.loads(r['data']))
        tasks, owners, task_sources = {}, {}, {}
        try:
            metadata, edges = self._metadata()
        except (sqlite3.Error, OSError):
            metadata, edges = {}, {}
        for path, events in sources.items():
            meta, task, annotated = annotate(events, path)
            task_sources[path] = task
            m = metadata.get(task, {})
            fallback_title = _fallback_task_title(meta, task)
            generated_default = '任务 ' + task[:8]
            title = _safe_title(m.get('title'), task) if m.get('title') else fallback_title
            window_name = _safe_title(m.get('name') or m.get('title'), task) if (m.get('name') or m.get('title')) else fallback_title
            # State metadata for internal review tasks contains a transcript, not a user-facing title.
            if title == generated_default:
                title = fallback_title
            if window_name == generated_default:
                window_name = fallback_title
            codex_project = m.get('codex_project_name') or '未加入 Codex 项目'
            project = m.get('cwd') or meta.get('cwd') or '未归属项目'
            if self.privacy_mode:
                title = '任务 ' + task[:8]
                window_name = title
                if codex_project != '未加入 Codex 项目':
                    codex_project = 'Codex 项目 ' + hashlib.sha256(codex_project.encode()).hexdigest()[:10]
                if project != '未归属项目':
                    project = '项目 ' + hashlib.sha256(project.encode()).hexdigest()[:10]
            state_source = m.get('thread_source')
            is_internal = state_source in ('subagent', 'guardian_review') or meta.get('thread_source') == 'guardian_review'
            # Old fixtures and older Codex logs may have no state database entry;
            # treat only those relationship-free records as top-level candidates.
            has_window_metadata = not is_internal and (bool((m.get('name') or m.get('title') or '').strip()) or not m)
            tasks[task] = dict(id=task, title=title,
                               project=project, codex_project=codex_project, window_name=window_name,
                               updated_at=max((e['timestamp'] or '' for e in events), default=''),
                               parent_id=edges.get(task) or meta.get('parent_id'), forked_from_id=meta.get('forked_from_id'),
                               cli_version=meta.get('cli_version'), archived=bool(m.get('archived')),
                               thread_source=meta.get('thread_source'), subagent_type=meta.get('subagent_type'),
                               has_window_metadata=has_window_metadata)
            for e in annotated:
                if e.get('turn_id'):
                    # Original files start before their own turns; copied history predates fork metadata.
                    native = not meta.get('timestamp') or (e['timestamp'] and e['timestamp'] >= meta['timestamp'])
                    candidate = (0 if native else 1, meta.get('timestamp') or '', task)
                    if e['turn_id'] not in owners or candidate < owners[e['turn_id']]:
                        owners[e['turn_id']] = candidate
        turn_owners = {key: value[2] for key, value in owners.items()}
        ledger, issues = {}, []
        for path, events in sources.items():
            rows, found = reconcile(events, path, turn_owners)
            issues.extend(found)
            for row in rows:
                if row['task_id'] not in tasks:
                    tasks[row['task_id']] = dict(id=row['task_id'], title='未归属用量' if row['task_id']=='unassigned' else '任务 '+row['task_id'][:8], project='未归属项目', codex_project='未加入 Codex 项目', window_name='未归属用量' if row['task_id']=='unassigned' else '关系待确认的历史任务', updated_at=row['timestamp'] or '', parent_id=None, forked_from_id=None, has_window_metadata=False)
                old = ledger.get(row['id'])
                if old and any(old[k] != row[k] for k in FIELDS):
                    issues.append(dict(task_id=row['task_id'], path=path, line=row['source_line'], code='duplicate_conflict', message='同一请求 ID 的用量不一致，保留原始所属任务记录。'))
                if old is None or (task_sources.get(path) == row['task_id'] and task_sources.get(old['source_path']) != old['task_id']):
                    ledger[row['id']] = row
        # A Codex thread is not necessarily a sidebar task window. Child agents,
        # forks and legacy internal threads inherit their top-level window; records
        # without a verifiable root remain explicitly unresolved.
        def window_root(task_id):
            seen = set()
            current = task_id
            while current in tasks and current not in seen:
                seen.add(current)
                item = tasks[current]
                relation = item.get('parent_id') or item.get('forked_from_id')
                if relation and relation in tasks:
                    current = relation
                    continue
                return current if item.get('has_window_metadata') else None
            return None
        for task_id, item in tasks.items():
            root_id = window_root(task_id)
            item['window_root_id'] = root_id
            item['is_window_root'] = root_id == task_id
            item['hierarchy_status'] = 'window' if root_id == task_id else 'nested' if root_id else 'unresolved'
            if root_id and root_id != task_id:
                root = tasks[root_id]
                item['window_name'] = root['window_name']
                item['codex_project'] = root['codex_project']
                item['project'] = root['project']
            elif not root_id:
                item['window_name'] = '关系待确认的历史任务'
        for r in con.execute('SELECT * FROM parse_issues'):
            issues.append(dict(r, task_id=task_sources.get(r['path'], 'unassigned')))
        for path in sources:
            if not Path(path).exists():
                issues.append(dict(task_id=task_sources[path], path=path, line=0, code='source_missing', message='原始日志已不存在，展示上次采集数据。'))
        con.execute('DELETE FROM ledger')
        con.execute('DELETE FROM tasks')
        con.execute('DELETE FROM issues')
        con.executemany('INSERT INTO tasks VALUES (?,?)', [(k, json.dumps(v)) for k,v in tasks.items()])
        con.executemany('INSERT INTO ledger VALUES (?,?,?)', [(k,v['task_id'],json.dumps(v)) for k,v in ledger.items()])
        con.executemany('INSERT INTO issues VALUES (:task_id,:path,:line,:code,:message)', issues)
        self._rebuild_activity(con, task_sources, ledger)

    def _data_digest(self, con):
        # Stable across DELETE+INSERT rebuilds; changes only when analytical data changes.
        task_digest=hashlib.sha256('\n'.join(r[0] for r in con.execute('SELECT data FROM tasks ORDER BY id')).encode()).hexdigest()
        return tuple(con.execute("SELECT COUNT(*),COALESCE(SUM(json_extract(data,'$.total_tokens')),0),COALESCE(MAX(json_extract(data,'$.timestamp')),'') FROM ledger").fetchone()) + \
               (task_digest,) + \
               tuple(con.execute("SELECT COUNT(*),COALESCE(SUM(line),0) FROM issues").fetchone()) + \
               tuple(con.execute("SELECT COUNT(*),COALESCE(MAX(timestamp),''),COALESCE(SUM(failed),0),COALESCE(SUM(source_line),0) FROM activity_events").fetchone())

    def _rebuild_activity(self, con, task_sources, ledger):
        """Persist only generated summaries and accounting fields, never raw payloads."""
        con.execute('DELETE FROM activity_events')
        turns_by_task = defaultdict(set)
        for row in ledger.values():
            if row.get('turn_id'):
                turns_by_task[row['task_id']].add(row['turn_id'])
        rows = []
        for path, task_id in task_sources.items():
            turns = turns_by_task.get(task_id, set())
            if not turns:
                continue
            source_root=next((root for root in self.source_roots if root in Path(path).resolve().parents),None)
            if source_root is None: continue
            for turn_id, activity in read_turn_activity([path], source_root, turns).items():
                for event in activity['timeline']:
                    # Request/assistant excerpts are conversation text. Keep only a safe label.
                    safe = dict(event)
                    if safe.get('type') in ('request', 'response'):
                        safe['detail'] = '用户请求内容未写入分析数据库。' if safe['type']=='request' else '模型生成了用户可见消息；正文未保存。'
                    safe.pop('result', None)
                    identity = hashlib.sha256(f'{path}:{event.get("source_line")}:{turn_id}:{event.get("type")}'.encode()).hexdigest()[:32]
                    rows.append((identity, task_id, turn_id, event.get('timestamp'), event.get('type'),
                                 event.get('tool'), event.get('status'), int(event.get('status')=='返回错误'),
                                 path, event.get('source_line'), json.dumps(safe, ensure_ascii=False)))
        con.executemany('INSERT OR REPLACE INTO activity_events VALUES (?,?,?,?,?,?,?,?,?,?,?)', rows)

    def snapshot(self):
        with self.connect() as con:
            con.execute('BEGIN')
            return dict(tasks={r['id']:json.loads(r['data']) for r in con.execute('SELECT * FROM tasks')},
                        rows=[json.loads(r[0]) for r in con.execute('SELECT data FROM ledger')],
                        issues=[dict(r) for r in con.execute('SELECT * FROM issues')],
                        prices=[dict(json.loads(r['data']), id=r['id']) for r in con.execute('SELECT * FROM prices')],
                        subscriptions=[json.loads(r[0]) for r in con.execute('SELECT data FROM subscriptions')])

    def status(self):
        with self.connect() as con:
            counts = {t: con.execute('SELECT COUNT(*) FROM ' + t).fetchone()[0] for t in ('files','events')}
            calls = con.execute("SELECT COUNT(*) FROM ledger WHERE json_extract(data,'$.kind')='call'").fetchone()[0]
            diagnostics = {r['code']:r['n'] for r in con.execute('SELECT code,COUNT(*) n FROM issues GROUP BY code')}
            revision = int(con.execute("SELECT value FROM config WHERE key='data_revision'").fetchone()[0])
        return dict(self.state, **counts, source_root=str(self.source_root),source_roots=[str(v) for v in self.source_roots], database_path=str(self.path),
                    calls=calls, data_revision=revision, diagnostic_count=sum(diagnostics.values()), diagnostic_counts=diagnostics)

    def issues_page(self, page=1, page_size=50, code='', task_id=''):
        clauses, args = [], []
        if code: clauses.append('code=?'); args.append(code)
        if task_id: clauses.append('task_id=?'); args.append(task_id)
        where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
        with self.connect() as con:
            total = con.execute('SELECT COUNT(*) FROM issues'+where, args).fetchone()[0]
            total_pages=max(1,(total+page_size-1)//page_size); page=min(page,total_pages)
            items = [dict(r) for r in con.execute('SELECT * FROM issues'+where+' ORDER BY path,line LIMIT ? OFFSET ?', args+[page_size,(page-1)*page_size])]
        return dict(items=items,total=total,page=page,page_size=page_size,total_pages=total_pages)

    def activity_page(self, task_id, turn_id, page=1, page_size=50, event_type='', failed=None):
        clauses=['task_id=?','turn_id=?']; args=[task_id,turn_id]
        if event_type: clauses.append('type=?'); args.append(event_type)
        if failed is not None: clauses.append('failed=?'); args.append(int(failed))
        where=' WHERE '+' AND '.join(clauses)
        with self.connect() as con:
            total=con.execute('SELECT COUNT(*) FROM activity_events'+where,args).fetchone()[0]
            total_pages=max(1,(total+page_size-1)//page_size); page=min(page,total_pages)
            items=[json.loads(r[0]) for r in con.execute('SELECT data FROM activity_events'+where+' ORDER BY timestamp,source_line LIMIT ? OFFSET ?',args+[page_size,(page-1)*page_size])]
        from .activity import merge_work_steps
        return dict(items=items,steps=merge_work_steps(items),total=total,page=page,page_size=page_size,total_pages=total_pages)

    def task_source_paths(self, task_id, turn_id=''):
        clauses=['task_id=?']; args=[task_id]
        if turn_id:
            clauses.append("json_extract(data,'$.turn_id')=?"); args.append(turn_id)
        with self.connect() as con:
            return {r[0] for r in con.execute("SELECT DISTINCT json_extract(data,'$.source_path') FROM ledger WHERE "+' AND '.join(clauses),args) if r[0]}

    def tool_aggregate(self, task_id='', tool='', failed=None, start='', end='', project='', model='', codex_project='', window=''):
        clauses=["type IN ('tool','memory')"]; args=[]
        if task_id: clauses.append('task_id=?'); args.append(task_id)
        if tool: clauses.append('tool=?'); args.append(tool)
        if failed is not None: clauses.append('failed=?'); args.append(int(failed))
        if start: clauses.append("date(timestamp,'+8 hours')>=?"); args.append(start)
        if end: clauses.append("date(timestamp,'+8 hours')<=?"); args.append(end)
        if project: clauses.append("json_extract(tasks.data,'$.project')=?"); args.append(project)
        if codex_project: clauses.append("json_extract(tasks.data,'$.codex_project')=?"); args.append(codex_project)
        if window: clauses.append("json_extract(tasks.data,'$.window_name')=?"); args.append(window)
        if model: clauses.append("json_extract(activity_events.data,'$.model')=?"); args.append(model)
        with self.connect() as con:
            rows=con.execute('SELECT COALESCE(tool,type) name,COUNT(*) calls,SUM(failed) failures,SUM(COALESCE(json_extract(activity_events.data,"$.associated_tokens"),0)) associated_tokens FROM activity_events LEFT JOIN tasks ON tasks.id=activity_events.task_id WHERE '+' AND '.join(clauses)+' GROUP BY name ORDER BY associated_tokens DESC,calls DESC',args)
            return [dict(r) for r in rows]

    def rate_limit_stats(self, start='', end=''):
        """Count explicit account limit transitions; never infer them from token volume."""
        with self.connect() as con:
            values=[]
            for row in con.execute("SELECT path,line,data FROM events ORDER BY path,line"):
                event=json.loads(row['data'])
                if event.get('type') not in ('checkpoint','rate_limit_state','rate_limit_event'):
                    continue
                values.append((row['path'],event))
        active={}; events=[]
        for path,event in values:
            limit_id=event.get('limit_id') or 'unknown'
            reached=event.get('limit_type') or ''
            key=(path,limit_id)
            if event.get('type')=='rate_limit_event':
                events.append(event); continue
            if reached and active.get(key)!=reached:
                events.append(event)
            active[key]=reached
        selected=[]
        for event in events:
            stamp=event.get('timestamp')
            if not stamp: continue
            day=datetime.fromisoformat(stamp).astimezone(LOCAL_TZ).date().isoformat()
            if start and day<start: continue
            if end and day>end: continue
            selected.append((event,day))
        by_day=defaultdict(int);by_hour=defaultdict(int);by_type=defaultdict(int)
        for event,day in selected:
            local=datetime.fromisoformat(event['timestamp']).astimezone(LOCAL_TZ)
            by_day[day]+=1;by_hour[f'{local.hour:02d}:00']+=1;by_type[event.get('limit_type') or 'unknown']+=1
        return {'total':len(selected),'by_day':[{'date':k,'count':v} for k,v in sorted(by_day.items())],
                'by_hour':[{'hour':f'{h:02d}:00','count':by_hour.get(f"{h:02d}:00",0)} for h in range(24)],
                'by_type':[{'name':k,'count':v} for k,v in sorted(by_type.items())],
                'evidence':'仅统计日志明确记录的 rate_limit、usage_limit_reached 或 rate_limit_reached_type 状态变化。'}

    def task_retry_stats(self, task_ids=None):
        wanted=set(task_ids) if task_ids is not None else None
        with self.connect() as con:
            rows=[dict(r) for r in con.execute("SELECT task_id,turn_id,tool,failed,source_line FROM activity_events WHERE type IN ('tool','memory') ORDER BY task_id,turn_id,source_line")]
        result=defaultdict(lambda:{'tool_call_count':0,'tool_failure_count':0,'retry_after_failure_count':0})
        previous={}
        for row in rows:
            task=row['task_id']
            if wanted is not None and task not in wanted: continue
            stats=result[task];stats['tool_call_count']+=1;stats['tool_failure_count']+=int(row['failed'] or 0)
            key=(task,row['turn_id']);old=previous.get(key)
            if old and old['failed'] and old['tool']==row['tool']:
                stats['retry_after_failure_count']+=1
            previous[key]=row
        return dict(result)

    def refresh_quota(self):
        value=read_quota()
        with self.connect() as con:
            old=con.execute('SELECT data FROM quota WHERE id=1').fetchone()
            previous=json.loads(old[0]) if old else {}
            if value['status']!='ok' and previous.get('last_success'):
                value['last_success']=previous['last_success']
            elif value['status']=='ok':
                value['last_success']={'fetched_at':value['fetched_at'],'limits':value['limits']}
            con.execute('INSERT OR REPLACE INTO quota VALUES (1,?)',(json.dumps(value),))
        return value

    def quota_status(self):
        with self.connect() as con:
            row=con.execute('SELECT data FROM quota WHERE id=1').fetchone()
        value=json.loads(row[0]) if row else {'status':'unavailable','message':'尚未取得额度数据。'}
        value['forecast']=quota_forecast(value)
        return value

    def task_runtime(self, task_id):
        with self.connect() as con:
            rows=[json.loads(r[0]) for r in con.execute('SELECT data FROM activity_events WHERE task_id=? ORDER BY timestamp,source_line',(task_id,))]
        last=rows[-1].get('timestamp') if rows else None
        compactions=[r.get('timestamp') for r in rows if r.get('type')=='compaction' and r.get('timestamp')]
        starts=[r for r in rows if r.get('type')=='start' and r.get('context_window')]
        models=[r for r in rows if r.get('model')]
        calls=[r for r in rows if r.get('type')=='model' and r.get('input_tokens') is not None]
        pressure=(calls[-1]['input_tokens']/starts[-1]['context_window']) if calls and starts else None
        return {'recent_activity_at':last,'last_compaction_at':compactions[-1] if compactions else None,
                'context_window':starts[-1]['context_window'] if starts else None,'context_pressure':pressure,
                'service_tier':next((r.get('service_tier') for r in reversed(models) if r.get('service_tier')),None) or 'unknown'}
