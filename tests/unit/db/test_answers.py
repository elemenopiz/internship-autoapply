"""Saved screening answers and the pending-question queue."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest

from autoapply.clock import FakeClock
from autoapply.db import NotFoundError, Repo
from autoapply.models import PendingQuestion, QuestionKind, ScreeningAnswer
from autoapply.normalize import norm_text

FELONY = "Have you ever been convicted of a felony?"


def answer(question: str = FELONY, text: str = "No", **fields: object) -> ScreeningAnswer:
    return ScreeningAnswer(question=question, answer=text, **fields)


def pending(
    question: str = FELONY, opportunity_id: str | None = "opp-1", **fields: object
) -> PendingQuestion:
    return PendingQuestion(question=question, opportunity_id=opportunity_id, **fields)


# ================================================================================== saved answers


def test_upsert_inserts_and_returns_the_stored_row(repo: Repo, fake_clock: FakeClock) -> None:
    stored = repo.upsert_answer(
        answer(intent="felony_conviction", answer_kind="boolean", source="user", use_count=0)
    )
    assert stored.id is not None
    assert stored.intent == "felony_conviction"
    assert stored.question == FELONY
    assert stored.question_norm == "have you ever been convicted of a felony"
    assert (stored.answer, stored.answer_kind, stored.source) == ("No", "boolean", "user")
    assert stored.created_at == stored.updated_at == fake_clock.now()
    assert stored.use_count == 0
    assert repo.list_answers() == [stored]


def test_find_by_intent_and_by_question_norm(repo: Repo) -> None:
    with_intent = repo.upsert_answer(answer(intent="felony_conviction"))
    free = repo.upsert_answer(answer("Describe your favourite project!", "The rover."))
    assert repo.find_answer(intent="felony_conviction") == with_intent
    assert repo.find_answer(question_norm=norm_text(FELONY)) == with_intent
    assert repo.find_answer(question_norm="describe your favourite project") == free
    assert (
        repo.find_answer(
            intent="felony_conviction", question_norm="describe your favourite project"
        )
        == with_intent
    )


def test_intent_match_wins_over_question_norm(repo: Repo) -> None:
    by_intent = repo.upsert_answer(answer("Are you a felon?", "No", intent="felony_conviction"))
    by_text = repo.upsert_answer(answer("Anything else to add?", "Nothing."))
    got = repo.find_answer(intent="felony_conviction", question_norm=by_text.question_norm)
    assert got == by_intent


def test_question_norm_is_used_when_the_intent_is_unknown_or_missing(repo: Repo) -> None:
    stored = repo.upsert_answer(answer("Anything else to add?", "Nothing."))
    assert repo.find_answer(intent="no_such_intent", question_norm=stored.question_norm) == stored
    assert repo.find_answer(intent=None, question_norm=stored.question_norm) == stored
    assert repo.find_answer(intent="", question_norm=stored.question_norm) == stored


def test_find_is_exact_never_fuzzy(repo: Repo) -> None:
    repo.upsert_answer(answer(FELONY, "No"))
    assert (
        repo.find_answer(question_norm="have you ever been convicted of a felony offense") is None
    )
    assert repo.find_answer(question_norm="have you been convicted of a felony") is None
    assert repo.find_answer(question_norm="convicted") is None
    assert repo.find_answer(intent="felony") is None


def test_find_accepts_raw_question_text_because_it_normalises(repo: Repo) -> None:
    stored = repo.upsert_answer(answer(FELONY, "No"))
    assert (
        repo.find_answer(question_norm="  HAVE you EVER been convicted of a FELONY??  ") == stored
    )
    assert repo.find_answer(question_norm=FELONY) == stored


def test_find_with_nothing_to_look_for_returns_none(repo: Repo) -> None:
    repo.upsert_answer(answer(intent="felony_conviction"))
    repo.upsert_answer(answer("", "x", intent="only_an_intent"))  # empty norm must never match ""
    assert repo.find_answer() is None
    assert repo.find_answer(intent=None, question_norm=None) is None
    assert repo.find_answer(question_norm="") is None
    assert repo.find_answer(question_norm="   ") is None
    assert repo.find_answer(intent="   ") is None


def test_question_norm_is_always_normalised_on_write(repo: Repo) -> None:
    stored = repo.upsert_answer(
        answer(
            "Salary Expectations?  (USD)", "Negotiable", question_norm="  SALARY expectations!! "
        )
    )
    assert stored.question_norm == "salary expectations"
    stored = repo.upsert_answer(answer("Ünïcode Q & A?", "x"))
    assert stored.question_norm == norm_text("Ünïcode Q & A?") == "unicode q and a"


def test_upserting_the_same_intent_updates_in_place(repo: Repo, fake_clock: FakeClock) -> None:
    first = repo.upsert_answer(answer(intent="felony_conviction", source="user"))
    assert repo.touch_answer(first.id) and repo.touch_answer(first.id)
    fake_clock.advance(timedelta(days=1))
    second = repo.upsert_answer(
        answer(
            "Any criminal record?",
            "Prefer not to say",
            intent="felony_conviction",
            source="generated",
        )
    )
    assert second.id == first.id
    assert second.created_at == first.created_at
    assert second.updated_at == fake_clock.now() > first.updated_at
    assert second.use_count == 2  # usage statistics survive an edit
    assert (second.question, second.answer, second.source) == (
        "Any criminal record?",
        "Prefer not to say",
        "generated",
    )
    assert second.question_norm == "any criminal record"
    assert len(repo.list_answers()) == 1


def test_upserting_without_an_intent_matches_by_question_text(repo: Repo) -> None:
    first = repo.upsert_answer(answer("Preferred pronouns?", "they/them"))
    second = repo.upsert_answer(answer("preferred   PRONOUNS", "she/her"))
    assert second.id == first.id and second.answer == "she/her"
    assert len(repo.list_answers()) == 1


def test_an_update_without_an_intent_keeps_the_stored_intent(repo: Repo) -> None:
    first = repo.upsert_answer(answer(intent="felony_conviction"))
    again = repo.upsert_answer(answer(FELONY, "Yes"))  # same wording, no intent given
    assert again.id == first.id
    assert (again.intent, again.answer) == ("felony_conviction", "Yes")


def test_upsert_by_id_edits_that_row_including_its_wording(repo: Repo) -> None:
    row = repo.upsert_answer(answer("Old wording?", "a"))
    edited = repo.upsert_answer(answer("New wording?", "b", id=row.id))
    assert edited.id == row.id
    assert (edited.question, edited.question_norm, edited.answer) == (
        "New wording?",
        "new wording",
        "b",
    )
    assert repo.find_answer(question_norm="old wording") is None
    assert len(repo.list_answers()) == 1


def test_upsert_by_a_missing_id_raises(repo: Repo) -> None:
    with pytest.raises(NotFoundError):
        repo.upsert_answer(answer(id=4242))


def test_upsert_needs_something_to_match_by(repo: Repo) -> None:
    with pytest.raises(ValueError, match="intent or question"):
        repo.upsert_answer(answer("", "x"))
    with pytest.raises(ValueError):
        repo.upsert_answer(answer("?!", "x"))  # normalises to nothing
    repo.upsert_answer(answer("", "x", intent="just_an_intent"))  # an intent alone is fine


def test_two_rows_can_never_share_an_intent(repo: Repo) -> None:
    a = repo.upsert_answer(answer("First?", "1", intent="alpha"))
    b = repo.upsert_answer(answer("Second?", "2", intent="beta"))
    with pytest.raises(ValueError, match="already uses intent"):
        repo.upsert_answer(answer("Second?", "2", id=b.id, intent="alpha"))
    assert repo.find_answer(intent="alpha") == a
    assert repo.find_answer(intent="beta") == b  # the failed edit changed nothing


def test_answer_kinds_and_sources_round_trip(repo: Repo) -> None:
    for i, (kind, source) in enumerate(
        [("boolean", "user"), ("text", "profile"), ("choice", "generated"), ("number", "user")]
    ):
        stored = repo.upsert_answer(
            answer(f"Question number {i}?", "x", answer_kind=kind, source=source)
        )
        assert (stored.answer_kind, stored.source) == (kind, source)


def test_list_answers_most_recently_updated_first(repo: Repo, fake_clock: FakeClock) -> None:
    a = repo.upsert_answer(answer("Alpha?", "1"))
    fake_clock.advance(timedelta(minutes=1))
    b = repo.upsert_answer(answer("Beta?", "2"))
    fake_clock.advance(timedelta(minutes=1))
    repo.upsert_answer(answer("Alpha?", "1 edited"))
    assert [r.question for r in repo.list_answers()] == ["Alpha?", "Beta?"]
    assert repo.list_answers()[1].id == b.id and repo.list_answers()[0].id == a.id


def test_delete_answer(repo: Repo) -> None:
    row = repo.upsert_answer(answer(intent="felony_conviction"))
    assert repo.delete_answer(row.id) is True
    assert repo.delete_answer(row.id) is False
    assert repo.find_answer(intent="felony_conviction") is None
    assert repo.list_answers() == []
    repo.upsert_answer(answer(intent="felony_conviction"))  # the intent is free again


def test_touch_answer_counts_uses(repo: Repo, fake_clock: FakeClock) -> None:
    row = repo.upsert_answer(answer())
    stamp = row.updated_at
    fake_clock.advance(timedelta(hours=1))
    for _ in range(3):
        assert repo.touch_answer(row.id) is True
    touched = repo.find_answer(question_norm=row.question_norm)
    assert touched is not None and touched.use_count == 3
    assert touched.updated_at == stamp  # using an answer is not editing it
    assert repo.touch_answer(9999) is False


def test_answers_with_unicode_and_long_text_round_trip(repo: Repo) -> None:
    long_text = ("Ünïcödé 日本語 🚀\r\n" * 5000).strip()  # models strip surrounding whitespace
    stored = repo.upsert_answer(answer("Tell us about yourself", long_text))
    assert repo.find_answer(question_norm="tell us about yourself").answer == long_text
    assert stored.answer == long_text


# ================================================================================== pending questions


def test_add_pending_question_returns_the_stored_row(repo: Repo, fake_clock: FakeClock) -> None:
    stored = repo.add_pending_question(
        pending(
            "Which pronouns do you use?",
            opportunity_id="opp-9",
            kind=QuestionKind.SINGLE_CHOICE,
            options=["she/her", "he/him", "they/them", "Prefer not to say"],
            company="Globex",
        )
    )
    assert stored.id is not None
    assert stored.question == "Which pronouns do you use?"
    assert stored.kind == QuestionKind.SINGLE_CHOICE
    assert stored.options == ["she/her", "he/him", "they/them", "Prefer not to say"]
    assert (stored.opportunity_id, stored.company) == ("opp-9", "Globex")
    assert stored.created_at == fake_clock.now()
    assert stored.resolved is False
    assert repo.list_pending_questions() == [stored]


def test_pending_questions_are_deduplicated_by_normalised_text_and_opportunity(repo: Repo) -> None:
    first = repo.add_pending_question(pending(FELONY, "opp-1", company="Acme"))
    again = repo.add_pending_question(
        pending("  have you EVER been convicted of a felony ", "opp-1")
    )
    assert again.id == first.id and again.question == FELONY  # the original wording is kept
    assert len(repo.list_pending_questions()) == 1


def test_the_same_question_for_another_opportunity_or_none_is_a_separate_entry(repo: Repo) -> None:
    ids = {
        repo.add_pending_question(pending(FELONY, "opp-1")).id,
        repo.add_pending_question(pending(FELONY, "opp-2")).id,
        repo.add_pending_question(pending(FELONY, None)).id,
    }
    assert len(ids) == 3
    # ... and each of them dedups against itself, including the opportunity-less one
    assert repo.add_pending_question(pending(FELONY, None)).id in ids
    # "" is treated as no opportunity
    assert repo.add_pending_question(pending(FELONY, "")).id in ids
    assert len(repo.list_pending_questions()) == 3


def test_a_resolved_question_is_history_and_can_be_queued_again(repo: Repo) -> None:
    first = repo.add_pending_question(pending())
    repo.resolve_pending_question(first.id, "No")
    second = repo.add_pending_question(pending())
    assert second.id != first.id and second.resolved is False
    assert [q.id for q in repo.list_pending_questions()] == [second.id]


@pytest.mark.parametrize("blank", ["", "   ", "?!", "\n\t"])
def test_blank_pending_questions_are_rejected(repo: Repo, blank: str) -> None:
    with pytest.raises(ValueError, match="empty"):
        repo.add_pending_question(pending(blank))
    assert repo.list_pending_questions(unresolved_only=False) == []


def test_list_pending_questions_filters_and_orders_oldest_first(
    repo: Repo, fake_clock: FakeClock
) -> None:
    a = repo.add_pending_question(pending("Question A?", "opp-1"))
    fake_clock.advance(timedelta(minutes=1))
    b = repo.add_pending_question(pending("Question B?", "opp-2"))
    fake_clock.advance(timedelta(minutes=1))
    c = repo.add_pending_question(pending("Question C?", "opp-1"))
    repo.resolve_pending_question(a.id, "yes")
    assert [q.id for q in repo.list_pending_questions()] == [b.id, c.id]
    assert [q.id for q in repo.list_pending_questions(unresolved_only=True)] == [b.id, c.id]
    assert [q.id for q in repo.list_pending_questions(unresolved_only=False)] == [a.id, b.id, c.id]
    assert [q.id for q in repo.list_pending_questions(opportunity_id="opp-1")] == [c.id]
    assert [
        q.id for q in repo.list_pending_questions(unresolved_only=False, opportunity_id="opp-1")
    ] == [a.id, c.id]
    assert repo.list_pending_questions(opportunity_id="nobody") == []


def test_resolving_marks_the_row_and_saves_a_user_answer_findable_by_question_norm(
    repo: Repo,
) -> None:
    queued = repo.add_pending_question(pending(FELONY, "opp-1", company="Acme"))
    resolved = repo.resolve_pending_question(queued.id, "  No  ")
    assert resolved.id == queued.id and resolved.resolved is True
    assert resolved.company == "Acme" and resolved.opportunity_id == "opp-1"
    assert repo.list_pending_questions() == []
    saved = repo.find_answer(question_norm=norm_text(FELONY))
    assert saved is not None
    assert saved.answer == "No"  # trimmed
    assert saved.source == "user"
    assert saved.intent is None
    assert saved.question == FELONY
    assert saved.question_norm == norm_text(FELONY)


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (QuestionKind.BOOLEAN, "boolean"),
        (QuestionKind.SINGLE_CHOICE, "choice"),
        (QuestionKind.MULTI_CHOICE, "choice"),
        (QuestionKind.NUMBER, "number"),
        (QuestionKind.TEXT, "text"),
        (QuestionKind.TEXTAREA, "text"),
        (QuestionKind.DATE, "text"),
        (QuestionKind.UNKNOWN, "text"),
    ],
)
def test_the_saved_answer_kind_follows_the_question_kind(
    repo: Repo, kind: QuestionKind, expected: str
) -> None:
    queued = repo.add_pending_question(pending(f"A {kind.value} question?", kind=kind))
    repo.resolve_pending_question(queued.id, "Yes")
    assert repo.find_answer(question_norm=queued.question).answer_kind == expected


def test_resolving_replaces_an_existing_saved_answer_for_the_same_wording(repo: Repo) -> None:
    repo.upsert_answer(answer(FELONY, "Yes", source="generated"))
    queued = repo.add_pending_question(pending(FELONY))
    repo.resolve_pending_question(queued.id, "No")
    rows = repo.list_answers()
    assert len(rows) == 1 and (rows[0].answer, rows[0].source) == ("No", "user")


def test_resolving_touches_only_the_targeted_row(repo: Repo) -> None:
    here = repo.add_pending_question(pending(FELONY, "opp-1"))
    there = repo.add_pending_question(pending(FELONY, "opp-2"))  # same wording, another company
    repo.resolve_pending_question(here.id, "No")
    assert [q.id for q in repo.list_pending_questions()] == [
        there.id
    ]  # the answer may differ per company


def test_resolving_again_updates_the_answer(repo: Repo) -> None:
    queued = repo.add_pending_question(pending())
    repo.resolve_pending_question(queued.id, "No")
    again = repo.resolve_pending_question(queued.id, "Prefer not to answer")
    assert again.resolved is True
    assert repo.find_answer(question_norm=norm_text(FELONY)).answer == "Prefer not to answer"
    assert len(repo.list_answers()) == 1


def test_resolve_errors(repo: Repo) -> None:
    queued = repo.add_pending_question(pending())
    with pytest.raises(NotFoundError):
        repo.resolve_pending_question(4242, "x")
    with pytest.raises(ValueError, match="empty"):
        repo.resolve_pending_question(queued.id, "   ")
    assert repo.list_pending_questions()[0].id == queued.id  # still pending, nothing saved
    assert repo.list_answers() == []


def test_a_failed_resolve_rolls_back_the_answer_too(repo: Repo) -> None:
    queued = repo.add_pending_question(pending())
    with (
        repo.transaction() as conn
    ):  # make the second half of the resolution fail inside its transaction
        conn.execute(
            "CREATE TRIGGER refuse_resolution BEFORE UPDATE ON pending_questions "
            "BEGIN SELECT RAISE(ABORT, 'refused'); END"
        )
    with pytest.raises(sqlite3.DatabaseError, match="refused"):
        repo.resolve_pending_question(queued.id, "No")
    assert repo.list_answers() == []  # the answer insert was rolled back with the failed resolution
    assert [q.id for q in repo.list_pending_questions()] == [queued.id]


def test_pending_question_options_survive_unicode(repo: Repo) -> None:
    options = [
        "Sí, estoy autorizado",
        "No, no estoy autorizado",
        "日本語 ✓",
        'quote " and \\ backslash',
    ]
    stored = repo.add_pending_question(
        pending("¿Autorizado?", kind=QuestionKind.SINGLE_CHOICE, options=options)
    )
    assert repo.list_pending_questions()[0].options == options == stored.options


def test_pending_questions_do_not_need_the_opportunity_to_exist(repo: Repo) -> None:
    # the answer engine may queue a question before the opportunity was ever stored (soft reference)
    stored = repo.add_pending_question(pending(FELONY, "not-in-opportunities-table"))
    assert stored.opportunity_id == "not-in-opportunities-table"
