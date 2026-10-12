import asyncio
import json

import pytest

from uo_brain import memory, planner
from uo_brain.world import World, WorldError

from conftest import SNAPSHOT
from test_planner import FakeSession, call, reply, scripted


def store(tmp_path, who="Brutus"):
    return memory.Memory(World(tmp_path / "w.sqlite"), who)


def test_memories_are_kept_per_character_and_survive_a_restart(tmp_path):
    m = store(tmp_path)
    a = m.add("Pulling one zombie at a time worked at the graveyard.", kind="lesson", area="Britain Graveyard",
              source="planner", importance=4)
    m.add("Died to a lich.", kind="event", source="death", importance=5, character="Hale")
    m.world.close()
    again = memory.Memory(World(tmp_path / "w.sqlite"), "Brutus")  # a new process, the same file
    rows = again.list()
    assert [r["id"] for r in rows] == [a] and rows[0]["kind"] == "lesson" and rows[0]["importance"] == 4
    assert again.list(character="Hale")[0]["text"] == "Died to a lich."
    assert again.characters() == {"Brutus": 1, "Hale": 1}
    with pytest.raises(WorldError):
        again.add("x", kind="rumour")
    with pytest.raises(WorldError):
        memory.Memory(again.world).add("nobody's")


def test_a_repeat_within_the_hour_is_counted_not_stored_again(tmp_path):
    m = store(tmp_path)
    first = m.add("Tried to go back to Britain Graveyard too soon.", source="refusal", importance=2)
    again = m.add("Tried to go back to Britain Graveyard too soon.", source="refusal", importance=3)
    assert again == first
    row = m.list()[0]
    assert row["times"] == 2 and row["importance"] == 3 and m.count() == 1
    assert "2 times" in memory.words(row)
    # An import dated days earlier is its own memory, and doesn't move the newer one back in time.
    old = m.add("Tried to go back to Britain Graveyard too soon.", source="refusal", at=1_700_000_000)
    assert old != first and m.list(search="graveyard")[0]["last_t"] == row["last_t"]


def test_search_and_forget(tmp_path):
    m = store(tmp_path)
    m.add("The Britain healer was sold out of bandages in the evening.", tags=["vendor"])
    lich = m.add("Liches at the graveyard drove the character off twice.", kind="lesson", area="Britain Graveyard")
    assert m.list(search="lich")[0]["id"] == lich  # stemmed: "lich" finds "Liches"
    assert [r["id"] for r in m.list(kind="lesson")] == [lich]
    assert m.forget(lich) and not m.forget(lich)
    assert [r["text"] for r in m.list()] == ["The Britain healer was sold out of bandages in the evening."]
    assert m.list(include_stale=True, kind="lesson")[0]["stale"] is True
    assert not memory.Memory(m.world, "Hale").forget(1)  # not Hale's to forget


def test_what_code_remembers_when_a_goal_ends():
    leave = memory.from_goal("hunt", {"area": "Britain Graveyard"}, {
        "ok": True, "result": "hunted ...", "minutes": 4.2, "kills": 3, "deaths": 0,
        "stopped_because": "left the area: 3 coming at once: a lich, 2 bone knights"})
    assert [(n.source, n.importance, n.area) for n in leave] == [("leave", 4, "Britain Graveyard")]
    assert "a lich, 2 bone knights" in leave[0].text
    died = memory.from_goal("hunt", {"area": "Britain Graveyard"}, {
        "ok": False, "result": "hunted", "minutes": 2, "kills": 1, "deaths": 1, "stopped_because": "dead"})
    assert died[0].source == "death" and died[0].importance == 5
    quiet = memory.from_goal("hunt", {"area": "Yew Woods"}, {
        "ok": True, "result": "hunted", "minutes": 10, "kills": 0, "deaths": 0, "gold_gained": 0,
        "stopped_because": "nothing showed up"})
    assert quiet[0].importance == 3 and "0 kills" in quiet[0].text
    refused = memory.from_goal("hunt", {"area": "Britain Graveyard"}, {
        "ok": False, "result": "not going back near Britain Graveyard yet: it left there 4 min ago (a lich)"})
    assert [(n.source, n.area) for n in refused] == [("refusal", "Britain Graveyard")]
    failed = memory.from_goal("buy", {"item": "bandage", "count": 50}, {"ok": False, "result": "sold out"})
    assert failed[0].text == "Failed to buy 50 bandage: sold out"
    assert memory.from_goal("bank", {}, {"ok": True, "result": "deposited 300 gold"}) == []
    seen = memory.from_goal("travel_to", {"place": "Yew"}, {
        "ok": True, "result": "arrived; stronger creatures seen: 1 ogre lord at Yew Woods"})
    assert seen[0].tags == ("danger",) and "ogre lord" in seen[0].text


def test_the_planner_keeps_goal_results_and_its_own_lessons(tmp_path):
    w = World(tmp_path / "w.sqlite")
    s = FakeSession(w)

    async def hunt(area, minutes):
        return planner.Result(True, "hunted", {"minutes": 5, "kills": 2, "deaths": 0,
                                               "stopped_because": "left the area: stronger: a lich"})
    s.hunt = hunt
    chat = scripted(
        reply(call("remember", {"text": "The graveyard has a lich in the evenings.", "kind": "lesson",
                                "area": "Britain Graveyard", "importance": 4})),
        reply(call("hunt", {"area": "Britain Graveyard", "minutes": 5, "why": "hunt"})))
    logged = []
    p = planner.Planner(s, "hunt the graveyard", chat_fn=chat, log=logged.append)
    asyncio.run(p.step())
    assert "remember" in chat.seen[0]["tools"]
    # The tool answer went back and the model was asked again within the same step.
    assert json.loads(chat.seen[1]["messages"][-1]["content"])["ok"] is True
    mem = memory.Memory(World(tmp_path / "w.sqlite"), SNAPSHOT["player"]["name"])  # read back after a "restart"
    rows = {r["source"]: r for r in mem.list()}
    assert rows["planner"]["kind"] == "lesson" and rows["planner"]["importance"] == 4
    assert rows["leave"]["area"] == "Britain Graveyard"
    assert {r["type"] for r in logged} >= {"memory"}
    assert p.summary()["memories_written"] == 2


def test_a_death_is_remembered_with_where_and_what_was_about(tmp_path):
    w = World(tmp_path / "w.sqlite")
    s = FakeSession(w)
    dead = json.loads(json.dumps(SNAPSHOT))
    dead["player"]["dead"] = True
    dead["mobiles"] = [{"serial": 9, "name": "a lich", "monster": True, "distance": 3}]

    async def snap(**_):
        return dead
    s.snap = snap
    chat = scripted(reply(call("rest", {"seconds": 5, "why": "dead"})), reply(call("finish", {"summary": "x", "why": "x"})))
    p = planner.Planner(s, "hunt", chat_fn=chat, escalate=False)
    asyncio.run(p.run(hours=0.01))
    assert p.deaths == 1  # once per death, however many steps it stays dead
    row = memory.Memory(w, SNAPSHOT["player"]["name"]).list(source="death")[0]
    assert row["importance"] == 5 and "a lich" in row["text"] and row["x"] == SNAPSHOT["player"]["x"]


def test_the_planner_can_run_without_memory(tmp_path):
    s = FakeSession(World(tmp_path / "w.sqlite"))
    chat = scripted(reply(call("rest", {"seconds": 10, "why": "w"})))
    p = planner.Planner(s, "rest", chat_fn=chat, memory=False)
    asyncio.run(p.step())
    assert "remember" not in chat.seen[0]["tools"] and p.memory is None


def test_ago_words():
    from datetime import datetime, timedelta, timezone
    at = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
    assert memory.ago((at - timedelta(minutes=12)).isoformat(), at) == "12 min ago"
    assert memory.ago((at - timedelta(hours=5)).isoformat(), at) == "5 h ago"
    assert memory.ago((at - timedelta(days=3)).isoformat(), at) == "3 days ago"
    assert memory.ago("nonsense", at) == "some time ago"


class MemoryJudge:
    """Says yes to memories with any of the good words, and records what it was asked."""

    name = "fake-jev"

    def __init__(self, good=("graveyard",), fail=False):
        self.good, self.fail, self.asked = good, fail, []

    async def ask(self, state, qs):
        from uo_brain.judge import Answers
        self.asked.append((state, qs))
        if self.fail:
            raise TimeoutError("no answer")
        a = Answers(model="fake", input_tokens=50 * len(qs), latency_ms=3.0, cost=0.00001)
        for q, spec in qs.items():
            ins = spec["instructions"]
            text = (ins.get("memory") or ins.get("result") or "").lower()
            a.nouls[q] = 0.9 if any(w in text for w in self.good) else 0.2
        return a


def filled(tmp_path):
    m = store(tmp_path)
    m.add("Never hunt in Despise.", kind="order", source="player", importance=5)
    m.add("Had to leave Britain Graveyard after 3 kills: a lich.", area="Britain Graveyard", source="leave",
          importance=4)
    m.add("The Britain healer was sold out of bandages.", tags=["vendor"])
    for i in range(12):
        m.add(f"Hunted Yew Woods for 10 min: {i} kills.", area="Yew Woods", source="goal")
    return m


def test_jev_picks_the_memories_that_bear_on_the_moment_and_orders_always_show(tmp_path):
    m = filled(tmp_path)
    judge = MemoryJudge()
    logged = []
    picker = memory.Picker(m, judge, log=logged.append)
    situation = {"position": {"x": 1, "y": 2, "area": "Britain"}}
    chosen = asyncio.run(picker.pick(situation, "hunt the undead at the Britain graveyard", ["bank() -> ok"]))
    assert [r["text"] for r in chosen["orders"]] == ["Never hunt in Despise."]
    assert [r["text"] for r in chosen["picked"]] == ["Had to leave Britain Graveyard after 3 kills: a lich."]
    assert chosen["picked"][0]["relevance"] == 0.9
    state, qs = judge.asked[0]
    assert state["player_goal"].startswith("hunt the undead") and len(qs) <= memory.SHORTLIST
    assert all("Despise" not in q["instructions"]["memory"] for q in qs.values())  # orders aren't scored
    assert logged[0]["type"] == "memories" and logged[0]["picked"] and logged[0]["cost_usd"] > 0
    text = memory.section(chosen)
    assert text.startswith("The player's standing orders:\n- Never hunt in Despise.")
    assert "What you remember that bears on this" in text and "lich" in text


def test_without_jev_the_most_important_and_newest_are_shown(tmp_path):
    m = filled(tmp_path)
    for judge in (None, MemoryJudge(fail=True)):
        chosen = asyncio.run(memory.Picker(m, judge, keep=2).pick({}, "earn gold", []))
        assert [r["source"] for r in chosen["picked"]] == ["leave", "goal"]
        assert chosen["picked"][1]["text"] == "Hunted Yew Woods for 10 min: 11 kills."


def test_memories_written_later_are_not_shown_for_an_earlier_moment(tmp_path):
    m = store(tmp_path)
    m.add("Old lesson about the graveyard.", kind="lesson", at=1_700_000_000)
    m.add("New lesson about the graveyard.", kind="lesson", at=1_800_000_000)
    chosen = asyncio.run(memory.Picker(m, MemoryJudge()).pick({}, "graveyard", [],
                                                              before=memory.stamp_of(1_750_000_000)))
    assert [r["text"] for r in chosen["picked"]] == ["Old lesson about the graveyard."]


def test_recall_searches_and_reranks(tmp_path):
    m = filled(tmp_path)
    picker = memory.Picker(m, MemoryJudge(good=("12",)))
    out = asyncio.run(picker.recall("how did hunting at Yew Woods go", limit=2))
    assert len(out) == 2 and all("Yew Woods" in o["memory"] for o in out)
    assert picker.calls == 1  # 12 matches for 2 asked: Jev re-ranked
    assert asyncio.run(picker.recall("dragons")) == {"memories": [], "note": "nothing remembered about that"}


def test_the_planner_sees_picked_memories_in_the_user_turn_and_can_recall(tmp_path):
    w = World(tmp_path / "w.sqlite")
    m = memory.Memory(w, "Brutus")
    m.add("Had to leave Britain Graveyard after 3 kills: a lich.", area="Britain Graveyard", source="leave",
          importance=4)
    s = FakeSession(w)
    s.judge = MemoryJudge()
    chat = scripted(reply(call("recall", {"query": "graveyard lich"})),
                    reply(call("rest", {"seconds": 30, "why": "wait"})))
    p = planner.Planner(s, "hunt the Britain graveyard", chat_fn=chat)
    asyncio.run(p.step())
    user = chat.seen[0]["messages"][1]["content"]
    assert "What you remember that bears on this" in user and user.index("lich") < user.index("The character now")
    assert chat.seen[0]["messages"][0]["content"] == planner.SYSTEM  # the cached prompt doesn't change
    assert "lich" in chat.seen[1]["messages"][-1]["content"]  # the recall answer
    summary = p.summary()
    assert summary["memories_shown"] == 1 and summary["memory_calls"] == 1


def test_earlier_session_logs_can_be_imported_once(tmp_path):
    m = store(tmp_path)
    recs = [{"type": "goal", "t": 1_791_000_000, "tool": "hunt", "args": {"area": "Britain Graveyard", "minutes": 10}},
            {"type": "goal_result", "t": 1_791_000_600, "tool": "hunt", "result": {
                "ok": True, "result": "hunted", "minutes": 10, "kills": 4, "deaths": 0,
                "stopped_because": "left the area: 2 coming at once: a lich, a wraith"}},
            {"type": "goal", "t": 1_791_000_700, "tool": "buy", "args": {"item": "bandage", "count": 30}},
            {"type": "goal_result", "t": 1_791_000_760, "tool": "buy", "result": {
                "ok": False, "result": "could not get to East Yew healer: no route"}}]
    assert memory.import_log(m, recs, "s1.jsonl") == {"session": "s1.jsonl", "memories": 2}
    assert memory.import_log(m, recs, "s1.jsonl")["memories"] == 2  # again: replaces its own rows
    m.session = "live.jsonl"
    m.add("Died at the Britain Graveyard, with a lich about.", source="death", importance=5)
    assert memory.import_log(m, recs, "live.jsonl")["skipped"]  # a live run's memories are left alone
    m.session = None
    rows = m.list()
    rows = [r for r in rows if r["source"] != "death"]
    assert len(rows) == 2 and rows[0]["t"] == memory.stamp_of(1_791_000_760)
    assert {r["source"] for r in rows} == {"leave", "goal"}
    bad = planner.bad_targets(m, memory.stamp_of(1_791_001_000), 1_791_001_000)
    assert bad == {"britain graveyard": "leave", "east yew healer": "no route"}
    assert planner.repeats(call("hunt", {"area": "Britain Graveyard", "minutes": 5}), bad) == "britain graveyard"
    assert planner.repeats(call("bank", {"deposit": "gold"}), bad) is None


def test_replay_asks_each_moment_with_and_without_memories(tmp_path):
    m = store(tmp_path)
    m.add("Had to leave Britain Graveyard after 3 kills: a lich.", area="Britain Graveyard", source="leave",
          importance=4, at=1_791_000_000)
    user = ("The player's goal: hunt the graveyard\n\nWhat has happened so far, oldest first:\n"
            "1. bank() -> {\"ok\":true}\n\nThe character now: {\"position\": {\"area\": \"Britain\"}}\n\n"
            "Choose the next goal by calling one tool.")
    recs = [{"type": "planner", "t": 1_791_000_100, "messages": [{"role": "user", "content": user}],
             "calls": [{"name": "hunt", "args": {}}]}]
    chat = scripted(reply(call("hunt", {"area": "Britain Graveyard", "minutes": 10, "why": "x"})),
                    reply(call("hunt", {"area": "Yew Woods", "minutes": 10, "why": "x"})))
    out = asyncio.run(planner.replay_memories(recs, "m", m, MemoryJudge(), chat_fn=chat))
    assert out["moments"] == 1 and out["repeats_without"] == 1 and out["repeats_with"] == 0
    assert out["same_tool"] == 1.0 and out["same_call"] == 0.0
    assert "lich" in chat.seen[1]["messages"][1]["content"] and "lich" not in chat.seen[0]["messages"][1]["content"]


class StrategySession(FakeSession):
    def __init__(self, world, strategy=""):
        super().__init__(world)
        self.strategy = strategy

    async def call(self, method, **params):
        if method == "strategy" and "text" in params:
            self.strategy = params["text"]
        await super().call(method, **params)
        return {"strategy": self.strategy}


def test_the_planners_strategy_lines_never_replace_the_players(tmp_path):
    s = StrategySession(World(tmp_path / "w.sqlite"), "Never flee.\nLoot everything.")
    p = planner.Planner(s, "hunt", chat_fn=scripted(), memory=False)
    step = asyncio.run(p.carry_out("set_strategy", {"why": "x"}))
    assert step.result["ok"] is False and s.strategy == "Never flee.\nLoot everything."  # empty: nothing to take out
    step = asyncio.run(p.carry_out("set_strategy", {"text": "Fight one undead at a time.", "why": "leaves"}))
    assert step.result == {"ok": True, "result": "your lines added; the player's 2 lines kept"}
    assert s.strategy == f"Never flee.\nLoot everything.\n\n{planner.PLANNER_HEAD}\nFight one undead at a time."
    step = asyncio.run(p.carry_out("set_strategy", {"text": "Leave from liches.", "why": "again"}))
    assert step.result["ok"] is False and "min ago" in step.result["result"]  # too soon
    p.strategy_set_at -= planner.STRATEGY_EVERY_S
    asyncio.run(p.carry_out("set_strategy", {"text": "Leave from liches.", "why": "again"}))
    assert s.strategy.endswith(f"{planner.PLANNER_HEAD}\nLeave from liches.") and s.strategy.count(planner.PLANNER_HEAD) == 1
    assert s.strategy.startswith("Never flee.\nLoot everything.")
    p.strategy_set_at -= planner.STRATEGY_EVERY_S
    asyncio.run(p.carry_out("set_strategy", {"text": "", "why": "back to the player's"}))
    assert s.strategy == "Never flee.\nLoot everything." and p.summary()["strategy_changes"] == 3
    assert planner.with_planner_lines("", "Pull singles.") == f"{planner.PLANNER_HEAD}\nPull singles."
