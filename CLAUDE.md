# Multiagent Claude Main Rules

## Topology

Claude Code interactive session is sole main/orchestrator. Launch only from this
repository with `bin/claude-main`. Main pins `claude-opus-5` and effort `high`; no
`claude-main` worker and no fallback model exist.

Worker pool is fixed:

- `codex-sol` — `gpt-5.6-sol`, reasoning `high`; only workspace-write worker. Its
  codex-side sandbox is disabled (`--sandbox danger-full-access`) because the
  dispatcher's Bubblewrap namespace is the enforcing boundary; a nested sandbox
  cannot run against a scoped read-only workspace root.
- `codex-terra` — `gpt-5.6-terra`, reasoning `max`; independent read-only review.
- `agy` — `gemini-3.1-pro-high`, effort `high`; read-only third-party,
  operations, or multimodal review after local preflight proves exact pin.
- `kimi-reviewer` — `opencode-go/kimi-k3`, variant `max`; read-only large-context
  review. `max` is the pinned variant by explicit human decision: the opencode
  catalog defines no other variant for K3. Automatic variant substitution stays
  forbidden; when the pinned variant is absent, preflight fails closed.
- `fable-advisor` — `claude-fable-5`; tools disabled, no session persistence,
  read-only advisor for important design, ambiguity, security, and regression risk.
  Runs without `--bare`, which reads neither OAuth nor keychain and therefore cannot
  authenticate this account; the sandbox's empty fake home supplies the isolation bare
  mode was there for, and no target repository is mounted so no workspace CLAUDE.md can
  steer the advisor.

## Main Operating Rules

1. Create task from `_templates/task.md`. Fill Control Plane JSON before a worker call.
2. Choose minimal worker set through `_shared/routing.md`.
3. Add matching planned `workers_approved` entry, then run `bin/worker approve`.
   Private `.runtime/approvals` journal is authority; task/log approval text is only
   audit mirror and never authorizes dispatch.
4. For `codex-sol`, require absolute `target_repo`, non-`none` `write_scope`, and
   Bubblewrap scope containment. Missing or malformed field fails closed.
5. Block delete, `git_push`, deploy, and secret access unless separate authoritative
   `bin/worker approve --action` record exists. Never put credentials or raw data in logs.
6. Run producer before reviewers. Run reviewers independently: never place another
   reviewer's conclusion in their input. Main alone reads private raw results and
   synthesizes final decision.
7. Require no-yes-man verdict schema from Terra, AGY, Kimi, and Fable. Missing,
   malformed, or rubber-stamp verdict is failure, never success.
8. Escalate important design, ambiguity, security, or regression risk to
   `fable-advisor`; main makes final decision.
9. Build the reviewer packet as evidence main can defend. Include the full command
   output, never a truncated tail; scope the diff to the one change under review;
   supply the pre-change baseline whenever a requirement says something stays
   unchanged. Reviewers judge the packet, so a defective packet earns a correct
   rejection about main's work rather than the producer's.
10. Re-test a reviewer's claim before acting on it. Verdicts have contained both
    confirmed defects and over-claims; treat every asserted fact as a hypothesis and
    measure it directly.
11. Approval binds the tuple task, role, write scope, and target. Restoring those
    exact values lets a worker be dispatched again with no new human confirmation,
    which is intended for repeated rounds inside one task. Split tasks when a round
    must carry its own approval.

## Invocation Gate

`bin/worker dispatch` builds fixed argument arrays with `subprocess`, never shell
text evaluation. External model calls remain off until authoritative approval exists,
caller sets `MULTIAGENT_ALLOW_BILLABLE=1`, and explicit auth mount/env opt-in plus
separate `secret_access` approval exists. Dispatcher gives workers fake HOME plus
minimal mounts; network is not isolated.
Raw stdout/stderr goes to unique private task run folders; log receives metadata only.

Use `bin/worker preflight` before selecting a backend. It performs local CLI help,
catalog, cache, and isolated sandbox `--version` checks only; it never validates a
model by spending tokens. Startup success remains separate from endpoint, auth, and
model-acceptance status.

## What Only a Live Call Establishes

Preflight proves a flag appears in `--help`. It cannot prove the flag is honored, and
a fixture written from our own assumption will agree with us forever. Every defect
found on this harness so far was invisible to the mock suite, and the worst one exited
zero while running an unpinned model. Treat a backend as usable only after one real
dispatch, and read `live_dispatch` in preflight for whether that has happened on the
current pin.

Argv contracts are per CLI and cannot be generalized. `agy --print` takes the prompt as
its value and stops parsing at the first positional, so its prompt goes last; opencode
`--file` is an array option that swallows trailing positionals, so its message goes
first. These are opposite rules for the same job.

Pins are narrower than they look. A model pin is enforced server-side for codex,
opencode, and agy, but it is not exclusive: the Claude CLI reported one small internal
`claude-haiku-4-5` call alongside the pinned model, and other backends do not report
per-model usage at all. The opencode `--variant` pin is not enforceable — an invalid
variant is accepted silently and no event states the variant in effect. Say what is
verified rather than what is configured.

Each backend declares the single credential file it needs, copied into the sandbox
home through a descriptor. There is no blanket auth mount: an undeclared kind fails
closed rather than exposing the whole auth directory.

## Re-entry

Read `task.md`, `context.md`, and append-only `log.md`; then re-run preflight and
invariant checks before dispatch. Do not rewrite log history. If a prior reviewer
result is needed for synthesis, main reads it directly; do not forward it to another
reviewer.

One task carries one `write_scope`, so a producer round and a review round cannot be
approved from the same control-plane state. Set the producer scope, approve, dispatch,
then set `workflow_stage` to `review` with `write_scope` `none` before reviewer
approval. Journal records keep the values they were approved with, so flipping the
control plane never rewrites past authority.
