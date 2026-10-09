from uo_brain import bench


def snap(mobiles, deaths=()):
    return {"player": {"hits": 50, "hits_max": 50, "dead": False}, "mobiles": mobiles, "pets": [],
            "deaths": list(deaths), "agent": {"engaged": 0, "looting": 0}}


def test_the_characters_own_summon_is_no_kill():
    tr = bench.Trace()
    orc = {"serial": 1, "name": "an orc", "monster": True, "distance": 3}
    blades = {"serial": 2, "name": "a blade spirit", "monster": True, "distance": 2}
    tr.add(snap([orc, blades]))                      # first seen red, before the client knows it's ours
    tr.add(snap([orc, dict(blades, summon=True)]))
    tr.add(snap([], deaths=[{"serial": 1, "time_ms": 5}, {"serial": 2, "time_ms": 6}]))
    assert [tr.names[s] for s in tr.kills()] == ["an orc"]
