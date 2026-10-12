import argparse

import pytest

from uo_brain import fleet, rules


def test_the_sets_load_and_name_only_rules():
    assert {"realistic", "run-always"} <= set(rules.names())
    real = rules.load("realistic")
    assert real["travel"] == "walk" and real["walk"] and real["roleplay"]
    assert rules.load("run-always")["travel"] == "run"
    assert "walk their trips" in rules.describe("realistic")


def test_a_bad_set_is_refused(tmp_path):
    (tmp_path / "odd.toml").write_text('travel = "fly"\n')
    (tmp_path / "typo.toml").write_text("smalltalk = true\n")
    with pytest.raises(ValueError, match="walk or run"):
        rules.load("odd", tmp_path)
    with pytest.raises(ValueError, match="isn't a rule"):
        rules.load("typo", tmp_path)
    with pytest.raises(ValueError, match="no rules called"):
        rules.load("nope", tmp_path)


def test_a_flag_given_wins_over_the_set():
    args = argparse.Namespace(travel=None, party_walk=False, no_pace=True, no_small_talk=False, roleplay=False,
                              no_callouts=False, no_party_help=False)
    applied = rules.apply(args, "realistic")
    assert args.travel == "walk" and args.party_walk and args.roleplay
    assert args.no_pace  # --no-pace was given: it stays
    assert "pace" not in applied and applied["travel"] == "walk"
    args = argparse.Namespace(travel="run", party_walk=False, no_pace=False, no_small_talk=False, roleplay=False,
                              no_callouts=False, no_party_help=False)
    rules.apply(args, "realistic")
    assert args.travel == "run"


FLEET = """
name = "t"
rules = "realistic"
roleplay = false
[[agent]]
name = "A"
account = "a"
password = "a"
port = 5001
goal = "hunt"
[[agent]]
name = "B"
account = "b"
password = "b"
port = 5002
goal = "hunt"
travel = "run"
"""


def test_a_fleet_takes_a_set_under_its_own_settings(tmp_path):
    path = tmp_path / "f.toml"
    path.write_text(FLEET)
    f = fleet.load(path)
    assert f.rules == "realistic" and f.travel == "walk" and f.walk and f.small_talk
    assert not f.roleplay  # the file's own setting wins over the set
    assert [a.travel for a in f.agents] == ["", "run"]
    f = fleet.load(path, ["rules=\"run-always\""])
    assert f.travel == "run" and not f.walk and not f.pace
    path.write_text(FLEET.replace('travel = "run"', 'travel = "crawl"'))
    with pytest.raises(ValueError, match="walk or run"):
        fleet.load(path)
