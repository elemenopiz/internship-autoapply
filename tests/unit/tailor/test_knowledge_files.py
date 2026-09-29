from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoapply.config import AppPaths
from autoapply.models import Experience, KnowledgeBase
from autoapply.tailor.knowledge import (
    experience_to_markdown,
    kb_from_experiences,
    load_experience_files,
    load_kb,
    load_kb_with_issues,
    parse_experience_markdown,
    save_kb,
)
from autoapply.testing.sample_profile import sample_experiences, write_sample_experience_files

EXAMPLES = Path(__file__).resolve().parents[3] / "docs" / "examples" / "experiences"

WORK_MD = """---
id: acme-analyst
kind: work
title: Data Analyst Intern
organization: Acme Robotics
location: Austin, TX
start: 2026-06
end: 2026-08
skills: [SQL, Excel, "Power BI"]
---
- Built a dashboard that cut reporting time by 6 hours
- Analyzed 18 months of data in SQL
"""


# ------------------------------------------------------------------------------------------ markdown


def test_markdown_front_matter_and_bullets() -> None:
    exp = parse_experience_markdown(WORK_MD)
    assert exp == Experience(
        id="acme-analyst",
        kind="work",
        title="Data Analyst Intern",
        organization="Acme Robotics",
        location="Austin, TX",
        start="2026-06",
        end="2026-08",
        bullets=[
            "Built a dashboard that cut reporting time by 6 hours",
            "Analyzed 18 months of data in SQL",
        ],
        skills=["SQL", "Excel", "Power BI"],
    )


def test_markdown_tolerates_crlf_bom_tabs_and_trailing_whitespace() -> None:
    text = "\N{ZERO WIDTH NO-BREAK SPACE}" + WORK_MD.replace("\n", " \r\n").replace(
        "- Built", "-\tBuilt"
    )
    exp = parse_experience_markdown(text)
    assert exp.title == "Data Analyst Intern" and len(exp.bullets) == 2
    assert exp.bullets[0].startswith("Built a dashboard")


@pytest.mark.parametrize(
    ("skills_block", "expected"),
    [
        ("skills: [SQL, Excel]", ["SQL", "Excel"]),
        ("skills: SQL, Excel; Tableau", ["SQL", "Excel", "Tableau"]),
        (
            'skills: ["Power BI", \'C++\', "Scikit-learn, advanced"]',
            ["Power BI", "C++", "Scikit-learn, advanced"],
        ),
        ('skills:\n  - SQL\n  - "Power BI"\n- Excel', ["SQL", "Power BI", "Excel"]),
        ("skills:", []),
        ("skills: [SQL, SQL, sql]", ["SQL", "sql"]),
    ],
)
def test_markdown_skill_list_forms(skills_block: str, expected: list[str]) -> None:
    exp = parse_experience_markdown(f"---\ntitle: Intern\n{skills_block}\n---\n- x\n")
    assert exp.skills == expected


def test_markdown_bullet_styles_continuations_and_emphasis() -> None:
    body = (
        "- first bullet\n"
        "* second bullet\n"
        "+ third bullet\n"
        "\N{BULLET} fourth bullet\n"
        "1. fifth bullet\n"
        "2) sixth bullet that wraps\n"
        "   onto a second line\n"
        "- **bold** and `code` and __under__\n"
        "not a bullet, ignored\n"
        "-\n"
    )
    exp = parse_experience_markdown(f"---\ntitle: Intern\n---\n{body}")
    assert exp.bullets == [
        "first bullet",
        "second bullet",
        "third bullet",
        "fourth bullet",
        "fifth bullet",
        "sixth bullet that wraps onto a second line",
        "bold and code and under",
    ]


def test_markdown_comments_quotes_and_colons_in_values() -> None:
    text = (
        "---\n# a comment\ntitle: \"Intern: Data & Insights\"\norganization: 'O''Brien & Sons'\n"
        "location: Austin, TX\nstart: May 2026\nend: Present\n---\n- x\n"
    )
    exp = parse_experience_markdown(text)
    assert exp.title == "Intern: Data & Insights"
    assert exp.organization == "O''Brien & Sons"
    assert (exp.start, exp.end) == ("2026-05", "present")


def test_markdown_defaults_and_derived_ids() -> None:
    exp = parse_experience_markdown(
        "---\ntitle: Barista\norganization: Café Central\n---\n", name="notes"
    )
    assert exp.kind == "work" and exp.id == "cafe-central-barista"
    assert exp.bullets == [] and exp.skills == [] and exp.organization == "Café Central"
    bare = parse_experience_markdown("---\ntitle: !!!\n---\n", name="My File")
    assert bare.id == "my-file"


def test_markdown_kind_synonyms_and_unknown_kinds() -> None:
    for word, kind in (
        ("internship", "work"),
        ("Club", "leadership"),
        ("school", "education"),
        ("Honors", "award"),
    ):
        assert parse_experience_markdown(f"---\ntitle: T\nkind: {word}\n---\n").kind == kind
    issues: list[str] = []
    exp = parse_experience_markdown(
        "---\ntitle: T\nkind: hobby\n---\n", name="hobby.md", issues=issues
    )
    assert exp.kind == "other" and "unknown kind 'hobby'" in issues[0]


def test_markdown_dates_are_normalised_or_kept_as_written() -> None:
    exp = parse_experience_markdown("---\ntitle: T\nstart: 06/2025\nend: ongoing\n---\n")
    assert (exp.start, exp.end) == ("2025-06", "present")
    year_only = parse_experience_markdown("---\ntitle: T\nstart: 2023\nend: Summer 2024\n---\n")
    assert (year_only.start, year_only.end) == ("2023", "Summer 2024")
    unset = parse_experience_markdown("---\ntitle: T\nstart: n/a\nend:\n---\n")
    assert (unset.start, unset.end) == (None, None)


def test_markdown_front_matter_may_carry_bullets_too() -> None:
    exp = parse_experience_markdown(
        "---\ntitle: T\nbullets:\n  - from front matter\n---\n- from body\n"
    )
    assert exp.bullets == ["from front matter", "from body"]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("title: no front matter\n- bullet\n", "no front matter"),
        ("---\norganization: Acme\n---\n- x\n", "missing 'title'"),
        ("---\ntitle: T\nthis line has no colon\n---\n", "without ':'"),
        ("", "no front matter"),
    ],
)
def test_markdown_errors_are_value_errors(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_experience_markdown(text)


@pytest.mark.parametrize(
    "exp",
    [
        *sample_experiences(),
        Experience(
            id="quoted",
            title='Intern: "Data" [Team] #1',
            organization="A&B, Inc.",
            bullets=["- leading dash", "Uses `code` no more"],
        ),
        Experience(
            id="accents",
            title="Ingénieur Stagiaire",
            organization="Société Générale",
            location="Zürich",
            bullets=["Réduit les coûts de 12 %", "O'Brien \N{EN DASH} l'équipe"],
            skills=["C++", "Node.js", "Power BI, advanced"],
        ),
        Experience(id="minimal", title="Only a title"),
        Experience(
            id="dates",
            kind="award",
            title="Award",
            start="2024",
            end="present",
            links=["https://example.test/a?b=1"],
        ),
    ],
)
def test_experience_to_markdown_round_trips(exp: Experience) -> None:
    parsed = parse_experience_markdown(experience_to_markdown(exp))
    expected = exp.model_copy(update={"bullets": [b.removeprefix("- ") for b in exp.bullets]})
    expected = expected.model_copy(
        update={"bullets": [b.replace("`", "") for b in expected.bullets]}
    )
    assert parsed == expected


# ------------------------------------------------------------------------------------------ json files


def write(directory: Path, name: str, content: str | bytes) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


def test_json_list_object_and_single_forms(tmp_path: Path) -> None:
    a = {"id": "a", "title": "Analyst", "bullets": ["one", "two"], "skills": ["SQL"]}
    b = {"id": "b", "kind": "project", "title": "App", "skills": "Python, Flask"}
    write(tmp_path, "1-list.json", json.dumps([a, b]))
    write(
        tmp_path,
        "2-object.json",
        json.dumps({"skills": ["Excel", "sql"], "experiences": [{"id": "c", "title": "Club"}]}),
    )
    write(
        tmp_path,
        "3-single.json",
        json.dumps({"id": "d", "kind": "education", "title": "B.S.", "organization": "State U"}),
    )
    experiences, skills, issues = load_experience_files(tmp_path)
    assert [e.id for e in experiences] == ["a", "b", "c", "d"]
    assert experiences[1].skills == ["Python", "Flask"] and experiences[3].kind == "education"
    assert skills == ["Excel", "sql"] and issues == []


def test_json_aliases_bullet_shapes_and_derived_ids(tmp_path: Path) -> None:
    write(
        tmp_path,
        "x.json",
        json.dumps(
            [
                {
                    "role": "Intern",
                    "company": "Acme",
                    "highlights": "did a\ndid b",
                    "tools": "SQL",
                    "start_date": "2024-06",
                    "end_date": "2024-08",
                },
                {"title": "Tutor", "bullets": [{"text": "helped"}, "  also helped  ", None, ""]},
            ]
        ),
    )
    experiences, _, issues = load_experience_files(tmp_path)
    first, second = experiences
    assert (first.title, first.organization, first.id) == ("Intern", "Acme", "acme-intern")
    assert (
        first.bullets == ["did a", "did b"] and first.skills == ["SQL"] and first.start == "2024-06"
    )
    assert second.bullets == ["helped", "also helped"] and second.id == "tutor"
    assert issues == []


def test_bad_json_content_is_reported_not_raised(tmp_path: Path) -> None:
    write(tmp_path, "broken.json", "{not json")
    write(tmp_path, "scalar.json", "42")
    write(tmp_path, "mixed.json", json.dumps([1, {"title": "ok"}, {"organization": "no title"}]))
    experiences, _, issues = load_experience_files(tmp_path)
    assert [e.title for e in experiences] == ["ok"]
    joined = " | ".join(issues)
    assert "broken.json: invalid JSON" in joined
    assert "scalar.json: expected a list or an object" in joined
    assert "mixed.json[1]: not an object" in joined
    assert "mixed.json[3]: missing 'title'" in joined


# ------------------------------------------------------------------------------------------ directory


def test_directory_order_hidden_files_readme_and_duplicates(tmp_path: Path) -> None:
    write(tmp_path, "B-second.md", "---\nid: same\ntitle: Second\n---\n")
    write(tmp_path, "a-first.md", "---\nid: same\ntitle: First\n---\n")
    write(tmp_path, ".hidden.md", "---\ntitle: Hidden\n---\n")
    write(tmp_path, "README.md", "# How to use this folder\n")
    write(tmp_path, "notes.txt", "---\ntitle: wrong suffix\n---\n")
    (tmp_path / "folder.md").mkdir()
    experiences, _, issues = load_experience_files(tmp_path)
    assert [(e.id, e.title) for e in experiences] == [("same", "First"), ("same-2", "Second")]
    assert any("README.md: skipped (no front matter)" in i for i in issues)
    assert any("duplicate id 'same' renamed to 'same-2'" in i for i in issues)


def test_missing_directory_and_legacy_windows_encoding(tmp_path: Path) -> None:
    assert load_experience_files(tmp_path / "nope") == ([], [], [])
    write(
        tmp_path,
        "cp1252.md",
        "---\ntitle: Café Manager\n---\n- Cut costs by 5% \N{EN DASH} fast\n".encode("cp1252"),
    )
    (exp,), _, _ = load_experience_files(tmp_path)
    assert exp.title == "Café Manager" and exp.bullets == ["Cut costs by 5% \N{EN DASH} fast"]


def test_unreadable_files_are_skipped_with_an_issue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(tmp_path, "good.md", "---\ntitle: Good\n---\n")
    bad = write(tmp_path, "bad.md", "---\ntitle: Bad\n---\n")
    original = Path.read_bytes

    def flaky(self: Path) -> bytes:
        if self == bad:
            raise PermissionError(13, "Permission denied")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", flaky)
    experiences, _, issues = load_experience_files(tmp_path)
    assert [e.title for e in experiences] == ["Good"]
    assert any("bad.md: unreadable" in i for i in issues)


def test_kb_skills_are_the_deduplicated_union_including_skill_entries() -> None:
    experiences = [
        Experience(id="a", title="A", skills=["SQL", "Excel"]),
        Experience(id="b", title="B", skills=["sql", "Python"]),
        Experience(id="s", kind="skill", title="Languages", skills=["Spanish"]),
    ]
    kb = kb_from_experiences(experiences, ["Tableau", "EXCEL"])
    assert kb.skills == ["Tableau", "EXCEL", "SQL", "Python", "Spanish"]
    assert kb.source == "experience_files"


# ------------------------------------------------------------------------------------------ load / save


def test_load_kb_prefers_experience_files_then_saved_kb_then_empty(app_paths: AppPaths) -> None:
    assert load_kb(app_paths) == KnowledgeBase(source="none")
    saved = KnowledgeBase(
        source="resume",
        experiences=[Experience(id="r", title="From resume", bullets=["x"])],
        skills=["SQL"],
    )
    save_kb(app_paths, saved)
    assert load_kb(app_paths) == saved
    write(app_paths.experiences_dir, "01.md", "---\nid: f\ntitle: From files\n---\n- y\n")
    kb = load_kb(app_paths)
    assert kb.source == "experience_files" and [e.id for e in kb.experiences] == ["f"]


def test_invalid_experience_files_fall_through_to_the_saved_kb(app_paths: AppPaths) -> None:
    saved = KnowledgeBase(source="resume", experiences=[Experience(id="r", title="From resume")])
    save_kb(app_paths, saved)
    write(app_paths.experiences_dir, "bad.json", "{broken")
    kb, issues = load_kb_with_issues(app_paths)
    assert kb == saved and any("bad.json" in i for i in issues)


def test_unusable_saved_kb_is_an_empty_kb_with_an_issue(app_paths: AppPaths) -> None:
    app_paths.knowledge_base_file.write_text("{ nope", encoding="utf-8")
    kb, issues = load_kb_with_issues(app_paths)
    assert kb == KnowledgeBase(source="none") and "knowledge_base.json: unusable" in issues[0]
    app_paths.knowledge_base_file.write_text(
        json.dumps({"skills": ["only skills"]}), encoding="utf-8"
    )
    assert load_kb(app_paths).source == "none"  # no experiences: nothing to tailor from


def test_save_kb_is_atomic_utf8_and_round_trips(app_paths: AppPaths) -> None:
    kb = KnowledgeBase(
        source="resume",
        experiences=[
            Experience(
                id="x", title="Ingénieur \N{EN DASH} Café", bullets=["Réduit 12 % \N{BULLET} vite"]
            )
        ],
        skills=["Zoë"],
    )
    save_kb(app_paths, kb)
    raw = app_paths.knowledge_base_file.read_bytes()
    assert b"\r\n" not in raw and "Ingénieur".encode() in raw  # UTF-8, LF, not \u-escaped
    assert [p.name for p in app_paths.profile_dir.iterdir() if p.is_file()] == [
        "knowledge_base.json"
    ]
    assert load_kb(app_paths) == kb
    save_kb(
        app_paths, KnowledgeBase(source="resume", experiences=[Experience(id="y", title="Second")])
    )
    assert [e.id for e in load_kb(app_paths).experiences] == ["y"]


def test_save_kb_creates_the_profile_directory(tmp_path: Path) -> None:
    paths = AppPaths(root=tmp_path / "fresh")
    save_kb(paths, KnowledgeBase(source="resume", experiences=[Experience(id="a", title="A")]))
    assert paths.knowledge_base_file.is_file()


def test_saved_kb_with_a_utf8_bom_still_loads(app_paths: AppPaths) -> None:
    payload = json.dumps(
        KnowledgeBase(source="resume", experiences=[Experience(id="a", title="A")]).model_dump(
            mode="json"
        )
    )
    app_paths.knowledge_base_file.write_bytes(b"\xef\xbb\xbf" + payload.encode("utf-8"))
    assert load_kb(app_paths).experiences[0].id == "a"


def test_sample_experience_files_load_back_exactly(app_paths: AppPaths) -> None:
    written = write_sample_experience_files(app_paths.experiences_dir)
    assert {p.suffix for p in written} == {".md", ".json"}
    kb, issues = load_kb_with_issues(app_paths)
    assert issues == []
    assert kb.experiences == sample_experiences()
    assert kb == kb_from_experiences(sample_experiences())


def test_documented_example_files_load_without_issues() -> None:
    experiences, skills, issues = load_experience_files(EXAMPLES)
    assert issues == [] and skills == []
    assert {e.kind for e in experiences} >= {"work", "project", "leadership", "education"}
    assert all(e.bullets for e in experiences)


def test_paths_with_spaces_and_unicode_work(tmp_path: Path) -> None:
    folder = tmp_path / "Mein Ordner ü" / "experiences"
    write(folder, "01 erste Erfahrung.md", WORK_MD)
    (exp,), _, _ = load_experience_files(folder)
    assert exp.id == "acme-analyst"
