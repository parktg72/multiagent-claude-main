# Rule: scan only ISSUES.md and tasks/INDEX.md.  Within their non-fenced,
# non-table prose, check inline-code identifiers when a structurally recognized
# present-tense declaration types them as a worker/backend/reviewer or model pin.
# Recognized declarations are Affects fields; current/currently/as-of/now typed
# statements; and current-policy/configuration/interim-rule clauses using the
# worker relationship verbs enumerated in POLICY_WORKER_RELATION.  A policy's narrower
# "For `<worker>`" form is recognized only when it introduces a claim about that
# worker's pinned/current model.  In an "As of" statement, any non-code temporal text
# before the typed clause is accepted, up to a period.  The guard against a vacuous
# scanner is corpus-wide, so an individual history-only document is valid.
#
# Deliberate limits: the document allowlist is fixed; worker authority comes from the
# `workers` keys plus the basename of the orchestrator launcher; aliases and descriptive
# orchestrator labels are not authoritative.  Model authority is the exact
# `workers[*].model` values plus the exact `orchestrator.model_id` in
# _shared/backends.json (providers, variants, and other fields are not validated); only
# simple backtick inline-code spans are checked; and Markdown constructs other than
# backtick fences and pipe-prefixed table rows are not parsed.  Fence recognition handles
# only lines beginning with triple backticks, does not support tilde fences, and does not
# validate matching fence lengths, so an unclosed fence hides the remaining file.  Prose
# is grouped by blank lines, and Affects/policy sentence and metadata boundaries are regex
# heuristics.  The present-tense vocabulary, type nouns,
# relationship verbs, and policy "For" continuation are finite, so other English
# current-state phrasings are missed; in particular, "the pool now uses" is recognized
# but copular "the reviewer/pin is now" forms are not.  Conversely, historical prose
# that quotes a recognized present-tense declaration can be reported.  "As of" accepts
# any non-code temporal description in the same prose paragraph but cannot cross a period,
# and an inline-code date or other inline-code text in that preface prevents a match.
# Negation is handled only when it interrupts a recognized shape (for example, "no longer
# the current worker").  An Affects field must begin a prose paragraph/sentence or use a
# Markdown field marker; it accepts "all backends" without expanding it, treats code
# outside parentheses as workers, and treats only slash-qualified code inside non-nested
# parentheses as model pins; it does not validate variants.  Policy scopes end at the
# next policy marker or period, which can mis-handle abbreviations.  Independently valid
# worker and model identifiers are not checked as a pair.  Identifiers are compared
# exactly, including case.  Finally, the ambiguous phrase "current `<value>` pin" treats,
# case-insensitively, a value matching a namespace of a slash-qualified authoritative
# model as a provider, while other provider-kind names are treated as model ids.
# Thus a stale model named like such a namespace can be masked, and prose using another
# bare provider kind in that ambiguous phrase can be a false positive.  The anti-vacuity
# guard applies only to the two-document corpus, not to each document independently.

import json
import re
import unittest
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = (ROOT / "ISSUES.md", ROOT / "tasks" / "INDEX.md")
RUNNER = ROOT / "tests" / "run.sh"
INLINE_CODE = r"`([^`]+)`"
FLAGS = re.IGNORECASE | re.DOTALL
POLICY_WORKER_RELATION = (
    r"(?:uses?|assigns?|affects?|dispatch(?:es)?|routes?(?:\s+to)?|"
    r"sends?(?:\s+work)?\s+to)"
)
POLICY_FOR_WORKER_CONTINUATION = r"say\s+(?:that\s+)?the\s+(?:pinned|current)\s+model\b"


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
            r"(?:^|(?<=[.!?])\s+|\*\*)Affects(?::\*\*|\*\*:|:)?\s+",
            paragraph.text,
            re.IGNORECASE,
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
                r"\bAs\s+of\s+[^.`]*?(?:,|\s)+(?:the\s+)?"
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
                rf"\bFor\s+{INLINE_CODE}\s+(?={POLICY_FOR_WORKER_CONTINUATION})",
                scope,
                FLAGS,
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
        configuration = json.load(handle)

    workers = configuration["workers"]
    model_pins = {record["model"] for record in workers.values()}
    model_pins.add(configuration["orchestrator"]["model_id"])
    provider_namespaces = {pin.split("/", 1)[0] for pin in model_pins if "/" in pin}
    worker_names = set(workers) | {
        Path(configuration["orchestrator"]["launcher"]).name
    }
    return {"worker": worker_names, "model": model_pins}, provider_namespaces


def runner_discovery_failures(text):
    """Check the runner's syntax and execution paths without executing the runner."""
    syntax_glob = '"$ROOT"/tests/test_*.py'
    syntax_is_discovered = any(
        "ast.parse" in line and syntax_glob in line for line in text.splitlines()
    )
    execution_loop = (
        'for test_file in "$ROOT"/tests/test_*.py; do\n'
        '    python3 -B "$test_file"\n'
        "done"
    )
    failures = []
    if not syntax_is_discovered:
        failures.append("run.sh syntax check does not discover tests/test_*.py")
    if execution_loop not in text:
        failures.append("run.sh execution does not discover and invoke tests/test_*.py")
    return failures


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


def corpus_failures(documents, valid, provider_namespaces):
    """Return failures for a name/text iterable, including the corpus-wide guard."""
    failures = []
    recognized = 0
    for name, text in documents:
        claims, document_errors = document_failures(
            name, text, valid, provider_namespaces
        )
        recognized += len(claims)
        failures.extend(document_errors)

    if not recognized:
        failures.append(
            "configured document corpus: no current claims recognized; "
            "the extraction rule may be vacuous"
        )
    return failures


class ClaimExtractionRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.valid, cls.provider_namespaces = backend_authority()

    def claim_pairs(self, text, provider_namespaces=None):
        if provider_namespaces is None:
            provider_namespaces = self.provider_namespaces
        claims, unparsed = current_claims(text, provider_namespaces)
        self.assertEqual(unparsed, [])
        return [(claim.kind, claim.value) for claim in claims]

    def test_affects_marker_spellings_are_checked(self):
        for marker in ("Affects ", "Affects: ", "**Affects:** ", "**Affects**: "):
            with self.subTest(marker=marker):
                self.assertEqual(
                    self.claim_pairs(f"{marker}`old-worker`"),
                    [("worker", "old-worker")],
                )

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

    def test_affects_scope_stops_at_each_historical_or_metadata_boundary(self):
        samples = {
            "since": "**Affects:** `live-worker` since `retired-worker` was removed",
            "em dash": "**Affects:** `live-worker` — `retired-worker` was removed",
            "en dash": "**Affects:** `live-worker` – `retired-worker` was removed",
            "evidence": "**Affects:** `live-worker` **Evidence:** `old/evidence`",
            "status": "**Affects:** `live-worker` **Status:** `old-status`",
            "period": "**Affects:** `live-worker`. Historical `retired-worker`.",
        }
        for boundary, text in samples.items():
            with self.subTest(boundary=boundary):
                self.assertEqual(
                    self.claim_pairs(text), [("worker", "live-worker")]
                )

    def test_affects_all_backends_is_a_recognized_exemption(self):
        claims, unparsed = current_claims("**Affects:** all backends", set())
        self.assertEqual(claims, [])
        self.assertEqual(unparsed, [])

    def test_affects_without_a_typed_identifier_is_reported(self):
        claims, failures = document_failures(
            "fixture.md",
            "intro\n**Affects:** variant max",
            {"worker": set(), "model": set()},
            set(),
        )
        self.assertEqual(claims, [])
        self.assertEqual(
            failures,
            ["fixture.md:2: unrecognized Affects declaration: variant max"],
        )

    def test_ambiguous_provider_namespace_is_case_insensitive_but_kind_is_visible(self):
        self.assertEqual(self.claim_pairs("The current `opencode` pin."), [])
        self.assertEqual(self.claim_pairs("The current `Opencode` pin."), [])
        self.assertEqual(
            self.claim_pairs(
                "The current `opencode` pin.", provider_namespaces={"OpenCode"}
            ),
            [],
        )
        self.assertEqual(
            self.claim_pairs("The current `codex` pin."), [("model", "codex")]
        )

    def test_as_of_accepts_iso_month_and_relative_temporal_descriptions(self):
        for temporal in ("2026-08-08", "August 2026", "yesterday"):
            with self.subTest(temporal=temporal):
                self.assertEqual(
                    self.claim_pairs(
                        f"As of {temporal} the reviewer is `retired-reviewer`."
                    ),
                    [("worker", "retired-reviewer")],
                )

    def test_as_of_does_not_cross_a_sentence_boundary(self):
        self.assertEqual(
            self.claim_pairs(
                "As of August 2026 the roster was unsettled. "
                "The reviewer is `historical-reviewer`."
            ),
            [],
        )

    def test_backend_authority_has_workers_and_orchestrator_name_and_models(self):
        configuration = json.loads(
            (ROOT / "_shared" / "backends.json").read_text(encoding="utf-8")
        )
        expected_workers = set(configuration["workers"])
        expected_workers.add(Path(configuration["orchestrator"]["launcher"]).name)
        expected_models = {
            record["model"] for record in configuration["workers"].values()
        }
        expected_models.add(configuration["orchestrator"]["model_id"])
        self.assertEqual(self.valid["worker"], expected_workers)
        self.assertEqual(self.valid["model"], expected_models)

    def test_orchestrator_name_is_authoritative_but_stale_name_is_not(self):
        for name, expected_failure_count in (("claude-main", 0), ("claude-old", 1)):
            with self.subTest(name=name):
                claims, failures = document_failures(
                    "fixture.md",
                    f"The current backend is `{name}`.",
                    self.valid,
                    self.provider_namespaces,
                )
                self.assertEqual(
                    [(claim.kind, claim.value) for claim in claims],
                    [("worker", name)],
                )
                self.assertEqual(len(failures), expected_failure_count)

    def test_current_orchestrator_model_is_authoritative(self):
        claims, failures = document_failures(
            "fixture.md",
            "The current model is `claude-opus-5`.",
            self.valid,
            self.provider_namespaces,
        )
        self.assertEqual(
            [(claim.kind, claim.value) for claim in claims],
            [("model", "claude-opus-5")],
        )
        self.assertEqual(failures, [])

    def test_pre_correction_stale_claims_fail_at_their_own_lines(self):
        documents = {
            "ISSUES.md": """**Affects:** `kimi-reviewer` (
`opencode/kimi-k3`)

The current `opencode/deepseek-v4-pro` pin remains configured.""",
            "tasks/INDEX.md": """Affects `kimi-reviewer` (variant `max`,
`opencode/kimi-k3`),
the pool's only opencode worker.

The current `opencode/deepseek-v4-pro` pin remains configured.""",
        }
        expected_lines = {
            "ISSUES.md": {
                ("worker", "kimi-reviewer", 1),
                ("model", "opencode/kimi-k3", 2),
                ("model", "opencode/deepseek-v4-pro", 4),
            },
            "tasks/INDEX.md": {
                ("worker", "kimi-reviewer", 1),
                ("model", "opencode/kimi-k3", 2),
                ("model", "opencode/deepseek-v4-pro", 5),
            },
        }
        for name, text in documents.items():
            with self.subTest(document=name):
                claims, failures = document_failures(
                    name, text, self.valid, self.provider_namespaces
                )
                stale = {
                    (claim.kind, claim.value, claim.line)
                    for claim in claims
                    if claim.value
                    in {
                        "kimi-reviewer",
                        "opencode/kimi-k3",
                        "opencode/deepseek-v4-pro",
                    }
                }
                self.assertEqual(stale, expected_lines[name])
                self.assertEqual(len(failures), 3)
                for kind, value, line in expected_lines[name]:
                    self.assertTrue(
                        any(
                            failure.startswith(
                                f"{name}:{line}: current {kind} {value!r} "
                            )
                            for failure in failures
                        ),
                        failures,
                    )

    def test_document_failures_enforces_exact_authority_membership(self):
        valid = {"worker": {"valid-worker"}, "model": {"provider/valid-model"}}
        claims, failures = document_failures(
            "fixture.md",
            "The current worker is `VALID-WORKER`.\n"
            "The current model is `provider/stale-model`.",
            valid,
            {"provider"},
        )
        self.assertEqual(len(claims), 2)
        self.assertEqual(len(failures), 2)
        self.assertIn("fixture.md:1: current worker 'VALID-WORKER'", failures[0])
        self.assertIn("fixture.md:2: current model 'provider/stale-model'", failures[1])

    def test_explicit_model_typing_and_bare_id_are_checked(self):
        self.assertCountEqual(
            self.claim_pairs(
                "The current model is `opencode`.\n\n"
                "The current `deepseek-v4-pro` pin remains configured."
            ),
            [("model", "opencode"), ("model", "deepseek-v4-pro")],
        )

    def test_typed_statement_orders_roles_and_tenses_are_checked(self):
        samples = (
            ("The current worker is `old-worker`.", "worker"),
            ("The current backend is `old-worker`.", "worker"),
            ("The current reviewer is `old-worker`.", "worker"),
            ("`old-worker` is the current worker.", "worker"),
            ("Worker is currently `old-worker`.", "worker"),
            ("The current model is `provider/old-model`.", "model"),
            ("The current pin is `provider/old-model`.", "model"),
            ("`provider/old-model` is the current model pin.", "model"),
            ("Model is currently `provider/old-model`.", "model"),
        )
        for text, kind in samples:
            with self.subTest(text=text):
                self.assertEqual(
                    self.claim_pairs(text),
                    [(kind, "old-worker" if kind == "worker" else "provider/old-model")],
                )

    def test_policy_markers_and_relationship_verbs_are_checked(self):
        markers = ("Current policy", "Current configuration", "Interim rule")
        relationships = (
            "use",
            "uses",
            "assign",
            "assigns",
            "affect",
            "affects",
            "dispatch",
            "dispatches",
            "route",
            "routes to",
            "send to",
            "sends work to",
        )
        for marker in markers:
            for relation in relationships:
                text = f"{marker}: {relation} `old-worker`."
                with self.subTest(marker=marker, relation=relation):
                    self.assertEqual(
                        self.claim_pairs(text), [("worker", "old-worker")]
                    )

    def test_policy_relationship_optional_model_is_checked(self):
        for preposition in ("at", "to"):
            with self.subTest(preposition=preposition):
                self.assertEqual(
                    self.claim_pairs(
                        f"Current policy: uses `old-worker` {preposition} "
                        "`provider/old-model`."
                    ),
                    [("worker", "old-worker"), ("model", "provider/old-model")],
                )

    def test_policy_for_requires_a_worker_model_continuation(self):
        self.assertEqual(
            self.claim_pairs(
                "**Interim rule.** For `old-worker` say the pinned model did the work."
            ),
            [("worker", "old-worker")],
        )
        for text in (
            "**Interim rule.** For `variant_verified` say the catalog lists it.",
            "**Interim rule.** For `tasks/INDEX.md` record the run id.",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.claim_pairs(text), [])

    def test_pool_now_relationship_is_checked(self):
        self.assertEqual(
            self.claim_pairs("The pool now sends work to `old-worker`."),
            [("worker", "old-worker")],
        )

    def test_current_model_pin_subject_is_checked(self):
        self.assertEqual(
            self.claim_pairs("The current model pin is `opencode/kimi-k3`."),
            [("model", "opencode/kimi-k3")],
        )

    def test_every_policy_clause_in_a_paragraph_is_checked(self):
        self.assertEqual(
            self.claim_pairs(
                "Current policy: uses `deepseek-reviewer`; assigns `kimi-reviewer`; "
                "Current policy: routes `codex-luna`."
            ),
            [
                ("worker", "deepseek-reviewer"),
                ("worker", "kimi-reviewer"),
                ("worker", "codex-luna"),
            ],
        )

    def test_policy_scope_stops_at_period_and_blank_paragraph(self):
        samples = (
            "Current policy: uses `live-worker`. Historical text uses `old-worker`.",
            "Current policy: uses `live-worker`\n\nHistorical text uses `old-worker`",
        )
        for text in samples:
            with self.subTest(text=text):
                self.assertEqual(
                    self.claim_pairs(text), [("worker", "live-worker")]
                )

    def test_plain_historical_narrative_is_ignored(self):
        self.assertEqual(
            self.claim_pairs("A historical `opencode/kimi-k3` mention."), []
        )

    def test_pipe_prefixed_table_rows_are_ignored(self):
        self.assertEqual(
            self.claim_pairs(
                "| old claim |\n  | Current policy: uses `table-reviewer`. |"
            ),
            [],
        )

    def test_backtick_fences_and_their_contents_are_ignored(self):
        self.assertEqual(
            self.claim_pairs(
                "```text\nCurrent policy: uses `fenced-reviewer`.\n```"
            ),
            [],
        )

    def test_unclosed_backtick_fence_hides_the_remainder(self):
        self.assertEqual(
            self.claim_pairs(
                "```text\ntranscript\nCurrent policy: uses `hidden-reviewer`."
            ),
            [],
        )

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

    def test_negation_that_interrupts_a_recognized_shape_is_ignored(self):
        self.assertEqual(
            self.claim_pairs(
                "`retired-worker` is no longer the current worker.\n"
                "The current worker is not `retired-worker`."
            ),
            [],
        )

    def test_claim_location_is_the_matching_line_not_paragraph_start(self):
        text = """Introductory line.
- unrelated limit
- another unrelated limit
- Current policy: uses `kimi-reviewer`."""
        claims, _ = current_claims(text, self.provider_namespaces)
        self.assertEqual([(claim.value, claim.line) for claim in claims], [("kimi-reviewer", 4)])


class CorpusGuardRegressionTest(unittest.TestCase):
    def test_document_allowlist_is_exact(self):
        self.assertEqual(
            tuple(path.relative_to(ROOT).as_posix() for path in DOCUMENTS),
            ("ISSUES.md", "tasks/INDEX.md"),
        )

    def test_history_only_corpus_triggers_anti_vacuity_guard(self):
        failures = corpus_failures(
            [("history.md", "A historical `old-worker` mention.")],
            {"worker": set(), "model": set()},
            set(),
        )
        self.assertEqual(
            failures,
            [
                "configured document corpus: no current claims recognized; "
                "the extraction rule may be vacuous"
            ],
        )


class RunnerWiringRegressionTest(unittest.TestCase):
    def test_runner_discovers_tests_for_syntax_checks_and_execution(self):
        runner_text = RUNNER.read_text(encoding="utf-8")
        self.assertEqual(runner_discovery_failures(runner_text), [])

    def test_fixture_runner_fails_when_discovery_wiring_is_removed(self):
        runner_text = RUNNER.read_text(encoding="utf-8")
        fixtures = {
            "syntax check": runner_text.replace(
                '"$ROOT"/tests/test_*.py', '"$ROOT/tests/test_worker.py"', 1
            ),
            "test invocation": runner_text.replace(
                '    python3 -B "$test_file"\n', "", 1
            ),
        }
        expected = {
            "syntax check": [
                "run.sh syntax check does not discover tests/test_*.py"
            ],
            "test invocation": [
                "run.sh execution does not discover and invoke tests/test_*.py"
            ],
        }
        for missing_line, fixture_text in fixtures.items():
            with self.subTest(missing_line=missing_line):
                self.assertNotEqual(fixture_text, runner_text)
                self.assertEqual(
                    runner_discovery_failures(fixture_text), expected[missing_line]
                )


class DocumentedPoolReferencesTest(unittest.TestCase):
    def test_current_worker_and_model_claims_match_backend_authority(self):
        valid, provider_namespaces = backend_authority()
        documents = (
            (
                document.relative_to(ROOT),
                document.read_text(encoding="utf-8"),
            )
            for document in DOCUMENTS
        )
        failures = corpus_failures(documents, valid, provider_namespaces)
        self.assertFalse(failures, "\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
