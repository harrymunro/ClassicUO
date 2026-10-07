import json
import sys

import pytest

from uo_brain import cli, logs, outcomes
from uo_brain.world import World

T0 = 1_791_340_000.0


def hostile(name, distance="adjacent, within weapon reach", target=False, hid="t1"):
    return {"id": hid, "name": name, "health": "unhurt", "distance": distance, "direction": "east",
            "your_current_target": target, "aggressive": True}


def decision(t, hp, fighting="nobody", hostiles=(), bandages=50, potions=5, intent="rest", target=None, note=None,
             mage=False, actions=()):
    you = {"health": f"wounded ({hp}%)", "fighting": fighting, "heal_potions_left": potions, "supplies": "plenty"}
    if mage:
        you["mana"] = "plenty (80%)"
    else:
        you["bandages_left"] = bandages
    return {"type": "decision", "t": T0 + t, "judge": "jev/openrouter",
            "state": {"you": you, "hostile_creatures": list(hostiles), "corpses_not_yet_looted": [],
                      "recent_events": []},
            "answers": {"choices": {}}, "intent": intent, "confidence": 0.9, "actions": list(actions), "results": [],
            "note": note or f"{intent} (0.90)", "target": target, "spell": None}


def fight(t, hp, name, serial, bandages=50, potions=5, keep=True, **kw):
    note = f"{'keep fighting' if keep else 'fight'} {name} (0.88)"
    return decision(t, hp, fighting=name, hostiles=[hostile(name, target=True)], bandages=bandages, potions=potions,
                    intent="fight", target=serial, note=note, **kw)


def graveyard_hunt(start=0.0, kills=4, deaths=0):
    """Ten minutes at the graveyard: two wraiths (6 bandages, 1 potion, 50% health between
    them) and a skeleton (1 bandage, 5%), then the summary and the hunted record."""
    s = start
    recs = [{"type": "goal", "t": T0 + s - 30, "tool": "hunt", "args": {"area": "Britain Graveyard", "minutes": 10}},
            {"type": "session", "event": "travelled", "t": T0 + s - 5, "to": "Britain Graveyard", "state": "arrived"},
            fight(s + 10, 100, "a wraith", 0x10, bandages=50, keep=False),
            fight(s + 11, 80, "a wraith", 0x10, bandages=48),
            fight(s + 12, 70, "a wraith", 0x10, bandages=47, potions=4),
            decision(s + 13, 75, bandages=47, potions=4),
            fight(s + 100, 90, "Vorgak (a wraith)", 0x11, bandages=47, potions=4, keep=False),
            fight(s + 101, 70, "Vorgak (a wraith)", 0x11, bandages=44, potions=4),
            decision(s + 103, 70, bandages=44, potions=4),
            # Fighting nobody, a skeleton close: its costs.
            decision(s + 200, 95, hostiles=[hostile("a skeleton", "close, a few steps away")], bandages=44, potions=4),
            fight(s + 201, 90, "a skeleton", 0x12, bandages=43, potions=4, keep=False),
            # After a long gap (dead, fleeing or looting): nothing charged.
            fight(s + 400, 20, "a skeleton", 0x12, bandages=30, potions=4),
            {"type": "summary", "t": T0 + s + 598, "minutes": 10.0,
             "client_stats": {"kills": kills, "deaths": deaths, "bandages": 20, "heal_potions": 1}},
            {"type": "session", "event": "hunted", "t": T0 + s + 600, "area": "Britain Graveyard", "kit": "warrior",
             "minutes": 10.0, "kills": kills, "deaths": deaths, "bandages_used": 18, "heal_potions_used": 1,
             "gold_gained": 300, "stopped_because": "10 minutes up"}]
    return recs


def write(path, recs):
    path.write_text("".join(json.dumps(r) + "\n" for r in recs) + "{not json\n")
    return path


@pytest.fixture
def w(tmp_path):
    with World.open("test", root=tmp_path / "worlds") as w:
        yield w


def test_reading_numbers_back_out_of_words():
    assert logs.creature_kind("Vitavi (a ratman)") == "ratman"
    assert logs.creature_kind("an ogre lord") == "ogre lord" and logs.creature_kind("An Ogre Lord") == "ogre lord"
    assert logs.creature_kind("Vorgak") == "vorgak"
    assert logs.health_pct({"you": {"health": "near death (9%)"}}) == 9
    assert logs.fight_target(fight(0, 50, "Frecckeki (a ratman)", 7)) == (7, "Frecckeki (a ratman)")
    mage = decision(0, 50, intent="fight", target=8, note="fight an orc with Energy Bolt (0.71)")
    assert logs.fight_target(mage) == (8, "an orc")
    flee = decision(0, 50, intent="flee", target=9, note="flee from an orc (danger 0.9)")
    assert logs.fight_target(flee) is None
    assert outcomes.plural("wraith") == "wraiths" and outcomes.plural("lich") == "liches"
    assert outcomes.plural("headless one") == "headless ones" and outcomes.plural("harpy") == "harpies"
    assert outcomes.plural("ratman") == "ratmen"


def test_costs_go_to_the_creature_being_fought():
    ds = [r for r in graveyard_hunt() if r["type"] == "decision"]
    costs = outcomes.creature_costs(ds)
    wraith, skel = costs["wraith"], costs["skeleton"]
    assert wraith.fights == 2  # two serials, one with a personal name
    assert (wraith.bandages, wraith.heal_potions, wraith.health_pct_lost) == (6, 1, 50)
    # 5 points and a bandage while the skeleton was close; the jump after the 199 s gap is not charged.
    assert (skel.fights, skel.bandages, skel.health_pct_lost) == (1, 1, 5)


def test_import_records_each_loop_and_a_note(w, tmp_path):
    log = write(tmp_path / "session1.jsonl", graveyard_hunt() + graveyard_hunt(start=1000, kills=6, deaths=1)
                + graveyard_hunt(start=2000, kills=5))
    out = outcomes.import_logs(w, [log])
    assert out["logs"] == [{"log": "session1.jsonl", "loops": 3, "rows": out["logs"][0]["rows"],
                            "areas": ["Britain Graveyard"]}]
    [s] = w.outcomes("britain graveyard")
    assert (s["kit"], s["loops"], s["sessions"], s["deaths"]) == ("warrior", 3, 1, 1)
    assert s["averages"]["kills_per_loop"] == 5.0 and s["averages"]["gold_per_loop"] == 300
    assert s["averages"]["bandages_per_kill"] == round((20 / 4 + 20 / 6 + 20 / 5) / 3, 2)  # the client's count
    wraith = s["creatures"][0]
    assert wraith == {"creature": "wraith", "fights": 6, "bandages": 18, "bandages_per_fight": 3.0,
                      "heal_potions": 3, "heal_potions_per_fight": 0.5, "health_pct_lost": 150,
                      "health_pct_lost_per_fight": 25.0}
    note = out["areas"][0]["note"]
    assert note.startswith("Britain Graveyard: 5 kills a loop for the warrior kit over 3 loops of about 10 min, "
                           "1 death in 3 loops")
    assert "wraiths cost the most bandages, 3 a fight over 6 fights, then skeletons at 1 a fight." in note
    assert w.notes(area="Britain Graveyard")[0]["text"] == note and s["note"] == note


def test_reimport_replaces_the_log_and_the_live_rows(w, tmp_path):
    log = write(tmp_path / "s.jsonl", graveyard_hunt())
    w.add_outcome("Britain Graveyard", "warrior", "kills_per_hour", 24, session="s.jsonl")  # what Session.hunt wrote
    w.add_outcome("Britain Graveyard", "warrior", "kills_per_hour", 99, session="other.jsonl")
    outcomes.import_logs(w, [log])
    n = w.db.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
    outcomes.import_logs(w, [log])
    assert w.db.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0] == n
    [s] = w.outcomes("Britain Graveyard")
    assert s["loops"] == 2 and s["sessions"] == 2  # this log's one loop, and the other session's
    assert len(w.notes(area="Britain Graveyard", limit=50)) == 1


def test_a_session_pair_reads_the_same_from_either_file(w, tmp_path):
    recs = graveyard_hunt()
    write(tmp_path / "s.jsonl", [r for r in recs if r["type"] not in ("decision", "summary")])
    write(tmp_path / "s.decisions.jsonl", [r for r in recs if r["type"] in ("decision", "summary")])
    a = outcomes.import_logs(w, [tmp_path / "s.jsonl"])
    b = outcomes.import_logs(w, [tmp_path / "s.decisions.jsonl"])
    assert a["logs"][0]["log"] == b["logs"][0]["log"] == "s.jsonl"
    assert a["areas"][0]["note"] == b["areas"][0]["note"]
    assert w.outcomes("Britain Graveyard")[0]["creatures"][0]["fights"] == 2


def test_tactical_runs_need_an_area(w, tmp_path):
    run = [r for r in graveyard_hunt() if r["type"] in ("decision", "summary")]
    log = write(tmp_path / "arena.jsonl", run)
    assert outcomes.import_logs(w, [log])["logs"][0]["loops"] == 0
    out = outcomes.import_logs(w, [log], area="Test arena")
    assert out["logs"][0]["loops"] == 1
    [s] = w.outcomes("Test arena")
    assert s["averages"]["kills_per_loop"] == 4 and "gold_per_loop" not in s["averages"]


def test_a_mage_is_told_from_its_state_and_costs_health(w, tmp_path):
    hunt = graveyard_hunt()
    for r in hunt:
        if r["type"] == "decision":
            r["state"]["you"].pop("bandages_left")
            r["state"]["you"]["mana"] = "plenty"
        if r.get("event") == "hunted":
            del r["kit"]
    out = outcomes.import_logs(w, [write(tmp_path / "m.jsonl", hunt + graveyard_hunt(start=1000)[2:])])
    kits = {k["kit"] for k in out["areas"][0]["kits"]}
    assert kits == {"mage", "warrior"}
    assert "for the mage kit" in out["areas"][0]["note"]


def test_planner_tool_and_cli(w, tmp_path, monkeypatch, capsys):
    assert w.call_tool("outcomes", {"area": "Britain Graveyard"})["note"] == "nothing recorded yet"
    log = write(tmp_path / "s.jsonl", graveyard_hunt())
    monkeypatch.setattr(sys, "argv", ["uo-brain", "world", "--shard", "test", "--root", str(tmp_path / "worlds"),
                                      "outcomes", str(log)])
    cli.main()
    out = json.loads(capsys.readouterr().out)
    assert out["logs"][0]["loops"] == 1 and out["areas"][0]["area"] == "Britain Graveyard"
    got = w.call_tool("outcomes", {"area": "graveyard", "kit": "warrior"})
    assert got[0]["area"] == "Britain Graveyard" and got[0]["loops"] == 1
