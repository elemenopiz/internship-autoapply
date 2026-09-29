# Product Requirements Document

**Feature**: Resume Versioning
**Task**: TASK-018
**Date**: 2026-03-11
**Author**: Claude (Product Manager)
**Status**: approved
**Target Version**: v2.1.0

---

## 1. Problem Statement

### What problem are we solving?
Users cannot track, compare, or reuse the tailored resumes generated for each application.
Every application generates a new resume, but there is no way to:
- View which resume variant was sent to which company
- Compare resume versions to see what changed
- Reuse a high-performing resume for similar roles
- Understand which resume styles correlate with interview callbacks

### Who has this problem?
Active job seekers using AutoApply who apply to 10+ positions and want to optimize
their resume strategy based on outcomes.

### How big is this problem?
Every user generates multiple resume variants. Without versioning, users are blind to
which resume approach works best. The data already exists (resume files on disk, application
outcomes in DB) but is not surfaced or connected.

### How is it solved today?
Users can manually navigate to `~/.autoapply/profile/resumes/` and open PDF files.
There is no in-app browsing, no connection to application outcomes, and no comparison.

---

## 2. User Personas

| Persona           | Description                    | Key Need                      | Pain Point                          | Frequency |
|--------------------|--------------------------------|-------------------------------|-------------------------------------|-----------|
| Active Applicant   | Applies daily via AutoApply    | See which resume was sent     | Cannot find resume for a given app  | Daily     |
| Strategy Optimizer | Reviews outcomes weekly        | Compare resume effectiveness  | No correlation between resume & result | Weekly |
| Manual Applier     | Uses review mode, applies manually | Reuse a good resume variant | Must regenerate from scratch        | Weekly    |

---

## 3. User Stories

| ID     | As a...            | I want to...                                    | So that...                                    | Priority | Size |
|--------|--------------------|-------------------------------------------------|-----------------------------------------------|----------|------|
| US-090 | Active Applicant   | view the resume used for any application        | I can see exactly what was sent to each company | P0       | M    |
| US-091 | Active Applicant   | browse all my generated resumes in one place     | I can find and review any version quickly      | P0       | M    |
| US-092 | Strategy Optimizer | see resume effectiveness metrics                | I can identify which resume styles get callbacks | P0      | M    |
| US-093 | Active Applicant   | preview a resume PDF in-app                     | I don't need to open external PDF viewers      | P1       | M    |
| US-094 | Manual Applier     | download any resume version                     | I can use it for manual applications           | P1       | S    |
| US-095 | Strategy Optimizer | compare two resume versions side by side        | I can see what changed between variants        | P2       | L    |
| US-096 | Active Applicant   | see which resume sections were customized per job | I understand the AI's tailoring decisions     | P2       | M    |
| US-097 | Active Applicant   | tag or star favorite resume versions             | I can quickly find my best variants            | P2       | S    |

### Acceptance Criteria per Story

#### US-090: View resume for application
- Given an application with a generated resume, When I open the application detail view, Then I see a "View Resume" button that shows the resume content
- Given an application without a resume (fallback used), When I open the detail view, Then the "View Resume" button is absent or shows "No tailored resume"

#### US-091: Browse all resumes
- Given I have generated resumes for multiple applications, When I navigate to a resume library screen, Then I see a list of all resume versions with company, job title, date, and application status
- Given I have no generated resumes, When I navigate to the resume library, Then I see an empty state message

#### US-092: Resume effectiveness metrics
- Given multiple applications with resumes, When I view the resume library, Then I see aggregate metrics: total versions, interview rate for tailored vs fallback, average match score by outcome

#### US-093: Preview resume PDF
- Given a resume version in the list, When I click to preview, Then the PDF renders inline in the app without opening an external viewer

#### US-094: Download resume
- Given a resume version, When I click download, Then the PDF file downloads to my chosen location

#### US-095: Compare two resumes
- Given two resume versions selected, When I click compare, Then I see a side-by-side diff highlighting added/removed/changed sections

#### US-096: View customization details
- Given a resume version, When I view its details, Then I see metadata: target job title, company, match score, generation date, LLM provider used

#### US-097: Tag/star favorites
- Given a resume version, When I click the star icon, Then it is marked as favorite and appears at the top of the library list

---

## 4. Success Metrics

| Metric                        | Current Baseline | Target           | Measurement Method              | Timeline    |
|-------------------------------|------------------|------------------|---------------------------------|-------------|
| Resume versions viewable      | 0%               | 100% of generated| Count resumes accessible in UI  | At launch   |
| Resume library page views     | N/A              | 3+ views/user/wk | Analytics event tracking        | 4 weeks     |
| Resume downloads              | 0 (no UI)        | 2+ downloads/wk  | Download button click count     | 4 weeks     |

---

## 5. Scope

### In Scope (this release — v2.1.0)
- Resume library screen listing all generated resume versions
- Resume detail view with metadata (company, job, score, date, status)
- PDF preview in-app (iframe or embed)
- Download button for any resume PDF
- Effectiveness metrics (interview rate for tailored vs fallback)
- Link from application detail view to resume version
- i18n for all new UI strings
- Accessibility (WCAG 2.1 AA) for all new components

### Out of Scope (explicitly excluded)
- **Side-by-side diff/comparison (US-095)**: Requires markdown parsing and diff algorithm. Deferred to v2.2.0.
- **Resume editing in-app**: Users should regenerate via the bot, not edit PDFs in-app.
- **Tagging/starring (US-097)**: Nice-to-have, deferred to v2.2.0. Requires new DB column.
- **Resume templates/themes**: Not in scope. Current ReportLab rendering is fixed format.
- **Cover letter versioning**: Could follow same pattern but scoped separately.

### Future Considerations (backlog)
- Resume A/B testing: generate 2 variants per job, track which gets callbacks
- Resume quality scoring via LLM analysis
- Export all resumes as ZIP

---

## 6. Prioritization

### MoSCoW

- **Must have** (P0 — 60% effort):
  - Resume library screen with list view (US-091)
  - Resume detail with metadata (US-090, US-096)
  - Effectiveness metrics (US-092)
- **Should have** (P1 — 25% effort):
  - PDF preview in-app (US-093)
  - Download button (US-094)
  - Link from application detail to resume
- **Could have** (P2 — 15% effort):
  - Sorting/filtering in resume library
  - Search by company/job title
- **Won't have** (this release):
  - Side-by-side comparison (US-095)
  - Tagging/starring (US-097)
  - Resume editing

---

## 7. Constraints

| Type       | Constraint                                      | Rationale                           |
|------------|--------------------------------------------------|-------------------------------------|
| Technical  | No new Python dependencies                       | Minimize bundle size                |
| Technical  | No database schema changes to `applications` table | Backward compatibility           |
| Technical  | Vanilla JS ES modules, no build step             | Existing architecture (ADR-017)     |
| Technical  | Resume files stored at existing paths            | `~/.autoapply/profile/resumes/`     |
| Platform   | Electron desktop app only                        | No browser mode                     |
| Data       | Resume data already on disk + path in DB         | No migration needed for existing data |

---

## 8. Risks

| Risk                                    | Probability | Impact | Mitigation                                  |
|-----------------------------------------|:-----------:|:------:|---------------------------------------------|
| Resume files deleted from disk          | M           | M      | Show "file missing" state gracefully        |
| Large number of resumes slows listing   | L           | L      | Paginate at 50 items, lazy load             |
| PDF preview fails in Electron webview   | M           | M      | Fallback to download-only with message      |
| Existing resume_path data inconsistent  | L           | M      | Validate file existence before displaying   |

---

## 9. Open Questions

| # | Question                                      | Needed By  | Status   | Resolution |
|---|-----------------------------------------------|------------|----------|------------|
| 1 | Should we index resume markdown content?      | Design     | Resolved | No — read from disk on demand. No full-text search needed for v2.1.0. |
| 2 | New screen or tab within existing dashboard?  | Design     | Resolved | New screen accessible from sidebar/nav. Consistent with application detail pattern. |
| 3 | Store LLM provider used per resume?           | Design     | Resolved | Yes — add to metadata display. Data available from config at generation time but not currently saved. Will need a new `resume_versions` table. |

---

## Product Vision — GATE 2 OUTPUT

**Document**: PRD-TASK-018-resume-versioning
**User Stories**: 8 stories (3 P0, 2 P1, 3 P2)
**Success Metrics**: 3 defined with baselines
**Scope**: bounded (in/out/future defined)

### Handoff
- Requirements Analyst: PRD + user stories for formal SRS
- Project Manager: scope + priorities for planning
- System Engineer: stories + constraints for feasibility
