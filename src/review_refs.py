"""Independent check of the reference answers by a fresh model session that did not build them.
Reviewer: a new `claude -p --model opus --tools ""` process per batch (Claude Opus, no tools, no memory of the build).
This is a model review, not a review by a human compliance expert.
Usage: python src/review_refs.py"""
import json, re, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import ROOT, jsonl_read, jsonl_write, render_packet

POLICY = (ROOT / 'policy' / 'policy_a.md').read_text()
cases = jsonl_read(ROOT / 'cases' / 'cases.jsonl')
PROMPT = """You are reviewing reference answers for a test set. Each case has an evidence packet (customer documents C*, one watchlist candidate record L1) and a reference answer written by someone else. Using ONLY the packet and the policy below, check each reference: is the permitted disposition right, are the decisive agreements, conflicts and missing items right, and is anything decisive left out? Be strict; disagree whenever the packet does not support the reference. Write in English.

{policy}

Return only a JSON array, one object per case, in this form:
[{{"case_id": "...", "agrees": true or false, "issue": "empty if agrees, else the precise problem", "your_disposition": "same_entity_supported | different_entity_supported | insufficient_evidence"}}]

Cases:
{cases}
"""


def block(c):
    ref = {k: c['reference'][k] for k in ('name_match', 'permitted_disposition', 'permitted_status', 'decisive_agreements',
                                          'decisive_conflicts', 'missing_information', 'justification')}
    return f"### {c['case_id']}\n{render_packet(c['packet'])}\n\nReference answer:\n{json.dumps(ref, indent=1)}\n"


def review(batch):
    p = PROMPT.format(policy=POLICY, cases='\n'.join(block(c) for c in batch))
    r = subprocess.run(['claude', '-p', '--model', 'opus', '--tools', ''], input=p, capture_output=True, text=True, timeout=900)
    m = re.search(r'\[.*\]', r.stdout, re.S)
    return json.loads(m[0]) if m else [{'case_id': c['case_id'], 'agrees': None, 'issue': 'reviewer output unreadable: ' + r.stdout[:200]} for c in batch]


batches = [cases[i:i + 10] for i in range(0, len(cases), 10)]
with ThreadPoolExecutor(4) as ex:
    res = [x for b in ex.map(review, batches) for x in b]
OUT = ROOT / 'review' / (sys.argv[1] if len(sys.argv) > 1 else 'reference_review.jsonl')
jsonl_write(res, OUT)
dis = [x for x in res if x.get('agrees') is not True]
print(f'reviewed {len(res)}; disagreements or unreadable: {len(dis)}')
for x in dis:
    print(x)
