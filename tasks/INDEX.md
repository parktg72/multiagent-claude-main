# Live Dispatch Records

Index of the real provider runs kept under `tasks/`. Nothing here is tracked by git:
`.gitignore` excludes `tasks/*`, so raw model output, verdicts, and approval journals
stay on this machine only. Keep these records to compare against when a symptom
reappears. Nine of the twelve defects below were found by dispatching for real; two were
caught reading a CLI's own help and one by a preflight that refused to spend, all three
before anything was spent. None of the twelve was reachable from the mock suite.

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
| 12 | `agy` | **`exact_catalog_line` requires a bare full-line match and `agy models` emits `<id>\t<display name>`, so the pin can never verify.** Measured 2026-08-07: the probe returns in ~5s, exit 0, 12 lines, and the pinned line is `'gemini-3.1-pro-high\tGemini 3.1 Pro (High)'`. `line.strip() == model` is False; splitting on the tab gives the id. Preflight therefore reports `unavailable_fail_closed` with detail "exact AGY model is absent from catalog" while the model is present, dispatch is refused, and `test_local_agy_catalog_probe_retains_home_without_exposing_it_to_worker` fails rather than skipping — its skip path covers a probe that did not answer, and this one answers | `live-restore-review`, no run folder — refused at preflight. Fixed under `agy-catalog-match` run 20260807T145505; `agy` dispatched successfully afterwards |

## Unresolved limits

- A model pin is not exclusive. The Claude CLI reported one small internal
  `claude-haiku-4-5` call beside the pinned model. All four backends report what a run
  spent in total, but only the Claude CLI breaks it down per model — and in that run the
  top-level total excluded the auxiliary call rather than folding it in, so on the other
  three such a call leaves no trace even in the figure they do report.
- The AGY catalog probe is intermittent, so preflight can fail closed at random.
  Separate from defect 12 and not fixed by it. Observed 2026-08-07 to 2026-08-08, after
  the matcher fix and after `agy` had dispatched successfully: one `bin/worker preflight`
  returned `unavailable_fail_closed` with "exact AGY model is absent from catalog", while
  five consecutive runs minutes later all returned `catalog_verified`. One `tests/run.sh`
  took 93s and skipped the live catalog test because the probe did not answer; the next
  took 8s and did not skip. Then five consecutive preflight runs all failed, and minutes
  later three in a row passed, same binary and same commit. Separately, `agy models` with
  stdout redirected to a regular file produced zero bytes at 240s and 300s, while the same
  command through a subprocess pipe returned in about 5s.

  Two explanations were proposed and both are refuted by measurement. A difference
  between the CLI's preflight path and a direct call: `backend_preflight("agy", ...)`
  called directly returned `available_pending_auth` while the CLI reported failure, but
  the CLI then passed three times unchanged, so the two paths do not differ. Rate
  limiting from rapid repeats: twelve consecutive probes with no pause returned the pinned
  model twelve times. **The cause is not known**, and the failures have not been
  reproduced on demand. The
  failure direction is safe: an empty catalog reads as an absent pin and refuses to
  spend. The cost is that a green preflight is not reproducible, so "agy is available"
  is a statement about one run and not about the pin. Found because three reviewers
  refused to approve a packet whose preflight section contradicted its other evidence.
- The opencode `--variant` pin is not enforceable: an invalid variant is accepted
  silently and no event states which variant ran. Open, upstream behaviour; exposure
  became active on 2026-08-06. Affects `deepseek-reviewer` (`opencode/deepseek-v4-flash`,
  variant `max`), the pool's only opencode worker since `kimi-reviewer` was replaced by
  `codex-luna` that same day. K3's catalog defined only `max`, so the gap was latent
  until `deepseek-reviewer` was pinned to a model whose catalog offers alternatives —
  V4 Pro offers `high`, and V4 Flash, the pin it was moved to hours later, offers both
  `low` and `high`. Recorded in `kimi-live-test/task.md` and `ISSUES.md`.

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

## What the third multi-reviewer run caught

`live-restore-review` restored five pins on a fresh checkout and, in the same round,
found a false statement main had published. The reviewed change was commit `d095632`,
main's own hand edit of `ISSUES.md` and this file. Five reviewers received a
byte-identical packet, ran independently, and all five rejected on the same sentence:
`kimi-k3 is absent from this provider entirely`, contradicted by the catalog objects
supplied in the same packet, which carry `kimi-k3` under `providerID` `opencode`.
Re-measuring per rule 10 confirmed the reviewers and refuted main.

Four ran together; `agy` ran last, after defect 12 had been fixed, and reached the same
verdict on the same evidence without having seen any of the other four. Nothing about the
correction changed as a result — it was already made and published — but it rests on five
independent readings rather than four.

The sentence claimed to be read off a command and was carried over from `CHANGELOG.md`
prose instead; main's own extraction at the time had printed the model as present and was
misread. This is rule 10 and rule 11 failing together in main's work rather than a
worker's, which is the case the review round exists to catch and the first time it has.

`deepseek-reviewer` alone found a second, smaller error the other four missed — the
changed text said both fenced transcripts name `kimi-k3` when only the second does. All
five separated the packet's unsupported claims into `unverified_claims` rather than
objecting to them, correctly: main cited `CHANGELOG.md` as evidence and did not include
it, a packet defect under rule 9.

## All runs

| Task | Role | Run | Outcome |
|---|---|---|---|
| agy-live-test | `agy` | 20260804T051317 | **fail** — unpinned model answered about the --model flag |
| agy-live-test | `agy` | 20260804T072435 | **ok** — verdict conditional |
| fable-live-test | `fable-advisor` | 20260805T001938 | **fail** — meta-schema URL could not be resolved |
| fable-live-test | `fable-advisor` | 20260805T002110 | **ok** — verdict approve |
| kimi-live-test | `kimi-reviewer` | 20260804T075406 | **fail** — --file consumed the message as a path |
| agy-catalog-match | `codex-sol` | 20260807T145505 | **ok** — producer edit applied |
| agy-catalog-review | `codex-terra` | 20260807T152141 | **ok** — verdict conditional |
| agy-catalog-review | `codex-luna` | 20260807T152517 | **ok** — verdict conditional |
| agy-catalog-review | `fable-advisor` | 20260807T152828 | **ok** — verdict conditional |
| live-restore-review | `codex-terra` | 20260807T140457 | **ok** — verdict reject |
| live-restore-review | `agy` | 20260807T150118 | **ok** — verdict reject, after defect 12 was fixed |
| live-restore-review | `codex-luna` | 20260807T141016 | **ok** — verdict reject |
| live-restore-review | `deepseek-reviewer` | 20260807T141147 | **ok** — verdict conditional |
| live-restore-review | `fable-advisor` | 20260807T141317 | **ok** — verdict reject |
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
