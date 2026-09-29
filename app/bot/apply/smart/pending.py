"""The list of questions the bot could not answer truthfully on its own.

Held applications append their blocking questions here (deduplicated by
question text, with a count and the companies that asked). The user answers
each one once — as a screening answer in config.json or under
``prepared_answers`` in candidate.yaml — and later runs submit on their own.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path

from bot.apply.smart.candidate import normalize

logger = logging.getLogger(__name__)

_lock = threading.Lock()


def load(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def record(path: Path, company: str, title: str, url: str,
           items: list[tuple[str, str, tuple[str, ...], str]]) -> None:
    """Merge (label, kind, options, reason) items into the pending list."""
    if not items:
        return
    with _lock:
        data = load(path)
        now = datetime.now().isoformat(timespec="seconds")
        for label, kind, options, reason in items:
            key = normalize(label) or f"unlabeled {kind}"
            entry = data.setdefault(key, {
                "question": label, "kind": kind, "options": list(options),
                "reason": reason, "times_seen": 0, "companies": [],
            })
            entry["times_seen"] += 1
            entry["reason"] = reason
            entry["last_seen"] = now
            entry["example_url"] = url
            where = f"{company} — {title}"
            if where not in entry["companies"]:
                entry["companies"].append(where)
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False),
                                  encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not write pending questions to %s: %s", path, exc)
