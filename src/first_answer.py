"""What the shared controls bought: re-score the FIRST answer of each model stage as if there were no validators and
no repair call. No new model call; reads the traces of the final run. Usage: python src/first_answer.py results/runs/test"""
import json, sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import ROOT, jsonl_read, jdump, apply_policy

run_dir = Path(sys.argv[1])
cases = {c['case_id']: c for c in jsonl_read(ROOT / 'cases' / 'cases.jsonl')}
sys.argv = [sys.argv[0], str(run_dir)]
out = {}
for cfg in ('small', 'large'):
    cnt, wrong_disp = Counter(), Counter()
    for f in sorted(run_dir.glob(f'{cfg}_r*.jsonl')):
        for r in jsonl_read(f):
            ref = cases[r['case_id']]['reference']
            st = next(s for s in r['trace'] if s['step'] == cfg)
            if not st.get('attempts'):
                cnt['no_answer'] += 1; continue
            p = st['attempts'][0]['parsed']
            if not isinstance(p, dict) or p.get('disposition') not in ('same_entity_supported', 'different_entity_supported', 'insufficient_evidence'):
                cnt['unusable'] += 1; continue
            cnt['answers'] += 1
            ok = p['disposition'] == ref['permitted_disposition']
            cnt['disposition_right' if ok else 'disposition_wrong'] += 1
            if not ok:
                wrong_disp[f"{ref['permitted_disposition']} -> {p['disposition']}"] += 1
            if ref['construction_identity'] == 'same' and p['disposition'] == 'different_entity_supported':
                cnt['wrong_exclusion'] += 1
            if not st['attempts'][0]['checks_failed']:
                cnt['passed_all_checks'] += 1
    out[cfg] = {**cnt, 'wrong_disposition_kinds': dict(wrong_disp)}
jdump(out, run_dir / 'first_answer_only.json')
print(json.dumps(out, indent=1))
