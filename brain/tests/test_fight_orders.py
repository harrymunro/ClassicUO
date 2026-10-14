"""Orders for the fight itself, for companions of a character a person plays (cuo-cym.24): a creature to go for,
falling back to the leader, a heal now. Read by system one beside the order question, carried out in the fight loop
(policy.PolicyConfig focus, fall_back, heal_now, laid over by Companion.live) and said back at once."""

import asyncio
import copy
from dataclasses import replace

from uo_brain import companion, party, policy, state
from uo_brain.world import World

from conftest import SNAPSHOT
from test_companion import MEMBERS, Mind, Talks, Walker, lines, person, view
from test_party import LICH, monster
from test_policy import answers

HARRY = person("Harry", 2, 2, 0)


def lich(**kw):
    """A lich in war mode a tile from the leader, three from the companion."""
    return {**monster(0x310, "a lich", 24, 3), "dx": 3, "dy": 0, **kw}


def evil_mage(**kw):
    return {**monster(0x311, "an evil mage", 400, 9), "dx": 0, "dy": -9, **kw}


def fight(journal=(), seq=10, creatures=None, leader=HARRY, **agent):
    snap = view([leader, *(creatures if creatures is not None else [lich(), evil_mage()])], journal, seq)
    snap["agent"].update(agent)
    return snap


def said_aloud(s):
    return [p["text"] for v, p in s.acts if v == "say"]


def test_the_creatures_offered_and_the_questions():
    snap = fight()
    snap["mobiles"].append({**monster(0x312, "a skeleton", 50, 1), "dx": -1, "dy": 0, "war_mode": False,
                            "attacking_me": True})
    snap["mobiles"].append({**monster(0x313, "Bramble", 212, 2), "pet": True})
    snap["mobiles"].sort(key=lambda m: m["distance"])
    cs = companion.creatures_in_view(snap, "Isolde", "Harry", ["Wystan"])
    assert [c["id"] for c in cs] == ["c1", "c2", "c3"] and [c["serial"] for c in cs] == [0x312, 0x310, 0x311]  # no pet
    assert cs[0]["words"] == "A skeleton: 1 tile from Isolde, 3 from Harry; in war mode, on Isolde."
    assert cs[1]["words"] == "A lich: 3 tiles from Isolde, 1 from Harry; in war mode, on Harry." and cs[1]["says"] == "the lich"
    assert cs[2]["words"] == "An evil mage: 9 tiles from Isolde, 9 from Harry; in war mode." and cs[2]["says"] == "the evil mage"
    # Two of a kind: a word back says "a skeleton", not "the skeleton".
    two = fight(creatures=[lich(), {**monster(0x314, "a lich", 24, 6), "dx": 6, "dy": 0}])
    assert [c["says"] for c in companion.creatures_in_view(two, "Isolde", "Harry", [])] == ["a lich", "a lich"]
    assert len(companion.creatures_in_view(fight(creatures=[lich(serial=0x400 + i) for i in range(9)]), "Isolde", "Harry", [])) \
        == companion.CREATURES_MAX
    assert set(companion.fight_question("Isolde", "Harry")["criteria"]) == {"attack", "fall_back", "heal", "none"}
    assert set(companion.target_question("Isolde", "Harry", cs)["criteria"]) == {"c1", "c2", "c3", "unclear"}
    assert set(companion.whom_question("Isolde", "Harry", ["Isolde", "Thea", "Wystan"])["criteria"]) == \
        {"Harry", "Thea", "Wystan", "unclear"}  # not itself


def test_told_to_go_for_one_it_goes_for_that_one_until_it_dies_and_says_so(tmp_path):
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Harry", MEMBERS)
    kill = "Kill the mage first!"
    s = Walker([fight(), fight(lines(kill), seq=11), fight(lines(kill), seq=11),
                fight(lines(kill), seq=11, creatures=[lich()])])  # the mage is dead
    s.judge = Mind(fight={kill: ("attack", 0.95)}, target={kill: ("an evil mage", 0.9)})
    seen = []
    c = companion.Companion(s, mine, log=seen.append)
    asyncio.run(c.step())
    asyncio.run(c.step())
    assert c.focus["serial"] == 0x311 and said_aloud(s) == ["On the evil mage."]
    r = c.reading(11)
    assert r.fight == "attack" and r.said == "On the evil mage." and not r.owed and "go for the evil mage" in r.note
    # One request with the fight question and which creature, the creatures in view as its options.
    st, q = s.judge.questions("fight")[0]
    tq = s.judge.questions("target")[0][1]
    assert set(tq["criteria"]) == {"c1", "c2", "unclear"} and "on Harry" in tq["criteria"]["c1"]
    assert st["creatures_in_view"][1].startswith("An evil mage") and st["line"] == kill
    # The fight loop is told: the named one is gone for (policy), with what the settings say laid under it.
    cfg = c.live(policy.PolicyConfig(escort="Harry", looting="nothing"))
    assert cfg.focus == 0x311 and not cfg.fall_back and cfg.heal_now == ("", 0) and cfg.looting == "nothing"
    asyncio.run(c.step())
    assert c.focus is not None
    asyncio.run(c.step())  # dead (or gone from view): the order is over
    assert c.focus is None and c.live(policy.PolicyConfig()).focus == 0
    orders = [x for x in seen if x["type"] == "fight_order"]
    assert orders[0]["order"] == "attack" and orders[0]["target"] == 0x311 and orders[0]["said"] == "On the evil mage."
    assert orders[1]["ended"] == "attack" and orders[1]["why"] == "dead or out of view"
    read = next(x for x in seen if x["type"] == "order_read")
    assert read["fight"]["order"] == "attack" and read["fight"]["target"]["serial"] == 0x311
    w.close()


def test_a_creature_named_is_held_only_so_long(tmp_path, monkeypatch):
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Harry", MEMBERS)
    s = Walker([fight(), fight(lines("That one!"), seq=11)])
    s.judge = Mind(fight={"That one!": ("attack", 0.9)}, target={"That one!": ("a lich", 0.8)})
    c = companion.Companion(s, mine)
    asyncio.run(c.step())
    asyncio.run(c.step())
    assert c.focus["serial"] == 0x310 and said_aloud(s) == ["On the lich."]
    monkeypatch.setattr(companion, "FOCUS_S", 0.0)
    asyncio.run(c.step())
    assert c.focus is None
    w.close()


def test_no_creature_in_view_no_question_and_which_one_when_it_cant_tell(tmp_path):
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Harry", MEMBERS)
    kill = "Kill the dragon!"
    # Nothing about: the line is put to the order question only.
    s = Walker([view([HARRY]), view([HARRY], lines(kill), seq=11)])
    s.judge = Mind(fight={kill: ("attack", 0.95)})
    asyncio.run(companion.Companion(s, mine).step())
    asyncio.run(companion.Companion(s, mine).step())
    assert not s.judge.questions("fight")
    # With creatures about and none of them the one meant: it asks which, at once in plain words with no talk.
    s = Walker([fight(), fight(lines(kill), seq=11)])
    s.judge = Mind(fight={kill: ("attack", 0.95)}, target={kill: ("", 0.9)})
    c = companion.Companion(s, mine)
    asyncio.run(c.step())
    asyncio.run(c.step())
    assert c.focus is None and said_aloud(s) == ["Which one?"]
    # ...and with talk, the one who writes its answer is told to ask.
    s = Walker([fight(), fight(lines(kill), seq=11)])
    s.judge = Mind(fight={kill: ("attack", 0.95)}, target={kill: ("", 0.9)})
    c = companion.Companion(s, mine)
    c.hears(Talks())
    asyncio.run(c.step())
    asyncio.run(c.step())
    r = c.reading(11)
    assert "can't tell which one" in r.owed and r.plain == "Which one?" and not said_aloud(s)
    w.close()


def test_only_a_creature_that_could_start_a_fight_makes_a_line_one_for_the_fight(tmp_path):
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Harry", MEMBERS)
    deer = {**monster(0x320, "a deer", 237, 6), "war_mode": False}

    class Store:
        @staticmethod
        def bestiary():
            return {237: {"name": "a deer", "peaceful": True}}

    s = Walker([fight(creatures=[deer]), fight(lines("Kill that deer."), seq=11, creatures=[deer])])
    s.world = Store()
    s.judge = Mind(fight={"Kill that deer.": ("attack", 0.95)})
    c = companion.Companion(s, mine)
    asyncio.run(c.step())
    asyncio.run(c.step())
    assert not s.judge.questions("fight") and c.focus is None
    w.close()


def test_told_to_fall_back_it_comes_away_to_the_leader_until_the_fight_is_over(tmp_path, monkeypatch):
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Harry", MEMBERS)
    back = "Back to me, now!"
    s = Walker([fight(), fight(lines(back), seq=11, engaged=0x310), fight(lines(back), seq=11),
                fight(lines(back), seq=11, creatures=[])])
    # Read as both: an order to come along (which it was doing) and a fall back. One word, the fall back's.
    s.judge = Mind(order={back: ("follow", 0.9)}, fight={back: ("fall_back", 0.95)})
    seen = []
    c = companion.Companion(s, mine, log=seen.append)
    asyncio.run(c.step())
    asyncio.run(c.step())
    assert c.falling_back is not None and c.mode == "follow" and said_aloud(s) == ["Falling back."]
    assert c.reading(11).said == "Falling back." and "fall back" in c.reading(11).note
    assert c.live(policy.PolicyConfig(escort="Harry")).fall_back
    asyncio.run(c.step())  # no longer engaged: the client's follow is asked for, which takes it to the leader
    assert ("follow", {"target": 0x700, "distance": companion.FOLLOW_TILES}) in s.acts
    monkeypatch.setattr(companion, "FALL_BACK_CALM_S", 0.0)
    asyncio.run(c.step())  # nothing about any more: the fight is over, and with it the fall back
    assert c.falling_back is None
    assert [x.get("why") for x in seen if x["type"] == "fight_order" and x.get("ended")] == ["the fight is over"]
    w.close()


def test_a_fall_back_brings_one_sent_away_or_told_to_wait_back_and_an_attack_order_ends_it(tmp_path):
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Harry", MEMBERS)
    s = Walker([fight(), fight(lines("Fall back!"), seq=11), fight(lines("Fall back!", "Wystan's lich, now!"), seq=12)])
    s.judge = Mind(fight={"Fall back!": ("fall_back", 0.9), "Wystan's lich, now!": ("attack", 0.9)},
                   target={"Wystan's lich, now!": ("a lich", 0.9)})
    c = companion.Companion(s, mine)
    asyncio.run(c.step())
    c.mode, c.dest = "go", {"kind": "inn", "says": "the inn", "name": "inn", "x": 1, "y": 1, "tiles": 40, "way": "east"}
    asyncio.run(c.step())
    assert c.mode == "follow" and c.dest is None and ("stop", {}) in s.acts and c.falling_back is not None
    asyncio.run(c.step())
    assert c.falling_back is None and c.focus["serial"] == 0x310
    w.close()


def test_told_to_heal_it_heals_once_whatever_the_health_or_says_it_cant(tmp_path):
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Harry", [*MEMBERS, "Thea"])
    heal = "Heal me!"

    def healer(snap, mana=True, bandages=0):
        snap["magic"] = {"spells": [{"name": "Greater Heal", **({} if mana else {"missing": "mana"})}]}
        snap["player"].setdefault("skills", {})["Healing"] = 0
        snap["player"].setdefault("supplies", {})["bandages"] = bandages
        return snap

    s = Walker([healer(fight()), healer(fight(lines(heal), seq=11))])
    s.archetype = "mage"
    s.judge = Mind(fight={heal: ("heal", 0.95)}, whom={heal: ("Harry", 0.95)})
    seen = []
    c = companion.Companion(s, mine, log=seen.append)
    asyncio.run(c.step())
    asyncio.run(c.step())
    assert c.heal == {"name": "Harry", "id": 1, "t": c.heal["t"]} and said_aloud(s) == ["Healing you."]
    assert c.live(policy.PolicyConfig()).heal_now == ("Harry", 1)
    assert set(s.judge.questions("whom")[0][1]["criteria"]) == {"Harry", "Wystan", "Thea", "unclear"}
    # No mana for it, and the line for this one alone (as its talk reads it): it says it can't.
    s = Walker([healer(fight(), mana=False), healer(fight(lines("Isolde, heal me"), seq=11), mana=False)])
    s.archetype = "mage"
    s.judge = Mind(fight={"Isolde, heal me": ("heal", 0.95)}, whom={"Isolde, heal me": ("Harry", 0.95)},
                   to={"Isolde, heal me": ("Isolde", 0.95)})
    c = companion.Companion(s, mine)
    c.hears(Talks())
    asyncio.run(c.step())
    asyncio.run(c.step())
    r = c.reading(11)
    assert c.heal is None and "can't" in r.owed and r.plain == "I can't heal." and not said_aloud(s)
    # ...and to all of them it leaves it to a healer: nothing owed, and no refusal of a request either.
    s = Walker([healer(fight(), mana=False), healer(fight(lines(heal), seq=11), mana=False)])
    s.archetype = "mage"
    s.judge = Mind(fight={heal: ("heal", 0.95)}, whom={heal: ("Harry", 0.95)}, asked={heal: 0.95})
    c = companion.Companion(s, mine)
    c.hears(Talks())
    asyncio.run(c.step())
    asyncio.run(c.step())
    assert c.heal is None and not c.reading(11).owed and "can't" in c.reading(11).note
    w.close()


def test_whom_to_heal_when_the_question_cant_tell(tmp_path):
    three = ["Isolde", "Thea", "Wystan"]
    assert companion.heal_whom("heal pls", "Isolde", "Harry", three) == "Harry"
    assert companion.heal_whom("someone heal Wystan", "Wystan", "Harry", three) == "Wystan"
    assert companion.heal_whom("someone heal Wystan", "Isolde", "Harry", three) == ""
    assert companion.heal_whom("Thea's hurt!", "Isolde", "Harry", three) == ""
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Harry", [*MEMBERS, "Thea"])
    snap = fight()
    snap["magic"] = {"spells": [{"name": "Greater Heal"}]}
    for line, heals in (("heal pls", "Harry"), ("someone heal Isolde", None)):
        s = Walker([snap, {**copy.deepcopy(snap), "journal": lines(line), "journal_seq": 11}])
        s.archetype = "mage"
        s.judge = Mind(fight={line: ("heal", 0.95)}, whom={line: ("unclear", 0.75)})
        c = companion.Companion(s, mine)
        asyncio.run(c.step())
        asyncio.run(c.step())
        assert (c.heal or {}).get("name") == heals and said_aloud(s) == (["Healing you."] if heals else [])
    w.close()


def test_a_fight_order_with_nobody_named_is_each_ones_though_whom_it_is_for_splits(tmp_path):
    """Live (2026-10-14 01:59): "Get the one on me!" was read as for Wystan alone at about 0.5 and for the others at
    0.35 to 0.47, and nobody took it. In a fight a line is each one's unless it is plainly for others."""
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Harry", MEMBERS)
    get = "Get the one on me!"
    s = Walker([fight(), fight(lines(get), seq=11)])
    s.judge = Mind(fight={get: ("attack", 0.95)}, target={get: ("an evil mage", 0.9)},
                   to={get: ({"Wystan": 0.5, "everyone": 0.35, "nobody": 0.15}, None)})
    c = companion.Companion(s, mine)
    c.hears(Talks())
    asyncio.run(c.step())
    asyncio.run(c.step())
    assert c.reading(11).to == ["Wystan"] and c.focus is not None and c.focus["serial"] == 0x311
    w.close()


def test_a_fight_order_to_another_or_a_line_of_talk_changes_nothing(tmp_path):
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Harry", MEMBERS)
    said_ = ["Wystan, shoot the lich", "Nice shot!", "cover me"]
    s = Walker([fight()] + [fight(lines(t, start=11 + i), seq=11 + i) for i, t in enumerate(said_)])
    s.judge = Mind(fight={"Wystan, shoot the lich": ("attack", 0.95)}, target={"Wystan, shoot the lich": ("a lich", 0.9)},
                   to={"Wystan, shoot the lich": ("Wystan", 0.99)}, asked={"cover me": 0.95})
    c = companion.Companion(s, mine)
    c.hears(Talks())
    for _ in range(4):
        asyncio.run(c.step())
    assert c.focus is None and c.falling_back is None and not said_aloud(s) and not c.fight_orders
    assert not c.reading(11).owed and not c.reading(12).owed
    # A request that is none of the orders is still answered with what it will and won't do: fight orders among them.
    assert "falls back to Harry" in c.reading(13).owed and "nothing else is done on command" in c.reading(13).owed
    w.close()


def test_a_fight_order_isnt_taken_from_a_line_that_sends_it_away(tmp_path):
    """ "Off to the inn with you, I'll deal with these.": the line is the order to go, and going is the companion's to
    agree to (send_off). Read as a fall back as well (Decision 1 did, 0.85 to 0.88), that is dropped."""
    w = World.open("local", root=tmp_path)
    w.add_place("inn", "The Wayfarer's Inn", 1040, 1000, source="import")
    mine = party.Party(w, "p1", "Isolde", "Harry", MEMBERS)
    off = "Off to the inn with you, I'll deal with these."
    s = Walker([fight(), fight(lines(off), seq=11)])
    s.world = w
    s.judge = Mind(order={off: ("go", 0.9)}, fight={off: ("fall_back", 0.88)}, place={off: ("inn", 0.9)},
                   willing={off: 0.05})
    c = companion.Companion(s, mine)
    asyncio.run(c.step())
    asyncio.run(c.step())
    assert c.falling_back is None and c.refused == 1 and said_aloud(s) == ["I'd rather stay with you for now."]
    w.close()


# ---- the fight loop -----------------------------------------------------------------------


def escorting(*creatures, hp=95, mates=None, **agent):
    snap = view([person("Harry", 6, 6, 0), *creatures])
    snap["player"].update(hits=hp, hits_max=100)
    snap["player"]["supplies"]["heal_potions"] = 0
    snap["agent"].update(bandaging=False, **agent)
    return state.build(snap, set(), [], bestiary=LICH, companions=mates or {"Harry": "companion"})


def test_the_creature_named_is_gone_for_whatever_else_is_about():
    cfg = policy.PolicyConfig(escort="Harry", looting="nothing")
    idle_far = {**monster(0x320, "a skeleton", 50, 10), "dx": -10, "dy": 0, "war_mode": False}  # nobody's to fight
    near = {**monster(0x321, "a skeleton", 50, 2), "dx": 2, "dy": 0}
    sit = escorting(idle_far, near)
    named = replace(cfg, focus=0x320)
    # Without the order the idle one isn't the companion's to fight; Jev's own pick is the one close by.
    dec = policy.decide(escorting(idle_far, near), answers("fight", target="t1"), policy.Memory(), cfg)
    assert dec.target.serial == 0x321
    dec = policy.decide(sit, answers("fight", target="t1"), policy.Memory(), named)
    assert dec.intent == "seek" and dec.target.serial == 0x320 and dec.note.startswith("at Harry's word: ")
    # Within reach it fights it, over Jev's pick and over resting.
    beside = {**idle_far, "distance": 1, "dx": 1}
    dec = policy.decide(escorting(beside, near), answers("rest", target="t2"), policy.Memory(), named)
    assert dec.intent == "fight" and dec.target.serial == 0x320 and {"verb": "attack", "target": 0x320} \
        .items() <= dec.actions[0].items()
    # Its floors still hold: nearly dead with something after it, it runs.
    dec = policy.decide(escorting(beside, near, hp=10), answers("fight", danger=0.97), policy.Memory(), named)
    assert dec.intent == "flee"


def test_a_tamer_sends_its_pet_at_the_one_named_before_what_is_on_it():
    snap = view([person("Harry", 6, 6, 0), {**monster(0x320, "a lich", 24, 5), "dx": 5, "dy": 0},
                 {**monster(0x321, "a skeleton", 50, 1), "dx": 1, "dy": 0, "attacking_me": True}])
    snap["player"].update(hits=95, hits_max=100)
    snap["pets"] = [{"serial": 0x500, "name": "Bramble", "distance": 2, "hits_pct": 100, "dx": 1, "dy": 1}]
    snap["agent"].update(bandaging=False, pet_target=0)
    sit = state.build(snap, set(), [], archetype="tamer", bestiary=LICH, companions={"Harry": "companion"})
    cfg = replace(policy.PolicyConfig(escort="Harry", looting="nothing"), focus=0x320)
    dec = policy.decide(sit, answers("fight", target="t1"), policy.Memory(), cfg)
    assert {"verb": "pet", "kind": "kill", "target": 0x320}.items() <= next(a for a in dec.actions if a["verb"] == "pet").items()


def test_falling_back_it_lets_go_and_fights_only_beside_the_leader():
    cfg = replace(policy.PolicyConfig(escort="Harry", looting="nothing"), fall_back=True)
    on_me = {**monster(0x320, "a skeleton", 50, 1), "dx": 1, "dy": 0, "attacking_me": True}
    # Six tiles from the leader with a skeleton on it: it lets the skeleton go, and the client's follow takes it back.
    dec = policy.decide(escorting(on_me, engaged=0x320), answers("fight", target="t1"), policy.Memory(), cfg)
    assert dec.intent != "fight" and {"verb": "disengage", "confidence": 1.0, "reason": "fall back"} in dec.actions
    assert dec.note.startswith("falling back to Harry")
    # Beside the leader it fights what is on it; what is further off isn't gone for.
    snap = view([person("Harry", 2, 2, 0), on_me, {**monster(0x321, "a skeleton", 50, 5), "dx": 5, "dy": 0}])
    snap["player"].update(hits=95, hits_max=100)
    snap["agent"].update(bandaging=False)
    sit = state.build(snap, set(), [], bestiary=LICH, companions={"Harry": "companion"})
    dec = policy.decide(sit, answers("fight", target="t2"), policy.Memory(), cfg)
    assert dec.intent == "fight" and dec.target.serial == 0x320
    sit = state.build(copy.deepcopy(snap), set(), [], bestiary=LICH, companions={"Harry": "companion"})
    dec = policy.decide(sit, answers("seek", target="t2"), policy.Memory(), cfg)
    assert dec.intent == "fight" and dec.target.serial == 0x320  # seeking is no option while falling back
    # A tamer calls its pet back from what it is on.
    snap = view([person("Harry", 6, 6, 0), {**monster(0x322, "a lich", 24, 5), "dx": 5, "dy": 0}])
    snap["player"].update(hits=95, hits_max=100)
    snap["pets"] = [{"serial": 0x500, "name": "Bramble", "distance": 4, "hits_pct": 100, "dx": 4, "dy": 0}]
    snap["agent"].update(bandaging=False, pet_target=0x322)
    sit = state.build(snap, set(), [], archetype="tamer", bestiary=LICH, companions={"Harry": "companion"})
    dec = policy.decide(sit, answers("fight", target="t1"), policy.Memory(), cfg)
    assert {"verb": "pet", "kind": "follow", "confidence": 1.0, "reason": "fall back"} in dec.actions


def test_a_heal_ordered_is_given_once_whatever_the_health():
    snap = copy.deepcopy(SNAPSHOT)
    snap["player"].update(name="Isolde", x=1000, y=1000, hits=95, hits_max=100)
    snap["mobiles"] = [person("Harry", 3, 3, 0)]
    snap["mobiles"][0]["hits_pct"] = 90
    snap["magic"] = {"spells": [{"name": "Greater Heal"}]}
    snap["agent"].update(engaged=0, bandaging=False)
    sit = state.build(snap, set(), [], archetype="mage", companions={"Harry": "companion"})
    from uo_brain.state import Mate
    sit.mates = [Mate("Harry", 0x700, "companion", 90, 3)]
    cfg = replace(policy.PolicyConfig(escort="Harry"), heal_now=("Harry", 1))
    mem = policy.Memory()
    dec = policy.decide(sit, answers("rest"), mem, policy.PolicyConfig(escort="Harry"))
    assert not any(a["verb"] == "cast" for a in dec.actions)  # at 90% nobody heals it unasked
    dec = policy.decide(sit, answers("rest"), mem, cfg)
    assert {"verb": "cast", "spell": "Greater Heal", "target": 0x700}.items() <= \
        next(a for a in dec.actions if a["verb"] == "cast").items()
    assert mem.heals_done == {1} and "at Harry's word" in dec.note
    mem.last_mate_help = -1e9
    dec = policy.decide(sit, answers("rest"), mem, cfg)
    assert not any(a["verb"] == "cast" for a in dec.actions)  # once
