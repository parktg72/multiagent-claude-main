# Open Issues

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

**Status:** open, upstream behaviour
**Affects:** `kimi-reviewer` (`opencode-go/kimi-k3`, variant `max`)
**Evidence:** `tasks/kimi-live-test/task.md` records this under Known Limitation

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

**Why it matters today: barely.** The opencode catalog defines exactly one variant for
K3, `max`, so the pinned value and any provider default coincide. The exposure is
latent: it appears the moment K3 gains a second variant, or the pin moves to a model
that has several — at which point `preflight` would still report `variant_verified`
purely because the token exists in the catalog.

**What would resolve it.** A CLI that echoes the variant in its event stream or exit
metadata, so the dispatcher can compare it against the pin and fail closed on a
mismatch. Failing that, restrict `kimi-reviewer` to models whose catalog exposes a
single variant, and make that restriction an invariant rather than a coincidence.

**Interim rule.** Treat `variant_verified` in preflight as "the catalog lists this
variant", never as "this variant ran".

---

## Reporting more

Both entries follow the same shape on purpose: what was measured, what it means, what
would close it, and what may be claimed meanwhile. An issue that cannot state its own
evidence path does not belong here — put it in a task under `tasks/` and dispatch
against it first.
