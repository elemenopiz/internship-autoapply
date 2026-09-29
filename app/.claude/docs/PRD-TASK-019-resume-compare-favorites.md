# Product Requirements Document

**Feature**: Resume Comparison & Favorites
**Task**: TASK-019
**Date**: 2026-03-11
**Author**: Claude (Product Manager)
**Status**: approved
**Target Version**: v2.1.0 (extends TASK-018)

---

## 1. Problem Statement

### What problem are we solving?
Users with many AI-generated resume versions cannot quickly identify their best-performing
variants or understand what changed between versions for similar roles. The Resume Library
(TASK-018) lists versions but provides no way to compare them side by side or mark favorites.

### Who has this problem?
Strategy Optimizers who apply to 10+ positions weekly and want to refine their resume approach.

### How is it solved today?
Users must open two detail views separately and manually compare. No favoriting mechanism exists.

---

## 2. User Stories

| ID     | As a...            | I want to...                              | So that...                                    | Priority | Size |
|--------|--------------------|--------------------------------------------|-----------------------------------------------|----------|------|
| US-095 | Strategy Optimizer | compare two resume versions side by side   | I can see what changed between variants        | P0       | M    |
| US-097 | Active Applicant   | tag or star favorite resume versions       | I can quickly find my best variants            | P0       | S    |

### Acceptance Criteria

#### US-095: Compare two resumes
- Given I select two resume versions via checkboxes, When I click "Compare", Then a side-by-side view shows both markdown contents with diff highlighting (added/removed lines)
- Given I have fewer than 2 versions selected, When I look at the Compare button, Then it is disabled
- Given I select more than 2 versions, When I click the 3rd checkbox, Then the oldest selection is deselected

#### US-097: Star/favorite resumes
- Given a resume version in the library list, When I click the star icon, Then it toggles starred/unstarred and the state persists
- Given starred versions exist, When I sort by "Favorites first", Then starred items appear at the top
- Given I view the detail of a starred resume, When the detail view opens, Then the star icon shows as active

---

## 3. Scope

### In Scope
- Star/favorite toggle on resume versions (new `is_favorite` DB column)
- API endpoint to toggle favorite status
- "Favorites first" sort option in library
- Side-by-side markdown comparison view for 2 selected resumes
- Line-level diff highlighting (added=green, removed=red, unchanged=neutral)
- i18n for all new strings
- Accessibility (WCAG 2.1 AA) for new UI elements

### Out of Scope
- Semantic diff (understanding section-level changes) — plain line diff only
- Comparing more than 2 versions at once
- Export comparison as PDF

---

## 4. Success Metrics

| Metric              | Target           | Measurement                  |
|---------------------|------------------|------------------------------|
| Favorites used      | 5+ stars/user    | Count starred versions       |
| Comparisons viewed  | 2+ per week      | Compare button click count   |

---

## Product Vision — GATE 2 OUTPUT

**Document**: PRD-TASK-019
**User Stories**: 2 stories (2 P0)
**Scope**: bounded
**Handoff**: Requirements Analyst for SRS
