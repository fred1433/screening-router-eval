"""Shared pieces: SDN parsing, name and date normalisation, the illustrative policy, and the
deterministic reader of structured records. Nothing here calls a model."""
import csv, json, re, unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MONTHS = {m: i for i, m in enumerate(
    ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'], 1)}

STRONG = {'date_of_birth', 'passport_number', 'national_id', 'registration_number'}
WEAK = {'nationality', 'place_of_birth', 'year_of_birth', 'country_of_registration', 'city'}
ATTRIBUTES = sorted(STRONG | WEAK)
DISPOSITIONS = ['same_entity_supported', 'different_entity_supported', 'insufficient_evidence']


# ---------------------------------------------------------------- SDN snapshot
def _clean(v):
    v = (v or '').strip()
    return '' if v == '-0-' else v


def load_sdn(raw_dir):
    raw_dir = Path(raw_dir)
    enc = 'latin-1'
    sdn = {}
    for r in csv.reader(open(raw_dir / 'sdn.csv', encoding=enc)):
        if len(r) < 12:
            continue
        sdn[r[0]] = {'uid': r[0], 'name': _clean(r[1]), 'type': _clean(r[2]) or 'entity',
                     'programs': _clean(r[3]), 'remarks': _clean(r[11]), 'aliases': [], 'addresses': []}
    for r in csv.reader(open(raw_dir / 'alt.csv', encoding=enc)):
        if len(r) >= 4 and r[0] in sdn:
            sdn[r[0]]['aliases'].append({'type': _clean(r[2]), 'name': _clean(r[3])})
    for r in csv.reader(open(raw_dir / 'add.csv', encoding=enc)):
        if len(r) >= 5 and r[0] in sdn:
            sdn[r[0]]['addresses'].append({'street': _clean(r[2]), 'city': _clean(r[3]), 'country': _clean(r[4])})
    return sdn


def parse_remarks(rem):
    """Pull identifiers out of the SDN remarks field (the format is semi-structured)."""
    out = {}
    parts = [p.strip() for p in rem.split(';')]
    for p in parts:
        m = re.match(r'^DOB (\d{2}) ([A-Z][a-z]{2}) (\d{4})$', p)
        if m:
            out.setdefault('dob', []).append(f'{m[3]}-{MONTHS[m[2].lower()]:02d}-{int(m[1]):02d}')
            continue
        m = re.match(r'^DOB (\d{4})$', p)
        if m:
            out.setdefault('dob_year', []).append(m[1])
            continue
        if p.startswith('alt. DOB'):
            out['alt_dob'] = True
        m = re.match(r'^POB (.+)$', p)
        if m:
            out['pob'] = m[1].rstrip('.')
        m = re.match(r'^nationality (.+)$', p)
        if m:
            out.setdefault('nationality', []).append(m[1].rstrip('.'))
        m = re.match(r'^Passport ([A-Z0-9 ]+?)(?: \(([^)]+)\))?(?: issued.*)?\.?$', p)
        if m:
            out.setdefault('passport', []).append({'number': m[1].strip(), 'country': m[2] or ''})
        m = re.match(r'^National ID No\. ([A-Z0-9 \-]+?)(?: \(([^)]+)\))?\.?$', p)
        if m:
            out.setdefault('national_id', []).append({'number': m[1].strip(), 'country': m[2] or ''})
        m = re.match(r'^(?:UK )?(?:Registration ID|Company Number|Registration Number) ([A-Z0-9\-/ ]+?)(?: \(([^)]+)\))?\.?$', p)
        if m:
            out.setdefault('registration', []).append({'number': m[1].strip(), 'country': m[2] or ''})
        m = re.match(r'^Gender (\w+)', p)
        if m:
            out['gender'] = m[1]
    return out


# ---------------------------------------------------------------- normalisation
ABSENT_VALUES = {'', 'not listed', 'not captured', 'not provided', 'n/a', 'na', 'none', 'none listed', 'not available',
                 'unknown', '-', '-0-'}


def is_absent(v):
    """The single test for an absent value, applied to both sides before any comparison (v2)."""
    return v is None or re.sub(r'\s+', ' ', str(v)).strip().lower().rstrip('.') in ABSENT_VALUES

def strip_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))


def name_tokens(name):
    s = strip_accents(name).lower().replace("'", '')
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    toks = [t for t in s.split() if t not in {'ltd', 'limited', 'llc', 'co', 'company', 'the', 'inc', 'sa', 'plc'}]
    return tuple(sorted(toks))


def name_class(customer_name, primary, aliases):
    """Name class computed by code (v2): 'exact', 'listed_alias', 'none', or 'unnormalizable' when normalisation
    leaves nothing to compare (for example a name in a non-Latin script). Never equality on empty strings."""
    c = name_tokens(customer_name or '')
    if not c:
        return 'unnormalizable'
    p = name_tokens(primary or '')
    if p and c == p:
        return 'exact'
    if any(name_tokens(a) and c == name_tokens(a) for a in aliases):
        return 'listed_alias'
    return 'none'


def list_names(list_text):
    f = read_fields(list_text)
    al = f.get('Aliases', '')
    return f.get('Primary name', ''), ([] if is_absent(al) else [a.strip() for a in al.split(' | ') if a.strip()])


def norm_id(s):
    return re.sub(r'[^A-Z0-9]', '', (s or '').upper())


def parse_date(s):
    """ISO 'YYYY-MM-DD' or the SDN style 'DD Mon YYYY'. Returns ISO or None."""
    s = (s or '').strip()
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', s)
    if m:
        return s
    m = re.match(r'^(\d{1,2}) ([A-Za-z]{3})[a-z]* (\d{4})$', s)
    if m and m[2].lower() in MONTHS:
        return f'{m[3]}-{MONTHS[m[2].lower()]:02d}-{int(m[1]):02d}'
    return None


# ---------------------------------------------------------------- the illustrative policy
POLICY_TEXT = (ROOT / 'policy' / 'policy_a.md').read_text() if (ROOT / 'policy' / 'policy_a.md').exists() else ''


def apply_policy(name_match, comparisons, policy='A2'):
    """Recompute disposition from a list of comparisons [{attribute, relation}].
    Policy A: same = name criterion + >=2 agreeing identifiers incl. >=1 strong + no conflict.
    Policy B (stricter, demo only): same additionally needs two agreeing strong identifiers.
    Policy A2 (v2 default): as A, but only a conflicting date of birth or registration number can exclude."""
    agree = {c['attribute'] for c in comparisons if c.get('relation') == 'agree'}
    conflict = {c['attribute'] for c in comparisons if c.get('relation') == 'conflict'}
    name_ok = name_match in ('exact', 'listed_alias', 'transliteration_variant')
    strong_agree, strong_conflict = agree & STRONG, conflict & STRONG
    if name_ok and len(agree) >= 2 and strong_agree and not conflict:
        if policy == 'B' and len(strong_agree) < 2:
            return 'insufficient_evidence'
        return 'same_entity_supported'
    if policy == 'A2':
        # v2: a passport or national ID mismatch alone never excludes (documents are renewed or duplicated);
        # exclusion needs a conflicting date of birth or registration number.
        if strong_conflict & {'date_of_birth', 'registration_number'} and not strong_agree:
            return 'different_entity_supported'
        return 'insufficient_evidence'
    if strong_conflict and not strong_agree:
        return 'different_entity_supported'
    return 'insufficient_evidence'


def status_for(disposition):
    return 'further_investigation' if disposition == 'insufficient_evidence' else 'ready_for_analyst_signoff'


# ---------------------------------------------------------------- deterministic reader of structured records
def read_fields(text):
    f = {}
    for line in text.splitlines():
        if ':' in line:
            k, v = line.split(':', 1)
            f[k.strip()] = v.strip()
    return f


def split_doc_numbers(v):
    """'304555 (Egypt); B 960789' -> [('304555','Egypt'), ...]"""
    out = []
    for part in [p.strip() for p in (v or '').split(';') if p.strip()]:
        m = re.match(r'^(.+?)(?: \(([^)]+)\))?$', part)
        out.append((norm_id(m[1]), (m[2] or '').strip()))
    return out


def structured_relations(packet):
    """Compare structured customer and list fields. Returns {attribute: (relation, cust_line, list_line)}
    using only the two structured records, plus name-match class. Free text is never read here."""
    cust = next(d for d in packet['documents'] if d['side'] == 'customer' and d['kind'] == 'structured')
    lst = next(d for d in packet['documents'] if d['side'] == 'list')
    cf, lf = read_fields(cust['text']), read_fields(lst['text'])
    def line(doc, key):
        for l in doc['text'].splitlines():
            if l.startswith(key + ':'):
                return l
        return None
    rel = {}
    na = is_absent
    # name
    cname = cf.get('Full name') or cf.get('Legal name') or ''
    nc = name_class(cname, *list_names(lst['text']))
    name_match = nc if nc in ('exact', 'listed_alias') else ('unnormalizable' if nc == 'unnormalizable' else 'unresolved')
    # date of birth
    cd, ld = cf.get('Date of birth'), lf.get('Date of birth')
    if not na(cd) and not na(ld):
        ci = parse_date(cd)
        lds = [x.strip() for x in ld.split(';') if x.strip()]
        lis = [parse_date(x) for x in lds]
        if ci and all(lis):
            rel['date_of_birth'] = ('agree' if ci in lis else 'conflict', line(cust, 'Date of birth'), line(lst, 'Date of birth'))
        elif ci and all(re.fullmatch(r'\d{4}', x) for x in lds):
            rel['year_of_birth'] = ('agree' if ci[:4] in lds else 'conflict', line(cust, 'Date of birth'), line(lst, 'Date of birth'))
    for attr, ck, lk in (('nationality', 'Nationality', 'Nationality'),
                         ('country_of_registration', 'Country of incorporation', 'Country of registration'),
                         ('city', 'Registered office city', 'Address cities')):
        cv, lv = cf.get(ck), lf.get(lk)
        if not na(cv) and not na(lv):
            lvs = {strip_accents(x).strip().lower() for x in re.split(r'[;|]', lv)}
            rel[attr] = ('agree' if strip_accents(cv).strip().lower() in lvs else 'conflict', line(cust, ck), line(lst, lk))
    cp, lp = cf.get('Place of birth'), lf.get('Place of birth')
    if not na(cp) and not na(lp):
        rel['place_of_birth'] = ('agree' if strip_accents(cp).lower().split(',')[-1].strip() == strip_accents(lp).lower().split(',')[-1].strip() else 'conflict',
                                 line(cust, 'Place of birth'), line(lst, 'Place of birth'))
    for attr, ck, lk in (('passport_number', 'Passport number', 'Passport'),
                         ('national_id', 'National ID number', 'National ID'),
                         ('registration_number', 'Registration number', 'Registration number')):
        cv, lv = cf.get(ck), lf.get(lk)
        if not na(cv) and not na(lv):
            cnums = {n for n, _ in split_doc_numbers(cv)}
            lnums = {n for n, _ in split_doc_numbers(lv)}
            rel[attr] = ('agree' if cnums & lnums else 'conflict', line(cust, ck), line(lst, lk))
    return name_match, rel


def has_free_text(packet):
    return any(d['kind'] == 'free_text' for d in packet['documents'])


def render_packet(packet):
    out = []
    for d in packet['documents']:
        out.append(f"<document id=\"{d['id']}\" side=\"{d['side']}\" title=\"{d['title']}\">\n{d['text']}\n</document>")
    return '\n\n'.join(out)


def jdump(obj, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + '\n')


def jsonl_write(rows, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')


def jsonl_read(path):
    return [json.loads(l) for l in open(path) if l.strip()]
