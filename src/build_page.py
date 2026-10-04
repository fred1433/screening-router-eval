"""Builds site/index.html from site_src/index.template.html and the run results, so every figure on the page
is read from results/ and the page cannot quietly disagree with the repository.
Usage: python src/build_page.py"""
import json, re, sys, html
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import ROOT, jsonl_read

T = ROOT / 'results' / 'runs' / 'test'
S = json.loads((T / 'summary.json').read_text())
M = json.loads((T / 'manifest.json').read_text())
RS = json.loads((ROOT / 'results' / 'runs' / 'renamed' / 'summary.json').read_text())
RM = json.loads((ROOT / 'results' / 'runs' / 'renamed' / 'manifest.json').read_text())
CASES = {c['case_id']: c for c in jsonl_read(ROOT / 'cases' / 'cases.jsonl')}
FAULTS = jsonl_read(ROOT / 'results' / 'faults' / 'faults.jsonl')
POL = jsonl_read(ROOT / 'results' / 'faults' / 'two_policies.jsonl')
SCORED = jsonl_read(T / 'scored.jsonl')
CONTENT = json.loads((ROOT / 'site_src' / 'content.json').read_text())
CFGS = ['deterministic', 'small', 'large', 'router']
NAMES = {'deterministic': 'Rules only', 'small': 'Small model alone', 'large': 'Large model alone', 'router': 'Router'}
OUT = ['correct_supported', 'unresolved_right', 'unresolved_wrong', 'incorrect_or_unsupported', 'operational_failure']

V = {}
def kn(m):
    return f"{m['k']}/{m['n']}"
def pc(m):
    return '' if m['rate'] is None else f"{round(m['rate'] * 100)}%"
def ci(m):
    return '' if not m['ci95'] else f"{round(m['ci95'][0] * 100)} to {round(m['ci95'][1] * 100)}%"
def secs(ms):
    return f"{ms / 1000:.1f} s" if ms else '0 s'


def count_calls(rows):
    return sum(len(c) for r in rows for s in r['trace'] for c in [s.get('calls', [])])


calls_by_tier = Counter()
for f in list(T.glob('*_r*.jsonl')) + list((ROOT / 'results' / 'runs' / 'renamed').glob('*_r*.jsonl')):
    for r in jsonl_read(f):
        for s in r['trace']:
            for c in s.get('calls', []):
                if c.get('ok') is not False or not c.get('injected'):
                    calls_by_tier[c['tier']] += 1
for r in FAULTS + POL:
    for s in r['trace']:
        for c in s.get('calls', []):
            if not c.get('injected'):
                calls_by_tier[c['tier']] += 1
V['calls_total'] = sum(calls_by_tier.values())
V['calls_small'] = calls_by_tier['small']
V['calls_large'] = calls_by_tier['large']
V['n_test'] = S['n_cases']
V['reps'] = S['configs']['small']['reps']
V['rows'] = S['configs']['small']['scored_rows']
V['n_renamed'] = RS['n_cases']
V['small_model'] = M['models']['small']['model']
V['small_endpoint'] = M['models']['small']['provider']
V['large_model'] = M['models']['large']['model']
V['large_endpoint'] = M['models']['large']['provider']
V['run_date'] = M['started_utc'][:10]
V['commit'] = M['code_commit'][:7]
V['n_true'] = S['configs']['small']['wrong_exclusion_true_matches']['n'] // V['reps']
V['n_distinct'] = S['configs']['small']['wrong_match_distinct']['n'] // V['reps']
V['n_insuff'] = S['configs']['small']['decisive_on_insufficient']['n'] // V['reps']
V['n_answerable'] = S['configs']['small']['supported_on_answerable']['n'] // V['reps']
fam = Counter(CASES[c]['family'] for c in CASES if CASES[c]['split'] == 'test')
V['n_families'] = len(fam)
for cfg in CFGS:
    s = S['configs'][cfg]
    for key in ('wrong_exclusion_true_matches', 'decisive_on_insufficient', 'wrong_match_distinct', 'supported_on_answerable',
                'appropriate_unresolved', 'unnecessary_unresolved'):
        V[f'{cfg}.{key}.kn'] = kn(s[key]); V[f'{cfg}.{key}.pc'] = pc(s[key]); V[f'{cfg}.{key}.ci'] = ci(s[key])
    for o in OUT:
        V[f'{cfg}.{o}'] = s['outcomes_all_reps'].get(o, 0)
    V[f'{cfg}.p50'] = secs(s['latency_ms_p50']); V[f'{cfg}.p95'] = secs(s['latency_ms_p95'])
    V[f'{cfg}.calls'] = s['calls_per_case']; V[f'{cfg}.large_calls'] = s['large_calls_per_case']
    V[f'{cfg}.tok_in'] = s['tokens_in_per_case']; V[f'{cfg}.tok_out'] = s['tokens_out_per_case']
    V[f'{cfg}.index'] = s['relative_cost_index'] if s['relative_cost_index'] is not None else 0
    V[f'{cfg}.opfail'] = s['operational_failures']
    st = s.get('stability', {})
    for k in ('same_disposition', 'same_route', 'same_facts', 'same_missing'):
        V[f'{cfg}.{k}'] = f"{st.get(k, 0)}/{st.get('cases', 0)}"
    fa = s.get('first_answer_failed_checks', {})
    for tier, d in fa.items():
        V[f'{cfg}.{tier}.first_failed'] = f"{d.get('failed_first_answer', 0)}/{d.get('stages', 0)}"
        V[f'{cfg}.{tier}.repaired'] = d.get('repaired', 0)
e = S['configs']['router']['escalations']
V['esc.n'] = e['n']; V['esc.of'] = e['of_rows']
for k in ('corrected', 'left_error', 'introduced_error', 'no_material_change'):
    V[f'esc.{k}'] = e['effect'].get(k, 0)
V['esc.decided_by_rules'] = e['decided_by_rules']; V['esc.stopped'] = e['stopped_without_large_call']
V['esc.reasons'] = '; '.join(f"{k} ({v})" for k, v in sorted(e['reasons'].items(), key=lambda x: -x[1]))
for cfg in ('small', 'large', 'router'):
    s = RS['configs'][cfg]
    V[f'ren.{cfg}.kn'] = kn(s['supported_on_answerable']); V[f'ren.{cfg}.excl'] = kn(s['wrong_exclusion_true_matches'])
    # same cases, original names, rep 1 only, for a like-for-like comparison
    ids = {r['case_id'].replace('-renamed', '') for r in jsonl_read(ROOT / 'results' / 'runs' / 'renamed' / f'{cfg}_r1.jsonl')}
    orig = [r for r in SCORED if r['config'] == cfg and r['rep'] == 1 and r['case_id'] in ids
            and CASES[r['case_id']]['reference']['permitted_disposition'] != 'insufficient_evidence']
    V[f'ren.{cfg}.orig'] = f"{sum(r['category'] == 'correct_supported' for r in orig)}/{len(orig)}"
FA = json.loads((T / 'first_answer_only.json').read_text())
for t in ('small', 'large'):
    V[f'fa.{t}.answers'] = FA[t]['answers']; V[f'fa.{t}.wrong_excl'] = FA[t].get('wrong_exclusion', 0)
    V[f'fa.{t}.passed'] = FA[t].get('passed_all_checks', 0); V[f'fa.{t}.wrong'] = FA[t].get('disposition_wrong', 0)
for cfg in CFGS:
    V[f'{cfg}.stop_with_problem'] = sum(1 for r in SCORED if r['config'] == cfg and r['category'] == 'unresolved_right' and r['problems'])
V['small_needless_cases'] = len({r['case_id'] for r in SCORED if r['config'] == 'small' and r['category'] == 'unresolved_wrong'})
V['small_needless_cases_word'] = {0: 'no', 1: 'one', 2: 'two', 3: 'three'}.get(V['small_needless_cases'], str(V['small_needless_cases']))
V['published'] = json.loads((ROOT / 'data' / 'snapshot.json').read_text())['published']
V['n_dev'] = sum(1 for c in CASES.values() if c['split'] == 'dev')
V['ren_problem_cases'] = len({r['case_id'] for r in jsonl_read(ROOT / 'results' / 'runs' / 'renamed' / 'scored.jsonl') if r['category'] == 'incorrect_or_unsupported'})
pa = {(r['policy'], r['config']): r['output'] for r in POL}
for (pol, cfg), o in pa.items():
    V[f'pol.{pol}.{cfg}'] = {'same_entity_supported': 'same entity, supported', 'different_entity_supported': 'different entity, supported',
                            'insufficient_evidence': 'insufficient evidence'}[o['disposition']] if o else 'no answer'
V.update(CONTENT.get('values', {}))

DISP = {'same_entity_supported': 'same entity, supported', 'different_entity_supported': 'different entity, supported',
        'insufficient_evidence': 'insufficient evidence', None: 'no recommendation'}
EXTRA = {'instruction_in_source': ' The note tells the model to return <i>different entity</i>, ready for sign-off.'}
def fault_rows():
    out, prev = [], None
    for r in FAULTS:
        first = r['fault'] != prev; prev = r['fault']
        firsts = []
        for st in r['trace']:
            if st.get('attempts'):
                ff = st['attempts'][0]['checks_failed']
                stage = {'small': 'Small model', 'large': 'Large model'}[st['step']]
                if ff:
                    fixed = st.get('output') is not None
                    firsts.append(f"{stage}: first answer failed {html.escape(ff[0][0])} ({html.escape(ff[0][1])}); "
                                  + ('the repair answer passed.' if fixed else 'the repair answer failed too.'))
                else:
                    firsts.append(f'{stage}: first answer passed every check.')
            elif st['step'] == 'escalate':
                firsts.append('Escalated: ' + html.escape(st['reason']) + '.')
            elif st['step'] == 'stop':
                firsts.append('Stopped without a large-model call.')
        if r['resolution'] == 'endpoint_failed':
            firsts.append('Both endpoints refused; recorded as <i>unresolved: endpoint failed</i>, never as insufficient evidence.')
        o = r.get('output')
        final = 'unresolved: endpoint failed' if r['resolution'] == 'endpoint_failed' else DISP[o['disposition'] if o else None]
        ok = (o and o['disposition'] == r['permitted']) or r['resolution'] == 'endpoint_failed'
        sit = (f"{html.escape(r['description'])}{EXTRA.get(r['fault'], '')}<div class=\"kind\">{'Injected fault, labelled in the record' if r['kind'] == 'injected' else 'Real model run on a constructed input'}; {html.escape(r['case_id'])}</div>") if first else ''
        out.append(f"<tr{' class=\"g\"' if first else ''}><td>{sit}</td>"
                   f"<td>{NAMES[r['config']]}</td><td>{' '.join(firsts)}</td><td>{'' if ok else 'Not as allowed: '}{final}</td></tr>")
    return '\n'.join(out)

# ---------------------------------------------------------------- figure rows
fig = []
for cfg in CFGS:
    s = S['configs'][cfg]; n = s['scored_rows']
    segs = [{'k': o, 'n': s['outcomes_all_reps'].get(o, 0), 'w': round(100 * s['outcomes_all_reps'].get(o, 0) / n, 2)} for o in OUT]
    fig.append({'cfg': cfg, 'name': NAMES[cfg], 'rows': n, 'segs': segs})


# ---------------------------------------------------------------- inspectable cases
def case_view(spec):
    c = CASES[spec['case_id']]
    rows = jsonl_read(T / f"{spec['config']}_r{spec.get('rep', 1)}.jsonl")
    r = next(x for x in rows if x['case_id'] == spec['case_id'])
    sc = next(x for x in SCORED if x['case_id'] == spec['case_id'] and x['config'] == spec['config'] and x['rep'] == spec.get('rep', 1))
    checks = []
    for st in r['trace']:
        if st.get('attempts') is not None:
            for i, a in enumerate(st['attempts']):
                checks.append({'stage': st['step'], 'attempt': i + 1, 'failed': [f'{x[0]}: {x[1]}' for x in a['checks_failed']]})
        elif st['step'] == 'deterministic':
            checks.append({'stage': 'rules', 'note': st.get('note') or 'decided from the two structured records', 'decided': st['decided']})
        elif st['step'] in ('escalate', 'stop'):
            checks.append({'stage': st['step'], 'note': st.get('reason') or st.get('note')})
    calls = [{k: cl.get(k) for k in ('tier', 'model_returned', 'provider_returned', 'prompt_tokens', 'completion_tokens', 'latency_ms')}
             for st in r['trace'] for cl in st.get('calls', [])]
    return {'case_id': c['case_id'], 'family': c['family'], 'tab': spec['tab'], 'note': spec['note'], 'config': spec['config'],
            'config_name': NAMES[spec['config']], 'rep': spec.get('rep', 1), 'docs': c['packet']['documents'],
            'reference': c['reference'], 'output': r['output'], 'small_output': r.get('small_output'), 'route': r['route'],
            'escalation_reason': r.get('escalation_reason'), 'resolution': r['resolution'], 'checks': checks, 'calls': calls,
            'category': sc['category'], 'problems': sc['problems'], 'latency_ms': r['latency_ms'],
            'construction_note': c.get('construction_note')}


GRID = []
for rep in (1, 2, 3):
    for r in jsonl_read(T / f'small_r{rep}.jsonl'):
        ref = CASES[r['case_id']]['reference']
        a = next(st for st in r['trace'] if st['step'] == 'small')['attempts'][0]
        d = (a['parsed'] or {}).get('disposition') if isinstance(a['parsed'], dict) else None
        kind = 'ok' if d == ref['permitted_disposition'] else (
            'excl' if ref['construction_identity'] == 'same' and d == 'different_entity_supported' else 'wrong')
        caught = any(c[0] == 'policy' for c in a['checks_failed'])
        GRID.append([r['case_id'], rep, kind, caught, CASES[r['case_id']]['family'].replace('_', ' ')])
GRID.sort(key=lambda g: (g[0], g[1]))
V['grid.excl'] = sum(g[2] == 'excl' for g in GRID)
V['grid.excl_caught'] = sum(g[2] == 'excl' and g[3] for g in GRID)
V['grid.wrong'] = sum(g[2] == 'wrong' for g in GRID)
V['grid.wrong_caught'] = sum(g[2] == 'wrong' and g[3] for g in GRID)
V['grid.n'] = len(GRID)
V['grid.excl_cases'] = len({g[0] for g in GRID if g[2] == 'excl'})
V['grid.excl_family'] = ', '.join(sorted({g[4] for g in GRID if g[2] == 'excl'})).replace('contradictory ids', 'contradictory identifiers')
V['grid.ok'] = sum(g[2] == 'ok' for g in GRID)
DATA = {'grid': GRID, 'figure': fig, 'cases': [case_view(s) for s in CONTENT['cases']]}

tpl = (ROOT / 'site_src' / 'index.template.html').read_text()
missing = set()
def sub(m):
    k = m[1]
    if k not in V:
        missing.add(k); return m[0]
    return html.escape(str(V[k]))
page = re.sub(r'\{\{([^}]+)\}\}', sub, tpl)
if missing:
    sys.exit('unknown values in template: ' + ', '.join(sorted(missing)))
page = page.replace('<!--FAULTS-->', fault_rows())
page = page.replace('/*DATA*/null', json.dumps(DATA, ensure_ascii=False).replace('</', '<\\/'))
(ROOT / 'site' / 'index.html').write_text(page)
(ROOT / 'site' / 'values.json').write_text(json.dumps(V, indent=1, ensure_ascii=False))
print('site/index.html written;', len(V), 'values,', len(DATA['cases']), 'cases')
