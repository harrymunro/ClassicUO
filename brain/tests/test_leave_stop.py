"""cuo-fhf.6: code keeps a leave's direction (the line away from them all); Jev says when it is safe to
stop running, within code's floors, instead of the fixed 40 s."""

from uo_brain import bench, policy, questions, state

from test_policy import PACK_BESTIARY, answers, pack_snapshot

JEV = policy.PolicyConfig(leave_stop="jev")


def leaving(snapshot, *creatures, hp=95):
    pack_snapshot(snapshot, *creatures)
    snapshot["player"]["hits"] = hp
    return state.build(snapshot, set(), [], bestiary=PACK_BESTIARY)


def test_jev_is_asked_only_while_a_leave_goes_on(snapshot):
    sit = leaving(snapshot, (4, "a gargoyle", 12, False, 100))
    mem = policy.Memory(leaving_until=50.0, leave_started=10.0)
    reason = policy.stop_wanted(sit, mem, JEV, now=20.0)
    assert reason.startswith("it has been running for 10 s; code would stop it in 30 s")
    assert "stop_running" in questions.build(sit, stop=reason)
    assert policy.stop_wanted(sit, mem, policy.PolicyConfig(), now=20.0) == ""      # code's rule: not asked
    assert policy.stop_wanted(sit, policy.Memory(), JEV, now=20.0) == ""            # not leaving
    assert "stop_running" not in questions.build(sit)


def test_a_yes_ends_the_leave_and_keeps_it_from_seeking(snapshot):
    sit = leaving(snapshot, (4, "a gargoyle", 12, False, 100))
    mem = policy.Memory(leaving_until=50.0, leave_started=10.0)
    dec = policy.decide(sit, answers("fight", stop_running=0.8), mem, JEV, now=20.0)
    assert dec.intent == "rest" and dec.note == "stopped running after 10 s: Jev says it is safe (0.80)"
    assert mem.leaving_until == 20.0 and mem.no_seek_until >= 140.0
    # Code's rule ignores the answer: still leaving.
    mem = policy.Memory(leaving_until=50.0, leave_started=10.0)
    assert policy.decide(sit, answers("fight", stop_running=0.8), mem, policy.PolicyConfig(), now=20.0).intent == "leave"


def test_code_floors_hold_whatever_jev_says(snapshot):
    # Not before the first run is under way.
    sit = leaving(snapshot, (4, "a gargoyle", 12, False, 100))
    mem = policy.Memory(leaving_until=50.0, leave_started=18.0)
    assert policy.decide(sit, answers("fight", stop_running=0.9), mem, JEV, now=20.0).intent == "leave"
    # Not with something on the character.
    sit = leaving(snapshot, (17, "an orc", 1, True, 100))
    mem = policy.Memory(leaving_until=50.0, leave_started=10.0)
    assert policy.decide(sit, answers("fight", stop_running=0.9), mem, JEV, now=20.0).intent == "leave"
    # Not under half health with a spellcaster in war mode that can still reach it.
    sit = leaving(snapshot, (4, "a gargoyle", 9, True, 100), hp=40)
    assert policy.leave_floor(sit) == "a gargoyle can still cast at it"
    mem = policy.Memory(leaving_until=50.0, leave_started=10.0)
    assert policy.decide(sit, answers("fight", stop_running=0.9), mem, JEV, now=20.0).intent == "leave"


def test_a_no_keeps_it_running_past_forty_seconds_up_to_ninety(snapshot):
    sit = leaving(snapshot, (4, "a gargoyle", 10, True, 100))
    mem = policy.Memory(leaving_until=50.0, leave_started=10.0)
    dec = policy.decide(sit, answers("fight", stop_running=0.1), mem, JEV, now=47.0)
    assert dec.intent == "leave" and mem.leaving_until == 60.0 and mem.no_seek_until == 180.0
    mem = policy.Memory(leaving_until=98.0, leave_started=10.0)
    policy.decide(sit, answers("fight", stop_running=0.1), mem, JEV, now=96.0)
    assert mem.leaving_until == 100.0  # leave_started + MAX_LEAVE_S
    # Unsure (between the two cuts): code's 40 s stand.
    mem = policy.Memory(leaving_until=50.0, leave_started=10.0)
    policy.decide(sit, answers("fight", stop_running=0.5), mem, JEV, now=47.0)
    assert mem.leaving_until == 50.0


def test_a_new_leave_starts_the_clock(snapshot):
    sit = leaving(snapshot, (4, "a gargoyle", 8, True, 100), (4, "a gargoyle", 9, True, 100), (47, "a reaper", 10, True, 100))
    mem = policy.Memory()
    dec = policy.decide(sit, answers("fight"), mem, JEV, now=100.0)
    assert dec.intent == "leave" and mem.leave_started == 100.0 and mem.leaving_until == 100.0 + policy.LEAVE_S


def test_bench_label_and_leave_measures():
    assert bench.JudgeSpec("jev/jevstop").leave_stop == "jev" and bench.JudgeSpec("jev").leave_stop == "rule"
    tr = bench.Trace()
    tr.wall_started = 1000.0
    tr.decisions = [{"t": 1001.0, "intent": "fight"}, {"t": 1002.0, "intent": "leave"}, {"t": 1012.0, "intent": "leave"},
                    {"t": 1013.0, "intent": "rest"}, {"t": 1030.0, "intent": "leave"}, {"t": 1034.0, "intent": "rest"}]
    tr.hp = [(1.0, 90), (12.0, 80), (20.0, 65), (34.0, 70)]
    tr.nearest = [(13.0, 6), (40.0, 9)]
    assert tr.leaves() == {"leaves": 2, "leave_s": 15.0, "caught_again": 1}  # the first: 80 -> 65 within 30 s


def test_jev_is_told_whether_each_creature_is_following(snapshot):
    """Asked with only "far away" and war mode to go on, Jev answered 0.3-0.5 nearly every time: whether a
    creature is following is what the question turns on."""
    mem = policy.Memory()
    gargoyle = lambda d: [{"serial": 0x200, "monster": True, "distance": d}, {"serial": 0x201, "monster": True,
                                                                             "distance": 10}]
    for t, d in ((0.0, 9), (1.0, 11), (2.0, 13), (3.5, 15)):
        mem.track(gargoyle(d), t)
    words = mem.movement(3.5)
    assert words[0x200] == "falling behind: 9 to 15 tiles in the last 4 s"
    assert words[0x201] == "keeping pace at about 10 tiles: it is following"
    mem.track([{"serial": 0x202, "monster": True, "distance": 12}], 10.0)
    mem.track([{"serial": 0x202, "monster": True, "distance": 7}], 12.0)
    assert mem.movement(12.0) == {0x202: "closing in: 12 to 7 tiles in the last 2 s"}  # the others left view
    pack_snapshot(snapshot, (4, "a gargoyle", 15, True, 100))
    sit = state.build(snapshot, set(), [], movement={0x200: words[0x200]})
    assert sit.hostiles[0].info["moving"] == "falling behind: 9 to 15 tiles in the last 4 s"
    assert "moving" not in state.build(snapshot, set(), []).hostiles[0].info  # not told outside such a leave
