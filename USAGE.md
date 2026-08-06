# Using the Harness

Practical order of operations. `README.md` explains why the pieces look the way they
do; `CLAUDE.md` is the orchestrator's rulebook. This file is what to actually type.

## Where you run it, and where the work happens

Two directories with different jobs. You do not write code inside this repository.

| Directory | Role |
|---|---|
| this repository | control plane — tasks, approvals, packets, run records |
| your project | the work — `target_repo` points here |

Neither launcher cares about your current directory. `bin/claude-main` derives the root
from its own path, and `bin/worker` resolves a relative `--task` against that root
rather than against the shell's working directory, so `--task tasks/x/task.md` means the
same file from anywhere. Call either by absolute path:

```sh
/home/ptg/multiagent-claude-main/bin/claude-main
```

Or put it on `PATH` once and forget where it lives:

```sh
ln -s /home/ptg/multiagent-claude-main/bin/claude-main ~/.local/bin/claude-main
```

The launcher passes `--add-dir <this repository>` and nothing else, so tell main the
absolute path of the project you want worked on; main writes it into the task's
`target_repo`. Pointing `target_repo` at this repository is refused — a worker may not
edit the control plane that authorizes it.

One catch, and it has bitten: it is the `--task` **argument** that resolves against the
root, not the `bin/worker` **path** you type. Running `bin/worker approve …` from your
home directory gets you `-bash: bin/worker: No such file or directory` before the
harness sees anything. Either `cd` into the repository first, or spell the binary out:

```sh
/home/ptg/multiagent-claude-main/bin/worker approve --role codex-luna \
  --task tasks/<name>/task.md --confirm
```

The `--task tasks/<name>/task.md` in that command means the same file from anywhere.

## Running the suite where Bubblewrap exists

`bin/worker` builds a Bubblewrap namespace for every dispatch, and Bubblewrap is built
on Linux user namespaces. There is no macOS port and no equivalent, so on darwin the
dispatcher fails closed before it reaches a backend:

```
$ bin/worker preflight --allow-unavailable
worker: required executable unavailable: bwrap

$ bash tests/run.sh
Ran 14 tests — FAILED (failures=10)
  worker: sandbox startup probe failed: sandbox runtime binding unavailable
```

Every one of those failures is that single cause. `bin/claude-main` is unaffected — it
is a plain launcher with no sandbox — so the orchestrator half runs anywhere and only
the six workers need Linux.

Do not try to make those tests pass on darwin. Swapping Bubblewrap for `sandbox-exec`
or adding a no-sandbox path would remove the boundary the dispatcher exists to enforce.
`build_inner_command` fails closed on purpose.

### Container recipe

What the image needs, in the order it was installed on `ubuntu:24.04`. This is a record
of what was run, not a Dockerfile that has been built as one file — assemble it however
your tooling prefers.

1. apt: `bubblewrap python3 git ca-certificates procps`, plus `curl xz-utils` for the
   steps below. Bubblewrap and Python are the only ones `tests/run.sh` itself needs.
2. node, for three of the four backend CLIs. Take the tarball matching the **container's**
   architecture, not the host's, from `https://nodejs.org/dist/`, and unpack it into
   `/usr/local` with `--strip-components=1`.
3. `npm i -g @anthropic-ai/claude-code @openai/codex opencode-ai`.
4. `agy` is a Go binary, not an npm package. `https://antigravity.google/cli/install.sh`
   names an updater whose per-platform manifest carries the download URL and a sha512:

   ```sh
   curl -fsSL <updater>/manifests/linux_arm64.json
   # -> {"version": "...", "url": "...", "sha512": "..."}
   ```

   Verify that checksum before installing. `linux_arm64` and `linux_amd64` both answer
   200; `linux_arm64_musl` is 404, so this is glibc only. The tarball holds one file,
   `antigravity`, which installs as `agy`.

Read that install script rather than piping it to a shell.

Without a CLI present, its role reports `unavailable_fail_closed` with
`sandbox_startup: unavailable`, and four `test_hardening.py` cases error on
`require_binary`. Installing all four clears both.

### Bubblewrap needs a privileged container

Under OrbStack only `--privileged` works. Tested and rejected:

| docker flags | result |
|---|---|
| (default) | `No permissions to create new namespace` |
| `--security-opt seccomp=unconfined` | `Can't mount proc on /newroot/proc: Operation not permitted` |
| `+ --cap-add SYS_ADMIN` | same |
| `+ --security-opt apparmor=unconfined` | same |
| `--privileged` | works |

Not an exhaustive matrix — `--security-opt systempaths=unconfined` is untested and is the
usual answer to that `proc` denial, since Docker masks `/proc` paths by default.

**Put no credentials in that container.** No `MULTIAGENT_AUTH_DIR`, no
`MULTIAGENT_ALLOW_AUTH_ENV=1`, no `MULTIAGENT_ALLOW_BILLABLE=1`. A privileged container
can reach the VM, which is the exposure the copy-not-bind credential design exists to
prevent. Clone the repository fresh inside the container rather than bind-mounting your
working copy read-write, so a root-owned `.runtime` never lands in it.

### What a credential-free run should report

```
tests/run.sh   test_worker.py 14/14 OK
               test_hardening.py OK (skipped=1)
```

The skip is `local agy catalog probe unavailable (exit)` — a signed-out `agy` cannot
answer the pin question, so the test skips rather than failing. That is the expected
outcome here, not a masked failure.

`preflight` reports `model_calls: 0`, every role's `cli_contracts.ok` true, every
`sandbox_startup` available, and `scope-sandbox available`. The three codex roles reach
`endpoint_unverified`, which is the best status any role can reach without a billable
call. The rest fail closed for reasons that are all absence of credentials rather than
pin drift: `agy` at `catalog_unavailable`, `deepseek-reviewer` at `variant_unavailable`
(see `ISSUES.md` and issue #5 — a signed-out opencode returns a smaller catalog rather
than an error), and `claude-main` at `candidate_unavailable`, since a fresh CLI install
has no local changelog to read a candidate from.

Reading any of those as a broken pin is the mistake this section exists to prevent.

## Who does what

| Step | Who |
|---|---|
| Write the task, build packets, dispatch, verify, synthesize | Main |
| **Type `APPROVE`**, create the credential copy | **Human only** |

Approval requires a real TTY and the exact word `APPROVE`. Main cannot supply it, and
that is the point: it is the one human gate in the system.

## One review, start to finish

**1. Copy the credential for the backends you plan to use.**

```sh
mkdir -p /home/ptg/multiagent-auth/codex
chmod 700 /home/ptg/multiagent-auth /home/ptg/multiagent-auth/codex
cp -p /home/ptg/.codex/auth.json /home/ptg/multiagent-auth/codex/
```

| Backend kind | Source | Destination under the auth dir |
|---|---|---|
| codex | `~/.codex/auth.json` | `codex/auth.json` |
| agy | `~/.gemini/antigravity-cli/antigravity-oauth-token` | `gemini/antigravity-oauth-token` |
| opencode | `~/.local/share/opencode/auth.json` | `opencode/auth.json` |
| claude | `~/.claude/.credentials.json` | `claude/.credentials.json` |

**2. Ask main for the work.** Main creates the task directory, fills the Control Plane,
writes the worker input or reviewer packet, and hands back the approval commands.

**3. Approve — two records per role.**

```sh
bin/worker approve --role codex-sol --task tasks/<name>/task.md --confirm
bin/worker approve --role codex-sol --task tasks/<name>/task.md --action secret_access --confirm
```

`secret_access` is required whenever a credential reaches the sandbox, which is every
real call. Declare it in `requested_actions` first.

**4. Main runs the rest**: a `--dry-run` to show the mount plan, then the real dispatch
with `MULTIAGENT_ALLOW_BILLABLE=1` and `MULTIAGENT_AUTH_DIR=/home/ptg/multiagent-auth`,
then verification of the result against the task's acceptance criteria.

**5. Remove the credential copy when the session's work is done.**

```sh
rm -rf /home/ptg/multiagent-auth
```

## When a dispatch fails

It will. Eight of the twenty-five live runs so far failed, and nine of the eleven
defects in `tasks/INDEX.md` were found that way — the other two were caught reading a
CLI's own help before spending anything. The order that works:

**Read the raw output before theorising.** It is at
`tasks/<name>/workers/<role>/runs/<run-id>/`, kept out of git on purpose. The `log.md`
line names the run id.

**Check `tasks/INDEX.md` first.** It maps every defect found so far to the run holding
its evidence. A symptom that already happened does not need re-diagnosing.

**Retry once before diagnosing.** On 2026-08-06 a `kimi-reviewer` dispatch exited zero
having answered nothing — `reason: "unknown"`, zero tokens, no text part, sixty-three
seconds — and an identical re-dispatch immediately after succeeded. Approval binds the
tuple, not the attempt, so a retry needs no new confirmation. A failure that reproduces
is a defect; one that does not is the provider having a bad minute, and both are worth
recording either way.

**Confirm the run actually counted.** A dispatch can exit zero and still not earn its
live observation: `finish_dispatch` withholds the record when the backend reports zero
tokens spent, logging `live_observation: withheld_zero_reported_tokens`. Check with

```sh
bin/worker preflight | python3 -c "import json,sys; print(json.load(sys.stdin)['backends']['<role>']['live_dispatch'])"
```

`succeeded` with today's run id means the pin is credited. `none_recorded_for_this_pin`
means it is not — either nothing has run on this pin, or what ran did not count.

## Producer then reviewers

A task carries exactly one `write_scope`, so the two stages cannot be approved from the
same control-plane state.

```
producer stage   workflow_stage: producer   write_scope: src/**, tests/**
                 approve codex-sol, dispatch, main runs the tests itself
review stage     workflow_stage: review     write_scope: none
                 approve reviewers, dispatch each with an identical packet
synthesis        main reads the raw verdicts and decides
```

Reviewers never see each other's verdicts or main's opinion. If they disagree on
something material, escalate to `fable-advisor` and let main make the call.

## Three gates that will stop you

- A role absent from `workers_approved` is refused even after a human types `APPROVE`.
  Planning declaration comes first, then authority.
- Read-only workers cannot be approved while `write_scope` is a producer scope. Flip
  the stage first.
- A missing credential file fails the dispatch closed rather than falling back to
  mounting the auth directory.

## Repeat rounds

Approval binds the tuple task, role, `write_scope`, target. Restoring those exact
values dispatches the same worker again with no new confirmation — convenient for a
second producer round inside one task. Split tasks when a round must carry its own
approval.

## Choosing workers

| Need | Worker |
|---|---|
| Implementation, debugging, scoped edits | `codex-sol` (the only writer) |
| Independent validation, regression analysis | `codex-terra` |
| Third-party perspective, operations, multimodal | `agy` |
| Cross-cutting impact, one contested verdict | `codex-luna` |
| Per-file audits and long enumerative findings | `deepseek-reviewer` |
| Design judgement, ambiguity, security risk | `fable-advisor` |

Two reviewers earn their cost, and the two ways that pays out are both worth having. On
one identical packet three reviewers once found three *different* failure modes, and one
of them found a defect in main's own evidence. On another, two reviewers independently
found the *same* structural point from different angles — which is the weaker-looking
result and the more useful one, because agreement between isolated readers is evidence
in a way one reader's confidence is not.

Pick the smallest set that covers the question. A second opinion on a one-line change
buys little; on a change to the approval or sandbox path it buys a lot.

## Cost

Roughly 8k–25k tokens per dispatch. Twenty-five live runs — a full day of building, one
complete pipeline, and one repin re-verification — came to a little over two dollars.
The repin's three dispatches were about seventeen cents, one of which was free because
it failed having spent nothing.

## The habit that matters most

Packet quality is review quality. Reviewers judge the packet, not the repository. Of
five findings in the pipeline run, three were defects in main's evidence rather than in
the code: a truncated test log, a diff spanning two rounds, and a missing pre-change
baseline. Each earned a correct rejection. Send full command output, scope the diff to
one change, and include the before state whenever a requirement claims something stays
unchanged.
