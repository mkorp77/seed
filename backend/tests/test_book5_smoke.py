from __future__ import annotations

from datetime import datetime, timedelta, timezone

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from seed_probes import CapabilityProfile, run_probe
from seed_probe_bank import PROBE_BANK
from seed_providers import ProviderResponse


def _profile(provider: str, domain: str, score: float, passed: int, total: int) -> CapabilityProfile:
    now = datetime.now(timezone.utc)
    return CapabilityProfile(
        provider=provider,
        model=f"{provider}-model",
        thinking_level="normal",
        domain_scores={
            domain: {"score": score, "passed": passed, "total": total, "points": passed, "max_points": total},
            "all": {"score": score, "passed": passed, "total": total, "points": passed, "max_points": total},
        },
        total_probes=total,
        total_passed=passed,
        tested_at=now,
        stale_after=now + timedelta(days=3),
        raw_results=[],
    )


class FakeAdapter:
    def __init__(self, provider: str = "claude", model: str | None = None) -> None:
        self.name = provider.split(":", 1)[0]
        self.model = model or f"{self.name}-model"
        self.timeout_seconds = 60

    def call(self, prompt: str, system: str = "", thinking_level: str = "normal", max_tokens: int = 1000) -> ProviderResponse:
        if "Review the draft" in prompt:
            text = "No material issues. The draft is sufficient as written."
        elif "pg_dump" in prompt or "backup" in prompt:
            text = "Use pg_dump -Fc custom format and restore with pg_restore."
        else:
            text = "Draft answer from fake adapter."
        return ProviderResponse(
            model=self.model,
            provider=self.name,
            text=text,
            thinking=None,
            tokens_in=10,
            tokens_out=12,
            latency_ms=3,
            raw={},
            error=None,
        )


def _client(module, route_router) -> TestClient:
    app = FastAPI()
    app.dependency_overrides[module.get_db] = lambda: None
    app.dependency_overrides[module.get_api_key] = lambda: {"id": "smoke"}
    app.include_router(route_router, prefix="/api")
    return TestClient(app)


def test_book5_routes_are_relative() -> None:
    import seed_collab
    import seed_compare
    import seed_router

    paths = [route.path for route in seed_router.router.routes + seed_compare.router.routes + seed_collab.router.routes]
    assert "/route" in paths
    assert "/route/exec" in paths
    assert "/compare" in paths
    assert "/collab" in paths
    assert not any(path.startswith("/api/") for path in paths)


def test_route_recommendation_smoke(monkeypatch) -> None:
    import seed_router

    monkeypatch.setattr(
        seed_router,
        "load_profiles_from_db",
        lambda db: [
            _profile("claude", "infrastructure", 1.0, 5, 5),
            _profile("gpt", "infrastructure", 0.6, 3, 5),
        ],
    )
    client = _client(seed_router, seed_router.router)
    response = client.post(
        "/api/route",
        json={"task": "Check if the Docker compose postgres volume will survive a reboot on WSL2"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["task_domain"] == "infrastructure"
    assert data["selected_provider"] == "claude"
    assert data["thinking_level"] == "normal"


def test_route_exec_smoke(monkeypatch) -> None:
    import seed_router

    monkeypatch.setattr(seed_router, "load_profiles_from_db", lambda db: [_profile("claude", "infrastructure", 1.0, 5, 5)])
    monkeypatch.setattr(seed_router, "get_adapter", lambda provider, model=None: FakeAdapter(provider, model))
    client = _client(seed_router, seed_router.router)
    response = client.post(
        "/api/route/exec",
        json={"task": "Should I use pg_dump -Fc or plain SQL for this backup?"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["response"]["provider"] == "claude"
    assert "pg_dump -Fc" in data["response"]["text"]


def test_compare_smoke(monkeypatch) -> None:
    import seed_compare

    monkeypatch.setattr(seed_compare, "get_adapter", lambda provider: FakeAdapter(provider))
    client = _client(seed_compare, seed_compare.router)
    response = client.post(
        "/api/compare",
        json={
            "prompt": "Should I use pg_dump -Fc or plain SQL for this backup?",
            "system": "You are advising on PostgreSQL backup strategy.",
            "models": ["claude", "gpt"],
            "thinking_level": "normal",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["responses"]) == 2
    assert data["consensus"] is True
    assert data["disagreements"] == []


def test_collab_chain_smoke(monkeypatch) -> None:
    import seed_collab

    monkeypatch.setattr(seed_collab, "get_adapter", lambda provider: FakeAdapter(provider))
    client = _client(seed_collab, seed_collab.router)
    response = client.post(
        "/api/collab",
        json={
            "task": "Review this trading strategy for logical flaws and backtest validity",
            "pattern": "chain",
            "models": {"drafter": "claude", "reviewer": "gpt"},
            "max_turns": 3,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["pattern"] == "chain"
    assert len(data["turns"]) == 2
    assert data["turns"][0]["role"] == "drafter"
    assert data["final_answer"]


def test_probe_runner_smoke() -> None:
    probe = next(probe for probe in PROBE_BANK if probe.id == "infra_pg_dump_custom_vs_plain")
    adapter = FakeAdapter("claude")
    result = run_probe(adapter, probe)
    assert result.passed is True
    assert result.score == result.max_score
