import asyncio
import csv
import io
import json
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .analytics import Analytics
from .activity import merge_work_steps, read_turn_activity
from .store import Store

BASE = Path(__file__).resolve().parent.parent


class Price(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: Optional[int] = None
    model: str = Field(min_length=1,max_length=150)
    currency: str = Field(pattern=r'^[A-Z]{3}$')
    effective_from: date
    input_per_million: float = Field(ge=0,le=1e9,allow_inf_nan=False)
    cached_input_per_million: float = Field(ge=0,le=1e9,allow_inf_nan=False)
    output_per_million: float = Field(ge=0,le=1e9,allow_inf_nan=False)
    reasoning_output_per_million: Optional[float] = Field(default=None,ge=0,le=1e9,allow_inf_nan=False)
    cache_write_per_million: Optional[float] = Field(default=None,ge=0,le=1e9,allow_inf_nan=False)
    context_limit: Optional[int] = Field(default=None,ge=1)
    long_input_per_million: Optional[float] = Field(default=None,ge=0,le=1e9,allow_inf_nan=False)
    long_cached_input_per_million: Optional[float] = Field(default=None,ge=0,le=1e9,allow_inf_nan=False)
    long_output_per_million: Optional[float] = Field(default=None,ge=0,le=1e9,allow_inf_nan=False)
    long_reasoning_output_per_million: Optional[float] = Field(default=None,ge=0,le=1e9,allow_inf_nan=False)
    long_cache_write_per_million: Optional[float] = Field(default=None,ge=0,le=1e9,allow_inf_nan=False)
    source: str = Field(min_length=1,max_length=2000)


class Subscription(BaseModel):
    month: str = Field(pattern=r'^\d{4}-\d{2}$')
    amount: float = Field(ge=0,le=1e12,allow_inf_nan=False)
    currency: str = Field(pattern=r'^[A-Z]{3}$')

    @field_validator('month')
    @classmethod
    def valid_month(cls, value):
        date.fromisoformat(value+'-01')
        return value


class Source(BaseModel):
    source_root: Optional[str] = Field(default=None,min_length=1,max_length=4096)
    source_roots: Optional[list[str]] = Field(default=None,min_length=1,max_length=20)

class Budget(BaseModel):
    id: Optional[int] = None
    scope: str = Field(pattern='^(day|month|project)$')
    metric: str = Field(pattern='^(tokens|api_cost)$')
    limit: float = Field(gt=0,allow_inf_nan=False)
    project: Optional[str] = Field(default=None,max_length=4096)
    currency: Optional[str] = Field(default=None,pattern=r'^[A-Z]{3}$')


def filters(start:Optional[date]=None,end:Optional[date]=None,project:str='',codex_project:str='',window:str='',model:str='',search:str=''):
    if start and end and start>end:
        raise HTTPException(422,'开始日期不能晚于结束日期')
    return dict(start=start.isoformat() if start else '',end=end.isoformat() if end else '',project=project,codex_project=codex_project,window=window,model=model,search=search)


def json_safe(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value,dict):
        return {k:json_safe(v) for k,v in value.items()}
    if isinstance(value,list):
        return [json_safe(v) for v in value]
    return value


def create_app(store=None, background=True):
    privacy_mode = os.environ.get('TOKEN_ANALYZER_PRIVACY_MODE', '').strip().lower() in ('1','true','yes','on')
    store = store or Store(os.environ.get('TOKEN_ANALYZER_DB', str(BASE/'.data'/'analytics.sqlite')),
                            os.environ.get('TOKEN_ANALYZER_SOURCE',str(Path.home()/'.codex')),
                            privacy_mode=privacy_mode)

    def seed_prices():
        seed = BASE/'analyzer'/'prices.json'
        with store.connect() as con:
            initialized=con.execute("SELECT 1 FROM config WHERE key='prices_initialized_v2'").fetchone()
            if not initialized:
                if seed.exists():
                    for p in json.loads(seed.read_text()):
                        existing=[]
                        for row in con.execute('SELECT data FROM prices'):
                            current=json.loads(row[0])
                            if current.get('model')==p['model'] and current.get('effective_from')==p['effective_from']:
                                existing.append(current)
                        if not existing:
                            con.execute('INSERT INTO prices(data) VALUES (?)',(json.dumps(p),))
                con.execute("INSERT INTO config VALUES ('prices_initialized_v2','1')")
    seed_prices()

    @asynccontextmanager
    async def lifespan(app):
        stop = threading.Event()
        def run():
            last_quota=0
            while not stop.is_set():
                store.sync()
                if time.monotonic()-last_quota>=300:
                    store.refresh_quota();last_quota=time.monotonic()
                stop.wait(5)
        worker = threading.Thread(target=run,daemon=True)
        if background:
            worker.start()
        yield
        stop.set()
        if background:
            await asyncio.to_thread(worker.join,10)

    app = FastAPI(title='Codex Token 本地账本',lifespan=lifespan)
    app.state.store = store
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','testserver'])

    @app.middleware('http')
    async def local_boundary(request:Request,call_next):
        origin = request.headers.get('origin')
        if request.method not in ('GET','HEAD','OPTIONS'):
            if request.headers.get('sec-fetch-site')=='cross-site':
                return JSONResponse({'detail':'禁止跨站修改本地数据'},403)
            if origin and urlparse(origin).netloc != request.headers.get('host'):
                return JSONResponse({'detail':'禁止跨源修改本地数据'},403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control']='no-store'
        return response

    def analytics():
        return Analytics(store.snapshot())

    @app.get('/api/status')
    def status():
        return store.status()

    @app.post('/api/quota/refresh',status_code=202)
    def refresh_quota():
        threading.Thread(target=store.refresh_quota,daemon=True).start()
        return {'accepted':True}

    @app.get('/api/issues')
    def issues(page:int=Query(1,ge=1),page_size:int=Query(50,ge=1,le=200),code:str='',task_id:str=''):
        return store.issues_page(page,page_size,code,task_id)

    @app.post('/api/sync',status_code=202)
    def sync():
        if not store.state['syncing']:
            threading.Thread(target=store.sync,daemon=True).start()
        return {'accepted':True}

    @app.get('/api/options')
    def options():
        snap=store.snapshot()
        tasks=list(snap['tasks'].values())
        return dict(projects=sorted({v['project'] for v in tasks}),
                    codex_projects=[{'name':name,'project':project} for project,name in sorted({(v['project'],v.get('codex_project') or '未加入 Codex 项目') for v in tasks})],
                    windows=[{'name':name,'codex_project':codex_project,'project':project} for project,codex_project,name in sorted({(v['project'],v.get('codex_project') or '未加入 Codex 项目',v.get('window_name') or v['title']) for v in tasks})],
                    models=sorted({r['model'] or '未知' for r in snap['rows']}))

    @app.get('/api/overview')
    def overview(f=Depends(filters)):
        return json_safe(analytics().overview(f))

    @app.get('/api/tasks')
    def tasks(f=Depends(filters),sort:str=Query('tokens',pattern='^(tokens|time|cost)$'),direction:str=Query('desc',pattern='^(asc|desc)$'),page:int=Query(1,ge=1),page_size:int=Query(25,ge=1,le=100)):
        return json_safe(analytics().task_list(f,sort,direction,page,page_size))

    @app.get('/api/tasks/{task_id}')
    def detail(task_id:str,f=Depends(filters),include_children:bool=False):
        try:
            result=analytics().detail(task_id,f,include_children,False)
            runtime=store.task_runtime(task_id)
            runtime.update(store.task_retry_stats({task_id}).get(task_id,{'tool_call_count':0,'tool_failure_count':0,'retry_after_failure_count':0}))
            recent=[]
            cutoff=datetime.now().astimezone().timestamp()-900
            for row in analytics().select(f,{task_id}):
                try:
                    if row.get('timestamp') and datetime.fromisoformat(row['timestamp']).timestamp()>=cutoff: recent.append(row)
                except ValueError: pass
            runtime['tokens_last_15m']=sum(r['total_tokens'] for r in recent)
            runtime['tokens_per_minute']=runtime['tokens_last_15m']/15
            runtime['projected_hour_tokens']=runtime['tokens_per_minute']*60
            result['runtime']=runtime
            return json_safe(result)
        except KeyError:
            raise HTTPException(404,'未找到此任务')

    @app.get('/api/tasks/{task_id}/turns/{turn_id}/calls')
    def turn_calls(task_id:str,turn_id:str,f=Depends(filters),page:int=Query(1,ge=1),page_size:int=Query(50,ge=1,le=200)):
        return json_safe(analytics().calls(task_id,turn_id,f,page,page_size))

    @app.get('/api/tasks/{task_id}/turns/{turn_id}/activity')
    def turn_activity(task_id:str,turn_id:str,page:int=Query(1,ge=1),page_size:int=Query(50,ge=1,le=200),event_type:str='',failed:Optional[bool]=None):
        paths=store.task_source_paths(task_id,turn_id)
        activity={'timeline':[]};request_excerpt=None
        for root in store.source_roots:
            selected={path for path in paths if root in Path(path).resolve().parents}
            part=read_turn_activity(selected,root,{turn_id}).get(turn_id,{'timeline':[]})
            activity['timeline'].extend(part.get('timeline') or [])
            request_excerpt=request_excerpt or part.get('request_excerpt')
        activity['timeline'].sort(key=lambda item:(item.get('timestamp') or '',item.get('source_line') or 0))
        activity['request_excerpt']=request_excerpt
        rich=activity['timeline']
        if event_type:
            rich=[event for event in rich if event.get('type')==event_type]
        if failed is not None:
            rich=[event for event in rich if (event.get('status')=='返回错误')==failed]
        total=len(rich); total_pages=max(1,(total+page_size-1)//page_size); page=min(page,total_pages); offset=(page-1)*page_size; items=rich[offset:offset+page_size]
        return dict(items=items,steps=merge_work_steps(items),total=total,page=page,page_size=page_size,
                    total_pages=total_pages,
                    request_excerpt=activity.get('request_excerpt'),persisted=False)

    @app.get('/api/tools')
    def tools(f=Depends(filters),task_id:str='',tool_category:str='',failed:Optional[bool]=None):
        return {'items':store.tool_aggregate(task_id,tool_category,failed,f['start'],f['end'],f['project'],f['model'],f['codex_project'],f['window']),'causality_note':'关联 Token 表示工具调用之后记录的模型调用用量，仅说明时间相关性，不代表因果。'}

    @app.get('/api/analysis')
    def analysis(f=Depends(filters),call_page:int=Query(1,ge=1),call_page_size:int=Query(10,ge=5,le=50),signal_page:int=Query(1,ge=1),signal_page_size:int=Query(10,ge=5,le=50)):
        result=analytics().analysis(f,call_page,call_page_size,signal_page,signal_page_size)
        result['rate_limits']=store.rate_limit_stats(f['start'],f['end'])
        retry=store.task_retry_stats({item['id'] for item in result['task_efficiency']})
        for item in result['task_efficiency']:
            item.update(retry.get(item['id'],{'tool_call_count':0,'tool_failure_count':0,'retry_after_failure_count':0}))
        return json_safe(result)

    @app.get('/api/rate-limits')
    def rate_limits(f=Depends(filters)):
        return store.rate_limit_stats(f['start'],f['end'])

    @app.get('/api/export.csv')
    def export(f=Depends(filters),scope:str=Query('tasks',pattern='^(tasks|calls)$'),task_id:Optional[str]=None,include_children:bool=False,sort:str=Query('tokens',pattern='^(tokens|time|cost)$'),direction:str=Query('desc',pattern='^(asc|desc)$'),page:Optional[int]=Query(None,ge=1),page_size:int=Query(25,ge=1,le=100)):
        a=analytics()
        ids=a.descendants(task_id) if task_id and include_children else {task_id} if task_id else None
        if scope=='calls':
            rows=a.select(f,ids)
            fields=['id','task_id','turn_id','title','timestamp','model','kind','input_tokens','cached_input_tokens','cache_write_input_tokens','output_tokens','reasoning_output_tokens','visible_output_tokens','total_tokens','api_costs','api_cost_breakdown','cache_savings','cache_savings_coverage','subscription_costs','pricing_coverage','interval_start','interval_end','source_path','source_line']
        else:
            rows=a.task_list(f,sort,direction,page or 1,page_size if page is not None else 100000)['items']
            tools_by_task={}
            for task in rows:
                grouped=store.tool_aggregate(task_id=task['id'])
                tools_by_task[task['id']]=(sum(x['calls'] for x in grouped),sum(x['failures'] for x in grouped))
                task['tool_count'],task['tool_failure_count']=tools_by_task[task['id']]
                task['non_cached_input_tokens']=(task['input_tokens']-task['cached_input_tokens']) if task.get('input_tokens') is not None and task.get('cached_input_tokens') is not None else None
            fields=['id','window_name','title','project','codex_project','model','updated_at','input_tokens','cached_input_tokens','non_cached_input_tokens','output_tokens','reasoning_output_tokens','visible_output_tokens','total_tokens','call_count','tool_count','tool_failure_count','summary_tokens','undated_tokens','cache_hit_rate','detail_coverage','api_costs','api_cost_breakdown','cache_savings','cache_savings_coverage','subscription_costs','pricing_coverage']
        stream=io.StringIO(newline='')
        writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            clean={}
            for key in fields:
                value=row.get(key)
                if isinstance(value,dict):
                    value=json.dumps(json_safe(value),ensure_ascii=False,sort_keys=True)
                if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')):
                    value="'"+value
                clean[key]=value
            writer.writerow(clean)
        return Response('\ufeff'+stream.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="codex-{scope}.csv"'})

    @app.get('/api/settings')
    def settings():
        snap=store.snapshot()
        with store.connect() as con:
            budgets=[dict(json.loads(r['data']),id=r['id']) for r in con.execute('SELECT * FROM budgets')]
            quota=con.execute('SELECT data FROM quota WHERE id=1').fetchone()
        a=Analytics(snap); today=date.today(); budget_status=[]
        for budget in budgets:
            if budget['scope']=='day': selected=a.select({'start':today.isoformat(),'end':today.isoformat()})
            elif budget['scope']=='month': selected=a.select({'start':today.replace(day=1).isoformat(),'end':today.isoformat()})
            else: selected=a.select({'project':budget.get('project') or ''})
            total=a.aggregate(selected)
            used=total['total_tokens'] if budget['metric']=='tokens' else float(total['api_costs'].get(budget.get('currency') or '',0))
            budget_status.append(dict(budget,used=used,ratio=used/budget['limit'],exceeded=used>budget['limit']))
        return dict(source_root=str(store.source_root),source_roots=[str(v) for v in store.source_roots],prices=snap['prices'],subscriptions=snap['subscriptions'],
                    budgets=budgets,budget_status=budget_status,quota=store.quota_status(),
                    pricing_note='Standard API 等价估算，非订阅账单。价格按模型与生效日匹配；推理费率留空时继承输出费率，且推理 Token 只作为输出子项计价一次。未知模型、时间或计价分项不计价。')

    @app.put('/api/settings')
    def set_settings(source:Source):
        try:
            roots=source.source_roots or ([source.source_root] if source.source_root else [])
            store.set_roots(roots)
        except ValueError as e:
            raise HTTPException(422,str(e))
        threading.Thread(target=store.sync,daemon=True).start()
        return settings()

    @app.put('/api/prices')
    def put_price(price:Price):
        p=price.model_dump(mode='json',exclude={'id'})
        with store.connect() as con:
            candidates=[(r['id'],json.loads(r['data'])) for r in con.execute('SELECT * FROM prices')]
            existing=next((key for key,v in candidates if v['model']==p['model'] and v['effective_from']==p['effective_from']),None)
            if price.id is not None:
                if not any(key==price.id for key,_ in candidates):
                    raise HTTPException(404,'价格记录不存在')
                if existing is not None and existing!=price.id:
                    raise HTTPException(409,'相同模型和生效日已有价格')
                existing=price.id
            if existing is None:
                existing=con.execute('INSERT INTO prices(data) VALUES (?)',(json.dumps(p),)).lastrowid
            else:
                con.execute('UPDATE prices SET data=? WHERE id=?',(json.dumps(p),existing))
        return dict(p,id=existing)

    @app.delete('/api/prices/{price_id}',status_code=204)
    def delete_price(price_id:int):
        with store.connect() as con:
            con.execute('DELETE FROM prices WHERE id=?',(price_id,))

    @app.put('/api/subscriptions')
    def put_subscription(subscription:Subscription):
        s=subscription.model_dump()
        with store.connect() as con:
            con.execute('INSERT OR REPLACE INTO subscriptions VALUES (?,?)',(s['month'],json.dumps(s)))
        return s

    @app.delete('/api/subscriptions/{month}',status_code=204)
    def delete_subscription(month:str):
        with store.connect() as con:
            con.execute('DELETE FROM subscriptions WHERE month=?',(month,))

    @app.put('/api/budgets')
    def put_budget(budget:Budget):
        data=budget.model_dump(exclude={'id'})
        if data['scope']=='project' and not data.get('project'):
            raise HTTPException(422,'项目预算必须选择项目')
        with store.connect() as con:
            if budget.id is None: key=con.execute('INSERT INTO budgets(data) VALUES (?)',(json.dumps(data),)).lastrowid
            else:
                if not con.execute('SELECT 1 FROM budgets WHERE id=?',(budget.id,)).fetchone(): raise HTTPException(404,'预算不存在')
                con.execute('UPDATE budgets SET data=? WHERE id=?',(json.dumps(data),budget.id)); key=budget.id
        return dict(data,id=key)

    @app.delete('/api/budgets/{budget_id}',status_code=204)
    def delete_budget(budget_id:int):
        with store.connect() as con: con.execute('DELETE FROM budgets WHERE id=?',(budget_id,))

    @app.get('/api/export.snapshot.json')
    def export_snapshot(f=Depends(filters)):
        view=json_safe(analytics().overview(f))
        payload={'format':'codex-token-snapshot','version':1,'generated_at':datetime.now().astimezone().isoformat(),'timezone':'Asia/Shanghai','filters':f,'metrics':view['metrics'],'daily':view['daily'],'by_model':view['by_model'],'by_project':view['by_project']}
        return JSONResponse(payload,headers={'Content-Disposition':'attachment; filename="codex-token-snapshot.json"'})

    def report_payload(f):
        a=analytics();view=a.analysis(f,1,10,1,10)
        return json_safe({'format':'codex-token-weekly-report','version':1,
            'generated_at':datetime.now().astimezone().isoformat(),'timezone':'Asia/Shanghai','filters':f,
            'metrics':view['metrics'],'weekly_comparison':view['weekly_comparison'],
            'anomalies':view['anomalies'],'model_comparison':view['model_comparison'],
            'top_tasks':view['high_tasks'],'data_notes':[
                '推理 Token 包含在输出 Token 中。','历史汇总不按小时分配。',
                '模型横向指标只描述用量与费用，不代表任务完成质量。']})

    @app.get('/api/export.report.json')
    def export_report_json(f=Depends(filters)):
        return JSONResponse(report_payload(f),headers={'Content-Disposition':'attachment; filename="codex-token-report.json"'})

    @app.get('/api/export.report.md')
    def export_report_markdown(f=Depends(filters)):
        report=report_payload(f);m=report['metrics']
        def safe(value): return str(value or '').replace('|','\\|').replace('\n',' ')
        lines=['# Codex Token 用量报告','',f'- 生成时间：{report["generated_at"]}',
               f'- 时间范围：{f["start"] or "不限"} 至 {f["end"] or "不限"}',
               f'- Token 总量：{m["total_tokens"]:,}',f'- 调用次数：{m["call_count"]:,}',
               f'- 缓存命中率：{m["cache_hit_rate"]:.1%}' if m['cache_hit_rate'] is not None else '- 缓存命中率：未知','',
               '## 本周与上周','', '| 星期 | 本周 | 上周 |','|---|---:|---:|']
        lines += [f'| {x["label"]} | {x["current_tokens"]:,} | {x["previous_tokens"]:,} |' for x in report['weekly_comparison']]
        lines += ['','## 高用量任务','', '| 任务 | Token | 轮次 | 平均每轮 |','|---|---:|---:|---:|']
        lines += [f'| {safe(x["title"])} | {x["total_tokens"]:,} | {x["turn_count"]:,} | {round(x["avg_tokens_per_turn"] or 0):,} |' for x in report['top_tasks']]
        lines += ['','## 异常记录','']
        lines += [f'- {x["kind"]}：{safe(x["id"])}，为中位数的 {x["ratio"]:.1f} 倍。' for x in report['anomalies']] or ['- 未发现符合当前规则的离群点。']
        lines += ['','## 口径说明','']+[f'- {x}' for x in report['data_notes']]
        return Response('\n'.join(lines)+'\n',media_type='text/markdown; charset=utf-8',headers={'Content-Disposition':'attachment; filename="codex-token-report.md"'})

    dist=BASE/'frontend'/'dist'
    if (dist/'assets').is_dir():
        app.mount('/assets',StaticFiles(directory=str(dist/'assets')),name='assets')

    @app.get('/')
    def index():
        if not (dist/'index.html').exists():
            raise HTTPException(503,'请先在 frontend 目录运行 npm ci && npm run build')
        return FileResponse(dist/'index.html')
    return app
