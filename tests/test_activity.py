import json

from analyzer.activity import clean_request, is_memory_access, read_turn_activity, result_summary, tool_description


def line(event_type, payload, timestamp='2026-09-09T01:00:00Z'):
    return json.dumps({'type': event_type, 'payload': payload, 'timestamp': timestamp})


def test_clean_request_removes_injected_context():
    value = '''
<in-app-browser-context source="ambient-ui-state">browser state</in-app-browser-context>
## My request:
查找高消耗调用。
'''
    assert clean_request(value) == '查找高消耗调用。'


def test_clean_request_removes_agents_and_runtime_context():
    value='''# AGENTS.md instructions\n<INSTRUCTIONS>secret system rules</INSTRUCTIONS>\n<environment_context>runtime cwd</environment_context>\n## My request:\n分析当前任务'''
    assert clean_request(value)=='分析当前任务'


def test_memory_access_does_not_mistake_patch_text_for_a_read():
    assert not is_memory_access({'name': 'exec', 'input': 'const patch = "MEMORY.md"'})
    assert not is_memory_access({'name': 'exec', 'input': 'const patch = "tools.exec_command MEMORY.md"'})
    assert is_memory_access({'name': 'exec', 'input': 'await tools.exec_command({"cmd":"rg token /Users/me/.codex/memories/MEMORY.md"})'})


def test_tool_descriptions_are_concrete_but_sanitized():
    patch = r'const patch = "*** Begin Patch\n*** Update File: /project/codex-token-analyzer/analyzer/activity.py\n*** End Patch";'
    assert tool_description({'name': 'exec', 'input': patch}) == '修改代码或文档：codex-token-analyzer/analyzer/activity.py'
    command = '{"cmd":"PYTHONPATH=. .venv/bin/pytest -q"}'
    assert tool_description({'name': 'exec', 'input': command}) == '运行后端测试（pytest）'
    browser = '{"code":"await tab.goto(\'http://127.0.0.1:8765/\'); await tab.getByRole(\'button\',{name:\'任务明细\'}).click();"}'
    assert '打开 http://127.0.0.1:8765/' in tool_description({'namespace': 'mcp__cua_repl', 'name': 'js', 'arguments': browser})
    assert result_summary({'output': '................................ 45 passed in 1.2s'}) == '后端测试通过：45 项'
    assert result_summary({'output': 'Script failed: secret detail'}) == '执行失败，详细错误未展示'


def test_read_turn_activity_groups_tools_and_deduplicates(tmp_path):
    root = tmp_path / 'source'
    sessions = root / 'sessions'
    sessions.mkdir(parents=True)
    path = sessions / 'task.jsonl'
    path.write_text('\n'.join([
        line('event_msg', {'type': 'task_started', 'turn_id': 'turn-1', 'model_context_window': 200000}, '2026-09-09T00:59:59Z'),
        line('turn_context', {'turn_id': 'turn-1', 'model': 'test-model'}),
        line('response_item', {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': '## My request:\n分析 Token 用量'}]}, '2026-09-09T01:00:01Z'),
        line('response_item', {'type': 'reasoning'}, '2026-09-09T01:00:02Z'),
        line('response_item', {'type': 'custom_tool_call', 'name': 'exec', 'call_id': 'call-1', 'input': '{"cmd":"cat /Users/me/.codex/memories/MEMORY.md"}'}, '2026-09-09T01:00:03Z'),
        line('response_item', {'type': 'custom_tool_call', 'name': 'exec', 'call_id': 'call-1', 'input': '{}'}, '2026-09-09T01:00:03Z'),
        line('response_item', {'type': 'custom_tool_call_output', 'call_id': 'call-1'}, '2026-09-09T01:00:04Z'),
        line('response_item', {'type': 'function_call', 'namespace': 'mcp__cua_repl', 'name': 'js', 'call_id': 'call-2'}, '2026-09-09T01:00:05Z'),
        line('token_usage_record', {'response_id': 'response-1', 'usage': {'input_tokens': 100, 'cached_input_tokens': 80, 'output_tokens': 10, 'total_tokens': 110}}, '2026-09-09T01:00:06Z'),
        line('compacted', {}, '2026-09-09T01:00:07Z'),
        line('response_item', {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': '已完成分析'}]}, '2026-09-09T01:00:08Z'),
    ]) + '\n')

    result = read_turn_activity([path, tmp_path / 'outside.jsonl'], root, {'turn-1'})['turn-1']

    assert result['request_excerpt'] == '分析 Token 用量'
    assert result['tool_call_count'] == 2
    assert result['tools'] == [
        {'name': '读取记忆', 'count': 1},
        {'name': '浏览器操作', 'count': 1},
    ]
    assert [item['type'] for item in result['timeline']] == [
        'start', 'context', 'request', 'reasoning', 'memory', 'tool', 'model', 'compaction', 'response'
    ]
    assert '200,000 tokens' in result['timeline'][0]['detail']
    memory = result['timeline'][4]
    assert memory['tool'] == '读取'
    assert memory['status'] == '已返回' and memory['duration_ms'] == 1000
    assert '.codex/memories/MEMORY.md' in memory['detail']
    assert '随后发生的可观测操作' in result['timeline'][3]['detail']
    model = result['timeline'][6]
    assert model['model'] == 'test-model'
    assert model['non_cached_input_tokens'] == 20 and model['total_tokens'] == 110
    assert '工具请求' in model['detail']
    assert '已完成分析' in result['timeline'][-1]['detail']
    assert result['context_token_breakdown_available'] is False
