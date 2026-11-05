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

def sysmsg(n, role='system'):
    return {'model': 'pearl', 'max_tokens': 1024, 'messages': [{'role': role, 'content': 'a' * n}, {'role': 'user', 'content': 'Reply with ok'}]}

variants = {}
for n in (200, 500, 700, 1000, 1400, 2000, 2800, 3500, 4500):
    variants[f'S_sys_{n}'] = sysmsg(n)
variants['U_user_5000'] = {'model': 'pearl', 'max_tokens': 1024, 'messages': [{'role': 'user', 'content': 'Reply with ok ' + 'a' * 5000}]}
variants['W_sys_2000_out64'] = {'model': 'pearl', 'max_tokens': 64, 'messages': [{'role': 'system', 'content': 'a' * 2000}, {'role': 'user', 'content': 'Reply with ok'}]}
variants['X_sys_5000_out16'] = {'model': 'pearl', 'max_tokens': 16, 'messages': [{'role': 'system', 'content': 'a' * 5000}, {'role': 'user', 'content': 'Reply with ok'}]}
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
