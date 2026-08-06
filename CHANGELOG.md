# Changelog

Narrative history. `CLAUDE.md` carries the rules and is loaded every session, so it
stays short; this file records how the rules were arrived at and is read only when
someone wants that. Published commit history begins at a squashed root commit, so the
work below predates what `git log` shows.

## 2026-08-06 — repin to opencode Zen, a sixth worker, and a second exit-zero failure

`kimi-reviewer` moved from provider `opencode-go` to `opencode`. The provider's
display name is "OpenCode Zen", but `opencode --pure models opencode-zen` answers
`Provider not found` — the display name is not the token the CLI accepts, and
`opencode-zen` must never appear in a pin. The opencode preflight branch was
generalized from a role-named hardcode of provider and model id to deriving both
from the configured `provider/model` pin, so the repin needed configuration rather
than new branch logic, and the same generalization is what let the sixth worker be
added as configuration too.

**`deepseek-reviewer`**, pinned to `opencode/deepseek-v4-pro` at variant `max`, joined
the pool for enumerative review — per-file audits and long findings lists, where its
384k output ceiling against Kimi's 131k matters. `max` was chosen by explicit human
decision even though this model's catalog also advertises `high`: it is the first
opencode pin where the unenforceable `--variant` limit in `ISSUES.md` (#2) stopped
being latent, since K3's catalog defines only `max` and could never silently
downgrade. Restricting opencode roles to single-variant models was considered and
declined on the same date, so the gap is recorded as a decision rather than an
oversight.

Live re-verification of both opencode reviewers turned up an eleventh defect,
distinct from the ten below: a `kimi-reviewer` dispatch exited zero with
`reason: "unknown"`, zero input and output tokens, cost 0, and no text part after 63s.
Only the no-yes-man verdict contract caught it — an identical re-dispatch immediately
after succeeded, so it reads as a transient, unannounced provider failure rather than
an argv or extraction bug. It is the second time this harness has seen exit zero used
as a stand-in for success, after the `agy --print` defect below, and it is why
`README.md` now says exit zero is necessary but not sufficient for a live
observation, and that for a reviewer it is the verdict contract, not the exit code,
that supplies the missing proof.

## 2026-08-05 — first live dispatch, and what it cost to get there

Before this date the harness had a passing test suite and had never run. Every backend
reported `endpoint_unverified`, every check was against a mock, and the mocks had been
written from the same assumptions as the code they tested.

### Fixed before any live call

- **Flaky catalog test.** `run_probe` collapsed a timeout and a refusal into the same
  return value, so a slow `agy models` call failed the suite indistinguishably from a
  missing pin. Failure reasons split into `ok` / `exit` / `timeout` / `missing`, catalog
  probes given a 90 s budget against 20 s for local help probes, and the network-bound
  assertion now skips with a stated reason instead of failing.
- **Test pollution of private runtime.** The suite wrote its writer lock and approval
  journals into the real `.runtime`. `private_runtime_dir()` now honours
  `MULTIAGENT_TEST_RUNTIME_DIR`, gated on the fixture test-mode pair and required to
  resolve inside the project; setting it without that pair raises rather than silently
  redirecting the authoritative journal.
- **Kimi pinned to a variant that does not exist.** The catalog defines only `max` for
  `opencode-go/kimi-k3`; `high` had never existed there and no cache refresh would
  change it. Repinned to `max` by explicit decision, with the ban on the dispatcher
  swapping variants left intact.

### Ten defects found by dispatching for real

None was reachable from the mock suite. Three lived in fixtures that encoded our own
mistake and therefore always passed. Full mapping to evidence in `tasks/INDEX.md`.

| Backend | Defect | How it failed |
|---|---|---|
| `codex-sol` | read-only `/auth` mount; codex initializes an app-server in `CODEX_HOME` | loud, EROFS |
| `codex-sol` | its nested sandbox creates dot directories at a workspace root our scope left read-only | loud |
| `codex-terra` | provider strict structured output requires `required` to name every property | loud, HTTP 400 |
| `agy` | `--print` consumed `--model`; every later flag dropped and an unpinned model answered | **silent, exit 0** |
| `agy` | credential path unsupported; only `antigravity-oauth-token` is needed | loud |
| `kimi-reviewer` | `--file` is an array option and swallowed the trailing message | loud |
| `kimi-reviewer` | verdict arrives in a JSONL event stream at `part.text`, not as a bare object | loud |
| `kimi-reviewer` | this CLI cannot enforce a schema, so the model added an undeclared key | loud |
| `fable-advisor` + `bin/claude-main` | `--bare` reads neither OAuth nor keychain, so this account could never authenticate | loud |
| `fable-advisor` | inlined schema rejected because its meta-schema URL could not be resolved | loud |

The agy defect is the one that matters. Nine failures announced themselves; that one
reported success while running a model nobody approved.

### Consequences for the design

- **Credential injection replaced the auth mount.** The host auth directory is no longer
  bound at all. Each backend kind declares one file, copied into the sandbox home
  through a descriptor, so a CLI that rewrites its credential home can do so against its
  own copy. An undeclared kind fails closed.
- **One sandbox layer, not two.** `codex-sol` pins `--sandbox danger-full-access`; the
  dispatcher's Bubblewrap namespace is the enforcing boundary and codex's nested layer
  cannot operate against a scoped read-only workspace root.
- **Live observation in preflight.** A successful non-dry-run dispatch records the pin
  digest and a timestamp; preflight reports `available_live_observed` while still
  spending nothing. Changing any pinned field invalidates the record, and fixture mode
  never writes one.
- **Fixtures now assert observed behaviour**, including refusing an argv that would put
  packet content where a host process list could read it.

### The pipeline, run end to end

Producer, three independent reviewers on byte-identical packets, escalation to the
advisor, a second producer round, and a verification review — 22 live runs, 15 ok and
7 failed, at roughly two dollars.

Three of five findings in that run were defects in main's own evidence, not in the
producer's code: a test log truncated to one line of six, a diff spanning two rounds
while a requirement said the logic was unchanged, and a missing pre-change baseline.
`codex-terra` returned reject and then approve on identical code eighteen minutes
apart, because the packet in between was wrong and got fixed. Reviewers judge the
packet, which makes them a check on the orchestrator as much as on the workers.

### Left unresolved

A model pin is not exclusive, and the opencode `--variant` pin is not enforceable. Both
are stated in `ISSUES.md` with what would close them, and filed as issues #1 and #2.
