from __future__ import annotations

import pytest

from autoapply.models import Experience, KnowledgeBase, Opportunity, Profile
from autoapply.tailor.grounding import (
    LenientModel,
    LetterGrounder,
    content_tokens,
    filter_cover_letter,
    find_entities,
    find_tech_terms,
    has_placeholder,
    is_claim,
    numeric_tokens,
    sanitize_generated_text,
    split_sentences,
    stem,
    token_overlap,
    validate_bullet,
    validate_cover_letter,
)

DASHBOARD = (
    "Built a Tableau dashboard tracking on-time delivery across 14 regional routes, "
    "cutting weekly reporting time by 6 hours"
)
SHIPMENTS = (
    "Analyzed 18 months of shipment data in SQL and Excel to identify three recurring bottlenecks, "
    "informing a pilot that reduced late deliveries by 12%"
)
GALA = "Led a team of five interns to plan the annual charity gala with a $5,000 budget"


@pytest.fixture
def small_kb() -> KnowledgeBase:
    return KnowledgeBase(
        source="experience_files",
        skills=["SQL", "Excel", "Tableau", "Python"],
        experiences=[
            Experience(
                id="lone-star",
                kind="work",
                title="Business Analyst Intern",
                organization="Lone Star Logistics Co.",
                location="Austin, TX",
                start="2026-06",
                end="2026-08",
                bullets=[DASHBOARD, SHIPMENTS, GALA],
                skills=["SQL", "Excel", "Tableau"],
            ),
            Experience(
                id="bluebonnet",
                kind="work",
                title="Help Desk Consultant",
                organization="Bluebonnet Software Labs",
                start="2024-09",
                end="2025-05",
                bullets=["Resolved 30+ support tickets per week for students and faculty"],
                skills=["Windows"],
            ),
        ],
    )


@pytest.fixture
def role() -> Opportunity:
    return Opportunity(
        company="Acme Robotics",
        title="Product Management Intern",
        location="Austin, TX",
        term="Summer 2027",
        description="Kubernetes and Python experience is a plus. Google-scale systems.",
    )


@pytest.fixture
def me() -> Profile:
    return Profile(
        first_name="Alex",
        last_name="Rivera",
        school="The University of Texas at Austin",
        degree="Bachelor of Science",
        major="Management Information Systems",
        graduation_date="2028-05",
        city="Austin",
        state="TX",
    )


# ------------------------------------------------------------------------------------------ numbers


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("cut costs by 30%", ["30%"]),
        ("cut costs by 30 percent", ["30%"]),
        ("cut costs by 30 per cent", ["30%"]),
        ("saved $5,000", ["$5000"]),
        ("saved $5k", ["$5000"]),
        ("saved 5,000 dollars", ["$5000"]),
        ("grew 1.5M users", ["1500000"]),
        ("raised $1.2M", ["$1200000"]),
        ("a 3x speedup", ["3x"]),
        ("500+ students", ["500+"]),
        ("the 10th cohort", ["10"]),
        ("over 1,200 rows and 2025-06", ["1200", "2025", "6"]),
        ("three teams, doubled output, a dozen tickets, half a day", ["3", "2x", "12", "0.5"]),
        ("wrote twelve reports", ["12"]),
        ("used S3, EC2 and 5G in Q3 of Web3", []),
        ("GPA 3.8", ["3.8"]),
        ("no numbers here", []),
    ],
)
def test_numeric_tokens_are_canonical(text: str, expected: list[str]) -> None:
    assert numeric_tokens(text) == expected


def test_the_word_one_is_only_a_number_when_asked() -> None:
    assert numeric_tokens("one of my goals, no one knows") == ["1", "1"]
    assert numeric_tokens("one of my goals", include_one=False) == []


# ------------------------------------------------------------------------------------------ entities / tech


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Built a Tableau dashboard for the sales team", ["Tableau"]),
        ("Worked at Acme Robotics and Google", ["Acme Robotics and Google"]),  # joined by "and"
        ("Studied at the University of Texas at Austin", ["University of Texas at Austin"]),
        ("Thanks to Acme's team", ["Acme"]),
        ("Google hired me", ["Google"]),
        ("Led the migration to PostgreSQL, then iOS apps", ["PostgreSQL", "iOS"]),
        ("Developed a plan for the team", []),
        ("I built it and I'm proud of the GPA", []),
        ("Tableau-based dashboards", ["Tableau"]),
        ("Grew revenue by $5K in Q3", ["Q3"]),
    ],
)
def test_find_entities(text: str, expected: list[str]) -> None:
    assert find_entities(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Deployed to Kubernetes on AWS with Docker", ["aws", "docker", "kubernetes"]),
        ("C++ and C# and Node.js and scikit-learn", ["c#", "c++", "node.js", "scikit-learn"]),
        ("Used Excel and R for analysis", ["excel", "r"]),
        ("They excel at teamwork, then go home", []),
        ("Worked at Google and Stanford", ["google", "stanford"]),
        ("R&D team, plan C, Go-to person", []),
        ("Analysis in R, and C for speed", ["c", "r"]),
        ("Built ci/cd pipelines and A/B tests", ["a/b testing", "ci/cd"]),
        ("Power BI dashboards", ["power bi"]),
    ],
)
def test_find_tech_terms(text: str, expected: list[str]) -> None:
    assert find_tech_terms(text) == expected


def test_ambiguous_terms_count_in_any_case_on_the_allowed_side() -> None:
    assert find_tech_terms("advanced excel skills", strict=False) == ["excel"]
    assert find_tech_terms("advanced excel skills", strict=True) == []


# ------------------------------------------------------------------------------------------ text helpers


def test_stemming_groups_common_verb_and_noun_forms() -> None:
    groups = [
        ("managed", "managing", "manager", "management", "manages"),
        ("created", "creating", "creation", "create"),
        ("dashboard", "dashboards"),
        ("analyzed", "analyzes", "analyze"),
        ("deliveries", "delivery"),
        ("process", "processes", "processed"),
    ]
    for group in groups:
        assert len({stem(w) for w in group}) == 1, group


def test_content_tokens_drop_stopwords_and_map_number_words() -> None:
    assert content_tokens("Led a team of five to plan the events") == [
        "led",
        "team",
        "5",
        "plan",
        "event",
    ]


def test_token_overlap_exempts_only_the_leading_verb() -> None:
    assert token_overlap(DASHBOARD, [DASHBOARD]) == 1.0
    swapped = DASHBOARD.replace("Built", "Developed")
    assert token_overlap(swapped, [DASHBOARD]) == 1.0
    assert token_overlap("Built an unrelated widget factory", [DASHBOARD]) < 0.4


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("One. Two! Three?", ["One.", "Two!", "Three?"]),
        (
            "I worked at Acme Inc. in Austin. It was great.",
            ["I worked at Acme Inc. in Austin.", "It was great."],
        ),
        (
            "Dr. Smith led the U.S. team. Then I joined.",
            ["Dr. Smith led the U.S. team.", "Then I joined."],
        ),
        ("Line one\nLine two", ["Line one", "Line two"]),
        ("Scored 3.8 GPA. Great.", ["Scored 3.8 GPA.", "Great."]),
        ("", []),
    ],
)
def test_split_sentences(text: str, expected: list[str]) -> None:
    assert split_sentences(text) == expected


def test_sanitize_strips_markdown_and_control_characters() -> None:
    raw = "## Heading\n**Bold** text with `code`\n- a list item\r\n\x00null"
    assert sanitize_generated_text(raw) == "Heading\nBold text with code\na list item\nnull"


@pytest.mark.parametrize(
    "text",
    ["Dear [Hiring Manager Name],", "at {{company}}", "<insert name>", "TBD", "XXX", "Lorem ipsum"],
)
def test_placeholders_are_detected(text: str) -> None:
    assert has_placeholder(text)


def test_ordinary_text_is_not_a_placeholder() -> None:
    assert not has_placeholder("I admire your company and its mission (see the careers page).")


def test_lenient_model_fills_missing_and_null_fields() -> None:
    class Shape(LenientModel):
        name: str
        items: list[int]

    shape = Shape.model_validate({"name": None, "extra": "ignored"})
    assert shape.name == "" and shape.items == []
    assert set(Shape.model_json_schema()["required"]) == {
        "name",
        "items",
    }  # still strict for providers


# ------------------------------------------------------------------------------------------ bullets


def report(
    text: str, sources: list[str], kb: KnowledgeBase, **kwargs: str | None
) -> tuple[bool, list[str]]:
    result = validate_bullet(text, sources, kb, **kwargs)
    return result.ok, result.violations


def test_identical_and_verb_swapped_bullets_pass(small_kb: KnowledgeBase) -> None:
    assert report(DASHBOARD, [DASHBOARD], small_kb)[0]
    assert report(DASHBOARD.replace("Built", "Developed"), [DASHBOARD], small_kb)[0]
    swapped = DASHBOARD.replace(
        "cutting weekly reporting time by 6 hours", "cutting weekly reporting time by six hours"
    )
    assert report(swapped, [DASHBOARD], small_kb)[0]  # "six" == 6


@pytest.mark.parametrize(
    ("rephrased", "needle"),
    [
        (DASHBOARD.replace("14", "40"), "number '40'"),
        (DASHBOARD + ", increasing revenue by 300%", "number '300%'"),
        (DASHBOARD.replace("6 hours", "6 hours and $2M"), "number '$2000000'"),
        (DASHBOARD.replace("Built a", "Built a Kubernetes-hosted"), "Kubernetes"),
        (DASHBOARD.replace("Tableau dashboard", "Tableau dashboard at Google"), "Google"),
        (DASHBOARD.replace("Tableau", "Power BI"), "power bi"),
        (DASHBOARD.replace("Built a", "Built a Stanford University"), "Stanford"),
        (
            "Built a [tool] dashboard tracking on-time delivery across 14 regional routes",
            "placeholder",
        ),
        ("Built dashboards", "too short"),
        (
            DASHBOARD + ", boosting efficiency and strategic alignment",
            "words the source does not have",
        ),
        (
            "Managed a $1M budget for a global sales organisation and closed enterprise deals",
            "number",
        ),
    ],
)
def test_bullets_that_add_facts_are_rejected(
    small_kb: KnowledgeBase, rephrased: str, needle: str
) -> None:
    ok, violations = report(rephrased, [DASHBOARD], small_kb)
    assert not ok
    assert any(needle in v for v in violations), violations


def test_currency_amounts_compare_by_value(small_kb: KnowledgeBase) -> None:
    assert report(GALA.replace("$5,000", "$5K"), [GALA], small_kb)[0]
    assert not report(GALA.replace("$5,000", "5,000"), [GALA], small_kb)[
        0
    ]  # the dollar sign matters
    assert not report(GALA.replace("$5,000", "$50,000"), [GALA], small_kb)[0]
    assert report(GALA.replace("five", "5"), [GALA], small_kb)[0]


def test_merged_bullets_may_draw_numbers_from_every_source(small_kb: KnowledgeBase) -> None:
    merged = (
        "Analyzed 18 months of shipment data in SQL and Excel and built a Tableau dashboard tracking "
        "on-time delivery across 14 regional routes"
    )
    assert report(merged, [SHIPMENTS, DASHBOARD], small_kb)[0]
    ok, violations = report(merged, [SHIPMENTS], small_kb)
    assert not ok and any("number '14'" in v for v in violations)


def test_bullet_rules_reject_empty_sourceless_and_oversized_input(small_kb: KnowledgeBase) -> None:
    assert report("   ", [DASHBOARD], small_kb) == (False, ["empty rephrasing"])
    assert report(DASHBOARD, [], small_kb) == (False, ["rephrasing cites no source bullet"])
    assert report(DASHBOARD, ["  "], small_kb)[0] is False
    assert not report(DASHBOARD * 3, [DASHBOARD], small_kb)[0]
    four = [
        DASHBOARD,
        SHIPMENTS,
        GALA,
        "Resolved 30+ support tickets per week for students and faculty",
    ]
    assert not report(DASHBOARD, four, small_kb)[0]


def test_default_scope_is_the_whole_kb_and_can_be_narrowed(small_kb: KnowledgeBase) -> None:
    borrowed = DASHBOARD.replace(
        "Tableau dashboard", "Tableau dashboard for Bluebonnet Software Labs"
    )
    assert report(borrowed, [DASHBOARD], small_kb)[0]  # another experience's employer is in the KB
    narrow = (
        small_kb.experiences[0].title
        + " Lone Star Logistics Co. "
        + " ".join(small_kb.experiences[0].bullets)
    )
    ok, violations = report(borrowed, [DASHBOARD], small_kb, extra_allowed_text=narrow)
    assert not ok and any("Bluebonnet Software Labs" in v for v in violations)
    python = DASHBOARD.replace("Tableau dashboard", "Python dashboard")
    assert report(python, [DASHBOARD], small_kb)[0]  # Python is a KB skill...
    assert not report(python, [DASHBOARD], small_kb, extra_allowed_text=narrow)[
        0
    ]  # ...but not this entry's


def test_the_job_description_never_makes_a_bullet_claim_acceptable(
    small_kb: KnowledgeBase, role: Opportunity
) -> None:
    assert role.description and "Kubernetes" in role.description
    assert not report(DASHBOARD + " on Kubernetes", [DASHBOARD], small_kb)[0]


def test_reports_are_serialisable_and_bounded(small_kb: KnowledgeBase) -> None:
    result = validate_bullet("x " * 200, [DASHBOARD], small_kb)
    assert result.ok is False and result.replaced_with_source == 0
    assert len(result.violations) <= 30


# ------------------------------------------------------------------------------------------ letters


def check(
    sentence: str,
    kb: KnowledgeBase,
    role: Opportunity,
    me: Profile,
    evidence: list[str] | None = None,
) -> bool:
    return validate_cover_letter(sentence, kb, role, me, evidence_ids=evidence).ok


SUPPORTED = [
    "I am excited to apply for the Product Management Intern role at Acme Robotics.",
    "During my internship at Lone Star Logistics Co., I built a Tableau dashboard that tracked on-time delivery across 14 regional routes.",
    "As a student at The University of Texas at Austin studying Management Information Systems, I am eager to learn.",
    "I graduate in May 2028.",
    "I would welcome the chance to discuss how I can contribute to Acme Robotics.",
    "Thank you for your time and consideration.",
    "Dear Hiring Manager, I appreciate your consideration.",
    "I analyzed 18 months of shipment data in SQL and Excel to find three bottlenecks.",
    "My Excel and SQL skills come from that work.",
    "I would love to apply my Tableau experience to Summer 2027 projects at Acme Robotics.",
]
UNSUPPORTED = [
    "I spent three years at Google increasing revenue by 300%.",
    "I hold an MBA from Harvard University.",
    "I built Kubernetes clusters and deployed them on AWS.",
    "I led a team of 50 engineers.",
    "I managed a large engineering team at a Fortune 500 company.",
    "I improved processes across the organization.",
    "I have five years of experience.",
    "Dear [Hiring Manager], I am a great fit.",
    "I am excited about the opportunity to work with Kubernetes at Acme Robotics.",
    "I graduated summa cum laude from Stanford University in 2019.",
    "I founded a startup called Zephyr Labs.",
]


@pytest.mark.parametrize("sentence", SUPPORTED)
def test_supported_sentences_pass_with_and_without_evidence_scoping(
    sentence: str, small_kb: KnowledgeBase, role: Opportunity, me: Profile
) -> None:
    assert check(sentence, small_kb, role, me, None), sentence
    assert check(sentence, small_kb, role, me, ["lone-star"]), sentence


@pytest.mark.parametrize("sentence", UNSUPPORTED)
def test_unsupported_sentences_fail_under_every_scope(
    sentence: str, small_kb: KnowledgeBase, role: Opportunity, me: Profile
) -> None:
    for evidence in (None, ["lone-star"], ["bluebonnet"], []):
        assert not check(sentence, small_kb, role, me, evidence), (sentence, evidence)


def test_evidence_scope_decides_which_experiences_a_paragraph_may_use(
    small_kb: KnowledgeBase, role: Opportunity, me: Profile
) -> None:
    about_bluebonnet = "Working with the Bluebonnet Software Labs team taught me a lot."
    assert check(about_bluebonnet, small_kb, role, me, None)
    assert check(about_bluebonnet, small_kb, role, me, ["bluebonnet"])
    assert not check(about_bluebonnet, small_kb, role, me, ["lone-star"])
    assert not check(about_bluebonnet, small_kb, role, me, [])
    metric = "I led a team of five interns and managed a $5,000 budget for the annual charity gala."
    assert check(metric, small_kb, role, me, ["lone-star"])
    assert not check(metric, small_kb, role, me, ["bluebonnet"])
    assert not check(metric, small_kb, role, me, ["does-not-exist"])  # unknown ids give no evidence


def test_claims_need_the_evidence_but_role_names_do_not(
    small_kb: KnowledgeBase, role: Opportunity, me: Profile
) -> None:
    kubernetes_claim = "I have used Kubernetes in production."
    assert not check(kubernetes_claim, small_kb, role, me)
    named = Opportunity(company="Acme", title="Python Developer Intern")
    assert check("I am excited to apply for the Python Developer Intern role.", small_kb, named, me)
    assert not check("I used Python to build a compiler at my internship.", small_kb, named, me)


def test_opportunity_description_numbers_and_names_are_not_evidence(
    small_kb: KnowledgeBase, me: Profile
) -> None:
    role = Opportunity(
        company="Acme", title="Intern", description="You will manage 400 servers at Globex."
    )
    assert not check("I managed 400 servers at Globex.", small_kb, role, me)


def test_filter_keeps_only_supported_sentences_and_explains_the_rest(
    small_kb: KnowledgeBase, role: Opportunity, me: Profile
) -> None:
    text = (
        "I am excited to apply for the Product Management Intern role at Acme Robotics. "
        "I spent three years at Google. "
        "Thank you for your time and consideration."
    )
    kept, result = filter_cover_letter(text, small_kb, role, me)
    assert kept == (
        "I am excited to apply for the Product Management Intern role at Acme Robotics. "
        "Thank you for your time and consideration."
    )
    assert not result.ok and len(result.violations) == 1 and "Google" in result.violations[0]
    assert filter_cover_letter("I founded Globex.", small_kb, role, me)[0] == ""


def test_letter_grounder_reuses_scopes_and_exposes_known_ids(
    small_kb: KnowledgeBase, role: Opportunity, me: Profile
) -> None:
    grounder = LetterGrounder(small_kb, role, me)
    assert grounder.known_ids == {"lone-star", "bluebonnet"}
    assert grounder.check("I would welcome the chance to talk.", ["lone-star"]) == []
    kept, dropped = grounder.filter("I built rockets at Globex. I am eager to learn.", [])
    assert kept == ["I am eager to learn."] and len(dropped) == 1


@pytest.mark.parametrize(
    ("sentence", "claim"),
    [
        ("I built a dashboard.", True),
        ("I have built a dashboard.", True),
        ("I successfully managed the budget.", True),
        ("My experience includes analytics.", True),
        ("I have experience with SQL.", True),
        ("I am excited to apply.", False),
        ("I would love to learn.", False),
        ("Thank you for your time.", False),
    ],
)
def test_claim_detection(sentence: str, claim: bool) -> None:
    assert is_claim(sentence) is claim


def test_acronyms_of_organisations_in_the_kb_are_supported(role: Opportunity) -> None:
    kb = KnowledgeBase(
        skills=[],
        experiences=[
            Experience(
                id="mit",
                kind="education",
                title="B.S.",
                organization="Massachusetts Institute of Technology",
                bullets=["Studied robotics"],
            )
        ],
    )
    profile = Profile(first_name="A", last_name="B", school="The University of Texas at Austin")
    assert check("I study robotics at MIT.", kb, role, profile)
    assert check("I am a proud student at UT Austin.", kb, role, profile)
    assert not check("I study robotics at Caltech.", kb, role, profile)


def test_dates_render_as_month_names_so_letters_can_mention_them(role: Opportunity) -> None:
    kb = KnowledgeBase(
        experiences=[
            Experience(
                id="x",
                kind="work",
                title="Analyst",
                organization="Initech",
                start="2025-06",
                end="2025-08",
                bullets=["Filed reports"],
            )
        ]
    )
    profile = Profile(first_name="A", last_name="B")
    assert check(
        "From June 2025 to August 2025 I filed reports at Initech.", kb, role, profile, ["x"]
    )
    assert not check(
        "From June 2024 to August 2024 I filed reports at Initech.", kb, role, profile, ["x"]
    )


# ------------------------------------------------------------------------------------------ number contexts / novelty


def test_numeric_contexts_pair_each_number_with_the_words_that_follow_it() -> None:
    from autoapply.tailor.grounding import numeric_contexts

    assert numeric_contexts("a team of five interns, 18 months of data, up 30%.") == [
        ("5", ("intern",)),
        ("18", ("month", "data")),
        ("30%", ()),
    ]
    assert numeric_contexts("a 6-week workshop and 2x growth") == [
        ("6", ("week", "workshop")),
        ("2x", ("growth",)),
    ]


def test_letter_numbers_must_keep_their_unit_word(
    small_kb: KnowledgeBase, role: Opportunity, me: Profile
) -> None:
    assert check("I led a team of five interns.", small_kb, role, me, ["lone-star"])
    assert not check("I have five years of experience.", small_kb, role, me, ["lone-star"])
    assert not check("I led five months of planning.", small_kb, role, me, ["lone-star"])
    assert check("My pilot reduced late deliveries by 12%.", small_kb, role, me, ["lone-star"])
    assert check("I graduate in 2028 and start in Summer 2027.", small_kb, role, me, [])


def test_novel_words_lists_what_a_rephrasing_adds() -> None:
    from autoapply.tailor.grounding import novel_words

    assert novel_words(DASHBOARD, [DASHBOARD]) == []
    assert novel_words("Developed " + DASHBOARD.split(" ", 1)[1], [DASHBOARD]) == []
    assert novel_words(DASHBOARD + ", boosting efficiency", [DASHBOARD]) == ["boost", "efficiency"]
