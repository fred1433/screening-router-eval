"""R6: new cases generated after the v2 code freeze (commit 8e11387), never seen while v2 was written.
Same OFAC snapshot (watchlist side real, field by field); customer side synthetic. References follow policy A2.
Usage: python src/build_cases_v2.py  (writes cases/cases_v2_new.jsonl)"""
import json, random, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import ROOT, jsonl_read, jsonl_write, apply_policy, status_for, parse_remarks

# reuse the v1 helpers and pools (snapshot parsing, record writers) without rewriting the v1 case file
src = (ROOT / 'src' / 'build_cases.py').read_text()
ns = {'__file__': str(ROOT / 'src' / 'build_cases.py')}
exec(src[:src.index('# --------------------------------------------------------------- families')], ns)
ns['rng'].seed(20261004)
ns['used'].update(c['sdn_uid'] for c in jsonl_read(ROOT / 'cases' / 'cases.jsonl'))
rng, take, ind, ent, sdn = ns['rng'], ns['take'], ns['ind'], ns['ent'], ns['sdn']
natural, cust_doc, free_doc, ind_fields, list_doc = ns['natural'], ns['cust_doc'], ns['free_doc'], ns['ind_fields'], ns['list_doc']
other_date, shift_date, fake_passport, fake_reg, cid, ascii_ok, MON3 = (ns[k] for k in (
    'other_date', 'shift_date', 'fake_passport', 'fake_reg', 'cid', 'ascii_ok', 'MON3'))

cases = []


def add(family, e, docs, identity, nm, ag, cf, note, lst=None):
    entity = family.startswith('entity')
    pool = ['registration_number', 'country_of_registration', 'city'] if entity else \
        ['date_of_birth', 'passport_number', 'national_id', 'nationality', 'place_of_birth']
    miss = [a for a in pool if a not in ag and a not in cf]
    comps = [{'attribute': a, 'relation': 'agree'} for a in ag] + [{'attribute': a, 'relation': 'conflict'} for a in cf]
    disp = apply_policy(nm, comps, 'A2')
    cases.append({'case_id': f'v2-{len(cases) + 1:03d}', 'family': family, 'split': 'v2_new', 'sdn_uid': e['uid'],
                  'packet': {'documents': [*docs, lst or list_doc(e)]},
                  'reference': {'construction_identity': identity, 'name_match': nm, 'permitted_disposition': disp,
                                'permitted_status': status_for(disp), 'decisive_agreements': ag, 'decisive_conflicts': cf,
                                'missing_information': miss, 'justification': note, 'trap': None, 'policy': 'A2'},
                  'construction_note': note})


def iso_to_list(iso):
    y, m, d = iso.split('-'); return f'{d} {MON3[int(m) - 1]} {y}'


# 1. customer identifier present, absent from the list
pool1 = lambda e: len(e['parsed'].get('dob', [])) == 1 and e['parsed'].get('nationality') and not e['parsed'].get('passport') \
    and not e['parsed'].get('national_id') and not e['parsed'].get('dob_year')
for i, e in enumerate(take(ind, pool1, 6)):
    r = e['parsed']
    f = ind_fields(natural(e['name']), r['dob'][0], nat=r['nationality'][0], passport=fake_passport())
    docs = [cust_doc(f)] + ([free_doc(2, 'Onboarding officer note', 'Passport seen in person at the branch.')] if i % 2 else [])
    add('id_present_list_absent', e, docs, 'same', 'exact', ['date_of_birth', 'nationality'], [],
        'The customer passport number has nothing to compare with: the list gives no passport.')

# 2. renewed passport
for i, e in enumerate(take(ind, ns['full_ind'], 6)):
    r = e['parsed']; pp = fake_passport()
    f = ind_fields(natural(e['name']), r['dob'][0], nat=r['nationality'][0], passport=pp)
    t = f'The customer said the passport was renewed in {2021 + i % 4}; the previous passport number was not recorded.'
    add('renewed_passport', e, [cust_doc(f), free_doc(2, 'Onboarding officer note', t)], 'same', 'exact',
        ['date_of_birth', 'nationality'], ['passport_number'],
        'Same date of birth and nationality; a different, renewed passport. A passport mismatch alone cannot exclude.')

# 3. alternative dates of birth on the list
alt_pool = []
for e in sdn.values():
    if e['type'] != 'individual' or ',' not in e['name'] or not ascii_ok(e['name']) or e['uid'] in ns['used']:
        continue
    r = parse_remarks(e['remarks'])
    MON = {m: i for i, m in enumerate(MON3, 1)}
    alts = [f'{y}-{MON[m]:02d}-{int(d):02d}' for d, m, y in re.findall(r'alt\. DOB (\d{2}) ([A-Z][a-z]{2}) (\d{4})', e['remarks'])]
    if len(r.get('dob', [])) == 1 and alts:
        r['dob'] = r['dob'] + alts
    if len(r.get('dob', [])) >= 2 and r.get('nationality'):
        e['parsed'] = r; alt_pool.append(e)
rng.shuffle(alt_pool)
for e in alt_pool[:5]:
    ns['used'].add(e['uid']); r = e['parsed']
    L = list_doc(e)
    L['text'] = re.sub(r'^Date of birth: .*$', 'Date of birth: ' + '; '.join(iso_to_list(d) for d in r['dob']), L['text'], flags=re.M)
    pp = r.get('passport', [{}])[0].get('number') if r.get('passport') else None
    f = ind_fields(natural(e['name']), r['dob'][1], nat=r['nationality'][0], passport=pp or 'not captured')
    ag = ['date_of_birth', 'nationality'] + (['passport_number'] if pp else [])
    add('alternative_dob', e, [cust_doc(f)], 'same', 'exact', ag, [],
        "The customer's date of birth is the list's second, alternative date.", lst=L)

# 4. two customer documents that contradict each other
for e in take(ind, ns['full_ind'], 5):
    r = e['parsed']; p = r['passport'][0]['number']; d2 = shift_date(r['dob'][0])
    f = ind_fields(natural(e['name']), r['dob'][0], nat=r['nationality'][0])
    t = f"Passport copy provided: {natural(e['name'])}, born {other_date(d2)}, passport {p}."
    add('contradictory_customer_documents', e, [cust_doc(f), free_doc(2, 'Passport copy transcription', t)], 'same', 'exact',
        ['date_of_birth', 'passport_number', 'nationality'], ['date_of_birth'],
        'The onboarding record and the passport copy give two different dates of birth for the customer.')

# 5. company homonym in the same country and city
for e in take(ent, lambda e: not e['parsed']['registration'][0]['number'].upper().startswith('IMO'), 5):  # an IMO ship number is not a company registration (review, 04/10)
    r = e['parsed']; reg = r['registration'][0]
    ctry = reg['country'] or next((a['country'] for a in e['addresses'] if a['country']), '')
    city = next(a['city'] for a in e['addresses'] if a['city'])
    rn = fake_reg()
    f = {'Customer ID': cid(), 'Legal name': e['name'], 'Registration number': rn,
         'Country of incorporation': ctry or 'not captured', 'Registered office city': city}
    add('entity_homonym_same_city', e, [cust_doc(f)], 'different', 'exact',
        ['country_of_registration', 'city'] if ctry else ['city'], ['registration_number'],
        'Synthetic company with the listed name in the same country and city, under another registration number.')

# 6. long names
long_ok = lambda e: ns['full_ind'](e) and len(re.findall(r'[A-Za-z]+', e['name'])) >= 5
for e in take(ind, long_ok, 4):
    r = e['parsed']; p = r['passport'][0]
    f = ind_fields(natural(e['name']), r['dob'][0], nat=r['nationality'][0],
                   passport=f"{p['number']} ({p['country']})" if p['country'] else p['number'])
    add('long_name', e, [cust_doc(f)], 'same', 'exact', ['date_of_birth', 'passport_number', 'nationality'], [],
        'A long listed name copied in natural order; identifiers copy the entry.')

jsonl_write(cases, ROOT / 'cases' / 'cases_v2_new.jsonl')
from collections import Counter
print(len(cases), dict(Counter(c['family'] for c in cases)), dict(Counter(c['reference']['permitted_disposition'] for c in cases)))
