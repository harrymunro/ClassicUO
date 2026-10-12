"""The planner's running summary of a session, with a memory flush first (cuo-6w8.4).

The planner sees its last steps word for word. Without this, a step that dropped out of
that window was gone ("(40 earlier goals not shown)"), so a long session lost its arc.
Now the steps that leave the window are folded into a summary of the session so far,
bounded to a couple of hundred words, which goes in the user turn while the system
prompt stays fixed and cached (Hermes keeps a frozen snapshot for the same reason).

Folding is one call to the planner's cheap model (kind `compact`, Haiku by default), made
when BATCH steps have left the window rather than at every step. The same call is the
memory flush (OpenClaw asks before compaction what is worth keeping): it names up to three
things from the steps being folded to keep in the character's memory, besides what code
keeps already (hunts, leaves, deaths, refusals). When a session ends, everything is folded
once more and the summary itself is kept as a memory, so a later session can recall it.
"""

import time
from collections.abc import Callable
from typing import Any

from . import costs, llm
from . import memory as memories

KEEP_RECENT = 12       # steps the planner sees word for word
BATCH = 4              # fold when this many steps have left the window
MAX_WORDS = 220        # the summary's bound
KEEP_MAX = 3           # memories one fold may keep

SYSTEM = """You keep the running summary of a character's session in the game Ultima Online, for the
planner that chooses the character's goals. You get the summary so far and the next few goals
that are leaving the planner's view, each with what came of it. Call fold exactly once with:

- summary: the whole session so far in at most 200 words, under four headings, each a line or two:
  "Done:" (where it went, hunts with kills, leaves and deaths, what it bought and banked),
  "Going on:" (what the planner is doing now and why), "Failed and why:" (goals that failed, the
  reason), "Left to do:" (what the player's goal still needs). Update the summary so far rather
  than rewriting it; copy place names, coordinates and counts exactly; drop routine detail.
- keep: up to three things from these goals worth remembering in later sessions, each one plain
  sentence with the place and the reason: a lesson (what worked, what to avoid) or an important
  event. Code already keeps every hunt, leave, death and refused goal, so keep only conclusions it
  can't draw. An empty list is fine and usual.
"""


def tool() -> dict[str, Any]:
    return {"type": "function", "function": {
        "name": "fold",
        "description": "The new summary of the session so far, and what to keep in the character's memory.",
        "parameters": {"type": "object", "properties": {
            "summary": {"type": "string", "description": "The whole session so far, at most 200 words."},
            "keep": {"type": "array", "maxItems": KEEP_MAX, "items": {"type": "object", "properties": {
                "text": {"type": "string"},
                "kind": {"type": "string", "enum": ["lesson", "event"]},
                "area": {"type": "string"},
                "importance": {"type": "integer", "description": "1 (minor) to 5."}},
                "required": ["text", "kind"]}}},
            "required": ["summary", "keep"], "additionalProperties": False}}}


def bound(text: str, words: int = MAX_WORDS) -> str:
    w = " ".join(str(text).split()).split(" ")
    return " ".join(w[:words]) + (" …" if len(w) > words else "")


class Folder:
    """The running summary of one planner's session."""

    def __init__(self, memory: "memories.Memory | None" = None, chat_fn: llm.ChatFn = llm.chat,
                 log: Callable[[dict[str, Any]], None] | None = None, keep_recent: int = KEEP_RECENT,
                 batch: int = BATCH):
        self.memory = memory
        self.chat = chat_fn
        self.log = log or (lambda _: None)
        self.keep_recent = keep_recent
        self.batch = batch
        self.summary = ""
        self.folded = 0           # history[:folded] is in the summary
        self.calls = 0
        self.cost = 0.0
        self.kept = 0

    def due(self, history: list[Any]) -> bool:
        return len(history) - self.folded - self.keep_recent >= self.batch

    async def maybe_fold(self, history: list[Any], goal: str) -> bool:
        """Fold the steps that have left the window, when enough have. Whether it folded."""
        if not self.due(history):
            return False
        return await self.fold(history, goal, upto=len(history) - self.keep_recent)

    async def fold(self, history: list[Any], goal: str, upto: int, final: bool = False) -> bool:
        steps = history[self.folded:upto]
        if not steps:
            return False
        if costs.ledger.over_budget() and not final:
            return False  # over a budget the window just grows a little; the next fold catches up
        lines = "\n".join(f"{self.folded + i + 1}. {s.line()}" for i, s in enumerate(steps))
        # What the character remembers already, so the flush doesn't keep the same lesson again in other
        # words (a soak's 19 folds kept 19 near-copies of "the graveyard gathers too many undead").
        known = []
        if self.memory is not None and self.memory.character:
            known = [r["text"][:200] for r in self.memory.list(min_importance=3, limit=12)]
        user = (f"The player's goal: {goal or '(none for this session)'}\n\n"
                f"The summary so far:\n{self.summary or '(nothing yet: these are the first goals)'}\n\n"
                f"Goals leaving the planner's view, oldest first:\n{lines}\n\n"
                + ("Already in the character's memory (keep nothing that says the same):\n"
                   + "\n".join(f"- {k}" for k in known) + "\n\n" if known else "")
                + "Call fold.")
        rec: dict[str, Any] = {"type": "fold", "t": time.time(), "steps": [self.folded + 1, upto], "final": final}
        try:
            with costs.kind("compact"):
                res = await self.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                                      tools=[tool()], tool_choice="fold", model=llm.planner_model(None), max_tokens=1500)
        except llm.LlmError as e:
            rec["error"] = str(e)[:300]
            self.log(rec)
            return False
        call = next((c for c in res.tool_calls if c.name == "fold"), None)
        args = (call.arguments if call else None) or {}
        if not args.get("summary"):
            rec["error"] = "no summary in the reply"
            self.log(rec | {"content": (res.content or "")[:300]})
            return False
        self.summary = bound(args["summary"])
        self.folded = upto
        self.calls += 1
        self.cost += res.usage.cost or 0.0
        kept = []
        for k in (args.get("keep") or [])[:KEEP_MAX]:
            if self.memory is not None and self.memory.character and str(k.get("text") or "").strip():
                try:
                    mid = self.memory.add(str(k["text"]), kind=k.get("kind") if k.get("kind") in ("lesson", "event") else "lesson",
                                          area=k.get("area") or None, source="flush",
                                          importance=int(k.get("importance") or 3))
                    kept.append({"id": mid, "text": k["text"]})
                except (ValueError, TypeError):
                    continue
        self.kept += len(kept)
        rec.update({"summary": self.summary, "summary_words": len(self.summary.split()), "kept": kept,
                    "usage": res.usage.to_log()})
        self.log(rec)
        return True

    async def close(self, history: list[Any], goal: str, character: str | None = None) -> str:
        """At the end of a session: fold everything, and keep the summary as a memory."""
        if len(history) > self.folded:
            await self.fold(history, goal, upto=len(history), final=True)
        if self.summary and self.memory is not None and self.memory.character:
            self.memory.add(f"Session of {time.strftime('%Y-%m-%d %H:%M')}, goal \"{goal or 'long goals'}\": {self.summary}",
                            kind="event", tags=("session",), source="flush", importance=3)
        return self.summary

    def section(self) -> str:
        if not self.summary:
            return ""
        return f"Earlier in this session ({self.folded} goals, summarised):\n{self.summary}"

    def to_log(self) -> dict[str, Any]:
        return {"folds": self.calls, "fold_cost_usd": round(self.cost, 5), "fold_memories_kept": self.kept,
                "summary_words": len(self.summary.split())}

