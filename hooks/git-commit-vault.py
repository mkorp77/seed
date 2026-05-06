"""
Git add and commit changes in the vault.
Called after session summaries are written.

Usage: python git-commit-vault.py "commit message"
"""
import sys
import os
import subprocess

VAULT_PATH = os.environ.get("SEED_VAULT_PATH", r"D:\Seed\vault")

def commit(message: str):
    # vault/ is absorbed into the outer D:\Seed\ repo, so run git from the repo root
    # and scope all operations to vault/ paths only.
    os.chdir(os.path.dirname(VAULT_PATH))

    # Check for changes scoped to vault/
    result = subprocess.run(["git", "status", "--porcelain", "vault/"], capture_output=True, text=True)
    if not result.stdout.strip():
        print("No vault changes to commit")
        return

    subprocess.run(["git", "add", "vault/"], check=True)
    subprocess.run(["git", "commit", "-m", message, "--", "vault/"], check=True)
    print(f"Committed: {message}")

if __name__ == "__main__":
    msg = sys.argv[1] if len(sys.argv) > 1 else f"Session update"
    commit(msg)
