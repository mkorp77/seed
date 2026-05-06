"""
PreToolUse hook: surface destructive bash commands for explicit confirmation.

Reads the Claude Code PreToolUse JSON event from stdin. If the bash command
matches a destructive pattern (DROP/DELETE/rm -rf/docker compose down -v/etc.),
emits a JSON response asking Claude Code to gate the call on user permission.

This does NOT yet do the project-count check requested in CODE-BOOK3-HOOKS.md
Step 6 — that requires an expected_count value to be defined first.
"""
import sys
import json
import re

# Patterns that should not run silently. Each entry: (regex, human label).
DESTRUCTIVE_PATTERNS = [
    (r"\bDROP\s+(TABLE|DATABASE|SCHEMA)\b", "SQL DROP statement"),
    (r"\bDELETE\s+FROM\b", "SQL DELETE statement"),
    (r"\bTRUNCATE\b", "SQL TRUNCATE"),
    (r"\brm\s+-rf?\b", "rm -rf"),
    (r"docker\s+compose\s+down\b[^&|;]*-v\b", "docker compose down -v (deletes volumes)"),
    (r"\balembic\s+\S+\s+downgrade\b", "alembic downgrade"),
    (r"\bmigrate\b[^&|;]*\b(reset|drop)\b", "destructive migration"),
]

def main():
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        # If stdin isn't valid JSON, don't block — allow the tool through.
        sys.exit(0)

    if event.get("tool_name") != "Bash":
        sys.exit(0)

    command = event.get("tool_input", {}).get("command", "") or ""

    for pattern, label in DESTRUCTIVE_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            output = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": (
                        f"Destructive pattern detected: {label}. "
                        "Per CLAUDE.md, run `SELECT count(*) FROM seed_projects;` "
                        "and verify the count before proceeding."
                    ),
                }
            }
            print(json.dumps(output))
            sys.exit(0)

    sys.exit(0)

if __name__ == "__main__":
    main()
