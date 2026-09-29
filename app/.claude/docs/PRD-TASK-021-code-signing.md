# Product Requirements Document

**Feature**: Code Signing & Notarization (D-4)
**Date**: 2026-03-11
**Author**: Claude (Product Manager)
**Status**: approved
**GitHub Issue**: #18

---

## 1. Problem Statement

### What problem are we solving?
Windows and macOS display security warnings when users install unsigned applications.
On Windows, SmartScreen shows "Unknown publisher" and may block execution. On macOS,
Gatekeeper prevents opening unsigned apps entirely, requiring manual xattr workaround.

### Who has this problem?
All end users downloading AutoApply installers from GitHub Releases.

### How big is this problem?
100% of users are affected. Security warnings erode trust and cause abandonment.

### How is it solved today?
Users must manually dismiss SmartScreen warnings (Windows) or run
`xattr -cr /Applications/AutoApply.app` (macOS). Linux AppImage is unaffected.

---

## 2. User Stories

| ID | As a... | I want to... | So that... | Priority | Size |
|--------|---------|---------------------------|------------------------|----------|------|
| US-098 | Windows user | install AutoApply without SmartScreen warnings | I trust the installer is safe | P0 | M |
| US-099 | macOS user | open AutoApply without Gatekeeper blocks | I can run the app normally after install | P0 | M |
| US-100 | Developer | have signing automated in CI | releases are always signed without manual steps | P0 | M |

### Acceptance Criteria

#### US-098: Windows Code Signing
- Given a Windows .exe installer built in CI, When signing secrets are configured, Then the installer is signed with the certificate
- Given a signed .exe, When a user downloads and runs it, Then no "Unknown publisher" warning appears

#### US-099: macOS Notarization
- Given a macOS .dmg built in CI, When Apple credentials are configured, Then the .dmg is signed and notarized
- Given a notarized .dmg, When a user opens it, Then Gatekeeper allows it without workaround

#### US-100: CI Automation
- Given the release workflow, When signing secrets are NOT configured, Then the build completes successfully without signing (graceful skip)
- Given the release workflow, When signing secrets ARE configured, Then signing runs automatically

---

## 3. Scope

### In Scope
- electron-builder signing configuration for Windows (certificate-based)
- electron-builder signing + notarization configuration for macOS (Apple Developer ID)
- CI workflow updates to pass signing secrets to electron-builder
- Graceful degradation when secrets are not available (unsigned builds still work)
- Documentation for setting up signing secrets

### Out of Scope
- Purchasing certificates (user responsibility)
- Linux signing (AppImage does not require signing)
- EV certificate setup (standard OV is sufficient for initial release)
- Auto-update (separate issue #16)

---

## 4. Constraints
- No runtime code changes — this is build/CI configuration only
- Must not break existing unsigned builds when secrets are absent
- GitHub Actions secrets are the only acceptable secret storage mechanism
