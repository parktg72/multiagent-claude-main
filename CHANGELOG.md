# Changelog

Narrative history. `CLAUDE.md` carries the rules and is loaded every session, so it
stays short; this file records how the rules were arrived at and is read only when
someone wants that. Published commit history begins at a squashed root commit, so the
work below predates what `git log` shows.

## 2026-08-06 — repin to opencode Zen, a sixth worker, and three negatives that were not

The day's request was small: move the opencode reviewer to the Zen provider and let the
pool use Kimi K3 or DeepSeek V4 Pro by role. Both landed. What made the day worth
recording is that neither the repin nor the split is the interesting part — a live
dispatch found an eleventh defect, that defect opened and closed an issue, and the fix
for it was wrong twice, each time because a question that looked settled was not.

The sections below are in the order things happened, and several were superseded within
hours. Where that is so, the section says what later narrowed it rather than being
rewritten — how a rule was arrived at is the point of this file, and a tidy account of
the destination would lose the three wrong turns that produced rule 11.

### The provider that was not a provider

`kimi-reviewer` moved from provider `opencode-go` to `opencode`. The provider's
display name is "OpenCode Zen", but `opencode --pure models opencode-zen` answers
`Provider not found` — the display name is not the token the CLI accepts, and
`opencode-zen` must never appear in a pin. The opencode preflight branch was
generalized from a role-named hardcode of provider and model id to deriving both
from the configured `provider/model` pin, so the repin needed configuration rather
than new branch logic, and the same generalization is what let the sixth worker be
added as configuration too.

The first answer to "does `opencode-zen` exist" was no, on the strength of one command
returning `Provider not found`. It exists; `opencode --pure auth list` names it by
display name and holds its credential. The right token was two commands away. This is
the day's first instance of a pattern that repeats below.

### A sixth worker, and a latent limit going live

**`deepseek-reviewer`**, pinned to `opencode/deepseek-v4-pro` at variant `max`, joined
the pool for enumerative review — per-file audits and long findings lists, where its
384k output ceiling against Kimi's 131k matters. `max` was chosen by explicit human
decision even though this model's catalog also advertises `high`: it is the first
opencode pin where the unenforceable `--variant` limit in `ISSUES.md` (#2) stopped
being latent, since K3's catalog defines only `max` and could never silently
downgrade. Restricting opencode roles to single-variant models was considered and
declined on the same date, so the gap is recorded as a decision rather than an
oversight.

### Defect 11: exit zero, nothing spent

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

*Superseded the same day.* That wording covered reviewers only, which was the whole
answer for about an hour. The next section asks what stands behind the worker that is
not one.

### The exit code stopped being enough

Kimi is a reviewer, which is the only reason defect 11 was caught. That prompted the
obvious question about the one worker that is not: `codex-sol` is the only backend with
`requires_no_yes_man: false`, so nothing stood between a zero-exit run and the
`live_dispatch` record preflight reads as `observed_reachable`. Filed as issue #3.

The fix was cheaper than the issue expected, because the evidence was already on disk.
`finish_dispatch` now reads what the backend says it spent and withholds the observation
on a reported zero. Replayed against all 25 saved runs, it flags exactly one — the
defect-11 run — and no legitimate run. Issue #3 closed the day it opened.

One thing this deliberately does not do: fail the dispatch. The exit code describes the
child, the observation describes the evidence, and conflating them would make a producer
round look broken when main can simply read the tree. An unreported count stays unknown
rather than zero — five saved runs report nothing, and every one of them is a run that
another gate had already failed.

*Narrowed twice after this.* The check as described here read two backends of four, for
the reason the next section gives. And "unreported stays unknown" turned out to be right
only where something else vouches for the run; the last section of this entry says what
replaced it and why the distinction is not cosmetic.

### The check that looked in one place

The first version of that check covered two backends, on the strength of a grep for
`tokens used` across saved stderr. That was the wrong place to look. Every one of the
four backends reports, and no two report alike: codex prints the count on stderr,
opencode carries per-step counts in its JSONL stdout, and agy and the Claude CLI each
put a `usage` object in their single stdout JSON under different key names. The Claude
CLI's `modelUsage` is the very field issue #1 is built on, which should have settled the
question before a grep did. Absence of a string is not absence of a fact.

It reached three documents and a public issue comment before a question caught it. The
narrow lesson is about where usage lives; the broad one is that a negative finding needs
the same evidence standard as a positive one, and a single grep is not that standard.

### What the process cost, and what it caught

Three live dispatches, about seventeen cents: `deepseek-reviewer` once, `kimi-reviewer`
twice because the first attempt was defect 11 and cost nothing. Both reviewers returned
substantive verdicts on a byte-identical packet — approve and conditional — and both
independently found the same structural point, that the pin-format and variant
guarantees live in different functions from the dispatch path.

Re-testing those claims, as rule 10 requires, confirmed two and refuted one. The refuted
one was a claim about cross-provider id collisions, closed by observing that the catalog
probe is already provider-scoped.

The whole-branch review then refuted a claim of main's own: main had reported that
`dispatch()` never calls `backend_preflight`, having tested by searching the function's
source text for the callee's name. It does call it, through `require_backend_ready`. A
substring search cannot see an indirect call. That correction is in the task log, and it
is the third time in one day that a confident negative turned out to be a shallow
lookup — the same shape as the provider that was not a provider, and the usage that was
not absent.

### The reconciliation pass, which was not cosmetic

The work above landed as a dozen commits that each edited whatever document was in
reach. A pass over `README.md`, `USAGE.md`, `CLAUDE.md`, `ISSUES.md`, and
`tasks/INDEX.md` afterwards was expected to be tidying. It found defects instead.

`README.md` contradicted itself: one paragraph still said `codex-sol` had nothing behind
its exit code while another described the token gate that had become exactly that.
`tasks/INDEX.md` filed a closed limit inside its Unresolved list, with no blank line to
separate them, and its header credited live dispatch with all eleven defects when two
were caught reading a CLI's help. Two `README.md` status cells named a dispatch date
that was not the one the live observation records. This entry itself had been appended
below the 2026-08-05 section, breaking the file's newest-first order.

The worst of it was one sentence. Issue 1 said codex, agy, and opencode "do not report
it at all", meaning per-model usage; read quickly it says they report nothing, and that
is precisely the reading that made the first token gate cover two backends instead of
four. The same phrasing had propagated to three files. A document that misleads its own
author is not a documentation problem — it had already caused a coding error hours
earlier, and correcting it turned up the sharper fact that a top-level usage total
excludes the auxiliary call rather than folding it in.

`CLAUDE.md` went the other way and got shorter. It is loaded every session, so it holds
rules and points elsewhere for history; the day's edits had it accumulating thirteen
lines of narrative this file already tells in full. What survived became rule 11: hold a
negative to the same standard as a positive, and say where you looked. Three negatives
failed in one day, which is enough to make it a rule rather than a note.

The GitHub mirrors of issues #1 and #2 were rewritten to match, since `CLAUDE.md` claims
that mirror relationship and a merge is the moment a stale mirror becomes public.

### Rule 11 turned on the session that produced it

Re-auditing the day's negative claims found that most held under a proper method —
`preflight` really does not reach the invariant gate, traced through the call graph and
then demonstrated with a drifted config; the five runs reporting no token count really
had all failed another gate, four of them before the check was reached. But two did not.

`CLAUDE.md` still carried "other backends do not report per-model usage at all". The
sweep that had declared the phrasing gone everywhere searched two literal strings
containing "at all"; this variant lacked those words and survived, in the one file
loaded every session. A string match is not a search for a claim.

The second was a remainder in the day's own fix. An exit-zero run whose output shape
breaks reports no count, which the gate read as unknown and credited — reachable only
where no verdict contract stands behind the run, so only on `codex-sol`, and never seen
there. It had been seen once elsewhere: the defect-4 agy run exited zero with no count,
because a swallowed flag had turned its JSON into prose. Observed mechanism, unobserved
target. Closed by refusing an unreadable count where nothing else vouches for the run,
keyed on the verdict contract rather than on a role name so a future non-reviewer
inherits it. Replayed against every saved run, the new rule reclassifies nothing.

Which is the argument for the rule. Both findings came from re-asking questions that had
already been answered, and the second one is a hole in a fix that had been reviewed,
tested, merged, and used to close an issue the same day.

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
