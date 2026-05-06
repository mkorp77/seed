# Seed — Session Context

## What this project is
Seed is a sovereign knowledge pipeline. Backend API at api.seed.wiki. PostgreSQL + Qdrant + Playwright scraper on this machine (SOURCE).

## Directory structure
- backend/ — FastAPI API (Windows service SeedBackend, port 8000)
- scraper/ — Playwright + Discourse JSON API (Docker seed-scraper, port 3000)
- vault/ — Obsidian vault, git-versioned (6 domains × 3 stages)
- docker-compose.yml — seed-net: postgres + qdrant + scraper

## Services
- SeedBackend: `nssm restart SeedBackend`
- Containers: `cd D:\Seed && docker compose up -d`
- Backend reaches scraper at 127.0.0.1:3000 (NOT Docker DNS)

## Rules
- Don't modify .env (contains database password)
- Don't run destructive database commands without verifying project count first
- Don't install software without asking Mike
- Don't touch anything outside D:\Seed\ without permission
- Stop after each step and confirm before proceeding

## Vault domains
trading, hardware, seed, models, anthropic-ops, fqhc
Each has raw/, active/, wiki/ subdirectories.

## Before any database operation
Run: `SELECT count(*) FROM seed_projects;`
If the result doesn't match expected count, STOP and report.
