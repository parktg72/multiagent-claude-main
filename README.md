# Claude Main Multiagent Harness

Claude Code interactive session is sole main/orchestrator. Six explicit workers run
only through `bin/worker`; no `claude-main` worker and no model fallback exist.

## Exact Runtime Map

| Role | Dispatcher argv core | Filesystem role | Local status |
|---|---|---|---|
| Claude main | `claude --model claude-opus-5 --effort high --add-dir <root>` | interactive main | sandbox startup/local candidate verified; endpoint/auth unverified |
| `codex-sol` | `codex exec --model gpt-5.6-sol -c model_reasoning_effort="high" --sandbox danger-full-access` | only scoped writer | argv verified; real dispatches applied producer edits on 2026-08-04 and 2026-08-05, the latter being the recorded observation |
| `codex-terra` | `codex exec --model gpt-5.6-terra -c model_reasoning_effort="max" --sandbox read-only` | read-only reviewer | argv verified; real dispatches returned schema-valid verdicts on 2026-08-04 and 2026-08-05 |
| `agy` | `agy --model gemini-3.1-pro-high --effort high --mode plan --sandbox --disable-slash-commands --add-dir /input --output-format json ... --print <instruction>` | read-only reviewer | argv and catalog verified; real dispatches returned schema-valid verdicts on 2026-08-04 and 2026-08-05, the latter being the recorded observation |
| `codex-luna` | `codex exec --model gpt-5.6-luna -c model_reasoning_effort="max" --sandbox read-only` | read-only reviewer | argv contract verified; the pin is a human reading of the codex model cache, which advertises `max` among its reasoning levels; no dispatch has run on this pin |
| `deepseek-reviewer` | `opencode run --model opencode/deepseek-v4-flash --variant max --format json --dir /workspace <message> --file /input/...` | read-only reviewer | argv and catalog verified against provider `opencode`; a real dispatch returned a schema-valid verdict on 2026-08-06; `--variant` is not enforceable, and this is the first pin whose catalog also offers other variants, `low` and `high` |
| `fable-advisor` | `claude --model claude-fable-5 --print --tools "" --no-session-persistence` | tools disabled advisor | argv verified; a real dispatch returned a schema-valid verdict on 2026-08-05 with no tool use |

Claude local changelog names `claude-opus-5`; launcher pins that exact candidate.
No non-billable endpoint-acceptance resolver exists; local catalogs verify only
advertised pins, so endpoint acceptance remains unverified until a separately approved
paid call.

The one opencode worker is pinned to `max` by explicit human decision. DeepSeek V4
Flash's catalog also offers `low` and `high`, and `max` was chosen anyway, knowing the
CLI cannot confirm which variant ran — recorded as a decision in `ISSUES.md` rather than
left as an oversight. The dispatcher never swaps a variant on its own, and
`build_inner_command` refuses to build any variant but `max` whatever the configuration
says.

`codex-luna` has no catalog probe at all. The codex CLI exposes no non-interactive
model listing, so preflight verifies its argv contract and nothing more, and the pin
rests on a human reading of the local model cache. That is a weaker guarantee than the
AGY and opencode roles carry, and it is stated here rather than implied.

The opencode provider ID is `opencode`; its display name is "OpenCode Zen", and
`opencode --pure models opencode-zen` answers `Provider not found`. Reading that as
"there is no Zen provider" is the wrong inference — the provider exists under a
different token, and `opencode --pure auth list` shows its credential by display name.

For the order of operations when actually running a task, see [USAGE.md](USAGE.md).
How the harness reached its current shape is in [CHANGELOG.md](CHANGELOG.md).
Known gaps between what is pinned and what can be proven are tracked in
[ISSUES.md](ISSUES.md) and filed as
[#1 a model pin is not exclusive](https://github.com/parktg72/multiagent-claude-main/issues/1)
and
[#2 the opencode variant pin is not enforceable](https://github.com/parktg72/multiagent-claude-main/issues/2).
A third,
[#3 codex-sol's live observation rested on the exit code alone](https://github.com/parktg72/multiagent-claude-main/issues/3),
was opened and closed on 2026-08-06; `CHANGELOG.md` has how it was found and fixed.

## Safe Start — No Model Calls

```sh
cd /path/to/multiagent-claude-main   # any location; the harness resolves its own root
bash tests/run.sh
bin/worker preflight --allow-unavailable
bin/claude-main
```

Preflight also reports a `live_dispatch` field per worker. When an earlier real
dispatch on the **same pin** exited zero, the dispatcher records the pin digest and a
timestamp under private runtime storage, and preflight surfaces it as
`status: available_live_observed` with `endpoint: observed_reachable`. Changing model,
effort, variant, sandbox mode, or command invalidates the record, and fixture test
mode never writes one. It stays a historical observation: a provider can retire a
model or expire a token after it was seen working, and preflight still spends nothing
to recheck.

Exit zero is necessary for this record but is not by itself proof that a model
answered: a 2026-08-06 `opencode/kimi-k3` dispatch exited zero with zero tokens and no
text part. Two gates stand behind it. For a reviewer the no-yes-man verdict contract
supplies the missing proof, since a run failing it never reaches
`record_live_observation`. For every backend, `finish_dispatch` reads the token count
the backend reports and withholds the observation on a reported zero — which is what
mattered for `codex-sol`, the one worker with `requires_no_yes_man` false and therefore
no verdict contract behind it.

All four backends report a count, and no two report alike: codex prints it on stderr,
opencode carries per-step counts in its JSONL stdout, and agy and the Claude CLI each
put a `usage` object in their stdout JSON under different key names. An absent count is
treated as unknown, never as zero, so a backend that stops reporting does not read as
one that answered nothing — for a reviewer, whose verdict has already vouched for the
run. Where no verdict contract stands behind a run, an unreadable count is refused too:
a swallowed flag once turned a backend's JSON into prose, erasing the count on a run
that exited zero, and a worker without a second gate has nothing left to catch that.

`preflight` uses local CLI help/catalog/cache inspection and a non-model Bubblewrap
filesystem probe. Every CLI also runs its isolated-network sandbox `--version` startup
probe with fake HOME and no auth mount. `sandbox_startup`, `endpoint`, `auth`, and
`model_acceptance` are separate status fields; startup success never proves provider
acceptance. It does not authenticate a provider or invoke a model.
`bin/claude-main` accepts no passthrough arguments so its model, effort, and project
context cannot be overridden.

`MULTIAGENT_TEST_MODE=1` requires
`MULTIAGENT_TEST_SENTINEL=fixture-only-dispatch`. This test-only pair selects exact
fixtures under `tests/fixtures`; worker dispatch cannot execute a real backend through
it. It never bypasses `--confirm`, an interactive TTY, or typed `APPROVE`.
`MULTIAGENT_TEST_RUNTIME_DIR` additionally relocates private runtime storage to a
per-test directory, so a suite run leaves the real `.runtime` untouched. It requires
that same pair plus an absolute path inside the project; without the pair, or pointing
outside the project, it fails closed instead of redirecting the approval journal.

## Approval Flow

1. Copy `_templates/task.md`, `_templates/context.md`, `_templates/log.md` into a
   task directory. `workers_approved` is only a planned-worker declaration.
2. Create a normal worker input file or reviewer JSON packet from template.
3. Human records authority through dispatcher, not by editing task/log text:

```sh
bin/worker approve --role codex-sol --task tasks/example/task.md --confirm
# protected actions require a second explicit event
bin/worker approve --role codex-sol --task tasks/example/task.md --action git_push --confirm
```

4. Inspect sandbox plan before spending quota:

```sh
bin/worker dispatch --role codex-sol --task tasks/example/task.md \
  --input tasks/example/sol-input.md --dry-run
```

5. Real provider calls additionally require `MULTIAGENT_ALLOW_BILLABLE=1` and one
   explicit auth contract. Because auth reaches a worker sandbox, record a separate
   `secret_access` action first (declare it in `requested_actions`):

```sh
bin/worker approve --role codex-sol --task tasks/example/task.md --action secret_access --confirm
MULTIAGENT_ALLOW_BILLABLE=1 MULTIAGENT_AUTH_DIR=/absolute/auth-dir \
  bin/worker dispatch --role codex-sol --task tasks/example/task.md \
  --input tasks/example/sol-input.md
```

Authoritative records are private append-only JSONL under `.runtime/approvals/`.
`task.md` and `[APPROVAL]` log lines are audit mirrors, never authorization evidence.

An approval binds the tuple task, role, `write_scope`, and target. Two consequences
follow. A task carries one `write_scope`, so a producer round and a review round cannot
be approved from the same control-plane state: set the producer scope, approve,
dispatch, then set `workflow_stage` to `review` with `write_scope` `none` before
approving reviewers. And restoring those exact values dispatches the same worker again
with no new human confirmation, which is intended for repeated rounds inside one task —
split tasks when a round must carry its own approval. Journal records keep the values
they were approved with, so flipping the control plane never rewrites past authority.
`tasks-only` mounts only `task_dir/artifacts/` writable; task control, audit log,
approval journal, inputs, and sibling outputs are hidden from worker sandbox.

## Why AGY Takes Its Prompt Last

`agy --print` consumes the next argument as the prompt and the CLI stops parsing flags
at the first positional, so `--print --model X` silently makes `--model` the prompt and
discards every later flag — including the model pin, the output format, and the verdict
schema. A live run proved it: the reviewer answered with documentation about the
`--model` flag while running an unpinned default model. `--print` therefore comes last,
after every other flag, and its value is an instruction that names the packet path
rather than the packet, keeping the diff and test evidence out of the host process
list. `--add-dir /input` puts that path inside the CLI's workspace.

## Why opencode Takes Its Message First

The opposite rule to AGY's, for the same job. `opencode run --file` is an array option
that consumes every positional after it, so a trailing message becomes a second
attachment path and the run dies with `File not found: <the message>`. The message
therefore precedes `--file`, and `--file` carries two paths: the packet and the verdict
schema. That schema is attached as evidence rather than enforced, because this CLI has
no equivalent of codex `--output-schema` or agy `--json-schema`; a live run returned a
well-formed verdict carrying an undeclared extra key, which `additionalProperties`
false correctly rejected. Its answer also arrives inside a JSONL event stream at
`part.text`, not as a bare JSON object.

## Why opencode Runs Without --pure

A run under `--pure` never reaches a model. Every attempt returned a server-side
`{"type":"error", ... "Unexpected server error"}` with a distinct `ref`, and dropping
the flag made the identical call succeed and report tokens. The failure is total rather
than selective: a free model fails, and so does an unrelated OAuth provider, which rules
out quota, billing, and the OpenCode gateway alone.

The mechanism is not established, and one observation cuts against the obvious guess.
`opencode --pure auth list` still lists every credential, so the flag is not simply
hiding `auth.json`; something else it suppresses is needed by `run` and not by
`auth list`. The error carries no local detail.

`--pure` is kept where it was proven to work and where no session is involved: the
`--pure --version` startup probe and the `--pure models <provider> --verbose` catalog
probe, both of which return authenticated results on a signed-in host. What the flag was
bought for on a run — keeping host configuration and plugins out of a worker — is
already supplied by the sandbox's empty fake home, which is the same reasoning that
removed `--bare` from the Claude advisor below.

## Why the Claude Advisor Runs Without --bare

`--bare` skips hooks, plugins, auto-memory, and CLAUDE.md discovery, but its own help
states that under it "Anthropic auth is strictly ANTHROPIC_API_KEY or apiKeyHelper via
--settings (OAuth and keychain are never read)". An OAuth account therefore cannot
authenticate under `--bare` at all — a live check returned "Not logged in" with valid
credentials present, and `CLAUDE_CODE_OAUTH_TOKEN` did not help. It is removed from the
advisor and from `bin/claude-main`. What it bought is supplied elsewhere: the worker
sandbox hands over an empty fake home, so there are no hooks or plugins to load, and
the remaining gap — CLAUDE.md auto-discovery from the working directory, which would
let a reviewed repository steer its own reviewer — is closed by mounting no target
repository for this backend. The inlined verdict schema also has its `$schema`, `$id`,
and `$comment` annotations stripped, because this CLI rejects a schema whose
meta-schema URL it cannot resolve; every validation keyword survives, and local
enforcement still runs against the source file.

## Why codex Runs With Its Own Sandbox Disabled

`codex-sol` passes `--sandbox danger-full-access`, and that flag name describes the
codex CLI's own layer only. The enforcing boundary is the dispatcher's Bubblewrap
namespace: the worker sees the approved writable scope, a fake home, its input, and
nothing else, so the kernel — not the CLI — decides what it can touch. Running codex's
nested sandbox inside that namespace added no restriction and broke outright, because
that nested layer creates dot directories at the workspace root while a scoped
`src/**` write surface leaves the root read-only (`bwrap: Can't mkdir /workspace/.git:
Read-only file system`). Reviewers keep `--sandbox read-only`, and
`build_inner_command` fails closed when the pinned sandbox mode and the access model
disagree.

## Sandbox and Auth Boundaries

Dispatcher does **not** bind host `/`, real `$HOME`, task control plane,
`.runtime`, or sibling reviewer runs. It binds minimal system runtime directories,
only needed CLI runtime file/root, fake writable `/home/worker`, explicit input, and
target workspace. Sol gets read-only target plus exact approved writable overlays;
reviewers get target read-only plus packet only.

Network is deliberately **not** isolated because a real model API may need it.
Read-only means filesystem/tool restrictions, not network isolation.

Default sandbox has no provider credentials. `MULTIAGENT_AUTH_DIR` names a host
directory that is **never mounted**. Each backend kind declares the one credential
file it needs, and the dispatcher copies that file into the sandbox home through a
file descriptor with `bwrap --file`:

| kind | source under the auth dir | destination in the sandbox |
|---|---|---|
| `codex` | `codex/auth.json` | `/home/worker/.codex/auth.json` (`CODEX_HOME`) |
| `agy` | `gemini/antigravity-oauth-token` | `/home/worker/.gemini/antigravity-cli/antigravity-oauth-token` |
| `opencode` | `opencode/auth.json` | `/home/worker/.local/share/opencode/auth.json` |
| `claude` | `claude/.credentials.json` | `/home/worker/.claude/.credentials.json` |

Copying rather than binding is what makes this work at all: these CLIs rewrite their
credential home — codex initializes an app-server there and dies with EROFS against a
read-only bind — so the worker gets a writable copy while the host file is never
mounted and cannot be modified from inside. An undeclared kind fails closed rather
than falling back to exposing the whole directory. `MULTIAGENT_ALLOW_AUTH_ENV=1`
forwards a small provider-key allowlist instead. Either path requires declared and
separately approved `secret_access`, and only trusted provider configuration.

## Independent Review

Reviewer packet accepts only neutral `question`, `requirements`, `diff`,
`test_evidence`, and optional artifact paths. Other reviewer conclusions are rejected
by schema and absent from reviewer filesystem. Verdict source-of-truth schema requires
enum verdict, nonempty evidence, separated unverified claims, and at least one
structured substantive risk: failure mode, trigger, impact, evidence/locator, and
mitigation. Semantic truth cannot be automated; schema/anti-generic checks only gate
format and minimum specificity.

Run producer then independent reviewers. Claude main alone reads private raw output
and synthesizes after reviewers finish.

## Verification Scope

`tests/run.sh` runs JSON parsing, Python/shell syntax, invariant self-test, strict
mock argv contracts for every backend, forged approval rejection, scope/isolation,
flock stale-lock behavior, child-124 vs dispatcher-timeout distinction, schema
adversarial cases, PTY approval confirmation, isolated CLI startup/dependency probes,
and a real non-network Bubblewrap visibility probe. Mock tests are not provider
integration verification.

## What the Mock Suite Cannot Prove

Every defect found on this harness so far was invisible to that suite, and the worst
one exited zero while running an unpinned model. Three lived in fixtures that encoded
our own assumption — an argv order, a message position, an output envelope the real
binary never emits — so the fixture agreed with us forever. Those fixtures now assert
against observed CLI behaviour instead, including refusing an argv that would carry
packet content where a host process list could read it.

Preflight proves a flag appears in `--help`; it cannot prove the flag is honoured.
Treat a backend as usable only after one real dispatch, and read `live_dispatch` for
whether that has happened on the current pin. An observation is not earned by the exit
code alone: `finish_dispatch` reads what the backend reports it spent and withholds the
record on a reported zero, which is what a run that answers nothing looks like. Two
limits stay unresolved and are recorded rather than papered over.

A model pin is not exclusive. The Claude CLI reported one small internal
`claude-haiku-4-5` call beside the pinned model. All four backends report what a run
spent in total, but only the Claude CLI breaks that total down **per model**, so on the
other three an equivalent auxiliary call would leave no trace in anything the dispatcher
captures. The gap is the breakdown, not the reporting — a distinction worth stating,
because a first pass at the token check read "no per-model usage" as "no usage" and
covered half the backends it should have.

The opencode `--variant` pin is not enforceable, since an invalid variant is accepted
silently and no event states what ran.

One suite check is not hermetic: the AGY catalog test runs the real `agy models`
command, which reaches a remote model index. It fails only when that CLI runs and
returns a wrong or missing pin; a timeout or an absent local CLI skips it with a
stated reason, because network latency is not a hardening regression. Catalog probes
use a 90 s budget, local help probes 20 s. `bin/worker preflight` remains the
authoritative fail-closed gate for catalog state before any dispatch.

## Source and License

See [NOTICE.md](NOTICE.md). Architecture references `netwaif/multi-agent-starter`
v3.5.0 (MIT); no generator or hook was executed.
