# Open Issues

Filed upstream as
[#1](https://github.com/parktg72/multiagent-claude-main/issues/1) and
[#2](https://github.com/parktg72/multiagent-claude-main/issues/2). This file holds the
long form, including the evidence paths, which stay on the machine that ran the
dispatches.

Known gaps between what the harness pins and what it can prove. Both were found by
dispatching for real on 2026-08-04 and 2026-08-05, and both are unresolved rather than
mitigated. Neither blocks use; each limits what a claim about a run may say.

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
requested nor authorized it. The worse part is the blind spot: the Claude CLI reports
per-model usage, so this was visible. codex, agy, and opencode do not report it at all,
so an equivalent internal call on those backends would leave no trace in anything the
dispatcher captures.

**Why it matters.** An approval authorizes a worker whose identity is stated as an exact
model. If a second model participates, the approval covered something narrower than what
ran. For a review verdict this is minor; for a change of policy or a security judgement
the provenance claim is weaker than the record implies.

**What would resolve it.** Any of: a provider flag that forbids auxiliary model calls; a
per-run usage report from every CLI so the dispatcher can assert a single model
participated and fail closed otherwise; or an explicit statement in the approval record
that auxiliary calls are permitted, which at least makes the gap deliberate.

**Interim rule.** Say the pinned model performed the reviewed work. Do not say it was the
only model involved.

---

## 2. The opencode `--variant` pin is not enforceable

**Status:** open, upstream behaviour; exposure became active on 2026-08-06
**Affects:** `kimi-reviewer` (`opencode/kimi-k3`, variant `max`) and
`deepseek-reviewer` (`opencode/deepseek-v4-pro`, variant `max`)
**Evidence:** `tasks/kimi-live-test/task.md` records the original probe under Known
Limitation; the catalog reading that activated it is in
`docs/superpowers/specs/2026-08-06-opencode-zen-reviewer-split-design.md`

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

**Why it matters now.** It stopped being latent. When this was filed, the only pinned
opencode model was K3, whose catalog defines exactly one variant, so the pinned value
and any provider default coincided and the gap could not bite. On 2026-08-06 the pool
gained `deepseek-reviewer`, pinned to `opencode/deepseek-v4-pro`, whose catalog
advertises both `high` and `max`. A silent downgrade from `max` to `high` is now
representable, would change the reasoning budget of a review verdict, and would leave
no trace in anything the dispatcher captures.

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
variant could not be verified. Recording the refusal keeps the gap deliberate rather
than accidental, which is the same standard issue #1 applies to auxiliary model calls.

**Interim rule.** Treat `variant_verified` in preflight as "the catalog lists this
variant", never as "this variant ran". For `deepseek-reviewer` say the pinned model
produced the review; do not state the reasoning effort it ran at.

---

## Reporting more

Both entries follow the same shape on purpose: what was measured, what it means, what
would close it, and what may be claimed meanwhile. An issue that cannot state its own
evidence path does not belong here — put it in a task under `tasks/` and dispatch
against it first.
