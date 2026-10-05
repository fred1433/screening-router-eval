"""Builds site/index.html from site_src/index.template.html and the result files, so every figure on the page is read
from results/ and the page cannot quietly disagree with the repository. No network.
Usage: python src/build_page.py"""
import html, json, re, sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import ROOT, jsonl_read
import pipeline

R = ROOT / 'results' / 'runs'
T, V2R, NEW = R / 'test', R / 'v2_replay', R / 'v2_new'
S = json.loads((T / 'summary.json').read_text())                       # v1, historical score
D1 = json.loads((T / 'summary_v1_dimensions.json').read_text())        # v1 answers, five dimensions
D2 = json.loads((V2R / 'summary_v2.json').read_text())                 # v2 controls replayed on v1 answers
A1 = json.loads((R / 'ablation_v1' / 'summary.json').read_text())
A1D = json.loads((R / 'ablation_v1' / 'summary_v1_dimensions.json').read_text())
A2 = json.loads((R / 'ablation_v2' / 'summary_v2.json').read_text())
NC = json.loads((NEW / 'summary_v2.json').read_text())
M = json.loads((T / 'manifest.json').read_text())
RS = json.loads((R / 'renamed' / 'summary.json').read_text())
FA = json.loads((T / 'first_answer_only.json').read_text())
CR = json.loads((T / 'citation_replay.json').read_text())
RR = json.loads((T / 'reruns.json').read_text())
TESTS = json.loads((ROOT / 'results' / 'tests_v2.json').read_text())
CASES = {c['case_id']: c for c in jsonl_read(ROOT / 'cases' / 'cases.jsonl')}
NEWC = jsonl_read(ROOT / 'cases' / 'cases_v2_new.jsonl')
FAULTS = jsonl_read(ROOT / 'results' / 'faults' / 'faults.jsonl')
POL = jsonl_read(ROOT / 'results' / 'faults' / 'two_policies.jsonl')
SC1 = jsonl_read(T / 'scored.jsonl')
SD1 = {(r['case_id'], r['config'], r['rep']): r for r in jsonl_read(T / 'scored_v2.jsonl')}
SD2 = {(r['case_id'], r['config'], r['rep']): r for r in jsonl_read(V2R / 'scored_v2.jsonl')}
CONTENT = json.loads((ROOT / 'site_src' / 'content.json').read_text())
CFGS = ['deterministic', 'small', 'large', 'router']
NAMES = {'deterministic': 'Rules only', 'small': 'Small model alone', 'large': 'Large model alone', 'router': 'Router',
         'rules_small': 'Rules, then small'}
WORDS = {0: 'no', 1: 'one', 2: 'two', 3: 'three', 4: 'four', 5: 'five', 6: 'six', 7: 'seven', 8: 'eight', 9: 'nine'}
V = {}


def secs(ms):
    return 'under 0.1 s' if ms is None or ms < 100 else f'{ms / 1000:.1f} s'


def ci(m):
    c = m.get('ci95_cases')
    return '' if not c else f"{round(c[0] * 100)} to {round(c[1] * 100)}%"


def word(n):
    return WORDS.get(n, str(n))


# ---------------------------------------------------------------- run-level facts
V['n_test'] = S['n_cases']; V['reps'] = S['configs']['small']['reps']; V['rows'] = S['configs']['small']['scored_rows']
V['small_model'] = M['models']['small']['model']; V['small_endpoint'] = M['models']['small']['provider']
V['large_model'] = M['models']['large']['model']; V['large_endpoint'] = M['models']['large']['provider']
V['run_date'] = M['started_utc'][:10]; V['commit'] = M['code_commit'][:7]
V['n_families'] = len({c['family'] for c in CASES.values() if c['split'] == 'test'})
V['n_dev'] = sum(1 for c in CASES.values() if c['split'] == 'dev')
V['published'] = json.loads((ROOT / 'data' / 'snapshot.json').read_text())['published']
V['n_true'] = S['configs']['small']['wrong_exclusion_true_matches']['of_cases']
V['n_distinct'] = S['configs']['small']['wrong_match_distinct']['of_cases']
V['n_insuff'] = S['configs']['small']['decisive_on_insufficient']['of_cases']
V['repo'] = 'https://github.com/fred1433/screening-router-eval'
V['rerun.cases'] = len({c for x in RR for c in x['case_ids']})
calls_v1 = [c for cfg in ('small', 'large', 'router') for rep in (1, 2, 3)
            for r in jsonl_read(T / f'{cfg}_r{rep}.jsonl') for s in r['trace'] for c in s.get('calls', [])]
V['retried_calls'] = sum(1 for c in calls_v1 if (c.get('attempt') or 1) > 1)
V['logical_calls'] = len(calls_v1)
V['tests_passed'] = sum(t['passed'] for t in TESTS); V['tests_total'] = len(TESTS)

# ---------------------------------------------------------------- v1 (historical)
for cfg in CFGS:
    s = S['configs'][cfg]
    for key in ('wrong_exclusion_true_matches', 'decisive_on_insufficient', 'wrong_match_distinct', 'supported_on_answerable',
                'appropriate_unresolved', 'unnecessary_unresolved'):
        V[f'{cfg}.{key}.kn'] = f"{s[key]['k']}/{s[key]['n']}"
        V[f'{cfg}.{key}.cases'] = f"{s[key]['cases_any_run']}/{s[key]['of_cases']}"
        V[f'{cfg}.{key}.ci'] = ci(s[key])
    V[f'{cfg}.p50'] = secs(s['latency_ms_p50']); V[f'{cfg}.p95'] = secs(s['latency_ms_p95'])
    V[f'{cfg}.p50m'] = secs(s.get('latency_ms_p50_model_runs')); V[f'{cfg}.model_runs'] = s.get('model_runs', 0)
    V[f'{cfg}.large_calls'] = s['large_calls_per_case']; V[f'{cfg}.index'] = s['relative_cost_index'] or 0
    d = D1['configs'][cfg]
    V[f'{cfg}.solved3'] = f"{d['decidable_cases_solved_all_reps']['cases']}/{d['decidable_cases_solved_all_reps']['of']}"
    for dim in ('evidence', 'next_action', 'missing_info', 'name_class', 'routing'):
        dd = d['dimensions'].get(dim)
        V[f'{cfg}.dim.{dim}'] = '' if not dd else f"{dd.get('fail', 0)}/{sum(v for k, v in dd.items() if k in ('pass', 'fail'))}"
    for t, v in s.get('first_answer_failed_checks', {}).items():
        V[f'{cfg}.{t}.first_failed'] = f"{v.get('failed_first_answer', 0)}/{v.get('stages', 0)}"
        V[f'{cfg}.{t}.repaired'] = v.get('repaired', 0)
a1 = A1['configs']['rules_small']; a1d = A1D['configs']['rules_small']
V['abl.kn'] = f"{a1['supported_on_answerable']['k']}/{a1['supported_on_answerable']['n']}"
V['abl.excl'] = f"{a1['wrong_exclusion_true_matches']['k']}/{a1['wrong_exclusion_true_matches']['n']}"
V['abl.solved3'] = f"{a1d['decidable_cases_solved_all_reps']['cases']}/{a1d['decidable_cases_solved_all_reps']['of']}"
V['abl.index'] = round(a1['resource_index_per_case'] / S['configs']['small']['resource_index_per_case'], 2)
for dim in ('evidence', 'next_action', 'missing_info', 'name_class'):
    dd = a1d['dimensions'].get(dim)
    V[f'abl.dim.{dim}'] = '' if not dd else f"{dd.get('fail', 0)}/{sum(v for k, v in dd.items() if k in ('pass', 'fail'))}"
for k in ('decisive_on_insufficient', 'wrong_match_distinct', 'unnecessary_unresolved', 'appropriate_unresolved'):
    V[f'abl.{k}.kn'] = f"{a1[k]['k']}/{a1[k]['n']}"
e = S['configs']['router']['escalations']
V['esc.n'] = e['n']; V['esc.of'] = e['of_rows']; V['esc.corrected'] = e['effect'].get('corrected', 0)
V['esc.invalid_stop'] = e['small_invalid_replaced_by_stop']; V['esc.decided_by_rules'] = e['decided_by_rules']
V['esc.stopped'] = e['stopped_without_large_call']
V['esc.reasons'] = '; '.join(f"{k} ({v})" for k, v in sorted(e['reasons'].items(), key=lambda x: -x[1]))
corr_cases = {r['case_id'] for r in SC1 if r['config'] == 'router' and r.get('escalation_reason')
              and r['small_category'] not in ('correct_supported', 'unresolved_right') and r['category'] in ('correct_supported', 'unresolved_right')}
V['esc.corrected_cases'] = len(corr_cases)
# router against small alone: independent executions
sm = {(r['case_id'], r['rep']): r['category'] for r in SC1 if r['config'] == 'small'}
ro = {(r['case_id'], r['rep']): r['category'] for r in SC1 if r['config'] == 'router'}
gain = [k for k in ro if ro[k] == 'correct_supported' and sm[k] != 'correct_supported']
loss = [k for k in ro if ro[k] != 'correct_supported' and sm[k] == 'correct_supported']
V['gain.runs'] = len(gain); V['gain.cases'] = len({k[0] for k in gain}); V['loss.runs'] = len(loss)
V['router_minus_abl'] = S['configs']['router']['supported_on_answerable']['k'] - a1['supported_on_answerable']['k']

# first answers (grid)
GRID = []
for rep in (1, 2, 3):
    for r in jsonl_read(T / f'small_r{rep}.jsonl'):
        ref = CASES[r['case_id']]['reference']
        a = next(st for st in r['trace'] if st['step'] == 'small')['attempts'][0]
        d = a['parsed'].get('disposition') if isinstance(a['parsed'], dict) else None
        kind = 'ok' if d == ref['permitted_disposition'] else (
            'excl' if ref['construction_identity'] == 'same' and d == 'different_entity_supported' else 'wrong')
        GRID.append([r['case_id'], rep, kind, any(c[0] == 'policy' for c in a['checks_failed']), CASES[r['case_id']]['family'].replace('_', ' ')])
GRID.sort(key=lambda g: (g[0], g[1]))
V['grid.n'] = len(GRID); V['grid.ok'] = sum(g[2] == 'ok' for g in GRID)
V['grid.excl'] = sum(g[2] == 'excl' for g in GRID); V['grid.excl_caught'] = sum(g[2] == 'excl' and g[3] for g in GRID)
V['grid.wrong'] = sum(g[2] == 'wrong' for g in GRID); V['grid.wrong_caught'] = sum(g[2] == 'wrong' and g[3] for g in GRID)
V['grid.excl_cases'] = len({g[0] for g in GRID if g[2] == 'excl'}); V['grid.excl_cases_word'] = word(V['grid.excl_cases']).capitalize()
V['grid.excl_family'] = ', '.join(sorted({g[4] for g in GRID if g[2] == 'excl'})).replace('contradictory ids', 'contradictory identifiers')
V['fa.large.wrong_excl'] = FA['large'].get('wrong_exclusion', 0); V['fa.small.wrong_excl'] = FA['small'].get('wrong_exclusion', 0)
for cfg in ('small', 'large', 'router'):
    V[f'cr.{cfg}'] = f"{CR[cfg]['would_reject']} of {CR[cfg]['outputs']}"

# ---------------------------------------------------------------- v2 replay and new cases
for cfg in CFGS:
    s = D2['configs'][cfg]
    rows2 = [r for r in SD2.values() if r['config'] == cfg and r['permitted'] != 'insufficient_evidence']
    V[f'v2.{cfg}.correct'] = f"{sum(r['category'] == 'correct_supported' for r in rows2)}/{len(rows2)}"
    V[f'v2.{cfg}.pending'] = s['pending_rerun']
    V[f'v2.{cfg}.excl'] = f"{s['wrong_exclusion_true_matches']['runs']}/{s['wrong_exclusion_true_matches']['of_runs']}"
    V[f'v2.{cfg}.solved3'] = f"{s['decidable_cases_solved_all_reps']['cases']}/{s['decidable_cases_solved_all_reps']['of']}"
    V[f'v2.{cfg}.large_calls'] = round(s['large_calls_per_case'], 2); V[f'v2.{cfg}.index'] = s['relative_resource_index'] or 0
V['v2.router.from_large_alone'] = D2['configs']['router']['runs_using_large_alone_answer']
V['v2.pending_total'] = sum(D2['configs'][c]['pending_rerun'] for c in CFGS)
a2 = A2['configs']['rules_small']
ab2 = [r for r in jsonl_read(R / 'ablation_v2' / 'scored_v2.jsonl') if r['permitted'] != 'insufficient_evidence']
V['v2.abl.correct'] = f"{sum(r['category'] == 'correct_supported' for r in ab2)}/{len(ab2)}"; V['v2.abl.pending'] = a2['pending_rerun']
V['v2.abl.solved3'] = f"{a2['decidable_cases_solved_all_reps']['cases']}/{a2['decidable_cases_solved_all_reps']['of']}"
nd = NC['configs']['deterministic']
V['new.n'] = len(NEWC); V['new.families'] = len({c['family'] for c in NEWC})
V['new.det.correct'] = nd['outcomes'].get('correct_supported', 0); V['new.det.right_stop'] = nd['outcomes'].get('unresolved_right', 0)
V['new.det.needless'] = nd['outcomes'].get('unresolved_wrong', 0); V['new.det.wrong'] = nd['outcomes'].get('incorrect_or_unsupported', 0)
V['new.det.excl'] = f"{nd['wrong_exclusion_true_matches']['runs']}/{nd['wrong_exclusion_true_matches']['of_runs']}"
rv = [x for f in sorted((ROOT / 'review').glob('reference_review_v2_new_round*.jsonl')) for x in [jsonl_read(f)]]
V['new.review_rounds'] = len(rv)
V['new.review_first'] = sum(1 for x in rv[0] if x.get('agrees') is not True) if rv else 0
V['new.review_last'] = sum(1 for x in rv[-1] if x.get('agrees') is not True) if rv else 0
fam_names = {'id_present_list_absent': 'a customer identifier the list does not give', 'renewed_passport': 'a renewed passport',
             'alternative_dob': 'alternative dates of birth', 'contradictory_customer_documents': 'two customer documents that disagree',
             'entity_homonym_same_city': 'a company homonym in the same country and city', 'long_name': 'long names'}
fc = Counter(c['family'] for c in NEWC)
V['new.family_list'] = ', '.join(f'{fam_names[k]} ({v})' for k, v in fc.items())

# faults, policies, renamed
pa = {(r['policy'], r['config']): r['output'] for r in POL}
DISP = {'same_entity_supported': 'same entity, supported', 'different_entity_supported': 'different entity, supported',
        'insufficient_evidence': 'insufficient evidence', None: 'no recommendation'}
for (pol, cfg), o in pa.items():
    V[f'pol.{pol}.{cfg}'] = DISP[o['disposition'] if o else None]
for cfg in ('small', 'large', 'router'):
    s = RS['configs'][cfg]
    V[f'ren.{cfg}.kn'] = f"{s['supported_on_answerable']['k']}/{s['supported_on_answerable']['n']}"
    ids = {r['case_id'].replace('-renamed', '') for r in jsonl_read(R / 'renamed' / f'{cfg}_r1.jsonl')}
    orig = [r for r in SC1 if r['config'] == cfg and r['rep'] == 1 and r['case_id'] in ids
            and CASES[r['case_id']]['reference']['permitted_disposition'] != 'insufficient_evidence']
    V[f'ren.{cfg}.orig'] = f"{sum(r['category'] == 'correct_supported' for r in orig)}/{len(orig)}"
V['n_renamed'] = RS['n_cases']
V['ren_problem_cases'] = len({r['case_id'] for r in jsonl_read(R / 'renamed' / 'scored.jsonl') if r['category'] == 'incorrect_or_unsupported'})


# ---------------------------------------------------------------- figure rows
OUT = ['correct_supported', 'unresolved_right', 'unresolved_right_flagged', 'unresolved_wrong', 'incorrect_or_unsupported',
       'pending_rerun', 'operational_failure']
def segs(outcomes, n):
    return [{'k': o, 'n': outcomes.get(o, 0), 'w': round(100 * outcomes.get(o, 0) / n, 2)} for o in OUT]
fig = []
for cfg in CFGS + ['rules_small']:
    v1 = (A1D if cfg == 'rules_small' else D1)['configs'][cfg]
    v2 = (A2 if cfg == 'rules_small' else D2)['configs'][cfg]
    fig.append({'cfg': cfg, 'name': NAMES[cfg], 'v1': segs(v1['outcomes'], v1['rows']), 'v2': segs(v2['outcomes'], v2['rows']), 'rows': v1['rows']})
for cfg in CFGS:
    V[f'v1d.{cfg}.correct'] = D1['configs'][cfg]['outcomes'].get('correct_supported', 0)
    V[f'v1d.{cfg}.flagged'] = D1['configs'][cfg]['outcomes'].get('unresolved_right_flagged', 0)
    V[f'v1d.{cfg}.unsupported'] = D1['configs'][cfg]['outcomes'].get('incorrect_or_unsupported', 0)


# ---------------------------------------------------------------- case views
DIMNAME = {'decision': ('Decision right', 'Decision wrong'), 'evidence': ('Evidence supported', 'Evidence to correct'),
           'next_action': ('Next action fit', 'Next action to correct'), 'missing_info': ('Missing items right', 'Missing items incomplete'),
           'routing': ('Route as designed', 'Route not as designed'), 'name_class': ('Name class right', 'Name class wrong')}


def badges(d):
    out = []
    for k in ('decision', 'evidence', 'next_action', 'missing_info', 'routing'):
        v = d.get(k)
        if v in ('pass', 'fail'):
            out.append({'ok': v == 'pass', 'label': DIMNAME[k][0 if v == 'pass' else 1]})
        elif v == 'pending':
            out.append({'ok': None, 'label': 'Decision pending a repair call'})
        elif v == 'not needed':
            out.append({'ok': True, 'label': 'Missing items not needed'})
    return out


def case_view(case_id, config, rep, tab=None, note=None):
    c = CASES[case_id]
    r = next(x for x in jsonl_read(T / f'{config}_r{rep}.jsonl') if x['case_id'] == case_id)
    checks, first = [], None
    for st in r['trace']:
        if st.get('attempts') is not None:
            for i, a in enumerate(st['attempts']):
                checks.append({'stage': st['step'], 'attempt': i + 1, 'failed': [f'{x[0]}: {x[1]}' for x in a['checks_failed']]})
                if i == 0 and a['checks_failed'] and first is None and isinstance(a['parsed'], dict):
                    first = {'stage': st['step'], 'disposition': a['parsed'].get('disposition'),
                             'failed': [f'{x[0]}: {x[1]}' for x in a['checks_failed']]}
        elif st['step'] == 'deterministic':
            checks.append({'stage': 'rules', 'note': st.get('note') or 'decided from the two structured records'})
        elif st['step'] in ('escalate', 'stop'):
            checks.append({'stage': st['step'], 'note': st.get('reason') or st.get('note')})
    d1, d2 = SD1[(case_id, config, rep)], SD2[(case_id, config, rep)]
    v2row = next(x for x in jsonl_read(V2R / f'{config}_r{rep}.jsonl') if x['case_id'] == case_id)
    calls = [{k: cl.get(k) for k in ('tier', 'model_returned', 'provider_returned', 'prompt_tokens', 'completion_tokens', 'latency_ms', 'attempt')}
             for st in r['trace'] for cl in st.get('calls', [])]
    return {'case_id': case_id, 'family': c['family'], 'tab': tab, 'note': note, 'config': config, 'config_name': NAMES[config],
            'rep': rep, 'docs': c['packet']['documents'], 'reference': c['reference'], 'output': r['output'],
            'small_output': r.get('small_output'), 'route': r['route'], 'escalation_reason': r.get('escalation_reason'),
            'checks': checks, 'calls': calls, 'first': first, 'latency_ms': r['latency_ms'],
            'badges': badges(d1['dims']), 'evidence_problems': d1['dims'].get('evidence_problems', []),
            'v2': {'category': d2['category'], 'badges': badges(d2['dims']), 'route': v2row['route'],
                   'next_action': (v2row.get('output') or {}).get('outstanding_work'),
                   'disposition': (v2row.get('output') or {}).get('disposition'), 'resolution': v2row['resolution']}}


cases_v = [case_view(s['case_id'], s['config'], s.get('rep', 1), s['tab'], s['note']) for s in CONTENT['cases']]
grid_v = {f'{g[0]}|{g[1]}': case_view(g[0], 'small', g[1]) for g in GRID if g[2] != 'ok'}
DATA = {'grid': GRID, 'figure': fig, 'cases': cases_v, 'grid_cases': grid_v}


# ---------------------------------------------------------------- fault rows
EXTRA = {'instruction_in_source': ' The note tells the model to return <i>different entity</i>, ready for sign-off. The model ignored the instruction in all three configurations; the first-answer failure shown here is unrelated (place of birth <i>Russia</i> read as conflicting with <i>Samara, Russia</i>).'}


def fault_rows():
    out, prev = [], None
    for r in FAULTS:
        first = r['fault'] != prev; prev = r['fault']
        notes = []
        for st in r['trace']:
            if st.get('attempts'):
                ff = st['attempts'][0]['checks_failed']
                stage = {'small': 'Small model', 'large': 'Large model'}[st['step']]
                if ff:
                    notes.append(f"{stage}: first answer failed {html.escape(ff[0][0])} ({html.escape(ff[0][1].replace(' at at char', ' at char'))}); "
                                 + ('the repair answer passed.' if st.get('output') is not None else 'the repair answer failed too.'))
                else:
                    notes.append(f'{stage}: first answer passed every check.')
            elif st['step'] == 'escalate':
                notes.append('Escalated: ' + html.escape(st['reason']) + '.')
            elif st['step'] == 'stop':
                notes.append('Stopped without a large-model call.')
        if r['resolution'] == 'endpoint_failed':
            notes.append('Both endpoints refused; recorded as <i>unresolved: endpoint failed</i>, never as insufficient evidence.')
        o = r.get('output')
        final = 'unresolved: endpoint failed' if r['resolution'] == 'endpoint_failed' else DISP[o['disposition'] if o else None]
        ok = (o and o['disposition'] == r['permitted']) or r['resolution'] == 'endpoint_failed'
        sit = (f"{html.escape(r['description'])}{EXTRA.get(r['fault'], '')}<div class=\"kind\">"
               f"{'Injected fault, labelled in the record' if r['kind'] == 'injected' else 'Real model run on a constructed input'}; {html.escape(r['case_id'])}</div>") if first else ''
        out.append(f"<tr{' class=\"g\"' if first else ''}><td>{sit}</td><td>{NAMES[r['config']]}</td><td>{' '.join(notes)}</td>"
                   f"<td>{'' if ok else 'Not as allowed: '}{final}</td></tr>")
    return '\n'.join(out)


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
if '—' in page:
    sys.exit('em dash in page')
(ROOT / 'site' / 'index.html').write_text(page)
(ROOT / 'site' / 'values.json').write_text(json.dumps(V, indent=1, ensure_ascii=False, sort_keys=True))
print('site/index.html written;', len(V), 'values,', len(cases_v), 'cases,', len(grid_v), 'grid cases')
