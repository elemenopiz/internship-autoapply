# Architecture Decision Record (ADR) Templates

## Lightweight ADR (small, easily reversible decisions)
```markdown
# ADR-{NNN}: {Title}
**Date**: {YYYY-MM-DD} | **Status**: accepted
**Decision**: {one sentence — what we chose}
**Reason**: {one sentence — why}
```

## Standard ADR (medium-impact decisions)
```markdown
# ADR-{NNN}: {Title}
**Date**: {YYYY-MM-DD} | **Status**: proposed | accepted | deprecated | superseded by ADR-{N}

## Context
{Problem or situation requiring a decision. Forces at play.}

## Decision
{What was decided. Specific and concrete.}

## Alternatives Considered
| Option | Pros | Cons |
|--------|------|------|
| {A} | {advantages} | {disadvantages} |
| {B} | {advantages} | {disadvantages} |
| **{Chosen}** | **{advantages}** | **{accepted tradeoffs}** |

## Consequences
- **Positive**: {benefits}
- **Negative**: {tradeoffs accepted}
- **Risks**: {what could go wrong + mitigation}

## Rationale
{The reasoning chain leading to this choice}
```

## Heavyweight ADR (large, hard-to-reverse, high-cost decisions)
```markdown
# ADR-{NNN}: {Title}
**Date**: {YYYY-MM-DD} | **Status**: {status}
**Deciders**: {who was involved}

## Context and Problem Statement
{Detailed situation description with business and technical context.}

## Decision Drivers
- {driver 1 — e.g., performance requirement}
- {driver 2 — e.g., team expertise}
- {driver 3 — e.g., budget constraint}

## Considered Options
### Option 1: {Name}
- Description: {full detail}
- Pros / Cons / Estimated effort / Risk level

### Option 2: {Name}
...

## Decision Outcome
**Chosen**: "{Option N}" because {justification linked to drivers}.

### Consequences
{Positive, Negative, Neutral}

## Validation
How we'll know this was right:
- {metric or signal 1}
- {metric or signal 2}

## Follow-up Actions
- [ ] {action 1}
- [ ] {action 2}

## Links
{Related ADRs, requirements, external references}
```

## When to Use Each Level
| Decision Impact | Reversibility | ADR Level |
|----------------|---------------|-----------|
| Low | Easy | Lightweight |
| Medium | Moderate | Standard |
| High | Difficult | Heavyweight |
| Any | Irreversible | Heavyweight |
