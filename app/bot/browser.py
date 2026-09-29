"""Browser manager — persistent Playwright context for bot automation.

Implements: FR-043 (browser session management).
"""

from __future__ import annotations

import logging
import os
import platform
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from config.settings import AppConfig

logger = logging.getLogger(__name__)


def _find_system_chrome() -> str | None:
    """Find system-installed Chrome for faster browser sessions."""
    candidates = []
    if platform.system() == "Windows":
        for base in [
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            os.path.expandvars(r"%LOCALAPPDATA%"),
        ]:
            candidates.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))
    elif platform.system() == "Darwin":
        candidates.append("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    else:
        candidates.extend(["/usr/bin/google-chrome", "/usr/bin/chromium-browser", "/usr/bin/chromium"])

    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


class BrowserManager:
    """Manages a Playwright persistent browser context.

    Preserves login sessions between bot runs by using a persistent
    user data directory at ``~/.autoapply/browser_profile/``.
    """

    def __init__(self, config: "AppConfig") -> None:
        self.headless = config.bot.apply_mode != "watch"
        from config.settings import get_data_dir
        self.profile_dir = get_data_dir() / "browser_profile"
        self.profile_dir.mkdir(parents=True, exist_ok=True)

        self._playwright: Any = None
        self._context: Any = None
        self._page: Any = None

    def get_page(self):
        """Get or create a Playwright Page in a persistent context.

        Returns:
            A Playwright Page instance.

        Raises:
            RuntimeError: If Playwright or Chromium is not installed.
        """
        if self._page and not self._page.is_closed():
            return self._page

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError(
                "Playwright is required but not installed. "
                "Run: pip install playwright && python -m playwright install chromium"
            )

        if self._playwright is None:
            self._playwright = sync_playwright().start()

        try:
            chrome_path = _find_system_chrome()
            launch_kwargs = dict(
                user_data_dir=str(self.profile_dir),
                headless=self.headless,
                viewport={"width": 1280, "height": 800},
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-extensions",
                    "--disable-default-apps",
                    "--disable-popup-blocking",
                    "--disable-sync",
                    "--disable-translate",
                ],
                ignore_default_args=["--enable-automation"],
            )
            if chrome_path:
                launch_kwargs["executable_path"] = chrome_path
                logger.info("Using system Chrome: %s", chrome_path)

            self._context = self._playwright.chromium.launch_persistent_context(
                **launch_kwargs
            )
        except Exception as e:
            error_msg = str(e)
            if "executable doesn't exist" in error_msg.lower():
                raise RuntimeError(
                    "Playwright Chromium not installed. "
                    "Run: python -m playwright install chromium"
                )
            raise

        self._page = self._context.new_page()
        logger.info(
            "Browser started (headless=%s, profile=%s)",
            self.headless, self.profile_dir,
        )
        return self._page

    def close(self) -> None:
        """Close the browser context and cleanup Playwright."""
        if self._context:
            try:
                self._context.close()
            except Exception as e:
                logger.debug("Failed to close browser context: %s", e)
            self._context = None
            self._page = None

        if self._playwright:
            try:
                self._playwright.stop()
            except Exception as e:
                logger.debug("Failed to stop Playwright: %s", e)
            self._playwright = None

        logger.info("Browser closed")
