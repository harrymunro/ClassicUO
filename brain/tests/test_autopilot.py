import asyncio
import copy

from uo_brain import autopilot, loop, policy
from uo_brain.judge import HeuristicJudge
from uo_brain.world import World

from conftest import SNAPSHOT


class FakeRpc:
    def __init__(self, snap):
        self.snap = snap
        self.calls = []

    async def call(self, method, **params):
        self.calls.append((method, params))
        return copy.deepcopy(self.snap) if method == "snapshot" else {}


def test_panel_hears_of_the_brain_before_the_planners_first_step(tmp_path, monkeypatch):
    """cuo-8tc: with a goal in auto mode the planner's first step and a walk could take a minute,
    and the panel read 'brain off' until the first fight decision."""
    snap = copy.deepcopy(SNAPSHOT)
    snap["agent"].update(goal={"text": "hunt at the graveyard", "rev": 1}, strategy="Never flee.")
    rpc = FakeRpc(snap)
    stop = asyncio.Event()
    seen_before_step = []

    async def step(self):
        seen_before_step.extend(m for m, _ in rpc.calls)
        stop.set()
        self.finished = "done"

    monkeypatch.setattr(autopilot.Planner, "step", step)
    loop.READINGS["Never flee."] = "never flees"
    pilot = autopilot.Autopilot(rpc, HeuristicJudge(), World(tmp_path / "w.sqlite"), loop.LoopConfig(),
                                policy.PolicyConfig(), None)
    asyncio.run(pilot.run(stop))

    assert "brain_info" in seen_before_step
    info = next(p for m, p in rpc.calls if m == "brain_info")
    assert info == {"judge": "heuristic", "archetype": "warrior", "strategy_reading": "never flees"}


def test_announce_leaves_an_unread_strategy_alone():
    rpc = FakeRpc(SNAPSHOT)
    pilot = autopilot.Autopilot(rpc, HeuristicJudge(), None, loop.LoopConfig(), policy.PolicyConfig(), None,
                                archetype="mage")
    asyncio.run(pilot.announce({**SNAPSHOT, "agent": {**SNAPSHOT["agent"], "strategy": "something new"}}))
    assert rpc.calls == [("brain_info", {"judge": "heuristic", "archetype": "mage", "strategy_reading": None})]
