"""UO's own party for a fleet's party (cuo-cym.3): the leader invites, a member accepts its own leader and
no other, and callouts can go to party chat."""

import asyncio
import copy

from uo_brain import fleet, gameparty, mates, party
from uo_brain.world import World

from conftest import SNAPSHOT
from test_party import COMPANIONS, NAMES, companion, monster

SERIALS = {"Roland": 0x900, "Isolde": 0x901, "Wystan": 0x902, "Thea": 0x903, "Corwin": 0x904}


class Rpc:
    def __init__(self):
        self.acts = []

    async def call(self, method, **params):
        self.acts.append((method, params))
        return {"status": "done"}

    def party(self):
        return [(p["kind"], p.get("target")) for m, p in self.acts if m == "act" and p.get("verb") == "party"]


def seen_by(name, mobiles=(), game=None):
    snap = copy.deepcopy(SNAPSHOT)
    snap["player"].update(name=name, serial=SERIALS[name], dead=False)
    snap["mobiles"] = [copy.deepcopy(m) for m in mobiles]
    snap["agent"]["engaged"] = 0
    if game is not None:
        snap["party"] = game
    return snap


def sides(tmp_path, me):
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", me, "Roland", NAMES)
    mine.companions.update({n: p for n, p in {**COMPANIONS, "Roland": "warrior"}.items() if n != me})
    # The leader's own report, with its serial: how the others know whose invitation is theirs.
    party.Party(w, "p1", "Roland", "Roland", NAMES).report(seen_by("Roland"), "warrior")
    return w, mine


def test_the_leader_invites_the_companions_it_can_reach_one_at_a_time(tmp_path):
    w, mine = sides(tmp_path, "Roland")
    rpc = Rpc()
    gp = gameparty.GameParty(rpc, mine)
    far = companion("Thea", 12)
    view = [companion("Isolde", 2), companion("Wystan", 5), far, monster(0x300, "a deer", 237, 20)]
    assert asyncio.run(gp.look(seen_by("Roland", view))) == "invited"
    assert rpc.party() == [("invite", SERIALS["Isolde"])]  # one a look: each is a target cursor
    asyncio.run(gp.look(seen_by("Roland", view)))
    assert rpc.party() == [("invite", SERIALS["Isolde"]), ("invite", SERIALS["Wystan"])]
    # Thea is out of the invitation's reach, and those asked aren't asked again for half a minute.
    assert asyncio.run(gp.look(seen_by("Roland", view))) is None and len(rpc.party()) == 2
    # Isolde is in: she isn't asked again once the others' invitations have lapsed.
    game = {"leader": SERIALS["Roland"], "leading": True,
            "members": [{"serial": SERIALS["Roland"], "name": "Roland"}, {"serial": SERIALS["Isolde"], "name": "Isolde"}]}
    gp.invited = {n: t - gameparty.INVITE_EVERY_S - 1 for n, t in gp.invited.items()}
    asyncio.run(gp.look(seen_by("Roland", view, game)))
    assert rpc.party()[-1] == ("invite", SERIALS["Wystan"]) and gp.joined
    w.close()


def test_the_leader_doesnt_invite_in_a_fight(tmp_path):
    w, mine = sides(tmp_path, "Roland")
    rpc = Rpc()
    lich = {**monster(0x300, "a lich", 24, 6), "attacking_me": True}
    assert asyncio.run(gameparty.GameParty(rpc, mine).look(seen_by("Roland", [companion("Isolde", 2), lich]))) is None
    assert rpc.party() == []
    w.close()


def test_a_member_accepts_its_own_leader_and_declines_anyone_else(tmp_path):
    """The leader is known by the serial its own brain reports, not by its name: a stranger can be called
    Roland too."""
    w, mine = sides(tmp_path, "Isolde")
    rpc = Rpc()
    gp = gameparty.GameParty(rpc, mine)
    stranger = {"invited_by": {"serial": 0x777, "name": "Roland"}}
    assert asyncio.run(gp.look(seen_by("Isolde", game=stranger))) == "declined"
    assert rpc.party() == [("decline", 0x777)] and not gp.joined
    ours = {"invited_by": {"serial": SERIALS["Roland"], "name": "Roland"}}
    assert asyncio.run(gp.look(seen_by("Isolde", game=ours))) == "accepted"
    assert rpc.party()[-1] == ("accept", SERIALS["Roland"])
    # In it now.
    game = {"leader": SERIALS["Roland"], "leading": False,
            "members": [{"serial": SERIALS["Roland"], "name": "Roland"}, {"serial": SERIALS["Isolde"], "name": "Isolde"}]}
    assert asyncio.run(gp.look(seen_by("Isolde", game=game))) is None and gp.joined
    # Found in somebody else's party (a stale one, a stranger's): out of it.
    other = {"leader": 0x777, "leading": False, "members": [{"serial": 0x777, "name": "X"}, {"serial": SERIALS["Isolde"], "name": "Isolde"}]}
    assert asyncio.run(gp.look(seen_by("Isolde", game=other))) == "left" and not gp.joined
    w.close()


def test_an_invitation_waits_until_the_leader_is_known(tmp_path):
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Isolde", "Roland", NAMES)  # the leader hasn't reported yet
    rpc = Rpc()
    gp = gameparty.GameParty(rpc, mine)
    ours = {"invited_by": {"serial": SERIALS["Roland"], "name": "Roland"}}
    assert asyncio.run(gp.look(seen_by("Isolde", game=ours))) is None and rpc.party() == []
    party.Party(w, "p1", "Roland", "Roland", NAMES).report(seen_by("Roland"), "warrior")
    assert asyncio.run(gp.look(seen_by("Isolde", game=ours))) == "accepted"
    w.close()


def test_callouts_go_where_the_party_says(tmp_path):
    """Party chat once the member is in the game's party; aloud until then, and aloud as well with "both"."""
    w, mine = sides(tmp_path, "Isolde")
    rpc = Rpc()
    link = mates.Mates(mine)
    link.game = gameparty.GameParty(rpc, mine)

    def said():
        return [(p["verb"], p.get("kind")) for m, p in rpc.acts if m == "act"]

    link.to = "party"
    asyncio.run(link.shout(rpc, "form_up"))
    assert said() == [("say", None)]  # not in the game's party yet
    link.game.joined = True
    asyncio.run(link.shout(rpc, "go_in"))
    assert said()[-1] == ("party", "say") and len(said()) == 2
    link.to = "both"
    asyncio.run(link.shout(rpc, "form_up"))
    assert said()[-2:] == [("party", "say"), ("say", None)]
    link.to = "say"
    asyncio.run(link.shout(rpc, "go_in"))
    assert said()[-1] == ("say", None)
    w.close()


def test_a_fleet_file_turns_the_games_party_on(tmp_path, monkeypatch):
    f = tmp_path / "p.toml"
    body = ('name = "p"\nparty = true\nleader = "A"\n{top}[[agent]]\nname = "A"\naccount = "a"\npassword = "a"\nport = 1\n'
            'goal = "hunt"\n[[agent]]\nname = "B"\naccount = "b"\npassword = "b"\nport = 2\n')
    f.write_text(body.format(top=""))
    fl = fleet.load(f)
    assert not fl.game_party and fl.callouts_to == "say"
    f.write_text(body.format(top='game_party = true\ncallouts_to = "party"\n'))
    fl = fleet.load(f)
    started = []
    monkeypatch.setattr(fleet.subprocess, "Popen", lambda cmd, **kw: started.append(cmd))
    fleet.start_brain(fl, fl.agents[1], tmp_path)
    assert "--game-party" in started[0] and started[0][started[0].index("--callouts-to") + 1] == "party"
    f.write_text(body.format(top='callouts_to = "everyone"\n'))
    try:
        fleet.load(f)
        raise AssertionError("a bad callouts_to should be refused")
    except ValueError as e:
        assert "callouts_to" in str(e)
