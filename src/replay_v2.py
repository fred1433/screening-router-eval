"""Run v2 on the 80 held-out cases WITHOUT new model calls: the v2 pipeline (src/pipeline.py, policy A2) is executed
for real, but every model call is answered by the answer recorded in the v1 run, in order. When v2 sends an answer back
for a repair that v1 never asked for, no recorded answer exists: the run is marked 'pending_rerun' (zero model spend).
For the router, a large-model step that v1 never made is answered by the large-alone run of the same case and repetition
(flagged 'large_from_large_alone_run'). Rules run for real. Latency is not reconstructed.
Usage: python src/replay_v2.py"""
import datetime, json, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import ROOT, jsonl_read, jsonl_write, jdump
import pipeline

V1 = ROOT / 'results' / 'runs' / 'test'
OUT = ROOT / 'results' / 'runs' / 'v2_replay'
cases = {c['case_id']: c for c in jsonl_read(ROOT / 'cases' / 'cases.jsonl') if c['split'] == 'test'}


def recorded(cfg, rep, step):
    rows = {r['case_id']: r for r in jsonl_read(V1 / f'{cfg}_r{rep}.jsonl')}
    out = {}
    for cid, r in rows.items():
        st = next((s for s in r['trace'] if s['step'] == step), None)
        out[cid] = [] if st is None else [{'raw': a['raw'], 'call': c} for a, c in zip(st.get('attempts', []), st.get('calls', []))]
    return out


def make_caller(queues, flags):
    def caller(tier, msgs, injected_fault=None):
        q = queues.get(tier, [])
        if not q:
            return {'not_rerun': True}
        a = q.pop(0)
        c = a['call']
        return {**{k: c.get(k) for k in ('tier', 'model_requested', 'endpoint_requested', 'model_returned', 'provider_returned',
                                          'generation_id', 'prompt_tokens', 'completion_tokens', 'latency_ms', 'attempt')},
                'ok': True, 'replayed_from_v1': True, 'source': flags.get(tier, 'same configuration'), 'content': a['raw']}
    return caller


commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
OUT.mkdir(parents=True, exist_ok=True)
for cfg in ('deterministic', 'small', 'large', 'router'):
    for rep in (1, 2, 3):
        rec = {}
        if cfg in ('small', 'router'):
            rec['small'] = recorded(cfg, rep, 'small')
        if cfg == 'large':
            rec['large'] = recorded('large', rep, 'large')
        if cfg == 'router':
            rl, la = recorded('router', rep, 'large'), recorded('large', rep, 'large')
        rows = []
        for cid in sorted(cases):
            queues, flags = {}, {}
            if cfg in ('small', 'router'):
                queues['small'] = list(rec['small'][cid])
            if cfg == 'large':
                queues['large'] = list(rec['large'][cid])
            if cfg == 'router':
                if rl[cid]:
                    queues['large'] = list(rl[cid])
                else:
                    queues['large'] = list(la[cid]); flags['large'] = 'large_from_large_alone_run'
            pipeline.CALLER = make_caller(queues, flags)
            r = pipeline.run_case(cfg, cases[cid], 'A2')
            r['rep'] = rep
            r['replay'] = True
            rows.append(r)
        jsonl_write(rows, OUT / f'{cfg}_r{rep}.jsonl')
        print(cfg, rep, {k: sum(1 for x in rows if x['resolution'] == k) for k in {x['resolution'] for x in rows}}, flush=True)
jdump({'created_utc': datetime.datetime.now(datetime.UTC).isoformat(timespec='seconds'), 'code_commit': commit,
       'kind': 'v2 controls replayed on the answers recorded in the v1 run; no model call', 'policy': 'A2',
       'source_run': 'results/runs/test (v1)', 'new_model_calls': 0,
       'pending_rerun_meaning': 'v2 sent an answer back for a repair that v1 never requested; the repair was not run'},
      OUT / 'manifest.json')
