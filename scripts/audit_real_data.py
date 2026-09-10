"""Independent ten-task source audit; reads only whitelisted accounting fields."""
import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyzer.store import Store


def source_exact(root):
    calls={}
    task_files=defaultdict(set)
    hashes={}
    for base in ('sessions','archived_sessions'):
        directory=root/base
        if not directory.is_dir():continue
        for path in directory.rglob('*.jsonl'):
            hashes[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
            task=None
            try:
                for line in path.open():
                    raw=json.loads(line);p=raw.get('payload',{})
                    if raw.get('type')=='session_meta':task=p.get('id')
                    if raw.get('type')=='token_usage_record' and isinstance(p,dict):
                        rid=p.get('response_id');u=p.get('usage',{})
                        if rid and type(u.get('total_tokens')) is int:
                            calls.setdefault(rid,(p.get('thread_id') or task,u['total_tokens']))
                            task_files[p.get('thread_id') or task].add(str(path))
            except (OSError,ValueError,TypeError):pass
    totals=defaultdict(int)
    for task,tokens in calls.values():totals[task]+=tokens
    return totals,task_files,hashes


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',default=str(Path.home()/'.codex'))
    parser.add_argument('--database',default='.data/analytics.sqlite')
    args=parser.parse_args();root=Path(args.source).resolve()
    expected,files,before=source_exact(root)
    store=Store(args.database,root);assert store.sync(),store.state['last_error']
    snapshot=store.snapshot();actual=defaultdict(int)
    for row in snapshot['rows']:
        if row['kind']=='call':actual[row['task_id']]+=row['total_tokens']
    # The currently-running Codex task legitimately appends while this audit runs.
    # Compare ten stable task sources and report concurrent external writes apart.
    _,_,after=source_exact(root)
    stable_paths={path for path,value in before.items() if after.get(path)==value}
    candidates=[task for task in sorted(expected,key=lambda task:expected[task],reverse=True)
                if files[task] and files[task] <= stable_paths][:10]
    rows=[]
    for task in candidates:
        rows.append(dict(task_id=task,source_exact_tokens=expected[task],ledger_exact_tokens=actual[task],match=expected[task]==actual[task],source_files=len(files[task])))
    changed=sorted(path for path,value in before.items() if after.get(path)!=value)
    report=dict(source=str(root),tasks_checked=len(rows),all_match=all(r['match'] for r in rows),
                audited_sources_unchanged=all(files[r['task_id']] <= stable_paths for r in rows),concurrently_changed_files=len(changed),rows=rows,
                note='Independent audit covers records with response_id and usage.total_tokens. Legacy summaries are verified by parser fixtures because no independent request identity exists.')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    raise SystemExit(0 if report['all_match'] and len(rows)>=10 and report['audited_sources_unchanged'] else 1)


if __name__=='__main__':main()
