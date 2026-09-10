import json
import pytest
from analyzer.store import Store


def u(i=100,c=40,o=10,r=3,w=0):
    return dict(input_tokens=i,cached_input_tokens=c,output_tokens=o,reasoning_output_tokens=r,cache_write_input_tokens=w,total_tokens=i+o)


def event(kind,payload,time='2026-09-09T01:00:00Z'):
    return dict(type=kind,payload=payload,timestamp=time)


def meta(task='t',**extra):
    return event('session_meta',dict(id=task,cwd='/project',cli_version='test',**extra),'2026-09-01T00:00:00Z')


def context(turn='turn',model='test-model'):
    return event('turn_context',dict(turn_id=turn,model=model))


def call(request='r',usage=None,total=None,task='t',turn='turn',time='2026-09-09T01:00:01Z'):
    usage=usage or u()
    return event('token_usage_record',dict(response_id=request,thread_id=task,turn_id=turn,usage=usage,thread_token_usage=total or usage),time)


def checkpoint(total=None,last=None,time='2026-09-09T01:00:02Z'):
    return event('event_msg',dict(type='token_count',info=dict(total_token_usage=total or u(),last_token_usage=last or u())),time)


@pytest.fixture
def fixture_store(tmp_path):
    root=tmp_path/'codex'
    (root/'sessions').mkdir(parents=True)
    s=Store(tmp_path/'data'/'test.sqlite',root)
    def write(events,name='a.jsonl',append=False):
        path=root/'sessions'/name
        with path.open('a' if append else 'w') as f:
            for e in events:f.write(json.dumps(e)+'\n')
        return path
    return s,write
