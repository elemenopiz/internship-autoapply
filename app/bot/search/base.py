"""Base classes for job search engines.

Implements: FR-044 (search abstraction).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from config.settings import SearchCriteria


@dataclass
class RawJob:
    """A job listing scraped from a platform, before scoring."""

    title: str
    company: str
    location: str
    salary: str | None
    description: str
    apply_url: str
    platform: str  # "linkedin" | "indeed"
    external_id: str  # Platform-specific ID for deduplication
    posted_at: str | None


class BaseSearcher(ABC):
    """Abstract base for platform-specific job searchers."""

    @abstractmethod
    def search(self, criteria: SearchCriteria, page=None) -> Iterator[RawJob]:
        """Yield RawJob objects one at a time as discovered.

        Args:
            criteria: Search parameters (titles, locations, filters).
            page: Optional Playwright Page for browser-based searching.
        """
        ...
