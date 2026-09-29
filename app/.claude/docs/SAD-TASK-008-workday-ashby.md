# System Architecture Document

**Document ID**: SAD-TASK-008-workday-ashby
**Version**: 1.0
**Date**: 2026-03-10
**Status**: approved (retroactive)
**Author**: Claude (System Engineer)
**SRS Reference**: SRS-TASK-008-workday-ashby (FR-070 through FR-073)

---

## 1. Executive Summary

This architecture adds two new applier modules — `bot/apply/workday.py` and `bot/apply/ashby.py` — extending AutoApply's application automation from 4 ATS platforms (LinkedIn, Indeed, Greenhouse, Lever) to 6. Both follow the established BaseApplier ABC pattern from Phase 3 (ADR-012). The release also introduces a **screening answers** subsystem: a `dict` stored on `UserProfile.screening_answers`, populated via the Settings UI and Setup Wizard, and consumed by the new appliers to pre-fill common screening questions (work authorization, visa sponsorship, years of experience, EEO disclosures). The `APPLIERS` dict in `bot/bot.py` expands from 4 to 6 entries, and `ATS_FINGERPRINTS` in `core/filter.py` already includes `myworkdayjobs.com` and `ashbyhq.com` mappings. WorkdayApplier handles Workday's multi-step wizard form using `data-automation-id` selectors; AshbyApplier handles Ashby's single-page React form using standard `name`/`type` attribute selectors.

---

## 2. Architecture Overview

### 2.1 Component Diagram

```
┌──────────────┐      ┌──────────────┐
│   bot.py     │─────>│ filter.py    │
│ (main loop)  │      │ detect_ats() │
│ APPLIERS dict│      └──────┬───────┘
└──────┬───────┘             │
       │                     │ returns "workday" | "ashby" | ...
       v                     v
┌───────────────────────────────────────────────────────────────────┐
│                      APPLIERS dispatch (6 entries)                │
├──────────┬──────────┬────────────┬────────┬──────────┬───────────┤
│LinkedIn  │Indeed    │Greenhouse  │Lever   │Workday   │Ashby      │
│Applier   │Applier   │Applier     │Applier │Applier   │Applier    │
│(Phase 3) │(Phase 3) │(Phase 5)   │(Phase 5)│(v1.8.0) │(v1.8.0)  │
└──────────┴──────────┴────────────┴────────┴──────────┴───────────┘
       │              │            │         │          │       │
       v              v            v         v          v       v
┌───────────────────────────────────────────────────────────────────┐
│                      BaseApplier ABC                              │
│  _human_type() · _random_pause() · _detect_captcha()             │
│  apply() abstract                                                 │
└───────────────────────────────────────────────────────────────────┘
       │                                         │
       v                                         v
┌──────────────┐                    ┌────────────────────────┐
│  Playwright   │                    │  UserProfile            │
│  Page object  │                    │  .screening_answers     │
└──────────────┘                    └────────────────────────┘
```

### 2.2 Data Flow

1. Bot loop calls `detect_ats(job.raw.apply_url)` which checks URL domains against `ATS_FINGERPRINTS`.
2. If domain contains `myworkdayjobs.com`, returns `"workday"`; if `ashbyhq.com`, returns `"ashby"`.
3. Bot loop looks up `APPLIERS["workday"]` or `APPLIERS["ashby"]` and instantiates with the Playwright page.
4. `applier.apply(job, resume_pdf_path, cover_letter_text, profile)` is called.
5. The applier reads `profile.screening_answers` to pre-fill screening questions and EEO fields.
6. Result is returned as `ApplyResult` for database recording.

### 2.3 Screening Answers Data Flow

```
Settings UI / Wizard UI
        │
        │  _collectScreeningAnswers()  /  _collectWizardScreeningAnswers()
        v
  PUT /api/config  ──>  config.json  ──>  UserProfile.screening_answers: dict
        │
        v
  WorkdayApplier._answer_screening_questions(profile)
  WorkdayApplier._fill_voluntary_disclosures(profile)
  WorkdayApplier._fill_self_identification(profile)
  AshbyApplier._answer_custom_questions(profile)
```

### 2.4 Layer Architecture

| Layer | Component | Responsibility |
|-------|-----------|----------------|
| Orchestration | `bot/bot.py` | Routes jobs to correct applier via APPLIERS dict |
| Detection | `core/filter.py` | Identifies ATS platform from URL domain |
| Application | `bot/apply/workday.py` | Workday multi-step wizard automation |
| Application | `bot/apply/ashby.py` | Ashby single-page form automation |
| Base | `bot/apply/base.py` | Shared ABC, human delays, CAPTCHA detection |
| Configuration | `config/settings.py` | `UserProfile.screening_answers` storage |
| Frontend | `templates/index.html` | Screening answers collection UI (Settings + Wizard) |
| Infrastructure | Playwright | Browser automation via persistent context |

---

## 3. Component Catalog

### 3.1 WorkdayApplier (`bot/apply/workday.py`)

| Attribute | Value |
|-----------|-------|
| Class | `WorkdayApplier(BaseApplier)` |
| Module | `bot.apply.workday` |
| LOC | ~470 |
| Form model | Multi-step wizard (up to 12 steps) |
| Selector strategy | `data-automation-id` attributes (Workday React SPA) |
| Element timeout | 8000 ms (`_ELEMENT_TIMEOUT`) |
| Steps | Sign In -> My Information -> My Experience -> Application Questions -> Voluntary Disclosures -> Self-Identification -> Review -> Submit |

**Public methods**: `apply(job, resume_pdf_path, cover_letter_text, profile) -> ApplyResult`

**Private methods**:
| Method | Purpose |
|--------|---------|
| `_do_apply()` | Inner implementation, multi-step loop |
| `_click_apply_button()` | Click initial Apply on job detail page |
| `_handle_auth_page(profile)` | Handle sign-in / create-account page |
| `_fill_my_information(profile)` | Fill name, phone, address, email |
| `_fill_my_experience(profile, resume_pdf_path)` | Upload resume |
| `_fill_application_questions(profile, cover_letter_text)` | Fill textareas and screening questions |
| `_answer_screening_questions(profile)` | Match labels to screening_answers by keyword |
| `_fill_voluntary_disclosures(profile)` | EEO dropdowns (gender, ethnicity, veteran) |
| `_fill_self_identification(profile)` | Disability status dropdown |
| `_click_next_or_submit()` | Click Next button |
| `_click_submit()` | Click Submit button on review page |
| `_is_submitted()` | Check for thank-you confirmation |
| `_get_page_errors()` | Extract visible error text |
| `_select_dropdown(automation_id_part, value)` | Workday listbox dropdown interaction |

### 3.2 AshbyApplier (`bot/apply/ashby.py`)

| Attribute | Value |
|-----------|-------|
| Class | `AshbyApplier(BaseApplier)` |
| Module | `bot.apply.ashby` |
| LOC | ~280 |
| Form model | Single-page React form |
| Selector strategy | Standard HTML `name`, `type`, `placeholder` attributes |
| URL pattern | `jobs.ashbyhq.com/{company}/application/{id}` |

**Public methods**: `apply(job, resume_pdf_path, cover_letter_text, profile) -> ApplyResult`

**Private methods**:
| Method | Purpose |
|--------|---------|
| `_do_apply()` | Inner implementation, single-page flow |
| `_fill_form_fields(profile)` | Fill name, email, phone, LinkedIn, portfolio, location |
| `_upload_resume(resume_path)` | Upload resume via file input |
| `_fill_cover_letter(text)` | Fill cover letter or additional info textarea |
| `_answer_custom_questions(profile)` | Match labels to screening_answers by key similarity |

### 3.3 Screening Answers Configuration

| Attribute | Value |
|-----------|-------|
| Storage | `UserProfile.screening_answers: dict` in `config/settings.py` |
| Persistence | Serialized into `~/.autoapply/config.json` via `save_config()` |
| UI entry points | Settings panel (`_collectScreeningAnswers()`) and Setup Wizard (`_collectWizardScreeningAnswers()`) |

### 3.4 ATS Fingerprints (Extended)

```python
ATS_FINGERPRINTS = {
    "greenhouse.io": "greenhouse",
    "lever.co": "lever",
    "myworkdayjobs.com": "workday",      # new in v1.8.0
    "ashbyhq.com": "ashby",              # new in v1.8.0
    "taleo.net": "taleo",
    "icims.com": "icims",
    "linkedin.com/jobs": "linkedin",
    "linkedin.com": "linkedin",
    "indeed.com": "indeed",
}
```

### 3.5 APPLIERS Dict (Extended)

```python
APPLIERS = {
    "linkedin": LinkedInApplier,
    "indeed": IndeedApplier,
    "greenhouse": GreenhouseApplier,
    "lever": LeverApplier,
    "workday": WorkdayApplier,           # new in v1.8.0
    "ashby": AshbyApplier,               # new in v1.8.0
}
```

---

## 4. Interface Contracts

### 4.1 WorkdayApplier.apply()

**Purpose**: Submit a job application through a Workday-powered portal.
**Category**: command (browser side effect)

**Signature**:
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| job | `ScoredJob` | yes | Scored job with `.raw.apply_url` pointing to `*.myworkdayjobs.com` |
| resume_pdf_path | `Path \| None` | yes | Path to tailored resume PDF, or None to skip upload |
| cover_letter_text | `str` | yes | Cover letter text for first empty textarea |
| profile | `UserProfile` | yes | User profile with personal info + `.screening_answers` |

**Output**:
| Field | Type | Description |
|-------|------|-------------|
| return | `ApplyResult` | `success=True` on confirmed submission |

**Error Conditions**:
| Condition | Handling |
|-----------|----------|
| Navigation timeout (45s) | Caught in `apply()` wrapper, returns `ApplyResult(success=False)` |
| CAPTCHA detected | Returns `ApplyResult(success=False, captcha_detected=True)` |
| Apply button not found | Returns `ApplyResult(success=False, manual_required=True)` |
| Ran out of steps (12 max) | Returns `ApplyResult(success=False, error_message="...ran out of steps")` |
| Form validation error | Logged as warning, continues attempting next step |
| Resume upload failure | Caught and logged at debug level, continues without resume |

**Side Effects**: Navigates browser, fills forms across multiple pages, uploads file, clicks submit.
**Thread Safety**: Not safe — tied to a single Playwright page instance.

---

### 4.2 AshbyApplier.apply()

**Purpose**: Submit a job application through an Ashby-powered portal.
**Category**: command (browser side effect)

**Signature**:
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| job | `ScoredJob` | yes | Scored job with `.raw.apply_url` pointing to `jobs.ashbyhq.com` |
| resume_pdf_path | `Path \| None` | yes | Path to tailored resume PDF |
| cover_letter_text | `str` | yes | Cover letter text |
| profile | `UserProfile` | yes | User profile with personal info + `.screening_answers` |

**Output**:
| Field | Type | Description |
|-------|------|-------------|
| return | `ApplyResult` | `success=True` on confirmed submission or no error visible |

**Error Conditions**:
| Condition | Handling |
|-----------|----------|
| Navigation timeout (30s) | Caught in `apply()` wrapper |
| CAPTCHA detected | Returns `ApplyResult(success=False, captcha_detected=True)` |
| Submit button not found | Returns `ApplyResult(success=False, manual_required=True)` |
| Form error after submit | Returns `ApplyResult(success=False, error_message="Ashby form error: ...")` |
| No success/error indicators | Assumes success (optimistic) |
| Resume upload failure | Caught at debug level, continues |

**Side Effects**: Navigates browser, fills single-page form, uploads file, clicks submit.
**Thread Safety**: Not safe — tied to a single Playwright page instance.

---

## 5. Data Model

### 5.1 screening_answers Dict Structure

The `UserProfile.screening_answers` field is a flat `dict[str, str]` with the following well-known keys:

| Key | Source UI | Consumed By | Workday Usage | Ashby Usage |
|-----|-----------|-------------|---------------|-------------|
| `work_authorization` | Settings + Wizard | `_answer_screening_questions()` | Matched via `"authorized"` keyword in label | Matched via key-to-label similarity |
| `visa_sponsorship` | Settings + Wizard | `_answer_screening_questions()` | Matched via `"sponsorship"` keyword | Matched via key-to-label similarity |
| `years_experience` | Settings + Wizard | `_answer_screening_questions()` | Matched via `"experience"` keyword | Matched via key-to-label similarity |
| `desired_salary` | Settings + Wizard | `_answer_screening_questions()` | Matched via `"salary"` keyword | Matched via key-to-label similarity |
| `willing_to_relocate` | Settings + Wizard | `_answer_screening_questions()` | Matched via `"relocate"` keyword | Matched via key-to-label similarity |
| `start_date` | Settings + Wizard | `_answer_screening_questions()` | Matched via `"start date"` keyword | Matched via key-to-label similarity |
| `referred_by` | (not in UI) | `_answer_screening_questions()` | Matched via `"referred"` keyword | Matched via key-to-label similarity |
| `gender` | Settings only | `_fill_voluntary_disclosures()` | Dropdown: `data-automation-id*="gender"` | N/A |
| `ethnicity` | Settings only | `_fill_voluntary_disclosures()` | Dropdown: `data-automation-id*="ethnicity"` | N/A |
| `veteran_status` | Settings only | `_fill_voluntary_disclosures()` | Dropdown: `data-automation-id*="veteranStatus"` | N/A |
| `disability_status` | Settings only | `_fill_self_identification()` | Dropdown: `data-automation-id*="disability"` | N/A |
| `workday_password` | (not in UI) | `_handle_auth_page()` | Password field for Workday sign-in | N/A |

**Storage format**: Flat JSON object inside `config.json`:
```json
{
  "profile": {
    "screening_answers": {
      "work_authorization": "Yes",
      "visa_sponsorship": "No",
      "years_experience": "5",
      "gender": "Decline to self-identify",
      "ethnicity": "Decline to self-identify",
      "veteran_status": "I am not a protected veteran",
      "disability_status": "I do not wish to answer"
    }
  }
}
```

### 5.2 Database Impact

No new database entities or schema changes. The existing `applications` table stores results from all applier types. The `platform` column will now contain `"workday"` or `"ashby"` in addition to the existing 4 platform strings.

---

## 6. Workday Multi-Step Flow

Workday applications use a React SPA with a multi-step wizard. The applier iterates through up to 12 steps, calling all fill methods on each step (only visible fields are filled, invisible ones are skipped).

### 6.1 Step-by-Step Sequence

```
Step 0: Job Detail Page
  ├── Click Apply button (selectors tried in order):
  │   1. [data-automation-id="adventureButton"]       (Quick Apply)
  │   2. [data-automation-id="applyManually"]
  │   3. a[data-automation-id="jobPostingApplyButton"]
  │   4. button[data-automation-id="jobPostingApplyButton"]
  │   5. a:has-text("Apply"), button:has-text("Apply") (fallback)
  └── If none found → return manual_required

Step 1: Auth Page (conditional)
  ├── Detect: [data-automation-id="createAccountLink"] or email input
  ├── Fill email: [data-automation-id="email"] or [data-automation-id="signIn-email"]
  ├── Fill password from screening_answers["workday_password"]
  │   Selector: [data-automation-id="password"] or [data-automation-id="signIn-password"]
  └── Click: [data-automation-id="signInSubmitButton"] or [data-automation-id="createAccountSubmitButton"]

Step 2: My Information
  ├── [data-automation-id="legalNameSection_firstName"]  ← profile.first_name
  ├── [data-automation-id="legalNameSection_lastName"]   ← profile.last_name
  ├── [data-automation-id="addressSection_addressLine1"] ← profile.address_line1
  ├── [data-automation-id="addressSection_city"]         ← profile.city
  ├── [data-automation-id="addressSection_postalCode"]   ← profile.zip_code
  ├── [data-automation-id="phone-number"]                ← profile.phone
  ├── [data-automation-id="email"]                       ← profile.email
  ├── Dropdown "country"                                 ← profile.country
  └── Dropdown "addressSection_countryRegion"            ← profile.state

Step 3: My Experience
  └── File upload: [data-automation-id="file-upload-input-ref"]
      or input[type="file"][data-automation-id*="upload"]
      or input[type="file"]

Step 4: Application Questions
  ├── First empty <textarea> ← cover_letter_text (filled once)
  └── Screening questions via _answer_screening_questions():
      Match <label> text against keyword map, fill associated input

Step 5: Voluntary Disclosures (EEO)
  ├── Dropdown "gender"        ← screening_answers["gender"]
  ├── Dropdown "ethnicity"     ← screening_answers["ethnicity"]
  └── Dropdown "veteranStatus" ← screening_answers["veteran_status"]

Step 6: Self-Identification
  └── Dropdown "disability"    ← screening_answers["disability_status"]

Step 7: Review + Submit
  ├── Try [data-automation-id="bottom-navigation-next-button"]:has-text("Submit")
  ├── Try [data-automation-id="submit-button"]
  ├── Try button:has-text("Submit Application")
  └── Try button:has-text("Submit")

Confirmation Detection:
  ├── [data-automation-id="thankYouMessage"]
  ├── text="Thank you"
  ├── text="Application submitted"
  ├── text="Your application has been submitted"
  └── h1:has-text("Thank")
```

### 6.2 Navigation Logic

The main loop runs up to `max_steps = 12` iterations. On each iteration:
1. Check for CAPTCHA.
2. Check if already submitted (`_is_submitted()`).
3. Check for page errors.
4. Call all `_fill_*` methods (each is a no-op if its fields are not visible).
5. Click Next (`[data-automation-id="bottom-navigation-next-button"]`).
6. If Next not found, try Submit.
7. If Submit clicked, wait and check for confirmation.

### 6.3 Dropdown Helper

Workday dropdowns use `aria-haspopup="listbox"` buttons. The `_select_dropdown(automation_id_part, value)` method:
1. Finds button matching `data-automation-id*="{automation_id_part}"` with `aria-haspopup="listbox"`.
2. Clicks the button to open the listbox.
3. Waits for `[role="listbox"]` (3s timeout).
4. Selects `[role="option"]:has-text("{value}")`.

---

## 7. Ashby Single-Page Flow

Ashby applications use a clean single-page React form with standard HTML inputs.

### 7.1 Step-by-Step Sequence

```
Step 1: Navigate to apply URL (30s timeout)
  └── CAPTCHA check

Step 2: Click Apply (if on job detail page)
  └── a/button:has-text("Apply for this job") or :has-text("Apply")

Step 3: Fill Personal Info (_fill_form_fields)
  ├── input[name="name"] or input[name*="Name"]     ← profile.full_name
  ├── input[name*="firstName"]                        ← profile.first_name
  ├── input[name*="lastName"]                         ← profile.last_name
  ├── input[name="email"] or input[type="email"]      ← profile.email
  ├── input[name="phone"] or input[type="tel"]        ← profile.phone_full
  ├── input[name*="linkedin"]                         ← profile.linkedin_url
  ├── input[name*="website"] or [name*="portfolio"]   ← profile.portfolio_url
  └── input[name*="location"]                         ← profile.location

Step 4: Upload Resume
  └── input[type="file"][name*="resume"] or
      input[type="file"][accept*="pdf"] or
      input[type="file"]

Step 5: Fill Cover Letter
  ├── textarea[name*="cover"] or [placeholder*="cover letter"]
  └── Fallback: textarea[name*="additional"] or [placeholder*="anything else"]

Step 6: Answer Custom Questions (_answer_custom_questions)
  └── Match <label> text against screening_answers keys (see Section 8)

Step 7: Submit
  ├── button[type="submit"]:has-text("Submit")
  ├── button[type="submit"]:has-text("Apply")
  └── button[type="submit"]

Confirmation Detection:
  ├── text="Your application has been submitted"
  ├── text="Application submitted"
  ├── text="Thank you"
  ├── h1:has-text("Thank")
  └── h2:has-text("Thank")

Error Detection:
  ├── [role="alert"]
  ├── .error-message
  └── .form-error
```

---

## 8. Screening Answer Matching

The two appliers use different matching strategies to connect saved answers to form questions.

### 8.1 Workday: Keyword-Based Matching

`_answer_screening_questions()` builds a static `question_map` of keyword-to-answer pairs:

```python
question_map = {
    "authorized":   answers.get("work_authorization", ""),
    "sponsorship":  answers.get("visa_sponsorship", ""),
    "relocate":     answers.get("willing_to_relocate", ""),
    "experience":   answers.get("years_experience", ""),
    "salary":       answers.get("desired_salary", ""),
    "start date":   answers.get("start_date", ""),
    "referred":     answers.get("referred_by", ""),
}
```

Algorithm:
1. Query all `<label>` elements on the page.
2. For each label, get `inner_text().lower()`.
3. For each keyword in `question_map`, check if keyword is a substring of the label text.
4. If match found: get the label's `for` attribute, find the input by `id`, fill it if empty.

This is a **partial substring match** — e.g., a label "Are you authorized to work in the US?" matches the `"authorized"` keyword.

### 8.2 Ashby: Key Similarity Matching

`_answer_custom_questions()` uses a bidirectional substring match on screening_answers keys:

```python
key_lower = key.replace("_", " ").lower()
if key_lower in label_text or label_text in key_lower:
    # match found
```

Algorithm:
1. Query all `<label>` elements on the page.
2. For each label, get `inner_text().strip().lower()`.
3. For each key-value pair in `screening_answers`:
   - Convert key to lowercase, replace underscores with spaces (e.g., `"work_authorization"` -> `"work authorization"`).
   - Check if key is substring of label text **OR** label text is substring of key.
4. If match found: get the label's `for` attribute, find the input by `id`.
5. Handle element type: `<select>` uses `select_option(label=value)`, `<textarea>` uses `fill()`, others use `_human_type()`.
6. Break after first matching key per label.

### 8.3 Matching Strategy Comparison

| Aspect | Workday | Ashby |
|--------|---------|-------|
| Match source | Fixed keyword map (7 entries) | All screening_answers keys |
| Match method | Keyword substring in label | Bidirectional substring (key in label or label in key) |
| Key transformation | None (hardcoded keywords) | Replace `_` with space, lowercase |
| Element type handling | Always `_human_type()` | Dispatches by tag: select/textarea/input |
| EEO fields | Separate `_fill_voluntary_disclosures()` + `_fill_self_identification()` methods | Not handled (Ashby rarely includes EEO) |

---

## 9. Architecture Decision Records

### ADR-014: data-automation-id Selectors for Workday

**Status**: accepted
**Context**: Workday is a React SPA that renders form elements dynamically. Standard CSS class selectors are unreliable because Workday uses obfuscated class names (e.g., `.WDMN`, `.WD56`) that change across versions. Need a stable selector strategy.
**Decision**: Use `data-automation-id` attributes as the primary selector strategy for Workday. These are stable automation hooks that Workday provides across all tenant deployments. Examples: `data-automation-id="legalNameSection_firstName"`, `data-automation-id="bottom-navigation-next-button"`, `data-automation-id="file-upload-input-ref"`.
**Rationale**: `data-automation-id` attributes are explicitly designed for automation and remain consistent across Workday versions and employer customizations. Selenium and Playwright Workday automation communities confirm these selectors are stable. Text-based fallback selectors (`:has-text("Apply")`, `:has-text("Next")`) provide resilience when automation IDs are absent.
**Consequences**: Selectors are human-readable and maintainable. If Workday deprecates specific automation IDs, fallback text selectors catch most cases. The `_select_dropdown()` helper encapsulates the Workday-specific listbox pattern (`aria-haspopup="listbox"` -> `[role="listbox"]` -> `[role="option"]`).

### ADR-015: Single-Page vs Multi-Step Detection

**Status**: accepted
**Context**: Workday uses a multi-step wizard (up to 8 pages), while Ashby uses a single-page form. The applier architecture must handle both paradigms.
**Decision**: WorkdayApplier uses a step-loop pattern (`for step in range(max_steps)`) that calls all `_fill_*` methods on every step (each is a no-op if its fields are not visible). AshbyApplier uses a linear single-pass flow (fill fields -> upload -> submit). No shared multi-step abstraction is introduced.
**Rationale**: The two form paradigms are fundamentally different. Workday's step loop must handle variable page counts (different employers configure different steps), unknown step ordering, and mid-flow errors. Ashby's single page has no navigation between steps. A shared abstraction would add complexity without benefit. The BaseApplier ABC already provides the common interface.
**Consequences**: Future multi-step ATS platforms (e.g., Taleo, iCIMS) can follow the WorkdayApplier step-loop pattern. Future single-page platforms follow the AshbyApplier linear pattern. Both patterns are documented as reference implementations.

### ADR-016: Screening Answer Storage Format

**Status**: accepted
**Context**: Need to store user-provided answers to common screening questions (work authorization, visa sponsorship, EEO disclosures, etc.) in a format accessible to all appliers.
**Decision**: Store as a flat `dict[str, str]` on `UserProfile.screening_answers`, persisted in `config.json`. Keys use snake_case identifiers (e.g., `work_authorization`, `visa_sponsorship`). Values are free-text strings that match expected form field values (e.g., `"Yes"`, `"No"`, `"5"`).
**Rationale**: A flat dict is the simplest structure that supports arbitrary key-value pairs. Snake_case keys map naturally to both Workday keyword matching and Ashby key-similarity matching. The dict is open-ended — users or future appliers can add custom keys without schema changes. Pydantic serializes `dict` to/from JSON transparently. The Settings UI collects a fixed set of well-known keys, but the structure is extensible.
**Consequences**: No migration needed — the field defaults to `{}`. New screening answer keys can be added to the UI without backend changes. Matching quality depends on key naming conventions matching question label text. The `workday_password` key demonstrates extensibility (not in UI, but consumed by `_handle_auth_page()`).

---

## 10. Error Handling Strategy

| Scenario | Handling | User Impact |
|----------|----------|-------------|
| Workday Apply button not found | Returns `manual_required=True` | Bot logs error, job marked for manual apply |
| Workday auth page present, no password | Signs in with email only (password field skipped) | May fail at auth step |
| Workday form error mid-wizard | Logged as warning, continues to next step | May accumulate errors |
| Workday ran out of steps (12 max) | Returns failure with descriptive message | Bot skips job |
| Ashby Submit button not found | Returns `manual_required=True` | Job marked for manual apply |
| Ashby form error after submit | Error text extracted (200 char limit), returned in `ApplyResult` | Bot logs details |
| Ashby no clear success/error | Assumes success (optimistic) | May record false positive |
| CAPTCHA on either platform | Returns `captcha_detected=True` | Bot skips job, emits CAPTCHA event |
| Resume upload failure (either) | Caught at debug level, continues | Application submitted without resume |
| Screening answer key mismatch | No match found, field left empty | Form may be incomplete |
| Dropdown option not found | `_select_dropdown()` returns silently | Dropdown left at default |

---

## 11. Design Traceability Matrix

| Requirement | Type | Design Component | Source Files | Interface | ADR |
|-------------|------|-----------------|--------------|-----------|-----|
| FR-070 | FR | WorkdayApplier | `bot/apply/workday.py` | `WorkdayApplier.apply()` | ADR-014, ADR-015 |
| FR-070.1 | FR | Workday multi-step navigation | `bot/apply/workday.py` | `_click_next_or_submit()`, `_click_submit()` | ADR-015 |
| FR-070.2 | FR | Workday My Information | `bot/apply/workday.py` | `_fill_my_information()` | ADR-014 |
| FR-070.3 | FR | Workday resume upload | `bot/apply/workday.py` | `_fill_my_experience()` | ADR-014 |
| FR-070.4 | FR | Workday auth handling | `bot/apply/workday.py` | `_handle_auth_page()` | ADR-014 |
| FR-070.5 | FR | Workday EEO/disclosures | `bot/apply/workday.py` | `_fill_voluntary_disclosures()`, `_fill_self_identification()` | ADR-014 |
| FR-070.6 | FR | Workday dropdown interaction | `bot/apply/workday.py` | `_select_dropdown()` | ADR-014 |
| FR-071 | FR | AshbyApplier | `bot/apply/ashby.py` | `AshbyApplier.apply()` | ADR-015 |
| FR-071.1 | FR | Ashby personal info | `bot/apply/ashby.py` | `_fill_form_fields()` | — |
| FR-071.2 | FR | Ashby resume upload | `bot/apply/ashby.py` | `_upload_resume()` | — |
| FR-071.3 | FR | Ashby cover letter | `bot/apply/ashby.py` | `_fill_cover_letter()` | — |
| FR-071.4 | FR | Ashby custom questions | `bot/apply/ashby.py` | `_answer_custom_questions()` | ADR-016 |
| FR-072 | FR | Screening answers subsystem | `config/settings.py`, `templates/index.html` | `UserProfile.screening_answers`, `_collectScreeningAnswers()` | ADR-016 |
| FR-072.1 | FR | Screening answers persistence | `config/settings.py` | `save_config()` -> `config.json` | ADR-016 |
| FR-072.2 | FR | Screening answers UI (Settings) | `templates/index.html` | `_collectScreeningAnswers()` (10 fields) | ADR-016 |
| FR-072.3 | FR | Screening answers UI (Wizard) | `templates/index.html` | `_collectWizardScreeningAnswers()` (6 fields) | ADR-016 |
| FR-073 | FR | Screening answer auto-fill | `bot/apply/workday.py`, `bot/apply/ashby.py` | `_answer_screening_questions()`, `_answer_custom_questions()` | ADR-016 |
| FR-073.1 | FR | Workday keyword-based matching | `bot/apply/workday.py` | `_answer_screening_questions()` — keyword map (7 entries) | ADR-016 |
| FR-073.2 | FR | Ashby key-similarity matching | `bot/apply/ashby.py` | `_answer_custom_questions()` — bidirectional substring match | ADR-016 |

---

*End of SAD-TASK-008-workday-ashby*
