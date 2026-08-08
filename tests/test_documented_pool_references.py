# Rule: check inline-code identifiers only when the prose explicitly presents them as
# present scope (an "Affects" declaration) or as current policy/configuration.  Fenced
# command transcripts and Markdown tables are evidence/history and are skipped.  In the
# otherwise ambiguous phrase "current `<value>` pin", a value equal to a provider is not
# a model claim.  Provider names are derived from each authoritative worker's `kind` and
# from the namespace of its slash-qualified model pin; all other values in that phrase
# are checked as exact model pins.  An explicit model noun still makes even a provider
# token a model claim, and a bare model id is checked rather than silently discarded.
# This deliberately cannot tell whether an ambiguous provider token was actually meant
# as a malformed model claim, validate a worker/model pairing when both identifiers are
# independently valid, or catch stale prose that avoids the current-state signals.

import json
import re
import unittest
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = (ROOT / "ISSUES.md", ROOT / "tasks" / "INDEX.md")
INLINE_CODE = r"`([^`]+)`"


@dataclass(frozen=True)
class Claim:
    kind: str
    value: str
    line: int
    context: str


def prose_paragraphs(text):
    """Yield (line number, prose), omitting fenced blocks and table rows."""
    paragraphs = []
    start_line = None
    in_fence = False

    def flush():
        nonlocal paragraphs, start_line
        if paragraphs:
            yield start_line, " ".join(part.strip() for part in paragraphs)
        paragraphs = []
        start_line = None

    for line_number, line in enumerate(text.splitlines(), 1):
        if re.match(r"^\s*```", line):
            yield from flush()
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not line.strip() or line.lstrip().startswith("|"):
            yield from flush()
            continue
        if start_line is None:
            start_line = line_number
        paragraphs.append(line)
    yield from flush()


def current_claims(text, provider_names):
    """Extract structurally typed worker/model claims from current-looking prose."""
    claims = []
    unparsed_affects = []

    for line, paragraph in prose_paragraphs(text):
        # An Affects declaration's scope ends before a date/history qualifier or the
        # next metadata field.  Code outside parentheses names workers; provider/model
        # code inside the scope names exact model pins.  Variant names are therefore
        # ignored without maintaining a list of them.
        for marker in re.finditer(
            r"\bAffects(?::\*\*|\*\*:|:)?\s+", paragraph, re.IGNORECASE
        ):
            scope = paragraph[marker.end() :]
            scope = re.split(
                r"\s+[—–]\s+|\s+since\s+|\*\*(?:Evidence|Status):\*\*|\.(?:\s|$)",
                scope,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
            models = []
            for parenthetical in re.findall(r"\(([^)]*)\)", scope):
                tokens = re.findall(INLINE_CODE, parenthetical)
                if tokens:
                    models.append(tokens[0])
            outside_parentheses = re.sub(r"\([^)]*\)", "", scope)
            workers = re.findall(INLINE_CODE, outside_parentheses)
            if not workers and not models and "all backends" not in scope.lower():
                unparsed_affects.append((line, scope.strip()))
            claims.extend(Claim("worker", value, line, scope.strip()) for value in workers)
            claims.extend(Claim("model", value, line, scope.strip()) for value in models)

        # These patterns require both a temporal signal and a type/relationship.  That
        # keeps a bare old identifier in narrative prose from becoming a current claim.
        typed_patterns = (
            ("model", rf"\bcurrent\s+{INLINE_CODE}\s+model\s+pin\b"),
            (
                None,
                rf"\bcurrent\s+(worker|backend|model|pin)\s+(?:is\s+)?{INLINE_CODE}",
            ),
            (
                None,
                rf"{INLINE_CODE}\s+is\s+(?:the\s+)?current\s+(worker|backend|model|pin)\b",
            ),
            (
                None,
                rf"\b(worker|backend|model|pin)\s+(?:is\s+)?currently\s+{INLINE_CODE}",
            ),
            ("worker", rf"\bcurrently\s+affects?\s+{INLINE_CODE}"),
        )
        for fixed_kind, pattern in typed_patterns:
            for match in re.finditer(pattern, paragraph, re.IGNORECASE):
                if fixed_kind is not None:
                    kind = fixed_kind
                    value = match.group(1)
                elif pattern.startswith(INLINE_CODE):
                    value, noun = match.group(1), match.group(2)
                    kind = "worker" if noun.lower() in {"worker", "backend"} else "model"
                else:
                    noun, value = match.group(1), match.group(2)
                    kind = "worker" if noun.lower() in {"worker", "backend"} else "model"
                claims.append(Claim(kind, value, line, match.group(0)))

        # Without a type noun, "current `x` pin" can describe either a provider or a
        # model.  A registry-derived provider token is the former; every other token is
        # retained as a model claim so stale qualified and bare model ids still fail.
        for match in re.finditer(
            rf"\bcurrent\s+{INLINE_CODE}\s+pin\b", paragraph, re.IGNORECASE
        ):
            value = match.group(1)
            if value not in provider_names:
                claims.append(Claim("model", value, line, match.group(0)))

        # Current policy/configuration often uses a verb rather than a type noun, as in
        # "Current policy: dispatch `worker` at `provider/model`".  The first code value
        # after the verb is the worker; a following value introduced by "at/to" is a model.
        policy = re.search(
            r"\b(?:current\s+(?:policy|configuration)|interim\s+rule)\b"
            rf".*?\b(?:uses?|dispatch(?:es)?|routes?\s+to|affects?|pins?)\s+{INLINE_CODE}"
            rf"(?:\s+(?:at|to)\s+{INLINE_CODE})?",
            paragraph,
            re.IGNORECASE,
        )
        if policy:
            claims.append(Claim("worker", policy.group(1), line, policy.group(0)))
            if policy.group(2):
                claims.append(Claim("model", policy.group(2), line, policy.group(0)))

        if re.search(r"\b(?:current\s+policy|interim\s+rule)\b", paragraph, re.IGNORECASE):
            for match in re.finditer(
                rf"\bFor\s+{INLINE_CODE}(?=\s|[,.—–;:])", paragraph, re.IGNORECASE
            ):
                claims.append(Claim("worker", match.group(1), line, match.group(0)))

    return claims, unparsed_affects


def backend_authority():
    with (ROOT / "_shared" / "backends.json").open(encoding="utf-8") as handle:
        workers = json.load(handle)["workers"]

    model_pins = {record["model"] for record in workers.values()}
    provider_names = {record["kind"] for record in workers.values()}
    provider_names.update(pin.split("/", 1)[0] for pin in model_pins if "/" in pin)
    return {"worker": set(workers), "model": model_pins}, provider_names


class ClaimExtractionRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.valid, cls.provider_names = backend_authority()

    def test_ambiguous_provider_pin_is_not_a_model_claim(self):
        text = """The two transcripts below name provider `opencode-go`, which is where the reviewer was
pinned when they were captured. They are records of commands that really ran; leave the
token alone rather than updating it to the current `opencode` pin."""

        claims, unparsed_affects = current_claims(text, self.provider_names)

        self.assertEqual(claims, [])
        self.assertEqual(unparsed_affects, [])

    def test_pre_correction_stale_claims_remain_invalid(self):
        text = """**Affects:** `kimi-reviewer` (`opencode/kimi-k3`)

The current `opencode/deepseek-v4-pro` pin remains configured."""

        claims, _ = current_claims(text, self.provider_names)
        invalid = {
            (claim.kind, claim.value)
            for claim in claims
            if claim.value not in self.valid[claim.kind]
        }

        self.assertEqual(
            invalid,
            {
                ("worker", "kimi-reviewer"),
                ("model", "opencode/kimi-k3"),
                ("model", "opencode/deepseek-v4-pro"),
            },
        )

    def test_explicit_model_typing_and_bare_id_are_checked(self):
        text = """The current model is `opencode`.

The current `deepseek-v4-pro` pin remains configured."""

        claims, _ = current_claims(text, self.provider_names)

        self.assertEqual(
            {(claim.kind, claim.value) for claim in claims},
            {("model", "opencode"), ("model", "deepseek-v4-pro")},
        )

    def test_historical_fences_tables_and_narrative_remain_ignored(self):
        text = """A historical `opencode/kimi-k3` mention.

| old worker | old model |
| `kimi-reviewer` | `opencode/deepseek-v4-pro` |

```
run `kimi-reviewer` at `opencode/kimi-k3`
```"""

        claims, unparsed_affects = current_claims(text, self.provider_names)

        self.assertEqual(claims, [])
        self.assertEqual(unparsed_affects, [])


class DocumentedPoolReferencesTest(unittest.TestCase):
    def test_current_worker_and_model_claims_match_backend_authority(self):
        valid, provider_names = backend_authority()
        failures = []

        for document in DOCUMENTS:
            claims, unparsed_affects = current_claims(
                document.read_text(encoding="utf-8"), provider_names
            )
            if not claims:
                failures.append(f"{document.relative_to(ROOT)}: no current claims recognized")
            failures.extend(
                f"{document.relative_to(ROOT)}:{line}: unrecognized Affects declaration: {scope}"
                for line, scope in unparsed_affects
            )
            failures.extend(
                f"{document.relative_to(ROOT)}:{claim.line}: current {claim.kind} "
                f"{claim.value!r} is absent from _shared/backends.json ({claim.context})"
                for claim in claims
                if claim.value not in valid[claim.kind]
            )

        self.assertFalse(failures, "\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
