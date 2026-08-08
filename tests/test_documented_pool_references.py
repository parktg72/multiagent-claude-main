# Rule: scan only ISSUES.md and tasks/INDEX.md.  Within their non-fenced,
# non-table prose, check inline-code identifiers when a structurally recognized
# present-tense declaration types them as a worker/backend/reviewer or model pin.
# Recognized declarations are Affects fields; current/currently/as-of/now typed
# statements; and current-policy/configuration/interim-rule clauses using the
# worker relationship verbs enumerated in POLICY_WORKER_RELATION.  The guard against
# a vacuous scanner is corpus-wide, so an individual history-only document is valid.
#
# Deliberate limits: the document allowlist is fixed; authority comes only from the
# `workers` records and their exact `model` values in _shared/backends.json (the
# orchestrator, providers, variants, and other fields are not validated); only simple
# backtick inline-code spans are checked; and Markdown constructs other than backtick
# fences and pipe-prefixed table rows are not parsed.  Fence recognition handles only
# lines beginning with triple backticks, does not support tilde fences, and does not
# validate matching fence lengths, so an unclosed fence hides the remaining file.  Prose
# is grouped by blank lines, and Affects/policy sentence and metadata boundaries are
# regex heuristics.  The present-tense vocabulary, type nouns, and relationship verbs
# are finite, so other English current-state phrasings are missed; conversely, historical
# prose that quotes a recognized present-tense declaration can be reported.  Negation is
# handled only when it interrupts a recognized shape (for example, "no longer the current
# worker").  An Affects field accepts "all backends" without expanding it, treats code
# outside parentheses as workers, and treats only slash-qualified code inside
# non-nested parentheses as model pins; it does not validate variants.  Policy scopes end
# at the next policy marker or period, which can mis-handle abbreviations.  Independently
# valid worker and model identifiers are not checked as a pair.  Identifiers are compared
# exactly, including case.  Finally, the ambiguous phrase "current `<value>` pin" treats,
# case-insensitively, a value matching a namespace of a slash-qualified authoritative
# model as a provider, while other provider-kind names are treated as model ids.  Thus a
# stale model named like such a namespace can be masked, and prose using another bare
# provider kind in that ambiguous phrase can be a false positive.  The anti-vacuity guard
# applies only to the two-document corpus, not to each document independently.

import json
import re
import unittest
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = (ROOT / "ISSUES.md", ROOT / "tasks" / "INDEX.md")
INLINE_CODE = r"`([^`]+)`"
FLAGS = re.IGNORECASE | re.DOTALL
POLICY_WORKER_RELATION = (
    r"(?:uses?|assigns?|affects?|dispatch(?:es)?|routes?(?:\s+to)?|"
    r"sends?(?:\s+work)?\s+to)"
)


@dataclass(frozen=True)
class Claim:
    kind: str
    value: str
    line: int
    context: str


@dataclass(frozen=True)
class ProseParagraph:
    start_line: int
    text: str

    def line_at(self, offset):
        return self.start_line + self.text.count("\n", 0, offset)


def prose_paragraphs(text):
    """Yield prose paragraphs with newlines retained for exact source locations."""
    lines = []
    start_line = None
    in_fence = False

    def flush():
        nonlocal lines, start_line
        if lines:
            yield ProseParagraph(start_line, "\n".join(part.strip() for part in lines))
        lines = []
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
        lines.append(line)
    yield from flush()


def _claim(kind, value, paragraph, match, context=None):
    return Claim(
        kind,
        value,
        paragraph.line_at(match.start()),
        (context if context is not None else match.group(0)).replace("\n", " "),
    )


def _policy_scopes(paragraph):
    """Yield each policy marker's sentence/clause, including repeated markers."""
    marker_pattern = re.compile(
        r"\b(?:current\s+(?:policy|configuration)|interim\s+rule)\b", re.IGNORECASE
    )
    markers = list(marker_pattern.finditer(paragraph.text))
    for index, marker in enumerate(markers):
        limit = markers[index + 1].start() if index + 1 < len(markers) else len(paragraph.text)
        sentence_end = re.search(r"\.(?:\s|$)", paragraph.text[marker.end() : limit])
        if sentence_end:
            limit = marker.end() + sentence_end.end()
        yield marker, paragraph.text[marker.end() : limit]


def current_claims(text, provider_namespaces):
    """Extract structurally typed worker/model claims from current-looking prose."""
    claims = []
    unparsed_affects = []
    folded_namespaces = {name.casefold() for name in provider_namespaces}

    for paragraph in prose_paragraphs(text):
        # An Affects declaration's scope ends before a date/history qualifier, the
        # next metadata field, or a sentence boundary.  Parenthetical position is not
        # semantic: slash shape identifies a model, while variants such as `max` do not.
        for marker in re.finditer(
            r"\bAffects(?::\*\*|\*\*:|:)?\s+", paragraph.text, re.IGNORECASE
        ):
            remainder = paragraph.text[marker.end() :]
            boundary = re.search(
                r"\s+[—–]\s+|\s+since\s+|\*\*(?:Evidence|Status):\*\*|\.(?:\s|$)",
                remainder,
                FLAGS,
            )
            scope = remainder[: boundary.start()] if boundary else remainder
            parentheticals = list(re.finditer(r"\(([^)]*)\)", scope, FLAGS))
            models = []
            for parenthetical in parentheticals:
                for token in re.finditer(INLINE_CODE, parenthetical.group(1)):
                    if "/" in token.group(1):
                        models.append((token.group(1), parenthetical.start(1) + token.start()))
            outside = re.sub(
                r"\([^)]*\)",
                lambda match: re.sub(r"[^\n]", " ", match.group(0)),
                scope,
                flags=FLAGS,
            )
            workers = list(re.finditer(INLINE_CODE, outside))
            if not workers and not models and "all backends" not in scope.lower():
                unparsed_affects.append(
                    (paragraph.line_at(marker.start()), scope.strip().replace("\n", " "))
                )
            claims.extend(
                Claim(
                    "worker",
                    match.group(1),
                    paragraph.line_at(marker.end() + match.start()),
                    scope.strip().replace("\n", " "),
                )
                for match in workers
            )
            claims.extend(
                Claim(
                    "model",
                    value,
                    paragraph.line_at(marker.end() + offset),
                    scope.strip().replace("\n", " "),
                )
                for value, offset in models
            )

        # Typed statements cover both noun-before-value and value-before-noun forms.
        # "Reviewer" is a worker role; "model pin" is kept as one compound type.
        typed_patterns = (
            (
                rf"\b(?:the\s+)?current\s+(worker|backend|reviewer|model(?:\s+pin)?|pin)"
                rf"\s+(?:is\s+)?{INLINE_CODE}",
                "noun_first",
            ),
            (
                rf"{INLINE_CODE}\s+is\s+(?:the\s+)?current\s+"
                r"(worker|backend|reviewer|model(?:\s+pin)?|pin)\b",
                "value_first",
            ),
            (
                r"\b(worker|backend|reviewer|model(?:\s+pin)?|pin)\s+"
                rf"(?:is\s+)?currently\s+{INLINE_CODE}",
                "noun_first",
            ),
            (
                r"\bAs\s+of\s+\d{4}-\d{2}-\d{2}(?:,|\s)+(?:the\s+)?"
                rf"(worker|backend|reviewer|model(?:\s+pin)?|pin)\s+is\s+{INLINE_CODE}",
                "noun_first",
            ),
        )
        for pattern, order in typed_patterns:
            for match in re.finditer(pattern, paragraph.text, FLAGS):
                if order == "value_first":
                    value, noun = match.group(1), match.group(2)
                else:
                    noun, value = match.group(1), match.group(2)
                kind = (
                    "worker"
                    if noun.lower() in {"worker", "backend", "reviewer"}
                    else "model"
                )
                claims.append(_claim(kind, value, paragraph, match))

        # A current pool relationship types the object as a worker without requiring
        # the more formal "current policy" label.
        for match in re.finditer(
            rf"\b(?:the\s+)?pool\s+now\s+{POLICY_WORKER_RELATION}\s+{INLINE_CODE}",
            paragraph.text,
            FLAGS,
        ):
            claims.append(_claim("worker", match.group(1), paragraph, match))

        # "current `x` pin" is grammatically ambiguous.  Only namespaces actually used
        # by slash-qualified model pins get the provider interpretation.  Comparison is
        # case-insensitive; all remaining values stay visible as potential model ids.
        for match in re.finditer(
            rf"\bcurrent\s+{INLINE_CODE}\s+pin\b", paragraph.text, FLAGS
        ):
            value = match.group(1)
            if value.casefold() not in folded_namespaces:
                claims.append(_claim("model", value, paragraph, match))

        # Examine every relationship under every policy marker.  A relationship verb
        # types its first code object as a worker; an optional at/to object is an exact
        # model pin.
        for marker, scope in _policy_scopes(paragraph):
            relations = re.finditer(
                rf"{POLICY_WORKER_RELATION}\s+{INLINE_CODE}"
                rf"(?:\s+(?:at|to)\s+{INLINE_CODE})?",
                scope,
                FLAGS,
            )
            for relation in relations:
                absolute = marker.end() + relation.start()
                context = relation.group(0).replace("\n", " ")
                claims.append(
                    Claim(
                        "worker",
                        relation.group(1),
                        paragraph.line_at(absolute),
                        context,
                    )
                )
                if relation.group(2):
                    claims.append(
                        Claim(
                            "model",
                            relation.group(2),
                            paragraph.line_at(absolute),
                            context,
                        )
                    )
            for match in re.finditer(
                rf"\bFor\s+{INLINE_CODE}(?=\s|[,.—–;:])", scope, FLAGS
            ):
                claims.append(
                    Claim(
                        "worker",
                        match.group(1),
                        paragraph.line_at(marker.end() + match.start()),
                        match.group(0).replace("\n", " "),
                    )
                )

    return claims, unparsed_affects


def backend_authority():
    with (ROOT / "_shared" / "backends.json").open(encoding="utf-8") as handle:
        workers = json.load(handle)["workers"]

    model_pins = {record["model"] for record in workers.values()}
    provider_namespaces = {pin.split("/", 1)[0] for pin in model_pins if "/" in pin}
    return {"worker": set(workers), "model": model_pins}, provider_namespaces


def document_failures(name, text, valid, provider_namespaces):
    claims, unparsed_affects = current_claims(text, provider_namespaces)
    failures = [
        f"{name}:{line}: unrecognized Affects declaration: {scope}"
        for line, scope in unparsed_affects
    ]
    failures.extend(
        f"{name}:{claim.line}: current {claim.kind} {claim.value!r} "
        f"is absent from _shared/backends.json ({claim.context})"
        for claim in claims
        if claim.value not in valid[claim.kind]
    )
    return claims, failures


class ClaimExtractionRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.valid, cls.provider_namespaces = backend_authority()

    def claim_pairs(self, text):
        claims, unparsed = current_claims(text, self.provider_namespaces)
        self.assertEqual(unparsed, [])
        return [(claim.kind, claim.value) for claim in claims]

    def test_affects_uses_identifier_shape_not_parenthetical_position(self):
        for parenthetical in (
            "variant `max`, `opencode/deepseek-v4-flash`",
            "`opencode/deepseek-v4-flash`, variant `max`",
        ):
            with self.subTest(parenthetical=parenthetical):
                self.assertEqual(
                    self.claim_pairs(
                        f"**Affects:** `deepseek-reviewer` ({parenthetical})"
                    ),
                    [
                        ("worker", "deepseek-reviewer"),
                        ("model", "opencode/deepseek-v4-flash"),
                    ],
                )

    def test_ambiguous_provider_namespace_is_case_insensitive_but_kind_is_visible(self):
        self.assertEqual(self.claim_pairs("The current `opencode` pin."), [])
        self.assertEqual(self.claim_pairs("The current `Opencode` pin."), [])
        self.assertEqual(
            self.claim_pairs("The current `codex` pin."), [("model", "codex")]
        )

    def test_pre_correction_stale_claims_remain_invalid(self):
        documents = {
            "ISSUES.md": """**Affects:** `kimi-reviewer` (`opencode/kimi-k3`)

The current `opencode/deepseek-v4-pro` pin remains configured.""",
            "tasks/INDEX.md": """Affects `kimi-reviewer` (variant `max`, `opencode/kimi-k3`),
the pool's only opencode worker.

The current `opencode/deepseek-v4-pro` pin remains configured.""",
        }
        for name, text in documents.items():
            with self.subTest(document=name):
                claims, _ = current_claims(text, self.provider_namespaces)
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
        self.assertCountEqual(
            self.claim_pairs(
                "The current model is `opencode`.\n\n"
                "The current `deepseek-v4-pro` pin remains configured."
            ),
            [("model", "opencode"), ("model", "deepseek-v4-pro")],
        )

    def test_current_worker_relationship_phrasings_are_checked(self):
        samples = (
            "Current policy: routes `kimi-reviewer`.",
            "Current policy: sends work to `kimi-reviewer`.",
            "Current policy: assigns `kimi-reviewer`.",
            "The pool now uses `kimi-reviewer`.",
            "As of 2026-08-08 the reviewer is `kimi-reviewer`.",
        )
        for text in samples:
            with self.subTest(text=text):
                self.assertEqual(
                    self.claim_pairs(text), [("worker", "kimi-reviewer")]
                )

    def test_current_model_pin_subject_is_checked(self):
        self.assertEqual(
            self.claim_pairs("The current model pin is `opencode/kimi-k3`."),
            [("model", "opencode/kimi-k3")],
        )

    def test_every_policy_clause_in_a_paragraph_is_checked(self):
        self.assertEqual(
            self.claim_pairs(
                "Current policy: uses `deepseek-reviewer`; assigns `kimi-reviewer`. "
                "Current policy: routes `codex-luna`."
            ),
            [
                ("worker", "deepseek-reviewer"),
                ("worker", "kimi-reviewer"),
                ("worker", "codex-luna"),
            ],
        )

    def test_historical_fences_tables_and_narrative_remain_ignored(self):
        text = """A historical `opencode/kimi-k3` mention.

| old claim |
| Current policy: uses `table-reviewer`. |

```
Current policy: uses `fenced-reviewer`.
```"""
        self.assertEqual(self.claim_pairs(text), [])

    def test_history_only_document_has_no_failure(self):
        claims, failures = document_failures(
            "history.md",
            "`kimi-reviewer` is no longer the current worker.\n\n"
            "A historical `opencode/kimi-k3` mention.",
            self.valid,
            self.provider_namespaces,
        )
        self.assertEqual(claims, [])
        self.assertEqual(failures, [])

    def test_claim_location_is_the_matching_line_not_paragraph_start(self):
        text = """Introductory line.
- unrelated limit
- another unrelated limit
- Current policy: uses `kimi-reviewer`."""
        claims, _ = current_claims(text, self.provider_namespaces)
        self.assertEqual([(claim.value, claim.line) for claim in claims], [("kimi-reviewer", 4)])


class DocumentedPoolReferencesTest(unittest.TestCase):
    def test_current_worker_and_model_claims_match_backend_authority(self):
        valid, provider_namespaces = backend_authority()
        failures = []
        recognized = 0

        for document in DOCUMENTS:
            claims, document_errors = document_failures(
                document.relative_to(ROOT),
                document.read_text(encoding="utf-8"),
                valid,
                provider_namespaces,
            )
            recognized += len(claims)
            failures.extend(document_errors)

        if not recognized:
            failures.append(
                "configured document corpus: no current claims recognized; "
                "the extraction rule may be vacuous"
            )
        self.assertFalse(failures, "\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
