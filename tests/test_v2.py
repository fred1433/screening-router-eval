"""v2 tests. No network: model calls are answered by a fake. Run: python3 tests/test_v2.py"""
import copy, io, json, re, sys
from collections import Counter
from pathlib import Path
ROOTP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOTP / 'src'))
from common import ROOT, jsonl_read, name_class, structured_relations
import pipeline, llm

CASES = {c['case_id']: c for c in jsonl_read(ROOT / 'cases' / 'cases.jsonl')}
results = []


def check(name, cond, detail=''):
    results.append((name, bool(cond), detail))


def set_field(case, doc_id, label, value):
    c = copy.deepcopy(case)
    d = next(x for x in c['packet']['documents'] if x['id'] == doc_id)
    d['text'] = re.sub(rf'^{label}: .*$', f'{label}: {value}', d['text'], flags=re.M)
    return c


# ---- R1: an identifier present on the customer side and absent from the list never excludes
c75 = set_field(CASES['case-075'], 'C1', 'Passport number', 'AB123456')
c75 = set_field(c75, 'L1', 'Passport', 'not listed')
out, why = pipeline.deterministic_stage(c75['packet'], 'A2')
check('R1 case-075 with passport AB123456 vs not listed is never different_entity',
      out is None or out['disposition'] != 'different_entity_supported', (out or {}).get('disposition', why))
for label_c, label_l, val, cid in (('Passport number', 'Passport', 'X1234567', 'case-001'),
                                   ('National ID number', 'National ID', '999888777', 'case-001'),
                                   ('Registration number', 'Registration number', '55555555', 'case-081')):
    base = CASES[cid]
    if label_c + ':' not in base['packet']['documents'][0]['text']:
        continue
    c = set_field(set_field(base, 'C1', label_c, val), 'L1', label_l, 'not listed')
    _, rel = structured_relations(c['packet'])
    attr = {'Passport number': 'passport_number', 'National ID number': 'national_id', 'Registration number': 'registration_number'}[label_c]
    check(f'R1 regression: {attr} present for the customer, absent from the list, is not compared', attr not in rel, rel.get(attr))

# ---- R1 / R7: the four configurations cross the same validator; simulated network response
GOOD = {'name_match': 'exact', 'name_cite': {'doc': 'C1', 'quote': 'x'}, 'comparisons': [], 'missing_information': ['date_of_birth'],
        'disposition': 'insufficient_evidence', 'status': 'further_investigation',
        'outstanding_work': 'Obtain the date of birth.', 'rationale': 'test'}


class FakeResp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def fake_urlopen(req, timeout=None):
    body = {'id': 'gen-test', 'model': 'fake', 'provider': 'fake', 'usage': {'prompt_tokens': 10, 'completion_tokens': 5},
            'choices': [{'message': {'content': json.dumps(GOOD)}}]}
    return FakeResp(json.dumps(body).encode())


import os
os.environ.setdefault('OPENROUTER_API_KEY', 'test-key')
llm.urllib.request.urlopen = fake_urlopen
pipeline.CALLER = llm.call
seen = Counter()
orig = pipeline.validate
def spy(out, packet, policy='A'):
    seen[spy.cfg] += 1
    return orig(out, packet, policy)
pipeline.validate = spy
case = CASES['case-047']
structured_only = copy.deepcopy(CASES['case-002'])
for cfg in ('deterministic', 'small', 'large', 'router'):
    spy.cfg = cfg
    r = pipeline.run_case(cfg, structured_only if cfg == 'deterministic' else case, 'A2')
    if cfg != 'deterministic':
        calls = [c for s in r['trace'] for c in s.get('calls', [])]
        check(f'R7 {cfg}: simulated network answer recorded with its HTTP attempts',
              calls and calls[0].get('http_attempts') and calls[0]['http_attempts'][0]['ok'])
check('R1 the four configurations cross the same validator', all(seen[c] > 0 for c in ('deterministic', 'small', 'large', 'router')), dict(seen))
pipeline.validate = orig

# ---- R2: next action never authorizes; the v1 outputs that did are rejected
hits = Counter()
for cfg in ('small', 'large', 'router'):
    for rep in (1, 2, 3):
        for r in jsonl_read(ROOT / 'results' / 'runs' / 'test' / f'{cfg}_r{rep}.jsonl'):
            if r['case_id'] in ('case-019', 'case-029', 'case-085', 'case-013') and r.get('output') and pipeline.next_action_problems(r['output']):
                hits[r['case_id']] += 1
check('R2 v1 next actions of case-019, 029, 085 and 013 are rejected', all(hits[c] for c in ('case-019', 'case-029', 'case-085', 'case-013')), dict(hits))
fin = pipeline.finalize({'disposition': 'same_entity_supported', 'outstanding_work': 'proceed with onboarding'})
check('R2 a supported match gets the fixed next action', fin['outstanding_work'] == pipeline.SAME_ENTITY_ACTION and not pipeline.next_action_problems(fin))

# ---- R3: name class computed by code
check('R3 two different non-Latin names are never exact', name_class('Иван Петров', 'Пётр Иванов', []) == 'unnormalizable')
check('R3 empty normalisation is an explicit stop', name_class('', 'X', []) == 'unnormalizable')
v2r = {}
for rep in (1, 2, 3):
    for r in jsonl_read(ROOT / 'results' / 'runs' / 'v2_replay' / f'router_r{rep}.jsonl'):
        v2r[(r['case_id'], rep)] = r
for cid in ('case-024', 'case-029', 'case-031'):
    rs = [v2r[(cid, rep)] for rep in (1, 2, 3)]
    check(f'R3 {cid} takes the escalation branch in v2', all('large' in r['route'] for r in rs), [r.get('escalation_reason') for r in rs])

# ---- R4: evidence bound to the field and to the customer
c47 = CASES['case-047']
bad = {'attribute': 'nationality', 'relation': 'agree', 'customer_value': 'Russia', 'list_value': 'Russia',
       'customer_cite': {'doc': 'C1', 'quote': 'Residence country: Russia'}, 'list_cite': {'doc': 'L1', 'quote': 'Nationality: Russia'}}
docs = {d['id']: d for d in c47['packet']['documents']}
check('R4 case-047: a Residence country line does not prove a nationality', pipeline.field_problems(bad, docs))
c20 = copy.deepcopy(CASES['case-020'])
c2 = next(d for d in c20['packet']['documents'] if d['id'] == 'C2')
c2['text'] = 'The customer says his brother was born on 01/01/1969 and holds passport number P07834700.'
bro = {'attribute': 'passport_number', 'relation': 'agree', 'customer_value': 'P07834700', 'list_value': 'P07834700',
       'customer_cite': {'doc': 'C2', 'quote': 'his brother was born on 01/01/1969 and holds passport number P07834700'},
       'list_cite': {'doc': 'L1', 'quote': 'Passport: P07834700 (Sudan)'}}
check('R4 case-020 variant: identifiers in a sentence about the brother are rejected',
      pipeline.field_problems(bro, {d['id']: d for d in c20['packet']['documents']}))
sv = {(r['case_id'], r['config'], r['rep']): r for r in jsonl_read(ROOT / 'results' / 'runs' / 'test' / 'scored_v2.jsonl')}
cls = {cid: sv[(cid, 'small', 1)]['dims'] for cid in ('case-050', 'case-075', 'case-077', 'case-079', 'case-080', 'case-030')}
check('R4 v1 outputs of case-050, 075, 077, 079, 080, 030 are classified on the five dimensions', all('decision' in d for d in cls.values()))

# ---- R9: summary fields equal the same computation redone from the traces
S = json.loads((ROOT / 'results' / 'runs' / 'test' / 'summary.json').read_text())
for cfg in ('small', 'large', 'router'):
    rows = [r for rep in (1, 2, 3) for r in jsonl_read(ROOT / 'results' / 'runs' / 'test' / f'{cfg}_r{rep}.jsonl')]
    calls = [c for r in rows for s in r['trace'] for c in s.get('calls', [])]
    check(f'R9 {cfg}: calls per case recomputed from traces', round(len(calls) / len(rows), 2) == S['configs'][cfg]['calls_per_case'])
    lc = sum(1 for c in calls if c['tier'] == 'large')
    check(f'R9 {cfg}: large calls per case recomputed', round(lc / len(rows), 2) == S['configs'][cfg]['large_calls_per_case'])
    excl = sum(1 for r in rows if CASES[r['case_id']]['reference']['construction_identity'] == 'same'
               and (r.get('output') or {}).get('disposition') == 'different_entity_supported')
    check(f'R9 {cfg}: wrong exclusions recomputed', excl == S['configs'][cfg]['wrong_exclusion_true_matches']['k'])
rows = [r for rep in (1, 2, 3) for r in jsonl_read(ROOT / 'results' / 'runs' / 'test' / f'router_r{rep}.jsonl')]
check('R9 router: escalations recomputed', sum(1 for r in rows if r.get('escalation_reason')) == S['configs']['router']['escalations']['n'])
e = S['configs']['router']['escalations']
check('R9 router: invalid small outputs split into stops and corrections',
      e['small_invalid_replaced_by_stop'] + e['small_invalid_corrected_by_large'] == e['reasons'].get('small output failed checks after repair', 0))

# ---- R13: no em dash in the page, the README or deterministic outputs
txt = (ROOT / 'README.md').read_text() + (ROOT / 'MEMO.md').read_text()
if (ROOT / 'site' / 'index.html').exists():
    txt += (ROOT / 'site' / 'index.html').read_text()
txt += pipeline.SAME_ENTITY_ACTION
check('R13 no em dash in page, README, memo, fixed texts', '—' not in txt)

ok = sum(r[1] for r in results)
for n, p, d in results:
    print(('PASS ' if p else 'FAIL ') + n + ('' if p else f'  ({d})'))
print(f'{ok}/{len(results)} passed')
(ROOT / 'results' / 'tests_v2.json').write_text(json.dumps([{'test': n, 'passed': p} for n, p, _ in results], indent=1) + '\n')
sys.exit(0 if ok == len(results) else 1)
