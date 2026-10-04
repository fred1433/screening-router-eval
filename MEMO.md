# Memo: what the run says about routing, fine-tuning and a pilot

Written after the final run (manifest: `results/runs/test/manifest.json`). Figures come from `results/runs/test/summary.json`
and `results/runs/test/first_answer_only.json`.

## What the run shows

- With the same evidence, output contract, validators and one repair call, the small model (qwen/qwen3.5-9b) and the large
  model (qwen/qwen3.5-397b-a17b) each produced 177 of 240 correct and supported runs and 3 needless stops. Neither
  recommended excluding a true match (0 of 153 runs, 95% Wilson interval 0 to 2.4%: zero observed, not zero risk).
- The router produced 180 of 240, no needless stop, 0.15 large-model calls per case, a resource index of 1.61 against 6.48
  for the large model alone (small alone = 1). Of its 31 escalations, 7 corrected the small model, 24 changed nothing
  material, none introduced an error.
- The validators carried the safety. Before any check the small model's first answer said "different entity" on 15 of 240
  runs where the customer really was the listed person and the identifiers conflicted. The policy check (the disposition must
  follow from the model's own comparisons) rejected all 15. The large model's first answers made no such error.

## Error families that remain, and which a training run could touch

| Family | Seen in | Fix that comes first | Could training help? |
|---|---|---|---|
| Disposition that does not follow from the model's own, correct comparisons | large model, 2 cases (3 runs) ended as needless stops | Let code apply the policy to the extracted comparisons; the model only extracts | No need |
| A field marked "not captured" read as a conflict | small model, case-030 (3 runs), rescued by escalation | One deterministic rule: an attribute absent on one side cannot conflict | No need |
| An attribute read as another (residence as nationality; nationality as place of birth) | small model: 3 stops on original names, 2 cases with invented names | A stricter extraction step that quotes the field label, and a reference-based check for the label | Possibly |
| Judging an unlisted spelling as a transliteration | escalated by design (12 runs), no error left | Keep escalating; add listed-alias lookup in code | Possibly, with reviewed pairs |

## When fine-tuning would be worth testing here

Not yet. After the two code fixes above, the only family left that training might reduce is attribute confusion in free
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
