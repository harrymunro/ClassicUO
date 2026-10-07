import asyncio
import json

from uo_brain import llm, planner
from uo_brain.session import Result, describe
from uo_brain.world import World

from conftest import SNAPSHOT


class FakeSession:
    """Records the goals the planner hands out; every goal succeeds."""

    def __init__(self, world):
        self.world = world
        self.goals = []
        self.rpc = self

    async def snap(self, **_):
        return json.loads(json.dumps(SNAPSHOT))

    async def call(self, method, **params):
        self.goals.append((method, params))
        return {}

    async def travel_to(self, place=None, x=None, y=None, distance=2):
        self.goals.append(("travel_to", place or (x, y)))
        return Result(True, f"arrived at {place}")

    async def hunt(self, area, minutes):
        self.goals.append(("hunt", area, minutes))
        return Result(True, "hunted", {"kills": 7, "deaths": 0})

    async def bank(self, deposit, withdraw):
        self.goals.append(("bank", deposit, withdraw))
        return Result(True, "banked")

    async def buy(self, item, count, vendor_kind=None):
        self.goals.append(("buy", item, count))
        return Result(True, f"bought {count} {item}")

    async def sell(self, items, vendor_kind):
        return Result(True, "sold")

    async def rest(self, seconds):
        return Result(True, "rested")


def call(name, args, cid="c1"):
    return llm.ToolCall(cid, name, args, json.dumps(args))


def reply(*calls, content=None, cost=0.001):
    msg = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = [{"id": c.id, "type": "function",
                              "function": {"name": c.name, "arguments": c.raw_arguments}} for c in calls]
    return llm.ChatResult(msg, content, list(calls), "tool_calls" if calls else "stop",
                          llm.LlmUsage(model="fake", prompt_tokens=1000, completion_tokens=50, cost=cost))


def scripted(*turns):
    """A chat function that answers with the given turns in order and records what it was sent."""
    seen = []
    queue = list(turns)

    async def chat(messages, tools=None, **_):
        seen.append({"messages": messages, "tools": [t["function"]["name"] for t in tools or []]})
        return queue.pop(0)

    chat.seen = seen
    return chat


def world(tmp_path):
    w = World(tmp_path / "w.sqlite")
    w.add_place("bank", "West Britain bank", 1425, 1690, source="test")
    w.add_place("healer", "Britain healer", 1471, 1611, sells=["bandages"], source="test")
    return w


def test_world_queries_answer_inside_one_step_then_a_goal_runs(tmp_path):
    w = world(tmp_path)
    s = FakeSession(w)
    chat = scripted(
        reply(call("find_place", {"kind": "healer", "near": "West Britain bank"})),
        reply(call("buy", {"item": "bandage", "count": 50, "why": "need bandages before hunting"}, "c2")),
    )
    p = planner.Planner(s, "hunt the graveyard", chat_fn=chat)
    step = asyncio.run(p.step())
    assert step.tool == "buy" and s.goals == [("buy", "bandage", 50)]
    # The query's answer went back to the model before it chose the goal.
    second = chat.seen[1]["messages"]
    assert second[-1]["role"] == "tool" and "Britain healer" in second[-1]["content"]
    assert {"travel_to", "hunt", "bank", "buy", "find_place", "hunting_spots"} <= set(chat.seen[0]["tools"])


def test_the_record_of_the_session_goes_into_the_next_prompt_not_a_growing_transcript(tmp_path):
    s = FakeSession(world(tmp_path))
    chat = scripted(
        reply(call("travel_to", {"place": "Britain graveyard", "why": "go hunt"})),
        reply(call("hunt", {"area": "Britain Graveyard", "minutes": 10, "why": "hunt"})),
        reply(call("finish", {"summary": "done", "why": "enough"})),
    )
    p = planner.Planner(s, "hunt the graveyard", chat_fn=chat)
    result = asyncio.run(p.run(hours=1))
    assert result["finished"] == "done" and result["goals"] == 3
    assert result["kills"] == 7 and result["planner_calls"] == 3
    last = chat.seen[-1]["messages"]
    assert len(last) == 2  # system + one user message, however long the session
    assert "travel_to(place='Britain graveyard')" in last[1]["content"]
    assert "hunt(area='Britain Graveyard', minutes=10)" in last[1]["content"]


def test_a_reply_without_a_tool_is_asked_again(tmp_path):
    s = FakeSession(world(tmp_path))
    chat = scripted(
        reply(content="I think we should bank first."),
        reply(call("bank", {"deposit": "gold", "withdraw": "", "why": "bank"})),
    )
    step = asyncio.run(planner.Planner(s, "bank", chat_fn=chat).step())
    assert step.tool == "bank"
    assert chat.seen[1]["messages"][-1]["content"].startswith("Answer by calling exactly one tool")


def test_a_dead_character_ends_the_session(tmp_path):
    s = FakeSession(world(tmp_path))
    dead = json.loads(json.dumps(SNAPSHOT))
    dead["player"]["dead"] = True

    async def snap(**_):
        return dead

    s.snap = snap
    result = asyncio.run(planner.Planner(s, "x", chat_fn=scripted()).run(hours=1))
    assert result["finished"] == "the character died" and result["goals"] == 0


def test_describe_puts_the_situation_in_words():
    d = describe(json.loads(json.dumps(SNAPSHOT)))
    assert d["health"] == "60/100" and d["bandages"] == 40 and d["hostiles_in_sight"] == 2


def test_the_planner_only_works_in_auto_mode_with_an_unpaused_goal():
    from uo_brain.autopilot import wants_planner

    def snap(mode, text, paused=False):
        return {"agent": {"mode": mode, "goal": {"text": text, "rev": 1, "paused": paused}}}

    assert wants_planner(snap("auto", "hunt the graveyard"))
    assert not wants_planner(snap("assist", "hunt the graveyard"))  # the player is driving
    assert not wants_planner(snap("auto", "hunt the graveyard", paused=True))
    assert not wants_planner(snap("auto", ""))
