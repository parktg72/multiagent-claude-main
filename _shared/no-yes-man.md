# No-Yes-Man Review Contract

Applies to `codex-terra`, `agy`, `kimi-reviewer`, `fable-advisor`, and every
future validator.

1. Input contains only question, requirements, diff, test evidence, and optional
   relative workspace/artifact paths. Dispatcher rejects `prior_reviewer_conclusion`,
   worker-output/control-plane artifact references, and every unrecognized field.
   Main must not inject its conclusion or another review.
2. Output must be one JSON object matching `schemas/review-verdict.schema.json`.
   `verdict` is exactly `approve`, `reject`, `conditional`, or
   `insufficient_evidence`.
3. Output must separate nonempty `evidence` from `unverified_claims` and contain at
   least one structured risk with `failure_mode`, `trigger`, `impact`,
   `evidence_or_locator`, and `mitigation`. `approve` does not waive evidence/risk.
4. Cosmetic or invented objections are forbidden. If evidence cannot support a
   substantive conclusion, use `insufficient_evidence`.
5. Dispatcher validates directly against JSON schema plus minimum anti-generic checks.
   Invalid output returns status 65. This cannot establish semantic truth.

This is a contract, not a promise that a model is correct. Main compares independent
verdicts after all reviews finish.
