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

variants = {
    'A_tiny_1024': {'model': 'pearl', 'max_tokens': 1024, 'messages': [{'role': 'user', 'content': 'Reply with ok'}]},
    'B_tiny_64': {'model': 'pearl', 'max_tokens': 64, 'messages': [{'role': 'user', 'content': 'Reply with ok'}]},
    'C_real_1024': body(),
    'D_real_512': body(max_tokens=512),
    'E_real_nocache_1024': body(prompt_cache_options=None, messages=[{'role': 'system', 'content': plain_system}, {'role': 'user', 'content': user}]),
    'F_real_maxcompletion_1024': body(max_tokens=None, max_completion_tokens=1024),
    'G_real_4000': body(max_tokens=4000),
    'H_real_string_system_1024': body(prompt_cache_options=None, messages=[{'role': 'system', 'content': '\n\n'.join(parts)}, {'role': 'user', 'content': user}]),
}
for name, payload in variants.items():
    try:
        r = requests.post(url, headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {token}', 'session-id': session}, json=payload, timeout=(10, 120))
        text = r.text.replace(token, '[redacted]')
        if r.status_code == 200:
            j = r.json()
            print(name, r.status_code, 'model=', j.get('model'), 'usage=', json.dumps(j.get('usage'))[:300], 'finish=', j['choices'][0].get('finish_reason'), flush=True)
        else:
            print(name, r.status_code, text[:300], flush=True)
    except Exception as e:
        print(name, 'EXC', str(e).replace(token, '[redacted]')[:200], flush=True)
    time.sleep(2)
