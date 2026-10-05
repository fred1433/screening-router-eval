"""Score runs against the reference answers. Usage: python src/score.py results/runs/test
Writes <dir>/scored.jsonl and <dir>/summary.json (every figure on the page comes from summary.json)."""
import json, math, sys, statistics
from collections import Counter, defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import ROOT, STRONG, jsonl_read, jsonl_write, jdump

run_dir = Path(sys.argv[1])
cases = {c['case_id']: c for c in jsonl_read(ROOT / 'cases' / 'cases.jsonl')}
if (ROOT / 'cases' / 'cases_renamed.jsonl').exists():
    cases.update({c['case_id']: c for c in jsonl_read(ROOT / 'cases' / 'cases_renamed.jsonl')})
PRICES = {}  # relative index only: per-token list prices of the pinned endpoints, normalised to the small model
try:
    PRICES = json.loads((ROOT / 'results' / 'price_index.json').read_text())
except FileNotFoundError:
    pass


def wilson(k, n, z=1.96):
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0, c - h), 3), round(min(1, c + h), 3)]


def support_problems(out, ref):
    """Reference-based fidelity checks (beyond the runtime validators)."""
    probs = []
    rel = defaultdict(set)
    for c in out['comparisons']:
        rel[c['attribute']].add(c['relation'])
    for a in ref['decisive_conflicts']:
        if 'conflict' not in rel.get(a, set()):
            probs.append(f'contradiction not covered: {a}')
    for a in ref['decisive_conflicts'] + ref['missing_information']:
        if 'agree' in rel.get(a, set()):
            probs.append(f'claims agreement on {a} that the reference marks as ' +
                         ('conflicting' if a in ref['decisive_conflicts'] else 'missing'))
    for a in ('date_of_birth', 'passport_number', 'national_id', 'registration_number'):
        if 'agree' in rel.get(a, set()) and a not in ref['decisive_agreements']:
            probs.append(f'unsupported agreement on {a}')
    return probs


def classify(row):
    ref = cases[row['case_id']]['reference']
    out = row.get('output')
    if row['resolution'] == 'endpoint_failed':
        return 'operational_failure', []
    if out is None or out['disposition'] == 'insufficient_evidence':
        if ref['permitted_disposition'] == 'insufficient_evidence':
            probs = support_problems(out, ref) if out else []
            return 'unresolved_right', probs
        return 'unresolved_wrong', []
    if out['disposition'] != ref['permitted_disposition']:
        return 'incorrect_or_unsupported', ['wrong disposition']
    probs = support_problems(out, ref)
    return ('correct_supported' if not probs else 'incorrect_or_unsupported'), probs


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    f = math.floor(k)
    return round(xs[f] + (xs[min(f + 1, len(xs) - 1)] - xs[f]) * (k - f))


runs = defaultdict(dict)
first_fail = defaultdict(lambda: defaultdict(Counter))  # cfg -> tier -> check -> count of model stages whose first answer failed it
scored = []
for f in sorted(run_dir.glob('*_r*.jsonl')):
    cfg, rep = f.stem.rsplit('_r', 1)
    for row in jsonl_read(f):
        cat, probs = classify(row)
        for st in row['trace']:
            if st.get('attempts'):
                ff = st['attempts'][0]['checks_failed']
                first_fail[cfg][st['step']]['stages'] += 1
                if ff:
                    first_fail[cfg][st['step']]['failed_first_answer'] += 1
                    for chk in sorted({x[0] for x in ff}):
                        first_fail[cfg][st['step']][chk] += 1
                    if st.get('output') is not None:
                        first_fail[cfg][st['step']]['repaired'] += 1
        calls = [c for s in row['trace'] for c in s.get('calls', [])]
        row2 = {'case_id': row['case_id'], 'config': cfg, 'rep': int(rep), 'category': cat, 'problems': probs,
                'disposition': row['output']['disposition'] if row.get('output') else 'unresolved',
                'resolution': row['resolution'], 'route': row['route'], 'escalation_reason': row.get('escalation_reason'),
                'latency_ms': row['latency_ms'], 'n_calls': len(calls),
                'calls_by_tier': dict(Counter(c['tier'] for c in calls)),
                'tokens_in': sum(c.get('prompt_tokens') or 0 for c in calls),
                'tokens_out': sum(c.get('completion_tokens') or 0 for c in calls),
                'resource_index': sum(((c.get('prompt_tokens') or 0) * PRICES.get(c['tier'], {}).get('in', 0) +
                                       (c.get('completion_tokens') or 0) * PRICES.get(c['tier'], {}).get('out', 0)) for c in calls),
                'facts': sorted(f"{c['attribute']}:{c['relation']}" for c in (row['output'] or {}).get('comparisons', [])
                                if c['relation'] != 'not_available') if row.get('output') else None,
                'missing': sorted((row['output'] or {}).get('missing_information', [])) if row.get('output') else None}
        if row.get('escalation_reason') is not None:
            so = row.get('small_output')
            fake = {**row, 'output': so, 'resolution': 'decided' if so else 'unresolved_validation'}
            if so:
                row2['small_category'] = classify(fake)[0]
            else:  # the small output failed its checks: the router would have stopped there anyway
                ok_stop = cases[row['case_id']]['reference']['permitted_disposition'] == 'insufficient_evidence'
                row2['small_category'] = 'unresolved_right' if ok_stop else 'unresolved_wrong'
                row2['small_invalid'] = True
        runs[cfg][int(rep)] = runs[cfg].get(int(rep), []) + [row2]
        scored.append(row2)
jsonl_write(scored, run_dir / 'scored.jsonl')

summary = {'n_cases': None, 'configs': {}}
for cfg, reps in runs.items():
    rows1 = reps[1]
    n = len(rows1); summary['n_cases'] = n
    ref = lambda r: cases[r['case_id']]['reference']
    allrows = [r for rs in reps.values() for r in rs]
    def rate(pred_den, pred_num, rows=None):
        rows = rows if rows is not None else allrows
        den = [r for r in rows if pred_den(r)]
        num = [r for r in den if pred_num(r)]
        cn, ck = len({r['case_id'] for r in den}), len({r['case_id'] for r in num})
        # runs at temperature 0 are not independent: the interval is computed over cases (a case counts if any run hits)
        return {'k': len(num), 'n': len(den), 'rate': round(len(num) / len(den), 3) if den else None,
                'cases_any_run': ck, 'of_cases': cn, 'ci95_cases': wilson(ck, cn)}
    true_match = lambda r: ref(r)['construction_identity'] == 'same'
    distinct = lambda r: ref(r)['construction_identity'] == 'different'
    insuff = lambda r: ref(r)['permitted_disposition'] == 'insufficient_evidence'
    answerable = lambda r: not insuff(r)
    cats = Counter(r['category'] for r in rows1)
    cats_all = Counter(r['category'] for r in allrows)
    s = {
        'reps': len(reps), 'scored_rows': len(allrows),
        'outcomes_rep1': dict(cats), 'outcomes_all_runs': dict(cats_all),
        'wrong_exclusion_true_matches': rate(true_match, lambda r: r['disposition'] == 'different_entity_supported'),
        'decisive_on_insufficient': rate(insuff, lambda r: r['disposition'] in ('same_entity_supported', 'different_entity_supported')),
        'wrong_match_distinct': rate(distinct, lambda r: r['disposition'] == 'same_entity_supported'),
        'supported_on_answerable': rate(answerable, lambda r: r['category'] == 'correct_supported'),
        'appropriate_unresolved': rate(insuff, lambda r: r['category'] == 'unresolved_right'),
        'unnecessary_unresolved': rate(answerable, lambda r: r['category'] == 'unresolved_wrong'),
        'operational_failures': sum(1 for r in allrows if r['category'] == 'operational_failure'),
        'validation_unresolved': sum(1 for r in allrows if r['resolution'] == 'unresolved_validation'),
        'latency_ms_p50': pct([r['latency_ms'] for r in allrows], .5),
        'latency_ms_p95': pct([r['latency_ms'] for r in allrows], .95),
        'latency_ms_p50_model_runs': pct([r['latency_ms'] for r in allrows if r['n_calls']], .5),
        'latency_ms_p95_model_runs': pct([r['latency_ms'] for r in allrows if r['n_calls']], .95),
        'model_runs': sum(1 for r in allrows if r['n_calls']),
        'calls_per_case': round(statistics.mean(r['n_calls'] for r in allrows), 2),
        'large_calls_per_case': round(statistics.mean(r['calls_by_tier'].get('large', 0) for r in allrows), 2),
        'tokens_in_per_case': round(statistics.mean(r['tokens_in'] for r in allrows)),
        'tokens_out_per_case': round(statistics.mean(r['tokens_out'] for r in allrows)),
        'resource_index_per_case': round(statistics.mean(r['resource_index'] for r in allrows), 2),
        'by_family': {},
    }
    s['first_answer_failed_checks'] = first_fail.get(cfg, {})
    fam = defaultdict(Counter)
    for r in allrows:
        fam[cases[r['case_id']]['family']][r['category']] += 1
    s['by_family'] = {k: dict(v) for k, v in fam.items()}
    esc = [r for r in allrows if r.get('escalation_reason')]
    if cfg == 'router':
        eff = Counter()
        for r in esc:
            a, b = r['small_category'], r['category']
            good = ('correct_supported', 'unresolved_right')
            if b in good and a not in good:
                eff['corrected'] += 1
            elif b not in good and a not in good:
                eff['left_error'] += 1
            elif b not in good and a in good:
                eff['introduced_error'] += 1
            else:
                eff['no_material_change'] += 1
        s['escalations'] = {'n': len(esc), 'of_rows': len(allrows), 'reasons': dict(Counter(r['escalation_reason'].split(':')[0] for r in esc)),
                            'effect': dict(eff),
                            'small_invalid_replaced_by_stop': sum(1 for r in esc if r.get('small_invalid') and r['category'] == 'unresolved_right'),
                            'small_invalid_corrected_by_large': sum(1 for r in esc if r.get('small_invalid') and r['category'] == 'correct_supported'),
                            'stopped_without_large_call': sum(1 for r in allrows if r['route'] == ['rules', 'small'] and r['disposition'] == 'insufficient_evidence'),
                            'decided_by_rules': sum(1 for r in allrows if r['route'] == ['rules'])}
    # stability across repetitions
    if len(reps) > 1:
        by_case = defaultdict(list)
        for r in allrows:
            by_case[r['case_id']].append(r)
        st = Counter()
        for cid, rs in by_case.items():
            st['of_cases'] += 1
            st['cases_same_disposition_all_reps'] += len({r['disposition'] for r in rs}) == 1
            st['cases_same_route_all_reps'] += len({tuple(r['route']) for r in rs}) == 1
            st['cases_same_facts_all_reps'] += len({tuple(r['facts'] or []) for r in rs}) == 1
            st['cases_same_missing_all_reps'] += len({tuple(r['missing'] or []) for r in rs}) == 1
        s['stability'] = dict(st)
    summary['configs'][cfg] = s
base = summary['configs'].get('small', {}).get('resource_index_per_case')
for cfg, s_ in summary['configs'].items():
    s_['relative_cost_index'] = round(s_['resource_index_per_case'] / base, 2) if base else None
    s_['first_answer_failed_checks'] = {k: dict(v) for k, v in s_['first_answer_failed_checks'].items()}
jdump(summary, run_dir / 'summary.json')
for cfg, s in summary['configs'].items():
    print(cfg, s['outcomes_all_runs'], 'wrongExcl', s['wrong_exclusion_true_matches']['k'], '/', s['wrong_exclusion_true_matches']['n'],
          'p50', s['latency_ms_p50'], 'p95', s['latency_ms_p95'], 'calls', s['calls_per_case'], s.get('escalations', ''), s.get('stability', ''))
