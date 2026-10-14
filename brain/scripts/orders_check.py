"""The leader's orders in its own words, read without the game (cuo-cym.15).

A companion of a character a person plays took three orders from the leader's speech by matching a short list
of phrases (companion.order_of). Now each line the leader says is a question for system one
(companion.order_question): follow, stay, the table, or none of them. This puts a set of lines to the models
and to the phrases, for each of three companions (Isolde, Wystan and Thea, led by Roland), and counts how often
each reads the line as written below.

  uv run python scripts/orders_check.py --out bench/2026-10-13-orders.json
  uv run python scripts/orders_check.py --models jev,decision1,haiku --cut 0.5

Two sets of lines. The first was written with the question, and the cut (companion.ORDER_CUT) was chosen on
it. The second was written after that and before any model had seen it: shorter, looser, as people type in a
game, some said while the companions wait. Its numbers are the ones to go by.

The lines, by group:
- plain: orders in the phrases' own words;
- follow, stay, table: the same orders in other words;
- who: lines that are for some of the companions and not the others;
- talk: lines that are no order, many with an order's words in them;
- at the table: said while the party sits, where only an order to move on counts.

For each model: how many readings were right in each group, orders missed (read as talk), talk taken for an
order, and one order taken for another; at each cut from 0.3 to 0.8, the same in total; latency and cost a
question. Every wrong reading is kept in the output.
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

BRAIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRAIN / "src"))

from uo_brain import companion, costs, standing  # noqa: E402
from uo_brain import judge as judges  # noqa: E402

LEADER = "Roland"
PARTY = ["Isolde", "Wystan", "Thea"]
F, S, T, G, N = "follow", "stay", "table", "go", None
C = "asked"  # asked for something that is none of the orders ("cover me"): no order, and it says so
SET = "a setting"  # no order, and it speaks to a setting: what it owes is that setting's question's (settings_check.py)

# (group, the line, what each companion should take: one order for all, or by name with "*" for the rest)
LINES: list[tuple[str, str, Any]] = [
    ("plain", "Follow me.", F),
    ("plain", "Stay here.", S),
    ("plain", "Sit down, drinks are on me.", T),
    ("plain", "Come on, let's go.", F),
    ("plain", "Stay close.", F),
    ("plain", "Follow me to the inn.", F),
    ("plain", "Hold position.", S),
    ("plain", "Wait here.", S),

    ("follow", "Right, on we go.", F),
    ("follow", "Keep up, you lot.", F),
    ("follow", "Come along then.", F),
    ("follow", "Time we were off.", F),
    ("follow", "ok lets move", F),
    ("follow", "This way, quickly!", F),
    ("follow", "Back to me, all of you!", F),
    ("follow", "We're leaving. Now.", F),
    ("follow", "Stick with me through here.", F),
    ("follow", "Get over here!", F),
    ("follow", "c'mon", F),
    ("follow", "Off we go then, mind the bones.", F),

    ("stay", "Hold up a moment.", S),
    ("stay", "Wait for me by this tree.", S),
    ("stay", "Don't move, I'll scout ahead.", S),
    ("stay", "Hang back here, I'll pull one to us.", S),
    ("stay", "Give me a minute. Nobody move.", S),
    ("stay", "Halt!", S),
    ("stay", "Stop right there, all of you.", S),
    ("stay", "Rest here while I check the path.", S),
    ("stay", "wait", S),
    ("stay", "You lot stop here, I'm going in alone.", S),

    ("table", "Who fancies an ale? Take the weight off.", T),
    ("table", "Pull up a chair, all of you.", T),
    ("table", "Time for a drink, I think. Sit.", T),
    ("table", "Let's rest our legs and talk it over.", T),
    ("table", "Sit yourselves down, the first round is mine.", T),
    ("table", "Grab a seat, we've earned a pint.", T),

    ("who", "Wystan, wait here.", {"Wystan": S, "*": N}),
    ("who", "Wystan, hang back here.", {"Wystan": S, "*": N}),
    ("who", "Wystan stay, the rest of you with me.", {"Wystan": S, "*": F}),
    # Thea was first written down as "none". Both models read it as "stay" for her, and they are right: left to
    # follow as before, she would come along with the rest.
    ("who", "Everyone but Thea, come on.", {"Thea": S, "*": F}),
    ("who", "Isolde, with me. Wystan and Thea, wait here.", {"Isolde": F, "*": S}),
    ("who", "Thea, keep up!", {"Thea": F, "*": N}),
    ("who", "Isolde and Wystan, hold here a moment.", {"Thea": N, "*": S}),
    ("who", "Just Isolde with me, you two stay put.", {"Isolde": F, "*": S}),

    ("talk", "You should have stayed back, Isolde.", N),
    ("talk", "It came straight to me, and I'll stay alive, thanks.", N),
    ("talk", "Nice weather for it.", N),
    ("talk", "That lich nearly had me.", N),
    ("talk", "Did you see that shot?", N),
    ("talk", "Come on, that was a warning shot?", N),
    ("talk", "I could follow that road for days and never tire of it.", N),
    ("talk", "My old dog never stayed when told either.", N),
    ("talk", "Wait, was that a dragon?", N),
    ("talk", "Hold on, what did you just say?", N),
    ("talk", "We'll sit down when this is done, not before.", N),
    ("talk", "vendor buy", N),
    ("talk", "bank", N),
    ("talk", "all kill", N),
    ("talk", "Thanks for the heal, Isolde.", N),
    ("talk", "Good fight, all of you.", N),
    # These two, and "heal pls" and "Isolde, stop casting at my target." in the second set, were written down as
    # talk when a line that asks for something else had no answer of its own (cuo-cym.15); they are requests.
    ("talk", "Isolde, heal me!", {"Isolde": C, "*": N}),
    ("talk", "Kill the lich first!", C),
    ("talk", "Wystan, how many arrows have you left?", N),
    ("talk", "I need to stop drinking so much.", N),
    ("talk", "Thea, does Bramble ever sit still?", N),
    ("talk", "Let's go to Covetous tomorrow, I hear it pays.", N),
    ("talk", "He followed me all the way from Yew, the fool.", N),
    ("talk", "lol", N),
    ("talk", "Remind me to buy bandages.", N),
    ("talk", "Who wants to wait for a dragon? Not me.", N),

    ("at the table", "Right. Follow me.", F),
    ("at the table", "On your feet.", F),
    ("at the table", "Right, we should get moving.", F),
    ("at the table", "Time to go, finish your drinks.", F),
    ("at the table", "Let's go.", F),
    ("at the table", "Up, all of you, the road's waiting.", F),
    ("at the table", "Drink with me! Drinks are on me.", N),
    ("at the table", "Come on, that was a warning shot?", N),
    ("at the table", "Wystan, you shot the bone knight in the back.", N),
    ("at the table", "Another round!", N),
    ("at the table", "Sit down, Wystan, nobody's leaving yet.", N),
    ("at the table", "Stay a while, the night is young.", N),
    ("at the table", "Isolde, what would you do differently next time?", N),
    ("at the table", "To the fallen!", N),
]

# The second set. A fourth field says the companions are waiting (after "stay here") when the line is said.
SECOND: list[tuple] = [
    ("follow", "w/me", F),
    ("follow", "lets roll", F),
    ("follow", "move it people", F),
    ("follow", "follow", F),
    ("follow", "everyone on me", F),
    ("follow", "Onwards!", F),
    ("follow", "regroup on me", F),
    ("follow", "Let's get out of here.", F),
    ("follow", "ok you can come now", F, "waiting"),
    ("follow", "All clear, come through.", F, "waiting"),
    ("follow", "I'm back. Let's go.", F, "waiting"),

    ("stay", "hold", S),
    ("stay", "stay", S),
    ("stay", "dont follow me in there", S),
    ("stay", "hold here pls", S),
    ("stay", "Stay back, it's trapped.", S),
    ("stay", "wait there a sec", S),
    ("stay", "Nobody goes any further.", S),
    ("stay", "Freeze!", S),

    ("table", "Let's have a sit down.", T),
    ("table", "beer time, sit", T),
    ("table", "Park yourselves, I'm buying.", T),
    ("table", "Take five. Sit down and have a drink.", T),

    ("who", "thea stay", {"Thea": S, "*": N}),
    ("who", "Wystan with me, the others hold.", {"Wystan": F, "*": S}),
    ("who", "Not you, Wystan. You wait.", {"Wystan": S, "*": N}),
    ("who", "Thea and Isolde, come here.", {"Wystan": N, "*": F}),
    ("who", "Wystan, stop following me for a minute.", {"Wystan": S, "*": N}),
    ("who", "Isolde, you coming or what?", {"Isolde": F, "*": N}),

    ("talk", "that was close", N),
    ("talk", "I can't stay long tonight.", N),
    ("talk", "anyone need to wait for mana?", N),
    ("talk", "Follow the money, as they say.", N),
    ("talk", "heal pls", C),
    ("talk", "gg", N),
    ("talk", "who took the gold?", N),
    ("talk", "Isolde, stop casting at my target.", {"Isolde": C, "*": N}),
    ("talk", "This place gives me the creeps.", N),
    ("talk", "Should we go left or right?", N),
    ("talk", "Wystan, nice shot.", N),
    ("talk", "wait for it...", N),
    ("talk", "Come on, you beauty!", N),
    ("talk", "I'd sit down but the chairs here bite.", N),
    ("talk", "Still there? Good.", N, "waiting"),
    ("talk", "Two more of them up ahead.", N, "waiting"),

    ("at the table", "ok up we get", F),
    ("at the table", "That's enough, we're off.", F),
    ("at the table", "Let's hit the road.", F),
    ("at the table", "I'm going to stretch my legs, wait here.", S),
    ("at the table", "One more and then we go.", N),
    ("at the table", "Sit, sit, I'm not done.", N),
    ("at the table", "Who's buying the next one?", N),
    ("at the table", "Follow that with a whisky.", N),
]


# The third set (cuo-cym.19, cuo-cym.20): a fourth order, to go somewhere without the leader, and the lines of the
# first playtest by a person (2026-10-13), where "off to the inn for you all - see you there" was read as "come
# along" at 0.99 by companions who were following, and nobody moved. Written with the question's new wording.
# A fourth field that is a list is the talk just before the line (the state's `said_before`); "away" says the
# companions are waiting at the inn they were sent to.
THIRD: list[tuple] = [
    ("go", "Right - off to the inn for you all - see you there", G),
    ("go", "Off you go then", G, ["Roland: Right - off to the inn for you all - see you there", "Isolde: The inn? Finally."]),
    ("go", "I'll follow you", G, ["Roland: Can you go off to the inn? I'll meet you there in a minute.",
                                  "Thea: Sounds good to me."]),
    ("go", "Go on ahead to the bank, I'll catch you up.", G),
    ("go", "Wait for me at the inn.", G),
    ("go", "You lot head to the tavern, I'll be along.", G),
    ("go", "Off to the healer with you, all of you.", G),
    ("go", "Go and wait at the stables.", G),
    ("go", "Meet you at the inn, I've a thing to do first.", G),
    ("go", "Wystan, go on to the inn. The rest of you with me.", {"Wystan": G, "*": F}),
    ("go", "Isolde and Thea, off to the bank. Wystan stays with me.", {"Wystan": F, "*": G}),
    ("go", "Thea, take Bramble to the stables and wait for us there.", {"Thea": G, "*": N}),

    ("back", "Right, come back.", F, "away"),
    ("back", "I'm at the graveyard gate, come and find me.", F, "away"),
    ("back", "Back to me, you lot.", F, "away"),
    ("back", "On my way. Order me an ale.", C, "away"),  # no order was wanted before requests were read: it is one
    ("back", "Stay there, I'm nearly with you.", S, "away"),

    ("talk", "Right, who wants to go off to the inn?", N),
    ("talk", "Shall we head to the graveyard? Yay or nay?", N),
    ("talk", "I might go to the bank later.", N),
    ("talk", "Go on then, tell me about the bear.", N),
    ("talk", "I'll meet you lot in hell.", N),
    ("talk", "feel free to loot", N),
    ("talk", "Please go ahead and loot", N),
    ("talk", "loot", N),
    ("request", "cover me", C),
    ("request", "heal me", C),
    ("request", "Isolde, kill that one for me.", {"Isolde": C, "*": N}),
    ("request", "someone go and buy me some reagents", C),
    ("request", "Wystan, scout ahead and tell me what's there.", {"Wystan": C, "*": N}),
    ("talk", "here's the plan...", N),
    ("talk", "So crew how are you all today?", N),
    ("talk", "Graveyard?", N, ["Roland: So, what do you all fancy doing next?", "Thea: Honey cakes first, then the caves."]),
    ("talk", "ok", N, ["Roland: Shall we head to the graveyard? Yay or nay?", "Wystan: Graveyard's fine, Roland."]),
    ("talk", "The inn was dreadful last time.", N),
    ("talk", "Off with his head, I say.", N),
    ("talk", "Who's off to the bank after this?", N),

    ("follow", "Follow me to the inn.", F),
    ("follow", "To the bank, all of you, with me.", F),
    ("follow", "Come on, the inn's this way.", F),
]


# The fourth set: written after the third had been run and the question settled, and run once. Its numbers are
# the ones to go by for the order to go somewhere.
FOURTH: list[tuple] = [
    ("go", "you guys go to the inn, brb", G),
    ("go", "Head on to the bank without me.", G),
    ("go", "I'll see you lot at the tavern.", G),
    ("go", "go ahead, I'll catch up", G),
    ("go", "Run along to the healer, I'll be right behind.", G),
    ("go", "Get yourselves to the bank and wait.", G),
    ("go", "inn. now. I'll join you.", G),
    ("go", "Thea, go wait at the stables.", {"Thea": G, "*": N}),
    ("go", "Wystan and Isolde, on to the inn with you. Thea, you're with me.", {"Thea": F, "*": G}),
    ("go", "Go on, then.", G, ["Roland: Why don't you three wait at the inn?", "Wystan: If you're buying."]),
    ("go", "yes, there", G, ["Roland: Go and wait for me at the bank.", "Isolde: The west one?"]),

    ("back", "Where are you? Get back here.", F, "away"),
    ("back", "come back to the graveyard", F, "away"),
    ("back", "Stay put, I'm coming.", S, "away"),
    ("back", "Nearly there, save me a seat.", N, "away"),

    ("talk", "Anyone been to the inn in Yew?", N),
    ("talk", "I'm off to bed soon.", N),
    ("talk", "We should go to the bank at some point.", N),
    ("talk", "Go on, laugh.", N),
    ("talk", "See you there, they said. And then nobody came.", N),
    ("talk", "He went off to the inn and never came back.", N),
    ("talk", "What's at the bank that you need?", N),
    ("talk", "vendor sell", N),
    ("talk", "I'll wait.", N),
    ("talk", "ok", N),
    ("talk", "right", N),
    ("talk", "nice", N),
    ("talk", "good idea", N, ["Isolde: We could rest at the inn, you know."]),

    ("follow", "To the inn! Follow me.", F),
    ("follow", "Let's all go to the bank.", F),
    ("follow", "Come with me to the healer.", F),
    ("follow", "Off we go again.", F),

    ("stay", "Wait here by the gate.", S),
    ("stay", "Hold here, I'll go to the bank and come back.", S),
]


# The fifth set: asked for what a companion doesn't do at a word (after two live runs in which "cover me" was
# read as talk and went unanswered). The reading of a request was settled on it and on the requests of the third:
# first tried as a sixth answer of the order question, where it took orders with it ("Stay here and cover the
# gate." and "Get yourselves to the bank and wait." were requests at 0.7 to 0.9), then as a yes/no of its own.
FIFTH: list[tuple] = [
    ("request", "tank that one for me", C),
    ("request", "Thea, send the bear in.", {"Thea": C, "*": N}),
    ("request", "somebody res me", C),
    ("request", "Wystan shoot the mage", {"Wystan": C, "*": N}),
    ("request", "can one of you carry this for me?", C),
    ("request", "protect Isolde", C),
    ("request", "go and get me some bandages from town", C),
    ("request", "Isolde, cast something on it!", {"Isolde": C, "*": N}),
    ("talk", "I could kill for an ale.", N),
    ("talk", "Heal thyself, as they say.", N),
    ("talk", "That bear could carry all of us.", N),
    ("talk", "Somebody shot the mage before I got there.", N),
    ("follow", "cover me, I'm going in. Follow!", F),
    ("stay", "Stay here and cover the gate.", S),
]


# The sixth set: written while a request was a sixth answer of the order question, run once with that and once
# more with the yes/no in the order's request.
SIXTH: list[tuple] = [
    ("request", "kill the archer first", C),
    ("request", "Thea, patch me up would you", {"Thea": C, "*": N}),
    ("request", "one of you go and find a healer", C),
    ("request", "keep them off me!", C),
    ("request", "Isolde, light it up.", {"Isolde": C, "*": N}),
    ("request", "watch my back", C),
    ("talk", "That one nearly killed me.", N),
    ("talk", "You lot fetch and carry like mules.", N),
    ("talk", "Scouts say the pass is clear.", N),
    ("talk", "Cover your ears, Corwin's tuning up.", N),
    ("follow", "Follow me and watch my back.", F),
    ("follow", "Come on, and kill anything that moves.", F),
    ("stay", "Wait here and guard the packs.", S),
    ("go", "Off to the inn with you, and order me a stew.", G),
    ("stay", "hold up", S),
    ("stay", "freeze", S),
]


# The seventh set: written after the yes/no became a request of its own and its wording was changed for what the
# sets above showed (asked to tell something, a cheer, a command to pets, "somebody"), and run once.
SEVENTH: list[tuple] = [
    ("request", "someone pull that ogre off me", C),
    ("request", "Wystan, pick that lock for me.", {"Wystan": C, "*": N}),
    ("request", "can anyone gate us home?", C),
    ("request", "give me a hand with this door", C),
    ("request", "Thea, lend me fifty gold.", {"Thea": C, "*": N}),
    ("request", "bandage me, quick", C),
    ("request", "hold this for me a sec", C),
    ("talk", "Tell me again how you lost that horse.", N),
    ("talk", "Go on, laugh.", N),
    ("talk", "all guard me", N),
    ("talk", "Wait till you see the size of it.", N),
    ("talk", "Someone ought to fix that bridge.", N),
    ("talk", "Remind me never to do that again.", N),
    ("talk", "vendor sell", N),
    ("talk", "grab whatever's worth taking", N),     # a setting, and no order
    ("talk", "don't run, whatever happens", N),       # a setting, and no order
    ("stay", "Stay put and keep an eye on the road.", S),
    ("follow", "With me, and somebody watch the rear.", F),
    ("go", "Get to the bank, the lot of you, and guard my pack there.", G),
]


def wanted(want: Any, who: str) -> str | None:
    return want.get(who, want.get("*")) if isinstance(want, dict) else want


def cases(lines: list[tuple]) -> list[dict[str, Any]]:
    out = []
    for group, line, want, *more in lines:
        for who in PARTY:
            out.append({"group": group, "line": line, "who": who, "want": wanted(want, who),
                        "at_table": group == "at the table", "waiting": more == ["waiting"], "away": more == ["away"],
                        "before": more[0] if more and isinstance(more[0], list) else []})
    return out


def taken(row: dict[str, Any], cut: float, ask_cut: float = companion.ASK_CUT) -> str | None:
    """The order a companion takes from the answer, as it does live (companion.order_in): the likeliest at the cut,
    or, when the answer is sure of an order to move and unsure which, sent or come along by their shares. With no
    order in it, whether it was asked for something all the same (companion.left_over), which it then answers; a
    line that speaks to one of its settings goes to that setting's question, which says what it owes."""
    order = companion.order_in(row["probabilities"], cut)[0]
    if order or row.get("settings"):
        return order or SET
    return C if companion.left_over(row["probabilities"], row.get("asked_p", 0.0), ask_cut) else None


async def ask(judge: Any, case: dict[str, Any]) -> dict[str, Any]:
    others = [n for n in PARTY if n != case["who"]]
    doing = "sitting at the table with the party" if case["at_table"] else \
        f"waiting where {LEADER} said to" if case["waiting"] else \
        f"waiting at the inn for {LEADER}" if case["away"] else f"following {LEADER}"
    state = companion.order_state(case["line"], case["who"], LEADER, others, doing, case["before"])
    # As a companion asks it: the question on its settings rides in the same request (standing.which_question).
    qs = {"order": companion.order_question(case["who"], LEADER, case["at_table"])}
    if not case["at_table"]:
        qs["setting"] = standing.which_question(case["who"], LEADER, case["who"] == "Isolde")
    err = ""
    for attempt in range(3):
        try:
            with costs.kind("order"):
                # ...and whether it asks for something, a request of its own sent beside it (as a companion that
                # talks sends it; not at the table).
                jobs = [judge.ask(state, qs)]
                if not case["at_table"]:
                    jobs.append(judge.ask(state, {"asked": companion.asked_question(case["who"], LEADER)}))
                a, *b = await asyncio.gather(*jobs)
            c = a.choices["order"]
            which = standing.Standing(caster=case["who"] == "Isolde").spoken_to(a.choices["setting"].probabilities) \
                if "setting" in a.choices else []
            return {"pick": c.choice, "p": c.probabilities.get(c.choice, c.confidence), "settings": which,
                    "asked_p": round(float(b[0].nouls.get("asked", 0.0)), 3) if b else 0.0,
                    "asked_cost": b[0].cost if b else 0.0,
                    "probabilities": {k: round(v, 3) for k, v in c.probabilities.items()},
                    "latency_ms": a.latency_ms, "cost": a.cost, "input_tokens": a.input_tokens, "model": a.model}
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"[:300]
            await asyncio.sleep(1 + attempt)
    return {"error": err}


def score(rows: list[dict[str, Any]], cut: float, ask_cut: float = companion.ASK_CUT) -> dict[str, Any]:
    """rows: each a case with what was read ("got", after the cut for a model). A line with no order in it that
    speaks to a setting is right when no order was wanted, whether or not it was put down as a request."""
    groups: dict[str, list[int]] = {}
    missed = false = wrong = requests = 0
    bad = []
    for r in rows:
        got = r["got"] if "got" in r else taken(r, cut, ask_cut)
        if got == SET:
            got = r["want"] if r["want"] in (None, C) else None
        ok = got == r["want"]
        g = groups.setdefault(r["group"], [0, 0])
        g[0] += ok
        g[1] += 1
        if not ok:
            missed += r["want"] not in (None, C) and got in (None, C)
            false += r["want"] in (None, C) and got not in (None, C)
            wrong += r["want"] not in (None, C) and got not in (None, C)
            requests += (r["want"] == C) != (got == C) and r["want"] in (None, C) and got in (None, C)
            bad.append({k: r[k] for k in ("group", "line", "who", "want")} | {"got": got}
                       | ({"asked": r["asked_p"]} if "asked_p" in r else {})
                       | ({"probabilities": r["probabilities"]} if "probabilities" in r else {}))
    right = sum(g[0] for g in groups.values())
    return {"right": right, "of": len(rows), "pct": round(100 * right / max(1, len(rows)), 1),
            "by_group": {k: f"{a}/{b}" for k, (a, b) in groups.items()},
            "orders_missed": missed, "talk_taken_for_an_order": false, "one_order_for_another": wrong,
            "talk_and_requests_mixed_up": requests, "wrong": bad}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="jev,decision1", help="aliases or model ids, comma-separated")
    ap.add_argument("--cut", type=float, default=companion.ORDER_CUT)
    ap.add_argument("--sets", default="first,second,third,fourth,fifth,sixth,seventh")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    judges.load_dotenv(BRAIN / ".env")
    sets = {k: cases(v) for k, v in (("first", LINES), ("second", SECOND), ("third", THIRD), ("fourth", FOURTH),
                                     ("fifth", FIFTH), ("sixth", SIXTH), ("seventh", SEVENTH))
            if k in args.sets.split(",") and v}
    result: dict[str, Any] = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "cut": args.cut,
                              "lines": {k: len(v) // len(PARTY) for k, v in sets.items()}, "models": {}}

    # The phrases: what was there before, and what reads the lines when there is no model.
    result["models"]["phrases"] = {}
    for which, todo in sets.items():
        rows = [{**c, "got": companion.order_of(c["line"], c["who"], PARTY, at_table=c["at_table"])} for c in todo]
        result["models"]["phrases"][which] = score(rows, args.cut)

    for name in [m.strip() for m in args.models.split(",") if m.strip()]:
        judge = judges.make("jev", model=None if name == "jev" else name)
        result["models"][name] = {}
        try:
            for which, todo in sets.items():
                got = []
                for i in range(0, len(todo), 6):  # six questions at a time
                    got += await asyncio.gather(*(ask(judge, c) for c in todo[i:i + 6]))
                rows = [{**c, **g} for c, g in zip(todo, got) if "error" not in g]
                errors = [g["error"] for g in got if "error" in g]
                s = score(rows, args.cut)
                # The question on settings that rode along: few of these lines change a setting (those that speak
                # of looting; "stay close"), so the rest of what it names are lines that would go on to a
                # setting's own question for nothing.
                s["spoken_to_a_setting"] = sorted({f"{r['line']} ({', '.join(r['settings'])})" for r in rows if r.get("settings")})
                s["cuts"] = {str(cut): {k: v for k, v in score(rows, cut).items()
                                        if k in ("right", "orders_missed", "talk_taken_for_an_order", "one_order_for_another")}
                             for cut in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8)}
                s["ask_cuts"] = {str(cut): {k: v for k, v in score(rows, args.cut, cut).items()
                                            if k in ("right", "talk_and_requests_mixed_up")}
                                 for cut in (0.5, 0.6, 0.7, 0.8, 0.9)}
                # Every line with no order taken from it and no setting spoken to: what the yes/no gave.
                s["asked"] = [{"line": r["line"], "who": r["who"], "want": r["want"], "p": r["asked_p"]}
                              for r in rows if taken(r, args.cut, 2.0) is None and not r["at_table"]]
                lat = sorted(r["latency_ms"] for r in rows)
                s |= {"judge": judge.name, "served_by": sorted({r["model"] for r in rows}),
                      "latency_ms": {"median": round(statistics.median(lat)), "p90": round(lat[int(0.9 * (len(lat) - 1))])}
                      if lat else {},
                      "cost_usd_a_question": round(statistics.mean(r["cost"] for r in rows), 7) if rows else None,
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
    for name, by_set in result["models"].items():
        for which, s in by_set.items():
            print(f"\n{name}, {which} set: {s['right']}/{s['of']} right ({s['pct']}%)  missed {s['orders_missed']}, talk taken "
                  f"for an order {s['talk_taken_for_an_order']}, one order for another {s['one_order_for_another']}")
            print("  " + "  ".join(f"{k} {v}" for k, v in s["by_group"].items()))
            if "cuts" in s:
                print("  by cut: " + "  ".join(f"{c}: {v['right']}" for c, v in s["cuts"].items()))
                print(f"  {s['latency_ms']} ms, ${s['cost_usd_a_question']} a question, {s['input_tokens']} input tokens, "
                      f"{s['errors']} errors")
            for b in s["wrong"]:
                print(f"    {b['group']:13} {b['who']:7} {b['line']!r}: wanted {b['want']}, got {b['got']}"
                      + (f"  {b['probabilities']}" if "probabilities" in b else ""))
            if s.get("spoken_to_a_setting"):
                print(f"  read as speaking to a setting: {s['spoken_to_a_setting']}")
    if args.out:
        print(f"\nresults: {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
