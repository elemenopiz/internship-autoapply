"""Local dashboard: FastAPI + Jinja2 + vanilla JS (docs/SPEC.md section 5.13)."""

from autoapply.dashboard.app import create_app
from autoapply.dashboard.deps import DashboardRuntime
from autoapply.dashboard.server import run_server

__all__ = ["DashboardRuntime", "create_app", "run_server"]
