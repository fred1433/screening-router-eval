"""Helpers shared by score.py (v1) and score_v2.py."""
import math
from collections import defaultdict


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



def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    f = math.floor(k)
    return round(xs[f] + (xs[min(f + 1, len(xs) - 1)] - xs[f]) * (k - f))
