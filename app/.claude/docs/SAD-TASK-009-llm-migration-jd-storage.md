# System Architecture Document

**Document ID**: SAD-TASK-009-llm-migration-jd-storage
**Version**: 1.0
**Date**: 2026-03-10
**Status**: approved (retroactive)
**Author**: Claude (System Engineer)
**SRS Reference**: SRS-TASK-009-llm-migration-jd-storage

---

## 1. Executive Summary

This document describes two changes shipped together:

1. **LLM API Migration** -- The AI engine was rewritten from a Claude Code CLI subprocess model (`invoke_claude_code()` via `claude --print`) to a multi-provider HTTP API model (`invoke_llm()` via `requests`). The system now supports Anthropic, OpenAI, Google Gemini, and DeepSeek as interchangeable LLM providers, configured through a new `LLMConfig` model and validated through a dedicated API endpoint.

2. **Job Description Storage** -- The bot now persists every passing job's full description as a self-contained HTML file on disk and records the path in the `applications` table. This supports interview preparation and application review from the detail view.

Both changes are fully shipped and tested. This SAD is retroactive documentation of the as-built architecture.

---

## 2. Architecture Overview

### 2.1 Component Diagram

```
┌──────────────┐      ┌──────────────┐      ┌──────────────────────────┐
│   app.py     │─────>│ ai_engine.py │─────>│ Provider HTTP APIs       │
│ (Flask API)  │      │ (abstraction)│      │  - api.anthropic.com     │
│              │      │ [requests]   │      │  - api.openai.com        │
└──────┬───────┘      └──────┬───────┘      │  - generativelanguage    │
       │                     │              │    .googleapis.com       │
       │                     │              │  - api.deepseek.com      │
       │                     ▼              └──────────────────────────┘
       │              ┌──────────────┐
       │              │ resume_      │
       │              │ renderer.py  │
       │              │ [ReportLab]  │
       │              └──────────────┘
       │
       ▼
┌──────────────┐      ┌──────────────┐      ┌──────────────────────────┐
│  bot.py      │─────>│ database.py  │─────>│ SQLite                   │
│ (bot loop)   │      │ (persistence)│      │  applications table      │
│              │      │              │      │  +description_path col   │
└──────┬───────┘      └──────────────┘      └──────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│ ~/.autoapply/profile/                │
│ ├── experiences/*.txt                │
│ ├── resumes/*.md, *.pdf              │
│ ├── cover_letters/*.txt              │
│ └── job_descriptions/*.html   [NEW]  │
└──────────────────────────────────────┘
```

### 2.2 Data Flow -- LLM Invocation

1. Caller passes `prompt` + `llm_config` (LLMConfig) to `invoke_llm()`.
2. `invoke_llm()` reads `llm_config.provider`, `llm_config.api_key`, `llm_config.model`.
3. Falls back to `DEFAULT_MODELS[provider]` if model is empty.
4. Dispatches to `_call_llm()` which routes to one of three internal functions:
   - `_call_anthropic()` for provider `"anthropic"`
   - `_call_openai_compatible()` for providers `"openai"` and `"deepseek"`
   - `_call_google()` for provider `"google"`
5. Each function constructs provider-specific headers, request body, and endpoint URL.
6. Makes synchronous HTTP POST via `requests.post()` with configurable timeout.
7. Parses provider-specific response JSON to extract generated text.
8. Returns stripped text string to caller.

### 2.3 Data Flow -- Job Description Storage

1. After `score_job()` passes filter, bot calls `_save_job_description(scored, profile_dir)`.
2. Constructs filename from `external_id[:8]`, sanitized company name, and UTC date.
3. Builds self-contained HTML with escaped title, company, location, salary, description, and source URL.
4. Writes to `~/.autoapply/profile/job_descriptions/{filename}.html`.
5. Returns `Path` to saved file (or `None` on failure).
6. Path is passed through `_save_application()` to `db.save_application(description_path=...)`.
7. Frontend retrieves via `GET /api/applications/:id/description` which calls `send_file()`.

### 2.4 Layer Architecture

| Layer | Component | Responsibility |
|-------|-----------|----------------|
| API | `app.py` | `/api/ai/validate`, `/api/applications/:id/description`, `ai_available` in status |
| Service | `core/ai_engine.py` | Multi-provider LLM abstraction, document generation |
| Service | `bot/bot.py` | JD storage pipeline, HTML generation |
| Infrastructure | `requests` | HTTP calls to LLM provider APIs |
| Infrastructure | `core/resume_renderer.py` | PDF rendering via ReportLab |
| Data | `db/database.py` | `description_path` column, migration |
| Data | `config/settings.py` | `LLMConfig` model |
| Data | `~/.autoapply/profile/` | File I/O for all artifacts |

---

## 3. Component Catalog

### 3.1 core/ai_engine.py

| Symbol | Kind | Purpose |
|--------|------|---------|
| `DEFAULT_MODELS` | dict | Maps provider string to default model ID |
| `API_ENDPOINTS` | dict | Maps provider string to API URL (Google uses `{model}` placeholder) |
| `RESUME_PROMPT` | str | Template with placeholders for experience, JD, profile fields |
| `COVER_LETTER_PROMPT` | str | Template with placeholders for experience, JD, name, bio |
| `check_ai_available(llm_config)` | function | Returns `True` if provider and api_key are set |
| `validate_api_key(provider, api_key, model)` | function | Makes minimal test request, returns bool |
| `invoke_llm(prompt, llm_config, timeout_seconds)` | function | Public entry point for LLM generation |
| `_call_llm(provider, api_key, model, prompt, timeout)` | function | Internal router to provider-specific functions |
| `_call_anthropic(api_key, model, prompt, timeout)` | function | Anthropic Messages API caller |
| `_call_openai_compatible(provider, api_key, model, prompt, timeout)` | function | OpenAI/DeepSeek chat completions caller |
| `_call_google(api_key, model, prompt, timeout)` | function | Google Gemini generateContent caller |
| `_raise_api_error(provider, resp)` | function | Extracts error message from response, raises RuntimeError |
| `read_all_experience_files(experience_dir)` | function | Reads and concatenates `.txt` files |
| `generate_documents(job, profile, ...)` | function | Orchestrates resume + cover letter generation |

### 3.2 bot/bot.py (JD Storage)

| Symbol | Kind | Purpose |
|--------|------|---------|
| `_save_job_description(scored, profile_dir)` | function | Saves JD as HTML file, returns Path or None |
| `_esc(text)` | function | Minimal HTML entity escaping (`&`, `<`, `>`, `"`) |
| `_plain_to_html(text)` | function | Converts plain text to `<p>` or `<br>` HTML |
| `_save_application(db, scored, ..., description_path)` | function | Persists application record including JD path |

### 3.3 config/settings.py

| Symbol | Kind | Purpose |
|--------|------|---------|
| `LLMConfig` | Pydantic model | Holds `provider`, `api_key`, `model` |

### 3.4 db/database.py

| Symbol | Kind | Purpose |
|--------|------|---------|
| `Database._migrate()` | method | Adds `description_path TEXT` column if missing |
| `Database.save_application(..., description_path)` | method | INSERT with description_path parameter |

### 3.5 db/models.py

| Symbol | Kind | Purpose |
|--------|------|---------|
| `Application.description_path` | field | `str | None` -- path to saved HTML file |

---

## 4. Interface Contracts

### 4.1 invoke_llm(prompt, llm_config, timeout_seconds)

**Purpose**: Generate text using the configured LLM provider.
**Category**: command (external HTTP side effect)

**Signature**:
| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| prompt | `str` | yes | -- | Full prompt text |
| llm_config | `LLMConfig` | yes | -- | Must have `.provider`, `.api_key`, `.model` |
| timeout_seconds | `int` | no | 120 | Max wait time for HTTP request |

**Output**:
| Field | Type | Description |
|-------|------|-------------|
| return | `str` | Generated text content, stripped of whitespace |

**Errors**:
| Condition | Error Type | Detail |
|-----------|-----------|--------|
| No API key configured | `RuntimeError` | "No AI provider configured..." |
| API returns non-200 | `RuntimeError` | Provider name, status code, error message |
| Unsupported provider | `RuntimeError` | "Unsupported LLM provider: {name}" |
| Network/timeout | `requests.exceptions.*` | Connection or timeout error |

**Thread Safety**: Safe -- no shared mutable state. Each call is an independent HTTP request.

---

### 4.2 validate_api_key(provider, api_key, model)

**Purpose**: Test whether an API key is valid by making a minimal request.
**Category**: query (external side effect: one small API call)

**Signature**:
| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| provider | `str` | yes | -- | `"anthropic"`, `"openai"`, `"google"`, or `"deepseek"` |
| api_key | `str` | yes | -- | The API key to test |
| model | `str | None` | no | `None` | Model override; defaults to `DEFAULT_MODELS[provider]` |

**Output**:
| Field | Type | Description |
|-------|------|-------------|
| return | `bool` | `True` if key is valid, `False` otherwise |

**Errors**: None raised -- all exceptions caught internally, return `False`.

**Implementation**: Calls `_call_llm(provider, api_key, model, "Reply with OK", timeout=15)`.

---

### 4.3 POST /api/ai/validate

**Purpose**: Validate an LLM API key from the frontend settings UI.
**Category**: REST endpoint

**Request**:
```json
{
    "provider": "anthropic",
    "api_key": "sk-ant-...",
    "model": ""
}
```

| Field | Type | Required | Constraints |
|-------|------|----------|-------------|
| provider | string | yes | One of: `"anthropic"`, `"openai"`, `"google"`, `"deepseek"` |
| api_key | string | yes | Non-empty |
| model | string | no | Empty string uses provider default |

**Response (200)**:
```json
{"valid": true}
```

**Error Responses**:
| Status | Body | Condition |
|--------|------|-----------|
| 400 | `{"error": "provider and api_key are required"}` | Missing fields |
| 400 | `{"error": "Unsupported provider: xyz"}` | Invalid provider string |

---

### 4.4 GET /api/applications/:id/description

**Purpose**: Serve the saved job description HTML file for a given application.
**Category**: REST endpoint

**URL Parameters**:
| Parameter | Type | Description |
|-----------|------|-------------|
| app_id | int | Application ID (primary key) |

**Response (200)**: Raw HTML file served with `Content-Type: text/html`.

**Error Responses**:
| Status | Body | Condition |
|--------|------|-----------|
| 404 | `{"error": "Application not found"}` | No application with that ID |
| 404 | `{"error": "Job description not found"}` | `description_path` is null or file missing |

---

### 4.5 _save_job_description(scored, profile_dir)

**Purpose**: Save a job description as a self-contained HTML file.
**Category**: command (file write)

**Signature**:
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| scored | `ScoredJob` | yes | Must have `.raw.external_id`, `.raw.company`, `.raw.title`, `.raw.location`, `.raw.salary`, `.raw.description`, `.raw.apply_url` |
| profile_dir | `Path` | yes | Base profile directory (`~/.autoapply/profile`) |

**Output**:
| Field | Type | Description |
|-------|------|-------------|
| return | `Path | None` | Path to saved HTML file, or `None` on any failure |

**Side Effects**: Creates directory `profile_dir/job_descriptions/` if needed. Writes one `.html` file.

**Filename Convention**:
```
{external_id[:8]}_{sanitized_company}_{YYYY-MM-DD}.html
```

Where `sanitized_company` = `re.sub(r"[^a-zA-Z0-9]+", "-", company).strip("-").lower()`.

**Errors**: All exceptions caught internally, logged at WARNING level, returns `None`.

---

### 4.6 _esc(text) and _plain_to_html(text)

**_esc(text)**:
- Escapes `&` to `&amp;`, `<` to `&lt;`, `>` to `&gt;`, `"` to `&quot;`.
- Used on all user-controlled text inserted into HTML output.

**_plain_to_html(text)**:
- First escapes via `_esc()`.
- If text contains double-newlines, splits into `<p>` paragraphs.
- Otherwise replaces single newlines with `<br>`.

---

### 4.7 generate_documents(job, profile, experience_dir, output_dir_resumes, output_dir_cover_letters, llm_config)

**Purpose**: Full orchestration -- read experience files, invoke LLM twice, save outputs.
**Category**: saga

**Signature**:
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| job | object | yes | Must have `.id`, `.raw.company`, `.raw.description` |
| profile | `UserProfile` | yes | Profile with full_name, email, phone_full, location, linkedin_url, portfolio_url, bio |
| experience_dir | `Path` | yes | Directory containing `.txt` experience files |
| output_dir_resumes | `Path` | yes | Destination for `.md` and `.pdf` resume files |
| output_dir_cover_letters | `Path` | yes | Destination for `.txt` cover letter files |
| llm_config | `LLMConfig | None` | no | Provider configuration; None triggers RuntimeError |

**Output**:
| Field | Type | Description |
|-------|------|-------------|
| return | `tuple[Path, Path]` | `(resume_pdf_path, cover_letter_txt_path)` |

**Side Effects**: Creates 3 files (`.md`, `.pdf`, `.txt`). Two HTTP API calls to LLM provider.

**Change from SAD-003**: Previously invoked `invoke_claude_code()` subprocess. Now invokes `invoke_llm()` HTTP API. Same prompt templates, same output format. The `llm_config` parameter replaces the implicit Claude Code CLI dependency.

---

## 5. Data Model

### 5.1 LLMConfig (Pydantic)

Defined in `config/settings.py`. Stored in `config.json` under the `llm` key.

```python
class LLMConfig(BaseModel):
    provider: str = ""   # "anthropic" | "openai" | "google" | "deepseek"
    api_key: str = ""
    model: str = ""      # Empty = use DEFAULT_MODELS[provider]
```

### 5.2 DEFAULT_MODELS

```python
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-20250514",
    "openai": "gpt-4o",
    "google": "gemini-2.0-flash",
    "deepseek": "deepseek-chat",
}
```

### 5.3 applications Table -- description_path Column

```sql
-- New column (added via migration for existing DBs, in schema for new DBs)
description_path TEXT
```

| Column | Type | Nullable | Default | Purpose |
|--------|------|----------|---------|---------|
| description_path | TEXT | yes | NULL | Absolute filesystem path to saved HTML file |

### 5.4 Application Model (Pydantic)

```python
class Application(BaseModel):
    # ... existing fields ...
    description_path: str | None   # NEW
```

### 5.5 HTML File Structure

Each saved job description is a self-contained HTML document:

```html
<!DOCTYPE html>
<html lang='en'>
<head>
  <meta charset='utf-8'>
  <title>{escaped_title} -- {escaped_company}</title>
  <style>/* inline system-ui styles, max-width 800px, centered */</style>
</head>
<body>
  <h1>{escaped_title}</h1>
  <div class='meta'>
    <span><strong>{escaped_company}</strong></span>
    <span>{escaped_location}</span>
    <span>{escaped_salary}</span>
  </div>
  <div class='description'>
    {plain_to_html(description)}
  </div>
  <hr>
  <p class='meta'>Source: <a href='{escaped_url}'>{escaped_url}</a> | Saved {date}</p>
</body>
</html>
```

---

## 6. Provider Routing

### 6.1 Routing Table

| Provider | Endpoint | Auth Header | Request Body Schema | Response Text Path |
|----------|----------|-------------|--------------------|--------------------|
| anthropic | `https://api.anthropic.com/v1/messages` | `x-api-key: {key}` + `anthropic-version: 2023-06-01` | `{"model", "max_tokens": 4096, "messages": [{"role": "user", "content": prompt}]}` | `data["content"][0]["text"]` |
| openai | `https://api.openai.com/v1/chat/completions` | `Authorization: Bearer {key}` | `{"model", "messages": [{"role": "user", "content": prompt}], "max_tokens": 4096}` | `data["choices"][0]["message"]["content"]` |
| google | `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent` | `?key={key}` (query param) | `{"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"maxOutputTokens": 4096}}` | `data["candidates"][0]["content"]["parts"][0]["text"]` |
| deepseek | `https://api.deepseek.com/v1/chat/completions` | `Authorization: Bearer {key}` | Same as OpenAI schema | Same as OpenAI path |

### 6.2 Internal Dispatch

```
invoke_llm(prompt, llm_config)
  └── _call_llm(provider, api_key, model, prompt, timeout)
        ├── provider == "anthropic"        → _call_anthropic()
        ├── provider == "google"           → _call_google()
        ├── provider in ("openai","deepseek") → _call_openai_compatible()
        └── else                           → RuntimeError
```

DeepSeek reuses the OpenAI-compatible function because its API follows the same chat completions schema. The only difference is the endpoint URL, resolved from `API_ENDPOINTS[provider]`.

### 6.3 Error Extraction

`_raise_api_error(provider, resp)` handles all providers uniformly:
1. Tries to parse response as JSON.
2. Looks for `error.message` (nested dict) or `error` (string).
3. Falls back to raw `resp.text`.
4. Raises `RuntimeError(f"{provider} API error ({status_code}): {msg}")`.

---

## 7. Database Migration

### 7.1 Strategy

SQLite does not support `ADD COLUMN IF NOT EXISTS`. The migration uses `PRAGMA table_info()` to check column existence before altering.

### 7.2 Implementation

```python
@staticmethod
def _migrate(conn: sqlite3.Connection) -> None:
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(applications)").fetchall()
    }
    if "description_path" not in columns:
        conn.execute("ALTER TABLE applications ADD COLUMN description_path TEXT")
```

### 7.3 Execution

- Called from `Database.init_schema()` after `CREATE TABLE IF NOT EXISTS` runs.
- Idempotent: safe to run on both new databases (column already in CREATE TABLE) and existing databases (ALTER TABLE adds it).
- No data migration needed: existing rows get `NULL` for `description_path`, which is the correct default (no JD was saved for those applications).

---

## 8. Security

### 8.1 XSS Prevention in HTML Generation

All user-controlled strings are escaped via `_esc()` before insertion into HTML:
- Job title, company, location, salary, apply URL -- all escaped.
- Job description text passes through `_plain_to_html()`, which calls `_esc()` first, then wraps in structural `<p>` / `<br>` tags.
- No raw user content is ever inserted into HTML without escaping.

Escaped characters: `&` `<` `>` `"` (the four HTML-significant characters).

### 8.2 API Key Storage

- API keys are stored in `~/.autoapply/config.json` as plaintext within the `llm` object.
- Config file permissions are inherited from the user's home directory.
- Keys are never logged: the `logger` calls in `ai_engine.py` do not reference `api_key`.
- Keys are never included in error messages: `_raise_api_error()` only includes the provider name and the API's error response.
- The `/api/ai/validate` endpoint accepts the key in POST body (not URL), preventing key leakage in server logs or browser history.

### 8.3 File Path Security

- `GET /api/applications/:id/description` looks up the path from the database by application ID. It does not accept a user-supplied file path, preventing path traversal attacks.
- The `send_file()` call uses the path stored at insertion time, which is always under `~/.autoapply/profile/job_descriptions/`.

---

## 9. Architecture Decision Records

### ADR-014: Multi-Provider LLM Abstraction via HTTP APIs

**Status**: accepted
**Context**: The original architecture (ADR-009) required Claude Code CLI to be installed locally. This created a hard dependency on a specific tool, limited provider choice, and required subprocess management. Users requested support for their own API keys across multiple providers.
**Decision**: Replace `invoke_claude_code()` (subprocess) with `invoke_llm()` (HTTP API via `requests`). Support four providers: Anthropic, OpenAI, Google Gemini, DeepSeek. Route through a single dispatch function.
**Rationale**:
- HTTP APIs are universally available without local tool installation.
- The `requests` library is already a project dependency.
- OpenAI and DeepSeek share the same API schema, reducing implementation to three distinct callers.
- Users choose their provider and model in Settings UI, stored in `LLMConfig`.
- Validation endpoint lets users test keys before saving.
**Consequences**:
- Claude Code CLI is no longer required (ADR-009 is superseded).
- Users must provide their own API key.
- Network connectivity is required for document generation.
- `check_ai_available()` now checks for a configured API key, not CLI presence.
- Fallback strategy (ADR-011) still applies when no API key is configured.

### ADR-015: HTML for Job Description Storage

**Status**: accepted
**Context**: Need to persist job descriptions for later viewing (interview prep, application review). Options: plain text, Markdown, PDF, HTML.
**Decision**: Save as self-contained HTML files with inline CSS.
**Rationale**:
- HTML preserves formatting better than plain text.
- Self-contained (inline CSS, no external dependencies) -- can be opened in any browser.
- Can be served directly via Flask's `send_file()` with `text/html` mimetype.
- Lighter than PDF generation (no ReportLab overhead for read-only content).
- Easy to render in an iframe or new tab from the frontend.
**Consequences**:
- Must escape all user content to prevent XSS (implemented via `_esc()`).
- Files are slightly larger than plain text but negligible at individual JD scale.

### ADR-016: Idempotent ALTER TABLE Migration

**Status**: accepted
**Context**: The `description_path` column must be added to existing databases without breaking new installations.
**Decision**: Use `PRAGMA table_info()` to check for column existence, then `ALTER TABLE ADD COLUMN` only if missing. Run on every `Database.__init__()`.
**Rationale**:
- SQLite lacks `ADD COLUMN IF NOT EXISTS` syntax.
- PRAGMA check is the standard SQLite pattern for introspection.
- Running on every init ensures any database version is automatically upgraded.
- New databases already include the column in the CREATE TABLE statement, so ALTER is skipped.
**Consequences**:
- Tiny overhead on every startup (one PRAGMA query). Negligible.
- Pattern is extensible: future columns follow the same `if col not in columns` check.

---

## 10. Design Traceability Matrix

| Requirement | Type | Design Component | Interface / File | ADR | Status |
|-------------|------|-----------------|------------------|-----|--------|
| FR-074 | FR | ai_engine.py | `invoke_llm()`, `_call_llm()`, `_call_anthropic()`, `_call_openai_compatible()`, `_call_google()` | ADR-014 | Shipped |
| FR-075 | FR | ai_engine.py | `validate_api_key()` | ADR-014 | Shipped |
| FR-076 | FR | app.py | `POST /api/ai/validate` | ADR-014 | Shipped |
| FR-077 | FR | bot/bot.py | `_save_job_description()`, `_esc()`, `_plain_to_html()` | ADR-015 | Shipped |
| FR-078 | FR | app.py, db/database.py | `GET /api/applications/:id/description`, `description_path` column, `_migrate()` | ADR-015, ADR-016 | Shipped |
| FR-079 | FR | config/settings.py, db/models.py | `LLMConfig` model, `Application.description_path` field | ADR-014 | Shipped |
