"""Headless model collaboration patterns for Seed Book 5."""

from __future__ import annotations

import json
import os
import re
import time
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

from seed_provider_config import normalize_provider_name, normalize_thinking_level
from seed_providers import ProviderResponse, get_adapter
from seed_router import classify_task, load_profiles_from_db, select_model

router = APIRouter(tags=["model-collab"])


class CollabRequest(BaseModel):
    task: str
    pattern: str = "chain"
    models: Dict[str, str] = Field(default_factory=dict)
    max_turns: int = Field(default=3, ge=1, le=12)
    context: Optional[str] = None
    thinking_level: str = "normal"
    max_tokens: int = Field(default=1200, ge=1, le=16000)


@router.post("/collab")
def collaborate(
    request: CollabRequest,
    db: Any = Depends(get_db),
    api_key: Any = Depends(get_api_key),
) -> Dict[str, Any]:
    """Run a headless collaboration pattern and store model turns."""
    start = time.perf_counter()
    pattern = (request.pattern or "chain").strip().lower()
    if pattern not in {"debate", "chain", "verify", "specialize"}:
        pattern = "chain"

    if pattern == "debate":
        result = _run_debate(request, db)
    elif pattern == "verify":
        result = _run_verify(request, db, api_key)
    elif pattern == "specialize":
        result = _run_specialize(request, db)
    else:
        result = _run_chain(request, db)

    turns = result.get("turns", [])
    result.setdefault("pattern", pattern)
    result["total_tokens"] = sum(int(turn.get("tokens_in", 0) or 0) + int(turn.get("tokens_out", 0) or 0) for turn in turns)
    result["total_latency_ms"] = sum(int(turn.get("latency_ms", 0) or 0) for turn in turns)
    result["wall_latency_ms"] = int((time.perf_counter() - start) * 1000)
    return result


def _run_chain(request: CollabRequest, db: Any) -> Dict[str, Any]:
    turns: List[Dict[str, Any]] = []
    providers = {
        "drafter": _provider_for(request, "drafter", "claude"),
        "reviewer": _provider_for(request, "reviewer", "gpt"),
    }
    for turn_no in range(1, request.max_turns + 1):
        role = "drafter" if turn_no % 2 == 1 else "reviewer"
        if role == "drafter" and turn_no == 1:
            instruction = "Draft the best answer to the task. Be explicit about assumptions and risks."
        elif role == "reviewer":
            instruction = "Review the draft. Identify defects, missing assumptions, unsafe advice, and concrete improvements. If it is sufficient, say 'no material issues'."
        else:
            instruction = "Revise the answer using the reviewer feedback. Return the improved final answer."
        turn = _call_turn(request, db, turns, role, providers[role], instruction, turn_no)
        turns.append(turn)
        if role == "reviewer" and _looks_converged(turn.get("text", "")):
            break
    final_answer = _last_role_text(turns, "drafter") or (turns[-1]["text"] if turns else "")
    return {"pattern": "chain", "turns": turns, "final_answer": final_answer}


def _run_debate(request: CollabRequest, db: Any) -> Dict[str, Any]:
    sequence = [
        ("proposer", _provider_for(request, "proposer", _provider_for(request, "a", "claude")), "Present the strongest position and recommendation for the task."),
        ("critic", _provider_for(request, "critic", _provider_for(request, "b", "gpt")), "Critique the proposer. Identify hidden risks, bad assumptions, and counterarguments."),
        ("judge", _provider_for(request, "judge", _provider_for(request, "c", "claude")), "Evaluate both positions, declare a winner, and give the final recommendation."),
    ]
    turns: List[Dict[str, Any]] = []
    for idx, (role, provider, instruction) in enumerate(sequence[: request.max_turns], start=1):
        turns.append(_call_turn(request, db, turns, role, provider, instruction, idx))
    return {"pattern": "debate", "turns": turns, "final_answer": turns[-1]["text"] if turns else ""}


def _run_verify(request: CollabRequest, db: Any, api_key: Any) -> Dict[str, Any]:
    turns: List[Dict[str, Any]] = []
    answerer = _provider_for(request, "answerer", _provider_for(request, "a", "claude"))
    verifier = _provider_for(request, "verifier", _provider_for(request, "b", "gpt"))

    answer_turn = _call_turn(
        request,
        db,
        turns,
        "answerer",
        answerer,
        "Answer the task directly. State assumptions and anything that should be checked against Seed wiki.",
        1,
    )
    turns.append(answer_turn)

    brain_context = _fetch_brain_context(request.task, api_key)
    verify_instruction = (
        "Fact-check the answer against the Seed wiki context below. Return one of: verified, conflicts, or uncertain. "
        "List any conflicts with wiki.\n\n"
        f"Seed wiki context:\n{json.dumps(brain_context, ensure_ascii=False)[:8000]}"
    )
    if request.max_turns >= 2:
        turns.append(_call_turn(request, db, turns, "verifier", verifier, verify_instruction, 2))

    verification_status = _verification_status(turns[-1].get("text", "") if len(turns) > 1 else "uncertain")
    final_answer = answer_turn.get("text", "")
    if len(turns) > 1:
        final_answer = (
            f"{answer_turn.get('text', '')}\n\nVerification status: {verification_status}.\n"
            f"Verifier notes:\n{turns[-1].get('text', '')}"
        ).strip()
    return {
        "pattern": "verify",
        "turns": turns,
        "final_answer": final_answer,
        "verification": {
            "status": verification_status,
            "brain_context_status": brain_context.get("status", "unknown"),
            "brain_items": brain_context.get("items", []),
        },
    }


def _run_specialize(request: CollabRequest, db: Any) -> Dict[str, Any]:
    profiles = load_profiles_from_db(db)
    subtasks = _split_subtasks(request.task)
    turns: List[Dict[str, Any]] = []

    for idx, subtask in enumerate(subtasks[: max(1, request.max_turns - 1)], start=1):
        classification = classify_task(subtask)
        selection = select_model(classification, profiles)
        provider = request.models.get(classification.domain) or request.models.get("specialist") or selection.provider
        instruction = (
            f"You are the specialist for domain '{classification.domain}'. Solve only this subtask, then state any constraints.\n\n"
            f"Subtask: {subtask}"
        )
        turns.append(_call_turn(request, db, turns, f"specialist:{classification.domain}", provider, instruction, idx))

    if not turns:
        return {"pattern": "specialize", "turns": [], "final_answer": ""}

    if len(turns) == 1 or len(turns) >= request.max_turns:
        final_answer = "\n\n".join(turn.get("text", "") for turn in turns)
        return {"pattern": "specialize", "turns": turns, "final_answer": final_answer}

    merger_provider = _provider_for(request, "merger", "claude")
    merge_instruction = (
        "Merge the specialist outputs into one coherent final answer. Preserve disagreements and uncertainty. "
        "Do not invent additional facts."
    )
    turns.append(_call_turn(request, db, turns, "merger", merger_provider, merge_instruction, len(turns) + 1))
    return {"pattern": "specialize", "turns": turns, "final_answer": turns[-1].get("text", "")}


def _call_turn(
    request: CollabRequest,
    db: Any,
    turns: List[Dict[str, Any]],
    role: str,
    provider_name: str,
    instruction: str,
    turn_no: int,
) -> Dict[str, Any]:
    prompt = _conversation_prompt(request.task, request.context or "", turns, instruction)
    provider_key = normalize_provider_name(provider_name)
    try:
        adapter = get_adapter(provider_name)
        response = adapter.call(
            prompt,
            system=COLLAB_SYSTEM,
            thinking_level=normalize_thinking_level(request.thinking_level),
            max_tokens=request.max_tokens,
        )
    except Exception as exc:  # Defensive; adapters should catch provider errors.
        response = ProviderResponse(
            model="unknown",
            provider=provider_key,
            text="",
            thinking=None,
            tokens_in=0,
            tokens_out=0,
            latency_ms=0,
            raw={"exception": f"{exc.__class__.__name__}: {exc}"},
            error=f"adapter_failure: {exc.__class__.__name__}: {exc}",
        )

    turn = {
        "role": role,
        "provider": response.provider,
        "model": response.model,
        "text": response.text,
        "turn": turn_no,
        "tokens_in": response.tokens_in,
        "tokens_out": response.tokens_out,
        "latency_ms": response.latency_ms,
        "error": response.error,
    }
    _store_turn(db, request, role, turn_no, prompt, response)
    return turn


def _conversation_prompt(task: str, context: str, turns: List[Dict[str, Any]], instruction: str) -> str:
    prior = []
    for turn in turns:
        prior.append(
            f"Turn {turn.get('turn')} — {turn.get('role')} ({turn.get('provider')}):\n{turn.get('text', '')}"
        )
    prior_text = "\n\n".join(prior) if prior else "(none)"
    return (
        f"Task:\n{task}\n\n"
        f"Additional context:\n{context or '(none)'}\n\n"
        f"Prior turns:\n{prior_text}\n\n"
        f"Your instruction:\n{instruction}"
    )


def _store_turn(db: Any, request: CollabRequest, role: str, turn_no: int, prompt: str, response: ProviderResponse) -> bool:
    if db is None or text is None:
        return False
    try:
        db.execute(
            text(
                """
                INSERT INTO seed_collab_turns
                    (task, pattern, role, provider, model, turn_number, prompt, response_text,
                     tokens_in, tokens_out, latency_ms, error, raw)
                VALUES
                    (:task, :pattern, :role, :provider, :model, :turn_number, :prompt, :response_text,
                     :tokens_in, :tokens_out, :latency_ms, :error, CAST(:raw AS jsonb))
                """
            ),
            {
                "task": request.task,
                "pattern": request.pattern,
                "role": role,
                "provider": response.provider,
                "model": response.model,
                "turn_number": turn_no,
                "prompt": prompt,
                "response_text": response.text,
                "tokens_in": response.tokens_in,
                "tokens_out": response.tokens_out,
                "latency_ms": response.latency_ms,
                "error": response.error,
                "raw": json.dumps(response.raw or {}),
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


def _fetch_brain_context(task: str, api_key: Any = None) -> Dict[str, Any]:
    """Call Seed's /api/brain endpoint when an internal base URL is configured."""
    base_url = os.getenv("SEED_INTERNAL_API_BASE_URL") or os.getenv("SEED_API_BASE_URL")
    if not base_url:
        return {"status": "unavailable", "reason": "SEED_INTERNAL_API_BASE_URL not set", "items": []}

    try:
        import httpx  # type: ignore
    except Exception as exc:  # pragma: no cover
        return {"status": "unavailable", "reason": f"httpx unavailable: {exc}", "items": []}

    root = base_url.rstrip("/")
    if not root.endswith("/api"):
        root = f"{root}/api"
    url = f"{root}/brain"
    headers: Dict[str, str] = {}
    internal_key = os.getenv("SEED_INTERNAL_API_KEY")
    if internal_key:
        headers["Authorization"] = f"Bearer {internal_key}"

    attempts = [
        ("post", {"json": {"query": task, "limit": 5}}),
        ("get", {"params": {"q": task, "limit": 5}}),
        ("get", {"params": {"query": task, "limit": 5}}),
    ]
    last_error = ""
    for method, kwargs in attempts:
        try:
            response = httpx.request(method, url, headers=headers, timeout=5, **kwargs)
            if response.status_code < 400:
                data = response.json()
                return {"status": "ok", "items": _normalize_brain_items(data), "raw": data}
            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
        except Exception as exc:
            last_error = f"{exc.__class__.__name__}: {exc}"
    return {"status": "error", "reason": last_error, "items": []}


def _normalize_brain_items(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, dict):
        candidates = data.get("items") or data.get("results") or data.get("nodes") or data.get("data") or []
    elif isinstance(data, list):
        candidates = data
    else:
        candidates = []
    items: List[Dict[str, Any]] = []
    for item in candidates[:5]:
        if isinstance(item, dict):
            items.append({key: item.get(key) for key in ["id", "title", "summary", "text", "content", "score"] if key in item})
        else:
            items.append({"text": str(item)})
    return items


def _split_subtasks(task: str) -> List[str]:
    lines = [line.strip(" -\t") for line in (task or "").splitlines() if line.strip(" -\t")]
    if len(lines) > 1:
        return lines
    pieces = [piece.strip() for piece in re.split(r";|\bthen\b|\band\b(?=\s+(?:check|review|compare|design|verify|build|analyze))", task or "") if piece.strip()]
    return pieces or [task]


def _provider_for(request: CollabRequest, role: str, default: str) -> str:
    return request.models.get(role) or default


def _last_role_text(turns: List[Dict[str, Any]], role: str) -> str:
    for turn in reversed(turns):
        if turn.get("role") == role and turn.get("text"):
            return turn["text"]
    return ""


def _looks_converged(text_value: str) -> bool:
    value = (text_value or "").lower()
    return any(marker in value for marker in ["no material issues", "looks good", "converged", "sufficient as written"])


def _verification_status(text_value: str) -> str:
    value = (text_value or "").lower()
    if "conflict" in value and "no conflict" not in value:
        return "conflicts"
    if "verified" in value or "no conflict" in value:
        return "verified"
    return "uncertain"


COLLAB_SYSTEM = (
    "You are participating in a headless Seed multi-model collaboration. "
    "Be direct, preserve uncertainty, and do not claim external actions were performed unless the prompt provides evidence."
)
