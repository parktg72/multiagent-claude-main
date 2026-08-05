#!/usr/bin/env python3
"""Dispatcher contracts using strict local mock CLIs; no provider calls."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import pty
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "bin" / "worker.py"
MOCK_BIN = ROOT / "tests" / "fixtures" / "mock-bin"
sys.path.insert(0, str(ROOT / "bin"))
import worker  # noqa: E402


class DispatcherContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="worker-test-", dir=ROOT))
        self.repo = self.tmp / "repo"
        (self.repo / "src").mkdir(parents=True)
        (self.repo / "tests").mkdir()
        self.task_dir = self.tmp / "tasks" / "sample"
        (self.task_dir / "artifacts").mkdir(parents=True)
        self.log = self.task_dir / "log.md"
        self.log.write_text("# audit mirror only\n", encoding="utf-8")
        self.task = self.task_dir / "task.md"
        # Private runtime state stays inside the per-test directory so a suite run
        # never writes locks or approval journals into the real .runtime.
        self.runtime = self.tmp / "runtime"
        key = hashlib.sha256(str(self.task.relative_to(ROOT)).encode("utf-8")).hexdigest()
        self.journal = self.runtime / "approvals" / f"{key}.jsonl"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp)

    def environment(self, **extra: str) -> dict[str, str]:
        env = os.environ.copy()
        env["PATH"] = str(MOCK_BIN) + os.pathsep + env.get("PATH", "")
        env["MULTIAGENT_TEST_MODE"] = "1"
        env["MULTIAGENT_TEST_SENTINEL"] = "fixture-only-dispatch"
        env["MULTIAGENT_ALLOW_BILLABLE"] = "1"
        env["MULTIAGENT_TEST_RUNTIME_DIR"] = str(self.runtime)
        env.update(extra)
        return env

    def call(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(WORKER), *args],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.environment(**env),
            check=False,
            timeout=20,
        )

    def call_with_pty_confirmation(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        master, slave = pty.openpty()
        try:
            process = subprocess.Popen(
                [sys.executable, str(WORKER), *args],
                cwd=ROOT,
                stdin=slave,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=self.environment(**env),
            )
            os.close(slave)
            slave = -1
            os.write(master, b"APPROVE\n")
            stdout, stderr = process.communicate(timeout=20)
            return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
        finally:
            if slave >= 0:
                os.close(slave)
            os.close(master)

    def write_task(self, role: str, scope: str, actions: list[str] | None = None) -> None:
        data = {
            "status": "in_progress",
            "workflow_stage": "producer" if role == "codex-sol" else "review",
            "target_repo": str(self.repo),
            "write_scope": scope,
            "workers_approved": [{"worker": role, "write_scope": scope}],
            "requested_actions": actions or (["write"] if role == "codex-sol" else ["read"]),
        }
        self.task.write_text("# Sample\n\n## Control Plane\n```json\n" + json.dumps(data) + "\n```\n", encoding="utf-8")

    def input_for(self, role: str) -> Path:
        if role == "codex-sol":
            path = self.task_dir / "sol-input.md"
            path.write_text("Implement only approved files.", encoding="utf-8")
            return path
        path = self.task_dir / "review-input.json"
        path.write_text(
            json.dumps(
                {
                    "question": "Does this change preserve approval and filesystem isolation behavior?",
                    "requirements": ["Use only supplied packet evidence."],
                    "diff": "diff --git a/src/a.py b/src/a.py\n",
                    "test_evidence": "test result: pass\n",
                }
            ),
            encoding="utf-8",
        )
        return path

    def approve(self, role: str, action: str | None = None) -> None:
        args = ["approve", "--role", role, "--task", str(self.task), "--confirm"]
        if action:
            args.extend(["--action", action])
        result = self.call_with_pty_confirmation(*args)
        self.assertEqual(result.returncode, 0, result.stderr)

    def dispatch(self, role: str, *, dry_run: bool = False, **env: str) -> subprocess.CompletedProcess[str]:
        args = ["dispatch", "--role", role, "--task", str(self.task), "--input", str(self.input_for(role))]
        if dry_run:
            args.append("--dry-run")
        return self.call(*args, **env)

    def test_unapproved_writer_is_rejected(self) -> None:
        self.write_task("codex-sol", "src/**")
        result = self.dispatch("codex-sol", dry_run=True)
        self.assertEqual(result.returncode, 77)
        self.assertIn("authoritative", result.stderr)

    def test_scope_traversal_is_rejected_before_approval(self) -> None:
        self.write_task("codex-sol", "../escape/**")
        result = self.dispatch("codex-sol", dry_run=True)
        self.assertEqual(result.returncode, 77)
        self.assertIn("write_scope", result.stderr)

    def test_review_input_cannot_include_prior_reviewer_conclusion(self) -> None:
        self.write_task("codex-terra", "none")
        self.approve("codex-terra")
        path = self.input_for("codex-terra")
        packet = json.loads(path.read_text(encoding="utf-8"))
        packet["prior_reviewer_conclusion"] = "approve"
        path.write_text(json.dumps(packet), encoding="utf-8")
        result = self.call(
            "dispatch", "--role", "codex-terra", "--task", str(self.task), "--input", str(path), "--dry-run"
        )
        self.assertEqual(result.returncode, 65)
        self.assertIn("forbidden", result.stderr)
        packet.pop("prior_reviewer_conclusion")
        packet["artifact_paths"] = ["workers/codex-terra/runs/old/raw-output.txt"]
        path.write_text(json.dumps(packet), encoding="utf-8")
        result = self.call(
            "dispatch", "--role", "codex-terra", "--task", str(self.task), "--input", str(path), "--dry-run"
        )
        self.assertEqual(result.returncode, 65)
        self.assertIn("artifact", result.stderr)

    def test_sol_strict_mock_happy_path(self) -> None:
        self.write_task("codex-sol", "src/**")
        self.approve("codex-sol")
        result = self.dispatch("codex-sol")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_terra_strict_mock_happy_path_saves_valid_verdict(self) -> None:
        self.write_task("codex-terra", "none")
        self.approve("codex-terra")
        result = self.dispatch("codex-terra")
        self.assertEqual(result.returncode, 0, result.stderr)
        verdicts = list((self.task_dir / "workers" / "codex-terra" / "runs").glob("*/review-verdict.json"))
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(json.loads(verdicts[0].read_text(encoding="utf-8"))["verdict"], "conditional")

    def test_agy_strict_mock_happy_path(self) -> None:
        self.write_task("agy", "none")
        self.approve("agy")
        result = self.dispatch("agy")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_kimi_strict_mock_happy_path_uses_max_without_agent(self) -> None:
        self.write_task("kimi-reviewer", "none")
        self.approve("kimi-reviewer")
        result = self.dispatch("kimi-reviewer")
        self.assertEqual(result.returncode, 0, result.stderr)
        input_path = self.input_for("kimi-reviewer")
        dry = self.call("dispatch", "--role", "kimi-reviewer", "--task", str(self.task), "--input", str(input_path), "--dry-run")
        self.assertEqual(dry.returncode, 0, dry.stderr)
        plan = json.loads(dry.stdout)
        command = plan["command"]
        pairs = list(zip(command, command[1:]))
        self.assertIn(("--variant", "max"), pairs)
        self.assertNotIn("high", command)
        self.assertNotIn("--agent", command)
        input_mount = next(mount for mount in plan["sandbox"]["mounts"] if mount["destination"] == "/input/review-input.json")
        self.assertNotEqual(input_mount["source"], str(input_path))
        # The packet is served from private runtime storage, never the original input path.
        packets = (self.runtime / "packets").resolve()
        self.assertEqual(Path(input_mount["source"]).resolve().parent, packets)

    def test_fable_strict_mock_happy_path(self) -> None:
        self.write_task("fable-advisor", "none")
        self.approve("fable-advisor")
        result = self.dispatch("fable-advisor")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_authoritative_action_approval_enables_protected_dry_run(self) -> None:
        self.write_task("codex-sol", "src/**", ["write", "git_push"])
        self.approve("codex-sol")
        self.approve("codex-sol", "git_push")
        result = self.dispatch("codex-sol", dry_run=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_stale_lock_file_does_not_block_flock_writer(self) -> None:
        self.write_task("codex-sol", "src/**")
        self.approve("codex-sol")
        lock = self.runtime / "locks" / "codex-sol.lock"
        lock.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock.write_text("stale-pid", encoding="utf-8")
        # A real dispatch outside the suite legitimately leaves its own lock here, so
        # compare before and after instead of demanding the path never exists.
        real_lock = ROOT / ".runtime" / "locks" / "codex-sol.lock"
        before = real_lock.stat().st_mtime_ns if real_lock.exists() else None
        result = self.dispatch("codex-sol")
        self.assertEqual(result.returncode, 0, result.stderr)
        after = real_lock.stat().st_mtime_ns if real_lock.exists() else None
        self.assertEqual(before, after)

    def test_strict_mock_binaries_reject_unknown_flags(self) -> None:
        cases = [
            ("codex", ["exec", "--unknown"]),
            ("agy", ["--unknown"]),
            ("opencode", ["run", "--unknown"]),
            ("claude", ["--unknown"]),
            ("bwrap", ["--ro-bind", "/", "/", "--", "/bin/true"]),
        ]
        for binary, args in cases:
            result = subprocess.run([str(MOCK_BIN / binary), *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            self.assertNotEqual(result.returncode, 0, binary)

    def test_preflight_uses_complete_mock_cli_contracts_without_model_call(self) -> None:
        result = self.call("preflight", "--allow-unavailable")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["model_calls"], 0)
        self.assertEqual(report["backends"]["agy"]["status"], "available_pending_auth")
        self.assertEqual(report["backends"]["kimi-reviewer"]["status"], "available_pending_auth")

    def test_source_has_no_whole_root_bind_or_dynamic_evaluator(self) -> None:
        source = WORKER.read_text(encoding="utf-8")
        self.assertNotIn("--ro-bind / /", source)
        self.assertNotIn("eval(", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
