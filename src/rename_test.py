"""Name-sensitivity test: every name token of the customer, the candidate and the aliases is replaced consistently
by an invented token, in every document of the packet. Relationships under test are kept (same name stays the same,
alias stays an alias). The transliteration family is left out because invented tokens would not preserve the
spelling relationship. A change in results may come from prior knowledge of listed names OR from the new names
being linguistically different; this test does not tell which.
Usage: python src/rename_test.py  (writes cases/cases_renamed.jsonl)"""
import copy, random, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import ROOT, jsonl_read, jsonl_write

rng = random.Random(41)
ONS = ['b', 'd', 'f', 'g', 'k', 'l', 'm', 'n', 'p', 'r', 's', 't', 'v', 'z', 'br', 'dr', 'kl', 'st', 'tr']
VOW = ['a', 'e', 'i', 'o', 'u', 'ai', 'ou']
used = set()


def invent():
    while True:
        w = ''.join(rng.choice(ONS) + rng.choice(VOW) for _ in range(rng.choice([2, 3]))) + rng.choice(['n', 'r', 's', 'k', ''])
        if w not in used:
            used.add(w); return w


STOP = {'al', 'el', 'bin', 'ibn', 'de', 'la', 'del', 'llc', 'ltd', 'limited', 'company', 'co', 'jsc', 'ooo', 'oao', 'pjsc',
        'zao', 'the', 'and', 'of', 'inc', 'plc', 'sa', 'gmbh', 'fze', 'fzc', 'liability'}
out = []
for c in jsonl_read(ROOT / 'cases' / 'cases.jsonl'):
    if c['split'] != 'test' or c['reference']['construction_identity'] != 'same' or c['family'] == 'transliteration':
        continue
    c = copy.deepcopy(c)
    L = c['packet']['documents'][-1]['text']
    names = [re.search(r'^Primary name: (.+)$', L, re.M)[1]]
    al = re.search(r'^Aliases: (.+)$', L, re.M)[1]
    if al != 'none listed':
        names += al.split(' | ')
    toks = sorted({t for n in names for t in re.findall(r"[A-Za-z][A-Za-z']+", n) if t.lower() not in STOP}, key=len, reverse=True)
    mp = {}
    for t in toks:
        mp.setdefault(t.lower(), invent())
    def sub(text):
        def rep(m):
            w = m[0]; n = mp.get(w.lower())
            if not n:
                return w
            return n.upper() if w.isupper() else n.capitalize() if w[0].isupper() else n
        return re.sub(r"[A-Za-z][A-Za-z']+", rep, text)
    for d in c['packet']['documents']:
        d['text'] = '\n'.join(l if l.startswith('Source:') else sub(l) for l in d['text'].splitlines())
    c['case_id'] += '-renamed'
    c['split'] = 'renamed'
    out.append(c)
jsonl_write(out, ROOT / 'cases' / 'cases_renamed.jsonl')
print(len(out), 'renamed cases')
