import asyncio
import json
import sys

import pytest

from uo_brain import cli, llm, logs, review

T0 = 1_791_340_000.0


def hostile(name, distance="adjacent, within weapon reach", target=False):
    return {"id": "t1", "name": name, "health": "unhurt", "distance": distance, "your_current_target": target}


def decision(t, hp, intent="fight", hostiles=(), actions=(), results=None, note=None, confidence=0.9, events=()):
    return {"type": "decision", "t": T0 + t, "judge": "jev/openrouter",
            "state": {"you": {"health": f"wounded ({hp}%)", "bandages_left": 40, "heal_potions_left": 2,
                              "heal_potion_ready": "ready now", "fighting": "nobody", "supplies": "plenty"},
                      "hostile_creatures": list(hostiles), "corpses_not_yet_looted": [],
                      "recent_events": list(events)},
            "answers": {"choices": {}}, "intent": intent, "confidence": confidence, "gated": False,
            "actions": list(actions), "results": results if results is not None else [{"status": "done"}] * len(actions),
            "note": note or f"{intent} ({confidence})", "target": None, "spell": None}


def summary(t, **stats):
    return {"type": "summary", "t": T0 + t, "minutes": 1.0, "client_stats": {"kills": 0, "deaths": 0, **stats}}


def session_log():
    orc = hostile("an orc")
    return [
        {"type": "strategy", "t": T0, "text": "Fight on.", "knobs": {"allow_flee": True, "aggression": 0.5,
                                                                      "target_priority": "current_first",
                                                                      "looting": "valuables", "opening_spell": "",
                                                                      "main_spell": ""}},
        # Run 1: loots with an orc adjacent, health falls, dies.
        decision(1, 90, hostiles=[orc], note="keep fighting an orc (0.90)"),
        decision(2, 80, intent="loot", hostiles=[orc], actions=[{"verb": "loot", "target": 5}], note="loot a corpse"),
        decision(3, 50, hostiles=[orc]),
        decision(4, 20, hostiles=[orc], confidence=0.4),
        decision(5, 8, hostiles=[orc], confidence=0.35,
                 events=["Your skill in Tactics has increased by 0.1.  It is now 50.1.", "You begin applying the bandages."]),
        summary(8, kills=2, deaths=1, bandages=3, flees=0),
        {"type": "error", "t": T0 + 9, "error": "HTTP 429: rate limited"},
        # Run 2: a flee at low health, then recovered.
        decision(20, 100, intent="seek", hostiles=[hostile("an ettin", "nearby")]),
        decision(21, 28, intent="flee", hostiles=[hostile("an ettin")], actions=[{"verb": "flee", "target": 6}]),
        decision(22, 26, intent="flee", hostiles=[hostile("an ettin")]),
        decision(30, 80, intent="rest"),
        summary(40, kills=1, deaths=0, flees=1, heal_potions=1),
    ]


def write(path, recs):
    path.write_text("".join(json.dumps(r) + "\n" for r in recs))
    return path


def test_digest_picks_out_deaths_loots_flees_and_low_health():
    d = review.digest(session_log(), "s.jsonl")
    assert d["strategy"] == {"text": "Fight on.", "read_as": "flees when losing, aggression 0.50, current first, "
                                                            "loots valuables"}
    assert d["totals"]["kills"] == 3 and d["totals"]["deaths"] == 1 and d["totals"]["judge_errors"] == 1
    assert d["intents"]["fight"] == {"count": 4, "average_confidence": 0.64, "below_0.5": 2}

    [death] = d["deaths"]["first"]
    assert death["time"] == logs.clock(T0 + 5)
    assert len(death["decisions_before"]) == 5 and "near death" not in death["decisions_before"][0]
    assert "wounded (8%)" in death["decisions_before"][-1] and "an orc adjacent" in death["decisions_before"][-1]
    assert "loot done" in death["decisions_before"][1]
    assert death["journal"] == ["You begin applying the bandages."]  # skill gains left out

    loot = d["looting_with_monsters_close"]
    assert loot["count"] == 1 and loot["first"][0]["close"] == ["an orc adjacent"]
    assert loot["first"][0]["died_within_s"] == 3

    flees = d["flees"]
    assert flees["count"] == 1 and flees["first"][0]["decisions"] == 2 and flees["first"][0]["died_within_s"] is None
    assert [s["then"] for s in d["low_health"]["first"]] == ["died", "recovered"]
    assert d["supplies"]["bandages"] == {"first": 40, "last": 40, "lowest": 40}
    assert d["judge_errors"]["examples"] == ["HTTP 429: rate limited"]


def test_a_death_is_placed_at_a_gap_after_near_death():
    ds = [decision(0, 60), decision(1, 12), decision(30, 100), decision(31, 70)]  # dead, resurrected, fought on
    run = logs.Run(ds, summary(40, deaths=1))
    assert review.death_points(run) == [1]
    healthy = logs.Run([decision(0, 90), decision(1, 80)], summary(5, deaths=1))
    assert review.death_points(healthy) == [1]  # nothing near death: the run's last decision stands in
    assert review.death_points(logs.Run(ds, summary(40))) == []


def test_the_digest_stays_small_for_a_long_log():
    long = []
    for i in range(400):
        long += [decision(i * 10 + 1, 20, hostiles=[hostile("an orc")], actions=[{"verb": "loot", "target": i}]),
                 decision(i * 10 + 2, 25, intent="flee", hostiles=[hostile("an orc")]),
                 decision(i * 10 + 3, 90, intent="rest"),
                 summary(i * 10 + 4, deaths=1)]
    d = review.digest(long)
    assert d["totals"]["decisions"] == 1200 and d["deaths"]["count"] == 400 and len(d["deaths"]["first"]) == 8
    assert len(json.dumps(d)) < 16_000


def propose_reply(lines, content=None, cost=0.0042):
    msg = {"role": "assistant", "content": content}
    calls = []
    if lines is not None:
        args = {"lines": lines}
        calls = [llm.ToolCall("call_1", "propose", args, json.dumps(args))]
        msg["tool_calls"] = [{"id": "call_1", "type": "function",
                              "function": {"name": "propose", "arguments": json.dumps(args)}}]
    return llm.ChatResult(msg, content, calls, "tool_calls" if calls else "stop",
                          llm.LlmUsage(model="anthropic/claude-sonnet-5.5", prompt_tokens=2500, completion_tokens=300,
                                       cost=cost))


def scripted(*replies):
    seen = []
    queue = list(replies)

    async def chat(messages, **kw):
        seen.append({"messages": messages, **kw})
        return queue.pop(0)

    chat.seen = seen
    return chat


LINES = [
    {"line": "Don't loot while a monster is adjacent.", "evidence": "died 3 s after looting with an orc adjacent",
     "confidence": 0.8},
    {"line": "fight on.", "evidence": "already says so", "confidence": 0.9},
    {"line": "Flee from an ettin when badly wounded.", "evidence": "one flee at 28% (13:02), survived",
     "confidence": 1.7},
    {"line": "Rest after each fight.", "evidence": "only 1 rest decision"},
]


def test_review_saves_numbered_proposals(tmp_path):
    log = write(tmp_path / "s.jsonl", session_log())
    chat = scripted(propose_reply(LINES))
    rv = asyncio.run(review.review(log, chat_fn=chat, model="m/x"))
    call = chat.seen[0]
    assert call["tool_choice"] == "auto" and call["model"] == "m/x"  # Sonnet refuses a forced tool
    assert [t["function"]["name"] for t in call["tools"]] == ["propose"]
    assert "calling the propose tool" in call["messages"][0]["content"]
    assert '"Fight on."' in call["messages"][1]["content"]
    # The line the strategy already has is dropped; confidence is clamped, and a missing one stays missing.
    assert [(p["n"], p["line"], p["confidence"]) for p in rv["proposals"]] == [
        (1, "Don't loot while a monster is adjacent.", 0.8), (2, "Flee from an ettin when badly wounded.", 1.0),
        (3, "Rest after each fight.", None)]
    saved = json.loads((tmp_path / "s.review.json").read_text())
    assert saved["proposals"] == rv["proposals"] and saved["usage"]["cost"] == 0.0042
    assert saved["digest"]["totals"]["deaths"] == 1
    assert json.loads(saved["reply"]["tool_calls"][0])["lines"][1]["confidence"] == 0.9  # as the model wrote it
    text = review.show(saved, saved=True)
    assert " 1. Don't loot while a monster is adjacent.  (confidence 0.80)" in text and "--accept N" in text
    assert "(confidence not given)" in text


def test_a_reply_without_the_tool_is_asked_again(tmp_path):
    log = write(tmp_path / "s.jsonl", session_log())
    chat = scripted(propose_reply(None, content="I would suggest fleeing more."), propose_reply([]))
    rv = asyncio.run(review.review(log, chat_fn=chat))
    assert rv["proposals"] == [] and rv["usage"]["calls"] == 2
    assert chat.seen[1]["messages"][-1]["content"].startswith("Record your proposals by calling the propose tool")
    text_json = propose_reply(None, content='Here: {"lines": [{"line": "Rest when wounded.", "evidence": "x", '
                                            '"confidence": 0.5}]}')
    assert review.lines_from(text_json)[0]["line"] == "Rest when wounded."
    with pytest.raises(ValueError, match="nothing was saved"):
        asyncio.run(review.review(log, chat_fn=scripted(propose_reply(None), propose_reply(None))))


class FakeRpc:
    def __init__(self, strategy=""):
        self.strategy = strategy
        self.calls = []

    async def call(self, method, **params):
        self.calls.append((method, params))
        assert method == "strategy"
        if "add" in params:
            self.strategy = f"{self.strategy}\n{params['add']}" if self.strategy else params["add"]
        return {"strategy": self.strategy}


def test_accept_adds_saved_lines_once_without_the_model(tmp_path):
    log = write(tmp_path / "s.jsonl", session_log())
    asyncio.run(review.review(log, chat_fn=scripted(propose_reply(LINES))))
    rpc = FakeRpc("Fight on.")
    said, now = asyncio.run(review.accept(rpc, log, [2, 1, 2]))
    assert now == "Fight on.\nFlee from an ettin when badly wounded.\nDon't loot while a monster is adjacent."
    assert said == ["2. added: Flee from an ettin when badly wounded.", "1. added: Don't loot while a monster is adjacent."]
    saved = json.loads(review.review_path(log).read_text())
    assert [bool(p["accepted"]) for p in saved["proposals"]] == [True, True, False]
    said, _ = asyncio.run(review.accept(rpc, log, [1]))
    assert said == ["1. already in the strategy: Don't loot while a monster is adjacent."]
    assert sum(1 for m, p in rpc.calls if "add" in p) == 2
    with pytest.raises(ValueError, match="no proposal 7"):
        asyncio.run(review.accept(rpc, log, [7]))
    with pytest.raises(ValueError, match="run uo-brain review"):
        asyncio.run(review.accept(rpc, tmp_path / "other.jsonl", [1]))


def test_cli_reviews_once_then_shows_the_saved_review(tmp_path, monkeypatch, capsys):
    log = write(tmp_path / "s.jsonl", session_log())
    posts = []

    def fake_post(body, key, timeout, retries):
        posts.append(body)
        args = json.dumps({"lines": LINES[:1]})
        return {"model": "anthropic/claude-sonnet-5.5", "choices": [{"finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "propose", "arguments": args}}]}}],
            "usage": {"prompt_tokens": 2400, "completion_tokens": 200, "cost": 0.011}}

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(llm, "_post", fake_post)
    for argv in (["review", str(log)], ["review", str(log)]):
        monkeypatch.setattr(sys, "argv", ["uo-brain", *argv])
        cli.main()
    out = capsys.readouterr().out
    assert len(posts) == 1 and posts[0]["tool_choice"] == "auto"
    assert out.count(" 1. Don't loot while a monster is adjacent.") == 2 and "($0.0110)" in out
    assert "Saved review" in out and "--again" in out
    monkeypatch.setattr(sys, "argv", ["uo-brain", "review", str(log), "--digest"])
    cli.main()
    assert json.loads(capsys.readouterr().out)["totals"]["deaths"] == 1
