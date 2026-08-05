# Notice and Source Basis

Architecture informed by a local plugin-marketplace cache of
`netwaif/multi-agent-starter` v3.5.0: file-backed task state, approval/write-scope
gates, Pipeline/Fan-out/Producer-Reviewer routing, and invariant checks. This
repository is a clean, manual reimplementation for requested backend pins; no
starter hook or generator was executed and no source files were copied.

Reference repository declares MIT license. Preserve its copyright and license when
copying any future upstream material; this project presently contains no copied
upstream code.

Its `validate.py --flavor claude` was run read-only against this project after
generation. Expected failure is not an acceptance failure here: that validator
requires starter-specific `claude-main`, `.claude/agents/claude-main.md`, MCP
records, legacy backend schema, and template files deliberately excluded by this
main-only Claude topology. `bin/check-invariants` is this project's canonical check.
