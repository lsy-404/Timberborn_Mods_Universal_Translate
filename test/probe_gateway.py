import copy, json, os, sys, time
from pathlib import Path
from uuid import uuid4
import requests
import toml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.github/scripts'))
sys.path.insert(0, str(ROOT / '.github/scripts/util'))
from translation_prompt import system_prompt_parts

token = os.environ['LLM_TOKEN']
cfg = toml.load(ROOT / '.github/config/config.toml')
url = cfg['llm']['api_url']
parts = system_prompt_parts('zhCN', 'Simplified Chinese')
system = [{'type': 'text', 'text': p} for p in parts]
system[0]['prompt_cache_breakpoint'] = {'mode': 'explicit'}
system[-1]['prompt_cache_breakpoint'] = {'mode': 'explicit'}
user = json.dumps({'source_text': 'Folktails Pump', 'context': {}}, ensure_ascii=False)
plain_system = [{'type': 'text', 'text': p} for p in parts]
session = str(uuid4())

def body(**kw):
    base = {'model': 'pearl', 'max_tokens': 1024, 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
            'prompt_cache_options': {'mode': 'explicit'}}
    base.update(kw)
    return {k: v for k, v in base.items() if v is not None}

big = 'a' * 3000
variants = {
    'P1_developer_3000': ('chat', {'model': 'pearl', 'max_tokens': 1024, 'messages': [{'role': 'developer', 'content': big}, {'role': 'user', 'content': 'Reply with ok'}]}),
    'P4_system_1char': ('chat', {'model': 'pearl', 'max_tokens': 1024, 'messages': [{'role': 'system', 'content': 'a'}, {'role': 'user', 'content': 'Reply with ok'}]}),
    'P5_responses_instr_3000': ('responses', {'model': 'pearl', 'max_output_tokens': 1024, 'instructions': big, 'input': 'Reply with ok'}),
    'P6_responses_tiny': ('responses', {'model': 'pearl', 'max_output_tokens': 1024, 'input': 'Reply with ok'}),
    'P7_messages_system_3000': ('messages', {'model': 'pearl', 'max_tokens': 1024, 'system': big, 'messages': [{'role': 'user', 'content': 'Reply with ok'}]}),
    'P8_messages_tiny': ('messages', {'model': 'pearl', 'max_tokens': 1024, 'messages': [{'role': 'user', 'content': 'Reply with ok'}]}),
    'P9_chat_user_after_user_long': ('chat', {'model': 'pearl', 'max_tokens': 1024, 'messages': [{'role': 'user', 'content': big}, {'role': 'user', 'content': 'Reply with ok'}]}),
    'P10_chat_system_nomax': ('chat', {'model': 'pearl', 'messages': [{'role': 'system', 'content': 'a'}, {'role': 'user', 'content': 'Reply with ok'}]}),
    'P11_chat_assistant_msg': ('chat', {'model': 'pearl', 'max_tokens': 1024, 'messages': [{'role': 'user', 'content': 'Say hi'}, {'role': 'assistant', 'content': 'hi'}, {'role': 'user', 'content': 'Reply with ok'}]}),
}
paths = {'chat': '/v1/chat/completions', 'responses': '/v1/responses', 'messages': '/v1/messages'}
origin = url.split('/v1/')[0]
for name, (kind, payload) in variants.items():
    try:
        hdr = {'Content-Type': 'application/json', 'Authorization': f'Bearer {token}', 'session-id': session}
        if kind == 'messages':
            hdr['anthropic-version'] = '2023-06-01'
        r = requests.post(origin + paths[kind], headers=hdr, json=payload, timeout=(10, 120))
        text = r.text.replace(token, '[redacted]')
        print(name, r.status_code, text[:260].replace('\n', ' '), flush=True)
    except Exception as e:
        print(name, 'EXC', str(e).replace(token, '[redacted]')[:200], flush=True)
    time.sleep(2)
