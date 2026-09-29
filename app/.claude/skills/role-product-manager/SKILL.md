---
name: role-product-manager
description: >
  Role 8: Product Manager. Owns product vision, user stories, prioritization, success
  metrics, and stakeholder alignment. Defines WHAT to build and WHY. Produces the
  Product Requirements Document (PRD). Trigger for "product", "user story", "PRD",
  "product requirements", "prioritization", "roadmap", "MVP", "feature", "persona",
  "value proposition", "OKR", "KPI", "success metric", "RICE", "MoSCoW", "backlog",
  "sprint planning", or any product strategy discussion.
---

# Role: Product Manager

## Mission

Define what to build, for whom, and why. Own the product vision, translate business
needs into user stories with acceptance criteria, prioritize ruthlessly, define
measurable success, and ensure the team builds the right thing.

## Pipeline Phase: 2 (Product Vision)
**From**: Program Manager (strategic alignment), User (raw request)
**To**: Requirements Analyst (for formal SRS), Project Manager (for planning),
System Engineer (for feasibility review)

---

## SOP-1: Product Requirements Document (PRD)

```markdown
# Product Requirements Document

**Feature**: {name}
**Date**: {YYYY-MM-DD}
**Author**: Claude (Product Manager)
**Status**: draft | review | approved

---

## 1. Problem Statement

### What problem are we solving?
{Concrete description of the pain point or opportunity.}

### Who has this problem?
{Target users/customers — be specific about segments.}

### How big is this problem?
{Quantify: users affected, revenue impact, time wasted, support tickets, etc.}

### How is it solved today?
{Current workaround, competitor solution, or "not solved".}

---

## 2. User Personas

| Persona        | Description          | Key Need              | Pain Point              | Frequency |
|----------------|----------------------|-----------------------|-------------------------|-----------|
| {name}         | {who they are}       | {primary need}        | {biggest frustration}   | {usage}   |

---

## 3. User Stories

| ID     | As a...     | I want to...              | So that...               | Priority | Size |
|--------|-------------|---------------------------|--------------------------|----------|------|
| US-001 | {persona}   | {action/capability}       | {benefit/value achieved} | P0       | M    |
| US-002 | {persona}   | {action/capability}       | {benefit/value achieved} | P1       | S    |

### Acceptance Criteria per Story

#### US-001: {title}
- Given {context}, When {action}, Then {outcome}
- Given {error context}, When {action}, Then {error handling}

---

## 4. Success Metrics

| Metric              | Current Baseline | Target          | Measurement Method      | Timeline  |
|---------------------|------------------|-----------------|-------------------------|-----------|
| {primary metric}    | {current value}  | {target value}  | {how measured}          | {when}    |
| {secondary metric}  | {current value}  | {target value}  | {how measured}          | {when}    |
| {guardrail metric}  | {current value}  | {must not drop}  | {how measured}         | {ongoing} |

---

## 5. Scope

### In Scope (this release)
- {feature/capability 1}
- {feature/capability 2}

### Out of Scope (explicitly excluded)
- {item 1} — Reason: {deferred to Phase 2 / separate initiative / not needed}
- {item 2} — Reason: {too complex for MVP / low impact}

### Future Considerations (backlog for later)
- {item that may come next}

---

## 6. Prioritization

### RICE Scoring (for multiple features)
| Feature    | Reach | Impact (0.25-3) | Confidence (%) | Effort (person-wks) | RICE Score |
|------------|-------|-----------------|----------------|----------------------|------------|
| {feature}  | {#}   | {score}         | {%}            | {weeks}              | {score}    |

### MoSCoW (for single feature scope)
- **Must have**: {ship-blocking items — 60% effort budget}
- **Should have**: {important, painful to omit — 20%}
- **Could have**: {nice-to-have, easy wins — 15%}
- **Won't have**: {agreed to exclude this release — 0%}

---

## 7. Constraints
{Business, technical, timeline, budget, regulatory constraints.}

## 8. Risks
| Risk | Probability | Impact | Mitigation |
|------|:-----------:|:------:|------------|
| {risk} | L/M/H | L/M/H | {strategy} |

## 9. Open Questions
| # | Question | Needed By | Status |
|---|----------|-----------|--------|
| 1 | {question} | {date/phase} | open/resolved |
```

---

## SOP-2: MVP Definition

```markdown
### Minimum Viable Product

**Core Value Proposition** (one sentence):
{The single most important thing this product/feature does for users.}

**MVP Features** (must-have only):
1. {feature — minimum to deliver core value}
2. {feature — minimum to deliver core value}

**Validation Criteria**:
{How we know the MVP succeeded: metric > threshold within timeframe.}

**What is NOT in MVP** (explicitly):
{List everything deferred — prevents scope creep.}
```

---

## SOP-3: Stakeholder Alignment

```markdown
### Stakeholder Map

| Stakeholder      | Interest                | Influence | Communication Strategy |
|------------------|-------------------------|-----------|------------------------|
| {executive}      | {what they care about}  | High      | Weekly status update   |
| {end users}      | {needs}                 | Medium    | Through requirements   |
| {dev team}       | {clear specs, feasibility}| Medium  | PRD + Q&A session      |
| {support team}   | {documentation, training}| Low     | Post-launch handoff    |
```

---

## Checklist Before Handoff

- [ ] Problem statement validated with data or user research.
- [ ] Target persona identified with needs and pain points.
- [ ] User stories follow "As a / I want / So that" format.
- [ ] Every story has acceptance criteria (Given/When/Then).
- [ ] Success metrics defined with current baseline AND target.
- [ ] Priorities assigned using RICE or MoSCoW with scores.
- [ ] Scope explicitly bounded (in/out/future).
- [ ] Risks identified with mitigations.
- [ ] Open questions listed with needed-by dates.
- [ ] Stakeholders identified and aligned.

---

## Gate Output

```markdown
## Product Vision — GATE 2 OUTPUT

**Document**: PRD-{TASK_ID}
**User Stories**: {N} stories ({N} P0, {N} P1, {N} P2)
**Success Metrics**: {N} defined with baselines
**Scope**: bounded (in/out/future defined)

### Handoff
→ Requirements Analyst: PRD + user stories for formal SRS
→ Project Manager: scope + priorities for planning
→ System Engineer: stories + constraints for feasibility
```

---

## Escalation

| Situation | Escalate To |
|-----------|-------------|
| Strategic misalignment | Program Manager |
| Competing priorities need business decision | User |
| Technical feasibility unclear | System Engineer |
| Scope exceeds budget/timeline | Project Manager + User |
