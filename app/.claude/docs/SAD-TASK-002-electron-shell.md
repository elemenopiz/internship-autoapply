# System Architecture Document

**Document ID**: SAD-TASK-002-electron-shell
**Version**: 1.0
**Date**: 2026-03-09
**Status**: approved
**Author**: Claude (System Engineer)
**SRS Reference**: SRS-TASK-002-electron-shell

---

## 1. Executive Summary

This architecture wraps AutoApply's existing Flask server in an Electron desktop shell. Electron's main process manages the app window, system tray, and Python backend lifecycle. Playwright reuses Electron's bundled Chromium via PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH, eliminating the double-Chromium overhead.

## 2. Architecture Overview

### 2.1 Component Diagram

```
┌─────────────────────── ELECTRON PROCESS (Node.js) ──────────────────────┐
│                                                                          │
│  ┌──────────────┐  ┌──────────────────┐  ┌───────────────────────────┐  │
│  │  main.js     │  │ python-backend.js │  │  tray.js                 │  │
│  │  App window  │  │ Spawn Python     │  │  System tray icon        │  │
│  │  Single inst │  │ Health check     │  │  Context menu            │  │
│  │  Lifecycle   │  │ Log capture      │  │  Show/Quit               │  │
│  └──────┬───────┘  │ Crash restart    │  └───────────────────────────┘  │
│         │          │ Port detection   │                                  │
│         │          │ Shared Chromium  │                                  │
│         │          └────────┬─────────┘                                  │
│         │                   │ child_process.spawn                        │
│  ┌──────▼───────────────────▼────────────────────────────────────────┐  │
│  │  BrowserWindow (Electron's Chromium)                              │  │
│  │  preload.js — contextBridge (openExternal, getVersion)           │  │
│  │  Loads: http://127.0.0.1:{port}  ← Flask SPA (unchanged)        │  │
│  └──────────────────────────┬────────────────────────────────────────┘  │
│                              │ HTTP + WebSocket                         │
│  ┌───────────────────────────▼───────────────────────────────────────┐  │
│  │  PYTHON CHILD PROCESS                                             │  │
│  │  run.py --no-browser                                              │  │
│  │  Flask + SocketIO on 127.0.0.1:{port}                            │  │
│  │  NEW: GET /api/health, POST /api/shutdown                        │  │
│  │  Playwright uses Electron's Chromium (via env var)               │  │
│  └───────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Process Communication

| From | To | Channel | Purpose |
|------|-----|---------|---------|
| main.js | python-backend.js | Module import | Lifecycle control |
| python-backend.js | Python process | child_process.spawn | Start/stop backend |
| python-backend.js | Flask | HTTP GET /api/health | Readiness probe |
| main.js | Flask | HTTP POST /api/shutdown | Graceful stop |
| BrowserWindow | Flask | HTTP + SocketIO | Existing SPA communication (unchanged) |
| preload.js | BrowserWindow | contextBridge | openExternal, getVersion |

### 2.3 Startup Sequence

```
1. User launches app
2. main.js: requestSingleInstanceLock() — if already running, focus existing window
3. main.js: Create splash BrowserWindow (400x300, splash.html)
4. python-backend.js: Find available port (try 5000, increment if busy)
5. python-backend.js: Set AUTOAPPLY_PORT and PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH env vars
6. python-backend.js: spawn('python', ['run.py', '--no-browser'])
7. python-backend.js: Poll GET /api/health every 500ms (timeout 30s)
8. On health OK: main.js destroys splash, creates main BrowserWindow (1280x850)
9. Main window loads http://127.0.0.1:{port}
10. Tray icon created
```

### 2.4 Shutdown Sequence

```
1. User closes window (or clicks Quit in tray)
2. main.js: 'before-quit' event fires
3. main.js: POST /api/shutdown to Flask
4. Flask: Initiates graceful shutdown (finish current request, stop SocketIO)
5. python-backend.js: Wait up to 5s for process exit
6. If still alive: force kill (SIGKILL on Unix, taskkill on Windows)
7. main.js: app.quit()
```

## 3. Interface Contracts

### 3.1 GET /api/health

**Purpose**: Readiness probe for Electron lifecycle management.
**Returns**: `{"status": "ok"}` with HTTP 200.
**No authentication required.**

### 3.2 POST /api/shutdown

**Purpose**: Graceful server shutdown triggered by Electron.
**Guard**: Only accepts requests from 127.0.0.1. Returns 403 for non-localhost.
**Returns**: `{"status": "shutting_down"}` with HTTP 200.
**Side effects**: Initiates Flask/SocketIO shutdown after response is sent.

### 3.3 run.py CLI Interface

**New arguments**:
| Arg | Type | Default | Description |
|-----|------|---------|-------------|
| --no-browser | flag | false | Skip auto-opening browser |

**New environment variables**:
| Var | Type | Default | Description |
|-----|------|---------|-------------|
| AUTOAPPLY_PORT | int | 5000 | Port for Flask to bind to |

### 3.4 python-backend.js Module

**Exports**:
| Function | Signature | Description |
|----------|-----------|-------------|
| startBackend | (options: {pythonPath?, scriptPath?, port?}) → Promise\<{port, process}> | Spawns Python, waits for health, returns port |
| stopBackend | () → Promise\<void> | Graceful shutdown with force-kill fallback |
| getPort | () → number | Returns the port the backend is running on |

### 3.5 preload.js — contextBridge API

**Exposes `window.electronAPI`**:
| Method | Signature | Description |
|--------|-----------|-------------|
| openExternal | (url: string) → void | Opens URL in system browser |
| getVersion | () → string | Returns app version from package.json |
| isElectron | () → true | Feature detection flag |

## 4. File Structure

```
AutoApply/
├── electron/                         # NEW — Electron desktop shell
│   ├── package.json                  # Electron deps + electron-builder config
│   ├── main.js                       # App window, lifecycle, single instance
│   ├── preload.js                    # contextBridge — minimal native API
│   ├── python-backend.js             # Python process manager
│   ├── tray.js                       # System tray icon and menu
│   ├── splash.html                   # Loading screen
│   ├── icons/
│   │   ├── icon.png                  # 512x512 app icon
│   │   ├── icon.ico                  # Windows icon
│   │   └── icon.icns                 # macOS icon
│   └── .gitignore                    # node_modules/, build/
├── app.py                            # MODIFIED — +2 endpoints
├── run.py                            # MODIFIED — --no-browser, port config
└── requirements.txt                  # UNCHANGED
```

## 5. ADRs

### ADR-005: Electron over PyWebView

**Status**: accepted
**Context**: Need standalone desktop app on 3 platforms.
**Decision**: Use Electron for its mature ecosystem, system tray, native menus, and packaging pipeline (electron-builder).
**Tradeoff**: Larger bundle than PyWebView, requires Node.js toolchain for development.

### ADR-006: Separate Chromium for Playwright (revised)

**Status**: superseded (original "shared Chromium" strategy abandoned)
**Context**: Electron bundles Chromium (~120MB). Playwright also downloads Chromium (~150MB). The original plan was to share Electron's Chromium with Playwright to save disk space.
**Problem**: Playwright persistent browser contexts require a custom user data directory and launch flags that are incompatible with Electron's embedded Chromium binary. The shared approach caused the browser to fail to launch (login button does nothing, bot cannot search).
**Decision**: Playwright must use its own Chromium installation. Users run `playwright install chromium` during setup. The ~150MB additional disk usage is accepted as a necessary tradeoff.
**Implication**: Do NOT set PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH to Electron's binary. Let Playwright manage its own browser.

### ADR-007: Python as Child Process (not embedded)

**Status**: accepted
**Context**: Could embed Python via pyodide (WASM) or link via N-API. Both have severe library compatibility issues (gevent, playwright, sqlite3).
**Decision**: Spawn Python as a child process via child_process.spawn(). Communicate via HTTP (existing Flask API).
**Rationale**: Zero changes to Python code. All existing tests continue to pass. HTTP is the proven interface.

### ADR-008: Port Auto-Detection

**Status**: accepted
**Context**: Port 5000 is used by AirPlay on macOS Monterey+. Hardcoding a port causes launch failures.
**Decision**: Try port 5000, if busy increment until finding an available port (max 5000-5010). Pass selected port via AUTOAPPLY_PORT env var.

## 6. Design Traceability Matrix

| Requirement | Type | Design Component | Interface | ADR |
|-------------|------|-----------------|-----------|-----|
| FR-019 | FR | main.js (BrowserWindow) | — | ADR-005 |
| FR-020 | FR | python-backend.js | startBackend() | ADR-007 |
| FR-021 | FR | python-backend.js | GET /api/health | — |
| FR-022 | FR | splash.html + main.js | — | — |
| FR-023 | FR | main.js + python-backend.js | POST /api/shutdown | — |
| FR-024 | FR | tray.js | — | — |
| FR-025 | FR | app.py | GET /api/health | — |
| FR-026 | FR | app.py | POST /api/shutdown | — |
| FR-027 | FR | run.py | --no-browser arg | — |
| FR-028 | FR | run.py | AUTOAPPLY_PORT env | ADR-008 |
| FR-029 | FR | python-backend.js | PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH | ADR-006 |
| FR-030 | FR | python-backend.js | Log file write | — |
| NFR-011 | NFR | Startup sequence | Health check 500ms poll | — |
| NFR-012 | NFR | Shared Chromium | — | ADR-006 |
| NFR-013 | NFR | electron-builder config | win/mac/linux targets | ADR-005 |
| NFR-014 | NFR | Shutdown sequence | Force kill fallback | — |
| NFR-015 | NFR | main.js | requestSingleInstanceLock | — |
| NFR-016 | NFR | main.js | nodeIntegration:false, contextIsolation:true | — |

## 7. Implementation Plan

| Order | Task | Description | Depends On | FR Coverage |
|-------|------|-------------|------------|-------------|
| 1 | IMPL-006 | Modify run.py: --no-browser flag + AUTOAPPLY_PORT | — | FR-027, FR-028 |
| 2 | IMPL-007 | Add /api/health and /api/shutdown to app.py | — | FR-025, FR-026 |
| 3 | IMPL-008 | Create electron/package.json and install deps | — | Foundation |
| 4 | IMPL-009 | Create electron/python-backend.js | IMPL-006 | FR-020, FR-021, FR-029, FR-030 |
| 5 | IMPL-010 | Create electron/splash.html | — | FR-022 |
| 6 | IMPL-011 | Create electron/preload.js | — | NFR-016 |
| 7 | IMPL-012 | Create electron/tray.js | — | FR-024 |
| 8 | IMPL-013 | Create electron/main.js (ties everything together) | IMPL-009 to 012 | FR-019, FR-023, NFR-015 |
| 9 | IMPL-014 | Add app icons | — | FR-019 |
| 10 | IMPL-015 | Configure electron-builder for 3 platforms | IMPL-013 | NFR-013 |
