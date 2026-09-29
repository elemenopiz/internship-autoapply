# SRS+SAD TASK-012: Production Readiness Phase B

**Version**: 1.0
**Date**: 2026-03-10
**Scope**: 5 medium-effort fixes covering CI, API auth, input validation, error handling, and CORS.

---

## ME-1: GitHub Actions CI Pipeline

### NFR-ME1: Automated Quality Checks on Every Push

**Traces to**: PRODUCTION-READINESS.md ME-1

#### Acceptance Criteria

**AC-ME1.1**
- **Given** a developer pushes to `master` or opens a pull request
- **When** GitHub Actions triggers
- **Then** a CI workflow runs: (1) `pytest tests/` (2) `ruff check .` (3) exit with failure if any step fails

**AC-ME1.2**
- **Given** the CI workflow runs
- **When** all checks pass
- **Then** the workflow exits with code 0 and shows a green checkmark

**AC-ME1.3 (Negative)**
- **Given** a test fails or a lint error exists
- **When** the workflow runs
- **Then** it exits non-zero and shows a red X

#### Design

**New file: `.github/workflows/ci.yml`**

```yaml
name: CI
on:
  push:
    branches: [master]
  pull_request:
    branches: [master]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - run: pip install -r requirements.txt
      - run: pip install ruff pytest
      - run: ruff check .
      - run: pytest tests/ -x -q
```

No `mypy` for now — the codebase doesn't use strict typing consistently. Can add later.

---

## ME-2: API Authentication (Localhost Token)

### NFR-ME2: API Endpoints Protected by Shared Secret

**Traces to**: PRODUCTION-READINESS.md ME-2

#### Acceptance Criteria

**AC-ME2.1**
- **Given** the Flask app starts
- **When** a token file `~/.autoapply/.api_token` does not exist
- **Then** a 32-byte random hex token is generated, written to the file (mode 0o600), and used for auth

**AC-ME2.2**
- **Given** a request arrives at any `/api/*` endpoint (except `/api/health`)
- **When** the `Authorization` header is missing or does not match `Bearer <token>`
- **Then** the response is `401 {"error": "Unauthorized"}` with no data leakage

**AC-ME2.3**
- **Given** Electron starts the Python backend
- **When** it reads `~/.autoapply/.api_token`
- **Then** it includes `Authorization: Bearer <token>` on all fetch calls to the backend

**AC-ME2.4 (Backward Compatibility)**
- **Given** the app is run in development mode (`AUTOAPPLY_DEV=1` env var)
- **When** any request arrives without auth
- **Then** auth is bypassed (development convenience)

**AC-ME2.5 (Negative)**
- **Given** a valid token exists
- **When** a request uses an incorrect token
- **Then** the response is `401 {"error": "Unauthorized"}`

#### Design

**File: `app.py`**

Add `_get_or_create_api_token()` (same pattern as `_get_or_create_secret_key()`):

```python
def _get_or_create_api_token() -> str:
    token_path = get_data_dir() / ".api_token"
    if token_path.exists():
        return token_path.read_text(encoding="utf-8").strip()
    token = _secrets_mod.token_hex(32)
    get_data_dir().mkdir(parents=True, exist_ok=True)
    token_path.write_text(token, encoding="utf-8")
    try:
        token_path.chmod(0o600)
    except OSError:
        pass
    return token

_api_token = _get_or_create_api_token()
```

Add `@app.before_request` middleware:

```python
@app.before_request
def _check_auth():
    if os.environ.get("AUTOAPPLY_DEV"):
        return None
    if request.path == "/" or request.path.startswith("/static"):
        return None
    if request.path == "/api/health":
        return None
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {_api_token}":
        return jsonify({"error": "Unauthorized"}), 401
```

**File: `electron/renderer.js` (or wherever fetch calls are made)**

Read token from file and include in headers. Since the frontend is served by Flask itself (same origin), the token is injected into the HTML template as a meta tag or JS variable at render time.

Simpler approach: inject token into `index.html` via Flask template variable:
```python
@app.route("/")
def index():
    return render_template("index.html", api_token=_api_token)
```

In `index.html`, all fetch calls include:
```javascript
headers: { "Authorization": "Bearer {{ api_token }}" }
```

---

## ME-3: Input Validation on API Endpoints

### NFR-ME3: All POST/PUT/PATCH Endpoints Validate Input

**Traces to**: PRODUCTION-READINESS.md ME-3

#### Acceptance Criteria

**AC-ME3.1**
- **Given** `PUT /api/config` receives invalid JSON that fails Pydantic validation
- **When** `AppConfig(**merged)` raises `ValidationError`
- **Then** the response is `400 {"error": "Invalid configuration: <details>"}` (not 500)

**AC-ME3.2**
- **Given** `PATCH /api/applications/<id>` receives a `status` value not in the allowed set
- **When** the request is processed
- **Then** the response is `400 {"error": "Invalid status value"}`

**AC-ME3.3**
- **Given** any POST/PUT/PATCH endpoint receives a non-JSON body or `Content-Type` is not `application/json`
- **When** `request.get_json()` returns `None`
- **Then** the response is `400 {"error": "Request body must be valid JSON"}`

**AC-ME3.4 (Negative)**
- **Given** `PUT /api/config` receives extra unknown fields
- **When** Pydantic parses the data
- **Then** extra fields are ignored (Pydantic default behavior), not an error

#### Design

**File: `app.py`**

1. Wrap `update_config()` in try/except for ValidationError:

```python
from pydantic import ValidationError

@app.route("/api/config", methods=["PUT"])
def update_config():
    data = request.get_json()
    if data is None:
        return jsonify({"error": "Request body must be valid JSON"}), 400
    try:
        # ... existing merge logic ...
        config = AppConfig(**merged)
    except ValidationError as e:
        return jsonify({"error": f"Invalid configuration: {e.error_count()} validation errors"}), 400
    save_config(config)
    return jsonify({"success": True})
```

2. Add status allowlist to `update_application()`:

```python
VALID_STATUSES = {"applied", "reviewed", "interviewing", "rejected", "accepted", "withdrawn", "saved"}

@app.route("/api/applications/<int:app_id>", methods=["PATCH"])
def update_application(app_id: int):
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body is required"}), 400
    status = data.get("status")
    if status and status not in VALID_STATUSES:
        return jsonify({"error": f"Invalid status. Must be one of: {', '.join(sorted(VALID_STATUSES))}"}), 400
    # ... rest unchanged
```

3. Add null-body check to routes that currently skip it: `bot_review_edit`, `bot_review_action`.

---

## ME-4: Hardened Error Handlers

### NFR-ME4: No Internal Details Leaked in Error Responses

**Traces to**: PRODUCTION-READINESS.md ME-4

#### Acceptance Criteria

**AC-ME4.1**
- **Given** an unhandled exception occurs in any route
- **When** the generic exception handler fires
- **Then** the response body contains only `{"error": "Internal server error"}` and status 500 — no stack trace, no exception class name, no file paths

**AC-ME4.2**
- **Given** the exception handler fires
- **When** the error is logged
- **Then** the full traceback is written to the server log at ERROR level

**AC-ME4.3**
- **Given** a `400 Bad Request` is raised by Flask (e.g., malformed JSON)
- **When** the error handler fires
- **Then** the response is `400 {"error": "Bad request"}` in JSON

#### Design

**File: `app.py`**

Update the generic exception handler:

```python
@app.errorhandler(Exception)
def handle_exception(e):
    if hasattr(e, "code") and isinstance(e.code, int):
        # HTTP exceptions (404, 405, etc.) — safe to expose the description
        return jsonify({"error": e.description if hasattr(e, 'description') else str(e)}), e.code
    # Unexpected exceptions — log full traceback, return generic message
    logger.exception("Unhandled exception")
    return jsonify({"error": "Internal server error"}), 500
```

Add explicit 400 handler:

```python
@app.errorhandler(400)
def handle_400(e):
    return jsonify({"error": "Bad request"}), 400
```

---

## ME-5: CORS Lockdown

### NFR-ME5: CORS Restricted to Localhost Origins

**Traces to**: PRODUCTION-READINESS.md ME-5

#### Acceptance Criteria

**AC-ME5.1**
- **Given** the Flask+SocketIO app is configured
- **When** `cors_allowed_origins` is set
- **Then** it allows only `http://localhost:*` and `http://127.0.0.1:*` origins (not `*`)

**AC-ME5.2 (Negative)**
- **Given** a WebSocket connection attempt from `http://evil.com`
- **When** the origin header is checked
- **Then** the connection is rejected

#### Design

**File: `app.py`**

Replace:
```python
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="gevent")
```

With:
```python
_ALLOWED_ORIGINS = [
    "http://localhost:*",
    "http://127.0.0.1:*",
]
socketio = SocketIO(app, cors_allowed_origins=_ALLOWED_ORIGINS, async_mode="gevent")
```

Note: Flask-SocketIO supports wildcard port matching in origin strings natively.

---

## Traceability Matrix

| NFR ID   | Item | Source Files | Tests Needed |
|----------|------|-------------|--------------|
| NFR-ME1  | CI Pipeline | `.github/workflows/ci.yml` | Manual: push and verify workflow runs |
| NFR-ME2  | API Auth | `app.py`, `templates/index.html` | test_api: unauthorized returns 401, valid token passes, health exempt, dev mode bypass |
| NFR-ME3  | Input Validation | `app.py` | test_api: invalid config returns 400, invalid status returns 400, null body returns 400 |
| NFR-ME4  | Error Handlers | `app.py` | test_api: unhandled exception returns generic 500 JSON, 400 handler returns JSON |
| NFR-ME5  | CORS Lockdown | `app.py` | test_api: verify cors_allowed_origins is not "*" |

## Implementation Order

1. **ME-4** (Error Handlers) — smallest, foundational for testing other changes
2. **ME-3** (Input Validation) — catches errors that would otherwise hit ME-4
3. **ME-5** (CORS) — one-line change, independent
4. **ME-2** (API Auth) — largest, touches app.py + frontend
5. **ME-1** (CI Pipeline) — new file, no code dependencies, can verify all tests pass
