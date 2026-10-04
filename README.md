# Open-model routing for screening recommendations

A measured comparison, on one compliance task, of four ways to prepare an identity recommendation for a sanctions-screening
analyst: rules only, a small open-weight model alone, a large open-weight model alone, and a router that uses rules, then
the small model, then the large model only for observable reasons.

Results page: https://theaipipe.com/demos/screening-router/ (generated from the files in `results/`).
One-page memo on fine-tuning and the pilot: [MEMO.md](MEMO.md).

## The question

Can a smaller open-weight model prepare a defensible screening recommendation, and when does calling a larger model
actually improve it, while preserving analyst sign-off?

## The task

Input: a customer file (synthetic), one watchlist candidate already selected upstream (a real OFAC SDN entry, copied field by
field, UID and snapshot date kept), and an illustrative matching policy (`policy/policy_a.md`). Output: a disposition
(same entity supported / different entity supported / insufficient evidence), comparisons with verbatim citations, missing
information, outstanding work, and a status (ready for analyst sign-off / further investigation). Analyst approval is a separate
human step that is never automated or scored. Out of scope: candidate search, adverse media, risk scoring, PEP screening.

## Cases and references

`cases/cases.jsonl`: 100 cases in 10 families (exact match, listed alias, transliteration, homonym, name only, contradictory
identifiers, relative's date of birth, year of birth only, entity match, entity homonym). 20 development cases, 80 held out
for the final score. Each reference gives the identity used to build the case and the disposition the evidence permits, with
decisive agreements, conflicts, missing information and a justification. OFAC snapshot: `data/snapshot.json` (export
published 2026-10-02, retrieved 2026-10-03, SHA-256 of each file). The raw CSV files are not committed; download them from the
URL in the snapshot file and check the hashes to rebuild the cases with `src/build_cases.py`.

References were written by the builder and checked twice by a fresh Claude Opus session with no tools and no memory of the
build (`src/review_refs.py`). Round 1: 14 disagreements, all on incomplete missing-information lists; the references were
corrected. Round 2: no disagreement (`review/`). This is a model review, not a review by a compliance expert.

## Configurations and controls

`src/pipeline.py`. All configurations share the packet, the prompt (`prompts/disposition_system.md`), the output contract, the
validators and a budget of one repair call per model. Deterministic validators: schema, citation from a document in the packet
and on the right side, quote found verbatim, disposition recomputed from the model's own comparisons under the policy, and
agreement with what code reads from the two structured records. The router escalates on observable reasons only (output still
failing checks after repair, a match resting on an unlisted spelling, agreeing and conflicting identifiers together); a stop
for missing evidence never calls the large model; an endpoint failure is recorded as `unresolved: endpoint failed`, never as
insufficient evidence.

## Run

`run_final.sh`, in order: the 80 held-out cases, 3 runs per configuration (`results/runs/test/`), scoring (`src/score.py`),
the fault suite and the two-policy demonstration (`src/faults.py`, `results/faults/`), and the name-sensitivity test on 43
true-match cases with invented names (`src/rename_test.py`, `results/runs/renamed/`). `src/first_answer.py` re-scores the
first answers without any check (no new call). `src/build_page.py` builds the page from these files.

Endpoints, pinned through OpenRouter with `allow_fallbacks: false` and `require_parameters: true`: `qwen/qwen3.5-9b` on
`parasail/bf16`, `qwen/qwen3.5-397b-a17b` on `deepinfra/fp8`; temperature 0, reasoning off, JSON output, concurrency 6, one
transport retry. Model identifiers are aliases the host can update. Latency is client wall-clock, network included. The
endpoint was chosen on development data only (`results/endpoint_probe_dev.json`). Model names and endpoints are environment
variables (`SMALL_MODEL`, `SMALL_PROVIDER`, `LARGE_MODEL`, `LARGE_PROVIDER`, `LLM_BASE_URL`), so the harness can point at any
OpenAI-compatible endpoint, including a self-hosted one; only the hosted endpoints above were tested.

No money amount is published. `results/price_index.json` holds relative token weights only.

## Results (held-out set, 240 runs per configuration)

| | Rules only | Small alone | Large alone | Router |
|---|---|---|---|---|
| Recommended excluding a true match (runs; cases) | 0/153; 0/51 | 0/153; 0/51 | 0/153; 0/51 | 0/153; 0/51 |
| Decided on insufficient evidence | 0/60 | 0/60 | 0/60 | 0/60 |
| Matched a different entity | 0/87 | 0/87 | 0/87 | 0/87 |
| Correct and supported, answerable | 60/180 | 176/180 | 178/180 | 180/180 |
| Stopped rightly | 60/60 (by default) | 60/60 | 60/60 | 60/60 |
| Stopped needlessly | 120/180 | 4/180 | 2/180 | 0/180 |
| Large-model calls per case | 0 | 0 | 1.07 | 0.17 |
| Latency median / p95 | under 0.1 s | 6.4 / 13.4 s | 13.4 / 25.3 s | 4.8 / 24.9 s |
| Resource index (small = 1) | 0 | 1.0 | 6.47 | 1.72 |

Intervals are computed over cases, not runs (`ci95_cases` in the summary): 0 of 51 true-match cases gives 0 to 7%. The
router's median is low because rules settle 93 of 240 runs; over runs that call a model it is 6.4 s.

Re-run after the final run: the relative-trap notes always said "elder", even for a relative born after the customer.
The generator was fixed (one word in 6 packets, references unchanged) and the 5 held-out cases it touched were re-run with
the frozen pipeline (`results/runs/test/reruns.json`). The two-policy demonstration was moved to an individual
(`src/two_policies.py`). A stricter citation check was added to `src/pipeline.py` after the run and replayed on the accepted
outputs without new calls (`src/replay_citations.py`, `results/runs/test/citation_replay.json`); the scored run used the
earlier checks.

Scoring limit: on a decided case the score checks the disposition, the conflicts covered and the agreements claimed; it does not penalise an incomplete missing-information list.

Full figures, intervals and stability: `results/runs/test/summary.json`.

## Layout

`site/` is the published page, `site_src/` its template and case notes, `worker.js` and `wrangler.toml` serve it.
