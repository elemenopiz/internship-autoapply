# SRS+SAD TASK-011: Production Readiness Quick Wins

**Version**: 1.0
**Date**: 2026-03-10
**Scope**: 5 targeted fixes covering secrets, exception handling, logging, thread safety, and temp file cleanup.

---

## QW-1: SECRET_KEY + Keyring for API Keys

### NFR-QW1: Secret Material Must Not Be Hardcoded

**Traces to**: PRODUCTION-READINESS.md -- "SECRET_KEY is hardcoded" and "API keys stored in plaintext config.json"

#### Acceptance Criteria

**AC-QW1.1 (Flask SECRET_KEY)**
- **Given** the application starts for the first time
- **When** no secret key file exists at `~/.autoapply/.flask_secret`
- **Then** a 32-byte random hex string is generated via `secrets.token_hex(32)`, written to `~/.autoapply/.flask_secret` (mode 0o600 on Unix), and used as `app.config["SECRET_KEY"]`

- **Given** the application starts on subsequent runs
- **When** `~/.autoapply/.flask_secret` exists
- **Then** the key is read from disk and used without regeneration

**AC-QW1.2 (API Key Keyring Storage)**
- **Given** the user saves an API key via Settings UI or Wizard
- **When** `save_config()` is called with an `LLMConfig` containing a non-empty `api_key`
- **Then** the key is stored in the OS keyring via `keyring.set_password("autoapply", "llm_api_key", key)` and `config.json` stores `api_key: ""` (empty)

- **Given** the application loads config
- **When** `load_config()` reads `config.json` with `api_key: ""`
- **Then** it retrieves the real key from `keyring.get_password("autoapply", "llm_api_key")` and populates `LLMConfig.api_key` in the returned object

**AC-QW1.3 (Keyring Fallback)**
- **Given** `keyring` is unavailable (import fails, or backend raises `NoKeyringError`)
- **When** save or load is called
- **Then** the key remains in `config.json` as plaintext (current behavior), and a WARNING is logged once: "keyring unavailable -- API key stored in plaintext config"

**AC-QW1.4 (Migration)**
- **Given** an existing `config.json` with a non-empty `api_key` field
- **When** `load_config()` runs and keyring is available
- **Then** the plaintext key is migrated into keyring, `config.json` is rewritten with `api_key: ""`, and a log message is emitted

#### Design

**File: `config/settings.py`**

Add module-level helper:

```python
import secrets
import logging

_logger = logging.getLogger(__name__)
_keyring_available: bool | None = None  # lazy-init sentinel

def _check_keyring() -> bool:
    """Return True if keyring backend is usable. Result is cached."""
    global _keyring_available
    if _keyring_available is not None:
        return _keyring_available
    try:
        import keyring
        # Probe: attempt a no-op get to verify backend
        keyring.get_password("autoapply", "__probe__")
        _keyring_available = True
    except Exception:
        _logger.warning("keyring unavailable -- API key stored in plaintext config")
        _keyring_available = False
    return _keyring_available

KEYRING_SERVICE = "autoapply"
KEYRING_KEY_NAME = "llm_api_key"
```

**Changes to `load_config()`:**
```python
def load_config() -> AppConfig | None:
    config_path = get_data_dir() / "config.json"
    if not config_path.exists():
        return None
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    config = AppConfig(**data)

    # Retrieve API key from keyring if available
    if _check_keyring():
        import keyring
        stored_key = keyring.get_password(KEYRING_SERVICE, KEYRING_KEY_NAME)
        if stored_key:
            config.llm.api_key = stored_key
        elif config.llm.api_key:
            # Migration: move plaintext key into keyring
            keyring.set_password(KEYRING_SERVICE, KEYRING_KEY_NAME, config.llm.api_key)
            config.llm.api_key = config.llm.api_key  # keep in-memory
            # Rewrite config.json without the key
            _save_config_raw(config, strip_api_key=True)
            _logger.info("Migrated API key from config.json to OS keyring")

    return config
```

**Changes to `save_config()`:**
```python
def save_config(config: AppConfig) -> None:
    # Store API key in keyring if possible
    if config.llm.api_key and _check_keyring():
        import keyring
        keyring.set_password(KEYRING_SERVICE, KEYRING_KEY_NAME, config.llm.api_key)

    _save_config_raw(config, strip_api_key=_check_keyring())

def _save_config_raw(config: AppConfig, strip_api_key: bool = False) -> None:
    data_dir = get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    config_path = data_dir / "config.json"
    dump = config.model_dump()
    if strip_api_key:
        dump.get("llm", {})["api_key"] = ""
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(dump, f, indent=2)
```

**File: `app.py`** -- Replace hardcoded SECRET_KEY:

```python
def _get_or_create_secret_key() -> str:
    secret_path = get_data_dir() / ".flask_secret"
    if secret_path.exists():
        return secret_path.read_text(encoding="utf-8").strip()
    import secrets as _secrets
    key = _secrets.token_hex(32)
    get_data_dir().mkdir(parents=True, exist_ok=True)
    secret_path.write_text(key, encoding="utf-8")
    try:
        secret_path.chmod(0o600)
    except OSError:
        pass  # Windows doesn't support Unix permissions
    return key

app = Flask(__name__)
app.config["SECRET_KEY"] = _get_or_create_secret_key()
```

**File: `run.py`** -- Ensure data dir exists before app import (already does, no change needed).

**Dependency**: Add `keyring` to `requirements.txt` / `pyproject.toml`.

---

## QW-2: Fix Swallowed Exceptions

### NFR-QW2: No Silent Exception Suppression

**Traces to**: PRODUCTION-READINESS.md -- "Swallowed exceptions hide bugs"

#### Acceptance Criteria

- **Given** any `except` block in the codebase
- **When** an exception is caught
- **Then** it is logged at minimum DEBUG level (never silently discarded)

#### Exhaustive Inventory of Swallowed Exceptions

Every site below currently has `except Exception: pass` (or equivalent) with **no logging**:

| # | File | Line | Context | Prescribed Log Level |
|---|------|------|---------|---------------------|
| 1 | `bot/bot.py` | 73 | `emit_func("feed_event", data)` fails | DEBUG -- SocketIO emit is best-effort |
| 2 | `bot/bot.py` | 83 | `db.save_feed_event(...)` fails | WARNING -- losing feed events matters |
| 3 | `bot/browser.py` | 124 | `self._context.close()` fails during cleanup | DEBUG -- teardown, expected on crash |
| 4 | `bot/browser.py` | 132 | `self._playwright.stop()` fails during cleanup | DEBUG -- teardown |
| 5 | `app.py` | 609 | `_login_proc.terminate()` fails | DEBUG -- process may already be dead |
| 6 | `app.py` | 651 | `proc.terminate()` in login_close | DEBUG -- process may already be dead |
| 7 | `app.py` | 713 | `tmp_cookies.unlink()` fails | DEBUG -- temp file cleanup best-effort |
| 8 | `core/ai_engine.py` | 141 | `validate_api_key` catches all, returns False | DEBUG -- caller needs bool, but should log reason |
| 9 | `core/ai_engine.py` | 248 | `resp.json()` parse in `_raise_api_error` | DEBUG -- JSON parse failure during error handling |
| 10 | `bot/search/linkedin.py` | 123 | `card.click()` fails | DEBUG -- UI interaction, card may be stale |
| 11 | `bot/search/indeed.py` | 126 | `title_link.click()` fails | DEBUG -- UI interaction, card may be stale |
| 12 | `bot/apply/workday.py` | 319 | Exception in dropdown option iteration | DEBUG -- DOM traversal, elements may be stale |
| 13 | `bot/apply/workday.py` | 415 | Exception in radio button iteration | DEBUG -- DOM traversal |
| 14 | `bot/apply/workday.py` | 431 | Exception reading error banner text | DEBUG -- error banner may disappear |
| 15 | `bot/apply/workday.py` | 461 | Exception in `_dismiss_overlays` | DEBUG -- overlay may not exist |
| 16 | `bot/apply/ashby.py` | 256 | Exception in dropdown option iteration | DEBUG -- DOM traversal |

#### Design

For each site, add `logger.debug("...: %s", e)` or `logger.warning(...)` per the table above.

Pattern for cleanup sites (items 3-7):
```python
except Exception as e:
    logger.debug("Failed to <action>: %s", e)
```

Pattern for item 2 (feed event save):
```python
except Exception as e:
    logger.warning("Failed to save feed event: %s", e)
```

Pattern for item 8 (validate_api_key):
```python
except Exception as e:
    logger.debug("API key validation failed for %s: %s", provider, e)
    return False
```

Files that need a `logger` added (currently use bare `logging.getLogger(__name__)` inline or have none):
- `bot/search/linkedin.py` -- already has logger
- `bot/search/indeed.py` -- already has logger
- `bot/apply/workday.py` -- already has logger
- `bot/apply/ashby.py` -- already has logger
- `bot/browser.py` -- already has logger
- `bot/bot.py` -- already has logger
- `core/ai_engine.py` -- already has logger
- `app.py` -- uses inline `logging.getLogger(__name__)` in some places; the swallowed sites in the `emit()` closure in `bot.py` already have `logger` in scope

---

## QW-3: Logging Configuration

### NFR-QW3: Structured Logging at Startup

**Traces to**: PRODUCTION-READINESS.md -- "No logging.basicConfig() call anywhere"

#### Acceptance Criteria

**AC-QW3.1**
- **Given** the application starts via `run.py main()`
- **When** before any Flask/SocketIO import
- **Then** `logging.basicConfig()` is called with:
  - Level: `DEBUG` if `AUTOAPPLY_DEBUG` env var is set, else `INFO`
  - Format: `"%(asctime)s [%(levelname)s] %(name)s: %(message)s"`
  - Handlers: StreamHandler (stderr) + FileHandler (`~/.autoapply/backend.log`, mode `"a"`, maxBytes=5MB via RotatingFileHandler)

**AC-QW3.2**
- **Given** the app is running
- **When** any module calls `logging.getLogger(__name__)`
- **Then** the message is formatted and routed to both console and file

#### Design

**File: `run.py`** -- Add at the top of `main()`, before any other imports:

```python
import logging
from logging.handlers import RotatingFileHandler

def _configure_logging(data_dir: Path) -> None:
    level = logging.DEBUG if os.environ.get("AUTOAPPLY_DEBUG") else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    root = logging.getLogger()
    root.setLevel(level)

    # Console handler
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    root.addHandler(console)

    # File handler (rotating, 5 MB, 3 backups)
    log_path = data_dir / "backend.log"
    file_handler = RotatingFileHandler(
        str(log_path), maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    root.addHandler(file_handler)
```

Call `_configure_logging(data_dir)` immediately after `data_dir = get_data_dir()` in `main()`, before the `from app import ...` line.

**No changes to `app.py`** -- inline `logging.getLogger(__name__)` calls will now inherit the root config.

---

## QW-4: Race Condition on `_bot_thread`

### NFR-QW4: Thread-Safe Access to Shared Mutable Globals

**Traces to**: PRODUCTION-READINESS.md -- "Race condition on `_bot_thread`"

#### Acceptance Criteria

**AC-QW4.1**
- **Given** multiple Flask request threads and the scheduler thread
- **When** they read or write `_bot_thread`
- **Then** access is serialized through a single `threading.Lock`

**AC-QW4.2**
- **Given** a lock is held
- **When** no other lock is acquired while holding it
- **Then** deadlock is impossible (single-lock design)

#### Design

**File: `app.py`**

Add a single lock for `_bot_thread`:

```python
_bot_lock = threading.Lock()
_bot_thread: threading.Thread | None = None
```

**Functions to modify** (all reads/writes of `_bot_thread` must be inside `with _bot_lock:`):

1. **`_scheduler_start_bot()`** -- wrap the check-and-start block:
```python
def _scheduler_start_bot() -> None:
    global _bot_thread
    with _bot_lock:
        if _bot_thread and _bot_thread.is_alive():
            return
        config = load_config()
        if config is None:
            return
        bot_state.start()

        def _run():
            # ... (unchanged)

        _bot_thread = threading.Thread(target=_run, daemon=True, name="bot-worker")
        _bot_thread.start()
```

2. **`_scheduler_stop_bot()`**:
```python
def _scheduler_stop_bot() -> None:
    with _bot_lock:
        thread = _bot_thread
    bot_state.stop()
    if thread and thread.is_alive():
        thread.join(timeout=10)
```

3. **`_is_bot_running()`**:
```python
def _is_bot_running() -> bool:
    with _bot_lock:
        return _bot_thread is not None and _bot_thread.is_alive()
```

4. **`bot_start()`** route:
```python
@app.route("/api/bot/start", methods=["POST"])
def bot_start():
    with _bot_lock:
        if _bot_thread and _bot_thread.is_alive():
            return jsonify({"error": "Bot is already running"}), 409
    # ... rest unchanged, calls _scheduler_start_bot() which acquires lock internally
```

Wait -- this creates nested locking if `bot_start` holds `_bot_lock` and then calls `_scheduler_start_bot` which also acquires it. Fix: `bot_start` should just call `_scheduler_start_bot` directly (which handles the is-alive check internally), or use a reentrant lock.

**Revised approach** -- use `threading.RLock()` (reentrant):

```python
_bot_lock = threading.RLock()
```

This allows the same thread to acquire the lock multiple times without deadlock. All access points wrap reads/writes of `_bot_thread` inside `with _bot_lock:`.

Alternatively (simpler): `bot_start` route delegates entirely to `_scheduler_start_bot()` without pre-checking -- the function already returns early if bot is alive. The 409 response needs the check though. So:

**Final approach**: Extract the check-and-start into `_scheduler_start_bot`, have it return a status:

```python
def _scheduler_start_bot() -> str:
    """Returns 'started', 'already_running', or 'no_config'."""
    global _bot_thread
    with _bot_lock:
        if _bot_thread and _bot_thread.is_alive():
            return "already_running"
        config = load_config()
        if config is None:
            return "no_config"
        bot_state.start()
        # ... create and start thread ...
        _bot_thread = threading.Thread(target=_run, daemon=True, name="bot-worker")
        _bot_thread.start()
        return "started"
```

Then `bot_start()` route:
```python
def bot_start():
    result = _scheduler_start_bot()
    if result == "already_running":
        return jsonify({"error": "Bot is already running"}), 409
    if result == "no_config":
        return jsonify({"error": "Configuration not found. Complete setup first."}), 400
    return jsonify({"status": "running"})
```

5. **`bot_stop()`** route:
```python
def bot_stop():
    bot_state.stop()
    with _bot_lock:
        thread = _bot_thread
    if thread and thread.is_alive():
        thread.join(timeout=10)
    return jsonify({"status": "stopped"})
```

**Lock acquisition order**: Only one lock (`_bot_lock`) exists for bot thread state. `_login_lock` is independent (protects `_login_proc` only). No function ever holds both, so no deadlock risk.

---

## QW-5: Temp File Leak in CSV Export

### NFR-QW5: Temporary Files Must Be Cleaned Up

**Traces to**: PRODUCTION-READINESS.md -- "Temp file leaked in export_applications"

#### Acceptance Criteria

**AC-QW5.1**
- **Given** a client requests `GET /api/applications/export`
- **When** the response has been fully sent
- **Then** the temporary CSV file is deleted from disk

**AC-QW5.2**
- **Given** the export request fails mid-stream
- **When** an exception occurs
- **Then** the temp file is still cleaned up

#### Design

**File: `app.py`** -- Replace the `export_applications` function:

**Option A (preferred): Use `io.BytesIO` -- no temp file at all.**

This requires `db.export_csv()` to accept a file-like object. Check if it does:

Looking at the current code, `db.export_csv(csv_path)` takes a `Path`. Changing the DB method is more invasive. Use Option B instead.

**Option B: Use `@after_this_request` to delete the file.**

```python
from flask import after_this_request

@app.route("/api/applications/export", methods=["GET"])
def export_applications():
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
    tmp.close()
    csv_path = Path(tmp.name)
    db.export_csv(csv_path)

    @after_this_request
    def _cleanup(response):
        try:
            csv_path.unlink(missing_ok=True)
        except OSError as e:
            logging.getLogger(__name__).debug("Failed to clean up temp CSV: %s", e)
        return response

    return send_file(
        csv_path,
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"applications_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
    )
```

Note: `after_this_request` runs after the response is sent, so the file is available during `send_file`.

**Caveat on gevent**: With gevent async mode, `send_file` streams the file. The `@after_this_request` callback fires after the response object is constructed but before streaming completes in some WSGI servers. To be safe, read the entire file into a `BytesIO` buffer, delete the file immediately, then return the buffer:

```python
import io

@app.route("/api/applications/export", methods=["GET"])
def export_applications():
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
    tmp.close()
    csv_path = Path(tmp.name)
    try:
        db.export_csv(csv_path)
        data = csv_path.read_bytes()
    finally:
        csv_path.unlink(missing_ok=True)

    buf = io.BytesIO(data)
    return send_file(
        buf,
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"applications_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
    )
```

This is the **recommended implementation** -- deterministic cleanup, no race condition, works with any WSGI server.

---

## Traceability Matrix

| NFR ID  | Quick Win | Source Files | Tests Needed | Traces To |
|---------|-----------|--------------|--------------|-----------|
| NFR-QW1 | SECRET_KEY + Keyring | `config/settings.py`, `app.py` | test_settings: secret key gen/load, keyring store/retrieve/fallback/migration | PRODUCTION-READINESS.md QW-1 |
| NFR-QW2 | Swallowed Exceptions | 9 files (16 sites -- see table) | Verify via grep: no `except ...: pass` without logging | PRODUCTION-READINESS.md QW-2 |
| NFR-QW3 | Logging Config | `run.py` | test_run: verify log file created, format correct | PRODUCTION-READINESS.md QW-3 |
| NFR-QW4 | Race Condition | `app.py` | test_api: concurrent start/stop/status requests | PRODUCTION-READINESS.md QW-4 |
| NFR-QW5 | Temp File Leak | `app.py` | test_api: export endpoint, verify no leftover temp files | PRODUCTION-READINESS.md QW-5 |

---

## Implementation Order

1. **QW-3** (Logging) -- must be first so all subsequent changes can rely on logging working
2. **QW-2** (Swallowed Exceptions) -- depends on logging being configured
3. **QW-1** (SECRET_KEY + Keyring) -- independent, touches settings + app
4. **QW-4** (Race Condition) -- touches app.py bot control
5. **QW-5** (Temp File Leak) -- smallest, independent change

## New Dependency

- `keyring` -- add to `requirements.txt`. Already available on PyPI, supports Windows Credential Locker, macOS Keychain, and Linux SecretService/KWallet.
