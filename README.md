# Seed Context Tagger

Your AI conversations are dying. Your context is scattered across platforms that delete it, summarize it wrong, or lose it when they update their models. This tool puts you back in control.

**Drag in your files. Highlight what matters. Tag the connections. Export for your database.**

No account. No telemetry. No AI deciding what's relevant about you. You are the only write authority.

## What It Does

- **Drag and drop** markdown, text, JSON, HTML, CSV, PDF, DOCX files
- **Auto-parses** Claude and ChatGPT conversation exports into readable text
- **Edit** documents before tagging — delete the junk, keep the gold
- **Highlight and tag** — your taxonomy, your connections, your notes
- **Create tags from highlights** — selected text becomes a new tag
- **Auto-scan** — finds your taxonomy terms in each document on load
- **Cross-file search** — highlight text, find it across all loaded files
- **Source tracking** — every annotation carries where it came from
- **Route to destinations** — tag where each annotation goes (wiki, database, todo, email, etc.)
- **Export** — JSON ready for Qdrant, PostgreSQL, or any vector database
- **Save to your drive** — File System Access API writes directly to a folder you choose

## Quick Start

1. Download `seed-context-tagger-public.html` (or `local` for offline use)
2. Double-click it. Opens in Chrome or Edge.
3. Drop your files in.
4. Start tagging.

No install. No build step. No server. No internet required (local version).

## Two Versions

| Version | File | Internet | PDF/DOCX | Use Case |
|---------|------|----------|----------|----------|
| **Public** | `seed-context-tagger-public.html` | First load only (CDN cache) | Yes | General use, ships in repo |
| **Local** | `seed-context-tagger-local.html` | Never | No | Air-gapped, sovereign, HIPAA |

## Who This Is For

Anyone who built deep context with AI models and lost it. Anyone with hundreds of conversation exports sitting on their drive with no way to organize them. Anyone who wants their AI knowledge base to be theirs — not a platform's summary of what it thinks matters about you.

## The Problem This Solves

Every AI platform stores your conversations, summarizes them into shallow memory profiles, and feeds those summaries back to new model instances that misinterpret them. You don't control what's remembered. You can't correct what's wrong. When the platform changes models or has an outage, your context dies.

This tool is the antidote: **you decide what's worth remembering, how it connects, and where it goes.**

## Part of Seed

The Context Tagger is Phase 0 of [Seed](./docs/seed-v01-spec.md) — an open-source sovereign AI workbench. Multi-model chat, air-gapped compute containers, wiki knowledge compounding, and bring-your-own credentials. The models think. The containers compute. The wiki remembers. The human decides.

## Export Format

Annotations export as JSON ready for vector databases:

```json
{
  "id": "unique-id",
  "payload": {
    "source_file": "conversation.claude.md",
    "text": "The session-conditional sigma flaw would have killed the BVC study",
    "tags": ["gemini", "BVC", "methodology"],
    "destinations": ["wiki", "rag"],
    "note": "Gemini caught this in ABCs session 3. Connected to lab pipe step 4.",
    "source_url": "https://claude.ai/chat/abc-123",
    "char_start": 4521,
    "char_end": 4587,
    "created_at": "2026-04-13T04:30:00.000Z"
  }
}
```

## License

MIT. No telemetry. No analytics. No phone-home. Your data stays on your machine.

## Contributing

This project exists because no one else built it. If you've been through the same context destruction and want to help, open an issue or PR. The [handoff document](./docs/HANDOFF-2026-04-13.md) has the full design context.
