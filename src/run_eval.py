"""Run configurations over a split. Usage:
python src/run_eval.py --split test --reps 3 --configs deterministic,small,large,router [--out results/runs/test]"""
import argparse, json, os, subprocess, sys, time, datetime
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import ROOT, jsonl_read, jsonl_write, jdump
import llm, pipeline

ap = argparse.ArgumentParser()
ap.add_argument('--split', default='dev')
ap.add_argument('--reps', type=int, default=1)
ap.add_argument('--configs', default='deterministic,small,large,router')
ap.add_argument('--cases', default=str(ROOT / 'cases' / 'cases.jsonl'))
ap.add_argument('--out', default=None)
ap.add_argument('--concurrency', type=int, default=6)
ap.add_argument('--policy', default=pipeline.DEFAULT_POLICY)
a = ap.parse_args()

cases = [c for c in jsonl_read(a.cases) if a.split == 'all' or c['split'] == a.split]
out_dir = Path(a.out or ROOT / 'results' / 'runs' / a.split)
out_dir.mkdir(parents=True, exist_ok=True)
commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
manifest = {'started_utc': datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat(timespec='seconds') + 'Z', 'code_commit': commit,
            'split': a.split, 'n_cases': len(cases), 'reps': a.reps, 'configs': a.configs.split(','),
            'policy': a.policy, 'models': llm.MODELS, 'settings': llm.SETTINGS,
            'routing': {'allow_fallbacks': False, 'require_parameters': True, 'base_url': llm.BASE_URL},
            'concurrency': a.concurrency, 'transport_retries': llm.TRANSPORT_RETRIES,
            'repair_budget_per_model': pipeline.REPAIR_BUDGET, 'timeout_s': llm.TIMEOUT_S,
            'latency_note': 'Wall-clock per case on the client, network included, cases run concurrently.',
            'model_identifier_note': 'OpenRouter model ids are aliases that the host can update; they are not immutable versions.',
            'quantization_note': 'Taken from the endpoint tag (bf16, fp8) as published by OpenRouter for that endpoint.'}
for cfg in a.configs.split(','):
    for rep in range(1, a.reps + 1):
        t0 = time.time()
        with ThreadPoolExecutor(a.concurrency if cfg != 'deterministic' else 1) as ex:
            rows = list(ex.map(lambda c: {**pipeline.run_case(cfg, c, a.policy), 'rep': rep}, cases))
        jsonl_write(rows, out_dir / f'{cfg}_r{rep}.jsonl')
        print(f'{cfg} rep {rep}: {len(rows)} cases in {time.time() - t0:.0f}s', flush=True)
calls = [c for f in out_dir.glob('*_r*.jsonl') for r in jsonl_read(f) for st in r['trace'] for c in st.get('calls', [])]
manifest['logical_model_calls'] = len(calls)
manifest['http_attempts'] = sum(len(c.get('http_attempts') or [None] * (c.get('attempt') or 1)) for c in calls)
manifest['failed_http_attempts'] = sum(1 for c in calls for a in (c.get('http_attempts') or []) if not a.get('ok'))
manifest['finished_utc'] = datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat(timespec='seconds') + 'Z'
jdump(manifest, out_dir / 'manifest.json')
