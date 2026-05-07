"""Provider configuration for Seed Book 5 multi-model orchestration.

This module is intentionally side-effect free: it reads environment variables only
when helper functions are called. Provider SDK imports happen in seed_providers.py
inside each adapter, so missing optional SDKs do not break process startup.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

PROVIDER_CONFIG: Dict[str, Dict[str, Any]] = {
    "claude": {
        "sdk": "anthropic",
        "key_env": "ANTHROPIC_API_KEY",
        "default_model": "claude-sonnet-4-20250514",
    },
    "gpt": {
        "sdk": "openai",
        "key_env": "OPENAI_API_KEY",
        "default_model": "gpt-4o",
    },
    "deepseek": {
        "sdk": "httpx",
        "key_env": "DEEPSEEK_API_KEY",
        "default_model": "deepseek-chat",
        "base_url": "https://api.deepseek.com",
    },
    "xai": {
        "sdk": "httpx",
        "key_env": "XAI_API_KEY",
        "default_model": "grok-4",
        "base_url": "https://api.x.ai/v1",
    },
    "gemini": {
        "sdk": "google-generativeai",
        "key_env": "GOOGLE_AI_API_KEY",
        "default_model": "gemini-2.0-flash",
    },
    "local": {
        "sdk": "httpx",
        "key_env": None,
        "base_url_env": "SEED_LOCAL_ENDPOINT",
        "default_model": "qwen",
    },
}

PROVIDER_ALIASES: Dict[str, str] = {
    "anthropic": "claude",
    "claude": "claude",
    "openai": "gpt",
    "gpt": "gpt",
    "chatgpt": "gpt",
    "deepseek": "deepseek",
    "xai": "xai",
    "grok": "xai",
    "google": "gemini",
    "gemini": "gemini",
    "local": "local",
    "gb10": "local",
}

THINKING_LEVELS = {"low", "normal", "medium", "high"}
REASONING_EFFORT_BY_LEVEL = {
    "low": "low",
    "normal": "medium",
    "medium": "medium",
    "high": "high",
}

# Conservative budgets. Provider adapters clamp these where providers require a
# budget smaller than max output tokens.
THINKING_BUDGET_BY_LEVEL = {
    "low": 512,
    "normal": 1024,
    "medium": 1024,
    "high": 4096,
}

PROVIDER_ORDER = ["claude", "gpt", "deepseek", "xai", "gemini", "local"]


def normalize_provider_name(provider: str) -> str:
    """Return Seed's canonical provider key for aliases such as openai/gpt."""
    key = (provider or "").strip().lower()
    if ":" in key:
        key = key.split(":", 1)[0]
    return PROVIDER_ALIASES.get(key, key)


def normalize_thinking_level(level: Optional[str]) -> str:
    value = (level or "normal").strip().lower()
    if value == "medium":
        return "normal"
    if value not in THINKING_LEVELS:
        return "normal"
    return value


def get_provider_config(provider: str) -> Dict[str, Any]:
    name = normalize_provider_name(provider)
    if name not in PROVIDER_CONFIG:
        raise KeyError(f"Unknown provider: {provider}")
    return PROVIDER_CONFIG[name]


def get_default_model(provider: str) -> str:
    return str(get_provider_config(provider)["default_model"])


def get_api_key(provider: str) -> Optional[str]:
    config = get_provider_config(provider)
    key_env = config.get("key_env")
    if not key_env:
        return None
    return os.getenv(str(key_env))


def get_base_url(provider: str) -> Optional[str]:
    config = get_provider_config(provider)
    base_url_env = config.get("base_url_env")
    if base_url_env:
        return os.getenv(str(base_url_env)) or config.get("base_url")
    return config.get("base_url")


def is_provider_configured(provider: str) -> bool:
    """Return True when required runtime configuration is present.

    Local provider is considered configured only when SEED_LOCAL_ENDPOINT is set;
    this prevents accidental calls to an unrelated localhost service when callers
    request all configured providers.
    """
    name = normalize_provider_name(provider)
    config = get_provider_config(name)
    key_env = config.get("key_env")
    if key_env and not os.getenv(str(key_env)):
        return False
    if name == "local" and config.get("base_url_env") and not os.getenv(str(config["base_url_env"])):
        return False
    return True


def configured_provider_names(include_unconfigured: bool = False) -> List[str]:
    if include_unconfigured:
        return list(PROVIDER_ORDER)
    return [name for name in PROVIDER_ORDER if is_provider_configured(name)]
