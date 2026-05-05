from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from urllib import error, request


BASE_URL = os.getenv("SEED_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def fail(message: str) -> None:
    print(f"ERROR: {message}")
    sys.exit(1)



def api_request(method: str, path: str, payload: dict | None = None) -> tuple[int, dict | list]:
    url = f"{BASE_URL}{path}"
    body = None
    headers = {"Accept": "application/json"}

    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = request.Request(url=url, data=body, headers=headers, method=method)

    try:
        with request.urlopen(req) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
            return resp.status, data
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        detail = raw or exc.reason
        fail(f"{method} {path} failed with {exc.code}: {detail}")
    except error.URLError as exc:
        fail(f"{method} {path} connection failed: {exc.reason}")

    fail(f"{method} {path} failed unexpectedly")



def expect(condition: bool, message: str) -> None:
    if not condition:
        fail(message)



def main() -> None:
    slug = f"seed-smoke-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()

    print(f"Base URL: {BASE_URL}")
    print("1) POST /api/projects")
    status_code, project = api_request(
        "POST",
        "/api/projects",
        {
            "slug": slug,
            "name": "Seed Smoke Test Project",
            "description": "Created by smoke_test.py",
            "status": "active",
        },
    )
    expect(status_code == 201, f"Expected 201 for create project, got {status_code}")
    project_id = project["id"]
    expect(project["slug"] == slug, "Project slug mismatch")

    print("2) POST /api/contexts")
    context_payload = {
        "project_id": project_id,
        "source_kind": "web",
        "source_uri": "https://forums.developer.nvidia.com/t/dgx-spark-edge-inference/seed-smoke",
        "source_title": "Seed Smoke Test Capture",
        "source_span_start": 0,
        "source_span_end": 42,
        "selected_text": "DGX Spark can act as an edge inference option.",
        "content_hash": "smoke-test-content-hash-001",
        "captured_at": now,
        "source_external": {
            "platform": "chrome_extension",
            "capture_mode": "stack",
        },
        "tags": [],
        "user_note": "",
        "destination": [],
    }
    status_code, context = api_request("POST", "/api/contexts", context_payload)
    expect(status_code == 201, f"Expected 201 for create context, got {status_code}")
    context_id = context["id"]
    expect(context["project_id"] == project_id, "Context project_id mismatch")
    expect(context["metadata"]["tags"] == [], "Expected empty tags on create")
    expect(context["feedback"] == [], "Expected empty feedback on create")

    print("3) GET /api/contexts/{id}")
    status_code, fetched_context = api_request("GET", f"/api/contexts/{context_id}")
    expect(status_code == 200, f"Expected 200 for get context, got {status_code}")
    expect(fetched_context["id"] == context_id, "Fetched context id mismatch")

    print("4) GET /api/contexts?project_id=X")
    status_code, context_list = api_request("GET", f"/api/contexts?project_id={project_id}")
    expect(status_code == 200, f"Expected 200 for list contexts, got {status_code}")
    expect(context_list["total"] >= 1, "Expected at least one context in list")
    expect(any(item["id"] == context_id for item in context_list["items"]), "Created context missing from list")

    print("5) PATCH /api/contexts/{id}/metadata")
    status_code, updated_context = api_request(
        "PATCH",
        f"/api/contexts/{context_id}/metadata",
        {
            "tags": ["gb10", "seed", "research"],
            "user_note": "Edge inference note captured from NVIDIA forum research.",
            "destination": ["wiki", "rag"],
        },
    )
    expect(status_code == 200, f"Expected 200 for metadata update, got {status_code}")
    expect(set(updated_context["metadata"]["tags"]) == {"gb10", "seed", "research"}, "Tags update failed")
    expect(set(updated_context["metadata"]["destination"]) == {"wiki", "rag"}, "Destination update failed")

    print("6) POST /api/contexts/{id}/feedback")
    status_code, feedback = api_request(
        "POST",
        f"/api/contexts/{context_id}/feedback",
        {
            "model_name": "gpt",
            "model_version": "5.4",
            "response_text": "This capture supports the DGX Spark edge-inference research thread.",
            "response_ref": "smoke-feedback-001",
            "source_model_thread_ref": "local-smoke-thread",
            "source_model_message_ref": "local-smoke-message",
        },
    )
    expect(status_code == 201, f"Expected 201 for append feedback, got {status_code}")
    expect(feedback["context_id"] == context_id, "Feedback context_id mismatch")

    print("7) GET /api/projects")
    status_code, projects = api_request("GET", "/api/projects")
    expect(status_code == 200, f"Expected 200 for list projects, got {status_code}")
    expect(any(item["id"] == project_id for item in projects), "Created project missing from list")

    print("8) GET /api/contexts/{id} (verify feedback persisted)")
    status_code, final_context = api_request("GET", f"/api/contexts/{context_id}")
    expect(status_code == 200, f"Expected 200 for final get context, got {status_code}")
    expect(len(final_context["feedback"]) == 1, "Expected exactly one feedback record")
    expect(final_context["feedback"][0]["model_version"] == "5.4", "Feedback model_version mismatch")

    print("9) POST /api/contexts again (dedup check)")
    status_code, reused_context = api_request("POST", "/api/contexts", context_payload)
    expect(status_code == 200, f"Expected 200 for dedup reuse, got {status_code}")
    expect(reused_context["id"] == context_id, "Dedup reuse returned a different context")

    print("Smoke test passed.")
    print(f"Project ID: {project_id}")
    print(f"Context ID: {context_id}")


if __name__ == "__main__":
    main()
