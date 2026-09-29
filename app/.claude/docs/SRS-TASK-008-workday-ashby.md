# Software Requirements Specification

**Document ID**: SRS-TASK-008-workday-ashby
**Version**: 1.0
**Date**: 2026-03-10
**Status**: approved (retroactive — shipped in v1.8.0)
**Author**: Claude (Requirements Analyst)
**PRD Reference**: PRD Section 9.8

---

## 1. Purpose and Scope

### 1.1 Purpose
Specifies requirements for AutoApply v1.8.0: Workday and Ashby ATS application automation, a user-facing Application Answers configuration UI, and automatic screening-answer auto-fill during bot-driven applications.

### 1.2 Scope
The system SHALL provide:
- Workday multi-step application form automation (*.myworkdayjobs.com)
- Ashby single-page application form automation (jobs.ashbyhq.com)
- Application Answers UI in both the Setup Wizard and Settings page for pre-filling common screening question answers
- Screening answer auto-fill that matches form labels to saved answers during Workday and Ashby applications
- ATS fingerprint registration for Workday and Ashby URL patterns
- Pipeline registration of both appliers in the APPLIERS dict

The system SHALL NOT provide:
- CAPTCHA solving (detected and reported only)
- AI-generated answers to novel screening questions
- Tesla, Microsoft, Amazon, Meta, Google, or Apple custom portal appliers (future phases)
- Taleo or iCIMS appliers (future phases)

### 1.3 Definitions
| Term | Definition |
|------|-----------|
| Workday | Enterprise ATS hosted on *.myworkdayjobs.com; uses React SPA with `data-automation-id` attributes |
| Ashby | ATS hosted on jobs.ashbyhq.com; used by OpenAI, YC startups; single-page React form |
| Screening Answers | User-provided answers to common application questions (work authorization, visa sponsorship, etc.) stored in `profile.screening_answers` |
| EEO Disclosures | Equal Employment Opportunity voluntary disclosures (gender, ethnicity, veteran status, disability) |
| `data-automation-id` | Workday's custom HTML attribute used as stable selectors for form elements |
| APPLIERS dict | Registry in `bot/bot.py` mapping ATS platform name strings to their applier classes |
| ATS_FINGERPRINTS | Dict in `core/filter.py` mapping URL substrings to platform names for ATS detection |

### 1.4 References
| Document | Relationship |
|----------|-------------|
| SRS-TASK-005 (ATS Portal Support) | Predecessor — established Greenhouse/Lever applier pattern and BaseApplier ABC |
| SAD-TASK-005 (ATS Portal SAD) | Architecture — ADR-012 applier plugin pattern reused here |
| SRS-TASK-004 (Bot Core) | FR-045 detect_ats(), FR-046 ApplyResult, FR-047 BrowserManager |

---

## 2. Functional Requirements

### FR-070: Workday Application Automation

**Description**: The system SHALL automate job applications on Workday ATS (*.myworkdayjobs.com) by navigating to the job page, clicking the Apply button, handling authentication, and processing a multi-step wizard form including My Information, My Experience, Application Questions, Voluntary Disclosures, Self-Identification, and Review/Submit.
**Priority**: P0
**Dependencies**: FR-046 (ApplyResult), FR-047 (BrowserManager), FR-073 (Screening Answer Auto-Fill)

**Source Files**: `bot/apply/workday.py` (WorkdayApplier class)

**Acceptance Criteria**:

- **AC-070-1**: Given a Workday job URL (*.myworkdayjobs.com) and a user profile, When `WorkdayApplier.apply()` is called, Then it navigates to the URL with `wait_until="domcontentloaded"` and a 45-second timeout.

- **AC-070-2**: Given the job detail page has an Apply button (via `data-automation-id` attributes: `adventureButton`, `applyManually`, or `jobPostingApplyButton`), When the page loads, Then it clicks the Apply button to enter the application wizard.

- **AC-070-3**: Given no known Apply button selector matches, When the page loads, Then it falls back to any `<a>` or `<button>` element containing "Apply" text.

- **AC-070-4**: Given the application presents a sign-in or create-account page (`data-automation-id="createAccountLink"` or email input), When authentication is required, Then it fills email from the user profile and password from `profile.screening_answers["workday_password"]` if available, and clicks the sign-in/create-account submit button.

- **AC-070-5**: Given the user is already authenticated (session cookie), When the auth page check runs, Then it is a no-op and proceeds to the form.

- **AC-070-6**: Given the My Information step is visible, When processing the step, Then it fills first name, last name, address line 1, city, postal code, phone, and email using `data-automation-id` selectors, and selects country and state via dropdown helpers.

- **AC-070-7**: Given the My Experience step is visible and a resume PDF path is provided, When processing the step, Then it uploads the resume via the file upload input (`data-automation-id="file-upload-input-ref"` or `input[type="file"]`).

- **AC-070-8**: Given the Application Questions step is visible, When processing the step, Then it fills the first empty textarea with the cover letter text, and calls the screening answer auto-fill logic (FR-073).

- **AC-070-9**: Given the Voluntary Disclosures step is visible and `screening_answers` contains `gender`, `ethnicity`, or `veteran_status`, When processing the step, Then it selects the appropriate values via Workday dropdown controls.

- **AC-070-10**: Given the Self-Identification step is visible and `screening_answers` contains `disability_status`, When processing the step, Then it selects the disability status via dropdown.

- **AC-070-11**: Given the form wizard has a Next button (`data-automation-id="bottom-navigation-next-button"`), When a step is complete, Then it clicks Next to advance. The wizard processes up to 12 steps maximum.

- **AC-070-12**: Given the Review/Submit page is reached, When Submit is clicked, Then it checks for a confirmation message (`data-automation-id="thankYouMessage"`, or text containing "Thank you" / "Application submitted") and returns `ApplyResult(success=True)`.

- **AC-070-13**: Given the application completes all 12 steps without finding a submission confirmation, Then it returns `ApplyResult(success=False, error_message="Could not complete Workday application — ran out of steps")`.

- **AC-070-N1**: Given a CAPTCHA is detected at any point during the application (initial page or within the form), Then it returns `ApplyResult(success=False, captcha_detected=True, error_message="CAPTCHA detected")`.

- **AC-070-N2**: Given the Apply button is not found on the job detail page, Then it returns `ApplyResult(success=False, manual_required=True, error_message="Workday Apply button not found")`.

- **AC-070-N3**: Given an unhandled exception occurs during any step, Then the top-level exception handler catches it, logs the error, and returns `ApplyResult(success=False, error_message=<exception string>)`.

- **AC-070-N4**: Given a form validation error is displayed on the page (via `data-automation-id="errorMessage"`, `.WDMN .WD56`, or `[role="alert"]`), Then the error text (truncated to 200 chars) is logged as a warning.

---

### FR-071: Ashby Application Automation

**Description**: The system SHALL automate job applications on Ashby ATS (jobs.ashbyhq.com) by navigating to the application page, filling a single-page form with personal info, resume upload, cover letter, custom questions, and submitting.
**Priority**: P0
**Dependencies**: FR-046 (ApplyResult), FR-047 (BrowserManager), FR-073 (Screening Answer Auto-Fill)

**Source Files**: `bot/apply/ashby.py` (AshbyApplier class)

**Acceptance Criteria**:

- **AC-071-1**: Given an Ashby job URL (jobs.ashbyhq.com) and a user profile, When `AshbyApplier.apply()` is called, Then it navigates to the URL with `wait_until="domcontentloaded"` and a 30-second timeout.

- **AC-071-2**: Given the page shows a job detail view with an "Apply" or "Apply for this job" button, When the page loads, Then it clicks the button to reveal the application form.

- **AC-071-3**: Given the application form is visible, When filling personal info, Then it populates fields using standard HTML `name` attribute selectors: full name (or first/last name), email, phone, LinkedIn URL, portfolio URL, and current location.

- **AC-071-4**: Given a resume PDF path is provided, When the form has a file upload input (matching `input[type="file"]` with name containing "resume" or accepting PDFs), Then the resume is uploaded via `set_input_files()`.

- **AC-071-5**: Given cover letter text is provided, When the form has a cover letter textarea (matching name/placeholder containing "cover", "Cover", "letter", "Cover Letter"), Then the text is entered via `fill()`.

- **AC-071-6**: Given no explicit cover letter field exists but an "Additional information" textarea is present, When filling cover letter, Then the cover letter text is entered there as fallback.

- **AC-071-7**: Given the form contains custom questions rendered as labeled form groups, When `_answer_custom_questions()` is called, Then it matches label text against `screening_answers` keys (with underscores replaced by spaces) and fills matching inputs, selects, or textareas.

- **AC-071-8**: Given the form is fully filled, When the submit button is found (`button[type="submit"]` with "Submit" or "Apply" text), Then it clicks submit and waits 2-4 seconds for confirmation.

- **AC-071-9**: Given submission succeeds (page shows "Your application has been submitted", "Application submitted", "Thank you", etc.), Then it returns `ApplyResult(success=True)`.

- **AC-071-10**: Given no clear success or error indicator appears after submission, Then it returns `ApplyResult(success=True)` as a best-effort assumption.

- **AC-071-N1**: Given a CAPTCHA is detected on the page, Then it returns `ApplyResult(success=False, captcha_detected=True, error_message="CAPTCHA detected")`.

- **AC-071-N2**: Given the submit button is not found, Then it returns `ApplyResult(success=False, manual_required=True, error_message="Submit button not found on Ashby form")`.

- **AC-071-N3**: Given a form validation error is displayed after submission (`[role="alert"]`, `.error-message`, `.form-error`), Then it returns `ApplyResult(success=False, error_message="Ashby form error: <text>")` with error text truncated to 200 chars.

- **AC-071-N4**: Given an unhandled exception occurs, Then the top-level exception handler catches it, logs the error, and returns `ApplyResult(success=False, error_message=<exception string>)`.

---

### FR-072: Application Answers Configuration

**Description**: The system SHALL provide a UI in both the Setup Wizard and the Settings page for users to pre-fill answers to common application screening questions. Answers are stored in `profile.screening_answers` (a dict) within `config.json` and persisted across sessions.
**Priority**: P1
**Dependencies**: FR-001 (UserProfile), FR-004 (Settings API)

**Source Files**: `templates/index.html` (Wizard step 3, Settings "Application Answers" section), `config/settings.py` (UserProfile.screening_answers field)

**Acceptance Criteria**:

- **AC-072-1**: Given the Setup Wizard is on step 3 (Personal Info), When the user expands the "Application Answers (recommended)" details section, Then the following fields are displayed:

  | Field | Type | Options / Placeholder |
  |-------|------|-----------------------|
  | Work Authorization | Select | Yes / No |
  | Visa Sponsorship Needed? | Select | Yes / No |
  | Years of Experience | Text input | e.g. 5 |
  | Desired Salary | Text input | e.g. 150000 |
  | Willing to Relocate? | Select | Yes / No |
  | Earliest Start Date | Text input | e.g. Immediately, 2 weeks |

- **AC-072-2**: Given the Settings page, When the user scrolls to the "Application Answers" section, Then the same six fields from AC-072-1 are displayed, plus an expandable "EEO / Voluntary Disclosures (optional)" section containing:

  | Field | Type | Options |
  |-------|------|---------|
  | Gender | Select | Male / Female / Non-Binary / Decline to Self Identify |
  | Ethnicity | Select | Hispanic or Latino / White / Black or African American / Asian / Native Hawaiian or Pacific Islander / American Indian or Alaska Native / Two or More Races / Decline to Self Identify |
  | Veteran Status | Select | I am a veteran / I am not a veteran / Decline to Self Identify |
  | Disability Status | Select | (as configured) |

- **AC-072-3**: Given the user completes the Wizard, When `_collectWizardScreeningAnswers()` executes, Then it collects non-empty values from the six Wizard fields and includes them in the profile payload as `screening_answers` with keys: `work_authorization`, `visa_sponsorship`, `years_experience`, `desired_salary`, `willing_to_relocate`, `start_date`.

- **AC-072-4**: Given the user saves Settings, When `_collectScreeningAnswers()` executes, Then it collects non-empty values from all ten Settings fields (six core + four EEO) and includes them in the profile payload as `screening_answers` with keys: `work_authorization`, `visa_sponsorship`, `years_experience`, `desired_salary`, `willing_to_relocate`, `start_date`, `gender`, `ethnicity`, `veteran_status`, `disability_status`.

- **AC-072-5**: Given saved config is loaded, When the Settings page populates, Then all screening answer fields are pre-filled from `profile.screening_answers` in the stored config.

- **AC-072-N1**: Given the user leaves all screening answer fields empty, When saving, Then `screening_answers` is saved as an empty dict `{}` and no errors occur.

- **AC-072-N2**: Given the `screening_answers` key is missing from a legacy config file, When loading the profile, Then the Pydantic model defaults it to `{}` and the UI shows empty fields.

---

### FR-073: Screening Answer Auto-Fill

**Description**: The system SHALL automatically match screening question labels on Workday and Ashby application forms to saved `profile.screening_answers` values and fill them during automated applications.
**Priority**: P1
**Dependencies**: FR-070, FR-071, FR-072

**Source Files**: `bot/apply/workday.py` (_answer_screening_questions), `bot/apply/ashby.py` (_answer_custom_questions)

**Acceptance Criteria**:

- **AC-073-1** (Workday): Given `profile.screening_answers` contains entries, When the Workday Application Questions step is visible, Then `_answer_screening_questions()` scans all visible `<label>` elements and matches their text against a keyword map:

  | Label keyword | screening_answers key |
  |---------------|-----------------------|
  | "authorized" | `work_authorization` |
  | "sponsorship" | `visa_sponsorship` |
  | "relocate" | `willing_to_relocate` |
  | "experience" | `years_experience` |
  | "salary" | `desired_salary` |
  | "start date" | `start_date` |
  | "referred" | `referred_by` |

- **AC-073-2** (Workday): Given a label matches a keyword and the associated input (via `label[for]` attribute) is visible and empty, When auto-filling, Then the answer is typed into the input using `_human_type()` with human-like delays.

- **AC-073-3** (Ashby): Given `profile.screening_answers` contains entries, When `_answer_custom_questions()` is called, Then it scans all visible `<label>` elements and matches label text against screening answer keys (with underscores replaced by spaces for fuzzy matching).

- **AC-073-4** (Ashby): Given a label matches a screening answer key and the associated input is a `<select>`, When auto-filling, Then the answer is selected via `select_option(label=value)`.

- **AC-073-5** (Ashby): Given a label matches a screening answer key and the associated input is a `<textarea>`, When auto-filling, Then the answer is entered via `fill()`.

- **AC-073-6** (Ashby): Given a label matches a screening answer key and the associated input is a standard `<input>`, When auto-filling, Then the answer is typed via `_human_type()`.

- **AC-073-7** (Workday EEO): Given `screening_answers` contains `gender`, `ethnicity`, `veteran_status`, or `disability_status`, When the Voluntary Disclosures or Self-Identification steps are visible, Then the values are selected via Workday dropdown controls (`_select_dropdown()` using `aria-haspopup="listbox"` buttons and `[role="listbox"]` option lists).

- **AC-073-N1**: Given `profile.screening_answers` is empty or `{}`, When auto-fill is attempted, Then no form fields are modified and no errors occur.

- **AC-073-N2**: Given a label on the form does not match any keyword or screening answer key, When scanning labels, Then that label is skipped silently.

- **AC-073-N3**: Given a matched input already has a value (non-empty), When auto-fill checks the field, Then it skips that field to avoid overwriting user-entered or pre-filled data.

---

## 3. Non-Functional Requirements

### NFR-030: Human-Like Interaction Delays (Workday & Ashby)
**Description**: All Workday and Ashby form interactions SHALL include random delays consistent with existing appliers: 30-80ms per keystroke via `_human_type()`, 0.5-2s between actions via `_random_pause()`, and 2-4s pauses after page navigation and submission.
**Metric**: No interaction faster than 30ms per character; no two form actions within 200ms.
**Priority**: P0

### NFR-031: Workday Element Timeout
**Description**: Workday React element rendering waits SHALL use an 8-second timeout (`_ELEMENT_TIMEOUT = 8000`). Page navigation SHALL use a 45-second timeout. Dropdown listbox rendering SHALL use a 3-second timeout.
**Metric**: Timeouts match specified values.
**Priority**: P1

### NFR-032: Ashby Page Timeout
**Description**: Ashby page navigation SHALL use a 30-second timeout. No explicit element timeout is required as Ashby forms render server-side.
**Metric**: Page timeout is 30 seconds.
**Priority**: P1

### NFR-033: CAPTCHA Detection (Workday & Ashby)
**Description**: Both Workday and Ashby appliers SHALL detect CAPTCHA challenges (reCAPTCHA, hCaptcha, or custom) via the shared `_detect_captcha()` method from BaseApplier and report them via `ApplyResult.captcha_detected`.
**Metric**: CAPTCHA elements detected with zero false negatives for reCAPTCHA and hCaptcha iframes.
**Priority**: P0

### NFR-034: Form Error Resilience
**Description**: Both appliers SHALL handle unexpected exceptions via top-level try/except, logging the error and returning `ApplyResult(success=False)` rather than crashing the bot loop.
**Metric**: Zero unhandled exceptions propagating from `apply()` methods.
**Priority**: P0

### NFR-035: Screening Answers Data Integrity
**Description**: The `screening_answers` dict SHALL be stored as a JSON-serializable dict within the Pydantic `UserProfile` model. Missing keys SHALL default to empty string. Legacy configs without `screening_answers` SHALL default to `{}`.
**Metric**: No `KeyError` or deserialization failures when loading configs from any version.
**Priority**: P1

---

## 4. Out of Scope

- **CAPTCHA solving** — Detected and reported, not solved
- **AI-generated answers** — Novel questions not matching screening_answers are left blank
- **Workday multi-factor authentication** — Only email/password sign-in supported
- **Ashby file upload for cover letter** — Only textarea-based cover letter entry
- **Tesla, Microsoft, Amazon, Meta, Google, Apple portals** — Future phase
- **Taleo, iCIMS appliers** — Future phase
- **Editing screening answers per-application** — All applications use the same saved answers

---

## 5. Traceability Seeds

| Req ID | User Story | Design Ref | Source Files | Unit Tests | Integ Tests | Docs |
|--------|------------|------------|--------------|------------|-------------|------|
| FR-070 | US-070: As a user, I want the bot to auto-apply on Workday so I don't fill multi-step forms manually | SAD-TASK-008 §3.1 | `bot/apply/workday.py` | `tests/test_appliers_ats.py` | `tests/test_integration.py` | `docs/guides/how-the-bot-works.md` |
| FR-071 | US-071: As a user, I want the bot to auto-apply on Ashby so I can apply to YC/OpenAI jobs automatically | SAD-TASK-008 §3.2 | `bot/apply/ashby.py` | `tests/test_appliers_ats.py` | `tests/test_integration.py` | `docs/guides/how-the-bot-works.md` |
| FR-072 | US-072: As a user, I want to pre-fill screening answers once so the bot uses them on every application | SAD-TASK-008 §3.3 | `templates/index.html`, `config/settings.py` | `tests/test_settings.py` | `tests/test_integration.py` | `docs/guides/configuration.md` |
| FR-073 | US-073: As a user, I want the bot to auto-fill screening questions from my saved answers so I don't miss common fields | SAD-TASK-008 §3.4 | `bot/apply/workday.py`, `bot/apply/ashby.py` | `tests/test_appliers_ats.py` | `tests/test_integration.py` | `docs/guides/how-the-bot-works.md` |

---

## 6. ATS Pipeline Registration

Both appliers are registered in the system via:

1. **ATS_FINGERPRINTS** in `core/filter.py`:
   - `"myworkdayjobs.com"` → `"workday"`
   - `"ashbyhq.com"` → `"ashby"`

2. **APPLIERS** dict in `bot/bot.py`:
   - `"workday"` → `WorkdayApplier`
   - `"ashby"` → `AshbyApplier`

3. **detect_ats()** routes jobs by matching `apply_url` against ATS_FINGERPRINTS domain substrings.

---

## 7. Assumptions and Constraints

1. Workday uses `data-automation-id` attributes as stable selectors. If Workday changes these IDs, selectors must be updated.
2. Ashby uses standard HTML form elements with `name` attributes. Selector stability depends on Ashby maintaining these conventions.
3. Workday's multi-step wizard has at most 12 steps. Applications requiring more steps will fail with a timeout.
4. Workday dropdown controls use `aria-haspopup="listbox"` and `[role="listbox"]` patterns. Non-standard dropdown implementations are not supported.
5. Screening answer matching is keyword-based (Workday) or key-name-based (Ashby). Questions with non-standard phrasing may not match.
6. EEO disclosure fields are optional. If the user does not configure them, those steps are passed through without modification.
