# Security

## Overview

AutoApply is a desktop application that runs a local Flask server bound to `127.0.0.1`. It does not accept connections from the network. Security measures protect against local threats, credential leakage, and common web vulnerabilities.

---

## Authentication

### Bearer Token

All API requests require a Bearer token:

```
Authorization: Bearer <token>
```

- **Generation**: A cryptographically random token is generated on first run using `secrets.token_urlsafe(32)`.
- **Storage**: Saved to `~/.autoapply/.api_token` with file permissions restricted to the current user.
- **Injection**: The Electron frontend receives the token via `window.__apiToken`, injected into `index.html` by Jinja2 at template render time.
- **Validation**: Checked in `@app.before_request` for all `/api/*` routes.
- **Exclusions**: The health endpoint (`GET /api/health`) and static file serving are excluded from auth checks.

### Development Bypass

Setting `AUTOAPPLY_DEV=1` disables authentication checks. This is used in development and testing. Never set this in production.

---

## API Key Storage

LLM API keys (Anthropic, OpenAI, Google, DeepSeek) are stored securely using the OS keyring:

| Platform | Keyring Backend |
|----------|----------------|
| Windows | Windows Credential Locker (via `keyring` library) |
| macOS | macOS Keychain (via `keyring` library) |
| Linux | SecretService API -- GNOME Keyring or KWallet (via `keyring` library) |

### Storage Flow

1. User enters API key in the Settings UI or setup wizard.
2. `PUT /api/config` receives the key.
3. If keyring is available, the key is stored in the keyring under service `autoapply` with username `llm_api_key`.
4. The `config.json` file stores only a placeholder: `"api_key": "***"`.
5. If keyring is unavailable, the key is stored in plaintext in `config.json` as a fallback.

### Auto-Migration

On startup, if a plaintext API key is found in `config.json` and the keyring is available:
1. The key is written to the keyring.
2. The plaintext key in `config.json` is replaced with `"***"`.
3. A log message records the migration.

---

## Security Headers

All responses include security headers set in the `@app.after_request` handler:

| Header | Value | Purpose |
|--------|-------|---------|
| `X-Content-Type-Options` | `nosniff` | Prevents MIME type sniffing |
| `X-Frame-Options` | `DENY` | Prevents clickjacking via iframes |
| `X-XSS-Protection` | `1; mode=block` | Enables browser XSS filter |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | Limits referrer information leakage |
| `Cache-Control` | `no-store, no-cache, must-revalidate` | Prevents caching of API responses |

---

## CORS

Cross-Origin Resource Sharing is locked to localhost origins only:

```python
CORS(app, origins=["http://127.0.0.1:*", "http://localhost:*"])
```

This prevents any external website from making API requests to the local AutoApply server, even if the Bearer token were leaked.

---

## Rate Limiting

Requests are rate-limited using a **token bucket** algorithm per client IP address:

| Bucket | Capacity | Refill Rate | Applies To |
|--------|----------|-------------|------------|
| Bot | 10/min | 1 token every 6s | `POST /api/bot/*` |
| Write | 30/min | 1 token every 2s | `PUT`, `POST`, `PATCH`, `DELETE` operations |
| Read | 60/min | 1 token every 1s | `GET` operations |

When a bucket is exhausted, the server returns:

```
HTTP/1.1 429 Too Many Requests
Retry-After: 6
Content-Type: application/json

{"error": "Rate limit exceeded. Try again in 6 seconds."}
```

The `Retry-After` header indicates the number of seconds until the next token is available.

---

## Input Validation

### Pydantic Models

All configuration input is validated through Pydantic v2 models defined in `config/settings.py`. Invalid data returns `422 Unprocessable Entity` with detailed error messages.

### Status Allowlist

The `PATCH /api/applications/:id` endpoint validates the `status` field against an explicit allowlist:

```python
ALLOWED_STATUSES = {"applied", "reviewing", "rejected", "interview", "offer", "closed"}
```

Any status value not in the allowlist is rejected with `400 Bad Request`.

### Request Size Limit

Flask is configured with a 16 MB maximum request size:

```python
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
```

Requests exceeding this limit receive `413 Request Entity Too Large`.

---

## Path Traversal Protection

Endpoints that accept filenames (experience file upload/download, resume/description serving) use `_is_safe_path()` to validate paths:

```python
def _is_safe_path(base_dir, user_path):
    """Ensure the resolved path is within the allowed base directory."""
    resolved = os.path.realpath(os.path.join(base_dir, user_path))
    return resolved.startswith(os.path.realpath(base_dir))
```

This prevents directory traversal attacks like `../../etc/passwd`. Filenames are additionally validated against an allowlist regex:

```python
SAFE_FILENAME = re.compile(r'^[a-zA-Z0-9_\-][a-zA-Z0-9_\-. ]{0,254}$')
```

---

## URL Validation

The `POST /api/login/open` endpoint validates the URL parameter:

1. **Parses** the URL using `urllib.parse.urlparse`.
2. **Validates scheme**: Must be `http` or `https`.
3. **Checks domain** against an allowlist of known platforms:
   - `linkedin.com`, `www.linkedin.com`
   - `indeed.com`, `www.indeed.com`
   - `greenhouse.io`, `boards.greenhouse.io`
   - `lever.co`, `jobs.lever.co`
   - `myworkdayjobs.com`, `myworkday.com`
   - `ashbyhq.com`, `jobs.ashbyhq.com`

Any URL not matching an allowed domain is rejected with `400 Bad Request`.

---

## Error Handling

### No Stack Traces

In production (when `AUTOAPPLY_DEV` is not set), error responses never expose stack traces or internal details:

```python
@app.errorhandler(500)
def internal_error(e):
    return jsonify({"error": "Internal server error"}), 500
```

Global error handlers are registered for all common HTTP error codes (400, 401, 403, 404, 405, 413, 422, 429, 500).

### Generic Messages

Error messages are generic and user-facing. Internal details are logged server-side only:

```python
# Bad: exposes internals
{"error": "sqlite3.OperationalError: database is locked"}

# Good: generic message
{"error": "Failed to save application. Please try again."}
```

---

## Dependency Scanning

### pip-audit in CI

The CI security job runs [pip-audit](https://pypi.org/project/pip-audit/) to check all Python dependencies against the [OSV](https://osv.dev/) vulnerability database:

```bash
pip-audit --strict
```

If any known vulnerabilities are found, the CI job fails, blocking the PR from merging.

### Dependabot

GitHub Dependabot is configured (`.github/dependabot.yml`) to automatically:
- Check for outdated Python packages (weekly).
- Check for outdated npm packages (weekly).
- Open pull requests with version bumps.
- Flag packages with known security advisories.

### Dependency Pinning

All Python dependencies are pinned in `pyproject.toml` with minimum versions to ensure reproducible builds and prevent supply chain attacks from unexpected upgrades:

```toml
[project]
dependencies = [
    "flask>=3.0.0",
    "flask-socketio>=5.3.0",
    "pydantic>=2.0.0",
    # ...
]
```

---

## Flask SECRET_KEY

The Flask `SECRET_KEY` (used for session signing) is:

1. **Generated** using `secrets.token_hex(32)` on first run.
2. **Persisted** to `~/.autoapply/.flask_secret` for consistency across restarts.
3. **File permissions** restricted to the current user.
4. **Never logged** or exposed in API responses.

---

## Network Binding

The Flask server binds exclusively to the loopback interface:

```python
socketio.run(app, host='127.0.0.1', port=port)
```

This ensures:
- The server is not accessible from other machines on the network.
- Only local processes (the Electron shell, local development tools) can connect.
- No firewall rules are needed.

---

## Security Checklist

| Control | Status | Implementation |
|---------|--------|----------------|
| API authentication | Implemented | Bearer token, auto-generated |
| API key encryption at rest | Implemented | OS keyring with plaintext fallback |
| Security headers | Implemented | 5 headers on all responses |
| CORS restriction | Implemented | Localhost-only origins |
| Rate limiting | Implemented | Token bucket, 3 tiers |
| Input validation | Implemented | Pydantic, allowlists, size limits |
| Path traversal protection | Implemented | `_is_safe_path()` + filename regex |
| URL validation | Implemented | Domain allowlist on login endpoint |
| Error handling | Implemented | No stack traces, generic messages |
| Dependency scanning | Implemented | pip-audit in CI, Dependabot |
| Secret key management | Implemented | Random generation, file persistence |
| Network binding | Implemented | 127.0.0.1 only |
| Code signing | Not yet | Tracked in Roadmap |
