# Memo: what the run says about routing, fine-tuning and a pilot

Written after the final run (manifest: `results/runs/test/manifest.json`). Figures come from `results/runs/test/summary.json`
and `results/runs/test/first_answer_only.json`.

## What the run shows

- With the same evidence, output contract, validators and one repair call, the small model (qwen/qwen3.5-9b) produced
  176/180 answerable runs correct and supported and the large model (qwen/qwen3.5-397b-a17b) 178/180; both stopped rightly
  on 60/60 insufficient-evidence runs. Neither recommended excluding a true match: 0 of 51 true-match cases in any of three
  runs, a 95% interval of 0 to 7% computed over cases (runs at temperature 0 are not independent). Zero observed, not zero risk.
- The router produced 180/180 and 60/60, with 0.17 large-model calls per case and a resource index of 1.72 against 6.47
  for the large model alone (small alone = 1). Of its 34 escalations, 6 runs over 2 cases were corrected, 4 small outputs
  that had failed their checks were replaced by valid stops, none introduced an error, the rest changed nothing material.
- The validators carried the safety. Before any check the small model's first answer said "different entity" on 15 of 240
  runs where the customer really was the listed person and the identifiers conflicted. The policy check (the disposition must
  follow from the model's own comparisons) rejected all 15. The large model's first answers made no such error.
- After the run, a stricter citation check (non-trivial quote, containing the value, never a "not captured" line) was
  replayed on every accepted output without new calls: it would have rejected 9 of 235 small-model outputs, 3 of 238
  large-model outputs and 3 of 240 router outputs, all for citing a line that records an absent value.

## After v2 (controls written after reading v1)

Replayed on the recorded answers without new model calls (`results/runs/v2_replay/summary_v2.json`), the v2 controls leave
the router at 180/180 answerable runs correct and supported and send 23 runs, across all configurations,
back for a repair call that was not made. The router alone did not need the large model's correction on most cases; stopping
it before the large-model call gives 174/180 in v1 at a resource index of 0.62 (small alone = 1), against
180/180 and 1.72 for the full router. The 31 new cases generated after the v2 freeze are the
unseen test; only the rules have run on them so far. Whether the router is worth its large-model step is the question that
model run would answer.

## Error families that remain, and which a training run could touch

| Family | Seen in | Fix that comes first | Could training help? |
|---|---|---|---|
| Disposition that does not follow from the model's own, correct comparisons | large model, 2 runs (2 cases) ended as needless stops | Let code apply the policy to the extracted comparisons; the model only extracts | No need |
| A field marked "not captured" read as a conflict | small and large first answers on case-030 (3 runs each); the large model fixed it on repair, the small did not; rescued by escalation | One deterministic rule: an attribute absent on one side cannot conflict | No need |
| An attribute read as another (residence as nationality; nationality as place of birth) | small model: 3 stops citing "Nationality: not captured" on original names, 2 cases with invented names | A stricter extraction step that quotes the field label, and a reference-based check for the label | Possibly |
| Judging an unlisted spelling as a transliteration | escalated by design (12 runs), no error left | Keep escalating; add listed-alias lookup in code | Possibly, with reviewed pairs |

## When fine-tuning would be worth testing here

Not yet. After the two code fixes above and the stricter citation check, the only family left that training might reduce is attribute confusion in free
text. A training run would need a reviewed set of comparison records (customer field, candidate field, relation,
verbatim citation), drawn from the institution's own document types and reviewed by an analyst, never list facts baked into
weights (lists change; the evidence packet must stay the source). The deciding experiment: the same small model with and
without a LoRA adapter, scored on a held-out split by family with the same validators, comparing unsupported agreements and
needless stops, with the large model's rate on the same split as the bar to beat.

## What a pilot would be

One workflow, two weeks from the day the workflow and access are agreed: map the current system, measure a baseline on
agreed cases, move policy application and absent-field rules into code, set the escalation reasons, freeze a reference set
with a second reviewer, then run small, large and routed configurations against it inside the client's environment. The
pilot's acceptance criteria are written before the run, in the order used here: wrong exclusions first.
