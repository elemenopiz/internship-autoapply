# SPEC — Summer 2027 internship auto-applier

Authoritative build spec. The README describes the product; this file says how it is built and when it is done.
Contract files (`models.py`, `contracts.py`, `config.py`, `normalize.py`, `clock.py`, `testing/mock_ats/base.py`,
`tests/conftest.py`, this file, `pyproject.toml`) are owned by the orchestrator: never edit them, report a
`CONTRACT-REQUEST` instead.

## 0. Definition of done

The project is done when every acceptance criterion in section 9 passes in the hermetic suite
(`scripts/gate.sh` + `pytest tests/acceptance`) and independent code + security review find nothing blocking.
"Autonomous" means: after the one-time setup (profile, resume, `OPENAI_API_KEY`, authorisation flag) a scheduled
run discovers, scores, tailors and submits applications with **zero human input** until the daily cap is reached.
Live employer sites and OpenAI are unreachable from the build sandbox, so everything is verified against local
mock ATS servers, a fake LLM and a fake mailbox; `autoapply doctor --live-dry-run <url>` exists so the user can
validate adapters against real sites (fills forms, never submits) on their own machine.

## 1. Non-negotiable product rules

1. **Never invent background.** Tailored documents come only from `data/profile/experiences` (or the knowledge base
   built from the uploaded resume). The LLM never emits employers, titles, dates or schools: the renderer takes those
   from the structured KB by id. Every rephrased bullet/sentence passes the grounding validator (section 5.5) or is
   reverted to the source text. No KB and no experience files -> attach the user's own resume unchanged.
2. **Never guess factual or legal answers.** Work authorisation, sponsorship, criminal history, clearance,
   citizenship, veteran/disability/gender/race (default *decline*), salary, relocation, age, non-compete... come from
   the profile or a saved user answer. Unknown + required -> `NEEDS_MANUAL(MISSING_ANSWER)` + a pending question in
   the dashboard; the run moves on. Never fabricate.
3. **Certification / consent / signature boxes** are ticked only if `config.apply.attestations_authorized` is true
   (one explicit user authorisation at setup); else `NEEDS_MANUAL(ATTESTATION_NOT_AUTHORIZED)`.
4. **No anti-bot circumvention.** No CAPTCHA solving, no fingerprint spoofing, no proxy rotation. A visible
   challenge -> stop, `NEEDS_MANUAL(BOT_CHECK)`. Polite pacing between applications (`apply.min_delay_s..max_delay_s`).
5. **LinkedIn / Indeed are discovery-only and opt-in.** Never use LinkedIn Easy Apply or any on-platform apply
   flow; follow the employer's external apply link. See section 7.
6. **Safety gates:** refuse to apply until ready (profile fields, resume PDF, `OPENAI_API_KEY`, and for full_auto the
   attestation authorisation); daily cap (default 5) counted from the DB by *calendar day in `config.timezone`*;
   never apply twice to the same job (same id or same fingerprint); kill switch (`data/STOP` file or dashboard
   button) checked between every step; the cap and idempotency survive restarts.
7. **Secrets:** `OPENAI_API_KEY` from the environment always wins and is never copied to `config.json`, the DB, logs
   or the credential store. Other secrets (ATS passwords, IMAP app password) live only in the OS credential store
   (`keyring` -> Windows Credential Manager). Passwords/keys never appear in logs, traces, API responses, artifacts.
8. **Tests can never submit a real application:** tests/e2e use only mock sites on `*.localhost`; the test browser is
   launched loopback-restricted (`--host-resolver-rules="MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1"`);
   `FakeLLM` only when `AUTOAPPLY_TESTING=1` or injected in code.
9. **The LLM is an enhancer, not a dependency of correctness:** every call site catches `LLMError` and falls back to a
   deterministic path or a defined failure outcome. A run never crashes on the LLM.

## 2. Architecture

```
sources (workbook, boards, linkedin, indeed) -> dedupe -> DB.opportunities
   -> scoring -> eligible (score >= min_score, open, not applied, attempts left)
   -> tailor (KB -> grounded PDF resume + cover letter | fallback: user's resume)
   -> apply.engine (browser, blockers, adapter registry) -> adapter (workday|greenhouse|lever|ashby|generic)
        uses: AnswerEngine, AccountManager (+EmailVerifier), TailoredDocs, Profile
   -> DB.applications (+ pending questions, artifacts)  -> dashboard / CLI / scheduler
```

Sync Playwright API everywhere; the pipeline runs in a worker thread (dashboard/scheduler) or inline (CLI); the
FastAPI dashboard only reads/writes DB+config and enqueues runs. SQLite (WAL, one connection per thread).

```
src/autoapply/
  models.py contracts.py config.py normalize.py clock.py        (contract files)
  db.py secrets.py llm.py readiness.py                          (5.1, 5.2)
  sources/{__init__,workbook,boards,dedupe,linkedin,indeed}.py  (5.3)
  scoring.py                                                    (5.4)
  tailor/{knowledge,generate,grounding,render}.py               (5.5)
  apply/{engine,browser,blockers,registry,answers,accounts,generic,emailverify}.py  adapters/{workday,greenhouse,lever,ashby}.py   (5.6-5.9)
  pipeline.py scheduler.py cli.py __main__.py                   (5.10-5.12)
  dashboard/{app.py,templates/,static/}                         (5.13)
  testing/{fake_llm.py,fixtures.py,mock_ats/*}                  (5.14)
set_openai_key.ps1 check_ready.ps1 launch.ps1                   (repo root, 5.12)
```

## 3. Working rules for every worker

- Work in your own git worktree/branch. Own ONLY the files your task lists; tests go in `tests/unit/<area>/`.
- Python: `/home/user/.venvs/autoapply/bin/python`, always `PYTHONPATH=src`. Do not install into the shared venv unless
  unavoidable (say so). Style: type hints everywhere (mypy `disallow_untyped_defs`), ruff-clean, small functions,
  docstrings that state behaviour and edge cases, comments only for non-obvious *why*.
- Gate before every commit: `scripts/gate.sh` (ruff format+check, mypy, pytest). It must be green for the WHOLE repo.
- Tests are hermetic: no real network (conftest blocks it); browser tests only against `testing/mock_ats`; mark them
  `@pytest.mark.browser`. Prefer many small deterministic tests; test failure paths and Windows-hostile inputs
  (spaces/unicode in paths, CRLF). Use `pathlib`; no POSIX-only APIs (`fcntl`, `os.fork`, `signal.SIGKILL`, `/tmp`).
- New third-party dependency: avoid; if essential, add to `pyproject.toml` and call it out in your report.
- Commit to your branch (do not push, do not merge). Final message <= 250 words: `BRANCH`, `HEAD`, files, test
  counts, deviations, `CONTRACT-REQUEST`s, known gaps. Do not paste code.
- Ambiguity: choose the safest interpretation, note it in your report. Never weaken a rule in section 1.

## 4. Data directory (`AppPaths`)

`data/{config.json, autoapply.db, profile/experiences/*.json|*.md, profile/knowledge_base.json, profile/resume.pdf,
documents/<application_id>/, artifacts/<application_id>/, browser_profile/, logs/, STOP}`. All git-ignored (PII).

## 5. Module specs

### 5.1 `db.py` — SQLite persistence
`Database(path)` (`connect()` per-thread; WAL; `foreign_keys=ON`; `busy_timeout=10000`; `Row` factory;
`migrate()` idempotent via `PRAGMA user_version`) and `Repo(db)` with at least:
- opportunities: `upsert_opportunity(op) -> (Opportunity, is_new)` (keeps `first_seen`, updates `last_seen`, merges
  by id; never regresses `is_open` from a fresher record without evidence), `get_opportunity`, `list_opportunities(
  min_score=None, status=None, source=None, search=None, limit=None, offset=0, order="score_desc"|"seen_desc")`,
  `set_score(id, ScoreResult)`, `count_opportunities`.
- applications: `create_application(opportunity_id, mode, run_id=None) -> Application` (status APPLYING, `attempt_no`
  = previous+1, written BEFORE the browser opens), `finish_application(id, ApplyResult, docs=None) -> Application`
  (sets `submitted_at` only for submitted statuses), `list_applications(status=None, opportunity_id=None, since=None,
  limit=None, offset=0)`, `latest_application(opportunity_id)`, `count_submitted_on(day, tz) -> int` (SUBMITTED +
  SUBMITTED_UNCONFIRMED, non-dry-run, by local calendar day), `has_submitted(opportunity_id, fingerprint=None) -> bool`,
  `recover_stale_applications(older_than: timedelta) -> int` (APPLYING -> FAILED/INTERRUPTED), `mark_manually_applied(
  opportunity_id)` (SKIPPED/ALREADY_APPLIED).
- screening answers: `upsert_answer`, `find_answer(intent=None, question_norm=None)`, `list_answers`, `delete_answer`,
  `touch_answer` (use_count). Pending questions: `add_pending_question` (dedup by normalised question+opportunity),
  `list_pending_questions(unresolved_only=True)`, `resolve_pending_question(id, answer_text)` (also upserts the answer).
- ats accounts (no passwords): `get_ats_account(host, email)`, `upsert_ats_account(host, email, verified=...)`.
- runs: `start_run(mode, trigger) -> run_id`, `finish_run(run_id, RunReport)`, `list_runs(limit)`;
  `acquire_run_lock(owner, ttl_s) -> bool`, `heartbeat_run_lock`, `release_run_lock` (cross-process, TTL-expiring).
- kv: `get_kv/set_kv`. All timestamps stored UTC ISO-8601.

### 5.2 `secrets.py`, `llm.py`, `readiness.py`
- `secrets.py`: `KeyringCredentialStore` (service names `autoapply:<kind>`), `MemoryCredentialStore`,
  `resolve_openai_key(env, store) -> str | None` (env wins; the key is never persisted by this function),
  `set_stored_openai_key(store, key)` (used only by an explicit "save key" action), `mask(key)`.
- `llm.py`: `OpenAIClient(LLMClient)` via the `openai` SDK (structured outputs -> pydantic schema; retries with
  backoff; per-application call budget; omit `temperature` for models that reject it and retry; every failure ->
  `LLMError`; never logs prompts containing profile data at INFO), `FakeLLM(LLMClient)` (scripted handlers by `purpose`,
  records calls, unregistered purpose -> `LLMError`), `build_llm(config, key) -> LLMClient`.
- `readiness.py`: `check_readiness(config, paths, env=os.environ, store=None) -> ReadinessReport(ok, issues[code, field,
  message])`. Required: every `REQUIRED_PROFILE_FIELDS` entry, a resume PDF (`resolve_resume_path`), an OpenAI key,
  >=1 enabled source with usable config (workbook path exists, or board tokens, or a browser platform), and for
  `full_auto` `apply.attestations_authorized`. `ensure_ready_or_raise(...)` raises `ReadinessError` (lists everything).

### 5.3 `sources/`
Providers are auto-discovered: each module exposes `PROVIDER` or `PROVIDERS` (`OpportunityProvider`).
`sources/__init__.py` (owned by the workbook worker) provides `discover_providers()` and `ingest_all(ctx) ->
IngestResult(opportunities, per_provider_counts, errors)` which isolates provider failures and dedupes.
- **workbook.py**: `.xlsx` via openpyxl (`read_only=True, data_only=True`). Sheet = `workbook.sheet` else best fuzzy
  match for "verified opportunities" (case/space/punctuation-insensitive; tolerate `Verified-Opportunities`). Header row
  auto-detected (first row with >=3 recognised aliases within the first 15 rows). Alias tables for company (Company,
  Employer, Organization), title (Role, Position, Job Title, Title), url (URL, Link, Apply Link, Application URL, Job
  Link, Posting), location (Location, City), term (Term, Season, Internship Term, Cohort), status (Status, Open?, State,
  Open/Closed), posted (Posted, Date Posted, Date Added), verified (Last Verified, Verified On, Date Verified), deadline,
  ats (ATS, Platform), notes/description; `workbook.column_map` overrides win. Cells with hyperlinks: prefer the
  hyperlink target over the display text. Filters: open (status not closed/filled/expired/inactive/"no"; deadline not
  past), term == `search.target_term` (if a term column exists it must match; else an explicit different term in title/
  notes excludes; else assume the workbook's term), recent (`last_verified` or `posted` within `recent_days`; no dates ->
  keep, mark `extra["date_unknown"]`), internship-ish. Unknown columns -> `extra`. Never crash on ragged rows,
  merged cells, formulas (cached values), blank rows, duplicate headers, dates as text or serials.
  `inspect_workbook(path) -> WorkbookReport` (sheets, detected header, mapping, sample rows) backs `autoapply inspect-workbook`.
- **dedupe.py**: `dedupe(opps) -> list[Opportunity]` merging by `id`, then by `fingerprint`; prefer a direct ATS/employer
  URL over aggregator URLs (linkedin/indeed), keep the freshest `last_verified`, union `extra`.
- **boards.py**: public JSON APIs (`boards-api.greenhouse.io/v1/boards/{t}/jobs?content=true`,
  `api.lever.co/v0/postings/{t}?mode=json`, Ashby `api.ashbyhq.com/posting-api/job-board/{t}`) for tokens in
  `config.boards.*` when the platform toggle is on. Keep internships only (title/department/commitment says intern /
  internship / co-op) whose text mentions the target term or no other term. Sets `ats`, `apply_url`, `posted_date`,
  description text (HTML stripped). Polite: shared `httpx.Client`, timeout, per-token failure isolation.
- **linkedin.py / indeed.py**: section 7.

### 5.4 `scoring.py`
`score_opportunity(op, search, profile=None) -> ScoreResult` (0..100, deterministic, explainable, no I/O) and
`score_all`. Title match against `role_families` (best family; multiplied by family weight), bonus for
`include_keywords` in title/description, location fit (preferred list / remote_ok / `us_only` penalty for clearly non-US),
allowlist bonus, hard fails (`passed=False`, score 0): `company_denylist`, `exclude_title_keywords`, closed, wrong term,
non-intern seniority; MBA/PhD-only roles excluded unless `profile.degree` says so. `reasons` are human sentences for the
dashboard. `passed` = score >= `search.min_score` and no hard fail. Golden tests for every README role family.

### 5.5 `tailor/`
- `knowledge.py`: `load_kb(paths) -> KnowledgeBase` from `profile/experiences/*.json|*.md` (Markdown: YAML-ish front
  matter `id,kind,title,organization,location,start,end,skills` + bullet list) else `knowledge_base.json` else empty;
  `build_kb_from_resume(pdf_path, llm) -> KnowledgeBase` (pypdf text -> LLM structuring with `LLMError` fallback to a
  heuristic section parser; every value must appear in the resume text or be dropped) + `save_kb`.
- `grounding.py`: `validate_bullet(rephrased, sources, kb)`, `validate_cover_letter(text, kb, opportunity, profile)`:
  numbers/percentages/currency in output must appear in the source; capitalised entities and tech terms must appear in the
  KB corpus, opportunity text or profile; token overlap with the source above a threshold; returns `GroundingReport`.
- `generate.py`: `generate_documents(op, kb, profile, paths, llm, resume_fallback) -> TailoredDocs`. LLM (`tailor_resume`,
  `cover_letter`) returns a *plan*: ordered experience ids, chosen bullet indexes, optional rephrasings with
  `source_bullets`, emphasised skills (subset of KB), cover-letter paragraphs citing experience ids. Renderer fills
  employer/title/dates from the KB. Violations -> reverted to source bullet / sentence dropped; cover letter still
  failing -> deterministic template letter or omitted. No LLM -> deterministic keyword-overlap bullet selection.
  No KB -> `mode="fallback_uploaded_resume"` (copy of the user's PDF). Output under `documents/<opportunity_id>/`.
- `render.py`: ReportLab, single column ATS-friendly (selectable text; header, education from profile, experience,
  projects, skills; fits one page by dropping lowest-priority bullets, then shrinking font to a floor), cover letter PDF.
  Text must round-trip through `pypdf`.

### 5.6 `apply/engine.py`, `browser.py`, `blockers.py`, `registry.py`
- `browser.py`: `BrowserManager(config, paths, restrict_to_loopback=False)` -> persistent Chromium context under
  `browser_profile/` (headless per config; `PLAYWRIGHT_BROWSERS_PATH` respected; nav/default timeouts; downloads
  disabled; `restrict_to_loopback` adds the host-resolver rule from section 1.8). Implements `BrowserProvider`.
- `blockers.py`: `detect_blocker(page) -> Reason | None`: visible CAPTCHA/challenge (recaptcha/hcaptcha/turnstile/arkose
  frames or "verify you are human"; an invisible reCAPTCHA badge alone is NOT a blocker), access denied/bot walls,
  SSO-only login, closed posting text ("no longer accepting", "position has been filled", 404), already applied.
- `registry.py`: discovers `ADAPTER`s in `apply.adapters`; `select(url, page=None)` by `matches_url` then
  `matches_page`, uses `normalize.host_of` (so `*.localhost` mock hosts match); `None` -> generic filler or
  `UNSUPPORTED_PORTAL`.
- `engine.py`: `apply_to(op, docs, deps, dry_run) -> ApplyResult`. Navigate with retries, follow employer "Apply"
  links/redirect chains (Wells Fargo -> Workday), run pre-flight `detect_blocker`, choose adapter, enforce the attempt
  timeout, convert any exception into a `FAILED` result with a screenshot artifact, always close pages, never leave
  the process with a hanging browser. Stop at once if the STOP file appears.

### 5.7 Adapters (`apply/adapters/*.py`, one `ADAPTER` each)
Each implements `ApplyAdapter`: fill standard fields from `Profile`, upload `docs.resume_pdf` (+ cover letter when a slot
exists), ask `ctx.answers` for every custom question (extract `FormQuestion`s), verify fields by reading them back, handle
inline validation errors (fix once, else `VALIDATION_ERROR`), honour `dry_run` (stop before the irreversible click ->
`DRY_RUN_OK`), detect the confirmation (text/URL) -> `SUBMITTED`, else `SUBMITTED_UNCONFIRMED`. Use resilient locators:
stable ids/`data-automation-id`/`name` first, then label text, never positional CSS. Cope with delayed rendering,
re-rendered/stale elements, custom dropdowns/comboboxes, typeahead selects, split date inputs, hidden file inputs,
repeatable sections. Call `detect_blocker` at each step.
- **workday**: hosts `*.myworkdayjobs.com`, `*.myworkdaysite.com`; DOM probe `[data-automation-id]`. Flow: Apply ->
  choose *Apply Manually* (avoid autofill-with-resume so we control the data) -> Sign in / Create account (per-tenant via
  `AccountManager`; consent checkbox; email verification through `EmailVerifier`, else `EMAIL_VERIFICATION`) -> My
  Information (legal name, address, phone type/country code, "how did you hear", previously worked here) -> My Experience
  (work experience + education entries from the KB/profile, resume upload, websites, skills) -> Application Questions ->
  Voluntary Disclosures (EEO: default decline; terms/consent checkbox) -> Self Identify (disability form) -> Review ->
  Submit. Known ids to expect: `adventureButton`, `applyManually`, `email`, `password`, `verifyPassword`,
  `createAccountCheckbox`, `createAccountSubmitButton`, `signInSubmitButton`, `legalNameSection_firstName`,
  `legalNameSection_lastName`, `addressSection_addressLine1|city|countryRegion|postalCode`, `phone-number`,
  `source--source`, `file-upload-input-ref`, `bottom-navigation-next-button`, `formField-*`, `menuItem`, `promptOption`.
- **greenhouse**: `boards.greenhouse.io`, `job-boards.greenhouse.io`, embedded `gh_jid` pages (iframe `#grnhse_iframe`).
  Standard fields, resume/cover-letter upload (attach or paste), custom questions (`question_*`), EEO selects, submit.
- **lever**: `jobs.lever.co/{co}/{id}/apply`; fields `name,email,phone,org,urls[*]`, resume upload with parsing wait,
  custom `cards[...]`, EEO survey, optional hCaptcha -> `BOT_CHECK`.
- **ashby**: `jobs.ashbyhq.com/{co}/{id}/application`; `_systemfield_*` inputs, custom fields, resume upload, submit.
- **generic (`apply/generic.py`)**: for unknown employer portals when `apply.generic_portal`. Extract a form model from
  the DOM, map fields to a FIXED slot vocabulary (heuristics first; LLM `map_form_fields` may only choose slots, never
  emit values), fill from Profile/AnswerEngine, read back, click only allow-listed next/submit labels, stay on the portal's
  domain, cap steps, require confirmation detection else `SUBMITTED_UNCONFIRMED`; SSO-only -> `LOGIN_REQUIRED`;
  `generic_portal=false` -> `UNSUPPORTED_PORTAL` (README behaviour: recorded for manual follow-up).

### 5.8 `apply/answers.py` — see section 6.
### 5.9 `apply/accounts.py`, `apply/emailverify.py`
`AccountManagerImpl(repo, store)`: `credentials_for(host, email)` returns the stored password or generates a strong
random one (>=20 chars, mixed classes, ATS-safe symbols) and stores it ONLY in the credential store under
`autoapply:ats:<host>` / `<email>`; DB records metadata only. `ImapEmailVerifier` (imaplib SSL; app password from the
credential store `autoapply:imap`; polls INBOX for mails to/from hints newer than the attempt start; extracts links/codes;
never deletes mail; every failure -> None). Both fully unit tested with fakes.

### 5.10 `pipeline.py`
`Deps` dataclass (config, paths, repo, llm, store, clock, email, browser, sleep function for pacing) and
`run_once(deps, *, trigger, mode=None, limit=None) -> RunReport`: readiness gate (discover_only exempt) -> acquire run lock ->
recover stale APPLYING rows -> ingest (`ingest_all`) -> upsert -> score -> select candidates (passed, open, not submitted,
not manually applied, fingerprint not submitted, attempts < `max_attempts_per_job`, retry policy) sorted by score desc ->
loop while cap not reached and attempt budget left and no STOP file: pace, tailor, `create_application`, `apply_to`,
`finish_application`, persist pending questions -> release lock -> `RunReport`. Dry-run results do NOT count toward the
cap. Never raises; failures are recorded. Retry policy (`NEEDS_MANUAL`/`FAILED` re-eligibility):

| reason | auto-retry |
|---|---|
| MISSING_ANSWER | once the pending question is resolved |
| ATTESTATION_NOT_AUTHORIZED | once `attestations_authorized` is true |
| EMAIL_VERIFICATION | after >=1h, if an email verifier is now configured |
| BOT_CHECK | after >=24h, within `max_attempts_per_job` |
| FAILED (technical: timeout, network, internal, unexpected flow) | after >=30min backoff, within `max_attempts_per_job` |
| everything else (unsupported portal, login required, closed, ineligible, ...) | never |

### 5.11 `scheduler.py`
`Scheduler(deps_factory, clock)` + `RunManager`: at most one run at a time (in-process lock + DB run lock), run in a
worker thread, `next_run_at(config, now)` from `schedule.run_times/days_of_week/jitter_minutes` in `config.timezone`
(DST-safe), catches up at most one missed run after downtime, re-reads config each tick (dashboard toggles apply live),
`run_now(mode)`, `stop()`, status snapshot (idle/running/next run/last report). Fully testable with `FakeClock`.

### 5.12 `cli.py`, `__main__.py`, PowerShell
argparse commands: `init`, `check-ready [--json]` (exit 0 only if ready), `serve [--host 127.0.0.1 --port 8765]`,
`run [--once] [--mode ...] [--limit N]`, `ingest`, `score`, `tailor <id>`, `apply <id> [--dry-run]`, `inspect-workbook <path>`,
`doctor [--live-dry-run URL]`, `status`, `stop` / `resume` (STOP file), `set-key` (getpass -> credential store, optional).
No command ever prompts during `run`/`serve`. PowerShell (repo root): `set_openai_key.ps1` (Read-Host -AsSecureString ->
`[Environment]::SetEnvironmentVariable('OPENAI_API_KEY', ..., 'User')`, never echoes/logs the key), `check_ready.ps1`,
`launch.ps1` (bootstraps `.venv`, `pip install -e .`, `playwright install chromium`, imports the User-scope
`OPENAI_API_KEY` into the process, runs check-ready, starts `serve`, opens the browser). Thin wrappers, ASCII only, `$ErrorActionPreference='Stop'`.

### 5.13 `dashboard/`
FastAPI + Jinja2 + vanilla JS (no CDN, works offline), bound to 127.0.0.1. Pages/APIs: Overview (status, today's cap
usage, readiness issues, mode + schedule toggles, Run now / Dry run / Stop), Profile (all fields + attestation
authorisation), Screening answers (list/edit/delete + **pending questions** queue), Resume & experiences (upload PDF, build KB
from resume, edit experiences), Search profile (families, keywords, locations, thresholds, platform toggles, boards,
workbook path), Opportunities (scores + reasons), Applications (status, reason, artifacts, "applied manually" button),
Runs. JSON API mirrors every page for tests. Security: Host-header allow-list (localhost/127.0.0.1), Origin/CSRF token check on
every state-changing request, never returns secrets (only `present: true`), PDF-only uploads with size limit and
content sniffing, path-traversal-safe artifact serving, output escaping.

### 5.14 `testing/`
- `mock_ats/`: faithful mock sites (base in `base.py`): `greenhouse`, `lever`, `ashby`, `workday` (multi-step wizard with
  the real `data-automation-id` vocabulary, sign-in/create-account, email-verification variant via the hub mailbox, custom
  dropdown widgets, typeahead school field, split dates, repeater sections), `employer_portal` (unknown DOM, multi-page),
  `blockers` (visible CAPTCHA, invisible-badge-only page, SSO-only login, closed posting, redirect-to-Workday employer page),
  each recording FINAL submissions with parsed fields + uploaded files. Mimic real-world quirks: delayed XHR rendering,
  re-render on input, validation errors, required-field gating, session expiry, fault injection.
- `fake_llm.py` (scripted + heuristic "fake brain" for the e2e), `fixtures.py` (sample workbook builder, sample profile,
  sample experiences, sample resume PDF).

## 6. Screening answers (`apply/answers.py`)

`AnswerEngineImpl(profile, config, repo, kb, llm)` implements `AnswerEngine`. Flow: normalise question -> classify **intent**
(regex/keyword rules first; LLM `classify_question` may ONLY return an intent id from the fixed list or `free_text`/`unknown`
— never an answer) -> resolve:

| intent group | source | notes |
|---|---|---|
| profile facts: `work_authorization_us`, `sponsorship_required`, `willing_to_relocate`, `age_18_plus`, `enrolled_student`, `graduation_date`, `school`, `degree`, `major`, `gpa`, `available_start/end`, `referral_source`, `linkedin/github/portfolio/website`, `phone`, `email`, `address*` | Profile | `None` value -> unanswerable |
| EEO: `gender`, `race_ethnicity`, `hispanic_latino`, `veteran_status`, `disability_status` | Profile.eeo | `decline` -> pick the site's "prefer not / decline / do not wish" option (fuzzy) |
| standing answers: `previously_employed_here`, `known_employee`, `felony_conviction`, `security_clearance`, `non_compete`, `export_control_us_person`, `accommodation_needed`, `salary_expectation`, `drug_test_consent`, `background_check_consent` ... | saved `ScreeningAnswer` (dashboard) | no saved answer -> unanswerable |
| attestations: `certify_truthful`, `agree_terms`, `privacy_consent`, `e_signature` | `config.apply.attestations_authorized` | e-signature = `profile.full_name` |
| free text: `why_company`, `why_role`, `tell_us_about_yourself`, `additional_info`, `strengths`, `cover_letter_text` | LLM grounded in KB + job description (max_length aware, grounding validated) | fail -> deterministic template from KB, else unanswerable if required |

Choice questions resolve to the EXACT option label (exact, then normalised, then fuzzy `difflib` >= 0.8, then semantic yes/no
mapping "Yes, I am"/"No, I am not"). Every unanswerable+required question is queued via `add_pending_question`. Never answer
from the LLM when the intent is a profile/standing/attestation intent.

## 7. LinkedIn / Indeed (discovery only, opt-in)

Default off. Uses the user's own logged-in persistent browser profile (they sign in once by hand via `serve` -> "Open
browser to sign in"); never stores their password. Search queries derive from the role families. Throttled (>=3-8s jitter
between page loads, low page caps), stops immediately on any challenge/CAPTCHA/login wall/rate limit and reports it. No Easy
Apply, no messaging, no scraping of people. Extract title/company/location/posted/description + the employer's external apply
URL; set `source`, `ats` from the external host. Parsers are pure functions tested against saved HTML fixtures. The dashboard
warns that these sites restrict automation and that the user accepts that risk.

## 8. Testing strategy

Unit tests per module; browser tests against mock sites; property/fuzz tests for parsers; the acceptance suite in `tests/acceptance`.
Fixtures come from `autoapply.testing.fixtures` so every suite shares one sample world. Coverage is judged by criteria, not by a number.

## 9. Acceptance criteria (each maps to a test named `test_A<n>_...` in `tests/acceptance/`)

| id | criterion |
|---|---|
| A1 | `check-ready` lists every missing profile field / resume / key / attestation flag and exits non-zero; the run path refuses when not ready (discover_only allowed); defaults are `full_auto`, cap 5, schedule off; env key never persisted anywhere; the 3 PowerShell scripts exist and pass static lint |
| A2 | workbook ingest (aliases, hyperlinks, ragged rows, term/open/recent filters, dedupe, `inspect-workbook`) |
| A3 | Greenhouse/Lever/Ashby board parsing; LinkedIn/Indeed parsers + guards (off by default, stop on challenge); cross-source dedupe |
| A4 | scoring golden tests for every role family + hard fails + explanations |
| A5 | tailoring: grounded PDFs; invented facts rejected/reverted; fallback to uploaded resume; no-LLM deterministic path |
| A6 | adapters submit to mock Workday/Greenhouse/Lever/Ashby/generic portal with correct fields + attached PDFs; dry-run stops before submit; captcha/login/closed/unsupported/missing-answer/attestation/email-verification each route to the specific `Reason`; invisible-captcha page is NOT blocked |
| A7 | answer engine per section 6 incl. decline mapping, never-guess, pending questions, saved answers reused |
| A8 | accounts: per-tenant create/sign-in, passwords only in the credential store, verification via mailbox; no secrets in DB/logs/traces |
| A9 | pipeline: cap by local calendar day, restart-safe, idempotent, bounded retries per policy, STOP file, stale-APPLYING recovery, dry-run not counted |
| A10 | scheduler: next-run computation (DST, days, jitter), single-flight, live config toggles, catch-up rule |
| A11 | dashboard: every page/API, profile/answers/search/schedule editing, uploads, pending-question resolution, security checks |
| A12 | **autonomy e2e**: zero-input `run --once` (stdin closed) against mock sites + fake LLM + fake mailbox submits exactly `min(cap, eligible)` applications with correct data/docs, routes blockers to manual, second run submits nothing new, next simulated day continues |
| A13 | ruff + mypy + full pytest green; no network in unit tests; no POSIX-only APIs; no secrets/PII committed |
| A14 | README documents setup, modes, coverage table, safety model, troubleshooting, adding an adapter, limitations; `docs/ADAPTERS.md` exists |
| A15 | `autoapply doctor --live-dry-run` works against a mock URL (fills, never submits) and reports adapter + fields |

## 10. Orchestration loop

`docs/PROGRESS.md` is the orchestrator's persistent state. Loop: gates -> gap report -> dispatch parallel Sonnet workers (one
worktree each, disjoint files) -> merge -> gates -> commit/push -> repeat. Exit only when section 0 holds.
