# Documentation Templates & Writing Standards

## README Quick Template
```markdown
# {Name}
{One-line description}

## Quick Start
1. `{install}` 2. `{configure}` 3. `{run}`

## Usage
{Primary use case with code example}

## API Reference
{Table: endpoint, method, description}

## Configuration
{Table: option, type, default, description}

## Testing
`{test command}`
```

## API Doc Template (per function/endpoint)
```markdown
### {name}
**Purpose**: {one sentence}
| Param | Type | Required | Default | Description |
| Return | Type | Description |
| Error | When |
**Example**: {concrete usage → output}
```

## Changelog Format
```markdown
## [version] - YYYY-MM-DD
### Added
### Changed
### Fixed
### Security
### Deprecated
### Removed
```

## ADR Template (Standard)
```markdown
# ADR-{N}: {Title}
**Status**: accepted | **Date**: YYYY-MM-DD
## Context: {why}
## Decision: {what}
## Consequences: {positive, negative, risks}
```

## Writing Rules
- Active voice: "The function returns" (not "is returned")
- Present tense: "handles" (not "will handle")
- Second person for guides: "You can configure..."
- Imperative for steps: "Run the command" (not "You should run")
- Specific: "Returns null if not found" (not "in some cases")
- One idea per sentence. Short sentences.
- Examples > explanations. Show, don't tell.
