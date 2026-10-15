import json

from analyzer.prompt_history import PromptHistory, _user_text


def test_generated_context_is_removed_before_user_request():
    text = ('<in-app-browser-context source="ambient-ui-state">automatic'
            '</in-app-browser-context>\n\n## My request:\n真正的需求')
    assert _user_text(text) == ('真正的需求', 0)
    assert _user_text('environment_context>\n<current_date>today</current_date>\n'
                      '</environment_context>') == ('', 0)
    assert _user_text('<environment_context>automatic</environment_context>\n真正的需求') == ('真正的需求', 0)
    assert _user_text('# Files mentioned by the user:\n/path/to/image.png\n'
                      '## My request:\n图片中的问题') == ('图片中的问题', 0)


def test_unrecognized_control_content_is_not_displayed():
    assert _user_text('<heartbeat>automatic</heartbeat>') == ('', 0)
    assert _user_text('<future-app-context>automatic</future-app-context>') == ('', 0)
    assert _user_text('<future-app-context source="auto">automatic') == ('', 0)
    assert _user_text('普通用户需求') == ('普通用户需求', 0)


def test_prompt_reader_requires_explicit_user_origin(tmp_path):
    path = tmp_path / 'session.jsonl'

    def message(text, kinds=None):
        payload = {'type': 'message', 'role': 'user',
                   'content': [{'type': 'input_text', 'text': text}]}
        if kinds is not None:
            payload['internal_chat_message_metadata_passthrough'] = {'content_item_kinds': kinds}
        return {'timestamp': '2026-10-14T14:30:27Z', 'type': 'response_item', 'payload': payload}

    entries = [message('无来源标记'),
               message('环境信息', ['environments.environment_context']),
               message('标记数量不符', []),
               message('<future-app-context>automatic</future-app-context>', ['user.text']),
               message('真正的需求', ['user.text'])]
    path.write_text(''.join(json.dumps(entry, ensure_ascii=False) + '\n' for entry in entries))
    assert [item['text'] for item in PromptHistory._read_file(path)['prompts']] == ['真正的需求']
