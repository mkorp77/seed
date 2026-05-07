"""Model router for Seed Book 5.

Routes are relative. seed_api.py owns the /api prefix and includes this router
with router.include_router(seed_router.router).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

try:
    from sqlalchemy import text  # type: ignore
except Exception:  # pragma: no cover
    text = None  # type: ignore

try:
    from seed_deps import get_db  # type: ignore
except Exception:  # pragma: no cover
    def get_db() -> None:  # type: ignore
        return None

try:
    from seed_auth import get_api_key  # type: ignore
except Exception:  # pragma: no cover
    def get_api_key() -> None:  # type: ignore
        return None

from seed_provider_config import PROVIDER_CONFIG, PROVIDER_ORDER, get_default_model, normalize_provider_name
from seed_probes import CapabilityProfile, profile_from_mapping
from seed_providers import get_adapter

router = APIRouter(tags=["model-router"])


@dataclass
class TaskClassification:
    domain: str
    complexity: str
    risk: str
    matched_keywords: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ModelSelection:
    provider: str
    model: str
    thinking_level: str
    reasoning: str
    fallbacks: List[str]
    profile_age_hours: Optional[float] = None
    domain_score: float = 0.0
    domain_passed: int = 0
    domain_total: int = 0
    soak_required: bool = False
    minimum_required_score: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RouteRequest(BaseModel):
    task: str
    prefer_provider: Optional[str] = None
    require_domain_score: Optional[float] = None
    system: str = ""
    max_tokens: int = Field(default=1000, ge=1, le=16000)


class RouteExecResponse(BaseModel):
    task_domain: str
    task_complexity: str
    task_risk: str
    selected_provider: str
    selected_model: str
    thinking_level: str
    reasoning: str
    fallbacks: List[str]
    profile_age_hours: Optional[float]
    domain_score: float
    domain_passed: int
    domain_total: int
    soak_required: bool
    response: Dict[str, Any]


@router.post("/route")
def route_task(
    request: RouteRequest,
    db: Any = Depends(get_db),
    api_key: Any = Depends(get_api_key),
) -> Dict[str, Any]:
    """Classify a task and recommend a provider/model. Does not call a model."""
    classification = classify_task(request.task)
    selection = select_model(
        classification,
        load_profiles_from_db(db),
        prefer_provider=request.prefer_provider,
        require_domain_score=request.require_domain_score,
    )
    return _route_payload(classification, selection)


@router.post("/route/exec")
def route_exec(
    request: RouteRequest,
    db: Any = Depends(get_db),
    api_key: Any = Depends(get_api_key),
) -> Dict[str, Any]:
    """Classify, select, call the selected provider, and return the model response."""
    classification = classify_task(request.task)
    selection = select_model(
        classification,
        load_profiles_from_db(db),
        prefer_provider=request.prefer_provider,
        require_domain_score=request.require_domain_score,
    )
    adapter = get_adapter(selection.provider, model=selection.model)
    provider_response = adapter.call(
        request.task,
        system=request.system,
        thinking_level=selection.thinking_level,
        max_tokens=request.max_tokens,
    )
    payload = _route_payload(classification, selection)
    payload["response"] = provider_response.to_dict(include_raw=False)
    return payload


def classify_task(description: str) -> TaskClassification:
    """Determine domain, complexity, and risk via keyword heuristics."""
    text_value = f" {description or ''} ".lower()
    domain_scores: Dict[str, int] = {}
    matched: List[str] = []
    for domain, keywords in DOMAIN_KEYWORDS.items():
        count = 0
        for keyword in keywords:
            if re.search(keyword, text_value, flags=re.IGNORECASE):
                count += 1
                matched.append(keyword.strip(r"\b"))
        domain_scores[domain] = count

    domain = max(domain_scores.items(), key=lambda item: (item[1], DOMAIN_PRIORITY.index(item[0]) * -1))[0]
    if domain_scores[domain] == 0:
        domain = "general_reasoning"

    complexity = "low"
    if any(re.search(pattern, text_value) for pattern in HIGH_COMPLEXITY_PATTERNS):
        complexity = "high"
    elif any(re.search(pattern, text_value) for pattern in MEDIUM_COMPLEXITY_PATTERNS) or len(description.split()) > 16:
        complexity = "medium"

    risk = "low"
    if any(re.search(pattern, text_value) for pattern in HIGH_RISK_PATTERNS):
        risk = "high"
    elif any(re.search(pattern, text_value) for pattern in MEDIUM_RISK_PATTERNS):
        risk = "medium"

    return TaskClassification(domain=domain, complexity=complexity, risk=risk, matched_keywords=sorted(set(matched))[:20])


def select_model(
    task: TaskClassification,
    profiles: List[CapabilityProfile],
    prefer_provider: Optional[str] = None,
    require_domain_score: Optional[float] = None,
) -> ModelSelection:
    """Pick the best model using fresh capability profiles and risk adjustment."""
    now = datetime.now(timezone.utc)
    fresh = [profile for profile in profiles if not profile.is_stale(now)]
    candidates = fresh or _default_profiles()
    preferred = normalize_provider_name(prefer_provider) if prefer_provider else None
    required = _minimum_score_for_risk(task.risk, require_domain_score)
    thinking_level = _thinking_level_for_complexity(task.complexity)

    ranked = sorted(
        candidates,
        key=lambda profile: (
            1 if preferred and profile.provider == preferred else 0,
            profile.score_for_domain(task.domain),
            profile.total_passed,
            -profile.age_hours(now),
            _provider_order_rank(profile.provider) * -1,
        ),
        reverse=True,
    )

    qualified = [profile for profile in ranked if profile.score_for_domain(task.domain) >= required]
    selected = (qualified or ranked)[0]
    fallbacks = [profile.provider for profile in ranked if profile.provider != selected.provider][:3]
    score = selected.score_for_domain(task.domain)
    passed, total = selected.passed_total_for_domain(task.domain)
    age = selected.age_hours(now)
    no_real_profile = not fresh
    below_required = score < required
    soak_required = task.risk == "high" or below_required

    if no_real_profile:
        reasoning = (
            f"No fresh capability profile is available; using {selected.provider} default order "
            f"for {task.domain}. Run calibration probes before high-risk use."
        )
    else:
        score_text = f"{passed}/{total}" if total else f"score {score:.2f}"
        reasoning = (
            f"{selected.provider} scored {score_text} on {task.domain} probes "
            f"(tested {age:.1f}h ago)."
        )
        if preferred and selected.provider == preferred:
            reasoning += " Provider preference was honored."
        elif preferred:
            reasoning += f" Provider preference {preferred} was not highest qualified."
    if below_required:
        reasoning += f" Domain score {score:.2f} is below required {required:.2f}; soak before acting."
    elif task.risk == "high":
        reasoning += " High-risk task: soak/review before acting."

    return ModelSelection(
        provider=selected.provider,
        model=selected.model,
        thinking_level=thinking_level,
        reasoning=reasoning,
        fallbacks=fallbacks,
        profile_age_hours=round(age, 2) if not no_real_profile else None,
        domain_score=round(score, 4),
        domain_passed=passed,
        domain_total=total,
        soak_required=soak_required,
        minimum_required_score=required,
    )


def load_profiles_from_db(db: Any) -> List[CapabilityProfile]:
    if db is None or text is None:
        return []
    try:
        result = db.execute(
            text(
                """
                SELECT provider, model, thinking_level, domain_scores, total_probes, total_passed,
                       tested_at, stale_after, raw_results
                FROM seed_capability_profiles
                ORDER BY tested_at DESC
                LIMIT 100
                """
            )
        )
        if hasattr(result, "mappings"):
            rows = result.mappings().all()
        else:
            rows = result.fetchall()
        profiles = []
        seen = set()
        for row in rows:
            mapping = dict(row)
            key = (mapping.get("provider"), mapping.get("model"), mapping.get("thinking_level"))
            if key in seen:
                continue
            seen.add(key)
            profiles.append(profile_from_mapping(mapping))
        return profiles
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return []


def _route_payload(classification: TaskClassification, selection: ModelSelection) -> Dict[str, Any]:
    return {
        "task_domain": classification.domain,
        "task_complexity": classification.complexity,
        "task_risk": classification.risk,
        "selected_provider": selection.provider,
        "selected_model": selection.model,
        "thinking_level": selection.thinking_level,
        "reasoning": selection.reasoning,
        "fallbacks": selection.fallbacks,
        "profile_age_hours": selection.profile_age_hours,
        "domain_score": selection.domain_score,
        "domain_passed": selection.domain_passed,
        "domain_total": selection.domain_total,
        "soak_required": selection.soak_required,
        "minimum_required_score": selection.minimum_required_score,
    }


def _default_profiles() -> List[CapabilityProfile]:
    now = datetime.now(timezone.utc)
    profiles: List[CapabilityProfile] = []
    for provider in PROVIDER_ORDER:
        if provider not in PROVIDER_CONFIG:
            continue
        profiles.append(
            CapabilityProfile(
                provider=provider,
                model=get_default_model(provider),
                thinking_level="normal",
                domain_scores={"all": {"score": 0.0, "passed": 0, "total": 0}},
                total_probes=0,
                total_passed=0,
                tested_at=now,
                stale_after=now + timedelta(minutes=5),
                raw_results=[],
            )
        )
    return profiles


def _provider_order_rank(provider: str) -> int:
    try:
        return len(PROVIDER_ORDER) - PROVIDER_ORDER.index(provider)
    except ValueError:
        return 0


def _thinking_level_for_complexity(complexity: str) -> str:
    if complexity == "low":
        return "low"
    if complexity == "high":
        return "high"
    return "normal"


def _minimum_score_for_risk(risk: str, explicit: Optional[float]) -> float:
    base = 0.0 if explicit is None else max(0.0, min(1.0, float(explicit)))
    if risk == "high":
        return max(base, 0.8)
    if risk == "medium":
        return max(base, 0.4)
    return base


DOMAIN_PRIORITY = ["seed", "infrastructure", "trading", "models_ai", "general_reasoning"]

DOMAIN_KEYWORDS: Dict[str, List[str]] = {
    "seed": [
        r"\bseed\b", r"anchor", r"scar", r"mutabil", r"append[- ]?only", r"knowledge node", r"wiki", r"context",
    ],
    "infrastructure": [
        r"docker", r"compose", r"wsl2", r"postgres", r"pg_dump", r"pg_restore", r"nssm", r"dns",
        r"volume", r"container", r"service", r"git", r"merge", r"backup", r"restore", r"deploy", r"nginx",
        r"database", r"migration", r"api", r"server",
    ],
    "trading": [
        r"\bes\b", r"futures", r"fomc", r"sharpe", r"backtest", r"rth", r"eth", r"session",
        r"strategy", r"pnl", r"drawdown", r"slippage", r"trade", r"roll", r"market", r"order",
    ],
    "models_ai": [
        r"\bllm\b", r"model", r"moe", r"dense", r"quant", r"prismaquant", r"context", r"memory",
        r"embedding", r"prompt", r"inference", r"reasoning", r"token", r"adapter",
    ],
    "general_reasoning": [
        r"sunk cost", r"correlation", r"causation", r"authority", r"recency", r"decision", r"evidence",
    ],
}

HIGH_COMPLEXITY_PATTERNS = [
    r"architect", r"architecture", r"multi[- ]?step", r"orchestrat", r"speciali[sz]e", r"design",
    r"refactor", r"migrat", r"review.*strategy", r"backtest validity", r"end[- ]?to[- ]?end",
    r"split.*subtasks", r"prove", r"verify", r"collaborat",
]
MEDIUM_COMPLEXITY_PATTERNS = [
    r"compare", r"analy[sz]e", r"should", r"check", r"debug", r"diagnos", r"backup", r"restore",
    r"roll", r"bias", r"timing", r"session", r"explain", r"recommend",
]
HIGH_RISK_PATTERNS = [
    r"down\s+-v", r"drop\s+database", r"delete", r"remove\s+volume", r"rm\s+-rf", r"truncate",
    r"live\s+(trade|order)", r"financial", r"production\s+deploy", r"rotate\s+key", r"secret",
]
MEDIUM_RISK_PATTERNS = [
    r"modify", r"write", r"change", r"backup", r"restore", r"service", r"volume", r"auth", r"permission",
    r"database", r"migration", r"deploy", r"trade", r"strategy", r"postgres",
]
