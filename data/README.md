# data/

Runtime state lives here (created by `autoapply init` / first launch). Everything in this folder except this
file is git-ignored because it contains personal information:

- `config.json`            settings, profile fields, search profile, platform toggles (never secrets)
- `autoapply.db`           SQLite: opportunities, applications, screening answers, runs
- `profile/experiences/`   your experience files (source of truth for tailored documents)
- `profile/resume.pdf`     your own uploaded resume (fallback document)
- `documents/`             tailored resumes / cover letters per application
- `artifacts/`             screenshots / traces from application attempts
- `browser_profile/`       persistent browser profile (ATS sessions)
- `STOP`                   kill switch: if this file exists no application is started
