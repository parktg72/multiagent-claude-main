#!/usr/bin/env python3
"""Fail-closed dispatcher for a Claude-main multiagent workspace.

No generic shell execution exists here. Backend records are fixed, every external
worker receives a purpose-built Bubblewrap filesystem, and task-file approvals are
only audit mirrors. Authoritative approval records live in private runtime storage.
"""

from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass, field
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
BACKENDS_FILE = ROOT / "_shared" / "backends.json"
SCHEMA_DIR = ROOT / "_shared" / "schemas"
REVIEW_INPUT_SCHEMA = SCHEMA_DIR / "review-input.schema.json"
REVIEW_VERDICT_SCHEMA = SCHEMA_DIR / "review-verdict.schema.json"
CONTROL_FENCE = re.compile(r"^## Control Plane\s*\n```json\s*\n(.*?)\n```", re.MULTILINE | re.DOTALL)
ALLOWED_TASK_STAGES = {"producer", "review", "advisory"}
SAFE_ACTIONS = {"read", "write", "test", "debug", "review", "analysis"}
PROTECTED_ACTIONS = {"delete", "git_push", "deploy", "secret_access"}
VALID_VERDICTS = {"approve", "reject", "conditional", "insufficient_evidence"}
MOCK_BIN = ROOT / "tests" / "fixtures" / "mock-bin"
MOCK_RUNTIME_ROOT = ROOT / "tests" / "fixtures" / "mock-runtime"
# A credential file copied into the sandbox stays small; the cap keeps an unexpected
# path from streaming a large file through the descriptor.
INJECTED_FILE_MAX_BYTES = 1_048_576
FD_ARGUMENT_PREFIX = "@fd:"
CODEX_SANDBOX_HOME = "/home/worker/.codex"
OPENCODE_REVIEW_MESSAGE = (
    "The first attached file is the independent review packet; the second is the verdict JSON schema. "
    "Reply with one JSON object matching that schema exactly and nothing else. "
    "Use only the properties the schema declares: any additional property is rejected outright, "
    "so fold extra analysis into summary, evidence, or recommendations."
)
# kind -> (path under MULTIAGENT_AUTH_DIR, sandbox destination, extra environment).
# Only the single credential file each CLI needs is copied in; nothing else from the
# host auth directory becomes visible.
CREDENTIAL_INJECTIONS = {
    "codex": ("codex/auth.json", CODEX_SANDBOX_HOME + "/auth.json", {"CODEX_HOME": CODEX_SANDBOX_HOME}),
    "agy": ("gemini/antigravity-oauth-token", "/home/worker/.gemini/antigravity-cli/antigravity-oauth-token", {}),
    "opencode": ("opencode/auth.json", "/home/worker/.local/share/opencode/auth.json", {}),
    "claude": ("claude/.credentials.json", "/home/worker/.claude/.credentials.json", {}),
}
# Local help probes answer from disk. Catalog probes may reach a remote model index,
# so they get a wider budget; a slow network must not read as an unavailable backend.
PROBE_TIMEOUT_SECONDS = 20
CATALOG_PROBE_TIMEOUT_SECONDS = 90
SCHEMA_VALIDATION_KEYWORDS = {"type", "additionalProperties", "required", "properties", "minItems", "items", "minLength", "enum"}
SCHEMA_ANNOTATION_KEYWORDS = {"$schema", "$id", "$comment", "title", "description", "default", "examples", "deprecated", "readOnly", "writeOnly"}
GENERIC_RISK_EXACT = {"generic", "unspecified", "unknown", "n/a", "tbd", "some risk", "risk may happen", "could happen"}
GENERIC_RISK_BOILERPLATE = re.compile(
    r"^(?:(?:generic|unspecified)\s+)+(?:risk|failure|trigger|impact|evidence|mitigation)(?:\s+(?:may|might|could)\s+(?:happen|occur)(?:\s+(?:someday|somehow))?)?[.!]?$",
    re.IGNORECASE,
)

NO_YES_MAN_CONTRACT = """# Independent no-yes-man contract

Return one JSON object only, matching supplied verdict schema.
Use only supplied question, requirements, diff, test evidence, and artifact paths.
No main conclusion or another reviewer conclusion is supplied or admissible evidence.
Separate evidence from unverified claims. State one structured, concrete risk with
failure mode, trigger, impact, evidence or reproducible locator, and mitigation.
Do not invent cosmetic objections. If evidence is insufficient, use conditional or
insufficient_evidence rather than approval.
"""


class ControlledError(Exception):
    """Expected failure with a stable public exit status."""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


class GateError(ControlledError):
    def __init__(self, message: str) -> None:
        super().__init__(message, 77)


class SchemaError(ControlledError):
    def __init__(self, message: str) -> None:
        super().__init__(message, 65)


class DependencyError(ControlledError):
    def __init__(self, message: str) -> None:
        super().__init__(message, 69)


@dataclass(frozen=True)
class MountSpec:
    """One intentional mount. source is absent for namespace-created mounts."""

    source: str | None
    destination: str
    mode: str

    def as_dict(self) -> dict[str, str | None]:
        return {"source": self.source, "destination": self.destination, "mode": self.mode}


@dataclass
class SandboxPlan:
    command: list[str]
    mounts: list[MountSpec]
    cwd: str
    environment: dict[str, str] = field(default_factory=dict)
    network: str = "host-network-not-isolated"

    def as_dict(self) -> dict[str, Any]:
        return {
            "cwd": self.cwd,
            "mounts": [mount.as_dict() for mount in self.mounts],
            "network": self.network,
            "home": "/home/worker",
        }


@dataclass
class RuntimeBinding:
    prefix: list[str]
    mounts: list[MountSpec]


@dataclass
class RunResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    duration_s: float
    timed_out: bool


@dataclass(frozen=True)
class StartupProbe:
    status: str
    detail: str


@dataclass
class PreflightCache:
    """Ephemeral cache for one preflight or dispatch readiness evaluation."""

    contracts: dict[str, dict[str, Any]] | None = None
    startup: dict[str, StartupProbe] = field(default_factory=dict)
    local_probes: dict[str, tuple[bool, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class BackendPreflight:
    status: str
    detail: str
    sandbox_startup: StartupProbe
    endpoint: str
    auth: str
    model_acceptance: str


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def pin_digest(backend: dict[str, Any]) -> str:
    """Identify the exact pin a live run exercised.

    A recorded observation credits only the pin that produced it, so changing model,
    effort, variant, sandbox mode, or command invalidates it instead of carrying a
    stale success forward.
    """
    material = {key: backend.get(key) for key in ("kind", "command", "model", "effort", "sandbox", "access")}
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode("utf-8")).hexdigest()


def live_observation_path(role: str) -> Path:
    if not re.fullmatch(r"[a-z0-9-]{1,40}", role):
        raise GateError("worker role has an unexpected shape")
    return private_runtime_child("live") / f"{role}.json"


def record_live_observation(role: str, backend: dict[str, Any], run_id: str) -> None:
    """Note that one real dispatch reached the provider and exited zero.

    Never written for fixture test mode or a dry run. The record holds no prompt,
    output, or credential material: only the pin it exercised and when.
    """
    if test_mode():
        return
    payload = {
        "role": role,
        "model": backend.get("model"),
        "effort": backend.get("effort"),
        "observed_at": utc_now(),
        "pin_digest": pin_digest(backend),
        "run": run_id,
    }
    path = live_observation_path(role)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def live_observation(role: str, backend: dict[str, Any]) -> dict[str, Any] | None:
    """Return a prior successful dispatch for this exact pin, if one was recorded."""
    try:
        path = live_observation_path(role)
        if not path.is_file() or path.is_symlink():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, GateError):
        return None
    if not isinstance(payload, dict) or payload.get("pin_digest") != pin_digest(backend):
        return None
    return payload


def test_mode() -> bool:
    return (
        os.environ.get("MULTIAGENT_TEST_MODE") == "1"
        and os.environ.get("MULTIAGENT_TEST_SENTINEL") == "fixture-only-dispatch"
    )


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
    except ValueError:
        return False
    return True


def project_path(value: str | Path, label: str, *, must_exist: bool = True) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    resolved = path.resolve(strict=False)
    if not is_within(resolved, ROOT):
        raise GateError(f"{label} must remain inside multiagent root")
    if must_exist and not resolved.exists():
        raise GateError(f"{label} does not exist")
    return resolved


def task_member(task_dir: Path, *parts: str, label: str) -> Path:
    candidate = task_dir.joinpath(*parts)
    if not is_within(candidate, task_dir):
        raise GateError(f"{label} escapes task directory")
    return candidate


def runtime_root_override() -> Path | None:
    """Fixture-test-only relocation of private runtime storage.

    Production never honors this. Setting it without the exact test-mode pair is a
    hard error rather than a silent ignore, so an attempt to redirect the
    authoritative approval journal stops dispatch instead of passing unnoticed.
    """
    raw = os.environ.get("MULTIAGENT_TEST_RUNTIME_DIR")
    if not raw:
        return None
    if not test_mode():
        raise GateError("private runtime override requires fixture test mode")
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise GateError("private runtime override must be an absolute path")
    if not is_within(candidate, ROOT) or candidate.resolve(strict=False) == ROOT.resolve():
        raise GateError("private runtime override must stay inside the project")
    return candidate


def private_runtime_dir() -> Path:
    runtime = runtime_root_override() or ROOT / ".runtime"
    if runtime.is_symlink() or (runtime.exists() and not runtime.is_dir()):
        raise GateError("private runtime path must be a real directory")
    if not is_within(runtime, ROOT):
        raise GateError("private runtime directory escapes project")
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(runtime, 0o700)
        metadata = runtime.stat()
    except OSError as exc:
        raise GateError("private runtime permissions cannot be secured") from exc
    if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
        raise GateError("private runtime must be owner-private")
    return runtime.resolve()


def private_runtime_child(name: str) -> Path:
    child = private_runtime_dir() / name
    if child.is_symlink() or (child.exists() and not child.is_dir()):
        raise GateError(f"private runtime child must be a real directory: {name}")
    if not is_within(child, private_runtime_dir()):
        raise GateError(f"private runtime child escapes runtime: {name}")
    child.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(child, 0o700)
        metadata = child.stat()
    except OSError as exc:
        raise GateError(f"private runtime child permissions cannot be secured: {name}") from exc
    if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
        raise GateError(f"private runtime child must be owner-private: {name}")
    return child.resolve()


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SchemaError(f"invalid {label}") from exc
    if not isinstance(parsed, dict):
        raise SchemaError(f"{label} must be a JSON object")
    return parsed


def load_backends() -> dict[str, Any]:
    return load_json(BACKENDS_FILE, "backends.json")


def backend_for(role: str) -> dict[str, Any]:
    workers = load_backends().get("workers")
    if not isinstance(workers, dict) or role not in workers:
        raise GateError(f"unknown worker role: {role}")
    backend = workers[role]
    if not isinstance(backend, dict):
        raise SchemaError("backend record must be an object")
    return backend


def extract_control(task: Path) -> dict[str, Any]:
    try:
        content = task.read_text(encoding="utf-8")
    except OSError as exc:
        raise GateError("task file is unreadable") from exc
    match = CONTROL_FENCE.search(content)
    if not match:
        raise SchemaError("task.md needs one Control Plane JSON fence")
    try:
        control = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise SchemaError("task Control Plane JSON is invalid") from exc
    if not isinstance(control, dict):
        raise SchemaError("task Control Plane must be an object")
    required = {"status", "workflow_stage", "target_repo", "write_scope", "workers_approved", "requested_actions"}
    missing = required.difference(control)
    if missing:
        raise SchemaError(f"task Control Plane missing: {', '.join(sorted(missing))}")
    if control["workflow_stage"] not in ALLOWED_TASK_STAGES:
        raise SchemaError("task workflow_stage is invalid")
    if not isinstance(control["workers_approved"], list):
        raise SchemaError("workers_approved must be a list")
    if not isinstance(control["requested_actions"], list) or not all(isinstance(item, str) for item in control["requested_actions"]):
        raise SchemaError("requested_actions must be a string list")
    return control


def target_repo_for(control: dict[str, Any], backend: dict[str, Any]) -> Path | None:
    value = control["target_repo"]
    if value is None:
        if backend.get("access") == "workspace-write":
            raise GateError("workspace-write requires an absolute target_repo")
        return None
    if not isinstance(value, str) or not value or not os.path.isabs(value):
        raise GateError("target_repo must be an absolute path or null")
    target = Path(value)
    if not target.is_dir() or target.is_symlink():
        raise GateError("target_repo must be an existing non-symlink directory")
    resolved = target.resolve()
    if resolved in {Path("/"), Path.home().resolve(), ROOT.resolve()} or is_within(ROOT, resolved):
        raise GateError("target_repo may not be system root, home root, or control-root ancestor")
    control_roots = [ROOT / "tasks", ROOT / ".runtime", ROOT / "_shared", ROOT / "bin", ROOT / ".opencode"]
    override = runtime_root_override()
    if override is not None:
        control_roots.append(override)
    if any(is_within(resolved, control_root) for control_root in control_roots):
        raise GateError("target_repo may not be a control-plane path")
    return resolved


def scope_paths(scope: Any, target: Path | None, task_dir: Path, access: str) -> list[tuple[Path, str]]:
    """Return host source and sandbox-relative destination for writable surfaces."""
    if not isinstance(scope, str):
        raise GateError("write_scope must be a string")
    if access != "workspace-write":
        if scope != "none":
            raise GateError("read-only worker requires write_scope none")
        return []
    if scope == "none":
        raise GateError("workspace-write worker requires a non-none write_scope")
    if scope == "tasks-only":
        artifacts = task_member(task_dir, "artifacts", label="task artifacts")
        if not artifacts.is_dir() or artifacts.is_symlink():
            raise GateError("tasks-only requires existing non-symlink task artifacts directory")
        return [(artifacts.resolve(), "/workspace")]
    if target is None:
        raise GateError("external write_scope requires target_repo")
    results: list[tuple[Path, str]] = []
    for raw_pattern in scope.split(","):
        pattern = raw_pattern.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*/\*\*", pattern):
            raise GateError("write_scope accepts only relative directory/** patterns")
        relative = pattern[:-3]
        candidate = target / relative
        if not candidate.is_dir() or candidate.is_symlink():
            raise GateError("write_scope directory must exist and not be a symlink")
        resolved = candidate.resolve()
        if not is_within(resolved, target):
            raise GateError("write_scope escapes target_repo")
        destination = "/workspace/" + relative
        pair = (resolved, destination)
        if pair not in results:
            results.append(pair)
    if not results:
        raise GateError("write_scope has no usable paths")
    return results


def task_key(task: Path) -> str:
    relative = task.resolve().relative_to(ROOT.resolve()).as_posix()
    return hashlib.sha256(relative.encode("utf-8")).hexdigest()


def authoritative_journal_path(task: Path) -> Path:
    approvals = private_runtime_child("approvals")
    journal = approvals / f"{task_key(task)}.jsonl"
    if journal.is_symlink() or (journal.exists() and not journal.is_file()):
        raise GateError("authoritative approval journal must be a regular non-symlink file")
    return journal


def target_digest(target: Path | None) -> str:
    source = "none" if target is None else str(target.resolve())
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def planned_worker(control: dict[str, Any], role: str, scope: str) -> bool:
    return any(
        isinstance(item, dict) and item.get("worker") == role and item.get("write_scope") == scope
        for item in control["workers_approved"]
    )


def append_authoritative_event(task: Path, event: dict[str, Any]) -> None:
    journal = authoritative_journal_path(task)
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(journal, flags, 0o600)
    except OSError as exc:
        raise GateError("authoritative approval journal cannot be opened safely") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise GateError("authoritative approval journal must be a non-hardlinked regular file")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        os.write(descriptor, (json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
        os.fsync(descriptor)
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def authoritative_events(task: Path) -> Iterable[dict[str, Any]]:
    journal = authoritative_journal_path(task)
    try:
        descriptor = os.open(journal, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise GateError("authoritative approval journal cannot be opened safely") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise GateError("authoritative approval journal must be a non-hardlinked regular file")
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            lines = handle.read().splitlines()
    except OSError as exc:
        raise GateError("authoritative approval journal is unreadable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    parsed: list[dict[str, Any]] = []
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise GateError("authoritative approval journal is malformed") from exc
        if not isinstance(event, dict):
            raise GateError("authoritative approval journal contains a non-object event")
        parsed.append(event)
    return parsed


def append_audit_mirror(log_path: Path, tag: str, event: dict[str, Any]) -> None:
    if tag not in {"DECISION", "WORKER_CALL", "VERIFICATION", "ERROR", "APPROVAL", "COMPLETE"}:
        raise SchemaError("invalid audit tag")
    if log_path.is_symlink() or not is_within(log_path, log_path.parent):
        raise GateError("task audit log may not be a symlink")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{utc_now()}] [{tag}] {json.dumps(event, ensure_ascii=False, sort_keys=True)}\n")


def require_authorization(control: dict[str, Any], task: Path, log_path: Path, role: str, scope: str, target: Path | None) -> None:
    if not planned_worker(control, role, scope):
        raise GateError("workers_approved lacks matching planned worker and write_scope")
    digest = target_digest(target)
    worker_event = any(
        event.get("kind") == "worker"
        and event.get("task_key") == task_key(task)
        and event.get("role") == role
        and event.get("write_scope") == scope
        and event.get("target_digest") == digest
        and event.get("approved_by") == "human"
        for event in authoritative_events(task)
    )
    if not worker_event:
        raise GateError("authoritative worker approval is required; task log text is audit-only")
    for action in control["requested_actions"]:
        if action in SAFE_ACTIONS:
            continue
        if action not in PROTECTED_ACTIONS:
            raise GateError(f"unsupported requested action: {action}")
        action_event = any(
            event.get("kind") == "action"
            and event.get("task_key") == task_key(task)
            and event.get("role") == role
            and event.get("write_scope") == scope
            and event.get("target_digest") == digest
            and event.get("action") == action
            and event.get("approved_by") == "human"
            for event in authoritative_events(task)
        )
        if not action_event:
            raise GateError(f"authoritative protected-action approval is required: {action}")


def schema_keyword_issues(schema: Any, location: str = "$") -> list[str]:
    if not isinstance(schema, dict):
        return [f"{location} schema must be an object"]
    issues: list[str] = []
    for key in schema:
        if key not in SCHEMA_VALIDATION_KEYWORDS and key not in SCHEMA_ANNOTATION_KEYWORDS:
            issues.append(f"unsupported schema keyword at {location}: {key}")
    expected_type = schema.get("type")
    if expected_type is not None and expected_type not in {"object", "array", "string"}:
        issues.append(f"unsupported schema type at {location}")
    if "additionalProperties" in schema and not isinstance(schema["additionalProperties"], bool):
        issues.append(f"unsupported additionalProperties form at {location}")
    if "required" in schema and (
        not isinstance(schema["required"], list) or not all(isinstance(item, str) for item in schema["required"])
    ):
        issues.append(f"unsupported required form at {location}")
    if "properties" in schema:
        properties = schema["properties"]
        if not isinstance(properties, dict):
            issues.append(f"unsupported properties form at {location}")
        else:
            for name, child in properties.items():
                if not isinstance(name, str):
                    issues.append(f"non-string property name at {location}")
                issues.extend(schema_keyword_issues(child, f"{location}.properties.{name}"))
    if "items" in schema:
        items = schema["items"]
        issues.extend(schema_keyword_issues(items, f"{location}.items"))
    for keyword in ("minItems", "minLength"):
        if keyword in schema and (not isinstance(schema[keyword], int) or isinstance(schema[keyword], bool) or schema[keyword] < 0):
            issues.append(f"unsupported {keyword} form at {location}")
    if "enum" in schema and not isinstance(schema["enum"], list):
        issues.append(f"unsupported enum form at {location}")
    return issues


def require_supported_schema(schema: dict[str, Any], label: str) -> None:
    issues = schema_keyword_issues(schema)
    if issues:
        raise SchemaError(f"{label}: {issues[0]}")


def validate_schema_value(value: Any, schema: dict[str, Any], location: str = "$") -> None:
    expected_type = schema.get("type")
    if expected_type == "object":
        if not isinstance(value, dict):
            raise SchemaError(f"{location} must be an object")
        required = schema.get("required", [])
        for name in required:
            if name not in value:
                raise SchemaError(f"{location}.{name} is required")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = set(value).difference(properties)
            if extra:
                raise SchemaError(f"{location} has forbidden fields: {', '.join(sorted(extra))}")
        for name, child_schema in properties.items():
            if name in value and isinstance(child_schema, dict):
                validate_schema_value(value[name], child_schema, f"{location}.{name}")
    elif expected_type == "array":
        if not isinstance(value, list):
            raise SchemaError(f"{location} must be an array")
        minimum = schema.get("minItems")
        if isinstance(minimum, int) and len(value) < minimum:
            raise SchemaError(f"{location} needs at least {minimum} items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                validate_schema_value(item, item_schema, f"{location}[{index}]")
    elif expected_type == "string":
        if not isinstance(value, str):
            raise SchemaError(f"{location} must be a string")
        minimum = schema.get("minLength")
        if isinstance(minimum, int) and len(value.strip()) < minimum:
            raise SchemaError(f"{location} must contain at least {minimum} characters")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaError(f"{location} is not an allowed enum value")


def validate_against_schema(value: Any, schema_path: Path) -> None:
    schema = load_json(schema_path, schema_path.name)
    require_supported_schema(schema, schema_path.name)
    validate_schema_value(value, schema)


def validate_review_input(path: Path) -> dict[str, Any]:
    packet = load_json(path, "review input")
    validate_against_schema(packet, REVIEW_INPUT_SCHEMA)
    forbidden_artifact_parts = {".runtime", "workers", "task.md", "log.md", "raw-output.txt", "raw-stderr.txt", "review-verdict.json"}
    for artifact_path in packet.get("artifact_paths", []):
        normalized = artifact_path.replace("\\", "/")
        parts = PurePosixPath(normalized).parts
        if (
            normalized.startswith("/")
            or re.match(r"^[A-Za-z]:/", normalized)
            or not parts
            or any(part in {".", ".."} or part in forbidden_artifact_parts for part in parts)
        ):
            raise SchemaError("artifact_paths must be relative neutral artifact or workspace paths, never worker output or control-plane paths")
    return packet


def substantive_text(value: str, field_name: str) -> None:
    normalized = value.strip().lower()
    tokens = re.findall(r"[a-z0-9가-힣_/-]+", normalized)
    if (
        len(value.strip()) < 24
        or len(set(tokens)) < 3
        or normalized in GENERIC_RISK_EXACT
        or GENERIC_RISK_BOILERPLATE.fullmatch(normalized) is not None
    ):
        raise SchemaError(f"risk {field_name} is generic or insufficiently concrete")


UNUSABLE_CODEPOINT_CATEGORIES = {"Cn", "Co", "Cs"}


def reject_unusable_codepoints(value: Any, location: str) -> None:
    """Refuse unassigned, private-use, and surrogate codepoints in a verdict.

    A live `gpt-5.6-luna` review returned a schema-valid verdict whose risk trigger
    carried the model's own reasoning and ended in U+5FFFF. `--output-schema` checks
    shape, and `substantive_text` checks length and token variety, so nothing looked
    inside the string and the contaminated verdict was accepted.

    This closes the detectable half. The leaked prose itself is ordinary English and
    no rule that caught it would spare legitimate review text, so it stays uncaught
    by deliberate decision rather than oversight.

    `Cc` is excluded: newlines and tabs are legitimate in a review. The `Cn` half
    depends on the local `unicodedata` version, since a codepoint assigned in a newer
    Unicode reads as unassigned on an older table. The drift direction is toward
    rejection, which is the safe one for a gate. The observed U+5FFFF is a Unicode
    noncharacter, permanently unassigned by guarantee, so that case does not drift.
    """
    if isinstance(value, str):
        for index, character in enumerate(value):
            if unicodedata.category(character) in UNUSABLE_CODEPOINT_CATEGORIES:
                raise SchemaError(
                    f"{location} carries an unusable codepoint U+{ord(character):04X} at offset {index}"
                )
        return
    if isinstance(value, dict):
        for key, item in value.items():
            reject_unusable_codepoints(item, f"{location}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            reject_unusable_codepoints(item, f"{location}[{index}]")


def validate_verdict(value: dict[str, Any]) -> dict[str, Any]:
    validate_against_schema(value, REVIEW_VERDICT_SCHEMA)
    reject_unusable_codepoints(value, "verdict")
    if value["verdict"] == "approve" and not value["evidence"]:
        raise SchemaError("approve requires evidence")
    for risk in value["risks"]:
        for field_name in ("failure_mode", "trigger", "impact", "evidence_or_locator", "mitigation"):
            substantive_text(risk[field_name], field_name)
    return value


def verdict_candidates(raw: str) -> Iterable[dict[str, Any]]:
    raw = raw.strip()
    if not raw:
        return
    decoder = json.JSONDecoder()
    candidates: list[Any] = []
    try:
        candidates.append(json.loads(raw))
    except json.JSONDecodeError:
        pass
    for match in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL | re.IGNORECASE):
        try:
            candidates.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            pass
    for index, character in enumerate(raw):
        if character != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(raw[index:])
        except json.JSONDecodeError:
            continue
        candidates.append(parsed)
    for item in reversed(candidates):
        if isinstance(item, dict):
            yield item
            yield from nested_verdict_candidates(item)


def nested_verdict_candidates(item: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Reach a verdict that a CLI wrapped in its own envelope.

    Some backends print one JSON object holding the answer as a string; opencode
    streams JSONL events and carries the answer in `part.text`. Only these known
    carrier keys are followed, not every string in the payload.
    """
    for nested_key in ("result", "output", "message", "text"):
        nested = item.get(nested_key)
        if isinstance(nested, str):
            yield from verdict_candidates(nested)
    part = item.get("part")
    if isinstance(part, dict):
        yield part
        yield from nested_verdict_candidates(part)


def extract_and_validate_verdict(raw: bytes) -> dict[str, Any]:
    errors: list[str] = []
    verdict_shaped_errors: list[str] = []
    for candidate in verdict_candidates(raw.decode("utf-8", errors="replace")):
        try:
            return validate_verdict(candidate)
        except SchemaError as exc:
            errors.append(str(exc))
            # A stream carries wrapper objects too. Reporting the last one's error
            # hides the real defect: prefer whatever actually looked like a verdict.
            if "verdict" in candidate:
                verdict_shaped_errors.append(str(exc))
    ranked = verdict_shaped_errors or errors
    detail = ranked[0] if ranked else "no JSON object found"
    raise SchemaError(f"review verdict failed validation: {detail}")


def review_prompt(packet: dict[str, Any]) -> bytes:
    return (
        NO_YES_MAN_CONTRACT
        + "\n## Independent review packet\n```json\n"
        + json.dumps(packet, ensure_ascii=False, indent=2)
        + "\n```\n"
    ).encode("utf-8")


def strip_schema_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: strip_schema_metadata(item) for key, item in value.items() if key not in {"$schema", "$id", "$comment"}}
    if isinstance(value, list):
        return [strip_schema_metadata(item) for item in value]
    return value


def provider_inline_schema() -> dict[str, Any]:
    """Verdict schema shaped for a CLI that inlines it as a structured-output spec.

    The declared 2020-12 meta-schema reference is dropped: one provider refuses the
    whole schema with `no schema with key or ref "https://json-schema.org/draft/2020-12/schema"`
    because it cannot resolve that URL. Only annotations are removed, never a
    validation keyword, and local enforcement still runs against the source file.
    """
    schema = load_json(REVIEW_VERDICT_SCHEMA, "review verdict schema")
    stripped = strip_schema_metadata(schema)
    if not isinstance(stripped, dict) or "properties" not in stripped or "required" not in stripped:
        raise SchemaError("inline verdict schema lost its validation keywords")
    return stripped


def agy_prompt(reviewer: bool, input_destination: str) -> str:
    """Instruction passed as the AGY prompt argument.

    Carries the contract and a path, never the packet itself, so nothing sensitive
    reaches the host process list.
    """
    if not reviewer:
        raise SchemaError("agy is a read-only reviewer backend")
    return (
        NO_YES_MAN_CONTRACT
        + f"\nThe review packet is the JSON file at {input_destination}. Read that file, then reply with "
        "the verdict JSON object only. Do not restate the packet, and do not write to any file.\n"
    )


def require_binary(name: str) -> Path:
    found = shutil.which(name)
    if found is None:
        raise DependencyError(f"required executable unavailable: {name}")
    return Path(found).resolve()


def runtime_protected_paths(*, install_root: bool = False) -> tuple[Path, ...]:
    protected = [ROOT, ROOT / ".runtime", ROOT / "tasks", ROOT / "_shared", ROOT / "bin", ROOT / ".opencode"]
    override = runtime_root_override()
    if override is not None:
        protected.append(override)
    raw_auth = os.environ.get("MULTIAGENT_AUTH_DIR")
    if raw_auth:
        protected.append(Path(raw_auth).resolve(strict=False))
    protected.extend(Path.home() / name for name in (".ssh", ".gnupg", ".aws", ".claude", ".codex"))
    if install_root:
        protected.append(Path.home() / ".opencode")
    return tuple(protected)


def paths_overlap(first: Path, second: Path) -> bool:
    return is_within(first, second) or is_within(second, first)


def validate_runtime_install_root(root: Path, label: str) -> Path:
    resolved = root.resolve()
    if not resolved.is_dir() or resolved == Path("/") or resolved == Path.home().resolve():
        raise GateError(f"{label} runtime install root is not a safe minimal directory")
    if test_mode() and resolved.parent == MOCK_RUNTIME_ROOT.resolve() and not resolved.is_symlink():
        return resolved
    if any(paths_overlap(resolved, protected) for protected in runtime_protected_paths(install_root=True)):
        raise GateError(f"{label} runtime install root overlaps protected storage")
    return resolved


def validate_runtime_binary(binary: Path, label: str) -> Path:
    resolved = binary.resolve()
    if not resolved.is_file() or any(is_within(resolved, protected) for protected in runtime_protected_paths()):
        raise GateError(f"{label} runtime executable overlaps protected storage")
    return resolved


def script_shebang(binary: Path) -> list[str] | None:
    try:
        with binary.open("rb") as handle:
            first_line = handle.readline(512)
    except OSError:
        return None
    if not first_line.startswith(b"#!"):
        return None
    try:
        tokens = shlex.split(first_line[2:].decode("utf-8", errors="strict"))
    except ValueError as exc:
        raise DependencyError("runtime script has an unsafe shebang") from exc
    if not tokens:
        raise DependencyError("runtime script has an empty shebang")
    return tokens


def script_runtime_root(script: Path) -> Path:
    for ancestor in script.parents:
        if (ancestor / "node_modules").is_dir() and is_within(script, ancestor / "node_modules"):
            return validate_runtime_install_root(ancestor, script.name)
        if (ancestor / "package.json").is_file() or ((ancestor / "pyvenv.cfg").is_file() and script.parent.name == "bin"):
            return validate_runtime_install_root(ancestor, script.name)
    raise DependencyError("runtime script package root was not found")


def script_interpreter(shebang: list[str]) -> Path:
    executable = shebang[0]
    if Path(executable).name == "env":
        arguments = shebang[1:]
        if arguments[:1] == ["-S"]:
            arguments = shlex.split(arguments[1]) if len(arguments) == 2 else []
        if len(arguments) != 1 or arguments[0].startswith("-"):
            raise DependencyError("runtime script uses an unsupported env shebang")
        return require_binary(arguments[0])
    if not os.path.isabs(executable):
        raise DependencyError("runtime script interpreter must be absolute or env-resolved")
    candidate = Path(executable).resolve()
    if not candidate.is_file():
        raise DependencyError("runtime script interpreter is unavailable")
    return candidate


def script_runtime_binding(name: str, script: Path, shebang: list[str]) -> RuntimeBinding:
    root = script_runtime_root(script)
    relative = script.relative_to(root)
    destination_root = f"/opt/multiagent/{name}-runtime"
    interpreter = script_interpreter(shebang)
    mounts = [MountSpec(str(root), destination_root, "ro")]
    if is_within(interpreter, Path("/usr")) or is_within(interpreter, Path("/bin")):
        prefix = [str(interpreter), f"{destination_root}/{relative.as_posix()}"]
    else:
        interpreter = validate_runtime_binary(interpreter, name)
        interpreter_destination = f"/opt/multiagent/bin/{name}-interpreter"
        mounts.append(MountSpec(str(interpreter), interpreter_destination, "ro"))
        prefix = [interpreter_destination, f"{destination_root}/{relative.as_posix()}"]
    return RuntimeBinding(prefix, mounts)


def add_directory_chain(mounts: list[MountSpec], destination: str) -> None:
    current = PurePosixPath(destination)
    parents: list[str] = []
    while str(current) not in {"/", "."}:
        parents.append(str(current))
        current = current.parent
    existing = {mount.destination for mount in mounts}
    for parent in reversed(parents):
        if parent not in existing:
            mounts.append(MountSpec(None, parent, "dir"))
            existing.add(parent)


def add_ro_mount(mounts: list[MountSpec], source: Path | str, destination: str, *, ensure_destination: bool = True) -> None:
    source_path = Path(source)
    if not source_path.exists():
        return
    if ensure_destination:
        add_directory_chain(mounts, str(PurePosixPath(destination).parent))
    mounts.append(MountSpec(str(source_path), destination, "ro"))


def add_rw_mount(mounts: list[MountSpec], source: Path, destination: str, *, ensure_destination: bool = False) -> None:
    if ensure_destination:
        add_directory_chain(mounts, str(PurePosixPath(destination).parent))
    mounts.append(MountSpec(str(source), destination, "rw"))


def add_injected_file(mounts: list[MountSpec], source: Path, destination: str) -> None:
    """Copy a host file into the sandbox instead of binding it.

    Bubblewrap reads the content through a file descriptor and materializes a fresh
    file inside the namespace, so the worker gets a writable copy while the host file
    is never bound and never writable from inside. Used for credentials that a CLI
    insists on rewriting, such as a refreshed OAuth token.
    """
    resolved = source.resolve(strict=False)
    if not resolved.is_absolute() or source.is_symlink() or not resolved.is_file():
        raise GateError("injected sandbox file must be an existing absolute non-symlink file")
    if resolved.stat().st_size > INJECTED_FILE_MAX_BYTES:
        raise GateError("injected sandbox file is larger than the credential-sized limit")
    add_directory_chain(mounts, str(PurePosixPath(destination).parent))
    mounts.append(MountSpec(str(resolved), destination, "file"))


def system_runtime_mounts() -> list[MountSpec]:
    mounts: list[MountSpec] = []
    for system_path in ("/usr", "/bin", "/lib", "/lib64"):
        add_ro_mount(mounts, system_path, system_path)
    for system_path in (
        "/etc/ssl",
        "/etc/ca-certificates",
        "/etc/resolv.conf",
        "/etc/hosts",
        "/etc/nsswitch.conf",
        "/etc/passwd",
        "/etc/group",
        "/etc/ld.so.cache",
        "/etc/ld.so.conf",
        "/etc/ld.so.conf.d",
    ):
        add_ro_mount(mounts, system_path, system_path)
    add_directory_chain(mounts, "/proc")
    mounts.append(MountSpec(None, "/proc", "proc"))
    add_directory_chain(mounts, "/dev")
    mounts.append(MountSpec(None, "/dev", "dev"))
    add_directory_chain(mounts, "/tmp")
    mounts.append(MountSpec(None, "/tmp", "tmpfs"))
    add_directory_chain(mounts, "/home")
    mounts.append(MountSpec(None, "/home", "tmpfs"))
    add_directory_chain(mounts, "/home/worker")
    add_directory_chain(mounts, "/workspace")
    add_directory_chain(mounts, "/input")
    add_directory_chain(mounts, "/opt/multiagent/bin")
    return mounts


def safe_mock_binary(name: str) -> Path:
    binary = require_binary(name)
    if not is_within(binary, MOCK_BIN):
        raise DependencyError("test mode requires fixture executable from tests/fixtures/mock-bin")
    return binary


def codex_runtime_binding() -> RuntimeBinding:
    if test_mode():
        source = safe_mock_binary("codex")
        return RuntimeBinding(["/opt/multiagent/bin/codex"], [MountSpec(str(source), "/opt/multiagent/bin/codex", "ro")])
    script = require_binary("codex")
    for ancestor in script.parents:
        node = ancestor / "bin" / "node"
        module_root = ancestor / "lib" / "node_modules"
        if node.is_file() and module_root.is_dir() and is_within(script, module_root):
            node = validate_runtime_binary(node, "codex")
            module_root = validate_runtime_install_root(module_root, "codex")
            relative = script.relative_to(ancestor)
            destination_root = "/opt/multiagent/codex-runtime"
            return RuntimeBinding(
                [destination_root + "/bin/node", destination_root + "/" + relative.as_posix()],
                [
                    MountSpec(str(node), destination_root + "/bin/node", "ro"),
                    MountSpec(str(module_root), destination_root + "/lib/node_modules", "ro"),
                ],
            )
    raise DependencyError("Codex runtime root with node and node_modules was not found")


def single_binary_binding(name: str) -> RuntimeBinding:
    if test_mode():
        source = safe_mock_binary(name)
        destination = f"/opt/multiagent/bin/{name}"
        return RuntimeBinding([destination], [MountSpec(str(source), destination, "ro")])
    source = validate_runtime_binary(require_binary(name), name)
    shebang = script_shebang(source)
    if shebang is not None:
        return script_runtime_binding(name, source, shebang)
    destination = f"/opt/multiagent/bin/{name}"
    return RuntimeBinding([destination], [MountSpec(str(source), destination, "ro")])


def runtime_binding(kind: str) -> RuntimeBinding:
    if kind == "codex":
        return codex_runtime_binding()
    if kind == "agy":
        return single_binary_binding("agy")
    if kind == "opencode":
        return single_binary_binding("opencode")
    if kind == "claude":
        return single_binary_binding("claude")
    raise SchemaError("backend kind is not allowlisted")


def add_auth_mount(mounts: list[MountSpec], environment: dict[str, str], backend: dict[str, Any]) -> None:
    raw = os.environ.get("MULTIAGENT_AUTH_DIR")
    if not raw:
        return
    source = Path(raw)
    if not source.is_absolute() or not source.is_dir() or source.is_symlink():
        raise GateError("MULTIAGENT_AUTH_DIR must be an existing absolute non-symlink directory")
    injection = CREDENTIAL_INJECTIONS.get(str(backend.get("kind")))
    if injection is None:
        # Every pinned backend declares the one credential file it needs. An
        # undeclared kind must not fall back to exposing the whole auth directory.
        raise GateError("backend kind declares no credential injection; refusing a blanket auth mount")
    # These CLIs keep credentials under a home directory they also write to, and they
    # fail with EROFS against a read-only bind. Copy the one credential file into the
    # sandbox home tmpfs instead: the worker may rewrite its copy, and the host auth
    # directory is never mounted at all.
    relative, destination, extra_environment = injection
    add_injected_file(mounts, source / relative, destination)
    environment.update(extra_environment)


def build_bwrap_plan(
    inner: list[str],
    cwd: str,
    mounts: list[MountSpec],
    environment: dict[str, str],
    *,
    isolate_network: bool = False,
    bwrap_binary: Path | None = None,
) -> SandboxPlan:
    bwrap = bwrap_binary or require_binary("bwrap")
    command = [str(bwrap), "--die-with-parent", "--new-session"]
    if isolate_network:
        command.append("--unshare-net")
    for mount in mounts:
        if mount.mode == "dir":
            command.extend(["--dir", mount.destination])
        elif mount.mode == "ro":
            command.extend(["--ro-bind", str(mount.source), mount.destination])
        elif mount.mode == "rw":
            command.extend(["--bind", str(mount.source), mount.destination])
        elif mount.mode == "tmpfs":
            command.extend(["--tmpfs", mount.destination])
        elif mount.mode == "file":
            # The descriptor number is unknown until launch; keep the plan readable
            # and resolve it in resolve_injected_fds right before exec.
            command.extend(["--file", FD_ARGUMENT_PREFIX + str(mount.source), mount.destination])
        elif mount.mode == "proc":
            command.extend(["--proc", mount.destination])
        elif mount.mode == "dev":
            command.extend(["--dev", mount.destination])
        else:
            raise SchemaError("unknown sandbox mount mode")
    for key, value in sorted(environment.items()):
        command.extend(["--setenv", key, value])
    command.extend(["--chdir", cwd, "--"])
    command.extend(inner)
    return SandboxPlan(
        command=command,
        mounts=mounts,
        cwd=cwd,
        environment=environment,
        network="isolated-network" if isolate_network else "host-network-not-isolated",
    )


def build_inner_command(backend: dict[str, Any], binding: RuntimeBinding, reviewer: bool, input_destination: str) -> list[str]:
    kind = backend.get("kind")
    model = backend.get("model")
    effort = backend.get("effort")
    if not isinstance(model, str):
        raise SchemaError("backend model must be a string")
    if kind == "codex":
        if effort not in {"high", "max"}:
            raise SchemaError("codex backend must pin high or max effort")
        # The dispatcher's own Bubblewrap layer is the enforcing boundary: the worker
        # sees only approved writable paths, so codex must not start a second sandbox
        # inside it. Its nested sandbox needs a writable workspace root and dies with
        # EROFS when only a scoped subdirectory is writable.
        expected_sandbox = "danger-full-access" if backend.get("access") == "workspace-write" else "read-only"
        if str(backend.get("sandbox")) != expected_sandbox:
            raise SchemaError("codex sandbox mode must match the pinned access model")
        command = binding.prefix + [
            "exec",
            "--model",
            model,
            "-c",
            f'model_reasoning_effort="{effort}"',
            "--sandbox",
            str(backend["sandbox"]),
            "--cd",
            "/workspace",
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
        ]
        if reviewer:
            command.extend(["--output-schema", "/input/review-verdict.schema.json"])
        return command + ["-"]
    if kind == "agy":
        if effort != "high":
            raise SchemaError("agy backend must pin high effort")
        # `--print` takes the prompt as its value, and this CLI stops parsing flags at
        # the first positional argument. Placing it anywhere but last silently feeds
        # the next flag in as the prompt and drops every flag after it, including the
        # model pin. It must stay last, and every other flag must precede it.
        command = binding.prefix + [
            "--model",
            model,
            "--effort",
            effort,
            "--mode",
            "plan",
            "--sandbox",
            "--disable-slash-commands",
            "--add-dir",
            "/input",
            "--output-format",
            "json",
        ]
        if reviewer:
            command.extend(["--json-schema", "/input/review-verdict.schema.json"])
        # The packet stays on the filesystem: putting a diff and test evidence in argv
        # would expose it in the host process list.
        return command + ["--print", agy_prompt(reviewer, input_destination)]
    if kind == "opencode":
        if effort != "max":
            raise SchemaError("opencode reviewer requires the pinned max variant")
        # `--file` is an array option: it swallows every positional that follows it,
        # so a trailing message becomes a second attachment path and the run dies with
        # "File not found: <message>". The message must precede --file. Note this is
        # the opposite of agy, whose prompt flag must come last — the ordering rule is
        # per CLI and only a live call proves it.
        # `--pure` is deliberately absent from a run. Under it every model on every
        # provider returns a server-side "Unexpected server error" — a free model and
        # an unrelated OAuth provider fail identically, so it is not quota, billing,
        # or one gateway. Dropping the flag makes the same call succeed. What it was
        # bought, keeping host config and plugins out of a worker, is supplied by the
        # sandbox's empty fake home, the same argument that removed `--bare` from the
        # Claude advisor. The flag still works for `--version` and `models`, which
        # need no session, and those probes keep it.
        return binding.prefix + [
            "run",
            "--model",
            model,
            "--variant",
            effort,
            "--format",
            "json",
            "--dir",
            "/workspace",
            # This CLI has no schema-enforcement flag, unlike codex --output-schema and
            # agy --json-schema, so the schema is attached as evidence and the shape is
            # demanded in the message. Extra keys are the observed failure: a live run
            # returned a well-formed verdict carrying an undeclared requirements
            # assessment, which additionalProperties false correctly rejected.
            OPENCODE_REVIEW_MESSAGE,
            "--file",
            input_destination,
            "/input/review-verdict.schema.json",
        ]
    if kind == "claude":
        # No --bare here. Its own help states that in bare mode "Anthropic auth is
        # strictly ANTHROPIC_API_KEY or apiKeyHelper via --settings (OAuth and keychain
        # are never read)", so an OAuth account cannot authenticate under it at all —
        # a live run returned "Not logged in" with the credential file present. What
        # bare mode buys, skipping hooks, plugins, auto-memory, and config discovery,
        # the sandbox already provides through an empty fake home; the one remaining
        # gap, CLAUDE.md auto-discovery from the workspace, is closed by not mounting a
        # target repository for this backend.
        return binding.prefix + [
            "--model",
            model,
            "--print",
            "--no-session-persistence",
            "--tools",
            "",
            "--permission-mode",
            "plan",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(provider_inline_schema(), separators=(",", ":")),
        ]
    raise SchemaError("backend kind is not allowlisted")


def base_sandbox_environment() -> dict[str, str]:
    return {
        "HOME": "/home/worker",
        "XDG_CACHE_HOME": "/home/worker/.cache",
        "XDG_CONFIG_HOME": "/home/worker/.config",
        "XDG_DATA_HOME": "/home/worker/.local/share",
        "PATH": "/opt/multiagent/bin:/usr/bin:/bin",
        "CI": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "NO_COLOR": "1",
    }


def build_sandbox_plan(
    backend: dict[str, Any],
    task_dir: Path,
    target: Path | None,
    writable: list[tuple[Path, str]],
    input_path: Path,
    reviewer: bool,
) -> SandboxPlan:
    mounts = system_runtime_mounts()
    binding = runtime_binding(str(backend.get("kind")))
    append_runtime_binding_mounts(mounts, binding)
    input_destination = "/input/review-input.json" if reviewer else "/input/worker-input.md"
    add_ro_mount(mounts, input_path, input_destination)
    if reviewer:
        add_ro_mount(mounts, REVIEW_VERDICT_SCHEMA, "/input/review-verdict.schema.json")
    environment = base_sandbox_environment()
    add_auth_mount(mounts, environment, backend)
    access = backend.get("access")
    scope = "none" if not writable else "write"
    if access == "workspace-write" and writable and writable[0][1] == "/workspace":
        add_rw_mount(mounts, writable[0][0], "/workspace", ensure_destination=False)
    else:
        # The Claude advisor runs without --bare, so it auto-discovers CLAUDE.md from
        # its working directory. A reviewed repository could steer the reviewer that
        # way, so this backend never receives the target at all: it judges from the
        # packet mounted under /input.
        if target is not None and str(backend.get("kind")) != "claude":
            add_ro_mount(mounts, target, "/workspace", ensure_destination=False)
        for source, destination in writable:
            add_rw_mount(mounts, source, destination, ensure_destination=False)
    inner = build_inner_command(backend, binding, reviewer, input_destination)
    plan = build_bwrap_plan(inner, "/workspace", mounts, environment)
    if any(mount.source == str(ROOT) or mount.source == str(private_runtime_dir()) for mount in plan.mounts):
        raise GateError("sandbox attempted to expose control plane")
    if scope == "none" and any(mount.mode == "rw" for mount in plan.mounts):
        raise GateError("read-only sandbox unexpectedly has writable mount")
    return plan


def child_environment(plan: SandboxPlan, backend: dict[str, Any]) -> dict[str, str]:
    environment = dict(plan.environment)
    if test_mode():
        for name, value in os.environ.items():
            if name.startswith("MOCK_") or name == "MULTIAGENT_TEST_MODE":
                environment[name] = value
        return environment
    if os.environ.get("MULTIAGENT_ALLOW_AUTH_ENV") == "1":
        for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENCODE_API_KEY"):
            if name in os.environ:
                environment[name] = os.environ[name]
    return environment


def resolve_injected_fds(command: list[str]) -> tuple[list[str], tuple[int, ...]]:
    """Replace `@fd:` placeholders with descriptors opened read-only on the host.

    Bubblewrap copies the content out of the descriptor, so the child never receives
    a path to the host file. Callers must close the returned descriptors.
    """
    resolved: list[str] = []
    opened: list[int] = []
    try:
        for argument in command:
            if not argument.startswith(FD_ARGUMENT_PREFIX):
                resolved.append(argument)
                continue
            source = argument[len(FD_ARGUMENT_PREFIX) :]
            descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
            os.set_inheritable(descriptor, True)
            opened.append(descriptor)
            resolved.append(str(descriptor))
    except OSError as exc:
        for descriptor in opened:
            os.close(descriptor)
        raise GateError("injected sandbox file could not be opened for the sandbox") from exc
    return resolved, tuple(opened)


def run_limited(
    plan: SandboxPlan,
    prompt: bytes,
    timeout_s: int,
    backend: dict[str, Any],
    *,
    environment: dict[str, str] | None = None,
) -> RunResult:
    started = time.monotonic()
    command, injected = resolve_injected_fds(plan.command)
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=ROOT,
            env=environment if environment is not None else child_environment(plan, backend),
            start_new_session=True,
            pass_fds=injected,
        )
    except FileNotFoundError as exc:
        raise DependencyError("sandbox executable disappeared before launch") from exc
    finally:
        for descriptor in injected:
            os.close(descriptor)
    try:
        stdout, stderr = process.communicate(input=prompt, timeout=timeout_s)
        return RunResult(process.returncode, stdout, stderr, time.monotonic() - started, False)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
        return RunResult(124, stdout, stderr, time.monotonic() - started, True)


def shell_status(returncode: int) -> int:
    return returncode if returncode >= 0 else 128 + abs(returncode)


class WriterLock:
    """flock releases with process exit; stale path files are harmless."""

    def __init__(self) -> None:
        self.path = private_runtime_child("locks") / "codex-sol.lock"
        self.descriptor: int | None = None

    def __enter__(self) -> "WriterLock":
        self.descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self.descriptor)
            self.descriptor = None
            raise GateError("codex-sol writer already active; concurrent writers are forbidden") from exc
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        if self.descriptor is not None:
            fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            os.close(self.descriptor)
            self.descriptor = None


def new_run_id() -> str:
    timestamp = utc_now().replace("-", "").replace(":", "").replace("Z", "")
    return f"{timestamp}-{os.getpid()}-{time.monotonic_ns()}"


def packet_bytes(input_path: Path, reviewer: bool) -> bytes:
    if reviewer:
        return review_prompt(validate_review_input(input_path))
    try:
        return input_path.read_bytes()
    except OSError as exc:
        raise GateError("worker input is unreadable") from exc


def packet_file(prompt: bytes) -> Path:
    runtime = private_runtime_child("packets")
    descriptor, name = tempfile.mkstemp(prefix="packet-", suffix=".md", dir=runtime)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(prompt)
    return Path(name)


def startup_bwrap_binary() -> Path:
    found = shutil.which("bwrap", path=os.defpath)
    if found is None:
        raise DependencyError("real Bubblewrap executable unavailable for startup probe")
    binary = Path(found).resolve()
    if is_within(binary, MOCK_BIN):
        raise DependencyError("startup probe refuses a test Bubblewrap adapter")
    return binary


def startup_probe_environment() -> dict[str, str]:
    environment = base_sandbox_environment()
    if test_mode():
        for name, value in os.environ.items():
            if name.startswith("MOCK_STARTUP_"):
                environment[name] = value
    return environment


def startup_arguments(kind: str) -> list[str]:
    if kind == "claude":
        return ["--version"]
    if kind == "opencode":
        return ["--pure", "--version"]
    if kind in {"codex", "agy"}:
        return ["--version"]
    raise SchemaError("startup probe backend kind is not allowlisted")


def append_runtime_binding_mounts(mounts: list[MountSpec], binding: RuntimeBinding) -> None:
    for mount in binding.mounts:
        add_directory_chain(mounts, str(PurePosixPath(mount.destination).parent))
        mounts.append(mount)


def build_startup_probe_plan(kind: str) -> SandboxPlan:
    binding = runtime_binding(kind)
    mounts = system_runtime_mounts()
    append_runtime_binding_mounts(mounts, binding)
    if any(mount.destination == "/auth" for mount in mounts):
        raise GateError("startup probe may not mount authentication")
    if any(mount.source == str(ROOT) or mount.source == str(ROOT / ".runtime") for mount in mounts):
        raise GateError("startup probe attempted to expose control plane")
    return build_bwrap_plan(
        binding.prefix + startup_arguments(kind),
        "/workspace",
        mounts,
        startup_probe_environment(),
        isolate_network=True,
        bwrap_binary=startup_bwrap_binary(),
    )


def execute_startup_probe(kind: str) -> StartupProbe:
    try:
        plan = build_startup_probe_plan(kind)
        result = run_limited(plan, b"", 15, {"kind": "startup-probe"}, environment=plan.environment)
    except ControlledError:
        return StartupProbe("unavailable_fail_closed", "sandbox runtime binding unavailable")
    if result.timed_out:
        return StartupProbe("unavailable_fail_closed", "sandbox --version startup timed out")
    if result.returncode != 0:
        return StartupProbe("unavailable_fail_closed", f"sandbox --version exited {shell_status(result.returncode)}; stderr redacted")
    return StartupProbe("available", "isolated-network fake-HOME no-auth --version completed")


def runtime_startup_probe(kind: str, cache: PreflightCache) -> StartupProbe:
    if kind not in cache.startup:
        cache.startup[kind] = execute_startup_probe(kind)
    return cache.startup[kind]


def probe_with_status(
    command: list[str],
    *,
    environment: dict[str, str] | None = None,
    timeout: int = PROBE_TIMEOUT_SECONDS,
) -> tuple[bool, str, str]:
    """Run a local probe and report why it failed.

    Status is one of `ok`, `exit` (ran and refused), `timeout`, or `missing`.
    Callers that must distinguish a slow or absent local CLI from a CLI that ran
    and rejected the request use this; `run_probe` keeps the two-value contract.
    """
    # Probe commands run in dispatcher process, never in a worker sandbox. Some
    # local catalogs require HOME to locate their non-secret model metadata.
    env = os.environ.copy()
    env.pop("BASH_ENV", None)
    env.pop("ENV", None)
    env["CI"] = "1"
    env["NO_COLOR"] = "1"
    if environment:
        env.update(environment)
    try:
        result = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return False, "", "timeout"
    except OSError:
        return False, "", "missing"
    if result.returncode != 0:
        return False, result.stdout, "exit"
    return True, result.stdout, "ok"


def run_probe(command: list[str], *, environment: dict[str, str] | None = None) -> tuple[bool, str]:
    ok, output, _status = probe_with_status(command, environment=environment)
    return ok, output


def exact_catalog_line(output: str, model: str) -> bool:
    return any(line.strip() == model for line in output.splitlines())


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


def all_flags_present(help_text: str, flags: Iterable[str]) -> list[str]:
    return [flag for flag in flags if flag not in help_text]


def cli_contracts(cache: PreflightCache | None = None) -> dict[str, dict[str, Any]]:
    active_cache = cache or PreflightCache()
    if active_cache.contracts is not None:
        return active_cache.contracts
    claude_ok, claude_help = run_probe(["claude", "--help"])
    codex_ok, codex_help = run_probe(["codex", "exec", "--help"])
    agy_ok, agy_help = run_probe(["agy", "--help"])
    open_ok, open_help = run_probe(["opencode", "run", "--help"])
    active_cache.contracts = {
        "claude-main": {
            "ok": claude_ok,
            "missing": all_flags_present(claude_help, ("--model", "--effort", "--add-dir")),
        },
        "fable-advisor": {
            "ok": claude_ok,
            "missing": all_flags_present(
                claude_help,
                ("--model", "--print", "--no-session-persistence", "--tools", "--permission-mode", "--output-format", "--json-schema"),
            ),
        },
        "codex-sol": {
            "ok": codex_ok,
            "missing": all_flags_present(
                codex_help,
                ("--model", "--config", "--sandbox", "--cd", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "--ignore-rules"),
            ),
        },
        "codex-terra": {
            "ok": codex_ok,
            "missing": all_flags_present(
                codex_help,
                ("--model", "--config", "--sandbox", "--cd", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--output-schema"),
            ),
        },
        "agy": {
            "ok": agy_ok,
            "missing": all_flags_present(
                agy_help,
                ("--print", "--model", "--effort", "--mode", "--sandbox", "--disable-slash-commands", "--add-dir", "--output-format", "--json-schema"),
            ),
        },
        "codex-luna": {
            "ok": codex_ok,
            "missing": all_flags_present(
                codex_help,
                ("--model", "--config", "--sandbox", "--cd", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--output-schema"),
            ),
        },
        "deepseek-reviewer": {
            "ok": open_ok,
            "missing": all_flags_present(open_help, ("--pure", "--model", "--variant", "--format", "--dir", "--file")),
        },
    }
    return active_cache.contracts


def cached_local_probe(cache: PreflightCache, key: str, command: list[str]) -> tuple[bool, str]:
    if key not in cache.local_probes:
        ok, output, _status = probe_with_status(command, timeout=CATALOG_PROBE_TIMEOUT_SECONDS)
        cache.local_probes[key] = (ok, output)
    return cache.local_probes[key]


def scope_sandbox_available() -> bool:
    mounts = system_runtime_mounts()
    plan = build_bwrap_plan(["/bin/true"], "/workspace", mounts, {"HOME": "/home/worker", "PATH": "/usr/bin:/bin"})
    result = run_limited(plan, b"", 10, {"kind": "probe"}, environment=plan.environment)
    return result.returncode == 0 and not result.timed_out


def backend_preflight(role: str, backend: dict[str, Any], cache: PreflightCache | None = None) -> BackendPreflight:
    active_cache = cache or PreflightCache()
    contracts = cli_contracts(active_cache)
    contract = contracts.get(role)
    kind = backend.get("kind")
    if not isinstance(kind, str):
        unavailable_startup = StartupProbe("unavailable_fail_closed", "sandbox runtime kind is invalid")
        return BackendPreflight("unavailable_fail_closed", "invalid backend kind", unavailable_startup, "not_attempted", "not_attempted", "not_checked")
    startup = runtime_startup_probe(kind, active_cache)
    if startup.status != "available":
        return BackendPreflight(
            "unavailable_fail_closed",
            f"sandbox startup probe failed: {startup.detail}",
            startup,
            "not_attempted",
            "not_attempted",
            "not_checked",
        )
    if not isinstance(contract, dict) or not contract.get("ok") or contract.get("missing"):
        missing = ",".join(contract.get("missing", [])) if isinstance(contract, dict) else "unknown"
        return BackendPreflight("unavailable_fail_closed", f"missing CLI contract flags: {missing}", startup, "not_attempted", "not_attempted", "not_checked")
    if role == "agy":
        ok, catalog = cached_local_probe(active_cache, "agy-models", ["agy", "models"])
        if not ok or not exact_catalog_line(catalog, str(backend.get("model"))):
            return BackendPreflight("unavailable_fail_closed", "exact AGY model is absent from catalog", startup, "unverified", "unverified", "catalog_unavailable")
        return BackendPreflight("available_pending_auth", "exact catalog token and CLI contract verified; endpoint/auth unverified", startup, "unverified", "unverified", "catalog_verified")
    if kind == "opencode":
        # The pin is `provider/model`. Deriving both from it keeps a second opencode
        # worker a configuration change instead of another role-named branch.
        provider, separator, model_id = str(backend.get("model", "")).partition("/")
        if not separator or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", provider) or not model_id:
            return BackendPreflight("unavailable_fail_closed", "opencode model pin must be provider/model", startup, "unverified", "unverified", "model_unavailable")
        ok, catalog = cached_local_probe(active_cache, f"opencode-models:{provider}", ["opencode", "--pure", "models", provider, "--verbose"])
        metadata = opencode_model_metadata(catalog, model_id) if ok else None
        variants = metadata.get("variants", {}) if isinstance(metadata, dict) else {}
        if not isinstance(variants, dict) or backend.get("effort") not in variants:
            return BackendPreflight("unavailable_fail_closed", "pinned opencode variant absent from catalog; automatic variant substitution forbidden", startup, "unverified", "unverified", "variant_unavailable")
        return BackendPreflight("available_pending_auth", "exact opencode model and variant plus CLI contract verified; endpoint/auth unverified", startup, "unverified", "unverified", "variant_verified")
    if role in {"codex-sol", "codex-terra", "codex-luna"}:
        return BackendPreflight("endpoint_unverified", "exact argv contract verified; endpoint/auth intentionally uncalled", startup, "unverified", "unverified", "unverified")
    if role == "fable-advisor":
        return BackendPreflight("endpoint_unverified", "exact Fable argv contract verified; endpoint/auth intentionally uncalled", startup, "unverified", "unverified", "unverified")
    return BackendPreflight("unavailable_fail_closed", "unknown worker", startup, "not_attempted", "not_attempted", "not_checked")


def invariant_issues(backends: dict[str, Any] | None = None) -> list[str]:
    data = backends if backends is not None else load_backends()
    workers = data.get("workers")
    expected = {"codex-sol", "codex-terra", "codex-luna", "agy", "deepseek-reviewer", "fable-advisor"}
    issues: list[str] = []
    orchestrator = data.get("orchestrator")
    if not isinstance(orchestrator, dict) or orchestrator.get("model_id") != "claude-opus-5" or orchestrator.get("effort") != "high":
        issues.append("main exact model or effort pin drift")
    if not isinstance(workers, dict) or set(workers) != expected:
        issues.append("worker inventory must contain exactly six named workers and no claude-main worker")
        return issues
    pins = {
        "codex-sol": ("codex", "codex", "gpt-5.6-sol", "high", "workspace-write"),
        "codex-terra": ("codex", "codex", "gpt-5.6-terra", "max", "read-only"),
        "agy": ("agy", "agy", "gemini-3.1-pro-high", "high", "read-only"),
        "codex-luna": ("codex", "codex", "gpt-5.6-luna", "max", "read-only"),
        "deepseek-reviewer": ("opencode", "opencode", "opencode/deepseek-v4-flash", "max", "read-only"),
        "fable-advisor": ("claude", "claude", "claude-fable-5", None, "read-only"),
    }
    for role, (kind, command, model, effort, access) in pins.items():
        backend = workers[role]
        if backend.get("kind") != kind or backend.get("command") != command or backend.get("model") != model or backend.get("access") != access:
            issues.append(f"{role} kind/command/model/access pin drift")
        if effort is not None and backend.get("effort") != effort:
            issues.append(f"{role} effort pin drift")
        if backend.get("fallbacks") != [] or backend.get("fallback_policy") != "forbid":
            issues.append(f"{role} fallback policy drift")
    if workers["codex-sol"].get("sandbox") != "danger-full-access":
        issues.append("codex-sol sandbox drift")
    if workers["codex-terra"].get("sandbox") != "read-only":
        issues.append("codex-terra sandbox drift")
    if not all(workers[role].get("requires_no_yes_man") is True for role in expected - {"codex-sol"}):
        issues.append("reviewer no-yes-man contract drift")
    fable = workers["fable-advisor"]
    if not (fable.get("tools") == "disabled" and fable.get("session_persistence") == "disabled"):
        issues.append("fable advisor constraint drift")
    required = (
        ROOT / "CLAUDE.md",
        ROOT / "README.md",
        ROOT / "_shared" / "routing.md",
        ROOT / "_shared" / "approval-policy.md",
        ROOT / "_shared" / "orchestrator-policy.md",
        ROOT / "_shared" / "no-yes-man.md",
        ROOT / "_templates" / "task.md",
        ROOT / "_templates" / "context.md",
        ROOT / "_templates" / "log.md",
        ROOT / "_templates" / "worker-input.md",
        ROOT / "_templates" / "worker-output.md",
        REVIEW_INPUT_SCHEMA,
        REVIEW_VERDICT_SCHEMA,
        ROOT / "bin" / "claude-main",
    )
    for path in required:
        if not path.is_file():
            issues.append(f"required artifact missing: {path.relative_to(ROOT)}")
    try:
        for schema_path in (REVIEW_INPUT_SCHEMA, REVIEW_VERDICT_SCHEMA):
            require_supported_schema(load_json(schema_path, schema_path.name), schema_path.name)
    except SchemaError as exc:
        issues.append(str(exc))
    launcher = (ROOT / "bin" / "claude-main")
    if launcher.is_file():
        text = launcher.read_text(encoding="utf-8")
        if (
            "--model claude-opus-5" not in text
            or "--effort high" not in text
            or "CLAUDE_MAIN_EFFORT" in text
            or '"$@"' in text
        ):
            issues.append("main launcher pin drift")
    readme = ROOT / "README.md"
    if readme.is_file():
        text = readme.read_text(encoding="utf-8")
        required_argv = (
            "claude --model claude-opus-5 --effort high --add-dir <root>",
            'codex exec --model gpt-5.6-sol -c model_reasoning_effort="high" --sandbox danger-full-access',
            'codex exec --model gpt-5.6-terra -c model_reasoning_effort="max" --sandbox read-only',
            "agy --model gemini-3.1-pro-high --effort high --mode plan --sandbox --disable-slash-commands --add-dir /input --output-format json ... --print <instruction>",
            'codex exec --model gpt-5.6-luna -c model_reasoning_effort="max" --sandbox read-only',
            "opencode run --model opencode/deepseek-v4-flash --variant max",
            'claude --model claude-fable-5 --print --tools "" --no-session-persistence',
        )
        if any(item not in text for item in required_argv) or "--model opus" in text:
            issues.append("README invocation mapping drift")
    source = Path(__file__).read_text(encoding="utf-8")
    if "--ro-bind /" + " /" in source:
        issues.append("dispatcher exposes whole root")
    if "ev" + "al(" in source:
        issues.append("dispatcher contains prohibited dynamic evaluator")
    return issues


def enforce_runtime_invariants() -> None:
    issues = invariant_issues()
    if issues:
        raise GateError("runtime invariant gate failed: " + "; ".join(issues))


def require_backend_ready(role: str, backend: dict[str, Any]) -> None:
    probe = backend_preflight(role, backend)
    if probe.status == "unavailable_fail_closed":
        raise DependencyError(probe.detail)


def auth_opt_in_enabled() -> bool:
    return bool(os.environ.get("MULTIAGENT_AUTH_DIR")) or os.environ.get("MULTIAGENT_ALLOW_AUTH_ENV") == "1"


def require_secret_access_approval(control: dict[str, Any]) -> None:
    if auth_opt_in_enabled() and "secret_access" not in control["requested_actions"]:
        raise GateError("explicit auth opt-in requires declared and separately approved secret_access action")


def require_explicit_auth_for_real_call() -> None:
    if test_mode():
        return
    if auth_opt_in_enabled():
        return
    raise GateError("real call requires explicit MULTIAGENT_AUTH_DIR or MULTIAGENT_ALLOW_AUTH_ENV=1")


def reported_token_total(kind: str, stdout: bytes, stderr: bytes) -> int | None:
    """Total tokens the backend says it spent, or None when it does not say.

    Defect 11 in `tasks/INDEX.md` is a run that exited zero having answered nothing:
    the provider reported zero tokens and emitted no content. A reviewer's verdict
    contract catches that, but `codex-sol` has none, so its live observation would
    otherwise rest on the exit code alone.

    Measured 2026-08-06 against every saved run. Each of the four backends reports, and
    no two report alike: codex prints `tokens used` and the count on the next line of
    stderr, opencode carries per-step counts in its JSONL stdout, and agy and the Claude
    CLI each put a `usage` object in their single stdout JSON under different key names.
    A first pass at this grepped only stderr and concluded agy and Claude reported
    nothing, which was wrong — the Claude CLI's `modelUsage` is the very evidence issue 1
    is built on.

    None means unknown, never zero: a backend that stops reporting must not read as a
    backend that answered nothing.
    """
    if kind == "codex":
        text = stderr.decode("utf-8", errors="replace")
        matches = re.findall(r"^tokens used\s*\r?\n\s*([\d,]+)\s*$", text, re.MULTILINE)
        if not matches:
            return None
        return int(matches[-1].replace(",", ""))
    if kind == "opencode":
        total: int | None = None
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            counts = event.get("part", {}).get("tokens") if isinstance(event.get("part"), dict) else None
            if not isinstance(counts, dict):
                continue
            step = sum(value for key, value in counts.items() if key in {"input", "output", "reasoning"} and isinstance(value, int))
            total = step if total is None else total + step
        return total
    if kind in {"agy", "claude"}:
        try:
            payload = json.loads(stdout.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return None
        usage = payload.get("usage") if isinstance(payload, dict) else None
        if not isinstance(usage, dict):
            return None
        # Count only what the model consumed and produced. A cache read is work the
        # provider skipped, so it must not vouch for a turn that generated nothing.
        counted = {"input_tokens", "output_tokens", "thinking_tokens", "cache_creation_input_tokens"}
        return sum(value for key, value in usage.items() if key in counted and isinstance(value, int))
    return None


def finish_dispatch(
    plan: SandboxPlan,
    prompt: bytes,
    timeout_s: int,
    backend: dict[str, Any],
    output_dir: Path,
    log_path: Path,
    start_event: dict[str, Any],
    reviewer: bool,
) -> int:
    result = run_limited(plan, prompt, timeout_s, backend)
    (output_dir / "raw-output.txt").write_bytes(result.stdout)
    (output_dir / "raw-stderr.txt").write_bytes(result.stderr)
    verification = {
        "worker": start_event["worker"],
        "model": start_event["model"],
        "child_exit_code": result.returncode,
        "dispatch_timed_out": result.timed_out,
        "duration_ms": round(result.duration_s * 1000),
        "raw_output": "saved",
        "raw_stderr": "saved",
        "review_contract": "not_applicable",
    }
    if result.returncode != 0:
        verification["status"] = "timeout" if result.timed_out else "worker_error"
        append_audit_mirror(log_path, "ERROR", verification)
        print(json.dumps({"status": verification["status"], "role": start_event["worker"], "exit_code": result.returncode}))
        return shell_status(result.returncode)
    if reviewer:
        try:
            verdict = extract_and_validate_verdict(result.stdout)
        except SchemaError:
            verification["status"] = "invalid_review_verdict"
            verification["review_contract"] = "failed"
            append_audit_mirror(log_path, "ERROR", verification)
            raise
        (output_dir / "review-verdict.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        verification["review_contract"] = "passed"
    verification["status"] = "ok"
    # An exit code says the process ended, not that a model answered. Where the backend
    # reports what it spent, an explicit zero means it answered nothing, and crediting
    # the pin for that would put `observed_reachable` on a run with no evidence behind
    # it.
    #
    # An absent count is weaker evidence than a zero, and how much weaker depends on what
    # else vouched for the run. A reviewer's verdict already did, so unknown stays
    # unknown there — refusing would strand any backend that stops reporting. A
    # non-reviewer has nothing else, and the shape is not hypothetical: the defect-4 agy
    # run exited zero reporting no count, because `--print` had swallowed
    # `--output-format json` and turned the JSON into prose. Today `codex-sol` is the
    # only non-reviewer, and keying on the contract rather than on its name means a
    # future one inherits the refusal instead of quietly missing it.
    tokens = reported_token_total(str(backend.get("kind")), result.stdout, result.stderr)
    verification["reported_tokens"] = "unreported" if tokens is None else tokens
    withheld = "withheld_zero_reported_tokens" if tokens == 0 else (
        "withheld_unreadable_token_count" if tokens is None and not reviewer else None
    )
    if withheld:
        verification["live_observation"] = withheld
        append_audit_mirror(log_path, "VERIFICATION", verification)
        print(json.dumps({"status": "ok", "role": start_event["worker"], "raw_output": "saved", "live_observation": "withheld"}))
        return 0
    verification["live_observation"] = "recorded"
    record_live_observation(str(start_event["worker"]), backend, str(start_event["run"]))
    append_audit_mirror(log_path, "VERIFICATION", verification)
    print(json.dumps({"status": "ok", "role": start_event["worker"], "raw_output": "saved"}))
    return 0


def dispatch(arguments: argparse.Namespace) -> int:
    enforce_runtime_invariants()
    backend = backend_for(arguments.role)
    task = project_path(arguments.task, "task")
    input_path = project_path(arguments.input, "worker input")
    task_dir = task.parent
    if not is_within(input_path, task_dir):
        raise GateError("worker input must remain in task directory")
    control = extract_control(task)
    access = backend.get("access")
    if access not in {"read-only", "workspace-write"}:
        raise SchemaError("backend access policy is invalid")
    if access == "workspace-write" and control["workflow_stage"] != "producer":
        raise GateError("writer can run only during producer stage")
    if access == "read-only" and control["workflow_stage"] not in {"review", "advisory"}:
        raise GateError("read-only worker can run only during review or advisory stage")
    target = target_repo_for(control, backend)
    scope = control["write_scope"]
    writable = scope_paths(scope, target, task_dir, str(access))
    log_path = task_member(task_dir, "log.md", label="task audit log")
    if log_path.is_symlink():
        raise GateError("task audit log may not be a symlink")
    require_authorization(control, task, log_path, arguments.role, scope, target)
    require_secret_access_approval(control)
    require_backend_ready(arguments.role, backend)
    reviewer = bool(backend.get("requires_no_yes_man"))
    prompt = packet_bytes(input_path, reviewer)
    packet_path: Path | None = packet_file(prompt) if backend.get("kind") == "opencode" else None
    sandbox_input = packet_path or input_path
    try:
        plan = build_sandbox_plan(backend, task_dir, target, writable, sandbox_input, reviewer)
        if arguments.dry_run:
            print(json.dumps({"status": "dry_run", "role": arguments.role, "command": plan.command, "sandbox": plan.as_dict()}))
            return 0
        if os.environ.get("MULTIAGENT_ALLOW_BILLABLE") != "1":
            raise GateError("set MULTIAGENT_ALLOW_BILLABLE=1 only after human approval")
        require_explicit_auth_for_real_call()
        timeout_s = arguments.timeout if arguments.timeout is not None else backend.get("timeout_s", 600)
        if not isinstance(timeout_s, int) or timeout_s < 1 or timeout_s > 3600:
            raise GateError("timeout must be an integer from 1 through 3600")
        run_id = new_run_id()
        output_dir = task_member(task_dir, "workers", arguments.role, "runs", run_id, label="worker output")
        output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        start_event = {
            "worker": arguments.role,
            "model": backend.get("model"),
            "effort": backend.get("effort", "none"),
            "access": access,
            "write_scope": scope,
            "target_repo": "bound" if target else "none",
            "timeout_s": timeout_s,
            "input_kind": "review" if reviewer else "worker",
            "run": run_id,
            "authority": "runtime-journal",
        }
        append_audit_mirror(log_path, "WORKER_CALL", start_event)
        if access == "workspace-write":
            with WriterLock():
                return finish_dispatch(plan, prompt, timeout_s, backend, output_dir, log_path, start_event, reviewer)
        return finish_dispatch(plan, prompt, timeout_s, backend, output_dir, log_path, start_event, reviewer)
    finally:
        if packet_path is not None:
            packet_path.unlink(missing_ok=True)


def confirm_approval(arguments: argparse.Namespace) -> None:
    if not arguments.confirm:
        raise GateError("approve requires --confirm")
    if not sys.stdin.isatty():
        raise GateError("approve requires an interactive TTY")
    response = input("Type APPROVE to record authoritative approval: ")
    if response != "APPROVE":
        raise GateError("approval confirmation not received")


def approve(arguments: argparse.Namespace) -> int:
    enforce_runtime_invariants()
    confirm_approval(arguments)
    backend = backend_for(arguments.role)
    task = project_path(arguments.task, "task")
    control = extract_control(task)
    target = target_repo_for(control, backend)
    scope = control["write_scope"]
    scope_paths(scope, target, task.parent, str(backend.get("access")))
    if not planned_worker(control, arguments.role, scope):
        raise GateError("workers_approved must declare requested role and write_scope before approval")
    action = arguments.action
    if action is not None and (action not in PROTECTED_ACTIONS or action not in control["requested_actions"]):
        raise GateError("approval action must be declared protected requested action")
    event = {
        "version": 1,
        "kind": "action" if action else "worker",
        "task_key": task_key(task),
        "role": arguments.role,
        "write_scope": scope,
        "target_digest": target_digest(target),
        "approved_by": "human",
        "approved_at": utc_now(),
    }
    if action:
        event["action"] = action
    append_authoritative_event(task, event)
    log_path = task_member(task.parent, "log.md", label="task audit log")
    if log_path.is_file() and not log_path.is_symlink():
        append_audit_mirror(
            log_path,
            "APPROVAL",
            {
                "worker": arguments.role,
                "write_scope": scope,
                "target_repo": "bound" if target else "none",
                "action": action or "worker",
                "authority": "runtime-journal",
            },
        )
    print(json.dumps({"status": "approved", "role": arguments.role, "action": action or "worker"}))
    return 0


def preflight(arguments: argparse.Namespace) -> int:
    cache = PreflightCache()
    contracts = cli_contracts(cache)
    backends = load_backends().get("workers", {})
    statuses: dict[str, dict[str, Any]] = {}
    main_cache = Path.home() / ".claude" / "cache" / "changelog.md"
    main_evidence = main_cache.is_file() and "claude-opus-5" in main_cache.read_text(encoding="utf-8", errors="ignore")
    main_contract = contracts["claude-main"]
    main_startup = runtime_startup_probe("claude", cache)
    main_available = main_contract["ok"] and not main_contract["missing"] and main_evidence and main_startup.status == "available"
    statuses["claude-main"] = {
        "status": "endpoint_unverified" if main_available else "unavailable_fail_closed",
        "model": "claude-opus-5",
        "detail": "local changelog candidate, CLI flags, and sandbox startup verified; endpoint intentionally uncalled" if main_available else "exact local candidate evidence, CLI flags, or sandbox startup missing",
        "sandbox_startup": {"status": main_startup.status, "detail": main_startup.detail},
        "endpoint": "unverified" if main_startup.status == "available" else "not_attempted",
        "auth": "unverified" if main_startup.status == "available" else "not_attempted",
        "model_acceptance": "local_candidate_only" if main_evidence else "candidate_unavailable",
    }
    for role in ("codex-sol", "codex-terra", "codex-luna", "agy", "deepseek-reviewer", "fable-advisor"):
        backend = backends.get(role, {}) if isinstance(backends, dict) else {}
        probe = backend_preflight(role, backend, cache)
        observation = live_observation(role, backend) if isinstance(backend, dict) and backend else None
        entry = {
            "status": probe.status,
            "model": str(backend.get("model", "missing")),
            "detail": probe.detail,
            "sandbox_startup": {"status": probe.sandbox_startup.status, "detail": probe.sandbox_startup.detail},
            "endpoint": probe.endpoint,
            "auth": probe.auth,
            "model_acceptance": probe.model_acceptance,
        }
        if observation is not None and probe.sandbox_startup.status == "available":
            # A past run is evidence, never a current guarantee: the provider can
            # retire a model or expire a token after it was observed. Preflight still
            # spends nothing, so it reports when the pin last worked and leaves the
            # freshness judgement to the reader.
            if probe.status in {"endpoint_unverified", "available_pending_auth"}:
                entry["status"] = "available_live_observed"
                entry["detail"] = probe.detail + "; a prior real dispatch on this pin exited zero"
            entry["endpoint"] = "observed_reachable"
            entry["auth"] = "observed_accepted"
            entry["model_acceptance"] = "observed_accepted"
            entry["live_dispatch"] = {
                "status": "succeeded",
                "observed_at": observation.get("observed_at"),
                "run": observation.get("run"),
                "note": "historical observation for this exact pin; not revalidated by preflight",
            }
        else:
            entry["live_dispatch"] = {"status": "none_recorded_for_this_pin"}
        statuses[role] = entry
    statuses["scope-sandbox"] = {
        "status": "available" if scope_sandbox_available() else "unavailable_fail_closed",
        "model": "bubblewrap",
        "detail": "minimal filesystem layout smoke-tested; network remains host-visible for APIs",
    }
    print(
        json.dumps(
            {
                "model_calls": 0,
                "cache_scope": "single_invocation_only",
                "cli_contracts": contracts,
                "backends": statuses,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    unavailable = [name for name, state in statuses.items() if state["status"].startswith("unavailable")]
    if unavailable and not arguments.allow_unavailable:
        return 1
    return 0


def validate_review(arguments: argparse.Namespace) -> int:
    path = project_path(arguments.file, "review file")
    verdict = extract_and_validate_verdict(path.read_bytes())
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    return 0


def run_filesystem_visibility_probe(tmp_root: Path) -> dict[str, Any]:
    """Non-model Bubblewrap probe used only by tests and preflight verification."""
    probe_root = tmp_root / "sandbox-probe"
    repo = probe_root / "repo"
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "src" / "read.txt").write_text("allowed", encoding="utf-8")
    fake_secret = probe_root / "fake-home" / "secret.txt"
    fake_secret.parent.mkdir(exist_ok=True)
    fake_secret.write_text("not-mounted", encoding="utf-8")
    control_file = probe_root / "control" / "approval.jsonl"
    control_file.parent.mkdir(exist_ok=True)
    control_file.write_text("not-mounted", encoding="utf-8")
    reviewer_mounts = system_runtime_mounts()
    add_ro_mount(reviewer_mounts, repo, "/workspace", ensure_destination=False)
    reviewer_code = (
        "from pathlib import Path\n"
        "assert Path('/workspace/src/read.txt').read_text() == 'allowed'\n"
        f"assert not Path({str(Path.home())!r}).exists()\n"
        f"assert not Path({str(fake_secret)!r}).exists()\n"
        f"assert not Path({str(control_file)!r}).exists()\n"
        "try:\n Path('/workspace/src/no-write.txt').write_text('x')\nexcept OSError:\n pass\nelse:\n raise SystemExit(31)\n"
    )
    reviewer_plan = build_bwrap_plan(
        ["/usr/bin/python3", "-c", reviewer_code],
        "/workspace",
        reviewer_mounts,
        {"HOME": "/home/worker", "PATH": "/usr/bin:/bin"},
    )
    reviewer = run_limited(reviewer_plan, b"", 15, {"kind": "probe"})
    writer_mounts = system_runtime_mounts()
    add_ro_mount(writer_mounts, repo, "/workspace", ensure_destination=False)
    add_rw_mount(writer_mounts, repo / "src", "/workspace/src", ensure_destination=False)
    writer_code = (
        "from pathlib import Path\n"
        "Path('/workspace/src/write-ok.txt').write_text('ok')\n"
        f"assert not Path({str(Path.home())!r}).exists()\n"
        f"assert not Path({str(fake_secret)!r}).exists()\n"
        f"assert not Path({str(control_file)!r}).exists()\n"
        "try:\n Path('/workspace/tests/no-write.txt').write_text('x')\nexcept OSError:\n pass\nelse:\n raise SystemExit(32)\n"
    )
    writer_plan = build_bwrap_plan(
        ["/usr/bin/python3", "-c", writer_code],
        "/workspace",
        writer_mounts,
        {"HOME": "/home/worker", "PATH": "/usr/bin:/bin"},
    )
    writer = run_limited(writer_plan, b"", 15, {"kind": "probe"})
    return {
        "status": "ok" if reviewer.returncode == 0 and writer.returncode == 0 else "failed",
        "reviewer_secret_hidden": reviewer.returncode == 0,
        "unbound_fake_secret_hidden": reviewer.returncode == 0 and writer.returncode == 0,
        "unbound_control_hidden": reviewer.returncode == 0 and writer.returncode == 0,
        "writer_scope_writable": (repo / "src" / "write-ok.txt").is_file(),
        "writer_control_hidden": writer.returncode == 0,
        "reviewer_stderr": reviewer.stderr.decode("utf-8", errors="replace"),
        "writer_stderr": writer.stderr.decode("utf-8", errors="replace"),
    }


def check_invariants(arguments: argparse.Namespace) -> int:
    issues = invariant_issues()
    if arguments.self_test:
        broken = copy.deepcopy(load_backends())
        del broken["workers"]["codex-terra"]
        if not invariant_issues(broken):
            issues.append("invariant self-test did not detect removed worker")
    if issues:
        for issue in issues:
            print(f"FAIL {issue}")
        return 1
    print("PASS invariant checks")
    if arguments.self_test:
        print("PASS invariant self-test")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Fail-closed multiagent worker dispatcher")
    subcommands = result.add_subparsers(dest="command", required=True)
    dispatch_parser = subcommands.add_parser("dispatch", help="run one approved worker")
    dispatch_parser.add_argument("--role", required=True)
    dispatch_parser.add_argument("--task", required=True)
    dispatch_parser.add_argument("--input", required=True)
    dispatch_parser.add_argument("--timeout", type=int)
    dispatch_parser.add_argument("--dry-run", action="store_true")
    dispatch_parser.set_defaults(handler=dispatch)
    approve_parser = subcommands.add_parser("approve", help="record authoritative human approval")
    approve_parser.add_argument("--role", required=True)
    approve_parser.add_argument("--task", required=True)
    approve_parser.add_argument("--action")
    approve_parser.add_argument("--confirm", action="store_true")
    approve_parser.set_defaults(handler=approve)
    preflight_parser = subcommands.add_parser("preflight", help="inspect local CLI contracts, catalogs, and isolated startup only")
    preflight_parser.add_argument("--allow-unavailable", action="store_true")
    preflight_parser.set_defaults(handler=preflight)
    review_parser = subcommands.add_parser("validate-review", help="validate a verdict without a model call")
    review_parser.add_argument("--file", required=True)
    review_parser.set_defaults(handler=validate_review)
    invariant_parser = subcommands.add_parser("check-invariants", help="validate policy wiring")
    invariant_parser.add_argument("--self-test", action="store_true")
    invariant_parser.set_defaults(handler=check_invariants)
    return result


def main() -> int:
    arguments = parser().parse_args()
    try:
        return int(arguments.handler(arguments))
    except ControlledError as exc:
        print(f"worker: {exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
