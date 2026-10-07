import pytest
import asyncio

from uo_brain import policy, questions, state
from uo_brain.judge import Answers, ChoiceResult, HeuristicJudge

CFG = policy.PolicyConfig()


def sit_of(snapshot):
    return state.build(snapshot, set(), [])


def answers(intent, intent_conf=0.9, probs=None, target="t1", danger=0.1, **nouls):
    probs = probs or {k: (1.0 if k == intent else 0.0) for k in questions.INTENTS}
    a = Answers()
    a.choices["intent"] = ChoiceResult(intent, probs, intent_conf)
    a.choices["target"] = ChoiceResult(target, {target: 1.0}, 0.9)
    a.choices["corpse"] = ChoiceResult("c1", {"c1": 1.0, "none": 0.0}, 0.9)
    a.nouls["in_danger"] = danger
    a.nouls.update(nouls)
    return a


def test_fight_current_target_sends_nothing_new(snapshot):
    dec = policy.decide(sit_of(snapshot), answers("fight"), policy.Memory(), CFG)
    assert dec.intent == "fight" and dec.actions == []


def test_fight_switches_target_when_chosen(snapshot):
    dec = policy.decide(sit_of(snapshot), answers("fight", target="t2"), policy.Memory(), CFG)
    assert dec.actions == [{"verb": "attack", "target": 0x101, "confidence": 0.9, "reason": "fight"}]


def test_flee_needs_danger_to_agree(snapshot):
    calm = policy.decide(sit_of(snapshot), answers("flee", danger=0.2), policy.Memory(), CFG)
    assert calm.intent == "fight"
    scared = policy.decide(sit_of(snapshot), answers("flee", danger=0.8), policy.Memory(), CFG)
    assert scared.intent == "flee"
    assert scared.actions[0]["verb"] == "flee" and scared.actions[0]["target"] == 0x100


def test_loot_is_masked_while_a_hostile_is_close(snapshot):
    probs = {"fight": 0.3, "flee": 0.0, "loot": 0.6, "seek": 0.0, "rest": 0.1}
    dec = policy.decide(sit_of(snapshot), answers("loot", 0.6, probs), policy.Memory(), CFG)
    assert dec.intent == "fight"
    assert "loot" in dec.masked
    assert abs(dec.confidence - 0.75) < 1e-9


def test_low_confidence_keeps_course(snapshot):
    probs = {"fight": 0.4, "flee": 0.3, "loot": 0.0, "seek": 0.0, "rest": 0.3}
    dec = policy.decide(sit_of(snapshot), answers("fight", 0.2, probs), policy.Memory(), CFG)
    assert dec.gated and dec.actions == []


def test_loot_takes_valued_items_and_opens_corpse(snapshot):
    snapshot["mobiles"] = []
    snapshot["agent"]["engaged"] = 0
    dec = policy.decide(sit_of(snapshot), answers("loot", take_i1=0.85), mem := policy.Memory(), CFG)
    verbs = [(a["verb"], a["target"]) for a in dec.actions]
    assert verbs == [("take", 0x40000301), ("loot", 0x40000200)]
    again = policy.decide(sit_of(snapshot), answers("loot", take_i1=0.85), mem, CFG)
    assert ("take", 0x40000301) not in [(a["verb"], a["target"]) for a in again.actions]


def test_seek_walks_to_the_creature(snapshot):
    snapshot["mobiles"] = [snapshot["mobiles"][1]]
    snapshot["corpses"] = []
    snapshot["agent"]["engaged"] = 0
    dec = policy.decide(sit_of(snapshot), answers("seek", target="t1"), policy.Memory(), CFG)
    assert dec.actions[0] | {"confidence": 0, "reason": ""} == {"verb": "walk_to", "x": 994, "y": 1002, "distance": 1,
                                                               "confidence": 0, "reason": ""}


def test_panic_overrides_when_dying_without_potions(snapshot):
    snapshot["player"]["hits"] = 15
    snapshot["player"]["supplies"]["heal_potions"] = 0
    dec = policy.decide(sit_of(snapshot), answers("fight", danger=0.95), policy.Memory(), CFG)
    assert dec.intent == "flee"


def test_memory_marks_corpse_looted_once_client_is_done(snapshot):
    mem = policy.Memory()
    mem.loot_started[0x40000200] = 0.0
    snapshot["agent"]["looting"] = 0x40000200
    mem.update(snapshot["agent"], 5.0)
    assert 0x40000200 not in mem.looted
    snapshot["agent"]["looting"] = 0
    mem.update(snapshot["agent"], 6.0)
    assert 0x40000200 in mem.looted


def test_heuristic_judge_answers_every_question(snapshot):
    snapshot["mobiles"][0]["distance"] = 5
    sit = sit_of(snapshot)
    qs = questions.build(sit)
    ans = asyncio.run(HeuristicJudge().ask(sit.state, qs))
    assert ans.choices["intent"].choice == "loot"
    assert {"intent", "target", "corpse"} <= set(ans.choices)
    assert "take_i1" in ans.nouls


# ---- strategy -------------------------------------------------------------

from uo_brain import strategy as strategies  # noqa: E402


def compile_text(text):
    return asyncio.run(strategies.compile_strategy(HeuristicJudge(), text))[0]


def test_empty_strategy_uses_defaults():
    assert compile_text("   ") == strategies.DEFAULT


def test_never_flee_switches_off_fleeing_and_panic(snapshot):
    knobs = compile_text("Attack relentlessly and never flee.")
    assert knobs.allow_flee is False and knobs.aggression == 1.0
    cfg = strategies.apply(CFG, knobs)
    snapshot["player"]["hits"] = 10
    snapshot["player"]["supplies"]["heal_potions"] = 0
    dec = policy.decide(sit_of(snapshot), answers("flee", danger=0.99), policy.Memory(), cfg)
    assert dec.intent == "fight"
    assert "flee" in dec.masked


def test_no_loot_strategy_stops_pickups(snapshot):
    snapshot["mobiles"] = []
    cfg = strategies.apply(CFG, compile_text("Don't loot anything, just kill."))
    assert cfg.looting == "nothing"
    dec = policy.decide(sit_of(snapshot), answers("rest", take_i1=0.99), policy.Memory(), cfg)
    assert dec.actions == []


def test_weakest_first_fallback(snapshot):
    cfg = strategies.apply(CFG, compile_text("Always finish off the weakest enemy first."))
    a = answers("fight", target="none")
    dec = policy.decide(sit_of(snapshot), a, policy.Memory(), cfg)
    assert dec.actions == []  # weakest is t1, which is already the target


def test_items_are_taken_after_looting_whatever_the_intent(snapshot):
    snapshot["mobiles"] = []
    mem = policy.Memory()
    dec = policy.decide(sit_of(snapshot), answers("rest", take_i1=0.9), mem, CFG)
    assert dec.actions[0]["verb"] == "take"
    declined = policy.Memory()
    policy.decide(sit_of(snapshot), answers("rest", take_i1=0.1), declined, CFG)
    assert 0x40000301 in declined.declined


def test_strategy_reaches_decision_questions_but_not_danger(snapshot):
    snapshot["agent"]["strategy"] = "Never flee."
    qs = questions.build(sit_of(snapshot))
    assert qs["intent"]["instructions"]["player_strategy"] == "Never flee."
    assert qs["target"]["instructions"]["player_strategy"] == "Never flee."
    assert "player_strategy" not in qs["in_danger"]["instructions"]


def test_panic_counts_a_potion_on_cooldown_as_unavailable(snapshot):
    snapshot["player"]["hits"] = 9
    snapshot["agent"]["heal_potion_ready_ms"] = 7000
    dec = policy.decide(sit_of(snapshot), answers("fight", danger=0.95), policy.Memory(), CFG)
    assert dec.intent == "flee"
    snapshot["agent"]["heal_potion_ready_ms"] = 0
    dec = policy.decide(sit_of(snapshot), answers("fight", danger=0.95), policy.Memory(), CFG)
    assert dec.intent == "fight"


def test_potion_readiness_in_words(snapshot):
    snapshot["agent"]["heal_potion_ready_ms"] = 6400
    assert sit_of(snapshot).state["you"]["heal_potion_ready"] == "not for another 6 seconds"


# ---------------------------------------------------------------- combat assist


def assist_snapshot(snapshot, engage="defend"):
    snapshot["agent"].update({"mode": "assist", "engage": engage,
                              "authority": {"heal": "auto", "cure": "auto", "potion": "auto", "fight": "auto",
                                            "loot": "suggest", "move": "off", "misc": "suggest"}})
    return snapshot


def test_combat_assist_offers_only_the_players_target_under_follow(snapshot):
    snap = assist_snapshot(snapshot, "follow")
    snap["mobiles"][1]["player_target"] = True  # the orc captain, 6 tiles away
    sit = state.build(snap, set(), [])
    assert [h.name for h in sit.targets] == ["an orc captain"]
    qs = questions.build(sit)
    assert set(qs["target"]["criteria"]) == {"t2", "none"}


def test_combat_assist_defends_against_attackers(snapshot):
    snap = assist_snapshot(snapshot, "defend")
    snap["mobiles"][0]["attacking_me"] = True
    sit = state.build(snap, set(), [])
    assert [h.serial for h in sit.targets] == [0x100]
    assert sit.hostiles[0].info["attacking_you"] is True


def test_combat_assist_never_walks_or_flees_and_warns_instead(snapshot):
    snap = assist_snapshot(snapshot, "nearby")
    snap["player"]["hits"] = 15
    snap["mobiles"][1].update({"distance": 1, "dx": 1, "dy": 1})
    sit = state.build(snap, set(), [])
    ans = answers("flee", probs={"flee": 0.9, "fight": 0.1}, danger=0.95)
    mem = policy.Memory()
    dec = policy.decide(sit, ans, mem, policy.PolicyConfig(), now=100.0)
    assert dec.intent == "fight"
    assert "flee" in dec.masked
    assert not any(a["verb"] in ("flee", "walk_to") for a in dec.actions)
    assert [a["text"] for a in dec.actions if a["verb"] == "hint"] == ["this fight is going badly, get out"]
    # The same warning is not repeated straight away.
    dec2 = policy.decide(sit, ans, mem, policy.PolicyConfig(), now=105.0)
    assert not any(a["verb"] == "hint" for a in dec2.actions)


def test_next_move_is_the_combat_action(mage):
    mage["agent"]["engaged"] = 0
    sit = state.build(mage, set(), [])
    ans = answers("fight", probs={"fight": 0.9, "rest": 0.1}, target="t1")
    dec = policy.decide(sit, ans, policy.Memory(), policy.PolicyConfig())
    assert dec.next_move is not None and dec.next_move["verb"] in ("attack", "cast")


# ---------------------------------------------------------------- other players


def test_other_players_reach_the_model_by_kind_never_by_name(snapshot):
    snapshot["mobiles"].append({"serial": 0x600, "name": "Xx_Ganker_xX", "label": "Xx_Ganker_xX", "body": 400,
                                "notoriety": "murderer", "human": True, "pet": False, "monster": False,
                                "hits_pct": None, "dead": False, "poisoned": False, "war_mode": True,
                                "distance": 5, "dx": 5, "dy": 0, "dir": "east", "my_target": False, "threat": "red"})
    sit = state.build(snapshot, set(), [])
    text = str(sit.state)
    assert "Ganker" not in text and "a red player (a murderer)" in text


def test_auto_mode_leaves_when_a_red_player_comes_close(snapshot):
    snapshot["agent"]["threats"] = [{"serial": 0x600, "kind": "red", "distance": 5}]
    dec = policy.decide(sit_of(snapshot), answers("fight"), policy.Memory(), CFG)
    assert dec.intent == "flee" and dec.actions[0] == {"verb": "flee", "target": 0x600, "tiles": 15,
                                                        "confidence": 1.0, "reason": "player"}


def test_combat_assist_does_not_move_for_a_red_player(snapshot):
    snap = assist_snapshot(snapshot)
    snap["agent"]["threats"] = [{"serial": 0x600, "kind": "red", "distance": 5}]
    dec = policy.decide(state.build(snap, set(), []), answers("fight"), policy.Memory(), CFG)
    assert not any(a["verb"] == "flee" for a in dec.actions)


# ---------------------------------------------------------------- leaving


BESTIARY = {7: {"type": "OgreLord", "name": "an ogre lord", "hits": 552, "damage": "20-25", "difficulty": "deadly",
                "caster": False},
            17: {"type": "Orc", "name": None, "hits": 72, "damage": "5-7", "difficulty": "weak", "caster": False}}


def test_strength_comes_from_the_bestiary(snapshot):
    sit = state.build(snapshot, set(), [], bestiary=BESTIARY)
    assert sit.hostiles[0].info["strength"] == "weak: an easy kill"
    assert sit.hostiles[1].info["strength"] == "far stronger than you: do not fight it alone"
    assert "far stronger than you" in questions.build(sit)["target"]["criteria"]["t2"]


def test_leave_runs_and_keeps_running_until_out_of_sight(snapshot):
    sit = state.build(snapshot, set(), [], bestiary=BESTIARY)
    mem = policy.Memory()
    dec = policy.decide(sit, answers("leave", probs={"leave": 0.9, "fight": 0.1}), mem, CFG, now=10.0)
    assert dec.intent == "leave" and dec.actions[-1]["verb"] == "flee" and dec.actions[-1]["target"] == 0x101
    # Still leaving on the next look, whatever Jev says, while something is in sight.
    again = policy.decide(sit, answers("fight"), mem, CFG, now=12.0)
    assert again.intent == "leave" and again.actions[0]["verb"] == "flee"
    for m in snapshot["mobiles"]:
        m["distance"] = 20
    clear = policy.decide(state.build(snapshot, set(), []), answers("fight"), mem, CFG, now=14.0)
    assert clear.intent == "leave" and clear.actions == []


def test_leave_needs_a_reason(snapshot):
    sit = state.build(snapshot, set(), [])  # no bestiary: nothing is known to be too strong
    dec = policy.decide(sit, answers("leave", probs={"leave": 0.9, "fight": 0.1}, danger=0.1), policy.Memory(), CFG)
    assert dec.intent == "fight"


def test_champion_goes_for_the_creature_with_most_to_it(snapshot):
    sit = state.build(snapshot, set(), [], bestiary=BESTIARY)
    cfg = policy.PolicyConfig(target_priority="strongest_first", min_target_confidence=2.0)
    assert policy.pick_target(sit, answers("fight"), cfg)[0].serial == 0x101


def test_leaving_is_asked_on_its_own_when_there_is_a_reason(snapshot):
    calm = questions.build(state.build(snapshot, set(), []))
    assert "leave_now" not in calm
    sit = state.build(snapshot, set(), [], bestiary=BESTIARY)
    qs = questions.build(sit)
    assert "far stronger" in qs["leave_now"]["instructions"]["reason"]
    # Jev's yes outweighs its intent vote for fighting.
    dec = policy.decide(sit, answers("fight", leave_now=0.8), policy.Memory(), CFG)
    assert dec.intent == "leave"
    # A relentless strategy never leaves.
    never = policy.PolicyConfig(allow_flee=False)
    assert policy.decide(sit, answers("fight", leave_now=0.95), policy.Memory(), never).intent == "fight"


def test_jev_leaving_decides_below_the_danger_threshold(snapshot):
    # Jev's "leave now" rarely goes past 0.6 even when staying kills the character, so it decides
    # at 0.2 under the strategy's danger threshold (0.425 with no strategy), and never under 0.4.
    sit = state.build(snapshot, set(), [], bestiary=BESTIARY)
    default = policy.PolicyConfig(flee_danger=0.625)
    assert policy.leave_cut(default) == pytest.approx(0.425)
    assert policy.decide(sit, answers("fight", leave_now=0.45), policy.Memory(), default).intent == "leave"
    assert policy.decide(sit, answers("fight", leave_now=0.3), policy.Memory(), default).intent == "fight"
    assert policy.leave_cut(policy.PolicyConfig(flee_danger=0.33)) == 0.4


def test_idle_creatures_are_described_as_not_fighting():
    idle = {"name": "a wisp", "health": "unhurt", "distance": "nearby", "aggressive": False, "casts_spells": "yes",
            "your_current_target": False}
    assert questions.describe_hostile(idle).endswith("a spellcaster, not fighting")
    assert "not fighting" not in questions.describe_hostile(dict(idle, aggressive=True))


def test_defending_only_fights_what_is_close_or_attacking_and_never_seeks_or_loots(snapshot):
    cfg = policy.PolicyConfig(defend_only=True)
    snapshot["mobiles"][0].update({"distance": 6, "dx": 6, "war_mode": False})   # an idle orc 6 tiles off
    snapshot["mobiles"][1].update({"distance": 5, "war_mode": True})            # the captain coming, 5 tiles
    sit = state.build(snapshot, set(), [])
    a = answers("seek", target="t1")
    dec = policy.decide(sit, a, policy.Memory(), cfg)
    assert dec.intent != "seek" and "seek" in dec.masked and "loot" in dec.masked
    assert [h.serial for h in sit.hostiles if h.allowed] == [0x101]


def test_jev_saying_attack_none_is_respected_unless_something_is_on_the_character(snapshot):
    snapshot["mobiles"] = snapshot["mobiles"][1:]  # the captain only, 6 tiles off and idle
    snapshot["mobiles"][0]["war_mode"] = False
    sit = state.build(snapshot, set(), [])
    a = answers("fight", target="none")
    a.choices["target"] = ChoiceResult("none", {"none": 0.56, "t1": 0.44}, 0.56)
    dec = policy.decide(sit, a, policy.Memory(), CFG)
    assert dec.target is None and not any(x["verb"] == "attack" for x in dec.actions)
    snapshot["mobiles"][0].update({"distance": 1, "dx": 1, "war_mode": True})
    dec = policy.decide(state.build(snapshot, set(), []), a, policy.Memory(), CFG)
    assert dec.target is not None
