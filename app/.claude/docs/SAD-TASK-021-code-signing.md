# System Architecture Document

**Document ID**: SAD-TASK-021-code-signing
**Version**: 1.0
**Date**: 2026-03-11
**Status**: approved
**Author**: Claude (System Engineer)
**SRS Reference**: SRS-TASK-021

---

## 1. Executive Summary

Configure electron-builder for platform code signing and Apple notarization, integrated
into the existing GitHub Actions release workflow. All signing is opt-in via secrets —
builds degrade gracefully to unsigned when secrets are absent.

---

## 2. Architecture Overview

No new components. Changes are configuration-only:

1. `electron/package.json` — add `win.sign`, `mac.identity`, `mac.notarize` to build config
2. `.github/workflows/release.yml` — pass signing secrets as env vars
3. `docs/guides/code-signing.md` — setup documentation

---

## 3. Design Decisions

### ADR-025: Graceful Signing Degradation

**Status**: accepted
**Context**: Not all developers/forks will have signing certificates. CI must work for
everyone, with signing as an opt-in enhancement.

**Decision**: electron-builder natively supports this — when `CSC_LINK` is absent, it
skips signing. We rely on this built-in behavior rather than adding conditional logic.

**Consequences**:
- Positive: Zero additional code, no conditional workflow steps
- Negative: None — this is electron-builder's designed behavior

### ADR-026: Apple Notarization via electron-builder

**Status**: accepted
**Context**: macOS notarization can be done via `xcrun notarytool` manually or via
electron-builder's built-in `notarize` config.

**Decision**: Use electron-builder's `afterSign` hook with `@electron/notarize` package,
configured declaratively in package.json `mac.notarize`.

**Alternatives Considered**:
- Manual `xcrun notarytool` in CI — more fragile, platform-specific script
- Third-party action — unnecessary dependency

**Consequences**:
- Positive: Declarative config, maintained by electron-builder team
- Negative: Requires `@electron/notarize` as devDependency

---

## 4. Implementation Plan

| Order | Task | Description | Files |
|-------|------|-------------|-------|
| 1 | IMPL-001 | Add `@electron/notarize` devDependency | `electron/package.json` |
| 2 | IMPL-002 | Add Windows signing config to electron-builder | `electron/package.json` |
| 3 | IMPL-003 | Add macOS signing + notarization config | `electron/package.json` |
| 4 | IMPL-004 | Add signing secrets to CI workflow | `.github/workflows/release.yml` |
| 5 | IMPL-005 | Create signing setup guide | `docs/guides/code-signing.md` |

---

## 5. Required GitHub Secrets

| Secret | Platform | Description |
|--------|----------|-------------|
| `WIN_CSC_LINK` | Windows | Base64-encoded .pfx certificate or HTTPS URL |
| `WIN_CSC_KEY_PASSWORD` | Windows | Password for the .pfx certificate |
| `CSC_LINK` | macOS | Base64-encoded .p12 Developer ID certificate |
| `CSC_KEY_PASSWORD` | macOS | Password for the .p12 certificate |
| `APPLE_ID` | macOS | Apple Developer account email |
| `APPLE_ID_PASSWORD` | macOS | App-specific password (not account password) |
| `APPLE_TEAM_ID` | macOS | Apple Developer Team ID (10-char alphanumeric) |

---

## 6. Design Traceability

| Requirement | Design Component | Files |
|-------------|-----------------|-------|
| FR-126 | electron-builder win config | `electron/package.json` |
| FR-127 | electron-builder mac config | `electron/package.json` |
| FR-128 | electron-builder mac.notarize | `electron/package.json` |
| FR-129 | CI env vars | `.github/workflows/release.yml` |
| FR-130 | Setup guide | `docs/guides/code-signing.md` |
