"""A party for the player (cuo-cym.6): companions that follow and fight beside a character a person plays."""

import asyncio
from dataclasses import replace
import copy

from uo_brain import companion, fleet, gameparty, party, policy, state
from uo_brain.session import Result
from uo_brain.world import World

from conftest import SNAPSHOT
from test_party import LICH, monster
from test_policy import answers

MEMBERS = ["Harry", "Isolde", "Wystan"]


def person(name, distance, dx=0, dy=0, serial=0x700, dead=False):
    return {"serial": serial, "name": name, "body": 400, "notoriety": "innocent", "human": True, "monster": False,
            "pet": False, "hits_pct": 100, "dead": dead, "distance": distance, "dx": dx, "dy": dy, "dir": "west"}


def said(seq, name, text, serial=0x700, kind="regular"):
    return {"seq": seq, "time_ms": seq, "name": name, "serial": serial, "text": text, "type": kind, "source": "speech"}


def view(mobiles=(), journal=(), seq=10, dead=False, **agent):
    snap = copy.deepcopy(SNAPSHOT)
    snap["player"].update(name="Isolde", x=1000, y=1000, dead=dead)
    snap["mobiles"] = [copy.deepcopy(m) for m in mobiles]
    snap["journal"], snap["journal_seq"] = list(journal), seq
    snap["agent"].update(engaged=0, fleeing=False, **agent)
    return snap


class Walker:
    """A companion's session: a script of snapshots, and what was asked of the client."""

    def __init__(self, snaps):
        self.snaps, self.acts, self.goals = list(snaps), [], []
        self.judge = None

    async def snap(self, **_):
        return copy.deepcopy(self.snaps.pop(0) if len(self.snaps) > 1 else self.snaps[0])

    @staticmethod
    def where(snap):
        return snap["player"]["x"], snap["player"]["y"]

    async def act(self, verb, **params):
        self.acts.append((verb, params))
        return {"status": "done"}

    async def resurrect(self):
        self.goals.append("resurrect")
        return Result(True, "resurrected")

    async def recover_corpse(self, go_anyway=False):
        self.goals.append(("recover_corpse", go_anyway))
        return Result(True, "took it back")


def test_an_order_is_for_all_unless_it_names_another():
    others = ["Isolde", "Wystan"]
    assert companion.order_of("Stay here.", "Isolde", others) == "stay"
    assert companion.order_of("Follow me!", "Isolde", others) == "follow"
    assert companion.order_of("Wystan, wait here", "Isolde", others) is None
    assert companion.order_of("Wystan, wait here", "Wystan", others) == "stay"
    assert companion.order_of("Isolde and Wystan, with me", "Isolde", others) == "follow"
    assert companion.order_of("Nice weather for it.", "Isolde", others) is None
    # Whole phrases, the earliest of them: a word of one inside a line of talk isn't an order.
    assert companion.order_of("Stay close.", "Isolde", others) == "follow"
    assert companion.order_of("You should have stayed back, Isolde.", "Isolde", others) is None
    assert companion.order_of("It came straight to me, and I'll stay alive, thanks.", "Isolde", others) is None
    assert companion.order_of("Stay here, don't follow me.", "Isolde", others) == "stay"
    assert companion.order_of("Wystan, follow.", "Wystan", others) == "follow"
    assert companion.order_of("To me, all of you!", "Isolde", others) == "follow"
    # At the table only an order to move on counts: the rest is talk, drinks and all.
    assert companion.order_of("Drink with me! Drinks are on me.", "Isolde", others, at_table=True) is None
    assert companion.order_of("Come on, that was a warning shot?", "Isolde", others, at_table=True) is None
    assert companion.order_of("Right. Follow me.", "Isolde", others, at_table=True) == "follow"
    assert companion.order_of("On your feet.", "Isolde", others, at_table=True) == "follow"


def test_a_companion_follows_the_leader_and_takes_its_orders(tmp_path):
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Harry", MEMBERS)
    harry = person("Harry", 5, 5, 0)
    s = Walker([
        view([harry]),                                                                   # first sight
        view([harry], follow={"state": "following"}),                                    # beside it: nothing to do
        view([harry], [said(11, "Harry", "Stay here.")], seq=11, follow={"state": "following"}),
        view([person("Harry", 12, 12, 0)], [said(12, "Mallory", "Follow me!", serial=0x999)], seq=12),  # a stranger's word
        view([person("Harry", 12, 12, 0)], [said(13, "Harry", "Wystan, follow me.")], seq=13),          # said to another
        view([person("Harry", 12, 12, 0)], [said(14, "Harry", "Come on, with me!")], seq=14),
    ])
    game = gameparty.GameParty(s, mine)
    c = companion.Companion(s, mine, game=game)

    def follows():
        return [p for v, p in s.acts if v == "follow"]

    asyncio.run(c.step())
    assert c.serial == 0x700 and game.leader_serial == 0x700 and c.seen_at == (1005, 1000)
    assert follows() == [{"target": 0x700, "distance": companion.FOLLOW_TILES}] and c.doing == "with Harry"
    asyncio.run(c.step())
    assert len(follows()) == 1  # already following
    asyncio.run(c.step())
    assert c.mode == "stay" and ("stop", {}) in s.acts and ("say", {"text": "Staying here."}) in s.acts
    asyncio.run(c.step())
    asyncio.run(c.step())
    assert c.mode == "stay" and len(follows()) == 1 and c.orders == 1  # neither a stranger's word nor another's order
    asyncio.run(c.step())
    assert c.mode == "follow" and len(follows()) == 2 and c.orders == 2
    w.close()


def test_a_companion_goes_to_where_the_leader_was_last_seen_and_only_knows_one_of_the_name(tmp_path):
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Harry", MEMBERS)
    s = Walker([view([person("Harry", 8, 8, 0)]), view([]), view([person("Harry", 3, 3, 0, serial=0x888)])])
    c = companion.Companion(s, mine, acknowledge=False)
    asyncio.run(c.step())
    asyncio.run(c.step())
    assert s.acts[-1] == ("travel", {"x": 1008, "y": 1000, "distance": companion.LOST_TILES - 1, "pace": "run"})
    assert c.lost == 1 and c.doing == "going to where Harry was last seen"
    # Another character of the same name isn't the leader.
    asyncio.run(c.step())
    assert len([v for v, _ in s.acts if v == "follow"]) == 1 and c.serial == 0x700
    w.close()


def test_a_companion_that_dies_comes_back_by_itself(tmp_path):
    w = World.open("local", root=tmp_path)
    harry = person("Harry", 2, 2, 0)
    # Dead; then, back on its feet, a journal that holds what the leader said while it was away.
    while_away = [said(11, "Harry", "Stay here.")]
    s = Walker([view(dead=True), view([harry], while_away, seq=11), view([harry], while_away, seq=11)])
    c = companion.Companion(s, party.Party(w, "p1", "Isolde", "Harry", MEMBERS))
    c.seq = 10
    asyncio.run(c.step())
    assert s.goals == ["resurrect", ("recover_corpse", True)] and not c.own
    # An order given while it was dead isn't taken on its return: it goes back to the leader.
    asyncio.run(c.step())
    assert c.mode == "follow" and c.orders == 0 and [v for v, _ in s.acts if v == "follow"]
    w.close()


def beside_harry(creature):
    snap = view([person("Harry", 9, 9, 0), creature])
    snap["player"].update(hits=95, hits_max=95)
    snap["agent"]["bandaging"] = False
    return state.build(snap, set(), [], bestiary=LICH, companions={"Harry": "companion", "Wystan": "archer"})


def test_a_companion_fights_what_is_on_the_leader_and_nothing_else():
    cfg = policy.PolicyConfig(escort="Harry", looting="nothing")
    # A skeleton in war mode beside the leader, 9 tiles from the companion: the party's to fight.
    on_harry = {**monster(0x310, "a skeleton", 50, 8), "dx": 8, "dy": 1}
    dec = policy.decide(beside_harry(on_harry), answers("seek", target="t1"), policy.Memory(), cfg)
    assert dec.intent == "seek" and dec.target.serial == 0x310
    # One in war mode far from both, and one idle beside the leader, are left alone.
    far = {**monster(0x311, "a skeleton", 50, 12), "dx": -12, "dy": 0}
    assert policy.decide(beside_harry(far), answers("seek", target="t1"), policy.Memory(), cfg).intent == "rest"
    idle = {**monster(0x312, "a skeleton", 50, 8), "dx": 8, "dy": 1, "war_mode": False}
    assert policy.decide(beside_harry(idle), answers("seek", target="t1"), policy.Memory(), cfg).intent == "rest"
    # What attacks the companion itself is fought, wherever the leader is.
    on_me = {**monster(0x313, "a skeleton", 50, 1), "dx": -1, "dy": 0, "war_mode": False, "attacking_me": True}
    assert policy.decide(beside_harry(on_me), answers("fight", target="t1"), policy.Memory(), cfg).intent == "fight"
    # Without the escort, the first would be anyone's to seek and the second too.
    assert policy.decide(beside_harry(far), answers("seek", target="t1"), policy.Memory(), policy.PolicyConfig()).intent == "seek"
    # One system one judged the leader's fight (cuo-cym.25), 12 tiles off: the party's too.
    judged = replace(cfg, escort_also=frozenset({0x311}))
    dec = policy.decide(beside_harry(far), answers("seek", target="t1"), policy.Memory(), judged)
    assert dec.intent == "seek" and dec.target.serial == 0x311


def test_a_fleet_file_for_the_players_party(tmp_path, monkeypatch):
    f = tmp_path / "p.toml"
    body = ('name = "p"\nparty = true\nplan = "player"\n{leader}[[agent]]\nname = "Isolde"\naccount = "a"\npassword = "a"\n'
            'port = 1\n[[agent]]\nname = "Wystan"\naccount = "b"\npassword = "b"\nport = 2\n')
    f.write_text(body.format(leader='leader = "Harry"\n'))
    fl = fleet.load(f)
    assert fl.plan == "player" and fl.leader == "Harry" and [a.name for a in fl.agents] == ["Isolde", "Wystan"]
    started = []
    monkeypatch.setattr(fleet.subprocess, "Popen", lambda cmd, **kw: started.append(cmd))
    fleet.start_brain(fl, fl.agents[0], tmp_path)
    cmd = started[0]
    assert cmd[cmd.index("--party-plan") + 1] == "player" and cmd[cmd.index("--leader") + 1] == "Harry"
    assert cmd[cmd.index("--party-members") + 1] == "Harry,Isolde,Wystan"
    assert cmd[cmd.index("--hear") + 1] == "Wystan,Harry"  # it hears the leader as it hears the others
    for bad in ("", 'leader = "Isolde"\n'):  # no leader named; or one of the agents
        f.write_text(body.format(leader=bad))
        try:
            fleet.load(f)
            raise AssertionError("refused")
        except ValueError as e:
            assert "the character the person plays" in str(e)


def test_a_companion_accepts_the_players_invitation(tmp_path):
    """A leader a person plays reports nothing: the companion's own lock on it says whose invitation is its."""
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Harry", MEMBERS)

    class Rpc:
        acts: list = []

        async def call(self, method, **params):
            self.acts.append(params)
            return {"status": "done"}

    rpc = Rpc()
    gp = gameparty.GameParty(rpc, mine)
    snap = view()
    snap["player"]["serial"] = 0x901
    snap["party"] = {"invited_by": {"serial": 0x700, "name": "Harry"}}
    assert asyncio.run(gp.look(snap)) is None  # not known yet: the invitation keeps
    gp.leader_serial = 0x700
    assert asyncio.run(gp.look(snap)) == "accepted" and rpc.acts[-1] == {"verb": "party", "kind": "accept", "target": 0x700}
    w.close()


def test_the_table_is_asked_for_by_its_own_words_and_a_walk_to_the_inn_is_a_walk():
    others = ["Isolde", "Wystan"]
    assert companion.order_of("Sit down, drinks are on me.", "Isolde", others) == "table"
    assert companion.order_of("Time for the inn.", "Isolde", others) == "table"
    assert companion.order_of("Follow me to the inn.", "Isolde", others) == "follow"
    assert companion.order_of("Wystan, take a seat", "Isolde", others) is None


def test_the_players_companions_hold_their_table_at_the_leaders_word_and_hear_what_it_says(tmp_path, monkeypatch):
    """No planner hosts a table in a party for the player: the first companion on the list keeps the turns, what
    the leader says while it is open is on the table for the next to answer, and its next order ends it."""
    from uo_brain import memory, table
    from test_table import Rpc, talker

    monkeypatch.setattr(companion, "HOST_WAITS_S", 0.3)
    monkeypatch.setattr(table, "TURN_S", 5.0)
    w = World.open("local", root=tmp_path)
    seen: list = []
    harry = person("Harry", 2, 2, 0)
    sit = [said(11, "Harry", "Sit down, drinks are on me.")]
    joke = [*sit, said(12, "Harry", "Wystan, you shot the bone knight in the back.")]
    up = [*joke, said(13, "Harry", "Right. Follow me.")]

    class Seated(Walker):
        """The journal as it grows: the order, then a line of the leader's, then (once the table has had four
        lines) its order to move on."""
        def __init__(self, name):
            super().__init__([view([harry])])
            self.name, self.looks = name, 0

        async def snap(self, **_):
            self.looks += 1
            lines = len([r for r in w.db.execute("SELECT 1 FROM party_table WHERE kind = 'line' AND who != 'Harry'")])
            journal, seq = ([], 10) if self.looks == 1 else (sit, 11) if self.looks == 2 else \
                (up, 13) if lines >= 4 else (joke, 12)
            snap = view([harry], journal, seq=seq)
            snap["player"]["name"] = self.name
            return snap

    made = {}
    for name in ("Isolde", "Wystan"):
        mine = party.Party(w, "p1", name, "Harry", MEMBERS)
        t = table.Table(mine, Rpc(), {"temperament": f"{name}'s own"}, None, memory.Memory(w, name),
                        chat_fn=talker(seen), pace=False)
        t.guest = "Harry"
        made[name] = companion.Companion(Seated(name), mine, table=t)

    async def one(c):
        await c.step()      # first sight of the leader
        await c.step()      # "Sit down": the table, until the leader's next order

    async def go():
        await asyncio.wait_for(asyncio.gather(*(one(c) for c in made.values())), 30)

    asyncio.run(go())
    rows = [dict(r) for r in w.db.execute("SELECT who, kind, text FROM party_table ORDER BY id")]
    assert [r["kind"] for r in rows].count("open") == 1 and rows[-1]["kind"] == "close"  # one table, one keeper
    assert {"who": "Harry", "kind": "line", "text": "Wystan, you shot the bone knight in the back."} in rows
    at = rows.index({"who": "Harry", "kind": "line", "text": "Wystan, you shot the bone knight in the back."})
    after = [r for r in rows[at + 1:] if r["kind"] == "next"]
    assert not after or after[0]["who"] == "Wystan" or rows[at - 1]["who"] == "Wystan"  # the one it names answers
    assert sum(1 for r in rows if r["who"] == "Harry") == 1  # its order to sit and its order to go aren't table talk
    spoke = {r["who"] for r in rows if r["kind"] == "line"} - {"Harry"}
    assert spoke == {"Isolde", "Wystan"} and not any(r["kind"] == "next" and r["who"] == "Harry" for r in rows)
    assert len([r for r in rows if r["kind"] == "line"]) < 12  # "Follow me." ended it before the evening ran out
    # The leader is at the table in what each is told, and its line is in what the next speaker reads.
    prompts = [m[1]["content"] for m in seen if "Who you speak for" in m[1]["content"]]
    assert all("Harry, who leads the party, sits with them" in p for p in prompts)
    assert any("Harry: Wystan, you shot the bone knight in the back." in p for p in prompts)
    for c in made.values():
        assert c.mode == "follow" and c.tables == 1 and ("say", {"text": "Aye, I'll drink to that."}) in c.session.acts
    w.close()


def test_with_no_talk_there_is_no_table_and_the_order_changes_nothing(tmp_path):
    w = World.open("local", root=tmp_path)
    harry = person("Harry", 2, 2, 0)
    s = Walker([view([harry]), view([harry], [said(11, "Harry", "Sit down.")], seq=11, follow={"state": "following"})])
    c = companion.Companion(s, party.Party(w, "p1", "Isolde", "Harry", MEMBERS))
    asyncio.run(c.step())
    asyncio.run(c.step())
    assert c.mode == "follow" and c.orders == 0 and not [a for a in s.acts if a[0] in ("stop", "say")]
    w.close()


def test_only_those_who_took_the_order_sit_and_the_first_of_them_keeps_the_turns(tmp_path, monkeypatch):
    """Who is near by the reports isn't who is sitting: the first on the list was in a fight when the leader said
    it, and didn't take it. Counted as seated and as keeper, nobody would have opened the table."""
    from uo_brain import memory, table
    from test_table import Rpc, talker

    monkeypatch.setattr(companion, "HOST_WAITS_S", 0.3)
    monkeypatch.setattr(table, "TURN_S", 5.0)
    w = World.open("local", root=tmp_path)
    four = ["Harry", "Isolde", "Wystan", "Thea"]
    harry = person("Harry", 2, 2, 0)
    sit = [said(11, "Harry", "Sit down.")]
    up = [*sit, said(12, "Harry", "On your feet.")]

    class Seated(Walker):
        def __init__(self, name, fighting=False):
            super().__init__([view([harry])])
            self.name, self.looks, self.fighting = name, 0, fighting

        async def snap(self, **_):
            self.looks += 1
            lines = len([r for r in w.db.execute("SELECT 1 FROM party_table WHERE kind = 'line'")])
            journal, seq = ([], 10) if self.looks == 1 else (up, 12) if lines >= 3 else (sit, 11)
            snap = view([harry], journal, seq=seq)
            snap["player"]["name"] = self.name
            if self.fighting:
                snap["agent"]["engaged"] = 0x300
            return snap

    made = {}
    for name in ("Isolde", "Wystan", "Thea"):
        mine = party.Party(w, "p1", name, "Harry", four)
        mine.report(view([harry]), "mage", "with Harry")  # all three are near, by their reports
        t = table.Table(mine, Rpc(), {"temperament": f"{name}'s own"}, None, memory.Memory(w, name),
                        chat_fn=talker(), pace=False)
        made[name] = companion.Companion(Seated(name, fighting=name == "Isolde"), mine, table=t)

    async def one(c):
        await c.step()
        await c.step()

    async def go():
        await asyncio.wait_for(asyncio.gather(*(one(c) for c in made.values())), 30)

    asyncio.run(go())
    rows = [dict(r) for r in w.db.execute("SELECT who, kind, text FROM party_table ORDER BY id")]
    opened = [r for r in rows if r["kind"] == "open"]
    assert len(opened) == 1 and opened[0]["who"] == "Wystan" and opened[0]["text"] == '["Wystan", "Thea"]'
    assert {r["who"] for r in rows if r["kind"] == "line"} == {"Wystan", "Thea"} and rows[-1]["kind"] == "close"
    assert made["Isolde"].tables == 0 and made["Isolde"].orders == 0 and made["Wystan"].tables == made["Thea"].tables == 1
    w.close()


def test_one_killed_at_the_table_gets_up_from_it(tmp_path):
    from uo_brain import table
    from test_table import Rpc

    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Harry", MEMBERS)
    s = Walker([view([person("Harry", 2, 2, 0)], dead=True)])
    c = companion.Companion(s, mine, table=table.Table(mine, Rpc(), pace=False))
    up = asyncio.Event()
    asyncio.run(asyncio.wait_for(c.leader_at_table(up, keeper=False), 3))
    assert up.is_set()
    w.close()


class Reader:
    """System one for the leader's lines (cuo-cym.15): what it reads in each, by the line's text."""

    name = "decision/test"

    def __init__(self, readings, fail=0):
        self.readings, self.asked, self.fail = readings, [], fail

    async def ask(self, state, questions):
        from uo_brain import judge
        self.asked.append((state, questions))
        if self.fail:
            self.fail -= 1
            raise TimeoutError("no answer")
        pick, p = self.readings.get(state["line"], ("none", 0.9))
        options = questions["order"]["criteria"]
        # What isn't the pick's is "none" of them, as the models give it ("Hold on, what did you just say?": wait
        # 0.52, none 0.47).
        spread = {o: p if o == pick else 0.0 for o in options}
        spread["none"] = spread["none"] + 1 - p if pick != "none" else p
        return judge.Answers(choices={"order": judge.ChoiceResult(pick, spread, p)}, model="test")


def test_the_leader_gives_its_orders_in_its_own_words(tmp_path):
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Harry", MEMBERS)
    near, off = person("Harry", 2, 2, 0), person("Harry", 12, 12, 0)
    s = Walker([
        view([near]),
        view([near], [said(11, "Harry", "Hold up a moment.")], seq=11, follow={"state": "following"}),
        view([off], [said(12, "Harry", "Come on, that was a warning shot?")], seq=12),   # the phrases' "come on"
        view([off], [said(13, "Harry", "Wystan, keep up!")], seq=13),                    # for another
        view([off], [said(14, "Harry", "Hold on, what did you just say?")], seq=14),     # an order, but under the cut
        view([off], [said(15, "Harry", "Right, on we go.")], seq=15),
    ])
    s.judge = Reader({"Hold up a moment.": ("stay", 0.97), "Hold on, what did you just say?": ("follow", 0.52),
                      "Right, on we go.": ("follow", 0.95)})
    seen = []
    c = companion.Companion(s, mine, log=seen.append)
    asyncio.run(c.step())
    assert not s.judge.asked  # nothing said yet
    asyncio.run(c.step())
    assert c.mode == "stay" and ("say", {"text": "Staying here."}) in s.acts
    state, qs = s.judge.asked[0]
    assert state == {"party": {"leader": "Harry", "companions": ["Isolde", "Wystan"]}, "companion": "Isolde",
                     "doing_now": "following Harry", "line": "Hold up a moment."}
    assert set(qs["order"]["criteria"]) == {"follow", "stay", "go", "table", "none"}
    # Asked beside it: which of this companion's settings does the line speak to?
    assert {"looting", "talk", "none"} <= set(qs["setting"]["criteria"]) and "opening_spell" not in qs["setting"]["criteria"]
    for _ in range(3):
        asyncio.run(c.step())
    assert c.mode == "stay" and c.orders == 1  # talk, another's order and a reading under the cut change nothing
    assert s.judge.asked[1][0]["doing_now"] == "waiting where Harry said to"
    asyncio.run(c.step())
    assert c.mode == "follow" and c.orders == 2 and c.read == 5
    reads = [r for r in seen if r["type"] == "order_read"]
    assert [r["order"] for r in reads] == ["stay", None, None, None, "follow"] and reads[3]["p"] == 0.52
    # The one that was nearly an order is kept as that for whoever writes an answer: nothing is agreed to.
    assert "unclear. It may have been the word to come along" in c.reading(14).note and not c.reading(12).note
    w.close()


def test_without_an_answer_the_phrases_read_the_leaders_line(tmp_path, monkeypatch):
    from uo_brain import judge
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Harry", MEMBERS)
    harry = person("Harry", 2, 2, 0)
    lines = ["Stay here.", "Follow me.", "Stay here.", "Follow me."]
    s = Walker([view([harry])] + [view([harry], [said(11 + i, "Harry", t)], seq=11 + i) for i, t in enumerate(lines)])
    s.judge = Reader({"Follow me.": ("follow", 0.99)}, fail=2)
    c = companion.Companion(s, mine, acknowledge=False)
    asyncio.run(c.step())
    asyncio.run(c.step())
    asyncio.run(c.step())
    assert c.mode == "follow" and c.orders == 2 and len(s.judge.asked) == 2  # two questions failed: the phrases read both
    asyncio.run(c.step())
    assert c.mode == "stay" and len(s.judge.asked) == 2                      # and read the next without asking
    c._deaf_until = 0.0
    asyncio.run(c.step())
    assert c.mode == "follow" and len(s.judge.asked) == 3 and c.read == 1    # after a while system one is asked again
    # The rule judge knows nothing of orders: the phrases read for it.
    s2 = Walker([view([harry]), view([harry], [said(11, "Harry", "Stay here.")], seq=11)])
    s2.judge = judge.HeuristicJudge()
    c2 = companion.Companion(s2, mine, acknowledge=False)
    asyncio.run(c2.step())
    asyncio.run(c2.step())
    assert c2.mode == "stay" and c2.read == 0
    w.close()


def test_at_the_table_the_question_is_only_whether_to_get_up(tmp_path):
    from uo_brain import table
    from test_table import Rpc

    assert set(companion.order_question("Isolde", "Harry", at_table=True)["criteria"]) == {"follow", "stay", "none"}
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Harry", MEMBERS)
    harry = person("Harry", 2, 2, 0)
    talk = [said(11, "Harry", "Stay a while, the night is young.")]
    s = Walker([view([harry], talk, seq=11), view([harry], [*talk, said(12, "Harry", "Right, we should get moving.")], seq=12)])
    s.judge = Reader({"Right, we should get moving.": ("follow", 0.9)})
    c = companion.Companion(s, mine, table=table.Table(mine, Rpc(), pace=False))
    c.seq, c.mode = 10, "stay"
    up = asyncio.Event()
    asyncio.run(asyncio.wait_for(c.leader_at_table(up, keeper=False), 5))
    assert up.is_set() and c.mode == "follow" and len(s.judge.asked) == 2
    assert all(st["doing_now"] == "sitting at the table with the party" for st, _ in s.judge.asked)
    w.close()


def test_an_order_is_acknowledged_in_the_companions_own_voice(tmp_path):
    from uo_brain import voice
    assert all(voice.KINDS[k][0] == "" for k in ("staying", "coming", "sitting"))  # lines with no slot to fill
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Harry", MEMBERS)
    harry = person("Harry", 2, 2, 0)
    s = Walker([view([harry]), view([harry], [said(11, "Harry", "Stay here.")], seq=11),
                view([harry], [said(12, "Harry", "Follow me.")], seq=12)])
    hers = voice.Voice({"staying": ["Fine. Don't dawdle."], "coming": ["At last."]})
    c = companion.Companion(s, mine, voice=hers)
    for _ in range(3):
        asyncio.run(c.step())
    assert [p["text"] for v, p in s.acts if v == "say"] == ["Fine. Don't dawdle.", "At last."]
    w.close()


def test_a_companion_loots_nothing_whatever_its_strategy_says():
    """The fight loop lays the strategy's settings over its own, and with no strategy those say "valuables": a
    companion looted the player's kills in every run until this was seen (one told to wait walked to a corpse)."""
    from uo_brain import strategy
    escorting = policy.PolicyConfig(escort="Harry", looting="nothing")
    for knobs in (strategy.Knobs(), strategy.Knobs(looting="everything")):
        cfg = strategy.apply(escorting, knobs)
        assert cfg.looting == "nothing" and cfg.take_item == 2.0 and cfg.escort == "Harry"
    assert strategy.apply(policy.PolicyConfig(), strategy.Knobs(looting="everything")).looting == "everything"


class Mind:
    """System one for every question a companion puts on a line of the leader's (cuo-cym.18 to .21): what it reads
    in each, by the line's text. order, setting, to and place: {line: (pick, how sure)}; values: {line: {setting:
    (value, how sure)}}, or by (line, companion) for a line that says one thing to one and another to the rest;
    willing: {line: how sure it goes}. For the fight itself (cuo-cym.24): fight and whom {line: (pick, how sure)};
    target {line: (the start of the creature's name, how sure)}."""

    name = "decision/test"

    def __init__(self, order=None, setting=None, values=None, to=None, place=None, willing=None, asked=None,
                 fight=None, target=None, whom=None, errand=None, goods=None, count=None, one_of=None):
        self.order, self.setting, self.values = order or {}, setting or {}, values or {}
        self.to, self.place, self.willing, self.asked_ = to or {}, place or {}, willing or {}, asked or {}
        self.fight, self.target, self.whom = fight or {}, target or {}, whom or {}
        # An errand (cuo-cym.27): errand, goods and count {line: (pick, how sure)}; one_of {line: how sure}.
        self.errand, self.goods, self.count, self.one_of = errand or {}, goods or {}, count or {}, one_of or {}
        self.theirs = {}  # which creatures are the leader's fight (cuo-cym.25): {creature's name: how sure}
        self.asked = []

    async def ask(self, state, questions):
        from uo_brain import judge, standing
        self.asked.append((state, questions))
        out = judge.Answers(model="test", cost=0.00002)
        if "creatures" in state:  # which creatures are the leader's fight (escort.py): a yes/no each
            for key, q in questions.items():
                name = q["instructions"]["question"].split("(", 1)[1].split(")", 1)[0]
                out.nouls[key] = self.theirs.get(name, 0.1)
            return out
        line, me = state["line"], state.get("companion")
        for key, q in questions.items():
            if key == "willing":
                out.nouls[key] = self.willing.get(line, 0.9)
                continue
            if key == "asked":  # does the line ask this companion to do something?
                out.nouls[key] = self.asked_.get(line, 0.05)
                continue
            if key == "one_of":
                out.nouls[key] = self.one_of.get(line, 0.05)
                continue
            if key in ("order", "setting", "for", "place", "fight", "whom", "errand", "goods", "count"):
                by_line = {"order": self.order, "setting": self.setting, "for": self.to, "place": self.place,
                           "fight": self.fight, "whom": self.whom, "errand": self.errand, "goods": self.goods,
                           "count": self.count}[key]
                pick, p = by_line.get(line, ({"for": "everyone", "place": "unclear", "whom": "unclear", "errand": "go",
                                              "goods": "unclear", "count": "unsaid"}.get(key, "none"), 0.95))
            elif key == "target":  # the creature by the start of its name, as the option's words begin
                start, p = self.target.get(line, ("", 0.95))
                pick = next((o for o, words in q["criteria"].items() if start and words.lower().startswith(start.lower())),
                            "unclear")
            else:  # a setting's own question
                pick, p = (self.values.get((line, me)) or self.values.get(line) or {}).get(key, (standing.UNCHANGED, 0.95))
            options = q["criteria"]
            if isinstance(pick, dict):  # the share of each option, for a line that speaks to two settings
                spread = {o: pick.get(o, 0.0) for o in options}
                pick = max(spread, key=spread.get)
                p = spread[pick]
            else:
                spread = {o: p if o == pick else (1 - p) / (len(options) - 1) for o in options}
            assert pick in options, (key, pick, list(options))
            out.choices[key] = judge.ChoiceResult(pick, spread, p)
        return out

    def questions(self, key):
        return [(state, qs[key]) for state, qs in self.asked if key in qs]


class Talks:
    """What a companion needs of its talk (talk.Ears): the lines heard, its persona, and how much it talks."""

    def __init__(self, small_talk=False):
        self.lines, self.persona, self.small_talk, self.muted, self.reader = [], {"temperament": "sharp"}, small_talk, False, None


def lines(*texts, start=11, name="Harry"):
    return [said(start + i, name, t) for i, t in enumerate(texts)]


def test_the_leaders_word_changes_a_companions_settings(tmp_path):
    """Asked to loot, three ways, nobody looted and nobody said a word (Harry's first playtest, 2026-10-13)."""
    from uo_brain import standing
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Harry", [*MEMBERS, "Thea"])
    harry = person("Harry", 2, 2, 0)
    said_ = ["feel free to loot", "Wystan, you loot; the rest of you keep off.", "Loot what you like, and no running.",
             "Nice loot on that one.", "Give me some room, you're crowding me.", "Stay close."]
    s = Walker([view([harry])] + [view([harry], lines(t, start=11 + i), seq=11 + i, follow={"state": "following"})
                                  for i, t in enumerate(said_)])
    s.judge = Mind(
        order={"Stay close.": ("follow", 0.95)},
        setting={"feel free to loot": ("looting", 0.98), "Wystan, you loot; the rest of you keep off.": ("looting", 0.9),
                 "Loot what you like, and no running.": ({"looting": 0.5, "fleeing": 0.45, "none": 0.05}, None),
                 "Give me some room, you're crowding me.": ("distance", 0.9),
                 "Stay close.": ("distance", 0.9)},
        values={"feel free to loot": {"looting": ("valuables", 0.95)},
                ("Wystan, you loot; the rest of you keep off.", "Isolde"): {"looting": ("nothing", 0.9)},
                "Loot what you like, and no running.": {"looting": ("everything", 0.9), "fleeing": ("never", 0.99)},
                "Give me some room, you're crowding me.": {"distance": ("loose", 0.9)},
                "Stay close.": {"distance": ("close", 0.9)}})
    seen = []
    c = companion.Companion(s, mine, log=seen.append)
    cfg = policy.PolicyConfig(escort="Harry", looting="nothing")
    asyncio.run(c.step())
    asyncio.run(c.step())
    # "feel free to loot": the first question names looting, looting's own question is then the only one put, and
    # the setting holds. With no talk of its own to write a line, it says so in plain words.
    assert c.settings.values == {"looting": "valuables"} and c.settings.apply(cfg).looting == "valuables"
    assert set(s.judge.asked[1][1]) == {"looting"} and s.judge.asked[1][0]["settings_now"] == {"looting": "leaves the corpses alone"}
    assert ("say", {"text": "I'll loot what's worth taking."}) in s.acts and c.reading(11).said == "I'll loot what's worth taking."
    assert [r["taken"][0]["now"] for r in seen if r["type"] == "setting_read"] == ["valuables"]
    assert [(r["setting"], r["was"], r["now"]) for r in seen if r["type"] == "setting"] == [("looting", "nothing", "valuables")]
    # Each reads a line for itself: the looting is Wystan's, and Isolde's is off again.
    asyncio.run(c.step())
    assert c.settings.values == {"looting": "nothing"} and s.acts[-1] == ("say", {"text": "Leaving the loot alone."})
    assert s.judge.asked[-1][0]["companion"] == "Isolde"
    # A line that speaks to two settings shares the first question's answer between them: both are asked about.
    asyncio.run(c.step())
    assert set(s.judge.asked[-1][1]) == {"looting", "fleeing"}
    assert c.settings.values == {"looting": "everything", "fleeing": "never"}
    assert s.acts[-1] == ("say", {"text": "I'll take everything I can carry. No running. Understood."})
    assert not c.settings.apply(cfg).allow_flee and c.settings.apply(cfg).take_item == 0.0
    # Talk with a setting's word in it changes nothing and costs no second question.
    asked = len(s.judge.asked)
    asyncio.run(c.step())
    assert len(s.judge.asked) == asked + 1 and c.settings.values == {"looting": "everything", "fleeing": "never"}
    # How close it keeps is a setting too: the follow is asked for again at the new distance.
    asyncio.run(c.step())
    assert c.follow_tiles == standing.FOLLOW_TILES["loose"] and s.acts[-1] == ("follow", {"target": 0x700, "distance": 5})
    # ...and from a line that is itself an order only when it is the follow already in force: told "stick close to
    # me lads" while following, three companions kept their distance (cuo-cym.29). "Stay close" to one following is
    # how close: the follow it is on, and right at the leader's side.
    acts = len(s.acts)
    asyncio.run(c.step())
    assert c.follow_tiles == standing.FOLLOW_TILES["close"] and ("say", {"text": "Still with you."}) in s.acts[acts:]
    assert ("say", {"text": "Right at your side."}) in s.acts[acts:]
    assert c.settings.to_log()["settings_changed"][0] == {"setting": "looting", "was": "nothing", "now": "valuables"}
    w.close()


def test_settings_kept_outside_the_fight_are_switched_too(tmp_path):
    from uo_brain import mates, voice
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Harry", MEMBERS)
    harry = person("Harry", 2, 2, 0)
    s = Walker([view([harry])] + [view([harry], lines(t, start=11 + i), seq=11 + i, follow={"state": "following"})
                                  for i, t in enumerate(["Quiet, all of you.", "Stop shouting in fights.", "You can talk again."])])
    s.raise_fallen = False
    s.judge = Mind(setting={"Quiet, all of you.": ("talk", 0.95), "Stop shouting in fights.": ("callouts", 0.95),
                            "You can talk again.": ("talk", 0.95)},
                   values={"Quiet, all of you.": {"talk": ("silent", 0.9)}, "Stop shouting in fights.": {"callouts": ("off", 0.9)},
                           "You can talk again.": {"talk": ("chatty", 0.9)}})
    link = mates.Mates(mine, voice.Voice())
    c = companion.Companion(s, mine, link=link)
    ears = Talks()
    c.hears(ears)
    assert ears.reader == c.reading and c.settings.now("talk") == "answers" and c.settings.now("callouts") == "on"
    for _ in range(3):
        asyncio.run(c.step())
    assert ears.muted and not ears.small_talk and not link.callouts
    # With talk of its own, what it owes on the line is left for the one who writes its answer: nothing said here.
    assert not [p for v, p in s.acts if v == "say"]
    r = c.reading(11)
    assert "says nothing but a word on an order" in r.owed and r.plain == "Not another word." and not r.said
    asyncio.run(c.step())
    assert not ears.muted and ears.small_talk
    # In a party that talks every line is also read for whom it is for, by one question the companions share.
    state, q = s.judge.questions("for")[0]
    assert set(q["criteria"]) == {"Isolde", "Wystan", "everyone", "nobody"} and c.reading(11).to == ["Isolde", "Wystan"]
    assert state == {"party": {"leader": "Harry", "companions": ["Isolde", "Wystan"]}, "said_before": [], "line": "Quiet, all of you."}
    assert s.judge.questions("for")[1][0]["said_before"] == ["Harry: Quiet, all of you."]
    w.close()


def test_who_a_line_is_for():
    three = ["Isolde", "Thea", "Wystan"]
    q = companion.for_question("Roland", three, {"Isolde": "mage", "Wystan": "archer", "Thea": "companion"})
    assert set(q["criteria"]) == {*three, "everyone", "all_but_Isolde", "all_but_Thea", "all_but_Wystan", "nobody"}
    assert "Wystan, the party's archer" in q["criteria"]["Wystan"] and "Thea, the party's" not in q["criteria"]["Thea"]
    assert "Isolde and Wystan" in q["criteria"]["all_but_Thea"]
    # Each companion is one of those a line is for when the options with it in them add up to half.
    whom = companion.for_whom({"everyone": 0.3, "all_but_Isolde": 0.4, "Wystan": 0.2, "nobody": 0.1}, three)
    assert whom == {"Isolde": 0.3, "Thea": 0.7, "Wystan": 0.9}


def inn_world(tmp_path):
    w = World.open("local", root=tmp_path)
    w.add_place("inn", "The Wayfarer's Inn", 1040, 1000, source="import")
    w.add_place("bank", "West bank", 1000, 1100, source="import")
    return w


def test_a_companion_goes_where_it_is_sent_and_is_the_leaders_again_when_it_comes(tmp_path):
    """Sent to the inn without the leader they agreed and didn't move: read as "come along" by companions who
    were already following (Harry's first playtest, 2026-10-13)."""
    w = inn_world(tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Harry", MEMBERS)
    harry = person("Harry", 2, 2, 0)
    off = "Right - off to the inn for you all - see you there"

    def at(x, mobiles=(), journal=(), seq=11, **agent):
        v = view(mobiles, journal, seq, **agent)
        v["player"]["x"] = x
        return v

    s = Walker([view([harry]),
                view([harry], lines(off), seq=11, follow={"state": "following"}),
                at(1010, [], lines(off), travel={"state": "walking"}),          # on its way, the leader out of view
                at(1020, [], lines(off, "Off you go then"), seq=12, travel={"state": "walking"}),
                at(1038, [], [], seq=12, travel={"state": "arrived"}),          # there
                at(1038, [], [], seq=12),                                       # waiting
                at(1038, [person("Harry", 6, 6, 0)], [], seq=12)])              # the leader walks in
    s.world = w
    s.judge = Mind(order={off: ("go", 0.97), "Off you go then": ("go", 0.8)}, place={off: ("inn", 0.95)})
    seen = []
    c = companion.Companion(s, mine, log=seen.append)
    asyncio.run(c.step())
    asyncio.run(c.step())
    assert c.mode == "go" and c.dest["name"] == "The Wayfarer's Inn" and c.sent == 1
    assert ("say", {"text": "On my way."}) in s.acts
    assert s.acts[-1] == ("travel", {"x": 1040, "y": 1000, "distance": companion.ARRIVE_TILES - 1, "pace": "run"})
    state, q = s.judge.questions("place")[0]
    assert set(q["criteria"]) == {"inn", "bank", "unclear"} and q["criteria"]["inn"] == "The inn: the nearest is 40 tiles to the east."
    assert state["moment"]["own_health"] and state["refused_before"] == 0 and "willing" in s.judge.asked[-1][1]
    assert c.doing == "on the way to the inn, sent there by Harry"
    # It doesn't follow the leader meanwhile, nor walk again while it walks.
    asyncio.run(c.step())
    assert len([v for v, _ in s.acts if v == "travel"]) == 1 and len([v for v, _ in s.acts if v == "follow"]) == 1
    # Told again to go, with no place in the line: it is going already, and says so.
    asyncio.run(c.step())
    assert s.acts[-1] == ("say", {"text": "On my way."}) and c.mode == "go" and c.sent == 1
    assert s.judge.asked[-2][0]["doing_now"] == "on the way to the inn, sent there by Harry"
    # There: a word, and it waits.
    asyncio.run(c.step())
    assert c.arrived and s.acts[-1] == ("say", {"text": "Here."}) and c.doing == "waiting at the inn for Harry"
    asyncio.run(c.step())
    assert c.mode == "go" and s.acts[-1] == ("say", {"text": "Here."})
    # The leader comes: it is the leader's again.
    asyncio.run(c.step())
    assert c.mode == "follow" and c.dest is None and ("say", {"text": "Right behind you."}) in s.acts
    asyncio.run(c.step())
    assert s.acts[-1] == ("follow", {"target": 0x700, "distance": companion.FOLLOW_TILES})
    orders = [r for r in seen if r["type"] == "order"]
    assert [(r.get("order"), r.get("place") or r.get("arrived") or r.get("why")) for r in orders] == [
        ("go", "The Wayfarer's Inn"), ("go", None), ("go", "The Wayfarer's Inn"), ("follow", "the leader came to where it waited")]
    w.close()


def test_called_back_it_stops_its_trip_and_comes(tmp_path):
    w = inn_world(tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Harry", MEMBERS)
    harry = person("Harry", 2, 2, 0)
    s = Walker([view([harry]), view([harry], lines("Off to the inn with you."), seq=11),
                view([], lines("Off to the inn with you.", "Right, come back.", start=11), seq=12, travel={"state": "walking"}),
                view([])])
    s.world = w
    s.judge = Mind(order={"Off to the inn with you.": ("go", 0.9), "Right, come back.": ("follow", 0.9)},
                   place={"Off to the inn with you.": ("inn", 0.9)})
    c = companion.Companion(s, mine)
    for _ in range(3):
        asyncio.run(c.step())
    assert c.mode == "follow" and c.dest is None and ("stop", {}) in s.acts and ("say", {"text": "Right behind you."}) in s.acts
    # The leader isn't in view: it goes to where it last saw it.
    asyncio.run(c.step())
    assert c.doing == "waiting for Harry"  # which is where it stands
    w.close()


def test_going_is_the_companions_to_agree_to(tmp_path):
    """Harry, 2026-10-13: "they need to be able to act independently if I can persuade them to do so"."""
    w = inn_world(tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Harry", MEMBERS)
    harry = person("Harry", 2, 2, 0)
    ask, again = "Off to the inn with you, I'll meet you there.", "Go on, I'm fine."
    snaps = [view([harry]), view([harry], lines(ask), seq=11), view([harry], lines(ask, again), seq=12)]
    s = Walker(snaps)
    s.world = w
    s.judge = Mind(order={ask: ("go", 0.95), again: ("go", 0.8)}, place={ask: ("inn", 0.9), again: ("inn", 0.8)},
                   willing={ask: 0.2, again: 0.9})
    seen = []
    c = companion.Companion(s, mine, log=seen.append)
    c.hears(Talks())
    asyncio.run(c.step())
    asyncio.run(c.step())
    # It won't, for now: it stays the leader's, and what it owes the line is a no with its reason, in its own way.
    assert c.mode == "follow" and c.refused == 1 and not [v for v, _ in s.acts if v == "travel"]
    r = c.reading(11)
    assert "won't, for now" in r.owed and "own health: " in r.owed and r.plain == "I'd rather stay with you for now."
    assert [x for x in seen if x["type"] == "order"][0]["refused"] is True
    # Asked again, the question is told of the refusal, and this time it goes.
    asyncio.run(c.step())
    assert s.judge.questions("willing")[1][0]["refused_before"] == 1 and c.mode == "go" and c.sent == 1
    assert "Harry: Off to the inn with you, I'll meet you there." in s.judge.questions("willing")[1][0]["said_before"]
    # A fleet can have them go when told: the yes/no isn't asked.
    s2 = Walker(snaps[:2])
    s2.world = w
    s2.judge = Mind(order={ask: ("go", 0.95)}, place={ask: ("inn", 0.9)}, willing={ask: 0.1})
    c2 = companion.Companion(s2, mine, persuade=False)
    asyncio.run(c2.step())
    asyncio.run(c2.step())
    assert c2.mode == "go" and not s2.judge.questions("willing")
    # Where to isn't said, or no such place is known: it asks, and goes nowhere.
    s3 = Walker([view([harry]), view([harry], lines("Off you go."), seq=11)])
    s3.world = w
    s3.judge = Mind(order={"Off you go.": ("go", 0.9)})
    c3 = companion.Companion(s3, mine)
    asyncio.run(c3.step())
    asyncio.run(c3.step())
    assert c3.mode == "follow" and ("say", {"text": "Go where?"}) in s3.acts and not [v for v, _ in s3.acts if v == "travel"]
    w.close()


def test_a_trip_it_cant_make_is_given_up(tmp_path):
    w = inn_world(tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Harry", MEMBERS)
    harry = person("Harry", 2, 2, 0)
    stuck = view([], lines("Off to the inn with you."), seq=11, travel={"state": "stuck", "stuck_at": [1005, 1000]})
    fight = view([], lines("Off to the inn with you."), seq=11, travel={"state": "stopped"})
    fight["agent"]["engaged"] = 0x300
    s = Walker([view([harry]), view([harry], lines("Off to the inn with you."), seq=11), fight,
                *[stuck] * (companion.GO_TRIES + 1)])
    s.world = w
    s.judge = Mind(order={"Off to the inn with you.": ("go", 0.9)}, place={"Off to the inn with you.": ("inn", 0.9)})
    c = companion.Companion(s, mine)
    for _ in range(3):
        asyncio.run(c.step())
    assert c.doing == "fighting on the way to the inn" and len([v for v, _ in s.acts if v == "travel"]) == 1
    for _ in range(companion.GO_TRIES):
        asyncio.run(c.step())
    # Stuck short of it every time: back to the leader, and it says so. A walk a fight broke off didn't count.
    assert c.mode == "follow" and s.acts[-1] == ("say", {"text": "Couldn't get to the inn."})
    assert len([v for v, _ in s.acts if v == "travel"]) == companion.GO_TRIES
    w.close()


def test_an_order_already_in_force_gets_a_word_but_not_every_time(tmp_path):
    """ "Off you go then", read as "come along" by companions who were following, showed nothing (cuo-cym.20)."""
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Harry", MEMBERS)
    harry = person("Harry", 2, 2, 0)
    said_ = ["Keep up, you lot.", "This way.", "Come on."]
    s = Walker([view([harry])] + [view([harry], lines(t, start=11 + i), seq=11 + i, follow={"state": "following"})
                                  for i, t in enumerate(said_)])
    s.judge = Mind(order={t: ("follow", 0.95) for t in said_})
    seen = []
    c = companion.Companion(s, mine, log=seen.append)
    for _ in range(3):
        asyncio.run(c.step())
    assert [p["text"] for v, p in s.acts if v == "say"] == ["Still with you."] and c.orders == 0
    assert c.reading(11).said == "Still with you." and "which it was doing already" in c.reading(12).note and not c.reading(12).said
    c._still_at -= companion.STILL_GAP_S
    asyncio.run(c.step())
    assert [p["text"] for v, p in s.acts if v == "say"] == ["Still with you.", "Still with you."]
    assert all(r["already"] for r in seen if r["type"] == "order") and len([r for r in seen if r["type"] == "order"]) == 3
    w.close()


def test_the_phrases_know_being_sent_somewhere():
    others = ["Isolde", "Wystan"]
    assert companion.order_of("Off you go to the inn, I'll meet you there.", "Isolde", others) == "go"
    assert companion.order_of("See you at the inn.", "Isolde", others) == "go"       # not the table's "the inn"
    assert companion.order_of("Wystan, go on ahead to the bank.", "Isolde", others) is None
    assert companion.order_of("Follow me to the inn.", "Isolde", others) == "follow"
    assert companion.place_of("Wait for me at the bank!") == "bank" and companion.place_of("Off you go.") is None
    assert companion.place_of("the cemetery, then") == "graveyard"


def test_without_a_model_the_phrases_send_it_where_the_line_says(tmp_path):
    from uo_brain import judge
    w = inn_world(tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Harry", MEMBERS)
    harry = person("Harry", 2, 2, 0)
    s = Walker([view([harry]), view([harry], lines("Go on ahead to the bank, I'll catch you up."), seq=11)])
    s.world, s.judge = w, judge.HeuristicJudge()
    c = companion.Companion(s, mine)
    asyncio.run(c.step())
    asyncio.run(c.step())
    assert c.mode == "go" and c.dest["kind"] == "bank" and s.acts[-1][0] == "travel"
    w.close()


def test_a_run_says_how_it_ended(tmp_path):
    """A run stopped by hand two minutes into its hour said "time up" (cuo-cym.23)."""
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Harry", MEMBERS)

    async def run(hours, stop_first):
        c = companion.Companion(Walker([view([person("Harry", 2, 2, 0)])]), mine)
        stop = asyncio.Event()
        if stop_first:
            stop.set()
        return await c.run(stop, hours)

    assert asyncio.run(run(1.0, True))["finished"] == "stopped"
    out = asyncio.run(run(0.0, False))
    assert out["finished"] == "time up" and out["sent_off"] == 0 and out["settings_changed"] == []
    w.close()


def test_an_order_to_move_that_is_unsure_which():
    """Live, "Right - off to the inn for you all - see you there" had being sent there at 0.50 to 0.65 and coming
    along at 0.35 to 0.50 for the three companions: one went and two stood still (2026-10-13 22:49)."""
    assert companion.order_in({"go": 0.646, "follow": 0.346, "none": 0.006}) == ("go", 0.646, False)
    order, p, doubt = companion.order_in({"go": 0.557, "follow": 0.433, "none": 0.01})
    assert (order, round(p, 2), doubt) == ("go", 0.99, True)
    assert companion.order_in({"go": 0.496, "follow": 0.496})[0] == "go"
    # Mostly coming along, with a little of being sent: to come along.
    assert companion.order_in({"follow": 0.5, "go": 0.2, "none": 0.3})[::2] == ("follow", True)
    # Unsure whether it is an order at all is still no order, and so is one of another kind under the cut.
    assert companion.order_in({"follow": 0.52, "none": 0.47}) == (None, 0.52, False)
    assert companion.order_in({"stay": 0.55, "go": 0.05, "none": 0.4})[0] is None
    assert companion.order_in({"stay": 0.7, "none": 0.3}) == ("stay", 0.7, False)
    assert companion.order_in({"none": 0.9, "follow": 0.1}) == (None, 0.9, False) and companion.order_in({}) == (None, 0.0, False)


def test_taken_as_sent_in_doubt_with_no_place_named_it_comes_along(tmp_path):
    w = inn_world(tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Harry", MEMBERS)
    harry = person("Harry", 2, 2, 0)
    both = {"go": 0.5, "follow": 0.45, "none": 0.05}
    s = Walker([view([harry]), view([harry], lines("Off we go, then."), seq=11, follow={"state": "following"}),
                view([harry], lines("Off we go, then.", "Off to the inn for you all, see you there."), seq=12)])
    s.world = w
    s.judge = Mind(order={"Off we go, then.": (both, None), "Off to the inn for you all, see you there.": (both, None)},
                   place={"Off to the inn for you all, see you there.": ("inn", 0.9)})
    c = companion.Companion(s, mine)
    asyncio.run(c.step())
    asyncio.run(c.step())
    # No place in it: an order to come along, which it is doing. It doesn't ask where to go.
    assert c.mode == "follow" and c.reading(11).order == "follow" and ("say", {"text": "Still with you."}) in s.acts
    assert not [p for v, p in s.acts if v == "say" and p["text"] == "Go where?"]
    # With a place in it, it is sent there.
    asyncio.run(c.step())
    assert c.mode == "go" and c.dest["kind"] == "inn" and c.reading(12).doubt
    w.close()


def test_asked_for_what_it_doesnt_do_at_a_word_it_says_so(tmp_path):
    """ "cover me" was read as no order, and the yes/no on answering gave it 0.13 to 0.29: a person who asked for
    something heard nothing back (cuo-cym.20)."""
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Harry", MEMBERS)
    harry = person("Harry", 2, 2, 0)
    said_ = ["cover me", "nice night", "ok lets move", "Heal thyself, as they say.", "loot the bodies", "loot", "buy",
             "Wystan, shoot the mage"]
    s = Walker([view([harry])] + [view([harry], lines(t, start=11 + i), seq=11 + i, follow={"state": "following"})
                                  for i, t in enumerate(said_)])
    s.judge = Mind(order={"ok lets move": ({"follow": 0.49, "none": 0.51}, None)},
                   asked={"cover me": 0.97, "ok lets move": 0.9, "loot the bodies": 0.95, "loot": 0.95, "buy": 0.9,
                          "Wystan, shoot the mage": 0.9},
                   to={"buy": ("nobody", 0.99), "Wystan, shoot the mage": ("Wystan", 0.99)},
                   setting={"loot the bodies": ("looting", 0.9), "loot": ("looting", 0.9)},
                   values={"loot the bodies": {"looting": ("valuables", 0.9)}, "loot": {"looting": ("valuables", 0.4)}})
    c = companion.Companion(s, mine)
    c.hears(Talks())
    for _ in range(9):
        asyncio.run(c.step())
    # A yes/no in a request of its own beside the order's: is the line asking this companion to do something?
    first = [qs for state, qs in s.judge.asked if state["line"] == "cover me"]
    assert [set(qs) for qs in first] == [{"order", "setting"}, {"asked"}, {"for"}] and first[1]["asked"]["type"] == "noul"
    assert set(first[0]["order"]["criteria"]) == {"follow", "stay", "go", "table", "none"}
    r = c.reading(11)
    assert r.order is None and "doesn't do at a word" in r.owed and "promises nothing" in r.owed and not r.plain
    assert c.mode == "follow" and not [p for v, p in s.acts if v == "say"]  # nothing changes, and its talk answers
    assert not c.reading(12).owed and not c.reading(14).owed                # talk is talk
    # Nearly an order, and asking for something: it asks what is wanted, and agrees to nothing.
    assert "isn't sure what Harry wants" in c.reading(13).owed and "come along" in c.reading(13).owed
    # A line that changes a setting owes that, and not a no.
    assert "takes what is worth having" in c.reading(15).owed
    # One that speaks to a setting and changes nothing is no request for what it doesn't do: it asks.
    assert "may have been about how Isolde plays" in c.reading(16).owed and "doesn't do at a word" not in c.reading(16).owed
    # A request that is for nobody (a word for a vendor), or for another, leaves this one owing nothing.
    assert c.reading(17).to == [] and not c.reading(17).owed
    assert c.reading(18).to == ["Wystan"] and not c.reading(18).owed
    assert companion.left_over({"none": 0.95}, 0.9) == "asked" and companion.left_over({"none": 0.95}, 0.5) == ""
    assert companion.left_over({"stay": 0.4, "none": 0.6}, 0.9) == "unsure"
    w.close()


def test_places_named_by_their_own_names_are_offered(tmp_path):
    """cuo-cym.27: a place by its name ('the east bank', 'Yew', 'the inn in Skara Brae') was no option; only kinds were."""
    w = World.open("local", root=tmp_path)
    w.add_place("bank", "West Britain bank", 1425, 1690, source="import")
    w.add_place("bank", "East Britain bank", 1650, 1608, source="import")
    w.add_place("bank", "West Trinsic bank", 1830, 2820, source="import")
    w.add_place("inn", "West Skara Brae inn", 590, 2160, source="import")
    w.add_place("inn", "Britain innkeeper", 1494, 1619, source="seen")
    w.replace_source("regions", "fixture", [
        {"name": "Skara Brae", "map": "Felucca", "kind": "town", "guarded": 1, "rects": "[[538, 2107, 688, 2297]]",
         "go_x": 632, "go_y": 2233, "go_z": 0},
        {"name": "Yew", "map": "Felucca", "kind": "town", "guarded": 1, "rects": "[[258, 881, 657, 1261]]",
         "go_x": 546, "go_y": 992, "go_z": 0}])
    here = (1392, 1528)

    def named(line):
        return [p["says"] for p in companion.named_places(w, [line], here)]

    assert named("Meet me at the east bank.") == ["East Britain bank"]
    assert named("go wait at the west bank") == ["West Britain bank"]  # the nearer of the two so named
    assert named("Off to the inn in Skara Brae with you") == ["West Skara Brae inn"]  # by its own name, the longest stretch
    assert named("Go to Skara Brae and wait for me at the inn.") == ["the inn in Skara Brae"]  # a town and a kind
    assert named("See you in Yew.") == ["Yew"]
    assert named("Off to the inn with you.") == [] and named("Go on ahead.") == []
    yew = companion.named_places(w, ["See you in Yew."], here)[0]
    assert yew["kind"] == "named1" and yew["tiles"] > 400  # further than a kind's nearest is offered: by name only
    q = companion.place_question("Isolde", "Harry", [*companion.places_near(w, here), yew])
    assert q["criteria"]["named1"].startswith("Yew, named by its own name: ") and "its own name" in q["instructions"]["role"]
    w.close()


def test_sent_on_an_errand_it_buys_and_comes_back(tmp_path):
    """cuo-cym.27: 'go and get me some bandages from the healer' was read as being sent there, to wait; it bought nothing."""
    w = inn_world(tmp_path)
    w.add_place("healer", "Britain healer", 1010, 1000, source="import")
    mine = party.Party(w, "p1", "Isolde", "Harry", MEMBERS)
    harry = person("Harry", 2, 2, 0)
    line = "Isolde, run to the healer and get me 10 bandages, then come back."
    s = Walker([view([harry]), view([harry], lines(line), seq=11), view([harry], seq=11)])
    s.world = w
    s.judge = Mind(order={line: ("go", 0.95)}, place={line: ("healer", 0.9)}, errand={line: ("buy", 0.9)},
                   goods={line: ("bandage", 0.9)}, count={line: ("10", 0.8)})
    bought = []

    async def buy(item, count, vendor_kind=None):
        bought.append((item, count, vendor_kind))
        return Result(True, f"bought {count} {item}", {"bought": count, "gold_spent": 50})

    async def defended(work):
        return await work

    s.buy, s.defended = buy, defended
    seen = []
    c = companion.Companion(s, mine, log=seen.append)
    asyncio.run(c.step())
    asyncio.run(c.step())  # the order read, and the errand run in the same look
    job = next(x for x in seen if x["type"] == "order")["errand"]
    assert job["item"] == "bandage" and job["count"] == 10 and job["vendor_kind"] == "healer"
    assert "to buy 10 bandages at the healer's" in job["words"] and ("say", {"text": "On my way."}) in s.acts
    assert bought == [("bandage", 10, "healer")] and c.mode == "follow" and c.job is None and c.errands == 1
    assert ("say", {"text": "Got 10 bandages, coming back."}) in s.acts
    assert [x for x in seen if x["type"] == "errand"][0]["ok"] is True
    # Sent only to go there and wait: no errand, as before.
    from uo_brain.judge import ChoiceResult
    assert companion.errand_of({"errand": ChoiceResult("go", {"go": 0.9}, 0.9)}, None) is None
    # Sent to buy, but not what: no errand (it would buy nothing).
    assert companion.errand_of({"errand": ChoiceResult("buy", {"buy": 0.9}, 0.9),
                                "goods": ChoiceResult("unclear", {"unclear": 0.8}, 0.8)}, None) is None
    w.close()


def test_one_of_several_sent_goes_and_the_others_stay(tmp_path):
    """cuo-cym.27: 'one of you go to the bank' sent all of them."""
    from uo_brain.talk import Floor
    w = inn_world(tmp_path)
    harry = person("Harry", 2, 2, 0)
    line = "One of you nip to the bank for me."
    went = []
    for me in ("Isolde", "Wystan"):
        mine = party.Party(w, "p1", me, "Harry", MEMBERS)
        s = Walker([view([harry]), view([harry], lines(line), seq=11)])
        s.world = w
        s.judge = Mind(order={line: ("go", 0.95)}, place={line: ("bank", 0.9)}, one_of={line: 0.9})
        c = companion.Companion(s, mine, floor=Floor(w, me, set(MEMBERS)))
        asyncio.run(c.step())
        asyncio.run(c.step())
        went.append(c.mode)
    assert went == ["go", "follow"]
    w.close()


def test_what_the_leader_fights_from_range_is_judged_per_creature(tmp_path):
    """cuo-cym.25: what stood in war mode within 4 tiles of the leader was its fight; one it shot at from 10 tiles off
    wasn't the companions' until it closed. System one is asked about the creatures the rule leaves out."""
    from uo_brain import escort, policy
    w = inn_world(tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Harry", MEMBERS)
    harry = {**person("Harry", 2, 1, 2), "war_mode": True, "facing": "north"}
    skel = {"serial": 0x4001, "name": "a skeleton", "monster": True, "pet": False, "dead": False, "dx": 1, "dy": -8,
            "distance": 8, "war_mode": True, "facing": "south", "hits_pct": 70}
    hind = {"serial": 0x4002, "name": "a hind", "monster": True, "pet": False, "dead": False, "dx": 6, "dy": 0,
            "distance": 6, "war_mode": False, "facing": "north", "hits_pct": 100}
    s = Walker([view([harry, skel, hind])])
    s.world = w
    s.judge = Mind()
    s.judge.theirs = {"a skeleton": 0.9}
    seen = []
    c = companion.Companion(s, mine, log=seen.append)
    asyncio.run(c.step())
    reach = [x for x in seen if x["type"] == "reach"]
    assert reach and reach[0]["theirs"] == ["a skeleton"] and reach[0]["beyond_the_rule"] == ["a skeleton"]
    cfg = c.live(policy.PolicyConfig(escort="Harry"))
    assert cfg.escort_also == frozenset({0x4001})
    # What the question was told of the skeleton: 10 tiles from the leader, facing it, the leader facing it, wounded.
    st = s.judge.asked[-1][0]
    words = next(v for v in st["creatures"].values() if v.startswith("A skeleton"))
    assert "10 from Harry" in words and "facing Harry" in words and "Harry faces it" in words and "wounded (70%)" in words
    # Asked at most every THEIRS_EVERY_S, and not at all with the rule alone.
    asyncio.run(c.step())
    assert len([x for x in seen if x["type"] == "reach"]) == 1
    c2 = companion.Companion(Walker([view([harry, skel])]), mine, judge_reach=False)
    c2.session.world, c2.session.judge = w, Mind()
    asyncio.run(c2.step())
    assert not c2.theirs and not c2.session.judge.questions("c1")
    # The rule: in war mode within 4 tiles of the leader or 6 of itself, or on it.
    assert escort.rule({"war_mode": True, "to_leader": 4, "to_me": 9}) and not escort.rule({"war_mode": True, "to_leader": 5, "to_me": 7})
    assert escort.rule({"war_mode": False, "attacking_me": True, "to_leader": 9, "to_me": 9})
    assert escort.faces("south", 0, 5) and escort.faces("southeast", 4, 5) and not escort.faces("north", 0, 5)
    trail = escort.Trail()
    assert trail.note(1, 10, now=0.0) == 0 and trail.note(1, 7, now=2.0) == 3 and trail.note(1, 7, now=10.0) == 0
    w.close()


def test_errands_and_one_of_after_the_review(tmp_path):
    """cuo-cym.27, from a review of the change: one who won't go doesn't take a line meant for one of them; an errand
    isn't dropped as 'told again' at the place it was sent; a restock's purchases are said in words."""
    from uo_brain.talk import Floor
    w = inn_world(tmp_path)
    w.add_place("healer", "Britain healer", 1010, 1000, source="import")
    harry = person("Harry", 2, 2, 0)
    # One of several, the first unwilling: it says no and claims nothing, and the next goes.
    line = "One of you nip to the bank for me."
    modes = []
    for me, willing in (("Isolde", 0.2), ("Wystan", 0.9)):
        mine = party.Party(w, "p2", me, "Harry", MEMBERS)
        s = Walker([view([harry]), view([harry], lines(line), seq=11)])
        s.world = w
        s.judge = Mind(order={line: ("go", 0.95)}, place={line: ("bank", 0.9)}, one_of={line: 0.9}, willing={line: willing})
        c = companion.Companion(s, mine, floor=Floor(w, me, set(MEMBERS)))
        asyncio.run(c.step())
        asyncio.run(c.step())
        modes.append((c.mode, c.refused))
    assert modes == [("follow", 1), ("go", 0)]
    # Waiting at the healer's it was sent to, then sent to buy there: the errand is run.
    mine = party.Party(w, "p3", "Isolde", "Harry", MEMBERS)
    go, buy = "Off to the healer with you.", "While you're there, buy 20 bandages."
    s = Walker([view([harry]), view([harry], lines(go), seq=11), view([harry], lines(go, buy), seq=12)])
    s.world = w
    s.judge = Mind(order={go: ("go", 0.95), buy: ("go", 0.9)}, place={go: ("healer", 0.9), buy: ("healer", 0.9)},
                   errand={buy: ("buy", 0.9)}, goods={buy: ("bandage", 0.9)}, count={buy: ("20", 0.9)})
    bought = []

    async def buy_it(item, count, vendor_kind=None):
        bought.append((item, count))
        return Result(True, "bought", {"bought": count})

    async def restock():
        return Result(True, "restocked", {"bought": {"black pearl": 22, "blood moss": 30, "garlic": 0}})

    s.buy, s.restock = buy_it, restock
    c = companion.Companion(s, mine)
    for _ in range(3):
        asyncio.run(c.step())
    assert bought == [("bandage", 20)] and c.errands == 1
    # A restock's purchases, worded.
    c.job = {"kind": "buy", "item": "reagents", "says": "the shops", "words": "to buy reagents at the shops"}
    asyncio.run(c.run_errand())
    assert ("say", {"text": "Got 22 black pearl, 30 blood moss, coming back."}) in s.acts
    w.close()


def test_a_line_in_party_chat_is_answered_in_party_chat(tmp_path):
    """cuo-cym.29: called back in party chat from out of earshot, three companions came and answered aloud, and the
    leader heard none of it. Its word goes back the way the line came; aloud again after a line said aloud."""
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Harry", MEMBERS)
    harry = person("Harry", 5, 5, 0)
    s = Walker([
        view([harry]),
        view([harry], [said(11, "Harry", "Stay here.", kind="party")], seq=11, follow={"state": "following"}),
        view([harry], [said(12, "Harry", "Come on, with me!")], seq=12),
    ])
    c = companion.Companion(s, mine)
    for _ in range(3):
        asyncio.run(c.step())
    assert ("party", {"kind": "say", "text": "Staying here."}) in s.acts
    assert ("say", {"text": "Right behind you."}) in s.acts and ("say", {"text": "Staying here."}) not in s.acts
    w.close()
