# Software Requirements Specification — Addendum

**Document ID**: SRS-TASK-010-login-manual-electron
**Version**: 1.0
**Date**: 2026-03-10
**Status**: approved (retroactive — features already implemented)
**Author**: Claude (Requirements Analyst)
**PRD Reference**: PRD Sections 4.4, 5.3, 9.1

---

## 1. Purpose and Scope

### 1.1 Purpose
Specifies requirements for three features that were implemented but lacked formal FR coverage: the Platform Login Browser subsystem, the Apply Manually review action, and the Electron-only distribution model. This addendum fills the FR-069 numbering gap and assigns FR-080 and FR-081 for the remaining uncovered features.

### 1.2 Scope
The system SHALL provide: (a) a mechanism for users to open a visible Chrome browser window to authenticate with LinkedIn and Indeed, with lifecycle management (open, close, status polling, session detection); (b) a review-mode action allowing users to mark a job for manual application instead of automated submission; (c) distribution exclusively as an Electron desktop application wrapping the Flask backend.

The system SHALL NOT provide: login automation (auto-filling credentials), support for platforms other than LinkedIn and Indeed in the login browser, bulk manual-apply actions, or a standalone browser/web mode.

### 1.3 Definitions
| Term | Definition |
|------|-----------|
| Login browser | A system Chrome instance launched via subprocess with a shared `browser_profile` user data directory, used solely for manual platform authentication |
| Browser profile | The `~/.autoapply/browser_profile/` directory storing Chrome user data, cookies, and session state shared between the login browser and Playwright bot |
| Session cookie | A platform-specific cookie (e.g., LinkedIn `li_at`, Indeed `INDEED_CSRF_TOKEN`) whose presence indicates an active authenticated session |
| Manual apply | A review decision where the user opts to apply to a job themselves rather than having the bot submit automatically; saved with status `manual_required` |
| Review mode | Bot operating mode where each application pauses for user approval before submission |

---

## 2. Functional Requirements

### FR-069: Platform Login Browser

**Description**: The system SHALL provide API endpoints and UI controls to open a visible Chrome browser window for users to authenticate with LinkedIn or Indeed, track whether the browser is still open, close the browser on demand, and detect active login sessions by inspecting cookies.
**Priority**: P0
**Dependencies**: System Chrome installation, `~/.autoapply/browser_profile/` directory

#### 2.1 Sub-requirements

##### FR-069.1: Open Login Browser (`POST /api/login/open`)

**Acceptance Criteria**:
- **AC-069.1-1**: Given a valid request with `{"url": "https://www.linkedin.com/login"}`, When `POST /api/login/open` is called, Then the system launches Chrome via subprocess with `--user-data-dir` pointing to `~/.autoapply/browser_profile/`, opens the specified URL in a new window, and returns `{"status": "opening"}` with HTTP 200.
- **AC-069.1-2**: Given a request with a URL containing `linkedin.com` or `indeed.com`, When the domain is validated, Then the request is accepted.
- **AC-069.1-3**: Given a login browser is already open, When `POST /api/login/open` is called again, Then the existing browser process is terminated before launching the new one.
- **AC-069.1-4**: Given Chrome passes `--no-first-run`, `--no-default-browser-check`, and `--disable-default-apps` flags, When the browser opens, Then no first-run dialogs or default-browser prompts appear.

**Negative Cases**:
- **AC-069.1-N1**: Given a request with no `url` field, When `POST /api/login/open` is called, Then it returns HTTP 400 with `{"error": "url is required"}`.
- **AC-069.1-N2**: Given a request with a URL not containing `linkedin.com` or `indeed.com`, When `POST /api/login/open` is called, Then it returns HTTP 400 with `{"error": "Only LinkedIn and Indeed URLs are supported"}`.
- **AC-069.1-N3**: Given Chrome is not installed on the system, When `POST /api/login/open` is called, Then it returns HTTP 500 with `{"error": "Google Chrome not found. Please install Chrome."}`.
- **AC-069.1-N4**: Given Chrome fails to launch (e.g., permission error), When the subprocess raises an exception, Then it returns HTTP 500 with a descriptive error message.

---

##### FR-069.2: Close Login Browser (`POST /api/login/close`)

**Acceptance Criteria**:
- **AC-069.2-1**: Given a login browser is currently open, When `POST /api/login/close` is called, Then the Chrome process is terminated and `{"status": "closed"}` is returned with HTTP 200.
- **AC-069.2-2**: Given no login browser is currently open, When `POST /api/login/close` is called, Then `{"status": "already_closed"}` is returned with HTTP 200 (idempotent).

**Negative Cases**:
- **AC-069.2-N1**: Given the Chrome process cannot be terminated (e.g., already crashed), When `POST /api/login/close` is called, Then the exception is caught silently, the internal reference is cleared, and `{"status": "closed"}` is still returned.

---

##### FR-069.3: Login Browser Status (`GET /api/login/status`)

**Acceptance Criteria**:
- **AC-069.3-1**: Given a login browser process is running, When `GET /api/login/status` is called, Then it returns `{"open": true}`.
- **AC-069.3-2**: Given no login browser was opened, When `GET /api/login/status` is called, Then it returns `{"open": false}`.
- **AC-069.3-3**: Given the login browser process has exited (user closed Chrome manually), When `GET /api/login/status` is called, Then it detects the exit via `poll()`, clears the internal reference, and returns `{"open": false}`.

---

##### FR-069.4: Login Session Detection (`GET /api/login/sessions`)

**Acceptance Criteria**:
- **AC-069.4-1**: Given the user has logged into LinkedIn and the `li_at` cookie exists in the Chrome Cookies SQLite DB, When `GET /api/login/sessions` is called, Then it returns `{"linkedin": true, "indeed": false}` (or `true` for both if Indeed session also exists).
- **AC-069.4-2**: Given no browser profile cookies file exists, When `GET /api/login/sessions` is called, Then it returns `{"linkedin": false, "indeed": false}`.
- **AC-069.4-3**: Given Chrome locks the Cookies DB, When the endpoint reads cookies, Then it copies the DB to a temporary file first and reads from the copy.

**Negative Cases**:
- **AC-069.4-N1**: Given the Cookies DB copy or read fails, When an exception is raised, Then the endpoint returns `{"linkedin": false, "indeed": false}` without crashing.

---

##### FR-069.5: Platform Login UI (Wizard and Settings)

**Acceptance Criteria**:
- **AC-069.5-1**: Given the setup wizard is on the Platform Login step (step 5), When the user views the step, Then "Open LinkedIn Login" and "Open Indeed Login" buttons are displayed, each calling `openLoginBrowser()` with the appropriate URL.
- **AC-069.5-2**: Given a login browser is opened, When the UI updates, Then a "Close Browser" button appears and the platform login buttons are disabled.
- **AC-069.5-3**: Given the Settings panel is displayed, When the user scrolls to the Platform Login section, Then the same login/close buttons and session status indicators are shown.
- **AC-069.5-4**: Given a platform has an active session cookie, When `checkLoginSessions()` runs, Then a green "Connected" badge is displayed next to that platform.
- **AC-069.5-5**: Given a platform has no session cookie, When `checkLoginSessions()` runs, Then a gray "Not connected" badge is displayed.
- **AC-069.5-6**: Given the login browser is open, When the frontend polls `/api/login/status` every 2 seconds and detects it has closed, Then the UI reverts to the idle state and `checkLoginSessions()` runs to refresh session badges.

**Negative Cases**:
- **AC-069.5-N1**: Given the backend is unreachable, When `openLoginBrowser()` fails to fetch, Then an alert is shown to the user: "Could not connect to server."

---

### FR-080: Apply Manually Review Action

**Description**: The system SHALL provide a review-mode action allowing the user to mark a job for manual application. This saves the application with status `manual_required`, opens the job URL in an external browser, and advances the bot to the next job.
**Priority**: P1
**Dependencies**: Review mode (FR-048), BotState review decision mechanism

**Acceptance Criteria**:
- **AC-080-1**: Given the bot is in review mode and an application is awaiting review, When the user clicks the "Apply Manually" button, Then the job URL is opened in a new browser tab.
- **AC-080-2**: Given the "Apply Manually" button is clicked, When `POST /api/bot/review/manual` is called, Then `bot_state.set_review_decision("manual")` is invoked and the endpoint returns `{"status": "manual"}` with HTTP 200.
- **AC-080-3**: Given the bot receives a `"manual"` review decision, When it processes the decision, Then it creates an `ApplyResult` with `success=False` and `status="manual_required"`, saves the application to the database with status `manual_required`, and emits a feed event with the message "Marked for manual apply: {title} at {company}".
- **AC-080-4**: Given the application is saved as `manual_required`, When the bot loop continues, Then it proceeds to the next job without attempting automated submission.
- **AC-080-5**: Given the review card is displayed, When the user views the action buttons, Then "Apply Manually" appears as a warning-styled button between "Edit & Apply" and "Skip".

**Negative Cases**:
- **AC-080-N1**: Given no application is currently awaiting review, When `POST /api/bot/review/manual` is called, Then it returns HTTP 409 with `{"error": "No application awaiting review"}`.
- **AC-080-N2**: Given the application has no `apply_url`, When the user clicks "Apply Manually", Then no new tab is opened (the `window.open` call is guarded by an `if (url)` check), but the review decision is still submitted.

---

### FR-081: Electron-Only Distribution

**Description**: The system SHALL be distributed exclusively as an Electron desktop application. The Flask backend runs as a child process managed by Electron, with no standalone browser/web access mode. The Electron shell provides window management, system tray integration, and native OS lifecycle handling.
**Priority**: P0
**Dependencies**: Electron 33+, Node.js, `python-backend.js` subprocess manager

**Acceptance Criteria**:
- **AC-081-1**: Given the application is launched, When Electron's `app.ready` event fires, Then a splash window is shown, the Flask backend is started via `startBackend()`, and upon successful backend startup a main window loads `http://127.0.0.1:{port}`.
- **AC-081-2**: Given the backend fails to start, When `startBackend()` throws, Then an error dialog is shown with a descriptive message and the application quits.
- **AC-081-3**: Given the main window is ready, When `ready-to-show` fires, Then the splash window closes and the main window is shown and focused.
- **AC-081-4**: Given the user closes the main window, When the close event fires, Then the window is hidden (minimized to tray) instead of destroyed, unless `app.isQuitting` is true.
- **AC-081-5**: Given `app.before-quit` fires, When the shutdown sequence runs, Then the system tray is destroyed, the Flask backend is stopped via `stopBackend()`, and the process exits with code 0.
- **AC-081-6**: Given another instance of the application is launched, When `requestSingleInstanceLock()` fails, Then the second instance quits immediately and the first instance's window is restored and focused.
- **AC-081-7**: Given the Electron `package.json`, When distribution is built, Then `electron-builder` produces platform-specific packages (`dist:win`, `dist:mac`, `dist:linux`) with no standalone web server entry point.
- **AC-081-8**: Given the Flask backend binds to `127.0.0.1`, When the app is running, Then the backend is not accessible from other machines on the network.

**Negative Cases**:
- **AC-081-N1**: Given the user attempts to access the Flask backend URL directly in a standalone browser, When the Electron app is not running, Then no server is available (the backend only runs as a child process of Electron).
- **AC-081-N2**: Given macOS dock behavior, When all windows are closed but the process is still running, Then `window-all-closed` does not quit the app on Darwin (standard macOS behavior).

---

## 3. Non-Functional Requirements

### NFR-030: Login Browser Thread Safety
**Description**: The login browser process reference (`_login_proc`) SHALL be protected by a threading lock (`_login_lock`) to prevent race conditions when multiple API requests arrive concurrently.
**Metric**: No data races under concurrent access to login endpoints.
**Priority**: P0

### NFR-031: Login Browser Startup Latency
**Description**: The `POST /api/login/open` endpoint SHALL return within 2 seconds. The actual Chrome window may appear asynchronously after the response.
**Metric**: API response time < 2s on standard hardware.
**Priority**: P1

### NFR-032: Session Detection Non-Destructive Read
**Description**: The `GET /api/login/sessions` endpoint SHALL NOT modify or lock the Chrome Cookies database. It SHALL copy the DB file to a temporary location before reading.
**Metric**: Chrome browsing is unaffected while session detection runs.
**Priority**: P0

### NFR-033: Single Instance Enforcement
**Description**: Only one instance of the Electron application SHALL run at a time. Subsequent launch attempts SHALL activate the existing instance.
**Metric**: `requestSingleInstanceLock()` prevents duplicate instances 100% of the time.
**Priority**: P0

### NFR-034: Graceful Shutdown
**Description**: The Electron app SHALL stop the Flask backend before exiting. On Windows, process termination SHALL use `taskkill` since `SIGTERM` is not supported.
**Metric**: No orphaned Python processes after app exit.
**Priority**: P0

---

## 4. Out of Scope

- **Login automation** — The system does not auto-fill usernames or passwords
- **Platforms beyond LinkedIn/Indeed** — The login browser URL allowlist is restricted to these two domains
- **Bulk manual-apply** — No batch endpoint for marking multiple jobs as manual
- **Standalone web mode** — No `flask run` or browser-only access; Electron is the sole distribution channel
- **Login session refresh** — No automatic re-authentication when sessions expire

---

## 5. Traceability Seeds

| Req ID | Source Files | Test Files | UI Location |
|--------|-------------|-----------|-------------|
| FR-069.1 | `app.py` (lines 578-636), `app.py` `_find_system_chrome()` (lines 552-570) | `tests/test_login_api.py` | Wizard step 5, Settings > Platform Login |
| FR-069.2 | `app.py` (lines 639-653) | `tests/test_login_api.py` | Wizard "Close Browser" button, Settings "Close Browser" button |
| FR-069.3 | `app.py` (lines 656-667) | `tests/test_login_api.py` | Frontend `pollLoginStatus()` (2s interval) |
| FR-069.4 | `app.py` (lines 670-714) | `tests/test_login_api.py` | Session status badges (`#linkedin-session-status`, `#indeed-session-status`) |
| FR-069.5 | `templates/index.html` — `openLoginBrowser()`, `closeLoginBrowser()`, `checkLoginSessions()`, `updateLoginUI()` | `tests/test_login_api.py` | Wizard step 5, Settings > Platform Login |
| FR-080 | `app.py` (lines 272-278), `bot/bot.py` (lines 190-209), `bot/state.py` `set_review_decision()` | `tests/test_api.py` | Review card "Apply Manually" button (`#review-manual-submit`) |
| FR-081 | `electron/main.js`, `electron/python-backend.js`, `electron/package.json`, `electron/tray.js`, `electron/preload.js` | — (manual/E2E) | Electron shell (splash, main window, system tray) |
