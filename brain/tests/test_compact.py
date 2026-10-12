import asyncio

from uo_brain import compact, llm, memory, planner
from uo_brain.planner import PlanStep
from uo_brain.world import World

from conftest import SNAPSHOT
from test_planner import FakeSession, call, reply


def fold_reply(summary, keep=()):
    return reply(call("fold", {"summary": summary, "keep": list(keep)}))


def steps(n, start=0):
    return [PlanStep("hunt", {"area": f"Spot {i}", "minutes": 10},
                     {"ok": True, "result": f"hunted Spot {i} for 10 min: {i} kills " + "x" * 300}) for i in range(start, start + n)]


class Chat:
    """Answers fold calls with a numbered summary and planner calls with a rest; records both."""

    def __init__(self, summary_words=20, keep=(), fail=False):
        self.calls, self.summary_words, self.keep, self.fail = [], summary_words, keep, fail

    async def __call__(self, messages, tools=None, **_):
        names = [t["function"]["name"] for t in tools or []]
        self.calls.append({"messages": messages, "tools": names})
        if "fold" in names:
            if self.fail:
                return reply(content="I can't.")
            n = sum(1 for c in self.calls if "fold" in c["tools"])
            return fold_reply(f"summary {n}: " + "word " * self.summary_words, self.keep)
        return reply(call("rest", {"seconds": 30, "why": "w"}))


def test_steps_that_leave_the_window_are_folded_with_a_flush_first(tmp_path):
    w = World(tmp_path / "w.sqlite")
    mem = memory.Memory(w, "Brutus")
    chat = Chat(keep=[{"text": "Spot 2 is crowded in the evening.", "kind": "lesson", "area": "Spot 2"}])
    s = FakeSession(w)
    p = planner.Planner(s, "hunt", chat_fn=chat, escalate=False)
    p.history = steps(15)
    asyncio.run(p.step())  # 15 steps: none folded yet
    assert not any("fold" in c["tools"] for c in chat.calls)
    p.history = steps(16)
    asyncio.run(p.step())
    folds = [c for c in chat.calls if "fold" in c["tools"]]
    assert len(folds) == 1 and "1. hunt(area='Spot 0'" in folds[0]["messages"][1]["content"]
    assert "5. hunt" not in folds[0]["messages"][1]["content"]  # only the four leaving the window
    user = chat.calls[-1]["messages"][1]["content"]
    assert "Earlier in this session (4 goals, summarised):\nsummary 1:" in user
    assert "Spot 0'" not in user and "5. hunt(area='Spot 4'" in user
    assert chat.calls[-1]["messages"][0]["content"] == planner.SYSTEM  # the cached prompt is untouched
    kept = mem.list(source="flush")
    assert [m["text"] for m in kept] == ["Spot 2 is crowded in the evening."] and kept[0]["kind"] == "lesson"
    assert p.summary()["folds"] == 1 and p.summary()["fold_memories_kept"] == 1


def test_the_prompt_stays_bounded_however_long_the_session(tmp_path):
    chat = Chat(summary_words=400)  # a model that writes too much is cut to the bound
    s = FakeSession(World(tmp_path / "w.sqlite"))
    p = planner.Planner(s, "hunt", chat_fn=chat, escalate=False, memory=False)
    sizes = []
    for n in range(0, 200, 7):
        p.history = steps(n) if n <= len(p.history) else p.history + steps(n - len(p.history), len(p.history))
        asyncio.run(p.step())
        sizes.append(len(chat.calls[-1]["messages"][1]["content"]))
    assert len(p.folder.summary.split()) <= compact.MAX_WORDS + 1
    assert max(sizes) < 9000 and max(sizes[-10:]) <= max(sizes[5:15]) + 600  # flat once folding starts


def test_a_failed_fold_keeps_the_last_steps_and_says_what_is_missing(tmp_path):
    chat = Chat(fail=True)
    logged = []
    p = planner.Planner(FakeSession(World(tmp_path / "w.sqlite")), "hunt", chat_fn=chat, escalate=False,
                        memory=False, log=logged.append)
    p.history = steps(30)
    asyncio.run(p.step())
    user = chat.calls[-1]["messages"][1]["content"]
    assert "(15 earlier goals not shown)" in user and "16. hunt(area='Spot 15'" in user
    assert any(r["type"] == "fold" and r.get("error") for r in logged)


def test_the_end_of_a_session_keeps_its_summary_as_a_memory(tmp_path):
    w = World(tmp_path / "w.sqlite")
    mem = memory.Memory(w, "Brutus")
    folder = compact.Folder(mem, Chat())
    asyncio.run(folder.close(steps(3), "hunt the graveyard"))
    row = mem.list(source="flush")[0]
    assert row["text"].startswith("Session of ") and 'goal "hunt the graveyard": summary 1:' in row["text"]
    assert "session" in row["tags"]


def test_the_last_session_is_the_newest_one_whatever_its_length(tmp_path):
    w = World(tmp_path / "w.sqlite")
    mem = memory.Memory(w, "Brutus")
    for day in range(8):
        mem.add(f"Session of 2026-10-0{day + 1}: " + ("long " * (40 if day == 7 else 2)) + f"day {day + 1}",
                source="flush", tags=("session",), at=1_790_000_000 + day * 86400)
    p = planner.Planner(FakeSession(w), "hunt", chat_fn=Chat(), escalate=False)
    p.memory.character = "Brutus"
    assert p.last_session().endswith("day 8")


def test_a_fold_is_shown_what_the_character_remembers_already(tmp_path):
    w = World(tmp_path / "w.sqlite")
    mem = memory.Memory(w, "Brutus")
    mem.add("Britain Graveyard gathers five undead at once; pull singles.", kind="lesson", importance=4)
    chat = Chat()
    asyncio.run(compact.Folder(mem, chat).fold(steps(4), "hunt", upto=4))
    user = chat.calls[0]["messages"][1]["content"]
    assert "Already in the character's memory" in user and "gathers five undead" in user
