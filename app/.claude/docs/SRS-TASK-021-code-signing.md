# Software Requirements Specification

**Document ID**: SRS-TASK-021-code-signing
**Version**: 1.0
**Date**: 2026-03-11
**Status**: approved
**Author**: Claude (Requirements Analyst)
**PRD Reference**: PRD-TASK-021

---

## 1. Purpose and Scope

### 1.1 Purpose
Specify requirements for code signing Windows installers and notarizing macOS installers
built by the AutoApply release pipeline.

### 1.2 Scope
Configuration-only changes to electron-builder and GitHub Actions. No runtime code changes.

---

## 2. Functional Requirements

### FR-126: Windows Code Signing Configuration

**Description**: The electron-builder configuration SHALL include Windows code signing
settings that use a PKCS#12 certificate when available.

**Priority**: P0
**Source**: US-098

**Acceptance Criteria**:
- **AC-126-1**: Given `WIN_CSC_LINK` and `WIN_CSC_KEY_PASSWORD` env vars are set,
  When electron-builder runs `--win`, Then the .exe installer is signed with the certificate.
- **AC-126-2**: Given signing env vars are NOT set,
  When electron-builder runs `--win`, Then the build completes successfully with an unsigned installer.

### FR-127: macOS Code Signing Configuration

**Description**: The electron-builder configuration SHALL include macOS code signing
settings that use an Apple Developer ID certificate when available.

**Priority**: P0
**Source**: US-099

**Acceptance Criteria**:
- **AC-127-1**: Given `CSC_LINK` and `CSC_KEY_PASSWORD` env vars are set,
  When electron-builder runs `--mac`, Then the .dmg is signed with the Developer ID certificate.
- **AC-127-2**: Given signing env vars are NOT set,
  When electron-builder runs `--mac`, Then the build completes successfully unsigned.

### FR-128: macOS Notarization

**Description**: The electron-builder configuration SHALL include Apple notarization
settings that submit the signed app to Apple for notarization.

**Priority**: P0
**Source**: US-099

**Acceptance Criteria**:
- **AC-128-1**: Given `APPLE_ID`, `APPLE_ID_PASSWORD`, and `APPLE_TEAM_ID` env vars are set,
  When electron-builder runs `--mac` with signing, Then the .dmg is notarized via Apple's notary service.
- **AC-128-2**: Given notarization env vars are NOT set,
  When electron-builder runs `--mac`, Then the build skips notarization gracefully.

### FR-129: CI Workflow Signing Integration

**Description**: The GitHub Actions release workflow SHALL pass signing-related secrets
to electron-builder as environment variables.

**Priority**: P0
**Source**: US-100

**Acceptance Criteria**:
- **AC-129-1**: Given the release workflow file, When a `v*` tag is pushed,
  Then signing secrets are passed as env vars to the "Build installer" step.
- **AC-129-2**: Given secrets are not configured in the repository,
  When the release workflow runs, Then all builds complete successfully (unsigned).

### FR-130: Signing Documentation

**Description**: A setup guide SHALL document the required secrets and certificate
procurement steps for both platforms.

**Priority**: P1
**Source**: US-100

**Acceptance Criteria**:
- **AC-130-1**: Given the docs, When a developer reads them,
  Then they can configure all required GitHub Secrets for Windows and macOS signing.

---

## 3. Non-Functional Requirements

### NFR-021-01: No Breaking Changes
Existing unsigned builds MUST continue to work when secrets are not configured.
Signing is opt-in via GitHub Secrets, never mandatory.

### NFR-021-02: Secret Security
Certificate files and passwords MUST only exist in GitHub Actions secrets.
They MUST NOT appear in code, logs, or artifacts.

### NFR-021-03: Traceability
All requirements must be tracked in the traceability matrix.

---

## 4. Out of Scope
- Certificate procurement (user responsibility)
- Linux signing (not applicable for AppImage)
- EV certificates (standard OV sufficient)
- Auto-update integration (separate issue #16)
