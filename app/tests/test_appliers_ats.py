"""Unit tests for Greenhouse and Lever ATS appliers.

Requirement traceability:
    FR-057 — Greenhouse form-filling applier
    FR-058 — Lever form-filling applier
    FR-059 — ATS applier registration in bot pipeline
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from bot.apply.greenhouse import GreenhouseApplier
from bot.apply.lever import LeverApplier
from bot.search.base import RawJob
from core.filter import ScoredJob

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _make_profile(**overrides):
    """Create a mock UserProfile."""
    profile = MagicMock()
    profile.full_name = overrides.get("full_name", "Jane Doe")
    profile.first_name = overrides.get("first_name", "Jane")
    profile.last_name = overrides.get("last_name", "Doe")
    profile.email = overrides.get("email", "jane@example.com")
    profile.phone_full = overrides.get("phone_full", "+1-555-0100")
    profile.linkedin_url = overrides.get("linkedin_url", "https://linkedin.com/in/janedoe")
    profile.portfolio_url = overrides.get("portfolio_url", "https://janedoe.dev")
    return profile


def _make_scored_job(apply_url: str = "https://boards.greenhouse.io/acme/jobs/123") -> ScoredJob:
    """Create a ScoredJob with a given apply_url."""
    raw = RawJob(
        title="Software Engineer",
        company="Acme Corp",
        location="Remote",
        salary="$130K",
        description="Build things.",
        apply_url=apply_url,
        platform="greenhouse",
        external_id="gh-123",
        posted_at=None,
    )
    return ScoredJob(
        id="test-uuid", raw=raw, score=85,
        pass_filter=True, skip_reason=None,
    )


def _make_page():
    """Create a MagicMock Playwright page with sensible defaults."""
    page = MagicMock()
    page.url = "https://boards.greenhouse.io/acme/jobs/123"
    # query_selector returns None by default (no elements found)
    page.query_selector.return_value = None
    return page


# ===================================================================
# GreenhouseApplier
# ===================================================================


class TestGreenhouseApplierSuccess:
    """FR-057: Greenhouse happy path — form fill and submit."""

    @patch("bot.apply.base.time.sleep")
    def test_successful_application(self, _sleep):
        """FR-057: Successful Greenhouse submit returns success=True."""
        page = _make_page()
        submit_btn = MagicMock()
        submit_btn.is_visible.return_value = True

        # query_selector routing: apply button → None, submit → found, error → None
        def qs_side_effect(selector):
            if "submit" in selector.lower():
                return submit_btn
            return None
        page.query_selector.side_effect = qs_side_effect

        applier = GreenhouseApplier(page)
        job = _make_scored_job()
        profile = _make_profile()

        result = applier.apply(job, Path("/tmp/resume.pdf"), "Cover letter", profile)

        assert result.success is True
        page.goto.assert_called_once()
        submit_btn.click.assert_called_once()


class TestGreenhouseApplierCaptcha:
    """FR-057: CAPTCHA detection on Greenhouse."""

    @patch("bot.apply.base.time.sleep")
    def test_captcha_detected(self, _sleep):
        """FR-057: Returns captcha_detected=True when CAPTCHA found."""
        page = _make_page()
        # _detect_captcha finds a CAPTCHA element
        page.query_selector.return_value = MagicMock()

        applier = GreenhouseApplier(page)
        result = applier.apply(
            _make_scored_job(), None, "", _make_profile(),
        )

        assert result.success is False
        assert result.captcha_detected is True


class TestGreenhouseApplierNoSubmit:
    """FR-057: Missing submit button."""

    @patch("bot.apply.base.time.sleep")
    def test_no_submit_button(self, _sleep):
        """FR-057: Returns manual_required when no submit button found."""
        page = _make_page()
        page.query_selector.return_value = None

        applier = GreenhouseApplier(page)
        result = applier.apply(
            _make_scored_job(), None, "", _make_profile(),
        )

        assert result.success is False
        assert result.manual_required is True
        assert "Submit button not found" in result.error_message


class TestGreenhouseApplierFormError:
    """FR-057: Server-side validation error after submit."""

    @patch("bot.apply.base.time.sleep")
    def test_form_error_after_submit(self, _sleep):
        """FR-057: Returns error when Greenhouse shows validation errors."""
        page = _make_page()
        submit_btn = MagicMock()
        error_el = MagicMock()
        error_el.is_visible.return_value = True
        error_el.inner_text.return_value = "Email is required"

        call_count = {"n": 0}

        def qs_side_effect(selector):
            call_count["n"] += 1
            if "submit" in selector.lower() and "error" not in selector.lower():
                return submit_btn
            if "error" in selector.lower() or "field_with_errors" in selector.lower():
                return error_el
            return None
        page.query_selector.side_effect = qs_side_effect

        applier = GreenhouseApplier(page)
        result = applier.apply(
            _make_scored_job(), None, "", _make_profile(),
        )

        assert result.success is False
        assert "Greenhouse form error" in result.error_message


class TestGreenhouseApplierException:
    """FR-057: Unexpected exception during apply."""

    @patch("bot.apply.base.time.sleep")
    def test_exception_returns_failure(self, _sleep):
        """FR-057: Exception during navigation returns error ApplyResult."""
        page = _make_page()
        page.goto.side_effect = Exception("Network timeout")

        applier = GreenhouseApplier(page)
        result = applier.apply(
            _make_scored_job(), None, "", _make_profile(),
        )

        assert result.success is False
        assert "Network timeout" in result.error_message


class TestGreenhouseFormFields:
    """FR-057: Greenhouse form field filling."""

    @patch("bot.apply.base.time.sleep")
    def test_fills_first_and_last_name(self, _sleep):
        """FR-057: Splits full_name into first and last for Greenhouse fields."""
        page = _make_page()
        first_name_input = MagicMock()
        first_name_input.input_value.return_value = ""
        last_name_input = MagicMock()
        last_name_input.input_value.return_value = ""

        def qs(selector):
            if "first_name" in selector:
                return first_name_input
            if "last_name" in selector:
                return last_name_input
            return None
        page.query_selector.side_effect = qs

        applier = GreenhouseApplier(page)
        profile = _make_profile(first_name="Alice", last_name="Smith")
        applier._fill_form_fields(profile)

        # _human_type is called character by character — verify the input was targeted
        assert first_name_input.type.call_count == len("Alice")
        assert last_name_input.type.call_count == len("Smith")


# ===================================================================
# LeverApplier
# ===================================================================


class TestLeverApplierSuccess:
    """FR-058: Lever happy path — form fill and submit."""

    @patch("bot.apply.base.time.sleep")
    def test_successful_application(self, _sleep):
        """FR-058: Successful Lever submit returns success=True."""
        page = _make_page()
        page.url = "https://jobs.lever.co/acme/abc-123/apply"
        form = MagicMock()
        submit_btn = MagicMock()

        def qs(selector):
            # Error selectors — return None (no errors)
            if "error" in selector.lower():
                return None
            # Form detection
            if "form" in selector.lower() or "application" in selector.lower():
                return form
            if "submit" in selector.lower():
                return submit_btn
            return None
        page.query_selector.side_effect = qs

        applier = LeverApplier(page)
        job = _make_scored_job("https://jobs.lever.co/acme/abc-123")
        result = applier.apply(job, Path("/tmp/resume.pdf"), "Cover letter", _make_profile())

        assert result.success is True
        # Should append /apply to URL
        page.goto.assert_called_once()
        call_url = page.goto.call_args[0][0]
        assert call_url.endswith("/apply")


class TestLeverApplierCaptcha:
    """FR-058: CAPTCHA detection on Lever."""

    @patch("bot.apply.base.time.sleep")
    def test_captcha_detected(self, _sleep):
        """FR-058: Returns captcha_detected=True when CAPTCHA found."""
        page = _make_page()
        page.query_selector.return_value = MagicMock()

        applier = LeverApplier(page)
        result = applier.apply(
            _make_scored_job("https://jobs.lever.co/acme/123"),
            None, "", _make_profile(),
        )

        assert result.success is False
        assert result.captcha_detected is True


class TestLeverApplierNoForm:
    """FR-058: No application form found."""

    @patch("bot.apply.base.time.sleep")
    def test_no_form_found(self, _sleep):
        """FR-058: Returns manual_required when form not found."""
        page = _make_page()
        page.query_selector.return_value = None

        applier = LeverApplier(page)
        result = applier.apply(
            _make_scored_job("https://jobs.lever.co/acme/123"),
            None, "", _make_profile(),
        )

        assert result.success is False
        assert result.manual_required is True
        assert "form not found" in result.error_message


class TestLeverApplierNoSubmit:
    """FR-058: Form found but no submit button."""

    @patch("bot.apply.base.time.sleep")
    def test_no_submit_button(self, _sleep):
        """FR-058: Returns manual_required when submit button missing."""
        page = _make_page()
        form = MagicMock()

        def qs(selector):
            if "form" in selector.lower() or "application" in selector.lower():
                return form
            return None
        page.query_selector.side_effect = qs

        applier = LeverApplier(page)
        result = applier.apply(
            _make_scored_job("https://jobs.lever.co/acme/123"),
            None, "", _make_profile(),
        )

        assert result.success is False
        assert result.manual_required is True
        assert "Submit button not found" in result.error_message


class TestLeverApplierFormError:
    """FR-058: Server-side validation error after submit."""

    @patch("bot.apply.base.time.sleep")
    def test_form_error_after_submit(self, _sleep):
        """FR-058: Returns error when Lever shows validation errors."""
        page = _make_page()
        form = MagicMock()
        submit_btn = MagicMock()
        error_el = MagicMock()
        error_el.is_visible.return_value = True
        error_el.inner_text.return_value = "Name is required"

        def qs(selector):
            if "form" in selector.lower() or "application" in selector.lower():
                return form
            if "submit" in selector.lower():
                return submit_btn
            if "error" in selector.lower():
                return error_el
            return None
        page.query_selector.side_effect = qs

        applier = LeverApplier(page)
        result = applier.apply(
            _make_scored_job("https://jobs.lever.co/acme/123"),
            None, "", _make_profile(),
        )

        assert result.success is False
        assert "Lever form error" in result.error_message


class TestLeverApplierException:
    """FR-058: Unexpected exception during apply."""

    @patch("bot.apply.base.time.sleep")
    def test_exception_returns_failure(self, _sleep):
        """FR-058: Exception during navigation returns error ApplyResult."""
        page = _make_page()
        page.goto.side_effect = Exception("Connection refused")

        applier = LeverApplier(page)
        result = applier.apply(
            _make_scored_job("https://jobs.lever.co/acme/123"),
            None, "", _make_profile(),
        )

        assert result.success is False
        assert "Connection refused" in result.error_message


class TestLeverApplierApplyUrlAppend:
    """FR-058: Lever URL /apply append logic."""

    @patch("bot.apply.base.time.sleep")
    def test_appends_apply_when_missing(self, _sleep):
        """FR-058: Appends /apply to URL when not present."""
        page = _make_page()
        page.query_selector.return_value = None  # triggers form-not-found early exit

        applier = LeverApplier(page)
        applier.apply(
            _make_scored_job("https://jobs.lever.co/acme/123"),
            None, "", _make_profile(),
        )

        call_url = page.goto.call_args[0][0]
        assert call_url == "https://jobs.lever.co/acme/123/apply"

    @patch("bot.apply.base.time.sleep")
    def test_does_not_double_append_apply(self, _sleep):
        """FR-058: Does not double-append /apply."""
        page = _make_page()
        page.query_selector.return_value = None

        applier = LeverApplier(page)
        applier.apply(
            _make_scored_job("https://jobs.lever.co/acme/123/apply"),
            None, "", _make_profile(),
        )

        call_url = page.goto.call_args[0][0]
        assert call_url == "https://jobs.lever.co/acme/123/apply"
        assert "/apply/apply" not in call_url


class TestLeverFormFields:
    """FR-058: Lever form field filling."""

    @patch("bot.apply.base.time.sleep")
    def test_fills_name_email_phone(self, _sleep):
        """FR-058: Fills name, email, phone fields."""
        page = _make_page()
        name_input = MagicMock()
        name_input.input_value.return_value = ""
        email_input = MagicMock()
        email_input.input_value.return_value = ""
        phone_input = MagicMock()
        phone_input.input_value.return_value = ""

        def qs(selector):
            if "name='name'" in selector:
                return name_input
            if "name='email'" in selector:
                return email_input
            if "name='phone'" in selector:
                return phone_input
            return None
        page.query_selector.side_effect = qs

        applier = LeverApplier(page)
        profile = _make_profile()
        applier._fill_form_fields(profile)

        assert name_input.type.call_count == len("Jane Doe")
        assert email_input.type.call_count == len("jane@example.com")
        assert phone_input.type.call_count == len("+1-555-0100")


# ===================================================================
# Pipeline registration (FR-059)
# ===================================================================


class TestAtsApplierRegistration:
    """FR-059: Greenhouse and Lever appliers registered in APPLIERS dict."""

    def test_greenhouse_in_appliers(self):
        """FR-059: GreenhouseApplier registered under 'greenhouse' key."""
        from bot.bot import APPLIERS
        assert "greenhouse" in APPLIERS
        assert APPLIERS["greenhouse"] is GreenhouseApplier

    def test_lever_in_appliers(self):
        """FR-059: LeverApplier registered under 'lever' key."""
        from bot.bot import APPLIERS
        assert "lever" in APPLIERS
        assert APPLIERS["lever"] is LeverApplier

    def test_ats_detection_routes_to_greenhouse(self):
        """FR-059: detect_ats() returns 'greenhouse' for greenhouse.io URLs."""
        from core.filter import detect_ats
        assert detect_ats("https://boards.greenhouse.io/acme/jobs/123") == "greenhouse"

    def test_ats_detection_routes_to_lever(self):
        """FR-059: detect_ats() returns 'lever' for lever.co URLs."""
        from core.filter import detect_ats
        assert detect_ats("https://jobs.lever.co/acme/abc-123") == "lever"
