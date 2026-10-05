#!/bin/bash
# Rebuild every number on the page from the recorded traces. No network, no key, no model call.
set -e
cd "$(dirname "$0")"
mkdir -p .private
python3 src/score.py results/runs/test > /dev/null
python3 src/score.py results/runs/renamed > /dev/null
python3 src/first_answer.py results/runs/test > /dev/null
python3 src/replay_citations.py results/runs/test > /dev/null
python3 src/score_v2.py results/runs/test --policy A --out results/runs/test/summary_v1_dimensions.json > /dev/null
python3 src/replay_v2.py > /dev/null
python3 src/score_v2.py results/runs/v2_replay > /dev/null
python3 src/ablation.py results/runs/test results/runs/ablation_v1 > /dev/null
python3 src/score.py results/runs/ablation_v1 > /dev/null
python3 src/score_v2.py results/runs/ablation_v1 --policy A --out results/runs/ablation_v1/summary_v1_dimensions.json > /dev/null
python3 src/ablation.py results/runs/v2_replay results/runs/ablation_v2 > /dev/null
python3 src/score_v2.py results/runs/ablation_v2 > /dev/null
python3 src/run_eval.py --cases cases/cases_v2_new.jsonl --split v2_new --reps 1 --configs deterministic --policy A2 --out results/runs/v2_new > /dev/null
python3 src/score_v2.py results/runs/v2_new --cases cases/cases_v2_new.jsonl > /dev/null
python3 tests/test_v2.py > /dev/null
python3 src/build_page.py
