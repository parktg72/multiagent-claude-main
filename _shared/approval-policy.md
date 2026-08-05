# Approval and Write Policy

Every worker invocation needs two distinct controls:

1. `task.md` Control Plane `workers_approved` declares exact worker and matching
   `write_scope`. It is mutable planning metadata, not authority.
2. `bin/worker approve --role <role> --task <task> --confirm` writes private,
   append-only `.runtime/approvals/<task-hash>.jsonl` authority bound to role, scope,
   and target digest.

`log.md` event is audit mirror only. Do not log actual path, prompt, secret, or dataset:

```text
[2026-08-02T00:00:00Z] [APPROVAL] {"action":"worker","authority":"runtime-journal","target_repo":"bound","worker":"codex-sol","write_scope":"src/**"}
```

This mirror is not authority. Private journal records additionally contain version,
task key, target digest, approver, and approval timestamp.

Only `codex-sol` can receive `workspace-write`. It requires absolute existing
non-control `target_repo`, scope `tasks-only` or comma-separated existing
`directory/**` patterns, and Bubblewrap minimal mounts. `tasks-only` means only
`task_dir/artifacts/`, never task.md/log/runtime. Any traversal, symlink escape,
control-root target, missing authoritative approval, or absent Bubblewrap fails closed.

Protected actions are `delete`, `git_push`, `deploy`, and `secret_access`. They are
blocked unless task `requested_actions` names action and a separate
`bin/worker approve --action <action>` record exists. Default tasks omit protected actions.

`MULTIAGENT_ALLOW_BILLABLE=1` plus explicit auth mount/env opt-in are extra runtime
gates; neither replaces human approval. Any auth mount or forwarded provider key also
requires declared and separately approved `secret_access`, even for a dry run.
`MULTIAGENT_TEST_MODE=1` requires the fixture-only sentinel and never bypasses
`--confirm`, TTY, or typed `APPROVE`.
