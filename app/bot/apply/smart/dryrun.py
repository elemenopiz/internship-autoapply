"""Dry run: show how the SmartApplier would answer real postings. Never submits.

    python -m bot.apply.smart.dryrun <url> [<url> ...] [--all]
    python -m bot.apply.smart.dryrun --workbook [--limit N]

Uses config.json + candidate.yaml from AUTOAPPLY_DATA_DIR. Opens each posting in
a fresh headless browser, reads the form, and prints every planned value with
its source, plus the questions that would hold the application. Nothing is
typed into the page and nothing is submitted (the applier's dry-run mode stops
before filling). LLM drafts run only when an API key is configured.
``--all`` also lists scanned questions that are optional and left blank.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from unittest.mock import patch

from bot.apply.smart.applier import SmartApplier
from bot.apply.smart.candidate import load_candidate
from bot.apply.smart.drafting import make_generate
from bot.browser import _find_system_chrome
from config.settings import get_data_dir, load_config
from core.filter import detect_ats


@dataclass
class _Raw:
    title: str
    company: str
    apply_url: str
    description: str = ""


@dataclass
class _Job:
    raw: _Raw


def _workbook_jobs(limit: int) -> list[_Raw]:
    from bot.bot import SMART_PLATFORMS
    from bot.search.workbook import WorkbookSearcher
    from core.internship_policy import is_target_internship

    jobs = [j for j in WorkbookSearcher().search(None)
            if is_target_internship(j) and detect_ats(j.apply_url) in SMART_PLATFORMS]
    return [_Raw(j.title, j.company, j.apply_url) for j in jobs[:limit]]


def _verdict(applier: SmartApplier, result) -> str:
    message = result.error_message or ""
    if result.held_for_answers:
        return "HOLD"
    if result.captcha_detected:
        return "CAPTCHA"
    if result.manual_required and "every required question answered" in message:
        return "WOULD SUBMIT"
    return result.status.upper()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("urls", nargs="*")
    parser.add_argument("--workbook", action="store_true",
                        help="use smart-platform postings from the workbook")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--all", action="store_true",
                        help="also list optional questions that would be left blank")
    args = parser.parse_args(argv)

    config = load_config()
    if config is None:
        print("No config.json found in", get_data_dir())
        return 1
    candidate = load_candidate(config.profile, get_data_dir() / "profile" / "candidate.yaml")
    if candidate is None:
        print("No candidate.yaml found in", get_data_dir() / "profile")
        return 1
    generate = make_generate(config.llm) if config.llm.api_key else None
    resume = config.profile.fallback_resume_path

    targets = [_Raw("(posting)", "(company)", u) for u in args.urls]
    if args.workbook:
        targets += _workbook_jobs(args.limit)
    if not targets:
        parser.print_help()
        return 1

    from playwright.sync_api import sync_playwright

    print(f"LLM drafting: {'on' if generate else 'off (no API key) — open questions will show as held'}")
    with sync_playwright() as pw:
        kwargs = {"headless": True}
        chrome = _find_system_chrome()
        if chrome:
            kwargs["executable_path"] = chrome
        browser = pw.chromium.launch(**kwargs)
        for raw in targets:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            applier = SmartApplier(page, candidate, generate=generate, dry_run=True)
            print("\n" + "=" * 100)
            print(f"{raw.company} | {raw.title} | ats={detect_ats(raw.apply_url)}\n{raw.apply_url}")
            try:
                with patch.object(SmartApplier, "_random_pause", lambda *a, **k: None):
                    result = applier.apply(_Job(raw), resume, "", config.profile)
            except Exception as exc:  # keep going through the list
                print(f"  ERROR: {exc}")
                page.close()
                continue
            verdict = _verdict(applier, result)
            print(f"  Form page: {page.url}")
            print(f"  Verdict: {verdict}  {'' if verdict == 'WOULD SUBMIT' else result.error_message or ''}")
            print(f"  Scanned {len(applier.last_questions)} questions"
                  + (f"; CAPTCHA: {applier.last_captcha}" if applier.last_captcha else "")
                  + (f"; notes: {'; '.join(applier.last_notes)}" if applier.last_notes else ""))
            for item in applier.last_plan:
                value = item.value if len(item.value) < 70 else item.value[:67] + "..."
                req = "*" if item.question.required else " "
                print(f"   {req} {item.question.label[:55]:55s} = {value!r:40s} [{item.source[:40]}]")
            for hold in applier.last_holds:
                opts = f" options={list(hold.question.options)[:6]}" if hold.question.options else ""
                print(f"   ! HOLD {hold.question.label[:60]!r} ({hold.question.kind}): {hold.reason}{opts}")
            if args.all:
                planned = {i.question.qid for i in applier.last_plan} | {h.question.qid for h in applier.last_holds}
                for q in applier.last_questions:
                    if q.qid not in planned:
                        opts = f" options={list(q.options)[:5]}" if q.options else ""
                        print(f"   - blank {q.label[:60]!r} ({q.kind}, optional){opts}")
            page.close()
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
