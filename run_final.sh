#!/bin/bash
# A new experiment (network, OPENROUTER_API_KEY). This is the v1 sequence; run today it uses the v2 controls in src/pipeline.py.
# To rebuild the published numbers from the recorded traces without network, use ./recompute.sh instead.
set -e
cd "$(dirname "$0")"
mkdir -p .private
: "${OPENROUTER_API_KEY:?set OPENROUTER_API_KEY to run a new experiment; recompute.sh needs no key}"
export PRIVATE_LEDGER="$PWD/.private/ledger.jsonl"
python3 src/run_eval.py --policy A --split test --reps 3 --configs deterministic,small,large,router --out results/runs/test
python3 src/score.py results/runs/test
python3 src/faults.py
python3 src/two_policies.py
python3 src/rename_test.py
python3 src/run_eval.py --policy A --cases cases/cases_renamed.jsonl --split renamed --reps 1 --configs small,large,router --out results/runs/renamed
python3 src/score.py results/runs/renamed
