# Approval Review Residual Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close nonblocking approval-review risks without provider calls or wider filesystem exposure.

**Architecture:** Preflight will reuse worker runtime bindings inside a no-network Bubblewrap `--version` probe and cache probe results only for one invocation. Schema validation will reject unsupported validation keywords before use. Approval confirmation will always require a real TTY path, including tests.

**Tech Stack:** Python 3 standard library, Bubblewrap, POSIX PTY, JSON Schema subset, local strict CLI fixtures.

## Global Constraints

- Modify only this repository's own tree (`./**`).
- No provider/API call, package install, global configuration, whole-home bind, or whole-root bind.
- Startup probe uses fake HOME, no auth mount, no stdin prompt, and isolated network namespace.
- Kimi stays `high` only; unavailable `high` fails closed and never falls back to `max`.

## Verification Status (checked 2026-08-03)

Boxes were ticked after inspecting the delivered artifacts, not from an implementation
session transcript. Evidence: `bash tests/run.sh` → 13 + 32 tests, OK in 3 of 4 runs on
this date (see the flaky-test note in `2026-08-03-dispatcher-hardening.md`: the real `agy
models` network probe can exceed `run_probe`'s 20 s timeout);
`bin/check-invariants --self-test` → PASS; `bin/worker preflight --allow-unavailable`
reports `sandbox_startup` separately from `endpoint`/`auth`/`model_acceptance` for all
six backends. `kimi-reviewer` was `unavailable_fail_closed` when this plan ran; it was
repinned to its only catalog variant, `max`, on 2026-08-03 and now reports
`available_pending_auth`. The constraint line above ("Kimi stays `high` only") is
superseded; the ban on the dispatcher swapping variants by itself is not.

Per-task evidence: Task 1 → `PreflightCache`, `runtime_startup_probe`,
`build_startup_probe_plan`, `validate_runtime_install_root` in `bin/worker.py`, covered by
`test_preflight_reports_isolated_sandbox_startup_separately`,
`test_startup_probe_failure_is_redacted_and_fail_closed`,
`test_startup_probe_plan_has_no_auth_and_unshares_network`,
`test_startup_probe_cache_reuses_actual_claude_probe`,
`test_script_runtime_binding_mounts_dependency_root_and_fails_when_dependency_missing`,
`test_runtime_install_root_rejects_control_or_auth_overlap`. Task 2 →
`schema_keyword_issues` and `confirm_approval`, covered by
`test_production_approve_accepts_pty_confirmation`,
`test_test_mode_does_not_bypass_noninteractive_approval`,
`test_invariant_rejects_unknown_schema_validation_keyword`,
`test_schema_annotations_remain_supported`,
`test_approval_mirror_matches_runtime_event_fields`,
`test_substantive_risk_can_describe_unknown_specific_input`. Task 3 → README documents
the startup-vs-endpoint split and test-mode limits; `.gitignore` carries
`dispatcher-test-*`, `hardening-test-*`, `startup-probe-*`, `sandbox-startup-*`.

Not retroactively verifiable: the TDD-order steps ("add failing tests", "confirm prior
bypass behavior fails") are inferred from the presence of the matching regression tests,
which now pass.

---

### Task 1: Sandboxed startup probe

**Files:**
- Modify: `bin/worker.py`, `tests/test_worker.py`, `tests/test_hardening.py`, `tests/fixtures/mock-bin/*`

**Interfaces:**
- `PreflightCache` stores only one command invocation's help, catalog, and startup probes.
- `runtime_startup_probe(kind, cache) -> StartupProbe` executes a no-network `--version` command through worker runtime mounts.

- [x] Add failing tests for startup success, missing runtime dependency failure, shared Claude/Fable startup result, no auth/no network probe plan, and in-process cache reuse.
- [x] Run targeted tests and confirm missing probe behavior fails.
- [x] Implement minimal runtime root detection, overlap rejection, startup probe, redacted failure status, and one-invocation cache.
- [x] Re-run targeted tests.

### Task 2: Approval, schema, and risk gates

**Files:**
- Modify: `bin/worker.py`, `tests/test_hardening.py`, `_shared/schemas/*.json`

**Interfaces:**
- `schema_keyword_issues(schema, location) -> list[str]` rejects unknown validation keywords while allowing annotations.
- `confirm_approval(arguments)` always uses TTY confirmation.

- [x] Add failing PTY approval, test-mode non-TTY rejection, unknown-keyword, mirror-field, and substantive-`unknown` tests.
- [x] Run targeted tests and confirm prior bypass/ignored-keyword behavior fails.
- [x] Implement keyword invariant, PTY-only confirmation, synchronized mirror fields, and structural boilerplate detection.
- [x] Re-run targeted tests.

### Task 3: Documentation and verification

**Files:**
- Modify: `README.md`, `_shared/approval-policy.md`, `_templates/log.md`, `.gitignore`, `tests/run.sh` if needed

- [x] Document sandbox startup versus endpoint/auth/model acceptance and test-mode limits.
- [x] Add dispatcher/hardening temporary ignore patterns.
- [x] Run `bash tests/run.sh`, invariant self-test, and preflight with no provider call.
