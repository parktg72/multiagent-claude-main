# Orchestrator Policy

Claude main is interactive and stateful through project task files plus private
runtime approval journals. Worker calls are one-shot subprocesses and may spend
provider quota only after approval.

- Preserve task `log.md` append-only. Allowed metadata tags: `DECISION`,
  `WORKER_CALL`, `VERIFICATION`, `ERROR`, `APPROVAL`, `COMPLETE`.
- Keep `context.md` under 1500 Korean characters or 300 English words. Keep normal
  worker input concise; evidence packet may be stored separately in task folder.
- Use relative project paths or explicit environment variables. Do not write global
  Claude, Codex, AGY, OpenCode, marketplace, or credential configuration.
- Do not request model access to secrets. Default fake HOME exposes no host secret.
  Real auth requires explicit auth mount/env opt-in plus declared and separately
  approved `secret_access`; it is a deliberate residual risk. Do not dispatch
  destructive action without separate authoritative approval; no push or deployment
  is part of default workflow.
- Capture tool and session version with `bin/worker preflight` and shell `--version`
  commands when reporting reproducibility. Do not capture credential values.
