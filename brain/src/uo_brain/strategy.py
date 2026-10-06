"""The player's strategy, in their own words, and what it means for the policy.

A strategy is free text kept per character in the client ("-agent strategy add
never flee, fight to the death"). It is used two ways:

1. As context. The text goes into every decision question, so Jev weighs it when
   choosing intent, target and loot.
2. As settings. When the text changes, Jev answers a few typed questions about the
   strategy itself, and code turns the answers into policy settings. That way
   "never flee" also switches off the code's own emergency flee, and "don't loot"
   stops item pickups, instead of only nudging the model.
"""

from dataclasses import dataclass, replace
from typing import Any

from .judge import Answers, Judge
from .policy import PolicyConfig

# Questions about the text, not the game: state is {"strategy": text}.
QUESTIONS: dict[str, dict[str, Any]] = {
    "allows_flee": {
        "type": "noul",
        "instructions": "Does this player's strategy for their Ultima Online warrior allow running away from a fight "
                        "that is going badly?",
        "criteria": {
            "true": "The strategy allows retreating, says nothing about it, or only prefers fighting on.",
            "false": "The strategy forbids fleeing: never flee, never retreat, fight to the death, always stand ground.",
        },
    },
    "aggression": {
        "type": "score",
        "instructions": "How aggressive does this strategy want the warrior to be?",
        "criteria": [
            "Very cautious: avoid fights where possible and retreat early.",
            "Cautious: fight when it is safe, pull back when hurt.",
            "Balanced, or the strategy does not say.",
            "Aggressive: seek out fights and keep pressing.",
            "Relentless: always attack, never back down.",
        ],
    },
    "target_priority": {
        "type": "choice",
        "instructions": "Which creature does this strategy want the warrior to attack first?",
        "criteria": {
            "current_first": "Finish the creature already being fought before switching.",
            "weakest_first": "Go for the most wounded or weakest creature.",
            "closest_first": "Go for whatever is closest.",
            "strongest_first": "Take on the most dangerous creature first.",
            "no_preference": "The strategy does not say anything about which target to pick.",
        },
    },
    "looting": {
        "type": "choice",
        "instructions": "What does this strategy want the warrior to pick up from corpses?",
        "criteria": {
            "everything": "Take everything that can be carried.",
            "valuables": "Take only useful or valuable items.",
            "nothing": "Do not loot corpses at all.",
            "no_preference": "The strategy does not say anything about looting.",
        },
    },
}


@dataclass(frozen=True)
class Knobs:
    allow_flee: bool = True
    aggression: float = 0.5        # 0 = very cautious, 1 = relentless
    target_priority: str = "current_first"
    looting: str = "valuables"

    def describe(self) -> str:
        parts = ["flees when losing" if self.allow_flee else "never flees",
                 f"aggression {self.aggression:.2f}",
                 self.target_priority.replace("_", " "),
                 f"loots {self.looting}"]
        return ", ".join(parts)


DEFAULT = Knobs()


async def compile_strategy(judge: Judge, text: str) -> tuple[Knobs, Answers | None]:
    if not text.strip():
        return DEFAULT, None
    ans = await judge.ask({"strategy": text}, QUESTIONS)
    target = ans.choices["target_priority"].choice
    looting = ans.choices["looting"].choice
    knobs = Knobs(
        allow_flee=ans.nouls["allows_flee"] >= 0.5,
        aggression=ans.scores.get("aggression", 0.5),
        target_priority=DEFAULT.target_priority if target == "no_preference" else target,
        looting=DEFAULT.looting if looting == "no_preference" else looting,
    )
    return knobs, ans


def apply(base: PolicyConfig, k: Knobs) -> PolicyConfig:
    """Strategy settings layered over the command-line policy config."""
    # Cautious players flee on weaker danger signals; relentless ones only when nearly certain.
    flee_danger = 0.3 + 0.65 * k.aggression
    return replace(
        base,
        allow_flee=k.allow_flee,
        flee_danger=flee_danger,
        panic_danger=max(base.panic_danger, flee_danger),
        target_priority=k.target_priority,
        looting=k.looting,
        take_item={"everything": 0.0, "valuables": base.take_item, "nothing": 2.0}[k.looting],
    )
