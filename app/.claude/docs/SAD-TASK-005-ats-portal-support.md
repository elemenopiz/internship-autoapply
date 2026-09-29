# System Architecture Document

**Document ID**: SAD-TASK-005-ats-portal-support
**Version**: 1.0
**Date**: 2026-03-10
**Status**: approved
**Author**: Claude (System Engineer)
**SRS Reference**: SRS-TASK-005-ats-portal-support

---

## 1. Executive Summary

This architecture adds two new applier modules — `bot/apply/greenhouse.py` and `bot/apply/lever.py` — extending AutoApply's application automation to cover Greenhouse and Lever ATS portals. Both follow the established BaseApplier ABC pattern from Phase 3, reusing the same human-like delay utilities, CAPTCHA detection, and error handling wrapper. The bot loop's `APPLIERS` dict expands from 2 to 4 entries, with jobs routed automatically by `detect_ats()` URL domain matching.

## 2. Architecture Overview

### 2.1 Component Diagram

```
┌──────────────┐      ┌──────────────┐
│   bot.py     │─────▶│ filter.py    │
│ (main loop)  │      │ detect_ats() │
│ APPLIERS dict│      └──────┬───────┘
└──────┬───────┘             │
       │                     │ returns "greenhouse" | "lever" | ...
       ▼                     ▼
┌─────────────────────────────────────────────┐
│              APPLIERS dispatch               │
├─────────────┬─────────────┬────────────┬────┤
│ LinkedInAp. │ IndeedAp.   │ Greenhouse │Lever│
│ (existing)  │ (existing)  │ Applier    │Ap.  │
└─────────────┴─────────────┴────────────┴────┘
       │              │            │         │
       ▼              ▼            ▼         ▼
┌─────────────────────────────────────────────┐
│           BaseApplier ABC                    │
│  _human_type() · _random_pause()            │
│  _detect_captcha() · apply() abstract       │
└─────────────────────────────────────────────┘
       │
       ▼
┌──────────────┐
│  Playwright   │
│  Page object  │
└──────────────┘
```

### 2.2 Data Flow

1. Bot loop calls `detect_ats(job.raw.apply_url)` which checks URL domains against `ATS_FINGERPRINTS`.
2. If domain contains `greenhouse.io`, returns `"greenhouse"`; if `lever.co`, returns `"lever"`.
3. Bot loop looks up `APPLIERS[platform]` and instantiates the appropriate applier with the Playwright page.
4. `applier.apply(job, resume_pdf_path, cover_letter_text, profile)` is called.
5. The `apply()` method wraps `_do_apply()` in a try/except, returning `ApplyResult` on success or failure.
6. `_do_apply()` navigates to the URL, fills form fields, uploads resume, submits.
7. Result is returned to the bot loop for database recording.

### 2.3 Layer Architecture

| Layer | Component | Responsibility |
|-------|-----------|----------------|
| Orchestration | `bot/bot.py` | Routes jobs to correct applier via APPLIERS dict |
| Detection | `core/filter.py` | Identifies ATS platform from URL domain |
| Application | `bot/apply/greenhouse.py` | Greenhouse portal form automation |
| Application | `bot/apply/lever.py` | Lever portal form automation |
| Base | `bot/apply/base.py` | Shared ABC, human delays, CAPTCHA detection |
| Infrastructure | Playwright | Browser automation via persistent context |

---

## 3. Interface Contracts

### 3.1 bot.apply.greenhouse.GreenhouseApplier.apply()

**Purpose**: Submit a job application through a Greenhouse-powered portal.
**Category**: command (browser side effect)

**Signature**:
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| job | `ScoredJob` | yes | Scored job with `.raw.apply_url` pointing to Greenhouse portal |
| resume_pdf_path | `Path \| None` | yes | Path to tailored resume PDF, or None to skip upload |
| cover_letter_text | `str` | yes | Cover letter text to paste into textarea |
| profile | `UserProfile` | yes | User profile with personal information |

**Output**:
| Field | Type | Description |
|-------|------|-------------|
| return | `ApplyResult` | `success=True` on submission, `captcha_detected=True` if CAPTCHA found |

**Errors**:
| Condition | Handling |
|-----------|----------|
| Navigation timeout | Caught in `apply()` wrapper, returns `ApplyResult(success=False, error_message=...)` |
| Form field not found | Caught, returns failure |
| CAPTCHA detected | Returns `ApplyResult(success=False, captcha_detected=True)` |
| File chooser timeout | Caught, returns failure |
| Submit button not found | Caught, returns failure |

**Side Effects**: Navigates browser page, fills forms, uploads file, clicks submit.
**Thread Safety**: Not safe — tied to a single Playwright page instance.

**Greenhouse-Specific Behavior**:
- Splits `profile.full_name` into first and last name for Greenhouse's separate `first_name` / `last_name` fields.
- Fills: `first_name`, `last_name`, `email`, `phone`, `linkedin_url`.
- Resume uploaded via Playwright file chooser dialog.
- Cover letter pasted into textarea element.
- Detects form validation errors after submit attempt.

---

### 3.2 bot.apply.greenhouse.GreenhouseApplier._do_apply()

**Purpose**: Inner implementation of the Greenhouse apply flow.
**Category**: internal

**Signature**: Same as `apply()`.

**Steps**:
1. Navigate to `job.raw.apply_url`.
2. Wait for form to load (selector-based).
3. Call `_fill_form_fields(page, profile)`.
4. If `resume_pdf_path` is not None, trigger file chooser and upload.
5. If `cover_letter_text` is non-empty, fill cover letter textarea.
6. Check for CAPTCHA via `_detect_captcha()`.
7. Click submit button.
8. Wait briefly, check for form error indicators.
9. Return `ApplyResult`.

---

### 3.3 bot.apply.greenhouse.GreenhouseApplier._fill_form_fields()

**Purpose**: Populate Greenhouse application form fields from user profile.
**Category**: internal helper

**Signature**:
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| page | `Page` | yes | Playwright page instance |
| profile | `UserProfile` | yes | User profile |

**Field Mapping**:
| Greenhouse Field | Source | Notes |
|-----------------|--------|-------|
| First Name | `profile.full_name.split()[0]` | First token of full_name |
| Last Name | `profile.full_name.split()[-1]` | Last token of full_name |
| Email | `profile.email` | Direct |
| Phone | `profile.phone` | Direct |
| LinkedIn URL | `profile.linkedin_url` | Optional, skipped if None |

**Human-like Behavior**: Uses `_human_type()` for each field, `_random_pause()` between fields.

---

### 3.4 bot.apply.lever.LeverApplier.apply()

**Purpose**: Submit a job application through a Lever-powered portal.
**Category**: command (browser side effect)

**Signature**:
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| job | `ScoredJob` | yes | Scored job with `.raw.apply_url` pointing to Lever portal |
| resume_pdf_path | `Path \| None` | yes | Path to tailored resume PDF |
| cover_letter_text | `str` | yes | Cover letter text |
| profile | `UserProfile` | yes | User profile |

**Output**:
| Field | Type | Description |
|-------|------|-------------|
| return | `ApplyResult` | `success=True` on submission, `captcha_detected=True` if CAPTCHA found |

**Errors**: Same pattern as GreenhouseApplier — all exceptions caught in `apply()` wrapper.

**Lever-Specific Behavior**:
- Auto-appends `/apply` to URL if not already present (Lever job pages need `/apply` suffix to reach the form).
- Uses single `name` field (not split into first/last).
- Fills: `name` (full_name), `email`, `phone`, `linkedin_url`, `portfolio_url`.
- `portfolio_url` sourced from `profile.portfolio_url` (optional, skipped if None).
- Resume uploaded via file chooser.
- Cover letter pasted into textarea.
- Form verification check after submit.

---

### 3.5 bot.apply.lever.LeverApplier._do_apply()

**Purpose**: Inner implementation of the Lever apply flow.
**Category**: internal

**Steps**:
1. Compute URL: if `apply_url` does not end with `/apply`, append it.
2. Navigate to computed URL.
3. Wait for application form to load.
4. Fill `name`, `email`, `phone` fields using `_human_type()`.
5. Fill `linkedin_url` if `profile.linkedin_url` is not None.
6. Fill `portfolio_url` if `profile.portfolio_url` is not None.
7. Upload resume via file chooser if `resume_pdf_path` is not None.
8. Fill cover letter textarea if non-empty.
9. Check CAPTCHA via `_detect_captcha()`.
10. Click submit button.
11. Verify form submission (check for confirmation or error indicators).
12. Return `ApplyResult`.

---

### 3.6 bot.bot.APPLIERS (expanded)

**Purpose**: Maps ATS platform names to applier classes.
**Category**: configuration

**Before** (Phase 3):
```python
APPLIERS = {
    "linkedin": LinkedInApplier,
    "indeed": IndeedApplier,
}
```

**After** (Phase 5):
```python
APPLIERS = {
    "linkedin": LinkedInApplier,
    "indeed": IndeedApplier,
    "greenhouse": GreenhouseApplier,
    "lever": LeverApplier,
}
```

**Routing Logic** (unchanged): `detect_ats(scored.raw.apply_url)` returns the platform string, which is used to look up `APPLIERS.get(platform)`. If no match, `manual_required=True` is returned.

---

## 4. Data Model

No new database entities or schema changes. The existing `applications` table stores results from all applier types. The `platform` column in `applications` will now contain `"greenhouse"` or `"lever"` in addition to `"linkedin"` and `"indeed"`.

### 4.1 ATS Fingerprints Extension

```python
ATS_FINGERPRINTS = {
    # existing
    "linkedin.com": "linkedin",
    "indeed.com": "indeed",
    # new
    "greenhouse.io": "greenhouse",
    "lever.co": "lever",
}
```

---

## 5. Error Handling Strategy

| Scenario | Handling | User Impact |
|----------|----------|-------------|
| Greenhouse form field not found | `_do_apply()` raises, caught by `apply()` wrapper | Bot logs error, marks job as failed |
| Lever URL missing `/apply` | Auto-appended before navigation | Transparent to user |
| CAPTCHA detected on either portal | Returns `ApplyResult(captcha_detected=True)` | Bot logs warning, skips job |
| File chooser dialog timeout | Exception caught, returns failure | Bot skips job |
| Form validation error after submit | Detected by checking error selectors, returns failure | Bot logs details |
| Network timeout during navigation | Playwright timeout caught, returns failure | Bot retries or skips |
| Name splitting fails (single-word name) | First and last both set to the single word | Greenhouse gets same value for both fields |

---

## 6. Architecture Decision Records

### ADR-012: BaseApplier Pattern for ATS Appliers

**Status**: accepted
**Context**: Need to add Greenhouse and Lever support. Must decide whether to create a new abstraction or follow the existing pattern.
**Decision**: All appliers follow the same BaseApplier ABC pattern — public `apply()` method wraps private `_do_apply()` in try/except, returning `ApplyResult`. All use the same `_human_type()`, `_random_pause()`, and `_detect_captcha()` utilities from the base class.
**Rationale**: The existing pattern (established in Phase 3 for LinkedIn and Indeed) is proven and consistent. Uniform error handling means the bot loop does not need platform-specific error logic. Human-like delay behavior is shared, ensuring consistent anti-detection patterns.
**Consequences**: Adding future ATS platforms (Workday, iCIMS, etc.) follows the same recipe: extend BaseApplier, implement `apply()` + `_do_apply()`, add to APPLIERS dict, add domain to ATS_FINGERPRINTS.

---

## 7. Design Traceability Matrix

| Requirement | Type | Design Component | Interface | ADR |
|-------------|------|-----------------|-----------|-----|
| FR-057 | FR | bot/apply/greenhouse.py | GreenhouseApplier.apply() | ADR-012 |
| FR-057.1 | FR | bot/apply/greenhouse.py | _fill_form_fields() — first/last name split | ADR-012 |
| FR-057.2 | FR | bot/apply/greenhouse.py | _do_apply() — resume upload, cover letter | ADR-012 |
| FR-057.3 | FR | bot/apply/greenhouse.py | _detect_captcha() — CAPTCHA detection | ADR-012 |
| FR-058 | FR | bot/apply/lever.py | LeverApplier.apply() | ADR-012 |
| FR-058.1 | FR | bot/apply/lever.py | _do_apply() — auto-append /apply | ADR-012 |
| FR-058.2 | FR | bot/apply/lever.py | _do_apply() — portfolio_url field | ADR-012 |
| FR-058.3 | FR | bot/apply/lever.py | _detect_captcha() — CAPTCHA detection | ADR-012 |
| FR-059 | FR | bot/bot.py | APPLIERS dict expanded to 4 entries | — |
| FR-059.1 | FR | core/filter.py | ATS_FINGERPRINTS — greenhouse.io, lever.co | — |

---

## 8. Implementation Plan

| Order | Task ID | Description | Depends On | Size | FR Coverage |
|-------|---------|-------------|------------|------|-------------|
| 1 | IMPL-020 | Create `bot/apply/greenhouse.py` with `GreenhouseApplier`, `apply()`, `_do_apply()`, `_fill_form_fields()` | — | M | FR-057 |
| 2 | IMPL-021 | Create `bot/apply/lever.py` with `LeverApplier`, `apply()`, `_do_apply()`, URL normalization | — | M | FR-058 |
| 3 | IMPL-022 | Add `"greenhouse.io"` and `"lever.co"` to `ATS_FINGERPRINTS` in `core/filter.py` | — | S | FR-059.1 |
| 4 | IMPL-023 | Expand `APPLIERS` dict in `bot/bot.py` to include `GreenhouseApplier` and `LeverApplier` | IMPL-020, IMPL-021 | S | FR-059 |
| 5 | IMPL-024 | Unit tests for `GreenhouseApplier` — form filling, name splitting, CAPTCHA detection | IMPL-020 | M | FR-057 |
| 6 | IMPL-025 | Unit tests for `LeverApplier` — URL normalization, field filling, portfolio_url | IMPL-021 | M | FR-058 |
| 7 | IMPL-026 | Integration test — detect_ats routing through APPLIERS for greenhouse/lever URLs | IMPL-022, IMPL-023 | S | FR-059 |
