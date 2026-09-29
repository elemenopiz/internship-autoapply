# API Reference

All endpoints are served by the Flask backend on `http://127.0.0.1:<port>` (port 5000-5010).

## Authentication

All API requests require a Bearer token in the `Authorization` header:

```
Authorization: Bearer <token>
```

The token is auto-generated on first run and stored at `~/.autoapply/.api_token`. The Electron frontend receives it via `window.__apiToken` injected by the Jinja2 template.

**Development bypass**: Set the environment variable `AUTOAPPLY_DEV=1` to disable authentication checks.

## Rate Limiting

Requests are rate-limited using a token bucket algorithm per client IP:

| Bucket | Limit | Applies To |
|--------|-------|------------|
| Bot | 10 requests/min | Bot start/stop, review actions |
| Write | 30 requests/min | PUT, POST, PATCH, DELETE operations |
| Read | 60 requests/min | GET operations |

When rate-limited, the API returns `429 Too Many Requests` with a `Retry-After` header indicating seconds until the next allowed request.

---

## Bot Blueprint

**Source**: `routes/bot.py`

### POST /api/bot/start

Start the bot loop.

**Request Body**: None

**Response** `200`:
```json
{
  "status": "running",
  "message": "Bot started"
}
```

**Response** `409`:
```json
{
  "error": "Bot is already running"
}
```

---

### POST /api/bot/stop

Stop the bot loop.

**Request Body**: None

**Response** `200`:
```json
{
  "status": "idle",
  "message": "Bot stopped"
}
```

---

### GET /api/bot/status

Get current bot status and review queue.

**Response** `200`:
```json
{
  "status": "running",
  "phase": "searching",
  "jobs_found": 15,
  "jobs_applied": 3,
  "review_queue": [
    {
      "id": "abc123",
      "title": "Software Engineer",
      "company": "Acme Corp",
      "score": 85,
      "platform": "linkedin",
      "url": "https://..."
    }
  ]
}
```

---

### POST /api/bot/review/:id/approve

Approve a job in the review queue for application.

**URL Parameters**:
- `id` (string) -- Application or review item ID.

**Response** `200`:
```json
{
  "message": "Application approved",
  "id": "abc123"
}
```

**Response** `404`:
```json
{
  "error": "Review item not found"
}
```

---

### POST /api/bot/review/:id/reject

Reject a job in the review queue.

**URL Parameters**:
- `id` (string) -- Review item ID.

**Response** `200`:
```json
{
  "message": "Application rejected",
  "id": "abc123"
}
```

---

### POST /api/bot/review/:id/skip

Skip a job in the review queue (can be reviewed later).

**URL Parameters**:
- `id` (string) -- Review item ID.

**Response** `200`:
```json
{
  "message": "Application skipped",
  "id": "abc123"
}
```

---

### POST /api/bot/review/:id/manual

Mark a job for manual application. Opens the job URL in the platform login browser.

**URL Parameters**:
- `id` (string) -- Review item ID.

**Response** `200`:
```json
{
  "message": "Marked for manual application",
  "id": "abc123"
}
```

---

## Applications Blueprint

**Source**: `routes/applications.py`

### GET /api/applications

List all applications with optional filtering and pagination.

**Query Parameters**:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `page` | int | 1 | Page number |
| `per_page` | int | 20 | Items per page (max 100) |
| `status` | string | -- | Filter by status: `applied`, `reviewing`, `rejected`, `interview`, `offer` |
| `platform` | string | -- | Filter by platform: `linkedin`, `indeed`, `greenhouse`, `lever`, `workday`, `ashby` |
| `search` | string | -- | Search in title and company |
| `sort` | string | `created_at` | Sort field |
| `order` | string | `desc` | Sort order: `asc` or `desc` |

**Response** `200`:
```json
{
  "applications": [
    {
      "id": "abc123",
      "title": "Software Engineer",
      "company": "Acme Corp",
      "platform": "linkedin",
      "status": "applied",
      "score": 85,
      "url": "https://...",
      "created_at": "2026-03-10T14:30:00Z",
      "updated_at": "2026-03-10T14:35:00Z"
    }
  ],
  "total": 42,
  "page": 1,
  "per_page": 20,
  "pages": 3
}
```

---

### GET /api/applications/:id

Get full details for a single application.

**URL Parameters**:
- `id` (string) -- Application ID.

**Response** `200`:
```json
{
  "id": "abc123",
  "title": "Software Engineer",
  "company": "Acme Corp",
  "platform": "greenhouse",
  "status": "applied",
  "score": 85,
  "url": "https://...",
  "job_description": "We are looking for...",
  "resume_path": "/path/to/generated/resume.pdf",
  "cover_letter": "Dear Hiring Manager...",
  "screening_answers": {},
  "created_at": "2026-03-10T14:30:00Z",
  "updated_at": "2026-03-10T14:35:00Z"
}
```

---

### PATCH /api/applications/:id

Update application status.

**URL Parameters**:
- `id` (string) -- Application ID.

**Request Body**:
```json
{
  "status": "interview"
}
```

Allowed status values: `applied`, `reviewing`, `rejected`, `interview`, `offer`, `closed`.

**Response** `200`:
```json
{
  "message": "Application updated",
  "id": "abc123",
  "status": "interview"
}
```

---

### DELETE /api/applications/:id

Delete an application record.

**URL Parameters**:
- `id` (string) -- Application ID.

**Response** `200`:
```json
{
  "message": "Application deleted",
  "id": "abc123"
}
```

---

### GET /api/applications/:id/events

Get the event timeline for an application.

**URL Parameters**:
- `id` (string) -- Application ID.

**Response** `200`:
```json
{
  "events": [
    {
      "type": "created",
      "timestamp": "2026-03-10T14:30:00Z",
      "details": "Job found via LinkedIn search"
    },
    {
      "type": "applied",
      "timestamp": "2026-03-10T14:35:00Z",
      "details": "Application submitted via Easy Apply"
    }
  ]
}
```

---

### GET /api/applications/export

Export all applications as CSV.

**Response**: `200` with `Content-Type: text/csv` and `Content-Disposition: attachment; filename=applications.csv`.

---

## Config Blueprint

**Source**: `routes/config.py`

### GET /api/config

Get the current configuration.

**Response** `200`:
```json
{
  "profile": {
    "full_name": "Jane Doe",
    "email": "jane@example.com",
    "phone": "+1-555-0100",
    "location": "San Francisco, CA",
    "linkedin_url": "https://linkedin.com/in/janedoe",
    "years_experience": 5,
    "education": "BS Computer Science, Stanford",
    "skills": ["Python", "JavaScript", "AWS"],
    "screening_answers": {}
  },
  "search_criteria": {
    "job_titles": ["Software Engineer"],
    "locations": ["San Francisco, CA"],
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

---

### PUT /api/config

Update the configuration. Accepts a partial or full config object.

**Request Body**: Same structure as GET response (partial updates allowed).

**Response** `200`:
```json
{
  "message": "Configuration saved"
}
```

**Response** `422`:
```json
{
  "error": "Validation error",
  "details": [...]
}
```

---

### GET /api/setup-status

Check if initial setup is complete.

**Response** `200`:
```json
{
  "setup_complete": true,
  "steps": {
    "profile": true,
    "experience_files": true,
    "ai_provider": true,
    "search_criteria": true
  }
}
```

---

### POST /api/validate-api-key

Validate an LLM API key.

**Request Body**:
```json
{
  "provider": "anthropic",
  "api_key": "sk-ant-..."
}
```

**Response** `200`:
```json
{
  "valid": true,
  "provider": "anthropic",
  "model": "claude-sonnet-4-20250514"
}
```

**Response** `400`:
```json
{
  "valid": false,
  "error": "Invalid API key"
}
```

---

## Profile Blueprint

**Source**: `routes/profile.py`

### GET /api/experience-files

List all uploaded experience files.

**Response** `200`:
```json
{
  "files": [
    {
      "name": "resume.pdf",
      "size": 245760,
      "uploaded_at": "2026-03-10T10:00:00Z"
    }
  ]
}
```

---

### POST /api/experience-files

Upload an experience file.

**Request**: `multipart/form-data` with a `file` field.

**Response** `201`:
```json
{
  "message": "File uploaded",
  "name": "resume.pdf"
}
```

---

### DELETE /api/experience-files/:name

Delete an experience file.

**URL Parameters**:
- `name` (string) -- File name.

**Response** `200`:
```json
{
  "message": "File deleted",
  "name": "resume.pdf"
}
```

---

### GET /api/experience-files/:name

Download an experience file.

**URL Parameters**:
- `name` (string) -- File name.

**Response**: `200` with file content and appropriate `Content-Type`.

---

## Analytics Blueprint

**Source**: `routes/analytics.py`

### GET /api/analytics/summary

Get analytics summary.

**Response** `200`:
```json
{
  "total_applications": 142,
  "today_applications": 12,
  "by_status": {
    "applied": 120,
    "interview": 15,
    "rejected": 5,
    "offer": 2
  },
  "by_platform": {
    "linkedin": 80,
    "indeed": 40,
    "greenhouse": 12,
    "lever": 10
  },
  "daily_counts": [
    {"date": "2026-03-10", "count": 15},
    {"date": "2026-03-09", "count": 22}
  ]
}
```

---

### GET /api/feed/history

Get the live feed history.

**Query Parameters**:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | int | 50 | Number of events to return |
| `offset` | int | 0 | Offset for pagination |

**Response** `200`:
```json
{
  "events": [
    {
      "type": "job_found",
      "message": "Found: Software Engineer at Acme Corp",
      "timestamp": "2026-03-10T14:30:00Z"
    }
  ],
  "total": 250
}
```

---

## Login Blueprint

**Source**: `routes/login.py`

### POST /api/login/open

Open a platform login browser for the user to authenticate.

**Request Body**:
```json
{
  "platform": "linkedin",
  "url": "https://www.linkedin.com/login"
}
```

The URL is validated against an allowlist of known domains.

**Response** `200`:
```json
{
  "message": "Login browser opened",
  "platform": "linkedin"
}
```

---

### POST /api/login/close

Close the platform login browser.

**Response** `200`:
```json
{
  "message": "Login browser closed"
}
```

---

### GET /api/login/status

Check if a login browser is currently open.

**Response** `200`:
```json
{
  "open": true,
  "platform": "linkedin"
}
```

---

## Lifecycle Blueprint

**Source**: `routes/lifecycle.py`

### GET /api/health

Health check endpoint.

**Response** `200`:
```json
{
  "status": "ok",
  "version": "1.9.0"
}
```

---

### POST /api/shutdown

Initiate graceful shutdown of the Flask backend.

**Response** `200`:
```json
{
  "message": "Shutting down"
}
```

The server completes in-flight requests, stops the bot if running, closes browser sessions, and then exits.

---

### GET /api/locales

List available locale files.

**Response** `200`:
```json
{
  "locales": ["en"],
  "default": "en"
}
```

---

## WebSocket Events (SocketIO)

The Flask backend emits real-time events over SocketIO:

| Event | Payload | Description |
|-------|---------|-------------|
| `bot_status` | `{status, phase}` | Bot state change |
| `job_found` | `{title, company, score, platform}` | New job discovered |
| `job_applied` | `{title, company, platform, id}` | Application submitted |
| `job_review` | `{id, title, company, score}` | Job added to review queue |
| `feed_event` | `{type, message, timestamp}` | General feed event |
| `error` | `{message}` | Error notification |

**Client connection example**:
```javascript
const socket = io({
  auth: { token: window.__apiToken }
});

socket.on('job_found', (data) => {
  console.log(`Found: ${data.title} at ${data.company}`);
});
```

---

## Error Responses

All error responses follow a consistent format:

```json
{
  "error": "Human-readable error message"
}
```

| Status Code | Meaning |
|-------------|---------|
| 400 | Bad request (invalid input) |
| 401 | Unauthorized (missing or invalid token) |
| 404 | Resource not found |
| 409 | Conflict (e.g., bot already running) |
| 422 | Validation error (Pydantic) |
| 429 | Rate limited (check `Retry-After` header) |
| 500 | Internal server error (no stack trace exposed) |
