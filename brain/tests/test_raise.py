"""Raising a fallen companion where it fell (cuo-cym.4): who can, what the ghost does, and what the raiser does."""

import asyncio
import copy

from uo_brain import mates, party, policy, state
from uo_brain.session import Session
from uo_brain.world import World

from conftest import SNAPSHOT, mage_snapshot
from test_party import COMPANIONS, LICH, NAMES, companion, monster
from test_policy import answers


def warrior(healing=80.0, anatomy=80.0, bandages=40):
    snap = copy.deepcopy(SNAPSHOT)
    snap["player"].update(name="Roland", hits=95, hits_max=95, dead=False)
    snap["player"]["skills"] = {**snap["player"].get("skills", {}), "Healing": healing, "Anatomy": anatomy}
    snap["player"].setdefault("supplies", {})["bandages"] = bandages
    snap["mobiles"] = []
    snap["agent"].update(engaged=0, bandaging=False)
    return snap


def with_resurrection(missing=""):
    snap = mage_snapshot()
    snap["player"].update(name="Isolde", dead=False)
    snap["magic"]["spells"] = [*snap["magic"]["spells"],
                               {"id": 59, "name": "Resurrection", "school": "magery", "circle": 8, "mana": 50,
                                "target": "beneficial", "missing": missing}]
    snap["mobiles"] = []
    snap["agent"].update(engaged=0)
    return snap


def test_who_can_raise():
    assert party.raises_with(warrior()) == "bandages"
    assert party.raises_with(warrior(healing=70)) == "" and party.raises_with(warrior(anatomy=60)) == ""
    assert party.raises_with(warrior(bandages=0)) == ""
    assert party.raises_with(with_resurrection()) == "spell"
    # Short of mana for the moment (a mage just out of a fight) still counts; without the reagents it doesn't.
    assert party.raises_with(with_resurrection(missing="mana")) == "spell"
    assert party.raises_with(with_resurrection(missing="reagents")) == ""
    assert party.raises_with(mage_snapshot()) == ""  # no such spell in the book


class Rpc:
    def __init__(self, snaps):
        self.snaps, self.acts = list(snaps), []

    async def call(self, method, **params):
        if method == "snapshot":
            return copy.deepcopy(self.snaps.pop(0) if len(self.snaps) > 1 else self.snaps[0])
        self.acts.append((method, params))
        return {"status": "done"}


def ghost(name="Wystan"):
    snap = copy.deepcopy(SNAPSHOT)
    snap["player"].update(name=name, dead=True, hits=0, x=1000, y=1000)
    snap["mobiles"] = []
    return snap


def test_a_ghost_waits_by_its_corpse_for_a_companion_who_can_raise_it(tmp_path, monkeypatch):
    """In war mode, which is when the living can see it; it is told who raised it by the raiser's callout."""
    async def no_sleep(_):
        pass

    monkeypatch.setattr("uo_brain.session.asyncio.sleep", no_sleep)
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Wystan", "Roland", NAMES)
    roland = party.Party(w, "p1", "Roland", "Roland", NAMES)
    near = warrior()
    near["player"].update(x=1004, y=1002)
    roland.report(near, "warrior")
    alive = ghost()
    alive["player"].update(dead=False, hits=10)
    rpc = Rpc([ghost(), ghost(), ghost(), alive])
    s = Session(rpc, w, None)
    s.pcfg = policy.PolicyConfig(link=mates.Mates(mine))
    assert asyncio.run(s.wait_to_be_raised()) == ""  # a party that doesn't raise its fallen: off to the healer
    s.raise_fallen = True
    roland.call("raising", about="Wystan")
    assert asyncio.run(s.wait_to_be_raised()) == "Roland"
    # Two at it: the ghost can't tell whose try it was, and names both.
    rpc.snaps = [ghost(), alive]
    party.Party(w, "p1", "Isolde", "Roland", NAMES).call("raising", about="Wystan")
    assert asyncio.run(s.wait_to_be_raised()) == "Roland and Isolde"
    assert ("act", {"verb": "war_mode", "on": True, "source": "manual"}) in rpc.acts
    w.close()


def test_a_ghost_with_nobody_to_raise_it_doesnt_wait(tmp_path, monkeypatch):
    async def no_sleep(_):
        pass

    monkeypatch.setattr("uo_brain.session.asyncio.sleep", no_sleep)
    monkeypatch.setattr(Session, "RAISE_NOBODY_S", 0.0)
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Wystan", "Roland", NAMES)
    # The warrior is 60 tiles off, and the bard beside the ghost hasn't the skill.
    far = warrior()
    far["player"].update(x=1060, y=1000)
    party.Party(w, "p1", "Roland", "Roland", NAMES).report(far, "warrior")
    party.Party(w, "p1", "Corwin", "Roland", NAMES).report(warrior(healing=60, anatomy=60), "bard")
    rpc = Rpc([ghost()])
    s = Session(rpc, w, None)
    s.pcfg, s.raise_fallen = policy.PolicyConfig(link=mates.Mates(mine)), True
    assert s.raisers((1000, 1000)) == [] and asyncio.run(s.wait_to_be_raised()) == ""
    assert not [a for a in rpc.acts if a[1].get("verb") == "war_mode"]
    w.close()


def fallen(snap, distance, hostile_at=None):
    """Wystan's ghost in view, and maybe something hostile."""
    snap["mobiles"] = [companion("Wystan", distance, distance, 0, dead=True), companion("Isolde", 3)]
    if hostile_at is not None:
        snap["mobiles"].append(monster(0x300, "a skeleton", 50, hostile_at))
    sit = state.build(snap, set(), [], bestiary=LICH, companions={**COMPANIONS, "Roland": "warrior"})
    sit.mates = [state.Mate("Wystan", 0x902, "archer", 0, distance, True, True), state.Mate("Isolde", 0x901, "mage", 100, 3)]
    return sit


CFG = policy.PolicyConfig(raise_fallen=True)


def test_one_with_the_skill_bandages_the_fallen_once_the_fight_is_over():
    mem = policy.Memory()
    dec = policy.decide(fallen(warrior(), 1), answers("rest"), mem, CFG, now=10.0)
    assert {"verb": "bandage", "target": 0x902, "confidence": 1.0, "reason": "raise"} in dec.actions
    assert mem.helped == ("Wystan", "raise") and "raise Wystan" in dec.note
    # Not again at once: a bandage on the dead takes six seconds and more.
    again = policy.decide(fallen(warrior(), 1), answers("rest"), mem, CFG, now=13.0)
    assert not [a for a in again.actions if a.get("reason") == "raise"]
    assert [a for a in policy.decide(fallen(warrior(), 1), answers("rest"), mem, CFG, now=18.0).actions if a.get("reason") == "raise"]
    # Further off, it walks over first; with something hostile about, the fight isn't over.
    walk = policy.decide(fallen(warrior(), 6), answers("rest"), policy.Memory(), CFG, now=10.0)
    assert [a["verb"] for a in walk.actions] == ["walk_to"] and "go to Wystan, who has fallen" in walk.note
    fight = policy.decide(fallen(warrior(), 1, hostile_at=6), answers("rest"), policy.Memory(), CFG, now=10.0)
    assert not [a for a in fight.actions if a.get("reason") in ("raise", "companion")]
    # Without the skill, or in a party that doesn't raise its fallen, nothing.
    assert not policy.decide(fallen(warrior(healing=70), 1), answers("rest"), policy.Memory(), CFG, now=10.0).actions
    assert not policy.decide(fallen(warrior(), 1), answers("rest"), policy.Memory(), policy.PolicyConfig(), now=10.0).actions


def test_a_mage_casts_resurrection_from_beside_the_fallen():
    def mage_by(distance):
        snap = with_resurrection()
        snap["mobiles"] = [companion("Wystan", distance, distance, 0, dead=True)]
        sit = state.build(snap, set(), [], bestiary=LICH, companions={**COMPANIONS, "Roland": "warrior"})
        sit.mates = [state.Mate("Wystan", 0x902, "archer", 0, distance, True, True)]
        return sit

    dec = policy.decide(mage_by(1), answers("rest"), policy.Memory(), CFG, now=10.0)
    assert {"verb": "cast", "spell": "Resurrection", "target": 0x902, "queue": True, "confidence": 1.0,
            "reason": "raise"} in dec.actions
    walk = policy.decide(mage_by(2), answers("rest"), policy.Memory(), CFG, now=10.0)  # the spell wants it beside
    assert [a["verb"] for a in walk.actions if a.get("reason") == "companion"] == ["walk_to"]


def test_everyone_who_can_raise_tries_and_each_says_so_once(tmp_path):
    """A try works one time in four. Live (2026-10-13) the mage's fifth cast brought the archer back after 63 s
    while the warrior, who could have bandaged him, stood by because she had called it."""
    w = World.open("local", root=tmp_path)
    roland = party.Party(w, "p1", "Roland", "Roland", NAMES)
    isolde = party.Party(w, "p1", "Isolde", "Roland", NAMES)
    for p in (roland, isolde):
        p.companions.update({n: c for n, c in {**COMPANIONS, "Roland": "warrior"}.items() if n != p.me})
    link = mates.Mates(roland)
    snap = warrior()
    snap["mobiles"] = [companion("Wystan", 1, 1, 0, dead=True)]
    sit = state.build(snap, set(), [], bestiary=LICH, companions=roland.companions)
    link.before(sit)
    assert sit.mates[0].dead and sit.mates[0].in_sight and not sit.mates[0].being_helped
    mem = policy.Memory()
    dec = policy.decide(sit, answers("rest"), mem, policy.PolicyConfig(link=link, raise_fallen=True), now=10.0)
    assert mem.helped == ("Wystan", "raise")
    link.events(sit, dec, mem)
    assert [c["kind"] for c in isolde.calls(15)] == ["down", "raising"] and link.story.raised == {"Wystan": 1}
    assert "bring Wystan back from the dead" in link.story.words()
    # His next try, seven seconds on, isn't called again.
    again = policy.decide(sit, answers("rest"), mem, policy.PolicyConfig(link=link, raise_fallen=True), now=18.0)
    assert [a for a in again.actions if a.get("reason") == "raise"]
    link.events(sit, again, mem)
    assert [c["kind"] for c in isolde.calls(15)] == ["down", "raising"]
    # The mage beside them hears that he is being raised, and casts her own spell on him all the same.
    hers = mates.Mates(isolde)
    msnap = with_resurrection()
    msnap["mobiles"] = [companion("Wystan", 1, 1, 0, dead=True), companion("Roland", 1, 0, 1)]
    msit = state.build(msnap, set(), [], bestiary=LICH, companions=isolde.companions)
    hers.before(msit)
    assert not next(m for m in msit.mates if m.name == "Wystan").being_helped
    dec = policy.decide(msit, answers("rest"), policy.Memory(), policy.PolicyConfig(link=hers, raise_fallen=True), now=10.0)
    assert [(a["verb"], a.get("spell")) for a in dec.actions if a.get("reason") == "raise"] == [("cast", "Resurrection")]
    assert "Roland (warrior), just now: bringing Wystan back from the dead" in msit.state["party_calls"]
    w.close()


def test_the_fallen_come_before_the_corpses():
    """After a fight the first thing in view is loot. A walk to a corpse would leave the ghost waiting, and
    one begun while the bandage is on would take the raiser out of the two tiles it needs."""
    snap = warrior()
    snap["corpses"] = [{"serial": 0x700, "name": "A Skeletal Corpse", "distance": 4, "dx": 4, "dy": 0, "monster": True}]
    sit = fallen(snap, 6)
    mem = policy.Memory()
    dec = policy.decide(sit, answers("loot"), mem, CFG, now=10.0)
    assert [a["verb"] for a in dec.actions] == ["walk_to"] and "go to Wystan, who has fallen" in dec.note
    beside = fallen(snap, 1)
    dec = policy.decide(beside, answers("loot"), mem, CFG, now=12.0)
    assert [a["verb"] for a in dec.actions] == ["bandage"]
    # Three seconds on, the bandage still on: no walk to the corpse yet.
    dec = policy.decide(fallen(snap, 1), answers("loot"), mem, CFG, now=15.0)
    assert not [a for a in dec.actions if a["verb"] in ("loot", "walk_to")]


def test_the_ghosts_wait_is_counted_from_the_end_of_the_fight(tmp_path, monkeypatch):
    """Nobody raises anyone in a fight: a minute of fighting isn't a minute of the ghost's patience."""
    looks = []

    async def no_sleep(_):
        looks.append(1)

    monkeypatch.setattr("uo_brain.session.asyncio.sleep", no_sleep)
    monkeypatch.setattr(Session, "RAISE_WAIT_S", 5.0)
    monkeypatch.setattr(Session, "RAISE_MAX_S", 1e9)
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Wystan", "Roland", NAMES)
    roland = party.Party(w, "p1", "Roland", "Roland", NAMES)
    near = warrior()
    near["player"].update(x=1004, y=1002)
    roland.report(near, "warrior")
    roland.fight({"hits": 90, "hits_max": 95, "dead": False, "attackers": [0x300], "target": 0x300, "pets": []})

    class Fighting(Rpc):
        async def call(self, method, **params):
            if method == "snapshot" and len(looks) == 40:  # the fight ends after forty looks
                w.db.execute("DELETE FROM party_fight")
                w.db.commit()
            return await super().call(method, **params)

    s = Session(Fighting([ghost()]), w, None)
    s.pcfg, s.raise_fallen = policy.PolicyConfig(link=mates.Mates(mine)), True
    assert asyncio.run(s.wait_to_be_raised()) == ""
    assert 44 <= len(looks) <= 47  # forty looks of fight, then its five seconds of quiet
    w.close()


def test_what_hasnt_noticed_anyone_doesnt_keep_a_raiser_from_trying():
    """Every creature in view counted once: a zombie ten tiles off that had noticed nobody, or a deer, kept the
    raiser from ever trying while its report still said it could."""
    sit = fallen(warrior(), 1, hostile_at=6)
    sit.raw["mobiles"][-1]["war_mode"] = False
    idle = state.build(sit.raw, set(), [], bestiary=LICH, companions={**COMPANIONS, "Roland": "warrior"})
    idle.mates = sit.mates
    assert not policy.coming(idle) and idle.hostiles
    dec = policy.decide(idle, answers("rest"), policy.Memory(), CFG, now=10.0)
    assert {"verb": "bandage", "target": 0x902, "confidence": 1.0, "reason": "raise"} in dec.actions


def test_a_ghost_doesnt_give_up_with_a_raiser_at_work_beside_it(tmp_path, monkeypatch):
    """The minute of quiet ran out under the raiser's hands for about one ghost in ten."""
    async def no_sleep(_):
        pass

    monkeypatch.setattr("uo_brain.session.asyncio.sleep", no_sleep)
    monkeypatch.setattr(Session, "RAISE_WAIT_S", 3.0)
    monkeypatch.setattr(Session, "RAISE_MAX_S", 0.4)
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Wystan", "Roland", NAMES)
    near = warrior()
    near["player"].update(x=1002, y=1000)
    party.Party(w, "p1", "Roland", "Roland", NAMES).report(near, "warrior")

    def waits(roland_distance):
        snap = ghost()
        snap["mobiles"] = [companion("Roland", roland_distance, roland_distance, 0)]
        rpc = Rpc([snap])
        looks = {"n": 0}
        real = rpc.call

        async def counted(method, **params):
            looks["n"] += method == "snapshot"
            return await real(method, **params)

        rpc.call = counted
        s = Session(rpc, w, None)
        s.pcfg, s.raise_fallen = policy.PolicyConfig(link=mates.Mates(mine)), True
        assert asyncio.run(s.wait_to_be_raised()) == ""
        return looks["n"]

    assert waits(10) == 3     # the raiser is near but not at it: three quiet looks, and off to the healer
    assert waits(2) > 20      # at it, two tiles off: the wait goes on to its own limit
    w.close()
