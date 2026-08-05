# Routing and Topology

## Worker Selection

- Implementation, debugging, tests, scoped edits: `codex-sol`.
- Independent validation, test evidence, regression analysis: `codex-terra`.
- Large repository context, cross-cutting impact: `kimi-reviewer`, only if its
  exact `max` variant preflight passes. `max` is the only variant the opencode
  catalog defines for K3; the dispatcher never swaps variants on its own, so an
  absent pinned variant disables the route instead of downgrading it.
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
delegation. Kimi uses explicit model/variant plus filesystem sandbox; project-agent
lookup is not an authority or permission boundary.

## Required Sequence

```text
Claude main: define task + neutral evidence packet
  -> human approval
  -> codex-sol producer (if edit needed)
  -> codex-terra / AGY / Kimi / Fable independently, each with neutral packet
  -> Claude main reads raw outputs and decides
```

`fable-advisor` is advisory only. It cannot edit or use tools.
