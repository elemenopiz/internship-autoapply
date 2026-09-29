"""Shared application state — singleton accessed by all blueprints.

Implements: FR-081 (shared state module).

This module holds the mutable globals that were previously in app.py:
db, bot_state, socketio, bot_scheduler, locks, tokens, etc.
Initialized once by create_app() in app.py, then imported by route modules.
"""

from __future__ import annotations

import re
import subprocess
import threading

from flask_socketio import SocketIO

from bot.state import BotState
from core.scheduler import BotScheduler
from db.database import Database

# Populated by create_app() —————————————————————————————————————————————
socketio: SocketIO | None = None
db: Database | None = None
bot_state: BotState = BotState()
bot_scheduler: BotScheduler | None = None
api_token: str = ""

# Bot thread management ————————————————————————————————————————————————
bot_lock = threading.Lock()
bot_thread: threading.Thread | None = None

# Login browser management —————————————————————————————————————————————
login_proc: subprocess.Popen[bytes] | None = None
login_lock = threading.Lock()

# Validation helpers ———————————————————————————————————————————————————
SAFE_FILENAME_RE = re.compile(r"^[a-zA-Z0-9_\- ]+\.txt$")

VALID_APP_STATUSES = {
    "applied", "manual_required", "error", "skipped", "saved",
    "reviewed", "interview", "interviewed", "interviewing",
    "rejected", "accepted", "withdrawn", "offer",
}
