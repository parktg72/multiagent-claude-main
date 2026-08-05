# Dispatcher Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove secret/control-plane exposure and make runtime worker dispatch fail closed on approval, pin, CLI-contract, and review-schema drift.

**Architecture:** Replace whole-root Bubblewrap mount with explicit system/runtime, workspace, input, fake-home, and optional auth mounts. Approval authority moves from mutable task files to private append-only runtime journal. Dispatcher derives a per-role sandbox view and invokes runtime invariant/CLI gates before every worker launch.

**Tech Stack:** Python 3 standard library, Bubblewrap, POSIX shell, JSON Schema subset validator, test-only strict mock CLIs.

## Global Constraints

- Modify only this repository's own tree (`./**`).
- No external model call, package install, global config write, push, deployment, or target-outside deletion.
- Network remains available only because model APIs may require it; filesystem isolation is not network isolation.
- Exact pins have no fallback. Kimi remains `high` and unavailable until local metadata exposes `high`.

## Verification Status (checked 2026-08-03)

Boxes were ticked after inspecting the delivered artifacts, not from an implementation
session transcript. Evidence: `bash tests/run.sh` → 13 + 32 tests, OK in 3 of 4 runs on
this date; `bin/check-invariants --self-test` → PASS; `bin/worker preflight
--allow-unavailable` → six backends, `sandbox_startup: available` each.

Resolved flakiness (2026-08-03): `test_local_agy_catalog_probe_retains_home_without_exposing_it_to_worker`
failed once out of four runs. It calls the real `agy models` catalog over the network
through `run_probe`, whose timeout was a hard 20 s; the slow run took 28 s overall while
`agy models` standalone takes ~4 s, and the test passed in isolation.

Fix: `probe_with_status` now reports `ok` / `exit` / `timeout` / `missing` separately
(`run_probe` keeps its two-value contract). Catalog probes get
`CATALOG_PROBE_TIMEOUT_SECONDS = 90`; help probes keep 20 s. The network assertion skips
with a stated reason on `timeout`/`missing` and still hard-fails when the CLI runs and the
exact pin is absent. The hermetic half moved to
`test_exact_catalog_line_rejects_partial_model_tokens`, which never skips, plus
`test_probe_environment_retains_home_but_worker_sandbox_does_not` for the HOME property the
old test name claimed but never asserted. Verified: skip branch reproduced by removing
`agy` from PATH; three consecutive full-suite runs green.

Per-task evidence: Task 1 → `tests/test_hardening.py` exists with the named regression
cases (`test_sandbox_plan_never_mounts_root_or_real_home`,
`test_reviewer_plan_hides_sibling_result_and_runtime`,
`test_tasks_only_mounts_artifacts_not_control_plane`,
`test_authoritative_approval_rejects_forged_task_log_action`,
`test_dispatch_stops_on_runtime_invariant_drift`,
`test_strict_mock_binaries_reject_unknown_flags`,
`test_child_exit_124_is_not_dispatch_timeout`,
`test_stale_lock_file_does_not_block_flock_writer`,
`test_local_agy_catalog_probe_retains_home_without_exposing_it_to_worker`,
`test_schema_rejects_empty_evidence_generic_risk_and_short_question`). Task 2 →
`authoritative_journal_path`, `build_sandbox_plan`, `WriterLock`, `confirm_approval`
in `bin/worker.py`; `test_actual_bwrap_probe_hides_unbound_secret_and_enforces_write_surface`
is a real non-network Bubblewrap probe. Task 3 → `enforce_runtime_invariants`,
`cli_contracts`, `invariant_issues` pinning `claude-opus-5`/`high`,
`_shared/schemas/review-verdict.schema.json`. Task 4 → README argv table is gated by
`test_dispatch_stops_on_readme_argv_mapping_drift`.

Superseded (2026-08-03): the constraint line above keeps Kimi at `high`. The opencode
catalog defines only `max` for `opencode-go/kimi-k3`, so the reviewer was repinned to
`max` by explicit human decision and now passes preflight. The dispatcher still refuses
to build a command for any variant other than the pinned one, in either direction.

Test-runtime isolation (2026-08-03): the suite used to write the writer lock and approval
journals into the real `.runtime`, leaving `codex-sol.lock` behind on every run.
`private_runtime_dir()` now honors `MULTIAGENT_TEST_RUNTIME_DIR`, gated on the exact
fixture test-mode pair and required to resolve inside the project; the override path is
also added to the control-root and protected-path guards. Setting it without the pair, or
pointing it outside the project, raises `GateError` rather than silently redirecting the
authoritative journal. Covered by `test_runtime_override_is_refused_outside_fixture_test_mode`,
`test_runtime_override_must_stay_inside_project`, and
`test_dry_run_leaves_real_private_runtime_untouched`; three consecutive full-suite runs
now leave zero files under `.runtime`.

Not retroactively verifiable: the TDD-order steps ("add failing tests", "confirm
failures identify missing behavior") are inferred from the presence of the matching
regression tests, which now pass. Test passage is not provider integration verification;
`endpoint`, `auth`, and `model_acceptance` remain unverified for every backend.

---

### Task 1: Regression tests first

**Files:**
- Create: `tests/test_hardening.py`
- Modify: `tests/test_worker.py`, `tests/fixtures/mock-bin/*`

**Interfaces:**
- Dispatcher commands: `approve`, `dispatch`, `preflight`, `check-invariants`.
- Sandbox-plan inspection exposes argv and allowed mounts without invoking models.

- [x] Add failing tests for no root bind, fake home, absent sibling reviewer/control mounts, artifacts-only task writes, forged task/log approval rejection, authoritative approval command, runtime invariant gate, strict mock flags, child `124`, stale flock, exact catalog parsing, and structured generic-risk rejection.
- [x] Run target tests and confirm failures identify missing hardening behavior.

### Task 2: Control plane and sandbox

**Files:**
- Modify: `bin/worker.py`, `_shared/backends.json`, task/log templates

**Interfaces:**
- `authoritative_journal_path(task) -> Path`
- `build_sandbox_plan(role, backend, task, target, scope, input_path) -> SandboxPlan`
- `approve --role ROLE --task TASK --confirm [--action ACTION]`

- [x] Implement private approval journal, interactive TTY confirmation in every mode, immutable binding digests, and flock writer lock.
- [x] Bind only required system/runtime files, fake home, explicit workspace/scope, and input. Never bind real home, task control, logs, runtime journal, or sibling reviewer output.
- [x] Restrict `tasks-only` to `task_dir/artifacts/`; reject self/control target repos and scope escapes.
- [x] Run sandbox visibility probe with Bubblewrap and no model/network request.

### Task 3: Contract and schema gates

**Files:**
- Modify: `bin/worker.py`, `_shared/schemas/*.json`, `_shared/backends.json`
- Modify: `bin/claude-main`, `.env.example`

**Interfaces:**
- Schema validator consumes source JSON schemas directly.
- Preflight reports CLI contract, catalog state, and endpoint-unverified state separately.

- [x] Enforce invariants at dispatch start.
- [x] Verify every used CLI flag in preflight; strict mocks reject unknown/missing arguments.
- [x] Use exact `claude-opus-5` and fixed `high` main effort.
- [x] Replace reviewer verdict with structured risks and source-of-truth schema validation.

### Task 4: Documentation and final verification

**Files:**
- Modify: `README.md`, `CLAUDE.md`, `_shared/*.md`, `_templates/*.md`, `tests/run.sh`, `NOTICE.md`

- [x] Document auth mount opt-in, non-isolated network, unavailable Kimi, and endpoint limits accurately.
- [x] Sync exact argv tables with dispatcher.
- [x] Run complete test suite, JSON/schema checks, syntax checks, runtime invariant self-test, preflight, and non-model Bubblewrap probe.
