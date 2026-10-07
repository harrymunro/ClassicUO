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

from . import loop, policy
from .facts import FactPicker
from .judge import Judge
from .planner import Planner, PlanStep
from .recorder import Recorder
from .rpc import AgentRpc
from .session import Session, describe, note_threats, routine_totals
from .world import World


def wants_planner(snap: dict[str, Any]) -> bool:
    goal = snap["agent"].get("goal") or {}
    return snap["agent"].get("mode") == "auto" and bool(goal.get("text")) and not goal.get("paused")


class Autopilot:
    def __init__(self, rpc: AgentRpc, judge: Judge, world: World | None, lcfg: loop.LoopConfig,
                 pcfg: policy.PolicyConfig, log_path: Path | None, archetype: str | None = None,
                 planner_model: str | None = None, facts_mode: str = "jev"):
        self.rpc = rpc
        self.judge = judge
        self.world = world
        self.lcfg = lcfg
        self.pcfg = pcfg
        self.archetype = archetype
        self.planner_model = planner_model
        self.facts_mode = facts_mode  # which world facts reach the fights (facts.py)
        self.log_path = log_path
        self.log_file = log_path.open("a") if log_path else None
        self.planners: dict[str, Planner] = {}  # goal text -> its planner, so a handover keeps history
        # Whatever the character sees, driven or not, goes into the world store.
        self.recorder = Recorder(world, judge) if world is not None else None
        self.driven_since: float | None = None   # when the player took over from the planner
        # Jev's routine questions inside hunts (routine.py), over every session object of a goal.
        self.routine = {"questions": 0, "input_tokens": 0, "cost_usd": 0.0, "hunt_minutes": 0.0}

    def log(self, rec: dict[str, Any]) -> None:
        if self.log_file:
            self.log_file.write(json.dumps(rec) + "\n")
            self.log_file.flush()

    async def run(self, stop: asyncio.Event) -> None:
        try:
            while not stop.is_set():
                snap = await self.rpc.call("snapshot", since=0)
                if not snap.get("in_game"):
                    await asyncio.sleep(1)
                    continue
                if wants_planner(snap) and self.world is not None:
                    await self.work_on_goal(snap, stop)
                else:
                    await self.fight(stop)
        finally:
            if self.log_file:
                self.log_file.close()

    async def fight(self, stop: asyncio.Event) -> None:
        """The tactical loop, until the player hands over to the planner (or stop)."""
        inner = asyncio.Event()

        noted: dict[str, float] = {}

        def watch(snap: dict[str, Any]) -> None:
            if self.world is not None:
                note_threats(self.world, snap, noted)
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
                       facts=FactPicker(self.world, self.judge, mode=self.facts_mode) if self.world is not None else None)

    async def work_on_goal(self, snap: dict[str, Any], stop: asyncio.Event) -> None:
        goal = snap["agent"]["goal"]
        text, rev = goal["text"], goal["rev"]
        session = Session(self.rpc, self.world, self.judge, log=self.log, pcfg=self.pcfg, archetype=self.archetype,
                          decisions_log=self.log_path)
        session.recorder = self.recorder
        session.facts_mode = self.facts_mode

        async def show(_goal: str, step: str, why: str) -> None:
            await self.rpc.call("goal_status", step=step, why=why)

        planner = self.planners.get(text)
        if planner is None:
            planner = self.planners[text] = Planner(session, text, model=self.planner_model, log=self.log, on_goal=show)
        else:
            planner.session = session
            planner.on_goal = show
        if self.driven_since is not None:
            minutes = round((time.monotonic() - self.driven_since) / 60, 1)
            self.driven_since = None
            here = describe(snap, self.world)
            planner.history.append(PlanStep("handover", {}, {
                "ok": True, "result": f"the player drove the character for {minutes} min and handed back; "
                                      f"it is now at {here['position']}, carrying {here['bandages']} bandages"}))
            self.log({"type": "handover", "t": time.time(), "minutes_driven": minutes})

        # Cut the current goal short as soon as the player switches away, pauses or changes the goal.
        async def guard() -> None:
            while not session.interrupt.is_set():
                await asyncio.sleep(1)
                s = await self.rpc.call("snapshot", since=0)
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
            while not session.interrupt.is_set() and not planner.finished:
                s = await self.rpc.call("snapshot", since=0)
                if s["player"]["dead"]:
                    planner.finished = "the character died"
                    break
                await planner.step()
        finally:
            watcher.cancel()
            for k, v in session.routine.items():
                self.routine[k] += v
        if planner.finished:
            await self.rpc.call("goal_status", step=f"finished: {planner.finished}", why="")
            await self.rpc.call("goal", pause=True)
            self.log({"type": "session_summary", "t": time.time(), "goal": text, **planner.summary(),
                      **routine_totals(self.routine)})
            del self.planners[text]
