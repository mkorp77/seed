"""Calibration probe runner and capability profile builder for Seed Book 5."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

try:  # SQLAlchemy is present in Seed; optional for pure function smoke tests.
    from sqlalchemy import text  # type: ignore
except Exception:  # pragma: no cover
    text = None  # type: ignore

from seed_provider_config import normalize_thinking_level
from seed_providers import ProviderAdapter, ProviderResponse


@dataclass
class Probe:
    id: str
    domain: str
    question: str
    correct_patterns: List[str]
    fail_patterns: List[str]
    score_type: str
    timeout_seconds: int = 5


@dataclass
class ProbeResult:
    probe_id: str
    provider: str
    model: str
    domain: str
    passed: bool
    score: float
    max_score: float
    response_text: str
    latency_ms: int
    error: Optional[str] = None
    matched_pattern: Optional[str] = None
    tested_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["tested_at"] = self.tested_at.isoformat()
        return data


@dataclass
class CapabilityProfile:
    provider: str
    model: str
    thinking_level: str
    domain_scores: Dict[str, Dict[str, Any]]
    total_probes: int = 0
    total_passed: int = 0
    tested_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    stale_after: Optional[datetime] = None
    raw_results: Optional[List[Dict[str, Any]]] = None

    def __post_init__(self) -> None:
        self.thinking_level = normalize_thinking_level(self.thinking_level)
        if self.stale_after is None:
            self.stale_after = self.tested_at + timedelta(days=3)

    def is_stale(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(timezone.utc)
        stale_after = _ensure_aware(self.stale_after) if self.stale_after else self.tested_at + timedelta(days=3)
        return stale_after <= now

    def score_for_domain(self, domain: str) -> float:
        entry = self.domain_scores.get(domain) or self.domain_scores.get("all") or {}
        return float(entry.get("score", 0.0) or 0.0)

    def passed_total_for_domain(self, domain: str) -> tuple[int, int]:
        entry = self.domain_scores.get(domain) or self.domain_scores.get("all") or {}
        return int(entry.get("passed", 0) or 0), int(entry.get("total", 0) or 0)

    def age_hours(self, now: Optional[datetime] = None) -> float:
        now = now or datetime.now(timezone.utc)
        tested = _ensure_aware(self.tested_at)
        return max(0.0, (now - tested).total_seconds() / 3600.0)

    def to_record(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "thinking_level": self.thinking_level,
            "domain_scores": self.domain_scores,
            "total_probes": self.total_probes,
            "total_passed": self.total_passed,
            "tested_at": self.tested_at.isoformat(),
            "stale_after": self.stale_after.isoformat() if self.stale_after else None,
            "raw_results": self.raw_results,
        }


def run_probe(adapter: ProviderAdapter, probe: Probe, thinking_level: str = "normal") -> ProbeResult:
    """Call the model with a probe question and score the response.

    Scoring order is fail patterns first, then correct patterns, then uncertain.
    For binary probes, uncertain receives 0. For 0-2 probes, uncertain receives 1.
    """
    previous_timeout = getattr(adapter, "timeout_seconds", None)
    if previous_timeout is not None:
        adapter.timeout_seconds = probe.timeout_seconds
    try:
        response = adapter.call(
            probe.question,
            system=PROBE_SYSTEM,
            thinking_level=thinking_level,
            max_tokens=500,
        )
    finally:
        if previous_timeout is not None:
            adapter.timeout_seconds = previous_timeout

    max_score = 2.0 if probe.score_type == "0-2" else 1.0
    text_value = response.text or response.error or ""

    fail_match = _first_match(probe.fail_patterns, text_value)
    if fail_match:
        return ProbeResult(
            probe_id=probe.id,
            provider=response.provider,
            model=response.model,
            domain=probe.domain,
            passed=False,
            score=0.0,
            max_score=max_score,
            response_text=response.text,
            latency_ms=response.latency_ms,
            error=response.error,
            matched_pattern=fail_match,
        )

    correct_match = _first_match(probe.correct_patterns, text_value)
    if correct_match and not response.error:
        return ProbeResult(
            probe_id=probe.id,
            provider=response.provider,
            model=response.model,
            domain=probe.domain,
            passed=True,
            score=max_score,
            max_score=max_score,
            response_text=response.text,
            latency_ms=response.latency_ms,
            error=response.error,
            matched_pattern=correct_match,
        )

    uncertain_score = 1.0 if probe.score_type == "0-2" and not response.error else 0.0
    return ProbeResult(
        probe_id=probe.id,
        provider=response.provider,
        model=response.model,
        domain=probe.domain,
        passed=False,
        score=uncertain_score,
        max_score=max_score,
        response_text=response.text,
        latency_ms=response.latency_ms,
        error=response.error,
        matched_pattern=None,
    )


def build_profile(adapter: ProviderAdapter, domain: str = "all", thinking_level: str = "normal") -> CapabilityProfile:
    """Run probes for a domain/all domains and aggregate a capability profile."""
    thinking = normalize_thinking_level(thinking_level)
    probes = get_probe_bank(domain)
    results = [run_probe(adapter, probe, thinking_level=thinking) for probe in probes]
    domain_scores = aggregate_domain_scores(results)
    tested_at = datetime.now(timezone.utc)
    return CapabilityProfile(
        provider=adapter.name,
        model=adapter.model,
        thinking_level=thinking,
        domain_scores=domain_scores,
        total_probes=len(results),
        total_passed=sum(1 for result in results if result.passed),
        tested_at=tested_at,
        stale_after=tested_at + timedelta(days=3),
        raw_results=[result.to_dict() for result in results],
    )


def aggregate_domain_scores(results: Iterable[ProbeResult]) -> Dict[str, Dict[str, Any]]:
    buckets: Dict[str, Dict[str, Any]] = {}
    total_score = 0.0
    total_max = 0.0
    total_passed = 0
    total_count = 0

    for result in results:
        bucket = buckets.setdefault(result.domain, {"score": 0.0, "points": 0.0, "max_points": 0.0, "passed": 0, "total": 0})
        bucket["points"] += result.score
        bucket["max_points"] += result.max_score
        bucket["passed"] += 1 if result.passed else 0
        bucket["total"] += 1
        total_score += result.score
        total_max += result.max_score
        total_passed += 1 if result.passed else 0
        total_count += 1

    for bucket in buckets.values():
        max_points = float(bucket["max_points"] or 0.0)
        bucket["score"] = round(float(bucket["points"]) / max_points, 4) if max_points else 0.0

    buckets["all"] = {
        "score": round(total_score / total_max, 4) if total_max else 0.0,
        "points": total_score,
        "max_points": total_max,
        "passed": total_passed,
        "total": total_count,
    }
    return buckets


def save_profile(db: Any, profile: CapabilityProfile) -> bool:
    """Persist a capability profile. Returns False if DB support/table is unavailable."""
    if db is None or text is None:
        return False
    try:
        db.execute(
            text(
                """
                INSERT INTO seed_capability_profiles
                    (provider, model, thinking_level, domain_scores, total_probes, total_passed, tested_at, stale_after, raw_results)
                VALUES
                    (:provider, :model, :thinking_level, CAST(:domain_scores AS jsonb), :total_probes, :total_passed,
                     :tested_at, :stale_after, CAST(:raw_results AS jsonb))
                """
            ),
            {
                "provider": profile.provider,
                "model": profile.model,
                "thinking_level": profile.thinking_level,
                "domain_scores": json.dumps(profile.domain_scores),
                "total_probes": profile.total_probes,
                "total_passed": profile.total_passed,
                "tested_at": profile.tested_at,
                "stale_after": profile.stale_after,
                "raw_results": json.dumps(profile.raw_results or []),
            },
        )
        db.commit()
        return True
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return False


def profile_from_mapping(row: Dict[str, Any]) -> CapabilityProfile:
    domain_scores = row.get("domain_scores") or {}
    raw_results = row.get("raw_results")
    if isinstance(domain_scores, str):
        domain_scores = json.loads(domain_scores)
    if isinstance(raw_results, str):
        raw_results = json.loads(raw_results)
    return CapabilityProfile(
        provider=row.get("provider") or "unknown",
        model=row.get("model") or "unknown",
        thinking_level=row.get("thinking_level") or "normal",
        domain_scores=domain_scores,
        total_probes=int(row.get("total_probes") or 0),
        total_passed=int(row.get("total_passed") or 0),
        tested_at=_parse_dt(row.get("tested_at")) or datetime.now(timezone.utc),
        stale_after=_parse_dt(row.get("stale_after")),
        raw_results=raw_results,
    )


def get_probe_bank(domain: str = "all") -> List[Probe]:
    from seed_probe_bank import PROBE_BANK

    if domain == "all":
        return list(PROBE_BANK)
    return [probe for probe in PROBE_BANK if probe.domain == domain]


def _first_match(patterns: Iterable[str], text_value: str) -> Optional[str]:
    for pattern in patterns:
        if re.search(pattern, text_value or "", flags=re.IGNORECASE | re.DOTALL):
            return pattern
    return None


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return _ensure_aware(value)
    if isinstance(value, str):
        try:
            return _ensure_aware(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


PROBE_SYSTEM = (
    "You are being calibrated. Answer the question directly, with enough detail "
    "to expose the operational decision. Do not be evasive."
)
