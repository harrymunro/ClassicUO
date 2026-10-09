import json

import pytest

from uo_brain import fleet

T0 = 1_791_400_000.0


def write(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def test_the_example_fleet_loads_and_a_bad_one_is_refused(tmp_path):
    f = fleet.load(fleet.BRAIN_DIR / "fleets" / "graveyard-three.toml")
    assert [a.profile for a in f.agents] == ["default", "npc", "npc"] and f.server_port == 2593
    assert len({a.port for a in f.agents}) == 3 and f.agents[0].prep[0] == "[AgentReset"
    bad = tmp_path / "bad.toml"
    bad.write_text('[[agent]]\nname="a"\naccount="x"\npassword="x"\nport=1\ngoal="g"\n'
                   '[[agent]]\nname="b"\naccount="y"\npassword="y"\nport=1\ngoal="g"\n')
    with pytest.raises(ValueError, match="own port"):
        fleet.load(bad)


def test_the_report_puts_each_character_and_the_fleet_together(tmp_path):
    write(tmp_path / "fleet.jsonl", [
        {"t": T0, "type": "fleet", "name": "two", "agents": [{"name": "Brutus", "profile": "default"},
                                                             {"name": "Hale", "profile": "npc"},
                                                             {"name": "Wren", "profile": "npc"}]},
        {"t": T0 + 1, "type": "login", "agent": "Brutus", "result": "in game as Brutus"},
        {"t": T0 + 2, "type": "login", "agent": "Hale", "result": "in game as Hale"},
        {"t": T0 + 3, "type": "login", "agent": "Wren", "result": "not in game: ip already has 10 accounts"},
        {"t": T0 + 30, "type": "load", "processes": {"Brutus/client": {"cpu": 40.0, "mb": 300.0},
                                                     "server": {"cpu": 10.0, "mb": 500.0}}},
        {"t": T0 + 60, "type": "load", "processes": {"Brutus/client": {"cpu": 60.0, "mb": 310.0},
                                                     "server": {"cpu": 12.0, "mb": 505.0}}},
    ])
    for name, model, cost, kills in (("Brutus", "anthropic/claude-sonnet-5.5", 0.02, 5),
                                     ("Hale", "anthropic/claude-haiku-5.5", 0.002, 3)):
        write(tmp_path / f"{name}.jsonl", [
            {"type": "ai_cost", "t": T0, "kind": "planner", "model": model, "cost": cost, "character": name},
            {"type": "goal", "t": T0 + 1, "tool": "hunt", "args": {"area": "x"}},
            {"type": "session", "event": "hunted", "t": T0 + 1800, "kills": kills, "deaths": 0},
            {"type": "goal_result", "t": T0 + 1800, "tool": "hunt", "result": {"ok": True}},
            {"type": "ai_cost", "t": T0 + 1800, "kind": "fight", "model": "typesafe/jev-1.13", "cost": 0.001,
             "character": name},
            {"type": "session_summary", "t": T0 + 1801, "finished": None}])
        write(tmp_path / f"{name}.decisions.jsonl", [{"type": "error", "t": T0 + 5, "error": "429"}] if name == "Hale" else [])
    r = fleet.report(tmp_path)
    assert r["agents"]["Brutus"]["kills"] == 5 and r["agents"]["Hale"]["judge_errors"] == 1
    assert r["agents"]["Hale"]["models"] == ["anthropic/claude-haiku-5.5", "typesafe/jev-1.13"]
    t = r["together"]
    assert t["cost_usd"] == pytest.approx(0.024) and t["cost_per_hour_usd"] == pytest.approx(0.048)
    assert t["kills"] == 8 and t["cost_per_kill_usd"] == 0.003 and set(t["by_character"]) == {"Brutus", "Hale"}
    assert r["load"]["Brutus/client"] == {"cpu_avg": 50.0, "cpu_max": 60.0, "mb_max": 310.0}
    assert "| Hale | npc |" in fleet.markdown(r)
    assert r["agents"]["Wren"]["error"] == "not in game: ip already has 10 accounts"
    assert "| Wren | npc | | | | | | did not play: not in game: ip already has 10 accounts |" in fleet.markdown(r)
