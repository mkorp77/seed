from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Sequence


# App-layer config. Keep this out of the DB for Book 2.
TAG_DOMAIN_MAP: dict[str, str] = {
    # hardware domain
    "hardware": "hardware",
    "nvidia": "hardware",
    "cluster": "hardware",
    "gb10": "hardware",
    "infrastructure": "hardware",

    # trading domain
    "trading": "trading",
    "fomc-edge": "trading",
    "bvc": "trading",
    "lab-pipe": "trading",

    # seed domain
    "architecture": "seed",
    "data-schema": "seed",
    "trust-protocol": "seed",
    "brain-evolution": "seed",

    # models domain
    "models": "models",
    "gemini": "models",
    "orchestration": "models",

    # anthropic-ops domain
    "debugging": "anthropic-ops",
    "build-log": "anthropic-ops",
}

DEFAULT_DOMAIN = "seed"


@dataclass(frozen=True)
class DomainDetection:
    domain: str
    confidence: str
    matching_tags: list[str]


def _norm_tag(tag: str) -> str:
    return str(tag).strip().lower()


def parse_tags_param(tags: str | Sequence[str] | None) -> list[str]:
    """Accept comma-separated query params or an already-materialized tag list."""
    if tags is None:
        return []
    if isinstance(tags, str):
        return [t.strip() for t in tags.split(",") if t.strip()]
    return [str(t).strip() for t in tags if str(t).strip()]


def detect_domain(tags: Iterable[str] | None) -> DomainDetection:
    """
    Resolution rules:
    1. Count domain hits across all tags.
    2. Domain with most hits wins.
    3. Tie resolves to the first matching tag's domain.
    4. No matches resolves to DEFAULT_DOMAIN.
    """
    tag_list = parse_tags_param(list(tags or []))
    hits: list[tuple[str, str]] = []  # (original_tag, domain)

    for tag in tag_list:
        domain = TAG_DOMAIN_MAP.get(_norm_tag(tag))
        if domain:
            hits.append((tag, domain))

    if not hits:
        return DomainDetection(
            domain=DEFAULT_DOMAIN,
            confidence="low",
            matching_tags=[],
        )

    counts = Counter(domain for _, domain in hits)
    max_count = max(counts.values())
    tied_domains = {domain for domain, count in counts.items() if count == max_count}

    if len(tied_domains) == 1:
        domain = next(iter(tied_domains))
    else:
        # First matching tag's domain wins ties.
        domain = next(domain for _, domain in hits if domain in tied_domains)

    matching_tags = [tag for tag, hit_domain in hits if hit_domain == domain]
    confidence = "high" if len(matching_tags) >= 2 else "medium"

    return DomainDetection(
        domain=domain,
        confidence=confidence,
        matching_tags=matching_tags,
    )
