"""Tests for the SmartApplier: rules, verified drafts, holds, DB retry semantics,
and end-to-end fills in a real headless Chrome against local HTML forms.

All personal data here is placeholder test data.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from bot.apply.smart import pending
from bot.apply.smart.applier import SmartApplier
from bot.apply.smart.candidate import Candidate, CandidateFacts, normalize
from bot.apply.smart.drafting import draft_answers, verify_text
from bot.apply.smart.questions import Question, clean_label, questions_from_scan
from bot.apply.smart.resolve import pick_option, resolve
from config.settings import UserProfile
from db.database import Database


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

FACTS = {
    "education": [{"school": "Test State University", "degree": "Bachelor of Business Administration",
                   "major": "Information Systems", "gpa": "3.7", "graduation": "May 2028"}],
    "experience": [{"organization": "Acme Robotics", "title": "Business Development Intern",
                    "dates": "Aug 2025 - Dec 2025",
                    "bullets": ["Built an outreach pipeline of 100+ prospects and lifted replies 10%"]}],
    "projects": [{"name": "querykit", "dates": "2026",
                  "bullets": ["Wrote SQL dashboards used by 25 paying customers"]}],
    "skills": {"Technical": ["SQL", "Python"]},
}


def _candidate(motivation: str = "", prepared: dict | None = None, **answers) -> Candidate:
    screening = {"work_authorization": "Yes", "visa_sponsorship": "No",
                 "graduation_date": "May 2028", "major": "Information Systems",
                 "ethnicity": "Not Hispanic or Latino"}
    screening.update(answers)
    profile = UserProfile(first_name="Test", last_name="User", email="test.user@example.com",
                          phone="5125550100", city="Austin", state="Texas", bio="",
                          screening_answers=screening)
    facts = CandidateFacts.model_validate(
        {**FACTS, "motivation": motivation, "prepared_answers": prepared or {}})
    return Candidate(profile=profile, facts=facts)


def q(label, kind="text", options=(), required=False, **kw) -> Question:
    return Question(kind=kind, label=label, selector=f"#{normalize(label)[:10] or 'x'}",
                    required=required, options=tuple(options), **kw)


# ---------------------------------------------------------------------------
# label / option helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_clean_label_strips_required_star(self):
        assert clean_label("Phone *") == ("Phone", True)
        assert clean_label("Why us?✱") == ("Why us?", True)
        assert clean_label("LinkedIn") == ("LinkedIn", False)

    def test_pick_option_exact_then_prefix(self):
        opts = ("Yes, I am authorized", "No")
        assert pick_option(opts, "yes") == "Yes, I am authorized"
        assert pick_option(opts, "no") == "No"

    def test_pick_option_never_substring_inside_word(self):
        # "no" must not match "None of the above" / "Not specified"
        assert pick_option(("None of the above", "Not specified"), "no") is None

    def test_pick_option_ambiguous_returns_none(self):
        assert pick_option(("Austin, TX", "Austin, MN"), "austin") is None

    def test_questions_from_scan_marks_starred_required(self):
        qs = questions_from_scan([{"kind": "text", "label": "City *", "selector": "#c"},
                                  {"kind": "text", "label": "x", "selector": ""}])
        assert len(qs) == 1 and qs[0].required and qs[0].label == "City"


# ---------------------------------------------------------------------------
# deterministic rules
# ---------------------------------------------------------------------------


class TestResolve:
    def test_identity_fields(self):
        c = _candidate()
        assert resolve(q("First Name"), c).value == "Test"
        assert resolve(q("Last Name"), c).value == "User"
        assert resolve(q("Email"), c).value == "test.user@example.com"
        assert resolve(q("Phone"), c).value == "512-555-0100"
        assert resolve(q("Phone", input_type="number"), c).value == "5125550100"
        assert resolve(q("Full name"), c).value == "Test User"

    def test_company_name_is_not_the_candidate_name(self):
        assert resolve(q("Current company name"), _candidate()).value is None

    def test_sponsorship_and_inverted_wording(self):
        c = _candidate()
        yn = ("Yes", "No")
        assert resolve(q("Will you now or in the future require visa sponsorship?", "select", yn), c).value == "No"
        assert resolve(q("Can you work in the US without sponsorship?", "select", yn), c).value == "Yes"

    def test_canada_authorization_is_its_own_answer(self):
        yn = ("Yes", "No")
        c = _candidate()                                   # US-authorized, Canada unknown
        assert resolve(q("Are you legally authorized to work in Canada?", "select", yn), c).value is None
        c2 = _candidate(work_authorization_canada="No", visa_sponsorship_canada="Yes")
        assert resolve(q("Are you legally authorized to work in Canada?", "select", yn), c2).value == "No"
        assert resolve(q("Will you require sponsorship to work in Canada?", "select", yn), c2).value == "Yes"

    def test_authorization_beats_location_wording(self):
        r = resolve(q("Are you legally authorized to work in the country where this job is located?",
                      "radio", ("Yes", "No")), _candidate())
        assert r.value == "Yes" and "work_authorization" in r.source

    def test_skill_question_is_not_prior_employment(self):
        # "Have you ever worked with SQL?" must not be answered as "worked for us before"
        assert not resolve(q("Have you ever worked with SQL?", "select", ("Yes", "No")),
                           _candidate(), company="Globex").answered

    def test_prior_employment_defaults_no(self):
        r = resolve(q("Have you previously worked for Globex?", "select", ("Yes", "No")),
                    _candidate(), company="Globex")
        assert r.value == "No"

    def test_prior_employment_held_when_candidate_worked_there(self):
        r = resolve(q("Have you previously worked for Acme Robotics?", "select", ("Yes", "No")),
                    _candidate(), company="Acme Robotics")
        assert r.value is None

    def test_education_rules(self):
        c = _candidate()
        assert resolve(q("University"), c).value == "Test State University"
        assert resolve(q("GPA", "select", ("3.0 - 3.49", "3.5 - 4.0")), c).value == "3.5 - 4.0"
        assert resolve(q("Expected graduation year"), c).value == "2028"
        assert resolve(q("Graduation date", "select", ("Spring 2028", "Fall 2028")), c).value == "Spring 2028"
        assert resolve(q("Field of study"), c).value == "Information Systems"

    def test_eeo_uses_configured_or_decline(self):
        c = _candidate()
        opts = ("Male", "Female", "Decline To Self Identify")
        r = resolve(q("Gender", "select", opts), c)
        assert (r.value, r.category) == ("Decline To Self Identify", "eeo")
        hisp = resolve(q("Are you Hispanic/Latino?", "select", ("Yes", "No", "Decline To Self Identify")), c)
        assert hisp.value == "No"

    def test_eeo_without_decline_option_is_never_guessed(self):
        r = resolve(q("Race", "select", ("Asian", "White"), required=True), _candidate())
        assert r.value is None and r.category == "eeo"

    def test_consent_requires_opt_in(self):
        box = q("I acknowledge the privacy notice", "checkbox", required=True)
        assert resolve(box, _candidate()).value is None
        assert resolve(box, _candidate(acknowledge_policies="Yes")).value == "Yes"

    def test_signature_never_filled_even_with_opt_in(self):
        sig = q("Signature: type your full legal name", "text", required=True)
        assert resolve(sig, _candidate(acknowledge_policies="Yes")).value is None

    def test_password_never_filled(self):
        assert resolve(q("Password", "password"), _candidate()).category == "credential"

    def test_files_route_to_resume_and_cover_letter(self, tmp_path):
        resume, cover = tmp_path / "r.pdf", tmp_path / "c.pdf"
        c = _candidate()
        assert resolve(q("Resume/CV", "file"), c, resume=resume).value == str(resume)
        assert resolve(q("Cover Letter", "file"), c, resume=resume).value is None
        assert resolve(q("Cover Letter", "file"), c, resume=resume, cover_letter=cover).value == str(cover)
        assert resolve(q("Transcript", "file", required=True), c, resume=resume).value is None

    def test_prepared_answer_overrides_rules(self):
        c = _candidate(prepared={"expected salary": "Open to the posted range"})
        assert resolve(q("What is your expected salary?"), c).value == "Open to the posted range"

    # --- regressions found on live Greenhouse/Lever forms ---------------------

    def test_referrer_name_is_never_the_candidates_name(self):
        c = _candidate()
        assert resolve(q("If you were referred, please share the associate's first and last name"), c).value is None
        assert resolve(q("Referrer last name"), c).value is None
        assert resolve(q("Emergency contact full name"), c).value is None

    def test_follow_up_fields_left_blank(self):
        r = resolve(q("If yes, please specify the individual(s) and their location"), _candidate())
        assert r.value is None

    def test_graduate_degree_question_is_not_graduation_date(self):
        r = resolve(q("Are you currently pursuing a graduate degree?", "select", ("Yes", "No")),
                    _candidate())
        assert r.value != "May 2028"

    def test_highest_level_achieved_is_not_degree_in_progress(self):
        r = resolve(q("Highest level of education achieved?", "select",
                      ("High School", "Bachelor's", "Master's")), _candidate())
        assert r.value is None
        c = _candidate(highest_education_completed="High School")
        assert resolve(q("Highest level of education achieved?", "select",
                         ("High School", "Bachelor's")), c).value == "High School"

    def test_gpa_single_values_never_round_up(self):
        opts = ("On a 4.0 scale.", "4.0", "3.9", "3.8", "3.7")
        assert resolve(q("What is your current overall GPA?", "select", opts),
                       _candidate(gpa="3.85")).value == "3.8"

    def test_sms_opt_in_declined_but_marketing_experience_is_not(self):
        c = _candidate()
        sms = q("By selecting YES, I consent to receive recruiting SMS messages", "combobox", ("Yes", "No"))
        assert resolve(sms, c).value == "No"
        assert resolve(q("Do you have marketing experience?", "select", ("Yes", "No")), c).value is None

    def test_in_person_acknowledgment_never_auto_accepted(self):
        r = resolve(q("I understand this is an in-person role in Philadelphia, PA.", "combobox",
                      ("Yes", "No")), _candidate(acknowledge_policies="Yes"))
        assert r.value is None and r.category == "consent"

    def test_privacy_acknowledgment_with_opt_in(self):
        r = resolve(q("Please acknowledge our Job Applicant Privacy Notice", "combobox", ("Acknowledge",)),
                    _candidate(acknowledge_policies="Yes"))
        assert r.value == "Acknowledge"

    def test_education_section_dates(self):
        c = _candidate(education_start_date="August 2024")
        end_year = Question(kind="text", label="End date year", selector="#e", context="education--0")
        start_month = Question(kind="combobox", label="Start date month", selector="#s",
                               options=("January", "August"), context="education--0")
        assert resolve(end_year, c).value == "2028"
        assert resolve(start_month, c).value == "August"
        # outside an education section the same label is ambiguous
        assert resolve(Question(kind="text", label="End date year", selector="#x"), c).value is None

    def test_referred_yes_no_uses_screening_answer(self):
        r = resolve(q("Were you referred to this role by a current associate?", "combobox", ("Yes", "No")),
                    _candidate(was_referred="No"))
        assert r.value == "No"

    def test_best_match_for_searchable_dropdowns(self):
        from bot.apply.smart.applier import best_match
        cities = ("Austin, Minnesota, United States", "Austin, Texas, United States")
        assert best_match(cities, "Austin, Texas") == "Austin, Texas, United States"
        schools = ("University of Texas at Arlington", "University of Texas at Austin")
        assert best_match(schools, "The University of Texas at Austin") == "University of Texas at Austin"

    def test_graduation_option_shapes(self):
        from bot.apply.smart.resolve import date_choice
        assert date_choice(("2028 - Spring", "2028 - Fall"), "May 2028") == "2028 - Spring"
        ranges = ("I've already graduated", "August 2027 - December 2027", "January 2028 - July 2028")
        assert date_choice(ranges, "May 2028") == "January 2028 - July 2028"
        assert date_choice(("Fall 2023", "Fall 2024"), "August 2024", start=True) == "Fall 2024"

    def test_yes_no_with_if_so_inside_label_is_a_real_question(self):
        from bot.apply.smart.resolve import is_follow_up
        parent = q("(a) Are you or a family member employed by a regulator? If so, explain below.",
                   "combobox", ("Yes", "No"))
        assert not is_follow_up(parent)
        assert is_follow_up(q('(a) If the answer is "Yes," please provide details below:', "textarea"))

    def test_citizenship_status_categories(self):
        opts = ("1) U.S. citizen or national of the United States",
                "2) U.S. lawful permanent resident (green card holder)")
        c = _candidate(citizenship_status="U.S. citizen")
        assert resolve(q("Citizenship Status", "combobox", opts), c).value == opts[0]
        assert resolve(q("Citizenship Status", "combobox", opts), _candidate()).value is None

    def test_unknown_question_is_left_for_drafting(self):
        r = resolve(q("Describe a project where you used SQL", "textarea"), _candidate())
        assert r.value is None and r.category == "none"


# ---------------------------------------------------------------------------
# verified drafting
# ---------------------------------------------------------------------------


def _gen(payload: dict):
    return lambda prompt: json.dumps(payload)


class TestDrafting:
    SQL_Q = q("Describe a project where you used SQL", "textarea", required=True)

    def test_answer_with_real_evidence_is_accepted(self):
        d = draft_answers([self.SQL_Q], _candidate(), _gen({self.SQL_Q.qid: {
            "answer": "I wrote SQL dashboards used by 25 paying customers.",
            "evidence": "Wrote SQL dashboards used by 25 paying customers",
            "status": "answered"}}), "Globex", "Intern", "posting")[self.SQL_Q.qid]
        assert d.ok, d.problems

    def test_fabricated_evidence_is_rejected(self):
        d = draft_answers([self.SQL_Q], _candidate(), _gen({self.SQL_Q.qid: {
            "answer": "I built a data warehouse.", "evidence": "Built a data warehouse at Initech",
            "status": "answered"}}), "Globex", "Intern", "posting")[self.SQL_Q.qid]
        assert not d.ok and "evidence" in d.problems[0]

    def test_invented_number_is_rejected(self):
        d = draft_answers([self.SQL_Q], _candidate(), _gen({self.SQL_Q.qid: {
            "answer": "My SQL dashboards served 400 customers.",
            "evidence": "Wrote SQL dashboards used by 25 paying customers",
            "status": "answered"}}), "Globex", "Intern", "posting")[self.SQL_Q.qid]
        assert not d.ok and any("400" in p for p in d.problems)

    def test_invented_employer_is_rejected(self):
        problems = verify_text("I interned at Initech Global last summer.", _candidate(), "")
        assert any("Initech" in p for p in problems)

    def test_option_answer_must_be_an_option(self):
        sel = q("Preferred team", "select", ("Growth", "Platform"), required=True)
        d = draft_answers([sel], _candidate(), _gen({sel.qid: {
            "answer": "Marketing", "evidence": "Built an outreach pipeline of 100+ prospects",
            "status": "answered"}}), "Globex", "Intern", "")[sel.qid]
        assert not d.ok

    def test_insufficient_is_not_an_answer(self):
        d = draft_answers([self.SQL_Q], _candidate(), _gen({self.SQL_Q.qid: {
            "answer": "", "evidence": "", "status": "insufficient"}}), "G", "I", "")[self.SQL_Q.qid]
        assert not d.ok

    def test_motivation_needs_users_own_words(self):
        why = q("Why do you want to work at Globex?", "textarea", required=True)
        gen_called = []
        d = draft_answers([why], _candidate(), lambda p: gen_called.append(p) or "{}",
                          "Globex", "Intern", "")[why.qid]
        assert not d.ok and not gen_called

    def test_llm_failure_leaves_question_unanswered(self):
        def boom(prompt):
            raise RuntimeError("quota")
        d = draft_answers([self.SQL_Q], _candidate(), boom, "G", "I", "")[self.SQL_Q.qid]
        assert not d.ok


# ---------------------------------------------------------------------------
# pending questions + DB retry semantics
# ---------------------------------------------------------------------------


class TestPending:
    def test_records_and_dedupes(self, tmp_path):
        path = tmp_path / "pending.json"
        item = [("Expected salary?", "text", (), "no stored answer")]
        pending.record(path, "Globex", "Intern", "https://x", item)
        pending.record(path, "Initech", "Intern", "https://y", item)
        data = pending.load(path)
        entry = data[normalize("Expected salary?")]
        assert entry["times_seen"] == 2 and len(entry["companies"]) == 2


def _record(db, ext, status):
    return db.record_application(
        external_id=ext, platform="workbook", job_title="Intern", company="Acme", location=None,
        salary=None, apply_url="https://example.com", match_score=70, resume_path=None,
        cover_letter_path=None, cover_letter_text=None, status=status, error_message=None)


class TestRetrySemantics:
    def test_retryable_statuses_are_retried_until_max_attempts(self, tmp_path):
        db = Database(tmp_path / "t.db")
        _record(db, "a", "needs_answers")
        assert not db.is_done("a", "workbook")
        _record(db, "a", "login_required")
        assert not db.is_done("a", "workbook")
        _record(db, "a", "error")
        assert db.is_done("a", "workbook")          # 3 attempts used

    def test_terminal_statuses_are_done_immediately(self, tmp_path):
        db = Database(tmp_path / "t.db")
        for ext, status in (("x", "applied"), ("y", "submitted_unconfirmed"),
                            ("z", "unsupported"), ("w", "site_blocked")):
            _record(db, ext, status)
            assert db.is_done(ext, "workbook")

    def test_retry_updates_one_row(self, tmp_path):
        db = Database(tmp_path / "t.db")
        first = _record(db, "a", "needs_answers")
        second = _record(db, "a", "applied")
        assert first == second
        assert db.count_applied_today() == 1

    def test_unconfirmed_counts_toward_daily_cap(self, tmp_path):
        db = Database(tmp_path / "t.db")
        _record(db, "u", "submitted_unconfirmed")
        assert db.count_applied_today() == 1


# ---------------------------------------------------------------------------
# end to end in headless Chrome
# ---------------------------------------------------------------------------

_CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

_FORM = """<!doctype html><html><body>
<h1>Strategy Intern</h1><p>We need SQL and customer insight.</p>
<form id="application-form" onsubmit="event.preventDefault(); {on_submit}">
  <label for="first_name">First Name*</label><input id="first_name" required>
  <label for="last_name">Last Name*</label><input id="last_name" required>
  <label for="email">Email*</label><input id="email" type="email" required>
  <label for="phone">Phone</label><input id="phone" type="tel">
  <label for="resume">Resume/CV*</label><input id="resume" type="file" required>
  <label for="auth">Are you legally authorized to work in the United States?*</label>
  <select id="auth" required><option value="">Select...</option><option>Yes</option><option>No</option></select>
  <fieldset><legend>Will you require visa sponsorship?*</legend>
    <label><input type="radio" name="spons" value="y" required>Yes</label>
    <label><input type="radio" name="spons" value="n">No</label></fieldset>
  <label for="gender">Gender</label>
  <select id="gender"><option value="">Select...</option><option>Male</option><option>Female</option>
    <option>Decline To Self Identify</option></select>
  <label for="sql">Describe a project where you used SQL*</label><textarea id="sql" required></textarea>
  {extra}
  <label><input type="checkbox" id="privacy">I acknowledge the privacy notice</label>
  <button type="submit">Submit application</button>
</form></body></html>"""

_THANKS = "document.body.innerHTML = '<h1>Thank you for applying!</h1>';"


@dataclass
class _Raw:
    title: str = "Strategy Intern"
    company: str = "Globex"
    apply_url: str = ""
    description: str = ""


@dataclass
class _Job:
    raw: _Raw


@pytest.fixture(scope="module")
def browser():
    if not os.path.isfile(_CHROME):
        pytest.skip("Google Chrome not installed")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=_CHROME, headless=True)
        yield b
        b.close()


def _sql_gen(prompt: str) -> str:
    # The drafter asks about the textarea by its selector id "#sql"
    return json.dumps({"#sql": {
        "answer": "I wrote SQL dashboards used by 25 paying customers.",
        "evidence": "Wrote SQL dashboards used by 25 paying customers", "status": "answered"}})


def _run(browser, tmp_path, html: str, *, gen=_sql_gen, confirm_timeout=3):
    page_file = tmp_path / "form.html"
    page_file.write_text(html, encoding="utf-8")
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    page = browser.new_page()
    applier = SmartApplier(page, _candidate(), generate=gen,
                           pending_path=tmp_path / "pending.json",
                           cover_letter_dir=tmp_path / "cl")
    applier.CONFIRM_TIMEOUT_S = confirm_timeout
    job = _Job(_Raw(apply_url=page_file.as_uri()))
    with patch.object(SmartApplier, "_random_pause", lambda *a, **k: None):
        result = applier.apply(job, resume, "", None)
    return result, applier, page


class TestFollowUps:
    def _plan(self, parent_value):
        parent = Question(kind="select", label="Do you have relatives here?", selector="#p",
                          required=True, options=("Yes", "No"))
        follow = Question(kind="textarea", label="If yes, please provide details",
                          selector="#f", required=True)
        from bot.apply.smart.applier import Hold, PlanItem
        plan = [PlanItem(parent, parent_value, "prepared")]
        holds = [Hold(follow, "follow-up to another answer")]
        return SmartApplier._settle_follow_ups((parent, follow), plan, holds)

    def test_required_follow_up_after_no_gets_na(self):
        plan, holds = self._plan("No")
        assert not holds and plan[-1].value == "N/A"

    def test_required_follow_up_after_yes_stays_held(self):
        plan, holds = self._plan("Yes")
        assert len(holds) == 1


class TestEndToEnd:
    def test_complete_form_is_filled_submitted_and_confirmed(self, browser, tmp_path):
        result, applier, page = _run(browser, tmp_path, _FORM.format(on_submit=_THANKS, extra=""))
        assert result.success, result.error_message
        filled = {i.question.label: i.value for i in applier.last_plan}
        assert filled["First Name"] == "Test"
        assert filled["Will you require visa sponsorship?"] == "No"
        assert filled["Gender"] == "Decline To Self Identify"
        assert "25 paying customers" in filled["Describe a project where you used SQL"]
        # optional consent box stays unticked without the opt-in
        assert "I acknowledge the privacy notice" not in filled

    def test_required_unanswerable_question_holds_without_submitting(self, browser, tmp_path):
        extra = '<label for="sal">Expected hourly pay*</label><input id="sal" required>'
        result, applier, page = _run(browser, tmp_path, _FORM.format(on_submit=_THANKS, extra=extra))
        assert result.held_for_answers and not result.success
        assert "Expected hourly pay" in result.held_questions
        assert "Thank you" not in page.inner_text("body")  # never submitted
        assert pending.load(tmp_path / "pending.json")

    def test_unverifiable_draft_holds(self, browser, tmp_path):
        bad = lambda p: json.dumps({"#sql": {"answer": "I ran SQL at Initech for 5 years.",
                                             "evidence": "ran SQL at Initech", "status": "answered"}})
        result, _, page = _run(browser, tmp_path, _FORM.format(on_submit=_THANKS, extra=""), gen=bad)
        assert result.held_for_answers
        assert "Thank you" not in page.inner_text("body")

    def test_no_confirmation_is_reported_unconfirmed(self, browser, tmp_path):
        result, _, _ = _run(browser, tmp_path, _FORM.format(on_submit="", extra=""))
        assert result.submitted_unconfirmed and not result.success
        assert result.status == "submitted_unconfirmed"

    def test_site_spam_rejection_stops_retry(self, browser, tmp_path):
        rejected = "document.body.insertAdjacentHTML('beforeend', " \
                   "'<div role=alert>Your application submission was " \
                   "flagged as possible spam.</div>');"
        result, _, _ = _run(browser, tmp_path, _FORM.format(on_submit=rejected, extra=""))
        assert result.site_blocked and not result.success
        assert result.status == "site_blocked"
        assert result.attempts == 1
