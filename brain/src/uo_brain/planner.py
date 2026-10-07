"""The slow planner: a larger model chooses the session's goals.

The player gives the session one goal in words ("hunt the undead at the Britain graveyard,
keep yourself supplied with bandages, bank your gold"). Whenever the current goal ends, and
in any case every few minutes because hunts are time-boxed, the planner model (Claude Sonnet
5.5 through OpenRouter) is shown the character's situation in words, the session goal and
what happened so far, and answers by calling one tool: a goal for code to carry out
(travel_to, hunt, bank, buy, sell, rest, set_strategy, finish), or a world-store query first.
There are no scripted routes: the planner puts the loop together itself. Jev keeps the
tactical decisions inside each goal.

Each planning step rebuilds the conversation from a fixed, cached system prompt plus a short
record of the session, instead of growing one transcript for hours, so a call stays a few
thousand tokens. Tokens and cost of every call go to the JSONL log.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import llm, world as worlds
from .facts import Ranker
from .session import Result, Session, describe

SYSTEM = """You plan a character's session in the game Ultima Online. The player gave you a goal in
their own words. You choose what the character does next, one goal at a time, by calling
exactly one tool. Code carries the goal out and fights, heals and loots on its own, so you
only decide where to go and what to do there; you never control single steps or attacks.

How a session usually goes, which you should arrange yourself as the player's goal needs:
buy supplies (bandages from a healer, reagents from a mage shop), travel to a hunting area,
hunt for a while, come back when supplies run low or the bag gets heavy, bank gold and loot,
restock, and go again. Before hunting, make sure there are enough supplies: a warrior wants
at least 50 bandages, a mage at least 30 of each reagent, an archer at least 50 bandages and
150 arrows or bolts for the bow in hand (a bowyer or a provisioner sells them), a tamer at least
80 bandages, for itself and its pet. A tamer whose pet has died can't hunt: finish the goal and say so. Selling loot is optional; banking
it is enough. Vendors take gold from the backpack, not the bank: buy before banking, or keep
some back when banking (withdraw 'gold:200').

Rules:
- Answer with one tool call. Give `why` in a short sentence; the player sees it.
- Use the world tools (place, find_place, hunting_spots, what_spawns, route, notes, outcomes)
  when you need a fact, rather than guessing coordinates. Places can be named loosely ("Britain
  bank"). outcomes says how earlier hunts in an area went for this character.
- Give notes, hunting_spots and what_spawns a `question` (what you want to find out): their
  results then come back best answer first.
- A hunt is time-boxed (`minutes`, at most 20) and also ends early when supplies run low,
  the bag gets heavy, the character is in danger, or nothing has shown up for a while. Its
  result says why it ended; plan the next goal from that.
- If a goal fails, read why and try something different (another vendor, another area,
  waiting for a respawn) rather than repeating the same thing.
- If the character is dead, or the player's goal is done or impossible, call finish.
"""


def goal_tools() -> list[dict[str, Any]]:
    def fn(name: str, description: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
        props = {**props, "why": {"type": "string", "description": "One short sentence for the player: why this goal now."}}
        return {"type": "function", "function": {"name": name, "description": description,
                                                 "parameters": {"type": "object", "properties": props,
                                                                "required": [*required, "why"]}}}

    s = {"type": "string"}
    return [
        fn("travel_to", "Walk to a named place (a town, a bank, a graveyard, a vendor) or an x,y tile. "
           "Fights what attacks on the way. Returns when there or stuck.",
           {"place": {**s, "description": "A place name, or 'x,y'."}}, ["place"]),
        fn("hunt", "Go to a hunting area (walking there first if needed) and fight there for a while. "
           "Returns kills, supplies used and why it stopped.",
           {"area": {**s, "description": "A spawn area or place name, e.g. 'Britain Graveyard'."},
            "minutes": {"type": "number", "description": "How long at most, 3 to 20."}}, ["area", "minutes"]),
        fn("bank", "At the nearest bank (walking there first): deposit gold and/or loot, withdraw supplies.",
           {"deposit": {**s, "description": "Comma list from gold, loot, all; '' for none."},
            "withdraw": {**s, "description": "Comma list of item:count, e.g. 'bandage:100' or 'gold:200'; '' for none."}}, []),
        fn("buy", "Buy an item from the nearest vendor that sells it (walking there first).",
           {"item": {**s, "description": "e.g. bandage, black pearl, heal potion."},
            "count": {"type": "integer"},
            "vendor_kind": {**s, "description": "Optional vendor kind, e.g. healer, mage_shop."}}, ["item", "count"]),
        fn("sell", "Sell loot (or named items) to the nearest vendor of a kind (walking there first).",
           {"items": {**s, "description": "'loot' or a comma list of item words."},
            "vendor_kind": {**s, "description": "e.g. weaponsmith, armourer, jeweler."}}, ["vendor_kind"]),
        fn("rest", "Stay put to recover health and mana, or wait for monsters to respawn.",
           {"seconds": {"type": "number"}}, ["seconds"]),
        fn("set_strategy", "Replace the character's fighting strategy, in plain words (how to fight, when to flee, "
           "what to loot). Jev follows it inside fights.", {"text": s}, ["text"]),
        fn("finish", "End the session: the goal is done or impossible, or the character is dead.",
           {"summary": s}, ["summary"]),
    ]


GOALS = {"travel_to", "hunt", "bank", "buy", "sell", "rest", "set_strategy", "finish"}


@dataclass
class PlanStep:
    tool: str
    args: dict[str, Any]
    result: dict[str, Any]
    t: float = field(default_factory=time.time)

    def line(self) -> str:
        args = ", ".join(f"{k}={v!r}" for k, v in self.args.items() if k != "why")
        return f"{self.tool}({args}) -> {json.dumps(self.result, separators=(',', ':'))[:400]}"


class Planner:
    def __init__(self, session: Session, goal: str, model: str | None = None,
                 chat_fn: llm.ChatFn = llm.chat, log: Callable[[dict[str, Any]], None] | None = None,
                 max_queries: int = 6, on_goal: Callable[[str, str, str], Any] | None = None):
        self.session = session
        self.goal = goal
        self.model = llm.planner_model(model)
        self.chat = chat_fn
        self.log = log or (lambda _: None)
        self.max_queries = max_queries
        self.on_goal = on_goal  # (goal, step, why) for the in-game panel
        self.history: list[PlanStep] = []
        self.usage = llm.LlmUsage(calls=0)
        self.finished: str | None = None
        self.started = time.monotonic()
        self.tools = goal_tools() + worlds.tool_schemas()
        # Jev re-ranks list answers against the planner's question (facts.py); the rule judge doesn't.
        self.ranker = Ranker(getattr(session, "judge", None), log=self.log)

    def messages(self, situation: dict[str, Any]) -> list[dict[str, Any]]:
        recent = self.history[-12:]
        record = "\n".join(f"{i + 1}. {s.line()}" for i, s in enumerate(recent)) or "(nothing yet)"
        earlier = len(self.history) - len(recent)
        if earlier > 0:
            record = f"({earlier} earlier goals not shown)\n" + record
        user = (f"The player's goal: {self.goal}\n\n"
                f"What has happened so far, oldest first:\n{record}\n\n"
                f"The character now: {json.dumps(situation)}\n\n"
                "Choose the next goal by calling one tool.")
        return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]

    async def step(self) -> PlanStep | None:
        """One planning decision, with any world queries it needs, then the goal carried out."""
        snap = await self.session.snap()
        situation = describe(snap, self.session.world)
        msgs = self.messages(situation)
        for _ in range(self.max_queries + 1):
            res = await self.chat(msgs, tools=self.tools, tool_choice="auto", model=self.model, max_tokens=600)
            self.usage = self.usage + res.usage
            self.log({"type": "planner", "t": time.time(), "usage": res.usage.to_log(),
                      "content": (res.content or "")[:500], "calls": [{"name": c.name, "args": c.arguments} for c in res.tool_calls]})
            if not res.tool_calls:
                msgs += [res.message, {"role": "user", "content": "Answer by calling exactly one tool."}]
                continue
            call = res.tool_calls[0]
            if call.name not in GOALS:
                answer = await self.ranker.call_tool(self.session.world, call.name, call.arguments, goal=self.goal,
                                                     context=res.content or "")
                msgs += [{**res.message, "tool_calls": res.message.get("tool_calls", [])[:1]},
                         llm.tool_result(call, answer)]
                continue
            return await self.carry_out(call.name, call.arguments)
        return await self.carry_out("rest", {"seconds": 30, "why": "the planner gave no goal"})

    async def carry_out(self, tool: str, args: dict[str, Any]) -> PlanStep:
        why = str(args.get("why", ""))
        if self.on_goal:
            await self.on_goal(self.goal, describe_goal(tool, args), why)
        self.log({"type": "goal", "t": time.time(), "tool": tool, "args": args})
        s = self.session
        try:
            match tool:
                case "travel_to":
                    place = str(args.get("place", ""))
                    xy = place.split(",")
                    if len(xy) == 2 and all(v.strip().lstrip("-").isdigit() for v in xy):
                        r = await s.defended(s.travel_to(x=int(xy[0]), y=int(xy[1])))
                    else:
                        r = await s.defended(s.travel_to(place))
                case "hunt":
                    r = await s.hunt(str(args.get("area", "")), max(3.0, min(20.0, float(args.get("minutes", 10)))))
                case "bank":
                    r = await s.defended(s.bank(str(args.get("deposit", "gold,loot")), str(args.get("withdraw", ""))))
                case "buy":
                    r = await s.defended(s.buy(str(args.get("item", "")), int(args.get("count", 1)),
                                               args.get("vendor_kind") or None))
                case "sell":
                    r = await s.defended(s.sell(str(args.get("items", "loot")), str(args.get("vendor_kind", "weaponsmith"))))
                case "rest":
                    r = await s.defended(s.rest(max(5.0, min(300.0, float(args.get("seconds", 30))))))
                case "set_strategy":
                    await s.rpc.call("strategy", text=str(args.get("text", "")))
                    r = Result(True, "strategy replaced")
                case _:
                    self.finished = str(args.get("summary", "finished"))
                    r = Result(True, self.finished)
        except Exception as e:  # a goal that crashed is reported to the planner like a failure
            r = Result(False, f"error: {type(e).__name__}: {e}"[:300])
        step = PlanStep(tool, args, r.to_tool())
        self.history.append(step)
        self.log({"type": "goal_result", "t": time.time(), "tool": tool, "result": step.result})
        return step

    async def run(self, hours: float = 1.0, stop: Any = None) -> dict[str, Any]:
        began = time.monotonic()
        while not self.finished and time.monotonic() - began < hours * 3600:
            if stop is not None and stop.is_set():
                break
            snap = await self.session.snap()
            if snap.get("in_game") and snap["player"]["dead"]:
                self.finished = "the character died"
                break
            await self.step()
        return self.summary(time.monotonic() - began)

    def summary(self, seconds: float | None = None) -> dict[str, Any]:
        seconds = time.monotonic() - self.started if seconds is None else seconds
        hours = max(seconds / 3600, 1e-9)
        hunts = [s for s in self.history if s.tool == "hunt"]
        return {
            "minutes": round(seconds / 60, 1),
            "goals": len(self.history),
            "goals_by_kind": {k: sum(1 for s in self.history if s.tool == k) for k in sorted(GOALS)},
            "kills": sum(s.result.get("kills", 0) for s in hunts),
            "deaths": sum(s.result.get("deaths", 0) for s in hunts),
            "failed_goals": sum(1 for s in self.history if not s.result.get("ok")),
            "finished": self.finished,
            "planner_calls": self.usage.calls,
            "planner_cost_usd": round(self.usage.cost or 0.0, 4),
            "planner_cost_per_hour": round((self.usage.cost or 0.0) / hours, 4),
            "reranked_queries": self.ranker.calls,
            "rerank_cost_usd": round(self.ranker.cost, 5),
        }


def describe_goal(tool: str, args: dict[str, Any]) -> str:
    match tool:
        case "travel_to":
            return f"travelling to {args.get('place')}"
        case "hunt":
            return f"hunting at {args.get('area')} for up to {args.get('minutes')} min"
        case "bank":
            parts = [p for p in (f"deposit {args.get('deposit')}" if args.get("deposit") else "",
                                 f"withdraw {args.get('withdraw')}" if args.get("withdraw") else "") if p]
            return "banking: " + (", ".join(parts) or "looking")
        case "buy":
            return f"buying {args.get('count')} {args.get('item')}"
        case "sell":
            return f"selling {args.get('items', 'loot')}"
        case "rest":
            return f"resting {args.get('seconds')} s"
        case "set_strategy":
            return "changing the strategy"
    return "finishing"
