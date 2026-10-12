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
    assert "| Wren | npc | | | | | | | did not play: not in game: ip already has 10 accounts |" in fleet.markdown(r)


def test_npcs_with_lives_of_their_own_load_with_personas_and_long_goals(tmp_path):
    from uo_brain import planner
    from uo_brain.longgoals import LongGoals
    from uo_brain.world import World
    f = fleet.load(fleet.BRAIN_DIR / "fleets" / "britain-lives.toml")
    garrick = f.agents[0]
    assert garrick.goal == "" and garrick.persona["wants"] == "a forge of his own"
    assert garrick.long_goals == ["save 5000 gold for a forge of my own"]
    assert fleet.seed_long_goals(garrick, root=tmp_path) != []
    assert fleet.seed_long_goals(garrick, root=tmp_path) == []  # once only
    with World.open("local", tmp_path) as w:
        assert LongGoals(w, "Garrick").list()[0]["kind"] == "gold"
    words = planner.persona_words({"name": "Garrick", **garrick.persona})
    assert "Who you plan for: Garrick" in words and "Wants: a forge of his own." in words
    bad = tmp_path / "bad.toml"
    bad.write_text('[[agent]]\nname="a"\naccount="x"\npassword="x"\nport=1\n')
    with pytest.raises(ValueError, match="needs a goal or long_goals"):
        fleet.load(bad)


def test_talk_is_counted_and_goals_that_followed_a_conversation_found():
    session = [
        {"type": "talk", "t": T0, "heard": True, "who": "Torv", "text": "Liches at the graveyard tonight."},
        {"type": "talk", "t": T0 + 5, "said": True, "to": "Torv", "text": "Then I'll keep to the Yew woods.",
         "gives": "warning", "about": "Britain Graveyard"},
        {"type": "ai_cost", "t": T0 + 5, "kind": "talk", "cost": 0.0004},
        {"type": "ai_cost", "t": T0 + 4, "kind": "chat", "cost": 0.00002},
        {"type": "goal", "t": T0 + 60, "tool": "hunt", "args": {"area": "Yew woods", "why": "Torv warned of liches."}},
        {"type": "goal", "t": T0 + 90, "tool": "resurrect", "args": {"why": "dead at the Britain Graveyard"}},
        {"type": "goal", "t": T0 + 2000, "tool": "bank", "args": {"why": "bank it"}},
    ]
    r = fleet.talk_report(session, ["Torv", "Ilsa"])
    assert (r["lines_said"], r["lines_heard"], r["talk_gave"]) == (1, 1, ["warning"])
    assert r["talk_cost_usd"] == 0.00042 and [g["tool"] for g in r["goals_after_talk"]] == ["hunt"]
    assert fleet.talk_report([{"type": "goal", "t": T0}], ["Torv"]) == {}



def test_a_client_back_at_the_login_screen_is_logged_in_again(monkeypatch):
    import asyncio
    calls = []

    class Rpc:
        def __init__(self, port):
            self.status = {"scene": "login", "in_game": False, "login_state": "in_game"}

        async def connect(self, attempts=1):
            pass

        async def close(self):
            pass

        async def call(self, method, **params):
            calls.append((method, params))
            return self.status

    async def log_in(rpc, account, password, character, host, port, **_):
        calls.append(("log_in", character))
        return f"in game as {character}"

    monkeypatch.setattr(fleet, "AgentRpc", Rpc)
    monkeypatch.setattr(fleet.client, "log_in", log_in)
    f = fleet.load(fleet.BRAIN_DIR / "fleets" / "britain-lives.toml")
    assert asyncio.run(fleet.relog(f, f.agents[0])) == "in game as Garrick"
    assert ("log_in", "Garrick") in calls and ("mode", {"mode": "auto"}) in calls

    class InGame(Rpc):
        def __init__(self, port):
            self.status = {"scene": "game", "in_game": True}

    monkeypatch.setattr(fleet, "AgentRpc", InGame)
    assert asyncio.run(fleet.relog(f, f.agents[0])) == ""



def test_an_agents_env_goes_to_its_brain(tmp_path, monkeypatch):
    f = tmp_path / "ab.toml"
    f.write_text('[[agent]]\nname="a"\naccount="x"\npassword="x"\nport=1\ngoal="g"\n'
                 'env = { UO_BRAIN_ASK_EVERY_TICK = "1" }\n'
                 '[[agent]]\nname="b"\naccount="y"\npassword="y"\nport=2\ngoal="g"\n')
    a, b = fleet.load(f).agents
    assert a.env == {"UO_BRAIN_ASK_EVERY_TICK": "1"} and b.env == {}
    seen = []
    monkeypatch.setattr(fleet.subprocess, "Popen", lambda cmd, **kw: seen.append(kw.get("env")))
    fleet.start_brain(fleet.load(f), a, tmp_path)
    fleet.start_brain(fleet.load(f), b, tmp_path)
    assert seen[0]["UO_BRAIN_ASK_EVERY_TICK"] == "1" and "PATH" in seen[0] and seen[1] is None


def test_seeding_doesnt_bring_back_a_finished_or_dropped_goal(tmp_path):
    from uo_brain.longgoals import LongGoals
    from uo_brain.world import World
    f = fleet.load(fleet.BRAIN_DIR / "fleets" / "britain-lives.toml")
    torv = f.agents[2]
    fleet.seed_long_goals(torv, root=tmp_path)
    with World.open("local", tmp_path) as w:
        g = LongGoals(w, "Torv")
        g.done(g.list()[0]["id"], "reached")
    assert fleet.seed_long_goals(torv, root=tmp_path) == []


def test_a_login_that_sticks_is_tried_once_more(monkeypatch):
    """The fifth of a party never came in (the server closed a stuck socket), and its prep spawns the wave."""
    import asyncio
    tries, acts = [], []

    class Rpc:
        def __init__(self, port):
            pass

        async def connect(self, attempts=1):
            pass

        async def close(self):
            pass

        async def call(self, method, **params):
            acts.append(method)
            return {}

    async def log_in(rpc, account, password, character, host, port, **_):
        tries.append(character)
        return "not in game: {'login_state': 'server_selected'}" if len(tries) in stuck else f"in game as {character}"

    async def no_sleep(_):
        pass

    monkeypatch.setattr(fleet, "AgentRpc", Rpc)
    monkeypatch.setattr(fleet.client, "log_in", log_in)
    monkeypatch.setattr(fleet.asyncio, "sleep", no_sleep)
    f = fleet.load(fleet.BRAIN_DIR / "fleets" / "party-lich.toml")
    stuck = {1}
    assert asyncio.run(fleet.log_in(f, f.agents[4])) == "in game as Corwin (at the second try)"
    assert tries == ["Corwin", "Corwin"] and acts.count("act") == len(f.agents[4].prep)
    stuck, tries[:] = {1, 2}, []
    r = asyncio.run(fleet.log_in(f, f.agents[4]))
    assert r.startswith("not in game") and "second try" in r and len(tries) == 2


def test_a_client_that_doesnt_answer_sits_the_run_out_and_the_others_play(tmp_path, monkeypatch):
    """A timeout in one login (its text is empty) ended a whole fleet with the message "fleet: "."""
    import asyncio
    import json

    async def log_in(fl, a):
        if a.name == "Ilsa":
            raise TimeoutError()
        return f"in game as {a.name}"

    class Brain:
        pid = 1

        def poll(self):
            return 0

    started = []
    monkeypatch.setattr(fleet, "start_client", lambda a, out: None)
    monkeypatch.setattr(fleet, "log_in", log_in)
    monkeypatch.setattr(fleet, "start_brain", lambda fl, a, out: started.append(a.name) or Brain())
    monkeypatch.setattr(fleet, "pid_listening", lambda port: 0)
    monkeypatch.setattr(fleet, "report", lambda out: {})
    f = fleet.load(fleet.BRAIN_DIR / "fleets" / "britain-lives.toml")
    asyncio.run(fleet.run(f, tmp_path / "run"))
    logins = [json.loads(line) for line in (tmp_path / "run" / "fleet.jsonl").open() if '"login"' in line]
    assert [r["result"] for r in logins] == ["in game as Garrick", "not in game: TimeoutError", "in game as Torv"]
