# How the Bot Works

When you hit **Start**, AutoApply runs a continuous loop: search for jobs, score them, generate tailored documents, and submit applications. Here's what happens at each step.

## The Pipeline

```
Search for jobs  →  Score & filter  →  Save JD  →  Generate resume  →  Apply  →  Save result
      ↑                                                                                │
      └────────────────────── wait, then repeat ───────────────────────────────────────┘
```

### 1. Search

AutoApply opens a browser and searches for jobs on your enabled platforms (LinkedIn, Indeed, or both). It uses the job titles, locations, and preferences you set during setup.

The browser runs in the background. It keeps your login sessions between runs, so you only need to log in once.

### 2. Score and Filter

Every job gets a score from 0 to 100 based on how well it matches your criteria:

| Factor | Points | How it's scored |
|--------|--------|-----------------|
| Job title | 0-35 | Full match to your target titles = 35. Partial word overlap = 20. No match = 0. |
| Salary | 0-20 | Meets your minimum = 20. Unknown salary = 10. Below minimum = 0. No minimum set = 20. |
| Location | 0-20 | Matches your locations = 20. Same state/country = 10. No match = 0. Remote + remote-only = 20. |
| Keywords | 0-25 | +5 points per matching include keyword, up to 25 max. |

Jobs are **automatically skipped** if:
- The company is on your blacklist
- An exclude keyword appears in the title or description
- You've already applied to this job

Jobs scoring below your **minimum match score** (default: 75) are also skipped.

### 3. Save Job Description

Before generating documents, AutoApply saves the full job description as a styled HTML file in `~/.autoapply/profile/job_descriptions/`. This is linked to the application in the dashboard — click "View Job Description" in the detail modal to review it later for interview prep, even if the original posting is taken down.

### 4. Generate Documents

For jobs that pass the filter, AutoApply creates a tailored resume and cover letter using your configured AI provider. See [How AI Generation Works](ai-generation.md) for details.

If no AI provider is configured, it uses your fallback resume and static cover letter template instead.

### 5. Apply

AutoApply fills out the application form automatically:
- Enters your name, email, phone number
- Uploads your resume PDF
- Pastes the cover letter (if the form has a field for it)
- Clicks through multi-step forms
- Submits the application

If it hits a CAPTCHA, it stops and logs the error — it won't try to solve CAPTCHAs.

If the job redirects to an unsupported application system (like Taleo or iCIMS), it marks the job as "manual required." AutoApply can automate LinkedIn Easy Apply, Indeed Quick Apply, Greenhouse, Lever, Workday, and Ashby forms.

### 6. Save and Repeat

Every application attempt is saved to your database with the result (applied, error, manual required, or captcha). You can see all of them in the Applications tab.

Click any row in the Applications table to open a **detail view** showing:
- Full job details (title, company, location, salary, match score)
- Current status (editable) and error message (if any)
- Notes field for tracking follow-ups
- Activity timeline showing every event the bot logged for that job
- Links to the original job posting, saved job description, generated resume, and cover letter

After finishing a search cycle, the bot waits for your configured **search interval** (default: 30 minutes), then starts again.

## Supported Platforms

| Platform | What's automated | What's not |
|----------|-----------------|------------|
| **LinkedIn** | Easy Apply jobs (the blue "Easy Apply" button) | External application links |
| **Indeed** | Quick Apply jobs (the "Apply now" button) | Jobs that redirect to company websites |
| **Greenhouse** | Standard application forms on `boards.greenhouse.io` | Custom question types beyond text fields |
| **Lever** | Application forms on `jobs.lever.co` | Custom question types beyond text fields |
| **Workday** | Multi-step applications on `*.myworkdayjobs.com` — personal info, resume upload, screening questions, EEO disclosures, and submission | Account creation requiring email verification, complex custom question types |
| **Ashby** | Application forms on `jobs.ashbyhq.com` (used by OpenAI, YC startups) — personal info, resume, cover letter, custom questions | Complex multi-part assessments |

All ATS platforms are detected automatically by URL — when a LinkedIn or Indeed listing links to a supported application page, AutoApply uses the correct form-filling strategy.

Jobs that require applying on an unsupported ATS (Taleo, iCIMS, etc.) are logged as "manual required" so you can apply to them yourself.

### Application Answers

Workday, Ashby, and other portals often ask screening questions during the application (work authorization, visa sponsorship, years of experience, etc.). AutoApply pre-fills these from your **Application Answers** in Settings.

To get the best results, fill out the Application Answers section in **Settings → Application Answers** before running the bot. The bot matches question labels to your saved answers and fills them automatically. See [Configuration](configuration.md#application-answers) for the full list of supported fields.

## Live Feed

While the bot runs, you'll see events in the dashboard feed:

| Event | What it means |
|-------|---------------|
| **Found** | A new job listing was discovered |
| **Filtered** | A job was skipped (low score, blacklist, duplicate, or excluded keyword) |
| **Generating** | Creating a tailored resume and cover letter |
| **Applying** | Filling out and submitting the application |
| **Applied** | Application submitted successfully |
| **Review** | Bot paused for your approval (review/watch mode only) |
| **Skipped** | You skipped a job during review |
| **CAPTCHA** | A CAPTCHA was detected — the bot skipped this job |
| **Error** | Something went wrong (network issue, form changed, etc.) |

## Scheduling

You can set the bot to run automatically on a schedule instead of starting it manually each time. Go to **Settings → Schedule** and configure:

- **Enable Schedule** — Turn the scheduler on or off.
- **Days of week** — Pick which days the bot should run (default: Mon-Fri).
- **Start / End time** — Set a time window in 24-hour format (default: 09:00-17:00). Overnight windows like 22:00-06:00 are supported.

When the schedule is enabled, the bot starts automatically when the time window opens and stops when it closes. The scheduler checks once per minute.

If you manually start the bot outside the schedule window, the scheduler will not interrupt it — it only stops bots it auto-started.

## Daily Limits

The bot respects your **daily application limit** (default: 50). Once it hits the limit, it stops applying and waits for the next day.

## Pausing and Stopping

- **Pause**: The bot finishes what it's doing, then waits. Hit Start to resume.
- **Stop**: The bot finishes the current action and shuts down. The browser closes.

Your login sessions are preserved, so you won't need to log in again next time.

## Tips for Best Results

- **Log into LinkedIn and Indeed first** — Open the dashboard, start the bot once, then log into your accounts in the browser that opens. Your sessions are saved for future runs.
- **Start with a high match score** (80-90) — Review what gets applied, then lower it if needed.
- **Keep the daily limit reasonable** — 20-30 per day looks more natural than 100.
- **Check the Applications tab regularly** — Follow up on "manual required" jobs yourself.
- **Update your experience files** — Better input = better AI-generated documents.
