import asyncio
import time

from uo_brain import costs, heartbeat, memory, planner
from uo_brain.world import World

from test_planner import FakeSession, call, reply, scripted


def reflecting(lessons=(), focus="push swordsmanship; avoid the graveyard at night"):
    """A chat function: the heartbeat's reflect and the daily digest get answers; planner turns rest."""
    seen = {"reflect": [], "digest": [], "planner": []}

    async def chat(messages, tools=None, **_):
        names = [t["function"]["name"] for t in tools or []]
        if names == ["reflect"]:
            seen["reflect"].append(messages)
            return reply(call("reflect", {"lessons": list(lessons), "focus": focus}))
        if names == ["digest"]:
            seen["digest"].append(messages)
            return reply(call("digest", {"day": "Hunted the graveyard twice, left once from a lich."}))
        if names == ["fold"]:
            return reply(call("fold", {"summary": "s", "keep": []}))
        seen["planner"].append(messages)
        return reply(call("rest", {"seconds": 30, "why": "w"}))

    chat.seen = seen
    return chat


def test_a_heartbeat_at_the_start_keeps_lessons_and_sets_the_focus(tmp_path):
    w = World(tmp_path / "w.sqlite")
    chat = reflecting(lessons=[{"text": "The graveyard is too busy after dark for a new warrior.", "area": "Britain Graveyard"}])
    p = planner.Planner(FakeSession(w), "hunt", chat_fn=chat, escalate=False)
    asyncio.run(p.step())
    assert len(chat.seen["reflect"]) == 1 and "Why now: the session started" in chat.seen["reflect"][0][1]["content"]
    lessons = memory.Memory(w, "Brutus").list(source="heartbeat")
    assert lessons[0]["kind"] == "lesson" and "after dark" in lessons[0]["text"]
    asyncio.run(p.step())  # not due again so soon
    assert len(chat.seen["reflect"]) == 1
    assert "Your focus, from your last reflection" in chat.seen["planner"][-1][1]["content"]
    assert "push swordsmanship" in chat.seen["planner"][-1][1]["content"]
    p.died({"in_game": True, "player": {"x": 1, "y": 2}, "mobiles": []})
    p.dead_since = None
    asyncio.run(p.step())  # a death is an event, but it waits until 10 minutes after the last reflection
    assert len(chat.seen["reflect"]) == 1
    p.heart.last -= heartbeat.MIN_GAP_MIN * 60
    asyncio.run(p.step())
    assert "Why now: a death" in chat.seen["reflect"][-1][1]["content"]
    p.heart.last -= heartbeat.HEARTBEAT_MIN * 60
    asyncio.run(p.step())
    assert "30 minutes since the last" in chat.seen["reflect"][-1][1]["content"]
    assert p.summary()["heartbeats"] == 3


def test_over_budget_the_heartbeat_is_skipped(tmp_path):
    costs.ledger.reset()
    costs.ledger.budget = costs.Budget(0.0001, "cheaper")
    try:
        costs.ledger.record(costs.Call("planner", "m", cost=1.0))
        chat = reflecting()
        logged = []
        p = planner.Planner(FakeSession(World(tmp_path / "w.sqlite")), "hunt", chat_fn=chat, escalate=False,
                            log=logged.append)
        asyncio.run(p.step())
        assert chat.seen["reflect"] == [] and any(r.get("skipped") == "over budget" for r in logged)
    finally:
        costs.ledger.reset()


def test_a_restarted_brain_carries_on_with_the_same_goal_summary_and_focus(tmp_path):
    w = World(tmp_path / "w.sqlite")
    p = planner.Planner(FakeSession(w), "hunt the graveyard", chat_fn=reflecting(), escalate=False)
    for _ in range(3):
        asyncio.run(p.step())
    p.folder.summary = "Hunted, rested, rested."
    p.save_state(current="hunting at Britain Graveyard for up to 10 min")
    logged = []
    again = planner.Planner(FakeSession(w), "hunt the graveyard", chat_fn=reflecting(), escalate=False, log=logged.append)
    asyncio.run(again.step())
    assert [s.tool for s in again.history[:3]] == ["rest", "rest", "rest"]
    assert again.history[3].tool == "restart" and "during: hunting at Britain Graveyard" in again.history[3].result["result"]
    assert again.folder.summary == "Hunted, rested, rested." and again.heart.focus.startswith("push")
    assert any(r["type"] == "resumed" for r in logged)
    other = planner.Planner(FakeSession(w), "bank everything", chat_fn=reflecting(), escalate=False)
    asyncio.run(other.step())
    assert all(s.tool != "restart" for s in other.history)  # another goal starts afresh
    w.db.execute("UPDATE planner_state SET goal = 'hunt the graveyard', updated = '2026-01-01T00:00:00+00:00'")
    stale = planner.Planner(FakeSession(w), "hunt the graveyard", chat_fn=reflecting(), escalate=False)
    asyncio.run(stale.step())
    assert all(s.tool != "restart" for s in stale.history)  # too old to resume


def test_earlier_days_are_digested_once(tmp_path):
    w = World(tmp_path / "w.sqlite")
    mem = memory.Memory(w, "Brutus")
    day = time.time() - 2 * 86400
    mem.add("Had to leave Britain Graveyard: a lich.", source="leave", importance=4, at=day)
    mem.add("Hunted Britain Graveyard: 8 kills.", source="goal", at=day + 600)
    mem.add("Today's memory.", source="goal")
    chat = reflecting()
    beat = heartbeat.Heartbeat(mem, chat)
    assert asyncio.run(beat.daily()) == 1
    rows = mem.list(source="daily")
    assert len(rows) == 1 and rows[0]["text"].startswith(f"Day {time.strftime('%Y-%m-%d', time.localtime(day))}: Hunted")
    assert "a lich" in chat.seen["digest"][0][1]["content"] and "Today's memory" not in chat.seen["digest"][0][1]["content"]
    assert asyncio.run(beat.daily()) == 0
