"""Where to stand (cuo-fhf.2): the client's places worded for Jev, the "stand" question, and runs,
leaves and steps back going to the place Jev (or code) picks."""

import asyncio
import copy
import io
import json

from uo_brain import bench, loop, policy, questions, state
from uo_brain.judge import Answers, ChoiceResult

from conftest import SNAPSHOT, mage_snapshot

ORC, CAPTAIN, GARGOYLE = 0x100, 0x101, 0x103


COMPASS = ["north", "north-northeast", "northeast", "east-northeast", "east", "east-southeast", "southeast",
           "south-southeast", "south", "south-southwest", "southwest", "west-southwest", "west", "west-northwest",
           "northwest", "north-northwest"]


def spot(kind, dx, dy, steps, near, room=100, seen_by=(), passes=(), name="", ring=None):
    s = {"kind": kind, "x": 1000 + dx, "y": 1000 + dy, "dx": dx, "dy": dy, "steps": steps,
         "dir": "here" if dx == dy == 0 else COMPASS[state.bearing16(dx, dy)], "room": room,
         "near": [list(n) for n in near], "seen_by": list(seen_by), "passes": list(passes)}
    if name:
        s["name"] = name
    if ring is not None:
        s["ring"] = ring
    return s


def hurt_warrior():
    """30% health, an orc on it (east), an orc captain 6 west, a gargoyle 6 north; five places and stay."""
    snap = copy.deepcopy(SNAPSHOT)
    snap["player"]["hits"] = 30
    snap["mobiles"].append({"serial": GARGOYLE, "name": "a gargoyle", "body": 4, "monster": True, "hits_pct": 100,
                            "dead": False, "war_mode": True, "distance": 6, "dx": 0, "dy": -6, "dir": "north"})
    snap["spots"] = [
        spot("stay", 0, 0, 0, [(ORC, 1), (CAPTAIN, 6), (GARGOYLE, 6)], seen_by=[GARGOYLE]),
        spot("ring", -3, 0, 3, [(CAPTAIN, 3), (ORC, 4), (GARGOYLE, 6)], room=5),
        spot("ring", 0, 8, 8, [(ORC, 8), (CAPTAIN, 10), (GARGOYLE, 14)]),
        spot("ring", 8, 0, 8, [(ORC, 7), (GARGOYLE, 10), (CAPTAIN, 14)], seen_by=[GARGOYLE]),
        spot("ring", 1, -1, 1, [(ORC, 1), (GARGOYLE, 5), (CAPTAIN, 7)]),
        spot("ring", 0, -8, 8, [(GARGOYLE, 2), (ORC, 8), (CAPTAIN, 10)], passes=[GARGOYLE]),
    ]
    return snap


def as_mage(snap):
    """The same fight for a mage: its player and spellbook."""
    mage = mage_snapshot()
    snap["player"], snap["magic"] = mage["player"], mage["magic"]
    return snap


BESTIARY = {4: {"hits": 105, "difficulty": "moderate", "caster": True}}


def sit_of(snap, **kw):
    return state.build(snap, set(), [], **kw)


def stand(probs):
    """Answers: flee, in danger, and the given probabilities for where to stand."""
    a = Answers()
    a.choices["intent"] = ChoiceResult("flee", {k: float(k == "flee") for k in questions.INTENTS}, 0.9)
    a.choices["target"] = ChoiceResult("t1", {"t1": 1.0}, 0.9)
    a.nouls["in_danger"] = 0.95
    best = max(probs, key=probs.get)
    a.choices["stand"] = ChoiceResult(best, probs, probs[best])
    return a


JEV = policy.PolicyConfig(spots="jev")


def test_places_are_listed_by_bearing_from_north_with_stay_last():
    sit = sit_of(hurt_warrior())
    assert [(c.id, c.info["dx"], c.info["dy"]) for c in sit.spots] == [
        ("p1", 0, -8), ("p2", 1, -1), ("p3", 8, 0), ("p4", 0, 8), ("p5", -3, 0), ("stay", 0, 0)]
    rev = sit_of(hurt_warrior(), spot_order="reversed")
    assert [(c.info["dx"], c.info["dy"]) for c in rev.spots] == [(-3, 0), (0, 8), (8, 0), (1, -1), (0, -8), (0, 0)]
    assert rev.spots[-1].id == "stay"


def test_each_place_is_worded_from_its_facts():
    sit = sit_of(hurt_warrior())
    words = {c.id: c.info["words"] for c in sit.spots}
    assert words["p4"].startswith("8 steps south: nearest hostile an orc 8 tiles away, none within 3")
    assert words["p4"].endswith("open ground beyond")
    assert "a dead end" in words["p5"]
    assert "the way there passes next to a gargoyle" in words["p1"]
    assert words["p2"].startswith("1 step northeast: ") and "nearest hostile an orc adjacent, 1 within 3" in words["p2"]
    assert words["stay"].startswith("stay here: nearest hostile an orc adjacent")


def test_spellcasters_that_could_see_a_place_are_named():
    sit = state.build(hurt_warrior(), set(), [], bestiary=BESTIARY)
    words = {c.id: c.info["words"] for c in sit.spots}
    assert "in sight of a gargoyle (a spellcaster)" in words["p3"]   # seen, 10 tiles off
    assert "out of sight of the spellcaster" in words["p2"]          # within reach, not seen
    assert "spellcaster" not in words["p4"]                          # 14 tiles off: out of its reach


def test_a_caster_hears_how_far_its_target_would_be_and_whether_it_sees_it():
    sit = state.build(as_mage(hurt_warrior()), set(), [])
    assert sit.is_mage and sit.focus.serial == ORC
    words = {c.id: c.info["words"] for c in sit.spots}
    assert "an orc (its target) 8 tiles away and out of sight" in words["p4"]
    assert "an orc (its target) adjacent" in words["p2"]
    assert "(its target)" not in sit_of(hurt_warrior()).spot("p4").info["words"]  # a warrior isn't told


def test_stand_is_asked_only_when_wanted():
    sit = sit_of(hurt_warrior())
    assert "stand" not in questions.build(sit)
    qs = questions.build(sit, stand=True)
    assert list(qs["stand"]["criteria"]) == [c.id for c in sit.spots]
    assert qs["stand"]["criteria"]["p4"] == sit.spot("p4").info["words"]
    assert "dead ends" in qs["stand"]["instructions"]["guidance"]
    no_spots = sit_of({k: v for k, v in hurt_warrior().items() if k != "spots"})
    assert "stand" not in questions.build(no_spots, stand=True)


def test_mage_and_tamer_guidance():
    sit = state.build(as_mage(hurt_warrior()), set(), [])
    assert "spellcaster also wants its target" in questions.stand_question(sit)["instructions"]["guidance"]


def test_stand_wanted_only_when_a_run_could_come():
    mem = policy.Memory()
    hurt = sit_of(hurt_warrior())
    assert policy.stand_wanted(hurt, mem, JEV, 0.0)            # 30% with an orc on it
    assert not policy.stand_wanted(hurt, mem, policy.PolicyConfig(), 0.0)  # spots off
    healthy = hurt_warrior()
    healthy["player"]["hits"] = 95
    healthy["mobiles"] = [m for m in healthy["mobiles"] if m["serial"] == ORC]
    assert not policy.stand_wanted(sit_of(healthy), mem, JEV, 0.0)  # one orc at good health
    mem.leaving_until = 10.0
    assert policy.stand_wanted(sit_of(healthy), mem, JEV, 0.0)      # leaving: every run goes somewhere


def test_a_flee_goes_to_jevs_place():
    sit = sit_of(hurt_warrior())
    dec = policy.decide(sit, stand({"p1": 0.1, "p2": 0.05, "p3": 0.6, "p4": 0.2, "p5": 0.05, "stay": 0.0}),
                        policy.Memory(), JEV, now=0.0)
    flee = next(a for a in dec.actions if a["verb"] == "flee")
    assert (flee["x"], flee["y"], flee["spot"], flee["spot_by"]) == (1008, 1000, "p3", "jev")
    assert flee["spot_p"] == round(0.6 / 0.95, 3)  # p2, beside the orc, is no place to run to
    assert dec.spot.id == "p3" and dec.spot_why == "jev"
    assert dec.note.endswith("; to 8 steps east (0.63)")


def test_staying_or_a_place_beside_a_creature_is_no_place_to_run_to():
    sit = sit_of(hurt_warrior())
    # Jev's own pick is to stay; next comes p2, beside the orc: the run goes to p4.
    dec = policy.decide(sit, stand({"stay": 0.5, "p2": 0.3, "p4": 0.15, "p1": 0.05}), policy.Memory(), JEV, now=0.0)
    flee = next(a for a in dec.actions if a["verb"] == "flee")
    assert flee["spot"] == "p4"
    assert abs(flee["spot_p"] - 0.15 / 0.2) < 1e-3  # renormalised over the places allowed


def test_code_picks_where_jev_was_not_asked_and_with_the_rule_setting():
    sit = sit_of(hurt_warrior())
    a = stand({"p1": 1.0})
    del a.choices["stand"]
    dec = policy.decide(sit, a, policy.Memory(), JEV, now=0.0)
    flee = next(x for x in dec.actions if x["verb"] == "flee")
    # Not past the gargoyle (p1), not a dead end (p5), not beside the orc (p2): p4, 8 from the nearest.
    assert (flee["spot"], flee["spot_by"]) == ("p4", "rule") and "spot_p" not in flee
    dec = policy.decide(sit, stand({"p1": 1.0}), policy.Memory(), policy.PolicyConfig(spots="rule"), now=0.0)
    assert next(x for x in dec.actions if x["verb"] == "flee")["spot"] == "p4"


def test_spots_off_leaves_the_run_to_the_client():
    sit = sit_of(hurt_warrior())
    dec = policy.decide(sit, stand({"p3": 1.0}), policy.Memory(), policy.PolicyConfig(), now=0.0)
    flee = next(a for a in dec.actions if a["verb"] == "flee")
    assert "x" not in flee and dec.spot is None


def test_a_leave_runs_to_the_place_too():
    sit = sit_of(hurt_warrior())
    mem = policy.Memory(leaving_until=30.0)
    dec = policy.decide(sit, stand({"p4": 0.9, "p3": 0.1}), mem, JEV, now=0.0)
    assert dec.intent == "leave"
    flee = next(a for a in dec.actions if a["verb"] == "flee")
    assert flee["spot"] == "p4" and flee["reason"] == "leave"


def test_a_leave_goes_to_the_far_rings():
    """cuo-fhf.5: Jev's leaves stopped 3 steps off with the orc still on the warrior. A leave is offered
    the 8- and 14-tile rings and far landmarks only; a run from danger may still go anywhere."""
    snap = hurt_warrior()
    snap["spots"] = [
        spot("stay", 0, 0, 0, [(ORC, 1), (CAPTAIN, 6), (GARGOYLE, 6)]),
        spot("ring", -3, 3, 3, [(ORC, 4), (CAPTAIN, 3)], ring=3),
        spot("ring", 0, 8, 8, [(ORC, 8), (CAPTAIN, 10)], ring=8),
        spot("doorway", 2, 3, 3, [(ORC, 3)]),
        spot("ring", 14, 3, 14, [(ORC, 13)], ring=14),
    ]
    sit = sit_of(snap)
    near_ring, doorway = (next(c for c in sit.spots if c.info["dx"] == dx) for dx in (-3, 2))
    leave = policy.spot_options(sit, "flee", leave=True)
    assert near_ring not in leave and doorway not in leave and {c.info["ring"] for c in leave} == {8, 14}
    assert near_ring in policy.spot_options(sit, "flee")
    mem = policy.Memory(leaving_until=30.0)
    dec = policy.decide(sit, stand({near_ring.id: 0.7, "p3": 0.2, "p4": 0.1}), mem, JEV, now=0.0)
    assert dec.intent == "leave" and next(a for a in dec.actions if a["verb"] == "flee")["spot"] != near_ring.id


def keep_warrior():
    """hurt_warrior with the orc captain north-west, so the place 8 south has nothing nearer to it."""
    snap = hurt_warrior()
    captain = next(m for m in snap["mobiles"] if m["serial"] == CAPTAIN)
    captain.update(dx=-6, dy=-2)
    return snap


def test_a_run_keeps_to_its_place_until_it_is_reached():
    """cuo-fhf.5: asked every decision, Jev sent one leave 3 steps northeast, 3 east, 14 north-northeast
    and 8 east, and the orc stayed beside the warrior throughout. The place is kept until reached."""
    mem = policy.Memory()
    first = policy.decide(sit_of(keep_warrior()), stand({"p4": 0.9, "p3": 0.1}), mem, JEV, now=0.0)
    flee = next(a for a in first.actions if a["verb"] == "flee")
    assert (flee["x"], flee["y"], flee["spot"]) == (1000, 1008, "p4")
    # Next decision Jev prefers the place to the east: the run keeps to the one it set off for.
    again = policy.decide(sit_of(keep_warrior()), stand({"p3": 0.9, "p4": 0.1}), mem, JEV, now=1.0)
    kept = next(a for a in again.actions if a["verb"] == "flee")
    assert (kept["x"], kept["y"], kept["spot"], kept["spot_by"]) == (1000, 1008, "kept", "kept")
    assert again.note.endswith("still to 1000,1008")
    # Kept no longer than SPOT_KEEP_S.
    late = policy.decide(sit_of(keep_warrior()), stand({"p3": 0.9, "p4": 0.1}), mem, JEV, now=1.0 + policy.SPOT_KEEP_S)
    assert next(a for a in late.actions if a["verb"] == "flee")["spot"] == "p3"


def test_a_kept_place_is_given_up_when_reached_or_when_a_creature_gets_nearer_to_it():
    mem = policy.Memory(spot_goal=(1000, 1008, 50.0, "flee"))
    there = keep_warrior()
    there["player"]["y"] = 1007  # a step from it
    assert policy.kept_spot(sit_of(there), mem, "flee", 1.0) is None
    blocked = keep_warrior()
    next(m for m in blocked["mobiles"] if m["serial"] == ORC).update(dx=0, dy=6)  # between it and the place
    assert policy.kept_spot(sit_of(blocked), mem, "flee", 1.0) is None
    assert policy.kept_spot(sit_of(keep_warrior()), mem, "flee", 1.0) == (1000, 1008)
    assert policy.kept_spot(sit_of(keep_warrior()), mem, "kite", 1.0) is None  # a step back picks its own
    # Back to fighting: the next run picks afresh.
    fight = Answers()
    fight.choices["intent"] = ChoiceResult("fight", {k: float(k == "fight") for k in questions.INTENTS}, 0.9)
    fight.choices["target"] = ChoiceResult("t1", {"t1": 1.0}, 0.9)
    fight.nouls["in_danger"] = 0.1
    calm = keep_warrior()
    calm["player"]["hits"] = 90
    policy.decide(sit_of(calm), fight, mem, JEV, now=2.0)
    assert mem.spot_goal is None


def test_a_step_back_goes_no_further_than_a_step_back():
    snap = as_mage(hurt_warrior())
    snap["spots"].append(spot("ring", -12, 0, 12, [(ORC, 13)]))
    sit = state.build(snap, set(), [])
    far = next(c for c in sit.spots if c.info["dx"] == -12)
    options = policy.spot_options(sit, "kite")
    assert far not in options and all(c.distance <= policy.KITE_STEPS for c in options)
    assert far in policy.spot_options(sit, "flee")


def test_rule_prefers_open_ground_away_from_all_of_them():
    sit = sit_of(hurt_warrior())
    assert policy.rule_spot(policy.spot_options(sit, "flee"), "flee").id == "p4"


class Rpc:
    def __init__(self):
        self.calls = []

    async def call(self, method, **params):
        self.calls.append((method, params))
        return {"status": "done", "detail": "to the spot 8 tiles south"} if method == "act" else {}


class Judge:
    name = "fake-jev"

    async def ask(self, state_words, qs):
        a = Answers(model="fake", input_tokens=100, latency_ms=5.0)
        for q, spec in qs.items():
            if spec["type"] == "choice":
                opts = list(spec["criteria"])
                pick = "flee" if q == "intent" else "p4" if q == "stand" else opts[0]
                a.choices[q] = ChoiceResult(pick, {o: float(o == pick) for o in opts}, 0.9)
            else:
                a.nouls[q] = 0.95 if q == "in_danger" else 0.1
        return a


def test_the_loop_asks_where_to_stand_sends_the_place_and_logs_it():
    sit = sit_of(hurt_warrior())
    rpc, log = Rpc(), io.StringIO()
    asyncio.run(loop.decide_once(rpc, Judge(), sit, policy.Memory(), JEV, loop.RunStats(), log))
    act = next(p for m, p in rpc.calls if m == "act" and p["verb"] == "flee")
    assert (act["x"], act["y"], act["spot"]) == (1000, 1008, "p4")
    rec = json.loads(log.getvalue().splitlines()[-1])
    assert "stand" in rec["questions"]
    assert rec["spot"]["id"] == "p4" and rec["spot"]["by"] == "jev" and rec["spot"]["p"] == 1.0
    view = next(p for m, p in rpc.calls if m == "decision")["questions"]
    assert any(q["q"] == "stand" and q["picked"] == "p4" for q in view)


def test_bench_labels_choose_where_runs_go():
    assert bench.JudgeSpec("jev").spots == "off"
    assert bench.JudgeSpec("jev/spots").spots == "jev"
    assert bench.JudgeSpec("jev/spotsrev").spots == "jev" and bench.JudgeSpec("jev/spotsrev").spot_order == "reversed"
    assert bench.JudgeSpec("jev/codespots").spots == "rule"
    assert bench.JudgeSpec("heuristic/spots/nokite").spots == "jev"
    assert "pack-pocket" in bench.SPOTS


def test_trace_reports_how_the_runs_went():
    tr = bench.Trace()
    tr.wall_started = 1000.0
    tr.nearest = [(1.0, 1), (2.5, 3), (4.0, 9)]
    tr.decisions = [
        {"t": 1001.0, "intent": "flee", "actions": [{"verb": "flee", "x": 1, "y": 2, "spot": "p4", "spot_by": "jev"}],
         "results": [{"status": "done", "detail": "to the spot 8 tiles south"}], "spot": {"jev_pick": "stay"}},
        {"t": 1002.0, "intent": "leave", "cornered": True, "actions": [{"verb": "flee", "spot_by": "rule"}],
         "results": [{"status": "failed", "detail": "no path to the spot; no path away"}]},
    ]
    m = tr.moves()
    assert m["runs"] == 2 and m["to_spot"] == {"jev": 1, "rule": 1, "kept": 0}
    assert m["no_path_to_spot"] == 1 and m["failed_runs"] == 1 and m["cornered"] == 1
    assert m["break_contact_s"] == 3.0 and m["jev_stayed"] == 1 and m["died_moving"] is False
