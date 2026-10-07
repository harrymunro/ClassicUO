"""Every model call, for the client's live view of them (cuo-2e5).

The panel shows the latest fight decision; much more is asked behind it. Each call is turned
into what the player can follow at a glance: per question, the options with Jev's probability
for each and which one it picked, or for a yes/no its probability, the cut that decides it and
the verdict. Fight decisions carry theirs in the `decision` RPC (loop.decision_payload); other
calls (routine hunt questions, world-fact picks, strategy reads, the planner's goals and plan
designs) go to the client as `ai_call` records through `emit`.
"""

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

# Who to send `ai_call` records to: set by whatever owns the client connection (loop.run, the
# autopilot, a session). One brain talks to one client.
sink: Callable[[dict[str, Any]], Awaitable[Any]] | None = None

TITLES = {
    "intent": "what next?", "target": "which creature?", "spell": "which spell?", "corpse": "which corpse?",
    "song": "which song?", "onto": "incite it onto?", "blessing": "which blessing?", "in_danger": "in danger?",
    "leave_now": "leave now?", "pull_back": "call the pet back?", "head_back": "head back to town?",
    "stay_here": "spot still worth it?", "move_spot": "walk elsewhere in the spawn?", "allows_flee": "strategy allows fleeing?",
    "aggression": "how aggressive?", "target_priority": "who first?", "looting": "how much to loot?",
    "opening_spell": "opening spell?", "main_spell": "main spell?",
}


def emit(record: dict[str, Any]) -> None:
    """Send one call to the client, without waiting: the view must never slow a decision."""
    if sink is None:
        return
    try:
        asyncio.get_running_loop().create_task(_send(record))
    except RuntimeError:
        pass  # no event loop (offline tools): nothing to show it on


async def _send(record: dict[str, Any]) -> None:
    try:
        await sink({"t": time.time(), **record})
    except Exception:  # noqa: BLE001 - the view is best effort
        pass


def label(key: str, text: Any) -> str:
    """A short label for an option: its own name when the key is a word ("fight", "provoke"),
    else the start of its description ("t1" -> "Vorgak (an orc)", "s2" -> "Explosion")."""
    if not re.fullmatch(r"[a-z]\d+", key):
        return key.replace("_", " ")
    s = str(text)
    cut = min((i for i in (s.find(":"), s.find(", ")) if i > 0), default=len(s))
    return s[:cut][:32]


def title(key: str, criteria: Any = None) -> str:
    if key in TITLES:
        return TITLES[key]
    if key.startswith("go_"):
        return f"plan: on to '{key[3:]}'?"
    if key.startswith("take_"):
        return "take this item?"
    return key.replace("_", " ") + "?"


def view(questions: dict[str, dict[str, Any]], answers: Any, cuts: dict[str, float] | None = None,
         used: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Each question with its options, Jev's probabilities and its pick; for yes/no questions the
    probability, the cut and the verdict. `used` is what code went with where that can differ
    from Jev's pick (a code rule, a strategy's spell, an unsure answer); `cuts` gives the yes cut
    per Noul (default 0.5)."""
    cuts, used = cuts or {}, used or {}
    out = []
    for key, q in questions.items():
        kind = q.get("type")
        if kind == "choice" and key in answers.choices:
            a = answers.choices[key]
            crit = q.get("criteria") or {}
            options = sorted(({"id": k, "label": label(k, crit.get(k, k)), "p": round(a.probabilities.get(k, 0.0), 3)}
                              for k in crit), key=lambda o: -o["p"])
            block = {"q": key, "title": title(key), "kind": "choice", "options": options[:6], "picked": a.choice,
                     "confidence": round(a.confidence, 3)}
            if key in used and used[key] != a.choice:
                block["used"] = used[key]
                if all(o["id"] != used[key] for o in block["options"]):
                    block["options"].append({"id": used[key], "label": label(used[key], crit.get(used[key], used[key])),
                                             "p": round(a.probabilities.get(used[key], 0.0), 3)})
            out.append(block)
        elif kind == "noul" and key in answers.nouls:
            p, cut = answers.nouls[key], cuts.get(key, 0.5)
            out.append({"q": key, "title": title(key), "kind": "yesno", "p": round(p, 3), "cut": round(cut, 3),
                        "verdict": "yes" if p >= cut else "no"})
        elif kind == "score" and key in answers.scores:
            out.append({"q": key, "title": title(key), "kind": "score", "p": round(answers.scores[key], 3)})
    return out
