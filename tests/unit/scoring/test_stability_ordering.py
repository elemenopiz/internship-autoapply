"""Determinism, ordering guarantees, score_all and robustness of the scorer."""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

import autoapply
from autoapply.models import Opportunity, Profile, ScoreResult, SearchProfile
from autoapply.scoring import score_all, score_opportunity

SEARCH = SearchProfile(
    preferred_locations=["Austin, TX", "Remote"],
    include_keywords=["sql", "python"],
    company_allowlist=["Globex"],
    company_denylist=["Initech"],
)


def _op(title: str = "Product Management Intern", **fields: object) -> Opportunity:
    base: dict[str, object] = {
        "company": "Acme Corp",
        "title": title,
        "location": "Austin, TX",
        "term": "Summer 2027",
    }
    base.update(fields)
    return Opportunity(**base)  # type: ignore[arg-type]


CORPUS: list[Opportunity] = [
    _op(),
    _op("APM Intern", term=None, location=None),
    _op("TPM Intern", location="Remote - US"),
    _op("Technology Consulting Summer Analyst", location="New York, NY"),
    _op("Strategy & Operations Intern", company="Globex"),
    _op("Business Operations Analyst Intern", description="Uses SQL and Python. Summer 2027."),
    _op("Data Analytics Intern", location="London, UK"),
    _op("Senior Product Manager"),
    _op("Strategy Manager"),
    _op("Software Engineering Intern"),
    _op("Product Management Intern", company="Initech"),
    _op("Fall 2027 Product Management Intern", term=None),
    _op("Business Analyst", term=None, description="Applications for Fall 2026 are open."),
    _op("Summer Intern", description="product management product manager product owner"),
    _op("MBA Strategy Intern"),
    _op("Product Management Intern", is_open=False),
]


def _dump(result: ScoreResult) -> str:
    return json.dumps(result.model_dump(mode="json"), sort_keys=True)


# ------------------------------------------------------------------------------------------ stability


def test_same_input_gives_same_output_every_time() -> None:
    for op in CORPUS:
        first = _dump(score_opportunity(op, SEARCH))
        for _ in range(3):
            assert _dump(score_opportunity(op, SEARCH)) == first


def test_equal_content_in_distinct_objects_scores_identically() -> None:
    for op in CORPUS:
        twin = Opportunity.model_validate(op.model_dump())
        twin_search = SearchProfile.model_validate(SEARCH.model_dump())
        assert twin is not op and twin_search is not SEARCH
        assert _dump(score_opportunity(twin, twin_search)) == _dump(score_opportunity(op, SEARCH))


def test_scoring_does_not_mutate_its_inputs() -> None:
    op = _op(extra={"employment_type": "Intern"})
    search = SearchProfile.model_validate(SEARCH.model_dump())
    profile = Profile(degree="B.S.")
    before = (op.model_dump(), search.model_dump(), profile.model_dump())
    score_opportunity(op, search, profile, today=date(2026, 9, 29))
    assert (op.model_dump(), search.model_dump(), profile.model_dump()) == before
    assert op.score is None


def test_irrelevant_fields_do_not_change_the_score() -> None:
    plain = score_opportunity(_op(), SEARCH)
    noisy = score_opportunity(
        _op(id="custom-id", url="https://example.test/job/1", extra={"note": "zzz", "n": 3}), SEARCH
    )
    assert _dump(plain) == _dump(noisy)


def test_results_do_not_depend_on_the_hash_seed() -> None:
    """Set iteration order changes with PYTHONHASHSEED; scores, reasons and their order must not."""
    script = (
        "import json, sys\n"
        "from autoapply.models import Opportunity, SearchProfile\n"
        "from autoapply.scoring import score_opportunity\n"
        "payload = json.load(sys.stdin)\n"
        "search = SearchProfile.model_validate(payload['search'])\n"
        "out = [score_opportunity(Opportunity.model_validate(o), search).model_dump(mode='json')\n"
        "       for o in payload['ops']]\n"
        "print(json.dumps(out, sort_keys=True))\n"
    )
    payload = json.dumps(
        {
            "search": SEARCH.model_dump(mode="json"),
            "ops": [o.model_dump(mode="json") for o in CORPUS],
        }
    )
    src = str(Path(autoapply.__file__).resolve().parent.parent)
    outputs = []
    for seed in ("0", "1", "4242"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": src}
        done = subprocess.run(
            [sys.executable, "-c", script],
            input=payload,
            capture_output=True,
            text=True,
            env=env,
            check=True,
            timeout=120,
        )
        outputs.append(done.stdout)
    assert outputs[0] == outputs[1] == outputs[2]
    in_process = json.dumps(
        [score_opportunity(o, SEARCH).model_dump(mode="json") for o in CORPUS], sort_keys=True
    )
    assert json.loads(outputs[0]) == json.loads(in_process)


def test_shuffling_families_only_changes_exact_ties() -> None:
    families = list(SEARCH.role_families.items())
    shuffled = SearchProfile.model_validate(
        {**SEARCH.model_dump(), "role_families": dict(reversed(families))}
    )
    for op in CORPUS:
        a, b = score_opportunity(op, SEARCH), score_opportunity(op, shuffled)
        assert a.score == b.score
        if a.role_family != b.role_family:
            # only allowed when two families tie on every ranking criterion (same points, same phrase)
            assert {a.role_family, b.role_family} <= {"strategy", "business_operations"}


# ------------------------------------------------------------------------------------------ ordering


def _higher_first(better: Opportunity, worse: Opportunity, search: SearchProfile = SEARCH) -> None:
    hi, lo = score_opportunity(better, search), score_opportunity(worse, search)
    assert hi.score > lo.score, (better.title, hi.score, worse.title, lo.score)


ORDERINGS: list[tuple[str, Opportunity, Opportunity]] = [
    (
        "title match beats description-only match",
        _op("Product Management Intern"),
        _op("Summer Intern", description="product management, product manager, product owner"),
    ),
    (
        "core family beats the 0.7 analytics family",
        _op("Business Analyst Intern"),
        _op("Data Analyst Intern"),
    ),
    (
        "core family beats analytics with every other signal missing",
        _op("Product Management Intern", term=None, location=None),
        _op("Data Analytics Intern", term=None, location=None),
    ),
    (
        "multi-word phrase beats a generic single word",
        _op("Corporate Strategy Intern"),
        _op("Strategy Intern"),
    ),
    (
        "intern in the title beats intern only in the description",
        _op("Business Analyst Intern", term=None),
        _op("Business Analyst", term=None, description="This internship supports the team."),
    ),
    (
        "employment-type metadata beats a description mention",
        _op("Business Analyst", term=None, extra={"employment_type": "Intern"}),
        _op("Business Analyst", term=None, description="This internship supports the team."),
    ),
    (
        "a description mention beats term-only evidence",
        _op("Business Analyst", term="Summer 2027", description="A great internship."),
        _op("Business Analyst", term="Summer 2027", description="A great team."),
    ),
    (
        "term-only evidence beats no internship signal at all",
        _op("Business Analyst", term="Summer 2027"),
        _op("Business Analyst", term=None),
    ),
    (
        "explicit term beats a term only in the description",
        _op(term="Summer 2027"),
        _op(term=None, description="Summer 2027 program."),
    ),
    (
        "term in the description beats no term",
        _op(term=None, description="Summer 2027 program."),
        _op(term=None),
    ),
    (
        "no term beats a description that names only other terms",
        _op(term=None),
        _op(term=None, description="Fall 2026 applications are open."),
    ),
    (
        "preferred location beats an unlisted one beats a non-preferred one",
        _op(location="Austin, TX"),
        _op(location="Seattle, WA"),
    ),
    ("US beats clearly non-US", _op(location="Seattle, WA"), _op(location="London, UK")),
    ("allowlisted company beats an unknown one", _op(company="Globex"), _op(company="Hooli")),
    (
        "include keyword in the title beats one in the description",
        _op("Product Management Intern (SQL)"),
        _op(description="We use SQL."),
    ),
    (
        "include keyword in the description beats none",
        _op(description="We use SQL."),
        _op(description="We use spreadsheets."),
    ),
    ("open beats closed", _op(), _op(is_open=False)),
    (
        "any pass beats a hard fail",
        _op("Business Analyst Intern", term=None),
        _op("Senior Business Analyst"),
    ),
]


@pytest.mark.parametrize(("label", "better", "worse"), ORDERINGS, ids=[o[0] for o in ORDERINGS])
def test_ordering_table(label: str, better: Opportunity, worse: Opportunity) -> None:
    _higher_first(better, worse)


def test_ordering_holds_for_every_core_family_against_analytics() -> None:
    core_titles = [
        "Product Management Intern",
        "TPM Intern",
        "Technology Consulting Intern",
        "Strategy & Operations Intern",
        "Business Operations Intern",
        "Business Analyst Intern",
    ]
    adjacent = score_opportunity(_op("Data Analytics Intern"), SEARCH)
    for title in core_titles:
        assert score_opportunity(_op(title), SEARCH).score > adjacent.score, title


# ------------------------------------------------------------------------------------------ score_all


def test_score_all_returns_scored_copies_in_input_order() -> None:
    scored = score_all(CORPUS, SEARCH)
    assert [o.title for o in scored] == [o.title for o in CORPUS]
    assert [o.id for o in scored] == [o.id for o in CORPUS]
    for original, copy in zip(CORPUS, scored, strict=True):
        assert original.score is None, "inputs are never mutated"
        assert copy is not original
        assert isinstance(copy.score, ScoreResult)
        assert _dump(copy.score) == _dump(score_opportunity(original, SEARCH))


def test_score_all_accepts_iterators_and_empty_input() -> None:
    assert score_all([], SEARCH) == []
    assert score_all(iter([]), SEARCH) == []
    scored = score_all((op for op in CORPUS[:3]), SEARCH)
    assert len(scored) == 3 and all(o.score is not None for o in scored)


def test_score_all_forwards_profile_and_today() -> None:
    mba = _op("MBA Product Management Intern", deadline=date(2026, 9, 1))
    [plain] = score_all([mba], SEARCH)
    [degree] = score_all([mba], SEARCH, Profile(degree="MBA"))
    [dated] = score_all([mba], SEARCH, Profile(degree="MBA"), today=date(2026, 9, 29))
    assert plain.score is not None and not plain.score.passed
    assert degree.score is not None and degree.score.passed
    assert dated.score is not None and not dated.score.passed


# ------------------------------------------------------------------------------------------ robustness


@pytest.mark.parametrize(
    "fields",
    [
        {"title": ""},
        {"title": "   "},
        {"title": "\U0001f680 Product Management Intern \U0001f680"},
        {"title": "PRODUCT   MANAGEMENT\tINTERN\n(Summer 2027)"},
        {"title": "Product Management Intern"},
        {"title": "Café Stratégie & Opérations Intern"},
        {"description": "x" * 200_000},
        {"description": "Summer " * 50_000},
        {"location": "北京, China"},
        {"extra": {"employment_type": 5, "commitment": None, "type": ["x", 3, None]}},
        {"extra": {"department": {"nested": "Intern"}}},
        {"term": "☃"},
        {"company": ""},
    ],
)
def test_odd_inputs_never_crash_and_stay_in_range(fields: dict[str, object]) -> None:
    result = score_opportunity(_op(**fields), SEARCH)
    assert 0.0 <= result.score <= 100.0
    assert isinstance(result.passed, bool)


def test_fuzzed_opportunities_keep_the_invariants() -> None:
    rng = random.Random(20270517)
    words = [
        "Product",
        "Management",
        "Manager",
        "Intern",
        "Internship",
        "Senior",
        "Strategy",
        "Operations",
        "Analyst",
        "Analytics",
        "Data",
        "Business",
        "Technical",
        "Program",
        "Consulting",
        "Summer",
        "2027",
        "2026",
        "Fall",
        "MBA",
        "PhD",
        "Co-op",
        "APM",
        "TPM",
        "&",
        "-",
        "(",
        ")",
        "II",
    ]
    cities = [
        "Austin, TX",
        "London, UK",
        "Remote",
        "Seattle, WA",
        "",
        "Toronto, ON",
        "New York, NY",
    ]
    companies = ["Acme", "Globex", "Initech", "Hooli"]
    for _ in range(300):
        title = " ".join(rng.choice(words) for _ in range(rng.randint(1, 7)))
        op = Opportunity(
            company=rng.choice(companies),
            title=title,
            location=rng.choice(cities) or None,
            term=rng.choice([None, "Summer 2027", "Fall 2026", "2027"]),
            description=rng.choice(
                [None, "", "product management strategy", "Summer 2027 internship"]
            ),
            is_open=rng.random() > 0.1,
        )
        result = score_opportunity(op, SEARCH)
        hard = [p for p in result.penalties if p.startswith("Hard fail:")]
        assert 0.0 <= result.score <= 100.0, (title, result)
        if hard:
            assert result.score == 0.0 and not result.passed, (title, result)
        else:
            assert result.passed == (result.score >= SEARCH.min_score), (title, result)
        assert all(isinstance(x, str) and x for x in [*result.reasons, *result.penalties])
        assert result.role_family is None or result.role_family in SEARCH.role_families
        assert _dump(result) == _dump(score_opportunity(op, SEARCH)), title
