# OpenCode Zen Repin and Reviewer Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the opencode reviewer from provider `opencode-go` to `opencode` (OpenCode Zen), and add a sixth worker `deepseek-reviewer` pinned to `opencode/deepseek-v4-pro` variant `max`.

**Architecture:** The dispatcher's opencode preflight is currently keyed on the role name `kimi-reviewer` and hardcodes both the provider `opencode-go` and the model id `kimi-k3`. This plan generalizes that branch to key on `kind == "opencode"` and derive the provider and model id from the configured `provider/model` token, so a second opencode worker needs configuration and pins rather than new branch logic. The `--variant max` literal in the argv builder stays hardcoded on purpose.

**Tech Stack:** Python 3 standard library, `unittest`, Bubblewrap, opencode CLI 1.18.14.

## Global Constraints

- Modify only this repository's own tree (`./**`). The design spec is `docs/superpowers/specs/2026-08-06-opencode-zen-reviewer-split-design.md`.
- Provider ID is `opencode` (display name "OpenCode Zen"). `opencode-zen` is not a valid provider token and must never appear in a model pin.
- Both opencode workers pin variant `max`. No fallback, no automatic variant substitution.
- `bin/worker.py:1152`'s `if effort != "max": raise SchemaError` stays a literal. Do not make it read from config.
- Never write a credential value into a log, task file, fixture, or commit.
- Model calls stay off until Task 5, which needs `MULTIAGENT_ALLOW_BILLABLE=1` and its own approval records.
- Run the full suite with `bash tests/run.sh` and the gate with `bin/check-invariants` before every commit.

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `_shared/backends.json` | worker pins, the only place a model token is declared | 1, 2 |
| `bin/worker.py` | preflight, invariants, CLI contracts, argv | 1, 2 |
| `tests/fixtures/mock-bin/opencode` | strict mock CLI: catalog + argv contract | 1, 2 |
| `tests/test_worker.py` | dispatch happy paths, preflight status | 1, 2 |
| `tests/test_hardening.py` | variant refusal, preflight per-role assertions | 1, 2 |
| `_shared/routing.md` | which worker a round should pick | 2 |
| `CLAUDE.md`, `README.md`, `USAGE.md` | operator-facing pins and routing | 1, 2, 5 |
| `ISSUES.md` | the unenforceable-variant limit | 3 |
| `tasks/zen-repin-live/` | the live re-verification task | 5 |
| `tasks/INDEX.md` | live dispatch record index | 5 |

---

### Task 1: Repin `kimi-reviewer` to provider `opencode`

Keeps the worker inventory at five. Generalizes the opencode preflight so Task 2 adds configuration only.

**Files:**
- Modify: `_shared/backends.json:55`
- Modify: `bin/worker.py:1514-1526` (`opencode_kimi_metadata`)
- Modify: `bin/worker.py:1621-1627` (preflight opencode branch)
- Modify: `bin/worker.py:1650` (pins table)
- Modify: `bin/worker.py:1712` (README argv invariant)
- Modify: `README.md:14`, `README.md:19-23`, `CLAUDE.md:18-21`
- Test: `tests/fixtures/mock-bin/opencode`, `tests/test_hardening.py:881`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `opencode_model_metadata(output: str, model_id: str) -> dict[str, Any] | None` — replaces `opencode_kimi_metadata`. Task 2 relies on this name and on the preflight branch being keyed on `kind == "opencode"` rather than a role name.

- [ ] **Step 1: Make the mock CLI refuse the old provider**

This is the failing test's mechanism: the mock stops answering for `opencode-go`, so any surviving hardcode fails loudly instead of silently probing the wrong provider.

Replace lines 33-39 of `tests/fixtures/mock-bin/opencode`:

```python
    # Only the pinned provider answers. A stale `opencode-go` probe must fail here
    # rather than quietly returning a catalog for the provider we moved off.
    if args == ["--pure", "models", "opencode", "--verbose"]:
        print("opencode/kimi-k3")
        print(json.dumps({"id": "kimi-k3", "variants": {"max": {"reasoningEffort": "max"}}}))
        return 0
    if len(args) >= 3 and args[:2] == ["--pure", "models"]:
        return 64
    # The real CLI treats --file as an array option that consumes trailing
    # positionals, so the message must come before it. Refuse any other order.
    expected = ["--pure", "run", "--model", "opencode/kimi-k3", "--variant", "max", "--format", "json", "--dir", "/workspace"]
```

Also update the module docstring on line 2:

```python
"""Strict test-only OpenCode CLI; its catalog mirrors the pinned Zen K3 max variant."""
```

- [ ] **Step 2: Run the suite to verify it fails**

Run: `bash tests/run.sh`
Expected: FAIL. `test_preflight_uses_complete_mock_cli_contracts_without_model_call` reports `unavailable_fail_closed` for `kimi-reviewer`, because `worker.py` still probes `opencode-go`.

- [ ] **Step 3: Generalize the metadata reader**

Replace `bin/worker.py:1514-1526` in full:

```python
def opencode_model_metadata(output: str, model_id: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for index, character in enumerate(output):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(output[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("id") == model_id:
            return value
    return None
```

- [ ] **Step 4: Key the preflight branch on the backend kind**

Replace `bin/worker.py:1621-1627` (the `if role == "kimi-reviewer":` block) in full:

```python
    if kind == "opencode":
        # The pin is `provider/model`. Deriving both from it keeps a second opencode
        # worker a configuration change instead of another role-named branch.
        provider, separator, model_id = str(backend.get("model", "")).partition("/")
        if not separator or not re.fullmatch(r"[a-z0-9._-]{1,64}", provider) or not model_id:
            return BackendPreflight("unavailable_fail_closed", "opencode model pin must be provider/model", startup, "unverified", "unverified", "model_unavailable")
        ok, catalog = cached_local_probe(active_cache, f"opencode-models:{provider}", ["opencode", "--pure", "models", provider, "--verbose"])
        metadata = opencode_model_metadata(catalog, model_id) if ok else None
        variants = metadata.get("variants", {}) if isinstance(metadata, dict) else {}
        if not isinstance(variants, dict) or backend.get("effort") not in variants:
            return BackendPreflight("unavailable_fail_closed", "pinned opencode variant absent from catalog; automatic variant substitution forbidden", startup, "unverified", "unverified", "variant_unavailable")
        return BackendPreflight("available_pending_auth", "exact opencode model and variant plus CLI contract verified; endpoint/auth unverified", startup, "unverified", "unverified", "variant_verified")
```

The `provider` regex matters: the value reaches a `subprocess` argv, and although the dispatcher never evaluates shell text, a config file should not be able to put an arbitrary string in an argument vector.

- [ ] **Step 5: Repin the model token**

`_shared/backends.json:55`:

```json
      "model": "opencode/kimi-k3",
```

`_shared/backends.json:63`:

```json
      "preflight": "opencode Zen catalog must expose the pinned max variant; --agent is not authoritative; the dispatcher never swaps variants on its own"
```

`bin/worker.py:1650`:

```python
        "kimi-reviewer": ("opencode", "opencode", "opencode/kimi-k3", "max", "read-only"),
```

`bin/worker.py:1712`, inside `required_argv`:

```python
            "opencode --pure run --model opencode/kimi-k3 --variant max",
```

- [ ] **Step 6: Update the hardening variant test**

`tests/test_hardening.py:887` and `:890` carry the model literal:

```python
                backend = {"kind": "opencode", "model": "opencode/kimi-k3", "effort": effort}
```

```python
        pinned = {"kind": "opencode", "model": "opencode/kimi-k3", "effort": "max"}
```

- [ ] **Step 7: Add a test that the provider prefix is honoured**

Append to `tests/test_hardening.py`, in `HardenedDispatcherTests`, next to
`test_kimi_command_refuses_any_variant_other_than_the_pinned_max`.

`backend_preflight` shells out twice before it reaches the provider check — once for
`runtime_startup_probe`, once for `cli_contracts` — and this class only puts `MOCK_BIN`
on `PATH` for its subprocess helpers, not for in-process calls. Both are patched so the
test exercises the parsing rule and nothing else:

```python
    def test_opencode_preflight_fails_closed_on_a_pin_without_a_provider_prefix(self) -> None:
        # A bare model id would make the dispatcher probe an empty provider. It must
        # refuse rather than guess which provider was meant.
        startup = worker.StartupProbe("available", "test stub")
        contracts = {"kimi-reviewer": {"ok": True, "missing": []}}
        with mock.patch.object(worker, "runtime_startup_probe", return_value=startup), \
             mock.patch.object(worker, "cli_contracts", return_value=contracts):
            probe = worker.backend_preflight(
                "kimi-reviewer",
                {"kind": "opencode", "command": "opencode", "model": "kimi-k3", "effort": "max"},
            )
        self.assertEqual(probe.status, "unavailable_fail_closed")
        self.assertEqual(probe.model_acceptance, "model_unavailable")
        self.assertIn("provider/model", probe.detail)
```

`StartupProbe` is a two-field dataclass, `(status, detail)`, defined at
`bin/worker.py:156`.

- [ ] **Step 8: Run the suite and the invariant gate**

Run: `bash tests/run.sh && bin/check-invariants`
Expected: PASS on both.

- [ ] **Step 9: Confirm the real catalog agrees, spending nothing**

Run: `bin/worker preflight | python3 -c "import json,sys; b=json.load(sys.stdin)['backends']['kimi-reviewer']; print(b['status'], b['model'], b['model_acceptance'], b['live_dispatch'])"`
Expected: `available_pending_auth opencode/kimi-k3 variant_verified {'status': 'none_recorded_for_this_pin'}`

The `none_recorded_for_this_pin` is the point: `pin_digest` hashes the model, so the old observation no longer credits this pin. Task 5 restores it.

- [ ] **Step 10: Update the operator docs to state the pin and its unverified status**

`CLAUDE.md:18-21`, replacing the `kimi-reviewer` bullet:

```markdown
- `kimi-reviewer` — `opencode/kimi-k3`, variant `max`; read-only reviewer on the
  OpenCode Zen provider. `opencode` is the provider ID; `opencode-zen` is only its
  display name and is not a valid pin. `max` is the pinned variant by explicit human
  decision: the opencode catalog defines no other variant for K3. Automatic variant
  substitution stays forbidden; when the pinned variant is absent, preflight fails
  closed.
```

`README.md:14`, replacing the `kimi-reviewer` row:

```markdown
| `kimi-reviewer` | `opencode --pure run --model opencode/kimi-k3 --variant max --format json --dir /workspace <message> --file /input/...` | read-only reviewer | argv and catalog verified against provider `opencode`; the 2026-08-04 live observation was invalidated by the repin and has not yet been replaced; `--variant` is not enforceable |
```

Append to the paragraph at `README.md:19-23`:

```markdown
The opencode provider ID is `opencode`; its display name is "OpenCode Zen", and
`opencode --pure models opencode-zen` answers `Provider not found`. Reading that as
"there is no Zen provider" is the wrong inference — the provider exists under a
different token, and `opencode --pure auth list` shows its credential by display name.
```

- [ ] **Step 11: Commit**

```bash
git add _shared/backends.json bin/worker.py tests/fixtures/mock-bin/opencode tests/test_hardening.py README.md CLAUDE.md
git commit -m "Repin kimi-reviewer to the opencode Zen provider

The opencode preflight branch now derives provider and model id from the
provider/model pin instead of hardcoding opencode-go and kimi-k3, so a second
opencode worker is configuration rather than branch logic. The repin invalidates
the recorded live observation, which the README now says outright."
```

---

### Task 2: Add the `deepseek-reviewer` worker

**Files:**
- Modify: `_shared/backends.json:64` (insert a block after `kimi-reviewer`)
- Modify: `bin/worker.py:1573-1576` (`cli_contracts`), `:1638` (`expected`), `:1650` (pins), `:1712` (`required_argv`), `:1937` (preflight loop)
- Modify: `_shared/routing.md`, `_shared/invariants.md:5`, `USAGE.md:121`, `CLAUDE.md`, `README.md`
- Test: `tests/fixtures/mock-bin/opencode`, `tests/test_worker.py`, `tests/test_hardening.py`

**Interfaces:**
- Consumes: `opencode_model_metadata(output, model_id)` and the `kind == "opencode"` preflight branch, both from Task 1.
- Produces: role name `deepseek-reviewer`, pinned `opencode/deepseek-v4-pro` variant `max`, `access` `read-only`. Task 5 dispatches this exact role name.

- [ ] **Step 1: Write the failing dispatch test**

Append to `tests/test_worker.py`, next to `test_kimi_strict_mock_happy_path_uses_max_without_agent`:

```python
    def test_deepseek_strict_mock_happy_path_uses_max(self) -> None:
        self.write_task("deepseek-reviewer", "none")
        self.approve("deepseek-reviewer")
        result = self.dispatch("deepseek-reviewer")
        self.assertEqual(result.returncode, 0, result.stderr)
        input_path = self.input_for("deepseek-reviewer")
        dry = self.call("dispatch", "--role", "deepseek-reviewer", "--task", str(self.task), "--input", str(input_path), "--dry-run")
        self.assertEqual(dry.returncode, 0, dry.stderr)
        command = json.loads(dry.stdout)["command"]
        pairs = list(zip(command, command[1:]))
        self.assertIn(("--model", "opencode/deepseek-v4-pro"), pairs)
        self.assertIn(("--variant", "max"), pairs)
        # The catalog offers `high` for this model. The dispatcher must never pick it.
        self.assertNotIn("high", command)
```

- [ ] **Step 2: Write the failing variant-refusal test**

DeepSeek is the first pin whose catalog exposes a second variant, so the refusal has real work to do here. Append to `tests/test_hardening.py`:

```python
    def test_deepseek_command_refuses_high_even_though_the_catalog_offers_it(self) -> None:
        # ISSUES #2: the CLI accepts any --variant silently, so the dispatcher is the
        # only thing standing between a config edit and a downgraded review.
        binding = worker.RuntimeBinding(prefix=["opencode"], mounts=[])
        for effort in ("high", "low", None):
            with self.subTest(effort=effort):
                backend = {"kind": "opencode", "model": "opencode/deepseek-v4-pro", "effort": effort}
                with self.assertRaises(worker.SchemaError):
                    worker.build_inner_command(backend, binding, True, "/input/review-input.json")
        pinned = {"kind": "opencode", "model": "opencode/deepseek-v4-pro", "effort": "max"}
        command = worker.build_inner_command(pinned, binding, True, "/input/review-input.json")
        self.assertIn(("--variant", "max"), list(zip(command, command[1:])))
```

- [ ] **Step 3: Run both tests to verify they fail**

Run: `python3 -m unittest tests.test_worker.WorkerTests.test_deepseek_strict_mock_happy_path_uses_max tests.test_hardening -k deepseek -v`
Expected: FAIL. The dispatch test fails first at `approve`, because `deepseek-reviewer` is not a known role.

- [ ] **Step 4: Teach the mock CLI both models**

Replace the catalog and argv-contract section of `tests/fixtures/mock-bin/opencode` (the block edited in Task 1, Step 1) with:

```python
    # Both pinned models live under the same Zen provider. DeepSeek deliberately
    # advertises `high` as well, so the fixture proves the dispatcher declines it.
    if args == ["--pure", "models", "opencode", "--verbose"]:
        print("opencode/kimi-k3")
        print(json.dumps({"id": "kimi-k3", "variants": {"max": {"reasoningEffort": "max"}}}))
        print("opencode/deepseek-v4-pro")
        print(json.dumps({"id": "deepseek-v4-pro", "variants": {"high": {"reasoningEffort": "high"}, "max": {"reasoningEffort": "max"}}}))
        return 0
    if len(args) >= 3 and args[:2] == ["--pure", "models"]:
        return 64
    if args[:3] != ["--pure", "run", "--model"] or args[3] not in {"opencode/kimi-k3", "opencode/deepseek-v4-pro"}:
        return 64
    # The real CLI treats --file as an array option that consumes trailing
    # positionals, so the message must come before it. Refuse any other order.
    expected = ["--pure", "run", "--model", args[3], "--variant", "max", "--format", "json", "--dir", "/workspace"]
```

And change `verdict()`'s summary on line 21 so it does not name one model:

```python
        "summary": "Mock OpenCode fixture verdict for contract tests only.",
```

- [ ] **Step 5: Add the backend configuration**

Insert into `_shared/backends.json` after the `kimi-reviewer` block's closing `},` (line 64):

```json
    "deepseek-reviewer": {
      "kind": "opencode",
      "command": "opencode",
      "model": "opencode/deepseek-v4-pro",
      "effort": "max",
      "access": "read-only",
      "timeout_s": 900,
      "requires_no_yes_man": true,
      "fallbacks": [],
      "fallback_policy": "forbid",
      "roles": ["enumerative-review", "per-file-audit", "independent-review"],
      "preflight": "opencode Zen catalog must expose the pinned max variant; this model also advertises high, which the dispatcher must never build"
    },
```

- [ ] **Step 6: Register the role in the dispatcher**

`bin/worker.py:1573-1576`, after the `kimi-reviewer` entry inside `cli_contracts`:

```python
        "deepseek-reviewer": {
            "ok": open_ok,
            "missing": all_flags_present(open_help, ("--pure", "--model", "--variant", "--format", "--dir", "--file")),
        },
```

`bin/worker.py:1638`:

```python
    expected = {"codex-sol", "codex-terra", "agy", "kimi-reviewer", "deepseek-reviewer", "fable-advisor"}
```

`bin/worker.py:1639-1640`, the message on the next line:

```python
        issues.append("worker inventory must contain exactly six named workers and no claude-main worker")
```

`bin/worker.py:1650`, after the `kimi-reviewer` pin:

```python
        "deepseek-reviewer": ("opencode", "opencode", "opencode/deepseek-v4-pro", "max", "read-only"),
```

`bin/worker.py:1712`, after the kimi entry in `required_argv`:

```python
            "opencode --pure run --model opencode/deepseek-v4-pro --variant max",
```

`bin/worker.py:1937`:

```python
    for role in ("codex-sol", "codex-terra", "agy", "kimi-reviewer", "deepseek-reviewer", "fable-advisor"):
```

- [ ] **Step 7: Run the two tests to verify they pass**

Run: `python3 -m unittest tests.test_worker.WorkerTests.test_deepseek_strict_mock_happy_path_uses_max -v`
Expected: PASS

Run: `python3 -m unittest tests.test_hardening -k deepseek -v`
Expected: PASS

- [ ] **Step 8: Extend the per-role preflight assertions**

`tests/test_hardening.py:429`:

```python
        for role in ("claude-main", "codex-sol", "codex-terra", "agy", "kimi-reviewer", "deepseek-reviewer", "fable-advisor"):
```

After `tests/test_hardening.py:434`:

```python
        self.assertEqual(report["backends"]["deepseek-reviewer"]["model_acceptance"], "variant_verified")
```

After `tests/test_worker.py:252`:

```python
        self.assertEqual(report["backends"]["deepseek-reviewer"]["status"], "available_pending_auth")
```

- [ ] **Step 9: Run the full suite and the invariant gate**

Run: `bash tests/run.sh && bin/check-invariants`
Expected: PASS on both.

- [ ] **Step 10: Confirm against the real catalog, spending nothing**

Run: `bin/worker preflight | python3 -c "import json,sys; b=json.load(sys.stdin)['backends']; [print(r, b[r]['status'], b[r]['model'], b[r]['model_acceptance']) for r in ('kimi-reviewer','deepseek-reviewer')]"`
Expected: both `available_pending_auth ... variant_verified`, with models `opencode/kimi-k3` and `opencode/deepseek-v4-pro`.

- [ ] **Step 11: Update the routing docs**

`_shared/routing.md`, replacing the `kimi-reviewer` bullet under **Worker Selection**:

```markdown
- Cross-cutting impact and contested single verdicts: `kimi-reviewer`
  (`opencode/kimi-k3`), only if its exact `max` variant preflight passes.
- Enumerative review whose answer is long — per-file audits, long findings lists,
  bulk low-risk checks: `deepseek-reviewer` (`opencode/deepseek-v4-pro`), whose
  output ceiling is 384k against Kimi's 131k at a quarter of the output price.
  Its catalog also advertises `high`; the dispatcher never builds it. For both,
  an absent pinned variant disables the route instead of downgrading it.
```

`CLAUDE.md`, after the `kimi-reviewer` bullet:

```markdown
- `deepseek-reviewer` — `opencode/deepseek-v4-pro`, variant `max`; read-only
  enumerative reviewer on the same Zen provider. Unlike K3 this model's catalog
  advertises a second variant, `high`, so it is the first pin where the
  unenforceable `--variant` limit in `ISSUES.md` is an active exposure rather than a
  latent one. `max` is an explicit human decision recorded on 2026-08-06.
```

Also change `README.md:3` from "Five explicit workers" to "Six explicit workers", and
`_shared/invariants.md:5` from "Exactly five named workers" to "Exactly six named
workers". That file documents what `bin/check-invariants` enforces; no code reads it, so
a stale count there is invisible to the gate and must be caught by eye.

Leave `CLAUDE.md:109` ("all five workers have completed a real dispatch") alone — it is
a statement about live verification, not inventory, and it stays false until Task 5
makes it true. Task 5 Step 10 owns that line.

`USAGE.md:121`, replacing the third row:

```markdown
| Third-party perspective, operations, multimodal | `agy` |
| Cross-cutting impact, one contested verdict | `kimi-reviewer` |
| Per-file audits and long enumerative findings | `deepseek-reviewer` |
```

- [ ] **Step 12: Run the invariant gate again and commit**

Run: `bash tests/run.sh && bin/check-invariants`
Expected: PASS on both. `check-invariants` reads `README.md` for the argv map, so a missed doc edit fails here rather than in review.

```bash
git add _shared/backends.json bin/worker.py tests/ _shared/routing.md CLAUDE.md README.md USAGE.md
git commit -m "Add deepseek-reviewer as the sixth worker

opencode/deepseek-v4-pro at variant max, on the same Zen provider and the same
credential as kimi-reviewer. It is the first pin whose catalog offers a second
variant, so the argv builder's max literal now has a model it could actually
downgrade; a test asserts it refuses."
```

---

### Task 3: Rewrite `ISSUES.md` #2 from latent to active

**Files:**
- Modify: `ISSUES.md:50-90`
- Modify: `_shared/capability-policy.md:9`

**Interfaces:**
- Consumes: the `deepseek-reviewer` role from Task 2.
- Produces: nothing code-facing. Task 5 cites this wording when reporting the dispatch.

- [ ] **Step 0: Correct the capability policy table**

Found by Task 2's reviewer, in no earlier task's file list. `_shared/capability-policy.md:9`
still names the pre-repin pin and the table has no row for the sixth worker, so the
worker-facing policy set states a pin the dispatcher would refuse. Replace line 9 with:

```markdown
| `kimi-reviewer` | `opencode/kimi-k3`, `max` | read-only | cross-cutting impact, one contested verdict |
| `deepseek-reviewer` | `opencode/deepseek-v4-pro`, `max` | read-only | per-file audits, long enumerative findings |
```

Verify no stale token survives outside history:

Run: `grep -rn "opencode-go" _shared/ README.md CLAUDE.md USAGE.md`
Expected: no hit. `ISSUES.md:60` and `:68` keep theirs — they are transcripts of probes
that really were run against `opencode-go`, and rewriting them would falsify evidence.
`CHANGELOG.md` and the 2026-08-02/08-03 plans keep theirs for the same reason.

- [ ] **Step 1: Replace the Affects and Evidence lines**

`ISSUES.md:53-55`:

```markdown
**Status:** open, upstream behaviour; exposure became active on 2026-08-06
**Affects:** `kimi-reviewer` (`opencode/kimi-k3`, variant `max`) and
`deepseek-reviewer` (`opencode/deepseek-v4-pro`, variant `max`)
**Evidence:** `tasks/kimi-live-test/task.md` records the original probe under Known
Limitation; the catalog reading that activated it is in
`docs/superpowers/specs/2026-08-06-opencode-zen-reviewer-split-design.md`
```

- [ ] **Step 2: Replace the "Why it matters today" paragraph**

Replace the paragraph beginning `**Why it matters today: barely.**` in full:

```markdown
**Why it matters now.** It stopped being latent. When this was filed, the only pinned
opencode model was K3, whose catalog defines exactly one variant, so the pinned value
and any provider default coincided and the gap could not bite. On 2026-08-06 the pool
gained `deepseek-reviewer`, pinned to `opencode/deepseek-v4-pro`, whose catalog
advertises both `high` and `max`. A silent downgrade from `max` to `high` is now
representable, would change the reasoning budget of a review verdict, and would leave
no trace in anything the dispatcher captures.

The dispatcher, not the CLI, is what prevents it: `build_inner_command` raises
`SchemaError` for any variant other than the literal `max`, independent of what
`_shared/backends.json` says, and `test_deepseek_command_refuses_high_even_though_the_catalog_offers_it`
holds that line. This bounds the exposure to what a provider-side default could do; it
does not observe what actually ran.
```

- [ ] **Step 3: Record the declined remedy**

Replace the `**What would resolve it.**` paragraph:

```markdown
**What would resolve it.** A CLI that echoes the variant in its event stream or exit
metadata, so the dispatcher can compare it against the pin and fail closed on a
mismatch.

The alternative this issue previously proposed — restrict the opencode roles to models
whose catalog exposes a single variant — was **considered and declined by explicit human
decision on 2026-08-06**, when `deepseek-reviewer` was pinned at `max` knowing the
variant could not be verified. Recording the refusal keeps the gap deliberate rather
than accidental, which is the same standard issue #1 applies to auxiliary model calls.
```

- [ ] **Step 4: Restate the interim rule for both roles**

Replace the `**Interim rule.**` line:

```markdown
**Interim rule.** Treat `variant_verified` in preflight as "the catalog lists this
variant", never as "this variant ran". For `deepseek-reviewer` say the pinned model
produced the review; do not state the reasoning effort it ran at.
```

- [ ] **Step 5: Verify no other file still calls the exposure latent**

Run: `grep -rn "latent" ISSUES.md README.md CLAUDE.md USAGE.md CHANGELOG.md _shared/`
Expected: no hit that describes the variant exposure as latent or as barely mattering.

- [ ] **Step 6: Commit**

```bash
git add ISSUES.md
git commit -m "ISSUES #2: the variant exposure is active, and the fix was declined

Pinning deepseek-v4-pro satisfies the exact condition #2 named as its trigger.
Records that restricting the role to single-variant models was refused on purpose,
so the gap reads as a decision rather than an oversight."
```

The GitHub mirror of issue #2 needs the same edit. That is a `gh issue edit 2` call against a remote and is **not** part of this plan — ask the human before touching the remote.

---

### Task 4: Verify the whole gate before spending anything

A checkpoint, not a code change. Everything after this costs money, and the harness's own record says a green mock run has never once predicted a live result.

**Files:** none modified.

- [ ] **Step 1: Full suite**

Run: `bash tests/run.sh`
Expected: PASS, with the new deepseek cases present in the output.

- [ ] **Step 2: Invariant gate and self-test**

Run: `bin/check-invariants --self-test && bin/check-invariants`
Expected: PASS on both.

- [ ] **Step 3: Preflight, and read every field**

Run: `bin/worker preflight`
Expected: `model_calls: 0`; six workers plus `claude-main` and `scope-sandbox`; both opencode roles at `available_pending_auth` / `variant_verified` / `none_recorded_for_this_pin`; the other three workers still `available_live_observed`.

- [ ] **Step 4: Confirm no credential material entered the tree**

Run: `git diff main --stat && git grep -nI -e "sk-" -e "ey_" -e "refresh" -- ':!docs' ':!*.md'`
Expected: the diff touches only the files this plan names, and the grep returns no credential value.

---

### Task 5: Live re-verification of both opencode reviewers

Restores what the repin invalidated. Needs a human at a TTY twice per role, and `MULTIAGENT_ALLOW_BILLABLE=1`.

**Files:**
- Create: `tasks/zen-repin-live/task.md`, `context.md`, `log.md` from `_templates/`
- Create: `tasks/zen-repin-live/review-packet.json`
- Modify: `tasks/INDEX.md`, `README.md:14`, `CLAUDE.md`

**Interfaces:**
- Consumes: role names `kimi-reviewer` and `deepseek-reviewer` from Tasks 1-2.
- Produces: `.runtime/live/kimi-reviewer.json` and `.runtime/live/deepseek-reviewer.json` matching the new pins.

- [ ] **Step 1: Create the task from the template**

```bash
mkdir -p tasks/zen-repin-live
cp _templates/task.md tasks/zen-repin-live/task.md
cp _templates/context.md tasks/zen-repin-live/context.md
cp _templates/log.md tasks/zen-repin-live/log.md
```

Fill the Control Plane in `tasks/zen-repin-live/task.md`:

```json
{
  "status": "in_progress",
  "workflow_stage": "review",
  "target_repo": null,
  "write_scope": "none",
  "workers_approved": [
    {"worker": "kimi-reviewer", "write_scope": "none"},
    {"worker": "deepseek-reviewer", "write_scope": "none"}
  ],
  "requested_actions": ["read", "secret_access"]
}
```

The goal to write under `## Goal`: both opencode reviewers return a verdict `validate_verdict` accepts against provider `opencode`, proving the Zen pin reaches the endpoint and that the argv contract survived the repin.

- [ ] **Step 2: Build one neutral packet both reviewers receive**

The packet is the Task 1-2 diff. Per `CLAUDE.md` rule 9 it carries the full command output, a diff scoped to this change only, and the pre-change baseline.

```bash
git diff main...HEAD -- bin/worker.py _shared/backends.json > /tmp/zen.diff
bash tests/run.sh > /tmp/zen-tests.txt 2>&1
python3 - <<'PY'
import json, pathlib
pathlib.Path("tasks/zen-repin-live/review-packet.json").write_text(json.dumps({
    "question": "Does moving both opencode reviewers to provider `opencode` and deriving provider/model from the pin preserve fail-closed behaviour on an absent or mismatched variant?",
    "requirements": [
        "Use only the supplied packet evidence.",
        "The dispatcher must never build a --variant other than max, whatever the config says.",
        "A pin without a provider prefix must fail closed rather than probe a default provider.",
    ],
    "diff": pathlib.Path("/tmp/zen.diff").read_text(),
    "test_evidence": pathlib.Path("/tmp/zen-tests.txt").read_text(),
}, indent=2), encoding="utf-8")
PY
```

Do not truncate `test_evidence`. A truncated log has already earned this harness one correct rejection, recorded in `tasks/pipeline-demo/log.md`.

- [ ] **Step 3: Copy the credential (human)**

```bash
mkdir -p /home/ptg/multiagent-auth/opencode
chmod 700 /home/ptg/multiagent-auth /home/ptg/multiagent-auth/opencode
cp -p /home/ptg/.local/share/opencode/auth.json /home/ptg/multiagent-auth/opencode/
```

One file serves both roles: the Zen and Go keys live in the same `auth.json`, and the descriptor at `bin/worker.py:61` maps `kind: opencode` to it.

- [ ] **Step 4: Approve — four records, human at a TTY**

```bash
bin/worker approve --role kimi-reviewer --task tasks/zen-repin-live/task.md --confirm
bin/worker approve --role kimi-reviewer --task tasks/zen-repin-live/task.md --action secret_access --confirm
bin/worker approve --role deepseek-reviewer --task tasks/zen-repin-live/task.md --confirm
bin/worker approve --role deepseek-reviewer --task tasks/zen-repin-live/task.md --action secret_access --confirm
```

- [ ] **Step 5: Dry-run both, and read the mount plan**

```bash
bin/worker dispatch --role kimi-reviewer --task tasks/zen-repin-live/task.md --input tasks/zen-repin-live/review-packet.json --dry-run
bin/worker dispatch --role deepseek-reviewer --task tasks/zen-repin-live/task.md --input tasks/zen-repin-live/review-packet.json --dry-run
```

Expected in each plan: `--model opencode/kimi-k3` or `--model opencode/deepseek-v4-pro`, `--variant max`, no `--agent`, an `/auth` mount holding only `opencode/auth.json`, and no mount of this repository's `.runtime` or `tasks` tree.

- [ ] **Step 6: Dispatch `kimi-reviewer` for real**

```bash
MULTIAGENT_ALLOW_BILLABLE=1 MULTIAGENT_AUTH_DIR=/home/ptg/multiagent-auth \
  bin/worker dispatch --role kimi-reviewer --task tasks/zen-repin-live/task.md \
  --input tasks/zen-repin-live/review-packet.json
```

Expected: exit 0, `review_contract: passed` in the log's VERIFICATION line.

If it fails, the run folder under `tasks/zen-repin-live/workers/kimi-reviewer/runs/` holds the raw stderr. Add the defect to `tasks/INDEX.md` with its run id before attempting a fix — that table is the reason none of the earlier ten defects had to be re-diagnosed.

- [ ] **Step 7: Dispatch `deepseek-reviewer` for real, independently**

```bash
MULTIAGENT_ALLOW_BILLABLE=1 MULTIAGENT_AUTH_DIR=/home/ptg/multiagent-auth \
  bin/worker dispatch --role deepseek-reviewer --task tasks/zen-repin-live/task.md \
  --input tasks/zen-repin-live/review-packet.json
```

The packet is byte-identical to Kimi's. Do not put Kimi's verdict, or any hint of it, into this input.

- [ ] **Step 8: Confirm both observations now match the pins**

Run: `bin/worker preflight | python3 -c "import json,sys; b=json.load(sys.stdin)['backends']; [print(r, b[r]['status'], b[r]['live_dispatch']) for r in ('kimi-reviewer','deepseek-reviewer')]"`
Expected: both `available_live_observed` with a `succeeded` `live_dispatch` naming a run id from today.

- [ ] **Step 9: Re-test every claim either reviewer makes**

`CLAUDE.md` rule 10. Treat each asserted fact as a hypothesis and measure it directly before acting. Past verdicts have contained both confirmed defects and over-claims. Record the result as a `[VERIFICATION]` line in `tasks/zen-repin-live/log.md` naming what was confirmed and what was refuted.

- [ ] **Step 10: Update the records**

Add both runs to the `## All runs` table in `tasks/INDEX.md` with their outcome, plus any new defect row.

`README.md:14`, replacing the status cell written in Task 1:

```markdown
argv and catalog verified against provider `opencode`; a real dispatch returned a schema-valid verdict on 2026-08-06; `--variant` is not enforceable
```

Add the `deepseek-reviewer` row with the same evidence, and update the state-of-the-harness paragraph in `CLAUDE.md` to say six workers have completed a real dispatch, dated 2026-08-06.

- [ ] **Step 11: Remove the credential copy**

```bash
rm -rf /home/ptg/multiagent-auth
```

- [ ] **Step 12: Commit**

`tasks/*` is gitignored, so only the docs are committed; the raw evidence stays on this machine, which is the intended split.

```bash
git add README.md CLAUDE.md
git commit -m "Record the live re-verification of both Zen reviewers

One real dispatch each on opencode/kimi-k3 and opencode/deepseek-v4-pro. The
preflight live_dispatch observations now match the new pins; the run folders and
INDEX rows stay local."
```

---

## Self-Review

**Spec coverage.** Spec section 1 (worker inventory) → Tasks 1, 2. Section 2 (role split, stated honestly) → Task 2 Step 11, which carries the output-ceiling and price wording and omits the attachment claim the spec rejected. Section 3 (code changes, all ten rows) → Tasks 1 and 2; the `worker.py:1152` "deliberately unchanged" line is a Global Constraint. Section 4 (verification consequence) → Task 1 Step 9 observes the invalidation, Task 5 restores it. Section 5 (ISSUES #2) → Task 3, all three required statements. Section 6 (documentation) → Task 1 Step 10 and Task 2 Step 11, including the `opencode-zen`-is-a-display-name sentence. Success criteria 1-3 → Task 4; 4 → Task 5 Step 8; 5 → Task 3; 6 → Task 4 Step 4. No gap found.

**Placeholder scan.** No TBD, no "add appropriate error handling", no "similar to Task N". Every code step carries the literal replacement text. The one deferred item is edged explicitly: the GitHub mirror of issue #2 is named as out of scope and needing a human, rather than left vague.

**Type consistency.** `opencode_model_metadata(output, model_id)` is defined in Task 1 Step 3 and called in Task 1 Step 4 with that signature; Task 2 consumes it without redefining it. The role string `deepseek-reviewer` is identical across `backends.json`, all five `worker.py` sites, both test files, the mock fixture, and Task 5's commands. The model token `opencode/deepseek-v4-pro` is identical in all seven places it appears. `BackendPreflight`'s six positional arguments — `(status, detail, sandbox_startup, endpoint, auth, model_acceptance)` — match the existing call sites in the same function, and `StartupProbe(status, detail)` matches its dataclass at `bin/worker.py:156`. `re` is already imported at `bin/worker.py:19`, and `from unittest import mock` at `tests/test_hardening.py:16`, so neither new snippet adds an import.

**One risk the plan does not remove.** Task 1 Step 1 makes the mock refuse `["--pure", "models", <anything but opencode>]`, which proves no stale `opencode-go` probe survives. It cannot prove the real Zen endpoint accepts either model — only Task 5 does that, and until it runs, both roles are honestly labelled unverified in `README.md`.
