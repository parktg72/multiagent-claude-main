# Task Log — append only

Allowed tags: `DECISION | WORKER_CALL | VERIFICATION | ERROR | APPROVAL | COMPLETE`.
Each entry uses UTC timestamp and metadata JSON. Never copy prompt, raw model output,
secret, token, or actual dataset into this file. `[APPROVAL]` is audit mirror only;
private `.runtime/approvals` journal is authoritative.

```text
[2026-08-02T00:00:00Z] [APPROVAL] {"action":"worker","authority":"runtime-journal","target_repo":"bound","worker":"codex-terra","write_scope":"none"}
```
