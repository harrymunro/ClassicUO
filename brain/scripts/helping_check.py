"""Helping a hurt companion: the thresholds against a system-one question, without the game (cuo-cym.26).

policy.help_mates decides by thresholds whether a party member heals or bandages a companion (under 60%, within 2
tiles for a bandage, never a walk over while anything hostile is within 8 tiles, nobody helped by one under 50%,
one being healed by another left to them above 30%). helping.help_question puts the same moment to system one
instead: whom to help, by the one way the character has to each (code's floors: helping.ways), or nobody. This puts a
set of fight moments to the rule and to the models and counts how often each does what is wanted.

  uv run python scripts/helping_check.py --out bench/2026-10-14-helping.json
  uv run python scripts/helping_check.py --sets second --out bench/2026-10-14-helping-held-back.json

A moment is the helper (who, its health and how fast it fell, mana or bandages, what it is fighting), its companions
(health, the points lost over the last few seconds, where they stand) and the creatures (where, and whom each is
on: a companion, "me", "" loose in war mode, "-" not in war mode), with positions in tiles east and south of the
helper. The first set is two parts: 22 decisions drawn at random (seed 26) from the party runs of 2026-10-13 00:08
to 04:59 (brain/logs/fleet/*-party-lich) among those where the helper had someone it could help, rebuilt from the
member's decision log, the others' own reports of their health and where.jsonl; and 20 written, mostly from the
cases in cuo-4ti's history (three ranged members walking into the melee to the warrior, a mage at low mana, two
healers on one companion, a warrior leaving the line). The wording was settled on the first set in three runs
(bench/2026-10-14-helping-first-wording.json, -second-wording.json, then 2026-10-14-helping.json). The second set
was written after that, before any model saw it, and run once: 14 drawn the same way (seed 2614) from the runs of
2026-10-13 05:00 to 09:59 and of 2026-10-14, and 14 written.

What is wanted, written before any model saw the moment, as a player would: help a companion near death or losing
health fast (some 15 points or more in a few seconds) that the helper can reach; a bandage within reach costs little,
a Greater Heal costs a mage mana, so a mage short of it keeps it for the one losing health; a companion hurt but
steady, or lightly hurt, is seeing to itself; one another is healing is in their hands unless it is near death or
still falling fast; a walk over during a fight only away from the creatures, or for a warrior to a companion near
death; a helper itself hurt and falling with creatures on it sees to itself, unless the companion is near death
beside it. Some moments take either answer (want lists both).
"""

import argparse
import asyncio
import json
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any

BRAIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRAIN / "src"))

from uo_brain import costs, helping, policy  # noqa: E402
from uo_brain import judge as judges  # noqa: E402
from uo_brain.spells import GREATER_HEAL  # noqa: E402
from uo_brain.state import Candidate, Mate, Situation  # noqa: E402

PARTY = {"Roland": ("warrior", {"Healing": 80.0, "Anatomy": 80.0, "Swords": 80.0}),
         "Isolde": ("mage", {"Magery": 90.0, "Evaluating Intelligence": 80.0}),
         "Wystan": ("archer", {"Healing": 80.0, "Anatomy": 80.0, "Archery": 80.0}),
         "Thea": ("tamer", {"Healing": 70.0, "Anatomy": 70.0, "Veterinary": 90.0}),
         "Corwin": ("bard", {"Healing": 60.0, "Anatomy": 60.0, "Provocation": 90.0})}
PEACEFUL = ("a crossbill", "a sparrow")


def M(mid: str, src: str, me: str, *, hp: int = 100, drop: int = 0, mana: int | None = None, band: int = 100,
      bandaging: bool = False, doing: str = "", pet_on: str = "", intent: str = "fight", helped: tuple = (),
      mates: dict[str, tuple], creatures: list[tuple], want: tuple, why: str) -> dict[str, Any]:
    return {"id": mid, "src": src, "me": me, "hp": hp, "drop": drop, "mana": mana, "band": band,
            "bandaging": bandaging, "doing": doing, "pet_on": pet_on, "intent": intent, "helped": list(helped),
            "mates": mates, "creatures": creatures, "want": list(want), "why": why}


N = None  # nobody: keep on with what it is doing

# ---------------------------------------------------------------- the first set
# Drawn at random from the party runs (seed 26), rebuilt from the logs.
RANDOM_FIRST = [
    M("r01", "20261013-000827 Isolde 1791860998", "Isolde", hp=89, mana=14, doing='k1',
      mates={'Corwin': (15, 65, (-4, -3)), 'Thea': (100, 0, (-5, -6)), 'Wystan': (100, 0, (-6, -6))},
      creatures=[('k1', 'a bone knight', (-5, -3), 'Corwin', 10)],
      want=("Corwin",), why="the bard near death and falling fast: the last heal's mana is for this"),
    M("r02", "20261013-000827 Thea 1791860972", "Thea", pet_on='k2', helped=('Roland',),
      mates={'Wystan': (91, 9, (4, -7)), 'Isolde': (81, 0, (7, 0))},
      creatures=[('k1', 'a skeleton', (3, -3), '', 100), ('k2', 'a bone knight', (-5, -5), '', 100),
                 ('k3', 'a skeleton', (5, 5), '', 100), ('k4', 'a skeleton', (10, 10), '', 100),
                 ('k5', 'a bone knight', (10, 10), '', 100)],
      want=(N,), why="nobody badly hurt, all far off"),
    M("r03", "20261013-004011 Corwin 1791862903", "Corwin", band=97, helped=('Isolde',),
      mates={'Roland': (94, 0, (0, -2)), 'Thea': (34, 25, (2, 2)), 'Wystan': (100, 0, (-3, -4)), 'Isolde': (47, 5, (5, -5))},
      creatures=[('k1', 'a bone knight', (3, 2), 'Thea', 60), ('k2', 'a skeleton', (0, -3), '', 100),
                 ('k3', 'a skeleton', (3, 2), 'Thea', 35), ('k4', 'a skeleton', (5, -5), '', 100)],
      want=("Thea",), why="the tamer badly hurt and falling, two on her, within reach"),
    M("r04", "20261013-004011 Wystan 1791862927", "Wystan", doing='k1',
      mates={'Corwin': (100, 0, (3, 4)), 'Roland': (88, 7, (7, -1)), 'Isolde': (71, 0, (8, 1)), 'Thea': (99, 0, (4, 10))},
      creatures=[('k1', 'a bone knight', (10, 10), '', 10)],
      want=(N,), why="the mage hurt but steady, 8 tiles off"),
    M("r05", "20261013-005033 Thea 1791863501", "Thea", band=99, pet_on='k1',
      mates={'Roland': (91, 1, (5, 3)), 'Wystan': (100, 0, (3, 4)), 'Corwin': (100, 0, (7, 6)), 'Isolde': (100, 0, (7, 5))},
      creatures=[('k1', 'a bone knight', (-3, -3), '', 35), ('k2', 'a skeleton', (6, 3), 'Roland', 100),
                 ('k3', 'a skeleton', (0, -5), '', 100), ('k4', 'a lich', (0, -10), '-', 100),
                 ('k5', 'a bone knight', (-10, -10), '', 100)],
      want=(N,), why="the warrior lightly hurt"),
    M("r06", "20261013-012010 Thea 1791865272", "Thea", pet_on='k2',
      mates={'Wystan': (100, 0, (4, 0)), 'Isolde': (100, 0, (4, 1)), 'Corwin': (88, 4, (8, -4)), 'Roland': (86, 14, (4, -7))},
      creatures=[('k1', 'a skeleton', (5, -7), 'Roland', 100), ('k2', 'a lich', (5, -7), 'Roland', 85),
                 ('k3', 'a skeleton', (9, -4), 'Corwin', 100), ('k4', 'a bone knight', (0, -5), '', 100),
                 ('k5', 'a skeleton', (10, -10), '', 100)],
      want=(N,), why="the warrior lightly hurt under a lich, 7 tiles off: no walk into it"),
    M("r07", "20261013-012010 Thea 1791865288", "Thea", pet_on='k1', helped=('Corwin',),
      mates={'Wystan': (69, 31, (2, -1)), 'Roland': (93, 7, (0, -4)), 'Isolde': (100, 0, (4, 1)), 'Corwin': (54, 2, (8, 3))},
      creatures=[('k1', 'a bone knight', (3, -1), 'Wystan', 85), ('k2', 'a skeleton', (3, -3), '', 10),
                 ('k3', 'a skeleton', (5, -5), '', 100), ('k4', 'a bone knight', (5, -5), '', 100),
                 ('k5', 'a skeleton', (10, -10), '', 10)],
      want=("Wystan",), why="the archer losing health fast to a bone knight, within reach; the bard is being healed"),
    M("r08", "20261013-014119 Corwin 1791866550", "Corwin", helped=('Wystan',),
      mates={'Thea': (100, 0, (-1, 2)), 'Isolde': (100, 0, (-2, -3)), 'Roland': (56, 37, (0, -4)), 'Wystan': (37, 0, (5, 9))},
      creatures=[('k1', 'a skeleton', (-3, -3), 'Isolde', 100), ('k2', 'a skeleton', (1, -4), 'Roland', 100),
                 ('k3', 'a lich', (-3, -3), 'Isolde', 35), ('k4', 'a bone knight', (-5, -5), '', 100)],
      want=(N,), why="the warrior 4 tiles off with a skeleton on him: the bard doesn't walk into the fight"),
    M("r09", "20261013-014119 Corwin 1791866568", "Corwin", band=99, helped=('Isolde',),
      mates={'Thea': (100, 0, (-1, 2)), 'Wystan': (100, 0, (-3, 0)), 'Isolde': (54, 22, (-2, -3)), 'Roland': (100, 0, (-2, -4))},
      creatures=[('k1', 'a bone knight', (-3, -3), 'Isolde', 35)],
      want=(N,), why="the mage is being healed, not near death; a walk would put the bard by the bone knight"),
    M("r10", "20261013-021901 Isolde 1791869013", "Isolde", mana=100, intent='rest',
      mates={'Thea': (100, 0, (-4, 1)), 'Corwin': (100, 0, (-5, 1)), 'Wystan': (100, 0, (-5, 2)), 'Roland': (63, 0, (-8, 2))},
      creatures=[],
      want=(N, "Roland"), why="the fight over, full mana: a heal costs nothing, and he heals himself too"),
    M("r11", "20261013-023159 Roland 1791869596", "Roland", hp=86, band=149, helped=('Thea',), doing='k3',
      mates={'Corwin': (100, 0, (2, -2)), 'Thea': (45, 39, (2, -1)), 'Isolde': (100, 0, (6, 3)), 'Wystan': (95, 5, (10, 2))},
      creatures=[('k1', 'a skeleton', (3, -1), 'Thea', 100), ('k2', 'a skeleton', (3, -1), 'Thea', 100),
                 ('k3', 'a skeleton', (0, -5), '', 60), ('k4', 'a bone knight', (5, 5), '', 100),
                 ('k5', 'a skeleton', (11, 2), 'Wystan', 35), ('k6', 'a bone knight', (-10, 0), '', 100)],
      want=("Thea",), why="the tamer falling fast with two on her, within reach, though another heals her"),
    M("r12", "20261013-023159 Wystan 1791869619", "Wystan", helped=('Roland',), doing='k2',
      mates={'Corwin': (100, 0, (-1, -2)), 'Thea': (69, 0, (-4, -4)), 'Roland': (59, 41, (-3, -4)), 'Isolde': (100, 0, (4, 1))},
      creatures=[('k1', 'a bone knight', (-4, -4), 'Roland', 85), ('k2', 'a bone knight', (-4, -4), 'Roland', 35)],
      want=(N,), why="the warrior is being healed; a walk would be into two bone knights"),
    M("r13", "20261013-024221 Isolde 1791870220", "Isolde", mana=53, helped=('Roland',), doing='k1',
      mates={'Corwin': (100, 0, (0, 0)), 'Thea': (64, 30, (-2, 0)), 'Roland': (41, 39, (-4, 1)), 'Wystan': (87, 7, (-5, 3))},
      creatures=[('k1', 'a bone knight', (-5, 1), 'Roland', 85)],
      want=("Roland",), why="the warrior still falling fast though another heals him; mana enough"),
    M("r14", "20261013-030943 Thea 1791871901", "Thea", band=91, pet_on='k1',
      mates={'Isolde': (85, 0, (1, 7)), 'Corwin': (100, 0, (0, 7)), 'Roland': (96, 0, (3, 11))},
      creatures=[('k1', 'a bone knight', (3, 3), '', 85)],
      want=(N,), why="nobody badly hurt"),
    M("r15", "20261013-031935 Thea 1791872473", "Thea", hp=99, band=97, pet_on='k1',
      mates={'Roland': (64, 3, (-2, -2)), 'Corwin': (100, 0, (-4, -3)), 'Wystan': (99, 0, (-5, -3)), 'Isolde': (100, 0, (-6, -8))},
      creatures=[('k1', 'a bone knight', (-3, -2), 'Roland', 35)],
      want=(N, "Roland"), why="the warrior hurt but steady beside her: a bandage costs her nothing, nor is it needed"),
    M("r16", "20261013-033028 Thea 1791873124", "Thea", band=98, pet_on='k2',
      mates={'Corwin': (100, 0, (1, 1)), 'Roland': (67, 29, (-5, -4)), 'Isolde': (100, 0, (2, -6)), 'Wystan': (83, 10, (-7, -3))},
      creatures=[('k1', 'a skeleton', (-3, -3), '', 60), ('k2', 'a bone knight', (-6, -4), 'Roland', 35),
                 ('k3', 'a skeleton', (-6, -4), 'Roland', 35)],
      want=(N,), why="the warrior 5 tiles off with two on him: the tamer doesn't walk into it"),
    M("r17", "20261013-035112 Corwin 1791874581", "Corwin",
      mates={'Thea': (100, 0, (0, 0)), 'Wystan': (100, 0, (0, 0)), 'Isolde': (100, 0, (-4, -4)), 'Roland': (59, 15, (4, 1))},
      creatures=[('k1', 'a bone knight', (5, 1), 'Roland', 35), ('k2', 'a bone knight', (5, 1), 'Roland', 100)],
      want=(N,), why="the warrior 4 tiles off with two bone knights on him: no walk into them"),
    M("r18", "20261013-040205 Isolde 1791875090", "Isolde", hp=31, mana=97, intent='rest',
      mates={'Roland': (27, 0, (0, 0))},
      creatures=[('k1', 'a crossbill', (-10, -10), '-', 100)],
      want=(N, "Roland"), why="the fight over, both badly hurt: herself first or him, either"),
    M("r19", "20261013-042329 Corwin 1791876292", "Corwin", band=98,
      mates={'Thea': (85, 0, (-1, -2)), 'Roland': (100, 0, (0, 7))},
      creatures=[('k1', 'a bone knight', (-2, -2), 'Thea', 85), ('k2', 'a bone knight', (-2, -2), 'Thea', 100),
                 ('k3', 'a skeleton', (10, 10), '', 100)],
      want=(N,), why="the tamer lightly hurt and steady"),
    M("r20", "20261013-043351 Roland 1791876960", "Roland", band=145, intent='rest',
      mates={'Corwin': (100, 0, (2, 2)), 'Thea': (100, 0, (2, 1)), 'Isolde': (91, 0, (3, 4)), 'Wystan': (100, 0, (8, 9))},
      creatures=[],
      want=(N,), why="nobody hurt to speak of"),
    M("r21", "20261013-044344 Wystan 1791877496", "Wystan", doing='k1',
      mates={'Thea': (92, 0, (4, 2)), 'Roland': (87, 0, (4, 0)), 'Isolde': (100, 0, (6, -1)), 'Corwin': (100, 0, (8, 3))},
      creatures=[('k1', 'a bone knight', (0, -3), '', 60), ('k2', 'a skeleton', (5, 0), 'Roland', 35)],
      want=(N,), why="nobody badly hurt"),
    M("r22", "20261013-045336 Roland 1791878084", "Roland", hp=88, drop=5, band=148, doing='k2',
      mates={'Wystan': (91, 9, (-1, 1)), 'Isolde': (100, 0, (6, 10)), 'Thea': (46, 22, (-10, -8))},
      creatures=[('k1', 'a skeleton', (0, 5), '', 60), ('k2', 'a skeleton', (-2, 1), 'Wystan', 10),
                 ('k3', 'a skeleton', (5, 5), '', 60), ('k4', 'a skeleton', (3, 3), '', 60),
                 ('k5', 'a bone knight', (3, 3), '', 85), ('k6', 'an unknown creature', (-10, -10), '', 100)],
      want=(N,), why="the archer lightly hurt; the tamer out of reach"),
]

# Written: the cases of cuo-4ti's history and others the rule can't weigh.
WRITTEN_FIRST = [
    M("w01", "the first party run's walk into the melee (cuo-4ti)", "Wystan", doing='lich',
      mates={'Roland': (28, 42, (0, -5)), 'Isolde': (100, 0, (-3, 0)), 'Thea': (100, 0, (2, 1)), 'Corwin': (100, 0, (1, -1))},
      creatures=[('lich', 'a lich', (1, -6), 'Roland', 70), ('bk1', 'a bone knight', (-1, -5), 'Roland', 100),
                 ('bk2', 'a bone knight', (0, -6), 'Roland', 100)],
      want=(N,), why="the warrior near death under a lich and two bone knights: the archer shoots, the mage heals"),
    M("w02", "the same, for the mage", "Isolde", mana=70, doing='lich',
      mates={'Roland': (28, 42, (3, -5)), 'Wystan': (100, 0, (3, 0)), 'Thea': (100, 0, (5, 1)), 'Corwin': (100, 0, (4, -1))},
      creatures=[('lich', 'a lich', (4, -6), 'Roland', 70), ('bk1', 'a bone knight', (2, -5), 'Roland', 100),
                 ('bk2', 'a bone knight', (3, -6), 'Roland', 100)],
      want=("Roland",), why="a heal from range for the warrior near death"),
    M("w03", "the same, for the tamer", "Thea", pet_on='lich',
      mates={'Roland': (28, 42, (-2, -6)), 'Wystan': (100, 0, (-2, -1)), 'Isolde': (100, 0, (-5, -1)), 'Corwin': (100, 0, (-1, -2))},
      creatures=[('lich', 'a lich', (-1, -7), 'Roland', 70), ('bk1', 'a bone knight', (-3, -6), 'Roland', 100),
                 ('bk2', 'a bone knight', (-2, -7), 'Roland', 100)],
      want=(N,), why="no walk into the lich and the bone knights"),
    M("w04", "a warrior pulled out of the line", "Roland", hp=90, band=148, doing='lich',
      mates={'Isolde': (48, 0, (-2, 4)), 'Wystan': (100, 0, (-4, 3)), 'Thea': (100, 0, (2, 4))},
      creatures=[('lich', 'a lich', (1, -1), 'me', 60), ('bk', 'a bone knight', (0, -1), 'me', 100)],
      want=(N,), why="the mage hurt but steady with nothing on her: the warrior holds the line"),
    M("w05", "a bandage that costs a warrior nothing", "Roland", hp=85, band=148, doing='bk',
      mates={'Corwin': (50, 20, (-1, 1)), 'Isolde': (100, 0, (-3, 3))},
      creatures=[('bk', 'a bone knight', (1, 0), 'me', 70), ('sk', 'a skeleton', (-2, 1), 'Corwin', 100)],
      want=("Corwin",), why="the bard falling with a skeleton on him, beside the warrior"),
    M("w06", "a mage at low mana, a companion hurt but steady", "Isolde", mana=18, doing='lich',
      mates={'Roland': (90, 0, (1, -4)), 'Thea': (55, 0, (-3, 1)), 'Wystan': (100, 0, (-2, 2))},
      creatures=[('lich', 'a lich', (2, -5), 'Roland', 60)],
      want=(N,), why="the tamer steady with nothing on her: the last mana is for the lich or a real need"),
    M("w07", "a mage at low mana, one heal for two", "Isolde", mana=14, doing='lich',
      mates={'Corwin': (57, 0, (-2, -2)), 'Roland': (62, 33, (1, -4))},
      creatures=[('lich', 'a lich', (2, -5), 'Roland', 75)],
      want=("Roland",), why="one heal left: the warrior losing health to the lich, not the steady bard"),
    M("w08", "two healers, the companion still falling fast", "Thea", pet_on='bk', helped=('Roland',),
      mates={'Roland': (41, 44, (1, -1)), 'Isolde': (100, 0, (-3, 2))},
      creatures=[('lich', 'a lich', (2, -3), 'Roland', 80), ('bk', 'a bone knight', (1, -2), 'Roland', 90)],
      want=("Roland",), why="one heal isn't keeping up with a lich and a bone knight"),
    M("w09", "two healers, the companion steady", "Isolde", mana=35, doing='lich', helped=('Roland',),
      mates={'Roland': (52, 0, (2, -3)), 'Wystan': (100, 0, (1, -2))},
      creatures=[('lich', 'a lich', (3, -5), 'Roland', 55)],
      want=(N,), why="the archer is bandaging him and he holds: the mana is for the lich"),
    M("w10", "two healers, another hurt", "Corwin", helped=('Wystan',),
      mates={'Wystan': (45, 0, (1, 1)), 'Roland': (55, 25, (-1, -2)), 'Thea': (100, 0, (2, 1))},
      creatures=[('sk', 'a skeleton', (-1, -3), 'Roland', 100)],
      want=("Roland",), why="the archer is in another's hands; the warrior is falling"),
    M("w11", "near death, a few steps away from the fight", "Corwin",
      mates={'Wystan': (22, 0, (0, 4)), 'Roland': (85, 10, (0, -6)), 'Isolde': (100, 0, (-2, -4)), 'Thea': (100, 0, (2, -5))},
      creatures=[('lich', 'a lich', (1, -7), 'Roland', 60), ('sk', 'a skeleton', (-1, -6), 'Roland', 100)],
      want=("Wystan",), why="the archer near death, nothing on him, the walk away from the fight"),
    M("w12", "near death, with things between, for a warrior", "Roland", band=148, doing='sk',
      mates={'Isolde': (18, 30, (-4, -3)), 'Wystan': (100, 0, (2, 3))},
      creatures=[('sk', 'a skeleton', (1, 0), 'me', 100), ('bk', 'a bone knight', (-5, -3), 'Isolde', 100),
                 ('sk2', 'a skeleton', (-2, -2), '', 100)],
      want=("Isolde",), why="the warrior can take the hits: to the mage's side, near death with a bone knight on her"),
    M("w13", "two near death, one falling", "Isolde", mana=45, doing='lich',
      mates={'Wystan': (20, 0, (-3, 0)), 'Roland': (35, 40, (2, -5))},
      creatures=[('lich', 'a lich', (3, -6), 'Roland', 70)],
      want=("Roland",), why="the warrior dies first: falling fast under the lich; the archer has nothing on him"),
    M("w14", "the helper hurt and falling", "Wystan", hp=54, drop=26, doing='bk',
      mates={'Corwin': (52, 0, (-2, 0)), 'Roland': (100, 0, (3, -3))},
      creatures=[('sk', 'a skeleton', (1, 0), 'me', 100), ('bk', 'a bone knight', (0, 1), 'me', 90)],
      want=(N,), why="two on the archer and he is falling; the bard is steady"),
    M("w15", "the helper hurt, a companion near death beside it", "Roland", hp=47, band=148, doing='sk',
      mates={'Isolde': (12, 33, (-1, 0))},
      creatures=[('sk', 'a skeleton', (1, 0), 'me', 60), ('bk', 'a bone knight', (-2, 0), 'Isolde', 80)],
      want=("Isolde",), why="the mage dies in seconds; the warrior's own health can wait one bandage"),
    M("w16", "the helper badly hurt", "Thea", hp=30, drop=20, pet_on='sk',
      mates={'Corwin': (50, 0, (-1, -1))},
      creatures=[('sk', 'a skeleton', (1, 0), 'me', 100)],
      want=(N,), why="the tamer sees to herself"),
    M("w17", "falling against steady", "Wystan", doing='sk1',
      mates={'Roland': (56, 0, (2, 0)), 'Corwin': (63, 35, (-1, 0))},
      creatures=[('sk1', 'a skeleton', (-2, 0), 'Corwin', 80), ('sk2', 'a skeleton', (-2, 1), 'Corwin', 100)],
      want=("Corwin",), why="the bard is losing health to two skeletons; the warrior is steady"),
    M("w18", "the fight over, a walk to bandage", "Corwin", intent='rest',
      mates={'Wystan': (46, 0, (0, 4)), 'Thea': (100, 0, (1, 1))},
      creatures=[('bk', 'a bone knight', (-12, 0), '', 100)],
      want=("Wystan",), why="nothing near: walk over and bandage him"),
    M("w19", "the fight over, lightly hurt", "Wystan", intent='rest',
      mates={'Roland': (82, 0, (4, 3))},
      creatures=[],
      want=(N,), why="he heals himself"),
    M("w20", "full mana, falling above 60%", "Isolde", mana=90, doing='lich',
      mates={'Roland': (72, 28, (1, -3)), 'Wystan': (100, 0, (-2, 1))},
      creatures=[('lich', 'a lich', (2, -4), 'Roland', 65), ('bk', 'a bone knight', (0, -4), 'Roland', 100)],
      want=("Roland",), why="the warrior losing health fast to two, and mana to spare"),
]

FIRST = RANDOM_FIRST + WRITTEN_FIRST

# ---------------------------------------------------------------- the second set, held back
# Written after the wording was settled on the first set, before any model saw it, and run once. Drawn at random
# (seed 2614) from the party runs of 2026-10-13 05:00 to 09:59 and 2026-10-14, as the first set's were.
RANDOM_SECOND = [
    M("s01", "20261013-051727 Isolde 1791879558", "Isolde", hp=64, mana=37, doing='k1',
      mates={'Thea': (100, 0, (0, -4)), 'Wystan': (64, 0, (-2, -7)), 'Roland': (100, 0, (-7, 7)), 'Corwin': (100, 0, (-6, -16))},
      creatures=[('k1', 'a bone knight', (0, -5), '', 60), ('k2', 'a bone knight', (0, -5), '', 10)],
      want=(N,), why="the archer hurt but steady with nothing on him; mana middling"),
    M("s02", "20261013-053025 Corwin 1791880320", "Corwin", band=99, doing='k1', intent='rest',
      mates={'Thea': (100, 0, (0, -1)), 'Roland': (88, 6, (-2, -2)), 'Wystan': (76, 24, (10, 8)), 'Isolde': (100, 0, (10, 12))},
      creatures=[('k1', 'a bone knight', (11, 8), 'Wystan', 60)],
      want=(N,), why="the warrior lightly hurt; the archer out of reach"),
    M("s03", "20261013-060536 Thea 1791882406", "Thea",
      mates={'Corwin': (100, 0, (0, -1)), 'Isolde': (100, 0, (-1, -4)), 'Wystan': (100, 0, (-5, -4)), 'Roland': (64, 36, (-6, -8))},
      creatures=[('k1', 'a skeleton', (-10, -10), '', 100), ('k2', 'a bone knight', (-7, -8), 'Roland', 60),
                 ('k3', 'a skeleton', (-10, -10), '', 100)],
      want=(N,), why="the warrior 8 tiles off with a bone knight on him: no walk"),
    M("s04", "20261013-062728 Thea 1791883713", "Thea",
      mates={'Wystan': (100, 0, (-2, 0)), 'Isolde': (100, 0, (2, 0)), 'Corwin': (100, 0, (3, 3)), 'Roland': (73, 27, (-1, -3))},
      creatures=[('k1', 'a bone knight', (-2, -3), 'Roland', 85), ('k2', 'a skeleton', (-2, -3), 'Roland', 100),
                 ('k3', 'a lich', (0, -10), '', 100)],
      want=(N,), why="the warrior at 73% with two on him, a walk into them for the tamer"),
    M("s05", "20261013-064813 Wystan 1791885273", "Wystan", band=96, intent='rest',
      mates={'Thea': (100, 0, (2, 1)), 'Roland': (92, 0, (-5, 1)), 'Corwin': (100, 0, (9, 5)), 'Isolde': (100, 0, (10, 0))},
      creatures=[('k1', 'a crossbill', (5, -5), '-', 100), ('k2', 'a sparrow', (-10, 0), '-', 100)],
      want=(N,), why="nobody hurt to speak of"),
    M("s06", "20261013-065806 Roland 1791885622", "Roland", hp=91, band=148,
      mates={'Thea': (100, 0, (2, 0)), 'Wystan': (100, 0, (1, 2)), 'Corwin': (100, 0, (2, -3)), 'Isolde': (68, 32, (2, 6))},
      creatures=[('k1', 'a bone knight', (3, 6), 'Isolde', 100)],
      want=(N,), why="the mage falling but not near death, 6 tiles off: the warrior goes for the bone knight, not to bandage"),
    M("s07", "20261013-065806 Thea 1791885660", "Thea", band=97, intent='rest',
      mates={'Roland': (100, 0, (-2, 0)), 'Isolde': (72, 0, (-3, 3)), 'Corwin': (100, 0, (-2, 9)), 'Wystan': (100, 0, (-2, 9))},
      creatures=[],
      want=(N,), why="the mage lightly hurt and steady, the fight over"),
    M("s08", "20261013-071956 Roland 1791886904", "Roland", hp=81, drop=19, band=148, doing='k1',
      mates={'Isolde': (74, 0, (0, -1)), 'Wystan': (100, 0, (-3, 5)), 'Thea': (100, 0, (0, 6)), 'Corwin': (100, 0, (-4, 8))},
      creatures=[('k1', 'a bone knight', (5, 0), '', 10)],
      want=(N,), why="the mage steady with nothing on her"),
    M("s09", "20261013-074120 Thea 1791888139", "Thea", pet_on='k1',
      mates={'Isolde': (100, 0, (2, 3)), 'Wystan': (100, 0, (0, 4)), 'Roland': (93, 7, (-4, -2)), 'Corwin': (100, 0, (4, 8))},
      creatures=[('k1', 'a bone knight', (-5, -2), 'Roland', 85), ('k2', 'a skeleton', (-5, -2), 'Roland', 100),
                 ('k3', 'a skeleton', (-5, -2), 'Roland', 100), ('k4', 'a lich', (-10, -10), '', 100),
                 ('k5', 'a skeleton', (-10, -10), '', 100), ('k6', 'a bone knight', (-10, -10), '-', 100)],
      want=(N,), why="the warrior lightly hurt"),
    M("s10", "20261013-075146 Thea 1791889071", "Thea", intent='rest',
      mates={'Corwin': (100, 0, (0, -5)), 'Wystan': (74, 26, (-1, -6)), 'Isolde': (100, 0, (1, -6))},
      creatures=[('k1', 'a bone knight', (-2, -6), 'Wystan', 35), ('k2', 'a skeleton', (0, -10), 'Roland', 100)],
      want=(N,), why="the archer 6 tiles off with a bone knight on him: no walk"),
    M("s11", "20261013-082630 Thea 1791890869", "Thea", band=99, pet_on='k1', helped=('Roland',),
      mates={'Isolde': (100, 0, (1, 1)), 'Corwin': (100, 0, (2, 4)), 'Wystan': (91, 9, (4, 2)), 'Roland': (25, 37, (0, 12))},
      creatures=[('k1', 'a skeleton', (-5, -5), '', 100), ('k2', 'a bone knight', (-3, -3), '', 100),
                 ('k3', 'a skeleton', (5, 2), 'Wystan', 100), ('k4', 'a skeleton', (0, 5), '', 100),
                 ('k5', 'a bone knight', (0, 5), '', 100)],
      want=(N,), why="the archer lightly hurt; the warrior out of reach"),
    M("s12", "20261013-093227 Wystan 1791894825", "Wystan", helped=('Isolde',), doing='k3',
      mates={'Corwin': (100, 0, (-4, 6)), 'Roland': (55, 0, (-6, 1)), 'Thea': (98, 0, (-7, 2)), 'Isolde': (55, 13, (-5, 12))},
      creatures=[('k1', 'a skeleton', (0, 3), '', 100), ('k2', 'a skeleton', (-3, 3), '', 100),
                 ('k3', 'a lich', (-7, 1), 'Roland', 10), ('k4', 'a bone knight', (-5, 6), 'Corwin', 60),
                 ('k5', 'a bone knight', (-5, -5), '', 100)],
      want=(N,), why="the warrior steady under a lich near death, 6 tiles off: the archer shoots the lich"),
    M("s13", "20261013-094250 Corwin 1791895456", "Corwin",
      mates={'Thea': (100, 0, (1, -1)), 'Roland': (96, 0, (3, -1)), 'Isolde': (64, 0, (7, 6)), 'Wystan': (89, 4, (8, -2))},
      creatures=[('k1', 'a bone knight', (4, -1), 'Roland', 60)],
      want=(N,), why="the mage hurt but steady, 7 tiles off"),
    M("s14", "20261014-001353 Thea 1791947720", "Thea", helped=('Corwin', 'Roland'),
      mates={'Wystan': (100, 0, (-3, -4)), 'Isolde': (100, 0, (1, -4)), 'Roland': (94, 0, (-6, -6)), 'Corwin': (59, 21, (-4, -6))},
      creatures=[('k1', 'a skeleton', (-5, -6), 'Corwin', 100), ('k2', 'a skeleton', (-5, -6), 'Corwin', 100),
                 ('k3', 'a lich', (0, -5), '', 100), ('k4', 'a skeleton', (-5, -6), 'Corwin', 60)],
      want=(N,), why="the bard being healed, 6 tiles off under three skeletons: no walk"),
]

WRITTEN_SECOND = [
    M("x01", "written", "Isolde", mana=40, doing='bk',
      mates={'Roland': (58, 30, (2, -3)), 'Wystan': (100, 0, (-2, 1))},
      creatures=[('bk', 'a bone knight', (3, -3), 'Roland', 80), ('sk', 'a skeleton', (2, -4), 'Roland', 100)],
      want=("Roland",), why="the warrior falling under two, mana enough"),
    M("x02", "written", "Wystan", doing='bk',
      mates={'Thea': (70, 30, (1, 0)), 'Roland': (100, 0, (4, -2))},
      creatures=[('bk', 'a bone knight', (2, 0), 'Thea', 90), ('sk', 'a skeleton', (5, -2), 'Roland', 100)],
      want=("Thea",), why="the tamer losing health fast to a bone knight, beside the archer"),
    M("x03", "written", "Isolde", mana=12, doing='sk1',
      mates={'Wystan': (50, 0, (-4, 0)), 'Corwin': (60, 30, (2, -3))},
      creatures=[('sk1', 'a skeleton', (3, -3), 'Corwin', 90), ('sk2', 'a skeleton', (2, -4), 'Corwin', 100)],
      want=("Corwin",), why="one heal left: the bard losing health to two skeletons, not the steady archer"),
    M("x04", "written", "Wystan", helped=('Roland',),
      mates={'Roland': (38, 0, (1, 0)), 'Isolde': (100, 0, (-2, 2))},
      creatures=[('sk', 'a skeleton', (6, -4), '', 100)],
      want=(N,), why="the warrior is being healed and holds, nothing on him"),
    M("x05", "written", "Roland", band=148, doing='sk2', helped=('Corwin',),
      mates={'Corwin': (14, 30, (-1, 1))},
      creatures=[('sk2', 'a skeleton', (1, -1), 'me', 100), ('sk', 'a skeleton', (-2, 1), 'Corwin', 70)],
      want=("Corwin",), why="the bard near death though another heals him"),
    M("x06", "written", "Corwin", hp=45, drop=25,
      mates={'Thea': (55, 0, (-1, 0))},
      creatures=[('bk', 'a bone knight', (1, 0), 'me', 85)],
      want=(N,), why="the bard is falling with a bone knight on him; the tamer is steady"),
    M("x07", "written", "Wystan", hp=52, doing='bk',
      mates={'Isolde': (15, 40, (-1, 1))},
      creatures=[('bk', 'a bone knight', (5, -2), '', 60), ('sk', 'a skeleton', (-2, 2), 'Isolde', 100)],
      want=("Isolde",), why="the mage near death beside the archer, who is steady"),
    M("x08", "written", "Thea", pet_on='lich',
      mates={'Corwin': (20, 0, (0, 4)), 'Roland': (80, 10, (1, -6)), 'Isolde': (100, 0, (-2, -3))},
      creatures=[('lich', 'a lich', (2, -7), 'Roland', 70), ('sk', 'a skeleton', (0, -7), 'Roland', 100),
                 ('sk2', 'a skeleton', (1, -7), 'Roland', 100)],
      want=("Corwin",), why="the bard near death, nothing on him, the walk away from the fight"),
    M("x09", "written", "Roland", band=148, intent='rest',
      mates={'Isolde': (40, 0, (-3, 4)), 'Thea': (100, 0, (1, 1))},
      creatures=[],
      want=("Isolde",), why="the fight over: walk over and bandage the mage"),
    M("x10", "written", "Isolde", mana=95, doing='bk',
      mates={'Wystan': (70, 30, (-3, 4)), 'Roland': (100, 0, (2, -1))},
      creatures=[('bk', 'a bone knight', (-4, 4), 'Wystan', 90)],
      want=("Wystan",), why="the archer losing health fast, mana to spare"),
    M("x11", "written", "Corwin",
      mates={'Roland': (30, 35, (0, -5)), 'Wystan': (100, 0, (2, 1))},
      creatures=[('lich', 'a lich', (1, -6), 'Roland', 80), ('sk1', 'a skeleton', (0, -4), 'Roland', 100),
                 ('sk2', 'a skeleton', (-1, -5), 'Roland', 100)],
      want=(N,), why="a walk into a lich and two skeletons for the bard"),
    M("x12", "written", "Thea", pet_on='sk',
      mates={'Roland': (80, 0, (1, 0))},
      creatures=[('sk', 'a skeleton', (2, 0), 'Roland', 70)],
      want=(N,), why="the warrior lightly hurt and steady"),
    M("x13", "written", "Isolde", mana=16, doing='lich',
      mates={'Roland': (22, 30, (4, -4)), 'Wystan': (100, 0, (-1, 2))},
      creatures=[('lich', 'a lich', (5, -5), 'Roland', 50)],
      want=("Roland",), why="the warrior near death under the lich: the last mana is for this"),
    M("x14", "written", "Roland", band=148, doing='bk',
      mates={'Isolde': (45, 0, (-5, 4)), 'Thea': (100, 0, (-2, 3))},
      creatures=[('bk', 'a bone knight', (1, 0), 'me', 70)],
      want=(N,), why="the warrior holds the line; the mage is steady with nothing on her"),
]

SECOND = RANDOM_SECOND + WRITTEN_SECOND


def build(m: dict[str, Any]) -> tuple[Situation, dict[str, int]]:
    """The moment as the fight loop would see it: a Situation with the companions in it (as mates.Mates.before
    fills them), and each companion's fall in health."""
    me = m["me"]
    plays, skills = PARTY[me]
    mobiles, hostiles, serial = [], [], {}
    for i, (key, name, (dx, dy), on, hp) in enumerate(m["creatures"]):
        s = 0x4000 + i
        serial[key] = s
        war = on != "-"
        mobiles.append({"serial": s, "name": name, "monster": True, "pet": False, "dead": False, "hits_pct": hp,
                        "dx": dx, "dy": dy, "distance": max(abs(dx), abs(dy)), "war_mode": war,
                        "attacking_me": on == "me"})
        info: dict[str, Any] = {"aggressive": war}
        if name in PEACEFUL:
            info["peaceful"] = True
        hostiles.append(Candidate(f"t{i + 1}", s, name, max(abs(dx), abs(dy)), info, hits_pct=hp))
    mates = []
    for i, (name, (hp, _drop, (dx, dy))) in enumerate(m["mates"].items()):
        s = 0x900 + i
        d = max(abs(dx), abs(dy))
        mobiles.append({"serial": s, "name": name, "monster": False, "pet": False, "dead": False, "hits_pct": hp,
                        "dx": dx, "dy": dy, "distance": d})
        mate = Mate(name, s, PARTY[name][0], hp, d, True, False,
                    [serial[k] for k, _, _, on, _ in m["creatures"] if on == name])
        mate.being_helped = name in m["helped"]
        mates.append(mate)
    mana = m["mana"]
    raw = {"player": {"name": me, "x": 1000, "y": 1000, "hits": m["hp"], "hits_max": 100, "skills": skills,
                      "supplies": {"bandages": 0 if plays == "mage" else m["band"]},
                      **({"mana": mana, "mana_max": 100} if mana is not None else {})},
           "agent": {"engaged": serial.get(m["doing"], 0), "bandaging": m["bandaging"],
                     "pet_target": serial.get(m["pet_on"], 0)},
           "mobiles": sorted(mobiles, key=lambda x: x["distance"]),
           "magic": {"spells": [{"name": GREATER_HEAL, "missing": [] if (mana or 0) >= helping.GREATER_HEAL_MANA else ["mana"]}]}
           if mana is not None else {}}
    sit = Situation(raw, {"you": {}}, hostiles=hostiles, hp_pct=m["hp"], archetype=plays,
                    mana_pct=mana if mana is not None else 100, mates=mates)
    sit.hp_drop = m["drop"]
    return sit, {name: v[1] for name, v in m["mates"].items()}


def rule(m: dict[str, Any]) -> tuple[str, str] | None:
    """What policy.help_mates does on the moment: (whom, how) or None."""
    sit, _ = build(m)
    dec = policy.Decision(m["intent"], 1.0, [], "")
    mem = policy.Memory()
    policy.help_mates(sit, dec, mem, policy.PolicyConfig(), now=100.0)
    if mem.helped:
        return mem.helped
    walk = re.search(r"go to (\w+)", dec.note)
    return (walk.group(1), "walk") if walk else None


def outcome(probs: dict[str, float], opts: dict[str, Any], cut: float) -> tuple[str, str] | None:
    best = max(probs, key=probs.get)
    if best in opts and probs[best] >= cut:
        mate, how = opts[best]
        return mate.name, how
    return None


async def ask(judge: Any, m: dict[str, Any]) -> dict[str, Any]:
    sit, drops = build(m)
    offered = helping.ways(sit)
    opts = helping.options(offered)
    st = helping.help_state(sit, drops)
    q = helping.help_question(sit, opts, drops)
    err = ""
    for attempt in range(3):
        try:
            with costs.kind("fight"):
                a = await judge.ask(st, {"help": q})
            c = a.choices["help"]
            return {"p": {k: round(v, 3) for k, v in c.probabilities.items()},
                    "ids": {k: [mate.name, how] for k, (mate, how) in opts.items()},
                    "latency_ms": a.latency_ms, "cost": a.cost, "input_tokens": a.input_tokens, "model_id": a.model}
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"[:300]
            await asyncio.sleep(1 + attempt)
    return {"error": err}


def judged(m: dict[str, Any], got: tuple[str, str] | None) -> str:
    """"right", or the kind of wrong: a help missed, a needless help, a walk not wanted, the wrong companion."""
    want = m["want"]
    name = got[0] if got else None
    if name in want:
        return "right"
    if name is None:
        return "missed"
    if got[1] == "walk":
        return "walk_not_wanted"
    return "needless" if want == [None] else "wrong_one"


def score(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    counts = {"right": 0, "missed": 0, "needless": 0, "walk_not_wanted": 0, "wrong_one": 0}
    parts: dict[str, list[int]] = {}
    wrong = []
    for r in rows:
        v = judged(r, r[key])
        counts[v] += 1
        part = parts.setdefault(r["id"][0], [0, 0])
        part[0] += v == "right"
        part[1] += 1
        if v != "right":
            wrong.append({"id": r["id"], "src": r["src"], "want": r["want"], "got": r[key], "how": v, "why": r["why"],
                          **({"p": r["p"], "ids": r["ids"]} if "p" in r else {})})
    return {**counts, "of": len(rows), "by_part": {k: f"{a}/{b}" for k, (a, b) in sorted(parts.items())}, "wrong": wrong}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="jev,decision1", help="aliases or model ids, comma-separated")
    ap.add_argument("--sets", default="first")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    judges.load_dotenv(BRAIN / ".env")
    sets = {k: v for k, v in (("first", FIRST), ("second", SECOND)) if k in args.sets.split(",") and v}
    result: dict[str, Any] = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "cut": helping.HELP_CUT,
                              "moments": {k: len(v) for k, v in sets.items()}, "rule": {}, "models": {}}
    for which, todo in sets.items():
        for m in todo:
            sit, _ = build(m)
            if not helping.ways(sit):
                raise SystemExit(f"{m['id']}: nobody could be helped; a moment the question isn't asked in")
        rows = [{**m, "rule": rule(m)} for m in todo]
        result["rule"][which] = score(rows, "rule")
    for name in [m.strip() for m in args.models.split(",") if m.strip()]:
        judge = judges.make("jev", model=None if name == "jev" else name)
        result["models"][name] = {}
        try:
            for which, todo in sets.items():
                got = []
                for i in range(0, len(todo), 6):  # six questions at a time
                    got += await asyncio.gather(*(ask(judge, m) for m in todo[i:i + 6]))
                rows = [{**m, **g} for m, g in zip(todo, got) if "error" not in g]
                errors = [g["error"] for g in got if "error" in g]
                for r in rows:
                    opts = {k: (Mate(n, 0, "", 0, 0), how) for k, (n, how) in r["ids"].items()}
                    r["model"] = outcome(r["p"], opts, helping.HELP_CUT)
                    for c in (0.3, 0.4, 0.6, 0.7):
                        r[f"at_{c}"] = outcome(r["p"], opts, c)
                s = score(rows, "model")
                s["cuts"] = {str(c): score(rows, f"at_{c}")["right"] for c in (0.3, 0.4, 0.6, 0.7)}
                lat = sorted(r["latency_ms"] for r in rows)
                s |= {"judge": judge.name, "served_by": sorted({r["model_id"] for r in rows if "model_id" in r}),
                      "latency_ms": {"median": round(statistics.median(lat)), "p90": round(lat[int(0.9 * (len(lat) - 1))])}
                      if lat else {},
                      "cost_usd_a_moment": round(statistics.mean(r["cost"] for r in rows), 7) if rows else None,
                      "input_tokens": round(statistics.mean(r["input_tokens"] for r in rows)) if rows else None,
                      "errors": len(errors), **({"first_error": errors[0]} if errors else {})}
                result["models"][name][which] = s
                if args.out:
                    args.out.write_text(json.dumps(result, indent=1))
        finally:
            await judge.close()
    result["costs"] = costs.ledger.summary()
    if args.out:
        args.out.write_text(json.dumps(result, indent=1))

    def show(label: str, s: dict[str, Any]) -> None:
        print(f"\n{label}: {s['right']}/{s['of']} right  (by part {s['by_part']})  missed {s['missed']}, needless "
              f"{s['needless']}, walk not wanted {s['walk_not_wanted']}, wrong one {s['wrong_one']}")
        if "cuts" in s:
            print(f"  by cut: {s['cuts']}  {s['latency_ms']} ms, ${s['cost_usd_a_moment']} a moment, "
                  f"{s['input_tokens']} input tokens, {s['errors']} errors")
        for w in s["wrong"]:
            print(f"    {w['id']} wanted {w['want']}, got {w['got']} ({w['how']})"
                  + (f"  {w['p']} {w['ids']}" if "p" in w else "") + f"  [{w['why']}]")

    for which, s in result["rule"].items():
        show(f"rule, {which} set", s)
    for name, by_set in result["models"].items():
        for which, s in by_set.items():
            show(f"{name}, {which} set", s)
    if args.out:
        print(f"\nresults: {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
