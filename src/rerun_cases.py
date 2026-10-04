"""Re-run only the cases whose packet text changed after the final run, with the frozen pipeline, and replace their rows
in the run files. Records what was re-run and why in <run_dir>/reruns.json.
Usage: python src/rerun_cases.py results/runs/test "reason" case-066 case-067 ..."""
import datetime, json, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import ROOT, jsonl_read, jsonl_write
import pipeline

run_dir, reason, ids = Path(sys.argv[1]), sys.argv[2], sys.argv[3:]
cases = {c['case_id']: c for c in jsonl_read(ROOT / 'cases' / 'cases.jsonl')}
log = {'date_utc': datetime.datetime.now(datetime.UTC).isoformat(timespec='seconds'), 'reason': reason, 'case_ids': ids,
       'code_commit': subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip(),
       'files': []}
for f in sorted(run_dir.glob('*_r*.jsonl')):
    cfg, rep = f.stem.rsplit('_r', 1)
    rows = jsonl_read(f)
    with ThreadPoolExecutor(6) as ex:
        new = dict(zip(ids, ex.map(lambda i: {**pipeline.run_case(cfg, cases[i], 'A'), 'rep': int(rep)}, ids)))
    rows = [new.get(r['case_id'], r) for r in rows]
    jsonl_write(rows, f)
    log['files'].append(f.name)
    print(f.name, 'replaced', len(new), flush=True)
prev = json.loads((run_dir / 'reruns.json').read_text()) if (run_dir / 'reruns.json').exists() else []
(run_dir / 'reruns.json').write_text(json.dumps(prev + [log], indent=1) + '\n')
