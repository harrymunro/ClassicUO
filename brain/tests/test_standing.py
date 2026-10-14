"""A companion's settings, changed by the leader's word (cuo-cym.18)."""

import copy

from uo_brain import judge, policy, standing, state, strategy

from conftest import SNAPSHOT
from test_party import LICH, monster
from test_policy import answers


def picks(**by_setting):
    """Answers to the settings' own questions: name -> (value, how sure)."""
    return judge.Answers(choices={name: judge.ChoiceResult(value, {value: p, standing.UNCHANGED: round(1 - p, 3)}, p)
                                  for name, (value, p) in by_setting.items()}, model="test")


def test_the_first_question_has_the_settings_as_its_options_and_spells_only_for_one_who_casts():
    q = standing.which_question("Wystan", "Roland")
    assert q["type"] == "choice" and set(q["criteria"]) == set(standing.SETTINGS) - {"opening_spell", "main_spell"} | {"none"}
    assert "Wystan" in q["criteria"]["looting"] and "'go ahead and loot'" in q["criteria"]["looting"]
    assert {"opening_spell", "main_spell"} <= set(standing.which_question("Isolde", "Roland", caster=True)["criteria"])
    # A line speaks to every setting given a quarter or more: two share the probability between them.
    s = standing.Standing()
    assert s.spoken_to({"looting": 0.55, "fleeing": 0.4, "talk": 0.03, "none": 0.02}) == ["looting", "fleeing"]
    assert s.spoken_to({"none": 0.9, "looting": 0.1}) == []
    assert s.spoken_to({"opening_spell": 0.9}) == [] and standing.Standing(caster=True).spoken_to({"opening_spell": 0.9})
    # And only those are then asked about, each with a way to say the line leaves it alone.
    qs = s.questions("Wystan", "Roland", only=["looting", "fleeing"])
    assert set(qs) == {"looting", "fleeing"}
    assert set(qs["looting"]["criteria"]) == {"valuables", "everything", "nothing", standing.UNCHANGED}
    assert "Roland" in qs["looting"]["criteria"]["nothing"]


def test_a_setting_changes_at_the_cut_and_holds():
    s = standing.Standing()
    assert s.now("looting") == "nothing" and s.values == {}
    assert s.take(picks(looting=("valuables", 0.55))) == [] and s.now("looting") == "nothing"  # under the cut
    taken = s.take(picks(looting=("valuables", 0.9), fleeing=("never", 0.97), talk=(standing.UNCHANGED, 0.99)))
    assert [(c["setting"], c["was"], c["now"]) for c in taken] == [("looting", "nothing", "valuables"), ("fleeing", "allowed", "never")]
    assert s.values == {"looting": "valuables", "fleeing": "never"}
    # Told what it already does, with nothing else changing, it is still in the list: the leader hears that it does.
    again = s.take(picks(looting=("valuables", 0.9)))
    assert [(c["setting"], c["was"], c["now"]) for c in again] == [("looting", "valuables", "valuables")]
    # ...and with something else changing, only the change is.
    both = s.take(picks(looting=("valuables", 0.9), fleeing=("allowed", 0.9)))
    assert [c["setting"] for c in both] == ["fleeing"]
    assert s.to_log()["settings_changed"] == [{"setting": "looting", "was": "nothing", "now": "valuables"},
                                              {"setting": "fleeing", "was": "allowed", "now": "never"},
                                              {"setting": "fleeing", "was": "never", "now": "allowed"}]


def test_what_the_leader_has_spoken_of_is_laid_over_the_fights_settings_and_nothing_else_is():
    base = strategy.apply(policy.PolicyConfig(escort="Roland", looting="nothing"), strategy.Knobs(allow_flee=False))
    assert base.looting == "nothing" and base.take_item == 2.0 and not base.allow_flee
    s = standing.Standing()
    assert s.apply(base) is base  # nothing said: the configuration as it is
    s.take(picks(looting=("valuables", 0.9)))
    cfg = s.apply(base)
    assert cfg.looting == "valuables" and cfg.take_item == policy.PolicyConfig().take_item
    assert not cfg.allow_flee and cfg.escort == "Roland"  # its strategy's "never flee" stands: the leader said nothing of it
    s.take(picks(looting=("everything", 0.9), fleeing=("allowed", 0.9), aggression=("cautious", 0.9),
                 target=("weakest_first", 0.9), helping=("off", 0.9), reach=("wide", 0.9)))
    cfg = s.apply(base)
    assert cfg.take_item == 0.0 and cfg.allow_flee and cfg.target_priority == "weakest_first" and not cfg.help_mates
    assert cfg.flee_danger == 0.3 + 0.65 * 0.2 and (cfg.escort_tiles, cfg.escort_own) == (12, 12)
    # Raising needs helping: one told to raise the fallen and not to heal raises nobody (as a fleet's switches go).
    s.take(picks(raising=("on", 0.9)))
    assert not s.apply(base).raise_fallen
    s.take(picks(helping=("on", 0.9)))
    assert s.apply(base).raise_fallen
    # A caster's spells, and its own choice again.
    c = standing.Standing(caster=True)
    c.take(picks(opening_spell=("Explosion", 0.9), main_spell=("Lightning", 0.9)))
    assert (c.apply(base).opening_spell, c.apply(base).main_spell) == ("Explosion", "Lightning")
    c.take(picks(opening_spell=("own", 0.9)))
    assert c.apply(base).opening_spell == ""


def test_what_it_says_of_a_change_says_what_it_now_does():
    s = standing.Standing()
    owed, plain = s.told(s.take(picks(looting=("valuables", 0.9), fleeing=("never", 0.9))), "Wystan", "Roland")
    assert "takes what is worth having from the corpses; never runs from a fight" in owed and "has made the change" in owed
    assert plain == "I'll loot what's worth taking. No running. Understood."
    owed, plain = s.told(s.take(picks(looting=("valuables", 0.9))), "Wystan", "Roland")
    assert "already does" in owed and plain == "I'll loot what's worth taking."
    assert s.told([], "Wystan", "Roland") == ("", "")
    assert s.words("distance", "Wystan", "Roland", "close") == "keeps right at Roland's side"


def test_how_far_a_companion_goes_for_a_fight_is_a_setting():
    """Only what attacks the party, or anything in sight: the escort's two distances (policy.decide)."""
    def beside(creature):
        snap = copy.deepcopy(SNAPSHOT)
        snap["player"].update(name="Isolde", x=1000, y=1000, hits=95, hits_max=95)
        leader = {"serial": 0x700, "name": "Roland", "body": 400, "notoriety": "innocent", "human": True, "monster": False,
                  "pet": False, "hits_pct": 100, "dead": False, "distance": 2, "dx": 2, "dy": 0, "dir": "west"}
        snap["mobiles"] = [leader, creature]
        snap["journal"], snap["journal_seq"] = [], 1
        snap["agent"].update(engaged=0, fleeing=False, bandaging=False)
        return state.build(snap, set(), [], bestiary=LICH, companions={"Roland": "companion"})

    far = {**monster(0x311, "a skeleton", 50, 10), "dx": -10, "dy": 0}  # in war mode, ten tiles from both
    cfg = policy.PolicyConfig(escort="Roland", looting="nothing")
    assert policy.decide(beside(far), answers("seek", target="t1"), policy.Memory(), cfg).intent == "rest"
    s = standing.Standing()
    s.take(picks(reach=("wide", 0.9)))
    assert policy.decide(beside(far), answers("seek", target="t1"), policy.Memory(), s.apply(cfg)).intent == "seek"
    # Told to fight only what comes at them, one in war mode five tiles off is left alone too.
    near = {**monster(0x312, "a skeleton", 50, 5), "dx": -5, "dy": 0}
    assert policy.decide(beside(near), answers("seek", target="t1"), policy.Memory(), cfg).intent == "seek"
    s.take(picks(reach=("defend", 0.9)))
    assert policy.decide(beside(near), answers("seek", target="t1"), policy.Memory(), s.apply(cfg)).intent == "rest"
