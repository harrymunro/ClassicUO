"""What a party did in a run (cuo-cym.7): fights found from the decisions, who joined when, who fell and
came back, fall backs, helps, callouts, forming up and cost."""

import json

from uo_brain import partyreport


def decision(t, health="unhurt (100%)", hostiles=(), intent="fight", actions=(), note="", corpses=(), **more):
    return {"type": "decision", "t": t, "intent": intent, "note": note, "actions": list(actions),
            "state": {"you": {"health": health}, "hostile_creatures": list(hostiles),
                      "corpses_not_yet_looted": [{"name": c} for c in corpses]}, **more}


def lich(target=False, aggressive=True, distance="nearby"):
    return {"name": "a lich", "distance": distance, "your_current_target": target, "aggressive": aggressive}


def write(path, records):
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def a_run(tmp_path):
    run = tmp_path / "20261012-000000-party-test"
    run.mkdir()
    write(run / "fleet.jsonl", [
        {"t": 0, "type": "fleet", "name": "party-test", "party": True, "leader": "Roland", "form_up": True,
         "callouts": True, "help": True, "memory": False, "strategy": "",
         "agents": [{"name": "Roland"}, {"name": "Isolde"}, {"name": "Wystan"}]},
        {"t": 1, "type": "login", "agent": "Roland", "result": "in game as Roland"},
        {"t": 2, "type": "login", "agent": "Isolde", "result": "in game as Isolde"},
        {"t": 3, "type": "login", "agent": "Wystan", "result": "in game as Wystan"}])
    attack = {"verb": "attack", "target": 0x300, "reason": "fight"}
    heal = {"verb": "cast", "spell": "Greater Heal", "target": 0x900, "reason": "companion"}
    write(run / "Roland.decisions.jsonl", [
        decision(90, intent="rest"),
        decision(100, hostiles=[lich(True)], actions=[attack]),
        decision(105, "badly wounded (30%)", [lich(True)], intent="leave", note="fall back to the rally point",
                 regroup=True, leave_why="health fell"),
        decision(108, "wounded (55%)", [lich()], intent="leave", regroup=True),
        decision(112, "lightly wounded (85%)", [lich(True)], actions=[attack]),
        decision(125, hostiles=[], intent="rest", corpses=["A Lich's Corpse"]),
        {"type": "ai_cost", "t": 101, "kind": "fight", "cost": 0.002},
        # A second fight, three minutes on, that he is in alone.
        decision(300, hostiles=[{**lich(True), "name": "a skeleton"}], actions=[attack]),
        decision(304, hostiles=[{**lich(True), "name": "a skeleton"}]),
        decision(306, hostiles=[], intent="rest", corpses=["A Lich's Corpse", "A Skeletal Corpse"])])
    write(run / "Roland.jsonl", [
        {"type": "goal", "t": 80, "tool": "hunt", "args": {"area": "Britain Graveyard"}},
        {"type": "rally", "t": 95, "key": "goal:1", "formed": True, "waited_s": 2.5, "with": ["Isolde"], "without": ["Wystan"]},
        {"type": "callout", "t": 96, "kind": "go_in", "text": "In we go."},
        {"type": "callout", "t": 104, "kind": "badly_hurt", "text": "Help me, now!"},
        {"type": "ai_cost", "t": 81, "kind": "planner", "cost": 0.001},
        {"type": "goal_result", "t": 130, "tool": "hunt", "result": {"ok": True, "result": "hunted Britain Graveyard"}},
        {"type": "goal", "t": 131, "tool": "rest", "args": {}}])
    write(run / "Isolde.decisions.jsonl", [
        decision(101.5, hostiles=[lich(True)], actions=[{"verb": "cast", "spell": "Flamestrike", "target": 0x300}]),
        decision(106, hostiles=[lich(True)], actions=[heal], note="fight a lich with Harm (0.9); heal Roland (30%)"),
        decision(118, hostiles=[lich(True)], actions=[{"verb": "cast", "spell": "Harm", "target": 0x300}])])
    write(run / "Isolde.jsonl", [{"type": "callout", "t": 107, "kind": "helping", "text": "Hold still, Roland."}])
    # The archer dies in the fight; his loop ends with him, so only his session log says so.
    write(run / "Wystan.decisions.jsonl", [
        decision(103, hostiles=[lich(True)], actions=[attack]),
        decision(109, "near death (8%)", [lich(True)], intent="flee", note="flee from a lich (danger 0.95)")])
    write(run / "Wystan.jsonl", [
        {"type": "session", "event": "hunted", "t": 111, "stopped_because": "died"},
        {"type": "goal", "t": 112, "tool": "resurrect", "args": {}, "party": True},
        {"type": "goal_result", "t": 171, "tool": "resurrect", "result": {"ok": True, "result": "resurrected"}, "party": True}])
    write(run / "where.jsonl", [{"t": 100 + i, "at": {"Roland": [1000, 1000, 0, 0], "Isolde": [1000, 1006, 0, 0],
                                                    "Wystan": [1000, 1004 + (20 if i > 8 else 0), int(i > 11), 0]}}
                                for i in range(0, 20)])
    return run


def test_a_runs_fights_deaths_and_forming_up(tmp_path):
    r = partyreport.report(a_run(tmp_path))
    assert r["leader"] == "Roland" and r["members"] == ["Roland", "Isolde", "Wystan"] and not r["void"]
    assert r["settings"]["form_up"] is True and r["settings"]["strategy"] == ""
    assert len(r["fights"]) == 2
    f = r["fights"][0]
    assert f["at_s"] == 10.0 and f["seconds"] == 18.0 and f["fought"] == ["a lich"] and f["dead_after"] == ["a lich"]
    assert f["joined"] == {"Roland": 0.0, "Isolde": 1.5, "Wystan": 3.0} and f["first_in"] == "Roland"
    assert f["joined_within_s"] == 3.0
    assert f["fell"] == ["Wystan"] and f["lowest_health"] == {"Isolde": 100, "Roland": 30, "Wystan": 8}
    assert (f["leaves"], f["fall_backs"], f["flees"]) == (0, 1, 1)
    assert f["runs_begun"][0] == {"who": "Roland", "s": 5.0, "intent": "leave", "health_pct": 30, "why": "health fell",
                                  "fall_back": True}
    assert f["helps"] == [{"by": "Isolde", "to": "Roland", "times": 1}]
    assert f["callouts"] == {"badly_hurt": 1, "helping": 1}  # "in we go" was said before it
    # Positions: the archer ran 24 tiles off for three seconds before he fell.
    assert f["from"] == "Roland" and f["tiles_from_it_at_start"] == {"Isolde": 6, "Wystan": 4}
    assert f["seconds_someone_apart"] == 3 and f["of_seconds"] == 19
    # The second fight is the warrior's alone, and names its own kill only.
    assert r["fights"][1]["joined"] == {"Roland": 0.0} and r["fights"][1]["dead_after"] == ["a skeleton"]
    assert r["deaths"] == [{"who": "Wystan", "at_s": 21.0, "fight": 1, "back_after_s": 60.0, "how": "at a healer"}]
    assert r["forming_up"] == {"formed": 1, "waited_s": [2.5], "went_in_without": [["Wystan"]], "not_formed": []}
    assert [(g["tool"], g["ok"]) for g in r["goals"]] == [("hunt", True), ("rest", None)]
    assert r["cost_usd"] == 0.003 and r["cost_by_kind"] == {"fight": 0.002, "planner": 0.001}
    text = partyreport.render(r)
    assert "fight 1 at 10.0 s, 18.0 s: a lich; dead after it: a lich" in text
    assert "joined: Roland +0.0, Isolde +1.5, Wystan +3.0 (all within 3.0 s)" in text
    assert "deaths: Wystan at 21.0 s (fight 1), back after 60.0 s (at a healer)" in text and "formed up 1 time(s)" in text


def test_a_run_without_all_its_members_is_void(tmp_path):
    run = a_run(tmp_path)
    with (run / "fleet.jsonl").open("a") as f:
        f.write(json.dumps({"t": 4, "type": "login", "agent": "Thea", "result": "not in game: TimeoutError"}) + "\n")
    r = partyreport.report(run)
    assert r["void"] and r["not_in_game"] == ["Thea"] and "VOID" in partyreport.render(r)


def test_one_death_is_counted_once_however_often_its_resurrection_is_asked_for():
    """A resurrection asked for a minute after the death, or asked for twice, is the same death; a second
    death after a sign of life is another."""
    dec = [decision(100, "badly wounded (20%)", [lich(True)]), decision(260, "wounded (60%)"),
           decision(300, "near death (5%)", [lich(True)])]
    session = [{"type": "session", "event": "hunted", "t": 104, "stopped_because": "died"},
               {"type": "goal", "t": 170, "tool": "resurrect", "args": {}},
               {"type": "goal_result", "t": 200, "tool": "resurrect", "result": {"ok": False, "result": "no offer came"}},
               {"type": "goal", "t": 205, "tool": "resurrect", "args": {}},
               {"type": "goal_result", "t": 240, "tool": "resurrect",
                "result": {"ok": True, "result": "raised where it fell by Roland, in 30 s; healthy again"}},
               {"type": "session", "event": "hunted", "t": 310, "stopped_because": "died"}]
    deaths = partyreport.deaths_of("Wystan", dec, session)
    assert [(d["t"], d["back_t"], d["how"]) for d in deaths] == [(104, 240, "raised where it fell by Roland"), (310, None, "")]
    # Alive with a point of health left reads "near death (0%)", and is no death.
    assert partyreport.deaths_of("Wystan", [decision(100, "near death (0%)", [lich(True)]), decision(103, "near death (1%)")], []) == []
    assert partyreport.health(decision(100, "near death (0%)")) == 1 and partyreport.health(decision(100, "dead (a ghost)")) == 0


def test_a_fall_back_that_became_a_leave_is_counted_as_one_and_who_was_hurt_first(tmp_path):
    run = a_run(tmp_path)
    recs = [json.loads(line) for line in (run / "Roland.decisions.jsonl").open()]
    # His fall back at 105 s ends as a leave for good three seconds on (nobody left fighting).
    recs.insert(4, decision(109, "wounded (55%)", [lich()], intent="leave", leave_why="fell back, and nobody is left"))
    write(run / "Roland.decisions.jsonl", recs)
    f = partyreport.report(run)["fights"][0]
    assert f["first_hurt"] == "Roland"  # 30% at 105 s; the archer's 8% came at 109 s
    assert (f["leaves"], f["fall_backs"]) == (1, 1) and f["runs_begun"][0]["became_a_leave"]
    assert "then left for good" in partyreport.render(partyreport.report(run))


def test_leaves_held_help_received_and_the_table(tmp_path):
    """A leave held back with the party beside it is counted each time it began, help by who received it, and
    a table by whoever kept its turns, with what every member kept of it."""
    run = a_run(tmp_path)
    held = "fight a lich (0.6); held with the party: 2 companions beside it"
    with (run / "Wystan.decisions.jsonl").open("a") as f:
        for t, note in ((103, held), (104, held), (106, "fight a lich (0.8)"), (109, held)):
            f.write(json.dumps(decision(t, hostiles=[lich(True)], note=note,
                                        actions=[{"verb": "attack", "target": 0x300, "reason": "fight"}])) + "\n")
    with (run / "Roland.jsonl").open("a") as f:
        f.write(json.dumps({"type": "table", "t": 400, "open": 1, "present": ["Roland", "Isolde"]}) + "\n")
        f.write(json.dumps({"type": "table", "t": 520, "closed": 1, "table": 1, "lines": 12, "minutes": 2.0,
                            "speakers": {"Roland": 6, "Isolde": 6}, "skipped": {}}) + "\n")
        f.write(json.dumps({"type": "table", "t": 525, "kept": [1, 2], "table": 1}) + "\n")
    with (run / "Isolde.jsonl").open("a") as f:
        f.write(json.dumps({"type": "table", "t": 526, "kept": [3, 4, 5], "table": 1}) + "\n")
    r = partyreport.report(run)
    f1 = r["fights"][0]
    assert f1["leaves_held"] == {"Wystan": 2}          # twice it began: 103-104, and again at 109
    assert f1["helped"] == {"Roland": 1}
    assert r["tables"] == [{"at_s": 310.0, "minutes": 2.0, "lines": 12, "speakers": {"Roland": 6, "Isolde": 6},
                            "skipped": {}, "memories_kept": 5}]
    text = partyreport.render(r)
    assert "leaves held back with the party: Wystan x2" in text and "received: Roland x1" in text
    assert "table at 310.0 s, 2.0 min: 12 lines (Roland 6, Isolde 6); 5 memories kept of it" in text
