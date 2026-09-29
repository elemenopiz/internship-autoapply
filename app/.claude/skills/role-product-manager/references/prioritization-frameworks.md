# Product Management Frameworks

## RICE Prioritization
Score = (Reach × Impact × Confidence) / Effort

| Factor | Scale | Description |
|--------|-------|-------------|
| Reach | # of users/quarter | How many people affected |
| Impact | 0.25 (minimal) to 3 (massive) | How much each person benefits |
| Confidence | 0-100% | How sure are we |
| Effort | Person-weeks | Total team effort |

## MoSCoW Prioritization
| Category | Meaning | Budget |
|----------|---------|--------|
| **Must** | Ship-blocking, non-negotiable | 60% of effort |
| **Should** | Important, painful to omit | 20% of effort |
| **Could** | Nice to have, easy wins | 15% of effort |
| **Won't** | Agreed to exclude this release | 0% (documented) |

## User Story Format
```
As a {persona},
I want to {action/capability},
So that {benefit/value}.

Acceptance Criteria:
- Given {context}, When {action}, Then {outcome}
```

## Product-Market Fit Signals
| Signal | Metric | Good |
|--------|--------|------|
| Retention | Day 7 / Day 30 | > 40% / > 20% |
| NPS | Net Promoter Score | > 40 |
| Organic growth | % users from referral | > 30% |
| Sean Ellis test | "Very disappointed" if gone | > 40% |

## PRD Quality Checklist
- [ ] Problem statement validated with data or user research
- [ ] Target persona identified
- [ ] Success metrics defined with current baseline and target
- [ ] Scope bounded (in/out/future explicitly stated)
- [ ] User stories with acceptance criteria
- [ ] Priorities assigned (MoSCoW or RICE)
- [ ] Risks identified with mitigations
- [ ] Timeline constraints noted
