"""Build the labelled case set from the OFAC SDN snapshot.
Watchlist side: real SDN entries, quoted field by field, uid and snapshot date kept.
Customer side: synthetic records written for this test (declared as such). No allegation about anyone is written.
Usage: python src/build_cases.py"""
import json, random, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import ROOT, load_sdn, parse_remarks, name_tokens, jsonl_write, jdump, apply_policy, status_for

SNAPSHOT = json.loads((ROOT / 'data' / 'snapshot.json').read_text())
rng = random.Random(20261003)
sdn = load_sdn(ROOT / 'data' / 'ofac_raw')

MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September',
               'October', 'November', 'December']
MON3 = [m[:3] for m in MONTH_NAMES]
TRANSLIT = [('Mohammad', 'Muhammad'), ('Muhammad', 'Mohammed'), ('Mohammed', 'Muhammad'), ('Mohamed', 'Mohammed'),
            ('Ahmad', 'Ahmed'), ('Ahmed', 'Ahmad'), ('Hussein', 'Husayn'), ('Husayn', 'Hussein'), ('Hasan', 'Hassan'),
            ('Hassan', 'Hasan'), ('Yusuf', 'Youssef'), ('Abdallah', 'Abdullah'), ('Abdullah', 'Abdallah'),
            ('Mahmud', 'Mahmoud'), ('Khalid', 'Khaled'), ('Umar', 'Omar'), ('Usama', 'Osama'), ('Said', 'Saeed'),
            ('Ibrahim', 'Ebrahim'), ('Mustafa', 'Mostafa'), ('Ali', 'Aly'), ('Abd al-', 'Abdul '), ('Salih', 'Saleh'),
            ('Yasin', 'Yassin'), ('Hamid', 'Hamed'), ('Rashid', 'Rasheed'), ('Jamal', 'Gamal'), ('Abu', 'Abou')]
RESIDENCE = ['United Arab Emirates', 'Turkey', 'Cyprus', 'Germany', 'United Kingdom', 'Malaysia', 'Spain',
             'Netherlands', 'Kenya', 'Panama', 'Canada', 'Singapore']


def natural(name):
    """'AL-AHDAL, Mohammad Hamdi' -> 'Mohammad Hamdi Al-Ahdal'."""
    if ',' not in name:
        return name
    sur, given = [x.strip() for x in name.split(',', 1)]
    sur = '-'.join(w.capitalize() for w in sur.lower().split('-'))
    sur = ' '.join(w[:1].upper() + w[1:] for w in sur.split(' '))
    return f'{given} {sur}'


def entity_name(name):
    return name  # company names kept exactly as written on the list


def long_date(iso):
    y, m, d = iso.split('-')
    return f'{int(d)} {MONTH_NAMES[int(m) - 1]} {y}'


def other_date(iso):
    y, m, d = iso.split('-')
    return rng.choice([f'{d}.{m}.{y}', f'{d}/{m}/{y} (day/month/year)', long_date(iso)])


def shift_date(iso):
    y, m, d = map(int, iso.split('-'))
    y2 = y + rng.choice([-11, -7, -4, 3, 6, 9, 13])
    return f'{y2:04d}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}'


def fake_passport():
    return rng.choice('ABCKNPRT') + str(rng.randint(1000000, 9999999))


def fake_reg():
    return str(rng.randint(10000000, 99999999))


def cid():
    return f'CUS-{rng.randint(10000, 99999)}'


def ascii_ok(s):
    return all(ord(c) < 128 for c in s) and len(s) < 60


# --------------------------------------------------------------- candidate pools
ind, ent = [], []
for e in sdn.values():
    if not ascii_ok(e['name']):
        continue
    r = parse_remarks(e['remarks'])
    e['parsed'] = r
    if e['type'] == 'individual' and ',' in e['name'] and not r.get('alt_dob'):
        ind.append(e)
    elif e['type'] == 'entity' and r.get('registration') and any(a['city'] for a in e['addresses']):
        ent.append(e)
rng.shuffle(ind); rng.shuffle(ent)
used = set()


def take(pool, pred, n):
    out = []
    for e in pool:
        if len(out) == n:
            break
        if e['uid'] in used or not pred(e):
            continue
        used.add(e['uid']); out.append(e)
    if len(out) < n:
        raise SystemExit(f'pool too small for predicate, got {len(out)}')
    return out


def full_ind(e):
    r = e['parsed']
    return len(r.get('dob', [])) == 1 and r.get('passport') and r.get('nationality') and not r.get('dob_year')


def full_alias(e):
    return [a['name'] for a in e['aliases'] if a['type'] == 'aka' and ',' in a['name'] and ascii_ok(a['name'])
            and name_tokens(a['name']) != name_tokens(e['name'])]


def translit_variant(e):
    nat = natural(e['name'])
    for a, b in TRANSLIT:
        if re.search(r'\b' + re.escape(a), nat):
            v = re.sub(r'\b' + re.escape(a), b, nat, count=1)
            if name_tokens(v) != name_tokens(nat) and all(name_tokens(v) != name_tokens(x['name']) for x in e['aliases']):
                return v
    return None


def year_only(e):
    r = e['parsed']
    return (len(r.get('dob_year', [])) == 1 and not r.get('dob') and r.get('nationality') and not r.get('passport')
            and not r.get('national_id'))


# --------------------------------------------------------------- watchlist record (real data, field by field)
def list_doc(e):
    r = e['parsed']
    lines = ['Source: OFAC Specially Designated Nationals list (sdn.csv, alt.csv, add.csv), '
             f"published {SNAPSHOT['published']}, retrieved {SNAPSHOT['retrieved']}",
             f"Entry UID: {e['uid']}", f"Primary name: {e['name']}",
             f"Entry type: {'individual' if e['type'] == 'individual' else 'entity'}"]
    al = [a['name'] for a in e['aliases'] if ascii_ok(a['name'])]
    lines.append('Aliases: ' + (' | '.join(al) if al else 'none listed'))
    if e['type'] == 'individual':
        if r.get('dob'):
            y, m, d = r['dob'][0].split('-'); lines.append(f'Date of birth: {d} {MON3[int(m) - 1]} {y}')
        elif r.get('dob_year'):
            lines.append(f"Date of birth: {r['dob_year'][0]}")
        else:
            lines.append('Date of birth: not listed')
        lines.append(f"Place of birth: {r.get('pob', 'not listed')}")
        lines.append('Nationality: ' + ('; '.join(r['nationality']) if r.get('nationality') else 'not listed'))
        lines.append('Passport: ' + ('; '.join(f"{p['number']} ({p['country']})" if p['country'] else p['number']
                                              for p in r['passport']) if r.get('passport') else 'not listed'))
        lines.append('National ID: ' + ('; '.join(f"{p['number']} ({p['country']})" if p['country'] else p['number']
                                                 for p in r['national_id']) if r.get('national_id') else 'not listed'))
    else:
        reg = r['registration']
        lines.append('Registration number: ' + '; '.join(f"{p['number']} ({p['country']})" if p['country'] else p['number'] for p in reg))
        ctry = reg[0]['country'] or next((a['country'] for a in e['addresses'] if a['country']), '')
        lines.append(f"Country of registration: {ctry or 'not listed'}")
        cities = sorted({a['city'] for a in e['addresses'] if a['city']})
        lines.append('Address cities: ' + '; '.join(cities))
    return {'id': 'L1', 'side': 'list', 'kind': 'structured', 'title': 'Watchlist candidate record', 'text': '\n'.join(lines)}


def cust_doc(fields):
    return {'id': 'C1', 'side': 'customer', 'kind': 'structured', 'title': 'Customer onboarding record (synthetic)',
            'text': '\n'.join(f'{k}: {v}' for k, v in fields.items())}


def free_doc(i, title, text):
    return {'id': f'C{i}', 'side': 'customer', 'kind': 'free_text', 'title': title + ' (synthetic)', 'text': text}


def ind_fields(name, dob='not captured', pob='not captured', nat='not captured', passport='not captured', res=None):
    return {'Customer ID': cid(), 'Full name': name, 'Date of birth': dob, 'Place of birth': pob,
            'Nationality': nat, 'Passport number': passport, 'National ID number': 'not captured',
            'Residence country': res or rng.choice(RESIDENCE)}


def pobc(e):
    p = e['parsed'].get('pob')
    return p.split(',')[-1].strip() if p else None


BENIGN = ['Met the customer at the branch. Purpose of the account: salary and household payments. Documents checked in person; nothing further to add.',
          'Account opened through the online channel. Expected activity: under 20 transfers a month. No other remarks from the onboarding officer.',
          'The customer asked for a debit card and online banking. The onboarding officer noted no other information.']


# --------------------------------------------------------------- families
cases = []


def add(family, e, docs, identity, note):
    packet = {'documents': [*docs, list_doc(e)]}
    cases.append({'family': family, 'sdn_uid': e['uid'], 'packet': packet,
                  'construction_identity': identity, 'construction_note': note})


for i, e in enumerate(take(ind, full_ind, 12)):                          # F1 exact
    r = e['parsed']; p = r['passport'][0]
    f = ind_fields(natural(e['name']), r['dob'][0], pobc(e) or 'not captured', r['nationality'][0],
                   f"{p['number']} ({p['country']})" if p['country'] else p['number'])
    docs = [cust_doc(f)] + ([free_doc(2, 'Onboarding officer note', BENIGN[i % 3])] if i % 3 == 0 else [])
    add('exact_match', e, docs, 'same', 'Customer record copies the list entry identifiers.')

for e in take(ind, lambda e: full_ind(e) and full_alias(e), 10):            # F2 listed alias
    r = e['parsed']; p = r['passport'][0]; al = natural(rng.choice(full_alias(e)))
    f = ind_fields(al, nat=r['nationality'][0])
    t = (f"A copy of the passport data page was provided. It shows {al}, a {r['nationality'][0]} national born on "
         f"{other_date(r['dob'][0])}, passport number {p['number']}.")
    add('listed_alias', e, [cust_doc(f), free_doc(2, 'Passport copy transcription', t)], 'same',
        'Customer uses a listed alias; identifiers copy the list entry.')

for e in take(ind, lambda e: full_ind(e) and translit_variant(e), 10):      # F3 transliteration
    r = e['parsed']; p = r['passport'][0]; v = translit_variant(e)
    f = ind_fields(v, r['dob'][0], nat=r['nationality'][0])
    t = (f"Passport data page seen at onboarding: holder {v.upper()}, date of birth {other_date(r['dob'][0])}, "
         f"document number {p['number']}.")
    add('transliteration', e, [cust_doc(f), free_doc(2, 'Passport copy transcription', t)], 'same',
        'Customer name is a spelling variant of the listed name, not itself listed; identifiers copy the list entry.')

for i, e in enumerate(take(ind, full_ind, 14)):                          # F4 homonym
    r = e['parsed']; d2 = shift_date(r['dob'][0]); pp = fake_passport()
    nat = r['nationality'][0] if i % 2 else rng.choice([c for c in RESIDENCE if c not in r['nationality']])
    f = ind_fields(natural(e['name']), d2, nat=nat, passport=pp)
    docs = [cust_doc(f)]
    if i % 2 == 0:
        docs.append(free_doc(2, 'Passport copy transcription',
                             f"Passport copy provided: {natural(e['name'])}, born {other_date(d2)}, passport {pp} issued by {nat}."))
    add('homonym', e, docs, 'different', 'Synthetic customer who shares the listed name; different date of birth and passport.')

for i, e in enumerate(take(ind, full_ind, 8)):                           # F5 name only
    r = e['parsed']
    same = i % 2 == 0
    f = ind_fields(natural(e['name']), res=(r['nationality'][0] if same else None))
    docs = [cust_doc(f)]
    if i % 3 == 0:
        docs.append(free_doc(2, 'Onboarding officer note', 'The customer did not bring an identity document today and will return with it next week. Account opening is on hold.'))
    add('name_only', e, docs, 'same' if same else 'different',
        'Only the name is captured. Underlying identity set by construction but not visible in the evidence.')

for i, e in enumerate(take(ind, full_ind, 10)):                          # F6 contradictory identifiers
    r = e['parsed']; pp = fake_passport()
    f = ind_fields(natural(e['name']), r['dob'][0], nat=r['nationality'][0], passport=pp)
    docs = [cust_doc(f)]
    if i % 2 == 0:
        docs.append(free_doc(2, 'Onboarding officer note', 'The customer stated that this is the only passport he has ever held.'
                             if r.get('gender') != 'Female' else 'The customer stated that this is the only passport she has ever held.'))
    add('contradictory_ids', e, docs, 'same', 'Same date of birth and nationality as the entry, but a passport number that is not listed.')

for e in take(ind, lambda e: full_ind(e), 8):                             # F7 relative trap
    r = e['parsed']; d2 = shift_date(r['dob'][0])
    f = ind_fields(natural(e['name']), d2, nat=r['nationality'][0])
    older = r['dob'][0] < d2                     # the relative's (listed) date is earlier: the relative is older
    pron = 'her' if r.get('gender') == 'Female' else 'his'
    rel = ('elder ' if older else 'younger ') + ('sister' if r.get('gender') == 'Female' else 'brother')
    t = (f"The customer explained that the account will also receive transfers from {pron} {rel}, born "
         f"{long_date(r['dob'][0])}, who lives abroad. The {rel.split()[-1]} is not a party to this account.")
    add('relative_trap', e, [cust_doc(f), free_doc(2, 'Onboarding officer note', t)], 'different',
        "Customer's own date of birth differs; the note gives a relative's date of birth equal to the listed one.")

for i, e in enumerate(take(ind, year_only, 8)):                          # F8 year-only listing
    r = e['parsed']; y = int(r['dob_year'][0])
    f = ind_fields(natural(e['name']), f'{y}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}', nat=r['nationality'][0])
    docs = [cust_doc(f)] + ([free_doc(2, 'Onboarding officer note', BENIGN[i % 3])] if i % 2 else [])
    add('year_only', e, docs, 'same', 'List gives only a birth year; the customer full date falls in it; no document number on the list.')

for i, e in enumerate(take(ent, lambda e: True, 10)):                    # F9 entity match
    r = e['parsed']; reg = r['registration'][0]
    ctry = reg['country'] or next((a['country'] for a in e['addresses'] if a['country']), '')
    city = next(a['city'] for a in e['addresses'] if a['city'])
    nm = entity_name(e['name'])
    f = {'Customer ID': cid(), 'Legal name': nm, 'Registration number': reg['number'],
         'Country of incorporation': ctry or 'not captured', 'Registered office city': city}
    docs = [cust_doc(f)]
    if i % 2 == 0:
        docs.append(free_doc(2, 'Company register extract',
                             f'Extract from the companies register: {nm.upper()} is registered under number {reg["number"]}, '
                             f'registered office in {city}. Status shown on the extract: active.'))
    add('entity_match', e, docs, 'same', 'Company record copies the entry registration number, country and city.')

for i, e in enumerate(take(ent, lambda e: True, 10)):                    # F10 entity homonym
    r = e['parsed']; reg = r['registration'][0]
    ctry = reg['country'] or next((a['country'] for a in e['addresses'] if a['country']), '')
    c2 = rng.choice([c for c in RESIDENCE if c != ctry])
    nm = entity_name(e['name']); rn = fake_reg()
    f = {'Customer ID': cid(), 'Legal name': nm, 'Registration number': rn,
         'Country of incorporation': c2, 'Registered office city': {'United Arab Emirates': 'Dubai', 'Turkey': 'Izmir', 'Cyprus': 'Larnaca', 'Germany': 'Hamburg',
                                                                    'United Kingdom': 'Leeds', 'Malaysia': 'Penang', 'Spain': 'Valencia', 'Netherlands': 'Utrecht',
                                                                    'Kenya': 'Mombasa', 'Panama': 'Colon', 'Canada': 'Calgary', 'Singapore': 'Singapore'}[c2]}
    docs = [cust_doc(f)]
    if i % 2 == 0:
        docs.append(free_doc(2, 'Company register extract',
                             f'Extract from the companies register of {c2}: {nm.upper()}, number {rn}, incorporated in {2008 + i}, registered office in {f["Registered office city"]}.'))
    add('entity_homonym', e, docs, 'different', 'Synthetic company sharing the listed name; different registration number, country and city.')


# --------------------------------------------------------------- reference answers
def reference(c):
    """Reference written from the construction, family by family, then checked by recomputing the policy."""
    fam = c['family']
    lst = c['packet']['documents'][-1]['text']
    has = lambda k: re.search(rf'^{k}: (?!not listed)', lst, re.M) is not None
    ag, cf, miss = [], [], []
    if fam in ('exact_match',):
        ag = ['date_of_birth', 'passport_number', 'nationality'] + (['place_of_birth'] if 'Place of birth: not captured' not in c['packet']['documents'][0]['text'] else [])
        nm = 'exact'
    elif fam == 'listed_alias':
        ag, nm = ['date_of_birth', 'passport_number', 'nationality'], 'listed_alias'
    elif fam == 'transliteration':
        ag, nm = ['date_of_birth', 'passport_number', 'nationality'], 'transliteration_variant'
    elif fam == 'homonym':
        cf, nm = ['date_of_birth', 'passport_number'], 'exact'
        if 'Nationality: ' in c['packet']['documents'][0]['text']:
            cn = re.search(r'^Nationality: (.+)$', c['packet']['documents'][0]['text'], re.M)[1]
            ln = re.search(r'^Nationality: (.+)$', lst, re.M)[1]
            (ag if cn in ln.split('; ') else cf).append('nationality')
    elif fam == 'name_only':
        nm, miss = 'exact', ['date_of_birth', 'passport_number', 'nationality']
    elif fam == 'contradictory_ids':
        ag, cf, nm = ['date_of_birth', 'nationality'], ['passport_number'], 'exact'
    elif fam == 'relative_trap':
        ag, cf, nm, miss = ['nationality'], ['date_of_birth'], 'exact', ['passport_number']
    elif fam == 'year_only':
        ag, nm, miss = ['year_of_birth', 'nationality'], 'exact', ['passport_number']
    elif fam == 'entity_match':
        ag, nm = ['registration_number', 'country_of_registration', 'city'], 'exact'
    elif fam == 'entity_homonym':
        cf, nm = ['registration_number', 'country_of_registration', 'city'], 'exact'
    entity = fam.startswith('entity')
    pool = (['registration_number', 'country_of_registration', 'city'] if entity else
            ['date_of_birth', 'passport_number', 'national_id', 'nationality', 'place_of_birth'])
    miss = [a for a in pool if a not in ag and a not in cf]  # every identifier that cannot be compared (review round 1)
    comps = [{'attribute': a, 'relation': 'agree'} for a in ag] + [{'attribute': a, 'relation': 'conflict'} for a in cf]
    disp = apply_policy(nm, comps)
    just = {
        'same_entity_supported': f"Name criterion met ({nm.replace('_', ' ')}); {', '.join(ag)} agree; nothing conflicts.",
        'different_entity_supported': f"{', '.join(x for x in cf)} conflict and no strong identifier agrees.",
        'insufficient_evidence': ('Only the name is available; ' if fam == 'name_only' else '') +
            ('Strong agreement and a conflict at the same time.' if cf and ag and fam == 'contradictory_ids' else
             'No strong identifier can be compared.' if fam in ('year_only', 'name_only') else ''),
    }[disp]
    trap = {'relative_trap': "The note's date of birth belongs to a relative, not the customer.",
            'year_only': 'The list gives only a birth year; a full-date agreement would be an overclaim.',
            'name_only': 'Underlying identity is known to the builder but not supported by the evidence.',
            'contradictory_ids': 'Agreeing date of birth and nationality do not outweigh an unlisted passport number under this policy.'}.get(fam)
    return {'construction_identity': c['construction_identity'], 'name_match': nm, 'permitted_disposition': disp,
            'permitted_status': status_for(disp), 'decisive_agreements': ag, 'decisive_conflicts': cf,
            'missing_information': miss, 'justification': just.strip(), 'trap': trap}


rng2 = random.Random(7)
by_fam = {}
for c in cases:
    by_fam.setdefault(c['family'], []).append(c)
out = []
for fam, cs in by_fam.items():
    idx = list(range(len(cs))); rng2.shuffle(idx)
    for k, j in enumerate(idx):
        c = cs[j]
        c['split'] = 'dev' if k < 2 else 'test'
for n, c in enumerate(cases, 1):
    c['case_id'] = f'case-{n:03d}'
    c['reference'] = reference(c)
    c.pop('construction_identity')
    out.append({k: c[k] for k in ('case_id', 'family', 'split', 'sdn_uid', 'packet', 'reference', 'construction_note')})
jsonl_write(out, ROOT / 'cases' / 'cases.jsonl')
summary = {}
for c in out:
    s = summary.setdefault(c['family'], {'dev': 0, 'test': 0, 'permitted': c['reference']['permitted_disposition']})
    s[c['split']] += 1
jdump(summary, ROOT / 'cases' / 'families.json')
print(json.dumps(summary, indent=1))
print('test true matches (construction):', sum(1 for c in out if c['split'] == 'test' and c['reference']['construction_identity'] == 'same'))
