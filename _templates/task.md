# Task title

## Control Plane

```json
{
  "status": "pending",
  "workflow_stage": "review",
  "target_repo": null,
  "write_scope": "none",
  "workers_approved": [],
  "requested_actions": ["read"]
}
```

For `codex-sol`, set `workflow_stage` to `producer`, `target_repo` to an absolute
existing non-control repository path, `write_scope` to `tasks-only` or
`src/**, tests/**`, and add a matching planned `workers_approved` object. Then human
runs `bin/worker approve`; task metadata itself cannot authorize. `tasks-only` writes
only task `artifacts/`. Do not add protected actions by default.

## Goal

One measurable outcome.

## Constraints

- No secrets, raw data, deletion, push, or deployment without separate approval.
- Any auth mount or forwarded provider key requires declared `secret_access` plus its
  own `bin/worker approve --action secret_access` record.
- Reviewers receive neutral evidence only.

## Acceptance Criteria

- [ ] Required worker output exists.
- [ ] Validation is recorded in append-only log.
