"""What `uo-brain run` does: fight with Jev, and in auto mode work towards the player's goal.

The client holds the play state and the goal (typed or picked in the panel). This loop
watches both:

- not in auto mode, or no goal: the tactical loop (loop.run), as before;
- auto mode with a goal: the planner (planner.py) picks session goals and code carries them
  out, with Jev still fighting inside each one.

Switching from auto to combat assist interrupts the current goal at once (the client stops
walking on its own) and pauses the planner, so it stops costing tokens; the goal is kept.
Switching back is a handover: the planner is told the player drove, and resumes from
wherever the character is now, with whatever it carries.
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from . import calls, costs, loop, policy, state
from .facts import FactPicker
from .judge import Judge
from .planner import Planner, PlanStep
from .recorder import Recorder
from .rpc import AgentRpc
from .session import Session, describe, note_threats, routine_totals
from .world import World


def death_advice(world: World, snap: dict[str, Any], advised: dict[str, Any]) -> str:
    """With the player driving (combat assist) and the character dead: the steps the planner would
    take, as advice for the panel, once per death and again when it is alive with its corpse to
    fetch (cuo-4b3). Nothing is done for the player."""
    if not snap.get("in_game") or snap["agent"].get("mode") == "auto":
        return ""
    p = snap["player"]
    death = p.get("last_death")
    if not death:
        return ""
    key = (death["x"], death["y"], death.get("resurrections"), p["dead"])
    if advised.get("key") == key:
        return ""
    advised["key"] = key
    if p["dead"]:
        healers = world.find_place("healer", near=[p["x"], p["y"]], limit=1)
        if not healers:
            return "dead: no healer known nearby; look for one or an ankh"
        h = healers[0]
        return (f"dead: the nearest healer is {h['name']}, {h.get('distance', '?')} tiles away at {h['x']},{h['y']}; "
                f"walk up to it and accept. Your corpse is at {death['x']},{death['y']}.")
    if death.get("ago_s", 1e9) < 20 * 60:
        return f"alive again: your corpse is at {death['x']},{death['y']}; take your things back before it decays."
    return ""


def wants_planner(snap: dict[str, Any]) -> bool:
    goal = snap["agent"].get("goal") or {}
    return snap["agent"].get("mode") == "auto" and bool(goal.get("text")) and not goal.get("paused")


class Autopilot:
    def __init__(self, rpc: AgentRpc, judge: Judge, world: World | None, lcfg: loop.LoopConfig,
                 pcfg: policy.PolicyConfig, log_path: Path | None, archetype: str | None = None,
                 planner_model: str | None = None, facts_mode: str = "jev", machine=None,
                 machines_allowed: bool = True, escalate: bool = True, memory: bool = True):
        self.rpc = rpc
        calls.sink = lambda rec: rpc.call("ai_call", **rec)  # the client's live view of every model call
        self.judge = judge
        self.world = world
        self.lcfg = lcfg
        self.pcfg = pcfg
        self.archetype = archetype
        self.planner_model = planner_model
        self.facts_mode = facts_mode  # which world facts reach the fights (facts.py)
        self.machine = machine        # machine.Runner: a plan for the fights, from --machine; the planner may replace it
        self.machines_allowed = machines_allowed
        self.escalate = escalate      # hard moments to the stronger model (planner.py, cuo-d4j)
        self.memory = memory          # the character's memory and long goals (memory.py, longgoals.py)
        self.log_path = log_path
        self.log_file = log_path.open("a") if log_path else None
        self.planners: dict[str, Planner] = {}  # goal text -> its planner, so a handover keeps history
        # Whatever the character sees, driven or not, goes into the world store.
        self.recorder = Recorder(world, judge) if world is not None else None
        self.driven_since: float | None = None   # when the player took over from the planner
        # Jev's routine questions inside hunts (routine.py), over every session object of a goal.
        self.routine = {"questions": 0, "input_tokens": 0, "cost_usd": 0.0, "hunt_minutes": 0.0}

    async def announce(self, snap: dict[str, Any]) -> None:
        """What the brain is running, for the panel's title row, as soon as it is connected. The
        fight loop says so at its first look, but the planner's first step and a walk from the bank
        can take a minute before that, and the panel read "brain off" meanwhile."""
        text = (snap["agent"].get("strategy") or "").strip()
        costs.ledger.character = snap["player"].get("name") or costs.ledger.character
        await self.rpc.call("brain_info", judge=self.judge.name, archetype=self.archetype or state.archetype_of(snap),
                            strategy_reading=loop.READINGS.get(text))

    def log(self, rec: dict[str, Any]) -> None:
        if self.log_file:
            self.log_file.write(json.dumps(rec) + "\n")
            self.log_file.flush()

    def key_check(self) -> None:
        """OpenRouter's own count for the key, at the start and end of the log (uo-brain costs --check)."""
        if self.log_file and (usage := costs.openrouter_usage()) is not None:
            self.log({"type": "openrouter_key", "t": time.time(), "usage": usage})

    async def keep_long_goals(self, stop: asyncio.Event) -> None:
        """The panel's view of the character's long goals, and what the player asks there (add,
        done, drop), every few seconds whether or not the planner is running."""
        from .longgoals import LongGoals
        if self.world is None:
            return
        goals = LongGoals(self.world)
        while not stop.is_set():
            try:
                snap = await self.rpc.call("snapshot", since=0)
                if snap.get("in_game"):
                    goals.character = snap["player"].get("name") or goals.character
                    await goals.sync(self.rpc, self.log)
            except Exception:
                pass
            await asyncio.sleep(3)

    async def run(self, stop: asyncio.Event) -> None:
        self.key_check()
        keeper = asyncio.create_task(self.keep_long_goals(stop))
        try:
            with costs.ledger.logging_to(self.log if self.log_file else None):
                while not stop.is_set():
                    snap = await self.rpc.call("snapshot", since=0)
                    if not snap.get("in_game"):
                        await asyncio.sleep(1)
                        continue
                    await self.announce(snap)
                    if wants_planner(snap) and self.world is not None:
                        await self.work_on_goal(snap, stop)
                    else:
                        await self.fight(stop)
        finally:
            keeper.cancel()
            if self.log_file:
                self.log({"type": "costs", "t": time.time(), **costs.ledger.summary()})
                self.key_check()
                self.log_file.close()

    async def fight(self, stop: asyncio.Event) -> None:
        """The tactical loop, until the player hands over to the planner (or stop)."""
        inner = asyncio.Event()

        noted: dict[str, float] = {}
        advised: dict[str, Any] = {}

        def watch(snap: dict[str, Any]) -> None:
            if self.world is not None:
                note_threats(self.world, snap, noted)
                if advice := death_advice(self.world, snap, advised):
                    asyncio.get_running_loop().create_task(self.rpc.call("note", text=advice))
            if self.recorder is not None:
                self.recorder.observe(snap)
                if self.recorder.due():
                    asyncio.get_running_loop().create_task(self.recorder.flush())
            if stop.is_set() or wants_planner(snap) and self.world is not None:
                inner.set()
            elif snap["agent"].get("mode") != "auto" and self.driven_since is None and self.planners:
                self.driven_since = time.monotonic()

        # The loop appends to the log itself; one run per stretch of fighting.
        if self.log_file:
            self.log_file.flush()
        await loop.run(self.rpc, self.judge, self.lcfg, self.pcfg, self.log_path, inner,
                       archetype=self.archetype, on_snapshot=watch,
                       bestiary=self.world.bestiary() if self.world is not None else None,
                       facts=FactPicker(self.world, self.judge, mode=self.facts_mode) if self.world is not None else None,
                       machine=self.machine)

    async def work_on_goal(self, snap: dict[str, Any], stop: asyncio.Event) -> None:
        goal = snap["agent"]["goal"]
        text, rev = goal["text"], goal["rev"]
        session = Session(self.rpc, self.world, self.judge, log=self.log, pcfg=self.pcfg, archetype=self.archetype,
                          decisions_log=self.log_path)
        session.recorder = self.recorder
        session.facts_mode = self.facts_mode
        session.machine = self.machine

        async def show(_goal: str, step: str, why: str) -> None:
            await self.rpc.call("goal_status", step=step, why=why)

        planner = self.planners.get(text)
        if planner is None:
            planner = self.planners[text] = Planner(session, text, model=self.planner_model, log=self.log, on_goal=show,
                                                    machines_allowed=self.machines_allowed, escalate=self.escalate,
                                                    memory=None if self.memory else False)
        else:
            planner.session = session
            planner.on_goal = show
            session.machine = planner.machine
        if self.driven_since is not None:
            minutes = round((time.monotonic() - self.driven_since) / 60, 1)
            self.driven_since = None
            here = describe(snap, self.world)
            planner.history.append(PlanStep("handover", {}, {
                "ok": True, "result": f"the player drove the character for {minutes} min and handed back; "
                                      f"it is now at {here['position']}, carrying {here['bandages']} bandages"}))
            self.log({"type": "handover", "t": time.time(), "minutes_driven": minutes})

        # Cut the current goal short as soon as the player switches away, pauses or changes the goal.
        # Meanwhile keep the panel's brain state fresh: it counts the brain gone after 15 s without
        # word, and the planner thinking, or a goal with nothing to fight, can say nothing for longer.
        async def guard() -> None:
            said = time.monotonic()
            while not session.interrupt.is_set():
                await asyncio.sleep(1)
                try:
                    s = await self.rpc.call("snapshot", since=0)
                except Exception:  # noqa: BLE001 - a slow or lost client: look again next second
                    continue
                if stop.is_set():
                    session.interrupt.set()
                    break
                if not s.get("in_game"):
                    continue  # out of game (a lost connection): the planner waits, so does the guard
                if time.monotonic() - said >= 10:
                    said = time.monotonic()
                    await self.announce(s)
                g = s["agent"].get("goal") or {}
                if stop.is_set() or not wants_planner(s) or g.get("rev") != rev:
                    session.interrupt.set()
                    if s["agent"].get("mode") != "auto":
                        self.driven_since = time.monotonic()
                        await self.rpc.call("goal_status", step="paused while you drive", why="")
                    elif g.get("paused"):
                        await self.rpc.call("goal_status", step="paused", why="")

        watcher = asyncio.create_task(guard())
        try:
            while not session.interrupt.is_set() and not planner.finished and not stop.is_set():
                s = await self.rpc.call("snapshot", since=0)
                await planner.step()  # dead, it resurrects and goes back for its corpse (cuo-4b3)
        finally:
            watcher.cancel()
            self.machine = planner.machine  # what the planner set carries on into plain fighting too
            for k, v in session.routine.items():
                self.routine[k] += v
        if planner.finished:
            await planner.close()
            await self.rpc.call("goal_status", step=f"finished: {planner.finished}", why="")
            await self.rpc.call("goal", pause=True)
            self.log({"type": "session_summary", "t": time.time(), "goal": text, **planner.summary(),
                      **routine_totals(self.routine)})
            del self.planners[text]
