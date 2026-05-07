"""Parallel model comparison route for Seed Book 5."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

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

from seed_provider_config import normalize_thinking_level
from seed_providers import ProviderResponse, get_adapter, provider_names_for_all_request

router = APIRouter(tags=["model-compare"])


class CompareRequest(BaseModel):
    prompt: str
    system: str = ""
    models: List[str] = Field(default_factory=list)
    thinking_level: str = "normal"
    max_tokens: int = Field(default=1000, ge=1, le=16000)


@router.post("/compare")
def compare_models(
    request: CompareRequest,
    db: Any = Depends(get_db),
    api_key: Any = Depends(get_api_key),
) -> Dict[str, Any]:
    """Call multiple providers in parallel and surface simple disagreements."""
    start = time.perf_counter()
    names = provider_names_for_all_request(request.models)
    thinking_level = normalize_thinking_level(request.thinking_level)
    responses = _call_all(names, request.prompt, request.system, thinking_level, request.max_tokens)
    disagreements = detect_disagreements(responses)
    total_latency_ms = int((time.perf_counter() - start) * 1000)
    return {
        "prompt": request.prompt,
        "responses": [response.to_dict(include_raw=False) for response in responses],
        "consensus": len(disagreements) == 0 and any(not response.error for response in responses),
        "disagreements": disagreements,
        "total_latency_ms": total_latency_ms,
    }


def _call_all(
    provider_names: List[str],
    prompt: str,
    system: str,
    thinking_level: str,
    max_tokens: int,
) -> List[ProviderResponse]:
    if not provider_names:
        return []
    responses_by_index: Dict[int, ProviderResponse] = {}
    with ThreadPoolExecutor(max_workers=min(len(provider_names), 8)) as executor:
        futures = {
            executor.submit(_call_one, provider_name, prompt, system, thinking_level, max_tokens): idx
            for idx, provider_name in enumerate(provider_names)
        }
        for future in as_completed(futures):
            idx = futures[future]
            try:
                responses_by_index[idx] = future.result()
            except Exception as exc:  # Defensive; adapters should already catch.
                responses_by_index[idx] = ProviderResponse(
                    model="unknown",
                    provider=provider_names[idx],
                    text="",
                    thinking=None,
                    tokens_in=0,
                    tokens_out=0,
                    latency_ms=0,
                    raw={"exception": f"{exc.__class__.__name__}: {exc}"},
                    error=f"adapter_failure: {exc.__class__.__name__}: {exc}",
                )
    return [responses_by_index[idx] for idx in sorted(responses_by_index)]


def _call_one(provider_name: str, prompt: str, system: str, thinking_level: str, max_tokens: int) -> ProviderResponse:
    adapter = get_adapter(provider_name)
    return adapter.call(prompt, system=system, thinking_level=thinking_level, max_tokens=max_tokens)


def detect_disagreements(responses: List[ProviderResponse]) -> List[str]:
    """Surface-level conflicting recommendation detector.

    This intentionally does not adjudicate correctness. It flags obvious keyword
    conflicts so a human can inspect the responses.
    """
    disagreements: List[str] = []
    signals_by_provider = {response.provider: _signals(response.text) for response in responses if not response.error}

    for response in responses:
        if response.error:
            disagreements.append(f"{response.provider} returned error: {response.error[:160]}")

    providers = list(signals_by_provider.keys())
    for i, left_provider in enumerate(providers):
        for right_provider in providers[i + 1 :]:
            left = signals_by_provider[left_provider]
            right = signals_by_provider[right_provider]
            for a, b, label_a, label_b in CONFLICT_PAIRS:
                if a in left and b in right:
                    disagreements.append(f"{left_provider} recommends {label_a}, {right_provider} recommends {label_b}")
                elif b in left and a in right:
                    disagreements.append(f"{left_provider} recommends {label_b}, {right_provider} recommends {label_a}")
    return _dedupe(disagreements)


def _signals(text: str) -> Set[str]:
    value = (text or "").lower()
    signals: Set[str] = set()
    if "-fc" in value or "custom format" in value or "pg_restore" in value:
        signals.add("pg_custom")
    if "plain sql" in value or "psql" in value or ".sql" in value:
        signals.add("pg_plain")
    if "down -v" in value or "remove volume" in value or "delete volume" in value or "data loss" in value:
        signals.add("destructive")
    if "safe" in value or "survive" in value or "persist" in value:
        signals.add("safe")
    if "do not" in value or "should not" in value or "avoid" in value:
        signals.add("no")
    if "should" in value or "use" in value or "recommend" in value:
        signals.add("yes")
    return signals


def _dedupe(values: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


CONFLICT_PAIRS = [
    ("pg_custom", "pg_plain", "pg_dump -Fc/custom format", "plain SQL"),
    ("destructive", "safe", "destructive/data-loss behavior", "safe/persistent behavior"),
    ("yes", "no", "yes/use", "no/avoid"),
]
