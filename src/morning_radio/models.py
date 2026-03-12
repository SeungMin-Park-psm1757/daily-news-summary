from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class CategoryDefinition:
    key: str
    label: str
    queries: tuple[str, ...]
    priority_terms: tuple[str, ...] = ()
    penalty_terms: tuple[str, ...] = ()


@dataclass(slots=True)
class NewsItem:
    category: str
    title: str
    source: str
    source_domain: str
    url: str
    published_at: datetime
    summary: str
    query: str
    fingerprint: str
    score: float = 0.0
    source_weight: float = 0.0
    resolved_url: str | None = None
    cluster_id: str | None = None
    cluster_size: int = 1
    verification_flags: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["published_at"] = self.published_at.isoformat()
        return data


@dataclass(slots=True)
class CategoryBrief:
    category: str
    label: str
    lead: str
    stories: list[dict[str, Any]]
    watch: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RadioShow:
    show_title: str
    show_summary: str
    estimated_minutes: int
    script_markdown: str
    script_plaintext: str
    quiet_categories: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
