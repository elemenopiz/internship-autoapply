# Setup & Installation

## What You Need

- **Python 3.11 or newer** — [Download from python.org](https://python.org/downloads/)
- **Node.js 18+** — [Download](https://nodejs.org)
- **AI API key** (optional) — For AI-generated resumes and cover letters. Supports Anthropic, OpenAI, Google, or DeepSeek. Configure in Settings → AI Provider.

Without an API key, AutoApply still works — it just uses generic templates instead of tailored documents.

## Install and Run

```bash
# Set up Python
python -m venv venv

# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

python setup_env.py

# Install Playwright's Chromium browser (required for job searching/applying)
playwright install chromium

# Launch the desktop app
cd electron
npm install
npm start
```

A native app window opens with the dashboard.

## First Launch: Setup Wizard

On first launch, a wizard walks you through 7 steps:

1. **Personal Info** — Name, email, phone, address, bio, LinkedIn/portfolio URLs. Includes a collapsible **Application Answers** section for screening questions (work authorization, visa sponsorship, years of experience, salary, relocation, start date).
2. **Job Preferences** — Job titles you're targeting, preferred locations, remote preference
3. **Filters** — Minimum salary, keywords to include or exclude
4. **Experience Level** — Mid, senior, staff, etc.
5. **Fallback Resume** — Upload a PDF resume for when no AI provider is configured
6. **Platform Login** — Log into LinkedIn and/or Indeed
7. **Done** — Summary and go to dashboard

Everything is editable later in the Settings tab. The full set of Application Answers (including EEO disclosures) is available in **Settings → Application Answers**.

## Log Into Your Job Platforms

This step is important. AutoApply uses a real browser to search and apply, so it needs your login sessions.

1. Start the bot once (hit the **Start** button)
2. A browser window opens in the background
3. Go to LinkedIn and/or Indeed and log in normally
4. Stop the bot

Your login sessions are saved. You won't need to log in again — AutoApply reuses the saved session on future runs.

## Next Steps

1. **[Write your experience files](experience-files.md)** — This is how AutoApply learns about your background to generate tailored resumes
2. **Review your settings** — Make sure job titles and locations match what you want
3. **Start the bot** — Hit Start on the dashboard and watch the activity feed
