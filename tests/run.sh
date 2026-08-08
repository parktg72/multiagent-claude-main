#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)

python3 -m json.tool "$ROOT/_shared/backends.json" >/dev/null
python3 -m json.tool "$ROOT/_shared/schemas/review-input.schema.json" >/dev/null
python3 -m json.tool "$ROOT/_shared/schemas/review-verdict.schema.json" >/dev/null
python3 -B -c 'import ast, pathlib, sys; [ast.parse(pathlib.Path(p).read_text(encoding="utf-8"), filename=p) for p in sys.argv[1:]]' "$ROOT/bin/worker.py" "$ROOT/tests/test_worker.py" "$ROOT/tests/test_hardening.py" "$ROOT/tests/test_documented_pool_references.py"
sh -n "$ROOT/bin/worker" "$ROOT/bin/claude-main" "$ROOT/bin/check-invariants" "$ROOT/tests/run.sh"
"$ROOT/bin/check-invariants" --self-test
python3 -B "$ROOT/tests/test_worker.py"
python3 -B "$ROOT/tests/test_hardening.py"
python3 -B "$ROOT/tests/test_documented_pool_references.py"
"$ROOT/bin/worker" preflight --allow-unavailable
