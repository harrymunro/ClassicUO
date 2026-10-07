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
