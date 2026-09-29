# Bot Operations

## Bot Lifecycle

The bot transitions through the following states:

```
         ┌──────────┐
         │          │
    ┌───▶│   IDLE   │◀──── stop / schedule end
    │    │          │
    │    └────┬─────┘
    │         │ start / schedule begin
    │         ▼
    │    ┌──────────┐
    │    │SEARCHING │───── querying LinkedIn / Indeed
    │    └────┬─────┘
    │         │ jobs found
    │         ▼
    │    ┌──────────┐
    │    │FILTERING │───── scoring + ATS detection
    │    └────┬─────┘
    │         │ qualified jobs
    │         ▼
    │    ┌──────────┐
    │    │APPLYING  │───── form filling + submission
    │    └────┬─────┘
    │         │ cycle complete
    │         ▼
    │    ┌──────────┐
    │    │ PAUSED   │───── waiting for next cycle / review
    │    └────┬─────┘
    │         │ next cycle / all reviewed
    └─────────┘
```

| State | Description |
|-------|-------------|
| **Idle** | Bot is stopped. No background activity. |
| **Searching** | Querying job boards for listings matching search criteria. |
| **Filtering** | Scoring found jobs and detecting ATS platforms. |
| **Applying** | Generating tailored documents and submitting applications. |
| **Paused** | Waiting between cycles, or waiting for user review in review mode. |

---

## Main Loop Flow

The bot's main loop (`bot/bot.py :: run_bot()`) executes this pipeline repeatedly:

```
┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐
│ Search  │───▶│ Filter  │───▶│Generate │───▶│  Apply  │───▶│  Save   │───▶│  Emit   │
│         │    │ & Score │    │  Docs   │    │         │    │  to DB  │    │ Events  │
└─────────┘    └─────────┘    └─────────┘    └─────────┘    └─────────┘    └─────────┘
```

### 1. Search
- Iterates over configured `search_engines` (LinkedIn, Indeed).
- Each searcher queries with `job_titles` and `locations` from search criteria.
- Returns `RawJob` objects (title, company, URL, location, description snippet).
- Deduplicates against previously seen jobs (by URL).

### 2. Filter and Score
- `score_job()` evaluates each `RawJob` against user criteria:
  - **Keyword matching** -- title and description vs. job titles and skills.
  - **Location matching** -- job location vs. preferred locations, remote preference.
  - **Experience level** -- entry/mid/senior alignment.
  - **Excluded companies** -- hard filter, score = 0 if matched.
- `detect_ats()` identifies the application platform from the job URL.
- Returns `ScoredJob` objects with a numeric score (0-100).
- Jobs below the score threshold are discarded.

### 3. Generate Documents
- If an AI provider is configured, calls `generate_documents()` to produce:
  - A tailored resume (rendered to PDF via ReportLab).
  - A tailored cover letter.
- If no AI provider, falls back to template-based generation using profile data.
- Reads all experience files from `~/.autoapply/experience_files/` for context.

### 4. Apply
- In **full_auto** mode: proceeds immediately.
- In **review** mode: adds to review queue and waits for user action (approve/reject/skip/manual).
- In **watch** mode: skips application entirely.
- Selects the appropriate applier from the `APPLIERS` dict based on detected platform.
- Applier fills the form and submits with human-like behavior.

### 5. Save to DB
- Creates an application record in SQLite with status, metadata, and event history.
- Stores job description text for reference.

### 6. Emit Events
- Pushes real-time updates via SocketIO to the dashboard:
  - `job_found` -- new job discovered
  - `job_applied` -- application submitted
  - `job_review` -- job added to review queue
  - `feed_event` -- general progress updates

---

## Apply Modes

### Full Auto

```
Search → Filter → Generate → Apply → Save
```

The bot operates completely autonomously. Every job that meets the score threshold is applied to automatically, up to `max_applications_per_day`.

**Best for**: Broad job searches where you want maximum application volume.

### Review

```
Search → Filter → Generate → [PAUSE: User reviews] → Apply (if approved) → Save
```

The bot finds and scores jobs, then presents them in the review queue on the dashboard. For each job, you can:

- **Approve** -- Bot submits the application.
- **Reject** -- Job is discarded.
- **Skip** -- Job stays in queue for later review.
- **Apply Manually** -- Opens the job URL in the platform login browser for you to apply yourself.

**Best for**: Targeted searches where you want control over which jobs to apply to.

### Watch

```
Search → Filter → Save (as "watching")
```

The bot finds and scores jobs but never submits applications. Useful for monitoring the job market, testing search criteria, or building a list of interesting positions.

**Best for**: Market research, criteria tuning, or when you are not actively job searching.

---

## Supported Platforms

AutoApply can search on and apply to the following platforms:

| Platform | Search | Apply | Applier Class | Key Technique |
|----------|--------|-------|---------------|---------------|
| LinkedIn | Yes | Yes (Easy Apply) | `LinkedInApplier` | Multi-step Easy Apply modal |
| Indeed | Yes | Yes (Quick Apply) | `IndeedApplier` | Quick Apply flow |
| Greenhouse | No | Yes | `GreenhouseApplier` | Standard form fill (`boards.greenhouse.io`) |
| Lever | No | Yes | `LeverApplier` | Standard form fill (`jobs.lever.co`) |
| Workday | No | Yes | `WorkdayApplier` | Multi-step wizard (`data-automation-id` selectors) |
| Ashby | No | Yes | `AshbyApplier` | Single-page form (`jobs.ashbyhq.com`) |

Greenhouse, Lever, Workday, and Ashby are ATS (Applicant Tracking System) portals. The bot reaches them when LinkedIn or Indeed job listings redirect to an external application page on one of these platforms.

---

## ATS Detection

ATS detection uses URL fingerprinting defined in `ATS_FINGERPRINTS` (in `core/filter.py`). The bot examines the job application URL and matches against 9 known patterns:

| URL Pattern | Detected Platform |
|-------------|-------------------|
| `linkedin.com` | linkedin |
| `indeed.com` | indeed |
| `boards.greenhouse.io` | greenhouse |
| `greenhouse.io/embed` | greenhouse |
| `jobs.lever.co` | lever |
| `lever.co/apply` | lever |
| `myworkdayjobs.com` | workday |
| `wd5.myworkday.com` | workday |
| `jobs.ashbyhq.com` | ashby |

If no fingerprint matches, the job is skipped (no generic form-filling is attempted).

---

## Job Scoring

The `score_job()` function in `core/filter.py` produces a score from 0 to 100:

| Factor | Weight | Description |
|--------|--------|-------------|
| Title match | 40 | How well the job title matches configured `job_titles` |
| Skill match | 25 | Keywords from `skills` found in the job description |
| Location match | 20 | Geographic or remote alignment |
| Experience level | 15 | Entry/mid/senior alignment with `experience_level` |
| Excluded company | -100 | Hard disqualification if company is in `excluded_companies` |

Jobs with a score below the threshold (default: 30) are discarded without further processing.

---

## Human-Like Automation

All appliers inherit from `BaseApplier` (`bot/apply/base.py`) which provides human-like interaction patterns to reduce detection risk:

| Behavior | Range | Implementation |
|----------|-------|----------------|
| Typing delay | 30-80ms per character | `_human_type()` with random jitter |
| Click pauses | 0.5-2.0s between actions | `_random_pause()` with uniform distribution |
| Scroll behavior | Gradual scrolling | Page scrolls in increments |
| Captcha detection | Per-page check | `_detect_captcha()` pauses and alerts user |

If a CAPTCHA is detected, the bot pauses and emits an event to the UI, allowing the user to solve it manually in the login browser.

---

## Browser Session

AutoApply uses a **persistent Playwright browser context** managed by `BrowserManager` (`bot/browser.py`):

- **Profile directory**: `~/.autoapply/browser_profile/`
- **Session persistence**: Cookies, localStorage, and login sessions survive between bot runs.
- **Single instance**: Only one browser context is active at a time.
- **Separate from Electron**: Playwright uses its own Chromium binary (installed via `playwright install chromium`), not Electron's bundled Chromium.

### Login Flow

Before the bot can search or apply on LinkedIn/Indeed, you must be logged in:

1. Click the platform login button on the dashboard (or use `POST /api/login/open`).
2. A Playwright browser window opens to the platform's login page.
3. Log in with your credentials.
4. Close the login browser (`POST /api/login/close`).
5. Your session is saved in the persistent context and reused for all bot operations.

---

## Screening Answers

For ATS platforms that present screening questions (especially Workday and Ashby), AutoApply auto-fills answers from `profile.screening_answers` in your configuration:

```json
{
  "screening_answers": {
    "Are you authorized to work in the US?": "Yes",
    "Do you require visa sponsorship?": "No",
    "What is your expected salary?": "$150,000",
    "Are you willing to relocate?": "Yes"
  }
}
```

The applier compares each screening question against the keys in your answers dictionary using fuzzy matching. If a match is found, the answer is filled automatically. Unmatched questions are left blank (and reported in the application event log).

You can manage screening answers through:
- **Settings UI**: Profile > Screening Answers section
- **Setup Wizard**: Step 2 (Profile) includes a screening answers sub-section
- **Direct editing**: Modify `config.json` under `profile.screening_answers`
