# Live Dispatch Records

Index of the real provider runs kept under `tasks/`. Nothing here is tracked by git:
`.gitignore` excludes `tasks/*`, so raw model output, verdicts, and approval journals
stay on this machine only. Keep these records to compare against when a symptom
reappears — every defect below was found by dispatching for real, and none of them
was reachable from the mock suite.

## Defects found, and where the evidence lives

| # | Backend | Defect | Evidence |
|---|---|---|---|
| 1 | `codex-sol` | auth mount was read-only; codex initializes an app-server in CODEX_HOME and died with EROFS | `sol-live-test` run 20260804T003518 |
| 2 | `codex-sol` | codex ran its own nested sandbox inside ours and could not create dot dirs at a read-only workspace root | `sol-live-test` runs 20260804T005939, 20260804T010214 |
| 3 | `codex-terra` | provider strict structured output rejected a schema whose `required` omitted a declared property | `terra-live-test` run 20260804T022600 |
| 4 | `agy` | **`--print` swallowed `--model`; every later flag was dropped and an unpinned model answered with exit 0** | `agy-live-test` run 20260804T051317 |
| 5 | `agy` | credential path was unsupported; only `antigravity-oauth-token` is needed | (diagnosed before dispatch) |
| 6 | `kimi-reviewer` | `--file` is an array option and consumed the trailing message as a second attachment | `kimi-live-test` run 20260804T075406 |
| 7 | `kimi-reviewer` | verdict arrives inside a JSONL event stream at `part.text`; the extractor read only bare objects | `kimi-live-test` run 20260804T081344 |
| 8 | `kimi-reviewer` | this CLI cannot enforce a schema, so the model added an undeclared key | `kimi-live-test` run 20260804T081344 |
| 9 | `fable-advisor` | `--bare` reads neither OAuth nor keychain, so this account could never authenticate — also broke `bin/claude-main` | (diagnosed before dispatch) |
| 10 | `fable-advisor` | inlined schema was rejected because its meta-schema URL could not be resolved | `fable-live-test` run 20260805T001938 |
| 11 | `kimi-reviewer` | **a failed request arrived as a successful step: exit 0, `reason: "unknown"`, zero input and output tokens, cost 0, after 63s with no text part.** Only the verdict contract caught it; an identical re-dispatch succeeded, so it is transient and unannounced | `zen-repin-live` run 20260806T042330 |

## Unresolved limits

- A model pin is not exclusive. The Claude CLI reported one small internal
  `claude-haiku-4-5` call beside the pinned model; other backends do not report
  per-model usage at all, so the same may happen there unobserved.
- The opencode `--variant` pin is not enforceable: an invalid variant is accepted
  silently and no event states which variant ran. Open, upstream behaviour; exposure
  became active on 2026-08-06. Affects `kimi-reviewer` (`opencode/kimi-k3`, variant
  `max`) and `deepseek-reviewer` (`opencode/deepseek-v4-pro`, variant `max`) — K3's
  catalog defines only `max`, so the gap was latent until `deepseek-reviewer` was
  pinned to a model whose catalog also offers `high`. Recorded in
  `kimi-live-test/task.md` and `ISSUES.md`.
- `codex-sol`'s live observation rests on the exit code alone. It is the one worker
  with `requires_no_yes_man: false`, so no verdict contract stands between a zero-exit
  run and `.runtime/live/codex-sol.json`. Defect 11 above proves a backend can exit
  zero having answered nothing; that was opencode, not codex, and the gap is that the
  harness could not tell either way. codex reports `tokens used` on stderr, which the
  dispatcher already saves, so the fix is available where the gap is. Recorded in
  `ISSUES.md`.

## Pipeline run

`pipeline-demo` is the only full topology run: producer, three independent reviewers on
byte-identical packets, escalation to the advisor, a second producer round, and a
verification review. Two of the findings in it were defects in main's own evidence —
a truncated test log and a diff spanning two rounds — each caught by a reviewer and
each a correct rejection. See its `log.md` for the append-only trail.
## All runs

| Task | Role | Run | Outcome |
|---|---|---|---|
| agy-live-test | `agy` | 20260804T051317 | **fail** — unpinned model answered about the --model flag |
| agy-live-test | `agy` | 20260804T072435 | **ok** — verdict conditional |
| fable-live-test | `fable-advisor` | 20260805T001938 | **fail** — meta-schema URL could not be resolved |
| fable-live-test | `fable-advisor` | 20260805T002110 | **ok** — verdict approve |
| kimi-live-test | `kimi-reviewer` | 20260804T075406 | **fail** — --file consumed the message as a path |
| kimi-live-test | `kimi-reviewer` | 20260804T081344 | **ok** — completed |
| kimi-live-test | `kimi-reviewer` | 20260804T081931 | **ok** — verdict approve |
| pipeline-demo | `agy` | 20260805T004243 | **ok** — verdict approve |
| pipeline-demo | `codex-sol` | 20260805T003730 | **ok** — producer edit applied |
| pipeline-demo | `codex-sol` | 20260805T012828 | **ok** — producer edit applied |
| pipeline-demo | `codex-terra` | 20260805T004136 | **ok** — verdict conditional |
| pipeline-demo | `codex-terra` | 20260805T013504 | **ok** — verdict reject |
| pipeline-demo | `codex-terra` | 20260805T013722 | **ok** — verdict approve |
| pipeline-demo | `fable-advisor` | 20260805T005226 | **ok** — verdict conditional |
| pipeline-demo | `kimi-reviewer` | 20260805T004303 | **ok** — verdict approve |
| sol-live-test | `codex-sol` | 20260804T003518 | **fail** — EROFS: read-only CODEX_HOME |
| sol-live-test | `codex-sol` | 20260804T005939 | **fail** — nested sandbox could not mkdir /workspace/.git |
| sol-live-test | `codex-sol` | 20260804T010214 | **fail** — nested sandbox could not mkdir /workspace/.agents |
| sol-live-test | `codex-sol` | 20260804T011215 | **ok** — producer edit applied |
| sol-live-test | `codex-sol` | 20260804T014718 | **ok** — producer edit applied |
| terra-live-test | `codex-terra` | 20260804T022600 | **fail** — strict schema rejected: missing 'summary' in required |
| terra-live-test | `codex-terra` | 20260804T042438 | **ok** — verdict conditional |
| zen-repin-live | `deepseek-reviewer` | 20260806T043336 | **ok** — verdict conditional |
| zen-repin-live | `kimi-reviewer` | 20260806T042330 | **fail** — exit 0, reason unknown, zero input and output tokens, cost 0, no text part after 63s |
| zen-repin-live | `kimi-reviewer` | 20260806T043516 | **ok** — verdict approve |
