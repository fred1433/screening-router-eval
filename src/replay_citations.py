"""Replay the stricter citation checks (pipeline.citation_problems) on every accepted final output of a run, without
any model call, and report what they would have rejected. Usage: python src/replay_citations.py results/runs/test"""
import json, sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import ROOT, jsonl_read, jdump
import pipeline

run_dir = Path(sys.argv[1])
cases = {c['case_id']: c for c in jsonl_read(ROOT / 'cases' / 'cases.jsonl')}
scored = {(r['case_id'], r['config'], r['rep']): r for r in jsonl_read(run_dir / 'scored.jsonl')}
out = {}
for f in sorted(run_dir.glob('*_r*.jsonl')):
    cfg, rep = f.stem.rsplit('_r', 1)
    o = out.setdefault(cfg, {'outputs': 0, 'would_reject': 0, 'by_check': Counter(), 'by_category': Counter(), 'examples': []})
    for r in jsonl_read(f):
        if not r.get('output'):
            continue
        o['outputs'] += 1
        probs = [p for c in r['output']['comparisons'] if c.get('relation') in ('agree', 'conflict') for p in pipeline.citation_problems(c)]
        if probs:
            o['would_reject'] += 1
            for p in {p[0] for p in probs}:
                o['by_check'][p] += 1
            o['by_category'][scored[(r['case_id'], cfg, int(rep))]['category']] += 1
            if len(o['examples']) < 4:
                o['examples'].append({'case_id': r['case_id'], 'rep': int(rep), 'problems': [p[1] for p in probs][:3]})
for o in out.values():
    o['by_check'], o['by_category'] = dict(o['by_check']), dict(o['by_category'])
jdump(out, run_dir / 'citation_replay.json')
print(json.dumps({k: {x: v[x] for x in ('outputs', 'would_reject', 'by_check', 'by_category')} for k, v in out.items()}, indent=1))
