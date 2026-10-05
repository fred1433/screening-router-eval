"""Score runs on five separate dimensions (v2): decision, evidence (right subject and right field), routing, next
action, missing information. Also scores the v1 rows the same way so the two can be compared.
References are re-derived under policy A2 from the reference facts. Usage:
python src/score_v2.py <run_dir> [--cases cases/cases.jsonl] [--policy A2] [--label v2]"""
import argparse, json, re, statistics, sys
from collections import Counter, defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import ROOT, STRONG, jsonl_read, jsonl_write, jdump, apply_policy, structured_relations
import pipeline
from scoring_utils import wilson, support_problems, pct

ap = argparse.ArgumentParser()
ap.add_argument('run_dir'); ap.add_argument('--cases', default=str(ROOT / 'cases' / 'cases.jsonl'))
ap.add_argument('--policy', default='A2'); ap.add_argument('--out', default=None)
a = ap.parse_args()
run_dir = Path(a.run_dir)
cases = {c['case_id']: c for c in jsonl_read(a.cases)}
PRICES = json.loads((ROOT / 'results' / 'price_index.json').read_text())
OBTAIN = re.compile(r'\b(obtain|request|collect|ask|provide|capture|verify|confirm|resolve|clarify|investigate|check|review|record)\w*', re.I)


def permitted(c):
    r = c['reference']
    comps = [{'attribute': x, 'relation': 'agree'} for x in r['decisive_agreements']] + \
            [{'attribute': x, 'relation': 'conflict'} for x in r['decisive_conflicts']]
    return apply_policy(r['name_match'], comps, a.policy)


def needed(c):
    r = c['reference']
    strong = [x for x in r['missing_information'] if x in STRONG]
    return set(strong or r['missing_information'])


def dims(row, c):
    ref = c['reference']; perm = permitted(c); out = row.get('output')
    d = {}
    if row['resolution'] == 'pending_rerun':
        return {'decision': 'pending'}, 'pending_rerun'
    if row['resolution'] == 'endpoint_failed':
        return {'decision': 'fail'}, 'operational_failure'
    stopped = out is None or out['disposition'] == 'insufficient_evidence'
    d['decision'] = 'pass' if (stopped and perm == 'insufficient_evidence') or (not stopped and out['disposition'] == perm) else 'fail'
    if out:
        docs = {x['id']: x for x in c['packet']['documents']}
        ev = [p for x in out['comparisons'] if x.get('relation') in ('agree', 'conflict')
              for p in pipeline.citation_problems(x) + pipeline.field_problems(x, docs)]
        ev += support_problems(out, ref)
        # a conflict claimed on an attribute that the reference marks as missing is not supported either
        for x in out['comparisons']:
            if x.get('relation') == 'conflict' and x['attribute'] in ref['missing_information']:
                ev.append(f"claims a conflict on {x['attribute']}, which is missing")
        d['evidence'] = 'pass' if not ev else 'fail'
        d['evidence_problems'] = [p if isinstance(p, str) else p[1] for p in ev]
        na = pipeline.next_action_problems(out)
        w = out.get('outstanding_work') or ''
        ok_action = not na and (out['disposition'] != 'insufficient_evidence' or bool(OBTAIN.search(w)))
        d['next_action'] = 'pass' if ok_action else 'fail'
        if out['disposition'] == 'insufficient_evidence':
            listed = set(out.get('missing_information') or [])
            compared = {x['attribute'] for x in out['comparisons'] if x.get('relation') in ('agree', 'conflict')}
            d['missing_info'] = 'pass' if (listed & needed(c) or not needed(c)) and not (listed & compared) else 'fail'
        else:
            d['missing_info'] = 'not needed'
        code = pipeline.code_name_class(c['packet'])
        said = out.get('name_match')
        d['name_class'] = 'pass' if said == ref['name_match'] else 'fail'
    if row['config'] == 'router':
        d['routing'] = 'pass' if routing_as_designed(row, c) else 'fail'
    if d['decision'] == 'fail':
        cat = 'unresolved_wrong' if stopped else 'incorrect_or_unsupported'
    elif stopped:
        cat = 'unresolved_right' if d.get('evidence', 'pass') == 'pass' else 'unresolved_right_flagged'
    else:
        cat = 'correct_supported' if d['evidence'] == 'pass' else 'incorrect_or_unsupported'
    return d, cat


def routing_as_designed(row, c):
    """The v2 design: rules decide only structured packets; the large model is called iff the small output failed its
    checks (or its name class), rests on an unlisted spelling, or holds agreeing and conflicting identifiers."""
    route = row['route']
    nm, rel = structured_relations(c['packet'])
    if route == ['rules']:
        return row.get('output') is not None
    small = next((s for s in row['trace'] if s['step'] == 'small'), None)
    if small is None:
        return False
    so = small.get('output')
    if small.get('pending_rerun'):
        return True
    should = so is None or (so['name_match'] == 'transliteration_variant' and so['disposition'] == 'same_entity_supported') or \
        (so['disposition'] == 'insufficient_evidence' and {'agree', 'conflict'} <= {x['relation'] for x in so['comparisons']})
    # v2 name class: a model claim the code does not confirm must escalate
    if so is not None and so.get('name_match') in ('exact', 'listed_alias') and pipeline.code_name_class(c['packet']) == 'none':
        should = True
    return ('large' in route) == should


rows_out, by_cfg = [], defaultdict(list)
for f in sorted(run_dir.glob('*_r*.jsonl')):
    cfg, rep = f.stem.rsplit('_r', 1)
    for row in jsonl_read(f):
        c = cases[row['case_id']]
        d, cat = dims({**row, 'config': cfg}, c)
        calls = [x for s in row['trace'] for x in s.get('calls', [])]
        r2 = {'case_id': row['case_id'], 'config': cfg, 'rep': int(rep), 'category': cat, 'dims': d,
              'permitted': permitted(c), 'identity': c['reference']['construction_identity'],
              'disposition': row['output']['disposition'] if row.get('output') else row['resolution'],
              'route': row['route'], 'resolution': row['resolution'], 'escalation_reason': row.get('escalation_reason'),
              'n_calls': len(calls), 'large_calls': sum(1 for x in calls if x['tier'] == 'large'),
              'large_from_large_alone': any(x.get('source') == 'large_from_large_alone_run' for x in calls),
              'resource_index': sum((x.get('prompt_tokens') or 0) * PRICES[x['tier']]['in'] + (x.get('completion_tokens') or 0) * PRICES[x['tier']]['out'] for x in calls)}
        rows_out.append(r2); by_cfg[cfg].append(r2)
jsonl_write(rows_out, run_dir / 'scored_v2.jsonl')

summary = {'policy': a.policy, 'configs': {}}
for cfg, rows in by_cfg.items():
    n_cases = len({r['case_id'] for r in rows})
    def runs_and_cases(den, num):
        D = [r for r in rows if den(r)]; N = [r for r in D if num(r)]
        cd = {r['case_id'] for r in D}; cn = {r['case_id'] for r in N}
        return {'runs': len(N), 'of_runs': len(D), 'cases_any_run': len(cn), 'of_cases': len(cd), 'ci95_cases': wilson(len(cn), len(cd))}
    s = {'rows': len(rows), 'cases': n_cases,
         'outcomes': dict(Counter(r['category'] for r in rows)),
         'wrong_exclusion_true_matches': runs_and_cases(lambda r: r['identity'] == 'same', lambda r: r['disposition'] == 'different_entity_supported'),
         'decisive_on_insufficient': runs_and_cases(lambda r: r['permitted'] == 'insufficient_evidence', lambda r: r['disposition'] in ('same_entity_supported', 'different_entity_supported')),
         'wrong_match_distinct': runs_and_cases(lambda r: r['identity'] == 'different', lambda r: r['disposition'] == 'same_entity_supported'),
         'dimensions': {}}
    for dim in ('decision', 'evidence', 'next_action', 'missing_info', 'name_class', 'routing'):
        vals = Counter(r['dims'].get(dim) for r in rows if r['dims'].get(dim) is not None)
        if vals:
            s['dimensions'][dim] = dict(vals)
    # decidable cases (permitted is not insufficient) solved, correct and supported, in all three repetitions
    dec = defaultdict(list)
    for r in rows:
        if r['permitted'] != 'insufficient_evidence':
            dec[r['case_id']].append(r['category'] == 'correct_supported')
    s['decidable_cases_solved_all_reps'] = {'cases': sum(all(v) for v in dec.values()), 'of': len(dec)}
    s['decidable_cases_solved_any_rep'] = {'cases': sum(any(v) for v in dec.values()), 'of': len(dec)}
    s['large_calls_per_case'] = round(statistics.mean(r['large_calls'] for r in rows), 3)
    s['model_calls_per_case'] = round(statistics.mean(r['n_calls'] for r in rows), 3)
    s['resource_index_per_case'] = round(statistics.mean(r['resource_index'] for r in rows), 1)
    s['runs_using_large_alone_answer'] = sum(r['large_from_large_alone'] for r in rows)
    s['pending_rerun'] = sum(r['category'] == 'pending_rerun' for r in rows)
    summary['configs'][cfg] = s
base = summary['configs'].get('small', {}).get('resource_index_per_case')
for s in summary['configs'].values():
    s['relative_resource_index'] = round(s['resource_index_per_case'] / base, 2) if base else None
jdump(summary, Path(a.out) if a.out else run_dir / 'summary_v2.json')
for cfg, s in summary['configs'].items():
    print(cfg, s['outcomes'], 'excl', s['wrong_exclusion_true_matches']['runs'], 'solved3', s['decidable_cases_solved_all_reps'], s['dimensions'])
