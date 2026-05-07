"""Provider adapters for Seed Book 5.

Adapters are deliberately thin wrappers. They preserve provider-specific features
while normalizing the response shape for the router/compare/collab layers.
Every adapter returns ProviderResponse on errors; provider SDK exceptions are not
raised to callers.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from seed_provider_config import (
    PROVIDER_CONFIG,
    REASONING_EFFORT_BY_LEVEL,
    THINKING_BUDGET_BY_LEVEL,
    configured_provider_names,
    get_api_key,
    get_base_url,
    get_default_model,
    normalize_provider_name,
    normalize_thinking_level,
)


@dataclass
class ProviderResponse:
    model: str
    provider: str
    text: str
    thinking: Optional[str]
    tokens_in: int
    tokens_out: int
    latency_ms: int
    raw: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self, include_raw: bool = False) -> Dict[str, Any]:
        data = asdict(self)
        if not include_raw:
            data.pop("raw", None)
        return data


class ProviderAdapter:
    name: str = "base"

    def __init__(self, model: Optional[str] = None, timeout_seconds: int = 60) -> None:
        self.model = model or get_default_model(self.name)
        self.timeout_seconds = timeout_seconds

    def call(
        self,
        prompt: str,
        system: str = "",
        thinking_level: str = "normal",
        max_tokens: int = 1000,
    ) -> ProviderResponse:
        raise NotImplementedError

    def _error_response(
        self,
        start: float,
        error: str,
        raw: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
    ) -> ProviderResponse:
        return ProviderResponse(
            model=model or self.model,
            provider=self.name,
            text="",
            thinking=None,
            tokens_in=0,
            tokens_out=0,
            latency_ms=_elapsed_ms(start),
            raw=raw or {},
            error=error[:1000],
        )


class ClaudeAdapter(ProviderAdapter):
    name = "claude"

    def call(
        self,
        prompt: str,
        system: str = "",
        thinking_level: str = "normal",
        max_tokens: int = 1000,
    ) -> ProviderResponse:
        start = time.perf_counter()
        try:
            import anthropic  # type: ignore
        except Exception as exc:  # pragma: no cover - exercised when SDK absent
            return self._error_response(start, f"anthropic SDK unavailable: {_exc_text(exc)}")

        api_key = get_api_key(self.name)
        if not api_key:
            return self._error_response(start, "Missing ANTHROPIC_API_KEY")

        try:
            client = anthropic.Anthropic(api_key=api_key, timeout=self.timeout_seconds)
            kwargs: Dict[str, Any] = {
                "model": self.model,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system:
                kwargs["system"] = system
            thinking = _claude_thinking_config(thinking_level, max_tokens)
            if thinking:
                kwargs["thinking"] = thinking
            response = client.messages.create(**kwargs)
            text, extended_thinking = _extract_anthropic_content(response)
            usage = getattr(response, "usage", None)
            return ProviderResponse(
                model=getattr(response, "model", None) or self.model,
                provider=self.name,
                text=text,
                thinking=extended_thinking,
                tokens_in=int(getattr(usage, "input_tokens", 0) or 0),
                tokens_out=int(getattr(usage, "output_tokens", 0) or 0),
                latency_ms=_elapsed_ms(start),
                raw=_jsonable(response),
                error=None,
            )
        except Exception as exc:
            return self._error_response(start, _classify_exception(exc), raw={"exception": _exc_text(exc)})


class GPTAdapter(ProviderAdapter):
    name = "gpt"

    def call(
        self,
        prompt: str,
        system: str = "",
        thinking_level: str = "normal",
        max_tokens: int = 1000,
    ) -> ProviderResponse:
        start = time.perf_counter()
        try:
            from openai import OpenAI  # type: ignore
        except Exception as exc:  # pragma: no cover - exercised when SDK absent
            return self._error_response(start, f"openai SDK unavailable: {_exc_text(exc)}")

        api_key = get_api_key(self.name)
        if not api_key:
            return self._error_response(start, "Missing OPENAI_API_KEY")

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        effort = REASONING_EFFORT_BY_LEVEL.get(normalize_thinking_level(thinking_level), "medium")

        try:
            client = OpenAI(api_key=api_key, timeout=self.timeout_seconds)
            kwargs: Dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "max_tokens": max_tokens,
                "reasoning_effort": effort,
            }
            try:
                response = client.chat.completions.create(**kwargs)
            except Exception as exc:
                # Some non-reasoning OpenAI models reject reasoning_effort. The
                # contract says to use it, but Seed should not fail closed here.
                if _looks_like_unsupported_reasoning(exc):
                    kwargs.pop("reasoning_effort", None)
                    response = client.chat.completions.create(**kwargs)
                else:
                    raise
            choice = response.choices[0] if getattr(response, "choices", None) else None
            message = getattr(choice, "message", None)
            text = getattr(message, "content", "") if message else ""
            usage = getattr(response, "usage", None)
            return ProviderResponse(
                model=getattr(response, "model", None) or self.model,
                provider=self.name,
                text=text or "",
                thinking=None,
                tokens_in=int(getattr(usage, "prompt_tokens", 0) or 0),
                tokens_out=int(getattr(usage, "completion_tokens", 0) or 0),
                latency_ms=_elapsed_ms(start),
                raw=_jsonable(response),
                error=None,
            )
        except Exception as exc:
            return self._error_response(start, _classify_exception(exc), raw={"exception": _exc_text(exc)})


class DeepSeekAdapter(ProviderAdapter):
    name = "deepseek"

    def call(
        self,
        prompt: str,
        system: str = "",
        thinking_level: str = "normal",
        max_tokens: int = 1000,
    ) -> ProviderResponse:
        start = time.perf_counter()
        try:
            import httpx  # type: ignore
        except Exception as exc:  # pragma: no cover - exercised when SDK absent
            return self._error_response(start, f"httpx unavailable: {_exc_text(exc)}")

        api_key = get_api_key(self.name)
        if not api_key:
            return self._error_response(start, "Missing DEEPSEEK_API_KEY")

        base_url = (get_base_url(self.name) or "https://api.deepseek.com").rstrip("/")
        model = self.model
        if normalize_thinking_level(thinking_level) == "high" and model == get_default_model(self.name):
            model = "deepseek-reasoner"

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        try:
            response = httpx.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model, "messages": messages, "max_tokens": max_tokens, "stream": False},
                timeout=self.timeout_seconds,
            )
            if response.status_code >= 400:
                return self._error_response(
                    start,
                    f"DeepSeek HTTP {response.status_code}: {response.text[:500]}",
                    raw={"status_code": response.status_code, "body": response.text[:2000]},
                    model=model,
                )
            data = response.json()
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            usage = data.get("usage") or {}
            return ProviderResponse(
                model=data.get("model") or model,
                provider=self.name,
                text=message.get("content") or "",
                thinking=message.get("reasoning_content"),
                tokens_in=int(usage.get("prompt_tokens") or 0),
                tokens_out=int(usage.get("completion_tokens") or 0),
                latency_ms=_elapsed_ms(start),
                raw=_jsonable(data),
                error=None,
            )
        except Exception as exc:
            return self._error_response(start, _classify_exception(exc), raw={"exception": _exc_text(exc)}, model=model)


class XaiAdapter(ProviderAdapter):
    name = "xai"

    def call(
        self,
        prompt: str,
        system: str = "",
        thinking_level: str = "normal",
        max_tokens: int = 1000,
    ) -> ProviderResponse:
        start = time.perf_counter()
        try:
            import httpx  # type: ignore
        except Exception as exc:  # pragma: no cover - exercised when SDK absent
            return self._error_response(start, f"httpx unavailable: {_exc_text(exc)}")

        api_key = get_api_key(self.name)
        if not api_key:
            return self._error_response(start, "Missing XAI_API_KEY")

        base_url = (get_base_url(self.name) or "https://api.x.ai/v1").rstrip("/")
        model = self.model

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        try:
            response = httpx.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model, "messages": messages, "max_tokens": max_tokens, "stream": False},
                timeout=self.timeout_seconds,
            )
            if response.status_code >= 400:
                return self._error_response(
                    start,
                    f"xAI HTTP {response.status_code}: {response.text[:500]}",
                    raw={"status_code": response.status_code, "body": response.text[:2000]},
                    model=model,
                )
            data = response.json()
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            usage = data.get("usage") or {}
            return ProviderResponse(
                model=data.get("model") or model,
                provider=self.name,
                text=message.get("content") or "",
                thinking=message.get("reasoning_content"),
                tokens_in=int(usage.get("prompt_tokens") or 0),
                tokens_out=int(usage.get("completion_tokens") or 0),
                latency_ms=_elapsed_ms(start),
                raw=_jsonable(data),
                error=None,
            )
        except Exception as exc:
            return self._error_response(start, _classify_exception(exc), raw={"exception": _exc_text(exc)}, model=model)


class GeminiAdapter(ProviderAdapter):
    name = "gemini"

    def call(
        self,
        prompt: str,
        system: str = "",
        thinking_level: str = "normal",
        max_tokens: int = 1000,
    ) -> ProviderResponse:
        start = time.perf_counter()
        try:
            import google.generativeai as genai  # type: ignore
        except Exception as exc:  # pragma: no cover - exercised when SDK absent
            return self._error_response(start, f"google-generativeai SDK unavailable: {_exc_text(exc)}")

        api_key = get_api_key(self.name)
        if not api_key:
            return self._error_response(start, "Missing GOOGLE_AI_API_KEY")

        try:
            genai.configure(api_key=api_key)
            generation_config: Dict[str, Any] = {"max_output_tokens": max_tokens}
            thinking_config = _gemini_thinking_config(thinking_level)
            if thinking_config:
                generation_config["thinking_config"] = thinking_config
            model = genai.GenerativeModel(
                self.model,
                system_instruction=system or None,
                generation_config=generation_config,
            )
            response = model.generate_content(prompt, request_options={"timeout": self.timeout_seconds})
            usage = getattr(response, "usage_metadata", None)
            return ProviderResponse(
                model=self.model,
                provider=self.name,
                text=getattr(response, "text", "") or _extract_gemini_text(response),
                thinking=None,
                tokens_in=int(getattr(usage, "prompt_token_count", 0) or 0),
                tokens_out=int(getattr(usage, "candidates_token_count", 0) or 0),
                latency_ms=_elapsed_ms(start),
                raw=_jsonable(response),
                error=None,
            )
        except Exception as exc:
            return self._error_response(start, _classify_exception(exc), raw={"exception": _exc_text(exc)})


class LocalAdapter(ProviderAdapter):
    name = "local"

    def call(
        self,
        prompt: str,
        system: str = "",
        thinking_level: str = "normal",
        max_tokens: int = 1000,
    ) -> ProviderResponse:
        start = time.perf_counter()
        try:
            import os
            import httpx  # type: ignore
        except Exception as exc:  # pragma: no cover - exercised when SDK absent
            return self._error_response(start, f"httpx unavailable: {_exc_text(exc)}")

        base_url = (get_base_url(self.name) or "").rstrip("/")
        if not base_url:
            return self._error_response(start, "Missing SEED_LOCAL_ENDPOINT")

        local_system = _local_system_prompt(system, thinking_level)
        messages = []
        if local_system:
            messages.append({"role": "system", "content": local_system})
        messages.append({"role": "user", "content": prompt})
        headers = {"Content-Type": "application/json"}
        local_key = os.getenv("SEED_LOCAL_API_KEY")
        if local_key:
            headers["Authorization"] = f"Bearer {local_key}"

        try:
            response = httpx.post(
                f"{base_url}/v1/chat/completions",
                headers=headers,
                json={"model": self.model, "messages": messages, "max_tokens": max_tokens, "stream": False},
                timeout=self.timeout_seconds,
            )
            if response.status_code >= 400:
                return self._error_response(
                    start,
                    f"Local HTTP {response.status_code}: {response.text[:500]}",
                    raw={"status_code": response.status_code, "body": response.text[:2000]},
                )
            data = response.json()
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            usage = data.get("usage") or {}
            return ProviderResponse(
                model=data.get("model") or self.model,
                provider=self.name,
                text=message.get("content") or "",
                thinking=message.get("reasoning_content"),
                tokens_in=int(usage.get("prompt_tokens") or 0),
                tokens_out=int(usage.get("completion_tokens") or 0),
                latency_ms=_elapsed_ms(start),
                raw=_jsonable(data),
                error=None,
            )
        except Exception as exc:
            return self._error_response(start, _classify_exception(exc), raw={"exception": _exc_text(exc)})


ADAPTER_CLASSES = {
    "claude": ClaudeAdapter,
    "gpt": GPTAdapter,
    "deepseek": DeepSeekAdapter,
    "xai": XaiAdapter,
    "gemini": GeminiAdapter,
    "local": LocalAdapter,
}


def get_adapter(provider: str, model: Optional[str] = None, timeout_seconds: int = 60) -> ProviderAdapter:
    """Factory for canonical provider names and provider:model shorthand."""
    provider_name, parsed_model = _split_provider_model(provider)
    name = normalize_provider_name(provider_name)
    if name not in ADAPTER_CLASSES:
        raise ValueError(f"Unknown provider: {provider}")
    return ADAPTER_CLASSES[name](model=model or parsed_model, timeout_seconds=timeout_seconds)


def get_adapters(providers: Optional[Iterable[str]] = None, timeout_seconds: int = 60) -> List[ProviderAdapter]:
    names = list(providers or configured_provider_names())
    if not names:
        names = list(PROVIDER_CONFIG.keys())
    return [get_adapter(name, timeout_seconds=timeout_seconds) for name in names]


def provider_names_for_all_request(models: Optional[List[str]]) -> List[str]:
    """Resolve compare/collab model lists where [] or [all] means configured providers."""
    if not models or [m.lower() for m in models] == ["all"]:
        names = configured_provider_names()
        return names or list(PROVIDER_CONFIG.keys())
    return models


def _split_provider_model(provider: str) -> Tuple[str, Optional[str]]:
    value = (provider or "").strip()
    if ":" in value:
        name, model = value.split(":", 1)
        return name.strip(), model.strip() or None
    return value, None


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _exc_text(exc: Exception) -> str:
    return f"{exc.__class__.__name__}: {str(exc)}"


def _classify_exception(exc: Exception) -> str:
    text = str(exc).lower()
    if "timeout" in text or exc.__class__.__name__.lower().endswith("timeout"):
        return f"timeout: {_exc_text(exc)}"
    if "rate" in text and "limit" in text:
        return f"rate_limit: {_exc_text(exc)}"
    if "401" in text or "403" in text or "auth" in text or "api key" in text:
        return f"auth_failure: {_exc_text(exc)}"
    return _exc_text(exc)


def _looks_like_unsupported_reasoning(exc: Exception) -> bool:
    text = str(exc).lower()
    return "reasoning_effort" in text or "unsupported" in text or "unrecognized" in text


def _jsonable(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    try:
        if hasattr(value, "model_dump"):
            value = value.model_dump()
        elif hasattr(value, "to_dict"):
            value = value.to_dict()
        elif not isinstance(value, (dict, list, tuple, str, int, float, bool)):
            value = getattr(value, "__dict__", str(value))
        return json.loads(json.dumps(value, default=str))
    except Exception:
        return {"repr": repr(value)}


def _claude_thinking_config(thinking_level: str, max_tokens: int) -> Optional[Dict[str, Any]]:
    level = normalize_thinking_level(thinking_level)
    if level == "low":
        return None
    budget = THINKING_BUDGET_BY_LEVEL.get(level, 1024)
    # Anthropic thinking budgets must leave room for visible output. If the
    # request is too small, omit the feature instead of sending invalid params.
    budget = min(budget, max(0, max_tokens - 256))
    if budget < 1024:
        return None
    return {"type": "enabled", "budget_tokens": budget}


def _gemini_thinking_config(thinking_level: str) -> Optional[Dict[str, Any]]:
    level = normalize_thinking_level(thinking_level)
    if level == "low":
        return {"thinking_budget": 0}
    return {"thinking_budget": THINKING_BUDGET_BY_LEVEL.get(level, 1024)}


def _local_system_prompt(system: str, thinking_level: str) -> str:
    level = normalize_thinking_level(thinking_level)
    instruction = ""
    if level == "high":
        instruction = "Think step by step internally before answering. Return only the final answer and key reasoning."
    elif level == "normal":
        instruction = "Reason carefully before answering. Keep the final answer concise."
    elif level == "low":
        instruction = "Answer directly. Do not include hidden reasoning."
    return "\n\n".join(part for part in [system, instruction] if part)


def _extract_anthropic_content(response: Any) -> Tuple[str, Optional[str]]:
    text_parts: List[str] = []
    thinking_parts: List[str] = []
    for block in getattr(response, "content", []) or []:
        block_type = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
        if block_type == "text":
            value = getattr(block, "text", None) if not isinstance(block, dict) else block.get("text")
            if value:
                text_parts.append(str(value))
        elif block_type in {"thinking", "redacted_thinking"}:
            value = getattr(block, "thinking", None) if not isinstance(block, dict) else block.get("thinking")
            value = value or (getattr(block, "text", None) if not isinstance(block, dict) else block.get("text"))
            if value:
                thinking_parts.append(str(value))
    return "\n".join(text_parts).strip(), ("\n".join(thinking_parts).strip() or None)


def _extract_gemini_text(response: Any) -> str:
    parts: List[str] = []
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            text = getattr(part, "text", None)
            if text:
                parts.append(str(text))
    return "\n".join(parts).strip()
