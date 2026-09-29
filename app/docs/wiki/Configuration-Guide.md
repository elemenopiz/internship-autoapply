# Configuration Guide

## Config File Location

All configuration is stored in a single JSON file:

```
~/.autoapply/config.json
```

On Windows this resolves to `C:\Users\<username>\.autoapply\config.json`.

The data directory can be overridden with the `AUTOAPPLY_DATA_DIR` environment variable.

> **Tip**: You can edit `config.json` directly, but it is recommended to use the Settings UI or API to avoid validation errors. The app validates configuration using Pydantic v2 models on load.

---

## LLM Provider Setup

AutoApply supports four LLM providers for AI-powered resume and cover letter generation.

### Supported Providers

| Provider | Default Model | API Key Prefix |
|----------|--------------|----------------|
| Anthropic | `claude-sonnet-4-20250514` | `sk-ant-` |
| OpenAI | `gpt-4o` | `sk-` |
| Google | `gemini-2.0-flash` | `AI...` |
| DeepSeek | `deepseek-chat` | `sk-` |

### Configuration

Set the provider in the `llm` section of `config.json`:

```json
{
  "llm": {
    "provider": "anthropic",
    "api_key": "sk-ant-...",
    "model": "claude-sonnet-4-20250514"
  }
}
```

Or configure via the Settings page in the UI (Settings > AI Provider).

You can change the model to any model supported by the provider. For example:

```json
{
  "llm": {
    "provider": "openai",
    "api_key": "sk-...",
    "model": "gpt-4o-mini"
  }
}
```

### Validate Your Key

Use the API to validate before saving:

```bash
curl -X POST http://127.0.0.1:5000/api/validate-api-key \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"provider": "anthropic", "api_key": "sk-ant-..."}'
```

### No AI Provider

If no provider is configured, AutoApply falls back to template-based document generation. Resumes and cover letters use your profile data directly without AI tailoring.

---

## API Key Storage

AutoApply stores API keys securely using the OS keyring when available:

| Platform | Backend |
|----------|---------|
| Windows | Windows Credential Locker |
| macOS | macOS Keychain |
| Linux | SecretService (GNOME Keyring / KWallet) |

If the keyring is unavailable (e.g., headless Linux without a desktop environment), keys fall back to plaintext storage in `config.json`.

**Auto-migration**: On startup, if a plaintext API key is found in `config.json` and the keyring is available, the key is automatically migrated to the keyring and removed from the config file.

---

## User Profile

The `profile` section stores your personal information used for applications:

```json
{
  "profile": {
    "full_name": "Jane Doe",
    "email": "jane@example.com",
    "phone": "+1-555-0100",
    "location": "San Francisco, CA",
    "linkedin_url": "https://linkedin.com/in/janedoe",
    "years_experience": 5,
    "education": "BS Computer Science, Stanford University",
    "skills": ["Python", "JavaScript", "TypeScript", "AWS", "Docker"],
    "screening_answers": {
      "Are you authorized to work in the US?": "Yes",
      "Do you require visa sponsorship?": "No",
      "What is your expected salary?": "$150,000",
      "Are you willing to relocate?": "Yes",
      "years_of_experience_python": "5",
      "years_of_experience_javascript": "4"
    }
  }
}
```

### Field Reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `full_name` | string | Yes | Your full name as it should appear on applications |
| `email` | string | Yes | Email address for applications |
| `phone` | string | No | Phone number |
| `location` | string | Yes | City, State/Country |
| `linkedin_url` | string | No | Your LinkedIn profile URL |
| `years_experience` | int | Yes | Total years of professional experience |
| `education` | string | No | Education summary |
| `skills` | string[] | No | List of skills |
| `screening_answers` | object | No | Pre-filled answers for common screening questions |

### Screening Answers

The `screening_answers` dictionary is used by Workday and Ashby appliers to auto-fill screening questions during the application process. Keys are the question text (or a normalized form), and values are your answers.

You can add screening answers via the Settings UI (Profile > Screening Answers) or directly in `config.json`.

---

## Search Criteria

The `search_criteria` section defines what jobs the bot looks for:

```json
{
  "search_criteria": {
    "job_titles": ["Software Engineer", "Backend Developer", "Full Stack Developer"],
    "locations": ["San Francisco, CA", "New York, NY", "Remote"],
    "remote_only": false,
    "experience_level": "mid",
    "excluded_companies": ["Company A", "Company B"]
  }
}
```

### Field Reference

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `job_titles` | string[] | `[]` | Job titles to search for |
| `locations` | string[] | `[]` | Geographic locations to search in |
| `remote_only` | bool | `false` | Only return remote positions |
| `experience_level` | string | `"mid"` | Filter: `entry`, `mid`, `senior`, `executive` |
| `excluded_companies` | string[] | `[]` | Companies to skip |

---

## Bot Settings

The `bot_settings` section controls bot behavior:

```json
{
  "bot_settings": {
    "apply_mode": "review",
    "max_applications_per_day": 50,
    "search_engines": ["linkedin", "indeed"]
  }
}
```

### Apply Modes

| Mode | Behavior |
|------|----------|
| `full_auto` | Searches, scores, generates documents, and applies automatically. No human intervention needed. |
| `review` | Searches and scores jobs, then pauses for your approval. You approve, reject, skip, or manually apply for each job. |
| `watch` | Searches and scores only. No applications are submitted. Useful for monitoring the job market. |

### Field Reference

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `apply_mode` | string | `"review"` | One of: `full_auto`, `review`, `watch` |
| `max_applications_per_day` | int | `50` | Daily application cap |
| `search_engines` | string[] | `["linkedin", "indeed"]` | Platforms to search |

---

## Schedule Configuration

The `schedule` section enables time-based automatic bot operation:

```json
{
  "schedule": {
    "enabled": true,
    "start_time": "09:00",
    "end_time": "17:00",
    "days_of_week": [0, 1, 2, 3, 4],
    "timezone": "America/Los_Angeles"
  }
}
```

### Field Reference

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable scheduled operation |
| `start_time` | string | `"09:00"` | Time to auto-start (HH:MM, 24-hour) |
| `end_time` | string | `"17:00"` | Time to auto-stop (HH:MM, 24-hour) |
| `days_of_week` | int[] | `[0,1,2,3,4]` | Days to run (0=Monday, 6=Sunday) |
| `timezone` | string | `"UTC"` | IANA timezone identifier |

When scheduling is enabled, the bot automatically starts at `start_time` and stops at `end_time` on the configured days. Outside the schedule window, the bot remains idle.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTOAPPLY_DEV` | unset | Set to `1` to enable development mode. Bypasses API authentication, enables debug logging. |
| `AUTOAPPLY_DEBUG` | unset | Set to `1` to enable Flask debug mode and verbose logging. |
| `AUTOAPPLY_LOG_FORMAT` | `text` | Log output format: `text` (human-readable) or `json` (structured). |
| `AUTOAPPLY_DATA_DIR` | `~/.autoapply` | Override the data directory path. Affects config, database, browser profile, and experience files locations. |

### Example Usage

```bash
# Run in development mode with JSON logging
export AUTOAPPLY_DEV=1
export AUTOAPPLY_LOG_FORMAT=json
python run.py

# Use a custom data directory
export AUTOAPPLY_DATA_DIR=/opt/autoapply/data
python run.py
```

---

## Full Config Example

```json
{
  "profile": {
    "full_name": "Jane Doe",
    "email": "jane@example.com",
    "phone": "+1-555-0100",
    "location": "San Francisco, CA",
    "linkedin_url": "https://linkedin.com/in/janedoe",
    "years_experience": 5,
    "education": "BS Computer Science, Stanford University",
    "skills": ["Python", "JavaScript", "AWS"],
    "screening_answers": {
      "Are you authorized to work in the US?": "Yes",
      "Do you require visa sponsorship?": "No"
    }
  },
  "search_criteria": {
    "job_titles": ["Software Engineer", "Backend Developer"],
    "locations": ["San Francisco, CA", "Remote"],
    "remote_only": false,
    "experience_level": "mid",
    "excluded_companies": []
  },
  "bot_settings": {
    "apply_mode": "review",
    "max_applications_per_day": 50,
    "search_engines": ["linkedin", "indeed"]
  },
  "llm": {
    "provider": "anthropic",
    "model": "claude-sonnet-4-20250514"
  },
  "schedule": {
    "enabled": false,
    "start_time": "09:00",
    "end_time": "17:00",
    "days_of_week": [0, 1, 2, 3, 4],
    "timezone": "America/Los_Angeles"
  }
}
```
