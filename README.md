# Summer 2027 internship auto-applier

This is a local Windows setup built around AutoApply. It reads the verified-opportunities sheet in the UT Austin workbook, keeps only recent open Summer 2027 internship records, scores roles against the saved search profile, creates tailored documents, and can submit through supported ATS sites.

The current search is aimed at product management, technical program management, technology consulting, strategy, business operations, business analysis, and adjacent analytics roles. It also searches LinkedIn and Indeed when those platforms are enabled in the dashboard.

## Start it

1. Run `set_openai_key.ps1` to save your OpenAI API key in the Windows user environment as `OPENAI_API_KEY`. Do not put the key in this file or in chat.
2. Run `check_ready.ps1` to see the remaining profile fields.
3. Add your resume through the dashboard or place the path in `data/config.json` under `profile.fallback_resume_path`.
4. Fill in the profile fields and screening answers in the dashboard. Answers are saved locally and reused on later applications.
5. Run `launch.ps1`.

The app starts in `full_auto` mode with a five-application daily cap. Schedule is disabled until the profile is complete. Enable it from the dashboard after the first successful dry run.

## Safety gates

The applier refuses to start (and `check_ready.ps1` lists what is missing) until the profile fields, a resume PDF, and `OPENAI_API_KEY` are all present. It never invents background: tailored documents are generated only from the experience files in `data/profile/experiences` (or the knowledge base built from an uploaded resume). If neither exists, it falls back to your own uploaded resume. The five-per-day cap is counted from the local database by calendar day, so restarting the app does not reset it. A key supplied through `OPENAI_API_KEY` is never copied into `config.json` or the Windows credential store, so replacing the variable with `set_openai_key.ps1` always takes effect.

Screening answers are saved locally and reused on later applications. Review them in the dashboard before running the applier.

## Coverage

Workday applications are supported, including McKesson, Boeing, and the Wells Fargo posting after its Apply link redirects to Workday. Tesla, Cemex, Keurig Dr Pepper, and other employer-specific application portals are discovered and tracked, but the current applier records them for manual follow-up until a matching portal adapter is added.
