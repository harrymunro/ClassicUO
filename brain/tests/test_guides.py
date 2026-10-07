import asyncio
import json
import sys

import pytest

from uo_brain import cli, guides, llm
from uo_brain.world import World

PAGE = """<html><head><title>Britain - UOGuide</title><script>var x = "not text";</script></head>
<body><nav>Main page | Random page</nav>
<h1>Britain</h1><p>Britain is the capital of  Britannia.</p>
<p>The <b>West Britain Bank</b> is at 1434, 1699.</p>
<table><tr><th>Shop</th><th>Where</th></tr><tr><td>Healer</td><td>near the castle</td></tr></table>
<footer>Privacy policy</footer></body></html>"""


def openrouter_reply(notes, cost=0.0123, cached=0):
    return {
        "id": "gen-1", "model": "anthropic/claude-sonnet-5.5",
        "choices": [{"finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "call_1", "type": "function",
                            "function": {"name": "add_notes", "arguments": json.dumps({"notes": notes})}}]}}],
        "usage": {"prompt_tokens": 1500, "completion_tokens": 300, "total_tokens": 1800, "cost": cost,
                  "prompt_tokens_details": {"cached_tokens": cached}},
    }


class FakeChat:
    def __init__(self, notes):
        self.notes = notes
        self.calls = []

    async def __call__(self, messages, **kw):
        self.calls.append({"messages": messages, **kw})
        return llm.parse_response(openrouter_reply(self.notes), kw.get("model") or "fake", 12.0)


@pytest.fixture
def w(tmp_path):
    with World.open("test", root=tmp_path / "worlds") as w:
        yield w


def test_page_text_keeps_content_and_drops_chrome():
    title, text = guides.page_text(PAGE)
    assert title == "Britain - UOGuide"
    assert "Britain is the capital of Britannia." in text
    assert "Healer | near the castle" in text
    assert "not text" not in text and "Random page" not in text and "Privacy" not in text


def test_cache_marks_go_on_the_system_prompt_only():
    msgs = [{"role": "system", "content": "rules"}, {"role": "user", "content": "hi"}]
    marked = llm.with_cache_marks(msgs)
    assert marked[0]["content"] == [{"type": "text", "text": "rules", "cache_control": {"type": "ephemeral"}}]
    assert marked[1] == msgs[1] and msgs[0]["content"] == "rules"  # input left alone
    parts = llm.with_cache_marks([{"role": "system", "content": [{"type": "text", "text": "a"},
                                                                   {"type": "text", "text": "b"}]}])[0]["content"]
    assert "cache_control" not in parts[0] and parts[1]["cache_control"] == {"type": "ephemeral"}


def test_request_shape(monkeypatch):
    monkeypatch.delenv("PLANNER_MODEL", raising=False)
    body = llm.build_request([{"role": "user", "content": "x"}], tools=[guides.ADD_NOTES], tool_choice="add_notes")
    assert body["model"] == "anthropic/claude-sonnet-5.5"
    assert body["usage"] == {"include": True}
    assert body["tool_choice"] == {"type": "function", "function": {"name": "add_notes"}}
    assert llm.build_request([], tool_choice="auto")["tool_choice"] == "auto"
    monkeypatch.setenv("PLANNER_MODEL", "anthropic/claude-haiku-4.5")
    assert llm.build_request([])["model"] == "anthropic/claude-haiku-4.5"
    assert llm.build_request([], model="x/y")["model"] == "x/y"


def test_parse_response_and_usage():
    r = llm.parse_response(openrouter_reply([{"text": "a", "area": None, "tags": []}], cost=0.01, cached=1024),
                           "requested", 250.0)
    assert r.tool_calls[0].name == "add_notes" and r.tool_calls[0].arguments["notes"][0]["text"] == "a"
    assert r.message["tool_calls"][0]["id"] == "call_1" and r.finish_reason == "tool_calls"
    u = r.usage
    assert (u.model, u.prompt_tokens, u.completion_tokens, u.cached_tokens, u.cost) == \
        ("anthropic/claude-sonnet-5.5", 1500, 300, 1024, 0.01)
    total = u + llm.LlmUsage(model="m", prompt_tokens=10, cost=None)
    assert total.prompt_tokens == 1510 and total.cost == 0.01 and total.calls == 2
    assert llm.tool_result(r.tool_calls[0], {"ok": True}) == {"role": "tool", "tool_call_id": "call_1",
                                                               "content": '{"ok": true}'}
    with pytest.raises(llm.LlmError):
        llm.parse_response({"error": {"message": "no credits", "code": 402}}, "m")


def test_chat_posts_once_and_returns_usage(monkeypatch):
    sent = {}

    def fake_post(body, key, timeout, retries):
        sent.update(body=body, key=key)
        return openrouter_reply([], cost=0.002)

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(llm, "_post", fake_post)
    r = asyncio.run(llm.chat([{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
                             tools=[guides.ADD_NOTES], tool_choice="add_notes"))
    assert sent["key"] == "sk-test"
    assert sent["body"]["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert r.usage.cost == 0.002 and "sk-test" not in json.dumps(r.usage.to_log())


def test_missing_key_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(llm, "BRAIN_DIR", tmp_path)
    with pytest.raises(llm.LlmError, match="OPENROUTER_API_KEY"):
        llm.api_key()


def test_import_guide_from_a_file(w, tmp_path):
    page = tmp_path / "britain.html"
    page.write_text(PAGE)
    fake = FakeChat([
        {"text": "The West Britain Bank is at 1434, 1699.", "area": "Britain", "tags": ["Location", "vendor"]},
        {"text": "A healer stands near Britain castle.", "area": None, "tags": ["vendor"]},
        {"text": "", "area": None, "tags": []},
    ])
    out = asyncio.run(guides.import_guide(w, str(page), area="Britain", chat_fn=fake, model="m/x"))
    assert out["source"] == f"guide:{page.resolve().as_uri()}" and out["title"] == "Britain - UOGuide"
    assert [n["area"] for n in out["notes"]] == ["Britain", "Britain"]  # --area fills the gaps
    assert out["usage"]["cost"] == 0.0123
    call = fake.calls[0]
    assert call["tool_choice"] == "add_notes" and call["model"] == "m/x"
    assert "Page: Britain - UOGuide" in call["messages"][1]["content"]
    assert "The page is about Britain" in call["messages"][1]["content"]
    found = w.notes(keywords="bank")
    assert found[0]["source"] == out["source"] and found[0]["tags"] == ["location", "vendor"]
    asyncio.run(guides.import_guide(w, str(page), area="Britain", chat_fn=fake))  # replaces, no duplicates
    assert len(w.notes(area="Britain", limit=50)) == 2


def test_fill_gaps_is_marked_unverified(w):
    fake = FakeChat([{"text": "Skeletons and zombies roam the Britain graveyard.", "area": "Somewhere else",
                      "tags": ["spawn"]}])
    out = asyncio.run(guides.fill_gaps(w, "Britain Graveyard", chat_fn=fake))
    assert out["notes"][0]["area"] == "Britain Graveyard"
    assert set(out["notes"][0]["tags"]) == {"spawn", "unverified"}
    w.add_note("Our own note.", area="Britain Graveyard")
    asyncio.run(guides.fill_gaps(w, "Britain Graveyard", chat_fn=fake))
    notes = w.notes(area="Britain Graveyard", limit=50)
    assert sorted(n["source"] for n in notes) == ["model:unverified", "note"]
    assert "Felucca" in fake.calls[0]["messages"][0]["content"]


def test_cli_import_guide_without_network(w, tmp_path, monkeypatch, capsys):
    page = tmp_path / "notes.txt"
    page.write_text("Liches in Covetous level 3 cast Energy Bolt.")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(llm, "_post", lambda body, key, timeout, retries: openrouter_reply(
        [{"text": "Liches on Covetous level 3 cast Energy Bolt.", "area": "Covetous", "tags": ["creature"]}]))
    monkeypatch.setattr(sys, "argv", ["uo-brain", "world", "--shard", "test", "--root", str(tmp_path / "worlds"),
                                      "import-guide", str(page)])
    cli.main()
    out = json.loads(capsys.readouterr().out)
    assert out["notes"][0]["area"] == "Covetous" and out["title"] == "notes"
    assert w.notes(keywords="lich")[0]["source"].startswith("guide:file://")


def test_forced_tool_falls_back_to_auto(monkeypatch):
    bodies = []

    def fake_post(body, key, timeout, retries):
        bodies.append(body)
        if body.get("tool_choice") != "auto":
            raise llm.LlmError('OpenRouter HTTP 400: {"raw": "tool_choice: type \\"tool\\" and \\"any\\" are not '
                               'supported for this model."}', 400)
        return openrouter_reply([{"text": "a fact", "area": None, "tags": []}])

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(llm, "_post", fake_post)
    monkeypatch.setattr(llm, "_AUTO_ONLY", set())
    ask = llm.chat([{"role": "user", "content": "x"}], tools=[guides.ADD_NOTES], tool_choice="add_notes",
                   model="anthropic/claude-sonnet-5.5")
    assert asyncio.run(ask).tool_calls[0].name == "add_notes"
    assert [b["tool_choice"] for b in bodies] == [{"type": "function", "function": {"name": "add_notes"}}, "auto"]
    asyncio.run(llm.chat([{"role": "user", "content": "x"}], tools=[guides.ADD_NOTES], tool_choice="add_notes",
                         model="anthropic/claude-sonnet-5.5"))
    assert bodies[2]["tool_choice"] == "auto"  # remembered: no second refusal


def test_notes_written_as_text_are_still_read():
    reply = {"model": "m", "choices": [{"finish_reason": "stop", "message": {
        "role": "assistant", "content": 'Here you go:\n{"notes": [{"text": "Orcs camp north of Britain.", '
                                        '"area": "Britain", "tags": ["spawn"]}]}'}}], "usage": {}}
    notes = guides._notes_from(llm.parse_response(reply, "m"))
    assert notes == [{"text": "Orcs camp north of Britain.", "area": "Britain", "tags": ["spawn"]}]


def test_an_empty_reply_keeps_the_old_notes(w, tmp_path):
    page = tmp_path / "page.txt"
    page.write_text("Some guide text.")
    asyncio.run(guides.import_guide(w, str(page), chat_fn=FakeChat([{"text": "Kept.", "area": "null", "tags": []}])))
    with pytest.raises(ValueError, match="no notes"):
        asyncio.run(guides.import_guide(w, str(page), chat_fn=FakeChat([])))
    kept = w.notes(keywords="kept")
    assert len(kept) == 1 and "area" not in kept[0]  # "null" from the model means no area
