"""Read numeric Codex rate limits through app-server; never inspect auth files."""
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path


def quota_forecast(value, now=None):
    """Linear forecast for the longest available quota window."""
    if not isinstance(value, dict):
        return {'status':'unavailable','message':'没有可用的额度快照。'}
    stale=value.get('status') != 'ok'
    if stale and isinstance(value.get('last_success'),dict):
        value={'status':'ok',**value['last_success']}
    elif value.get('status') != 'ok':
        return {'status':'unavailable','message':'没有可用的额度快照。'}
    candidates=[]
    for limit in value.get('limits') or []:
        for name in ('primary','secondary'):
            window=limit.get(name)
            if not isinstance(window,dict): continue
            duration=window.get('windowDurationMins')
            used=window.get('usedPercent')
            reset=window.get('resetsAt')
            if isinstance(duration,(int,float)) and isinstance(used,(int,float)) and isinstance(reset,(int,float)):
                candidates.append((duration,limit,name,window))
    if not candidates:
        return {'status':'unavailable','message':'额度快照缺少窗口比例或重置时间。'}
    duration,limit,name,window=max(candidates,key=lambda item:item[0])
    current=now or datetime.now(timezone.utc)
    if isinstance(current,str): current=datetime.fromisoformat(current.replace('Z','+00:00'))
    sample=datetime.fromisoformat(str(value.get('fetched_at')).replace('Z','+00:00')) if value.get('fetched_at') else current
    reset=datetime.fromtimestamp(window['resetsAt'],timezone.utc)
    start=reset-timedelta(minutes=duration)
    if reset <= current:
        return {'status':'expired','used_percent':float(window['usedPercent']),'reset_at':reset.isoformat(),
                'fetched_at':value.get('fetched_at'),'stale':True,'message':'最后额度快照所属周期已经结束，请刷新额度。'}
    elapsed=max(0,(sample-start).total_seconds())
    total=max(1,duration*60)
    used=max(0,min(100,float(window['usedPercent'])))
    rate=used/elapsed if elapsed and used else 0
    exhaustion=sample+timedelta(seconds=(100-used)/rate) if rate else None
    within=bool(exhaustion and current < exhaustion < reset)
    return {'status':'ok','limit_id':limit.get('limit_id'),'limit_name':limit.get('limit_name'),
            'window':name,'used_percent':used,'window_start':start.isoformat(),
            'reset_at':reset.isoformat(),'fetched_at':value.get('fetched_at'),
            'elapsed_days':elapsed/86400,'daily_percent':rate*86400,
            'projected_reset_percent':min(100,rate*total) if rate else used,
            'exhaustion_at':exhaustion.isoformat() if within else None,
            'days_remaining':(exhaustion-current).total_seconds()/86400 if within else None,
            'will_exhaust_before_reset':within,'stale':stale,
            'message':'按本周期截至当前的平均使用速度线性估算；实际用量变化会改变结果。'}


class _JsonLineReader:
    """Read app-server stdout without relying on select(), which cannot poll pipes on Windows."""
    def __init__(self, stream):
        self.values = queue.Queue()
        self.thread = threading.Thread(target=self._run, args=(stream,), daemon=True)
        self.thread.start()

    def _run(self, stream):
        try:
            for raw in iter(stream.readline, b''):
                try:
                    self.values.put(json.loads(raw))
                except (ValueError, TypeError, UnicodeDecodeError):
                    continue
        finally:
            self.values.put(None)

    def read(self, request_id, timeout):
        end=time.monotonic()+timeout
        while True:
            remaining=end-time.monotonic()
            if remaining<=0:
                raise TimeoutError('app-server 响应超时')
            try:
                value=self.values.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError('app-server 响应超时')
            if value is None:
                raise RuntimeError('app-server 已退出')
            if isinstance(value,dict) and value.get('id')==request_id:
                return value


def _bundled_codex_paths():
    if sys.platform != 'darwin':
        return ()
    relative = Path('Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex')
    apps = ('ChatGPT.app', 'Codex.app')
    return tuple(root / app / relative for root in (Path('/Applications'), Path.home() / 'Applications') for app in apps)


def _codex_command(system=None, executable=None):
    system = system or os.name
    executable = executable or shutil.which('codex')
    if not executable:
        executable = next((str(path) for path in _bundled_codex_paths()
                           if path.is_file() and os.access(path, os.X_OK)), None)
    if not executable:
        raise FileNotFoundError('未找到 codex 命令，请先安装 Codex CLI 并加入 PATH')
    if system == 'nt' and executable.lower().endswith(('.cmd','.bat')):
        comspec=os.environ.get('COMSPEC') or 'cmd.exe'
        command=subprocess.list2cmdline([executable,'app-server','--stdio'])
        return [comspec,'/d','/s','/c',command]
    return [executable,'app-server','--stdio']


def _read(reader, request_id, timeout):
    return reader.read(request_id, timeout)


def read_quota(timeout=4):
    proc=None
    try:
        proc=subprocess.Popen(_codex_command(),stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL,bufsize=0)
        reader=_JsonLineReader(proc.stdout)
        def send(value):
            proc.stdin.write((json.dumps(value,separators=(',',':'))+'\n').encode());proc.stdin.flush()
        send({'id':1,'method':'initialize','params':{'clientInfo':{'name':'codex-token-analyzer','version':'1.0'}}})
        initialized=_read(reader,1,timeout/2)
        if initialized.get('error'): raise RuntimeError('app-server 初始化失败')
        send({'id':2,'method':'account/rateLimits/read','params':None})
        reply=_read(reader,2,timeout/2)
        if reply.get('error'): raise RuntimeError('额度接口返回错误')
        result=reply.get('result') or {}
        def window(value):
            if not isinstance(value,dict): return None
            return {k:value.get(k) for k in ('usedPercent','windowDurationMins','resetsAt')}
        snapshots=result.get('rateLimitsByLimitId') or {'codex':result.get('rateLimits')}
        limits=[]
        for key,value in snapshots.items():
            if not isinstance(value,dict): continue
            limits.append({'limit_id':value.get('limitId') or key,'limit_name':value.get('limitName'),
                           'primary':window(value.get('primary')),'secondary':window(value.get('secondary'))})
        return {'status':'ok','fetched_at':datetime.now(timezone.utc).isoformat(),'limits':limits}
    except Exception as exc:
        return {'status':'unavailable','checked_at':datetime.now(timezone.utc).isoformat(),'message':str(exc)[:180]}
    finally:
        if proc is not None:
            proc.terminate()
            try: proc.wait(timeout=.5)
            except subprocess.TimeoutExpired: proc.kill()
