import asyncio
import json

import pytest

from uo_brain import fleet, hamlet, planner
from uo_brain.hamlet import Hamlet
from uo_brain.world import World

from test_planner import FakeSession, call, reply, scripted

T0 = 1_791_400_000.0
COVE = [(2200, 1110, 2250, 1160), (2200, 1160, 2286, 1246)]


def write(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def maren(**kw):
    return Hamlet("Cove", COVE, 10, "Maren", (2245, 1231), "the healer's house", "tends the hurt",
                  [{"name": "Brannoc", "trade": "watchman", "home_words": "the gatehouse"},
                   {"name": "Wenna", "trade": "hunter", "home_words": "the fisherman's house"}], **kw)


def cove_store(root):
    with World.open("local", root) as w:
        w.replace_source("regions", "fixture", [
            {"name": "Cove", "map": "Felucca", "kind": "town", "guarded": 1, "rects": json.dumps([list(r) for r in COVE]),
             "go_x": 2275, "go_y": 1210, "go_z": 0}])
        w.replace_source("places", "fixture", [
            {"kind": "healer", "name": "Cove healer", "map": "Felucca", "x": 2245, "y": 1231, "region": "Cove"},
            {"kind": "landmark", "name": "Cove Gates", "map": "Felucca", "x": 2285, "y": 1209, "region": "Cove"},
            {"kind": "landmark", "name": "Cove Guard Post", "map": "Felucca", "x": 2218, "y": 1116, "region": "Cove"},
            {"kind": "fisherman", "name": "Cove fisherman", "map": "Felucca", "x": 2256, "y": 1181, "region": "Cove"},
            {"kind": "provisioner", "name": "Cove provisioner", "map": "Felucca", "x": 2216, "y": 1192, "region": "Cove"}])


def test_the_hamlet_fleet_loads_with_homes_and_the_control_keeps_it_to_measure_by(tmp_path):
    f = fleet.load(fleet.BRAIN_DIR / "fleets" / "hamlet-cove.toml")
    assert f.hamlet == {"name": "Cove", "edge": 10, "told": True, "lands": []}
    assert [a.home for a in f.agents] == ["Cove Gates", "2235,1187", "Cove healer", "Cove provisioner"]
    assert f.agents[2].home_words == "the healer's house" and "bandages" in f.agents[2].work
    # The personas don't name the place: the control knows nothing of it.
    assert not any("Cove" in v for a in f.agents for v in a.persona.values())
    control = fleet.load(fleet.BRAIN_DIR / "fleets" / "hamlet-cove.toml", ["hamlet=false"])
    assert control.hamlet == {"name": "Cove", "edge": 10, "told": False, "lands": []}
    homeless = tmp_path / "homeless.toml"
    homeless.write_text('[hamlet]\nname="Cove"\n[[agent]]\nname="a"\naccount="x"\npassword="x"\nport=1\ngoal="g"\n')
    with pytest.raises(ValueError, match="needs a home: a"):
        fleet.load(homeless)


def test_each_resident_is_told_its_home_and_neighbours_and_the_control_nothing(tmp_path, monkeypatch):
    cove_store(tmp_path)
    f = fleet.load(fleet.BRAIN_DIR / "fleets" / "hamlet-cove.toml")
    h = fleet.hamlet_of(f, root=tmp_path)
    assert h["rects"] == [list(r) for r in COVE] and h["told"]
    assert h["homes"]["Maren"] == {"x": 2245, "y": 1231, "words": "the healer's house"}
    known = h["known"]["Maren"]
    assert known.home == (2245, 1231) and [n["name"] for n in known.neighbours] == ["Brannoc", "Wenna", "Tamsin"]
    assert known.neighbours[0]["trade"] == "watchman" and known.neighbours[0]["home_words"].startswith("the gatehouse")
    monkeypatch.setattr(fleet, "seed_long_goals", lambda *a, **k: [])
    monkeypatch.setattr(fleet, "seed_memories", lambda *a, **k: 0)
    cmds = []
    monkeypatch.setattr(fleet.subprocess, "Popen", lambda cmd, **kw: cmds.append(cmd))
    fleet.start_brain(f, f.agents[2], tmp_path, h)
    told = cmds[-1]
    assert "--hamlet" in told and "--persona" in told
    sent = json.loads((tmp_path / "Maren.hamlet.json").read_text())
    assert sent["home"] == [2245, 1231] and sent["me"] == "Maren" and len(sent["neighbours"]) == 3
    fleet.start_brain(f, f.agents[2], tmp_path, {**h, "told": False})
    assert "--hamlet" not in cmds[-1]
    f.hamlet["name"] = "Nowhere"
    with pytest.raises(ValueError, match="no region called 'Nowhere'"):
        fleet.hamlet_of(f, root=tmp_path)


def test_where_a_resident_is_against_home_and_the_hamlet():
    h = maren()
    assert h.where(2246, 1229) == "at home (the healer's house), in Cove"
    assert h.where(2230, 1180) == "in Cove, 51 tiles from home (the healer's house)"
    assert h.where(2290, 1200).startswith("at the edge of Cove") and h.inside(2290, 1200)
    assert h.where(2416, 1172) == "away from Cove: 131 tiles from it and 171 from home (the healer's house)"
    assert not h.inside(2416, 1172) and h.beyond(2216, 1192) == 0
    for said in ("home", "Home", "my home", "back home", "the house", "home (the healer's house)", "The healer's house"):
        assert h.is_home(said), said
    for said in ("Cove healer", "homestead", "Britain bank", "", None):
        assert not h.is_home(said), said
    assert "neighbours: Brannoc (watchman); Wenna (hunter)" in h.lives()
    assert Hamlet.from_json(json.loads(json.dumps(h.to_json()))) == h


def test_a_residents_planner_is_told_where_it_lives_and_where_it_is_each_turn(tmp_path):
    from test_planner import world
    s = FakeSession(world(tmp_path))
    s.hamlet = maren()
    chat = scripted(reply(call("travel_to", {"place": "home", "why": "back to the healer's house"})))
    p = planner.Planner(s, "", chat_fn=chat, persona={"name": "Maren", "trade": "healer's apprentice"})
    assert "Where Maren lives: the hamlet of Cove" in p.system and "travel_to 'home' goes there" in p.system
    assert "Brannoc, watchman, at the gatehouse" in p.system and "Maren's work: tends the hurt" in p.system
    asyncio.run(p.step())
    user = chat.seen[0]["messages"][1]["content"]
    assert '"home": "away from Cove: 1200 tiles from it and 1245 from home' in user  # the test snapshot is at 1000,1000
    assert s.goals[-1] == ("travel_to", "home")
    plain = planner.Planner(FakeSession(world(tmp_path)), "", chat_fn=chat)
    assert "Where" not in plain.system.split("Who you plan for")[0][-200:] and "hamlet of" not in plain.system


def test_a_resident_with_no_players_goal_doesnt_finish_its_days(tmp_path):
    """cuo-m3n.3: the watchman, sure every way out of Cove was too dangerous, finished half an hour into an hour."""
    from test_planner import world
    s = FakeSession(world(tmp_path))
    s.hamlet = maren()
    chat = scripted(reply(call("finish", {"summary": "staying home, the roads are too dangerous"})),
                    reply(call("rest", {"seconds": 60, "why": "at home"})))
    p = planner.Planner(s, "", chat_fn=chat, persona={"name": "Maren", "trade": "healer's apprentice"})
    step = asyncio.run(p.step())
    assert not step.result["ok"] and step.result["result"].startswith("not finishing: you live in Cove")
    assert p.finished is None
    assert asyncio.run(p.step()).tool == "rest"
    # A player's goal can be done with, and outside a hamlet nobody lives anywhere.
    for goal, hamlet in (("buy 10 bandages", maren()), ("", None)):
        s = FakeSession(world(tmp_path))
        s.hamlet = hamlet
        p = planner.Planner(s, goal, chat_fn=scripted(reply(call("finish", {"summary": "done"}))))
        asyncio.run(p.step())
        assert p.finished == "done"


def test_travel_to_home_goes_to_the_residents_home(tmp_path):
    from test_planner import world
    from uo_brain.session import Session
    s = Session(None, world(tmp_path))
    assert s.resolve("home", None, None) is None  # nobody's home
    s.hamlet = maren()
    assert s.resolve("home", None, None) == (2245, 1231, "home")
    assert s.resolve("my house", None, None) == (2245, 1231, "home")
    assert s.resolve("West Britain bank", None, None)[:2] == (1425, 1690)


def test_the_hamlet_report_measures_each_resident_against_it(tmp_path):
    head = {"t": T0, "type": "fleet", "name": "hamlet-cove", "hamlet": {
        "name": "Cove", "rects": [list(r) for r in COVE], "edge": 10, "told": True,
        "homes": {"Maren": {"x": 2245, "y": 1231, "words": "the healer's house"},
                  "Wenna": {"x": 2256, "y": 1181, "words": "the fisherman's house"}}},
        "agents": [{"name": "Maren", "profile": "npc"}, {"name": "Wenna", "profile": "npc"}]}
    # Maren: home, out to the beasts for 15 min (standing still for 11 of them), back home, in the hamlet.
    maren_at = [(2245, 1231)] * 4 + [(2300, 1200)] + [(2416, 1172)] * 23 + [(2300, 1200)] + [(2245, 1230)] * 6 \
        + [(2230, 1180)] * 6
    wheres = [{"t": T0 + 60 + 30 * i, "type": "where", "agent": "Maren", "x": x, "y": y} for i, (x, y) in enumerate(maren_at)]
    wheres += [{"t": T0 + 60 + 30 * i, "type": "where", "agent": "Wenna", "x": 2256, "y": 1181} for i in range(40)]
    write(tmp_path / "fleet.jsonl", [head, {"t": T0 + 1, "type": "login", "agent": "Maren", "result": "in game as Maren"},
                                     {"t": T0 + 2, "type": "login", "agent": "Wenna", "result": "in game as Wenna"},
                                     *wheres])
    write(tmp_path / "Maren.jsonl", [
        {"type": "ai_cost", "t": T0 + 10, "kind": "planner", "model": "anthropic/claude-haiku-5.5", "cost": 0.01},
        {"type": "goal", "t": T0 + 100, "tool": "hunt", "args": {"area": "south-west of Cove Cemetery", "minutes": 15}},
        {"type": "session", "event": "hunted", "t": T0 + 900, "kills": 3, "deaths": 0},
        {"type": "goal", "t": T0 + 950, "tool": "travel_to", "args": {"place": "home"}},
        {"type": "goal", "t": T0 + 1000, "tool": "sell", "args": {"items": "loot", "vendor_kind": "weaponsmith"}},
        {"type": "session", "event": "sell", "t": T0 + 1010, "ok": True, "gold": 120},
        {"type": "session", "event": "buy", "t": T0 + 1020, "got": 20},
        {"type": "talk", "t": T0 + 1100, "said": True, "to": "Wenna", "text": "Back safe, Wenna."},
        {"type": "talk", "t": T0 + 1110, "heard": True, "who": "Wenna", "text": "Good."},
        {"type": "ai_cost", "t": T0 + 1200, "kind": "fight", "model": "x", "cost": 0.002}])
    write(tmp_path / "Wenna.jsonl", [{"type": "ai_cost", "t": T0 + 10, "kind": "planner", "model": "x", "cost": 0.001},
                                     {"type": "goal", "t": T0 + 20, "tool": "rest", "args": {"seconds": 60}},
                                     {"type": "ai_cost", "t": T0 + 1200, "kind": "planner", "model": "x", "cost": 0.001}])
    with World.open("local", tmp_path) as w:
        from uo_brain import memory as memories
        memories.Memory(w, "Maren").add("Talked with Wenna (2026-10-19): back safe.", kind="relationship",
                                        source="talk", importance=3, at=T0 + 1150)
        # The next run's memory, and this one's set aside before it: the report keeps to its own run (cuo-m3n.4).
        memories.Memory(w, "Maren").add("Talked with Wenna (2026-10-20): the next run.", kind="relationship",
                                        source="talk", importance=3, at=T0 + 9000)
        w.db.execute("UPDATE memories SET stale = 1")
        r = hamlet.report(tmp_path, w)
        # A run whose file named no lands, measured against them (--lands): her time out on the woods counts.
        given = hamlet.report(tmp_path, w, lands=[{"name": "the woods", "x": 2416, "y": 1172, "r": 25}])
    assert given["lands_given"] == ["the woods"] and given["residents"]["Maren"]["in_hamlet_or_lands"] > 0.39
    m = r["residents"]["Maren"]
    assert m["samples"] == 41 and m["in_hamlet"] == 0.39 and m["at_home"] == 0.24 and m["came_home"] == 1
    assert m["longest_away_min"] == 12.0 and m["farthest"] == 131
    # No lands in this run: in the hamlet or its lands is in the hamlet; home came between the hunt and the sale.
    assert m["in_hamlet_or_lands"] == 0.39 and m["home_between_work"] == {"pairs": 1, "home_between": 1, "hunts_joined": 0}
    assert [(s["minutes"], s["goal"]) for s in m["still_away"]] == [(11.0, "hunt(area=south-west of Cove Cemetery, minutes=15)")]
    assert m["work"]["kills"] == 3 and m["work"]["gold_from_sales"] == 120 and m["work"]["items_bought"] == 20
    assert m["work"]["goals"] == {"hunt": 1, "travel_to": 1, "sell": 1} and m["work"]["trips"] == {"home": 1}
    assert m["talk"] == {"Wenna": {"said": 1, "heard": 1}} and r["talked_with"] == {"Maren": 1, "Wenna": 0}
    assert [x[:43] for x in m["remembers"]["Wenna"]] == ["relationship: Talked with Wenna (2026-10-19"]
    wenna = r["residents"]["Wenna"]
    assert (wenna["in_hamlet"], wenna["at_home"], wenna["came_home"], wenna["still_away"]) == (1.0, 1.0, 0, [])
    text = hamlet.markdown(r)
    assert "| Maren | " in text and "| 39% | 39% | 24% | 1 | 1 of 1 | 12.0 | 131 | 1 | 1 of 1 |" in text
    assert "- Maren stood still 11.0 min at 2416,1172" in text and "- Maren: Wenna 1/1" in text
    write(tmp_path / "fleet.jsonl", [{"t": T0, "type": "fleet", "name": "x", "agents": []}])
    assert "not a hamlet's run" in hamlet.report(tmp_path)["error"]


# ---- the hamlet's lands and its work at hand (cuo-m3n.3)

WOODS = {"name": "south-west of Cove Cemetery", "x": 2390, "y": 1180, "r": 40}


def test_on_the_hamlets_lands_is_at_work_and_the_turn_says_how_long_since_home():
    h = maren(lands=[WOODS])
    assert h.where(2245, 1231, now=0) == "at home (the healer's house), in Cove"
    assert h.on_lands(2400, 1190) == "south-west of Cove Cemetery" and h.at_work_or_home(2400, 1190)
    assert h.where(2400, 1190, now=120) == ("on Cove's lands (south-west of Cove Cemetery), 115 tiles from Cove and "
                                             "155 from home (the healer's house)")
    assert h.where(2400, 1190, now=60 * 23).endswith("; away from home 23 minutes")
    assert h.where(2600, 1190, now=60 * 24).startswith("away from Cove: 315 tiles from it")
    assert not h.at_work_or_home(2600, 1190)
    h.where(2245, 1231, now=60 * 30)  # home again: the count starts over
    assert "away from home" not in h.where(2240, 1200, now=60 * 33)
    words = maren(lands=[WOODS]).words()
    assert "Cove's lands, where its people work: south-west of Cove Cemetery (round 2390,1180; fair fights for one alone)" in words
    assert "between one piece of work and the next, come home" in words
    assert Hamlet.from_json(maren(lands=[WOODS]).to_json()).lands == [WOODS]
    assert "lands" not in maren().to_json() and "lands" not in maren().words()


def test_a_land_is_the_square_round_a_hunting_areas_spawns(tmp_path):
    with World.open("local", tmp_path) as w:
        w.replace_source("spawns", "fixture", [
            {"map": "Felucca", "area": "south-west of Cove Cemetery", "region": "", "x": 2380, "y": 1170, "walk_range": 10,
             "creature": "Boar"},
            {"map": "Felucca", "area": "south-west of Cove Cemetery", "region": "", "x": 2400, "y": 1190, "walk_range": 20,
             "creature": "BrownBear"}])
        land = fleet.land_of(w, "south-west of Cove Cemetery")
        assert land == {"name": "south-west of Cove Cemetery", "x": 2390, "y": 1180, "r": 10 + 15 + hamlet.LAND_EDGE}
        with pytest.raises(ValueError, match="no hunting area"):
            fleet.land_of(w, "the moon")


def test_the_report_counts_the_lands_and_home_between_pieces_of_work():
    session = [{"type": "goal", "t": 10, "tool": "hunt"}, {"type": "goal", "t": 100, "tool": "travel_to",
               "args": {"place": "home"}}, {"type": "goal", "t": 200, "tool": "sell"},
               {"type": "goal", "t": 300, "tool": "hunt"}, {"type": "goal", "t": 400, "tool": "rest"}]
    homes = [(50, False), (150, True), (250, False), (350, False)]
    # hunt -> sell had home between (the trip home isn't a piece of work); sell -> hunt didn't
    assert hamlet.home_between_work(session, homes) == {"pairs": 2, "home_between": 1, "hunts_joined": 0}
    # A walk to a neighbour's home isn't a piece of work.
    visits = session + [{"type": "goal", "t": 450, "tool": "travel_to", "args": {"place": "Tamsin's home"}}]
    assert hamlet.home_between_work(visits, homes) == {"pairs": 2, "home_between": 1, "hunts_joined": 0}
    # Home between two looks: a trip home that arrived at 270 s, the next hunt setting out at once.
    session[3:3] = [{"type": "session", "event": "travelled", "t": 270, "to": "home", "state": "arrived"}]
    assert hamlet.home_between_work(session, sorted(homes + hamlet.home_arrivals(session))) == \
        {"pairs": 2, "home_between": 2, "hunts_joined": 0}
    assert hamlet.home_arrivals([{"type": "goal_result", "t": 5, "tool": "travel_to",
                                  "result": {"ok": True, "result": "already at home"}},
                                 {"type": "session", "event": "travelled", "t": 9, "to": "home", "state": "stuck"},
                                 {"type": "session", "event": "travelled", "t": 9, "to": "Vesper", "state": "arrived"}]) \
        == [(5, True)]


def test_a_trip_over_several_grounds_is_one_piece_of_work():
    """Harry, 2026-10-27, after Tamsin's 2 of 6 in the second lands hour: she went from an empty ground straight on to
    the next ("The last ground was empty, so I'll try a fair hunting ground near Cove") and was counted not home between
    them. Her goals that hour, minutes in."""
    goal = lambda t, tool, place=None: {"type": "goal", "t": t * 60, "tool": tool,
                                        **({"args": {"place": place}} if place else {"args": {"area": "a ground"}})}
    session = [goal(0.1, "buy"), goal(0.6, "hunt"), goal(5.8, "hunt"), goal(12.0, "travel_to", "home"),
               goal(14.2, "hunt"), goal(28.2, "travel_to", "home"), goal(30.7, "rest"), goal(35.9, "hunt"),
               goal(41.7, "hunt"), goal(57.3, "hunt")]
    homes = [(14.1 * 60, True), (30.6 * 60, True), (33 * 60, True)]
    # buy -> trip (two grounds): not home; trip -> hunt: home; hunt -> trip (three hunts): home
    assert hamlet.home_between_work(session, homes) == {"pairs": 3, "home_between": 2, "hunts_joined": 3}
    # A hunt after the trip home is a new piece, and so is one after other work: a sale between two hunts parts them.
    assert hamlet.home_between_work([goal(1, "hunt"), goal(2, "sell"), goal(3, "hunt")], []) == \
        {"pairs": 2, "home_between": 0, "hunts_joined": 0}


def test_a_neighbours_home_is_a_place_to_go(tmp_path):
    """Maren, taking the watchman the bandages she had promised, asked for "Cove east gate" and "Cove gatehouse"."""
    h = maren()
    h.neighbours[0]["home"] = "2285,1209"
    h.neighbours[0]["home_words"] = "the gatehouse by the east gate"
    assert h.neighbour_home("Brannoc's home") == (2285, 1209, "Brannoc's home")
    assert h.neighbour_home("to brannoc's house") == (2285, 1209, "Brannoc's home")
    assert h.neighbour_home("Cove gatehouse") is None  # only as a last resort, after the store's own places
    for said in ("Cove gatehouse", "Cove east gate", "the gatehouse"):
        assert h.neighbour_home(said, loose=True) == (2285, 1209, "Brannoc's home"), said
    for said in ("Wenna's home", "Cove", "the gate to Britain", "Tamsin's room"):  # no home known, or not one
        assert h.neighbour_home(said, loose=True) is None, said
    assert 'travel_to "Brannoc\'s home" goes to a neighbour\'s' in h.words()
    from uo_brain.session import Session
    cove_store(tmp_path)
    s = Session(None, World.open("local", tmp_path), None)
    s.hamlet = h
    assert s.resolve("Cove east gate", None, None) == (2285, 1209, "Brannoc's home")
    assert s.resolve("Cove healer", None, None)[2] == "Cove healer"  # the store's own places first


def test_a_visit_to_a_neighbours_home_says_whether_they_are_there(tmp_path):
    """cuo-m3n.18: Maren went to Tamsin's home four times in four minutes to look at her arm, each time told only that
    she had arrived; Tamsin was out at the orc fort, and dead for the last two."""
    import copy

    from conftest import SNAPSHOT
    from uo_brain.session import Session

    h = maren()
    h.neighbours[0]["home"] = "1100,1000"
    snap = copy.deepcopy(SNAPSHOT)
    snap["agent"]["travel"] = {"state": "arrived"}

    class Rpc:
        async def call(self, method, **params):
            return copy.deepcopy(snap) if method == "snapshot" else {"status": "done"}

    s = Session(Rpc(), World.open("local", tmp_path), None)
    s.hamlet = h
    s.recall_to = lambda *a: asyncio.sleep(0)  # no runes
    r = asyncio.run(s.travel_to("Brannoc's home", recall=False))
    assert r.ok and r.summary.endswith("; Brannoc isn't here (not in sight)")
    snap["mobiles"].append({"serial": 9, "name": "Brannoc", "human": True, "monster": False, "notoriety": "innocent",
                            "hits_pct": 60, "dead": False, "distance": 2, "dx": 2, "dy": 0})
    assert asyncio.run(s.travel_to("Brannoc's home", recall=False)).summary.endswith(
        "; Brannoc is here, 2 tiles away, hurt (60% of health)")
    assert Session.at_their_home("brannoc", {"mobiles": [{"name": "Brannoc", "dead": True, "distance": 1}]}) == \
        "Brannoc is here, beside you, a ghost"
    # Not a neighbour's home: nothing said of who is there.
    assert asyncio.run(s.travel_to(x=1100, y=1000, recall=False)).summary.startswith("arrived at 1100,1000 in")


def test_a_watch_has_posts_the_watchman_is_told_of_and_a_land_is_within_reach_of_the_hamlet(tmp_path):
    """cuo-m3n.12: the watchman's work is a watch over the hamlet, a round of posts named in the fleet file; cuo-m3n.3:
    a hamlet's land is within LANDS_TILES of it, so a ground can't be named after a run to make the measure come out."""
    cove_store(tmp_path)
    f = fleet.load(fleet.BRAIN_DIR / "fleets" / "hamlet-cove.toml")
    assert f.agents[0].posts == ["Cove Gates", "Cove healer", "Cove Guard Post"] and not f.agents[1].posts
    h = fleet.hamlet_of(f, root=tmp_path)
    brannoc = h["known"]["Brannoc"]
    assert [p["name"] for p in brannoc.posts] == ["Cove Gates", "Cove healer", "Cove Guard Post"]
    assert brannoc.posts[0] == {"name": "Cove Gates", "x": 2285, "y": 1209} and not h["known"]["Wenna"].posts
    assert "the `watch` goal walks a round of its posts" in brannoc.words()
    assert "watch" not in h["known"]["Wenna"].words().split("Brannoc")[0]
    again = Hamlet.from_json(json.loads(json.dumps(brannoc.to_json())))
    assert again.posts == brannoc.posts
    from test_planner import world
    s = FakeSession(world(tmp_path / "planner"))
    s.hamlet = brannoc
    assert "watch" in [t["function"]["name"] for t in planner.Planner(s, "", chat_fn=scripted(), memory=False).tools]
    s.hamlet = h["known"]["Wenna"]
    assert "watch" not in [t["function"]["name"] for t in planner.Planner(s, "", chat_fn=scripted(), memory=False).tools]
    # A land 150 tiles out is the hamlet's; one further isn't.
    with World.open("local", tmp_path) as w:
        w.replace_source("spawns", "fixture", [
            {"map": "Felucca", "area": "near", "region": "", "x": 2435, "y": 1180, "walk_range": 5, "creature": "Boar"},
            {"map": "Felucca", "area": "far", "region": "", "x": 2436, "y": 1180, "walk_range": 5, "creature": "Boar"}])
    lands = tmp_path / "lands.toml"
    body = (fleet.BRAIN_DIR / "fleets" / "hamlet-cove.toml").read_text().replace('edge = 10', 'edge = 10\nlands = [LANDS]')
    lands.write_text(body.replace("LANDS", '"near"'))
    assert [x["name"] for x in fleet.hamlet_of(fleet.load(lands), root=tmp_path)["lands"]] == ["near"]
    lands.write_text(body.replace("LANDS", '"near", "far"'))
    with pytest.raises(ValueError, match=r"within 150 tiles of Cove: not far \(151 tiles out\)"):
        fleet.hamlet_of(fleet.load(lands), root=tmp_path)


def test_the_report_counts_a_watch_as_work_and_its_kills():
    session = [{"type": "goal", "t": 1, "tool": "watch", "args": {"minutes": 10}},
               {"type": "session", "t": 2, "event": "watched", "kills": 3, "minutes": 10},
               {"type": "goal", "t": 3, "tool": "travel_to", "args": {"place": "home"}},
               {"type": "goal", "t": 5, "tool": "watch", "args": {"minutes": 10}}]
    assert hamlet.work(session)["kills"] == 3 and hamlet.work(session)["goals"]["watch"] == 2
    assert hamlet.home_between_work(session, [(4, True)]) == {"pairs": 1, "home_between": 1, "hunts_joined": 0}


def test_each_land_is_told_with_what_there_is_too_strong_for_one_alone(tmp_path):
    """cuo-m3n.21: told only "Cove's lands, where its people work", three residents hunted the orc fort's savages in the
    first 12 minutes of a lands hour and died there."""
    with World.open("local", tmp_path) as w:
        w.replace_source("creatures", "fixture", [
            {"type": "Savage", "name": "a savage", "hits_min": 96, "hits_max": 115, "difficulty": "strong"},
            {"type": "Orc", "name": "an orc", "hits_min": 58, "hits_max": 72, "difficulty": "weak"},
            {"type": "Lich", "name": "a lich", "hits_min": 103, "hits_max": 120, "difficulty": "strong"},
            {"type": "Boar", "name": "a boar", "hits_min": 15, "hits_max": 15, "difficulty": "weak"}])
        w.replace_source("spawns", "fixture", [
            {"map": "Felucca", "area": "the fort", "region": "", "x": 2160, "y": 1360, "walk_range": 10, "creature": "Savage",
             "max_count": 2},
            {"map": "Felucca", "area": "the fort", "region": "", "x": 2165, "y": 1365, "walk_range": 10, "creature": "Orc",
             "max_count": 6},
            {"map": "Felucca", "area": "the wilds", "region": "", "x": 2400, "y": 1100, "walk_range": 10, "creature": "Boar",
             "max_count": 9},
            {"map": "Felucca", "area": "the wilds", "region": "", "x": 2410, "y": 1110, "walk_range": 10, "creature": "Lich",
             "max_count": 1}])
        fort, wilds = fleet.land_of(w, "the fort"), fleet.land_of(w, "the wilds")
    assert fort["strong"] == ["a savage"] and fort["mostly"] and wilds["strong"] == ["a lich"] and not wilds["mostly"]
    h = maren(lands=[fort, wilds, {"name": "the woods", "x": 2400, "y": 1170, "r": 25}])
    words = h.words()
    assert "the fort (round 2162,1362; a savage there is too strong for one alone: not a ground to work on your own)" in words
    assert "the wilds (round 2405,1105; a lich there is too strong for one alone: keep clear of them)" in words
    assert "the woods (round 2400,1170; fair fights for one alone)" in words
    assert Hamlet.from_json(json.loads(json.dumps(h.to_json()))).lands == h.lands
