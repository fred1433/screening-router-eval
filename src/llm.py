"""OpenRouter client with a pinned endpoint. Model names and endpoints come from environment variables
so the harness can point at another endpoint (including a self-hosted OpenAI-compatible server) without code changes."""
import json, os, time, urllib.request, urllib.error
from pathlib import Path

MODELS = {
    'small': {'model': os.environ.get('SMALL_MODEL', 'qwen/qwen3.5-9b'),
              'provider': os.environ.get('SMALL_PROVIDER', 'deepinfra/bf16')},
    'large': {'model': os.environ.get('LARGE_MODEL', 'qwen/qwen3.5-397b-a17b'),
              'provider': os.environ.get('LARGE_PROVIDER', 'deepinfra/fp8')},
}
BASE_URL = os.environ.get('LLM_BASE_URL', 'https://openrouter.ai/api/v1')
SETTINGS = {'temperature': 0, 'max_tokens': 1600, 'reasoning': {'enabled': False},
            'response_format': {'type': 'json_object'}}
TIMEOUT_S = 120
TRANSPORT_RETRIES = 1
# Dollar amounts stay out of the public repository; they go to a private ledger outside it.
LEDGER = os.environ.get('PRIVATE_LEDGER')


class EndpointError(Exception):
    pass


def call(tier, messages, injected_fault=None):
    cfg = MODELS[tier]
    body = {'model': cfg['model'], 'messages': messages,
            'provider': {'order': [cfg['provider']], 'allow_fallbacks': False, 'require_parameters': True},
            'usage': {'include': True}, **SETTINGS}
    last = None
    for attempt in range(TRANSPORT_RETRIES + 1):
        t0 = time.time()
        try:
            if injected_fault == 'endpoint_down':
                raise EndpointError('injected fault: endpoint marked unavailable for this test')
            req = urllib.request.Request(BASE_URL + '/chat/completions', data=json.dumps(body).encode(),
                                         headers={'Authorization': 'Bearer ' + os.environ['OPENROUTER_API_KEY'],
                                                  'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                d = json.loads(r.read())
            if 'error' in d or not d.get('choices'):
                raise EndpointError(str(d.get('error'))[:300])
            ms = int((time.time() - t0) * 1000)
            u = d.get('usage', {})
            if LEDGER:
                with open(LEDGER, 'a') as f:
                    f.write(json.dumps({'id': d.get('id'), 'tier': tier, 'cost': u.get('cost')}) + '\n')
            return {'ok': True, 'tier': tier, 'model_requested': cfg['model'], 'endpoint_requested': cfg['provider'],
                    'model_returned': d.get('model'), 'provider_returned': d.get('provider'), 'generation_id': d.get('id'),
                    'prompt_tokens': u.get('prompt_tokens'), 'completion_tokens': u.get('completion_tokens'),
                    'latency_ms': ms, 'attempt': attempt + 1, 'content': d['choices'][0]['message'].get('content') or ''}
        except (urllib.error.URLError, TimeoutError, EndpointError, json.JSONDecodeError, ConnectionError) as e:
            last = {'ok': False, 'tier': tier, 'model_requested': cfg['model'], 'endpoint_requested': cfg['provider'],
                    'error': f'{type(e).__name__}: {str(e)[:200]}', 'latency_ms': int((time.time() - t0) * 1000),
                    'attempt': attempt + 1, 'injected': injected_fault == 'endpoint_down'}
            if injected_fault == 'endpoint_down':
                break
            time.sleep(2)
    return last
