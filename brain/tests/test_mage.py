import asyncio

from uo_brain import loop, policy, questions, state
from uo_brain.judge import Answers, ChoiceResult, HeuristicJudge

CFG = policy.PolicyConfig()


def sit_of(snapshot, casts_at=None):
    return state.build(snapshot, set(), [], casts_at=casts_at)


def answers(intent="fight", conf=0.9, target="t1", spell="s2", spell_conf=0.8, danger=0.1):
    a = Answers()
    a.choices["intent"] = ChoiceResult(intent, {k: (1.0 if k == intent else 0.0) for k in questions.MAGE_INTENTS}, conf)
    a.choices["target"] = ChoiceResult(target, {target: 1.0}, 0.9)
    a.choices["spell"] = ChoiceResult(spell, {spell: spell_conf}, spell_conf)
    a.nouls["in_danger"] = danger
    return a


def test_archetype_follows_skills_and_spellbook(mage, snapshot):
    assert state.archetype_of(mage) == "mage"
    assert state.archetype_of(snapshot) == "warrior"
    mage["player"]["skills"]["Swordsmanship"] = 100.0
    assert state.archetype_of(mage) == "warrior"


def test_mage_state_has_mana_spells_and_range_in_words(mage):
    sit = sit_of(mage, casts_at={0x100: 2})
    you = sit.state["you"]
    assert sit.is_mage
    assert you["mana"] == "full (90%)"
    assert you["next_spell"] == "can cast now"
    assert "bandages_left" not in you
    # Ordered strongest first; Flame Strike lacks reagents.
    assert you["attack_spells_available"] == ["Energy Bolt", "Explosion", "Lightning", "Fireball", "Magic Arrow"]
    assert you["reagents"] == "running out of nightshade"
    t1, t2 = sit.state["hostile_creatures"]
    assert t1["in_spell_range"] is True and t2["in_spell_range"] is False
    assert t1["your_spells_at_it"] == "2 so far" and t2["your_spells_at_it"] == "none yet"


def test_spell_question_offers_castable_spells_and_none(mage):
    mage["agent"]["strategy"] = "Open every fight with an explosion spell."
    qs = questions.build(sit_of(mage))
    assert set(qs) == {"intent", "in_danger", "target", "spell"}
    assert set(qs["spell"]["criteria"]) == {"s1", "s2", "s3", "s4", "s5", "none"}
    assert qs["spell"]["criteria"]["s2"].startswith("Explosion:")
    assert qs["spell"]["instructions"]["player_strategy"] == "Open every fight with an explosion spell."
    assert set(qs["intent"]["criteria"]) == {"fight", "flee", "leave", "loot", "seek", "rest"}
    assert "mage" in qs["intent"]["instructions"]["role"]
    assert "within spell range" in qs["target"]["criteria"]["t1"]


def test_no_spell_question_without_mana(mage):
    mage["player"]["mana"] = 2
    for s in mage["magic"]["spells"]:
        s["missing"] = "mana"
    sit = sit_of(mage)
    assert "spell" not in questions.build(sit)
    assert sit.state["you"]["attack_spells_available"] == ["none: not enough mana or reagents"]


def test_fight_casts_the_chosen_spell_at_the_target(mage):
    dec = policy.decide(sit_of(mage), answers(spell="s2"), policy.Memory(), CFG)
    assert [a["verb"] for a in dec.actions] == ["cast"]
    assert dec.actions[0] | {"confidence": 0, "reason": ""} == {"verb": "cast", "spell": "Explosion", "target": 0x100,
                                                               "queue": True, "confidence": 0, "reason": ""}
    assert dec.spell.name == "Explosion" and dec.spell_confidence == 0.8
    assert dec.target.serial == 0x100


def test_new_target_is_engaged_at_spell_range(mage):
    mage["agent"]["engaged"] = 0
    dec = policy.decide(sit_of(mage), answers(), policy.Memory(), CFG)
    assert dec.actions[0]["verb"] == "attack" and dec.actions[0]["range"] == CFG.spell_range
    assert dec.actions[1]["verb"] == "cast"


def test_next_spell_is_queued_while_recovering(mage):
    mage["magic"]["cast_ready_ms"] = 900
    sit = sit_of(mage)
    assert sit.state["you"]["next_spell"] == "in about 1 second"
    dec = policy.decide(sit, answers(), policy.Memory(), CFG)
    assert [(a["verb"], a["queue"]) for a in dec.actions] == [("cast", True)]


def test_no_cast_out_of_spell_range(mage):
    mage["mobiles"][0].update({"distance": 11, "dx": 11})
    dec = policy.decide(sit_of(mage), answers(), policy.Memory(), CFG)
    assert dec.spell is None and [a["verb"] for a in dec.actions] == []


def test_unsure_spell_pick_falls_back_to_strongest_damage(mage):
    dec = policy.decide(sit_of(mage), answers(spell="s5", spell_conf=0.1), policy.Memory(), CFG)
    assert dec.spell.name == "Energy Bolt" and dec.spell_confidence is None


def test_confident_none_holds_fire(mage):
    dec = policy.decide(sit_of(mage), answers(spell="none", spell_conf=0.9), policy.Memory(), CFG)
    assert dec.actions == []


def test_keeping_course_still_casts_at_the_current_target(mage):
    dec = policy.decide(sit_of(mage), answers(conf=0.1), policy.Memory(), CFG)
    assert dec.gated
    assert [(a["verb"], a["target"]) for a in dec.actions] == [("cast", 0x100)]


def test_seek_stops_at_spell_range(mage):
    mage["mobiles"] = [mage["mobiles"][1]]
    mage["agent"]["engaged"] = 0
    dec = policy.decide(sit_of(mage), answers("seek"), policy.Memory(), CFG)
    assert dec.actions[0]["verb"] == "walk_to" and dec.actions[0]["distance"] == CFG.spell_range - 1


def test_seek_is_ruled_out_when_something_is_in_range(mage):
    probs = {"fight": 0.2, "flee": 0.0, "loot": 0.0, "seek": 0.7, "rest": 0.1}
    a = answers()
    a.choices["intent"] = ChoiceResult("seek", probs, 0.7)
    dec = policy.decide(sit_of(mage), a, policy.Memory(), CFG)
    assert dec.intent == "fight" and "seek" in dec.masked


def test_resting_mage_meditates_when_mana_is_low_once_per_delay(mage):
    mage["mobiles"] = []
    mage["player"]["mana"] = 30
    mem = policy.Memory()
    dec = policy.decide(sit_of(mage), answers("rest"), mem, CFG, now=100.0)
    assert dec.actions == [{"verb": "skill", "name": "Meditation", "confidence": 0.9, "reason": "rest"}]
    again = policy.decide(sit_of(mage), answers("rest"), mem, CFG, now=105.0)
    assert again.actions == []
    mage["player"]["buffs"] = ["ActiveMeditation"]
    meditating = policy.decide(sit_of(mage), answers("rest"), mem, CFG, now=200.0)
    assert meditating.actions == []


def test_heuristic_judge_picks_a_spell(mage):
    sit = sit_of(mage)
    ans = asyncio.run(HeuristicJudge().ask(sit.state, questions.build(sit)))
    assert ans.choices["intent"].choice == "fight"
    assert ans.choices["spell"].choice == "s1"
    mage["player"]["mana"] = 20
    sit = sit_of(mage)
    ans = asyncio.run(HeuristicJudge().ask(sit.state, questions.build(sit)))
    assert sit.spell(ans.choices["spell"].choice).name == "Magic Arrow"


def test_signature_changes_when_mana_crosses_a_band(mage):
    full = sit_of(mage).signature()
    mage["player"]["mana"] = 40
    assert sit_of(mage).signature() != full


def test_decision_payload_for_the_panel(mage):
    sit = sit_of(mage)
    qs = questions.build(sit)
    ans = answers(spell="s2")
    ans.latency_ms = 240.0
    dec = policy.decide(sit, ans, policy.Memory(), CFG)

    class J:
        name = "jev/test"

    out = loop.decision_payload(J(), sit, qs, ans, dec, [{"status": "done"}])
    assert list(out["intents"]) == ["fight", "flee", "leave", "loot", "seek", "rest"]
    assert out["target"] == {"serial": 0x100, "name": "an orc", "confidence": 0.9}
    assert out["spell"] == {"name": "Explosion", "confidence": 0.8, "why": "jev"}
    assert out["results"] == ["done"] and out["archetype"] == "mage" and out["danger"] == 0.1


# ---- the player's spell plan ------------------------------------------------

from dataclasses import replace  # noqa: E402

from uo_brain import strategy as strategies  # noqa: E402


def compile_text(text):
    return asyncio.run(strategies.compile_strategy(HeuristicJudge(), text))[0]


def test_strategy_names_an_opener_and_a_main_spell():
    k = compile_text("Open every fight with an explosion spell, then finish the creature off with lightning.")
    assert (k.opening_spell, k.main_spell) == ("Explosion", "Lightning")
    assert "opens with Explosion, then Lightning" in k.describe()
    assert compile_text("Attack relentlessly and never flee.").opening_spell == ""


def test_opener_on_a_fresh_target_then_the_main_spell(mage):
    cfg = strategies.apply(CFG, compile_text("Always open with Lightning. After that use only Magic Arrow."))
    jev_says_explosion = answers(spell="s2", spell_conf=0.9)
    fresh = policy.decide(sit_of(mage), jev_says_explosion, policy.Memory(), cfg)
    assert (fresh.spell.name, fresh.spell_why) == ("Lightning", "strategy")
    hit = policy.decide(sit_of(mage, casts_at={0x100: 1}), jev_says_explosion, policy.Memory(), cfg)
    assert (hit.spell.name, hit.spell_why) == ("Magic Arrow", "strategy")


def test_named_spell_that_cannot_be_cast_falls_back_to_jev(mage):
    cfg = replace(CFG, opening_spell="Flamestrike")  # no reagents for it in the fixture
    dec = policy.decide(sit_of(mage), answers(spell="s2", spell_conf=0.9), policy.Memory(), cfg)
    assert (dec.spell.name, dec.spell_why) == ("Explosion", "jev")


def test_spell_question_says_where_the_fight_stands(mage):
    fresh = questions.build(sit_of(mage))["spell"]["instructions"]["question"]
    assert "no spell has been cast at it yet" in fresh and "open the fight" in fresh
    later = questions.build(sit_of(mage, casts_at={0x100: 2}))["spell"]["instructions"]["question"]
    assert "already cast 2 spells at an orc" in later


def test_protection_first_when_a_creature_is_in_melee_reach(mage):
    mage["mobiles"][0].update({"distance": 1, "dx": 1})
    dec = policy.decide(sit_of(mage), answers(), policy.Memory(), CFG)
    assert [(a["verb"], a["spell"], a["target"]) for a in dec.actions] == [("cast", "Protection", "self")]
    mage["player"]["buffs"] = ["Protection"]
    dec = policy.decide(sit_of(mage), answers(spell="s1"), policy.Memory(), CFG)
    assert [(a["verb"], a["spell"]) for a in dec.actions] == [("cast", "Energy Bolt")]


def test_protection_only_where_it_stops_interruptions(mage):
    from uo_brain import policy, state
    from test_policy import answers
    mage["mobiles"][0].update({"distance": 1, "dx": 1, "dy": 0})
    cfg = policy.PolicyConfig(kite=False)
    aos = policy.decide(state.build(mage, set(), []), answers("fight", target="t1"), policy.Memory(), cfg, now=50.0)
    assert any(a.get("spell") == "Protection" for a in aos.actions)
    mage["era"] = "pre-aos"
    pre = policy.decide(state.build(mage, set(), []), answers("fight", target="t1"), policy.Memory(), cfg, now=50.0)
    assert not any(a.get("spell") == "Protection" for a in pre.actions)


def test_protection_is_not_recast_while_its_icon_is_missing(mage):
    from uo_brain import policy, state
    from test_policy import answers
    mage["mobiles"][0].update({"distance": 1, "dx": 1, "dy": 0})
    cfg, mem = policy.PolicyConfig(kite=False), policy.Memory()
    first = policy.decide(state.build(mage, set(), []), answers("fight", target="t1"), mem, cfg, now=50.0)
    again = policy.decide(state.build(mage, set(), []), answers("fight", target="t1"), mem, cfg, now=55.0)
    assert any(a.get("spell") == "Protection" for a in first.actions)
    assert not any(a.get("spell") == "Protection" for a in again.actions)


def test_a_mage_with_four_close_is_asked_whether_to_leave(mage):
    import copy as _copy
    orc = mage["mobiles"][0]
    mage["mobiles"] = [dict(_copy.deepcopy(orc), serial=0x100 + i, distance=1 + i % 2, dx=1 + i % 2) for i in range(4)]
    sit = sit_of(mage)
    qs = questions.build(sit)
    assert qs["leave_now"]["instructions"]["reason"] == "4 creatures are close to the mage, who is weak in melee."
    a = answers(intent="leave", danger=0.2)
    a.nouls["leave_now"] = 0.5
    assert policy.decide(sit, a, policy.Memory(), CFG, now=10.0).intent == "leave"


def test_a_mage_with_four_on_it_leaves_once_jev_says_it_is_in_danger(mage):
    import copy as _copy
    orc = mage["mobiles"][0]
    mage["mobiles"] = [dict(_copy.deepcopy(orc), serial=0x100 + i, distance=1, dx=1) for i in range(4)]
    sit = sit_of(mage)
    calm = policy.decide(sit, answers(danger=0.3), policy.Memory(), CFG, now=10.0)
    assert calm.intent == "fight"
    assert policy.decide(sit_of(mage), answers(danger=0.55), policy.Memory(), CFG, now=10.0).intent == "leave"
