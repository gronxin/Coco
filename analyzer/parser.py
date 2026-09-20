"""Whitelist-only log extraction and cumulative interval reconciliation.

No prompt, reasoning text, credentials or tool output is retained.
"""
import hashlib
import json
from datetime import datetime, timezone

FIELDS = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
          'output_tokens', 'reasoning_output_tokens', 'total_tokens')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:32]


def usage(value):
    value = value if isinstance(value, dict) else {}
    return {k: value[k] if type(value.get(k)) is int and value[k] >= 0 else None for k in FIELDS}


def timestamp(value):
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if not dt.tzinfo:
            return None
        return dt.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, AttributeError):
        return None


def sanitize(raw):
    """Return only metadata/accounting events; never persist arbitrary payloads."""
    if not isinstance(raw, dict) or not isinstance(raw.get('payload'), dict):
        return None
    p, kind = raw['payload'], raw.get('type')
    event = {'timestamp': timestamp(raw.get('timestamp')), 'type': kind}
    if kind == 'session_meta':
        source = p.get('source')
        parent = p.get('parent_thread_id')
        subagent_type = None
        if isinstance(source, dict):
            sub = source.get('subagent', {})
            if isinstance(sub, dict):
                if isinstance(sub.get('thread_spawn'), dict):
                    parent = sub['thread_spawn'].get('parent_thread_id') or parent
                    subagent_type = 'thread_spawn'
                elif sub.get('other'):
                    subagent_type = str(sub.get('other'))[:80]
        event.update({k: p.get(k) for k in ('id', 'cwd', 'cli_version', 'forked_from_id')})
        event['parent_id'] = parent
        event['thread_source'] = str(p.get('thread_source') or '')[:80]
        event['subagent_type'] = subagent_type
    elif kind == 'turn_context':
        event.update({k: p.get(k) for k in ('turn_id', 'root_turn_id', 'model', 'service_tier')})
    elif kind == 'token_usage_record':
        event.update({k: p.get(k) for k in ('thread_id', 'turn_id', 'root_turn_id', 'response_id')})
        event['usage'] = usage(p.get('usage'))
        event['cumulative'] = usage(p.get('thread_token_usage'))
    elif kind == 'event_msg' and p.get('type') == 'token_count':
        info = p.get('info')
        if not isinstance(info, dict):
            limits=p.get('rate_limits') if isinstance(p.get('rate_limits'),dict) else {}
            reached=limits.get('rate_limit_reached_type')
            if not reached:
                return None
            event.update(type='rate_limit_state', limit_type=str(reached)[:100], limit_id=str(limits.get('limit_id') or '')[:100])
            return event
        event['type'] = 'checkpoint'
        event['cumulative'] = usage(info.get('total_token_usage'))
        event['last'] = usage(info.get('last_token_usage'))
        limits=p.get('rate_limits') if isinstance(p.get('rate_limits'),dict) else {}
        event['limit_type'] = str(limits.get('rate_limit_reached_type') or '')[:100]
        event['limit_id'] = str(limits.get('limit_id') or '')[:100]
    elif kind == 'event_msg' and p.get('type') in ('rate_limit', 'rate_limit_reached', 'usage_limit_reached'):
        event.update(type='rate_limit_event', limit_type=str(p.get('type'))[:100],
                     limit_id=str(p.get('limit_id') or '')[:100])
    elif kind == 'event_msg' and p.get('type') in ('task_started', 'task_complete', 'task_completed', 'turn_aborted'):
        event.update(type=p['type'], turn_id=p.get('turn_id'))
    elif kind == 'event_msg' and p.get('type') == 'model_rerouted':
        event.update(type='model_rerouted', model=p.get('to_model'))
    else:
        return None
    return event


def annotate(events, path):
    meta = next((e for e in events if e['type'] == 'session_meta'), {})
    task = str(meta.get('id') or 'unassigned-' + digest(path))
    turn, model, service_tier = None, None, None
    result = []
    for original in events:
        e = dict(original)
        if e['type'] in ('task_started', 'turn_context'):
            next_turn = e.get('turn_id')
            if next_turn != turn:
                model, service_tier = None, None
            turn = next_turn
            if e['type'] == 'turn_context':
                model = e.get('model')
                service_tier = e.get('service_tier')
        if e['type'] == 'model_rerouted':
            model = e.get('model')
        e.update(task_id=e.get('thread_id') or task, turn_id=e.get('turn_id') or turn,
                 model=model, service_tier=service_tier, source_path=path, source_line=e['line'])
        result.append(e)
        if e['type'] in ('task_complete', 'task_completed', 'turn_aborted'):
            turn, model, service_tier = None, None, None
    return meta, task, result


def reconcile_modern(events, path, turn_owners=None):
    meta, task, events = annotate(events, path)
    issues, rows, segments = [], [], [[]]
    previous, seen = 0, set()
    local_ids = {}
    turn_owners = turn_owners or {}

    def issue(e, code, message):
        issues.append(dict(task_id=task, path=path, line=e.get('source_line', 1), code=code, message=message))

    for e in events:
        if e['type'] not in ('token_usage_record', 'checkpoint'):
            continue
        c = e['cumulative']['total_tokens']
        if c is not None:
            if c < previous:
                issue(e, 'counter_reset', '累计计数下降，已建立独立区间；总量存在不确定性。')
                segments.append([])
                seen, previous = set(), 0
            seen.add(c)
            previous = max(previous, c)
        e['segment'] = len(segments) - 1
        if e['type'] == 'token_usage_record':
            u = e['usage']
            if u['total_tokens'] is None:
                if u['input_tokens'] is not None and u['output_tokens'] is not None:
                    u['total_tokens'] = u['input_tokens'] + u['output_tokens']
                    issue(e, 'derived_total', '总 token 缺失，使用输入与输出之和。')
                else:
                    issue(e, 'missing_usage', '调用缺少可计算的总 token，无法计入总量。')
                    continue
            key = 'call:' + str(e['response_id']) if e.get('response_id') else 'event:' + digest([path, e['line']])
            if key in local_ids:
                if local_ids[key] != u:
                    issue(e, 'duplicate_conflict', '同一请求 ID 的用量不一致，保留首次记录。')
                continue
            local_ids[key] = u
            if not e.get('response_id'):
                issue(e, 'missing_request_id', '调用缺少请求 ID，跨文件去重无法确认。')
            e['key'] = key
            e['start'] = c - u['total_tokens'] if c is not None else None
            if e['start'] is not None and e['start'] < 0:
                issue(e, 'invalid_checkpoint', '累计值小于单次用量，不能用于覆盖区间。')
                e['start'] = None
            for sub, main in [('cached_input_tokens', 'input_tokens'), ('reasoning_output_tokens', 'output_tokens')]:
                if u[sub] is not None and u[main] is not None and u[sub] > u[main]:
                    issue(e, 'invalid_components', '细分 token 超过所属总量。')
            if u['input_tokens'] is not None and u['output_tokens'] is not None and u['input_tokens'] + u['output_tokens'] != u['total_tokens']:
                issue(e, 'total_mismatch', '报告的总 token 与输入加输出不一致，保留报告值。')
            owner = e['task_id']
            row = dict(id=key, task_id=owner, turn_id=e.get('turn_id'), timestamp=e['timestamp'],
                       model=e.get('model'), service_tier=e.get('service_tier'), kind='call', source_path=path, source_line=e['line'],
                       interval_start=e['timestamp'], interval_end=e['timestamp'], **u)
            rows.append(row)
        segments[-1].append(e)

    for segment in segments:
        exact = [e for e in segment if e['type'] == 'token_usage_record' and e.get('start') is not None]
        checkpoints = {}
        for e in segment:
            c = e['cumulative']['total_tokens']
            if c is not None:
                # Prefer legacy checkpoint: it provides last usage and helps date the delta.
                if c not in checkpoints or e['type'] == 'checkpoint':
                    checkpoints[c] = e
        prev = {k: 0 for k in FIELDS}
        prev_time = None
        for end, e in sorted(checkpoints.items()):
            start = prev['total_tokens']
            if end <= start:
                continue
            covered = []
            for call in exact:
                a, b = call['start'], call['cumulative']['total_tokens']
                if a < end and b > start:
                    covered.append((max(a, start), min(b, end), call))
            covered.sort(key=lambda item: item[:2])
            union, cursor = 0, start
            for a, b, call in covered:
                if a < cursor:
                    issue(call, 'overlapping_calls', '不同调用的累计区间重叠，需核对原始记录。')
                union += max(0, b - max(a, cursor))
                cursor = max(cursor, b)
            residual = end - start - union
            if residual > 0:
                current = e['cumulative']
                components = {k: None for k in FIELDS}
                components['total_tokens'] = residual
                wholly_covered = all(c['start'] >= start and c['cumulative']['total_tokens'] <= end for _, _, c in covered)
                if wholly_covered:
                    for k in FIELDS[:-1]:
                        values = [c['usage'][k] for _, _, c in covered]
                        if current[k] is not None and prev[k] is not None and all(v is not None for v in values):
                            value = current[k] - prev[k] - sum(values)
                            if value >= 0:
                                components[k] = value
                            else:
                                issue(e, 'component_conflict', '累计分项小于已记录调用分项，差额标为未知。')
                last = e.get('last', {})
                known_time = not covered and last.get('total_tokens') == residual
                owner = turn_owners.get(e.get('turn_id'), e['task_id'])
                # Unknown owner for inherited, context-free records: keep visible, not guessed.
                if meta.get('forked_from_id') and not e.get('turn_id') and e['timestamp'] and meta.get('timestamp') and e['timestamp'] < meta['timestamp']:
                    owner = 'unassigned'
                    issue(e, 'inherited_unknown', '分叉历史缺少轮次归属，记录在未归属用量中。')
                identity = [owner, e.get('turn_id'), e['timestamp'], current, prev]
                if not e.get('turn_id'):
                    identity.append(task)
                rows.append(dict(id='summary:' + digest(identity), task_id=owner,
                                 turn_id=e.get('turn_id') if known_time else None,
                                 timestamp=e['timestamp'] if known_time else None,
                                 model=e.get('model') if known_time else None, kind='summary',
                                 source_path=path, source_line=e['line'], interval_start=prev_time,
                                 interval_end=e['timestamp'], **components))
                issue(e, 'legacy_summary' if known_time else 'undated_summary',
                      '旧格式用量，仅有汇总，未伪造模型调用。' if known_time else
                      '累计差额缺少调用时间或归属，保留区间汇总，不计入具体日期或费用。')
            prev, prev_time = e['cumulative'], e['timestamp']
    return rows, issues


def reconcile(events, path, turn_owners=None):
    """Legacy and modern counters have DIFFERENT baselines in real resumed logs.

    Never interleave those cumulative counters. Legacy windows cover intervening
    exact calls; modern cumulative intervals are only used in modern-only logs.
    """
    if not any(e['type']=='checkpoint' for e in events):
        return reconcile_modern(events,path,turn_owners)
    meta, task, annotated = annotate(events,path)
    turn_owners = turn_owners or {}
    # Reuse modern validation/dedup, but suppress its independent cumulative domain.
    modern_only=[]
    for event in events:
        if event['type']=='checkpoint':
            continue
        e=dict(event)
        if e['type']=='token_usage_record':
            e['cumulative']={k:None for k in FIELDS}
        modern_only.append(e)
    rows,issues=reconcile_modern(modern_only,path,turn_owners)
    exact_by_line={r['source_line']:r for r in rows}
    pending=[]
    consumed=set()
    prev={k:0 for k in FIELDS}
    prev_time=None
    segment=0
    checkpoint_seen=set()

    def issue(e,code,message):
        issues.append(dict(task_id=task,path=path,line=e['line'],code=code,message=message))

    for index,e in enumerate(annotated):
        if e['type']=='token_usage_record':
            row=exact_by_line.get(e['line'])
            if row and row['id'] not in consumed:
                pending.append(row)
                consumed.add(row['id'])
            continue
        if e['type']!='checkpoint':
            continue
        current=e['cumulative']
        end=current['total_tokens']
        if end is None:
            issue(e,'missing_checkpoint','累计总量缺失，无法核对当前区间。')
            continue
        if end<prev['total_tokens']:
            segment+=1
            issue(e,'counter_reset','旧格式累计计数回退，已分段核对；保留回退证据。')
            prev={k:0 for k in FIELDS}
        provenance=digest([segment,e.get('turn_id'),current,e.get('last')])
        if provenance in checkpoint_seen:
            continue
        checkpoint_seen.add(provenance)
        first=prev_time is None
        delta=end-prev['total_tokens']
        last=e.get('last',{})
        valid_last=(e.get('turn_id') and last.get('total_tokens') is not None and
                    last.get('input_tokens') is not None and last.get('output_tokens') is not None and
                    last['input_tokens']+last['output_tokens']==last['total_tokens'])
        matching=next((r for r in pending if all(r[k]==last.get(k) for k in FIELDS)),None) if valid_last else None
        covered_rows=list(pending)
        # Some writers flush the legacy checkpoint immediately before the richer
        # record. Treat it as an echo only when both usage and cumulative agree.
        if valid_last:
            nxt=next((x for x in annotated[index+1:] if x['type'] in ('checkpoint','token_usage_record','task_started','turn_context')),None)
            if nxt and nxt['type']=='token_usage_record' and nxt['usage']==last and nxt['cumulative']['total_tokens']==end:
                lookahead=exact_by_line.get(nxt['line'])
                if lookahead:
                    matching=lookahead
                    consumed.add(lookahead['id'])
                    if all(r['id']!=lookahead['id'] for r in covered_rows):
                        covered_rows.append(lookahead)
        covered=sum(r['total_tokens'] for r in covered_rows)
        if valid_last and matching is None:
            owner=turn_owners.get(e.get('turn_id'),task)
            legacy=dict(id='summary:'+digest([owner,e.get('turn_id'),e['timestamp'],last,current]),task_id=owner,
                        turn_id=e.get('turn_id'),timestamp=e['timestamp'],model=e.get('model'),kind='summary',
                        source_path=path,source_line=e['line'],interval_start=e['timestamp'],interval_end=e['timestamp'],**last)
            rows.append(legacy)
            covered_rows.append(legacy)
            covered+=last['total_tokens']
            issue(e,'legacy_summary','旧格式本次用量，仅有汇总，未伪造请求 ID。')
        elif not e.get('turn_id'):
            issue(e,'baseline_snapshot','任务轮次之外的累计快照仅作基线，不计入用量。')
        # A first checkpoint can carry all history when a task resumes. Only its
        # independently reported last usage is attributable to this source span.
        if first and end!=covered:
            issue(e,'baseline_excluded','首个累计快照含此前历史，仅计入可验证的本次用量。')
            residual=0
        else:
            residual=delta-covered
        if residual<0:
            issue(e,'coverage_conflict','旧格式增量小于区间内已知调用；保留调用，未重复加入汇总。')
        elif residual>0:
            components={k:None for k in FIELDS};components['total_tokens']=residual
            for k in FIELDS[:-1]:
                values=[r[k] for r in covered_rows]
                if current[k] is not None and prev[k] is not None and all(v is not None for v in values):
                    value=current[k]-prev[k]-sum(values)
                    if value>=0:components[k]=value
                    else:issue(e,'component_conflict','累计分项与调用分项不一致，差额分项标记未知。')
            known=False
            owner=turn_owners.get(e.get('turn_id'),task)
            inherited=meta.get('forked_from_id') and not e.get('turn_id') and e['timestamp'] and meta.get('timestamp') and e['timestamp']<meta['timestamp']
            if inherited:
                owner='unassigned'
                issue(e,'inherited_unknown','分叉历史缺少归属，保留为未归属汇总；去重状态未知。')
            identity=[owner,e.get('turn_id'),e['timestamp'],current,prev,segment]
            rows.append(dict(id='summary:'+digest(identity),task_id=owner,
                         turn_id=e.get('turn_id') if known else None,timestamp=e['timestamp'] if known else None,
                         model=e.get('model') if known else None,kind='summary',source_path=path,source_line=e['line'],
                         interval_start=prev_time,interval_end=e['timestamp'],**components))
            issue(e,'legacy_summary' if known else 'undated_summary',
                  '旧格式用量，仅有汇总，未伪造模型调用。' if known else '累计差额跨越未知调用，保留区间汇总，不计入具体日期或费用。')
        elif prev_time is not None and delta==0 and pending:
            issue(e,'unchanged_counter','累计值未增长，但出现新调用，请核对计数域。')
        prev,prev_time=current,e['timestamp']
        pending=[]
    return rows,issues
