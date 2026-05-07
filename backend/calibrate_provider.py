"""Run the Book 5 probe bank against one provider and persist a capability profile.

Usage:
    python calibrate_provider.py <provider> [--thinking=normal] [--domain=all]

Examples:
    python calibrate_provider.py deepseek
    python calibrate_provider.py xai --thinking=high
    python calibrate_provider.py claude --domain=infrastructure

The provider must already have its API key set in the SeedBackend service env
(or the current shell env). Failed/missing probes don't abort the run; the
profile records what happened. The result is INSERTed into
seed_capability_profiles with a 3-day stale_after window.
"""
from __future__ import annotations

import argparse
import sys

from seed_db_v2 import build_engine, build_session_factory
from seed_probes import build_profile, save_profile
from seed_provider_config import is_provider_configured, normalize_provider_name
from seed_providers import get_adapter


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("provider", help="Provider name (claude, gpt, deepseek, xai, gemini, local)")
    ap.add_argument("--thinking", default="normal", choices=["low", "normal", "medium", "high"])
    ap.add_argument("--domain", default="all", help="Probe domain filter or 'all'")
    args = ap.parse_args()

    canonical = normalize_provider_name(args.provider)
    if not is_provider_configured(canonical):
        print(f"ERROR: provider {args.provider!r} (canonical: {canonical!r}) is not configured.")
        print("Check that the relevant API key env var is set in this process.")
        return 2

    print(f"Calibrating: provider={canonical} thinking={args.thinking} domain={args.domain}")
    adapter = get_adapter(canonical)
    print(f"Adapter: {type(adapter).__name__}  default_model={adapter.model}")

    print("Running probe bank... (this calls the real provider API)")
    profile = build_profile(adapter, domain=args.domain, thinking_level=args.thinking)

    print()
    print(f"Total probes: {profile.total_probes}")
    print(f"Total passed: {profile.total_passed}")
    print(f"Overall pass rate: {profile.total_passed}/{profile.total_probes} = "
          f"{(profile.total_passed / max(profile.total_probes, 1)) * 100:.1f}%")
    print()
    print("Per-domain scores:")
    for domain, scores in sorted(profile.domain_scores.items()):
        print(f"  {domain:24s}  {scores.get('passed', 0)}/{scores.get('total', 0)}  "
              f"score={scores.get('score', 0):.2f}")
    print()

    engine = build_engine()
    session_factory = build_session_factory(engine)
    with session_factory() as db:
        ok = save_profile(db, profile)
        if ok:
            db.commit()
            print(f"Profile saved to seed_capability_profiles (stale_after={profile.stale_after.isoformat()})")
        else:
            print("WARNING: save_profile returned False (DB or text() unavailable)")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
