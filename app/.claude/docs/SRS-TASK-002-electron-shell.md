# Software Requirements Specification

**Document ID**: SRS-TASK-002-electron-shell
**Version**: 1.0
**Date**: 2026-03-09
**Status**: approved
**Author**: Claude (Requirements Analyst)

---

## 1. Purpose and Scope

### 1.1 Purpose
Specifies requirements for wrapping AutoApply's Flask web server in an Electron desktop shell, providing a standalone native application experience on Windows, macOS, and Ubuntu.

### 1.2 Scope
- Electron main process that manages app lifecycle and spawns Python backend
- Shared Chromium strategy (Playwright reuses Electron's Chromium)
- Splash screen during backend startup
- Graceful shutdown and process cleanup
- System tray integration
- Development mode (live reload) and production mode (packaged)

**Out of Scope**: Auto-update mechanism (deferred), code signing (deferred), custom titlebar/frameless window (deferred).

### 1.3 Definitions

| Term | Definition |
|------|-----------|
| Main process | Electron's Node.js process that creates windows and manages app lifecycle |
| Renderer process | The Chromium window that displays the Flask SPA |
| Shared Chromium | Strategy where Playwright bot reuses Electron's bundled Chromium binary instead of downloading its own |
| Backend process | The Python child process running Flask + SocketIO |

---

## 2. Assumptions

| # | Assumption | Risk if Wrong | Mitigation |
|---|-----------|---------------|------------|
| A1 | Playwright can use an external Chromium binary via PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH | Bot automation fails | Fall back to Playwright's own Chromium download |
| A2 | Node.js 18+ is available on dev machine for building | Cannot build Electron | Document in prerequisites |
| A3 | Port 5000 is available on user's machine | App fails to start | Auto-detect available port |
| A4 | Electron's Chromium version is compatible with Playwright's protocol | Automation commands fail | Pin compatible Electron + Playwright versions |

---

## 3. Functional Requirements

### FR-019: Electron App Launch
**Description**: The system shall launch as a native desktop application with its own window, taskbar icon, and window controls.
**Priority**: P0
**Acceptance Criteria**:
- **AC-019-1**: Given the app is installed, When the user launches it, Then a native window opens with the AutoApply dashboard.
- **AC-019-2**: Given the app is launched, When the window appears, Then it has a title of "AutoApply", standard window controls (minimize, maximize, close), and an app icon.
- **AC-019-N1**: Given port 5000 is occupied, When the app launches, Then it selects the next available port and still starts successfully.

### FR-020: Python Backend Lifecycle
**Description**: The Electron main process shall spawn the Python Flask backend as a child process and manage its lifecycle.
**Priority**: P0
**Acceptance Criteria**:
- **AC-020-1**: Given the Electron app starts, When the main process initializes, Then it spawns Python running run.py with the --no-browser flag.
- **AC-020-2**: Given Python is spawning, When the backend process starts, Then stdout and stderr are captured to ~/.autoapply/backend.log.
- **AC-020-3**: Given the backend is running, When the Electron main process monitors it, Then it detects crashes and restarts the backend automatically (max 3 retries).
- **AC-020-N1**: Given Python is not installed or not found, When the app launches, Then it shows an error dialog with instructions to install Python 3.11+.

### FR-021: Health Check
**Description**: The Electron main process shall verify backend readiness via a health endpoint before showing the main window.
**Priority**: P0
**Acceptance Criteria**:
- **AC-021-1**: Given the backend is spawned, When the main process polls GET /api/health every 500ms, Then it waits until a 200 response is received.
- **AC-021-2**: Given the health check succeeds, When the backend is ready, Then the splash screen is replaced by the main dashboard window.
- **AC-021-N1**: Given the health check does not succeed within 30 seconds, When the timeout expires, Then the app shows an error dialog and offers to retry or quit.

### FR-022: Splash Screen
**Description**: The system shall show a loading screen while the Python backend starts.
**Priority**: P0
**Acceptance Criteria**:
- **AC-022-1**: Given the app launches, When the backend is not yet ready, Then a splash screen with the app name and a loading indicator is displayed.
- **AC-022-2**: Given the backend becomes ready, When the health check succeeds, Then the splash screen transitions to the main dashboard.

### FR-023: Graceful Shutdown
**Description**: The system shall cleanly shut down both Electron and the Python backend when the user closes the app.
**Priority**: P0
**Acceptance Criteria**:
- **AC-023-1**: Given the user closes the window (and tray is disabled), When the close event fires, Then Electron sends POST /api/shutdown to Flask, waits up to 5 seconds, then terminates the Python process.
- **AC-023-2**: Given the Python process does not stop within 5 seconds, When the timeout expires, Then the process is force-killed.
- **AC-023-3**: Given the app is shut down, When no AutoApply processes remain, Then port 5000 is freed and no orphan processes exist.

### FR-024: System Tray
**Description**: The system shall support minimizing to the system tray.
**Priority**: P1
**Acceptance Criteria**:
- **AC-024-1**: Given the app is running, When the user minimizes or closes the window, Then the app minimizes to the system tray with an icon.
- **AC-024-2**: Given the app is in the tray, When the user clicks the tray icon, Then the window is restored.
- **AC-024-3**: Given the app is in the tray, When the user right-clicks the tray icon, Then a context menu appears with "Show", "Quit" options.

### FR-025: Health Endpoint
**Description**: The Flask backend shall expose a GET /api/health endpoint for lifecycle management.
**Priority**: P0
**Acceptance Criteria**:
- **AC-025-1**: Given the Flask server is running, When GET /api/health is called, Then it returns {"status": "ok"} with HTTP 200.

### FR-026: Shutdown Endpoint
**Description**: The Flask backend shall expose a POST /api/shutdown endpoint for graceful termination.
**Priority**: P0
**Acceptance Criteria**:
- **AC-026-1**: Given the Flask server is running, When POST /api/shutdown is called from 127.0.0.1, Then the server initiates graceful shutdown.
- **AC-026-N1**: Given a shutdown request comes from a non-localhost origin, When POST /api/shutdown is called, Then it is rejected with 403.

### FR-027: No-Browser Flag
**Description**: run.py shall support a --no-browser flag that skips auto-opening the browser.
**Priority**: P0
**Acceptance Criteria**:
- **AC-027-1**: Given run.py is invoked with --no-browser, When the server starts, Then no browser window is opened.
- **AC-027-2**: Given run.py is invoked without --no-browser, When the server starts, Then the browser opens as before (backward compatible).

### FR-028: Configurable Port
**Description**: The Flask backend shall accept a port number via the AUTOAPPLY_PORT environment variable.
**Priority**: P0
**Acceptance Criteria**:
- **AC-028-1**: Given AUTOAPPLY_PORT=5050 is set, When run.py starts, Then Flask binds to port 5050.
- **AC-028-2**: Given AUTOAPPLY_PORT is not set, When run.py starts, Then Flask binds to port 5000 (default).

### FR-029: Shared Chromium
**Description**: Playwright shall use Electron's bundled Chromium binary for bot automation instead of downloading its own.
**Priority**: P0
**Acceptance Criteria**:
- **AC-029-1**: Given PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH points to Electron's Chromium, When the bot launches a browser, Then Playwright uses that binary.
- **AC-029-2**: Given the shared Chromium path is invalid, When the bot tries to launch, Then it falls back to Playwright's default Chromium and logs a warning.

### FR-030: Backend Log Capture
**Description**: The Python backend's stdout and stderr shall be written to a log file.
**Priority**: P1
**Acceptance Criteria**:
- **AC-030-1**: Given the backend is running, When output is produced, Then it is written to ~/.autoapply/backend.log.
- **AC-030-2**: Given the log file exceeds 10MB, When the app starts, Then the old log is rotated to backend.log.1.

---

## 4. Non-Functional Requirements

### NFR-011: Startup Time
**Description**: The app shall be fully interactive within 5 seconds of launch.
**Metric**: Time from double-click to dashboard visible < 5 seconds.
**Priority**: P0
**Validation**: Manual timing test on each platform.

### NFR-012: Bundle Size
**Description**: The packaged installer shall be under 250MB.
**Metric**: Installer file size < 250MB.
**Priority**: P1
**Validation**: Measure output of electron-builder.

### NFR-013: Cross-Platform
**Description**: The app shall run on Windows 10+, macOS 12+, and Ubuntu 20.04+.
**Priority**: P0
**Validation**: Manual launch test on each platform.

### NFR-014: Process Isolation
**Description**: No orphan Python processes shall remain after app exit.
**Metric**: Zero AutoApply-related processes after quit.
**Priority**: P0
**Validation**: Process list check after close on each platform.

### NFR-015: Single Instance
**Description**: Only one instance of the app shall run at a time.
**Metric**: Second launch focuses existing window instead of opening a new one.
**Priority**: P0
**Validation**: Attempt double launch.

### NFR-016: Security — Node.js
**Description**: The Electron renderer shall have nodeIntegration disabled and contextIsolation enabled.
**Priority**: P0
**Validation**: Security audit of BrowserWindow config.

---

## 5. Out of Scope

- **Auto-update mechanism** — Deferred to a future task.
- **Code signing and notarization** — Deferred (requires paid certificates).
- **Custom frameless titlebar** — Standard OS chrome is sufficient.
- **Linux Wayland support** — X11 only for now.

---

## 6. Requirements Traceability Seeds

| Req ID | Source | Traces Forward To |
|--------|-------|-------------------|
| FR-019 | US-007 | Design: main.js → Code: electron/main.js → Test: test_electron |
| FR-020 | US-009 | Design: python-backend.js → Code: electron/python-backend.js |
| FR-021 | US-008 | Design: health check → Code: python-backend.js + app.py |
| FR-022 | US-008 | Design: splash → Code: electron/splash.html |
| FR-023 | US-009 | Design: shutdown → Code: main.js + app.py |
| FR-024 | US-010 | Design: tray → Code: main.js |
| FR-025 | US-009 | Design: /api/health → Code: app.py |
| FR-026 | US-009 | Design: /api/shutdown → Code: app.py |
| FR-027 | US-007 | Design: CLI flag → Code: run.py |
| FR-028 | US-007 | Design: port config → Code: run.py |
| FR-029 | US-011 | Design: shared chromium → Code: python-backend.js |
| FR-030 | US-009 | Design: log capture → Code: python-backend.js |
