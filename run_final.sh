#!/bin/bash
# The final run, in the order it was executed. Code, prompts, routing and scoring are frozen at the commit recorded in each manifest.
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
