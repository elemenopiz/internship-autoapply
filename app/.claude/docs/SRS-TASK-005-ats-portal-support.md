# Software Requirements Specification

**Document ID**: SRS-TASK-005-ats-portal-support
**Version**: 1.0
**Date**: 2026-03-10
**Status**: approved
**Author**: Claude (Requirements Analyst)
**PRD Reference**: PRD Section 9.5, 11

---

## 1. Purpose and Scope

### 1.1 Purpose
Specifies requirements for AutoApply Phase 5 (ATS Portal Support): form-filling automation for Greenhouse and Lever applicant tracking systems, including personal info entry, resume upload, cover letter submission, CAPTCHA detection, and pipeline registration.

### 1.2 Scope
The system SHALL provide: Greenhouse ATS form-filling automation, Lever ATS form-filling automation, automatic URL correction for Lever apply pages, CAPTCHA detection on both portals, form error detection, and registration of both appliers in the bot pipeline via `detect_ats()` routing.

The system SHALL NOT provide: Workday, Taleo, or iCIMS appliers (future phases), CAPTCHA solving (detected and reported only), custom question types beyond standard text fields, screening question AI answers.

### 1.3 Definitions
| Term | Definition |
|------|-----------|
| ATS | Applicant Tracking System — external hiring portal (Greenhouse, Lever, etc.) |
| Greenhouse | ATS platform hosted on greenhouse.io domains |
| Lever | ATS platform hosted on lever.co domains |
| APPLIERS dict | Registry in bot/bot.py mapping ATS platform names to their applier classes |
| CAPTCHA | Challenge-response test to distinguish humans from bots |
| Form error | Validation message displayed by the ATS after form submission attempt |

---

## 2. Functional Requirements

### FR-057: Greenhouse Applier

**Description**: The system SHALL automate job applications on Greenhouse ATS by navigating to the application page, filling personal info fields, uploading a resume, entering a cover letter, and submitting the form.
**Priority**: P0
**Dependencies**: FR-046 (ApplyResult), FR-047 (BrowserManager)

**Acceptance Criteria**:
- **AC-057-1**: Given a Greenhouse job URL and user profile data, When `GreenhouseApplier.apply()` is called, Then it navigates to the URL and fills the first name field (split from `full_name` before the first space) and last name field (remainder of `full_name` after the first space).
- **AC-057-2**: Given the form contains email, phone, and LinkedIn URL fields, When filling the form, Then it populates email, phone, and LinkedIn URL from the user profile.
- **AC-057-3**: Given a resume PDF path is provided, When the form has a file upload input, Then the resume is uploaded via the file chooser.
- **AC-057-4**: Given a cover letter is provided, When the form has a cover letter text area, Then the cover letter text is entered.
- **AC-057-5**: Given the form is fully filled, When submitting, Then it clicks the submit button and waits for confirmation or error.
- **AC-057-6**: Given the application succeeds, Then it returns `ApplyResult(success=True)`.
- **AC-057-7**: Given a CAPTCHA is detected on the page, Then it returns `ApplyResult(success=False, captcha_detected=True)`.
- **AC-057-8**: Given a form validation error is displayed after submission, Then it returns `ApplyResult(success=False, error_message=<error text>)`.
- **AC-057-N1**: Given the submit button is not found, Then it returns `ApplyResult(success=False, manual_required=True)`.

---

### FR-058: Lever Applier

**Description**: The system SHALL automate job applications on Lever ATS by navigating to the application page, filling personal info fields, uploading a resume, entering a cover letter, and submitting the form.
**Priority**: P0
**Dependencies**: FR-046 (ApplyResult), FR-047 (BrowserManager)

**Acceptance Criteria**:
- **AC-058-1**: Given a Lever job URL that does not end with `/apply`, When `LeverApplier.apply()` is called, Then it auto-appends `/apply` to the URL before navigating.
- **AC-058-2**: Given a Lever job URL that already ends with `/apply`, When navigating, Then the URL is used as-is (no double `/apply`).
- **AC-058-3**: Given the form contains name, email, phone, LinkedIn URL, and portfolio URL fields, When filling the form, Then it populates all fields from the user profile (full name without splitting).
- **AC-058-4**: Given a resume PDF path is provided, When the form has a file upload input, Then the resume is uploaded via the file chooser.
- **AC-058-5**: Given a cover letter is provided, When the form has a cover letter or additional info text area, Then the cover letter text is entered.
- **AC-058-6**: Given the form is fully filled, When submitting, Then it clicks the submit button and waits for confirmation or error.
- **AC-058-7**: Given the application succeeds, Then it returns `ApplyResult(success=True)`.
- **AC-058-8**: Given a CAPTCHA is detected on the page, Then it returns `ApplyResult(success=False, captcha_detected=True)`.
- **AC-058-9**: Given a form validation error is displayed after submission, Then it returns `ApplyResult(success=False, error_message=<error text>)`.
- **AC-058-N1**: Given the submit button is not found, Then it returns `ApplyResult(success=False, manual_required=True)`.

---

### FR-059: ATS Pipeline Registration

**Description**: The system SHALL register `GreenhouseApplier` and `LeverApplier` in the `APPLIERS` dict in `bot/bot.py`, and the existing `detect_ats()` function SHALL route jobs with `greenhouse.io` or `lever.co` URLs to the correct applier.
**Priority**: P0
**Dependencies**: FR-045 (ATS Detection), FR-057, FR-058

**Acceptance Criteria**:
- **AC-059-1**: Given a job with an `apply_url` containing "greenhouse.io", When `detect_ats()` is called, Then it returns "greenhouse".
- **AC-059-2**: Given a job with an `apply_url` containing "lever.co", When `detect_ats()` is called, Then it returns "lever".
- **AC-059-3**: Given `detect_ats()` returns "greenhouse", When the bot loop looks up the `APPLIERS` dict, Then it resolves to `GreenhouseApplier`.
- **AC-059-4**: Given `detect_ats()` returns "lever", When the bot loop looks up the `APPLIERS` dict, Then it resolves to `LeverApplier`.
- **AC-059-5**: Given a job with an unrecognized ATS URL, When `detect_ats()` returns None, Then the bot skips application and logs a warning.

---

## 3. Non-Functional Requirements

### NFR-024: Human-Like Interaction Delays
**Description**: All Greenhouse and Lever form interactions SHALL include random delays (30-80ms per keystroke, 0.5-2s between actions) consistent with the existing LinkedIn and Indeed appliers.
**Metric**: No interaction faster than 30ms per character; no two form actions within 500ms.
**Priority**: P0

### NFR-025: CAPTCHA Detection
**Description**: Both Greenhouse and Lever appliers SHALL detect CAPTCHA challenges (reCAPTCHA, hCaptcha, or custom) before or after form submission and report them via `ApplyResult.captcha_detected`.
**Metric**: CAPTCHA elements detected with zero false negatives for reCAPTCHA and hCaptcha iframes.
**Priority**: P0

### NFR-028: Form Error Detection
**Description**: Both appliers SHALL detect form validation errors displayed by the ATS after submission and include the error text in `ApplyResult.error_message` to prevent silent failures.
**Metric**: Any visible error message after submission is captured and returned.
**Priority**: P1

---

## 4. Out of Scope

- **Workday, Taleo, iCIMS appliers** — Future phases
- **CAPTCHA solving** — Detected and reported, not solved
- **Custom question types beyond text fields** — Dropdowns, checkboxes, radio buttons with non-standard options
- **Screening question AI answers** — Future phase
- **Multi-page Greenhouse forms** — Only single-page application forms supported initially
