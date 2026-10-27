"""The leader's part of a party for the player, played without a person (cuo-cym.6).

  uv run python scripts/party_player.py [--fleet fleets/party-player.toml] [--out logs/fleet/RUN] [--windowed]
  uv run python scripts/party_player.py --fleet fleets/party-player-talk.toml --table     # and the table
  uv run python scripts/party_player.py --own-words                                       # orders as a person says them

A person's character has no brain: it walks where it is walked and attacks what it is told to. This plays
that part with walks, attacks and three lines of speech, and nothing an agent would do for it, so that what
the companions do beside it can be checked without someone at the keyboard:

1. starts the leader's client, logs it in (the fleet file's `leader`, on --account/--port), kits it as a
   warrior and puts it south of the Britain graveyard, in auto mode so that its own reflexes bandage it;
2. starts the fleet (the companions) and waits for them to be beside it, then invites each to the game's
   party as a person would from the party gump;
3. says "Follow me." and walks 18 tiles north; spawns a bone knight and three skeletons ahead, walks up and
   attacks one after another until they are dead (or 90 s);
4. says "Stay here.", walks 12 tiles off and back, then "Follow me." and walks back to the start;
   With --stranger ACCOUNT:PORT:NAME, before the leader's invitations another character with no brain logs
   in beside them and invites two companions to a party of its own: each should decline (gameparty.py).
5. with --table (a fleet that talks): says "Sit down, drinks are on me.", puts one line of its own to a
   companion by name half a minute in, and after two minutes says "Follow me." and walks on.

6. with --own-words (cuo-cym.15) the orders are said as a person might, in words no phrase of the old list
   matches ("Hold up a moment, I'll scout ahead."), with a line of talk that has an order's word in it on the
   way, and one more step: an order to one companion by name, who waits while the others come along. In a
   fleet that talks it then talks with them (cuo-cym.16): a question to one by name, another straight after
   the answer with no name in it, something it can't be ordered to do, and a question to nobody in particular.

7. with --playtest (cuo-cym.18 to .22), after the fight, what a person tried in the first five minutes of playing
   it (Harry, 2026-10-13), in his own lines where the log has them: "feel free to loot" and "Leave the rest."
   (loot decisions counted before, between and after); "Keep up, you lot." to companions who are following;
   "So crew how are you all today?" and "Shall we head to the graveyard? Yay or nay?" (who answered, and when);
   "cover me"; "Our archer, how many arrows have you left?" and "What about the rest of you, how are your
   supplies?"; then three skeletons and "Off to the inn with you, I'll deal with these." (who refuses), and in that
   fight two orders for the fight itself (cuo-cym.24): "Get the one on me!" four seconds in (which creature each went
   for, and when) and "Fall back!" at twenty, the leader running 8 tiles south (how far each was from it, second by
   second, whether each let go of what it fought, what each said, and its decisions' notes); then,
   with nothing about, "Right - off to the inn for you all - see you there", the leader standing where it is
   until they are there, and walking there after them; one sent back to the inn while the others come along,
   and called back in party chat. The other steps after the fight are left out.

Through all of it, once a second, it writes where the leader and each companion it can see are (walk.jsonl
in the run's folder), and at the end says: how far the furthest companion was while the leader walked,
whether they stayed when told and followed when told, who was in the game's party, and what the
companions' own logs say they fought and healed (`uo-brain party-report` has the rest).
"""

import argparse
import asyncio
import json
import os
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

BRAIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRAIN / "src"))

from uo_brain import client, fleet  # noqa: E402
from uo_brain.rpc import AgentRpc  # noqa: E402

START = (1392, 1528)
# --walls (cuo-cym.28): the street outside the Sweet Dreams inn's west wall in Britain, where the party starts
# (fleets/party-player-inn.toml), and the inn's ground floor 8 tiles east of it, out of its sight by the client's rule
# (further south a window in that wall lets the street see in). [AgentGo can't put anyone inside: it puts them at the
# land's height, under the inn's floor, where no step is possible.
OUTSIDE_THE_INN = (1488, 1616)
INN = (1496, 1618)
WAVE = (1392, 1500)
# What the leader says: the phrases a companion always knew, or (--own-words) the same orders as system one reads
# them. {name} is the second companion in the file.
WORDS = {
    "plain": {"follow": "Follow me.", "stay": "Stay here.", "back": "Follow me.", "table": "Sit down, drinks are on me.",
              "up": "Right. Follow me."},
    "own": {"follow": "Right, on we go. Keep up.", "talk": "You'd have stayed in bed if I'd let you.",
            "stay": "Hold up a moment, I'll scout ahead.", "back": "All clear. Come along, you lot.",
            "one stays": "{name}, hang back here a moment.", "one comes": "{name}, catch up!",
            "table": "Who fancies an ale? Take the weight off.", "up": "Right, we should get moving."},
}


async def say(r: AgentRpc, text: str, wait: float = 1.5) -> None:
    await r.call("act", verb="say", text=text, source="manual")
    await asyncio.sleep(wait)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fleet", type=Path, default=BRAIN / "fleets" / "party-player.toml")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--account", default="life1")
    ap.add_argument("--port", type=int, default=5661)
    ap.add_argument("--windowed", action="store_true")
    ap.add_argument("--stranger", help="ACCOUNT:PORT:NAME of a character that invites the companions first: they decline")
    ap.add_argument("--table", action="store_true", help="ask for the table at the end (a fleet with talk = true)")
    ap.add_argument("--playtest", action="store_true",
                    help="after the fight, what a person tried in the first five minutes of playing it (cuo-cym.18 to "
                         ".22): asks them to loot and to stop, speaks to all of them at once, sends them to the inn "
                         "with a fight on (they stay) and with none (they go) and walks there after them, sends "
                         "one back and calls it in party chat (a fleet that talks)")
    ap.add_argument("--own-words", action="store_true",
                    help="say the orders as a person might, in words the old phrases don't know (cuo-cym.15)")
    ap.add_argument("--range", action="store_true",
                    help="only this, after the invitations: the leader, as an archer, shoots one skeleton from 12 tiles "
                         "off; when each companion goes for it, and how far it was from the leader then (cuo-cym.25)")
    ap.add_argument("--foe", default="RatmanArcher",
                    help="with --range: the creature shot at (a type the server knows); one that stays at range, as a "
                         "ratman archer does, keeps it beyond the 4-tile rule (a skeleton charged and was within 4 in 2 s)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", dest="settings",
                    help="passed to the companions' fleet (e.g. --set judge_reach=false)")
    ap.add_argument("--walls", action="store_true",
                    help="only this, after the invitations (with fleets/party-player-inn.toml): the leader walks them into the "
                         "Sweet Dreams inn, says 'wait here', walks out into the street beyond the inn's wall and calls "
                         "them out aloud; who heard it, directly or passed on from the leader's client, and who came "
                         "(cuo-cym.28)")
    ap.add_argument("--fresh-words", action="store_true",
                    help="only this, after the invitations: what a new person might say, none of it from the earlier "
                         "playtests, each line with what it means written down before the run (cuo-cym.29): how close to "
                         "keep, how hard to fight, quiet and talk again, gold and no looting, questions to all and to one "
                         "by what it plays, two of them by name, somewhere and back, small talk, nothing at all")
    ap.add_argument("--errand", action="store_true",
                    help="only this, after the invitations: one companion sent by name to buy 10 bandages at the healer "
                         "and come back (cuo-cym.27); what it bought, what it said, how soon it was back")
    args = ap.parse_args()
    fl = fleet.load(args.fleet)
    names = [a.name for a in fl.agents]
    words = {k: v.format(name=names[1]) for k, v in WORDS["own" if args.own_words else "plain"].items()}
    start = OUTSIDE_THE_INN if args.walls else START
    out = args.out or BRAIN / "logs" / "fleet" / f"{time.strftime('%Y%m%d-%H%M%S')}-{fl.name}"
    out.mkdir(parents=True, exist_ok=True)
    log = (out / "walk.jsonl").open("a")
    began = time.time()

    def note(event: str, **data) -> None:
        print(f"{time.time() - began:6.1f} s  {event} {json.dumps(data) if data else ''}", flush=True)
        log.write(json.dumps({"t": time.time(), "event": event, **data}) + "\n")
        log.flush()

    proc = client.start(args.port, out / f"{fl.leader}.client.log", headless=not args.windowed)
    r = AgentRpc(port=args.port)
    await r.connect(attempts=90)
    res = await client.log_in(r, args.account, args.account, fl.leader, fl.host, fl.server_port, create=client.warrior(fl.leader))
    note("leader in", result=res)
    if not res.startswith("in game"):
        sys.exit(f"the leader isn't in game: {res}")
    await r.call("mode", mode="off")
    await r.call("goal", clear=True)
    await r.call("strategy", clear=True)
    for cmd in ("[AgentDisrupt restore quiet", "[AgentReset", f"[AgentGo {start[0]} {start[1]}",
                "[AgentDisrupt despawn 1384 1492 36 15 quiet", "[AgentKit warrior", "[AgentSupplies bandages 150 heal 10"):
        # Heal potions too, which its reflexes drink when low, as a person would: with bandages alone the leader
        # died in its first fight in two playtests of eight (2026-10-14, 95 to 0 in four or five seconds), and a
        # ghost's speech reaches nobody, so the rest of the run was void.
        await say(r, cmd)
    await r.call("mode", mode="auto")  # its own reflexes (bandages) and walking, as a person would see to

    companions = subprocess.Popen([sys.executable, "-m", "uo_brain.cli", "fleet", str(args.fleet), "--out", str(out),
                                   *(["--windowed"] if args.windowed else []),
                                   *[x for kv in args.settings for x in ("--set", kv)]], cwd=BRAIN,
                                  stdout=(out / "fleet.out").open("w"), stderr=subprocess.STDOUT)
    samples: list[dict] = []
    phase = "gathering"

    async def look() -> dict:
        snap = await r.call("snapshot", since=0)
        seen = {m["name"]: m["distance"] for m in snap.get("mobiles") or [] if m.get("name") in names and not m.get("dead")}
        rec = {"t": time.time(), "phase": phase, "at": [snap["player"]["x"], snap["player"]["y"]], "hits": snap["player"]["hits"],
               "seen": seen, "walking": (snap["agent"].get("travel") or {}).get("state") == "walking",
               "party": [m.get("name") for m in (snap.get("party") or {}).get("members") or []],
               "hostile": sum(1 for m in snap.get("mobiles") or [] if m.get("monster") and not m.get("dead") and m.get("war_mode"))}
        samples.append({**rec, "snap": None})
        log.write(json.dumps(rec) + "\n")
        log.flush()
        return snap

    async def wait(seconds: float, until=None) -> dict:
        t0 = time.monotonic()
        snap = await look()
        while time.monotonic() - t0 < seconds and not (until and until(snap)):
            await asyncio.sleep(1.0)
            snap = await look()
        return snap

    def beside(snap: dict, within: int = 6) -> list[str]:
        return [m["name"] for m in snap.get("mobiles") or [] if m.get("name") in names and not m.get("dead")
                and m["distance"] <= within]

    def places(snap: dict) -> dict[str, tuple[int, int]]:
        """Where each companion in view stands, by the leader's own view of it."""
        p = snap["player"]
        return {m["name"]: (p["x"] + m.get("dx", 0), p["y"] + m.get("dy", 0)) for m in snap.get("mobiles") or []
                if m.get("name") in names and not m.get("dead")}

    def moved(before: dict, after: dict) -> dict[str, int]:
        """How many tiles each has moved between two looks. Who is near the leader says nothing of who moved: one
        standing where the leader walks to is beside it without a step, and was counted as not having stayed."""
        return {n: max(abs(after[n][0] - at[0]), abs(after[n][1] - at[1])) for n, at in before.items() if n in after}

    async def walk(x: int, y: int, timeout: float = 40) -> None:
        await r.call("act", verb="travel", x=x, y=y, distance=1, pace="walk", source="manual")
        await asyncio.sleep(1.0)
        await wait(timeout, lambda s: (s["agent"].get("travel") or {}).get("state") != "walking")

    snap = await wait(240, lambda s: len(beside(s)) == len(names))
    note("companions beside the leader", who=beside(snap), of=names)

    stranger = {}
    if args.stranger:
        # An invitation from somebody who isn't the party's leader: a companion declines it.
        acct, sport, sname = args.stranger.split(":")
        # The game's party outlives a logout on ModernUO: companions still in the leader's party from an earlier
        # run can't be invited by anyone ("already in a party"), so the leader disbands it first.
        if (snap.get("party") or {}).get("members"):
            res = await r.call("act", verb="party", kind="leave", source="manual")
            await wait(4)
            snap = await look()
            note("the leader disbanded its old party", result=res.get("status"),
                 members_now=[m.get("name") for m in (snap.get("party") or {}).get("members") or []])
        sproc = client.start(int(sport), out / f"{sname}.stranger.client.log", headless=not args.windowed)
        sr = AgentRpc(port=int(sport))
        await sr.connect(attempts=90)
        res = await client.log_in(sr, acct, acct, sname, fl.host, fl.server_port, create=client.warrior(sname))
        note("the stranger is in", who=sname, result=res)
        await sr.call("goal", clear=True)
        await sr.call("mode", mode="auto")  # the invitation's target cursor is answered by the client's agent
        for cmd in ("[AgentReset", f"[AgentGo {START[0] + 1} {START[1] + 3}"):
            await say(sr, cmd)
        await wait(3)
        ssnap = await sr.call("snapshot", since=0)
        asked = []
        for m in [m for m in ssnap.get("mobiles") or [] if m.get("name") in names][:2]:
            res = await sr.call("act", verb="party", kind="invite", target=m["serial"], source="manual")
            asked.append(m["name"])
            note("the stranger invited", who=m["name"], result=res.get("status"), detail=res.get("detail"))
            await wait(7)
        ssnap = await sr.call("snapshot", since=0)
        joined_it = [m.get("name") for m in (ssnap.get("party") or {}).get("members") or [] if m.get("name") != sname]
        told = [e.get("text") for e in ssnap.get("journal") or [] if "party" in str(e.get("text", "")).lower()][-6:]
        stranger = {"invited": asked, "joined_its_party": joined_it, "what_it_was_told": told}
        note("after the stranger's invitations", **stranger)
        await sr.call("mode", mode="off")
        await sr.close()
        client.stop(sproc)

    # The game's own party, as a person makes it: an invitation each.
    for m in [m for m in snap.get("mobiles") or [] if m.get("name") in names]:
        res = await r.call("act", verb="party", kind="invite", target=m["serial"], source="manual")
        note("invited", who=m["name"], result=res.get("status"), detail=res.get("detail"))
        await wait(4)
    snap = await wait(8)
    note("the game's party", members=[m.get("name") for m in (snap.get("party") or {}).get("members") or []])

    def logged(kind: str, since: float = 0.0, **match) -> dict[str, list[dict]]:
        """Each companion's own log records of a kind since a time, as they stand now (the brains write as they go)."""
        found: dict[str, list[dict]] = {}
        for n in names:
            path = out / f"{n}.jsonl"
            for line in path.read_text().splitlines() if path.exists() else []:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") == kind and d.get("t", 0) >= since and all(d.get(k) == v for k, v in match.items()):
                    found.setdefault(n, []).append(d)
        return found

    def decisions(since: float) -> dict[str, list[dict]]:
        """Each companion's fight decisions since a time, from its own decisions log."""
        found: dict[str, list[dict]] = {}
        for n in names:
            path = out / f"{n}.decisions.jsonl"
            for line in path.read_text().splitlines() if path.exists() else []:
                if '"decision"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") == "decision" and d.get("t", 0) >= since:
                    found.setdefault(n, []).append(d)
        return found

    async def tell(text: str, wait_s: float, until=None, party_chat: bool = False) -> list[dict]:
        """Say a line as the leader and listen: what each companion said after it, and how long after."""
        t0, seq = time.monotonic(), (await look()).get("journal_seq", 0)
        if party_chat:
            await r.call("act", verb="party", kind="say", text=text, source="manual")
        else:
            await say(r, text, 0)
        answers: list[dict] = []
        while time.monotonic() - t0 < wait_s and not (until and until(answers)):
            await asyncio.sleep(1.0)
            snap = await look()
            for e in snap.get("journal") or []:
                if e.get("seq", 0) > seq and e.get("source") == "speech" and e.get("name") in names \
                        and e.get("type", "regular") in ("regular", "yell", "whisper", "party"):
                    answers.append({"who": e["name"], "text": e.get("text"), "after_s": round(time.monotonic() - t0, 1)})
            seq = max(seq, snap.get("journal_seq", seq))
        note("said", said=text, answers=answers)
        return answers

    if args.range:
        # cuo-cym.25: the leader shoots from range. The escort rule took a creature for the leader's fight only within 4
        # tiles of it; system one is asked about one further off.
        phase = "range"
        for cmd in ("[AgentKit archer", "[AgentSupplies arrows 300"):
            await say(r, cmd)
        await wait(3)
        foe = (START[0], START[1] - 12)
        before = {m["serial"] for m in (await look()).get("mobiles") or []}
        await say(r, f"[AgentDisrupt strong {foe[0]} {foe[1]} {args.foe} 1 quiet")

        def new_foes(s: dict) -> list:
            return [m for m in s.get("mobiles") or [] if m.get("monster") and not m.get("dead") and m["serial"] not in before]

        snap = await wait(10, lambda s: bool(new_foes(s)))
        skel = min(new_foes(snap), key=lambda m: m["distance"], default=None)
        ranged: dict = {"foe": args.foe, "seen": (skel or {}).get("name")}
        if skel is not None:
            # The leader stands and shoots: its client walked up to what it attacked (10 tiles in 3 s), and the companions
            # following it were then beside the creature whatever the rule said.
            await r.call("mode", authority={"move": "off"})
            t0 = time.time()
            ranged["from_the_leader_at_the_first_shot"] = skel["distance"]
            await r.call("act", verb="attack", target=skel["serial"], source="manual")
            gaps: list[tuple[float, int]] = []
            while time.time() - t0 < 60:
                snap = await look()
                m = next((m for m in snap.get("mobiles") or [] if m["serial"] == skel["serial"] and not m.get("dead")), None)
                if m is None:
                    break
                gaps.append((time.time() - t0, m["distance"]))
                if snap["agent"].get("engaged") != skel["serial"]:
                    await r.call("act", verb="attack", target=skel["serial"], source="manual")
                await asyncio.sleep(1.0)
            ranged["dead_or_gone_after_s"] = round(time.time() - t0, 1)
            await r.call("mode", authority={"move": "auto"})

            def gap_at(t: float) -> int | None:
                return next((g for s_, g in gaps if s_ >= t), gaps[-1][1] if gaps else None)

            went = {}
            for n, ds in decisions(t0).items():
                first = next((d for d in ds if any(a.get("target") == skel["serial"] for a in d.get("actions") or [])), None)
                if first is not None:
                    went[n] = {"after_s": round(first["t"] - t0, 1), "foe_from_the_leader": gap_at(first["t"] - t0),
                               "intent": first.get("intent")}
            ranged["companions_went_for_it"] = went
            ranged["judged"] = {n: [{k: d.get(k) for k in ("theirs", "beyond_the_rule")} for d in ds][:3]
                                for n, ds in logged("reach", t0).items()}
            ranged["the_rule_would_have_it_at_s"] = next((round(s_, 1) for s_, g in gaps if g <= 4), None)
        note("the leader shot from range", **ranged)
        companions.send_signal(signal.SIGINT)
        try:
            companions.wait(45)
        except subprocess.TimeoutExpired:
            companions.kill()
        await r.call("mode", mode="off")
        await say(r, "[AgentDisrupt restore quiet")
        await r.close()
        client.stop(proc)
        print(json.dumps({"run": out.name, "settings": args.settings, "range": ranged}, indent=1))
        return

    if args.walls:
        # cuo-cym.28: ModernUO sends speech only to those in line of sight of the speaker, so a line said aloud in the
        # street doesn't reach companions inside the inn 6 tiles off. Their brains pass it on from the leader's client.
        phase = "walls"
        await wait(4)
        await tell("Follow me, in we go.", 3)
        await walk(*INN, timeout=60)
        snap = await wait(20, lambda s: len(beside(s, 3)) == len(names))
        went_in = beside(snap, 3)
        stay = await tell("Wait here a moment, I'll be right back.", 8)
        t_out = time.time()
        await walk(*OUTSIDE_THE_INN, timeout=60)
        snap = await wait(3)
        sight = {}
        for m in snap.get("mobiles") or []:
            if m.get("name") in names:
                seen = await r.call("sight", serial=m["serial"])
                sight[m["name"]] = {"tiles": m["distance"], "in_sight": seen.get("mobile")}
        stayed = [n for n in names if n in sight and sight[n]["tiles"] >= 4]
        line = "Come on out here, all of you."
        t_call = time.time()
        answers = await tell(line, 30, lambda a: False)
        snap = await wait(1)
        read = {n: [d.get("order") for d in ds if d.get("text") == line] for n, ds in logged("order_read", t_call - 1).items()}
        relayed = {n: [d.get("after_s") for d in ds if d.get("line") == line]
                   for n, ds in logged("relay", t_call - 1, event="relayed").items()}
        walls = {"went_in_with_the_leader": went_in, "said_inside": stay, "stayed_inside": stayed, "where_they_were": sight, "line": line,
                 "read_by": {n: v for n, v in read.items() if v}, "passed_on_to": {n: v for n, v in relayed.items() if v},
                 "answers": answers, "came_out_within_3": beside(snap, 3), "walked_out_s": round(t_call - t_out, 1)}
        note("called from beyond the wall", **walls)
        companions.send_signal(signal.SIGINT)
        try:
            companions.wait(45)
        except subprocess.TimeoutExpired:
            companions.kill()
        await r.call("mode", mode="off")
        await r.close()
        client.stop(proc)
        print(json.dumps({"run": out.name, "settings": args.settings, "walls": walls}, indent=1))
        return

    if args.fresh_words:
        # cuo-cym.29: Harry's second playtest, played by script. The lines are what a new person might type, in
        # lower case and without care, written with what each means before the run (memory: a script asks what the
        # builder thought of, so these come from a player's side: settings, questions, names, places, nothing).
        phase = "fresh words"
        await say(r, "Follow me.")
        await walk(START[0], START[1] - 10)
        mage, archer, tamer = names[0], names[1], names[2]
        LINES = [
            ("stick close to me lads", "setting: keep close (distance)", 12),
            ("spread out a bit, youre crowding me", "setting: keep further off (distance)", 12),
            ("go easy, dont go chasing stuff", "setting: fight only what comes at us (reach/aggression)", 12),
            ("ok go hard, clear the place out", "setting: fight everything (aggression)", 12),
            ("shh keep it down for a bit", "setting: talk off; nobody answers, or one word", 12),
            ("ok you can talk again", "setting: talk on", 12),
            ("grab any gold you see", "setting: loot gold", 12),
            ("leave the loot", "setting: loot nothing", 12),
            ("anyone hurt?", "a question to all: each answers, none is an order", 25),
            ("tamer, hows the bear?", f"a question to {tamer} by what she plays: {tamer} answers", 20),
            (f"{mage} and {archer} wait here", f"order to two by name: {mage} and {archer} stay; {tamer} comes along", 8),
            ("WALK", "the leader walks 12 tiles off: who stayed", 0),
            ("everyone with me", "order to all: follow", 12),
            ("go wait for me at the healer", "go somewhere (the nearest healer), each agreeing or not", 30),
            ("actually nah, come back", "call back: those who went come back to the leader", 45),
            ("what are you lot up to?", "a question to all: each answers", 25),
            ("cheers guys, good work today", "talk, no order: thanks, at most a word back", 15),
            ("lol", "nothing: no order, no reading as one", 10),
        ]
        fresh = []
        for line, meant, wait_s in LINES:
            t0 = time.time()
            if line == "WALK":
                stood = places(await look())
                here = (await look())["player"]
                await walk(here["x"] + 12, here["y"])
                snap = await wait(4)
                steps = moved(stood, places(snap))
                fresh.append({"line": "(the leader walks 12 tiles east)", "meant": meant, "tiles_each_moved": steps,
                              "stayed": [n for n, d in steps.items() if d <= 2], "came": [n for n, d in steps.items() if d >= 6]})
                note("fresh words: walked off", **fresh[-1])
                continue
            answers = await tell(line, wait_s)
            await asyncio.sleep(2.0)
            reads = {}
            for kind in ("order_read", "setting_read", "place_read", "whom_read"):
                for n, ds in logged(kind, t0 - 0.5).items():
                    for d in ds:
                        reads.setdefault(n, []).append({"kind": kind, **{k: v for k, v in d.items()
                                                                         if k not in ("t", "type", "from", "state")}})
            orders = {n: [{k: d.get(k) for k in ("order", "place", "refused", "why", "said", "arrived") if d.get(k) is not None}
                          for d in ds] for n, ds in logged("order", t0 - 0.5).items()}
            snap = await look()
            fresh.append({"line": line, "meant": meant, "answers": [f"{a['who']} ({a['after_s']} s): {a['text']}" for a in answers],
                          "reads": reads, "orders": orders, "beside_after": beside(snap, 8)})
            note("fresh words", **{k: v for k, v in fresh[-1].items() if k != "reads"})
            if line == "go wait for me at the healer":
                # Give them the walk: those who agreed are on their way when "come back" is said.
                await wait(8)
            await asyncio.sleep(2.0)
        companions.send_signal(signal.SIGINT)
        try:
            companions.wait(45)
        except subprocess.TimeoutExpired:
            companions.kill()
        await r.call("mode", mode="off")
        await say(r, "[AgentDisrupt restore quiet")
        await r.close()
        client.stop(proc)
        (out / "fresh-words.json").write_text(json.dumps(fresh, indent=1))
        print(json.dumps({"run": out.name, "fresh_words": fresh, "party_report": f"uv run uo-brain party-report {out}"},
                         indent=1))
        return

    if args.errand:
        # cuo-cym.27: an errand at the leader's word, said as a person might. The others stay with the leader.
        phase = "errand"
        who = names[0]
        line = f"{who}, run to the healer and get me 10 bandages, then come back."
        t0 = time.time()
        await say(r, line, 2)
        snap = await wait(20, lambda s: who not in beside(s))
        left = time.time() - t0
        snap = await wait(360, lambda s: bool(logged("errand", t0).get(who)) and who in beside(s))
        back = [d for d in logged("errand", t0).get(who, [])]
        order = next((d for d in logged("order", t0).get(who, []) if d.get("errand")), None)
        errand = {"line": line, "who": who, "read_as": (order or {}).get("errand"), "said": (order or {}).get("said"),
                  "went_off_after_s": round(left, 1) if who not in beside(snap) or back else None,
                  "errand": [{k: d.get(k) for k in ("ok", "result", "said")} for d in back],
                  "back_beside_the_leader_after_s": round(time.time() - t0, 1) if back and who in beside(snap) else None,
                  "others_beside_the_leader": [n for n in beside(snap) if n != who]}
        note("the errand", **errand)
        companions.send_signal(signal.SIGINT)
        try:
            companions.wait(45)
        except subprocess.TimeoutExpired:
            companions.kill()
        await r.call("mode", mode="off")
        await say(r, "[AgentDisrupt restore quiet")
        await r.close()
        client.stop(proc)
        print(json.dumps({"run": out.name, "errand": errand, "party_report": f"uv run uo-brain party-report {out}"}, indent=1))
        return

    phase = "walking"
    await say(r, words["follow"])
    await walk(START[0], START[1] - 18)
    snap = await look()
    note("walked north", beside=beside(snap))
    if "talk" in words:
        await say(r, words["talk"], 3)  # talk with an order's word in it: nobody should stop for it

    phase = "fighting"
    for cmd in (f"[AgentDisrupt strong {WAVE[0]} {WAVE[1]} BoneKnight 1 quiet", f"[AgentDisrupt strong {WAVE[0]} {WAVE[1]} Skeleton 3 quiet"):
        await say(r, cmd)
    fight = time.monotonic()
    low = 100
    while time.monotonic() - fight < 90:
        snap = await look()
        p = snap["player"]
        low = min(low, round(100 * p["hits"] / max(1, p["hits_max"])))
        if p.get("dead"):
            note("the leader died")
            break
        hostile = sorted((m for m in snap.get("mobiles") or [] if m.get("monster") and not m.get("dead")
                          and not m.get("pet") and m["distance"] <= 14), key=lambda m: m["distance"])
        if not hostile and time.monotonic() - fight > 6:
            break
        if hostile and snap["agent"].get("engaged") != hostile[0]["serial"]:
            await r.call("act", verb="attack", target=hostile[0]["serial"], source="manual")  # the person's click
        await asyncio.sleep(1.0)
    note("the fight is over", seconds=round(time.monotonic() - fight), leader_lowest_health_pct=low)

    def fight_orders_done(meant: dict, fell_back: dict) -> dict:
        """What each companion made of the leader's orders for the fight (cuo-cym.24): how it took each (its
        fight_order records: the creature, its word, when the order ended and why), and what its fight decisions did
        after (whom they went for, whether they let go, their notes), with how far each was from the leader."""
        done: dict = {}

        def taken(kind: str, since: float) -> dict[str, list[dict]]:
            return {n: [{k: d[k] for k in ("order", "target", "name", "said", "none", "ended", "why", "after_s") if k in d}
                        for d in ds if kind in (d.get("order"), d.get("ended"))]
                    for n, ds in logged("fight_order", since - 1).items()}

        if meant:
            t = meant["t"]
            ds = decisions(t)
            done["target"] = {
                "said": meant["said"], "the_one_on_the_leader": {k: meant[k] for k in ("serial", "name", "tiles")},
                "taken": taken("attack", t),
                "first_went_for_it_after_s": {n: next((round(d["t"] - t, 1) for d in v if d.get("target") == meant["serial"]),
                                                      None) for n, v in ds.items()},
                "notes": {n: [d.get("note") for d in v[:6]] for n, v in ds.items()}}
        if fell_back:
            t = fell_back["t"]
            ds = decisions(t)
            done["fall_back"] = {
                **{k: fell_back[k] for k in ("from", "companions_tiles_away", "companions_tiles_away_each_second", "said")},
                "taken": taken("fall_back", t),
                "let_go": {n: any(a.get("verb") == "disengage" for d in v for a in d.get("actions") or []) for n, v in ds.items()},
                "notes": {n: [d.get("note") for d in v[:6]] for n, v in ds.items()}}
        return done

    played: dict = {}
    stayed: list[str] = []
    if args.playtest:
        # What a person did in the first five minutes of playing it (Harry, 2026-10-13), in his own lines where
        # the log has them: asked them to loot, sent them to the inn to wait for him, spoke to all of them at once.
        phase = "playtest"
        await wait(4)
        everyone = lambda answers: {a["who"] for a in answers} >= set(names)  # noqa: E731
        # Looting: off until told, on at a word, off again at another.
        t_on = time.time()
        said = await tell("feel free to loot", 20)
        t_off = time.time()
        said_off = await tell("Leave the rest.", 10)
        await wait(6)
        loots = {n: [d["t"] for d in ds if d.get("intent") == "loot"] for n, ds in
                 {n: [json.loads(x) for x in (out / f"{n}.decisions.jsonl").read_text().splitlines() if '"decision"' in x]
                  for n in names if (out / f"{n}.decisions.jsonl").exists()}.items()}
        played["looting"] = {
            "said_on_being_told_to": said, "said_on_being_told_to_stop": said_off,
            "settings_changed": {n: [f"{d['setting']}: {d['was']} -> {d['now']}" for d in ds]
                                 for n, ds in logged("setting", t_on - 1).items()},
            "loot_decisions_before_being_told": {n: sum(1 for t in ts if t < t_on) for n, ts in loots.items()},
            "loot_decisions_while_allowed": {n: sum(1 for t in ts if t_on <= t < t_off + 2) for n, ts in loots.items()},
            "loot_decisions_after_being_told_to_stop": {n: sum(1 for t in ts if t >= t_off + 4) for n, ts in loots.items()}}
        note("looting", **played["looting"])

        # An order already in force gets a word; a line to all of them an answer from each; one they can't
        # carry out isn't promised; a line for the others isn't taken by the one who just spoke.
        t_talk = time.time()
        talk = {"an order already in force": await tell("Keep up, you lot.", 8),
                "to all of them": await tell("So crew how are you all today?", 30, everyone),
                "a yes or no for each": await tell("Shall we head to the graveyard? Yay or nay?", 30, everyone),
                "something they can't do": await tell("cover me", 14),
                "by what one plays": await tell("Our archer, how many arrows have you left?", 14, lambda a: bool(a)),
                "to the others": await tell("What about the rest of you, how are your supplies?", 22, lambda a: len(a) >= 2)}
        delays = sorted(d["delay_s"] for ds in logged("talk", t_talk, said=True).values() for d in ds if d.get("delay_s") is not None)
        played["talk"] = {k: [f"{a['who']} ({a['after_s']} s): {a['text']}" for a in v] for k, v in talk.items()}
        dropped = {n: [d["dropped"] for d in ds if d.get("dropped")] for n, ds in logged("talk", t_talk).items()}
        played["answers_to_the_leader"] = {
            "count": len(delays), "seconds_after_its_line": {"median": statistics.median(delays) if delays else None,
                                                             "min": min(delays, default=None), "max": max(delays, default=None)},
            "written_and_not_said": {n: v for n, v in dropped.items() if v}}
        note("talk", **played["answers_to_the_leader"])

        # Sent away with a fight on: going is theirs to agree to (Harry: "if I can persuade them"), and with
        # something on the party they don't. Three skeletons where the first wave stood; the same order a moment later.
        try:
            await say(r, f"[AgentDisrupt strong {WAVE[0]} {WAVE[1]} Skeleton 3 quiet")
            await wait(12, lambda s: any(m.get("monster") and not m.get("dead") and not m.get("pet") and m.get("war_mode")
                                         and m["distance"] <= 8 for m in s.get("mobiles") or []))
            t_fight = time.time()
            said = await tell("Off to the inn with you, I'll deal with these.", 8)
            t1 = time.monotonic()
            while time.monotonic() - t1 < 75:
                snap = await look()
                hostile = sorted((m for m in snap.get("mobiles") or [] if m.get("monster") and not m.get("dead")
                                  and not m.get("pet") and m["distance"] <= 14), key=lambda m: m["distance"])
                if snap["player"].get("dead") or (not hostile and time.monotonic() - t1 > 6):
                    break
                if hostile and snap["agent"].get("engaged") != hostile[0]["serial"]:
                    await r.call("act", verb="attack", target=hostile[0]["serial"], source="manual")
                await asyncio.sleep(1.0)
            orders = logged("order", t_fight - 1, order="go")
            played["sent_away_with_a_fight_on"] = {
                "said": said,
                "refused": sorted(n for n, ds in orders.items() if any(d.get("refused") for d in ds)),
                "went": sorted(n for n, ds in orders.items() if any(d.get("place") and not d.get("refused") for d in ds)),
                "how_willing": {n: [d.get("willing") for d in ds] for n, ds in logged("place_read", t_fight - 1).items()},
                "fight_seconds": round(time.monotonic() - t1), "beside_the_leader_after": beside(await look(), 8)}
            note("sent away with a fight on", **played["sent_away_with_a_fight_on"])
            await wait(6)
        except Exception as e:  # noqa: BLE001 - this step is the script's own: the run goes on without it
            note("the step with a fight on failed", error=f"{type(e).__name__}: {e}"[:200])

        # Sent to the inn without the leader, who stays where it is until they are there.
        t_sent = time.time()
        here = (await look())["player"]
        said = await tell("Right - off to the inn for you all - see you there", 6)
        went = logged("order", t_sent - 1, order="go")
        t0 = time.monotonic()
        while time.monotonic() - t0 < 150:
            arrived = {n: [d for d in ds if d.get("arrived")] for n, ds in logged("order", t_sent - 1, order="go").items()}
            if sum(1 for v in arrived.values() if v) >= len([n for n, ds in went.items() if any(d.get("place") for d in ds)]) > 0:
                break
            await wait(3)
            went = logged("order", t_sent - 1, order="go")
        sent = {n: next((d for d in ds if d.get("place")), None) for n, ds in went.items()}
        arrived = {n: round(next(d["t"] for d in ds if d.get("arrived")) - t_sent, 1)
                   for n, ds in logged("order", t_sent - 1, order="go").items() if any(d.get("arrived") for d in ds)}
        snap = await look()
        played["sent_to_the_inn"] = {
            "said": said, "went": {n: d["place"] for n, d in sent.items() if d},
            "refused": sorted(n for n, ds in went.items() if any(d.get("refused") for d in ds)),
            "no_place": sorted(n for n, ds in went.items() if any(d.get("none") for d in ds)),
            "arrived_after_s": arrived,
            "seconds_between_the_first_and_the_last_there": round(max(arrived.values()) - min(arrived.values()), 1) if arrived else None,
            "leader_moved_tiles": max(abs(snap["player"]["x"] - here["x"]), abs(snap["player"]["y"] - here["y"])),
            "still_beside_the_leader": beside(snap)}
        note("sent to the inn", **played["sent_to_the_inn"])
        place = next((d for d in sent.values() if d), None)
        if place:
            # The leader walks there: they are its again.
            t_came = time.time()
            await r.call("act", verb="travel", x=place["at"][0], y=place["at"][1], distance=2, pace="run", source="manual")
            await asyncio.sleep(1.0)
            await wait(90, lambda s: (s["agent"].get("travel") or {}).get("state") != "walking")
            snap = await wait(15, lambda s: len(beside(s, 4)) == len(names))
            played["the_leader_came"] = {
                "beside_within_4": beside(snap, 4),
                "fell_in": sorted(n for n, ds in logged("order", t_came, order="follow").items() if any(d.get("why") for d in ds))}
            note("the leader came to the inn", **played["the_leader_came"])

        # One sent back to the inn while the others come along; then called back from out of earshot, in party chat.
        if place:
            # In party chat: inside the inn a wall can stand between them, and on ModernUO speech needs line of sight.
            await tell("Follow me.", 3, party_chat=True)
            snap = await look()
            await walk(snap["player"]["x"] - 14, snap["player"]["y"], timeout=30)
            t_one = time.time()
            # In party chat as well: said aloud on the way out of the inn it reached nobody in one run (the leader
            # 5 to 7 tiles from each of them, walls between).
            one_said = await tell(f"{names[1]}, wait for me at the inn. The rest of you with me.", 5, party_chat=True)
            snap = await look()
            await walk(snap["player"]["x"] - 14, snap["player"]["y"], timeout=30)
            snap = await wait(10)
            with_leader = beside(snap, 6)
            went = {n: [d.get("place") or ("refused" if d.get("refused") else "already" if d.get("already") else "?")
                        for d in ds] for n, ds in logged("order", t_one - 1, order="go").items()}
            called = await tell(f"{names[1]}, come back to me.", 45, lambda a: False, party_chat=True)
            snap = await look()
            played["one_sent_back"] = {"said": one_said, "went": went, "beside_the_leader_meanwhile": with_leader,
                                       "said_on_being_called_back": called, "beside_after_being_called_back": beside(snap, 8)}
            note("one sent back to the inn", **played["one_sent_back"])

            # Orders for the fight itself (cuo-cym.24), in a fight of their own and last, so that a death in it spoils
            # nothing before: two bone knights 5 tiles north (the skeletons above are dead in about six seconds,
            # too soon for both orders). Three seconds in, the one on the leader to go for; at fourteen, a fall back,
            # the leader running 8 tiles south while the companions are watched coming to it.
            try:
                await wait(4)
                await say(r, "[AgentSpawn BoneKnight 2 5 n")
                await wait(15, lambda s: any(m.get("monster") and not m.get("dead") and not m.get("pet") and m.get("war_mode")
                                             and m["distance"] <= 8 for m in s.get("mobiles") or []))
                meant: dict = {}
                fell_back: dict = {}
                t1 = time.monotonic()
                while time.monotonic() - t1 < 75:
                    snap = await look()
                    hostile = sorted((m for m in snap.get("mobiles") or [] if m.get("monster") and not m.get("dead")
                                      and not m.get("pet") and m["distance"] <= 14), key=lambda m: m["distance"])
                    if snap["player"].get("dead") or (not hostile and time.monotonic() - t1 > 6):
                        break
                    if not meant and hostile and time.monotonic() - t1 >= 3:
                        on = next((m for m in hostile if m["distance"] <= 1 and m.get("war_mode")), hostile[0])
                        meant = {"serial": on["serial"], "name": on.get("name"), "t": time.time(), "tiles": on["distance"]}
                        meant["said"] = await tell("Get the one on me!", 6)
                        continue
                    if meant and not fell_back and time.monotonic() - t1 >= 14:
                        p0 = snap["player"]
                        fell_back = {"t": time.time(), "from": [p0["x"], p0["y"]], "seq": snap.get("journal_seq", 0),
                                     "companions_tiles_away": {n: m["distance"] for n, m in
                                                               ((m["name"], m) for m in snap.get("mobiles") or [])
                                                               if n in names and not m.get("dead")}}
                        await r.call("act", verb="disengage", source="manual")  # its travel waits while it is engaged
                        await say(r, "Fall back!", 0)
                        await r.call("act", verb="travel", x=p0["x"], y=p0["y"] + 8, distance=1, pace="run", source="manual")
                        trail = []
                        for _ in range(12):
                            await asyncio.sleep(1.0)
                            s2 = await look()
                            trail.append({n: m["distance"] for n, m in ((m["name"], m) for m in s2.get("mobiles") or [])
                                          if n in names and not m.get("dead")})
                        fell_back["companions_tiles_away_each_second"] = trail
                        seq0 = fell_back.pop("seq")
                        fell_back["said"] = [{"who": e["name"], "text": e.get("text")} for e in s2.get("journal") or []
                                             if e.get("seq", 0) > seq0 and e.get("source") == "speech" and e.get("name") in names]
                        continue
                    if hostile and snap["agent"].get("engaged") != hostile[0]["serial"]:
                        await r.call("act", verb="attack", target=hostile[0]["serial"], source="manual")
                    await asyncio.sleep(1.0)
                if meant or fell_back:
                    played["fight_orders"] = fight_orders_done(meant, fell_back)
                    note("orders for the fight", **played["fight_orders"])
            except Exception as e:  # noqa: BLE001 - this step is the script's own: the run goes on without it
                note("the step with orders for the fight failed", error=f"{type(e).__name__}: {e}"[:200])
    else:
        phase = "staying"
        await wait(5)
        await say(r, words["stay"], 3)
        snap = await look()
        stay_from, stood = snap["player"], places(snap)  # one that isn't here to be told (its client never logged in) didn't "stay"
        await walk(stay_from["x"] + 12, stay_from["y"])
        snap = await wait(4)
        steps = moved(stood, places(snap))
        stayed = [n for n, d in steps.items() if d <= 2]
        note(f"walked 12 tiles off after {words['stay']!r}", tiles_each_moved=steps, stayed=stayed)
        await walk(stay_from["x"], stay_from["y"])

        phase = "following"
        await say(r, words["back"], 3)
        await walk(*START)
        snap = await wait(5)
        note(f"walked back to the start after {words['back']!r}", beside=beside(snap))

    one = {}
    if "one stays" in words and not args.playtest:
        # An order to one of them by name, with all of them following again: that one waits, the others come along.
        phase = "one stays"
        await wait(3)
        await say(r, words["one stays"], 3)
        stood = places(await look())
        await walk(START[0] - 12, START[1])
        snap = await wait(4)
        steps = moved(stood, places(snap))
        one = {"told": names[1], "tiles_each_moved": steps, "stayed": [n for n, d in steps.items() if d <= 2],
               "came_along": [n for n, d in steps.items() if d >= 6]}
        note(f"walked 12 tiles off after {words['one stays']!r}", **one)
        await say(r, words["one comes"], 3)
        snap = await wait(15, lambda s: names[1] in beside(s))
        one["beside_after_being_called"] = beside(snap)
        note(f"after {words['one comes']!r}", beside=beside(snap))

    chat: list[dict] = []
    if args.own_words and any(a.talk for a in fl.agents) and not args.playtest:
        # Talking with them: what each is asked, who answered and how long after.
        phase = "talking"
        await wait(4)
        heard = (await look()).get("journal_seq", 0)

        async def ask(text: str, of: str | None = None, wait_s: float = 25) -> None:
            nonlocal heard
            t0 = time.monotonic()
            await say(r, text, 0)
            answers: list[dict] = []
            while time.monotonic() - t0 < wait_s and not any(of in (None, a["who"]) for a in answers):
                await asyncio.sleep(1.0)
                snap = await look()
                for e in snap.get("journal") or []:
                    if e.get("seq", 0) > heard and e.get("source") == "speech" and e.get("name") in names \
                            and e.get("type", "regular") in ("regular", "yell", "whisper"):
                        answers.append({"who": e["name"], "text": e.get("text"), "after_s": round(time.monotonic() - t0, 1)})
                heard = max(heard, snap.get("journal_seq", heard))
            chat.append({"said": text, "answers": answers})
            note("asked", said=text, answers=answers)
            await asyncio.sleep(2.0)

        first = names[0]
        await ask(f"{first}, how are you holding up?", first)
        # No name in it and inside the 20 s between a character's lines: it follows on from that one's answer.
        await ask("And what did you make of that bone knight?", first)
        await ask(f"{first}, run back to town and buy me some bandages, would you?", first)  # not something it does on command
        await ask("What did the rest of you make of it?")

    table = {}
    if args.table:
        phase = "table"
        await say(r, words["table"], 3)
        sat = time.monotonic()
        asked = False
        while time.monotonic() - sat < 120:
            await wait(5)
            if not asked and time.monotonic() - sat > 30:
                asked = True
                await say(r, f"{names[1]}, what would you do differently next time?")
        await say(r, words["up"], 3)
        phase = "following"
        await walk(START[0], START[1] - 8)
        snap = await wait(8)
        note("walked on after the table", beside=beside(snap))
        table["beside_after_it"] = beside(snap)

    # Ctrl-C, not a kill: the fleet closes the clients it started on its way out. Its brains are its children
    # and outlive it, so each is ended by its own process id.
    companions.send_signal(signal.SIGINT)
    try:
        companions.wait(45)  # the fleet asks its brains to stop first (each writes its summary), then closes its clients
    except subprocess.TimeoutExpired:
        companions.kill()
    for a in fl.agents:
        found = subprocess.run(["pgrep", "-f", f"uo_brain.cli --port {a.port} session"], capture_output=True, text=True).stdout
        for pid in found.split():
            try:
                os.kill(int(pid), signal.SIGTERM)
            except (ProcessLookupError, ValueError):
                pass
    await r.call("mode", mode="off")
    await say(r, "[AgentDisrupt restore quiet")
    await r.close()
    client.stop(proc)

    if args.table:
        # What the companions' own logs say of the table: who spoke, and what followed the leader's question.
        said = []
        for n in names:
            for line in (out / f"{n}.jsonl").read_text().splitlines() if (out / f"{n}.jsonl").exists() else []:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") == "table" and d.get("said"):
                    said.append((d["t"], n, d.get("text"), d.get("to")))
        said.sort()
        table |= {"lines": len(said), "speakers": {n: sum(1 for x in said if x[1] == n) for n in names},
                  "said": [f"{n}: {text}" + (f" (to {to})" if to else "") for _, n, text, to in said]}
    # How each companion read what the leader said (system one's reading of each line, cuo-cym.15), from its own log.
    read: dict[str, list[str]] = {}
    for n in names:
        for line in (out / f"{n}.jsonl").read_text().splitlines() if (out / f"{n}.jsonl").exists() else []:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") == "order_read":
                read.setdefault(d.get("text") or "", []).append(
                    f"{n}: {d.get('order') or 'no order'}" + (f" ({d['p']:.2f})" if "p" in d else f" ({d.get('error')})"))
    walking = [max(s["seen"].values(), default=None) for s in samples if s["phase"] in ("walking", "following") and s["walking"]]
    walking = [d for d in walking if d is not None]
    unseen = sum(1 for s in samples if s["phase"] in ("walking", "following") and s["walking"] and len(s["seen"]) < len(names))
    print(json.dumps({
        "run": out.name, "companions": names,
        "furthest_companion_while_the_leader_walked": {"median": statistics.median(walking) if walking else None,
                                                        "most": max(walking, default=None), "samples": len(walking),
                                                        "samples_with_one_out_of_view": unseen},
        **({"playtest": played} if played else {"stayed_when_told": stayed}),
        "leader_lowest_health_pct": low, **({"table": table} if args.table else {}),
        **({"one_told_to_stay": one} if one else {}), **({"talk_with_the_leader": chat} if chat else {}),
        **({"lines_as_read": read} if read else {}),
        **({"stranger": stranger} if args.stranger else {}),
        "party_report": f"uv run uo-brain party-report {out}"}, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
