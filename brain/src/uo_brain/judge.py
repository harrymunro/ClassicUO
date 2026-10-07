"""Who answers the questions: Jev (via OpenRouter or TypeSafe directly), or a
rule-based stand-in for running the loop without a model or credits."""

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from . import spells

OPENROUTER_BASE_URL = "https://openrouter.ai/api"
OPENROUTER_MODEL = "~typesafe/jev-latest"


@dataclass
class ChoiceResult:
    choice: str
    probabilities: dict[str, float]
    confidence: float


@dataclass
class Answers:
    choices: dict[str, ChoiceResult] = field(default_factory=dict)
    nouls: dict[str, float] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)  # 0 = first level, 1 = last
    model: str = ""
    input_tokens: int = 0
    latency_ms: float = 0.0

    def to_log(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "latency_ms": round(self.latency_ms, 1),
            "input_tokens": self.input_tokens,
            "choices": {k: {"choice": v.choice, "confidence": round(v.confidence, 3),
                            "probabilities": {o: round(p, 3) for o, p in v.probabilities.items()}}
                        for k, v in self.choices.items()},
            "nouls": {k: round(v, 3) for k, v in self.nouls.items()},
            "scores": {k: round(v, 3) for k, v in self.scores.items()},
        }


class Judge(Protocol):
    name: str

    async def ask(self, state: dict[str, Any], questions: dict[str, dict[str, Any]]) -> Answers: ...

    async def close(self) -> None: ...


def load_dotenv(path: Path) -> None:
    """Minimal .env reader so keys can live in brain/.env (gitignored)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class JevJudge:
    def __init__(self, provider: str = "auto", model: str | None = None, timeout: float = 5.0):
        from typesafe_sdk import AsyncTypeSafeClient

        or_key = os.environ.get("OPENROUTER_API_KEY")
        ts_key = os.environ.get("TYPESAFE_API_KEY")
        if provider == "auto":
            provider = "openrouter" if or_key else "typesafe"

        if provider == "openrouter":
            if not or_key:
                raise SystemExit("OPENROUTER_API_KEY is not set (export it or put it in brain/.env)")
            self.client = AsyncTypeSafeClient(api_key=or_key, base_url=OPENROUTER_BASE_URL,
                                              model=model or os.environ.get("JEV_MODEL", OPENROUTER_MODEL),
                                              timeout=timeout)
        else:
            if not ts_key:
                raise SystemExit("TYPESAFE_API_KEY is not set")
            self.client = AsyncTypeSafeClient(api_key=ts_key, model=model or os.environ.get("JEV_MODEL"),
                                              timeout=timeout)
        self.name = f"jev/{provider}"

    async def ask(self, state, questions) -> Answers:
        t = time.perf_counter()
        r = await self.client.system_one(state=state, questions=questions)
        out = Answers(model=r.model, input_tokens=r.usage.input_tokens or 0,
                      latency_ms=(time.perf_counter() - t) * 1000)
        for k, a in r.choices.items():
            out.choices[k] = ChoiceResult(a.choice, dict(a.probabilities), a.confidence)
        for k, a in r.nouls.items():
            out.nouls[k] = a.noul
        for k, a in r.scores.items():
            levels = sorted(a.legend)
            span = levels[-1] - levels[0]
            out.scores[k] = (a.score - levels[0]) / span if span else 0.5
        return out

    async def close(self) -> None:
        await self.client.aclose()


class HeuristicJudge:
    """Answers the same questions with fixed rules over the state. Used for
    plumbing tests and as a baseline to compare Jev against."""

    name = "heuristic"

    async def ask(self, state, questions) -> Answers:
        if "allows_flee" in questions:
            return self._strategy(state["strategy"].lower(), questions)
        you = state["you"]
        hostiles = state["hostile_creatures"]
        hp = int(you["health"].split("(")[-1].rstrip("%)"))
        close = [h for h in hostiles if h["distance"].startswith(("adjacent", "close"))]
        mage = "mana" in you
        supplies = you.get("bandages_left", 0) + you["heal_potions_left"]
        danger = hp < 30 and (len(close) >= 2 or supplies == 0)
        in_range = [h for h in hostiles if h.get("in_spell_range")]
        mana = int(you["mana"].split("(")[-1].rstrip("%)")) if mage else 100

        if danger and close:
            intent = "flee"  # the rule baseline never leaves an area: it has no idea of a creature's strength
        elif close or (mage and in_range and mana >= 15):
            intent = "fight"
        elif state["corpses_not_yet_looted"]:
            intent = "loot"
        elif hostiles and hp >= 70:
            intent = "seek"
        else:
            intent = "rest"

        out = Answers(model="heuristic")
        out.choices["intent"] = one_hot(intent, questions["intent"]["criteria"])
        out.nouls["in_danger"] = 0.9 if danger else 0.1

        if "target" in questions:
            current = next((h for h in hostiles if h["your_current_target"]), None)
            pick = current or (close[0] if close else hostiles[0])
            out.choices["target"] = one_hot(pick["id"], questions["target"]["criteria"])
        if "corpse" in questions:
            out.choices["corpse"] = one_hot(state["corpses_not_yet_looted"][0]["id"], questions["corpse"]["criteria"])
        if "spell" in questions:
            # Strongest damage spell on offer (the options come strongest first); cheap ones when mana is low.
            options = [k for k, v in questions["spell"]["criteria"].items()
                       if k != "none" and v.split(":", 1)[0] not in ("Poison", "Paralyze")]
            pick = (options[-1] if mana < 35 else options[0]) if options else "none"
            out.choices["spell"] = one_hot(pick, questions["spell"]["criteria"])
        for name in questions:
            if name.startswith("take_"):
                out.nouls[name] = 0.8
        return out

    def _strategy(self, text: str, questions) -> Answers:
        """Keyword reading of a strategy; Jev does this properly."""
        def has(*words):
            return any(w in text for w in words)

        out = Answers(model="heuristic")
        out.nouls["allows_flee"] = 0.05 if has("never flee", "never run", "never retreat", "to the death",
                                              "stand my ground", "stand ground") else 0.9
        out.scores["aggression"] = 1.0 if has("relentless", "never flee", "to the death") else \
            0.75 if has("aggressive") else 0.0 if has("cautious", "careful", "safe") else 0.5
        target = "weakest_first" if has("weakest", "wounded first") else "closest_first" if has("closest", "nearest") \
            else "strongest_first" if has("strongest", "biggest", "most dangerous") else "no_preference"
        out.choices["target_priority"] = one_hot(target, questions["target_priority"]["criteria"])
        loot = "nothing" if has("don't loot", "do not loot", "never loot", "no loot") else \
            "everything" if has("loot everything", "take everything") else "valuables" if has("valuable") else "no_preference"
        out.choices["looting"] = one_hot(loot, questions["looting"]["criteria"])
        # Spells: the clause that says "open" names the opener; the next clause naming a spell, the main one.
        opener = main = "no_preference"
        for clause in re.split(r"[.;,\n]| then ", text):
            if (spell := spells.find(clause)) is None:
                continue
            if "open" in clause and opener == "no_preference":
                opener = spell
            elif main == "no_preference":
                main = spell
        if "opening_spell" in questions:
            out.choices["opening_spell"] = one_hot(opener, questions["opening_spell"]["criteria"])
            out.choices["main_spell"] = one_hot(main, questions["main_spell"]["criteria"])
        return out

    async def close(self) -> None:
        pass


def one_hot(choice: str, options: dict[str, Any]) -> ChoiceResult:
    return ChoiceResult(choice, {o: 1.0 if o == choice else 0.0 for o in options}, 1.0)


def make(kind: str, provider: str = "auto", model: str | None = None) -> Judge:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    if kind == "heuristic":
        return HeuristicJudge()
    return JevJudge(provider, model)
