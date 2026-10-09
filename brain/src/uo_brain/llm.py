"""A small OpenRouter chat client for the planner model.

Jev answers typed questions through typesafe-sdk. The planner (Claude Sonnet through
OpenRouter) needs ordinary chat with tool calling instead, so this is a second, plain
client. It uses only the standard library: urllib, run in a worker thread so the asyncio
loop that also talks to the game isn't blocked.

Some models refuse a forced tool_choice (Sonnet 5.5 on OpenRouter does: "type tool and
any are not supported for this model"). chat() then asks again with "auto" and remembers
the model, so prompts should still say which tool to call.

Two things keep it cheap to call often. The system prompt is sent with Anthropic's
cache mark (OpenRouter passes it through), so a long, fixed prompt plus tool list is
billed at the cached rate after the first call. And every call asks OpenRouter for its
cost, which comes back in LlmUsage for the caller to log.

The key is OPENROUTER_API_KEY, the same one Jev uses, read from the environment or
brain/.env. It is never printed or logged.
"""

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import costs
from .judge import load_dotenv

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
PLANNER_MODEL = "anthropic/claude-sonnet-5.5"
BRAIN_DIR = Path(__file__).resolve().parents[2]
RETRY_STATUS = (408, 429, 500, 502, 503, 504)
_AUTO_ONLY: set[str] = set()  # models seen refusing a forced tool_choice


class LlmError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class LlmUsage:
    """What one call (or a sum of calls) used. `cost` is in US dollars, as OpenRouter
    reports it; None when the response didn't say."""

    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    cost: float | None = None
    latency_ms: float = 0.0
    calls: int = 1

    def __add__(self, other: "LlmUsage") -> "LlmUsage":
        cost = None if self.cost is None and other.cost is None else (self.cost or 0.0) + (other.cost or 0.0)
        return LlmUsage(other.model or self.model, self.prompt_tokens + other.prompt_tokens,
                        self.completion_tokens + other.completion_tokens, self.cached_tokens + other.cached_tokens,
                        self.cache_write_tokens + other.cache_write_tokens, cost,
                        self.latency_ms + other.latency_ms, self.calls + other.calls)

    def to_log(self) -> dict[str, Any]:
        out = asdict(self)
        out["latency_ms"] = round(self.latency_ms, 1)
        if self.cost is not None:
            out["cost"] = round(self.cost, 6)
        return out


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str = ""


@dataclass
class ChatResult:
    message: dict[str, Any]  # the assistant turn, ready to append to the conversation
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    usage: LlmUsage = field(default_factory=LlmUsage)


# The shape callers depend on, so tests and later code can pass a fake.
ChatFn = Callable[..., Awaitable[ChatResult]]


def planner_model(model: str | None = None) -> str:
    """The model given, else the one the active profile names for the kind of call being made
    (models.py: the profile, then PLANNER_MODEL, then Sonnet)."""
    if model:
        return model
    from . import models
    return models.active.model_for(costs.current_kind("planner"))


def api_key() -> str:
    load_dotenv(BRAIN_DIR / ".env")
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise LlmError("OPENROUTER_API_KEY is not set (export it or put it in brain/.env)")
    return key


def with_cache_marks(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copy of the messages with the last part of each system message marked for
    Anthropic prompt caching. Everything before the mark (tools, then system) is cached."""
    out = []
    for m in messages:
        if m.get("role") != "system":
            out.append(m)
            continue
        content = m.get("content")
        parts = [{"type": "text", "text": content}] if isinstance(content, str) else [dict(p) for p in content or []]
        if parts:
            parts[-1] = {**parts[-1], "cache_control": {"type": "ephemeral"}}
        out.append({**m, "content": parts})
    return out


def build_request(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
                  tool_choice: str | dict[str, Any] | None = None, model: str | None = None,
                  max_tokens: int = 1024, temperature: float | None = None, cache: bool = True,
                  response_format: dict[str, Any] | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": planner_model(model),
        "messages": with_cache_marks(messages) if cache else messages,
        "max_tokens": max_tokens,
        "usage": {"include": True},
    }
    if tools:
        body["tools"] = tools
    if tool_choice is not None:
        body["tool_choice"] = ({"type": "function", "function": {"name": tool_choice}}
                               if isinstance(tool_choice, str) and tool_choice not in ("auto", "none", "required")
                               else tool_choice)
    if temperature is not None:
        body["temperature"] = temperature
    if response_format is not None:
        body["response_format"] = response_format
    return {**body, **(extra or {})}


def parse_response(data: dict[str, Any], model: str, latency_ms: float = 0.0) -> ChatResult:
    if data.get("error"):
        err = data["error"]
        raise LlmError(f"OpenRouter error: {err.get('message', err) if isinstance(err, dict) else err}",
                       err.get("code") if isinstance(err, dict) else None)
    if not data.get("choices"):
        raise LlmError("OpenRouter returned no choices")
    choice = data["choices"][0]
    msg = choice.get("message") or {}
    calls = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except ValueError:
            args = {}
        calls.append(ToolCall(tc.get("id", ""), fn.get("name", ""), args if isinstance(args, dict) else {}, str(raw)))
    u = data.get("usage") or {}
    details = u.get("prompt_tokens_details") or {}
    usage = LlmUsage(model=data.get("model") or model, prompt_tokens=u.get("prompt_tokens") or 0,
                     completion_tokens=u.get("completion_tokens") or 0,
                     cached_tokens=details.get("cached_tokens") or 0,
                     cache_write_tokens=details.get("cache_write_tokens") or 0,
                     cost=u.get("cost"), latency_ms=latency_ms)
    message: dict[str, Any] = {"role": "assistant", "content": msg.get("content")}
    if msg.get("tool_calls"):
        message["tool_calls"] = msg["tool_calls"]
    return ChatResult(message, msg.get("content"), calls, choice.get("finish_reason"), usage)


def tool_result(call: ToolCall, result: Any) -> dict[str, Any]:
    """The message that answers one tool call, for the next turn."""
    return {"role": "tool", "tool_call_id": call.id,
            "content": result if isinstance(result, str) else json.dumps(result)}


def _post(body: dict[str, Any], key: str, timeout: float, retries: int) -> dict[str, Any]:
    req = urllib.request.Request(OPENROUTER_CHAT_URL, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/harrymunro/ClassicUO", "X-Title": "uo-brain"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:1000]
            if e.code in RETRY_STATUS and attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise LlmError(f"OpenRouter HTTP {e.code}: {detail}", e.code) from None
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise LlmError(f"OpenRouter unreachable: {e}") from None
    raise LlmError("OpenRouter: out of retries")


async def chat(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
               tool_choice: str | dict[str, Any] | None = None, model: str | None = None, max_tokens: int = 1024,
               temperature: float | None = None, cache: bool = True, timeout: float = 120.0,
               retries: int = 2, response_format: dict[str, Any] | None = None,
               extra: dict[str, Any] | None = None) -> ChatResult:
    """One chat completion. `tools` are OpenAI-style function definitions; `tool_choice`
    is "auto", "none", "required", a tool name to force, or an OpenAI tool_choice dict."""
    forced = tool_choice == "required" or isinstance(tool_choice, dict) or (
        isinstance(tool_choice, str) and tool_choice not in ("auto", "none"))
    if forced and planner_model(model) in _AUTO_ONLY:
        tool_choice = "auto"
    body = build_request(messages, tools, tool_choice, model, max_tokens, temperature, cache, response_format, extra)
    key = api_key()
    started = time.perf_counter()
    try:
        data = await asyncio.to_thread(_post, body, key, timeout, retries)
    except LlmError as e:
        if not (forced and e.status == 400 and "tool_choice" in str(e)):
            raise
        _AUTO_ONLY.add(body["model"])
        data = await asyncio.to_thread(_post, {**body, "tool_choice": "auto"}, key, timeout, retries)
    res = parse_response(data, body["model"], (time.perf_counter() - started) * 1000)
    u = res.usage
    call = costs.ledger.record(costs.make_call(costs.current_kind("other"), u.model, u.prompt_tokens,
                                               u.completion_tokens, u.cached_tokens, u.latency_ms, u.cost))
    u.cost = call.cost  # the table's estimate when OpenRouter didn't say
    return res
