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

def swap(text):
    return text.replace('<', '(').replace('>', ')')

swapped = [{'type': 'text', 'text': swap(x)} for x in parts]
variants = {
    'I_real_noangle': body(prompt_cache_options=None, messages=[{'role': 'system', 'content': swapped}, {'role': 'user', 'content': user}]),
    'K_ascii_long_noangle': {'model': 'pearl', 'max_tokens': 1024, 'messages': [{'role': 'system', 'content': 'Translate carefully. ' * 280}, {'role': 'user', 'content': 'Folktails Pump'}]},
    'L_short_angle': {'model': 'pearl', 'max_tokens': 1024, 'messages': [{'role': 'user', 'content': 'Reply with ok <b>x</b> <i>y</i>'}]},
    'M_short_amp': {'model': 'pearl', 'max_tokens': 1024, 'messages': [{'role': 'user', 'content': 'Reply with ok A & B'}]},
    'N_short_nonascii': {'model': 'pearl', 'max_tokens': 1024, 'messages': [{'role': 'user', 'content': 'Reply with ok 你好 世界 日本語'}]},
    'O_long_angle': {'model': 'pearl', 'max_tokens': 1024, 'messages': [{'role': 'system', 'content': 'Translate carefully. ' * 280 + ' <b>x</b>'}, {'role': 'user', 'content': 'Folktails Pump'}]},
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
