"""SmartApplier — autonomous, verified applications on no-account ATS forms.

Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Jobvite, BambooHR,
Rippling, Recruitee, Breezy HR, JazzHR, Teamtailor, Pinpoint, Dover, Gem,
Personio and Comeet — directly, or embedded on a company's career page.

For one posting:
  1. open the posting, keep its text as the job description, open the form
     (the ATS's direct apply URL, an Apply button, or the ATS iframe)
  2. stop when the posting says the role is unpaid, or when a CAPTCHA / bot
     check is showing — it is never solved or bypassed
  3. scan every question (questions.scan_form)
  4. answer from stored facts (resolve), then verified LLM drafts (drafting),
     generating a cover letter only if the form asks for one
  5. HOLD if any required question lacks a verified answer — nothing is
     submitted; the questions go to pending_questions.json
  6. fill (multi-page forms: Next, then scan/answer/fill the next page)
  7. submit, and require NEW confirmation text or a confirmation URL before
     reporting success. No confirmation -> submitted_unconfirmed (never retried).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from bot.apply.base import ApplyResult, BaseApplier
from bot.apply.smart import pending
from bot.apply.smart.candidate import Candidate, normalize
from bot.apply.smart.cover_letter import full_letter, render_pdf, write_cover_letter
from bot.apply.smart.drafting import Generate, draft_answers
from bot.apply.smart.questions import Question, scan_form
from bot.apply.smart.resolve import NEVER_DRAFT, is_follow_up, pick_option, resolve

logger = logging.getLogger(__name__)

_DRAFTABLE = {"text", "textarea", "select", "radio", "combobox", "yesno", "checkboxes"}

# ---------------------------------------------------------------------------
# confirmation evidence
# ---------------------------------------------------------------------------

_CONFIRMATION = re.compile(
    r"thank(s| you)[^.\n]{0,60}(appl|submi|interest)|application (has been |was )?"
    r"(successfully )?(submitted|received|sent|completed)|we('ve| have) (successfully )?received your "
    r"application|successfully (submitted|applied|sent)|your application (is|has been) "
    r"(complete|in|sent|submitted|received)|application submitted|you('ve| have) "
    r"(successfully )?applied|we (got|received) your application|application (is )?complete"
    r"|vielen dank f(ü|ue)r ihre bewerbung|ihre bewerbung (wurde|ist) (erfolgreich )?"
    r"(versendet|übermittelt|eingegangen|gesendet)", re.IGNORECASE)
#: URL words that prove a submit only when the NEW url has them and the old didn't.
_CONFIRMATION_URL_WORDS = ("confirmation", "confirm", "thank", "submitted", "success", "applied",
                           "complete")

# ---------------------------------------------------------------------------
# unpaid postings — the user never applies to unpaid roles
# ---------------------------------------------------------------------------

_UNPAID = re.compile(
    r"\bunpaid,?\s+(?:summer\s+|part[- ]time\s+|full[- ]time\s+|remote\s+)?"
    r"(?:internships?|interns?|positions?|roles?|opportunit(?:y|ies)|placements?|volunteers?|fellowships?)\b"
    r"|\b(?:internship|position|role|opportunity|program(?:me)?|placement)\s+is\s+(?:an?\s+)?unpaid\b"
    r"|\bis\s+an?\s+unpaid\b"
    r"|\bthis\s+is\s+(?:an?\s+)?(?:unpaid|volunteer)\b"
    r"|\b(?:no|without)\s+(?:monetary\s+|financial\s+)?(?:compensation|remuneration)\b"
    r"(?!\s+(?:for|of)\s+(?:travel|relocation|expenses|housing))"
    r"|\b(?:is|are|will)\s+not\s+(?:be\s+)?(?:paid|compensated)\b"
    r"|\bvolunteer\s+(?:position|role|internship|basis)\b"
    r"|\b(?:academic|course|college|school)\s+credit\s+only\b",
    re.IGNORECASE)
_NEGATION_BEFORE = re.compile(r"(\bnot|\bnever|n't|\bno longer|\bdon't offer|\bdo not offer)\s+(?:an?\s+|any\s+)?$",
                              re.IGNORECASE)
_NEGATION_AFTER = re.compile(r"^\W{0,3}(?:are|is)\s+not\b", re.IGNORECASE)


def unpaid_phrase(text: str) -> str | None:
    """The phrase saying the role is unpaid, or None.

    'unpaid leave' / 'paid time off' never match; negated statements ('this
    is not an unpaid internship', 'we do not offer unpaid internships') don't
    either. A form question like 'Are you open to an unpaid internship?' does:
    it is a strong sign the role pays nothing, and the user reviews it.
    """
    for m in _UNPAID.finditer(text or ""):
        before = text[max(0, m.start() - 30):m.start()]
        after = text[m.end():m.end() + 20]
        if _NEGATION_BEFORE.search(before) or _NEGATION_AFTER.search(after):
            continue
        return " ".join(m.group(0).split())
    return None


# ---------------------------------------------------------------------------
# dates for date inputs
# ---------------------------------------------------------------------------

_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


def _month(word: str) -> int | None:
    w = word.lower()[:3]
    return _MONTHS.index(w) + 1 if w in _MONTHS else None


def exact_date(text: str) -> date | None:
    """A full calendar date in ``text`` ('2027-05-24', '5/24/2027',
    'May 24, 2027', '24 May 2027'); None for 'May 2027' — never invent a day."""
    t = (text or "").strip()
    try:
        if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t):
            return date(int(m[1]), int(m[2]), int(m[3]))
        if m := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", t):
            return date(int(m[3]), int(m[1]), int(m[2]))
        if (m := re.search(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", t)) and _month(m[1]):
            return date(int(m[3]), _month(m[1]), int(m[2]))
        if (m := re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b", t)) and _month(m[2]):
            return date(int(m[3]), _month(m[2]), int(m[1]))
    except ValueError:
        return None
    return None


def _month_year(text: str) -> tuple[int, int] | None:
    d = exact_date(text)
    if d:
        return d.year, d.month
    m = re.search(r"\b([A-Za-z]{3,9})\.?,?\s+(\d{4})\b", text or "")
    if m and _month(m[1]):
        return int(m[2]), _month(m[1])
    m = re.fullmatch(r"\s*(\d{1,2})/(\d{4})\s*", text or "")
    if m and 1 <= int(m[1]) <= 12:
        return int(m[2]), int(m[1])
    return None


def fit_value(q: Question, value: str) -> tuple[str | None, str]:
    """Shape ``value`` for the control (date/number inputs, date placeholders).

    Returns (value, "") or (None, reason) when the stored answer cannot be
    expressed in the control's format without inventing detail.
    """
    if q.kind not in ("text", "textarea") or value is None:
        return value, ""
    itype = (q.input_type or "").lower()
    if itype == "date":
        d = exact_date(value)
        return (d.isoformat(), "") if d else (None, "the form needs an exact date")
    if itype == "month":
        my = _month_year(value)
        return (f"{my[0]:04d}-{my[1]:02d}", "") if my else (None, "the form needs a month")
    if itype == "number":
        m = re.fullmatch(r"\s*\$?\s*(\d[\d,]*(?:\.\d+)?)\s*", value)
        return (m[1].replace(",", ""), "") if m else (None, "the form needs a plain number")
    ph = (q.placeholder or "").lower().replace(" ", "")
    fmt = {"mm/dd/yyyy": "%m/%d/%Y", "dd/mm/yyyy": "%d/%m/%Y", "dd.mm.yyyy": "%d.%m.%Y",
           "yyyy-mm-dd": "%Y-%m-%d", "mm-dd-yyyy": "%m-%d-%Y"}.get(ph)
    if fmt:
        d = exact_date(value)
        return (d.strftime(fmt), "") if d else (None, f"the form needs an exact date ({q.placeholder})")
    if ph in ("mm/yyyy", "mm.yyyy", "mm-yyyy"):
        my = _month_year(value)
        sep = ph[2]
        return (f"{my[1]:02d}{sep}{my[0]}", "") if my else (None, f"the form needs {q.placeholder}")
    return value, ""


def best_match(options: tuple[str, ...], value: str) -> str | None:
    """The option containing every word of ``value`` (ties -> shortest), else
    the single option containing >= 80% of them; None when unclear.

    'Austin, Texas' picks 'Austin, Texas, United States' over 'Austin, MN';
    'The University of Texas at Austin' picks 'University of Texas at Austin'
    over '... at Arlington'.
    """
    want = set(normalize(value).split())
    if not want:
        return None
    scored = []
    for opt in options:
        have = set(normalize(opt).split())
        scored.append((len(want & have) / len(want), -len(opt), opt))
    full = [s for s in scored if s[0] == 1.0]
    if full:
        return max(full)[2]
    strong = [s for s in scored if s[0] >= 0.8]
    return strong[0][2] if len(strong) == 1 else None


# ---------------------------------------------------------------------------
# page scripts
# ---------------------------------------------------------------------------

#: Deep (shadow-DOM aware) element listing shared by the page scripts.
_DEEP = r"""
const __deep = (sel, root = document) => {
  const out = [];
  const rec = (node) => {
    for (let c = node.firstElementChild; c; c = c.nextElementSibling) {
      if (c.matches(sel)) out.push(c);
      if (c.shadowRoot) rec(c.shadowRoot);
      rec(c);
    }
  };
  rec(root);
  return out;
};
const __vis = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
  && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
const __clean = (t) => (t || '').replace(/[​-‍⁠﻿]/g, '').replace(/\s+/g, ' ').trim();
"""

#: Is an application form on the page? (a file input, or a visible email box)
_FORM_PRESENT_JS = "() => {" + _DEEP + r"""
  if (__deep('input[type="file"]').length) return true;
  return __deep('input').some((el) => {
    if (!__vis(el)) return false;
    if ((el.type || '').toLowerCase() === 'email') return true;
    const hint = `${el.name || ''} ${el.id || ''} ${el.getAttribute('autocomplete') || ''} `
      + `${el.getAttribute('aria-label') || ''} ${el.getAttribute('placeholder') || ''}`;
    return /e-?mail/i.test(hint);
  });
}"""

#: Why the page can't be completed by a bot, or '' — a challenge popup, a
#: DataDome/Cloudflare block page, or a checkbox CAPTCHA that a human must
#: tick (an invisible CAPTCHA or an already-passed Turnstile is not one).
_CHALLENGE_JS = "() => {" + _DEEP + r"""
  const solved = (name) => __deep(`[name="${name}"]`).some((el) => (el.value || '').trim().length > 0);
  for (const f of __deep('iframe')) {
    const src = (f.getAttribute('src') || '').toLowerCase();
    const title = (f.getAttribute('title') || '').toLowerCase();
    const r = f.getBoundingClientRect();
    const st = getComputedStyle(f);
    const shownFrame = r.width > 30 && r.height > 30 && st.visibility !== 'hidden'
      && st.display !== 'none' && st.opacity !== '0';
    const big = shownFrame && r.width > 100 && r.height > 100;
    if (big && (src.includes('recaptcha/api2/bframe') || src.includes('recaptcha/enterprise/bframe')
        || (src.includes('hcaptcha') && !src.includes('frame=checkbox'))
        || src.includes('challenges.cloudflare') || title.includes('challenge'))) return 'challenge';
    if (!shownFrame) continue;
    if (src.includes('captcha-delivery.com')) return 'bot check (DataDome)';
    if (/recaptcha\/(api2|enterprise)\/anchor/.test(src) && !src.includes('size=invisible')
        && !solved('g-recaptcha-response')) return 'reCAPTCHA checkbox';
    if (src.includes('hcaptcha') && src.includes('frame=checkbox') && !src.includes('checkbox-invisible')
        && !solved('h-captcha-response')) return 'hCaptcha checkbox';
    if (src.includes('challenges.cloudflare.com') && src.includes('turnstile')
        && !solved('cf-turnstile-response')) return 'Turnstile';
  }
  const title = (document.title || '').toLowerCase();
  const text = ((document.body && document.body.innerText) || '').slice(0, 3000).toLowerCase();
  if (title.includes('just a moment') || title.includes('attention required')
      || document.querySelector('#challenge-form, #cf-challenge-running, #challenge-stage')) return 'bot check (Cloudflare)';
  if (text.includes('access is temporarily restricted') || text.includes('verify you are human')
      || text.includes('are you a robot')) return 'bot check';
  return '';
}"""

_ERRORS_JS = r"""
() => {
  const vis = (el) => !!(el.offsetWidth || el.offsetHeight) && getComputedStyle(el).visibility !== 'hidden';
  const texts = [];
  document.querySelectorAll('[aria-invalid="true"]').forEach((el) => {
    if (vis(el)) texts.push((el.getAttribute('aria-label') || el.name || el.id || 'field') + ' invalid');
  });
  document.querySelectorAll('[role="alert"], .error, .field-error, .error-message, .helper-text--error, '
      + '.invalid-feedback, [class*="errorMessage"], [class*="error-message"]')
    .forEach((el) => { const t = (el.innerText || '').trim(); if (t && vis(el)) texts.push(t); });
  return texts.slice(0, 5);
}
"""

#: Visible "Apply" buttons/links that open the form. Returns [{sel, href, text}].
_APPLY_BUTTONS_JS = "() => {" + _DEEP + r"""
  const GOOD = /^(apply|apply now|apply here|apply online|apply for (this|the) (job|position|role|opportunity)|apply to (this )?(job|position|role)|i'?m interested|start (your |my )?application|begin (your )?application|apply for job)\b/i;
  const BAD = /linkedin|indeed|seek|xing|google|facebook|apple|glassdoor|later|save|share|filter|refer|alert|similar/i;
  window.__aaqCounter = window.__aaqCounter || 0;
  const out = [];
  for (const el of __deep('a, button, [role="button"], input[type="button"], input[type="submit"]')) {
    if (!__vis(el)) continue;
    const text = __clean(el.innerText || el.value || el.getAttribute('aria-label') || '');
    if (!text || text.length > 60 || !GOOD.test(text) || BAD.test(text)) continue;
    if (el.closest('header nav, footer')) continue;
    if (!el.dataset.aaq) el.dataset.aaq = String(++window.__aaqCounter);
    out.push({ sel: `[data-aaq="${el.dataset.aaq}"]`, href: el.tagName === 'A' ? el.href : '', text });
  }
  return out.slice(0, 5);
}"""

#: Decline non-essential cookies when a consent banner is up (never "accept").
_DECLINE_COOKIES_JS = "() => {" + _DEEP + r"""
  const NO = /^(decline|decline all|reject|reject all|reject all cookies|deny|deny all|refuse|refuse all|only necessary|necessary only|only essential|essential only|use necessary cookies only|accept necessary only|accept only necessary( cookies)?|allow necessary only|continue without accepting|disagree)$/i;
  const BANNER = /cookie|consent|gdpr|privacy|onetrust|cmp|usercentrics|didomi|cookiebot|truste|osano/i;
  for (const el of __deep('button, a, [role="button"]')) {
    if (!__vis(el)) continue;
    const text = __clean(el.innerText || el.getAttribute('aria-label') || '');
    if (!NO.test(text)) continue;
    let ctx = '';
    for (let n = el, i = 0; n && i < 8; n = n.parentElement, i++) {
      ctx += ` ${n.id || ''} ${typeof n.className === 'string' ? n.className : ''} ${n.getAttribute && (n.getAttribute('aria-label') || '')} ${n.getAttribute && (n.getAttribute('data-ui') || '')}`;
    }
    if (!BANNER.test(ctx) && !/cookie/i.test((el.closest('[role="dialog"], [role="alertdialog"], [role="region"]') || {}).innerText || '')) continue;
    el.click();
    return text;
  }
  return '';
}"""

#: The form's final submit (kind='submit') or next-page button (kind='next').
_FORM_BUTTON_JS = "(kind) => {" + _DEEP + r"""
  const SUBMIT = /^(submit|submit( my| your)?( job)? application|send|send( my)? application|apply|apply now|finish|complete( my)? application|submit and finish|apply for (this )?(job|position))$/i;
  const NEXT = /^(next|next step|continue|save (and|&) continue|save (and|&) next|proceed|continue to next step|next page)$/i;
  const BAD = /linkedin|indeed|google|apple|facebook|seek|xing|dropbox|drive|cancel|back|previous|upload|attach|add |remove|delete|autofill/i;
  const forms = __deep('form').filter((f) => f.querySelector('input[type="file"], input[type="email"]'));
  forms.sort((a, b) => b.querySelectorAll('input, select, textarea').length - a.querySelectorAll('input, select, textarea').length);
  const scope = forms[0] || document;
  window.__aaqCounter = window.__aaqCounter || 0;
  const cands = [];
  for (const el of __deep('button, input[type="submit"], [role="button"]', scope)) {
    if (!__vis(el) || el.disabled && kind === 'next') continue;
    const text = __clean(el.innerText || el.value || el.getAttribute('aria-label') || '');
    if (!text || text.length > 50 || BAD.test(text)) continue;
    const isSubmit = (el.getAttribute('type') || '').toLowerCase() === 'submit' || el.tagName === 'INPUT';
    let score = 0;
    if (kind === 'submit') {
      if (SUBMIT.test(text)) score = isSubmit ? 3 : 2;
      else if (isSubmit && !NEXT.test(text)) score = 1;
    } else if (NEXT.test(text)) score = 2;
    if (score) cands.push([score, el]);
  }
  if (!cands.length) return '';
  cands.sort((a, b) => b[0] - a[0]);
  const el = cands[0][1];
  if (!el.dataset.aaq) el.dataset.aaq = String(++window.__aaqCounter);
  return `[data-aaq="${el.dataset.aaq}"]`;
}"""

#: Texts of the visible options of an open dropdown (roles, or list items in
#: an open listbox/menu/popover).
_OPTIONS_JS = "() => {" + _DEEP + r"""
  const sel = '[role="option"], [role="menuitem"], [role="menuitemradio"], [role="treeitem"], '
    + '[role="listbox"] li, [role="menu"] li, [cmdk-item]';
  const seen = new Set();
  const out = [];
  for (const el of __deep(sel)) {
    if (!__vis(el) || el.closest('select')) continue;
    if (el.querySelector('[role="option"], [role="menuitem"]')) continue;  // a wrapper, not an option
    const t = __clean(el.innerText || el.textContent || '');
    if (!t || t.length > 200 || seen.has(t)) continue;
    seen.add(t);
    out.push(t);
  }
  return out.slice(0, 400);
}"""

#: Click the visible option whose text is exactly ``text``.
_CLICK_OPTION_JS = "(text) => {" + _DEEP + r"""
  const sel = '[role="option"], [role="menuitem"], [role="menuitemradio"], [role="treeitem"], '
    + '[role="listbox"] li, [role="menu"] li, [cmdk-item]';
  for (const el of __deep(sel)) {
    if (!__vis(el) || el.closest('select')) continue;
    if (__clean(el.innerText || el.textContent || '') === text) { el.scrollIntoView({block: 'center'}); el.click(); return true; }
  }
  return false;
}"""

#: Known ATS hosts, for finding an application iframe on a career page.
_ATS_FRAME_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "smartrecruiters.com", "workable.com",
                    "jobvite.com", "bamboohr.com", "rippling.com", "recruitee.com", "breezy.hr",
                    "applytojob.com", "teamtailor.com", "pinpointhq.com", "dover.com", "gem.com",
                    "personio.de", "personio.com", "comeet.co", "comeet.com")


def form_url(url: str) -> tuple[str, str | None]:
    """(posting page URL, direct application-form URL or None) for known ATSs."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    base = url.split("?")[0].split("#")[0].rstrip("/")
    rules = (
        ("lever.co", r"/apply$", "/apply"),
        ("ashbyhq.com", r"/application$", "/application"),
        ("apply.workable.com", r"/apply$", "/apply/"),
        ("jobs.jobvite.com", r"/apply$", "/apply"),
        ("breezy.hr", r"/apply$", "/apply"),
        ("recruitee.com", r"/c/new$", "/c/new"),
        ("jobs.personio.", r"/apply$", "/apply"),
        ("pinpointhq.com", r"/applications/new$", "/applications/new"),
    )
    for domain, suffix, add in rules:
        if domain not in host:
            continue
        posting = re.sub(suffix, "", base)
        path = urlparse(posting).path
        # only a single posting's page has an application form
        if (domain == "apply.workable.com" and "/j/" not in path) \
                or (domain == "jobs.jobvite.com" and "/job/" not in path) \
                or (domain == "breezy.hr" and "/p/" not in path) \
                or (domain == "recruitee.com" and "/o/" not in path) \
                or (domain == "jobs.personio." and "/job/" not in path) \
                or (domain == "pinpointhq.com" and "/postings/" not in path):
            return url, None
        return posting, posting + add
    return url, None


@dataclass(frozen=True)
class PlanItem:
    question: Question
    value: str
    source: str


@dataclass(frozen=True)
class Hold:
    question: Question
    reason: str


class SmartApplier(BaseApplier):
    NAV_TIMEOUT = 45000
    #: Seconds to wait for a confirmation page after clicking submit.
    CONFIRM_TIMEOUT_S = 20
    #: Milliseconds to wait for a single-page-app form to render.
    FORM_WAIT_MS = 10000
    #: Seconds an auto-passing Turnstile may take before it counts as a CAPTCHA.
    TURNSTILE_GRACE_S = 10
    #: Pages of a multi-step form the applier will walk through.
    MAX_STEPS = 5

    def __init__(self, page, candidate: Candidate, *, generate: Generate | None = None,
                 pending_path: Path | None = None, cover_letter_dir: Path | None = None,
                 dry_run: bool = False) -> None:
        super().__init__(page)
        self.candidate = candidate
        self.generate = generate
        self.pending_path = pending_path
        self.cover_letter_dir = cover_letter_dir
        self.dry_run = dry_run
        self.last_plan: list[PlanItem] = []
        self.last_holds: list[Hold] = []
        self.last_questions: tuple[Question, ...] = ()
        self.last_captcha = ""
        self.last_notes: list[str] = []
        self.cover_letter_text = ""
        self.cover_letter_pdf: Path | None = None

    # ------------------------------------------------------------------ flow

    def _do_apply(self, job, resume_pdf_path, cover_letter_text, profile) -> ApplyResult:
        raw = job.raw
        logger.info("SmartApplier: %s at %s", raw.title, raw.company)
        self.last_plan, self.last_holds, self.last_questions = [], [], ()
        self.last_captcha, self.last_notes = "", []
        posting = self._open_application(raw.apply_url)
        description = getattr(raw, "description", "") or ""
        if len(description) > 200 and description[:200] not in posting:
            # Embedded forms show no job text; use the board's description.
            posting = f"{description}\n\n{posting}"

        unpaid = unpaid_phrase(posting)
        if unpaid:
            logger.info("Skipping unpaid role %s at %s (%r)", raw.title, raw.company, unpaid)
            self.last_notes.append(f"unpaid: {unpaid!r}")
            return ApplyResult(success=False, manual_required=True,
                               error_message="Posting says the role is unpaid")

        captcha = self._captcha_gate()
        if captcha:
            self.last_captcha = captcha
            if not self.dry_run:
                return ApplyResult(success=False, captcha_detected=True,
                                   error_message=f"CAPTCHA/bot check on the application page ({captcha})")

        resume = Path(resume_pdf_path) if resume_pdf_path else None
        filled: list[PlanItem] = []
        for step in range(self.MAX_STEPS):
            questions = scan_form(self.page)
            if step == 0 and not self._looks_like_application(questions):
                return ApplyResult(success=False, manual_required=True,
                                   error_message="Application form not found on the page")
            questions = self._load_combobox_options(questions)
            plan, holds = self.plan(questions, resume, raw.company, raw.title, posting)
            self.last_questions = questions
            self.last_plan, self.last_holds = filled + plan, holds

            if holds:
                return self._hold(raw, holds)
            if self.dry_run:
                missing = self._identity_gap(plan)
                if missing:
                    self.last_notes.append(f"not submittable: {missing}")
                    return ApplyResult(success=False, manual_required=True,
                                       error_message=f"Dry run — {missing}")
                if captcha:
                    return ApplyResult(success=False, captcha_detected=True,
                                       error_message=f"Dry run — answers ready, but a CAPTCHA "
                                                     f"blocks the form ({captcha})")
                return ApplyResult(success=False, manual_required=True,
                                   error_message="Dry run — every required question answered; not submitted")

            failed = self._fill(plan)
            failed_required = [Hold(i.question, "could not be filled automatically")
                               for i in failed if i.question.required]
            if failed_required:
                return self._hold(raw, failed_required)
            filled += [i for i in plan if i not in failed]

            try:
                advanced = self._next_page()
            except RuntimeError as exc:
                # a result, not an exception: the base class would retry
                errors = self._form_errors()
                return ApplyResult(success=False, error_message=f"Form error: {exc}"
                                   + (" — " + " | ".join(errors)[:300] if errors else ""))
            if not advanced:
                break
        else:
            return ApplyResult(success=False, manual_required=True,
                               error_message=f"Form has more than {self.MAX_STEPS} pages")

        missing = self._identity_gap(filled)
        if missing:
            return ApplyResult(success=False, manual_required=True, error_message=missing)
        captcha = self._captcha_gate()
        if captcha:
            return ApplyResult(success=False, captcha_detected=True,
                               error_message=f"CAPTCHA/bot check before submit ({captcha})")

        url_before = self.page.url
        text_before = self._page_text(limit=60000)
        if not self._click_submit():
            return ApplyResult(success=False, manual_required=True,
                               error_message="Submit button not found")
        return self._outcome(self.CONFIRM_TIMEOUT_S, url_before, text_before)

    @staticmethod
    def _looks_like_application(questions) -> bool:
        return any(q.kind == "file" or q.input_type == "email" or "email" in q.label.lower()
                   or "e mail" in normalize(q.label) for q in questions)

    @staticmethod
    def _identity_gap(items) -> str:
        """Why the filled values can't be an application: no email/name found.

        Guards against forms whose fields could not be labeled — every
        genuine application form asks for the applicant's email and name.
        """
        sources = {i.source for i in items}
        if "profile.email" not in sources:
            return "could not identify the email field on the form"
        if not sources & {"profile.first_name", "profile.full_name", "profile.last_name"}:
            return "could not identify the name fields on the form"
        return ""

    # --------------------------------------------------------------- planning

    def plan(self, questions: tuple[Question, ...], resume: Path | None, company: str,
             title: str, posting: str) -> tuple[list[PlanItem], list[Hold]]:
        """Decide a verified value for every question, or a reason to hold."""
        if self._wants_cover_letter(questions):
            self._prepare_cover_letter(company, title, posting)

        plan: list[PlanItem] = []
        holds: list[Hold] = []
        to_draft: list[Question] = []

        def accept(q: Question, value: str, source: str) -> None:
            fitted, why = fit_value(q, value)
            if fitted is not None:
                plan.append(PlanItem(q, fitted, source))
            elif q.required:
                holds.append(Hold(q, why))

        for q in questions:
            if q.is_phone_country and self._already_us(q):
                continue  # the country-code picker already shows +1
            if self._is_cover_letter_text(q):
                if self.cover_letter_text:
                    plan.append(PlanItem(q, full_letter(self.cover_letter_text, self.candidate,
                                                        company), "generated cover letter"))
                elif q.required:
                    holds.append(Hold(q, "cover letter could not be written from your record"))
                continue
            res = resolve(q, self.candidate, resume=resume, cover_letter=self.cover_letter_pdf,
                          company=company)
            if res.answered:
                accept(q, res.value, res.source)
            elif res.category in NEVER_DRAFT or q.kind not in _DRAFTABLE:
                if q.required:
                    holds.append(Hold(q, res.note or f"no stored answer ({res.category})"))
            else:
                to_draft.append(q)

        if to_draft:
            if self.generate is None:
                holds.extend(Hold(q, "no stored answer and no AI key to draft one")
                             for q in to_draft if q.required)
            else:
                drafts = draft_answers(to_draft, self.candidate, self.generate,
                                       company, title, posting)
                for q in to_draft:
                    d = drafts.get(q.qid)
                    if d is not None and d.ok:
                        accept(q, d.value, f"ai-draft (evidence: {d.evidence[:80]})")
                    elif q.required:
                        reason = "; ".join(d.problems) if d else "not drafted"
                        holds.append(Hold(q, reason))
        return self._settle_follow_ups(questions, plan, holds)

    @staticmethod
    def _already_us(q: Question) -> bool:
        cur = normalize(q.current)
        return bool(re.search(r"(^|\s)(\+?1|us|usa|united states)(\s|$)", cur)) or "+1" in q.current

    @staticmethod
    def _settle_follow_ups(questions, plan: list[PlanItem], holds: list[Hold]):
        """A required 'If yes, explain…' box after a question answered No gets 'N/A'.

        After any other answer (Yes, held, unknown) it stays held: the
        explanation would have to come from the user.
        """
        answered = {item.question.qid: item.value for item in plan}
        still_held = []
        for hold in holds:
            q = hold.question
            if not (q.required and q.kind in ("text", "textarea") and is_follow_up(q)):
                still_held.append(hold)
                continue
            idx = next(i for i, x in enumerate(questions) if x.qid == q.qid)
            parent = next((x for x in reversed(questions[:idx])
                           if x.kind in ("select", "radio", "combobox", "yesno")), None)
            parent_value = answered.get(parent.qid, "") if parent else ""
            if normalize(parent_value).startswith("no"):
                plan.append(PlanItem(q, "N/A", "follow-up: previous answer was No"))
            else:
                still_held.append(Hold(q, "explain the previous answer (it wasn't No)"))
        return plan, still_held

    def _wants_cover_letter(self, questions) -> bool:
        return self.generate is not None and any(
            self._is_cover_letter_text(q) or (q.kind == "file" and "cover" in q.label.lower())
            for q in questions)

    @staticmethod
    def _is_cover_letter_text(q: Question) -> bool:
        return q.kind in ("textarea", "text") and "cover letter" in normalize(q.label)

    def _prepare_cover_letter(self, company: str, title: str, posting: str) -> None:
        if self.cover_letter_text:
            return  # already written for an earlier page of this form
        body = write_cover_letter(self.candidate, self.generate, company, title, posting)
        if not body:
            return
        self.cover_letter_text = body
        if self.cover_letter_dir is not None:
            slug = re.sub(r"[^a-z0-9]+", "-", f"{company}-{title}".lower()).strip("-")[:80]
            try:
                self.cover_letter_pdf = render_pdf(body, self.candidate, company,
                                                   self.cover_letter_dir / f"{slug}.pdf")
            except Exception as exc:
                logger.warning("Cover letter PDF render failed: %s", exc)

    def _hold(self, raw, holds: list[Hold]) -> ApplyResult:
        if self.pending_path is not None:
            pending.record(self.pending_path, raw.company, raw.title, raw.apply_url,
                           [(h.question.label, h.question.kind, h.question.options, h.reason)
                            for h in holds])
        labels = tuple(h.question.label or h.question.kind for h in holds)
        return ApplyResult(
            success=False, held_for_answers=True, held_questions=labels,
            error_message="Held, needs your answer: " + " | ".join(labels)[:400])

    # ------------------------------------------------------------ navigation

    def _open_application(self, url: str) -> str:
        """Open the posting, return its text, and leave the page on the form."""
        posting_url, direct_form = form_url(url)
        self._goto(posting_url)
        self._settle()
        self._decline_cookies()
        posting = self._page_text()
        if direct_form:
            self._goto(direct_form)
            self._settle()
            self._decline_cookies()
        if self._wait_for_form():
            return posting
        for _ in range(2):  # an Apply button, sometimes a second one in a modal
            if not self._click_apply():
                break
            self._decline_cookies()
            if self._wait_for_form():
                return posting
        frame_url = self._form_frame_url()
        if frame_url:
            # Career pages often iframe the ATS form; open it directly.
            self._goto(frame_url)
            self._settle()
            self._decline_cookies()
            self._wait_for_form()
        return posting

    def _goto(self, url: str) -> None:
        try:
            self._safe_goto(url)
        except Exception as exc:  # a slow page still renders; the form check decides
            logger.debug("Navigation to %s: %s", url, exc)

    def _settle(self) -> None:
        self._random_pause(1, 2)
        try:
            self.page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            pass

    def _wait_for_form(self, timeout_ms: int | None = None) -> bool:
        """Poll until a form is on the page (SPAs render late) or a block shows."""
        waited, step = 0, 500
        limit = self.FORM_WAIT_MS if timeout_ms is None else timeout_ms
        while True:
            if self._form_on_page():
                return True
            if waited >= limit or self._challenge_reason().startswith(("bot check", "challenge")):
                return False
            self.page.wait_for_timeout(step)
            waited += step

    def _form_on_page(self) -> bool:
        try:
            return bool(self.page.evaluate(_FORM_PRESENT_JS))
        except Exception:
            return False

    def _decline_cookies(self) -> None:
        try:
            clicked = self.page.evaluate(_DECLINE_COOKIES_JS)
            if clicked:
                logger.debug("Declined cookies via %r", clicked)
                self.page.wait_for_timeout(500)
        except Exception as exc:
            logger.debug("Cookie banner check failed: %s", exc)

    def _click_apply(self) -> bool:
        """Follow the posting's Apply link (same tab) or click its Apply button."""
        try:
            buttons = self.page.evaluate(_APPLY_BUTTONS_JS)
        except Exception:
            return False
        for b in buttons:
            href = b.get("href") or ""
            try:
                same_page = href.split("#")[0] == self.page.url.split("#")[0]
                if href.startswith("http") and not same_page:
                    self._goto(href)
                else:
                    loc = self.page.locator(b["sel"]).first
                    try:
                        loc.click(timeout=4000)
                    except Exception:
                        loc.evaluate("el => el.click()")  # an overlay intercepted the click
                self._settle()
                return True
            except Exception as exc:
                logger.debug("Apply button %r failed: %s", b.get("text"), exc)
        return False

    def _form_frame_url(self) -> str | None:
        """URL of an iframe that holds the application form (ATS host preferred)."""
        frames = [f for f in self.page.frames if f is not self.page.main_frame and f.url.startswith("http")]
        frames.sort(key=lambda f: not any(h in f.url for h in _ATS_FRAME_HOSTS))
        for frame in frames:
            try:
                if frame.evaluate(_FORM_PRESENT_JS):
                    return frame.url
            except Exception:
                continue
        return None

    def _page_text(self, limit: int = 8000) -> str:
        try:
            return self.page.evaluate("() => document.body.innerText || ''")[:limit]
        except Exception:
            return ""

    def _challenge_reason(self) -> str:
        try:
            return self.page.evaluate(_CHALLENGE_JS) or ""
        except Exception:
            return ""

    def _challenge_visible(self) -> bool:
        return bool(self._challenge_reason())

    def _captcha_gate(self) -> str:
        """A CAPTCHA/bot check that blocks the form, or ''.

        An automatic Turnstile gets a grace period to pass on its own; any
        check that needs a human (a checkbox, a picture puzzle, a block page)
        stops the attempt. Nothing is ever clicked or solved.
        """
        reason = self._challenge_reason()
        waited = 0
        while reason == "Turnstile" and waited < self.TURNSTILE_GRACE_S * 1000:
            self.page.wait_for_timeout(1000)
            waited += 1000
            reason = self._challenge_reason()
        return reason

    #: Longer option lists are treated as searchable (schools, cities): the
    #: rendered list is often virtualized/truncated, so values are matched by
    #: typing at fill time instead.
    MAX_LISTED_OPTIONS = 60

    def _load_combobox_options(self, questions: tuple[Question, ...]) -> tuple[Question, ...]:
        """Open each option-less combobox once to read its options, then close it."""
        out = []
        for q in questions:
            if q.kind == "combobox" and not q.options and not (q.is_phone_country and self._already_us(q)):
                try:
                    loc = self.page.locator(q.selector).first
                    loc.click(timeout=3000)
                    self.page.wait_for_timeout(500)
                    texts = [t for t in self._option_texts() if not re.match(r"^no (options|results)", t, re.I)]
                    self._close_popup(loc)
                    if texts and len(texts) <= self.MAX_LISTED_OPTIONS:
                        q = q.with_options(tuple(dict.fromkeys(texts)))
                except Exception as exc:
                    logger.debug("Combobox options unreadable for %r: %s", q.label, exc)
            out.append(q)
        return tuple(out)

    def _option_texts(self) -> list[str]:
        try:
            return [t.strip() for t in self.page.evaluate(_OPTIONS_JS) if t.strip()]
        except Exception:
            return []

    def _close_popup(self, loc) -> None:
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(150)

    # ---------------------------------------------------------------- filling

    def _fill(self, plan: list[PlanItem]) -> list[PlanItem]:
        failed = []
        for item in plan:
            try:
                self._fill_one(item)
            except Exception as exc:
                logger.warning("Could not fill %r: %s", item.question.label, exc)
                failed.append(item)
            self._random_pause(0.2, 0.6)
        return failed

    def _fill_one(self, item: PlanItem) -> None:
        q, value, page = item.question, item.value, self.page
        loc = page.locator(q.selector).first
        if q.kind == "file":
            loc.set_input_files(value)
        elif q.kind in ("text", "textarea"):
            loc.scroll_into_view_if_needed(timeout=5000)
            if q.input_type == "contenteditable":
                loc.click(timeout=3000)
                loc.fill(value)
                if not loc.inner_text().strip():
                    raise RuntimeError("value did not stick")
                return
            loc.fill(value)
            self._pick_suggestion(q, value)
            if not loc.input_value():
                raise RuntimeError("value did not stick")
        elif q.kind == "select":
            try:
                loc.select_option(label=value, timeout=5000)
            except Exception:
                # option text with odd whitespace: select by position instead
                index = loc.evaluate(
                    "(el, v) => Array.from(el.options).findIndex(o => o.text.replace(/\\s+/g,' ').trim() === v)",
                    value)
                if index < 0:
                    raise
                loc.select_option(index=index)
        elif q.kind == "combobox":
            self._choose_combobox(loc, value)
        elif q.kind in ("radio", "checkboxes"):
            index = q.options.index(value)
            self._check(page.locator(q.option_selectors[index]).first)
        elif q.kind == "checkbox":
            self._check(loc)
        elif q.kind == "yesno":
            loc.get_by_role("button", name=value, exact=True).first.click(timeout=3000)
        else:
            raise RuntimeError(f"unsupported field kind {q.kind}")

    _SUGGEST_LABEL = re.compile(r"location|city|address|school|university|college|where", re.I)

    def _pick_suggestion(self, q: Question, value: str) -> None:
        """Location/school autocompletes: choose the suggestion matching ``value``.

        Only when a suggestion list opened for this field; plain text boxes
        are left as typed.
        """
        if not self._SUGGEST_LABEL.search(q.label):
            return
        self.page.wait_for_timeout(900)
        texts = tuple(self._option_texts())
        if not texts:
            return
        choice = pick_option(texts, value) or best_match(texts, value) \
            or best_match(texts, value.split(",")[0])
        if choice and self._click_option(choice):
            return
        self.page.keyboard.press("Escape")

    def _check(self, loc) -> None:
        try:
            loc.check(timeout=3000)
        except Exception:
            loc.evaluate(
                "el => { const r = el.getRootNode();"
                " const l = (el.id && r.querySelector(`label[for=\"${CSS.escape(el.id)}\"]`))"
                " || el.closest('label') || el.closest('[role=\"radio\"], [role=\"checkbox\"]');"
                " (l || el).click(); }")
        if not loc.is_checked():
            raise RuntimeError("box did not check")

    def _click_option(self, choice: str) -> bool:
        try:
            self.page.get_by_role("option", name=choice, exact=True).first.click(timeout=2000)
            return True
        except Exception:
            try:
                return bool(self.page.evaluate(_CLICK_OPTION_JS, choice))
            except Exception:
                return False

    def _choose_combobox(self, loc, value: str) -> None:
        """Type progressively shorter searches until an option clearly matches."""
        typeable = loc.evaluate("el => el.tagName === 'INPUT' || el.tagName === 'TEXTAREA'")
        searches = list(dict.fromkeys(s for s in (
            value, re.sub(r"^the\s+", "", value, flags=re.IGNORECASE),
            value.split(",")[0]) if s.strip())) if typeable else [value]
        for search in searches:
            loc.click(timeout=3000)
            if typeable:
                try:
                    loc.fill(search[:60])
                except Exception:
                    pass  # read-only input: clicking opened the list
            self.page.wait_for_timeout(900)
            texts = tuple(self._option_texts())
            choice = pick_option(texts, value) or best_match(texts, value)
            if choice is not None and self._click_option(choice):
                return
            self.page.keyboard.press("Escape")
        raise RuntimeError(f"no option matches {value!r}")

    # ------------------------------------------------------------- submitting

    def _form_button(self, kind: str) -> str:
        try:
            return self.page.evaluate(_FORM_BUTTON_JS, kind) or ""
        except Exception:
            return ""

    def _next_page(self) -> bool:
        """On a multi-page form, go to the next page; False on the last page."""
        if self._form_button("submit"):
            return False
        selector = self._form_button("next")
        if not selector:
            return False
        before = self.page.url, self._page_text(limit=3000)
        try:
            self.page.locator(selector).first.click(timeout=5000)
        except Exception as exc:
            logger.debug("Next button failed: %s", exc)
            return False
        for _ in range(20):
            self.page.wait_for_timeout(500)
            if (self.page.url, self._page_text(limit=3000)) != before:
                self._random_pause(0.5, 1)
                return True
        raise RuntimeError("the form did not advance past this page (validation error?)")

    def _click_submit(self) -> bool:
        selectors = [s for s in (self._form_button("submit"),) if s] + [
            "form:has(input[type='file']) button[type='submit']",
            "form:has(input[type='email']) button[type='submit']",
            "button:has-text('Submit application')",
            "button:has-text('Submit Application')",
            "button[type='submit']",
            "input[type='submit']",
            "button:has-text('Submit')",
        ]
        for selector in selectors:
            btn = self.page.locator(selector).first
            try:
                if btn.count() and btn.is_visible():
                    btn.scroll_into_view_if_needed(timeout=3000)
                    btn.click(timeout=5000)
                    return True
            except Exception as exc:
                logger.debug("Submit via %s failed: %s", selector, exc)
        return False

    @staticmethod
    def new_confirmation(text: str, before: str) -> str | None:
        """A confirmation phrase in ``text`` that was NOT on the page before
        submit ('Thank you for your interest' in a job description proves
        nothing)."""
        seen = (before or "").lower()
        for m in _CONFIRMATION.finditer(text or ""):
            phrase = m.group(0).lower()
            if phrase not in seen:
                return m.group(0)
        return None

    @staticmethod
    def confirmation_url(url: str, url_before: str) -> bool:
        """The URL changed to one naming a confirmation (and the old one didn't)."""
        if not url or url == url_before:
            return False
        new, old = url.lower(), (url_before or "").lower()
        return any(w in new and w not in old for w in _CONFIRMATION_URL_WORDS)

    def _form_errors(self) -> list[str]:
        try:
            return self.page.evaluate(_ERRORS_JS)
        except Exception:
            return []

    def _outcome(self, timeout_s: int = 20, url_before: str = "", text_before: str = "") -> ApplyResult:
        """Wait for proof of submission: confirmation text that was not on the
        form page, or a URL that newly names a confirmation."""
        errors: list[str] = []
        reason = ""
        for _ in range(timeout_s):
            self.page.wait_for_timeout(1000)
            if self.confirmation_url(self.page.url, url_before) \
                    or self.new_confirmation(self._page_text(limit=20000), text_before):
                return ApplyResult(success=True)
            reason = self._challenge_reason()
            if reason and reason != "Turnstile":  # a Turnstile may still pass by itself
                return ApplyResult(success=False, captcha_detected=True,
                                   error_message=f"CAPTCHA challenge appeared on submit ({reason})")
            errors = self._form_errors()
        if reason == "Turnstile":
            return ApplyResult(success=False, captcha_detected=True,
                               error_message="A Turnstile check appeared on submit and did not pass")
        if errors:
            return ApplyResult(success=False,
                               error_message="Form error after submit: " + " | ".join(errors)[:300])
        return ApplyResult(success=False, submitted_unconfirmed=True,
                           error_message="Submitted, but no confirmation page was detected — check your email")
