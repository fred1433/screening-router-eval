"""Two illustrative policies on the same factual case. The case is an individual from the exact-match family whose
customer record has its passport number removed (labelled in the record), so that one strong identifier (date of birth)
and weak ones agree: policy A (two agreeing identifiers, one strong) and policy B (two agreeing strong identifiers) then
differ. Usage: python src/two_policies.py"""
import copy, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import ROOT, jsonl_read, jsonl_write
import pipeline

cases = [c for c in jsonl_read(ROOT / 'cases' / 'cases.jsonl') if c['split'] == 'test']
base = next(c for c in cases if c['family'] == 'exact_match' and 'Full name' in c['packet']['documents'][0]['text']
            and 'Passport number: not captured' not in c['packet']['documents'][0]['text']
            and not any(d['kind'] == 'free_text' for d in c['packet']['documents']))
pc = copy.deepcopy(base)
pc['case_id'] = base['case_id'] + '-no-passport'
d = pc['packet']['documents'][0]
d['text'] = re.sub(r'^Passport number: .*$', 'Passport number: not captured', d['text'], flags=re.M)
pc['construction_note'] = f"Variant of {base['case_id']}: the customer passport number is removed to show two policies diverging."
rows = []
for policy in ('A', 'B'):
    for cfg in ('deterministic', 'small'):
        r = pipeline.run_case(cfg, pc, policy)
        rows.append({'policy': policy, 'variant_of': base['case_id'], **r})
        print('policy', policy, cfg, (r['output'] or {}).get('disposition'), (r['output'] or {}).get('status'), flush=True)
jsonl_write(rows, ROOT / 'results' / 'faults' / 'two_policies.jsonl')
jsonl_write([pc], ROOT / 'results' / 'faults' / 'two_policies_case.jsonl')
