"""Read a small, non-persistent turn description directly from source logs."""
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


BLOCKS = (
    re.compile(r'<in-app-browser-context\b[^>]*>.*?</in-app-browser-context>', re.S),
    re.compile(r'<recommended_plugins>.*?</recommended_plugins>', re.S),
    re.compile(r'<(?:app-context|skills_instructions|environment_context|permissions instructions|plugins_instructions|apps_instructions|collaboration_mode)\b[^>]*>.*?</(?:app-context|skills_instructions|environment_context|permissions instructions|plugins_instructions|apps_instructions|collaboration_mode)>', re.S),
    re.compile(r'#\s*AGENTS\.md instructions.*?<INSTRUCTIONS>.*?</INSTRUCTIONS>', re.S | re.I),
)


def clean_request(value, limit=360):
    if not isinstance(value, str):
        return None
    for pattern in BLOCKS:
        value = pattern.sub('', value)
    # Ambient wrappers sometimes put the actual request after this marker.
    match = re.search(r'(?:^|\n)#{1,6}\s*My request:\s*(.*)', value, re.I | re.S)
    if match:
        value = match.group(1)
    value = re.sub(r'^\s*#{1,6}\s*My request:\s*', '', value, flags=re.I)
    value = re.sub(r'\s+', ' ', value).strip()
    if not value:
        return None
    return value[:limit - 1] + '…' if len(value) > limit else value


def merge_work_steps(events):
    """Merge adjacent observable events without exposing raw payloads."""
    steps = []
    for event in events:
        if event.get('type') in ('start', 'context', 'complete'):
            continue
        item = dict(event)
        item['event_count'] = 1
        item['raw_lines'] = [event.get('source_line')]
        previous = steps[-1] if steps else None
        same_tool = (previous and item.get('type') in ('tool', 'memory') and
                     previous.get('type') == item.get('type') and
                     previous.get('tool') == item.get('tool') and
                     previous.get('status') == item.get('status'))
        if same_tool:
            previous['event_count'] += 1
            previous['raw_lines'].append(item.get('source_line'))
            previous['title'] = f'{item["title"]} × {previous["event_count"]}'
            previous['duration_ms'] = (previous.get('duration_ms') or 0) + (item.get('duration_ms') or 0)
            previous['associated_tokens'] = (previous.get('associated_tokens') or 0) + (item.get('associated_tokens') or 0)
            if item.get('status') == '返回错误':
                previous['status'] = '包含错误'
            continue
        steps.append(item)
    return steps


def message_text(payload):
    parts = []
    for item in payload.get('content') or []:
        if isinstance(item, dict):
            value = item.get('text') or item.get('input_text')
            if isinstance(value, str):
                parts.append(value)
    return clean_request('\n'.join(parts))


def tool_group(payload):
    namespace, name = payload.get('namespace'), payload.get('name')
    if namespace == 'collaboration':
        return '协作代理'
    if namespace == 'mcp__cua_repl':
        return '浏览器操作'
    if namespace == 'web':
        return '网络检索'
    if namespace == 'image_gen':
        return '图像生成'
    if name == 'exec':
        value = str(payload.get('input', payload.get('arguments', '')))
        if '*** Update File:' in value or '*** Add File:' in value or 'apply_patch' in value:
            return '修改'
        if re.search(r'\b(rg|grep|find)\b', value):
            return '搜索'
        if re.search(r'\b(cat|sed|head|tail|jq)\b', value):
            return '读取'
        return '命令'
    return {
        'wait': '等待后台任务',
        'request_user_input': '用户澄清',
    }.get(name, name or namespace or '其他工具')


def tool_input(payload):
    value = payload.get('input', payload.get('arguments', ''))
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return ''


def decode_unicode_escapes(value):
    return re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), value)


def compact_path(value):
    value = decode_unicode_escapes(value).strip('`\"\'.,:()[]{}\\')
    for marker in ('codex-token-analyzer/', '.codex/memories/', '.codex/sessions/', '.agents/skills/'):
        if marker in value:
            return marker + value.split(marker, 1)[1]
    if value.startswith('/'):
        parts = Path(value).parts
        return '/'.join(parts[-2:]) if len(parts) > 2 else value
    return value


def command_targets(command, limit=5):
    patterns = (
        r'(?<![\w.:])/(?!/)[^\s;|,\"\'()]+',
        r'(?<![-\w])(?:[\w.-]+/)+[\w.-]+',
        r'(?<![-/\w])[\w.-]+\.(?:py|tsx|css|md|json|jsonl|sh|toml|sqlite)(?!\w)',
    )
    found = []
    for pattern in patterns:
        for value in re.findall(pattern, command):
            value = compact_path(value)
            same_file = any(Path(previous).name == Path(value).name for previous in found)
            if value and not value.startswith(('http:', 'https:')) and value not in found and not same_file:
                found.append(value)
    return found[:limit]


def command_description(command):
    flat = re.sub(r'\s+', ' ', decode_unicode_escapes(command)).strip()
    targets = command_targets(flat)
    target_text = '、'.join(targets)
    if 'pytest' in flat:
        return '运行后端测试（pytest）'
    if 'npm run build' in flat:
        return '构建前端生产包（npm run build）'
    if 'compileall' in flat:
        return '执行 Python 编译检查'
    if 'py_compile' in flat:
        py_targets = '、'.join(path for path in targets if path.endswith('.py'))
        return f'检查 Python 文件语法：{py_targets or "Python 源文件"}'
    if re.search(r'\bbash\s+run\.sh\b', flat):
        return '启动本地 Token 分析服务'
    if re.search(r'(^|[;&|]\s*)kill\s+\d+', flat):
        return '停止旧版本地分析服务'
    if 'lsof ' in flat or 'curl ' in flat:
        return '检查本地服务端口和 API 状态'
    if re.search(r'(^|[;&|]\s*)rg\s', flat):
        return f'搜索{target_text or "项目文件"}中的匹配内容'
    if re.search(r'(^|[;&|]\s*)(sed|cat|head|tail|jq)\s', flat):
        return f'读取或检查：{target_text or "本地记录"}'
    if re.search(r'(^|[;&|]\s*)(find|ls)\s', flat):
        return f'检查文件与目录：{target_text or "当前项目"}'
    if 'coze auth status' in flat:
        return '检查 Coze CLI 登录状态'
    if re.search(r'(?:\S*/)?python(?:3)?\s+-\s+<<', flat):
        return '运行临时 Python 脚本，检查解析或统计结果'
    script = re.search(r'(?:\S*/)?python(?:3)?\s+([^\s]+\.py)\b', flat)
    if script:
        return f'运行 Python 脚本：{compact_path(script.group(1))}'
    first = re.match(r'(?:[A-Z_]+=\S+\s+)*(?:\S*/)?([\w.-]+)', flat)
    return f'执行本地命令：{first.group(1) if first else "未知命令"}' + (f'，涉及 {target_text}' if target_text else '')


def exec_commands(value):
    try:
        direct = json.loads(value)
    except (TypeError, ValueError):
        direct = None
    if isinstance(direct, dict) and isinstance(direct.get('cmd'), str):
        return [direct['cmd']]
    commands = []
    for match in re.finditer(r'\"cmd\"\s*:\s*\"((?:\\.|[^\"\\])*)\"', value):
        try:
            commands.append(json.loads('"' + match.group(1) + '"'))
        except (TypeError, ValueError):
            continue
    return commands


def browser_description(value):
    actions = []
    urls = re.findall(r'https?://[^\'\"\s)]+', value)
    if urls:
        actions.append('打开 ' + urls[0].split('?', 1)[0])
    names = re.findall(r'name\s*:\s*[\'\"]([^\'\"]+)', value)
    if names:
        labels = '、'.join(f'“{clean_request(name, 55)}”' for name in names[:3])
        actions.append('点击或查找 ' + labels)
    if 'reload(' in value:
        actions.append('刷新页面')
    if any(word in value for word in ('domSnapshot', 'innerText', 'textContent', 'dev.logs')):
        actions.append('读取页面内容并核验')
    return '；'.join(actions) or '操作并检查本地网页'


def tool_description(payload):
    value = tool_input(payload)
    namespace, name = payload.get('namespace'), payload.get('name')
    patch_view = value.replace('\\n', '\n')
    patch_files = re.findall(r'^\*\*\* (?:Add|Update|Delete) File: (.+)$', patch_view, re.M)
    if patch_files:
        unique = []
        for path in patch_files:
            path = compact_path(path)
            if path and path not in unique:
                unique.append(path)
        files = '、'.join(unique[:6])
        return f'修改代码或文档：{files}'
    if name == 'exec':
        commands = exec_commands(value)
        if commands:
            descriptions = []
            for command in commands[:4]:
                description = command_description(command)
                if description not in descriptions:
                    descriptions.append(description)
            return '；'.join(descriptions)
        return '执行本地代码或文件操作'
    if namespace == 'mcp__cua_repl':
        return browser_description(value)
    if namespace == 'collaboration':
        return {
            'list_agents': '查看协作代理状态', 'spawn_agent': '创建协作代理',
            'send_message': '向协作代理发送信息', 'wait_agent': '等待协作代理结果',
        }.get(name, f'执行协作操作：{name or "未知"}')
    if name == 'request_user_input':
        return '向用户请求补充信息或选择'
    if namespace == 'web':
        return '搜索或读取网络资料'
    return f'调用工具：{namespace + "." if namespace else ""}{name or "未知"}'


def collaboration_title(payload):
    return {
        'list_agents': '查询代理状态',
        'spawn_agent': '创建协作代理',
        'send_message': '发送协作消息',
        'followup_task': '追加协作任务',
        'wait_agent': '等待协作代理',
        'interrupt_agent': '中断协作代理',
    }.get(payload.get('name'), '协作代理操作')


def collaboration_target(payload):
    """Expose the agent path/name, never the delegated prompt or message body."""
    value = tool_input(payload)
    try:
        arguments = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        arguments = None
    if not isinstance(arguments, dict):
        return None
    target = arguments.get('target') or arguments.get('task_name')
    if isinstance(target, str) and target.strip():
        return clean_request(target, 120)
    targets = arguments.get('targets')
    if isinstance(targets, list):
        labels = []
        for item in targets[:5]:
            if isinstance(item, dict):
                label = item.get('threadId') or item.get('target')
                if isinstance(label, str) and label not in labels:
                    labels.append(label)
        return '、'.join(labels) or None
    return None


def is_memory_access(payload):
    """Classify a tool call without returning its arguments to the client."""
    value = tool_input(payload)
    # The outer exec tool also carries source-code patches. A memory path inside
    # code being edited is not evidence that the turn read that memory.
    if payload.get('name') == 'exec' and value.lstrip().startswith('const patch'):
        return False
    if payload.get('name') == 'exec' and 'tools.exec_command' not in value:
        try:
            direct = json.loads(value)
        except (TypeError, ValueError):
            return False
        if not isinstance(direct, dict) or not isinstance(direct.get('cmd'), str):
            return False
    return bool(re.search(r'(?:\.codex[/\\]memories|memory_summary\.md|(?:^|[/\\])MEMORY\.md|rollout_summaries)', value, re.I))


def elapsed_ms(start, end):
    try:
        return max(0, round((datetime.fromisoformat(end.replace('Z', '+00:00')) -
                             datetime.fromisoformat(start.replace('Z', '+00:00'))).total_seconds() * 1000))
    except (AttributeError, TypeError, ValueError):
        return None


def flow_event(kind, timestamp, line, title, **extra):
    return dict(type=kind, timestamp=timestamp, source_line=line, title=title, **extra)


def output_failed(payload):
    try:
        value = json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        value = ''
    return bool(re.search(r'(Script failed|"isError"\s*:\s*true|"exit_code"\s*:\s*[1-9]\d*)', value, re.I))


def result_summary(payload):
    strings = []

    def collect(value):
        if isinstance(value, str):
            strings.append(value)
        elif isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, dict):
            for key in ('text', 'output', 'stdout', 'stderr', 'content'):
                if key in value:
                    collect(value[key])

    collect(payload)
    value = '\n'.join(strings)
    if output_failed(payload):
        return '执行失败，详细错误未展示'
    passed = re.search(r'(\d+) passed', value)
    if passed:
        return f'后端测试通过：{passed.group(1)} 项'
    if '✓ built in' in value or 'built in ' in value and 'vite' in value.lower():
        return '前端生产构建成功'
    if 'Uvicorn running on http://127.0.0.1:8765' in value:
        return '本地服务已启动，监听 127.0.0.1:8765'
    lines = [line for line in value.splitlines() if line.strip()]
    if lines:
        return f'已返回 {len(lines)} 行结果，正文未展示'
    return '工具已返回结果'


def read_turn_activity(paths, source_root, turn_ids):
    """Return request excerpts and tool counts without writing them to SQLite."""
    root = Path(source_root).resolve()
    wanted = set(turn_ids)
    requests, tools, timelines = {}, defaultdict(Counter), defaultdict(list)
    seen_calls, pending_tools = set(), {}
    pending_reasoning, model_actions = {}, {}

    def connect_reasoning(turn, description, planning=False):
        item = pending_reasoning.pop(turn, None)
        if item:
            if planning:
                item['type'] = 'planning'
                item['title'] = '执行规划'
                item['detail'] = f'根据随后发生的可观测操作，本阶段规划了下一步：{description}。'
            else:
                item['detail'] = f'日志确认模型在生成下一步结果前进行了推理；随后发生：{description}。内部推理原文不可见。'
    for raw_path in sorted(set(paths)):
        path = Path(raw_path).resolve()
        if path.suffix != '.jsonl' or root not in path.parents or not path.is_file():
            continue
        active = None
        try:
            stream = path.open(errors='replace')
        except OSError:
            continue
        with stream:
            model = None
            for line_number, line in enumerate(stream, 1):
                try:
                    event = json.loads(line)
                except (ValueError, TypeError):
                    continue
                payload = event.get('payload') if isinstance(event, dict) else None
                if not isinstance(payload, dict):
                    continue
                if event.get('type') == 'event_msg' and payload.get('type') == 'task_started':
                    active = payload.get('turn_id')
                    if active in wanted:
                        window = payload.get('model_context_window')
                        detail = '开始处理该轮请求'
                        if type(window) is int:
                            detail += f'；模型上下文窗口 {window:,} tokens'
                        timelines[active].append(flow_event(
                            'start', event.get('timestamp'), line_number, '轮次启动', detail=detail,context_window=window if type(window) is int else None))
                    continue
                if event.get('type') == 'turn_context':
                    active = payload.get('turn_id')
                    model = payload.get('model') or model
                    if active in wanted:
                        context = [f'模型 {model}' if model else '模型未记录']
                        if payload.get('effort'):
                            context.append(f'推理强度 {payload["effort"]}')
                        if payload.get('cwd'):
                            context.append(f'工作目录 {compact_path(payload["cwd"])}')
                        roots = payload.get('workspace_roots')
                        if isinstance(roots, list):
                            context.append(f'{len(roots)} 个工作区根目录')
                        timelines[active].append(flow_event(
                            'context', event.get('timestamp'), line_number, '上下文准备', model=model,
                            service_tier=payload.get('service_tier'),
                            detail='；'.join(context) + '。日志未记录各上下文片段的 Token 分解。'))
                    continue
                if active not in wanted:
                    continue
                event_type = event.get('type')
                if event_type == 'token_usage_record':
                    usage = payload.get('usage') if isinstance(payload.get('usage'), dict) else {}
                    input_tokens = usage.get('input_tokens') if type(usage.get('input_tokens')) is int else None
                    cached_tokens = usage.get('cached_input_tokens') if type(usage.get('cached_input_tokens')) is int else None
                    output_tokens = usage.get('output_tokens') if type(usage.get('output_tokens')) is int else None
                    reasoning_tokens = usage.get('reasoning_output_tokens') if type(usage.get('reasoning_output_tokens')) is int else None
                    total_tokens = usage.get('total_tokens') if type(usage.get('total_tokens')) is int else None
                    action = model_actions.pop(active, None)
                    pending = next((item for (turn, _), item in reversed(list(pending_tools.items()))
                                    if turn == active and item.get('associated_tokens') is None), None)
                    if pending is not None:
                        pending['associated_tokens'] = total_tokens
                    timelines[active].append(flow_event(
                        'model', event.get('timestamp'), line_number, '模型交互', model=model,
                        detail=(f'本次模型响应{action}。' if action else '本次模型响应的具体产物未在日志中单独标注。'),
                        response_id=payload.get('response_id'), input_tokens=input_tokens,
                        cached_input_tokens=cached_tokens,
                        non_cached_input_tokens=(input_tokens-cached_tokens if input_tokens is not None and cached_tokens is not None and cached_tokens <= input_tokens else None),
                        output_tokens=output_tokens, reasoning_output_tokens=reasoning_tokens,
                        total_tokens=total_tokens))
                    continue
                if event_type == 'compacted':
                    connect_reasoning(active, '压缩并重建上下文', planning=True)
                    timelines[active].append(flow_event(
                        'compaction', event.get('timestamp'), line_number, '上下文压缩',
                        detail='日志记录了压缩事件，不展示压缩后的对话内容。'))
                    continue
                if event_type == 'event_msg' and payload.get('type') == 'model_rerouted':
                    model = payload.get('to_model') or model
                    timelines[active].append(flow_event(
                        'context', event.get('timestamp'), line_number, '模型切换', model=model))
                    continue
                if event_type == 'event_msg' and payload.get('type') in ('task_complete', 'task_completed', 'turn_aborted'):
                    title = '轮次中止' if payload.get('type') == 'turn_aborted' else '轮次完成'
                    connect_reasoning(active, title)
                    timelines[active].append(flow_event('complete', event.get('timestamp'), line_number, title))
                    continue
                if event_type != 'response_item':
                    continue
                subtype = payload.get('type')
                if subtype == 'message' and payload.get('role') == 'user' and active not in requests:
                    excerpt = message_text(payload)
                    if excerpt:
                        requests[active] = excerpt
                        timelines[active].append(flow_event(
                            'request', event.get('timestamp'), line_number, '用户请求', detail=excerpt))
                elif subtype == 'reasoning':
                    item = flow_event(
                        'reasoning', event.get('timestamp'), line_number, '模型推理',
                        detail='日志可确认此处发生了推理，但不展示内部推理原文。')
                    timelines[active].append(item)
                    pending_reasoning[active] = item
                elif subtype == 'message' and payload.get('role') == 'assistant':
                    excerpt = message_text(payload)
                    description = f'生成用户可见消息：{excerpt}' if excerpt else '生成用户可见消息'
                    connect_reasoning(active, description)
                    timelines[active].append(flow_event(
                        'response', event.get('timestamp'), line_number, '模型输出消息',
                        detail=description))
                    model_actions[active] = f'生成了用户可见消息' + (f'：{excerpt}' if excerpt else '')
                elif subtype in ('function_call', 'custom_tool_call'):
                    call_id = payload.get('call_id') or payload.get('id')
                    identity = (active, call_id)
                    if call_id and identity in seen_calls:
                        continue
                    if call_id:
                        seen_calls.add(identity)
                    group = '读取记忆' if is_memory_access(payload) else tool_group(payload)
                    description = tool_description(payload)
                    connect_reasoning(active, description, planning=True)
                    tools[active][group] += 1
                    is_collaboration = payload.get('namespace') == 'collaboration'
                    kind = 'memory' if group == '读取记忆' else ('collaboration' if is_collaboration else 'tool')
                    title = collaboration_title(payload) if is_collaboration else group
                    target = collaboration_target(payload) if is_collaboration else None
                    if target:
                        description += f'；目标：{target}'
                    item = flow_event(
                        kind, event.get('timestamp'), line_number,
                        title, tool=tool_group(payload), model=model, detail=description,
                        status='等待返回', duration_ms=None)
                    timelines[active].append(item)
                    model_actions[active] = f'产生了工具请求：{description}'
                    if call_id:
                        pending_tools[(active, call_id)] = item
                elif subtype in ('function_call_output', 'custom_tool_call_output'):
                    call_id = payload.get('call_id') or payload.get('id')
                    item = pending_tools.get((active, call_id))
                    if item:
                        item['status'] = '返回错误' if output_failed(payload) else '已返回'
                        item['duration_ms'] = elapsed_ms(item.get('timestamp'), event.get('timestamp'))
                        item['result'] = result_summary(payload)
    return {
        turn: {
            'request_excerpt': requests.get(turn),
            'tools': [{'name': name, 'count': count} for name, count in tools[turn].most_common()],
            'tool_call_count': sum(tools[turn].values()),
            'timeline': sorted(timelines[turn], key=lambda item: (item.get('timestamp') or '', item['source_line'])),
            'steps': merge_work_steps(sorted(timelines[turn], key=lambda item: (item.get('timestamp') or '', item['source_line']))),
            'context_token_breakdown_available': False,
        }
        for turn in wanted
    }
