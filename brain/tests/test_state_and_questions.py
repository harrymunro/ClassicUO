from uo_brain import questions, state


def build(snapshot):
    return state.build(snapshot, set(), state.journal_events(snapshot["journal"]))


def test_hostiles_are_monsters_only_with_words_not_numbers(snapshot):
    sit = build(snapshot)
    assert [h.serial for h in sit.hostiles] == [0x100, 0x101]
    orc = sit.state["hostile_creatures"][0]
    assert orc == {"id": "t1", "name": "an orc", "health": "badly wounded", "distance": "adjacent, within weapon reach",
                   "direction": "east", "your_current_target": True, "aggressive": True}
    assert sit.state["you"]["fighting"] == "an orc"
    assert sit.state["you"]["health"] == "wounded (60%)"


def test_other_players_speech_is_kept_out_of_model_state(snapshot):
    sit = build(snapshot)
    assert "You begin applying the bandages." in sit.state["recent_events"]
    assert not any("give me gold" in e for e in sit.state["recent_events"])


def test_auto_loot_items_are_not_asked_about(snapshot):
    sit = build(snapshot)
    assert [i.serial for i in sit.items] == [0x40000301]
    assert sit.items[0].info["properties"] == "weight: 7 stones\nexceptional"


def test_looted_corpse_is_not_a_loot_target_but_its_items_still_are(snapshot):
    sit = state.build(snapshot, {0x40000200}, [])
    assert sit.corpses == []
    assert [i.serial for i in sit.items] == [0x40000301]


def test_taken_or_declined_items_are_not_asked_again(snapshot):
    sit = state.build(snapshot, set(), [], skip_items={0x40000301})
    assert sit.items == []


def test_questions_fan_out_with_none_options(snapshot):
    snapshot["mobiles"][0]["distance"] = 5  # nothing close: looting questions are asked too
    qs = questions.build(build(snapshot))
    assert set(qs) == {"intent", "in_danger", "target", "corpse", "take_i1"}
    assert set(qs["target"]["criteria"]) == {"t1", "t2", "none"}
    assert set(qs["intent"]["criteria"]) == {"fight", "flee", "loot", "seek", "rest"}
    assert "already fighting" in qs["target"]["criteria"]["t1"]


def test_no_optional_questions_when_alone(snapshot):
    snapshot["mobiles"] = []
    snapshot["corpses"] = []
    qs = questions.build(build(snapshot))
    assert set(qs) == {"intent", "in_danger"}


def test_signature_changes_with_new_hostile(snapshot):
    a = build(snapshot).signature()
    snapshot["mobiles"][1]["serial"] = 0x999
    assert build(snapshot).signature() != a


def test_named_monsters_get_their_kind(snapshot):
    snapshot["mobiles"][0]["name"] = "Vorgak"
    sit = build(snapshot)
    assert sit.state["hostile_creatures"][0]["name"] == "Vorgak (an orc)"


def test_no_looting_questions_mid_fight(snapshot):
    qs = questions.build(build(snapshot))
    assert set(qs) == {"intent", "in_danger", "target"}
