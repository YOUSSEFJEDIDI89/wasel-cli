"""Provider registry + a small OpenAI-compatible HTTP client.

Every provider below speaks the ``/chat/completions`` dialect, so the agent
core only ever deals with one wire format. All of them have a real free tier
(``Pollinations`` even needs no key at all).
"""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .config import load_keys

USER_AGENT = "wasel-cli/1.0 (+https://github.com/YOUSSEFJEDIDI89)"


@dataclass
class Provider:
    id: str
    name: str
    base_url: str
    env_key: Optional[str] = None
    default_model: str = ""
    models: List[str] = field(default_factory=list)
    context_limit: int = 32768
    needs_key: bool = True
    free: bool = True
    signup: str = ""
    notes: str = ""
    extra_headers: Dict[str, str] = field(default_factory=dict)
    supports_tools: bool = True


PROVIDERS: Dict[str, Provider] = {
    "pollinations": Provider(
        id="pollinations",
        name="Pollinations (no key needed)",
        base_url="https://text.pollinations.ai/openai",
        env_key=None,
        default_model="openai-fast",
        models=["openai-fast"],
        context_limit=32768,
        needs_key=False,
        signup="none — works out of the box",
        notes="Zero-config fallback. Anonymous tier, tools + streaming supported.",
    ),
    "gemini": Provider(
        id="gemini",
        name="Google Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        env_key="GEMINI_API_KEY",
        default_model="gemini-2.5-flash",
        models=["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-3-flash-preview"],
        context_limit=1048576,
        signup="https://aistudio.google.com/app/apikey",
        notes="Biggest free context window on the free tier.",
    ),
    "groq": Provider(
        id="groq",
        name="Groq",
        base_url="https://api.groq.com/openai/v1",
        env_key="GROQ_API_KEY",
        default_model="openai/gpt-oss-120b",
        models=["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen3-27b"],
        context_limit=131072,
        signup="https://console.groq.com/keys",
        notes="Fastest free inference (LPU).",
    ),
    "openrouter": Provider(
        id="openrouter",
        name="OpenRouter (free models)",
        base_url="https://openrouter.ai/api/v1",
        env_key="OPENROUTER_API_KEY",
        default_model="openrouter/free",
        models=["openrouter/free", "google/gemma-4-31b-it:free", "qwen/qwen3-235b-a22b:free"],
        context_limit=65536,
        signup="https://openrouter.ai/keys",
        notes="Any model id ending in :free costs nothing.",
    ),
    "cerebras": Provider(
        id="cerebras",
        name="Cerebras",
        base_url="https://api.cerebras.ai/v1",
        env_key="CEREBRAS_API_KEY",
        default_model="gpt-oss-120b",
        models=["gpt-oss-120b", "qwen3-235b-a22b-instruct-2507"],
        context_limit=65536,
        signup="https://cloud.cerebras.ai",
        notes="Very fast; free tier depends on account credits.",
    ),
    "mistral": Provider(
        id="mistral",
        name="Mistral",
        base_url="https://api.mistral.ai/v1",
        env_key="MISTRAL_API_KEY",
        default_model="mistral-small-latest",
        models=["mistral-small-latest", "codestral-latest"],
        context_limit=32768,
        signup="https://console.mistral.ai",
        notes="Free experiment tier.",
    ),
    "nvidia": Provider(
        id="nvidia",
        name="NVIDIA NIM",
        base_url="https://integrate.api.nvidia.com/v1",
        env_key="NVIDIA_API_KEY",
        default_model="qwen/qwen3-coder-480b-a35b-instruct",
        models=["qwen/qwen3-coder-480b-a35b-instruct", "meta/llama-3.3-70b-instruct"],
        context_limit=131072,
        signup="https://build.nvidia.com",
        notes="Wide free model catalogue.",
    ),
    "ollama": Provider(
        id="ollama",
        name="Ollama (local, offline)",
        base_url="http://127.0.0.1:11434/v1",
        env_key=None,
        default_model="llama3.2",
        models=["llama3.2", "qwen2.5-coder", "gemma3"],
        context_limit=32768,
        needs_key=False,
        signup="https://ollama.com",
        notes="Runs 100% on-device — good for Termux with llama.cpp/Ollama.",
    ),
    "custom": Provider(
        id="custom",
        name="Custom OpenAI-compatible endpoint",
        base_url="",
        env_key="CUSTOM_API_KEY",
        default_model="",
        models=[],
        context_limit=32768,
        signup="set CUSTOM_BASE_URL / CUSTOM_MODEL",
        notes="Any server that implements /v1/chat/completions.",
    ),
}


class ProviderError(Exception):
    """A provider failed in a way that lets us try the next one."""

    def __init__(self, message: str, kind: str = "error", status: int = 0) -> None:
        super().__init__(message)
        self.kind = kind  # auth | ratelimit | server | network | badrequest
        self.status = status


class Cooldown:
    """Remember providers that just failed so we don't hammer them."""

    def __init__(self) -> None:
        self.until: Dict[str, float] = {}

    def fail(self, pid: str, seconds: float) -> None:
        self.until[pid] = time.time() + seconds

    def ok(self, pid: str) -> bool:
        return self.until.get(pid, 0) <= time.time()

    def remaining(self, pid: str) -> float:
        return max(0.0, self.until.get(pid, 0) - time.time())


def provider_key(pid: str) -> Optional[str]:
    """API key for a provider: env var first, then ~/.wasel/keys.json."""
    prov = PROVIDERS.get(pid)
    if not prov:
        return None
    if prov.env_key and os.environ.get(prov.env_key):
        return os.environ[prov.env_key].strip()
    stored = load_keys().get(pid)
    return stored.strip() if stored else None


def provider_ready(pid: str, cfg: Dict[str, Any]) -> bool:
    """True when the provider could actually answer a request right now."""
    prov = PROVIDERS.get(pid)
    if prov is None:
        return False
    if pid == "custom":
        base = (cfg.get("custom_base_url") or os.environ.get("CUSTOM_BASE_URL") or "").rstrip("/")
        model = cfg.get("custom_model") or os.environ.get("CUSTOM_MODEL") or ""
        if not base or not model:
            return False
        prov.base_url = base
        prov.default_model = model
        prov.models = [model]
        return True
    if not prov.needs_key:
        return True
    return bool(provider_key(pid))


def _post(url: str, payload: Dict[str, Any], headers: Dict[str, str], timeout: int):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", USER_AGENT)
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:  # noqa: PERF203
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:800]
        except Exception:  # pragma: no cover
            pass
        message = _clean_error(detail) or str(exc.reason)
        raise ProviderError(message, _kind_for(exc.code, message), exc.code) from None
    except urllib.error.URLError as exc:
        raise ProviderError(f"network error: {exc.reason}", "network") from None
    except TimeoutError:
        raise ProviderError("request timed out", "network") from None


RATE_HINTS = (
    "still running",
    "rate limit",
    "too many requests",
    "quota",
    "try again later",
    "one request at a time",
    "concurrent",
)


def _kind_for(code: int, message: str = "") -> str:
    low = (message or "").lower()
    if code in (401, 403):
        return "auth"
    if code == 429:
        return "ratelimit"
    if code >= 500:
        return "server"
    # Some gateways answer 400 for what is really a temporary quota/queue state.
    if any(hint in low for hint in RATE_HINTS):
        return "ratelimit"
    return "badrequest"


def _clean_error(detail: str) -> str:
    try:
        data = json.loads(detail)
        err = data.get("error", data)
        if isinstance(err, dict):
            return str(err.get("message") or err.get("type") or err)[:400]
        return str(err)[:400]
    except Exception:
        return detail.strip()[:400]


def _sse_lines(resp) -> List[str]:
    out: List[str] = []
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if line.startswith("data:"):
            out.append(line[5:].strip())
    return out


def _merge_tool_call(acc: Dict[int, Dict[str, Any]], frag: Dict[str, Any]) -> None:
    idx = frag.get("index", 0)
    slot = acc.setdefault(idx, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
    if frag.get("id"):
        slot["id"] = frag["id"]
    fn = frag.get("function") or {}
    if fn.get("name"):
        slot["function"]["name"] = (slot["function"]["name"] or "") + fn["name"]
    if fn.get("arguments"):
        slot["function"]["arguments"] = (slot["function"]["arguments"] or "") + fn["arguments"]


def chat(
    pid: str,
    model: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    stream: bool = True,
    on_delta=None,
    on_reason=None,
    timeout: int = 180,
    extra_body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Call a provider and return an assistant message dict."""
    prov = PROVIDERS[pid]
    url = prov.base_url.rstrip("/") + "/chat/completions"
    headers = dict(prov.extra_headers)
    key = provider_key(pid)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if pid == "openrouter":
        headers.setdefault("HTTP-Referer", "https://github.com/YOUSSEFJEDIDI89/wasel-cli")
        headers.setdefault("X-Title", "Wasel CLI")

    payload: Dict[str, Any] = {"model": model, "messages": messages}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if extra_body:
        payload.update(extra_body)
    if stream:
        payload["stream"] = True

    resp = _post(url, payload, headers, timeout)
    if not stream:
        data = json.loads(resp.read().decode("utf-8", "replace"))
        return _from_payload(data)

    content: List[str] = []
    reasoning: List[str] = []
    calls: Dict[int, Dict[str, Any]] = {}
    try:
        for chunk in _sse_lines(resp):
            if chunk == "[DONE]":
                break
            try:
                obj = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            for choice in obj.get("choices", []):
                delta = choice.get("delta") or choice.get("message") or {}
                if delta.get("content"):
                    content.append(delta["content"])
                    if on_delta:
                        on_delta(delta["content"])
                if delta.get("reasoning"):
                    reasoning.append(delta["reasoning"])
                    if on_reason:
                        on_reason(delta["reasoning"])
                for frag in delta.get("tool_calls") or []:
                    _merge_tool_call(calls, frag)
    except Exception as exc:  # broken stream → retry once without streaming
        if on_delta:
            on_delta("")
        return chat(pid, model, messages, tools, False, timeout=timeout, extra_body=extra_body)

    msg: Dict[str, Any] = {"role": "assistant", "content": "".join(content)}
    if reasoning:
        msg["reasoning"] = "".join(reasoning)
    if calls:
        msg["tool_calls"] = [calls[k] for k in sorted(calls)]
    if not content and not calls:
        # Some gateways answer 200 with an empty body over SSE.
        return chat(pid, model, messages, tools, False, timeout=timeout, extra_body=extra_body)
    return msg


def _from_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    choices = data.get("choices") or []
    if not choices:
        raise ProviderError(_clean_error(json.dumps(data)[:300]) or "empty response", "badrequest")
    msg = choices[0].get("message") or {}
    out: Dict[str, Any] = {"role": "assistant", "content": msg.get("content") or ""}
    if msg.get("reasoning"):
        out["reasoning"] = msg["reasoning"]
    if msg.get("tool_calls"):
        out["tool_calls"] = [
            {
                "id": tc.get("id") or f"call_{i}",
                "type": "function",
                "function": {
                    "name": tc.get("function", {}).get("name", ""),
                    "arguments": tc.get("function", {}).get("arguments", "") or "{}",
                },
            }
            for i, tc in enumerate(msg["tool_calls"])
        ]
    return out


def list_models(pid: str) -> Tuple[bool, List[str]]:
    """Ask the provider which models it currently serves."""
    prov = PROVIDERS.get(pid)
    if prov is None:
        return False, []
    key = provider_key(pid)
    if prov.needs_key and not key:
        return False, []
    url = prov.base_url.rstrip("/") + "/models"
    headers = {"User-Agent": USER_AGENT}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        req = urllib.request.Request(url, method="GET")
        for k, v in headers.items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        ids = [m.get("id") for m in data.get("data", []) if m.get("id")]
        return True, ids
    except Exception:
        return False, []


def backoff_delay(attempt: int) -> float:
    return min(8.0, (2**attempt) * 0.6) + random.random() * 0.4
