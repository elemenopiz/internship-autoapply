# Software Requirements Specification — Phase 8 Distribution

**Document ID**: SRS-TASK-016-distribution
**Version**: 1.0
**Date**: 2026-03-11
**Status**: approved
**Author**: Claude (Requirements Analyst)
**PRD Reference**: PRD Phase 8.1 (DIST-1 through DIST-6)

---

## 1. Purpose and Scope

### 1.1 Purpose
Specify the requirements for producing distributable installers of AutoApply that bundle the Python backend, all dependencies, and Playwright Chromium so end users need only install the app — no Python, Node.js, or manual setup required.

### 1.2 Scope
**In scope**: App icon generation, version synchronization between Electron and Python, Python runtime bundling, platform-specific installer builds (Windows NSIS, macOS DMG, Linux AppImage), CI-automated release pipeline.

**Out of scope**: Code signing (requires Apple/Microsoft developer accounts — documented for future), auto-update mechanism (deferred to D-1), crash reporting (deferred to D-2), cross-compilation (each platform builds natively).

### 1.3 Definitions and Acronyms
| Term | Definition |
|------|-----------|
| NSIS | Nullsoft Scriptable Install System — Windows installer framework used by electron-builder |
| DMG | Disk Image — macOS application distribution format |
| AppImage | Portable Linux application format that runs without installation |
| electron-builder | npm package that packages Electron apps into platform-specific installers |
| Embeddable Python | Official minimal Python distribution for Windows (~15MB) that can be bundled without an installer |
| python-build-standalone | Community project providing relocatable Python builds for macOS/Linux |
| extraResources | electron-builder config that copies files into the app's resources directory |

---

## 2. Overall Description

### 2.1 Product Perspective
AutoApply currently runs in dev mode: users clone the repo, create a venv, install deps, and launch via `cd electron && npm start`. This task produces one-click installers so non-technical users can install and run AutoApply like any desktop app.

### 2.2 User Classes
| User Class | Description | Technical Expertise |
|-----------|-------------|---------------------|
| End User | Job seekers who want to automate applications | Novice — expects double-click install |
| Developer | Contributors building from source | Expert — uses dev mode, not installers |

### 2.3 Operating Environment
| Platform | OS Version | Architecture |
|----------|-----------|-------------|
| Windows | 10+ | x64 |
| macOS | 12 (Monterey)+ | x64 and arm64 (universal) |
| Linux | Ubuntu 22.04+ / equivalent | x64 |

### 2.4 Assumptions
| # | Assumption | Risk if Wrong | Mitigation |
|---|-----------|---------------|------------|
| A1 | Windows embeddable Python 3.11+ supports all runtime dependencies (flask, playwright, reportlab, pydantic, gevent) | Build fails or runtime crash | Test pip install into embeddable Python during development |
| A2 | python-build-standalone relocatable builds work for macOS/Linux without symlink issues | Imports fail at runtime | Set PYTHONPATH explicitly; test on clean VM |
| A3 | Playwright Chromium can be pre-downloaded and relocated via PLAYWRIGHT_BROWSERS_PATH env var | Browser automation fails on installed app | Verify Playwright respects the env var in bundled context |
| A4 | Installer size of 120-200MB (compressed) is acceptable for a desktop app bundling a browser engine | Users abandon download | Document size in release notes; consider light build later |
| A5 | Unsigned macOS DMGs are acceptable for initial release with documented xattr workaround | Users cannot open the app | Document Gatekeeper bypass in README and in-app |

### 2.5 Constraints
| Type | Constraint | Rationale |
|------|-----------|-----------|
| Technical | Must use electron-builder (already configured in package.json) | Existing tooling, no reason to switch |
| Technical | Python 3.11+ required (matches pyproject.toml requires-python) | Runtime dependency |
| Technical | Each platform builds natively (no cross-compilation) | electron-builder + Python bundling requires native OS |
| Resource | macOS/Linux builds require CI runners (developer machine is Windows) | Cannot build DMG/AppImage locally |

---

## 3. Functional Requirements

### FR-DIST-01: App Icon Assets

**Description**: The build system shall include application icon files in all three platform formats, stored in `electron/icons/`.

**Priority**: P0 (blocks all installer builds)
**Source**: DIST-1
**Dependencies**: None

**Acceptance Criteria**:

- **AC-01-1**: Given the `electron/icons/` directory,
  When the developer runs the icon generation script,
  Then `icon.png` (≥ 256x256 pixels, PNG format), `icon.ico` (Windows ICO format, multi-resolution), and `icon.icns` (macOS ICNS format) are created in `electron/icons/`.

- **AC-01-2**: Given the generated icon files,
  When electron-builder runs `--win`, `--mac`, or `--linux`,
  Then the installer uses the corresponding icon without errors.

**Negative Cases**:
- **AC-01-N1**: Given a corrupt or missing icon file,
  When electron-builder runs,
  Then the build fails with a descriptive error (not a silent fallback to Electron default icon).

---

### FR-DIST-02: Version Synchronization

**Description**: A build-time script shall read the version from `pyproject.toml` and update `electron/package.json` to match, ensuring the installed application reports the correct version.

**Priority**: P0
**Source**: DIST-2
**Dependencies**: None

**Acceptance Criteria**:

- **AC-02-1**: Given `pyproject.toml` contains `version = "1.9.0"` and `electron/package.json` contains `"version": "1.0.0"`,
  When the sync-version script runs,
  Then `electron/package.json` is updated to `"version": "1.9.0"`.

- **AC-02-2**: Given the version has been synced,
  When the Electron app calls `app.getVersion()`,
  Then it returns the same version string as `pyproject.toml`.

**Negative Cases**:
- **AC-02-N1**: Given `pyproject.toml` has no `version` field or is malformed,
  When the sync-version script runs,
  Then it exits with a non-zero code and a descriptive error message.

---

### FR-DIST-03: Python Runtime Bundling

**Description**: A build script shall download a platform-appropriate embeddable/relocatable Python distribution, install all pip dependencies into it, download Playwright Chromium into a known location, and place the result in `electron/python-runtime/` for inclusion in the installer via extraResources.

**Priority**: P0
**Source**: DIST-3, DIST-4, DIST-5
**Dependencies**: None

**Acceptance Criteria**:

- **AC-03-1**: Given the developer runs the bundle script on Windows,
  When the script completes,
  Then `electron/python-runtime/python.exe` exists and can execute `python -c "import flask; import playwright; import reportlab; print('ok')"` successfully.

- **AC-03-2**: Given the developer runs the bundle script on macOS,
  When the script completes,
  Then `electron/python-runtime/bin/python3` exists and can import all runtime dependencies.

- **AC-03-3**: Given the developer runs the bundle script on Linux,
  When the script completes,
  Then `electron/python-runtime/bin/python3` exists and can import all runtime dependencies.

- **AC-03-4**: Given the bundle script has completed,
  When `PLAYWRIGHT_BROWSERS_PATH` is set to `electron/python-runtime/playwright-browsers/`,
  Then Playwright can launch Chromium from that path.

- **AC-03-5**: Given the bundle is complete,
  When the `electron/python-runtime/` directory is measured,
  Then it shall not exceed 500MB uncompressed (target: ~200MB without Chromium, ~350MB with).

**Negative Cases**:
- **AC-03-N1**: Given no internet connection,
  When the bundle script runs,
  Then it fails with a clear error message indicating the download URL that failed.

- **AC-03-N2**: Given the bundle script is run on an unsupported platform (e.g., ARM32 Linux),
  When the script detects the platform,
  Then it exits with error "Unsupported platform: {platform}-{arch}".

---

### FR-DIST-04: Packaged Mode Python Detection

**Description**: In packaged mode (app.isPackaged === true), `python-backend.js` shall locate the bundled Python runtime in `process.resourcesPath/python-runtime/` before falling back to venv or system Python. It shall set `PLAYWRIGHT_BROWSERS_PATH` and `PYTHONPATH` environment variables so the backend can find its dependencies and Playwright Chromium.

**Priority**: P0
**Source**: DIST-3, DIST-4, DIST-5
**Dependencies**: FR-DIST-03

**Acceptance Criteria**:

- **AC-04-1**: Given the app is packaged and `python-runtime/python.exe` (Windows) or `python-runtime/bin/python3` (macOS/Linux) exists in `process.resourcesPath`,
  When `findPython()` is called,
  Then it returns the bundled Python path.

- **AC-04-2**: Given the app is packaged,
  When `startBackend()` spawns the Python process,
  Then `PLAYWRIGHT_BROWSERS_PATH` is set to `{resourcesPath}/python-runtime/playwright-browsers` and `PYTHONPATH` is set to `{resourcesPath}/python-backend`.

- **AC-04-3**: Given the app is in dev mode (not packaged),
  When `findPython()` is called,
  Then behavior is unchanged (checks venv first, then system Python).

**Negative Cases**:
- **AC-04-N1**: Given the app is packaged but `python-runtime/` is missing or corrupt,
  When `findPython()` is called,
  Then it falls back to system Python and logs a warning (graceful degradation).

---

### FR-DIST-05: Updated extraResources Configuration

**Description**: The `electron/package.json` build.extraResources configuration shall include all source files required by the Python backend, including files added after the initial v1.0 setup.

**Priority**: P0
**Source**: DIST-3
**Dependencies**: None

**Acceptance Criteria**:

- **AC-05-1**: Given the extraResources filter,
  When electron-builder copies files,
  Then the following are included: `app.py`, `app_state.py`, `run.py`, `pyproject.toml`, `config/**/*`, `bot/**/*`, `core/**/*`, `db/**/*`, `templates/**/*`, `routes/**/*`, `static/**/*`.

- **AC-05-2**: Given the extraResources filter,
  When electron-builder copies files,
  Then `__pycache__/` directories and `*.pyc` files are excluded.

- **AC-05-3**: Given the extraResources filter,
  When electron-builder copies files,
  Then `tests/`, `venv/`, `.git/`, `electron/`, `node_modules/`, and `*.txt` temp files are excluded.

---

### FR-DIST-06: Windows NSIS Installer

**Description**: Running `npm run dist:win` shall produce a Windows NSIS installer (.exe) that installs AutoApply, creates Start Menu shortcuts, and allows the user to choose the installation directory.

**Priority**: P0
**Source**: DIST-3
**Dependencies**: FR-DIST-01, FR-DIST-02, FR-DIST-03, FR-DIST-05

**Acceptance Criteria**:

- **AC-06-1**: Given all dependencies are met,
  When `npm run dist:win` completes,
  Then an `.exe` installer exists in `electron/build/`.

- **AC-06-2**: Given the installer is run on a clean Windows 10+ machine (no Python/Node installed),
  When the user completes the installation wizard,
  Then AutoApply launches, the setup wizard appears, and the backend health endpoint responds.

- **AC-06-3**: Given AutoApply is installed,
  When the user uninstalls via Add/Remove Programs,
  Then all installed files are removed (except user data at `~/.autoapply/`).

---

### FR-DIST-07: macOS DMG Installer

**Description**: Running `npm run dist:mac` shall produce a macOS DMG containing the AutoApply.app bundle.

**Priority**: P1
**Source**: DIST-4
**Dependencies**: FR-DIST-01, FR-DIST-02, FR-DIST-03, FR-DIST-05

**Acceptance Criteria**:

- **AC-07-1**: Given all dependencies are met,
  When `npm run dist:mac` completes on a macOS runner,
  Then a `.dmg` file exists in `electron/build/`.

- **AC-07-2**: Given the DMG is mounted on a clean macOS 12+ machine,
  When the user drags AutoApply.app to Applications and launches it,
  Then the app starts (after Gatekeeper approval) and the backend health endpoint responds.

**Negative Cases**:
- **AC-07-N1**: Given the DMG is unsigned,
  When the user opens the app,
  Then macOS shows a Gatekeeper warning. The user can proceed via right-click → Open or `xattr -cr`.

---

### FR-DIST-08: Linux AppImage

**Description**: Running `npm run dist:linux` shall produce a Linux AppImage that runs on Ubuntu 22.04+ without installation.

**Priority**: P1
**Source**: DIST-5
**Dependencies**: FR-DIST-01, FR-DIST-02, FR-DIST-03, FR-DIST-05

**Acceptance Criteria**:

- **AC-08-1**: Given all dependencies are met,
  When `npm run dist:linux` completes on an Ubuntu runner,
  Then an `.AppImage` file exists in `electron/build/`.

- **AC-08-2**: Given the AppImage is downloaded on Ubuntu 22.04+,
  When the user makes it executable (`chmod +x`) and runs it,
  Then AutoApply launches and the backend health endpoint responds.

---

### FR-DIST-09: Release CI Workflow

**Description**: A GitHub Actions workflow shall automatically build installers for all three platforms when a version tag (e.g., `v1.9.0`) is pushed, and upload the artifacts to a GitHub Release.

**Priority**: P1
**Source**: DIST-6
**Dependencies**: FR-DIST-01 through FR-DIST-08

**Acceptance Criteria**:

- **AC-09-1**: Given a tag matching `v*` is pushed to the repository,
  When the release workflow triggers,
  Then three parallel jobs run: Windows (windows-latest), macOS (macos-latest), Linux (ubuntu-latest).

- **AC-09-2**: Given all three jobs succeed,
  When the release job runs,
  Then a GitHub Release is created with the tag name, and the `.exe`, `.dmg`, and `.AppImage` artifacts are attached.

- **AC-09-3**: Given the existing CI workflow (`ci.yml`) runs on push to master,
  When a tag is pushed,
  Then the release workflow runs independently (does not interfere with the existing CI).

**Negative Cases**:
- **AC-09-N1**: Given one platform build fails (e.g., macOS),
  When the release job checks,
  Then it still uploads artifacts from successful platforms and marks the release as draft.

---

## 4. Non-Functional Requirements

### NFR-DIST-01: Installer Size

**Description**: Compressed installer size shall not exceed 200MB per platform.
**Metric**: `.exe` / `.dmg` / `.AppImage` file size ≤ 200MB.
**Priority**: P1
**Validation Method**: Measure artifact size in CI; log warning if > 150MB, fail if > 200MB.

### NFR-DIST-02: Build Time

**Description**: The full dist build (bundle Python + electron-builder) shall complete within 15 minutes per platform on CI runners.
**Metric**: CI job duration ≤ 15 minutes.
**Priority**: P2
**Validation Method**: Monitor CI job duration.

### NFR-DIST-03: Startup Time

**Description**: The installed app shall reach the dashboard (or setup wizard) within 15 seconds of launch on commodity hardware.
**Metric**: Time from double-click to main window visible ≤ 15 seconds.
**Priority**: P1
**Validation Method**: Manual testing on clean VM.

### NFR-DIST-04: Clean Uninstall

**Description**: Uninstalling shall remove all application files but preserve user data at `~/.autoapply/`.
**Metric**: No application files remain in Program Files (Windows) or Applications (macOS) after uninstall. `~/.autoapply/` directory is untouched.
**Priority**: P1
**Validation Method**: Uninstall and verify file system state.

---

## 5. Interface Requirements

### 5.1 Build Scripts Interface
| Script | Input | Output |
|--------|-------|--------|
| `electron/scripts/bundle-python.js` | Platform detection, `pyproject.toml` deps | `electron/python-runtime/` directory |
| `electron/scripts/sync-version.js` | `pyproject.toml` version | Updated `electron/package.json` |
| `npm run dist:win` | All above | `electron/build/*.exe` |
| `npm run dist:mac` | All above | `electron/build/*.dmg` |
| `npm run dist:linux` | All above | `electron/build/*.AppImage` |

### 5.2 Modified Existing Interfaces
| File | Change |
|------|--------|
| `electron/python-backend.js` | `findPython()` checks bundled runtime first in packaged mode |
| `electron/python-backend.js` | `startBackend()` sets `PLAYWRIGHT_BROWSERS_PATH` and `PYTHONPATH` |
| `electron/package.json` | Updated `extraResources`, `version`, `scripts` |

---

## 6. Out of Scope

- **Code signing (Windows/macOS)**: Requires paid developer accounts. Documented for future in README.
- **Auto-update (Squirrel/electron-updater)**: Deferred to D-1.
- **Cross-compilation**: Each platform builds natively on its own OS.
- **ARM Linux builds**: Only x64 Linux supported initially.
- **Reducing Playwright Chromium size**: Accept the ~150MB browser engine; optimize later if needed.

---

## 7. Dependencies

### External Dependencies
| Dependency | Type | Status | Risk if Unavailable |
|-----------|------|--------|---------------------|
| electron-builder ^25.0.0 | Build | Available (in devDeps) | Cannot build installers |
| Python embeddable (Windows) | Build | Available at python.org | Cannot bundle Python for Windows |
| python-build-standalone | Build | Available on GitHub | Cannot bundle Python for macOS/Linux |
| GitHub Actions runners | CI | Available | Cannot automate release builds |

### Internal Dependencies
| This Feature Needs | From | Status |
|-------------------|------|--------|
| All Python source files | Phases 1-7 | Done |
| All static/template files | Phase 1 + LE-1 | Done |
| routes/ directory | Production Readiness | Done |
| app_state.py | Production Readiness | Done |

---

## 8. Risks

| # | Risk | Probability | Impact | Mitigation |
|---|------|:-----------:|:------:|------------|
| R1 | Embeddable Python missing native dependencies for gevent/reportlab | Medium | High | Test pip install early; fall back to full Python if needed |
| R2 | Playwright Chromium path relocation breaks at runtime | Low | High | Explicitly set PLAYWRIGHT_BROWSERS_PATH; integration test |
| R3 | Installer size exceeds user expectations (>150MB) | Medium | Low | Document in release notes; Chromium is the bulk |
| R4 | macOS Gatekeeper blocks unsigned app | Certain | Medium | Document xattr workaround; plan code signing for future |
| R5 | CI build time exceeds free-tier limits | Low | Medium | Cache Python runtime download between builds |

---

## 9. Requirements Traceability Seeds

| Req ID | Source (PRD) | Traces Forward To |
|--------|-------------|-------------------|
| FR-DIST-01 | DIST-1 | Design: icon gen script → Code: `electron/scripts/`, `electron/icons/` → Test: file existence check |
| FR-DIST-02 | DIST-2 | Design: sync script → Code: `electron/scripts/sync-version.js` → Test: version parity check |
| FR-DIST-03 | DIST-3,4,5 | Design: bundle script → Code: `electron/scripts/bundle-python.js` → Test: import validation |
| FR-DIST-04 | DIST-3,4,5 | Design: packaged mode → Code: `electron/python-backend.js` → Test: findPython() tests |
| FR-DIST-05 | DIST-3 | Design: build config → Code: `electron/package.json` → Test: build output validation |
| FR-DIST-06 | DIST-3 | Design: NSIS config → Code: `electron/package.json` → Test: install/launch/uninstall on Windows |
| FR-DIST-07 | DIST-4 | Design: DMG config → Code: `electron/package.json` → Test: mount/launch on macOS |
| FR-DIST-08 | DIST-5 | Design: AppImage config → Code: `electron/package.json` → Test: chmod/launch on Linux |
| FR-DIST-09 | DIST-6 | Design: CI workflow → Code: `.github/workflows/release.yml` → Test: tag push triggers release |

---

## Gate 3 Output

**Document**: SRS-TASK-016-distribution
**FRs**: 9 functional requirements (FR-DIST-01 through FR-DIST-09)
**NFRs**: 4 non-functional requirements (NFR-DIST-01 through NFR-DIST-04)
**ACs**: 22 acceptance criteria (18 positive + 4 negative)
**Quality Checklist**: 25/25 items passed

### Handoff Routing
| Recipient | What They Receive |
|-----------|-------------------|
| System Engineer | Full SRS for architecture design (SAD) |
| Backend Developer | FR-DIST-03 (bundle script), FR-DIST-04 (python-backend.js changes) |
| Release Engineer | FR-DIST-05 (extraResources), FR-DIST-06/07/08 (installers), FR-DIST-09 (CI) |
| Documenter | Installation instructions for README, Gatekeeper workaround |

→ System Engineer: produce SAD with ADRs for Python bundling strategy and CI workflow design.
→ Release Engineer: implement build scripts, package.json updates, and CI workflow.

---
---

# System Architecture Document — Phase 8 Distribution

**Document ID**: SAD-TASK-016-distribution
**Version**: 1.0
**Date**: 2026-03-11
**Status**: approved
**Author**: Claude (System Engineer)
**SRS Reference**: SRS-TASK-016-distribution

---

## 1. Executive Summary

The distribution architecture bundles a complete Python runtime with all dependencies and Playwright Chromium alongside the Electron shell, producing platform-specific installers via electron-builder. A two-phase build pipeline (1: bundle Python runtime, 2: electron-builder packaging) runs natively on each platform, automated via GitHub Actions on version tag pushes.

---

## 2. Architecture Overview

### 2.1 Build Pipeline

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│ sync-version.js  │────▶│ bundle-python.js  │────▶│ electron-builder │
│ Read pyproject   │     │ Download Python   │     │ Package .exe/    │
│ Update pkg.json  │     │ pip install deps  │     │ .dmg/.AppImage   │
└──────────────────┘     │ Install Chromium  │     └──────────────────┘
                         │ → python-runtime/ │            │
                         └──────────────────┘            ▼
                                                  ┌──────────────────┐
                                                  │ electron/build/  │
                                                  │ Installer output │
                                                  └──────────────────┘
```

### 2.2 Installed App Structure (Windows)

```
C:\Program Files\AutoApply\
├── AutoApply.exe              ← Electron main process
├── resources/
│   ├── python-backend/        ← extraResources[0]: Python source
│   │   ├── app.py
│   │   ├── app_state.py
│   │   ├── run.py
│   │   ├── config/
│   │   ├── core/
│   │   ├── db/
│   │   ├── bot/
│   │   ├── routes/
│   │   ├── static/
│   │   └── templates/
│   └── python-runtime/        ← extraResources[1]: Bundled Python
│       ├── python.exe         (Windows) or bin/python3 (macOS/Linux)
│       ├── Lib/
│       │   └── site-packages/ ← All pip dependencies
│       └── playwright-browsers/
│           └── chromium-*/    ← Pre-downloaded Playwright Chromium
└── [Electron internals]
```

### 2.3 Runtime Detection Flow (python-backend.js)

```
findPython()
    │
    ├── app.isPackaged?
    │   ├── YES → Check {resourcesPath}/python-runtime/python.exe
    │   │         Found? → return bundled path
    │   │         Missing? → log warning, fall through
    │   │
    │   └── NO (dev mode) → unchanged behavior
    │
    ├── Check venv/ or .venv/ in project root
    │   Found? → return venv python
    │
    └── Check system python3/python
        Found 3.x? → return system python
        Not found → return null (startup error)
```

---

## 3. Interface Contracts

### 3.1 electron/scripts/sync-version.js

**Purpose**: Read version from pyproject.toml and write it to electron/package.json.
**Category**: command (mutates package.json)

**Invocation**: `node electron/scripts/sync-version.js`

**Input**: Reads `../pyproject.toml` (relative to electron/) for `version = "X.Y.Z"` line.
**Output**: Updates `electron/package.json` `"version"` field in-place.
**Exit codes**: 0 = success, 1 = error (version not found or file unreadable).

**Algorithm**:
1. Read `../pyproject.toml` as text.
2. Match `/^version\s*=\s*"([^"]+)"/m`.
3. If no match, exit(1) with error "Could not find version in pyproject.toml".
4. Read `package.json`, parse JSON, set `version` field, write back with 2-space indent + trailing newline.
5. Log `Synced version to X.Y.Z`.

---

### 3.2 electron/scripts/generate-icon.js

**Purpose**: Generate a placeholder app icon programmatically and convert to all platform formats.
**Category**: command (creates icon files)

**Invocation**: `node electron/scripts/generate-icon.js`

**Output**: Creates in `electron/icons/`:
- `icon.png` — 1024x1024 PNG (source)
- `icon.ico` — Multi-resolution Windows ICO (256, 128, 64, 48, 32, 16)
- `icon.icns` — macOS ICNS

**Dependencies**: Uses `canvas` npm package (node-canvas) for PNG generation, `png2icons` npm package for ICO/ICNS conversion.

**Algorithm**:
1. Create 1024x1024 canvas.
2. Draw rounded rectangle background (dark blue #1a1a2e).
3. Draw "AA" text in center (white, bold, ~400px font).
4. Draw subtle border (#16213e).
5. Export as `icons/icon.png`.
6. Convert to ICO via png2icons: `png2icons icon.png icon --icns --ico`.

**Note**: These are placeholder icons for development and CI. A professionally designed icon can replace `icon.png` at any time, then re-run conversion.

---

### 3.3 electron/scripts/bundle-python.js

**Purpose**: Download platform-appropriate Python distribution, install dependencies, and download Playwright Chromium.
**Category**: command (creates python-runtime/ directory)

**Invocation**: `node electron/scripts/bundle-python.js`

**Input**: Platform auto-detected via `process.platform` and `process.arch`. Dependencies read from `../pyproject.toml`.

**Output**: `electron/python-runtime/` directory containing:
- Python executable
- All pip packages from pyproject.toml [dependencies]
- Playwright Chromium browser

**Exit codes**: 0 = success, 1 = error.

**Platform-specific download URLs**:

| Platform | Source | URL Pattern |
|----------|--------|-------------|
| win32-x64 | python.org embeddable | `https://www.python.org/ftp/python/{VER}/python-{VER}-embed-amd64.zip` |
| darwin-x64 | python-build-standalone | `https://github.com/indygreg/python-build-standalone/releases/download/{TAG}/cpython-{VER}+{TAG}-x86_64-apple-darwin-install_only.tar.gz` |
| darwin-arm64 | python-build-standalone | `https://github.com/indygreg/python-build-standalone/releases/download/{TAG}/cpython-{VER}+{TAG}-aarch64-apple-darwin-install_only.tar.gz` |
| linux-x64 | python-build-standalone | `https://github.com/indygreg/python-build-standalone/releases/download/{TAG}/cpython-{VER}+{TAG}-x86_64-unknown-linux-gnu-install_only.tar.gz` |

**Algorithm**:

```
1. Clean: Remove existing python-runtime/ if present.
2. Create: mkdir python-runtime/
3. Download: Fetch Python archive for current platform.
4. Extract: Unzip/untar into python-runtime/.
5. [Windows only] Patch: Uncomment "import site" in python3XX._pth.
6. [Windows only] Bootstrap pip: Download get-pip.py, run it.
7. Parse deps: Read pyproject.toml, extract [project].dependencies list.
8. pip install: Run bundled python -m pip install <deps> --no-cache-dir.
9. Playwright: Set PLAYWRIGHT_BROWSERS_PATH=python-runtime/playwright-browsers/
   Run: bundled python -m playwright install chromium.
10. Cleanup: Remove pip cache, __pycache__, *.pyc, test dirs from site-packages.
11. Verify: Run bundled python -c "import flask; import playwright; print('ok')".
12. Report: Log directory size.
```

**Error handling**: Each step logs progress. Download failures include URL and HTTP status. pip failures include the package name. Cleanup failures are logged but non-fatal.

---

### 3.4 python-backend.js — Modified findPython()

**Purpose**: Locate Python executable, prioritizing bundled runtime in packaged mode.
**Category**: query

**New priority order**:
1. **Bundled runtime** (packaged mode only): `{resourcesPath}/python-runtime/python.exe` (Windows) or `{resourcesPath}/python-runtime/bin/python3` (macOS/Linux).
2. **Local venv** (dev mode): `{projectRoot}/venv/Scripts/python.exe` or `bin/python3`.
3. **System Python**: `python3`, `python`, `py` (Windows).

**New code block** (inserted before venv check):

```javascript
// Priority 1: Bundled Python runtime (packaged mode)
if (isPackaged) {
  const bundledCandidates = process.platform === 'win32'
    ? [path.join(process.resourcesPath, 'python-runtime', 'python.exe')]
    : [
        path.join(process.resourcesPath, 'python-runtime', 'bin', 'python3'),
        path.join(process.resourcesPath, 'python-runtime', 'bin', 'python'),
      ];
  for (const p of bundledCandidates) {
    if (fs.existsSync(p)) return p;
  }
  console.warn('Bundled Python runtime not found, falling back to venv/system Python');
}
```

---

### 3.5 python-backend.js — Modified startBackend()

**Purpose**: Set environment variables for bundled mode before spawning Python.
**Category**: command

**New env vars set when `app.isPackaged`**:

| Env Variable | Value | Purpose |
|-------------|-------|---------|
| `PLAYWRIGHT_BROWSERS_PATH` | `{resourcesPath}/python-runtime/playwright-browsers` | Tell Playwright where to find Chromium |
| `PYTHONPATH` | `{resourcesPath}/python-backend` | Ensure Python can import app modules |

**New code block** (after `const env = { ...process.env };`):

```javascript
if (isPackaged) {
  env.PLAYWRIGHT_BROWSERS_PATH = path.join(
    process.resourcesPath, 'python-runtime', 'playwright-browsers'
  );
  env.PYTHONPATH = path.join(process.resourcesPath, 'python-backend');
}
```

---

### 3.6 electron/package.json — Updated Build Config

**Full updated `build` section**:

```json
{
  "build": {
    "appId": "com.autoapply.desktop",
    "productName": "AutoApply",
    "directories": {
      "output": "build"
    },
    "extraResources": [
      {
        "from": "../",
        "to": "python-backend",
        "filter": [
          "app.py",
          "app_state.py",
          "run.py",
          "pyproject.toml",
          "config/**/*",
          "bot/**/*",
          "core/**/*",
          "db/**/*",
          "templates/**/*",
          "routes/**/*",
          "static/**/*",
          "!**/__pycache__/**",
          "!**/*.pyc",
          "!**/tests/**",
          "!**/venv/**",
          "!**/.venv/**",
          "!**/node_modules/**",
          "!**/.git/**"
        ]
      },
      {
        "from": "python-runtime",
        "to": "python-runtime",
        "filter": ["**/*"]
      }
    ],
    "win": {
      "target": "nsis",
      "icon": "icons/icon.ico"
    },
    "mac": {
      "target": "dmg",
      "icon": "icons/icon.icns",
      "category": "public.app-category.productivity"
    },
    "linux": {
      "target": "AppImage",
      "icon": "icons/icon.png",
      "category": "Office"
    },
    "nsis": {
      "oneClick": false,
      "allowToChangeInstallationDirectory": true,
      "installerIcon": "icons/icon.ico",
      "uninstallerIcon": "icons/icon.ico"
    }
  }
}
```

**Updated `scripts` section**:

```json
{
  "scripts": {
    "start": "electron .",
    "prebuild": "node scripts/sync-version.js && node scripts/bundle-python.js",
    "dist:win": "npm run prebuild && electron-builder --win",
    "dist:mac": "npm run prebuild && electron-builder --mac",
    "dist:linux": "npm run prebuild && electron-builder --linux",
    "dist:all": "npm run prebuild && electron-builder --win --mac --linux",
    "icons:generate": "node scripts/generate-icon.js"
  }
}
```

---

## 4. Architecture Decision Records

### ADR-018: Python Bundling Strategy

**Status**: accepted
**Context**: End users shall not need Python installed. The app must bundle a Python runtime. Options differ by platform.

**Decision**: Use platform-native embeddable/relocatable Python distributions:
- **Windows**: Python.org embeddable zip (official, minimal, ~15MB).
- **macOS/Linux**: python-build-standalone (community, relocatable, ~45MB).

**Alternatives Considered**:

| Option | Pros | Cons |
|--------|------|------|
| PyInstaller (freeze app.py) | Single executable, no Python needed | Breaks Playwright (needs subprocess python), complex debugging, huge binary |
| Full Python installer bundled | 100% compatibility | Requires admin rights, ~100MB, conflicts with existing Python |
| **Embeddable + python-build-standalone** | **Minimal size, no installer, relocatable** | **Requires pip bootstrap on Windows, untested with all deps** |
| conda/mamba env | Good dep resolution | Massive (~500MB+), overkill |

**Consequences**:
- Positive: Small footprint, no admin rights, no conflicts, works offline after install.
- Negative: Must bootstrap pip on Windows (embeddable doesn't include it). Must test all native deps (gevent, reportlab compile C extensions).
- Risk: If a C extension fails to install in embeddable Python, fall back to using `actions/setup-python` in CI and bundling the full interpreter. Mitigation: test early.

---

### ADR-019: Icon Generation Approach

**Status**: accepted
**Context**: electron-builder requires icon files in platform-specific formats. We need icons for development/CI now, with the option to swap in professional icons later.

**Decision**: Generate placeholder icons programmatically via a Node.js script using `canvas` (node-canvas) for PNG creation and `png2icons` for format conversion. Store generated files in git so builds work without running the generator.

**Alternatives Considered**:

| Option | Pros | Cons |
|--------|------|------|
| Manual design (Figma/GIMP) | Professional result | Requires designer, blocks progress |
| **Programmatic placeholder** | **Immediate, automatable, replaceable** | **Not professional quality** |
| Electron default icon | Zero effort | Not branded, looks generic |
| SVG → conversion | Scalable source | Requires librsvg or sharp, more complex |

**Consequences**:
- Positive: Unblocks all installer work immediately. Icons committed to git.
- Negative: Placeholder quality — acceptable for development, should be replaced for public release.
- Future: Drop a professional `icon.png` into `electron/icons/`, re-run `npm run icons:generate`, commit.

---

### ADR-020: CI Release Workflow Design

**Status**: accepted
**Context**: Installers must be built on native OS runners (Windows, macOS, Linux) and uploaded to GitHub Releases on tag push.

**Decision**: Create `.github/workflows/release.yml` triggered on `v*` tags. Three parallel build jobs (one per OS) + one release job that collects artifacts and creates a GitHub Release. Use `actions/upload-artifact` between jobs and `softprops/action-gh-release` for release creation.

**Key design choices**:
- **Tag-triggered only**: No accidental releases from branch pushes.
- **Parallel builds**: Minimize total time (each ~10-15 min).
- **Draft release on partial failure**: If one platform fails, release others as draft.
- **Cache Python downloads**: Use `actions/cache` to avoid re-downloading Python runtime on every build.
- **No code signing**: Unsigned builds for now; signing requires secrets setup (future).

---

## 5. Design Traceability Matrix

| Requirement | Type | Design Component(s) | Interface(s) | ADR |
|-------------|------|---------------------|---------------|-----|
| FR-DIST-01 | FR | generate-icon.js | §3.2 | ADR-019 |
| FR-DIST-02 | FR | sync-version.js | §3.1 | — |
| FR-DIST-03 | FR | bundle-python.js | §3.3 | ADR-018 |
| FR-DIST-04 | FR | python-backend.js (modified) | §3.4, §3.5 | ADR-018 |
| FR-DIST-05 | FR | package.json build config | §3.6 | — |
| FR-DIST-06 | FR | electron-builder + NSIS config | §3.6 | — |
| FR-DIST-07 | FR | electron-builder + DMG config | §3.6 | — |
| FR-DIST-08 | FR | electron-builder + AppImage config | §3.6 | — |
| FR-DIST-09 | FR | release.yml workflow | §3.7 (ADR-020) | ADR-020 |
| NFR-DIST-01 | NFR | bundle-python.js (size check) | §3.3 step 12 | — |
| NFR-DIST-02 | NFR | CI caching, parallel builds | ADR-020 | ADR-020 |
| NFR-DIST-03 | NFR | splash screen (existing) | — | — |
| NFR-DIST-04 | NFR | NSIS uninstaller config | §3.6 | — |

**Completeness**: 9/9 FRs mapped, 4/4 NFRs mapped. Zero gaps.

---

## 6. Implementation Plan

| Order | Task ID | Description | Depends On | Size | Risk | FR Coverage |
|-------|---------|-------------|------------|------|------|-------------|
| 1 | IMPL-001 | Update electron/.gitignore (add python-runtime/) | — | S | Low | Foundation |
| 2 | IMPL-002 | Create sync-version.js | — | S | Low | FR-DIST-02 |
| 3 | IMPL-003 | Create generate-icon.js + generate icons | — | M | Low | FR-DIST-01 |
| 4 | IMPL-004 | Update package.json (extraResources, scripts, version) | IMPL-002 | S | Low | FR-DIST-05 |
| 5 | IMPL-005 | Create bundle-python.js | — | L | High | FR-DIST-03 |
| 6 | IMPL-006 | Update python-backend.js (findPython + env vars) | — | S | Medium | FR-DIST-04 |
| 7 | IMPL-007 | Test Windows build locally (npm run dist:win) | 001-006 | M | High | FR-DIST-06 |
| 8 | IMPL-008 | Create release.yml CI workflow | 001-006 | M | Medium | FR-DIST-09 |
| 9 | IMPL-009 | Test CI by pushing a tag | IMPL-008 | S | Medium | FR-DIST-07, FR-DIST-08 |

### Per-Task Detail

#### IMPL-001: Update .gitignore
- **Modifies**: `electron/.gitignore`
- **Adds**: `python-runtime/`
- **Done when**: `git status` does not show python-runtime/ contents

#### IMPL-002: Version Sync Script
- **Creates**: `electron/scripts/sync-version.js` (~30 lines)
- **Tests**: Run script, verify package.json version matches pyproject.toml
- **Done when**: `node scripts/sync-version.js` updates version correctly

#### IMPL-003: Icon Generation
- **Creates**: `electron/scripts/generate-icon.js` (~60 lines), `electron/icons/icon.png`, `icon.ico`, `icon.icns`
- **Dev deps**: `canvas`, `png2icons` (added to electron/package.json devDependencies)
- **Done when**: All three icon files exist and are valid format

#### IMPL-004: Package.json Updates
- **Modifies**: `electron/package.json`
- **Changes**: extraResources filter (add routes, static, app_state.py, pyproject.toml; add exclusions), scripts (prebuild, dist:*), version field
- **Done when**: electron-builder --dir produces correct resource structure

#### IMPL-005: Python Bundle Script
- **Creates**: `electron/scripts/bundle-python.js` (~200 lines)
- **Algorithm**: Download → Extract → Bootstrap pip → Install deps → Install Chromium → Cleanup → Verify
- **Done when**: `python-runtime/python.exe -c "import flask; import playwright"` succeeds

#### IMPL-006: Packaged Mode Detection
- **Modifies**: `electron/python-backend.js`
- **Changes**: findPython() bundled runtime priority, startBackend() env vars
- **Done when**: Dev mode still works; packaged mode detects bundled Python

#### IMPL-007: Windows Build Test
- **Runs**: `npm run dist:win` locally on Windows
- **Validates**: Installer produced, installs on clean path, app launches, backend responds
- **Done when**: End-to-end install → launch → health check passes

#### IMPL-008: Release CI Workflow
- **Creates**: `.github/workflows/release.yml` (~80 lines)
- **Design**: Tag-triggered, 3 parallel OS jobs, artifact upload, GitHub Release creation
- **Done when**: Workflow YAML passes `actionlint` validation

#### IMPL-009: CI Release Test
- **Pushes**: Tag `v1.9.0-rc1` to trigger workflow
- **Validates**: All 3 platform builds succeed, artifacts uploaded to draft release
- **Done when**: GitHub Release contains .exe, .dmg, .AppImage

---

## Gate 4 Output

**Document**: SAD-TASK-016-distribution
**Components**: 6 components (3 new scripts, 1 modified module, 1 updated config, 1 CI workflow)
**Interfaces**: 6 contracts specified (§3.1–§3.6)
**ADRs**: 3 decisions documented (ADR-018, ADR-019, ADR-020)
**Impl Tasks**: 9 tasks in dependency order
**Traceability**: 13/13 requirements mapped (100%)
**Checklist**: 20/20 items passed

### Handoff Routing
| Recipient | What They Receive |
|-----------|-------------------|
| Backend Developer | IMPL-005 (bundle script), IMPL-006 (python-backend.js) |
| Release Engineer | IMPL-001-004, IMPL-007-009 (config, CI, testing) |
| Documenter | Installation instructions, Gatekeeper workaround |

→ Developers MUST implement according to contracts EXACTLY.
→ Icon files SHALL be committed to git (not generated at build time in CI).
→ The bundle-python.js script runs at build time only, never at runtime.
