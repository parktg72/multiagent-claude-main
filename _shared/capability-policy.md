# Capability Policy

| Worker | Exact runtime pin | Access | Use |
|---|---|---|---|
| Claude main | `claude-opus-5`, `high` | interactive main only | routing, synthesis, final decision |
| `codex-sol` | `gpt-5.6-sol`, `high` | scoped workspace-write | implementation, debugging, tests |
| `codex-terra` | `gpt-5.6-terra`, `max` | read-only | independent validation, regression review |
| `agy` | `gemini-3.1-pro-high`, `high` | read-only | operations, multimodal, third-party review |
| `kimi-reviewer` | `opencode/kimi-k3`, `max` | read-only | cross-cutting impact, one contested verdict |
| `deepseek-reviewer` | `opencode/deepseek-v4-flash`, `max` | read-only | per-file audits, long enumerative findings |
| `fable-advisor` | `claude-fable-5` | tools disabled/read-only | critical design, ambiguity, security, regression advice |

No backend has fallback. An unavailable exact pin returns failure. `preflight` uses
local CLI discovery/catalog plus isolated fake-HOME/no-auth sandbox `--version` startup
checks; endpoint and account authorization remain unverified until an explicitly
approved call.
