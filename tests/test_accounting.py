import io
import json
from decimal import Decimal
from datetime import datetime
from analyzer.analytics import Analytics,api_cost,api_cost_details
from analyzer.parser import sanitize
from analyzer.store import Store, _safe_title, _project_for_cwd, _fallback_task_title
from analyzer.quota import _JsonLineReader, _codex_command, quota_forecast


def test_state_title_is_bounded_and_generated_history_is_hidden():
    assert _safe_title('A' * 200, 'task-123').endswith('…')
    assert len(_safe_title('A' * 200, 'task-123')) <= 160
    assert _safe_title('The following is the Codex agent history...', 'task-123') == '任务 task-123'


def test_cost_outliers_accept_decimal_values():
    items = [
        {'date': f'2026-09-{day:02d}', 'cost': Decimal(value)}
        for day, value in enumerate(('1', '1', '1', '1', '4'), start=1)
    ]
    outliers = Analytics._outliers(items, 'cost', 'date', 'day_cost')
    assert len(outliers) == 1
    assert outliers[0]['id'] == '2026-09-05'
    assert outliers[0]['ratio'] == 4


def test_privacy_mode_masks_title_and_project(fixture_store):
    original, write = fixture_store
    write([meta(), context(), call()])
    private = Store(original.path, original.source_root, privacy_mode=True)
    assert private.sync()
    task = private.snapshot()['tasks']['t']
    assert task['title'] == '任务 t'
    assert task['window_name'] == '任务 t'
    assert task['project'].startswith('项目 ')
    assert '/project' not in task['project']


def test_codex_project_and_window_metadata_are_imported(fixture_store):
    import sqlite3
    store,write=fixture_store
    write([meta(),context(),call()])
    with sqlite3.connect(store.source_root/'state_5.sqlite') as con:
        con.execute('CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT)')
        con.execute('CREATE TABLE project_roots(project_id TEXT,position INTEGER,path TEXT)')
        con.execute('CREATE TABLE threads(id TEXT PRIMARY KEY,title TEXT,name TEXT,cwd TEXT,updated_at INTEGER,archived INTEGER,project_id TEXT)')
        con.execute('INSERT INTO projects VALUES (?,?)',('p','AI办公日常问题场景'))
        con.execute('INSERT INTO project_roots VALUES (?,?,?)',('p',0,'/project'))
        con.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?)',('t','首条用户任务','梳理AI办公问题并设计方案','/project',1,0,None))
    assert store.sync()
    task=store.snapshot()['tasks']['t']
    assert task['codex_project']=='AI办公日常问题场景'
    assert task['window_name']=='梳理AI办公问题并设计方案'
    assert task['title']=='首条用户任务'
    analytics=Analytics(store.snapshot())
    assert analytics.task_list({'codex_project':'AI办公日常问题场景'})['total']==1
    assert analytics.task_list({'window':'梳理AI办公问题并设计方案'})['items'][0]['id']=='t'


def test_project_root_uses_the_most_specific_ancestor():
    projects = {'parent': '父项目', 'child': '子项目'}
    roots = [('parent', '/work'), ('child', '/work/topic')]
    assert _project_for_cwd('/work/topic/repo', None, projects, roots) == '子项目'


def test_internal_tasks_get_explanatory_titles_and_parent_links(fixture_store):
    store, write = fixture_store
    event = meta(thread_source='guardian_review', parent_thread_id='parent', source={'subagent': {'other': 'guardian'}})
    write([event, context(), call()])
    import sqlite3
    with sqlite3.connect(store.source_root/'state_5.sqlite') as con:
        con.execute('CREATE TABLE threads(id TEXT PRIMARY KEY,title TEXT,name TEXT,cwd TEXT)')
        con.execute('INSERT INTO threads VALUES (?,?,?,?)',(
            't', 'The following is the Codex agent history whose request action you are assessing.', None, '/project'))
    assert store.sync()
    task = store.snapshot()['tasks']['t']
    assert task['title'] == '权限审批审查'
    assert task['window_name'] == '关系待确认的历史任务'
    assert task['hierarchy_status'] == 'unresolved'
    assert task['parent_id'] == 'parent'
    assert _fallback_task_title({'subagent_type': 'thread_spawn'}, 'abcdefghi') == '子代理任务 abcdefgh'


def test_subagent_is_nested_under_real_task_window(fixture_store):
    import sqlite3
    store, write = fixture_store
    write([meta('parent'), context('parent-turn'), call('parent-call', task='parent', turn='parent-turn')], name='parent.jsonl')
    write([meta('child', parent_thread_id='parent'), context('child-turn'), call('child-call', task='child', turn='child-turn')], name='child.jsonl')
    with sqlite3.connect(store.source_root/'state_5.sqlite') as con:
        con.execute('CREATE TABLE threads(id TEXT PRIMARY KEY,title TEXT,name TEXT,cwd TEXT,thread_source TEXT)')
        con.executemany('INSERT INTO threads VALUES (?,?,?,?,?)', [
            ('parent', '首条任务', '真实窗口', '/project', 'user'),
            ('child', '', None, '/project', 'subagent'),
        ])
    assert store.sync()
    snapshot = store.snapshot()
    child = snapshot['tasks']['child']
    assert child['window_root_id'] == 'parent'
    assert child['window_name'] == '真实窗口'
    assert child['hierarchy_status'] == 'nested'
    items = Analytics(snapshot).task_list()['items']
    assert len(items) == 1
    assert items[0]['id'] == 'parent'
    assert items[0]['total_tokens'] == 220
    detail = Analytics(snapshot).detail('parent')
    assert detail['child_summary']['count'] == 1
    assert detail['child_summary']['total_tokens'] == 110
    assert detail['children'][0]['kind'] == 'subagent'
    assert detail['children'][0]['parent_turn_id'] is None
from conftest import u,event,meta,context,call,checkpoint


def load(fixture_store,events):
    s,write=fixture_store;write(events);assert s.sync(),s.state['last_error'];return s


def test_exact_legacy_echo_no_double_count(fixture_store):
    s=load(fixture_store,[meta(),context(),call(),checkpoint(),checkpoint()])
    a=Analytics(s.snapshot());m=a.overview()['metrics']
    assert (m['total_tokens'],m['call_count'],m['summary_tokens'])==(110,1,0)
    assert m['cache_hit_rate']==0.4
    assert sum(t['total_tokens'] for t in a.detail('t')['turns'])==110


def test_legacy_before_new_is_replaced(fixture_store):
    s=load(fixture_store,[meta(),context(),checkpoint(),call()])
    rows=s.snapshot()['rows'];assert len(rows)==1 and rows[0]['kind']=='call'


def test_lookahead_exact_call_covers_the_preceding_checkpoint(fixture_store):
    one=u(100,0,0,0);two=u(200,0,0,0)
    s=load(fixture_store,[meta(),context(),checkpoint(one,one),checkpoint(two,one),call(total=two,usage=one)])
    m=Analytics(s.snapshot()).overview()['metrics']
    assert (m['total_tokens'],m['input_tokens'],m['call_count'],m['summary_tokens'])==(200,200,1,100)


def test_lookahead_exact_matches_when_other_calls_are_pending(fixture_store):
    one=u(100,0,0,0);two=u(200,0,0,0);four=u(400,0,0,0)
    s=load(fixture_store,[meta(),context(),checkpoint(one,one),call('a',one,two),
                          checkpoint(four,two),call('b',two,four)])
    m=Analytics(s.snapshot()).overview()['metrics']
    assert (m['total_tokens'],m['input_tokens'],m['call_count'],m['summary_tokens'])==(400,400,2,100)


def test_lookahead_cumulative_wins_when_pending_usage_is_identical(fixture_store):
    one=u(100,0,0,0);two=u(200,0,0,0);three=u(300,0,0,0)
    s=load(fixture_store,[meta(),context(),checkpoint(one,one),call('a',one,two),
                          checkpoint(three,one),call('b',one,three)])
    m=Analytics(s.snapshot()).overview()['metrics']
    assert (m['total_tokens'],m['input_tokens'],m['call_count'],m['summary_tokens'])==(300,300,2,100)


def test_legacy_residual_components_subtract_last_summary(fixture_store):
    one=u(100,0,0,0);three=u(300,0,0,0)
    s=load(fixture_store,[meta(),context(),checkpoint(one,one),checkpoint(three,one)])
    m=Analytics(s.snapshot()).overview()['metrics']
    assert (m['total_tokens'],m['input_tokens'],m['summary_tokens'])==(300,300,300)


def test_mixed_prefix_and_exact_tail(fixture_store):
    s=load(fixture_store,[meta(),context(),checkpoint(),call('b',total=u(200,80,20,6)),checkpoint(u(200,80,20,6))])
    a=Analytics(s.snapshot());m=a.overview()['metrics']
    assert (m['total_tokens'],m['summary_tokens'],m['call_count'])==(220,110,1)


def test_legacy_delta_is_summary_not_call(fixture_store):
    s=load(fixture_store,[meta(),context(),checkpoint(),checkpoint(u(200,80,20,6))])
    m=Analytics(s.snapshot()).overview()['metrics']
    assert m['total_tokens']==220 and m['call_count']==0 and m['undated_tokens']==0


def test_initial_missing_history_not_assigned_to_today(fixture_store):
    s=load(fixture_store,[meta(),context(),call(total=u(500,200,50,15))])
    a=Analytics(s.snapshot());m=a.overview()['metrics']
    assert m['total_tokens']==550 and m['undated_tokens']==440
    assert a.overview({'start':'2026-09-09'})['metrics']['total_tokens']==110


def test_cross_day_gap_not_guessed(fixture_store):
    s=load(fixture_store,[meta(),context(),checkpoint(),checkpoint(u(500,200,50,15),time='2026-09-10T01:00:00Z')])
    a=Analytics(s.snapshot())
    assert a.overview()['metrics']['undated_tokens']==330
    assert len(a.overview()['daily'])==2


def test_missing_components_remain_unknown(fixture_store):
    v={'input_tokens':100,'output_tokens':10,'total_tokens':110}
    s=load(fixture_store,[meta(),context(),call(usage=v)])
    m=Analytics(s.snapshot()).overview()['metrics']
    assert m['cached_input_tokens'] is None and m['cache_hit_rate'] is None


def test_duplicate_calls_across_fork_keep_owner(fixture_store):
    s,write=fixture_store
    write([meta(),context(),call()])
    write([meta('fork',forked_from_id='t'),context(),call()],name='fork.jsonl')
    assert s.sync()
    rows=s.snapshot()['rows'];assert len(rows)==1 and rows[0]['task_id']=='t'


def test_legacy_inherited_turn_deduplicates(fixture_store):
    s,write=fixture_store
    write([meta(),context(),checkpoint()])
    fork=event('session_meta',{'id':'fork','forked_from_id':'t'},'2026-09-10T00:00:00Z')
    write([fork,context(),checkpoint()],name='fork.jsonl')
    assert s.sync()
    rows=s.snapshot()['rows'];assert len(rows)==1 and rows[0]['task_id']=='t'


def test_children_included_only_on_request(fixture_store):
    s,write=fixture_store
    write([meta(),context(),call()])
    write([meta('child',source={'subagent':{'thread_spawn':{'parent_thread_id':'t'}}}),context('ct'),call('cr',task='child',turn='ct')],name='child.jsonl')
    assert s.sync();a=Analytics(s.snapshot())
    assert a.detail('t')['task']['total_tokens']==110
    assert a.detail('t',include_children=True)['task']['total_tokens']==220
    assert a.overview()['metrics']['total_tokens']==220


def test_partial_line_restart_and_truncation(fixture_store):
    s,write=fixture_store;p=write([meta(),context(),call()])
    with p.open('ab') as f:f.write(json.dumps(checkpoint()).encode())
    assert s.sync();n=len(s.snapshot()['rows'])
    with p.open('ab') as f:f.write(b'\n')
    assert s.sync();assert len(s.snapshot()['rows'])==n
    restarted=Store(s.path,s.source_root);assert restarted.sync();assert len(restarted.snapshot()['rows'])==n
    write([meta(),context(),call('new',usage=u(20,0,2,0))])
    assert restarted.sync();assert sum(r['total_tokens'] for r in restarted.snapshot()['rows'])==22


def test_atomic_replacement_same_length(fixture_store):
    s,write=fixture_store;p=write([meta(),context(),call('a')]);assert s.sync()
    replacement=p.with_suffix('.tmp');replacement.write_text(p.read_text().replace('"a"','"b"'));replacement.replace(p)
    assert s.sync();assert s.snapshot()['rows'][0]['id']=='call:b'


def test_corrupt_line_and_no_private_content(fixture_store):
    s,write=fixture_store;p=write([meta(),context(),event('response_item',{'type':'message','content':'SECRET_PROMPT'}),call()])
    with p.open('a') as f:f.write('{broken\n')
    assert s.sync()
    assert 'invalid_json' in {i['code'] for i in s.snapshot()['issues']}
    with s.connect() as con:
        assert 'SECRET_PROMPT' not in '\n'.join(r[0] for r in con.execute('SELECT data FROM events'))
    assert sanitize(event('response_item',{'api_key':'SECRET'})) is None


def test_counter_reset_visible(fixture_store):
    s=load(fixture_store,[meta(),context(),call(usage=u(200,80,20,6)),call('b',usage=u())])
    assert 'counter_reset' in {i['code'] for i in s.snapshot()['issues']}
    assert Analytics(s.snapshot()).overview()['metrics']['total_tokens']==330


def test_overlap_reports_conflict(fixture_store):
    s=load(fixture_store,[meta(),context(),call(),call('b')])
    assert 'overlapping_calls' in {i['code'] for i in s.snapshot()['issues']}


def price(**overrides):
    return dict(dict(id=1,model='test-model',currency='USD',effective_from='2026-01-01',input_per_million=2,cached_input_per_million=1,output_per_million=10,cache_write_per_million=3),**overrides)


def test_decimal_pricing_cache_and_reasoning(fixture_store):
    s=load(fixture_store,[meta(),context(),call()]);row=s.snapshot()['rows'][0]
    cost,_=api_cost(row,[price()]);assert cost['USD']==Decimal('0.00026')
    row['cache_write_input_tokens']=10
    assert api_cost(row,[price()])[0]['USD']==Decimal('0.00027')
    row['cached_input_tokens']=None;assert api_cost(row,[price()])==({},None)


def test_reasoning_override_and_cache_savings(fixture_store):
    s=load(fixture_store,[meta(),context(),call()]);row=s.snapshot()['rows'][0]
    costs,price_id,parts,savings=api_cost_details(row,[price(reasoning_output_per_million=20)])
    assert price_id==1 and costs['USD']==Decimal('0.00029')
    assert parts['USD']['visible_output']==Decimal('0.00007')
    assert parts['USD']['reasoning_output']==Decimal('0.00006')
    assert savings['USD']==Decimal('0.00004')
    metrics=Analytics(dict(s.snapshot(),prices=[price()])).overview()['metrics']
    assert metrics['visible_output_tokens']==7
    assert metrics['cache_savings']['USD']==Decimal('0.00004')
    assert metrics['cache_savings_coverage']==1


def test_weekly_quota_burn_forecast():
    reset=datetime.fromisoformat('2026-09-08T00:00:00+00:00')
    value={'status':'ok','fetched_at':'2026-09-04T00:00:00+00:00','limits':[{
        'limit_id':'codex','secondary':{'usedPercent':60,'windowDurationMins':10080,'resetsAt':reset.timestamp()}}]}
    result=quota_forecast(value,now='2026-09-04T00:00:00+00:00')
    assert result['status']=='ok' and result['will_exhaust_before_reset']
    assert result['days_remaining']==2 and result['daily_percent']==20


def test_quota_reader_works_with_plain_pipe_stream():
    stream=io.BytesIO(b'{"method":"notice"}\nnot-json\n{"id":2,"result":{"ok":true}}\n')
    assert _JsonLineReader(stream).read(2,.5)=={'id':2,'result':{'ok':True}}


def test_windows_codex_cmd_uses_command_interpreter(monkeypatch):
    monkeypatch.setenv('COMSPEC',r'C:\\Windows\\System32\\cmd.exe')
    command=_codex_command(system='nt',executable=r'C:\\Users\\demo\\AppData\\Roaming\\npm\\codex.cmd')
    assert command[:4]==[r'C:\\Windows\\System32\\cmd.exe','/d','/s','/c']
    assert 'codex.cmd' in command[4] and 'app-server --stdio' in command[4]


def test_native_codex_executable_does_not_use_shell():
    assert _codex_command(system='posix',executable='/usr/local/bin/codex') == [
        '/usr/local/bin/codex','app-server','--stdio'
    ]


def test_bundled_codex_is_found_without_path(monkeypatch, tmp_path):
    import analyzer.quota as quota
    bundled = tmp_path / 'codex'
    bundled.write_text('')
    bundled.chmod(0o755)
    monkeypatch.setattr(quota.shutil, 'which', lambda name: None)
    monkeypatch.setattr(quota, '_bundled_codex_paths', lambda: (bundled,))
    assert _codex_command() == [str(bundled), 'app-server', '--stdio']


def test_rate_limit_states_are_transition_deduplicated(fixture_store):
    store,write=fixture_store
    def state(reached,time):
        payload={'type':'token_count','info':{'total_token_usage':u(),'last_token_usage':u()},
                 'rate_limits':{'limit_id':'codex','rate_limit_reached_type':reached}}
        return event('event_msg',payload,time)
    write([meta(),context(),state('weekly','2026-09-09T01:00:00Z'),
           state('weekly','2026-09-09T01:01:00Z'),state(None,'2026-09-09T02:00:00Z'),
           state('weekly','2026-09-10T01:00:00Z')])
    assert store.sync()
    stats=store.rate_limit_stats()
    assert stats['total']==2
    assert stats['by_type']==[{'name':'weekly','count':2}]
    assert store.rate_limit_stats('2026-09-10','2026-09-10')['total']==1


def test_prices_effective_dates_and_unknown_model(fixture_store):
    s=load(fixture_store,[meta(),context(),call()]);row=s.snapshot()['rows'][0]
    assert api_cost(row,[price(effective_from='2026-09-10')])==({},None)
    assert api_cost(row,[price(effective_from='2026-09-10')],latest=True)[0]['USD']==Decimal('0.00026')
    assert api_cost(row,[price(model='another')])==({},None)
    assert api_cost(row,[price(),price(id=2,effective_from='2026-09-09',input_per_million=4)])[0]['USD']==Decimal('0.00038')


def test_period_adds_current_rate_estimate_without_backdating_ledger(fixture_store):
    s=load(fixture_store,[meta(),context(),call()])
    snapshot=s.snapshot();snapshot['prices']=[price(effective_from='2026-09-10')]
    analytics=Analytics(snapshot);period=analytics.period(analytics.rows)
    assert period['api_costs']=={}
    assert period['current_rate_api_costs']['USD']==Decimal('0.00026')
    assert period['current_rate_pricing_coverage']==1


def test_monthly_allocation_whole_denominator_and_timezone(fixture_store):
    s,write=fixture_store
    write([meta(),context(),call(time='2026-08-31T16:00:00Z')])
    write([meta('b'),context('bt'),call('b',task='b',turn='bt',usage=u(300,120,30,9))],name='b.jsonl')
    assert s.sync();v=s.snapshot();v['subscriptions']=[{'month':'2026-09','amount':200,'currency':'CNY'}]
    a=Analytics(v)
    assert a.detail('t')['task']['subscription_costs']['CNY']==Decimal(50)
    assert a.overview({'end':'2026-09-01'})['metrics']['subscription_costs']['CNY']==Decimal(50)
    assert a.overview()['metrics']['subscription_costs']['CNY']==Decimal(200)


def test_cross_month_and_separate_currency(fixture_store):
    s=load(fixture_store,[meta(),context(),call(time='2026-08-31T15:00:00Z'),call('b',total=u(200,80,20,6))])
    v=s.snapshot();v['subscriptions']=[{'month':'2026-08','amount':20,'currency':'USD'},{'month':'2026-09','amount':100,'currency':'CNY'}]
    m=Analytics(v).overview()['metrics'];assert m['subscription_costs']=={'USD':Decimal(20),'CNY':Decimal(100)}


def test_zero_month_and_undated_not_allocated(fixture_store):
    s=load(fixture_store,[meta(),context(),checkpoint(u(500,200,50,15))])
    v=s.snapshot();v['subscriptions']=[{'month':'2026-09','amount':200,'currency':'CNY'}]
    m=Analytics(v).overview()['metrics'];assert m['subscription_costs']['CNY']==200 and m['unallocated_tokens']==0


def test_new_call_without_cumulative_not_counted_twice(fixture_store):
    e=call();e['payload'].pop('thread_token_usage')
    s=load(fixture_store,[meta(),context(),e,checkpoint()])
    assert Analytics(s.snapshot()).overview()['metrics']['total_tokens']==110


def test_new_and_old_counter_domains_differ(fixture_store):
    s=load(fixture_store,[meta(),context(),checkpoint(u(500,200,50,15)),call(),checkpoint(u(600,240,60,18)),call('b',total=u(200,80,20,6)),checkpoint(u(700,280,70,21))])
    a=Analytics(s.snapshot());assert a.overview()['metrics']['total_tokens']==330
    assert not any(i['code']=='counter_reset' for i in s.snapshot()['issues'])


def test_archive_no_turn_keeps_one_summary(fixture_store):
    s,write=fixture_store;p=write([meta(),context(),checkpoint()]);assert s.sync()
    archived=s.source_root/'archived_sessions';archived.mkdir();p.rename(archived/p.name)
    assert s.sync();assert Analytics(s.snapshot()).overview()['metrics']['total_tokens']==110


def test_reset_to_previously_seen_counter(fixture_store):
    s=load(fixture_store,[meta(),context(),checkpoint(),checkpoint(u(200,80,20,6)),checkpoint(time='2026-09-10T01:00:00Z'),checkpoint(u(200,80,20,6),time='2026-09-10T01:00:01Z')])
    assert Analytics(s.snapshot()).overview()['metrics']['total_tokens']==440
    assert any(i['code']=='counter_reset' for i in s.snapshot()['issues'])


def test_duplicate_usage_conflict_is_visible(fixture_store):
    s=load(fixture_store,[meta(),context(),call(),call(usage=u(200,80,20,6))])
    assert any(i['code']=='duplicate_conflict' for i in s.snapshot()['issues'])


def test_repeated_checkpoint_with_new_timestamp_is_echo(fixture_store):
    s=load(fixture_store,[meta(),context(),call(),checkpoint(),checkpoint(time='2026-09-09T01:01:00Z')])
    m=Analytics(s.snapshot()).overview()['metrics']
    assert (m['total_tokens'],m['summary_tokens'])==(110,0)


def test_temporal_heatmap_uses_shanghai_hour_and_excludes_summaries(fixture_store):
    s=load(fixture_store,[meta(),context(),call(time='2026-09-09T16:30:00Z')])
    analytics=Analytics(s.snapshot())
    heat=analytics.temporal_heatmap(analytics.rows)
    cell=next(v for v in heat['cells'] if v['weekday']==3 and v['hour']==0)
    assert len(heat['cells'])==168 and cell['total_tokens']==110
    assert heat['exact_tokens']==heat['timed_tokens']==110 and heat['coverage']==1


def test_outlier_rule_requires_material_robust_spike():
    items=[{'id':str(i),'tokens':v} for i,v in enumerate([100,100,105,110,1000])]
    result=Analytics._outliers(items,'tokens','id','task_tokens')
    assert len(result)==1 and result[0]['id']=='4' and result[0]['ratio']>9
