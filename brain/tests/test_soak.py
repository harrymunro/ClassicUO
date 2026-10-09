import json

from uo_brain import soak

T0 = 1_791_340_000.0


def write(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


def test_report_puts_goals_hunts_time_and_cost_together(tmp_path):
    log = write(tmp_path / "soak.jsonl", [
        {"type": "planner", "t": T0, "usage": {"cost": 0.015}},
        {"type": "goal", "t": T0 + 1, "tool": "buy", "args": {"item": "bandage", "count": 40, "why": "restock"}},
        {"type": "session", "event": "travelled", "t": T0 + 31, "to": "Britain healer", "state": "arrived",
         "seconds": 30, "stuck_at": [[1, 2]]},
        {"type": "session", "event": "buy", "t": T0 + 60, "item": "bandage", "got": 40},
        {"type": "goal_result", "t": T0 + 60, "tool": "buy", "result": {"ok": True, "result": "bought 40"}},
        {"type": "planner", "t": T0 + 62, "usage": {"cost": 0.015}},
        {"type": "goal", "t": T0 + 63, "tool": "hunt", "args": {"area": "Britain Graveyard", "why": "supplied"}},
        {"type": "routine", "t": T0 + 300, "answers": {"input_tokens": 1000}},
        {"type": "session", "event": "hunted", "t": T0 + 663, "kills": 9, "deaths": 0, "gold_gained": 300,
         "stopped_because": "Jev: time to head back (0.7)"},
        {"type": "goal_result", "t": T0 + 663, "tool": "hunt", "result": {"ok": True, "result": "hunted"}},
        {"type": "goal", "t": T0 + 665, "tool": "bank", "args": {"deposit": "gold", "why": "bank it"}},
        {"type": "session", "event": "bank", "t": T0 + 700, "detail": "moved 1 item, gold -300"},
        {"type": "goal_result", "t": T0 + 720, "tool": "bank", "result": {"ok": True, "result": "banked"}},
    ])
    write(tmp_path / "soak.decisions.jsonl", [{"type": "decision", "t": T0 + 100, "answers": {"input_tokens": 2000}}])
    dis = write(tmp_path / "dis.jsonl", [{"t": T0 + 600, "what": "despawn the graveyard"}])
    r = soak.report(log, dis, price_per_million=0.05)
    assert (r["hunts"], r["kills"], r["deaths"], r["gold_banked"], r["bought"]) == (1, 9, 0, 300, {"bandage": 40})
    assert r["goal_counts"] == {"bank": 1, "buy": 1, "hunt": 1}
    assert r["time_minutes"] == {"bank": 0.9, "buy": 1.0, "hunt": 10.0, "between goals (planner)": 0.1}
    assert r["travel"] == {"trips": 1, "arrived": 1, "stuck_spots": 1, "minutes": 0.5}
    assert r["cost_usd"] == {"system1": 0.00015, "system2": 0.03, "total": 0.03015}
    assert not r["costs_recorded"]
    assert r["disruptions"] == [{"minute": 10.0, "what": "despawn the graveyard", "next_goals": ["bank: bank it"]}]
    assert "1 hunts, 9 kills, 0 deaths; 300 gold banked (300 gold gained in hunts" in soak.markdown(r)


def test_a_death_outside_a_hunt_counts(tmp_path):
    log = write(tmp_path / "s.jsonl", [
        {"type": "goal", "t": T0, "tool": "rest", "args": {"seconds": 300, "why": "wait for respawn"}},
        {"type": "goal_result", "t": T0 + 300, "tool": "rest", "result": {"ok": True, "result": "rested 300 s",
                                                                          "health": "0/95"}},
        {"type": "session_summary", "t": T0 + 301, "finished": "the character died"},
    ])
    assert soak.report(log)["deaths"] == 1


def test_recorded_costs_are_used_and_checked_against_the_key(tmp_path):
    log = write(tmp_path / "s.jsonl", [
        {"type": "openrouter_key", "t": T0, "usage": 10.0},
        {"type": "ai_cost", "t": T0 + 1, "kind": "planner", "role": "system2", "model": "anthropic/claude-sonnet-5.5",
         "input_tokens": 4000, "output_tokens": 100, "cost": 0.02},
        {"type": "goal", "t": T0 + 2, "tool": "hunt", "args": {"area": "Britain Graveyard", "why": "go"}},
        {"type": "ai_cost", "t": T0 + 300, "kind": "routine", "role": "system1", "model": "typesafe/jev-1.13",
         "input_tokens": 1000, "cost": 0.000042},
        {"type": "session", "event": "hunted", "t": T0 + 600, "kills": 4, "deaths": 0, "gold_gained": 100},
        {"type": "goal_result", "t": T0 + 600, "tool": "hunt", "result": {"ok": True, "result": "hunted"}},
        {"type": "openrouter_key", "t": T0 + 601, "usage": 10.0213},
    ])
    write(tmp_path / "s.decisions.jsonl", [
        {"type": "decision", "t": T0 + 100, "answers": {"input_tokens": 2000}},
        {"type": "ai_cost", "t": T0 + 100, "kind": "fight", "role": "system1", "model": "typesafe/jev-1.13",
         "input_tokens": 2000, "cost": 0.000084}])
    r = soak.report(log)
    assert r["costs_recorded"] and r["cost_usd"] == {"system1": 0.00013, "system2": 0.02, "total": 0.02013}
    assert r["costs"]["by_kind"]["fight"]["calls"] == 1 and r["cost_per_kill_usd"] == round(0.020126 / 4, 5)
    assert r["openrouter_key_spent_usd"] == 0.0213
    assert "OpenRouter's own count for the key: $0.0213" in soak.markdown(r)
