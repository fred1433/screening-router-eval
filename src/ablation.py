"""R5: the 'rules then small model' configuration, derived from the router's recorded steps by stopping before the
large-model call. Derived from recorded steps, not an independent run; no latency is reconstructed (latency fields are
set to null in the derived rows and must not be read). Usage: python src/ablation.py <router_run_dir> <out_dir>"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import jsonl_read, jsonl_write, jdump

src, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
for f in sorted(src.glob('router_r*.jsonl')):
    rows = []
    for r in jsonl_read(f):
        if 'large' in r['route']:
            i = next(k for k, s in enumerate(r['trace']) if s['step'] == 'escalate')
            small = next(s for s in r['trace'] if s['step'] == 'small')
            r = {**r, 'output': small.get('output'), 'route': ['rules', 'small'], 'trace': r['trace'][:i],
                 'resolution': 'pending_rerun' if small.get('pending_rerun') else ('decided' if small.get('output') else 'unresolved_validation'),
                 'escalation_reason': None, 'small_output': None, 'stopped_before_large': True}
        r = {**r, 'config': 'rules_small', 'latency_ms': 0, 'derived': True}
        rows.append(r)
    jsonl_write(rows, out / f.name.replace('router_', 'rules_small_'))
jdump({'kind': 'derived from recorded steps, not an independent run', 'source': str(src), 'latency': 'not reconstructed'},
      out / 'manifest.json')
print('written', out)
