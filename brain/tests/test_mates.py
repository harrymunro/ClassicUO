"""A party in a fight (cuo-cod.2, cuo-4ti, cuo-e1p): callouts and their meaning, helping a companion,
taking what is on one, and carrying on while the leader is down."""

import asyncio
import copy
import json
from types import SimpleNamespace

from uo_brain import fleet, llm, mates, party, policy, questions, state, voice
from uo_brain.session import Result
from uo_brain.world import World

from conftest import SNAPSHOT, mage_snapshot
from test_party import COMPANIONS, LICH, NAMES, Following, companion, lich_fight, monster
from test_policy import answers


class Rpc:
    def __init__(self):
        self.said = []

    async def call(self, method, **params):
        if method == "act" and params.get("verb") == "say":
            self.said.append(params["text"])
        return {"status": "done"}


def two_sides(tmp_path, me="Roland", other="Isolde"):
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", me, "Roland", NAMES)
    theirs = party.Party(w, "p1", other, "Roland", NAMES)
    mine.companions.update({n: p for n, p in {**COMPANIONS, "Roland": "warrior"}.items() if n != me})
    return w, mine, theirs


def fight(mine, snap, bestiary=LICH):
    snap["agent"]["bandaging"] = False  # the base snapshot is in the middle of a bandage
    sit = state.build(snap, set(), [], bestiary=bestiary, companions=mine.companions)
    link = mates.Mates(mine)
    link.before(sit)
    return sit, link


# ---------------------------------------------------------------- the voice

def test_a_voice_fills_its_slot_and_doesnt_repeat_itself():
    v = voice.Voice()
    assert v.plain
    lines = {v.pick("on_me", target="the lich") for _ in range(3)}
    assert len(lines) == 3 and all("the lich" in ln.lower() and "{" not in ln for ln in lines)
    assert v.pick("down", name="Isolde").count("Isolde") == 1
    assert v.pick("no such kind") == ""
    # A line without the slot its kind needs, with another slot, or far too long, isn't used.
    own = voice.Voice({"on_me": ["Get this {target} off me, you layabouts!", "No slot here", "{name} help!"],
                       "hurt": ["Bleeding like a stuck pig!", "x" * 90], "e_drink": ["*has a swig of ale*"]})
    assert own.lines["on_me"] == ["Get this {target} off me, you layabouts!"] and not own.plain
    assert own.lines["hurt"] == ["Bleeding like a stuck pig!"] and own.lines["e_drink"] == ["has a swig of ale"]
    assert own.lines["leaving"] == voice.KINDS["leaving"][2]  # a kind it didn't write keeps the plain lines


def test_a_voice_pack_is_written_once_for_a_persona_and_kept(tmp_path):
    w = World.open("local", root=tmp_path)
    asked = []

    async def chat(messages, **kw):
        asked.append(messages[1]["content"])
        args = {k: [f"{k} line" + (f" {{{slot}}}" if slot else "")] * 2 for k, (slot, _, _) in voice.KINDS.items()}
        return llm.ChatResult({}, None, [llm.ToolCall("1", "voice", args)], usage=llm.LlmUsage("m", cost=0.001))

    persona = {"name": "Wystan", "temperament": "vain, quick with a jibe"}
    v = asyncio.run(voice.make(w, "Wystan", persona, ["Roland", "Isolde"], chat_fn=chat))
    assert not v.plain and v.pick("hurt") == "Hurt line" and "Roland, Isolde" in asked[0] and "vain" in asked[0]
    again = asyncio.run(voice.make(w, "Wystan", persona, ["Isolde", "Roland"], chat_fn=chat))
    assert len(asked) == 1 and again.pick("leaving") == "Leaving line"  # from the store, whatever the order of names
    # No persona: the plain lines, and no call. A failed call: the plain lines for this session.
    assert asyncio.run(voice.make(w, "Roland", None, chat_fn=chat)).plain and len(asked) == 1

    async def broken(messages, **kw):
        raise llm.LlmError("no")

    assert asyncio.run(voice.make(w, "Thea", {"temperament": "gentle"}, chat_fn=broken)).plain
    w.close()


# ---------------------------------------------------------------- what is on whom

def test_what_is_on_a_companion_reaches_the_fight_state(tmp_path):
    """Isolde reports a lich on her and her health; Roland's state says so, in its creature and in its party."""
    w, mine, theirs = two_sides(tmp_path)
    theirs.fight({"hits": 18, "hits_max": 60, "dead": False, "attackers": [0x300], "target": 0x300, "pets": []})
    theirs.call("badly_hurt", hits=18, hits_max=60)
    theirs.call("on_me", target=0x300, about="a lich")
    sit, _ = fight(mine, lich_fight(lord=False))
    isolde = next(m for m in sit.mates if m.name == "Isolde")
    assert isolde.hits_pct == 30 and isolde.attackers == [0x300] and isolde.weak_in_melee
    assert sit.hostiles[0].info["attacking_your_companion"] == "Isolde (mage, badly wounded)"
    assert next(e for e in sit.state["your_party"] if e["name"] == "Isolde")["attacked_by"] == ["a lich"]
    assert sit.state["party_calls"] == ["Isolde (mage), just now: near death, 18/60 health",
                                        "Isolde (mage), just now: a lich is attacking them"]
    # The target question says who it is on, and that it is worth taking off her.
    q = questions.build(sit)["target"]
    assert "attacking your companion Isolde (mage, badly wounded)" in q["criteria"]["t1"]
    assert "worth taking off them" in q["instructions"]["guidance"]
    # A row gone stale says nothing: the companion is still seen, by the client's own figure.
    w.db.execute("UPDATE party_fight SET t = t - 60")
    w.db.execute("UPDATE party_calls SET t = t - 60")
    w.db.commit()
    sit, _ = fight(mine, lich_fight(lord=False))
    assert next(m for m in sit.mates if m.name == "Isolde").attackers == [] and "party_calls" not in sit.state
    assert "attacking_your_companion" not in sit.hostiles[0].info
    assert "worth taking off them" not in questions.build(sit)["target"]["instructions"]["guidance"]
    w.close()


def test_a_warrior_takes_what_is_on_the_mage_when_jev_isnt_sure(tmp_path):
    w, mine, theirs = two_sides(tmp_path)
    snap = lich_fight(lord=False)
    snap["mobiles"].insert(0, monster(0x301, "a skeleton", 50, 1))  # t1: the skeleton on Roland; t2: the lich
    snap["agent"]["engaged"] = 0x301
    theirs.fight({"hits": 40, "hits_max": 60, "dead": False, "attackers": [0x300], "pets": []})
    sit, _ = fight(mine, snap)
    unsure = answers("fight", target="t1")
    unsure.choices["target"].confidence = 0.1
    dec = policy.decide(sit, unsure, policy.Memory(), policy.PolicyConfig())
    assert dec.target.serial == 0x300 and {"verb": "attack", "target": 0x300, "confidence": 0.9, "reason": "fight"} in dec.actions
    # Jev's own sure pick stands; and with helping off, the creature already being fought.
    sit, _ = fight(mine, snap)
    assert policy.decide(sit, answers("fight", target="t1"), policy.Memory(), policy.PolicyConfig()).target.serial == 0x301
    sit, _ = fight(mine, snap)
    assert policy.decide(sit, unsure, policy.Memory(), policy.PolicyConfig(help_mates=False)).target.serial == 0x301
    # An archer doesn't go in for it: it can't take the hits either.
    w2, archer, _ = two_sides(tmp_path, me="Wystan")
    snap2 = copy.deepcopy(snap)
    snap2["player"]["ranged"] = {"kind": "bow", "ammo": "arrows", "range": 10}
    snap2["player"]["supplies"]["arrows"] = 100
    sit, _ = fight(archer, snap2)
    assert policy.rescue_target(sit, sit.targets, policy.PolicyConfig()) is None
    w.close()


# ---------------------------------------------------------------- helping

def hurt_mate(snap, name, pct, distance):
    m = next(m for m in snap["mobiles"] if m.get("name") == name)
    m.update(hits_pct=pct, distance=distance, dx=distance, dy=0)
    return snap


def test_a_companion_within_reach_is_bandaged_and_one_further_off_only_after_the_fight(tmp_path):
    w, mine, _ = two_sides(tmp_path)
    snap = hurt_mate(lich_fight(lord=False), "Isolde", 40, 2)
    snap["mobiles"][0]["distance"] = 6  # the lich isn't on Roland
    sit, _ = fight(mine, snap)
    mem = policy.Memory()
    dec = policy.decide(sit, answers("fight", target="t1"), mem, policy.PolicyConfig(), now=100.0)
    assert {"verb": "bandage", "target": 0x901, "confidence": 1.0, "reason": "companion"} in dec.actions
    assert mem.helped == ("Isolde", "bandage") and "bandage Isolde (40%)" in dec.note
    # Not again within a few seconds; and not at all when it is badly hurt itself, or told not to.
    sit, _ = fight(mine, snap)
    assert not any(a["verb"] == "bandage" for a in policy.decide(sit, answers("fight"), mem, policy.PolicyConfig(), now=102.0).actions)
    low = copy.deepcopy(snap)
    low["player"]["hits"] = 30
    sit, _ = fight(mine, low)
    assert not any(a["verb"] == "bandage" for a in policy.decide(sit, answers("fight"), policy.Memory(), policy.PolicyConfig()).actions)
    sit, _ = fight(mine, snap)
    off = policy.decide(sit, answers("fight"), policy.Memory(), policy.PolicyConfig(help_mates=False))
    assert not any(a.get("reason") == "companion" for a in off.actions)
    # Five tiles off: nobody walks over while the fight is on (the first live run's archer, tamer and bard
    # all walked to the warrior, into the lich on him). Once nothing hostile is near, one does.
    far = hurt_mate(lich_fight(lord=False), "Isolde", 40, 5)
    far["mobiles"][0]["distance"] = 6
    sit, _ = fight(mine, far)
    assert not any(a.get("reason") == "companion" for a in policy.decide(sit, answers("rest"), policy.Memory(),
                                                                          policy.PolicyConfig()).actions)
    far["mobiles"][0]["distance"] = 12
    sit, _ = fight(mine, far)
    dec = policy.decide(sit, answers("rest"), policy.Memory(), policy.PolicyConfig())
    assert any(a["verb"] == "walk_to" and a["reason"] == "companion" and a["x"] == 1005 for a in dec.actions)
    w.close()


def test_a_mage_heals_the_worst_hurt_companion_from_a_distance(tmp_path):
    w, mine, _ = two_sides(tmp_path, me="Isolde", other="Roland")
    snap = mage_snapshot()
    snap["player"]["name"] = "Isolde"
    snap["mobiles"] += [companion("Roland", 6), companion("Wystan", 4)]
    hurt_mate(hurt_mate(snap, "Roland", 35, 6), "Wystan", 55, 4)
    sit, _ = fight(mine, snap, bestiary=None)
    mem = policy.Memory()
    dec = policy.decide(sit, answers("fight", target="t1"), mem, policy.PolicyConfig())
    heal = next(a for a in dec.actions if a.get("reason") == "companion")
    assert heal == {"verb": "cast", "spell": "Greater Heal", "target": 0x900, "queue": True, "confidence": 1.0,
                    "reason": "companion"} and dec.actions[-1] is heal  # queued last, so it goes first
    assert mem.helped == ("Roland", "heal")
    # One another member is already healing is left to them, unless it is near death.
    sit, link = fight(mine, snap, bestiary=None)
    next(m for m in sit.mates if m.name == "Roland").being_helped = True
    dec = policy.decide(sit, answers("fight", target="t1"), policy.Memory(), policy.PolicyConfig())
    assert next(a for a in dec.actions if a.get("reason") == "companion")["target"] == 0x902  # Wystan
    w.close()


def test_a_tamer_vets_another_tamers_pet(tmp_path):
    w, mine, theirs = two_sides(tmp_path, me="Wystan", other="Thea")
    snap = lich_fight(lord=False)
    snap["player"]["name"] = "Wystan"
    snap["player"]["skills"] = {"Veterinary": 60.0, "Healing": 0.0}
    snap["mobiles"][0]["distance"] = 8
    snap["mobiles"].append({"serial": 0x777, "name": "a grizzly bear", "body": 212, "notoriety": "innocent", "human": False,
                            "monster": False, "pet": False, "hits_pct": 35, "dead": False, "distance": 2, "dx": 2,
                            "dy": 0, "dir": "east"})
    theirs.fight({"hits": 60, "hits_max": 60, "dead": False, "attackers": [],
                  "pets": [{"serial": 0x777, "name": "a grizzly bear", "hits_pct": 35}]})
    sit, _ = fight(mine, snap)
    assert next(m for m in sit.mates if m.name == "Thea").pets[0]["distance"] == 2
    mem = policy.Memory()
    dec = policy.decide(sit, answers("rest"), mem, policy.PolicyConfig())
    assert {"verb": "bandage", "target": 0x777, "confidence": 1.0, "reason": "companion pet"} in dec.actions
    assert mem.helped == ("Thea", "vet")
    w.close()


# ---------------------------------------------------------------- callouts

def decide(sit, link, rpc, intent="fight", mem=None, **kw):
    mem = mem or policy.Memory()
    dec = policy.decide(sit, answers(intent, **kw), mem, policy.PolicyConfig())
    return dec, asyncio.run(link.after(rpc, sit, dec, mem))


def test_a_member_calls_out_what_it_is_on_and_when_it_is_hurt(tmp_path):
    w, mine, theirs = two_sides(tmp_path)
    rpc = Rpc()
    snap = lich_fight(lord=False)
    snap["mobiles"][0].update(distance=1, attacking_me=True)
    sit, link = fight(mine, snap)
    _, said = decide(sit, link, rpc)
    # A lich on it (a spellcaster, stronger): called at once, and its meaning is in the store for the others.
    assert said in [ln.replace("{target}", "the lich").capitalize()[:1] + ln.replace("{target}", "the lich")[1:]
                    for ln in voice.KINDS["on_me"][2]]
    heard = theirs.calls(15)
    assert [(c["who"], c["kind"], c["target"]) for c in heard] == [("Roland", "on_me", 0x300), ("Roland", "on_target", 0x300)]
    # The next callout waits its turn: what it is on isn't said a second after "on me".
    assert asyncio.run(link.flush(rpc, sit)) is None and len(rpc.said) == 1
    # Under a third of its health: said at once, whatever was said a moment ago.
    snap["player"]["hits"] = 25
    sit = state.build(snap, set(), [], bestiary=LICH, companions=mine.companions)
    link.before(sit)
    _, said = decide(sit, link, rpc)
    assert said in voice.KINDS["badly_hurt"][2] and theirs.calls(15, kind="badly_hurt")[0]["hits"] == 25
    # Healed back over 60%: "I'm all right", once the gap allows.
    snap["player"]["hits"] = 80
    sit = state.build(snap, set(), [], bestiary=LICH, companions=mine.companions)
    link.before(sit)
    link.last_said = -1e9
    link.pending.clear()
    _, said = decide(sit, link, rpc)
    assert said in voice.KINDS["recovered"][2]
    assert link.to_log()["callouts"] == {"on_me": 1, "on_target": 1, "badly_hurt": 1, "recovered": 1}
    w.close()


def test_leaving_running_out_a_fallen_companion_and_the_end_are_called(tmp_path):
    w, mine, theirs = two_sides(tmp_path, me="Corwin", other="Roland")  # Corwin: first by name of the five
    rpc = Rpc()
    snap = lich_fight(lord=False)
    snap["player"]["name"] = "Corwin"
    snap["mobiles"] = [m for m in snap["mobiles"] if m.get("name") != "Corwin"] + [companion("Roland", 3)]
    snap["mobiles"][0].update(distance=2)
    snap["player"]["supplies"]["bandages"] = 0
    for m in snap["mobiles"]:
        if m.get("name") in ("Isolde", "Wystan", "Thea"):
            m["distance"] = 11  # in view, not beside it: with two beside it a member doesn't leave on its own
    sit, link = fight(mine, snap)
    dec, said = decide(sit, link, rpc, intent="leave", leave_now=0.9, danger=0.9)
    assert dec.intent == "leave" and said in voice.KINDS["leaving"][2]
    assert {c["kind"] for c in theirs.calls(15)} == {"leaving", "out_of_bandages"}
    # A companion falls: only the first by name of those who see it says so (Corwin here, not a Zed beside it).
    for m in snap["mobiles"]:
        if m.get("name") in ("Isolde", "Wystan", "Thea"):
            m["distance"] = 4
    next(m for m in snap["mobiles"] if m.get("name") == "Thea")["dead"] = True
    sit, _ = fight(mine, snap)
    link.before(sit)
    link.events(sit, dec, policy.Memory())
    assert link.called.get("down") == 1 and link.pending["down"].slots == {"name": "Thea"} and link.down == {"Thea"}
    other = mates.Mates(party.Party(w, "p1", "Zed", "Roland", [*NAMES, "Zed"]))
    other.party.companions.update({**COMPANIONS, "Roland": "warrior", "Corwin": "bard"})
    seen = copy.deepcopy(snap)
    seen["mobiles"].append(companion("Corwin", 2))
    sit2 = state.build(seen, set(), [], bestiary=LICH, companions=other.party.companions)
    other.before(sit2)
    other.events(sit2, dec, policy.Memory())
    assert "down" not in other.called and other.down == {"Thea"}
    # The last creature dead after a fight: "all clear", from the first by name.
    link.in_fight = True
    quiet = copy.deepcopy(snap)
    quiet["mobiles"] = [m for m in quiet["mobiles"] if not m.get("monster")]
    sit3 = state.build(quiet, set(), [], bestiary=LICH, companions=mine.companions)
    link.before(sit3)
    link.events(sit3, policy.decide(sit3, answers("rest"), policy.Memory(), policy.PolicyConfig()), policy.Memory())
    assert link.called.get("clear") == 1 and not link.in_fight
    w.close()


def test_helping_is_called_so_two_dont_heal_the_same_one(tmp_path):
    w, mine, theirs = two_sides(tmp_path)
    rpc = Rpc()
    snap = hurt_mate(lich_fight(lord=False), "Wystan", 40, 2)
    snap["mobiles"][0]["distance"] = 6
    sit, link = fight(mine, snap)
    mem = policy.Memory()
    dec = policy.decide(sit, answers("fight", target="t1"), mem, policy.PolicyConfig())
    asyncio.run(link.after(rpc, sit, dec, mem))
    assert theirs.calls(15, kind="helping")[0]["about"] == "Wystan"
    # Isolde, beside them, sees that Wystan is being seen to.
    isolde = mates.Mates(theirs)
    theirs.companions.update({"Roland": "warrior", "Wystan": "archer"})
    other = mage_snapshot()
    other["player"]["name"] = "Isolde"
    other["mobiles"] += [companion("Wystan", 3), companion("Roland", 2)]
    hurt_mate(other, "Wystan", 40, 3)
    sit2 = state.build(other, set(), [], companions=theirs.companions)
    isolde.before(sit2)
    assert next(m for m in sit2.mates if m.name == "Wystan").being_helped
    dec2 = policy.decide(sit2, answers("fight", target="t1"), policy.Memory(), policy.PolicyConfig())
    assert not any(a.get("reason") == "companion" for a in dec2.actions)
    w.close()


def test_callouts_can_be_off_and_the_rest_still_works(tmp_path):
    w, mine, theirs = two_sides(tmp_path)
    rpc = Rpc()
    link = mates.Mates(mine, callouts=False)
    snap = lich_fight(lord=False)
    snap["mobiles"][0].update(distance=1, attacking_me=True)
    sit = state.build(snap, set(), [], bestiary=LICH, companions=mine.companions)
    link.before(sit)
    dec = policy.decide(sit, answers("fight"), policy.Memory(), policy.PolicyConfig())
    assert asyncio.run(link.after(rpc, sit, dec, policy.Memory())) is None and rpc.said == [] and theirs.calls(15) == []
    assert theirs.fights()["Roland"]["attackers"] == [0x300]  # the fight row is still written
    w.close()


# ---------------------------------------------------------------- the leader down (cuo-e1p)

class Hunting(Following):
    async def hunt(self, area, minutes):
        self.goals.append(("hunt", area, minutes))
        return Result(True, "hunted", {"minutes": minutes, "kills": 2, "deaths": 0})


def test_followers_carry_on_the_hunt_while_the_leader_is_down(tmp_path):
    w = World.open("local", root=tmp_path)
    lead = party.Party(w, "p1", "Roland", "Roland", NAMES)
    s = Hunting(w)
    f = party.Follower(s, party.Party(w, "p1", "Isolde", "Roland", NAMES))
    lead.post("hunt", {"area": "Britain Graveyard", "minutes": 12})
    asyncio.run(f.step())
    # The leader dies and sees to itself: its resurrection isn't waited out, the hunt goes on in short spells.
    lead.post("resurrect", {})
    asyncio.run(f.step())
    lead.post("recover_corpse", {})
    asyncio.run(f.step())
    asyncio.run(f.step())  # still its corpse: another spell
    assert s.goals == [("hunt", "Britain Graveyard", 12.0)] + [("hunt", "Britain Graveyard", party.CARRY_ON_MIN)] * 3
    assert f.carried_on == 3 and "while the leader is down" in f.doing
    # Back, the leader's next goal is followed as before.
    lead.post("rest", {"seconds": 20})
    asyncio.run(f.step())
    assert s.goals[-1] == ("rest", 20.0)
    w.close()


def test_a_hunt_isnt_cut_short_by_the_leaders_own_business(tmp_path):
    w = World.open("local", root=tmp_path)
    lead = party.Party(w, "p1", "Roland", "Roland", NAMES)
    s = Hunting(w)
    f = party.Follower(s, party.Party(w, "p1", "Isolde", "Roland", NAMES))

    async def go(tool, hunting):
        s.interrupt.clear()
        f.hunting = hunting
        stop = asyncio.Event()
        watcher = asyncio.create_task(f.watch(stop))
        lead.post(tool, {})
        await asyncio.sleep(0.1)
        stop.set()
        watcher.cancel()
        return s.interrupt.is_set()

    assert asyncio.run(go("resurrect", True)) is False       # fights on
    assert asyncio.run(go("resurrect", False)) is True       # a rest or a walk is cut short, as before
    assert asyncio.run(go("travel_to", True)) is True        # a goal for the party cuts a hunt short
    w.close()


def test_with_no_hunt_to_carry_on_a_follower_waits_and_keeps_to_the_deputy(tmp_path):
    w = World.open("local", root=tmp_path)
    lead = party.Party(w, "p1", "Roland", "Roland", NAMES)
    s = Hunting(w)
    f = party.Follower(s, party.Party(w, "p1", "Wystan", "Roland", NAMES))
    lead.post("resurrect", {})
    asyncio.run(f.step())
    assert s.goals == [("rest", 15)]  # nothing hunted yet: waits where it is
    # The leader a ghost far off; Isolde, first of the others, is the deputy: Wystan keeps to her, not to it.
    ghost = copy.deepcopy(SNAPSHOT) | {"in_game": True}
    ghost["player"].update(x=1300, y=1000, dead=True)
    lead.report(ghost, "warrior")
    deputy = copy.deepcopy(SNAPSHOT) | {"in_game": True}
    deputy["player"].update(x=1030, y=1000)
    party.Party(w, "p1", "Isolde", "Roland", NAMES).report(deputy, "mage")
    assert f.anchor()["x"] == 1030
    s.goals.clear()
    asyncio.run(f.keep_up(json.loads(json.dumps(SNAPSHOT))))
    assert s.goals == [("travel_to", (1030, 1000), 3)]
    # The deputy itself holds where it is.
    assert party.Follower(s, party.Party(w, "p1", "Isolde", "Roland", NAMES)).anchor() is None
    w.close()


# ---------------------------------------------------------------- the fleet file

def test_a_fleet_run_can_override_the_files_settings(tmp_path):
    f = tmp_path / "p.toml"
    f.write_text('name = "p"\nparty = true\nleader = "A"\n[[agent]]\nname = "A"\naccount = "a"\npassword = "a"\nport = 1\n'
                 'goal = "hunt"\n[[agent]]\nname = "B"\naccount = "b"\npassword = "b"\nport = 2\n')
    fl = fleet.load(f)
    assert fl.callouts and fl.help
    off = fleet.load(f, ["help=false", "callouts = false", "hours=0.25"])
    assert not off.help and not off.callouts and off.hours == 0.25
    assert fleet.parse_setting('name="x y"') == ("name", "x y") and fleet.parse_setting("leader=B") == ("leader", "B")
    # A model for one kind of call, for every brain of the run.
    assert fleet.load(f, ['use="table=anthropic/claude-sonnet-5.5"']).use == ["table=anthropic/claude-sonnet-5.5"]
    assert fleet.load(f, ['use=["table=sonnet", "talk=sonnet"]']).use == ["table=sonnet", "talk=sonnet"] and fl.use == []
    started = {}

    class Proc(SimpleNamespace):
        pass

    import subprocess
    real = subprocess.Popen
    try:
        subprocess.Popen = lambda cmd, **kw: started.setdefault(cmd[cmd.index("--port") + 1], cmd) or Proc()
        fleet.start_brain(off, off.agents[1], tmp_path)
        fleet.start_brain(fl, fl.agents[0], tmp_path)
    finally:
        subprocess.Popen = real
    assert "--no-callouts" in started["2"] and "--no-party-help" in started["2"]
    assert "--no-callouts" not in started["1"] and "--no-party-help" not in started["1"]


def test_a_creature_is_shouted_by_what_it_is():
    hostiles = [state.Candidate("t1", 1, "a bone knight (a skeleton)", 2, {}), state.Candidate("t2", 2, "a skeleton", 3, {}),
                state.Candidate("t3", 3, "Vorgak (an orc)", 4, {}), state.Candidate("t4", 4, "a lich", 5, {}),
                state.Candidate("t5", 5, "a skeleton", 6, {})]
    assert [mates.short(h, hostiles) for h in hostiles] == ["the bone knight", "a skeleton", "the orc", "the lich",
                                                           "a skeleton"]


# ---------------------------------------------------------------- found in review (2026-10-11)

def test_a_death_ends_the_members_own_fight(tmp_path):
    """Its first decision after the healer isn't "all clear" from a hundred tiles off."""
    w, mine, theirs = two_sides(tmp_path, me="Corwin", other="Roland")
    snap = lich_fight(lord=False)
    snap["player"]["name"] = "Corwin"
    snap["mobiles"] = [m for m in snap["mobiles"] if m.get("name") != "Corwin"]
    sit, link = fight(mine, snap)
    dec, _ = decide(sit, link, Rpc())
    link.note(sit, "hurt")   # something still waiting to be said
    assert link.in_fight and link.pending and link.target_called
    link.died()
    assert not link.in_fight and not link.pending and link.target_called == 0 and link.story.deaths == 1
    alone = copy.deepcopy(snap)   # resurrected at the healer: nobody near, no bandages, a sliver of health
    alone["mobiles"] = []
    alone["player"].update(hits=12)
    alone["player"]["supplies"]["bandages"] = 0
    sit2 = state.build(alone, set(), [], bestiary=LICH, companions=mine.companions)
    link.before(sit2)
    before = dict(link.called)
    link.events(sit2, policy.decide(sit2, answers("rest"), policy.Memory(), policy.PolicyConfig()), policy.Memory())
    assert link.called == before and not theirs.calls(15, kind="clear")
    # Nor anything else from the healer, whatever comes by there: not until it is in a fight again.
    by_the_healer = copy.deepcopy(alone)
    by_the_healer["mobiles"] = [monster(0x333, "a skeleton", 50, 9)]
    sit3 = state.build(by_the_healer, set(), [], bestiary=LICH, companions=mine.companions)
    link.before(sit3)
    link.events(sit3, policy.decide(sit3, answers("rest"), policy.Memory(), policy.PolicyConfig()), policy.Memory())
    assert link.away and link.called == before and not link.pending
    back = copy.deepcopy(snap)
    back["player"]["supplies"]["bandages"] = 0
    sit4 = state.build(back, set(), [], bestiary=LICH, companions=mine.companions)
    link.before(sit4)
    decide(sit4, link, Rpc())
    assert not link.away and link.called.get("out_of_bandages") == 1
    w.close()


def test_a_companion_who_leaves_the_view_and_reports_dead_has_fallen(tmp_path):
    """A dead companion's ghost isn't sent to the living: its own report is how the others know."""
    w, mine, theirs = two_sides(tmp_path, me="Corwin", other="Thea")
    snap = lich_fight(lord=False)
    snap["player"]["name"] = "Corwin"
    snap["mobiles"] = [m for m in snap["mobiles"] if m.get("name") != "Corwin"]
    sit, link = fight(mine, snap)
    assert "Thea" in {m.name for m in sit.mates}
    gone = copy.deepcopy(snap)
    gone["mobiles"] = [m for m in gone["mobiles"] if m.get("name") != "Thea"]
    sit = state.build(gone, set(), [], bestiary=LICH, companions=mine.companions)
    link.before(sit)
    assert "Thea" not in {m.name for m in sit.mates}  # out of view, and nothing says she is dead
    dead = copy.deepcopy(SNAPSHOT) | {"in_game": True}
    dead["player"].update(name="Thea", dead=True, hits=0)
    theirs.report(dead, "tamer")
    link._stats = (0.0, {})
    sit = state.build(gone, set(), [], bestiary=LICH, companions=mine.companions)
    link.before(sit)
    thea = next(m for m in sit.mates if m.name == "Thea")
    assert thea.dead and thea.distance == 5
    assert {"name": "Thea", "plays_as": "tamer", "health": "dead: fell a moment ago"} in sit.state["your_party"]
    link.events(sit, policy.decide(sit, answers("fight"), policy.Memory(), policy.PolicyConfig()), policy.Memory())
    assert link.called.get("down") == 1 and link.story.fell == ["Thea"]
    w.close()


def test_what_was_heard_is_kept_once_and_counted_by_decision(tmp_path):
    w, mine, theirs = two_sides(tmp_path)
    theirs.call("badly_hurt", hits=18, hits_max=60)
    snap = lich_fight(lord=False)
    sit, link = fight(mine, snap)
    for _ in range(5):   # `before` runs at every look, a decision or not
        sit = state.build(snap, set(), [], bestiary=LICH, companions=mine.companions)
        link.before(sit)
    assert link.story.heard == ["Isolde (mage): near death, 18/60 health"] and link.heard == 0
    decide(sit, link, Rpc())
    assert link.to_log()["decisions_with_calls_heard"] == 1
    w.close()


def test_a_voice_line_with_its_own_article_doesnt_say_the_the():
    v = voice.Voice({"on_me": ["Get this {target} off my back!"], "on_target": ["Everyone, the {target} is mine!"]})
    assert v.pick("on_me", target="the bone knight") == "Get this bone knight off my back!"
    assert v.pick("on_target", target="a bone knight") == "Everyone, the bone knight is mine!"
    assert voice.Voice().pick("down", name="Thea") in [ln.replace("{name}", "Thea") for ln in voice.KINDS["down"][2]]


class Fragile(Hunting):
    """A session whose travel breaks, as a lost client's would."""

    async def travel_to(self, *a, **kw):
        raise TimeoutError("no answer from the client")

    def hunt_spot(self, area):
        return (1380, 1500, area, 10)

    async def snap(self, **_):
        s = copy.deepcopy(SNAPSHOT) | {"in_game": True}
        s["player"].update(x=self.x, y=1500)
        return s


def test_a_goal_the_follower_cant_read_or_that_breaks_is_its_failure_not_its_end(tmp_path):
    """One `minutes: null` from the leader's planner ended all four followers' sessions."""
    w = World.open("local", root=tmp_path)
    lead = party.Party(w, "p1", "Roland", "Roland", NAMES)
    s, logged = Fragile(w), []
    s.x = 1380
    f = party.Follower(s, party.Party(w, "p1", "Isolde", "Roland", NAMES), log=logged.append)
    for tool, args in (("hunt", {"area": "Britain Graveyard", "minutes": None}), ("rest", {"seconds": "ten"}),
                       ("travel_to", {"place": ["Britain", "Graveyard"]}), ("inn", {"place": "the inn", "x": None})):
        lead.post(tool, args)
        asyncio.run(f.step())
    assert s.goals == [("hunt", "Britain Graveyard", 10.0), ("rest", 30.0)]
    results = [r["result"] for r in logged if r.get("type") == "goal_result"]
    assert [r["ok"] for r in results] == [True, True, False, False]
    assert results[2]["result"].startswith("error: TimeoutError") and f.goals == 4
    w.close()


def test_the_hunt_is_carried_on_only_by_those_still_at_it_once_the_leader_is_alive(tmp_path):
    """The leader's corpse goal was refused in 0.4 s; three in town set off for a graveyard 130 tiles away."""
    w = World.open("local", root=tmp_path)
    lead = party.Party(w, "p1", "Roland", "Roland", NAMES)
    s = Fragile(w)
    s.x = 1380
    f = party.Follower(s, party.Party(w, "p1", "Isolde", "Roland", NAMES))
    lead.post("hunt", {"area": "Britain Graveyard", "minutes": 12})
    asyncio.run(f.step())
    alive = copy.deepcopy(SNAPSHOT) | {"in_game": True}
    lead.report(alive, "warrior")
    lead.post("recover_corpse", {})
    s.x = 1500   # in town, 120 tiles from the hunt
    asyncio.run(f.step())
    assert f.carried_on == 0 and s.goals[-1] == ("rest", 15)
    s.x = 1390   # at the graveyard: it fights on
    asyncio.run(f.step())
    assert f.carried_on == 1 and s.goals[-1] == ("hunt", "Britain Graveyard", party.CARRY_ON_MIN)
    alive["player"].update(dead=True)   # the leader dead: wherever they are
    lead.report(alive, "warrior")
    s.x = 1500
    asyncio.run(f.step())
    assert f.carried_on == 2
    w.close()


def test_a_second_run_into_the_same_folder_is_another_party(tmp_path, monkeypatch):
    seen = []

    async def no_login(fl, a):
        return "ok"

    class Brain:
        pid = 1

        def poll(self):
            return 0

    monkeypatch.setattr(fleet, "start_client", lambda a, out: None)
    monkeypatch.setattr(fleet, "log_in", no_login)
    monkeypatch.setattr(fleet, "start_brain", lambda fl, a, out, *_: seen.append(fl.party_id) or Brain())
    monkeypatch.setattr(fleet, "pid_listening", lambda port: 0)
    monkeypatch.setattr(fleet, "report", lambda out: {})
    f = tmp_path / "party-lich.toml"
    f.write_text('name = "party-lich"\nparty = true\nleader = "A"\n[[agent]]\nname = "A"\naccount = "a"\npassword = "a"\n'
                 'port = 1\ngoal = "hunt"\n[[agent]]\nname = "B"\naccount = "b"\npassword = "b"\nport = 2\n')
    fl = fleet.load(f)
    asyncio.run(fleet.run(fl, tmp_path / "video-1"))
    first = fl.party_id
    asyncio.run(fleet.run(fl, tmp_path / "video-1"))
    assert first == "party-lich-video-1" and fl.party_id.startswith("party-lich-video-1-") and fl.party_id != first
    w = [json.loads(line) for line in (tmp_path / "video-1" / "fleet.jsonl").open()]
    assert [r["party_id"] for r in w if r.get("type") == "fleet"] == [first, fl.party_id]
