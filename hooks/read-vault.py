"""
Read wiki entries from a domain and print them as context.
Used to inject vault knowledge at session start.

Usage: python read-vault.py [domain]
       python read-vault.py trading
       python read-vault.py all
"""
import sys
import os
import glob

VAULT_PATH = os.environ.get("SEED_VAULT_PATH", r"D:\Seed\vault")
DOMAINS = ["trading", "hardware", "seed", "models", "anthropic-ops", "fqhc"]

def read_wiki(domain: str) -> str:
    """Read all wiki entries for a domain."""
    wiki_path = os.path.join(VAULT_PATH, domain, "wiki", "*.md")
    files = sorted(glob.glob(wiki_path))
    if not files:
        return f"No wiki entries for domain: {domain}"

    output = [f"## {domain} wiki ({len(files)} entries)\n"]
    for f in files:
        with open(f, "r", encoding="utf-8") as fh:
            content = fh.read()
        name = os.path.basename(f)
        output.append(f"### {name}\n{content}\n---\n")
    return "\n".join(output)

def read_raw_recent(domain: str, limit: int = 5) -> str:
    """Read most recent raw captures for a domain."""
    raw_path = os.path.join(VAULT_PATH, domain, "raw", "*.md")
    files = sorted(glob.glob(raw_path), reverse=True)[:limit]
    if not files:
        return f"No raw captures for domain: {domain}"

    output = [f"## {domain} recent captures ({len(files)} shown)\n"]
    for f in files:
        with open(f, "r", encoding="utf-8") as fh:
            # Read just the frontmatter + first 500 chars
            content = fh.read()
            preview = content[:500] + "..." if len(content) > 500 else content
        name = os.path.basename(f)
        output.append(f"### {name}\n{preview}\n---\n")
    return "\n".join(output)

if __name__ == "__main__":
    domain = sys.argv[1] if len(sys.argv) > 1 else "all"

    if domain == "all":
        for d in DOMAINS:
            wiki = read_wiki(d)
            if "No wiki entries" not in wiki:
                print(wiki)
    else:
        print(read_wiki(domain))
        print(read_raw_recent(domain))
