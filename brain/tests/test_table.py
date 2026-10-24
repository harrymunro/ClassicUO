"""R&R at the inn (cuo-cod.3, .4, .5): the leader's goal, the turns round the table, each member's own story of
the hunt, and what each keeps of the evening."""

import asyncio
import copy
import random

from uo_brain import memory, party, planner, table
from uo_brain.session import Result
from uo_brain.world import World

from conftest import SNAPSHOT
from test_party import NAMES, Following
from test_planner import FakeSession, call, reply, scripted


class Rpc:
    def __init__(self):
        self.said = []

    async def call(self, method, **params):
        if method == "act" and params.get("verb") == "say":
            self.said.append((params["text"], params.get("kind")))
        return {"status": "done"}


def seat(w, name, chat, mem=True, **kw):
    p = party.Party(w, "p1", name, "Roland", NAMES)
    return table.Table(p, Rpc(), {"temperament": f"{name}'s own"}, None, memory.Memory(w, name) if mem else None,
                       chat_fn=chat, pace=False, **kw)


def talker(seen=None):
    """A model that says one line for whoever it speaks for, with an emote now and then and a lesson when asked."""
    n = {"i": 0}

    async def chat(messages, tools=None, **_):
        user = messages[1]["content"]
        if seen is not None:
            seen.append(messages)
        name = tools[0]["function"]["name"]
        if name == "keep":
            who = user.split("Who you are: ", 1)[1].split(".", 1)[0]
            return reply(call("keep", {"lessons": [f"{who} should stand further back from the lich at the graveyard."],
                                       "companions": ["Isolde nearly died on the lich and blames Wystan.",
                                                      "Nothing about anyone in particular."],
                                       "tactic": "Stand behind Roland when the lich casts."}))
        who = user.split("Who you speak for: ", 1)[1].split(".", 1)[0]
        n["i"] += 1
        args = {"line": f"{who} says line {n['i']}.", "to": "Roland" if who != "Roland" else "Isolde"}
        if n["i"] % 3 == 0:
            args["emote"] = "*has a swig of ale*"
        if "winding down" in user:
            args["lesson"] = f"{who} would do one thing differently."
        return reply(call("table_say", args))

    return chat


def test_the_evening_runs_a_toast_the_stories_the_banter_and_a_lesson_each():
    assert table.Table.plan(5) == ["toast"] + ["stories"] * 7 + ["banter"] * 7 + ["lessons"] * 5
    assert len(table.Table.plan(2)) == 12 and len(table.Table.plan(9)) == 30 and table.Table.plan(3, 14).count("lessons") == 3


def test_who_speaks_next():
    present = ["Roland", "Isolde", "Wystan"]
    rnd = random.Random(1)

    def line(who, to="", phase="stories"):
        return {"kind": "line", "who": who, "to_who": to, "phase": phase}

    nxt = table.Table.next_speaker
    # The one the last line was aimed at answers; never the same speaker twice running.
    assert nxt(present, [line("Roland", "Wystan")], "stories", rnd=rnd) == "Wystan"
    assert nxt(present, [line("Roland", "Roland")], "stories", rnd=rnd) in ("Isolde", "Wystan")
    # Aimed at one who has already said much more than the others: the one who has said least instead.
    said = [line("Wystan"), line("Isolde"), line("Wystan"), line("Isolde"), line("Wystan"), line("Roland", "Wystan")]
    assert nxt(present, said + [line("Isolde", "Wystan")], "banter", rnd=rnd) == "Roland"
    # The last round: whoever hasn't said their piece yet, in seating order; then nobody.
    assert nxt(present, said + [line("Roland", phase="lessons")], "lessons") == "Isolde"
    assert nxt(present, said + [line("Isolde")], "lessons") == "Roland"  # not Isolde straight after her own line
    assert nxt(present, [line(p, phase="lessons") for p in present], "lessons") is None
    # One who has stopped answering is passed over.
    assert nxt(present, [line("Roland", "Wystan")], "stories", skip={"Wystan"}, rnd=rnd) == "Isolde"


def test_a_table_is_held_with_everyone_speaking_from_their_own_brain(tmp_path, monkeypatch):
    w = World.open("local", root=tmp_path)
    seen = []
    chat = talker(seen)
    seats = {n: seat(w, n, chat, emotes=True) for n in ("Roland", "Isolde", "Wystan")}
    seats["Isolde"].story.low = (18, "the lich", ["Roland"])
    seats["Isolde"].story.fought = {"the lich": 20}
    seats["Isolde"].story.healed_by = {"Corwin": 2}

    async def go():
        sitting = [asyncio.create_task(seats[n].sit(wait_s=5)) for n in seats]
        held = await seats["Roland"].host(list(seats))
        return held, await asyncio.gather(*sitting)

    held, sat = asyncio.run(go())
    assert held["lines"] == 12 and sat == [1, 1, 1] and held["skipped"] == {}
    assert all(n >= 3 for n in held["speakers"].values()) and sum(held["speakers"].values()) == 12
    rows = seats["Roland"].rows(1)
    kinds = [r["kind"] for r in rows]
    assert kinds[0] == "open" and kinds[-1] == "close" and kinds.count("next") == 12 and "emote" in kinds
    lines = [r for r in rows if r["kind"] == "line"]
    assert [r["phase"] for r in lines] == table.Table.plan(3)
    assert all(a["who"] != b["who"] for a, b in zip(lines, lines[1:]))  # never the same twice running
    # Each spoke through its own client, an emote just before its line, and wrote from its own story.
    isolde = seats["Isolde"]
    assert isolde.spoken == held["speakers"]["Isolde"] and len([t for t, k in isolde.rpc.said if k is None]) == isolde.spoken
    assert all(k == "emote" and t.startswith("*") for t, k in isolde.rpc.said if k)
    hers = next(m for m in seen if "Who you speak for: Isolde" in m[1]["content"])[1]["content"]
    assert "At the worst of it they were nearly dead (18% of their health left), with the lich on them; Roland beside them then." in hers
    assert "Healed or bandaged by Corwin (2 times)" in hers and "At the table: Roland, Isolde, Wystan." in hers
    assert "don't hold back" in seen[0][0]["content"] and "Swearing is fine" in seen[0][0]["content"]
    # The last round asked each for what they would do differently.
    assert sum("winding down" in m[1]["content"] for m in seen) == 3
    assert seats["Roland"].current() is None  # closed
    w.close()


def test_one_who_doesnt_answer_is_passed_over_and_the_table_still_closes(tmp_path, monkeypatch):
    monkeypatch.setattr(table, "TURN_S", 1.0)
    w = World.open("local", root=tmp_path)
    chat = talker()
    roland, isolde = seat(w, "Roland", chat), seat(w, "Isolde", chat)

    async def go():
        sitting = [asyncio.create_task(s.sit(wait_s=5)) for s in (roland, isolde)]
        held = await roland.host(["Roland", "Isolde", "Wystan"])  # Wystan's brain never sits down
        await asyncio.gather(*sitting)
        return held

    held = asyncio.run(go())
    assert held["skipped"] == {"Wystan": 2} and held["speakers"]["Wystan"] == 0 and held["lines"] >= 8
    w.close()


def test_each_keeps_its_own_memories_of_the_evening_and_only_a_tactic_reaches_its_fights(tmp_path):
    w = World.open("local", root=tmp_path)
    chat = talker()
    roland, isolde = seat(w, "Roland", chat), seat(w, "Isolde", chat)

    async def go():
        sitting = [asyncio.create_task(s.sit(wait_s=5)) for s in (roland, isolde)]
        await roland.host(["Roland", "Isolde"])
        await asyncio.gather(*sitting)
        return await isolde.reflect(1, "Britain Graveyard"), await roland.reflect(1, "Britain Graveyard")

    kept, _ = asyncio.run(go())
    mine = memory.Memory(w, "Isolde").list(limit=20)
    assert len(kept) == 3 and len(mine) == 3
    lesson = next(m for m in mine if m["kind"] == "lesson" and "inn" in m["tags"] and "tactic" not in m["tags"])
    assert lesson["text"].startswith("From the inn after the hunt") and lesson["importance"] == 4 \
        and lesson["area"] == "Britain Graveyard"
    # About a companion: kept as a relationship memory naming them (the one naming nobody is dropped).
    rel = next(m for m in mine if m["kind"] == "relationship")
    assert "wystan" in rel["tags"] and "Isolde nearly died" in rel["text"]
    # Private: Roland's are his own, and there is no shared note.
    assert len(memory.Memory(w, "Roland").list(limit=20)) == 3
    assert not w.notes(None, "inn", limit=5)
    # Its line on how to fight is what its next fights see; one about whether to fight never is.
    assert table.tactics(memory.Memory(w, "Isolde")) == ["Stand behind Roland when the lich casts."]
    memory.Memory(w, "Isolde").add("Leave the graveyard when the lich shows.", kind="lesson", tags=("tactic",), source="talk")
    assert table.tactics(memory.Memory(w, "Isolde")) == ["Stand behind Roland when the lich casts."]
    assert table.NOT_A_TACTIC.search("Never fight the lich again") and not table.NOT_A_TACTIC.search("Heal Roland first")
    w.close()


def test_a_story_of_the_hunt_in_plain_sentences():
    st = table.Story()
    assert st.words().startswith("Nothing much happened")
    st.fought = {"the lich": 14, "a skeleton": 3}
    st.low = (3, "the lich", [])
    st.deaths = 1
    st.fell = ["Thea", "Thea"]
    st.healed = {"Wystan": 1}
    st.ran = 2
    st.out_of = ["arrows"]
    st.called = {"badly_hurt": 2, "on_target": 5}
    st.dead_seen = ["a lich"]
    text = st.words()
    assert "They fought the lich, a skeleton." in text and "nearly dead (3% of their health left), with the lich on them; nobody beside" in text
    assert "They died 1 time." in text and "Companions who fell: Thea." in text and "They ran from the fight 2 times." in text
    assert "ran out of arrows" in text and "shouted that they were nearly dead (2x)" in text and "Dead at the end of it: a lich." in text
    st.reset()
    assert st.empty()


def test_the_leader_takes_the_party_to_the_inn_and_the_others_follow(tmp_path, monkeypatch):
    w = World.open("local", root=tmp_path)
    w.add_place("inn", "North Britain inn", 1458, 1525, source="modernuo")
    monkeypatch.setattr(table, "GATHER_S", 0.5)
    chat = talker()
    lead_party = party.Party(w, "p1", "Roland", "Roland", ["Roland", "Isolde"])
    lead_table = table.Table(lead_party, Rpc(), {}, None, memory.Memory(w, "Roland"), chat_fn=chat, pace=False)

    class Lead(FakeSession):
        async def defended(self, work):
            return await work

        async def find_inn(self):
            return w.find_place("inn", near=[1392, 1528], limit=1)[0]

        async def travel_to(self, place=None, x=None, y=None, distance=2, refuse=True, together=False):
            self.goals.append(("travel_to", place, x, y, together))
            self.distances.append(distance)
            return Result(True, "arrived")

    s = Lead(w)
    s.distances = []
    plan = planner.Planner(s, "hunt", chat_fn=scripted(reply(call("inn", {"why": "the lich is dead"}))), escalate=False,
                           party=lead_party, table=lead_table, memory=False)
    assert "inn" in {t["function"]["name"] for t in plan.tools}
    lead_party.inn = True
    assert "take them to the inn" in lead_party.section() and "never travel_to" in lead_party.section()

    # The follower: walks to the inn the leader named, sits, and keeps its own memories.
    fs = Following(w)
    f_party = party.Party(w, "p1", "Isolde", "Roland", ["Roland", "Isolde"])
    f_table = table.Table(f_party, Rpc(), {}, None, memory.Memory(w, "Isolde"), chat_fn=chat, pace=False)
    f = party.Follower(fs, f_party, table=f_table)
    at_inn = copy.deepcopy(SNAPSHOT)
    at_inn["player"].update(x=1457, y=1526)
    f_party.report(at_inn, "mage")

    async def go():
        following = asyncio.create_task(f.step_when_goal())
        step = await plan.step()
        await following
        return step

    async def step_when_goal():
        while f_party.latest() is None:
            await asyncio.sleep(0.05)
        await f.step()

    f.step_when_goal = step_when_goal
    step = asyncio.run(go())
    posted = f_party.latest()
    assert posted["tool"] == "inn" and posted["args"]["place"] == "North Britain inn" and posted["args"]["x"] == 1458
    assert step.result["ok"] and step.result["present"] == ["Roland", "Isolde"] and step.result["lines"] == 12
    assert ("travel_to", "North Britain inn", 1458, 1525, True) in s.goals  # a trip the party makes together
    assert s.distances == [0]   # onto the innkeeper's own tile: two tiles off can be outside the wall
    assert fs.goals[0] == ("travel_to", "North Britain inn", 3)
    # ...and the follower comes in after it: nearer and nearer until the leader is in sight (never, in this fake).
    assert fs.goals[1:3] == [("travel_to", (1458, 1525), 2), ("travel_to", (1458, 1525), 1)]
    assert f_table.spoken == 6 and memory.Memory(w, "Isolde").count() == 2 and "at the inn" in f.doing
    assert planner.describe_goal("inn", posted["args"]) == "R&R: the party goes to North Britain inn to talk the hunt over"
    # Without a table (a party that doesn't talk) there is no inn goal.
    quiet = planner.Planner(Lead(w), "hunt", chat_fn=scripted(), escalate=False, party=lead_party, memory=False)
    assert "inn" not in {t["function"]["name"] for t in quiet.tools}
    w.close()


def test_emotes_are_kept_to_every_other_line_and_never_done_twice(tmp_path):
    w = World.open("local", root=tmp_path)

    def always(emote):
        async def chat(messages, tools=None, **_):
            chat.seen.append(messages[1]["content"])
            return reply(call("table_say", {"line": "Another round, and " + "very " * 30 + "long.", "emote": emote}))
        chat.seen = []
        return chat

    chat = always("sets her cup down with a pointed click")
    t = seat(w, "Isolde", chat, emotes=True)
    t.add(1, "open", '["Roland", "Isolde"]', who="Roland")
    for _ in range(4):
        asyncio.run(t.speak(1, {"phase": "banter"}))
    emotes = [text for text, kind in t.rpc.said if kind == "emote"]
    assert emotes == ["*sets her cup down with a pointed click*"]  # once: not on the next line, and not the same again
    assert "has done tonight already: sets her cup down with a pointed click" in chat.seen[-1]
    lines = [text for text, kind in t.rpc.said if kind is None]
    assert all(len(ln) <= table.LINE_CAP + 3 and not ln.endswith("ver") for ln in lines)
    # A table without roleplay: no emotes at all.
    plain = seat(w, "Roland", always("drinks"), emotes=False)
    asyncio.run(plain.speak(1, {"phase": "toast"}))
    assert all(kind is None for _, kind in plain.rpc.said)
    w.close()


def test_at_the_table_the_ears_listen_and_the_turns_do_the_talking(tmp_path):
    w = World.open("local", root=tmp_path)
    t = seat(w, "Isolde", talker())

    class Ears:
        paused = False
        seen = []

    t.ears = Ears()
    t.add(1, "open", '["Roland", "Isolde"]', who="Roland")

    async def go():
        sitting = asyncio.create_task(t.sit(wait_s=2))
        await asyncio.sleep(0.4)
        Ears.seen.append(t.ears.paused)
        t.add(1, "close", who="Roland")
        await sitting

    asyncio.run(go())
    assert Ears.seen == [True] and t.ears.paused is False
    w.close()


def test_a_follower_waits_for_the_table_longer_than_the_leader_gathers():
    """With one member missing the leader gathered for 120 s and the others had stopped waiting at 90: it
    opened a table for nobody."""
    assert party.TABLE_WAIT_S > table.GATHER_S + 30


def test_one_who_was_never_hurt_doesnt_boast_of_a_hundred_percent_low():
    st = table.Story()
    st.fought = {"the lich": 4}
    st.low = (100, "", ["Roland"])
    assert "They were not hurt at all." in st.words() and "100" not in st.words()


def test_a_line_sent_as_a_list_or_written_like_one_is_read_as_the_line():
    assert table.clean_line('["Hold the front with the shield."][0]') == "Hold the front with the shield."
    assert table.clean_line(["Hold the front.", "Second"]) == "Hold the front."
    assert table.clean_line("  Hold   the front. ") == "Hold the front." and table.clean_line(None) == ""
    assert table.TRAILING.sub("", "tears a corner off a honey cake and passes it down to") == \
        "tears a corner off a honey cake and passes it down"


def test_one_who_sits_down_after_the_table_opened_is_given_turns_and_a_line_on_tomorrow(tmp_path):
    # The hamlet's evenings of 2026-10-24: Brannoc came to two tables after they had opened, sat through each and was
    # never named, as the table's turns went only to those there when it opened.
    w = World.open("local", root=tmp_path)
    chat = talker()
    roland, isolde, wystan = (seat(w, n, chat) for n in ("Roland", "Isolde", "Wystan"))

    async def go():
        sitting = [asyncio.create_task(s.sit(wait_s=5)) for s in (roland, isolde)]
        hosting = asyncio.create_task(roland.host(["Roland", "Isolde"]))
        await asyncio.sleep(0.3)  # Wystan comes in once the table is open
        late = asyncio.create_task(wystan.sit(wait_s=5))
        held = await hosting
        await asyncio.gather(*sitting, late)
        return held

    held = asyncio.run(go())
    assert held["speakers"]["Wystan"] >= 1 and held["skipped"] == {}
    lessons = [r for r in roland.rows(held["table"]) if r["kind"] == "line" and r["phase"] == "lessons"]
    assert {r["who"] for r in lessons} == {"Roland", "Isolde", "Wystan"}
    w.close()


def test_a_keeper_left_with_nobody_answering_closes_the_table_and_doesnt_talk_to_itself(tmp_path, monkeypatch):
    # Wenna kept two tables for Maren, who was resting at home and never sat: nine lines of her own each,
    # "Maren, I've asked four times now".
    monkeypatch.setattr(table, "TURN_S", 0.5)
    w = World.open("local", root=tmp_path)
    roland = seat(w, "Roland", talker())

    async def go():
        sitting = asyncio.create_task(roland.sit(wait_s=5))
        held = await roland.host(["Roland", "Isolde"])  # Isolde's brain never sits down
        await sitting
        return held

    held = asyncio.run(go())
    assert held["skipped"] == {"Isolde": 2} and held["speakers"]["Roland"] <= 2 and held["lines"] <= 2
    assert roland.current() is None
    w.close()
