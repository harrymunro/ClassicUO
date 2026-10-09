"""What every AI call costs (cuo-m70.1).

Every call to a model goes through one of two places: `judge.JevJudge.ask` (system one: Jev's
typed questions) and `llm.chat` (system two: the planner model's chat). Both record a `Call`
here, in the process's one `ledger`, with:

- its kind, from the `kind()` context its caller sets: fight, routine, facts, rerank,
  strategy, recorder or replay for system one; planner, review, guide or design for system two;
- the model, input, output and cached tokens, and latency;
- its cost in US dollars: the provider's own figure where it reports one (OpenRouter does, for
  Jev and the planner alike), else an estimate from the price table below (`estimated` is then
  true).

The ledger keeps totals by kind and by model, writes one `{"type": "ai_cost", ...}` record per
call to whichever log is current (`logging_to`), and answers the questions people ask of it:
what has this run cost, per hour, per kill, per decision, by role. A budget (a cost-per-hour
cap) can be set on it; the loop and the planner ask `over_budget()` and slow down, switch to
cheaper models or stop, as the budget says.

One brain process plays one character, so the process's ledger is that character's.
"""

import contextvars
import json
import os
import time
import urllib.request
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SYSTEM_ONE = ("fight", "routine", "facts", "rerank", "strategy", "recorder", "replay")
SYSTEM_TWO = ("planner", "review", "guide", "design")

# Dollars per million tokens: (input, output, cached input). Used only when the provider
# doesn't report a call's cost (TypeSafe direct, or a model OpenRouter didn't price). The
# OpenRouter figures are its list prices on 2026-10-08; Jev's output tokens are free.
PRICES: dict[str, tuple[float, float, float]] = {
    "typesafe/jev": (0.042, 0.0, 0.042),
    "jev": (0.042, 0.0, 0.042),
    "anthropic/claude-sonnet-5.5": (2.0, 10.0, 0.2),
    "anthropic/claude-haiku-5.5": (0.10, 0.50, 0.01),
    "anthropic/claude-opus-5.5": (4.0, 20.0, 0.4),
}
PRICES_FILE = Path(__file__).resolve().parents[2] / "prices.json"  # `uo-brain costs prices` writes it

_KIND: contextvars.ContextVar[str] = contextvars.ContextVar("ai_kind", default="")
# Where a call's cost record is written: per task, so the planner thinking in one task and the fight
# loop defending in another (Session.defended) each write to their own log.
_LOG: contextvars.ContextVar[Callable[[dict[str, Any]], None] | None] = contextvars.ContextVar("ai_log", default=None)


@contextmanager
def kind(name: str) -> Iterator[None]:
    """The kind of AI call made inside this block (and in tasks started from it)."""
    token = _KIND.set(name)
    try:
        yield
    finally:
        _KIND.reset(token)


def current_kind(default: str = "") -> str:
    return _KIND.get() or default


def role_of(k: str) -> str:
    return "system2" if k in SYSTEM_TWO else "system1"


_price_cache: dict[str, tuple[float, float, float]] | None = None


def price_of(model: str) -> tuple[float, float, float] | None:
    """Dollars per million (input, output, cached input) for a model, or None when unknown.
    Jev's dated names (typesafe/jev-1.13-20260917) match "typesafe/jev"."""
    global _price_cache
    if _price_cache is None:
        _price_cache = {}
        try:
            for k, v in json.loads(PRICES_FILE.read_text()).items():
                _price_cache[k] = (float(v[0]), float(v[1]), float(v[2]))
        except (OSError, ValueError, TypeError, IndexError):
            pass
    m = model.removeprefix("~")
    for table in (_price_cache, PRICES):
        if m in table:
            return table[m]
    for prefix, p in PRICES.items():
        if m.startswith(prefix) or m.split("/")[-1].startswith(prefix.split("/")[-1] + "-"):
            return p
    return None


def estimate(model: str, input_tokens: int, output_tokens: int = 0, cached_tokens: int = 0) -> float | None:
    p = price_of(model)
    if p is None:
        return None
    fresh = max(0, input_tokens - cached_tokens)
    return (fresh * p[0] + cached_tokens * p[2] + output_tokens * p[1]) / 1e6


@dataclass
class Call:
    """One AI call. `cost` in US dollars; `estimated` when it came from the price table."""

    kind: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    latency_ms: float = 0.0
    cost: float = 0.0
    estimated: bool = False
    t: float = field(default_factory=time.time)
    character: str = ""

    @property
    def role(self) -> str:
        return role_of(self.kind)

    def to_log(self) -> dict[str, Any]:
        return {"type": "ai_cost", "t": round(self.t, 3), "kind": self.kind, "role": self.role, "model": self.model,
                "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "cached_tokens": self.cached_tokens, "latency_ms": round(self.latency_ms, 1),
                "cost": round(self.cost, 7), **({"estimated": True} if self.estimated else {}),
                **({"character": self.character} if self.character else {})}


def make_call(k: str, model: str, input_tokens: int, output_tokens: int = 0, cached_tokens: int = 0,
              latency_ms: float = 0.0, reported: float | None = None) -> Call:
    """A call's record, with the provider's cost if it reported one, else the table's estimate
    (0 for a model the table doesn't know, still marked estimated)."""
    if reported is not None:
        return Call(k, model, input_tokens, output_tokens, cached_tokens, latency_ms, float(reported))
    est = estimate(model, input_tokens, output_tokens, cached_tokens)
    return Call(k, model, input_tokens, output_tokens, cached_tokens, latency_ms, est or 0.0, True)


@dataclass
class Total:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    latency_ms: float = 0.0
    estimated: int = 0  # calls whose cost was estimated

    def add(self, c: Call) -> None:
        self.calls += 1
        self.input_tokens += c.input_tokens
        self.output_tokens += c.output_tokens
        self.cost += c.cost
        self.latency_ms += c.latency_ms
        self.estimated += c.estimated

    def minus(self, other: "Total") -> "Total":
        return Total(self.calls - other.calls, self.input_tokens - other.input_tokens,
                     self.output_tokens - other.output_tokens, self.cost - other.cost,
                     self.latency_ms - other.latency_ms, self.estimated - other.estimated)

    def copy(self) -> "Total":
        return Total(self.calls, self.input_tokens, self.output_tokens, self.cost, self.latency_ms, self.estimated)

    def to_log(self) -> dict[str, Any]:
        out = {"calls": self.calls, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
               "cost_usd": round(self.cost, 6),
               "latency_ms_avg": round(self.latency_ms / self.calls, 1) if self.calls else None}
        if self.estimated:
            out["estimated_calls"] = self.estimated
        return out


@dataclass
class Budget:
    """A cap on what a session spends an hour, and what to do when it's reached: "slow" (decide
    less often), "cheaper" (switch to the profile's cheaper models) or "stop" (end the session)."""

    per_hour: float
    action: str = "slow"
    window_s: float = 600.0     # spending is judged over the last ten minutes...
    min_span_s: float = 300.0   # ...as an hourly rate, never over less than five (one early planner call isn't a trend)

    def __post_init__(self) -> None:
        if self.action not in ("slow", "cheaper", "stop"):
            raise ValueError(f"budget action should be slow, cheaper or stop, not {self.action!r}")


@dataclass
class Mark:
    """The ledger's totals at one moment, to tell what a run (a bench round, a hunt) cost since."""

    t: float
    by_kind: dict[str, Total]
    by_model: dict[str, Total]


class Ledger:
    def __init__(self) -> None:
        self.started = time.time()
        self.by_kind: dict[str, Total] = {}
        self.by_model: dict[str, Total] = {}
        self.recent: deque[tuple[float, float]] = deque()  # (t, cost) over the last hour, for the budget
        self.budget: Budget | None = None
        self.character = ""  # who the calls are for: set from the snapshot, written on each record
        self.on_call: list[Callable[[Call], None]] = []

    # ------------------------------------------------------------ recording

    def record(self, c: Call) -> Call:
        c.character = c.character or self.character
        self.by_kind.setdefault(c.kind, Total()).add(c)
        self.by_model.setdefault(c.model, Total()).add(c)
        self.recent.append((c.t, c.cost))
        while self.recent and self.recent[0][0] < c.t - 3600:
            self.recent.popleft()
        if (write := _LOG.get()) is not None:
            try:
                write(c.to_log())
            except (OSError, ValueError):
                pass  # a closed log: the totals still count it
        for f in self.on_call:
            f(c)
        return c

    @contextmanager
    def logging_to(self, write: Callable[[dict[str, Any]], None] | None) -> Iterator[None]:
        """Cost records of the calls made inside this block, and in tasks started from it, go to
        `write` (the innermost wins; another task keeps its own)."""
        if write is None:
            yield
            return
        token = _LOG.set(write)
        try:
            yield
        finally:
            _LOG.reset(token)

    def reset(self) -> None:
        self.__init__()

    # ------------------------------------------------------------ totals

    def total(self) -> Total:
        out = Total()
        for t in self.by_kind.values():
            out = Total(out.calls + t.calls, out.input_tokens + t.input_tokens, out.output_tokens + t.output_tokens,
                        out.cost + t.cost, out.latency_ms + t.latency_ms, out.estimated + t.estimated)
        return out

    @property
    def spent(self) -> float:
        return sum(t.cost for t in self.by_kind.values())

    def mark(self) -> Mark:
        return Mark(time.time(), {k: v.copy() for k, v in self.by_kind.items()},
                    {k: v.copy() for k, v in self.by_model.items()})

    def since(self, mark: Mark | None = None) -> Mark:
        """What was spent since `mark` (everything, without one), as a Mark of differences."""
        if mark is None:
            return Mark(self.started, {k: v.copy() for k, v in self.by_kind.items()},
                        {k: v.copy() for k, v in self.by_model.items()})

        def diff(now: dict[str, Total], then: dict[str, Total]) -> dict[str, Total]:
            out = {k: v.minus(then.get(k, Total())) for k, v in now.items()}
            return {k: v for k, v in out.items() if v.calls}

        return Mark(mark.t, diff(self.by_kind, mark.by_kind), diff(self.by_model, mark.by_model))

    def summary(self, mark: Mark | None = None, kills: int | None = None, decisions: int | None = None,
                hours: float | None = None) -> dict[str, Any]:
        """Cost since `mark` (or the ledger's start): total, per hour, per kill, per decision, and the
        split by role, kind and model."""
        d = self.since(mark)
        hours = hours if hours is not None else max((time.time() - d.t) / 3600, 1e-9)
        return summarise(d.by_kind, d.by_model, hours, kills, decisions)

    def live(self) -> dict[str, Any]:
        """The running totals the client's calls window shows."""
        hours = max((time.time() - self.started) / 3600, 1e-9)
        out: dict[str, Any] = {"total": round(self.spent, 5), "per_hour": round(self.spent / hours, 4),
                               "calls": sum(t.calls for t in self.by_kind.values()),
                               "by_kind": {k: round(t.cost, 5) for k, t in sorted(self.by_kind.items(),
                                                                                  key=lambda kv: -kv[1].cost)}}
        if self.budget:
            out["budget_per_hour"] = self.budget.per_hour
            out["over_budget"] = self.over_budget()
        return out

    # ------------------------------------------------------------ budget

    def rate(self, now: float | None = None) -> float:
        """Spending as dollars an hour over the budget's window (ten minutes by default), never
        measured over less than its minimum span."""
        b = self.budget or Budget(0.0)
        now = now or time.time()
        spent = sum(c for t, c in self.recent if t >= now - b.window_s)
        span = min(b.window_s, max(b.min_span_s, now - self.started))
        return spent / (span / 3600)

    def over_budget(self, now: float | None = None) -> bool:
        return self.budget is not None and self.budget.per_hour >= 0 and self.rate(now) > self.budget.per_hour


def summarise(by_kind: dict[str, Total], by_model: dict[str, Total], hours: float, kills: int | None = None,
              decisions: int | None = None) -> dict[str, Any]:
    total = sum(t.cost for t in by_kind.values())
    roles: dict[str, float] = {}
    for k, t in by_kind.items():
        roles[role_of(k)] = roles.get(role_of(k), 0.0) + t.cost
    fights = by_kind.get("fight")
    out: dict[str, Any] = {
        "cost_usd": round(total, 6),
        "cost_per_hour_usd": round(total / hours, 4) if hours > 0 else None,
        "calls": sum(t.calls for t in by_kind.values()),
        "by_role": {r: round(c, 6) for r, c in sorted(roles.items())},
        "by_kind": {k: t.to_log() for k, t in sorted(by_kind.items(), key=lambda kv: -kv[1].cost)},
        "by_model": {m: t.to_log() for m, t in sorted(by_model.items(), key=lambda kv: -kv[1].cost)},
    }
    decisions = decisions if decisions is not None else (fights.calls if fights else None)
    if decisions:
        out["cost_per_decision_usd"] = round(total / decisions, 7)
    if kills:
        out["cost_per_kill_usd"] = round(total / kills, 5)
    if (est := sum(t.estimated for t in by_kind.values())):
        out["estimated_calls"] = est
    return out


def from_records(records: list[dict[str, Any]], hours: float | None = None, kills: int | None = None,
                 decisions: int | None = None) -> dict[str, Any] | None:
    """The same summary from the `ai_cost` records of one or more logs (None when there are none:
    a log from before costs were recorded)."""
    recs = [r for r in records if r.get("type") == "ai_cost"]
    if not recs:
        return None
    by_kind: dict[str, Total] = {}
    by_model: dict[str, Total] = {}
    by_character: dict[str, Total] = {}
    for r in recs:
        c = Call(r.get("kind", "?"), r.get("model", "?"), r.get("input_tokens", 0), r.get("output_tokens", 0),
                 r.get("cached_tokens", 0), r.get("latency_ms", 0.0), r.get("cost", 0.0), bool(r.get("estimated")),
                 r.get("t", 0.0), r.get("character", ""))
        by_kind.setdefault(c.kind, Total()).add(c)
        by_model.setdefault(c.model, Total()).add(c)
        by_character.setdefault(c.character or "?", Total()).add(c)
    if hours is None:
        ts = [r.get("t", 0.0) for r in records if r.get("t")]
        hours = max((max(ts) - min(ts)) / 3600, 1e-9) if ts else 1e-9
    out = summarise(by_kind, by_model, hours, kills, decisions)
    if len(by_character) > 1:  # logs of several characters: what each cost
        out["by_character"] = {k: t.to_log() for k, t in sorted(by_character.items(), key=lambda kv: -kv[1].cost)}
    return out


def describe(s: dict[str, Any] | None) -> str:
    """A cost summary in one line."""
    if not s:
        return "no cost records"
    kinds = ", ".join(f"{k} ${t['cost_usd']:.4f} ({t['calls']})" for k, t in s["by_kind"].items())
    extra = []
    if s.get("cost_per_decision_usd"):
        extra.append(f"${s['cost_per_decision_usd']:.6f} a decision")
    if s.get("cost_per_kill_usd"):
        extra.append(f"${s['cost_per_kill_usd']:.4f} a kill")
    return (f"${s['cost_usd']:.4f} in {s['calls']} calls, ${s['cost_per_hour_usd']:.4f}/h"
            + (f", {', '.join(extra)}" if extra else "") + f"; {kinds}")


# ------------------------------------------------------------ OpenRouter's own figures

def openrouter_usage(timeout: float = 15.0) -> float | None:
    """What the OpenRouter key has spent in all (dollars), from its own account, or None. Taken
    before and after a session, the difference checks the ledger (other use of the key in between,
    such as a second bench, counts too)."""
    from .judge import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None
    req = urllib.request.Request("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return float((json.loads(r.read()).get("data") or {}).get("usage"))
    except (OSError, ValueError, TypeError):
        return None


def update_prices(timeout: float = 30.0) -> int:
    """Fetch OpenRouter's list prices for every model into prices.json; returns how many."""
    with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=timeout) as r:
        models = json.loads(r.read())["data"]
    out: dict[str, list[float]] = {}
    for m in models:
        p = m.get("pricing") or {}
        try:
            i, o = float(p["prompt"]) * 1e6, float(p["completion"]) * 1e6
        except (KeyError, TypeError, ValueError):
            continue
        if i < 0 or o < 0:
            continue  # a router whose price depends on where it sends the call
        cached = float(p.get("input_cache_read") or p["prompt"]) * 1e6
        out[m["id"]] = [round(i, 6), round(o, 6), round(cached, 6)]
    PRICES_FILE.write_text(json.dumps(out, indent=0, sort_keys=True) + "\n")
    global _price_cache
    _price_cache = None
    return len(out)


ledger = Ledger()
