# Software Requirements Specification

**Document ID**: SRS-TASK-009-llm-migration-jd-storage
**Version**: 1.0
**Date**: 2026-03-10
**Status**: approved (retroactive)
**Author**: Claude (Requirements Analyst)
**PRD Reference**: PRD Section 8 (AI Engine), Phase 2 migration from Claude Code CLI to multi-provider LLM APIs

---

## 1. Purpose and Scope

### 1.1 Purpose
Specifies all functional and non-functional requirements for two features delivered across v1.2.0 and an unreleased update: (A) migration from Claude Code CLI subprocess invocation to direct multi-provider LLM API calls (Anthropic, OpenAI, Google Gemini, DeepSeek), and (B) job description storage as styled HTML files linked to application records. This is a retroactive SRS documenting already-shipped code.

Audience: System Engineer, Backend Developer, Frontend Developer, Unit Tester, Integration Tester, Security Engineer, Documenter, Release Engineer.

### 1.2 Scope
The system SHALL provide: multi-provider LLM support via HTTP APIs, API key validation, a settings UI for provider/model/key configuration, provider-specific request routing and response parsing, graceful fallback to static templates, job description persistence as styled HTML, database linkage of descriptions to application records, and dashboard access to stored descriptions.

The system SHALL NOT provide: prompt customization by end users, streaming LLM responses, cost tracking or token counting, or job description editing after save.

### 1.3 Definitions and Acronyms
| Term | Definition |
|------|-----------|
| LLM | Large Language Model — AI model accessed via HTTP API for text generation |
| Provider | An LLM API vendor: Anthropic, OpenAI, Google (Gemini), or DeepSeek |
| API Key | Secret credential issued by a provider to authenticate API requests |
| LLMConfig | Pydantic model (`config/settings.py`) holding provider, api_key, and model fields |
| Job Description | The full text of a job posting, saved as a styled HTML file for interview prep |
| Fallback | Behavior when no LLM provider is configured or API call fails: use static resume/cover letter templates |
| ATS | Applicant Tracking System |

---

## 2. Overall Description

### 2.1 Product Perspective
This task replaces the Phase 2 Claude Code CLI subprocess integration (FR-031/FR-032) with direct HTTP calls to four LLM provider APIs. The `core/ai_engine.py` module was rewritten: `invoke_claude_code()` and `check_claude_code_available()` were replaced by `invoke_llm()`, `check_ai_available()`, `validate_api_key()`, and provider-specific `_call_*()` functions. The job description storage feature adds a new data path from the bot loop through the database to the dashboard detail view.

### 2.2 User Classes and Characteristics
| User Class | Description | Frequency | Technical Expertise |
|-----------|-------------|-----------|---------------------|
| Job Seeker | Professional using AutoApply to generate tailored application documents | Daily | Intermediate — has an API key from at least one LLM provider |

### 2.3 Operating Environment
| Requirement | Specification |
|-------------|---------------|
| OS | macOS 12+, Windows 10/11 (64-bit), Ubuntu 20.04/22.04 LTS |
| Runtime | Python 3.11+ |
| Python dependency | `requests` for HTTP API calls, `reportlab` for PDF generation |
| Network | Outbound HTTPS to provider API endpoints (api.anthropic.com, api.openai.com, generativelanguage.googleapis.com, api.deepseek.com) |

### 2.4 Assumptions
| # | Assumption | Risk if Wrong | Mitigation |
|---|-----------|---------------|------------|
| A1 | Provider API formats remain stable (Anthropic Messages v1, OpenAI Chat Completions v1, Gemini v1beta, DeepSeek v1) | API calls fail | Pin to known API versions in headers; wrap response parsing |
| A2 | DeepSeek API is OpenAI-compatible | Routing breaks | Shared `_call_openai_compatible()` handler tested against both |
| A3 | API keys are valid across all models offered by the provider | Validation passes but generation fails | Validate with the same model that will be used for generation |
| A4 | Job descriptions are UTF-8 text that can be safely HTML-escaped | Garbled output | Apply `_esc()` to all user-sourced text before embedding in HTML |
| A5 | Users have at most one LLM provider configured at a time | N/A | `LLMConfig` stores a single provider/key/model triple |

### 2.5 Constraints
| Type | Constraint | Rationale |
|------|-----------|-----------|
| Technical | API key stored in local `config.json` only, never transmitted except to the configured provider | Privacy — user's key must not leak |
| Technical | No SDK dependencies — all API calls use `requests` library | Minimizes dependency surface; all four providers have simple REST APIs |
| Technical | Job descriptions saved as self-contained HTML (inline CSS, no external resources) | Files must render correctly when opened offline in any browser |
| Backward Compat | `core/ai_engine.py` public interface changed from `invoke_claude_code()`/`check_claude_code_available()` to `invoke_llm()`/`check_ai_available()` | Breaking change handled within same release; no external consumers |

---

## 3. Functional Requirements

### FR-074: Multi-Provider LLM Support

**Description**: The system SHALL support four LLM API providers for document generation: Anthropic (Claude), OpenAI (GPT), Google (Gemini), and DeepSeek. Each provider has a default model, configurable via `LLMConfig.model`.

**Priority**: P0
**Source**: Phase 2 migration — replacing Claude Code CLI with direct API calls
**Dependencies**: None

**Acceptance Criteria**:

- **AC-074-1**: Given `LLMConfig.provider` is `"anthropic"` and a valid API key is set, When `invoke_llm(prompt, llm_config)` is called, Then the system sends a POST request to `https://api.anthropic.com/v1/messages` with the Anthropic Messages API format and returns the generated text.

- **AC-074-2**: Given `LLMConfig.provider` is `"openai"` and a valid API key is set, When `invoke_llm(prompt, llm_config)` is called, Then the system sends a POST request to `https://api.openai.com/v1/chat/completions` with the OpenAI Chat Completions format and returns the generated text.

- **AC-074-3**: Given `LLMConfig.provider` is `"google"` and a valid API key is set, When `invoke_llm(prompt, llm_config)` is called, Then the system sends a POST request to `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent` with the Gemini format and returns the generated text.

- **AC-074-4**: Given `LLMConfig.provider` is `"deepseek"` and a valid API key is set, When `invoke_llm(prompt, llm_config)` is called, Then the system sends a POST request to `https://api.deepseek.com/v1/chat/completions` using OpenAI-compatible format and returns the generated text.

- **AC-074-5**: Given `LLMConfig.model` is empty, When a provider is selected, Then the system uses the provider's default model: `claude-sonnet-4-20250514` (Anthropic), `gpt-4o` (OpenAI), `gemini-2.0-flash` (Google), `deepseek-chat` (DeepSeek).

- **AC-074-6**: Given `LLMConfig.model` is set to a custom value, When `invoke_llm()` is called, Then the custom model is used instead of the default.

**Negative Cases**:
- **AC-074-N1**: Given `LLMConfig.provider` is an unsupported value (e.g., `"cohere"`), When `invoke_llm()` is called, Then it raises `RuntimeError` with message `"Unsupported LLM provider: cohere"`.

- **AC-074-N2**: Given a valid provider but the API returns a non-200 status, When `invoke_llm()` is called, Then it raises `RuntimeError` containing the provider name, HTTP status code, and error message extracted from the response body.

---

### FR-075: Job Description Storage

**Description**: The system SHALL save each job's description as a self-contained styled HTML file at the time of application, link the file path in the application database record, and make it accessible from the dashboard application detail view.

**Priority**: P1
**Source**: Interview preparation feature — unreleased
**Dependencies**: FR-036 (document generation orchestration), database schema

**Acceptance Criteria**:

- **AC-075-1**: Given a scored job passes the filter, When the bot processes it for application, Then `_save_job_description()` is called before document generation, producing an HTML file at `~/.autoapply/profile/job_descriptions/{external_id_prefix}_{company}_{date}.html`.

- **AC-075-2**: Given the HTML file is generated, Then it contains: the job title as `<h1>`, company/location/salary in a `.meta` div, the full description in a `.description` div with paragraphs or `<br>` line breaks, and a source URL link with save date.

- **AC-075-3**: Given the HTML file is generated, Then it is self-contained with inline CSS (system-ui font, max-width 800px, 40px auto margin, 1.6 line-height, dark text on white) and no external resource dependencies.

- **AC-075-4**: Given the job description is saved, When the application record is saved to the database, Then the `description_path` column stores the absolute file path to the HTML file.

- **AC-075-5**: Given an application has a `description_path`, When the user opens the application detail view in the dashboard, Then a "View Job Description" button is visible that opens the HTML file in a new browser tab via `GET /api/applications/{id}/description`.

- **AC-075-6**: Given the `applications` table was created before the `description_path` column existed, When the database initializes, Then `_migrate()` adds the `description_path TEXT` column via `ALTER TABLE`.

**Negative Cases**:
- **AC-075-N1**: Given `_save_job_description()` fails (e.g., filesystem permission error), When the bot processes the job, Then it logs a warning and returns `None` for `desc_path`, and the bot continues processing the job without a saved description.

- **AC-075-N2**: Given an application has no `description_path` (null), When the user views the application detail, Then the "View Job Description" button is not rendered.

- **AC-075-N3**: Given `GET /api/applications/{id}/description` is called for an application whose `description_path` file has been deleted from disk, When the endpoint handles the request, Then it returns HTTP 404 with `{"error": "Job description not found"}`.

- **AC-075-N4**: Given the company name contains special characters, When the filename is constructed, Then non-alphanumeric characters are replaced with hyphens (`re.sub(r"[^a-zA-Z0-9]+", "-", ...)`), leading/trailing hyphens are stripped, and the result is lowercased.

---

### FR-076: API Key Validation

**Description**: The system SHALL validate an API key by making a minimal test request to the provider's API (`"Reply with OK"` prompt) and returning a boolean result. Validation is triggered from the UI before saving settings.

**Priority**: P1
**Source**: Settings UX — prevent saving invalid keys
**Dependencies**: FR-074 (multi-provider support)

**Acceptance Criteria**:

- **AC-076-1**: Given a valid Anthropic API key, When `validate_api_key("anthropic", key)` is called, Then it returns `True` within 15 seconds.

- **AC-076-2**: Given an invalid API key for any provider, When `validate_api_key(provider, key)` is called, Then it returns `False` (no exception propagated to caller).

- **AC-076-3**: Given a valid key and a custom model, When `validate_api_key(provider, key, model)` is called, Then the validation request uses the specified model.

- **AC-076-4**: Given a valid key and no model specified, When `validate_api_key(provider, key)` is called, Then the validation request uses `DEFAULT_MODELS[provider]`.

- **AC-076-5**: Given `POST /api/ai/validate` is called with `{"provider": "openai", "api_key": "sk-..."}`, When the key is valid, Then the endpoint returns `{"valid": true}` with HTTP 200.

**Negative Cases**:
- **AC-076-N1**: Given `POST /api/ai/validate` is called without `provider` or `api_key`, Then the endpoint returns HTTP 400 with `{"error": "provider and api_key are required"}`.

- **AC-076-N2**: Given `POST /api/ai/validate` is called with an unsupported provider (e.g., `"cohere"`), Then the endpoint returns HTTP 400 with `{"error": "Unsupported provider: cohere"}`.

- **AC-076-N3**: Given the provider API is unreachable (network error), When `validate_api_key()` is called, Then it catches the exception and returns `False`.

---

### FR-077: LLM Configuration UI

**Description**: The Settings screen SHALL include an "AI Provider" section with a provider dropdown, a model text input, an API key password input, and a "Validate" button with inline status feedback.

**Priority**: P1
**Source**: Dashboard UX for configuring LLM provider
**Dependencies**: FR-076 (API key validation)

**Acceptance Criteria**:

- **AC-077-1**: Given the user navigates to Settings, Then they see an "AI Provider" section with: a Provider `<select>` dropdown offering "-- Select --", "Anthropic (Claude)", "OpenAI (GPT)", "Google (Gemini)", and "DeepSeek" options.

- **AC-077-2**: Given the user selects a provider and the model field is empty, Then the model input's placeholder updates to the provider's default model name (e.g., `claude-sonnet-4-20250514` for Anthropic).

- **AC-077-3**: Given the user enters an API key and clicks "Validate", Then the button shows "Validating..." while the request is in-flight, and upon completion displays either "API key is valid" (green) or "API key is invalid" (red) below the input.

- **AC-077-4**: Given settings are saved, Then the `llm` object in `config.json` contains `provider`, `api_key`, and `model` fields matching the UI inputs.

- **AC-077-5**: Given settings are loaded, Then the provider dropdown, model input, and API key input are populated from `config.llm`.

**Negative Cases**:
- **AC-077-N1**: Given the user clicks "Validate" without selecting a provider, Then inline text reads "Select a provider first" in red and no API request is made.

- **AC-077-N2**: Given the user clicks "Validate" without entering an API key, Then inline text reads "Enter an API key" in red and no API request is made.

---

### FR-078: Provider-Specific API Routing

**Description**: The system SHALL route LLM calls to the correct API endpoint based on the configured provider and parse the provider-specific response format to extract generated text.

**Priority**: P0
**Source**: Implementation detail of FR-074
**Dependencies**: FR-074

**Acceptance Criteria**:

- **AC-078-1**: Given provider is `"anthropic"`, When `_call_llm()` dispatches the request, Then it calls `_call_anthropic()` which sends headers `x-api-key` and `anthropic-version: 2023-06-01`, a JSON body with `model`, `max_tokens: 4096`, and `messages` array, and extracts text from `response.content[0].text`.

- **AC-078-2**: Given provider is `"openai"` or `"deepseek"`, When `_call_llm()` dispatches the request, Then it calls `_call_openai_compatible()` which sends `Authorization: Bearer {key}` header, a JSON body with `model`, `max_tokens: 4096`, and `messages` array, and extracts text from `response.choices[0].message.content`.

- **AC-078-3**: Given provider is `"google"`, When `_call_llm()` dispatches the request, Then it calls `_call_google()` which sends the API key as a `key` query parameter, a JSON body with `contents[0].parts[0].text` and `generationConfig.maxOutputTokens: 4096`, and extracts text from `response.candidates[0].content.parts[0].text`.

- **AC-078-4**: Given any provider returns a non-200 status, When `_raise_api_error()` is called, Then it extracts the error message from `response.error.message` (if available) or falls back to `response.text`, and raises `RuntimeError` with format `"{Provider} API error ({status}): {message}"`.

**Negative Cases**:
- **AC-078-N1**: Given the response JSON structure is unexpected (e.g., missing `content` key for Anthropic), When response parsing occurs, Then a `KeyError` or `IndexError` propagates as an unhandled exception from `_call_*()`, which the caller treats as a generation failure.

---

### FR-079: Graceful Fallback

**Description**: The system SHALL fall back to static templates when the LLM API call fails or no provider is configured. The fallback uses `config.profile.fallback_resume_path` for the resume and `config.bot.cover_letter_template` for the cover letter.

**Priority**: P1
**Source**: Continuity — bot must not stop if LLM is unavailable
**Dependencies**: FR-074

**Acceptance Criteria**:

- **AC-079-1**: Given `llm_config` is `None` or `llm_config.api_key` is empty, When `check_ai_available(llm_config)` is called, Then it returns `False`.

- **AC-079-2**: Given `check_ai_available()` returns `False`, When the dashboard loads, Then a warning banner is displayed: "No AI provider configured -- using fallback templates. Add an API key in Settings -> AI Provider for tailored resumes and cover letters."

- **AC-079-3**: Given `generate_documents()` raises an exception (API failure, timeout, invalid key), When the bot's `_generate_docs()` wrapper catches the error, Then it logs a warning, uses `config.profile.fallback_resume_path` as the resume (if it exists), uses `config.bot.cover_letter_template` as the cover letter text, and continues the application flow.

- **AC-079-4**: Given no LLM is configured and no fallback resume path is set, When `_generate_docs()` executes the fallback path, Then `resume_path` is `None` and `cover_letter_text` is `""`, and the bot proceeds (the applier handles missing documents).

- **AC-079-5**: Given `invoke_llm()` is called with no API key, When it checks the config, Then it raises `RuntimeError` with message `"No AI provider configured. Add an API key in Settings -> AI Provider."`.

**Negative Cases**:
- **AC-079-N1**: Given `fallback_resume_path` points to a file that does not exist, When fallback is triggered, Then `resume_path` remains `None` (the non-existent path is not returned).

---

## 4. Non-Functional Requirements

### NFR-023: LLM API Timeout

**Description**: Every LLM HTTP request SHALL have a configurable timeout defaulting to 120 seconds for generation calls and 15 seconds for validation calls.
**Metric**: Request raises exception if no response within timeout.
**Priority**: P0
**Validation**: Unit test with mocked `requests.post` that exceeds timeout.

### NFR-024: API Key Secrecy

**Description**: The API key SHALL be stored only in the local `config.json` file and transmitted only to the configured provider's HTTPS endpoint. It SHALL NOT appear in logs, prompts, or error messages.
**Metric**: Code review confirms no logging of `api_key` field; prompts contain only experience content, job descriptions, and profile fields.
**Priority**: P0
**Validation**: Security audit grep for `api_key` in log statements.

### NFR-025: Job Description File Size

**Description**: Saved job description HTML files SHALL be under 1 MB each for typical job postings (up to 10,000 words).
**Metric**: File size < 1 MB for descriptions up to 10,000 words.
**Priority**: P2
**Validation**: Generate HTML for a 10,000-word description and verify file size.

### NFR-026: API Validation Latency

**Description**: `validate_api_key()` SHALL complete within 15 seconds. The UI SHALL show a loading indicator during validation.
**Metric**: Validation returns within 15 seconds; button text changes to "Validating..." during request.
**Priority**: P1
**Validation**: Manual test with valid and invalid keys; observe UI behavior.

### NFR-027: No External Resource Dependencies in Saved HTML

**Description**: Saved job description HTML files SHALL be fully self-contained with inline CSS and no references to external stylesheets, scripts, images, or fonts.
**Metric**: HTML file renders correctly when opened from local filesystem with no network connection.
**Priority**: P1
**Validation**: Open saved HTML file in browser with network disabled; verify rendering.

### NFR-028: Database Migration Safety

**Description**: The `_migrate()` method SHALL be idempotent — running it on a database that already has the `description_path` column SHALL NOT raise an error or alter data.
**Metric**: Calling `init_schema()` twice on the same database succeeds without error.
**Priority**: P0
**Validation**: Unit test that initializes schema twice and verifies no exception.

---

## 5. Interface Requirements

### 5.1 Internal Interfaces

| Function | Module | Input | Output |
|----------|--------|-------|--------|
| `check_ai_available(llm_config)` | `core/ai_engine.py` | `LLMConfig \| None` | `bool` |
| `validate_api_key(provider, api_key, model)` | `core/ai_engine.py` | `str, str, str \| None` | `bool` |
| `invoke_llm(prompt, llm_config, timeout_seconds)` | `core/ai_engine.py` | `str, LLMConfig, int` | `str` |
| `_call_anthropic(api_key, model, prompt, timeout)` | `core/ai_engine.py` | `str, str, str, int` | `str` |
| `_call_openai_compatible(provider, api_key, model, prompt, timeout)` | `core/ai_engine.py` | `str, str, str, str, int` | `str` |
| `_call_google(api_key, model, prompt, timeout)` | `core/ai_engine.py` | `str, str, str, int` | `str` |
| `_save_job_description(scored, profile_dir)` | `bot/bot.py` | `ScoredJob, Path` | `Path \| None` |
| `generate_documents(job, profile, exp_dir, res_dir, cl_dir, llm_config)` | `core/ai_engine.py` | See signature | `tuple[Path, Path]` |
| `Database._migrate(conn)` | `db/database.py` | `sqlite3.Connection` | `None` |
| `Database.save_application(..., description_path)` | `db/database.py` | `..., str \| None` | `int` |

### 5.2 External Interfaces (API Endpoints)

| Endpoint | Method | Request Body | Response |
|----------|--------|-------------|----------|
| `/api/ai/validate` | POST | `{"provider": str, "api_key": str, "model"?: str}` | `{"valid": bool}` |
| `/api/applications/{id}/description` | GET | — | HTML file (200) or `{"error": str}` (404) |

### 5.3 External Interfaces (LLM Providers)

| Provider | Endpoint | Auth Mechanism | Request Format | Response Text Path |
|----------|----------|---------------|----------------|-------------------|
| Anthropic | `https://api.anthropic.com/v1/messages` | `x-api-key` header | Messages API (`messages[]`) | `content[0].text` |
| OpenAI | `https://api.openai.com/v1/chat/completions` | `Authorization: Bearer` header | Chat Completions (`messages[]`) | `choices[0].message.content` |
| Google | `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent` | `key` query param | Gemini (`contents[0].parts[]`) | `candidates[0].content.parts[0].text` |
| DeepSeek | `https://api.deepseek.com/v1/chat/completions` | `Authorization: Bearer` header | OpenAI-compatible (`messages[]`) | `choices[0].message.content` |

---

## 6. Data Requirements

### 6.1 Configuration Model

```python
class LLMConfig(BaseModel):
    provider: str = ""    # "anthropic" | "openai" | "google" | "deepseek"
    api_key: str = ""     # Provider API key
    model: str = ""       # Empty = use DEFAULT_MODELS[provider]
```

Stored in `~/.autoapply/config.json` under the `llm` key of the root `AppConfig` object.

### 6.2 Database Schema Addition

```sql
ALTER TABLE applications ADD COLUMN description_path TEXT;
```

Applied by `Database._migrate()` if the column does not already exist (checked via `PRAGMA table_info`).

### 6.3 File Outputs

| File Type | Location | Naming Convention | Retention |
|-----------|----------|-------------------|-----------|
| Job Description HTML | `~/.autoapply/profile/job_descriptions/` | `{external_id[:8]}_{company}_{date}.html` | Indefinite |

---

## 7. Out of Scope

- **Streaming LLM responses** — all calls are synchronous request/response.
- **Token counting or cost tracking** — user monitors usage via provider dashboard.
- **Multiple simultaneous providers** — only one provider configured at a time.
- **Prompt customization by user** — prompts are hardcoded in `RESUME_PROMPT` and `COVER_LETTER_PROMPT`.
- **Job description editing or annotation** — HTML files are read-only after save.
- **Claude Code CLI support** — fully replaced by direct API calls; `invoke_claude_code()` and `check_claude_code_available()` are removed.

---

## 8. Dependencies

### External Dependencies
| Dependency | Type | Status | Risk if Unavailable |
|-----------|------|--------|---------------------|
| `requests` library | Build | Available via pip | All LLM API calls fail |
| Anthropic API | Runtime (optional) | Available | Choose different provider |
| OpenAI API | Runtime (optional) | Available | Choose different provider |
| Google Gemini API | Runtime (optional) | Available | Choose different provider |
| DeepSeek API | Runtime (optional) | Available | Choose different provider |

### Internal Dependencies
| This Feature Needs | From | Status |
|-------------------|------|--------|
| `LLMConfig` model | `config/settings.py` | Done |
| `AppConfig.llm` field | `config/settings.py` | Done |
| `UserProfile.fallback_resume_path` | `config/settings.py` (Phase 1) | Done |
| `BotConfig.cover_letter_template` | `config/settings.py` (Phase 1) | Done |
| `Application.description_path` field | `db/models.py` | Done |
| `Database.save_application()` with `description_path` param | `db/database.py` | Done |
| `ScoredJob` with `raw.description` | `core/filter.py` (Phase 3) | Done |

---

## 9. Risks

| # | Risk | Probability | Impact | Score | Mitigation |
|---|------|:-----------:|:------:|:-----:|------------|
| R1 | Provider API format changes (breaking) | Low | High | M | Pin API version in headers (Anthropic `2023-06-01`); monitor changelogs |
| R2 | API key leaked in error message or log | Low | Critical | H | `_raise_api_error()` excludes key from error; no key in log statements |
| R3 | Provider rate-limits validation requests | Medium | Low | L | Validation uses minimal prompt; user initiates manually |
| R4 | Job description HTML contains XSS if description has script tags | Low | Medium | M | All text is HTML-escaped via `_esc()` before embedding |
| R5 | Large job descriptions cause slow HTML generation | Low | Low | L | HTML generation is string concatenation, negligible cost |
| R6 | Migration fails on locked database | Low | Medium | M | Migration runs at startup within `init_schema()`; single-writer SQLite |

---

## 10. Requirements Traceability Seeds

| Req ID | Source | Traces Forward To |
|--------|--------|-------------------|
| FR-074 | PRD Section 8 (migration) | Design: ai_engine -> Code: core/ai_engine.py -> Test: test_ai_engine.py |
| FR-075 | Interview prep feature | Design: bot_loop -> Code: bot/bot.py, db/database.py, db/models.py, app.py -> Test: test_bot.py, test_database.py, test_api.py |
| FR-076 | Settings UX | Design: ai_engine -> Code: core/ai_engine.py, app.py -> Test: test_ai_engine.py, test_api.py |
| FR-077 | Settings UX | Design: dashboard -> Code: templates/index.html, config/settings.py -> Test: test_api.py (integration) |
| FR-078 | Implementation of FR-074 | Design: ai_engine -> Code: core/ai_engine.py -> Test: test_ai_engine.py |
| FR-079 | Continuity / fallback | Design: bot_loop + dashboard -> Code: bot/bot.py, core/ai_engine.py, templates/index.html -> Test: test_ai_engine.py, test_api.py |
