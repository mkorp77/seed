"""
Write a session summary to the vault's seed/raw/ folder.
Called at end of session or before compaction.

Usage: python session-summary.py "Brief description of what happened"
"""
import sys
import os
from datetime import datetime

VAULT_PATH = os.environ.get("SEED_VAULT_PATH", r"D:\Seed\vault")
DOMAIN = "seed"
STAGE = "raw"

def write_summary(description: str):
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    slug = description[:50].lower()
    slug = "".join(c if c.isalnum() else "-" for c in slug)
    slug = slug.strip("-")

    filename = f"{date_str}-session-{slug}.md"
    folder = os.path.join(VAULT_PATH, DOMAIN, STAGE)
    os.makedirs(folder, exist_ok=True)
    filepath = os.path.join(folder, filename)

    # Handle collision
    if os.path.exists(filepath):
        base, ext = os.path.splitext(filepath)
        n = 2
        while os.path.exists(f"{base}-{n}{ext}"):
            n += 1
        filepath = f"{base}-{n}{ext}"

    content = f"""---
type: session_summary
captured_at: {now.isoformat()}
domain: {DOMAIN}
source: claude_code_session
---

# Session Summary — {date_str} {time_str}

{description}
"""

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"Summary written to: {filepath}")
    return filepath

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python session-summary.py \"description\"")
        sys.exit(1)
    write_summary(sys.argv[1])
