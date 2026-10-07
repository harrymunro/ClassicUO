import asyncio
import copy

from uo_brain.judge import Answers, HeuristicJudge
from uo_brain.routine import HuntWatch, RoutineConfig, lasts, questions

from conftest import SNAPSHOT, mage_snapshot


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeJev:
    """Answers each Noul from a table (default: carry on), and records what it was asked."""
    name = "jev/fake"

    def __init__(self, **nouls):
        self.nouls = {"head_back": 0.1, "stay_here": 0.9, "move_spot": 0.2} | nouls
        self.asked = []

    async def ask(self, state, qs):
        self.asked.append((state, qs))
        a = Answers(model="fake", input_tokens=900)
        for q in qs:
            a.nouls[q] = self.nouls[q]
        return a


class Broken(FakeJev):
    async def ask(self, state, qs):
        raise TimeoutError("no answer")


def snap(bandages=60, potions=2, weight=120, kills=0, hostiles=True, engaged=0x100, strategy=""):
    s = copy.deepcopy(SNAPSHOT)
    s["player"]["supplies"].update(bandages=bandages, heal_potions=potions)
    s["player"]["weight"] = weight
    s["agent"]["stats"] = {"kills": kills, "deaths": 0, "flees": 0}
    s["agent"]["engaged"] = engaged if hostiles else 0
    s["agent"]["strategy"] = strategy
    if not hostiles:
        s["mobiles"] = [m for m in s["mobiles"] if not m["monster"]]
    return s


def watch(judge, **cfg):
    clock = Clock()
    return HuntWatch("Britain Graveyard", 15, judge, RoutineConfig(**cfg), log=LOG.append, clock=clock), clock


LOG: list = []


def test_a_kill_is_a_moment_to_ask_but_not_inside_the_gap():
    hw, clock = watch(FakeJev())
    assert hw.observe(snap(kills=0)).ask is None       # the first look sets the counts
    clock.t += 12
    look = hw.observe(snap(kills=1))
    assert look.ask == "kill" and look.stop is None
    asyncio.run(hw.ask(snap(kills=1), look.ask))
    clock.t += 3
    assert hw.observe(snap(kills=2)).ask is None        # 3 s after the last question: waits for the gap
    clock.t += 8
    assert hw.observe(snap(kills=2)).ask == "kill"      # remembered, asked once the gap is over


def test_supplies_crossing_a_level_and_the_minute_are_moments():
    hw, clock = watch(FakeJev())
    hw.observe(snap(bandages=60))
    clock.t += 15
    assert hw.observe(snap(bandages=55)).ask is None    # still "plenty"
    assert hw.observe(snap(bandages=48)).ask == "supplies"
    asyncio.run(hw.ask(snap(bandages=48), "supplies"))
    clock.t += 59
    assert hw.observe(snap(bandages=47)).ask is None
    clock.t += 2
    assert hw.observe(snap(bandages=47)).ask == "minute"


def test_one_question_at_a_time():
    hw, clock = watch(FakeJev())
    hw.observe(snap(kills=0))
    clock.t += 12
    assert hw.observe(snap(kills=1)).ask == "kill"
    clock.t += 15
    assert hw.observe(snap(kills=2)).ask is None        # the first is still out


def test_head_back_waits_for_the_fight_at_hand():
    jev = FakeJev(head_back=0.9)
    hw, clock = watch(jev)
    hw.observe(snap(kills=0, bandages=30))
    clock.t += 12
    v = asyncio.run(hw.ask(snap(kills=1, bandages=12), "kill"))
    assert v.kind == "head_back" and v.reason.startswith("Jev: time to head back (0.90): 12 bandages")
    assert hw.observe(snap(kills=1, bandages=12)).stop is None   # an orc is adjacent
    assert hw.observe(snap(kills=2, bandages=12, hostiles=False)).stop == v.reason
    clock.t += 31
    assert hw.observe(snap(kills=2, bandages=12)).stop == v.reason  # or 30 s, fight or not


def test_not_worth_staying_ends_the_hunt():
    hw, clock = watch(FakeJev(stay_here=0.2))
    hw.observe(snap(hostiles=False))
    clock.t += 60
    v = asyncio.run(hw.ask(snap(hostiles=False), "minute"))
    assert v.kind == "leave_spot" and "no kill in 1.0 min" in v.reason
    assert hw.observe(snap(hostiles=False)).stop == v.reason


def test_unsure_twice_running_goes_to_the_planner():
    hw, clock = watch(FakeJev(head_back=0.5))
    hw.observe(snap())
    assert asyncio.run(hw.ask(snap(), "minute")).kind == "unsure"
    assert hw.observe(snap(hostiles=False)).stop is None
    v = asyncio.run(hw.ask(snap(), "minute"))
    assert v.kind == "handover" and "head back 0.50" in v.reason and "planner" in v.reason
    assert hw.observe(snap(hostiles=False)).stop == v.reason


def test_a_confident_answer_resets_unsure():
    jev = FakeJev(head_back=0.5)
    hw, _ = watch(jev)
    hw.observe(snap())
    assert asyncio.run(hw.ask(snap(), "minute")).kind == "unsure"
    jev.nouls["head_back"] = 0.1
    assert asyncio.run(hw.ask(snap(), "minute")).kind == "stay"
    jev.nouls["head_back"] = 0.5
    assert asyncio.run(hw.ask(snap(), "minute")).kind == "unsure"


def test_quiet_asks_about_moving_and_patrols_on_yes():
    jev = FakeJev(move_spot=0.8)
    hw, clock = watch(jev)
    hw.observe(snap(hostiles=False))
    clock.t += 21
    look = hw.observe(snap(hostiles=False))
    assert look.ask == "quiet"
    v = asyncio.run(hw.ask(snap(hostiles=False), "quiet"))
    assert "move_spot" in jev.asked[-1][1] and v.kind == "patrol"
    assert hw.observe(snap(hostiles=False)).patrol
    assert not hw.observe(snap(hostiles=False)).patrol  # once per yes
    # A confident move beats doubts about the spot: no step towards handing over.
    jev.nouls["stay_here"] = 0.5
    assert asyncio.run(hw.ask(snap(hostiles=False), "quiet")).kind == "patrol" and hw.unsure == 0
    jev.nouls["stay_here"] = 0.9
    jev.nouls["head_back"] = 0.5   # doubts about supplies aren't walked away from
    assert asyncio.run(hw.ask(snap(hostiles=False), "quiet")).kind == "unsure"
    jev.nouls["head_back"] = 0.1
    # Not quiet: the question isn't asked at all.
    asyncio.run(hw.ask(snap(), "minute"))
    assert "move_spot" not in jev.asked[-1][1]


def test_floors_need_no_question():
    jev = FakeJev()
    hw, clock = watch(jev)
    assert hw.observe(snap(bandages=0, potions=0)).stop == "out of bandages and heal potions"
    assert hw.observe(snap(weight=395)).stop == "the bag is full"
    m = mage_snapshot()
    for sp in m["magic"]["spells"]:
        sp["missing"] = "reagents" if sp["target"] == "harmful" else ""
    assert hw.observe(m).stop == "out of reagents for every attack spell"
    hw.observe(snap(hostiles=False))
    clock.t += 601
    assert hw.observe(snap(hostiles=False)).stop == "nothing to fight for 10 minutes"
    assert not jev.asked


def test_with_jev_the_old_thresholds_are_its_call():
    hw, _ = watch(FakeJev())
    assert hw.observe(snap(bandages=5, weight=350)).stop is None  # 5 bandages, 88% full: ask, don't stop


def test_without_jev_the_old_thresholds_apply():
    hw, clock = watch(HeuristicJudge())
    assert hw.observe(snap(bandages=5)).stop == "low on bandages"
    assert hw.observe(snap(weight=350)).stop == "bag is heavy"
    hw.observe(snap(hostiles=False))
    clock.t += 21
    assert hw.observe(snap(hostiles=False)).patrol
    clock.t += 220
    assert hw.observe(snap(hostiles=False)).stop == "nothing to fight for 4 minutes"
    assert hw.summary()["judged_by"] == "rules"


def test_two_failed_questions_fall_back_to_the_rules():
    hw, _ = watch(Broken())
    hw.observe(snap(bandages=5))
    assert asyncio.run(hw.ask(snap(bandages=5), "minute")) is None
    assert hw.observe(snap(bandages=5)).stop is None
    asyncio.run(hw.ask(snap(bandages=5), "minute"))
    assert hw.observe(snap(bandages=5)).stop == "low on bandages"
    assert "error" in LOG[-1]


def test_the_hunt_in_words():
    hw, clock = watch(FakeJev())
    hw.observe(snap(bandages=60, kills=2))
    clock.t += 300
    w = hw.words(snap(bandages=40, kills=7, weight=300))
    assert w["you"]["supplies"] == "40 bandages and 2 heal potions left"
    assert w["you"]["supplies_last"] == "about 4.0 bandages a kill so far: enough for about 10 more kills"
    assert w["you"]["bag"] == "getting heavy (75% of what the character can carry)"
    assert w["this_hunt"]["kills"] == 5 and w["this_hunt"]["minutes_so_far"] == 5.0
    assert w["this_hunt"]["minutes_left_of_the_planned_hunt"] == 10.0
    assert w["around_now"] == "2 hostile creatures in sight, 1 close"
    assert lasts(10, 0, 0, "bandages") == "no kills yet on this hunt"


def test_a_mage_in_words():
    m = mage_snapshot()
    hw = HuntWatch("Britain Graveyard", 10, FakeJev(), clock=Clock())
    w = hw.words(m)
    assert w["you"]["character"] == "mage" and w["you"]["reagents"] == "running low: nightshade (3 left)"
    assert "Flamestrike" not in w["you"]["attack_spells_with_reagents"]
    assert "bandages" not in str(w["you"])


def test_the_strategy_goes_into_every_question():
    qs = questions({}, "warrior", strategy="Stay out until the bandages are all gone.", quiet=True)
    assert set(qs) == {"head_back", "stay_here", "move_spot"}
    assert all(q["instructions"]["player_strategy"] == "Stay out until the bandages are all gone." for q in qs.values())
    assert "player_strategy" not in questions({}, "warrior")["head_back"]["instructions"]


def test_each_question_is_logged_and_costed():
    LOG.clear()
    hw, clock = watch(FakeJev())
    hw.observe(snap())
    asyncio.run(hw.ask(snap(strategy="careful"), "minute"))
    rec = LOG[-1]
    assert rec["type"] == "routine" and rec["moment"] == "minute" and rec["verdict"] == "stay"
    assert rec["answers"]["nouls"]["head_back"] == 0.1 and rec["state"]["this_hunt"]["area"] == "Britain Graveyard"
    clock.t += 3600
    s = hw.summary()
    assert s["questions"] == 1 and s["input_tokens"] == 900
    assert s["cost_usd"] == round(900 / 1e6 * 0.042, 5) and s["cost_usd_per_hour"] == round(900 / 1e6 * 0.042, 4)


def archer(arrows):
    s = snap(bandages=60)
    s["player"]["ranged"] = {"kind": "bow", "ammo": "arrows", "range": 10}
    s["player"]["supplies"]["arrows"] = arrows
    return s


def test_an_archers_arrows_are_a_supply():
    jev = FakeJev()
    hw, _ = watch(jev)
    assert hw.observe(archer(0)).stop == "out of arrows"
    hw, clock = watch(jev)
    hw.observe(archer(120))
    clock.t += 15
    assert hw.observe(archer(90)).ask == "supplies"   # plenty -> some
    w = hw.words(archer(90))
    assert w["you"]["character"] == "archer" and w["you"]["ammo"] == "90 arrows left"
    assert "arrows or bolts" in questions(w, "archer")["head_back"]["criteria"]["true"]
    rules, _ = watch(HeuristicJudge())
    assert rules.observe(archer(15)).stop == "low on arrows"


def test_walking_round_an_empty_spawn_hands_over_after_four_tries():
    jev = FakeJev(move_spot=0.75, stay_here=0.5)
    hw, clock = watch(jev)
    hw.observe(snap(hostiles=False))
    kinds = [asyncio.run(hw.ask(snap(hostiles=False), "quiet")).kind for _ in range(5)]
    assert kinds == ["patrol"] * 4 + ["handover"]
    # A fight in between starts the count again.
    asyncio.run(hw.ask(snap(), "kill"))
    assert asyncio.run(hw.ask(snap(hostiles=False), "quiet")).kind == "patrol" and hw.patrols == 1
