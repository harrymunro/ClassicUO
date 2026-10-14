"""Helping a hurt companion put to system one (cuo-cym.26): the floors as code, the moment in words, the pick, and
the check script's moments and rule."""

import importlib.util
from pathlib import Path

from uo_brain import helping
from uo_brain.judge import ChoiceResult

SPEC = importlib.util.spec_from_file_location("helping_check", Path(__file__).resolve().parents[1] / "scripts" / "helping_check.py")
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)

M = check.M


def ways(m):
    sit, _ = check.build(m)
    return [(mate.name, how) for mate, how in helping.ways(sit)]


def test_whom_it_can_help_and_how_are_code():
    archer = M("t", "", "Wystan", doing="sk",
               mates={"Roland": (50, 0, (1, 1)), "Thea": (60, 0, (4, 0)), "Corwin": (40, 0, (9, 0)),
                      "Isolde": (97, 0, (1, 0))},
               creatures=[("sk", "a skeleton", (2, 2), "Roland", 100)], want=(None,), why="")
    # Within reach a bandage, a few tiles off a walk, past HELP_WALK nothing; nobody unhurt.
    assert ways(archer) == [("Roland", "bandage"), ("Thea", "walk")]
    # One bandage at a time: with one on someone, nothing; none without bandages; and none with healing off.
    assert ways({**archer, "bandaging": True}) == [] and ways({**archer, "band": 0}) == []
    sit, _ = check.build(archer)
    sit.raw["agent"]["authority"] = {"move": "suggest"}
    assert [(m.name, how) for m, how in helping.ways(sit)] == [("Roland", "bandage")]  # no walk without moving
    sit.raw["agent"]["authority"] = {"heal": "off"}
    assert helping.ways(sit) == []
    # A mage heals from where it stands, within spell range and in sight, with the mana for it.
    mage = M("t", "", "Isolde", mana=40, mates={"Roland": (50, 0, (6, 0)), "Wystan": (30, 0, (11, 0))},
             creatures=[], want=(None,), why="")
    assert ways(mage) == [("Roland", "heal")]
    assert ways({**mage, "mana": 9}) == []
    sit, _ = check.build(mage)
    sit.mates[0].in_sight = False
    assert helping.ways(sit) == []
    sit.mates[0].in_sight, sit.mates[0].dead = True, True
    assert helping.ways(sit) == []


def test_the_moment_in_words():
    m = next(x for x in check.FIRST if x["id"] == "r07")
    sit, drops = check.build(m)
    opts = helping.options(helping.ways(sit))
    st = helping.help_state(sit, drops)
    wystan = next(c for c in st["companions"] if c["name"] == "Wystan")
    assert wystan["health"] == "69%, down 31 points in the last few seconds"
    assert wystan["attacked_by"] == ["a bone knight (3 tiles from you)"]
    corwin = next(c for c in st["companions"] if c["name"] == "Corwin")
    assert corwin["health"] == "54%, not falling" and "being_healed" in corwin
    assert st["you"]["doing"].startswith("its pet is fighting a bone knight") and st["you"]["bandages"] == "100 left"
    assert st["the_fight"].startswith("on: ")
    q = helping.help_question(sit, opts, drops)
    assert q["type"] == "choice" and set(q["criteria"]) == {"h1", "h2", "h3", "none"}
    assert q["criteria"]["h1"].startswith("Wystan (archer, 69%, down 31") and "within reach" in q["criteria"]["h1"]
    # A walk says what is by the way: the bard 8 tiles off has a bone knight by it.
    assert "walk the 8 tiles" in q["criteria"]["h3"] and "a bone knight" in q["criteria"]["h3"]
    assert "another companion is healing them" in q["criteria"]["h3"]
    # The mage's state has its mana and what a heal costs; the fight over says so.
    mage = M("t", "", "Isolde", mana=30, intent="rest", mates={"Roland": (40, 0, (3, 0))}, creatures=[],
             want=(None,), why="")
    sit, drops = check.build(mage)
    st = helping.help_state(sit, drops)
    assert st["you"]["mana"] == "30% (30 points; a Greater Heal costs 11)" and st["the_fight"].startswith("over")
    assert st["creatures"] == ["none in view"]


def test_the_pick_is_a_companion_at_the_cut_or_nobody():
    m = next(x for x in check.FIRST if x["id"] == "r07")
    sit, _ = check.build(m)
    opts = helping.options(helping.ways(sit))
    sure = ChoiceResult("h1", {"h1": 0.7, "h2": 0.1, "none": 0.2}, 0.7)
    assert helping.pick(sure, opts)[0].name == "Wystan" and helping.pick(sure, opts)[1] == "bandage"
    assert helping.pick(ChoiceResult("h1", {"h1": 0.45, "none": 0.4, "h2": 0.15}, 0.45), opts) is None
    assert helping.pick(ChoiceResult("none", {"h1": 0.3, "none": 0.7}, 0.7), opts) is None


def test_the_check_scripts_moments_and_rule():
    # Every moment offers someone (the question is asked in it), and the ids are unique.
    for m in check.FIRST + check.SECOND:
        sit, _ = check.build(m)
        assert helping.ways(sit), m["id"]
    ids = [m["id"] for m in check.FIRST + check.SECOND]
    assert len(ids) == len(set(ids)) and len(check.FIRST) == 42 and len(check.SECOND) == 28
    by = {m["id"]: m for m in check.FIRST + check.SECOND}
    # The rule as policy.help_mates does it: a bandage within reach under 60%, nothing for one falling fast at
    # 69%, the lowest of two for a mage's one heal, no walk while the fight is on, and a walk once it is over.
    assert check.rule(by["w05"]) == ("Corwin", "bandage")
    assert check.rule(by["r07"]) is None
    assert check.rule(by["w07"]) == ("Corwin", "heal")
    assert check.rule(by["w11"]) is None
    assert check.rule(by["w18"]) == ("Wystan", "walk")
    assert check.judged(by["w07"], ("Corwin", "heal")) == "wrong_one"
    assert check.judged(by["r08"], ("Roland", "walk")) == "walk_not_wanted"
    assert check.judged(by["r10"], None) == "right" and check.judged(by["r10"], ("Roland", "heal")) == "right"
