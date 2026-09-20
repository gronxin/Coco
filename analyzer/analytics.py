"""Shared filtering and Decimal accounting for all views and exports."""
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from statistics import median
from zoneinfo import ZoneInfo

from .parser import FIELDS

TZ = ZoneInfo('Asia/Shanghai')
INFO_CODES = {'legacy_summary', 'derived_total'}


def local_day(value):
    return datetime.fromisoformat(value).astimezone(TZ).date().isoformat() if value else None


def local_datetime(value):
    return datetime.fromisoformat(value).astimezone(TZ) if value else None


def api_cost_details(row, prices, latest=False):
    day = local_day(row['timestamp'])
    if not day or not row['model']:
        return {}, None, {}, {}
    matches = [p for p in prices if p['model'] == row['model'] and (latest or p['effective_from'] <= day)]
    if not matches:
        return {}, None, {}, {}
    p = max(matches, key=lambda x: (x['effective_from'], x.get('id', 0)))
    i, c, o, w = [row[k] for k in ('input_tokens','cached_input_tokens','output_tokens','cache_write_input_tokens')]
    if None in (i,c,o) or c > i or i + o != row['total_tokens']:
        return {}, None, {}, {}
    if w is None:
        if p.get('cache_write_per_million') is not None:
            return {}, None, {}, {}
        w=0
    if w + c > i or (w and p.get('cache_write_per_million') is None):
        return {}, None, {}, {}
    prefix = 'long_' if p.get('context_limit') is not None and i > p['context_limit'] else ''
    keys = [prefix+k for k in ('input_per_million','cached_input_per_million','output_per_million')]
    if any(p.get(k) is None for k in keys):
        return {}, None, {}, {}
    a,b,d = [Decimal(str(p[k])) for k in keys]
    reasoning = row.get('reasoning_output_tokens')
    reasoning_rate_value = p.get(prefix+'reasoning_output_per_million')
    if reasoning is not None and (reasoning < 0 or reasoning > o):
        return {}, None, {}, {}
    if reasoning_rate_value is not None and reasoning is None:
        return {}, None, {}, {}
    parts = {'non_cached_input':Decimal(i-c-w)*a, 'cached_input':Decimal(c)*b,
             'cache_write':Decimal(0), 'visible_output':Decimal(0),
             'reasoning_output':Decimal(0), 'output_unclassified':Decimal(0)}
    if reasoning is None:
        parts['output_unclassified'] = Decimal(o)*d
    else:
        parts['visible_output'] = Decimal(o-reasoning)*d
        reasoning_rate = Decimal(str(reasoning_rate_value)) if reasoning_rate_value is not None else d
        parts['reasoning_output'] = Decimal(reasoning)*reasoning_rate
    if w:
        write_rate = p.get(prefix+'cache_write_per_million')
        if write_rate is None:
            return {}, None, {}, {}
        parts['cache_write'] = Decimal(w)*Decimal(str(write_rate))
    million = Decimal(1000000)
    breakdown = {p['currency']:{key:value/million for key,value in parts.items()}}
    return ({p['currency']:sum(parts.values())/million}, p['id'], breakdown,
            {p['currency']:Decimal(c)*(a-b)/million})


def api_cost(row, prices, latest=False):
    costs, price_id, _, _ = api_cost_details(row, prices, latest)
    return costs, price_id


class Analytics:
    def __init__(self, snapshot):
        self.tasks, self.issues = snapshot['tasks'], snapshot['issues']
        self.prices = snapshot['prices']
        rows = snapshot['rows']
        self.rows = []
        months = defaultdict(int)
        subscriptions = {s['month']: s for s in snapshot['subscriptions']}
        for row in rows:
            day = local_day(row['timestamp'])
            if day:
                months[day[:7]] += row['total_tokens']
        by_source = defaultdict(list)
        for issue in self.issues:
            by_source[(issue['path'],issue['line'])].append(issue)
        for original in rows:
            row = dict(original)
            row['date'] = local_day(row['timestamp'])
            row['project'] = self.tasks[row['task_id']]['project']
            row['codex_project'] = self.tasks[row['task_id']].get('codex_project') or '未加入 Codex 项目'
            row['window_name'] = self.tasks[row['task_id']].get('window_name') or self.tasks[row['task_id']]['title']
            row['title'] = self.tasks[row['task_id']]['title']
            costs, price_id, breakdown, savings = api_cost_details(row, snapshot['prices'])
            row.update(api_costs=costs, price_id=price_id, subscription_costs={},
                       api_cost_breakdown=breakdown, cache_savings=savings,
                       cache_savings_priced_tokens=(row['cached_input_tokens'] or 0) if price_id is not None else 0,
                       cache_savings_coverage=1 if price_id is not None and row['cached_input_tokens'] is not None else None,
                       visible_output_tokens=(row['output_tokens']-row['reasoning_output_tokens']) if row['output_tokens'] is not None and row['reasoning_output_tokens'] is not None else None,
                       priced_tokens=row['total_tokens'] if price_id is not None else 0,
                       pricing_coverage=1 if price_id is not None else 0,
                       call_count=int(row['kind']=='call'), summary_tokens=row['total_tokens'] if row['kind']=='summary' else 0,
                       cache_hit_rate=row['cached_input_tokens']/row['input_tokens'] if row['input_tokens'] and row['cached_input_tokens'] is not None else None,
                       issues=len(by_source[(row['source_path'],row['source_line'])]),
                       undated_tokens=row['total_tokens'] if row['date'] is None else 0,
                       unallocated_tokens=0)
            month = row['date'][:7] if row['date'] else None
            if month in subscriptions and months[month]:
                s = subscriptions[month]
                row['subscription_costs'] = {s['currency']:Decimal(str(s['amount']))*Decimal(row['total_tokens'])/Decimal(months[month])}
            else:
                row['unallocated_tokens'] = row['total_tokens']
            self.rows.append(row)

    def select(self, filters=None, task_ids=None):
        f = filters or {}
        result = []
        for r in self.rows:
            if task_ids is not None and r['task_id'] not in task_ids:
                continue
            day = r['date']
            if f.get('start') and (not day or day < f['start']):
                continue
            if f.get('end') and (not day or day > f['end']):
                continue
            if f.get('project') and r['project'] != f['project']:
                continue
            if f.get('codex_project') and r['codex_project'] != f['codex_project']:
                continue
            if f.get('window') and r['window_name'] != f['window']:
                continue
            if f.get('model') and (r['model'] or '未知') != f['model']:
                continue
            if f.get('search') and f['search'].lower() not in (r['window_name']+' '+r['title']+' '+r['task_id']).lower():
                continue
            result.append(r)
        return result

    def aggregate(self, rows):
        result = {}
        for k in FIELDS:
            result[k] = sum(r[k] for r in rows) if all(r[k] is not None for r in rows) else None
        result['total_tokens'] = sum(r['total_tokens'] for r in rows)
        for key in ('call_count','summary_tokens','priced_tokens','cache_savings_priced_tokens','issues','undated_tokens','unallocated_tokens'):
            result[key] = sum(r[key] for r in rows)
        for key in ('api_costs','subscription_costs','cache_savings'):
            amounts = defaultdict(Decimal)
            for r in rows:
                for currency, amount in r[key].items():
                    amounts[currency] += amount
            result[key] = dict(amounts)
        breakdown = defaultdict(lambda: defaultdict(Decimal))
        for r in rows:
            for currency, parts in r['api_cost_breakdown'].items():
                for part, amount in parts.items():
                    breakdown[currency][part] += amount
        result['api_cost_breakdown'] = {currency:dict(parts) for currency,parts in breakdown.items()}
        i,c = result['input_tokens'],result['cached_input_tokens']
        o, reasoning = result['output_tokens'], result['reasoning_output_tokens']
        result['visible_output_tokens'] = o-reasoning if o is not None and reasoning is not None else None
        result['cache_hit_rate'] = c/i if i and c is not None else None
        result['pricing_coverage'] = result['priced_tokens']/result['total_tokens'] if result['total_tokens'] else 0
        result['cache_savings_coverage'] = result['cache_savings_priced_tokens']/c if c else (0 if c == 0 else None)
        result['detail_coverage'] = (result['total_tokens']-result['summary_tokens'])/result['total_tokens'] if result['total_tokens'] else 0
        turns={(r['task_id'],r['turn_id']) for r in rows if r.get('turn_id')}
        result['turn_count'] = len(turns)
        result['avg_tokens_per_turn'] = result['total_tokens']/len(turns) if turns else None
        return result

    @staticmethod
    def _outliers(items, value_key, identity_key, kind):
        values=[float(item[value_key]) for item in items if item.get(value_key) is not None]
        if len(values)<5:
            return []
        center=median(values); deviations=[abs(value-center) for value in values]; mad=median(deviations)
        result=[]
        for item in items:
            value=item.get(value_key)
            if value is None: continue
            numeric_value=float(value)
            ratio=(numeric_value/center) if center else None
            score=(abs(numeric_value-center)/mad) if mad else None
            unusual=(score is not None and score>=3.5 and ratio is not None and ratio>=2) or (mad==0 and center and ratio>=3)
            if unusual:
                result.append({'kind':kind,'id':item.get(identity_key),'value':value,'median':center,
                               'ratio':ratio,'robust_score':score,
                               'label':item.get('title') or item.get('date') or item.get(identity_key),
                               'project':item.get('project')})
        return sorted(result,key=lambda item:item['value'],reverse=True)

    def temporal_heatmap(self, rows):
        cells=defaultdict(int); exact=0; timed=0
        for row in rows:
            if not row.get('timestamp'): continue
            timed+=row['total_tokens']
            if row['kind']!='call': continue
            dt=local_datetime(row['timestamp']);cells[(dt.weekday(),dt.hour)]+=row['total_tokens'];exact+=row['total_tokens']
        return {'cells':[{'weekday':day,'hour':hour,'total_tokens':cells.get((day,hour),0)} for day in range(7) for hour in range(24)],
                'exact_tokens':exact,'timed_tokens':timed,'coverage':exact/timed if timed else 0,
                'note':'仅按可确认单次调用的发生时间分桶；历史汇总无法可靠还原到具体小时。'}

    def weekly_comparison(self, filters=None):
        base=dict(filters or {});base['start']=base['end']=''
        rows=self.select(base);today=datetime.now(TZ).date();start=today-timedelta(days=today.weekday())
        labels=['周一','周二','周三','周四','周五','周六','周日'];result=[]
        for offset in range(today.weekday()+1):
            current=(start+timedelta(days=offset)).isoformat();previous=(start-timedelta(days=7)+timedelta(days=offset)).isoformat()
            result.append({'label':labels[offset],'current_date':current,'previous_date':previous,
                           'current_tokens':sum(r['total_tokens'] for r in rows if r['date']==current),
                           'previous_tokens':sum(r['total_tokens'] for r in rows if r['date']==previous)})
        return result

    def model_comparison(self, rows):
        groups=defaultdict(list)
        for row in rows:
            if row['kind']=='call': groups[row.get('model') or '未知'].append(row)
        result=[]
        for model,values in groups.items():
            metrics=self.aggregate(values);tasks={r['task_id'] for r in values};turns={(r['task_id'],r['turn_id']) for r in values if r.get('turn_id')}
            result.append({'model':model,**metrics,'task_count':len(tasks),
                           'tokens_per_task':metrics['total_tokens']/len(tasks) if tasks else None,
                           'tokens_per_call':metrics['total_tokens']/len(values) if values else None,
                           'calls_per_task':len(values)/len(tasks) if tasks else None,
                           'cost_per_task':{k:v/len(tasks) for k,v in metrics['api_costs'].items()} if tasks else {}})
        return sorted(result,key=lambda item:item['total_tokens'],reverse=True)

    def group(self, rows, key, label='name'):
        groups = defaultdict(list)
        for r in rows:
            groups[r.get(key) or '未知'].append(r)
        return [dict(self.aggregate(v), **{label:k}) for k,v in sorted(groups.items())]

    def period(self, rows):
        """Add a current-rate estimate without changing effective-dated ledger costs."""
        result = self.aggregate(rows)
        amounts = defaultdict(Decimal)
        priced_tokens = 0
        for row in rows:
            costs, price_id = api_cost(row, self.prices, latest=True)
            if price_id is not None:
                priced_tokens += row['total_tokens']
            for currency, amount in costs.items():
                amounts[currency] += amount
        result['current_rate_api_costs'] = dict(amounts)
        result['current_rate_priced_tokens'] = priced_tokens
        result['current_rate_pricing_coverage'] = priced_tokens/result['total_tokens'] if result['total_tokens'] else 0
        return result

    def overview(self, filters=None):
        rows = self.select(filters)
        today = datetime.now(TZ).date()
        start_week = today - timedelta(days=today.weekday())
        month_start=today.replace(day=1)
        previous_month_end=month_start-timedelta(days=1)
        previous_month_start=previous_month_end.replace(day=1)
        periods={
            'today': ([r for r in rows if r['date']==today.isoformat()], [r for r in rows if r['date']==(today-timedelta(days=1)).isoformat()]),
            'week': ([r for r in rows if r['date'] and start_week.isoformat()<=r['date']<=today.isoformat()], [r for r in rows if r['date'] and (start_week-timedelta(days=7)).isoformat()<=r['date']<start_week.isoformat()]),
            'month': ([r for r in rows if r['date'] and month_start.isoformat()<=r['date']<=today.isoformat()], [r for r in rows if r['date'] and previous_month_start.isoformat()<=r['date']<=previous_month_end.isoformat()]),
        }
        compared={}
        for name,(current,previous) in periods.items():
            cur,prev=self.period(current),self.period(previous)
            cur['previous']=prev
            cur['token_change']=cur['total_tokens']-prev['total_tokens']
            cur['token_change_rate']=(cur['total_tokens']/prev['total_tokens']-1) if prev['total_tokens'] else None
            cur['cache_hit_change']=(cur['cache_hit_rate']-prev['cache_hit_rate']) if cur['cache_hit_rate'] is not None and prev['cache_hit_rate'] is not None else None
            compared[name]=cur
        daily=self.group([r for r in rows if r['date']], 'date', 'date')
        return dict(metrics=self.aggregate(rows), **compared,
                    daily=daily, calendar=daily,
                    temporal_heatmap=self.temporal_heatmap(rows),weekly_comparison=self.weekly_comparison(filters),
                    by_model=self.group(rows,'model'), by_project=self.group(rows,'project'),
                    excluded_undated_tokens=sum(r['total_tokens'] for r in self.rows if not r['date']))

    def task_list(self, filters=None, sort='tokens', direction='desc', page=1, page_size=25):
        groups = defaultdict(list)
        for r in self.select(filters):
            root_id = self.tasks[r['task_id']].get('window_root_id')
            if root_id:
                groups[root_id].append(r)
        items = []
        for task_id, rows in groups.items():
            models = sorted({r['model'] or '未知' for r in rows})
            items.append(dict(self.tasks[task_id], **self.aggregate(rows), model=' / '.join(models)))
        if sort=='time':
            key=lambda r:r['updated_at'] or ''
        elif sort=='cost':
            # Currency-aware lexicographic order never adds different currencies.
            key=lambda r:tuple(sorted(r['api_costs'].items()))
        else:
            key=lambda r:r['total_tokens']
        items.sort(key=key, reverse=direction!='asc')
        total=len(items)
        total_pages=max(1,(total+page_size-1)//page_size)
        page=min(page,total_pages)
        start=(page-1)*page_size
        return dict(items=items[start:start+page_size],total=total,page=page,page_size=page_size,
                    total_pages=total_pages)

    def descendants(self, task):
        found = {task}
        while True:
            more = {k for k,v in self.tasks.items() if (v.get('parent_id') or v.get('forked_from_id')) in found}
            if more <= found:
                return found
            found.update(more)

    def _turn_explanation(self, calls):
        exact=[r for r in calls if r['kind']=='call']
        failures=0
        if not exact:
            return dict(observations=['该轮仅有历史汇总，无法还原单次变化。'],evidence=[])
        first,last=exact[0],exact[-1]
        observations=[]
        def change(label,key):
            a,b=first.get(key),last.get(key)
            if a is not None and b is not None and len(exact)>1:
                observations.append(f'{label}从 {a:,} 变为 {b:,}，变化 {b-a:+,}。')
        change('输入', 'input_tokens')
        nc=lambda r: r['input_tokens']-r['cached_input_tokens'] if r.get('input_tokens') is not None and r.get('cached_input_tokens') is not None else None
        a,b=nc(first),nc(last)
        if a is not None and b is not None and len(exact)>1: observations.append(f'非缓存输入从 {a:,} 变为 {b:,}，变化 {b-a:+,}。')
        if first.get('cache_hit_rate') is not None and last.get('cache_hit_rate') is not None and len(exact)>1:
            observations.append(f'缓存命中率变化 {(last["cache_hit_rate"]-first["cache_hit_rate"])*100:+.1f} 个百分点。')
        output=sum(r.get('output_tokens') or 0 for r in exact); reasoning=sum(r.get('reasoning_output_tokens') or 0 for r in exact)
        if output: observations.append(f'推理输出占输出 {reasoning/output:.1%}。')
        return dict(observations=observations,evidence=[dict(call_id=r['id'],source_path=r['source_path'],source_line=r['source_line']) for r in exact])

    def detail(self, task_id, filters=None, include_children=False, include_calls=False):
        if task_id not in self.tasks:
            raise KeyError(task_id)
        ids = self.descendants(task_id) if include_children else {task_id}
        rows = self.select(filters,ids)
        groups = defaultdict(list)
        for r in sorted(rows,key=lambda r:(r['timestamp'] or '',r['source_line'])):
            groups[(r['task_id'],r['turn_id'] or 'unassigned')].append(r)
        issues = [i for i in self.issues if i['task_id'] in ids]
        conflicts = sum(i['code'] not in INFO_CODES for i in issues)
        metrics = self.aggregate(rows)
        turns=[]
        for k,v in groups.items():
            turn=dict(id=k[1],task_id=k[0],**self.aggregate(v),explanation=self._turn_explanation(v))
            if include_calls: turn['calls']=v
            turns.append(turn)
        tree=self._agent_tree(task_id, rows)
        child_ids=self.descendants(task_id)-{task_id}
        child_rows=defaultdict(list)
        for row in self.select(filters,child_ids): child_rows[row['task_id']].append(row)
        child_items=[]
        for child_id in sorted(child_ids,key=lambda key:self.tasks[key].get('updated_at') or '',reverse=True):
            child=self.tasks[child_id]
            source=child.get('thread_source')
            kind='review' if source=='guardian_review' or child.get('subagent_type')=='guardian' else 'subagent' if source=='subagent' or child.get('parent_id') else 'fork' if child.get('forked_from_id') else 'other'
            path=(child.get('agent_path') or '').rstrip('/').split('/')[-1]
            title=child.get('title') or ''
            label=title if title and not title.startswith(('任务 ','子代理任务 ','权限审批审查')) else path or ({'review':'权限审批审查','subagent':'子代理任务','fork':'分叉任务'}.get(kind,'内部任务'))
            own=self.aggregate(child_rows.get(child_id,[]))
            child_items.append(dict(id=child_id,label=label,kind=kind,agent_path=child.get('agent_path'),agent_nickname=child.get('agent_nickname'),
                                    parent_id=child.get('parent_id'),forked_from_id=child.get('forked_from_id'),parent_turn_id=None,
                                    archived=child.get('archived'),updated_at=child.get('updated_at'),**own))
        child_metrics=self.aggregate([row for values in child_rows.values() for row in values])
        return dict(task=dict(self.tasks[task_id],**metrics),
                    turns=turns,
                    children=child_items,
                    child_summary=dict(count=len(child_items),total_tokens=child_metrics['total_tokens'],call_count=child_metrics['call_count']),
                    agent_tree=tree, issue_count=len(issues),reconciliation=dict(exact_tokens=metrics['total_tokens']-metrics['summary_tokens'],
                       summary_tokens=metrics['summary_tokens'],undated_tokens=metrics['undated_tokens'],
                       conflict_count=conflicts,complete=not conflicts and not metrics['summary_tokens']))

    def _agent_tree(self, root, selected_rows):
        rows_by_task=defaultdict(list)
        for r in selected_rows: rows_by_task[r['task_id']].append(r)
        def node(task_id):
            own=self.aggregate(rows_by_task.get(task_id,[]))
            children=[node(k) for k,v in self.tasks.items() if (v.get('parent_id') or v.get('forked_from_id'))==task_id]
            return dict(id=task_id,title=self.tasks[task_id]['title'],own_tokens=own['total_tokens'],
                        children_tokens=sum(c['combined_tokens'] for c in children),
                        combined_tokens=own['total_tokens']+sum(c['combined_tokens'] for c in children),children=children)
        return node(root)

    def calls(self, task_id, turn_id, filters=None, page=1, page_size=50):
        rows=[r for r in self.select(filters,{task_id}) if (r['turn_id'] or 'unassigned')==turn_id]
        total=len(rows); total_pages=max(1,(total+page_size-1)//page_size); page=min(page,total_pages); start=(page-1)*page_size
        return dict(items=rows[start:start+page_size],total=total,page=page,page_size=page_size,total_pages=total_pages)

    def analysis(self, filters=None, call_page=1, call_page_size=10, signal_page=1, signal_page_size=10):
        view = self.overview(filters)
        rows = [r for r in self.select(filters) if r['kind']=='call']
        ranked_calls=sorted(rows,key=lambda r:r['total_tokens'],reverse=True)
        call_pages=max(1,(len(ranked_calls)+call_page_size-1)//call_page_size); call_page=min(call_page,call_pages)
        call_start=(call_page-1)*call_page_size
        view['high_calls'] = ranked_calls[call_start:call_start+call_page_size]
        view['high_calls_page'] = dict(page=call_page,page_size=call_page_size,total=len(ranked_calls),total_pages=call_pages)
        view['high_tasks'] = self.task_list(filters,page_size=10)['items']
        task_items=self.task_list(filters,page_size=100000)['items']
        day_items=view['daily']
        anomalies=self._outliers(task_items,'total_tokens','id','task_tokens')+self._outliers(day_items,'total_tokens','date','day_tokens')
        currencies={currency for item in day_items for currency in item['api_costs']}
        for currency in currencies:
            priced=[dict(item, cost_value=item['api_costs'].get(currency)) for item in day_items if currency in item['api_costs']]
            for anomaly in self._outliers(priced,'cost_value','date','day_cost'):
                anomaly['currency']=currency;anomalies.append(anomaly)
        view['anomalies']=sorted(anomalies,key=lambda item:item.get('ratio') or 0,reverse=True)
        view['model_comparison']=self.model_comparison(self.select(filters))
        view['task_efficiency']=task_items[:50]
        groups, signals = defaultdict(list), []
        for r in rows:
            if r['turn_id'] and r['timestamp']:
                groups[(r['task_id'],r['turn_id'])].append(r)
        for (task,turn), calls in groups.items():
            calls.sort(key=lambda r:(r['timestamp'],r['source_line']))
            inputs = [r['input_tokens'] for r in calls]
            rates = [r['cache_hit_rate'] for r in calls]
            if len(calls)>=3 and all(x is not None for x in inputs) and inputs[-1]>inputs[0] and all(a<=b for a,b in zip(inputs,inputs[1:])):
                signals.append(dict(task_id=task,turn_id=turn,message='该轮次输入 token 持续增长，可检查上下文积累。',evidence=dict(call_ids=[r['id'] for r in calls],input_tokens=inputs)))
            if len(calls)>=3 and all(x is not None for x in rates) and rates[0]-rates[-1]>=0.1:
                signals.append(dict(task_id=task,turn_id=turn,message='该轮次末次缓存命中率比首次下降至少 10 个百分点。',evidence=dict(call_ids=[calls[0]['id'],calls[-1]['id']],cache_hit_rates=[rates[0],rates[-1]])))
        signal_pages=max(1,(len(signals)+signal_page_size-1)//signal_page_size); signal_page=min(signal_page,signal_pages)
        signal_start=(signal_page-1)*signal_page_size
        view['signals'] = signals[signal_start:signal_start+signal_page_size]
        view['signals_page'] = dict(page=signal_page,page_size=signal_page_size,total=len(signals),total_pages=signal_pages)
        return view
