"""A companion hears its leader through walls (cuo-cym.28): the leader's lines its own client didn't get, passed on
from the leader's client."""

import asyncio

from uo_brain import fleet, relay


class Client:
    """A client's journal RPC: lines are added as the game would, and `hear` adds a passed-on one."""

    def __init__(self, player: str, serial: int, x: int, y: int):
        self.player, self.serial, self.x, self.y = player, serial, x, y
        self.lines: list[dict] = []
        self.heard: list[dict] = []
        self.closed = False

    def add(self, name: str, text: str, source: str = "speech", type: str = "regular", **extra) -> None:
        self.lines.append({"seq": len(self.lines) + 1, "name": name, "text": text, "source": source, "type": type,
                           **extra})

    async def call(self, method: str, **p):
        if method == "journal":
            return {"player": self.player, "serial": self.serial, "x": self.x, "y": self.y, "map": 0,
                    "journal_seq": len(self.lines), "journal": [e for e in self.lines if e["seq"] > p["since"]]}
        assert method == "hear"
        self.heard.append(p)
        self.add(p["name"], p["text"], type=p["type"], relayed=True)
        return {"seq": len(self.lines)}

    async def close(self):
        self.closed = True


def setup(own_at=(1400, 1500), leader_at=(1403, 1500)):
    leader = Client("Roland", 0x1234, *leader_at)
    me = Client("Isolde", 0x2222, *own_at)
    clients = {5661: leader, 5662: me, 5670: Client("Garrick", 0x3333, 0, 0)}
    log: list[dict] = []

    async def connect(port):
        return clients[port]

    r = relay.Relay("Roland", me, me_port=5662, log=log.append, connect=connect, ports=lambda: sorted(clients))
    return r, leader, me, log


def steps(r, *times):
    async def go():
        for t in times:
            await r.step(now=t)
    asyncio.run(go())


def test_a_line_the_wall_kept_from_it_is_passed_on_within_earshot():
    r, leader, me, log = setup()
    steps(r, 0.0)
    assert r.port == 5661  # found by its character's name, not its own client or another's
    leader.add("Roland", "Wystan, wait for me at the inn.", source="self")
    steps(r, 1.0, 2.0)  # said at 1.0, its grace not over
    assert me.heard == []
    steps(r, 2.6)
    assert [h["text"] for h in me.heard] == ["Wystan, wait for me at the inn."]
    assert me.heard[0]["serial"] == 0x1234 and me.heard[0]["name"] == "Roland"
    assert r.relayed == 1 and log[-1]["event"] == "relayed" and log[-1]["tiles"] == 3


def test_a_line_its_own_client_heard_isnt_passed_on_again():
    r, leader, me, _ = setup()
    steps(r, 0.0)
    leader.add("Roland", "Follow me.", source="self")
    me.add("Roland", "Follow me.")
    steps(r, 1.0, 3.0)
    assert me.heard == [] and r.direct == 1


def test_beyond_earshot_nothing_is_passed_on_and_a_yell_carries_further():
    r, leader, me, _ = setup(leader_at=(1417, 1500))  # 17 tiles: past speech, within a yell
    steps(r, 0.0)
    leader.add("Roland", "Come here.", source="self")
    leader.add("Roland", "TO ME!", source="self", type="yell")
    steps(r, 1.0, 3.0)
    assert [h["text"] for h in me.heard] == ["TO ME!"] and r.out_of_earshot == 1


def test_party_chat_and_others_lines_arent_passed_on():
    r, leader, me, _ = setup()
    steps(r, 0.0)
    leader.add("Roland", "Off to the inn.", source="self", type="party")  # party chat reaches the party anyway
    leader.add("Brigand", "Your gold!", source="speech")
    steps(r, 1.0, 3.0)
    assert me.heard == []


def test_lines_said_before_it_found_the_leader_dont_count():
    r, leader, me, _ = setup()
    leader.add("Roland", "Before the fleet came.", source="self")
    steps(r, 0.0, 2.0)
    assert me.heard == []


def test_the_leader_logged_out_its_client_is_looked_for_again():
    r, leader, me, log = setup()
    steps(r, 0.0)
    leader.player = "nobody"
    steps(r, 1.0)
    assert r.rpc is None and log[-1]["event"] == "lost"
    leader.player = "Roland"
    steps(r, 5.0)  # looked for again only every FIND_EVERY_S
    assert r.rpc is None
    steps(r, 1.0 + relay.FIND_EVERY_S)
    assert r.rpc is leader


def test_the_fleet_file_turns_it_off_or_names_the_port(tmp_path, monkeypatch):
    f = tmp_path / "p.toml"
    f.write_text('party = true\nplan = "player"\nleader = "Roland"\nhear_leader = false\nleader_port = 5661\n'
                 '[[agent]]\nname = "Isolde"\naccount = "a"\npassword = "a"\nport = 5662\n')
    fl = fleet.load(f)
    assert fl.hear_leader is False and fl.leader_port == 5661
    seen = []
    monkeypatch.setattr(fleet.subprocess, "Popen", lambda cmd, **_: seen.append(cmd))
    fleet.start_brain(fl, fl.agents[0], tmp_path)
    assert "--no-hear-leader" in seen[0] and seen[0][seen[0].index("--leader-port") + 1] == "5661"
