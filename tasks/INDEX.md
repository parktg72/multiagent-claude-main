# Live Dispatch Records

Index of the real provider runs kept under `tasks/`. Nothing here is tracked by git:
`.gitignore` excludes `tasks/*`, so raw model output, verdicts, and approval journals
stay on this machine only. Keep these records to compare against when a symptom
reappears. Nine of the eleven defects below were found by dispatching for real; the
other two were caught reading a CLI's own help before anything was spent. None of the
eleven was reachable from the mock suite.

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
  `claude-haiku-4-5` call beside the pinned model. All four backends report what a run
  spent in total, but only the Claude CLI breaks it down per model — and in that run the
  top-level total excluded the auxiliary call rather than folding it in, so on the other
  three such a call leaves no trace even in the figure they do report.
- The opencode `--variant` pin is not enforceable: an invalid variant is accepted
  silently and no event states which variant ran. Open, upstream behaviour; exposure
  became active on 2026-08-06. Affects `kimi-reviewer` (`opencode/kimi-k3`, variant
  `max`) and `deepseek-reviewer` (`opencode/deepseek-v4-pro`, variant `max`) — K3's
  catalog defines only `max`, so the gap was latent until `deepseek-reviewer` was
  pinned to a model whose catalog also offers `high`. Recorded in
  `kimi-live-test/task.md` and `ISSUES.md`.

## Limits since closed

- **`codex-sol`'s live observation rested on the exit code alone** — closed 2026-08-06,
  filed and closed the same day as issue #3. It is the one worker with
  `requires_no_yes_man: false`, so no verdict contract stood between a zero-exit run and
  `.runtime/live/codex-sol.json`, and defect 11 proved a backend can exit zero having
  answered nothing. `finish_dispatch` now reads the count the backend reports — codex on
  stderr, opencode in its JSONL stream, agy and the Claude CLI in a `usage` object in
  their stdout JSON — and withholds the observation on a reported zero. Replayed against
  every run in this index it flags run 20260806T042330 and nothing else. Five runs report
  no count at all; each is a run some other gate had already failed. Four of those five
  exited nonzero and never reached the check.

  The fifth exited *zero* with no parseable count: the defect-4 agy run, whose output was
  prose rather than JSON because `--print` had swallowed `--output-format json`. A
  reviewer's verdict contract caught that one, but the same shape on a worker without one
  would have been credited, because an absent count reads as unknown rather than as zero.
  Closed the same day by refusing an unreadable count where no verdict contract stands
  behind the run — keyed on the contract, not on a role name, so `codex-sol` is covered
  today and any future non-reviewer inherits it. A reviewer keeps the old behaviour,
  since refusing there would strand a backend that stops reporting. Replayed against
  every run in this index, accounting for the four that exit nonzero and never reach the
  check, the new rule reclassifies nothing.

## Pipeline run

`pipeline-demo` is the only full topology run: producer, three independent reviewers on
byte-identical packets, escalation to the advisor, a second producer round, and a
verification review. Two of the findings in it were defects in main's own evidence —
a truncated test log and a diff spanning two rounds — each caught by a reviewer and
each a correct rejection. See its `log.md` for the append-only trail.

## Re-verification run

`zen-repin-live` is the second multi-reviewer run and the reason there is an eleventh
defect. Both opencode workers had been repinned to provider `opencode`, which changes
`pin_digest` and so invalidates the observation recorded against the old pin — mock
tests and preflight were green and proved nothing about the new one. Two reviewers
received a byte-identical packet and returned approve and conditional; both
independently reported that the pin-format and variant guarantees live in different
functions from the dispatch path.

Re-testing those claims, per rule 10, confirmed two and refuted one, and the subsequent
whole-branch review refuted a third that main had made about main's own code. Its
`log.md` carries the correction as an appended entry rather than an edit, which is what
the append-only rule is for.

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
