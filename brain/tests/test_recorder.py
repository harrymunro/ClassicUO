import asyncio
import copy

from uo_brain.judge import Answers, ChoiceResult
from uo_brain.recorder import Recorder, kind_of, title_of
from uo_brain.world import World

from conftest import SNAPSHOT


class FakeJudge:
    def __init__(self, choices=None, nouls=None):
        self.asked = []
        self.choices, self.nouls = choices or {}, nouls or {}

    async def ask(self, state, questions):
        self.asked.append((state, questions))
        a = Answers(model="fake")
        for q, spec in questions.items():
            if spec["type"] == "choice":
                pick = self.choices.get(q, "other")
                a.choices[q] = ChoiceResult(pick, {pick: 1.0}, 0.9)
            else:
                a.nouls[q] = self.nouls.get(q, 0.9)
        return a


def townsperson(serial, label, dx, dy):
    return {"serial": serial, "name": label.split(" ")[0], "label": label, "body": 400, "notoriety": "invulnerable",
            "human": True, "pet": False, "monster": False, "hits_pct": None, "dead": False, "poisoned": False,
            "war_mode": False, "distance": max(abs(dx), abs(dy)), "dx": dx, "dy": dy, "dir": "east", "my_target": False}


def snap_with(*mobiles):
    s = copy.deepcopy(SNAPSHOT)
    s["mobiles"] = list(mobiles)
    return s


def test_titles():
    assert title_of("Lucy the healer") == "healer"
    assert kind_of("healer") == "healer" and kind_of("weaponsmith") == "weapon_vendor"
    assert kind_of("healer guildmistress") is None and kind_of("guard") is None
    assert kind_of("lute maker") is False


def test_townsfolk_with_known_titles_go_in_as_places(tmp_path):
    w = World(tmp_path / "w.sqlite")
    r = Recorder(w, FakeJudge())
    r.observe(snap_with(townsperson(1, "Lucy the healer", 2, 0), townsperson(2, "Ava the banker", -3, 1)))
    out = asyncio.run(r.flush())
    assert {o.split(":")[0] for o in out} == {"place healer", "place bank"}
    assert w.find_place("healer", near=[1002, 1000])[0]["source"] == "seen"


def test_an_unknown_title_is_a_question_for_jev(tmp_path):
    w = World(tmp_path / "w.sqlite")
    judge = FakeJudge(choices={"kind_0": "mage_shop"})
    r = Recorder(w, judge)
    r.observe(snap_with(townsperson(5, "Merlin the arcanist", 1, 1)))
    out = asyncio.run(r.flush())
    assert out == ["place mage_shop: wilderness near 1000,1000 arcanist at 1001,1001"]
    assert "kind_0" in judge.asked[0][1]


def test_players_and_titleless_people_are_never_recorded(tmp_path):
    w = World(tmp_path / "w.sqlite")
    r = Recorder(w, FakeJudge())
    pk = townsperson(9, "Xx_Ganker_xX", 1, 0) | {"notoriety": "murderer", "threat": "red"}
    r.observe(snap_with(pk, townsperson(10, "Bob", 2, 0)))
    assert asyncio.run(r.flush()) == []


def test_a_creature_seen_again_and_again_becomes_a_spawn(tmp_path):
    w = World(tmp_path / "w.sqlite")
    r = Recorder(w, FakeJudge(nouls={"regular_0": 0.9}))
    snap = copy.deepcopy(SNAPSHOT)
    snap["mobiles"] = snap["mobiles"][:1]  # one orc
    for _ in range(6):
        r.observe(snap)
    key = next(iter(r._sightings))
    r._sightings[key].first -= 120  # watched for two minutes
    out = asyncio.run(r.flush())
    assert out == ["spawn: orc in wilderness near 1000,1000"]
    assert w.what_spawns(area="wilderness near 1000,1000")


def test_shop_signs_go_in_as_places(tmp_path):
    w = World(tmp_path / "w.sqlite")
    r = Recorder(w, FakeJudge())
    snap = snap_with()
    snap["signs"] = [{"text": "The Healer's Hut", "x": 1470, "y": 1610}]
    r.observe(snap)
    r.observe(snap)
    assert r.written == ["sign: The Healer's Hut at 1470,1610"]
    assert w.place("Healer's Hut")[0]["kind"] == "shop_sign"
