# Getting Started

## Prerequisites

| Platform | Minimum Version |
|----------|----------------|
| Windows | 10 (64-bit) |
| macOS | 12 Monterey |
| Ubuntu/Debian | 22.04 LTS |

Additionally you need:

- **Python 3.11+** (for source installs)
- **Node.js 18+** and **npm 9+** (for source installs)
- **Git** (for source installs)
- At least **2 GB free disk space** (includes Playwright Chromium)

---

## Installation from Installer

1. Go to [GitHub Releases](https://github.com/AbhishekMandapmalvi/AutoApply/releases).
2. Download the installer for your platform:
   - **Windows**: `AutoApply-Setup-x.y.z.exe`
   - **macOS**: `AutoApply-x.y.z.dmg`
   - **Linux**: `AutoApply-x.y.z.AppImage`
3. Run the installer and follow the on-screen prompts.

> **Note (Windows):** The installer is unsigned, so Windows SmartScreen may show a warning. Click "More info" then "Run anyway".

> **Note (macOS):** The app is unsigned, so Gatekeeper may block it. Right-click the app and select "Open" to bypass the warning on first launch.

4. Launch AutoApply from your applications menu or desktop shortcut.

---

## Installation from Source

### 1. Clone the repository

```bash
git clone https://github.com/AbhishekMandapmalvi/AutoApply.git
cd AutoApply
```

### 2. Set up the Python backend

```bash
python -m venv venv

# Activate the virtual environment
# Windows:
venv\Scripts\activate
# macOS / Linux:
source venv/bin/activate

# Install dependencies (including dev extras)
pip install -e ".[dev]"

# Install Playwright's Chromium browser
playwright install chromium
```

### 3. Set up the Electron frontend

```bash
cd electron
npm install
```

### 4. Start the application

```bash
# From the electron/ directory
npm start
```

This launches the Electron shell, which automatically spawns the Flask backend as a child process.

### Running Flask standalone (without Electron)

For development or debugging, you can run the Flask backend directly:

```bash
# From the project root, with venv activated
python run.py
```

The API server starts on `http://127.0.0.1:5000` (or the next available port up to 5010).

---

## Setup Wizard Walkthrough

On first launch, AutoApply presents a 7-step setup wizard:

### Step 1: Welcome
Introduction to AutoApply and what the wizard will configure.

### Step 2: Profile
Enter your personal details:
- Full name
- Email address
- Phone number
- Location (city, state/country)
- LinkedIn URL
- Years of experience
- Education
- Skills (comma-separated)

### Step 3: Experience Files
Upload your existing resume and any supporting documents (cover letter templates, project descriptions). These are stored in `~/.autoapply/experience_files/` and used by the AI engine to generate tailored applications.

### Step 4: AI Provider
Choose and configure your LLM provider:
- **Anthropic** (Claude) -- default model: `claude-sonnet-4-20250514`
- **OpenAI** (GPT) -- default model: `gpt-4o`
- **Google** (Gemini) -- default model: `gemini-2.0-flash`
- **DeepSeek** -- default model: `deepseek-chat`

Enter your API key. The wizard validates it with a test call before proceeding. Keys are stored securely in your OS keyring when available.

### Step 5: Search Criteria
Define what jobs to look for:
- Job titles (e.g., "Software Engineer", "Backend Developer")
- Locations (e.g., "San Francisco, CA", "Remote")
- Remote only toggle
- Experience level (entry, mid, senior)
- Excluded companies

### Step 6: Apply Mode
Choose how the bot handles applications:
- **Full Auto**: Searches, generates documents, and applies automatically.
- **Review**: Searches and scores jobs, but pauses for your approval before applying.
- **Watch**: Searches and scores only -- no applications submitted.

### Step 7: Done
Summary of your configuration. Click "Start" to begin your first bot run.

---

## First Bot Run

1. After completing the wizard, click **Start Bot** on the dashboard.
2. The bot enters its main loop:
   - **Searching** -- Queries LinkedIn and/or Indeed for matching jobs.
   - **Filtering** -- Scores each job against your criteria.
   - **Generating** -- Uses AI to tailor your resume and cover letter (if configured).
   - **Applying** -- Submits applications on supported platforms.
3. Watch progress in the **Live Feed** panel on the dashboard.
4. View submitted applications in the **Applications** tab.

If you selected **Review** mode, the bot pauses after scoring and presents each job for your approval. Click Approve, Reject, Skip, or Apply Manually for each job.

---

## Troubleshooting

### Port conflicts

AutoApply tries ports 5000 through 5010. If all are occupied:

```
ERROR: Could not find an available port in range 5000-5010
```

**Fix**: Close other applications using those ports, or set a custom port:

```bash
# Set a custom data directory (which also affects port selection)
export AUTOAPPLY_DATA_DIR=/path/to/data
```

### Playwright Chromium not installed

```
playwright._impl._errors.Error: Executable doesn't exist at ...
```

**Fix**: Install Playwright's bundled Chromium:

```bash
playwright install chromium
```

> **Important**: Playwright requires its own Chromium installation. It cannot use Electron's bundled Chromium because Playwright needs persistent browser contexts with a custom user data directory.

### gevent exit code 15 on Windows

When running tests on Windows, you may see the process exit with code 15. This is a known gevent signal-handling quirk on Windows and **does not indicate a test failure**. Check the actual test results (pass/fail counts) rather than the exit code.

### Application fails to start on macOS

If you see a Gatekeeper error:
1. Open **System Preferences > Security & Privacy > General**.
2. Click **Open Anyway** next to the AutoApply message.

Alternatively, right-click the app and select "Open" from the context menu.

### API key validation fails

- Verify the key is correct and has not expired.
- Check your internet connection.
- Ensure your provider account has API access enabled and sufficient credits.
- Try the key with `curl` to isolate whether the issue is in AutoApply or the provider.

### Browser automation issues

- Clear the browser profile: delete `~/.autoapply/browser_profile/` and restart.
- Ensure no other Playwright instances are running.
- On Linux, install required system dependencies: `playwright install-deps chromium`.
