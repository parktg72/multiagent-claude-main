# OpenCode Zen Repin and Reviewer Split — Design

**Date:** 2026-08-06
**Status:** approved by human, ready for implementation planning

## Problem

The `kimi-reviewer` worker is pinned to `opencode-go/kimi-k3`. Two changes are wanted:
move the provider to OpenCode Zen, and make the reviewer pool able to use either
Kimi K3 or DeepSeek V4 Pro depending on the role a round needs.

## What the catalog actually says

Measured on 2026-08-06 with `opencode --pure models --refresh` and
`opencode --pure models <provider> --verbose`. No model call was spent.

`opencode-zen` is not a provider ID. It is the *display name* of the provider whose ID
is `opencode`:

```
provider id 'opencode-go' -> "OpenCode Go"   api https://opencode.ai/zen/go/v1
provider id 'opencode'    -> "OpenCode Zen"  api https://opencode.ai/zen/v1
```

`opencode --pure models opencode-zen` answers `Provider not found`, which is correct and
misleading at the same time: the provider exists under a different token. `opencode
--pure auth list` shows both `OpenCode Zen` and `OpenCode Go` credentials, and both keys
live in the same `~/.local/share/opencode/auth.json`.

Both models under provider `opencode`:

| | `kimi-k3` | `deepseek-v4-pro` |
|---|---|---|
| context / output | 1,048,576 / 131,072 | 1,000,000 / 384,000 |
| cost in / out | $3 / $15 | $1.74 / $3.84 |
| variants | `max` | `high`, `max` |
| attachment | true | false |
| status | active | active |

## Decisions

Three decisions were made by the human operator, each recorded here because none of
them is derivable from the code.

1. **Provider becomes `opencode`** (OpenCode Zen), for both reviewers.
2. **Two workers with fixed pins**, not one worker with a selectable model. A selectable
   model would have to be bound into the approval tuple, or a Kimi approval would
   dispatch DeepSeek. Separate roles get that binding for free, since approval already
   binds the role.
3. **DeepSeek variant is `max`.** The catalog exposes `high` and `max`; `max` matches
   `kimi-reviewer`, so a disagreement between the two reviewers reads as a difference of
   perspective rather than a difference of reasoning budget.

## Worker inventory: five to six

| role | model | variant | access |
|---|---|---|---|
| `kimi-reviewer` | `opencode/kimi-k3` | `max` | read-only |
| `deepseek-reviewer` (new) | `opencode/deepseek-v4-pro` | `max` | read-only |

Auth is unchanged. The dispatcher's auth descriptor maps `kind: opencode` to a single
`opencode/auth.json` (`bin/worker.py:61`), and both providers' keys are in that file.

## Role split, stated honestly

The context windows are effectively identical — 1,048,576 against 1,000,000, under 5%
apart — so "large context" does not distinguish them and must not be written as if it
did. The differences that survive measurement are output ceiling and price:

- `deepseek-reviewer` — 384k output against Kimi's 131k, at $3.84 per million out
  against $15. Suits enumerative review: per-file audits, long findings lists, bulk
  low-risk checks, anything whose answer is long.
- `kimi-reviewer` — the incumbent, with a verdict history on this harness and a
  four-times higher output price. Suits review whose answer is short and whose judgement
  matters: a verdict on one contested change.

Kimi's `attachment: true` is **not** a reason to route to it today. Review packets are
text JSON, and the multimodal route already belongs to `agy`. If image packets are ever
sent, the distinction becomes real and routing can be revisited then.

The substantive gain is fan-out width: the reviewer pool goes from three to four, and
two of the four are different model families, so independent verdicts are more
independent.

## Code changes

| Location | Change |
|---|---|
| `_shared/backends.json` | replace kimi model token; add `deepseek-reviewer` block |
| `worker.py:1638` `expected` | six roles |
| `worker.py:1650` `pins` | kimi token replaced; deepseek entry added |
| `worker.py:1514` `opencode_kimi_metadata` | drop the `kimi-k3` literal; take model id as an argument |
| `worker.py:1621` preflight branch | key on `kind == "opencode"` rather than the role name; derive provider from the model token prefix; cache the catalog probe per provider |
| `worker.py:1573` `cli_contracts` | add `deepseek-reviewer` (same opencode flag set); contracts are keyed by role |
| `worker.py:1712` README invariant | update the kimi argv line; add the deepseek line |
| `worker.py:1937` | add the role to the preflight loop |
| `tests/fixtures/mock-bin/opencode` | update provider, model, and argv fixtures |
| `tests/test_worker.py`, `tests/test_hardening.py` | update kimi references; add deepseek cases |

**Deliberately unchanged:** `worker.py:1152`, `if effort != "max": raise SchemaError`.
That literal is a defence that holds regardless of what the config file says, so a
config edit alone cannot downgrade a variant. Both models pin `max`, so it still passes.
Reading the value from config would trade the guarantee for nothing.

## Consequence for verification status

`pin_digest` hashes the model (`bin/worker.py:184`), so changing kimi's provider
invalidates its recorded live observation. After this change:

- `.runtime/live/kimi-reviewer.json` no longer matches the pin; preflight drops to
  `available_pending_auth`.
- `deepseek-reviewer` has no observation at all.
- The claim in `CLAUDE.md` that all five workers have completed a real dispatch becomes
  false.

Therefore **one real dispatch per reviewer is part of this work, not a follow-up.**
Every defect this harness has found was invisible to the mock suite; a green mock run
is not evidence that a Zen pin answers. Each dispatch needs
`MULTIAGENT_ALLOW_BILLABLE=1` and its own approval record.

## ISSUES.md #2 moves from latent to live

Issue #2 currently reads: the `--variant` pin is unenforceable, but "the exposure is
latent — it appears the moment K3 gains a second variant, or the pin moves to a model
that has several." Pinning `deepseek-v4-pro` satisfies that condition. The entry must be
rewritten to say:

- The exposure is **active**, not latent. `deepseek-reviewer` is pinned to a two-variant
  model, the CLI accepts an invalid variant silently, and no event names the variant in
  effect.
- The remedy #2 proposed — restrict the role to single-variant models — was **considered
  and declined by explicit human decision** on 2026-08-06.
- Reading rule: for `deepseek-reviewer`, `variant_verified` means "the catalog lists
  `max`". It never means "`max` ran".

## Documentation

Update `CLAUDE.md` (worker list), `README.md` (invocation table), `USAGE.md` (routing
table), `_shared/routing.md` (worker selection).

Record in `README.md` that `opencode-zen` is a display name and `opencode` is the
provider ID. Finding this took several probes, and reading `Provider not found` as
"no such provider" is exactly the wrong inference — worth one sentence so nobody repeats
it.

## Success criteria

1. `bin/check-invariants` passes with six workers.
2. `bash tests/run.sh` passes, including new `deepseek-reviewer` cases.
3. `bin/worker preflight` reports both reviewers against provider `opencode` with the
   pinned variant found in the catalog.
4. One real dispatch each for `kimi-reviewer` and `deepseek-reviewer` exits zero and
   returns a verdict that `validate_verdict` accepts; both `.runtime/live/*.json`
   observations match the new pins.
5. `ISSUES.md` #2 states the exposure is active and records the declined remedy.
6. No credential value appears in any log, task file, or commit.
