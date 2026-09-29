"""Runtime wiring for the dashboard: everything it needs from the rest of the system, injectable for tests.

``DashboardRuntime`` bundles the data directory, the repository, the run controller, the clock, the
environment and the optional credential store. Four collaborators live in modules owned by other parts of the
system (knowledge base I/O and resume structuring in ``autoapply.tailor``, the workbook inspector in
``autoapply.sources``). They are hooks: tests inject stubs, production resolves them lazily on first use, so
importing the dashboard never imports (or breaks on) those modules.

Config is deliberately NOT cached anywhere: ``read_config`` loads ``config.json`` on every call so edits made
in the dashboard, in an editor or by the scheduler apply immediately.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import os
import threading
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoapply.clock import Clock, SystemClock
from autoapply.config import AppConfig, AppPaths, load_config
from autoapply.contracts import CredentialStore, LLMClient, RunController
from autoapply.db import Repo
from autoapply.models import KnowledgeBase
from autoapply.secrets import KeyResolution, resolve_openai_key

log = logging.getLogger("autoapply.dashboard")

__all__ = [
    "BuildKbFromResume",
    "DashboardRuntime",
    "FeatureUnavailableError",
    "InspectWorkbook",
    "LlmFactory",
    "LoadKb",
    "SaveKb",
]

LoadKb = Callable[[AppPaths], KnowledgeBase]
SaveKb = Callable[[AppPaths, KnowledgeBase], None]
BuildKbFromResume = Callable[[Path, LLMClient], KnowledgeBase]
InspectWorkbook = Callable[[Path], object]
LlmFactory = Callable[[AppConfig], LLMClient | None]


class FeatureUnavailableError(RuntimeError):
    """A collaborator module (tailoring, workbook source) is not importable in this installation."""


def _lazy_callable(module: str, name: str, feature: str) -> Callable[..., Any]:
    """Import ``module.name`` on first use. Never called at import time of the dashboard."""
    try:
        target = getattr(importlib.import_module(module), name)
    except (ImportError, AttributeError) as exc:
        raise FeatureUnavailableError(
            f"{feature} is not available: {module}.{name} could not be imported."
        ) from exc
    if not callable(target):
        raise FeatureUnavailableError(
            f"{feature} is not available: {module}.{name} is not callable."
        )
    result: Callable[..., Any] = target
    return result


def _accepts_config(function: Callable[..., Any]) -> bool:
    """True when ``function`` has a parameter named ``config`` (the real workbook inspector does)."""
    try:
        return "config" in inspect.signature(function).parameters
    except (TypeError, ValueError):
        return False


@dataclass
class DashboardRuntime:
    """Everything the dashboard app needs. Only ``paths``, ``repo`` and ``controller`` are required."""

    paths: AppPaths
    repo: Repo
    controller: RunController
    clock: Clock = field(default_factory=SystemClock)
    env: Mapping[str, str] = field(default_factory=lambda: os.environ)
    store: CredentialStore | None = None
    llm_factory: LlmFactory | None = None
    load_kb: LoadKb | None = None
    save_kb: SaveKb | None = None
    build_kb_from_resume: BuildKbFromResume | None = None
    inspect_workbook: InspectWorkbook | None = None
    # Serialises read-modify-write cycles on config.json across request threads.
    config_lock: AbstractContextManager[bool] = field(
        default_factory=threading.RLock, repr=False, compare=False
    )

    # ------------------------------------------------------------------------------------- config
    def read_config(self) -> AppConfig:
        """Load ``config.json`` fresh (never cached). Raises ``ValueError`` when it is unusable."""
        return load_config(self.paths)

    # ------------------------------------------------------------------------------------- secrets
    def openai_key_resolution(self) -> KeyResolution:
        """Where the OpenAI key comes from. Callers must expose only ``present`` and ``source``."""
        return resolve_openai_key(self.env, self.store)

    def make_llm(self, config: AppConfig) -> LLMClient | None:
        """The LLM for dashboard actions, or ``None`` when none can be built (no key configured)."""
        if self.llm_factory is not None:
            return self.llm_factory(config)
        from autoapply.llm import FAKE_LLM_ENV, TESTING_ENV, build_llm

        resolution = self.openai_key_resolution()
        fake = self.env.get(TESTING_ENV) == "1" and self.env.get(FAKE_LLM_ENV) == "1"
        if not resolution.present and not fake:
            return None
        return build_llm(config, resolution.key, self.env)

    # ------------------------------------------------------------------------------------- hooks
    def load_knowledge_base(self) -> KnowledgeBase:
        hook = self.load_kb or _lazy_callable(
            "autoapply.tailor", "load_kb", "The knowledge base loader"
        )
        result: KnowledgeBase = hook(self.paths)
        return result

    def save_knowledge_base(self, kb: KnowledgeBase) -> None:
        hook = self.save_kb or _lazy_callable(
            "autoapply.tailor", "save_kb", "The knowledge base writer"
        )
        hook(self.paths, kb)

    def build_knowledge_base(self, pdf_path: Path, llm: LLMClient) -> KnowledgeBase:
        hook = self.build_kb_from_resume or _lazy_callable(
            "autoapply.tailor", "build_kb_from_resume", "The resume structuring step"
        )
        result: KnowledgeBase = hook(pdf_path, llm)
        return result

    def inspect_workbook_file(self, path: Path, config: AppConfig) -> object:
        """Run the workbook inspector. An injected hook is called as ``hook(path)``; the real inspector also
        receives the config so the user's ``workbook.sheet`` / ``column_map`` overrides are honoured."""
        if self.inspect_workbook is not None:
            return self.inspect_workbook(path)
        function = _lazy_callable("autoapply.sources", "inspect_workbook", "The workbook inspector")
        if _accepts_config(function):
            return function(path, config=config)
        return function(path)

    # ------------------------------------------------------------------------------------- misc
    @property
    def stop_active(self) -> bool:
        """Whether the kill-switch file exists."""
        try:
            return self.paths.stop_file.exists()
        except OSError:
            return False
