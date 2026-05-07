"""Seed BYOL provider adapters.

Playwright-backed BYOL ("bring your own login") adapters for Claude, ChatGPT,
Gemini, and DeepSeek consumer web plans. The adapters expose the same
ProviderAdapter.call(prompt, system, thinking_level, max_tokens) -> ProviderResponse
interface as the Book 5 BYOK adapters.

Trust protocol
--------------
CONFIRMED:
- Provider interface matches Book 5 BYOK: ProviderAdapter.call returns
  ProviderResponse with model/provider/text/thinking/tokens/latency/raw/error.
- Browser sessions persist under D:\\Seed\\sessions\\{provider}\\ on Windows by
  default. Override with SEED_BYOL_SESSION_ROOT for tests or non-Windows hosts.
- A visible browser login path is provided; credentials are entered only into the
  provider site by the user.
- Selector health is checked before each browser call. Missing/invalid required
  selectors trigger BYOK fallback when that provider is configured.

INFERRED:
- This is an adapter add-on, not a FastAPI route module.
- Consumer UIs do not provide SDK-style token accounting or extended thinking
  traces. Token counts are estimates; thinking is None.

PROPOSED:
- Install: pip install playwright && python -m playwright install chromium
- Login once:
    python seed_byol.py login claude
    python seed_byol.py login chatgpt
    python seed_byol.py login gemini
    python seed_byol.py login deepseek
- Check health:
    python seed_byol.py health all
- Embedded smoke test:
    python seed_byol.py smoke-test

UNKNOWN:
- Provider DOMs can change without notice. Selector candidates are defensive,
  not guaranteed. Broken selectors produce structured errors and BYOK fallback.

This module does not bypass login, CAPTCHA, rate limits, subscriptions, or
paywalls. Use only with accounts you control and respect provider terms.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import traceback
from typing import Any, Callable, Iterable
import unittest


# ---------------------------------------------------------------------------
# Book 5 compatibility. The fallback definitions let the embedded smoke test run
# outside the Seed backend.
# ---------------------------------------------------------------------------

try:  # pragma: no cover - used in Seed backend.
    from seed_providers import ProviderAdapter, ProviderResponse, get_adapter as _seed_get_adapter  # type: ignore
except Exception:  # pragma: no cover - standalone smoke-test path.
    _seed_get_adapter = None

    @dataclass
    class ProviderResponse:  # type: ignore[no-redef]
        model: str
        provider: str
        text: str
        thinking: str | None
        tokens_in: int
        tokens_out: int
        latency_ms: int
        raw: dict[str, Any]
        error: str | None

        def to_dict(self) -> dict[str, Any]:
            return asdict(self)

    class ProviderAdapter:  # type: ignore[no-redef]
        name = "base"

        def __init__(self, model: str | None = None, timeout_seconds: float = 30.0) -> None:
            self.model = model or "consumer"
            self.timeout_seconds = timeout_seconds

        def call(
            self,
            prompt: str,
            system: str = "",
            thinking_level: str = "normal",
            max_tokens: int = 1000,
        ) -> ProviderResponse:
            raise NotImplementedError


try:  # pragma: no cover - used in Seed backend.
    from seed_provider_config import is_provider_configured as _seed_provider_configured  # type: ignore
except Exception:  # pragma: no cover - standalone smoke-test path.
    _seed_provider_configured = None


# ---------------------------------------------------------------------------
# Profiles and state.
# ---------------------------------------------------------------------------

DEFAULT_SESSION_ROOT = r"D:\Seed\sessions"

BYOK_KEY_ENVS: dict[str, str] = {
    "claude": "ANTHROPIC_API_KEY",
    "gpt": "OPENAI_API_KEY",
    "gemini": "GOOGLE_AI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}

PROVIDER_ALIASES: dict[str, str] = {
    "claude": "claude",
    "anthropic": "claude",
    "gpt": "gpt",
    "openai": "gpt",
    "chatgpt": "gpt",
    "gemini": "gemini",
    "google": "gemini",
    "deepseek": "deepseek",
    "deep-seek": "deepseek",
}


@dataclass(frozen=True)
class SelectorProfile:
    provider: str
    session_slug: str
    display_name: str
    consumer_model: str
    app_url: str
    prompt_selectors: tuple[str, ...]
    submit_selectors: tuple[str, ...]
    response_selectors: tuple[str, ...]
    login_indicators: tuple[str, ...] = ()
    stop_selectors: tuple[str, ...] = ()
    submit_keys: tuple[str, ...] = ("Enter",)
    response_stable_seconds: float = 2.0


@dataclass
class SelectorHealthReport:
    provider: str
    session_dir: str
    url: str
    ok: bool
    status: str
    matched: dict[str, str | None] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    selector_errors: dict[str, str] = field(default_factory=dict)
    login_required: bool = False
    checked_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SELECTOR_PROFILES: dict[str, SelectorProfile] = {
    "claude": SelectorProfile(
        provider="claude",
        session_slug="claude",
        display_name="Claude",
        consumer_model="claude-consumer-web",
        app_url="https://claude.ai/new",
        prompt_selectors=(
            '[data-testid="chat-input"]',
            'div[contenteditable="true"][role="textbox"]',
            'div.ProseMirror[contenteditable="true"]',
            'textarea[placeholder*="Message" i]',
            'textarea[aria-label*="Message" i]',
            '[aria-label*="Message Claude" i]',
        ),
        submit_selectors=(
            'button[data-testid="send-button"]',
            'button[aria-label*="Send" i]',
            'button:has-text("Send")',
            'button[type="submit"]',
        ),
        response_selectors=(
            '[data-testid="message"]',
            '[data-testid*="assistant" i]',
            '.font-claude-message',
            '.prose',
            'div[data-is-streaming]',
        ),
        login_indicators=(
            'text=/Sign in|Log in|Continue with Google|Continue with email/i',
            'input[type="email"]',
            'input[name="email"]',
        ),
        stop_selectors=('button[aria-label*="Stop" i]', 'button:has-text("Stop")'),
    ),
    "gpt": SelectorProfile(
        provider="gpt",
        session_slug="chatgpt",
        display_name="ChatGPT",
        consumer_model="chatgpt-consumer-web",
        app_url="https://chatgpt.com/",
        prompt_selectors=(
            '#prompt-textarea',
            '[data-testid="prompt-textarea"]',
            'textarea#prompt-textarea',
            'textarea[placeholder*="Message" i]',
            'div#prompt-textarea[contenteditable="true"]',
            'div[contenteditable="true"][role="textbox"]',
            'textarea',
        ),
        submit_selectors=(
            'button[data-testid="send-button"]',
            'button[aria-label*="Send" i]',
            'button:has-text("Send")',
            'button[type="submit"]',
        ),
        response_selectors=(
            '[data-message-author-role="assistant"]',
            '[data-testid^="conversation-turn-"] [data-message-author-role="assistant"]',
            'article:has([data-message-author-role="assistant"])',
            '.markdown',
        ),
        login_indicators=(
            'text=/Log in|Sign up|Get started|Continue with Google|Continue with Microsoft/i',
            'input[type="email"]',
        ),
        stop_selectors=('button[data-testid="stop-button"]', 'button[aria-label*="Stop" i]'),
    ),
    "gemini": SelectorProfile(
        provider="gemini",
        session_slug="gemini",
        display_name="Gemini",
        consumer_model="gemini-consumer-web",
        app_url="https://gemini.google.com/app",
        prompt_selectors=(
            'rich-textarea div[contenteditable="true"]',
            'rich-textarea [contenteditable="true"]',
            'div[contenteditable="true"][role="textbox"]',
            '[aria-label*="Enter a prompt" i]',
            '[aria-label*="Message" i][contenteditable="true"]',
            'textarea[aria-label*="prompt" i]',
        ),
        submit_selectors=(
            'button[aria-label*="Send" i]',
            'button[aria-label*="Submit" i]',
            'button.send-button',
            'button[type="submit"]',
        ),
        response_selectors=(
            'message-content',
            '.model-response-text',
            '[data-response-index]',
            'div[class*="model-response" i]',
            'markdown',
        ),
        login_indicators=(
            'text=/Sign in|Use your Google Account|Email or phone/i',
            'input[type="email"]',
            'input[type="password"]',
        ),
        stop_selectors=('button[aria-label*="Stop" i]', 'button[aria-label*="Cancel" i]'),
    ),
    "deepseek": SelectorProfile(
        provider="deepseek",
        session_slug="deepseek",
        display_name="DeepSeek",
        consumer_model="deepseek-consumer-web",
        app_url="https://chat.deepseek.com/",
        prompt_selectors=(
            'textarea[placeholder*="Message" i]',
            'textarea[aria-label*="Message" i]',
            'textarea',
            'div[contenteditable="true"][role="textbox"]',
            'div[contenteditable="true"]',
        ),
        submit_selectors=(
            'button[aria-label*="Send" i]',
            'button:has-text("Send")',
            'button[type="submit"]',
            'button[class*="send" i]',
        ),
        response_selectors=(
            '.ds-markdown',
            '.markdown',
            '[class*="answer" i]',
            '[class*="assistant" i]',
            '[class*="message" i]',
        ),
        login_indicators=(
            'text=/Log in|Sign in|Sign up|Continue with Google|Email/i',
            'input[type="email"]',
            'input[type="password"]',
        ),
        stop_selectors=('button[aria-label*="Stop" i]', 'button[class*="stop" i]'),
    ),
}


class BYOLError(RuntimeError):
    pass


class BYOLBrowserUnavailable(BYOLError):
    pass


class BYOLSelectorError(BYOLError):
    def __init__(self, message: str, report: SelectorHealthReport | None = None) -> None:
        super().__init__(message)
        self.report = report


class BYOLTimeoutError(BYOLError):
    pass


BrowserCallFn = Callable[["BYOLProviderAdapter", str, str, str, int], ProviderResponse]
ByokAdapterFactory = Callable[[str, str | None, float], ProviderAdapter]
ByokAvailableFn = Callable[[str], bool]

_PROVIDER_LOCKS: dict[str, threading.RLock] = {}
_PROVIDER_LOCKS_GUARD = threading.Lock()


# ---------------------------------------------------------------------------
# Public adapters.
# ---------------------------------------------------------------------------

class BYOLProviderAdapter(ProviderAdapter):
    name = "base"
    profile_key = "base"

    def __init__(
        self,
        model: str | None = None,
        timeout_seconds: float = 90.0,
        *,
        session_root: str | Path | None = None,
        headless: bool | None = None,
        fallback_to_byok: bool = True,
        byok_adapter_factory: ByokAdapterFactory | None = None,
        byok_available_fn: ByokAvailableFn | None = None,
        browser_call_fn: BrowserCallFn | None = None,
    ) -> None:
        super().__init__(model=model, timeout_seconds=timeout_seconds)
        self.profile = SELECTOR_PROFILES[self.profile_key]
        self.model = model or self.profile.consumer_model
        self.timeout_seconds = float(timeout_seconds)
        self.session_root = Path(session_root) if session_root else default_session_root()
        self.headless = _env_bool("SEED_BYOL_HEADLESS", True) if headless is None else bool(headless)
        self.fallback_to_byok = bool(fallback_to_byok)
        self.byok_adapter_factory = byok_adapter_factory or _default_byok_adapter_factory
        self.byok_available_fn = byok_available_fn or _default_byok_available
        self.browser_call_fn = browser_call_fn

    @property
    def session_dir(self) -> Path:
        return self.session_root / self.profile.session_slug

    def call(
        self,
        prompt: str,
        system: str = "",
        thinking_level: str = "normal",
        max_tokens: int = 1000,
    ) -> ProviderResponse:
        started = time.monotonic()
        if not str(prompt or "").strip():
            return self._error("empty prompt", started, {"transport": "byol"})
        try:
            return (self.browser_call_fn or _real_browser_call)(self, prompt, system, thinking_level, max_tokens)
        except BYOLSelectorError as exc:
            raw: dict[str, Any] = {"transport": "byol", "error_type": exc.__class__.__name__}
            if exc.report:
                raw["selector_health"] = exc.report.to_dict()
            return self._fallback_or_error(prompt, system, thinking_level, max_tokens, str(exc), started, raw)
        except (BYOLBrowserUnavailable, BYOLTimeoutError, BYOLError) as exc:
            return self._fallback_or_error(
                prompt, system, thinking_level, max_tokens, str(exc), started,
                {"transport": "byol", "error_type": exc.__class__.__name__},
            )
        except Exception as exc:
            return self._fallback_or_error(
                prompt, system, thinking_level, max_tokens, str(exc), started,
                {"transport": "byol", "error_type": exc.__class__.__name__, "traceback": traceback.format_exc(limit=4)},
            )

    def selector_health_check(self, *, headless: bool | None = None, timeout_seconds: float | None = None) -> SelectorHealthReport:
        return _real_selector_health_check(
            self,
            headless=self.headless if headless is None else bool(headless),
            timeout_seconds=self.timeout_seconds if timeout_seconds is None else float(timeout_seconds),
        )

    def login(self, *, timeout_seconds: float = 600.0) -> SelectorHealthReport:
        return _interactive_login(self, timeout_seconds=timeout_seconds)

    def _fallback_or_error(
        self,
        prompt: str,
        system: str,
        thinking_level: str,
        max_tokens: int,
        reason: str,
        started: float,
        raw: dict[str, Any],
    ) -> ProviderResponse:
        if self.fallback_to_byok and self._byok_available():
            try:
                byok = self.byok_adapter_factory(canonical_byok_provider(self.name), None, self.timeout_seconds)
                response = byok.call(prompt, system=system, thinking_level=thinking_level, max_tokens=max_tokens)
                byok_raw = response.raw if isinstance(response.raw, dict) else {"raw": str(response.raw)}
                response.raw = {
                    "transport": "byok_fallback",
                    "byol_failure": {"reason": reason, **raw},
                    "byok_raw": byok_raw,
                }
                return response
            except Exception as exc:
                raw = {**raw, "byok_fallback_error": str(exc), "byok_fallback_error_type": exc.__class__.__name__}
        return self._error(reason, started, raw)

    def _byok_available(self) -> bool:
        try:
            return bool(self.byok_available_fn(canonical_byok_provider(self.name)))
        except Exception:
            return False

    def _error(self, error: str, started: float, raw: dict[str, Any]) -> ProviderResponse:
        return ProviderResponse(
            model=self.model,
            provider=self.name,
            text="",
            thinking=None,
            tokens_in=0,
            tokens_out=0,
            latency_ms=latency_ms(started),
            raw=raw,
            error=error,
        )


class ClaudeBYOLAdapter(BYOLProviderAdapter):
    name = "claude"
    profile_key = "claude"


class ChatGPTBYOLAdapter(BYOLProviderAdapter):
    # Same Book 5 provider key as BYOK GPT; session slug remains "chatgpt".
    name = "gpt"
    profile_key = "gpt"


class GeminiBYOLAdapter(BYOLProviderAdapter):
    name = "gemini"
    profile_key = "gemini"


class DeepSeekBYOLAdapter(BYOLProviderAdapter):
    name = "deepseek"
    profile_key = "deepseek"


BYOL_ADAPTERS: dict[str, type[BYOLProviderAdapter]] = {
    "claude": ClaudeBYOLAdapter,
    "gpt": ChatGPTBYOLAdapter,
    "gemini": GeminiBYOLAdapter,
    "deepseek": DeepSeekBYOLAdapter,
}


def get_byol_adapter(provider: str, model: str | None = None, timeout_seconds: float = 90.0, **kwargs: Any) -> BYOLProviderAdapter:
    key = normalize_provider(provider)
    return BYOL_ADAPTERS[key](model=model, timeout_seconds=timeout_seconds, **kwargs)


def call_byol_provider(
    provider: str,
    prompt: str,
    system: str = "",
    thinking_level: str = "normal",
    max_tokens: int = 1000,
    model: str | None = None,
    **kwargs: Any,
) -> ProviderResponse:
    return get_byol_adapter(provider, model=model, **kwargs).call(prompt, system, thinking_level, max_tokens)


def selector_health(provider: str, **kwargs: Any) -> SelectorHealthReport:
    return get_byol_adapter(provider, **kwargs).selector_health_check()


def login_provider(provider: str, *, timeout_seconds: float = 600.0, **kwargs: Any) -> SelectorHealthReport:
    return get_byol_adapter(provider, **kwargs).login(timeout_seconds=timeout_seconds)


def all_byol_provider_names() -> list[str]:
    return ["claude", "chatgpt", "gemini", "deepseek"]


def normalize_provider(provider: str) -> str:
    key = str(provider or "").strip().lower()
    if key not in PROVIDER_ALIASES:
        raise ValueError(f"unknown provider alias: {provider}")
    return PROVIDER_ALIASES[key]


def canonical_byok_provider(provider: str) -> str:
    return "gpt" if normalize_provider(provider) == "gpt" else normalize_provider(provider)


def default_session_root() -> Path:
    return Path(os.getenv("SEED_BYOL_SESSION_ROOT") or DEFAULT_SESSION_ROOT)


def byol_session_dir(provider: str, session_root: str | Path | None = None) -> Path:
    root = Path(session_root) if session_root else default_session_root()
    return root / SELECTOR_PROFILES[normalize_provider(provider)].session_slug


# ---------------------------------------------------------------------------
# Playwright implementation.
# ---------------------------------------------------------------------------

def _real_browser_call(adapter: BYOLProviderAdapter, prompt: str, system: str, thinking_level: str, max_tokens: int) -> ProviderResponse:
    started = time.monotonic()
    composed = compose_consumer_prompt(prompt, system, thinking_level, max_tokens)
    profile = adapter.profile
    with _provider_lock(profile.session_slug):
        with launch_context(adapter, headless=adapter.headless) as context:
            page = first_page(context)
            goto_app(page, profile, adapter.timeout_seconds)
            health = health_check_on_page(adapter, page)
            persist_health(adapter, health)
            if not health.ok:
                raise BYOLSelectorError(f"selector health failed: {health.status}", health)

            initial_count = response_count(page, profile)
            fill_prompt(page, profile, composed, adapter.timeout_seconds)
            submit_prompt(page, profile, adapter.timeout_seconds)
            text = wait_for_response_text(page, profile, initial_count, adapter.timeout_seconds)

    return ProviderResponse(
        model=adapter.model,
        provider=adapter.name,
        text=trim_to_max_tokens_estimate(text, max_tokens),
        thinking=None,
        tokens_in=estimate_tokens(composed),
        tokens_out=estimate_tokens(text),
        latency_ms=latency_ms(started),
        raw={
            "transport": "byol",
            "provider_ui": profile.display_name,
            "session_dir": str(adapter.session_dir),
            "url": profile.app_url,
            "token_counts": "estimated",
        },
        error=None,
    )


def _real_selector_health_check(adapter: BYOLProviderAdapter, *, headless: bool, timeout_seconds: float) -> SelectorHealthReport:
    with _provider_lock(adapter.profile.session_slug):
        with launch_context(adapter, headless=headless) as context:
            page = first_page(context)
            goto_app(page, adapter.profile, timeout_seconds)
            report = health_check_on_page(adapter, page)
            persist_health(adapter, report)
            return report


def _interactive_login(adapter: BYOLProviderAdapter, *, timeout_seconds: float) -> SelectorHealthReport:
    sync_playwright = import_sync_playwright()
    adapter.session_dir.mkdir(parents=True, exist_ok=True)
    print(f"Opening visible {adapter.profile.display_name} login browser.", file=sys.stderr)
    print(f"Session directory: {adapter.session_dir}", file=sys.stderr)
    print("Complete login in the browser. This returns when the prompt selector is healthy.", file=sys.stderr)

    started = time.monotonic()
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(str(adapter.session_dir), **browser_launch_kwargs(headless=False))
        try:
            page = first_page(context)
            goto_app(page, adapter.profile, timeout_seconds)
            last = health_check_on_page(adapter, page)
            while time.monotonic() - started < timeout_seconds:
                last = health_check_on_page(adapter, page)
                if last.ok:
                    persist_health(adapter, last)
                    return last
                page.wait_for_timeout(2000)
            persist_health(adapter, last)
            return last
        finally:
            context.close()


class launch_context:
    def __init__(self, adapter: BYOLProviderAdapter, *, headless: bool) -> None:
        self.adapter = adapter
        self.headless = headless
        self.manager: Any = None
        self.pw: Any = None
        self.context: Any = None

    def __enter__(self) -> Any:
        sync_playwright = import_sync_playwright()
        self.adapter.session_dir.mkdir(parents=True, exist_ok=True)
        self.manager = sync_playwright()
        self.pw = self.manager.__enter__()
        self.context = self.pw.chromium.launch_persistent_context(
            str(self.adapter.session_dir),
            **browser_launch_kwargs(headless=self.headless),
        )
        return self.context

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            if self.context:
                self.context.close()
        finally:
            if self.manager:
                self.manager.__exit__(exc_type, exc, tb)


def import_sync_playwright() -> Any:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
        return sync_playwright
    except Exception as exc:
        raise BYOLBrowserUnavailable(
            "Playwright unavailable; install with `pip install playwright` and "
            "`python -m playwright install chromium`"
        ) from exc


def browser_launch_kwargs(*, headless: bool) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "headless": headless,
        "viewport": {
            "width": int(os.getenv("SEED_BYOL_VIEWPORT_WIDTH", "1440")),
            "height": int(os.getenv("SEED_BYOL_VIEWPORT_HEIGHT", "1000")),
        },
        "locale": os.getenv("SEED_BYOL_LOCALE", "en-US"),
        "accept_downloads": False,
        "args": ["--disable-dev-shm-usage"],
    }
    channel = os.getenv("SEED_BYOL_BROWSER_CHANNEL", "").strip()
    if channel:
        kwargs["channel"] = channel
    slow = os.getenv("SEED_BYOL_SLOW_MO_MS", "").strip()
    if slow:
        try:
            kwargs["slow_mo"] = int(slow)
        except ValueError:
            pass
    return kwargs


def first_page(context: Any) -> Any:
    return context.pages[0] if getattr(context, "pages", None) else context.new_page()


def goto_app(page: Any, profile: SelectorProfile, timeout_seconds: float) -> None:
    page.goto(profile.app_url, wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000))
    try:
        page.wait_for_load_state("domcontentloaded", timeout=min(10000, int(timeout_seconds * 1000)))
    except Exception:
        pass


def health_check_on_page(adapter: BYOLProviderAdapter, page: Any) -> SelectorHealthReport:
    profile = adapter.profile
    errors: dict[str, str] = {}
    matched = {
        "prompt": first_matching_selector(page, profile.prompt_selectors, errors, require_visible=True),
        "submit": first_matching_selector(page, profile.submit_selectors, errors, require_visible=False),
        "response": first_matching_selector(page, profile.response_selectors, errors, require_visible=False),
    }
    missing = [] if matched["prompt"] else ["prompt"]
    login_required = looks_like_login(page, profile, errors)

    if login_required and not matched["prompt"]:
        status, ok = "login_required", False
    elif errors:
        status, ok = "selector_syntax_or_query_error", False
    elif missing:
        status, ok = "missing_required_selectors", False
    else:
        status, ok = "ok", True

    warnings: list[str] = []
    if not matched["submit"]:
        warnings.append("submit selector not visible/present before prompt; keyboard fallback available")
    if not matched["response"]:
        warnings.append("response selector may appear only after first answer")

    return SelectorHealthReport(
        provider=adapter.name,
        session_dir=str(adapter.session_dir),
        url=safe_url(page),
        ok=ok,
        status=status,
        matched=matched,
        missing=missing,
        selector_errors=errors,
        login_required=login_required,
        raw={"display_name": profile.display_name, "app_url": profile.app_url, "warnings": warnings},
    )


def looks_like_login(page: Any, profile: SelectorProfile, errors: dict[str, str]) -> bool:
    url = safe_url(page).lower()
    if any(part in url for part in ("/login", "/signin", "/sign-in", "/auth", "accounts.google.com")):
        return True
    for selector in profile.login_indicators:
        try:
            loc = page.locator(selector)
            for i in range(min(loc.count(), 3)):
                try:
                    if loc.nth(i).is_visible(timeout=250):
                        return True
                except Exception:
                    pass
        except Exception as exc:
            errors[f"login:{selector}"] = str(exc)
    return False


def first_matching_selector(page: Any, selectors: Iterable[str], errors: dict[str, str], *, require_visible: bool) -> str | None:
    for selector in selectors:
        try:
            loc = page.locator(selector)
            count = loc.count()
            if count <= 0:
                continue
            if not require_visible:
                return selector
            for i in range(min(count, 5)):
                try:
                    if loc.nth(i).is_visible(timeout=250):
                        return selector
                except Exception:
                    pass
        except Exception as exc:
            errors[selector] = str(exc)
    return None


def fill_prompt(page: Any, profile: SelectorProfile, text: str, timeout_seconds: float) -> None:
    errors: dict[str, str] = {}
    selector = first_matching_selector(page, profile.prompt_selectors, errors, require_visible=True)
    if not selector:
        raise BYOLSelectorError(f"prompt selector missing; errors={errors}")
    loc = page.locator(selector).first
    loc.wait_for(state="visible", timeout=int(min(timeout_seconds, 15) * 1000))
    loc.click(timeout=int(min(timeout_seconds, 15) * 1000))

    try:
        loc.fill(text, timeout=int(min(timeout_seconds, 15) * 1000))
        return
    except Exception:
        pass

    try:
        handle = loc.element_handle(timeout=int(min(timeout_seconds, 15) * 1000))
        if handle is None:
            raise RuntimeError("no element handle")
        handle.evaluate(
            """(el, value) => {
                el.focus();
                if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') { el.value = value; }
                else { el.textContent = value; }
                el.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: value}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
            }""",
            text,
        )
        page.wait_for_timeout(250)
        return
    except Exception:
        pass

    page.keyboard.press("Meta+A" if sys.platform == "darwin" else "Control+A")
    page.keyboard.type(text, delay=0)
    page.wait_for_timeout(250)


def submit_prompt(page: Any, profile: SelectorProfile, timeout_seconds: float) -> None:
    errors: dict[str, str] = {}
    selector = first_matching_selector(page, profile.submit_selectors, errors, require_visible=True)
    if selector:
        loc = page.locator(selector).first
        deadline = time.monotonic() + min(timeout_seconds, 10)
        while time.monotonic() < deadline:
            try:
                if loc.is_enabled(timeout=250):
                    loc.click(timeout=int(min(timeout_seconds, 10) * 1000))
                    return
            except Exception:
                pass
            page.wait_for_timeout(250)

    for key in profile.submit_keys:
        try:
            page.keyboard.press(key)
            page.wait_for_timeout(500)
            return
        except Exception:
            pass
    raise BYOLSelectorError(f"submit selector missing/disabled; errors={errors}")


def wait_for_response_text(page: Any, profile: SelectorProfile, initial_count: int, timeout_seconds: float) -> str:
    deadline = time.monotonic() + timeout_seconds
    last = ""
    stable_since: float | None = None
    while time.monotonic() < deadline:
        count = response_count(page, profile)
        text = latest_response_text(page, profile)
        if text and (count > initial_count or (initial_count == 0 and text)):
            if text != last:
                last = text
                stable_since = time.monotonic()
            elif stable_since is not None and not is_streaming(page, profile):
                if time.monotonic() - stable_since >= profile.response_stable_seconds:
                    return clean_response_text(text)
        page.wait_for_timeout(750)
    if last:
        return clean_response_text(last)
    raise BYOLTimeoutError(f"no response detected within {timeout_seconds:.1f}s")


def response_count(page: Any, profile: SelectorProfile) -> int:
    count = 0
    for selector in profile.response_selectors:
        try:
            count = max(count, page.locator(selector).count())
        except Exception:
            pass
    return count


def latest_response_text(page: Any, profile: SelectorProfile) -> str:
    for selector in profile.response_selectors:
        try:
            loc = page.locator(selector)
            count = loc.count()
            for i in range(count - 1, max(-1, count - 6), -1):
                try:
                    text = loc.nth(i).inner_text(timeout=750).strip()
                    if text:
                        return text
                except Exception:
                    pass
        except Exception:
            pass
    return ""


def is_streaming(page: Any, profile: SelectorProfile) -> bool:
    for selector in profile.stop_selectors:
        try:
            loc = page.locator(selector)
            for i in range(min(loc.count(), 3)):
                try:
                    if loc.nth(i).is_visible(timeout=250):
                        return True
                except Exception:
                    pass
        except Exception:
            pass
    return False


def persist_health(adapter: BYOLProviderAdapter, report: SelectorHealthReport) -> None:
    try:
        adapter.session_dir.mkdir(parents=True, exist_ok=True)
        (adapter.session_dir / "selector_health.json").write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# BYOK fallback and utility helpers.
# ---------------------------------------------------------------------------

def _default_byok_adapter_factory(provider: str, model: str | None, timeout_seconds: float) -> ProviderAdapter:
    if _seed_get_adapter is None:
        raise RuntimeError("seed_providers.get_adapter is unavailable")
    return _seed_get_adapter(provider, model=model, timeout_seconds=timeout_seconds)


def _default_byok_available(provider: str) -> bool:
    provider = canonical_byok_provider(provider)
    if _seed_provider_configured is not None:
        try:
            return bool(_seed_provider_configured(provider))
        except Exception:
            pass
    env_name = BYOK_KEY_ENVS.get(provider)
    return bool(env_name and os.getenv(env_name))


def _provider_lock(slug: str) -> threading.RLock:
    with _PROVIDER_LOCKS_GUARD:
        if slug not in _PROVIDER_LOCKS:
            _PROVIDER_LOCKS[slug] = threading.RLock()
        return _PROVIDER_LOCKS[slug]


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def latency_ms(started_at: float) -> int:
    return int((time.monotonic() - started_at) * 1000)


def estimate_tokens(text: str) -> int:
    return 0 if not text else max(1, len(text) // 4)


def trim_to_max_tokens_estimate(text: str, max_tokens: int) -> str:
    clean = text.strip()
    if not max_tokens or max_tokens <= 0:
        return clean
    words = clean.split()
    max_words = max(1, int(max_tokens * 0.85))
    return clean if len(words) <= max_words else " ".join(words[:max_words]).rstrip() + " …"


def compose_consumer_prompt(prompt: str, system: str, thinking_level: str, max_tokens: int) -> str:
    parts: list[str] = []
    if system.strip():
        parts.append(f"System/context instructions:\n{system.strip()}")
    effort = (thinking_level or "normal").lower().strip()
    if effort in {"high", "deep", "maximum", "max"}:
        parts.append("Reason carefully in private. Do not reveal hidden chain-of-thought. Return the final answer.")
    elif effort in {"low", "direct", "none", "minimal"}:
        parts.append("Answer directly.")
    if max_tokens and max_tokens > 0:
        parts.append(f"Target length: no more than about {max(50, int(max_tokens * 0.75))} words unless required.")
    parts.append(prompt.strip())
    return "\n\n".join(parts)


def clean_response_text(text: str) -> str:
    clean = re.sub(r"\n{3,}", "\n\n", text or "").strip()
    junk = {"copy", "retry", "regenerate", "share", "good response", "bad response", "read aloud"}
    return "\n".join(line.rstrip() for line in clean.splitlines() if line.strip().lower() not in junk).strip()


def safe_url(page: Any) -> str:
    try:
        return str(page.url)
    except Exception:
        return ""


def print_json(value: Any) -> None:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    elif hasattr(value, "__dataclass_fields__"):
        value = asdict(value)
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


# ---------------------------------------------------------------------------
# Embedded smoke test. No Playwright or real sessions required.
# ---------------------------------------------------------------------------

class _FakeByokAdapter(ProviderAdapter):
    def __init__(self, provider: str, model: str | None = None, timeout_seconds: float = 30.0) -> None:
        super().__init__(model=model or f"{provider}-byok-test", timeout_seconds=timeout_seconds)
        self.name = provider

    def call(self, prompt: str, system: str = "", thinking_level: str = "normal", max_tokens: int = 1000) -> ProviderResponse:
        return ProviderResponse(
            model=self.model,
            provider=self.name,
            text=f"fallback ok: {prompt[:20]}",
            thinking=None,
            tokens_in=estimate_tokens(prompt + system),
            tokens_out=4,
            latency_ms=1,
            raw={"transport": "byok", "thinking_level": thinking_level, "max_tokens": max_tokens},
            error=None,
        )


def _fake_byok_factory(provider: str, model: str | None, timeout_seconds: float) -> ProviderAdapter:
    return _FakeByokAdapter(provider, model, timeout_seconds)


def _broken_browser_call(adapter: BYOLProviderAdapter, prompt: str, system: str, thinking_level: str, max_tokens: int) -> ProviderResponse:
    report = SelectorHealthReport(
        provider=adapter.name,
        session_dir=str(adapter.session_dir),
        url=adapter.profile.app_url,
        ok=False,
        status="missing_required_selectors",
        matched={"prompt": None, "submit": None, "response": None},
        missing=["prompt"],
    )
    raise BYOLSelectorError("selector health failed: missing_required_selectors", report)


def _successful_browser_call(adapter: BYOLProviderAdapter, prompt: str, system: str, thinking_level: str, max_tokens: int) -> ProviderResponse:
    started = time.monotonic()
    return ProviderResponse(
        model=adapter.model,
        provider=adapter.name,
        text="browser ok",
        thinking=None,
        tokens_in=estimate_tokens(prompt + system),
        tokens_out=2,
        latency_ms=latency_ms(started),
        raw={"transport": "byol"},
        error=None,
    )


class BYOLSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_selector_profiles_cover_required_providers(self) -> None:
        self.assertEqual(set(SELECTOR_PROFILES), {"claude", "gpt", "gemini", "deepseek"})
        for key, profile in SELECTOR_PROFILES.items():
            with self.subTest(provider=key):
                self.assertTrue(profile.app_url.startswith("https://"))
                self.assertGreaterEqual(len(profile.prompt_selectors), 3)
                self.assertGreaterEqual(len(profile.response_selectors), 3)
                self.assertTrue(profile.consumer_model.endswith("web"))

    def test_session_paths_are_provider_scoped(self) -> None:
        root = Path(self.tmp.name)
        self.assertEqual(byol_session_dir("chatgpt", root), root / "chatgpt")
        self.assertEqual(byol_session_dir("openai", root), root / "chatgpt")
        self.assertEqual(byol_session_dir("claude", root), root / "claude")

    def test_factory_aliases_and_interface(self) -> None:
        adapter = get_byol_adapter("openai", session_root=self.tmp.name, fallback_to_byok=False, browser_call_fn=_successful_browser_call)
        response = adapter.call("ping", system="system", thinking_level="low", max_tokens=20)
        self.assertIsInstance(adapter, ProviderAdapter)
        self.assertIsInstance(response, ProviderResponse)
        self.assertEqual(response.provider, "gpt")
        self.assertEqual(response.raw.get("transport"), "byol")
        self.assertIsNone(response.error)

    def test_selector_failure_falls_back_to_byok_when_available(self) -> None:
        adapter = get_byol_adapter(
            "chatgpt",
            session_root=self.tmp.name,
            fallback_to_byok=True,
            byok_adapter_factory=_fake_byok_factory,
            byok_available_fn=lambda provider: True,
            browser_call_fn=_broken_browser_call,
        )
        response = adapter.call("hello world", thinking_level="normal", max_tokens=50)
        self.assertIsNone(response.error)
        self.assertEqual(response.provider, "gpt")
        self.assertIn("fallback ok", response.text)
        self.assertEqual(response.raw.get("transport"), "byok_fallback")
        self.assertIn("selector_health", response.raw["byol_failure"])

    def test_selector_failure_returns_structured_error_without_byok(self) -> None:
        adapter = get_byol_adapter("claude", session_root=self.tmp.name, fallback_to_byok=False, browser_call_fn=_broken_browser_call)
        response = adapter.call("hello")
        self.assertIsNotNone(response.error)
        self.assertEqual(response.provider, "claude")
        self.assertEqual(response.raw.get("transport"), "byol")
        self.assertEqual(response.raw["selector_health"]["status"], "missing_required_selectors")

    def test_empty_prompt_never_calls_browser(self) -> None:
        called = {"value": False}
        def fail_if_called(adapter: BYOLProviderAdapter, prompt: str, system: str, thinking_level: str, max_tokens: int) -> ProviderResponse:
            called["value"] = True
            raise AssertionError("browser should not be called")
        adapter = get_byol_adapter("gemini", session_root=self.tmp.name, fallback_to_byok=False, browser_call_fn=fail_if_called)
        response = adapter.call("   ")
        self.assertFalse(called["value"])
        self.assertEqual(response.error, "empty prompt")


def run_smoke_tests() -> int:
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(BYOLSmokeTests))
    return 0 if result.wasSuccessful() else 1


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------

def iter_requested_providers(value: str) -> list[str]:
    return all_byol_provider_names() if value.strip().lower() == "all" else [value]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed BYOL Playwright provider adapters")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("smoke-test", help="run embedded smoke tests; no browser required")

    login_p = sub.add_parser("login", help="open visible browser and persist provider session")
    login_p.add_argument("provider", help="claude|chatgpt|gemini|deepseek|all")
    login_p.add_argument("--timeout", type=float, default=600.0)
    login_p.add_argument("--session-root", default=None)

    health_p = sub.add_parser("health", help="check selector health for persisted session")
    health_p.add_argument("provider", help="claude|chatgpt|gemini|deepseek|all")
    health_p.add_argument("--headed", action="store_true")
    health_p.add_argument("--session-root", default=None)
    health_p.add_argument("--timeout", type=float, default=60.0)

    call_p = sub.add_parser("call", help="make one BYOL call from the console")
    call_p.add_argument("provider", help="claude|chatgpt|gemini|deepseek")
    call_p.add_argument("prompt")
    call_p.add_argument("--system", default="")
    call_p.add_argument("--thinking-level", default="normal")
    call_p.add_argument("--max-tokens", type=int, default=1000)
    call_p.add_argument("--session-root", default=None)
    call_p.add_argument("--headed", action="store_true")
    call_p.add_argument("--no-byok-fallback", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "smoke-test":
        return run_smoke_tests()

    if args.command == "login":
        exit_code = 0
        for provider in iter_requested_providers(args.provider):
            try:
                report = login_provider(provider, timeout_seconds=args.timeout, session_root=args.session_root)
                print_json(report)
                exit_code = exit_code or (0 if report.ok else 2)
            except Exception as exc:
                print(f"{provider}: {exc}", file=sys.stderr)
                exit_code = 2
        return exit_code

    if args.command == "health":
        exit_code = 0
        for provider in iter_requested_providers(args.provider):
            try:
                adapter = get_byol_adapter(provider, session_root=args.session_root, headless=not args.headed, timeout_seconds=args.timeout)
                report = adapter.selector_health_check(headless=not args.headed, timeout_seconds=args.timeout)
                print_json(report)
                exit_code = exit_code or (0 if report.ok else 2)
            except Exception as exc:
                print(f"{provider}: {exc}", file=sys.stderr)
                exit_code = 2
        return exit_code

    if args.command == "call":
        response = call_byol_provider(
            args.provider,
            args.prompt,
            system=args.system,
            thinking_level=args.thinking_level,
            max_tokens=args.max_tokens,
            session_root=args.session_root,
            headless=not args.headed,
            fallback_to_byok=not args.no_byok_fallback,
        )
        print_json(response)
        return 0 if response.error is None else 2

    parser.error("unknown command")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
