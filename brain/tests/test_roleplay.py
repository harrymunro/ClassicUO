"""Roleplay in a party (cuo-cod.8) and walking its trips together (cuo-cod.7), the brain's side."""

import asyncio
import copy
import random

from uo_brain import mates, policy, roleplay, session, voice
from uo_brain.session import Session
from uo_brain.world import World

from conftest import SNAPSHOT
from test_mates import fight, two_sides
from test_party import companion, lich_fight
from test_policy import answers


class Rpc:
    """A client that records what it was asked, with a script of snapshots for `snapshot`."""

    def __init__(self, snaps=None, refuse=()):
        self.acts, self.snaps, self.refuse = [], list(snaps or []), set(refuse)

    async def call(self, method, **params):
        if method == "act":
            self.acts.append(params)
            return {"status": "failed", "detail": "unknown verb"} if params["verb"] in self.refuse else {"status": "done"}
        if method == "snapshot":
            return self.snaps.pop(0) if len(self.snaps) > 1 else self.snaps[0]
        return {}


def test_small_things_are_done_in_character_and_kept_sparse():
    rpc = Rpc()
    rp = roleplay.Roleplay(rpc, voice.Voice(), rnd=random.Random(3), min_gap_s=60)
    assert asyncio.run(rp.act("meet")) == "bow" and rpc.acts[-1] == {"verb": "bow"}
    # Not again within the gap, whatever happens.
    assert asyncio.run(rp.act("fallen", name="Thea")) is None
    rp.last = -1e9
    did = asyncio.run(rp.act("fallen", name="Thea", key="fallen:Thea"))
    assert "Thea" in did and rpc.acts[-1] == {"verb": "say", "text": f"*{did}*", "kind": "emote"}
    rp.last = -1e9
    assert asyncio.run(rp.act("fallen", name="Thea", key="fallen:Thea")) is None  # once for each fall
    # An event with a chance is acted on only some of the time.
    done = 0
    for _ in range(40):
        rp.last = -1e9
        done += asyncio.run(rp.act("kill")) is not None
    assert 8 <= done <= 24 and rp.to_log()["roleplay_acts"]["kill"] == done
    # A follower salutes its leader; a client without the gesture leaves nothing done.
    f = roleplay.Roleplay(Rpc(refuse={"salute"}), voice.Voice(), follower=True)
    assert asyncio.run(f.act("meet")) is None and f.rpc.acts == [{"verb": "salute"}] and f.done == {}
    assert asyncio.run(roleplay.Roleplay(Rpc(), voice.Voice(), follower=True).act("meet")) == "salute"


def test_after_a_fight_a_member_does_something_and_kneels_by_the_fallen(tmp_path):
    w, mine, _ = two_sides(tmp_path)
    rpc = Rpc()
    snap = lich_fight(lord=False)
    snap["mobiles"] = [m for m in snap["mobiles"] if not m.get("monster")]   # the fight is over
    next(m for m in snap["mobiles"] if m.get("name") == "Isolde").update(dead=True, distance=2)
    sit, link = fight(mine, snap)
    link.roleplay = roleplay.Roleplay(rpc, voice.Voice(), rnd=random.Random(1))
    link.in_fight = True
    dec = policy.decide(sit, answers("rest"), policy.Memory(), policy.PolicyConfig())
    asyncio.run(link.after(rpc, sit, dec, policy.Memory()))
    emotes = [a["text"] for a in rpc.acts if a.get("kind") == "emote"]
    assert len(emotes) == 1 and "Isolde" in emotes[0] and link.roleplay.done == {"fallen": 1}
    # In a fight nothing of the kind is done.
    rpc2 = Rpc()
    sit, link = fight(mine, lich_fight(lord=False))
    link.roleplay = roleplay.Roleplay(rpc2, voice.Voice())
    asyncio.run(link.after(rpc2, sit, policy.decide(sit, answers("fight"), policy.Memory(), policy.PolicyConfig()), policy.Memory()))
    assert not any(a.get("kind") == "emote" for a in rpc2.acts)
    w.close()


def walking(x, y, state="walking", mobiles=()):
    snap = copy.deepcopy(SNAPSHOT)
    snap["player"].update(x=x, y=y)
    snap["mobiles"] = list(mobiles)
    snap["agent"]["travel"] = {"state": state}
    return snap


def test_a_walking_partys_trip_is_walked_and_never_recalled(tmp_path):
    w = World.open("local", root=tmp_path)
    rpc = Rpc([walking(1000, 1000), walking(1000, 1000), walking(1060, 1000, "arrived")])
    s = Session(rpc, w)
    s.pace = "walk"
    s.recall_to = None  # would raise if a walking party tried to recall
    r = asyncio.run(s.travel_to(x=1060, y=1000, together=True))
    assert r.ok and "at a walk with the party" in r.summary
    assert rpc.acts[0] == {"verb": "travel", "x": 1060, "y": 1000, "distance": 2, "pace": "walk"}
    # On its own a character runs as before, and so does a walking party's member on an errand of its own
    # (a shop, its corpse): only the trips the party makes together are walked.
    rpc = Rpc([walking(1000, 1000), walking(1000, 1000), walking(1060, 1000, "arrived")])
    asyncio.run(Session(rpc, w).travel_to(x=1060, y=1000, recall=False, together=True))
    assert rpc.acts[0]["pace"] == "run"
    rpc = Rpc([walking(1000, 1000), walking(1000, 1000), walking(1060, 1000, "arrived")])
    s = Session(rpc, w)
    s.pace = "walk"
    asyncio.run(s.travel_to(x=1060, y=1000, recall=False))
    assert rpc.acts[0]["pace"] == "run"
    w.close()


def test_every_trip_at_a_walk_when_the_rules_say_so(tmp_path):
    """cuo-der: realistic travel for any character, its own errands too; a corpse run still runs."""
    w = World.open("local", root=tmp_path)
    rpc = Rpc([walking(1000, 1000), walking(1000, 1000), walking(1060, 1000, "arrived")])
    s = Session(rpc, w)
    s.travel_pace = "walk"
    s.recall_to = None  # a walker doesn't recall
    asyncio.run(s.travel_to(x=1060, y=1000))
    assert rpc.acts[0]["pace"] == "walk"
    rpc = Rpc([walking(1000, 1000), walking(1000, 1000), walking(1060, 1000, "arrived")])
    s = Session(rpc, w)
    s.travel_pace = "walk"
    asyncio.run(s.travel_to(x=1060, y=1000, recall=False, pace="run"))
    assert rpc.acts[0]["pace"] == "run"
    # Unset, the client's own setting (agent_travel_pace) decides.
    snap = walking(1000, 1000)
    snap["agent"]["travel_pace"] = "walk"
    rpc = Rpc([snap, walking(1000, 1000), walking(1060, 1000, "arrived")])
    s = Session(rpc, w)
    s.recall_to = None
    asyncio.run(s.travel_to(x=1060, y=1000))
    assert rpc.acts[0]["pace"] == "walk"
    w.close()


def test_the_leader_waits_for_one_who_has_fallen_behind(tmp_path, monkeypatch):
    w, mine, _ = two_sides(tmp_path)
    real = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda s: real(0))
    behind = [companion("Isolde", 12, dx=-12), companion("Wystan", 2)]   # 12 tiles back along the way
    close = [companion("Isolde", 3, dx=-3), companion("Wystan", 2)]
    rpc = Rpc([walking(1000, 1000), walking(1005, 1000, mobiles=behind), walking(1005, 1000, mobiles=behind),
               walking(1005, 1000, mobiles=close), walking(1005, 1000, mobiles=close), walking(1060, 1000, "arrived", close)])
    s = Session(rpc, w, pcfg=policy.PolicyConfig(companions=mine.companions, link=mates.Mates(mine)))
    s.pace, s.leads = "walk", True
    r = asyncio.run(s.travel_to(x=1060, y=1000, together=True))
    verbs = [a["verb"] for a in rpc.acts]
    assert r.ok and verbs == ["travel", "stop", "travel"] and r.data.get("waited_for_the_party_s") is not None
    # One far off on business of its own (a ghost at the healer) isn't waited for; nor does a follower wait.
    assert s.laggards(walking(1005, 1000, mobiles=[companion("Isolde", 80, dx=-80)])) == []
    ahead = walking(1005, 1000, mobiles=[companion("Isolde", 12, dx=12)])
    assert s.laggards(ahead, (1060, 1000)) == [] and s.laggards(ahead) == ["Isolde"]  # gone ahead: not waited for
    s.leads = False
    assert s.laggards(walking(1005, 1000, mobiles=behind)) == []
    w.close()


def test_a_follower_walks_beside_its_leader_and_makes_its_own_way_when_it_must(tmp_path, monkeypatch):
    w = World.open("local", root=tmp_path)
    real = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda s: real(0))
    leader = {**companion("Roland", 3), "serial": 0x900, "dx": 3, "dy": 0}

    def at(x, lead_dx=3, follow=None):
        snap = walking(x, 1000, "idle", [{**leader, "dx": lead_dx, "distance": abs(lead_dx)}])
        if follow:
            snap["agent"]["follow"] = {"target": 0x900, "state": follow}
        return snap

    # Beside the leader all the way: one follow order, then it is there.
    rpc = Rpc([at(1000), at(1020, follow="following"), at(1040, follow="following"), at(1058, follow="following")])
    s = Session(rpc, w)
    r = asyncio.run(s.walk_with("Roland", x=1060, y=1000))
    assert r.ok and r.data["with"] == "Roland"
    assert rpc.acts == [{"verb": "follow", "target": 0x900, "distance": 2, "pace": "walk"}, {"verb": "stop"}]
    # Lost sight of it: the follow is given again when it is seen once more.
    rpc = Rpc([at(1000), at(1020, follow="lost"), at(1040, follow="following"), at(1058, follow="following")])
    asyncio.run(Session(rpc, w).walk_with("Roland", x=1060, y=1000))
    assert [a["verb"] for a in rpc.acts] == ["follow", "follow", "stop"]
    # The leader out of view: it runs there on its own. A client without follow: it walks there on its own.
    alone = walking(1000, 1000, "idle")
    rpc = Rpc([alone, alone, walking(1060, 1000, "arrived")])
    s = Session(rpc, w)
    s.pace = "walk"
    assert asyncio.run(s.walk_with("Roland", x=1060, y=1000)).ok and rpc.acts[0] == {"verb": "travel", "x": 1060, "y": 1000,
                                                                                    "distance": 3, "pace": "run"}
    rpc = Rpc([at(1000), at(1000), at(1000), walking(1060, 1000, "arrived")], refuse={"follow"})
    s = Session(rpc, w)
    s.pace = "walk"
    asyncio.run(s.walk_with("Roland", x=1060, y=1000))
    assert [a["verb"] for a in rpc.acts] == ["follow", "travel"] and rpc.acts[1].get("pace") == "walk"
    w.close()


# ---------------------------------------------------------------- found in review (2026-10-11)

def test_one_still_behind_after_a_full_wait_isnt_waited_for_again(tmp_path, monkeypatch):
    """Forty waits made a crawl of a 179-tile trip: each member is waited for once, until it catches up."""
    w, mine, _ = two_sides(tmp_path)
    real = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda s: real(0))
    monkeypatch.setattr(Session, "LAG_WAIT_S", 0.05)
    behind = [companion("Isolde", 12, dx=-12), companion("Wystan", 2)]
    rpc = Rpc([walking(1000, 1000)] + [walking(1005, 1000, mobiles=behind)] * 400
              + [walking(1060, 1000, "arrived", behind)])
    s = Session(rpc, w, pcfg=policy.PolicyConfig(companions=mine.companions, link=mates.Mates(mine)))
    s.pace, s.leads = "walk", True
    logged = []
    s._log = logged.append
    r = asyncio.run(s.travel_to(x=1060, y=1000, together=True))
    assert r.ok and [a["verb"] for a in rpc.acts] == ["travel", "stop", "travel"]
    waits = [rec for rec in logged if rec.get("event") == "waited_for"]
    assert len(waits) == 1 and waits[0]["went_on_without"] == ["Isolde"]
    w.close()


def test_a_rest_ends_when_the_character_is_killed_in_it(tmp_path, monkeypatch):
    """A follower killed on its way to a rest "rested 68 s, health 0/85" as a ghost."""
    w = World.open("local", root=tmp_path)
    real = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda s: real(0))
    clock = [0.0]

    def tick():
        clock[0] += 0.5
        return clock[0]

    monkeypatch.setattr(session.time, "monotonic", tick)
    alive, dead = copy.deepcopy(SNAPSHOT) | {"in_game": True}, copy.deepcopy(SNAPSHOT) | {"in_game": True}
    dead["player"].update(dead=True, hits=0)
    r = asyncio.run(Session(Rpc([alive, alive, dead]), w).rest(300))
    assert not r.ok and r.summary.startswith("died while resting") and r.data == {"died": True}
    r = asyncio.run(Session(Rpc([alive]), w).rest(20))
    assert r.ok and r.summary.startswith("rested")
    w.close()


def test_a_caster_rests_for_its_mana_after_a_resurrection(tmp_path, monkeypatch):
    """cuo-tyv: resurrected with no mana to recall, every planner model sometimes set off on foot."""
    real = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda s: real(0))
    w = World.open("local", root=tmp_path)

    def me(hits, mana, magery=True):
        snap = copy.deepcopy(SNAPSHOT)
        snap["player"].update(hits=hits, hits_max=100, mana=mana, mana_max=100)
        if magery:
            snap["player"]["skills"] = {"Magery": 90.0, "Evaluating Intelligence": 80.0, "Meditation": 80.0}
            snap["player"]["weapon"] = None
            snap["magic"] = {"schools": ["magery"]}
        return snap

    rpc = Rpc([me(80, 5), me(80, 30), me(80, 60)])
    said = asyncio.run(Session(rpc, w).recover_health())
    assert "mana 60/100" in said and not rpc.snaps[1:]  # waited for the third look
    rpc = Rpc([me(80, 5, magery=False)])  # a warrior doesn't wait for mana
    assert asyncio.run(Session(rpc, w).recover_health()).startswith("healed to 80/100 in")
    w.close()
