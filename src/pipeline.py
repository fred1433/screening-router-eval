"""The four configurations under test and the shared controls they all use.

deterministic : structured-field rules only; anything that needs reading free text stays unresolved.
small / large : one model reads the packet; shared validators; one repair call on a failed check.
router        : deterministic stage, then the small model, then the large model only for observable reasons.
"""
import json, re, time
from pathlib import Path
from common import (ROOT, ATTRIBUTES, DISPOSITIONS, STRONG, apply_policy, status_for, structured_relations,
                    has_free_text, render_packet)
import llm

POLICY = (ROOT / 'policy' / 'policy_a.md').read_text()
POLICY_B = (ROOT / 'policy' / 'policy_b.md').read_text() if (ROOT / 'policy' / 'policy_b.md').exists() else ''
SYSTEM_TMPL = (ROOT / 'prompts' / 'disposition_system.md').read_text()
REPAIR_TMPL = ('Your previous answer failed these checks:\n{errors}\n\nReturn the corrected JSON object only, '
               'following the same rules. Quotes must be copied exactly from the cited document.')
REPAIR_BUDGET = 1  # one repair call per model, identical for every configuration


def system_prompt(policy='A'):
    return SYSTEM_TMPL.replace('{POLICY}', POLICY if policy == 'A' else POLICY_B)


def user_prompt(packet):
    return 'Packet:\n\n' + render_packet(packet)


# ------------------------------------------------------------------ shared validators
def _norm(s):
    return re.sub(r'\s+', ' ', (s or '')).strip().lower()


def validate(out, packet, policy='A'):
    """Returns list of (check, message). Empty list = passes every deterministic control."""
    errs = []
    if not isinstance(out, dict):
        return [('schema', 'output is not a JSON object')]
    for k in ('name_match', 'comparisons', 'missing_information', 'disposition', 'status'):
        if k not in out:
            errs.append(('schema', f'missing key {k}'))
    if errs:
        return errs
    if out['name_match'] not in ('exact', 'listed_alias', 'transliteration_variant', 'none'):
        errs.append(('schema', f"bad name_match {out['name_match']}"))
    if out['disposition'] not in DISPOSITIONS:
        errs.append(('schema', f"bad disposition {out['disposition']}"))
    if not isinstance(out['comparisons'], list):
        return errs + [('schema', 'comparisons is not a list')]
    docs = {d['id']: d for d in packet['documents']}
    for i, c in enumerate(out['comparisons']):
        if not isinstance(c, dict) or c.get('attribute') not in ATTRIBUTES or c.get('relation') not in ('agree', 'conflict', 'not_available'):
            errs.append(('schema', f'comparison {i} has an unknown attribute or relation'))
            continue
        if c['relation'] == 'not_available':
            continue
        for side, cite_key in (('customer', 'customer_cite'), ('list', 'list_cite')):
            cite = c.get(cite_key)
            if not isinstance(cite, dict) or not cite.get('doc') or not cite.get('quote'):
                errs.append(('citation_missing', f"{c['attribute']}: {side} cite missing"))
                continue
            d = docs.get(cite['doc'])
            if d is None:
                errs.append(('citation_source', f"{c['attribute']}: document {cite['doc']} is not in this packet"))
                continue
            if d['side'] != side:
                errs.append(('citation_source', f"{c['attribute']}: {side} fact cited from {cite['doc']} ({d['side']} side)"))
            if _norm(cite['quote']) not in _norm(d['text']):
                errs.append(('citation_exact', f"{c['attribute']}: quote not found verbatim in {cite['doc']}"))
    if errs:
        return errs
    # policy applied to the model's own comparisons
    expect = apply_policy(out['name_match'], out['comparisons'], policy)
    if expect != out['disposition']:
        errs.append(('policy', f"comparisons imply {expect}, output says {out['disposition']}"))
    if out.get('status') != status_for(out['disposition']):
        errs.append(('policy', f"status {out.get('status')} does not follow from {out['disposition']}"))
    # cross-check against what code can read from the two structured records
    det_name, det = structured_relations(packet)
    mine = {}
    for c in out['comparisons']:
        mine.setdefault(c['attribute'], set()).add(c['relation'])
    for attr, (rel, _, _) in det.items():
        got = mine.get(attr, set())
        if rel not in got:
            if got - {'not_available'}:
                errs.append(('structured_mismatch', f'{attr}: structured records give {rel}, output gives {sorted(got)}'))
            else:
                errs.append(('structured_omitted', f'{attr}: both structured records give it ({rel}) but the output omits it'))
    if det_name in ('exact', 'listed_alias') and out['name_match'] == 'none':
        errs.append(('structured_mismatch', 'name: structured records match the name, output says none'))
    return errs


def parse_json(text):
    t = (text or '').strip()
    t = re.sub(r'^```(?:json)?\s*|\s*```$', '', t)
    try:
        return json.loads(t), None
    except json.JSONDecodeError as e:
        return None, f'not valid JSON ({e.msg} at char {e.pos})'


# ------------------------------------------------------------------ one model with shared controls
def model_stage(tier, packet, policy='A', fault=None):
    """Call, validate, at most one repair. Returns dict with output (or None), calls, checks."""
    msgs = [{'role': 'system', 'content': system_prompt(policy)}, {'role': 'user', 'content': user_prompt(packet)}]
    calls, attempts = [], []
    for k in range(REPAIR_BUDGET + 1):
        r = llm.call(tier, msgs, injected_fault=fault if fault == 'endpoint_down' else None)
        calls.append({x: r.get(x) for x in r if x != 'content'})
        if not r['ok']:
            return {'tier': tier, 'output': None, 'calls': calls, 'attempts': attempts, 'endpoint_failed': True,
                    'error': r['error']}
        content = r['content']
        if fault == 'malformed' and k == 0:
            content = content[:len(content) // 2]
            calls[-1]['injected_fault'] = 'malformed: output truncated to half its length before validation'
        out, perr = parse_json(content)
        if out is not None and fault == 'wrong_record' and k == 0:
            for c in out.get('comparisons', []):
                if c.get('relation') in ('agree', 'conflict') and isinstance(c.get('customer_cite'), dict):
                    c['customer_cite'] = {'doc': 'C7', 'quote': 'Date of birth: 1971-03-12'}
                    calls[-1]['injected_fault'] = 'wrong_record: one customer citation replaced by a citation to a record from another file'
                    break
        errs = [('schema', perr)] if perr else validate(out, packet, policy)
        attempts.append({'raw': content[:6000], 'parsed': out, 'checks_failed': [list(e) for e in errs]})
        if not errs:
            return {'tier': tier, 'output': out, 'calls': calls, 'attempts': attempts, 'endpoint_failed': False}
        msgs = msgs + [{'role': 'assistant', 'content': content[:6000]},
                       {'role': 'user', 'content': REPAIR_TMPL.format(errors='\n'.join(f'- {a}: {b}' for a, b in errs))}]
    return {'tier': tier, 'output': None, 'calls': calls, 'attempts': attempts, 'endpoint_failed': False,
            'failed_checks': attempts[-1]['checks_failed']}


# ------------------------------------------------------------------ deterministic stage
def deterministic_stage(packet, policy='A'):
    name_match, rel = structured_relations(packet)
    if has_free_text(packet):
        return None, 'free-text document present: code does not read it'
    if name_match not in ('exact', 'listed_alias'):
        return None, 'name is neither the listed name nor a listed alias'
    comps = []
    for attr, (r, cl, ll) in rel.items():
        comps.append({'attribute': attr, 'relation': r, 'customer_value': cl.split(': ', 1)[1], 'list_value': ll.split(': ', 1)[1],
                      'customer_cite': {'doc': 'C1', 'quote': cl}, 'list_cite': {'doc': 'L1', 'quote': ll}})
    disp = apply_policy(name_match, comps, policy)
    have = set(rel)
    entity = 'Legal name:' in packet['documents'][0]['text']
    need = ['registration_number'] if entity else ['date_of_birth', 'passport_number']
    missing = [a for a in need if a not in have]
    out = {'name_match': name_match, 'comparisons': comps, 'missing_information': missing if disp == 'insufficient_evidence' else [],
           'disposition': disp, 'status': status_for(disp),
           'outstanding_work': 'Analyst sign-off.' if disp != 'insufficient_evidence' else
           ('Obtain ' + ', '.join(missing) + '.' if missing else 'Resolve the conflicting identifiers.'),
           'rationale': 'Rule-based comparison of the two structured records.'}
    return out, None


# ------------------------------------------------------------------ configurations
def _final(stage):
    if stage is None:
        return None
    return stage['output']


def run_case(config, case, policy='A', fault=None):
    packet = case['packet']
    t0 = time.time()
    trace, route, reason = [], [], None
    result = {'case_id': case['case_id'], 'config': config, 'policy': policy, 'fault': fault}
    if config in ('deterministic', 'router'):
        out, why = deterministic_stage(packet, policy)
        trace.append({'step': 'deterministic', 'decided': out is not None, 'note': why})
        route.append('rules')
        if out is not None or config == 'deterministic':
            result.update(output=out, route=route, trace=trace, resolution='decided' if out else 'unresolved_no_model',
                          escalation_reason=None, latency_ms=int((time.time() - t0) * 1000))
            return result
    if config in ('small', 'large'):
        st = model_stage(config, packet, policy, fault)
        route.append(config)
        trace.append({'step': config, **{k: st[k] for k in st if k != 'tier'}})
        res = 'endpoint_failed' if st['endpoint_failed'] else ('decided' if st['output'] else 'unresolved_validation')
        result.update(output=st['output'], route=route, trace=trace, resolution=res, escalation_reason=None,
                      latency_ms=int((time.time() - t0) * 1000))
        return result
    # router
    s = model_stage('small', packet, policy, fault)
    route.append('small')
    trace.append({'step': 'small', **{k: s[k] for k in s if k != 'tier'}})
    out = s['output']
    if s['endpoint_failed']:
        reason = 'small endpoint failed'
    elif out is None:
        reason = 'small output failed checks after repair: ' + ', '.join(sorted({c[0] for c in s.get('failed_checks', [])}))
    elif out['name_match'] == 'transliteration_variant' and out['disposition'] == 'same_entity_supported':
        reason = 'match rests on a name spelling that is not listed'
    elif out['disposition'] == 'insufficient_evidence':
        rels = {c['relation'] for c in out['comparisons']}
        if 'conflict' in rels and 'agree' in rels:
            reason = 'contradictory identifiers'
        else:
            trace.append({'step': 'stop', 'note': 'evidence absent: a larger model cannot supply it; no further call'})
    if reason is None:
        result.update(output=out, route=route, trace=trace,
                      resolution='endpoint_failed' if s['endpoint_failed'] else 'decided', escalation_reason=None,
                      latency_ms=int((time.time() - t0) * 1000))
        return result
    trace.append({'step': 'escalate', 'reason': reason})
    L = model_stage('large', packet, policy, None if fault in ('malformed', 'wrong_record') else fault)
    route.append('large')
    trace.append({'step': 'large', **{k: L[k] for k in L if k != 'tier'}})
    res = 'endpoint_failed' if L['endpoint_failed'] else ('decided' if L['output'] else 'unresolved_validation')
    result.update(output=L['output'], small_output=out, route=route, trace=trace, resolution=res,
                  escalation_reason=reason, latency_ms=int((time.time() - t0) * 1000))
    return result
