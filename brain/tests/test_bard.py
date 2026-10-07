import copy

import pytest
from conftest import SNAPSHOT

from uo_brain import policy, questions, state
from uo_brain.judge import Answers, ChoiceResult

CFG = policy.PolicyConfig()
ORC, CAPTAIN = 0x100, 0x101


@pytest.fixture
def bard():
    snap = copy.deepcopy(SNAPSHOT)
    p = snap["player"]
    p.update({"name": "Lyra", "weapon": ""})
    p["skills"] = {"Musicianship": 90.0, "Provocation": 90.0, "Peacemaking": 90.0, "Discordance": 90.0,
                   "Healing": 60.0, "Wrestling": 50.0}
    p["supplies"]["instrument"] = True
    snap["mobiles"][0].update({"distance": 5, "dx": 5, "dy": 0, "my_target": False, "hits_pct": 100})
    snap["mobiles"][1].update({"distance": 7, "dx": -7, "dy": 0})
    snap["agent"]["engaged"] = 0
    return snap


def sit_of(snapshot):
    return state.build(snapshot, set(), [])


def answers(target="t1", song=None, onto=None, conf=0.9):
    a = Answers()
    a.choices["intent"] = ChoiceResult("fight", {k: (1.0 if k == "fight" else 0.0) for k in questions.BARD_INTENTS}, conf)
    a.choices["target"] = ChoiceResult(target, {target: 1.0}, 0.9)
    if song:
        a.choices["song"] = ChoiceResult(song, {song: 0.9}, 0.9)
    if onto:
        a.choices["onto"] = ChoiceResult(onto, {onto: 0.8, target: 0.2}, 0.8)
    a.nouls["in_danger"] = 0.1
    return a


def test_bard_skills_make_a_bard(bard, snapshot):
    assert state.archetype_of(bard) == "bard"
    assert state.archetype_of(snapshot) == "warrior"
    you = sit_of(bard).state["you"]
    assert you["weapon"].startswith("music") and you["instrument"] == "in the pack"


def test_bard_questions_ask_for_a_song_and_whom_the_incited_one_attacks(bard):
    qs = questions.build(sit_of(bard))
    assert "bard" in qs["intent"]["instructions"]["role"]
    assert set(qs["song"]["criteria"]) == {"provoke", "peace", "discord", "none"}
    assert set(qs["onto"]["criteria"]) == {"t1", "t2"}
    bard["mobiles"] = bard["mobiles"][:1]
    assert "onto" not in questions.build(sit_of(bard))


def test_jev_incites_one_against_the_other(bard):
    dec = policy.decide(sit_of(bard), answers(target="t2", song="provoke", onto="t1"), policy.Memory(), CFG, now=10.0)
    [song] = dec.actions
    assert song["verb"] == "skill" and song["name"] == "Provocation" and song["targets"] == [CAPTAIN, ORC]


def test_onto_never_names_the_incited_creature(bard):
    dec = policy.decide(sit_of(bard), answers(target="t1", song="provoke", onto="t1"), policy.Memory(), CFG, now=10.0)
    assert dec.actions[0]["targets"] == [ORC, CAPTAIN]


def test_rule_of_thumb_without_a_sure_answer(bard):
    # Two creatures: incite the stronger (more hits in the bestiary, else the first) against the other.
    dec = policy.decide(sit_of(bard), answers(), policy.Memory(), CFG, now=10.0)
    assert dec.actions[0]["name"] == "Provocation" and len(dec.actions[0]["targets"]) == 2
    # One creature close: calm it.
    bard["mobiles"] = bard["mobiles"][:1]
    bard["mobiles"][0].update({"distance": 1, "dx": 1})
    dec = policy.decide(sit_of(bard), answers(), policy.Memory(), CFG, now=10.0)
    assert dec.actions[0]["name"] == "Peacemaking" and dec.actions[0]["targets"] == [ORC]


def test_one_song_every_few_seconds_and_not_at_the_same_creatures(bard):
    mem = policy.Memory()
    first = policy.decide(sit_of(bard), answers(song="provoke", onto="t2"), mem, CFG, now=10.0)
    assert first.actions
    soon = policy.decide(sit_of(bard), answers(song="provoke", onto="t2"), mem, CFG, now=12.0)
    assert soon.actions == [] and soon.note.startswith("between songs")
    later = policy.decide(sit_of(bard), answers(song="provoke", onto="t2"), mem, CFG, now=20.0)
    assert later.actions == [] and later.note == "the creatures are busy with each other"


def test_no_instrument_no_fight(bard):
    bard["player"]["supplies"]["instrument"] = False
    dec = policy.decide(sit_of(bard), answers(), policy.Memory(), CFG, now=10.0)
    assert dec.intent != "fight" and "fight" in dec.masked
