# Open Issues

The sections below are filed upstream as
[#1](https://github.com/parktg72/multiagent-claude-main/issues/1),
[#2](https://github.com/parktg72/multiagent-claude-main/issues/2), and
[#14](https://github.com/parktg72/multiagent-claude-main/issues/14). This file holds the
long form, including the evidence paths, which stay on the machine that ran the
dispatches.

Known gaps between what the harness pins and what it can prove. All three were found by
dispatching for real between 2026-08-04 and 2026-08-08, and all three are unresolved
rather than mitigated. None blocks use; each limits what a claim about a run may say.

Other issues in the tracker are not mirrored here because they were closed, or because
they are defects with a fix rather than gaps between a pin and its evidence: #3
(`codex-sol`'s live observation rested on the exit code alone, fixed in
`finish_dispatch`, which now withholds the observation on a reported zero), #4 (a
signed-out `agy` failing the catalog test instead of skipping it), #6 and #7 (the
opencode repin), and the open #5 and #10. `tasks/INDEX.md` is the index of what was found
by dispatching; this file is only the subset that stays open as an unprovable claim.

---

## 1. A model pin is not exclusive

**Status:** open, no known fix at the harness layer
**Affects:** all backends; only observable on `fable-advisor`
**Evidence:** `tasks/fable-live-test/`, `tasks/pipeline-demo/workers/fable-advisor/`

The dispatcher pins one model per worker and the provider enforces that pin for the
inference it is asked to perform. It does not follow that the pinned model is the only
model a run touches. A live `fable-advisor` dispatch reported:

```
claude-fable-5:            in=2      out=3262   $0.2948   <- the review
claude-haiku-4-5-20251001: in=1005   out=15     $0.0011   <- CLI-internal call
```

The auxiliary call is small and appears to be CLI housekeeping, but the harness neither
requested nor authorized it. The worse part is the blind spot, and it is narrower than
"they report nothing" — measured 2026-08-06, all four backends report what a run spent:
codex prints a count on stderr, opencode carries per-step counts in its JSONL, and agy
and the Claude CLI each put a `usage` object in their stdout JSON. Only the Claude CLI
breaks that down **per model**, which is the sole reason the call above was visible.

Worse still, the breakdown is not merely a convenience. In that same run the top-level
`usage` reported `output_tokens: 7676`, while the per-model figures summed to 7691 — the
auxiliary call is excluded from the total, not folded into it. So on a backend that
reports only a total, an equivalent internal call would leave no trace even in the number
it does report.

**Why it matters.** An approval authorizes a worker whose identity is stated as an exact
model. If a second model participates, the approval covered something narrower than what
ran. For a review verdict this is minor; for a change of policy or a security judgement
the provenance claim is weaker than the record implies.

**What would resolve it.** Any of: a provider flag that forbids auxiliary model calls; a
**per-model** usage breakdown from every CLI, not just a per-run total, so the dispatcher
can assert a single model participated and fail closed otherwise; or an explicit
statement in the approval record that auxiliary calls are permitted, which at least makes
the gap deliberate.

The middle option is closer than it was. `reported_token_total` already reads all four
backends' per-run figures for a different purpose — withholding a live observation when a
run spent nothing. What it cannot do is attribute those figures, and on three of the four
there is nothing to attribute them from.

**Interim rule.** Say the pinned model performed the reviewed work. Do not say it was the
only model involved.

---

## 2. The opencode `--variant` pin is not enforceable

**Status:** open, upstream behaviour; exposure became active on 2026-08-06 and widened
the same day
**Affects:** `deepseek-reviewer` (`opencode/deepseek-v4-flash`, variant `max`) — since
2026-08-06 the pool's only opencode worker
**Evidence:** `tasks/kimi-live-test/task.md` records the original probe under Known
Limitation; the catalog reading that activated it is in
`docs/superpowers/specs/2026-08-06-opencode-zen-reviewer-split-design.md`, and the repin
that widened it is in `CHANGELOG.md` under "`deepseek-reviewer` moved to V4 Flash"

Both transcripts below name provider `opencode-go`; the second also names model
`kimi-k3`, the pin the reviewer carried when they were captured, on a role that has since
left the pool. The first names a deliberately invalid model id and no real pin at all.
They are records of commands that really ran; leave those tokens alone rather than
updating them to the current `opencode/deepseek-v4-flash` pin.

The model half of the pin is enforced: an unknown model id is rejected server-side.

```
opencode --pure run --model opencode-go/definitely-not-real --variant max ...
  -> {"type":"error", ... "Unexpected server error"}
```

The variant half is not. An obviously invalid variant is accepted and the run proceeds
normally:

```
opencode --pure run --model opencode-go/kimi-k3 --variant definitely-not-a-variant ...
  -> {"type":"step_start"} {"type":"text"} ...
```

No event in the JSONL stream names the variant in effect, so the dispatcher cannot
observe which reasoning effort actually ran, nor detect a silent downgrade.

**Why it matters now.** It stopped being latent, and then widened. When this was filed,
the only pinned opencode model was K3, whose catalog defines exactly one variant, so the
pinned value and any provider default coincided and the gap could not bite. On 2026-08-06
the pool gained `deepseek-reviewer` at `opencode/deepseek-v4-pro`, whose catalog
advertises `high` beside `max`, and was repinned the same day to
`opencode/deepseek-v4-flash`, which advertises both `low` and `high` beside `max`. Read
back off `opencode --pure models opencode --verbose` on 2026-08-07: `deepseek-v4-flash`
lists `high`, `low`, `max`; `deepseek-v4-pro` lists `high`, `max`. A silent downgrade from
`max` is now representable in two directions rather than one, would change the reasoning
budget of a review verdict, and would leave no trace in anything the dispatcher captures.

**Correction, 2026-08-07.** An earlier revision of this paragraph claimed the same
readback showed `kimi-k3` absent from provider `opencode`. It does not. The listing
carries `kimi-k3` with `providerID` `opencode` and the single variant `max`, measured
twice that day. What is true is narrower: the catalog lists the model and the server
refuses to serve it. `CHANGELOG.md` records a live `opencode/kimi-k3` dispatch on
2026-08-06 returning exactly the server-side refusal this entry documents above as how an
unknown model id fails. Catalog presence, reachability, and pool membership are three
separate facts, and the removed sentence collapsed them. It was written as though read
off the command while actually carried over from prose; four independent reviewers caught
it in one round on 2026-08-07, which is what that round was for.

`kimi-reviewer` left the pool the same day, replaced by `codex-luna` on the codex
backend, which this issue does not reach. So the exposure narrowed to a single worker and
widened in variants at once, and the two changes must not be read as cancelling.

The dispatcher, not the CLI, is what prevents it: `build_inner_command` raises
`SchemaError` for any variant other than the literal `max`, independent of what
`_shared/backends.json` says, and `test_deepseek_command_refuses_high_even_though_the_catalog_offers_it`
holds that line. This bounds the exposure to what a provider-side default could do; it
does not observe what actually ran.

**What would resolve it.** A CLI that echoes the variant in its event stream or exit
metadata, so the dispatcher can compare it against the pin and fail closed on a
mismatch.

The alternative this issue previously proposed — restrict the opencode roles to models
whose catalog exposes a single variant — was **considered and declined by explicit human
decision on 2026-08-06**, when `deepseek-reviewer` was pinned at `max` knowing the
variant could not be verified — and reaffirmed hours later when the repin to V4 Flash
took the count of unverifiable alternatives from one to two rather than backing away.
Recording the refusal keeps the gap deliberate rather than accidental, which is the same
standard issue #1 applies to auxiliary model calls.

**Interim rule.** Treat `variant_verified` in preflight as "the catalog lists this
variant", never as "this variant ran". For `deepseek-reviewer` say the pinned model
produced the review; do not state the reasoning effort it ran at.

---

## 3. The AGY catalog probe answers intermittently, so preflight fails closed at random

**Status:** open, cause not known, observed 2026-08-07 to 2026-08-08
**Affects:** `agy` (`gemini-3.1-pro-high`) — preflight only; a dispatch that starts is
unaffected
**Not affected:** the other five workers, whose probes did not misbehave in the same
window
**Related:** #4, a signed-out `agy` failing the catalog test rather than skipping it —
a different mechanism, and this account is of an authenticated CLI
**Evidence:** `tasks/live-restore-review/log.md` and `tasks/agy-catalog-review/log.md`

`bin/worker preflight` reports `agy` as `unavailable_fail_closed` with the detail "exact
AGY model is absent from catalog", while the model is in the catalog. Minutes later the
same command on the same commit reports `catalog_verified`. The failures come in runs
rather than singly.

## What was measured

| when | what ran | result |
|---|---|---|
| 2026-08-07 | 5 consecutive `bin/worker preflight` | all `catalog_verified` |
| same session | 1 capture taken for an evidence packet | `unavailable_fail_closed` |
| same session | 5 consecutive `bin/worker preflight` | all `unavailable_fail_closed` |
| same session | 4 direct `probe_with_status(["agy","models"])` | all returned the pin |
| same session | 3 consecutive `bin/worker preflight` | all `catalog_verified` |
| same session | 12 consecutive direct probes, no pause | all returned the pin |
| same session | 3 consecutive `bin/worker preflight` | all `unavailable_fail_closed` |
| 2026-08-08 | 8 interleaved pairs, CLI then direct | both sides passed 8 times |

One `tests/run.sh` in the failing period took 93s and skipped
`test_local_agy_catalog_probe_retains_home_without_exposing_it_to_worker` because the
probe did not answer; the next took 8s and did not skip.

Separately, `agy models` with stdout redirected to a regular file produced zero bytes at
240s and at 300s, while the same command through a subprocess pipe returned in about 5s.
That observation is unexplained and is not established as the same phenomenon.

## Two explanations, neither established

**That the CLI's preflight path differs from a direct call.** Called refuted once and it
should not have been. The reasoning was that `backend_preflight("agy", ...)` invoked
directly returned `available_pending_auth` while the CLI reported failure, and that the
CLI then passed three times — but a CLI that also fails in bursts is consistent with
both, so that is not a refutation. Across the session no direct probe ever failed and the
CLI failed in three bursts; every direct probe also ran during a passing window, so the
asymmetry is not evidence either.

**That rapid repeats are rate limited.** Twelve consecutive probes with no pause between
them returned the pinned model twelve times.

The deciding experiment is to measure both paths inside a failing window. Eight
interleaved pairs all passed on both sides, so it never fired. **The cause is not known
and the failures have not been reproduced on demand.**

## Why it matters

The failure direction is safe. An empty catalog reads as an absent pin, preflight fails
closed, and no dispatch is attempted, so nothing is spent on an unverified pin. What is
lost is reproducibility: a green preflight for `agy` is a statement about one run and not
about the pin, and an evidence packet containing a preflight capture can disagree with
the rest of itself depending on when it was taken. That is how this was found — three
reviewers refused to approve a packet whose preflight section contradicted its other
evidence, and they were right to.

## What would resolve it

Catching a failing window with both paths instrumented, which needs a long observation
rather than a fix. Failing that, a probe that distinguishes "the catalog does not contain
this pin" from "the catalog did not arrive" would turn a silent fail-closed into a stated
one; today both land in the same `unavailable_fail_closed` with the same detail string.

**Interim rule.** Do not quote a single `bin/worker preflight` run as evidence that `agy`
is available or that it is not. Say when the capture was taken, and re-run before acting
on either answer.

---

## Reporting more

The entries follow the same shape on purpose: what was measured, what it means, what
would close it, and what may be claimed meanwhile. An issue that cannot state its own
evidence path does not belong here — put it in a task under `tasks/` and dispatch
against it first.
