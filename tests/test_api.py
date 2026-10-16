import csv
import io
import json
import pytest
from fastapi.testclient import TestClient
from analyzer.app import create_app
from conftest import meta,context,call,u,event


@pytest.fixture
def client(fixture_store):
    s,write=fixture_store
    write([meta(),context(),call()])
    write([meta('b'),context('bt'),call('b',task='b',turn='bt',usage=u(300,120,30,9))],name='b.jsonl')
    assert s.sync()
    with TestClient(create_app(s,background=False)) as client:
        yield client


def test_endpoints_and_csv_agree(client):
    assert client.get('/openapi.json').json()['info']['title']=='Coco Codex管家'
    query='?start=2026-09-09&end=2026-09-09&model=test-model'
    overview=client.get('/api/overview'+query).json()
    tasks=client.get('/api/tasks'+query).json()['items']
    csv_rows=list(csv.DictReader(io.StringIO(client.get('/api/export.csv'+query).text.lstrip('\ufeff'))))
    assert overview['metrics']['total_tokens']==sum(t['total_tokens'] for t in tasks)==sum(int(r['total_tokens']) for r in csv_rows)==440
    assert sum(d['total_tokens'] for d in overview['daily'])==440
    detail=client.get('/api/tasks/t').json()
    assert detail['task']['total_tokens']==sum(t['total_tokens'] for t in detail['turns'])==110
    assert 'calls' not in detail['turns'][0] and 'activity' not in detail['turns'][0]
    calls_page=client.get('/api/tasks/t/turns/turn/calls').json()
    activity=client.get('/api/tasks/t/turns/turn/activity').json()
    assert calls_page['total']==1 and [e['type'] for e in activity['items']]==['context','model']
    calls=list(csv.DictReader(io.StringIO(client.get('/api/export.csv?scope=calls&task_id=t').text.lstrip('\ufeff'))))
    assert len(calls)==1 and calls[0]['total_tokens']=='110'


def test_price_edit_delete_and_partial_coverage(client):
    price=dict(model='test-model',currency='USD',effective_from='2026-09-09',input_per_million=2,cached_input_per_million=1,output_per_million=10,cache_write_per_million=None,source='manual test')
    r=client.put('/api/prices',json=price);assert r.status_code==200
    m=client.get('/api/overview').json()['metrics'];assert m['pricing_coverage']==1 and m['api_costs']['USD']==pytest.approx(0.00104)
    price['input_per_million']=4
    assert client.put('/api/prices',json=price).json()['id']==r.json()['id']
    assert client.delete('/api/prices/'+str(r.json()['id'])).status_code==204
    assert client.get('/api/overview').json()['metrics']['pricing_coverage']==0


def test_subscription_filter_does_not_reallocate(client):
    assert client.put('/api/subscriptions',json={'month':'2026-09','amount':200,'currency':'CNY'}).status_code==200
    assert client.get('/api/tasks/t').json()['task']['subscription_costs']=={'CNY':50}
    assert client.get('/api/overview?search=任务%20t').json()['metrics']['subscription_costs']=={'CNY':50}


def test_status_is_slim_and_tasks_paginate(client):
    status=client.get('/api/status')
    assert status.status_code==200 and len(status.content)<20_000
    body=status.json()
    assert isinstance(body['data_revision'],int) and 'issues' not in body
    tasks=client.get('/api/tasks?page=2&page_size=1').json()
    assert tasks['page']==2 and tasks['page_size']==1 and tasks['total_pages']==2 and len(tasks['items'])==1
    overflow=client.get('/api/tasks?page=9999&page_size=1').json()
    assert overflow['page']==overflow['total_pages']==2 and len(overflow['items'])==1
    assert client.get('/api/issues?page_size=1').json()['page_size']==1
    options=client.get('/api/options').json()
    assert {'projects','codex_projects','windows','models'} <= options.keys()


def test_analysis_pagination_and_sorted_csv(client):
    analysis=client.get('/api/analysis?call_page=9999&call_page_size=5&signal_page_size=5').json()
    assert analysis['high_calls_page']['page']==analysis['high_calls_page']['total_pages']
    assert len(analysis['high_calls'])<=5
    rows=list(csv.DictReader(io.StringIO(client.get('/api/export.csv?sort=tokens&direction=asc').text.lstrip('\ufeff'))))
    assert [int(row['total_tokens']) for row in rows]==sorted(int(row['total_tokens']) for row in rows)


def test_tools_budgets_snapshot_and_period_comparison(client):
    tools=client.get('/api/tools').json()
    assert 'causality_note' in tools
    budget={'scope':'day','metric':'tokens','limit':1000,'project':None,'currency':None}
    saved=client.put('/api/budgets',json=budget)
    assert saved.status_code==200
    assert client.get('/api/settings').json()['budgets'][0]['limit']==1000
    assert client.delete('/api/budgets/'+str(saved.json()['id'])).status_code==204
    overview=client.get('/api/overview').json()
    assert 'previous' in overview['today'] and 'token_change' in overview['week'] and 'calendar' in overview
    assert overview['quota']['status']=='unavailable'
    assert client.get('/api/quota').json()['status']=='unavailable'
    response=client.get('/api/export.snapshot.json')
    assert response.headers['content-disposition']=='attachment; filename="coco-codex-manager-snapshot.json"'
    snap=response.json()
    assert set(snap)=={'format','version','generated_at','timezone','filters','metrics','daily','by_model','by_project'}
    assert (snap['format'],snap['version'])==('codex-token-snapshot',1)


def test_activity_index_privacy_and_revision_idempotence(fixture_store):
    store,write=fixture_store
    secret='DO_NOT_PERSIST_FULL_CONVERSATION_42'
    write([meta(),event('event_msg',{'type':'task_started','turn_id':'turn'}),context(),
           event('response_item',{'type':'message','role':'user','content':[{'text':secret}]}),
           event('response_item',{'type':'custom_tool_call','name':'exec','call_id':'x','input':'{"cmd":"cat secret.txt"}'}),
           event('response_item',{'type':'custom_tool_call_output','call_id':'x','output':secret}),call()])
    assert store.sync()
    first=store.status()['data_revision']
    assert store.sync() and store.status()['data_revision']==first
    with store.connect() as con:
        indexed=' '.join(r[0] for r in con.execute('SELECT data FROM activity_events'))
    assert secret not in indexed and 'cat secret.txt' not in indexed


@pytest.mark.parametrize('path,body',[
    ('/api/subscriptions',{'month':'2026-13','amount':1,'currency':'CNY'}),
    ('/api/subscriptions',{'month':'2026-09','amount':-1,'currency':'CNY'}),
    ('/api/subscriptions',{'month':'2026-09','amount':1,'currency':'bad'}),
    ('/api/settings',{'source_root':'/not-a-codex-root'}),
])
def test_invalid_settings_rejected(client,path,body):
    assert client.put(path,json=body).status_code==422


def test_validation_and_local_boundaries(client):
    assert client.get('/api/overview?start=2026-10-01&end=2026-01-01').status_code==422
    assert client.get('/api/tasks/nonexistent').status_code==404
    assert client.post('/api/sync',headers={'origin':'https://evil.example'}).status_code==403
    assert client.get('/api/overview',headers={'host':'evil.example'}).status_code==400


def test_csv_formula_escape(fixture_store):
    s,write=fixture_store;write([meta(),context(),call()]);assert s.sync()
    with s.connect() as con:
        task=json.loads(con.execute("SELECT data FROM tasks WHERE id='t'").fetchone()[0]);task['title']='=1+1'
        con.execute('UPDATE tasks SET data=? WHERE id=?',(json.dumps(task),'t'))
    with TestClient(create_app(s,background=False)) as c:
        rows=list(csv.DictReader(io.StringIO(c.get('/api/export.csv').text.lstrip('\ufeff'))))
        assert rows[0]['title']=="'=1+1"


def test_search_special_characters_returns_safe_empty_result(client):
    result=client.get('/api/tasks',params={'search':'%_<script>'})
    assert result.status_code==200
    assert result.json()['total']==0 and result.json()['items']==[]


def test_new_analysis_dimensions_and_reports(client):
    overview=client.get('/api/overview').json()
    assert len(overview['temporal_heatmap']['cells'])==168
    assert overview['weekly_comparison']
    analysis=client.get('/api/analysis').json()
    assert {'anomalies','task_efficiency','model_comparison'} <= analysis.keys()
    assert analysis['task_efficiency'][0]['turn_count']>=1
    report=client.get('/api/export.report.json')
    assert report.status_code==200
    assert report.headers['content-disposition']=='attachment; filename="coco-codex-manager-report.json"'
    payload=report.json()
    assert set(payload)=={'format','version','generated_at','timezone','filters','metrics','weekly_comparison','anomalies','model_comparison','top_tasks','data_notes'}
    assert (payload['format'],payload['version'])==('codex-token-weekly-report',1)
    markdown=client.get('/api/export.report.md')
    assert markdown.status_code==200
    assert markdown.headers['content-disposition']=='attachment; filename="coco-codex-manager-report.md"'
    assert markdown.text.startswith('# Coco Codex管家用量报告\n')


def test_multiple_local_sources_merge_without_duplicate_sync(tmp_path):
    from analyzer.store import Store
    roots=[tmp_path/'first',tmp_path/'second']
    for root in roots:(root/'sessions').mkdir(parents=True)
    (roots[0]/'sessions'/'a.jsonl').write_text('\n'.join(json.dumps(v) for v in [meta(),context(),call()])+'\n')
    (roots[1]/'sessions'/'b.jsonl').write_text('\n'.join(json.dumps(v) for v in [meta('b'),context('bt'),call('rb',task='b',turn='bt')])+'\n')
    store=Store(tmp_path/'data.sqlite',roots[0]);store.set_roots([str(v) for v in roots])
    assert store.sync()
    assert len(store.source_roots)==2 and sum(r['total_tokens'] for r in store.snapshot()['rows'])==220
    revision=store.status()['data_revision']
    assert store.sync() and store.status()['data_revision']==revision
