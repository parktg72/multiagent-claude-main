# Fail-Closed Multiagent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Claude Code-main multiagent harness with exact worker pins, approval gates, independent no-yes-man review, and local-only verification.

**Architecture:** Claude Code interactive session is sole orchestrator. `bin/worker.py` loads immutable backend records, validates mutable task planning data against private runtime approval records, builds allowlisted subprocess argument arrays, then captures raw worker output separately from append-only metadata. Reviewer inputs and output verdicts use strict JSON schemas.

**Tech Stack:** Python 3 standard library, POSIX shell launchers, JSON, Bubblewrap per-worker filesystem containment.

## Global Constraints

- Only this repository's own tree (`./**`) is writable.
- No package install, global-config change, external model call, push, deployment, or third-party hook execution.
- No fallback model paths. Exact model and effort arguments required.
- `codex-sol` is sole workspace-write worker; all other workers remain read-only.
- Writer calls require task `workers_approved`, absolute `target_repo`, in-scope `write_scope`, and matching private runtime approval record; task/log approval text is audit-only.
- Reviewer output must conform to verdict schema and include evidence, unverified claims, and one concrete failure mode/risk.

## Verification Status (checked 2026-08-03)

Boxes were ticked after inspecting the delivered artifacts, not from an implementation
session transcript. Evidence: `bash tests/run.sh` → 13 + 32 tests, OK in 3 of 4 runs on
this date (see the flaky-test note in `2026-08-03-dispatcher-hardening.md`: the real `agy
models` network probe can exceed `run_probe`'s 20 s timeout);
`bin/check-invariants --self-test` → PASS; `bin/worker preflight --allow-unavailable`
→ status categories only, no model inference call.

Per-task evidence: Task 1 → `tests/test_worker.py` plus fixtures for `bwrap`, `codex`,
`agy`, `opencode`, `claude`, and both `_shared/schemas/*.json`; contract cases present
(`test_unapproved_writer_is_rejected`, `test_scope_traversal_is_rejected_before_approval`,
`test_review_input_cannot_include_prior_reviewer_conclusion`, strict-mock happy paths per
backend). Task 2 → `bin/worker.py` provides `dispatch`, `validate-review`, `preflight`,
`approve`, `check-invariants`, with `require_authorization`, `authoritative_journal_path`,
`scope_paths`, `WriterLock`, and dispatch timeout handling; `invariant_issues` pins
`gpt-5.6-sol/high`, `gpt-5.6-terra/max`, `gemini-3.1-pro-high/high`,
`opencode-go/kimi-k3/max`, `claude-fable-5` and requires `fallbacks == []` with
`fallback_policy == "forbid"`. Task 3 → all named policy, template, and launcher files
exist; `invariant_issues` additionally checks worker inventory, reviewer no-yes-man
contract, Fable tool/session constraints, and required document presence. Task 4 → suite,
self-test, and preflight all re-run on 2026-08-03.

Superseded (2026-08-03): the Kimi pin below reads `high`. The opencode catalog defines
only `max` for `opencode-go/kimi-k3`, so the reviewer was repinned to `max` by explicit
human decision; `invariant_issues` now pins `opencode-go/kimi-k3/max`. The rule against
the dispatcher swapping variants on its own is unchanged.

Caveats: the TDD-order steps ("write failing tests", "expect failure") are inferred from
the presence of the matching regression tests, which now pass. The Task 4 change-boundary
check was filesystem-only — this directory is not a git repository, so no `git status`
evidence exists. Endpoint, auth, and model acceptance stay unverified by design;
`kimi-reviewer` now reports `available_pending_auth` with `variant_verified` against its
pinned `max` variant.

---

### Task 1: Contract tests and schemas

**Files:**
- Create: `tests/test_worker.py`
- Create: `tests/fixtures/mock-bin/bwrap`
- Create: `tests/fixtures/mock-bin/codex`
- Create: `_shared/schemas/review-input.schema.json`
- Create: `_shared/schemas/review-verdict.schema.json`

**Interfaces:**
- Consumes: `bin/worker.py` command `dispatch`.
- Produces: deterministic tests for approval, scope, review-contract, timeout, and output validation behavior.

- [x] Write failing tests for unapproved writer, missing authoritative approval, scope traversal, reviewer input contamination, invalid verdict, and mock dispatch.
- [x] Run `python3 -m unittest -v tests.test_worker`; expect import/file failure before dispatcher exists.
- [x] Add JSON schemas and fixture executables.
- [x] Re-run targeted tests; expect implementation-specific failures.

### Task 2: Fail-closed dispatcher

**Files:**
- Create: `bin/worker.py`
- Create: `bin/worker`
- Create: `_shared/backends.json`
- Create: `_shared/no-yes-man.md`

**Interfaces:**
- `python3 bin/worker.py dispatch --role ROLE --task TASK --input INPUT [--dry-run]`
- `python3 bin/worker.py validate-review --file FILE`
- `python3 bin/worker.py preflight`

- [x] Implement restricted task-control parser, private approval journal, path containment, scope grammar, protected-action gate, writer lock, subprocess timeout, raw result capture, and metadata-only logging.
- [x] Build commands only from backend records and fixed allowlist: `codex`, `agy`, `opencode`, `claude`, `bwrap`.
- [x] Use exact `gpt-5.6-sol/high`, `gpt-5.6-terra/max`, `gemini-3.1-pro-high/high`, `opencode-go/kimi-k3/high`, and `claude-fable-5` pins. Do not add fallback arguments.
- [x] Run unit tests after each small behavior change until all pass.

### Task 3: Operating policies and task templates

**Files:**
- Create: `CLAUDE.md`, `README.md`, `.env.example`, `.gitignore`, `NOTICE.md`
- Create: `_shared/routing.md`, `_shared/capability-policy.md`, `_shared/approval-policy.md`, `_shared/orchestrator-policy.md`, `_shared/invariants.md`
- Create: `_templates/task.md`, `_templates/context.md`, `_templates/log.md`, `_templates/worker-input.md`, `_templates/worker-output.md`, `_templates/review-input.json`
- Create: `bin/claude-main`, `bin/check-invariants`, `tests/run.sh`

**Interfaces:**
- Main launch: `bin/claude-main`.
- Invariant check: `bin/check-invariants`.
- Test suite: `tests/run.sh`.

- [x] Document main-only Claude topology, sequential Producer-Reviewer workflow, independent reviewer isolation, and Fable escalation.
- [x] Document model verification limits and exact user commands.
- [x] Implement invariant checks for worker inventory, model pins, no fallback, tool constraints, approval terms, schemas, and launchers.
- [x] Run JSON, Python, shell, invariant self-test, mock-dispatch tests, and capability preflight without model calls.

### Task 4: Final verification

**Files:**
- Verify all created files only.

- [x] Run `bash tests/run.sh`.
- [x] Run `bin/check-invariants --self-test`.
- [x] Run `bin/worker preflight` and record status categories only.
- [x] Verify target-only change boundary with filesystem and git-status checks where applicable.
