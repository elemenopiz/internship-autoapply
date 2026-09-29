# Project Management Templates

## Estimation Table
| Size | Effort | Includes | Risk Buffer |
|------|--------|----------|-------------|
| S | < 2 hours | All phases condensed | 10% |
| M | 2-8 hours | Full process | 20% |
| L | 1-3 days | Separate artifacts | 30% |
| XL | 3+ days | Must decompose first | 40% |

## WBS Template
| # | Phase | Role | Deliverable | Size | Depends On | Status |
|---|-------|------|-------------|------|------------|--------|

## Progress Tracker
```markdown
## {Task Title}
**Overall**: 🟢/🟡/🔴
| Phase | Role | Status | Notes |
```

## Risk Register
| Risk | Probability | Impact | Score | Mitigation | Owner | Status |
|------|:-----------:|:------:|:-----:|------------|-------|--------|
| {desc} | L/M/H | L/M/H | {P×I} | {action} | {role} | open/mitigated |

## Scope Change Request
```markdown
### Change Request: {title}
**Requested by**: {who} | **Date**: {when}
**Description**: {what changed}
**Impact**: Time: {+N hours} | Risk: {L/M/H} | Affected roles: {list}
**Options**: 1. Add to current | 2. Backlog | 3. Replace {lower priority item}
**Decision**: {chosen option} | **Approved by**: {who}
```

## Definition of Done (Universal)
- [ ] All activated role checklists passed
- [ ] All tests passing with coverage targets
- [ ] Security audit passed
- [ ] Documentation complete
- [ ] Traceability matrix complete (zero gaps)
- [ ] Changelog updated
- [ ] Release package assembled
