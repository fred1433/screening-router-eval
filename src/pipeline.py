"""The four configurations under test and the shared controls they all use.

deterministic : structured-field rules only; anything that needs reading free text stays unresolved.
small / large : one model reads the packet; shared validators; one repair call on a failed check.
router        : deterministic stage, then the small model, then the large model only for observable reasons.
"""
import json, re, time
from pathlib import Path
from common import (ROOT, ATTRIBUTES, DISPOSITIONS, STRONG, apply_policy, status_for, structured_relations,
                    has_free_text, render_packet, name_class, list_names, read_fields)
import llm

POLICY = (ROOT / 'policy' / 'policy_a.md').read_text()
POLICY_B = (ROOT / 'policy' / 'policy_b.md').read_text() if (ROOT / 'policy' / 'policy_b.md').exists() else ''
POLICY_A2 = (ROOT / 'policy' / 'policy_a2.md').read_text()
VERSION = 'v2'
DEFAULT_POLICY = 'A2'
CALLER = llm.call            # replaced by a replay caller when recorded answers are re-validated without new calls
SAME_ENTITY_ACTION = ('Review the supported identity match and refer it for the applicable sanctions decision. '
                      'This result does not authorize onboarding.')
AUTHORIZING = re.compile(r'\b(proceed(?:s|ing)?\s+(?:with|to)\b|approv(?:e[sd]?|ing)\b|onboard(?:s|ed)?\s+(?:the|this)\b'
                         r'|clear(?:s|ed)?\s+(?:the\s+|this\s+)?(?:customer|alert|match|account)'
                         r'|(?:can|may)\s+be\s+(?:onboarded|cleared|approved))', re.I)
RELATIVE = re.compile(r'\b(brother|sister|father|mother|son|daughter|wife|husband|spouse|relative|sibling|cousin|uncle|aunt)s?\b', re.I)
FIELD_LABELS = {
    'date_of_birth': {'date of birth'}, 'year_of_birth': {'date of birth'},
    'passport_number': {'passport number', 'passport'}, 'national_id': {'national id number', 'national id'},
    'nationality': {'nationality'}, 'place_of_birth': {'place of birth'},
    'registration_number': {'registration number'}, 'country_of_registration': {'country of incorporation', 'country of registration'},
    'city': {'registered office city', 'address cities'}}
KNOWN_LABELS = set().union(*FIELD_LABELS.values()) | {'residence country', 'customer id', 'full name', 'legal name',
                                                     'primary name', 'aliases', 'entry uid', 'entry type', 'source'}
SYSTEM_TMPL = (ROOT / 'prompts' / 'disposition_system.md').read_text()
REPAIR_TMPL = ('Your previous answer failed these checks:\n{errors}\n\nReturn the corrected JSON object only, '
               'following the same rules. Quotes must be copied exactly from the cited document.')
REPAIR_BUDGET = 1  # one repair call per model, identical for every configuration


def system_prompt(policy='A'):
    return SYSTEM_TMPL.replace('{POLICY}', {'A': POLICY, 'B': POLICY_B, 'A2': POLICY_A2}[policy])


def field_problems(c, docs):
    """v2: a quote is bound to the field it is cited for, and to the customer. A structured line proves only its own
    label (Residence country is not a nationality); a free-text quote about a relative proves nothing about the customer."""
    errs = []
    for side, cite_key in (('customer', 'customer_cite'), ('list', 'list_cite')):
        cite = c.get(cite_key) or {}
        q = str(cite.get('quote') or '')
        m = re.match(r'^\s*([A-Za-z ]+?)\s*:', q)
        if m and m[1].strip().lower() in KNOWN_LABELS and m[1].strip().lower() not in FIELD_LABELS.get(c['attribute'], set()):
            errs.append(('citation_field', f"{c['attribute']}: {side} quote is the field '{m[1].strip()}'"))
        d = docs.get(cite.get('doc'))
        if side == 'customer' and d is not None and d.get('kind') == 'free_text' and RELATIVE.search(q):
            errs.append(('citation_subject', f"{c['attribute']}: customer quote is about another person"))
    return errs


def next_action_problems(out):
    w = str((out or {}).get('outstanding_work') or '')
    if w == SAME_ENTITY_ACTION:
        return []
    m = AUTHORIZING.search(w)
    return [('next_action', f'next action uses authorizing language: "{m[0]}"')] if m else []


def code_name_class(packet):
    cust = next(d for d in packet['documents'] if d['side'] == 'customer' and d['kind'] == 'structured')
    f = read_fields(cust['text'])
    lst = next(d for d in packet['documents'] if d['side'] == 'list')
    return name_class(f.get('Full name') or f.get('Legal name') or '', *list_names(lst['text']))


def set_code_name(out, packet):
    """v2: when code finds exact or listed_alias, that class replaces the model's."""
    if isinstance(out, dict) and out.get('name_match') in ('exact', 'listed_alias', 'transliteration_variant', 'none'):
        code = code_name_class(packet)
        if code in ('exact', 'listed_alias') and out['name_match'] != code:
            out = {**out, 'name_match': code, 'name_match_model': out['name_match']}
    return out


def name_problems(out, packet):
    """v2: exact and listed_alias are computed by code; the model cannot claim them on its own."""
    code = code_name_class(packet)
    said = out.get('name_match')
    if code == 'unnormalizable':
        return [('name_unnormalizable', 'the customer name leaves nothing to compare after normalisation; stop for a person')]
    if said == 'exact' and code != 'exact':
        return [('name_class', f'model says exact, code finds {code}')]
    if said == 'listed_alias' and code not in ('exact', 'listed_alias'):
        return [('name_class', f'model says listed_alias, code finds {code}')]
    return []


def finalize(out):
    """v2: for a supported match the next action is fixed text tied to the status; the model does not write it."""
    if isinstance(out, dict) and out.get('disposition') == 'same_entity_supported':
        out = {**out, 'outstanding_work': SAME_ENTITY_ACTION}
    return out


def user_prompt(packet):
    return 'Packet:\n\n' + render_packet(packet)


# ------------------------------------------------------------------ shared validators
def _norm(s):
    return re.sub(r'\s+', ' ', (s or '')).strip().lower()


ABSENT = re.compile(r'not captured|not listed|not provided|none listed|not available', re.I)
DATE_PATTERNS = [r'\d{4}-\d{2}-\d{2}', r'\d{1,2} [A-Za-z]{3,9} \d{4}', r'\d{1,2}/\d{1,2}/\d{4}']
STRICT_CITATIONS = True   # added after the final run (fresh review, 2026-10-04); the scored run used the earlier checks


def _dates(text):
    from common import parse_date
    out = set()
    for pat in DATE_PATTERNS:
        for m in re.findall(pat, text or ''):
            if '/' in m:
                d, mo, y = m.split('/')
                out.add(f'{y}-{int(mo):02d}-{int(d):02d}')
            else:
                v = parse_date(m)
                if v:
                    out.add(v)
    return out


def _tokens(s):
    from common import strip_accents
    return set(re.findall(r'[a-z0-9]+', strip_accents(str(s or '')).lower()))


def citation_problems(c):
    """Stricter citation checks for one agree/conflict comparison: a non-trivial quote, not a line that records an
    absent value, and one that contains the value it is cited for (dates compared across formats)."""
    errs = []
    for side, cite_key, val_key in (('customer', 'customer_cite', 'customer_value'), ('list', 'list_cite', 'list_value')):
        cite = c.get(cite_key) or {}
        q, v = str(cite.get('quote') or ''), c.get(val_key)
        if len(re.sub(r'[^A-Za-z0-9]', '', q)) < 4:
            errs.append(('citation_trivial', f"{c['attribute']}: {side} quote is empty or too short")); continue
        if ABSENT.search(q):
            errs.append(('citation_absent_value', f"{c['attribute']}: {side} quote records an absent value")); continue
        if v in (None, ''):
            errs.append(('citation_value', f"{c['attribute']}: {side} value missing")); continue
        vd = _dates(str(v))
        if vd:
            if not (vd & _dates(q)):
                errs.append(('citation_value', f"{c['attribute']}: {side} quote does not contain the date {v}"))
            continue
        vt, qt = _tokens(v), _tokens(q)
        digits = {t for t in vt if any(ch.isdigit() for ch in t)}
        words = {t for t in vt - digits if len(t) >= 3}
        if (digits and not digits <= qt) or (not digits and words and not (words & qt)):
            errs.append(('citation_value', f"{c['attribute']}: {side} quote does not contain the value {v}"))
    return errs


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
        if STRICT_CITATIONS and not any(e[1].startswith(c['attribute'] + ':') for e in errs):
            errs.extend(citation_problems(c))
        if VERSION == 'v2' and not any(e[1].startswith(c['attribute'] + ':') for e in errs):
            errs.extend(field_problems(c, docs))
    if VERSION == 'v2':
        errs.extend(name_problems(out, packet))
        errs.extend(next_action_problems(out))
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
        return None, f'not valid JSON ({e.msg}, char {e.pos})'


# ------------------------------------------------------------------ one model with shared controls
def model_stage(tier, packet, policy='A', fault=None, no_repair=()):
    """Call, validate, at most one repair. Returns dict with output (or None), calls, checks.
    Checks listed in no_repair end the stage at once (the router escalates on them instead of repairing)."""
    msgs = [{'role': 'system', 'content': system_prompt(policy)}, {'role': 'user', 'content': user_prompt(packet)}]
    calls, attempts = [], []
    for k in range(REPAIR_BUDGET + 1):
        r = CALLER(tier, msgs, injected_fault=fault if fault == 'endpoint_down' else None)
        if r.get('not_rerun'):
            return {'tier': tier, 'output': None, 'calls': calls, 'attempts': attempts, 'endpoint_failed': False,
                    'pending_rerun': True, 'failed_checks': attempts[-1]['checks_failed'] if attempts else []}
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
        if VERSION == 'v2':
            out = finalize(set_code_name(out, packet))
        errs = [('schema', perr)] if perr else validate(out, packet, policy)
        attempts.append({'raw': content[:6000], 'parsed': out, 'checks_failed': [list(e) for e in errs]})
        if not errs:
            return {'tier': tier, 'output': out, 'calls': calls, 'attempts': attempts, 'endpoint_failed': False}
        if any(e[0] in no_repair for e in errs):
            return {'tier': tier, 'output': None, 'calls': calls, 'attempts': attempts, 'endpoint_failed': False,
                    'failed_checks': [list(e) for e in errs]}
        msgs = msgs + [{'role': 'assistant', 'content': content[:6000]},
                       {'role': 'user', 'content': REPAIR_TMPL.format(errors='\n'.join(f'- {a}: {b}' for a, b in errs))}]
    return {'tier': tier, 'output': None, 'calls': calls, 'attempts': attempts, 'endpoint_failed': False,
            'failed_checks': attempts[-1]['checks_failed']}


# ------------------------------------------------------------------ deterministic stage
def deterministic_stage(packet, policy='A'):
    name_match, rel = structured_relations(packet)
    if name_match == 'unnormalizable':
        return None, 'the customer name leaves nothing to compare after normalisation'
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
           'outstanding_work': 'Review the supported identity difference before closing the alert.' if disp != 'insufficient_evidence' else
           ('Obtain ' + ', '.join(missing) + '.' if missing else 'Resolve the conflicting identifiers.'),
           'rationale': 'Rule-based comparison of the two structured records.'}
    if VERSION == 'v2':
        out = finalize(out)
        errs = validate(out, packet, policy)       # the rules' output crosses the same validator as the models'
        if errs:
            return None, 'rules output failed the shared checks: ' + '; '.join(e[1] for e in errs)
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
        res = 'endpoint_failed' if st['endpoint_failed'] else ('pending_rerun' if st.get('pending_rerun') else
                                                               'decided' if st['output'] else 'unresolved_validation')
        result.update(output=st['output'], route=route, trace=trace, resolution=res, escalation_reason=None,
                      latency_ms=int((time.time() - t0) * 1000))
        return result
    # router
    s = model_stage('small', packet, policy, fault, no_repair=('name_class',) if VERSION == 'v2' else ())
    route.append('small')
    trace.append({'step': 'small', **{k: s[k] for k in s if k != 'tier'}})
    out = s['output']
    if s.get('pending_rerun'):
        result.update(output=None, route=route, trace=trace, resolution='pending_rerun', escalation_reason=None,
                      latency_ms=int((time.time() - t0) * 1000))
        return result
    if s['endpoint_failed']:
        reason = 'small endpoint failed'
    elif out is None and any(c[0] == 'name_class' for c in s.get('failed_checks', [])):
        reason = 'name class not confirmed by code'
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
    res = 'endpoint_failed' if L['endpoint_failed'] else ('pending_rerun' if L.get('pending_rerun') else
                                                          'decided' if L['output'] else 'unresolved_validation')
    result.update(output=L['output'], small_output=out, route=route, trace=trace, resolution=res,
                  escalation_reason=reason, latency_ms=int((time.time() - t0) * 1000))
    return result
