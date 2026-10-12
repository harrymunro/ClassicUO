import asyncio
import json

import pytest

from uo_brain import longgoals, memory, planner
from uo_brain.world import World, WorldError

from conftest import SNAPSHOT
from test_memory import MemoryJudge
from test_planner import FakeSession, call, reply, scripted


def store(tmp_path, who="Brutus"):
    w = World(tmp_path / "w.sqlite")
    w.add_place("dungeon_entrance", "Covetous", 2499, 919, source="test")
    w.add_place("dungeon_entrance", "Despise", 1301, 1080, source="test")
    w.add_place("dungeon_entrance", "Deceit", 4111, 434, source="test")
    w.add_place("dungeon_entrance", "Shame", 513, 1561, source="test")
    w.add_place("dungeon_entrance", "Destard", 1176, 2637, source="test")
    w.add_place("town", "Britain", 1475, 1645, source="test")
    w.add_place("graveyard", "Britain Graveyard", 1369, 1475, source="test")
    return longgoals.LongGoals(w, who)


def test_goals_in_words_are_read_for_what_code_can_measure(tmp_path):
    w = store(tmp_path).world
    assert longgoals.parse("Reach 90 swordsmanship") == ("skill", {"skill": "Swordsmanship", "value": 90.0})
    assert longgoals.parse("get magery to 100") == ("skill", {"skill": "Magery", "value": 100.0})
    assert longgoals.parse("GM resist 100 by friday")[1]["skill"] == "Resisting Spells"
    assert longgoals.parse("save 20,000 gold for a house") == ("gold", {"gold": 20000})
    assert longgoals.parse("bank 25k gold") == ("gold", {"gold": 25000})
    assert longgoals.parse("learn the dungeons near Britain") == \
        ("places", {"place_kind": "dungeon_entrance", "near": "Britain", "count": 3})  # no store to pick from
    assert longgoals.parse("learn the dungeons near Britain", w) == \
        ("places", {"places": ["Despise", "Shame", "Destard"], "near": "Britain"})
    assert longgoals.parse("visit Covetous and Despise", w) == ("places", {"places": ["Covetous", "Despise"]})
    assert longgoals.parse("make friends in Vesper", w) == ("other", {})
    # Found in review: numbers that belong to gold, and thousands separators.
    assert longgoals.parse("save 10,000 gold for a vet's stable") == ("gold", {"gold": 10000})
    assert longgoals.parse("save 50,000 gold to buy a magery spellbook") == ("gold", {"gold": 50000})
    assert longgoals.parse("Save 20.000 gold") == ("gold", {"gold": 20000})
    assert longgoals.parse("swordsmanship to 95") == ("skill", {"skill": "Swordsmanship", "value": 95.0})


def test_skill_progress_milestones_and_done_by_measure(tmp_path):
    g = store(tmp_path)
    gid = g.add("reach 82 swordsmanship")
    snap = json.loads(json.dumps(SNAPSHOT))
    snap["player"]["skills"] = {"Swordsmanship": 80.0}
    assert g.observe(snap) == []
    snap["player"]["skills"]["Swordsmanship"] = 81.1
    events = g.observe(snap)
    assert [e["milestone"] for e in events] == [0.25, 0.5]
    assert "Swordsmanship now 81.1 (from 80.0, target 82.0); past 50%" == longgoals.words(g.get(gid))
    snap["player"]["skills"]["Swordsmanship"] = 82.0
    events = g.observe(snap)
    assert events[-1]["done"] and g.get(gid)["status"] == "done" and g.list() == []
    assert g.get(gid)["why"] == "reached, measured by code"


def test_gold_and_places_progress_from_goal_results(tmp_path):
    g = store(tmp_path)
    gold = g.add("save 1000 gold")
    places = g.add("learn the dungeons near Britain")
    g.note_result("bank", {}, {"ok": True, "result": "deposited", "gold_before": 700, "gold_carried": 100})
    assert g.get(gold)["progress"]["banked"] == 600 and g.get(gold)["progress"]["milestones"] == [0.25, 0.5]
    g.note_result("travel_to", {"place": "Shame"}, {"ok": True, "result": "arrived"})
    g.note_result("travel_to", {"place": "Shame"}, {"ok": True, "result": "arrived"})  # once only
    g.note_result("hunt", {"area": "Despise"}, {"ok": False, "result": "hunted", "minutes": 3, "deaths": 1})
    g.note_result("travel_to", {"place": "Covetous"}, {"ok": True, "result": "arrived"})  # not one of the nearest
    g.note_result("travel_to", {"place": "Britain Graveyard"}, {"ok": True, "result": "arrived"})  # not a dungeon
    assert g.get(places)["progress"]["visited"] == ["Shame", "Despise"]
    assert longgoals.words(g.get(places)) == \
        "visited Shame, Despise of Despise, Shame, Destard (the dungeons nearest Britain); past 50%"
    g.note_result("travel_to", {"place": "Britain"}, {"ok": False, "result": "stuck"})
    assert g.get(gold)["status"] == "active"


def test_add_change_done_drop_and_characters(tmp_path):
    g = store(tmp_path)
    a = g.add("make friends in Vesper")
    assert g.get(a)["kind"] == "other" and "not measured" in longgoals.words(g.get(a))
    assert g.change(a, "reach 70 tactics", "the player changed it")
    assert g.get(a)["kind"] == "skill" and g.get(a)["why"] == "the player changed it"
    assert g.drop(a, "no longer wanted") and g.list() == []
    hale = longgoals.LongGoals(g.world, "Hale")
    hale.add("save 500 gold")
    assert not hale.done(a)  # not Hale's
    assert [x["character"] for x in g.list(all_characters=True)] == ["Hale"]
    with pytest.raises(WorldError):
        g.add("  ")


class PanelRpc:
    """The client's long_goals RPC: shows what the brain sends, hands over the player's requests."""

    def __init__(self, requests):
        self.requests = requests
        self.shown = None
        self.acked = 0

    async def call(self, method, **params):
        assert method == "long_goals"
        self.shown = params["goals"]
        if "ack" in params:
            self.acked = params["ack"]
            self.requests = [r for r in self.requests if r["seq"] > params["ack"]]
        return {"requests": self.requests}


def test_the_panel_sees_the_goals_and_its_requests_are_applied_once(tmp_path):
    g = store(tmp_path)
    old = g.add("save 1000 gold")
    rpc = PanelRpc([{"seq": 1, "action": "add", "id": 0, "text": "reach 90 swordsmanship"},
                    {"seq": 2, "action": "done", "id": old, "text": ""}])
    logged = []
    applied = asyncio.run(g.sync(rpc, logged.append))
    assert [a["action"] for a in applied] == ["add", "done"] and rpc.acked == 2
    assert [x["text"] for x in rpc.shown] == ["reach 90 swordsmanship"]  # sent again after applying
    assert asyncio.run(g.sync(rpc)) == []
    assert logged[0]["by"] == "player"


def test_the_planner_sees_long_goals_and_can_change_them(tmp_path):
    w = World(tmp_path / "w.sqlite")
    g = longgoals.LongGoals(w, "Brutus")
    gid = g.add("make friends in Vesper")
    s = FakeSession(w)
    chat = scripted(reply(call("long_goal", {"action": "done", "id": gid, "why": "they are friends now"})),
                    reply(call("long_goal", {"action": "add", "text": "reach 85 swordsmanship", "why": "next"})),
                    reply(call("rest", {"seconds": 30, "why": "w"})))
    p = planner.Planner(s, "", chat_fn=chat)
    asyncio.run(p.step())
    user = chat.seen[0]["messages"][1]["content"]
    assert user.startswith("The player's goal: (none for this session)")
    assert f"{gid}. make friends in Vesper: not measured" in user
    assert "The player gave no goal for this session: choose what to work on from these." in user
    assert "long_goal" in chat.seen[0]["tools"]
    assert g.get(gid)["status"] == "done" and g.get(gid)["why"] == "they are friends now"
    assert [x["text"] for x in g.list()] == ["reach 85 swordsmanship"]


def test_a_milestone_is_remembered(tmp_path):
    w = World(tmp_path / "w.sqlite")
    g = longgoals.LongGoals(w, "Brutus")
    g.add("save 100 gold")
    s = FakeSession(w)

    async def bank(deposit, withdraw):
        return planner.Result(True, "deposited", {"gold_before": 150, "gold_carried": 40})
    s.bank = bank
    p = planner.Planner(s, "bank", chat_fn=scripted(reply(call("bank", {"deposit": "gold", "why": "w"}))))
    asyncio.run(p.step())
    rows = memory.Memory(w, "Brutus").list(search="long goal")
    assert rows and rows[0]["text"].startswith("Reached a long goal: save 100 gold") and rows[0]["importance"] == 4
