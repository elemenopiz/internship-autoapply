# Troubleshooting

## "AI Provider not configured" warning

**What it means**: No AI API key is set up. AutoApply can't generate tailored resumes and cover letters without one.

**How to fix**:
1. Go to **Settings → AI Provider**
2. Select a provider (Anthropic, OpenAI, Google, or DeepSeek)
3. Enter your API key
4. Click **Validate** to confirm it works
5. Click **Save**

AutoApply works without an API key — it just uses generic templates instead of tailored documents.

## App won't start

**Check Python is running**:
- Make sure you ran `python setup_env.py` with your virtual environment activated
- Check `~/.autoapply/backend.log` for error messages

**Port conflict**:
- AutoApply auto-detects a free port in the range 5000-5010. If all are taken, it fails
- Close other apps using those ports, or set `AUTOAPPLY_PORT=8080` as an environment variable

## "ModuleNotFoundError: No module named 'flask'"

You're running Python outside the virtual environment. Re-run setup:

```bash
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

python setup_env.py
```

## Login button does nothing / browser doesn't open

**What it means**: Playwright can't find a Chromium browser to launch.

**How to fix**:
1. Make sure you installed Playwright's Chromium: `playwright install chromium`
2. This is required even if you're running the Electron desktop app — Electron's built-in Chromium cannot be used for Playwright's persistent browser contexts
3. After installing, restart AutoApply and try the login button again

**Note**: Playwright needs its own Chromium installation (~150MB). This is separate from Electron's bundled Chromium because Playwright requires a persistent browser context with a custom user data directory, which is incompatible with Electron's embedded browser.

## Bot starts but doesn't find any jobs

- **Log into LinkedIn/Indeed first.** Start the bot once, log into your accounts in the browser that opens, then restart. See [Setup](setup.md#log-into-your-job-platforms).
- Check your job titles and locations in Settings — try broader terms like "Engineer" instead of "Senior Staff Software Engineer II".
- If using "Remote only", some jobs aren't tagged as remote even if they are. Try turning it off and adding "Remote" to your locations.

## Bot finds jobs but doesn't apply

- Your **match score threshold** might be too high. Try lowering it to 60-70 in Settings.
- Check the activity feed for "Filtered" events — they show exactly why jobs were skipped.
- Make sure your **exclude keywords** aren't too aggressive (e.g., excluding "senior" skips all senior roles).

## "CAPTCHA detected" errors

The bot can't solve CAPTCHAs. This usually happens when:
- You're applying too fast — increase the **delay between applications** in Settings (try 60-90 seconds)
- Your account has been flagged — take a break for a few hours
- The platform is rate-limiting — try again later

## "Manual required" applications

These are jobs that redirect to an application system AutoApply can't automate (Taleo, iCIMS, custom company portals, etc.).

AutoApply **can** automate:
- LinkedIn Easy Apply
- Indeed Quick Apply
- Greenhouse forms (`boards.greenhouse.io`)
- Lever forms (`jobs.lever.co`)
- Workday forms (`*.myworkdayjobs.com`)
- Ashby forms (`jobs.ashbyhq.com`)

Jobs on unsupported systems are saved in the Applications tab with the apply URL so you can submit them yourself.

## Workday applications failing

- **Fill out Application Answers first** — Workday asks many screening questions. Go to **Settings → Application Answers** and fill in work authorization, visa sponsorship, years of experience, etc.
- **Account creation**: If the Workday portal requires creating an account, the bot attempts to sign in with your email. Some portals require email verification which the bot cannot complete — these are marked as manual required.
- **Dropdown selections not matching**: Workday dropdown values must match exactly. If your state or country selection isn't working, check that the value in your profile matches what Workday expects (e.g., "California" not "CA").

## Bot stops unexpectedly

Check the terminal or `~/.autoapply/backend.log` for errors. Common causes:
- Internet connection dropped
- LinkedIn or Indeed changed their page layout (rare)
- The browser crashed — just restart the bot

## Generated resume looks wrong

- Add more detail to your experience files — vague input produces vague output
- The resume uses Helvetica font and simple formatting for ATS compatibility (no columns, colors, or tables)
- Resumes are kept to one page by default

## Where is my data?

Everything is stored locally at `~/.autoapply/`:

| Path | What's there |
|------|-------------|
| `config.json` | Your settings |
| `autoapply.db` | Application history (SQLite database) |
| `profile/experiences/` | Your experience `.txt` files |
| `profile/resumes/` | Generated resumes (PDF + Markdown) |
| `profile/cover_letters/` | Generated cover letters |
| `profile/job_descriptions/` | Saved job postings (HTML) |
| `browser_profile/` | Saved browser login sessions |
| `backend.log` | Backend log (desktop app only) |

## Running tests

For developers — run with the virtual environment activated:

```bash
pytest                                        # All tests
pytest --cov=. --cov-report=term-missing      # With coverage
```

Exit code 15 on Windows is a gevent signal handling quirk, not a real failure. If all tests show "passed", you're fine.
