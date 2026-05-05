from __future__ import annotations

import os
import sys
import uuid

import requests


API_BASE = os.getenv("SEED_API_BASE", "https://api.seed.wiki").rstrip("/")


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"{status} {name}{': ' + detail if detail else ''}")
    if not ok:
        raise SystemExit(1)


def main() -> None:
    # 1. Detect hardware route.
    r = requests.get(
        f"{API_BASE}/api/domains/detect",
        params={"tags": "hardware,nvidia,cluster"},
        timeout=15,
    )
    check("domain detect status", r.status_code == 200, str(r.status_code))
    data = r.json()
    check("domain detect hardware", data.get("domain") == "hardware", str(data))
    check("domain detect confidence", data.get("confidence") == "high", str(data))

    # 2. Detect default route.
    r = requests.get(
        f"{API_BASE}/api/domains/detect",
        params={"tags": "unknown-tag"},
        timeout=15,
    )
    check("domain default status", r.status_code == 200, str(r.status_code))
    data = r.json()
    check("domain default seed", data.get("domain") == "seed", str(data))
    check("domain default confidence low", data.get("confidence") == "low", str(data))

    # 3. Publish 404 guard.
    bogus_id = str(uuid.uuid4())
    r = requests.post(f"{API_BASE}/api/contexts/{bogus_id}/publish", json={}, timeout=15)
    check("publish missing context returns 404", r.status_code == 404, str(r.status_code))

    print("Book 2 smoke route checks passed.")
    print("Manual publish test still requires a real context id with vault directories present.")


if __name__ == "__main__":
    main()
