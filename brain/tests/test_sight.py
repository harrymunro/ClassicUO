"""Line of sight (cuo-ehm): the snapshot's in_sight in the state, target choice, spells and the checks."""

import asyncio
import copy

from uo_brain import bench, policy, questions, sightcheck, state
from uo_brain.judge import Answers, ChoiceResult, HeuristicJudge

from conftest import SNAPSHOT

CFG = policy.PolicyConfig()


def answers(intents, intent="fight", target="t1", conf=0.9, spell="s1"):
    a = Answers()
    a.choices["intent"] = ChoiceResult(intent, {k: (1.0 if k == intent else 0.0) for k in intents}, conf)
    a.choices["target"] = ChoiceResult(target, {target: 1.0}, 0.9)
    a.choices["spell"] = ChoiceResult(spell, {spell: 0.8}, 0.8)
    a.nouls["in_danger"] = 0.1
    return a


def archer_snap():
    snap = copy.deepcopy(SNAPSHOT)
    p = snap["player"]
    p.update({"weapon": "a bow", "ranged": {"kind": "bow", "ammo": "arrows", "range": 10}})
    p["skills"] = {"Archery": 80.0, "Tactics": 80.0, "Healing": 80.0, "Anatomy": 80.0}
    p["supplies"].update({"arrows": 120})
    # t1, the nearer orc, stands behind a wall; t2 is in the open, further off.
    snap["mobiles"][0].update({"distance": 5, "dx": 0, "dy": -5, "my_target": False, "in_sight": False,
                               "war_mode": False})
    snap["mobiles"][1].update({"distance": 7, "dx": 7, "dy": 0, "in_sight": True})
    snap["agent"]["engaged"] = 0
    return snap


def test_state_words_a_creature_out_of_sight():
    sit = state.build(archer_snap(), set(), [])
    t1, t2 = sit.hostiles
    assert not t1.in_sight and t2.in_sight
    assert t1.info["out_of_sight"].startswith("yes")
    assert "out_of_sight" not in t2.info
    qs = questions.build(sit)
    assert "out of sight" in qs["target"]["criteria"]["t1"]
    assert "out of sight" not in qs["target"]["criteria"]["t2"]
    assert "a creature in sight comes first" in qs["target"]["instructions"]["guidance"]


def test_without_in_sight_everything_counts_as_seen(snapshot):
    sit = state.build(snapshot, set(), [])
    assert all(h.in_sight for h in sit.hostiles)
    assert "in sight comes first" not in questions.build(sit)["target"]["instructions"]["guidance"]


def test_archer_takes_the_creature_in_sight_over_the_nearer_one():
    sit = state.build(archer_snap(), set(), [])
    # Jev's own pick of the one behind the wall is passed over while another is in sight.
    dec = policy.decide(sit, answers(questions.ARCHER_INTENTS, target="t1"), policy.Memory(), CFG)
    assert dec.target.serial == 0x101
    # With nothing in sight, the archer still takes one on: the client walks until it sees it.
    snap = archer_snap()
    snap["mobiles"][1]["in_sight"] = False
    dec = policy.decide(state.build(snap, set(), []), answers(questions.ARCHER_INTENTS, target="t1"),
                        policy.Memory(), CFG)
    assert dec.target.serial == 0x100
    assert dec.actions[0]["verb"] == "attack" and "moving to see it" in dec.note


def test_a_warrior_keeps_its_pick_out_of_sight(snapshot):
    snapshot["mobiles"][1]["in_sight"] = False
    sit = state.build(snapshot, set(), [])
    dec = policy.decide(sit, answers(questions.INTENTS, target="t2"), policy.Memory(), CFG)
    assert dec.target.serial == 0x101


def test_mage_casts_only_at_a_creature_in_sight(mage):
    mage["mobiles"][0]["in_sight"] = False
    mage["mobiles"][1]["in_sight"] = False
    sit = state.build(mage, set(), [])
    dec = policy.decide(sit, answers(questions.MAGE_INTENTS, target="t1", spell="s2"), policy.Memory(), CFG)
    assert dec.target.serial == 0x100
    assert not [a for a in dec.actions if a["verb"] == "cast"]
    assert "moving to see it" in dec.note
    # In sight again, it casts.
    mage["mobiles"][0]["in_sight"] = True
    dec = policy.decide(state.build(mage, set(), []), answers(questions.MAGE_INTENTS, target="t1", spell="s2"),
                        policy.Memory(), CFG)
    assert [a for a in dec.actions if a["verb"] == "cast"]


def test_no_field_at_a_creature_out_of_sight(mage):
    names = {s["name"] for s in mage["magic"]["spells"]}
    mage["magic"]["spells"].append({"id": 28, "name": "Wall of Stone", "circle": 3, "mana": 9, "target": "neutral",
                                    "missing": ""})
    sit = state.build(mage, set(), [])
    assert "Wall of Stone" in [c.name for c in sit.spells]
    mage["mobiles"][0]["in_sight"] = False
    mage["mobiles"][1]["in_sight"] = False
    sit = state.build(mage, set(), [])
    assert "Wall of Stone" not in [c.name for c in sit.spells]
    assert names <= {s["name"] for s in mage["magic"]["spells"]}


def test_seeking_one_in_reach_but_out_of_sight_fights_it():
    snap = archer_snap()
    del snap["mobiles"][1]  # only the orc behind the wall, 5 tiles off: within reach, out of sight
    sit = state.build(snap, set(), [])
    dec = policy.decide(sit, answers(questions.ARCHER_INTENTS, intent="seek", target="t1"), policy.Memory(), CFG)
    assert "seek" not in dec.masked
    assert dec.intent == "fight" and dec.target.serial == 0x100
    assert dec.actions[0]["verb"] == "attack" and dec.actions[0]["range"] == CFG.bow_range


def test_signature_changes_when_a_creature_goes_out_of_sight():
    snap = archer_snap()
    before = state.build(snap, set(), []).signature()
    snap["mobiles"][0]["in_sight"] = True
    assert state.build(snap, set(), []).signature() != before


def test_heuristic_archer_shoots_from_range():
    sit = state.build(archer_snap(), set(), [])
    ans = asyncio.run(HeuristicJudge().ask(sit.state, questions.build(sit)))
    assert ans.choices["intent"].choice == "fight"


def test_nosight_judge_label():
    assert bench.JudgeSpec("jev/nosight").sight is False
    assert bench.JudgeSpec("jev/nosight").kind == "jev"
    assert bench.JudgeSpec("heuristic").sight is True
    assert set(bench.SIGHT) <= set(bench.SCENARIOS)


def test_sight_check_reads_the_servers_lines():
    lines = ["AgentSight 1424 1694 0: 1420,1690,14,1,1,1;1430,1700,34,0,21,0;",
             "Spawn: something else",
             "AgentSight 1424 1694 0: 1411,1699,14,0,1,1;"]
    samples = sightcheck.parse(lines)
    assert len(samples) == 3
    assert samples[0] == {"from": [1424, 1694, 14], "x": 1420, "y": 1690, "zm": 14, "mobile": True, "zg": 1,
                          "ground": True}
    assert samples[1]["mobile"] is False and samples[1]["zg"] == 21
