"""Headless clients (cuo-1k7): the fleet file key, the client command line, and the load report."""

import json

from uo_brain import client, fleet

T0 = 1_791_400_000.0


def test_a_fleet_file_says_which_clients_run_headless(tmp_path):
    f = tmp_path / "f.toml"
    f.write_text('headless = true\n'
                 '[[agent]]\nname="Pat"\naccount="a"\npassword="a"\nport=5610\ngoal="g"\nheadless=false\n'
                 '[[agent]]\nname="Npc"\naccount="b"\npassword="b"\nport=5611\ngoal="g"\n')
    assert [a.headless for a in fleet.load(f).agents] == [False, True]
    f.write_text('[[agent]]\nname="Pat"\naccount="a"\npassword="a"\nport=5610\ngoal="g"\n')
    assert fleet.load(f).agents[0].headless is False  # windowed unless the file says so


def test_a_headless_client_is_started_with_the_flag_and_without_its_own_brain():
    assert client.command(5610) == ["./cuo", "-agent_port", "5610", "-agent_start_brain", "false"]
    assert client.command(5611, headless=True)[-1] == "-headless"


def test_the_fleet_report_marks_headless_clients_and_their_loop_rate(tmp_path):
    (tmp_path / "fleet.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"t": T0, "type": "fleet", "name": "two", "agents": [{"name": "Pat", "profile": "default", "headless": False},
                                                             {"name": "Npc", "profile": "npc", "headless": True}]},
        {"t": T0 + 30, "type": "load", "processes": {"Pat/client": {"cpu": 15.0, "mb": 400.0, "footprint_mb": 700.0},
                                                     "Npc/client": {"cpu": 4.0, "mb": 300.0}},
         "loops": {"Pat/client": {"ups": 800, "draws": 60, "headless": False},
                   "Npc/client": {"ups": 760, "draws": 0, "headless": True}}},
        {"t": T0 + 60, "type": "load", "processes": {"Pat/client": {"cpu": 17.0, "mb": 410.0},
                                                     "Npc/client": {"cpu": 6.0, "mb": 310.0}},
         "loops": {"Npc/client": {"ups": 740, "draws": 0, "headless": True}}},  # Pat didn't answer this time
    ]) + "\n")
    r = fleet.report(tmp_path)
    assert r["load"]["Npc/client"] == {"cpu_avg": 5.0, "cpu_max": 6.0, "mb_max": 310.0, "headless": True,
                                       "ups_avg": 750, "ups_min": 740}
    assert r["load"]["Pat/client"]["ups_avg"] == 800 and "headless" not in r["load"]["Pat/client"]
    assert r["load"]["Pat/client"]["footprint_mb_max"] == 700.0
    md = fleet.markdown(r)
    assert "| Npc/client (headless) | 5.0 | 6.0 | 310.0 |  | 750 (740) |" in md
    assert "| Pat/client | 16.0 | 17.0 | 410.0 | 700.0 | 800 (800) |" in md


def test_footprints_are_read_from_macos_footprint(monkeypatch):
    out = ("cuo [86229]: 64-bit    Footprint: 740215280 B (16384 bytes per page)\n"
           "    phys_footprint: 740215280 B\n"
           "cuo [86230]: 64-bit    Footprint: 376342904 B (16384 bytes per page)\n"
           "Summary Footprint: 1116246888 B\n")
    monkeypatch.setattr(fleet.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": out})())
    assert fleet.footprints([86229, 86230]) == {86229: 705.9, 86230: 358.9}
