# Routing and Topology

## Worker Selection

- Implementation, debugging, tests, scoped edits: `codex-sol`.
- Independent validation, test evidence, regression analysis: `codex-terra`.
- Cross-cutting impact and contested single verdicts: `kimi-reviewer`
  (`opencode/kimi-k3`), only if its exact `max` variant preflight passes.
- Enumerative review whose answer is long — per-file audits, long findings lists,
  bulk low-risk checks: `deepseek-reviewer` (`opencode/deepseek-v4-flash`), whose
  catalog entry carries a 1M context and a 384k output ceiling at $0.14/$0.28 per
  million in and out. Its catalog also advertises `low` and `high`; the dispatcher
  never builds them. For both, an absent pinned variant disables the route instead
  of downgrading it.
- Operations, multimodal, or third-party review: `agy`, only if AGY exact pin
  preflight passes.
- Important design, ambiguity, security, or regression risk: `fable-advisor`, then
  Claude main decides.

## Allowed Patterns

1. **Pipeline** — producer then reviewer when later work needs earlier artifact.
2. **Fan-out/Fan-in** — independent reviewers receive same neutral packet; main
   reads raw results separately and synthesizes only after all finish.
3. **Expert Pool** — select smallest capable worker subset.
4. **Producer-Reviewer** — `codex-sol` produces; one or more read-only reviewers
   validate sequentially. Writer lock forbids simultaneous writers.

Never let a reviewer invoke another worker. Never send reviewer A's verdict to
reviewer B. Never use hidden fallback, a separate supervisor, or recursive worker
delegation. Both opencode workers use explicit model/variant plus filesystem sandbox;
project-agent lookup is not an authority or permission boundary.

## Required Sequence

```text
Claude main: define task + neutral evidence packet
  -> human approval
  -> codex-sol producer (if edit needed)
  -> codex-terra / AGY / Kimi / DeepSeek / Fable independently, each with neutral packet
  -> Claude main reads raw outputs and decides
```

`fable-advisor` is advisory only. It cannot edit or use tools.
