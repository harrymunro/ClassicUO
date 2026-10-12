"""Rules of play by name (cuo-der): how a character travels, talks and behaves, a set at a time.

A set is a TOML file in brain/rules/ (`realistic`, `run-always`) whose keys are the fleet file's own
settings (README, "Rules of play"). `uo-brain session --rules NAME` and `rules = "NAME"` in a fleet
file take one; a setting given on its own (a flag, the fleet file's key, `--set`) wins over the set.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

RULES_DIR = Path(__file__).resolve().parents[2] / "rules"

# Each rule: its kind and what it does. The fleet file and a rules file use these names.
RULES: dict[str, tuple[type, str]] = {
    "travel": (str, "walk or run: the pace of every trip the brain asks for (a walk runs with danger in view, to "
                    "catch up, as a ghost or for a corpse); unset, the client's own agent_travel_pace"),
    "walk": (bool, "a party walks its trips together, the others beside the leader (cuo-cod.7)"),
    "pace": (bool, "lines said at a human pace, one speaker at a time (cuo-cod.9)"),
    "small_talk": (bool, "characters who talk chat when they have time (cuo-cod.6)"),
    "roleplay": (bool, "emotes and gestures in character (cuo-cod.8)"),
    "callouts": (bool, "a party's members call out in fights (cuo-cod.2)"),
    "help": (bool, "a party's members heal, bandage and take creatures off each other (cuo-4ti)"),
}


def names(root: Path = RULES_DIR) -> list[str]:
    return sorted(p.stem for p in root.glob("*.toml"))


def load(name: str, root: Path = RULES_DIR) -> dict[str, Any]:
    """The rules a set names, checked: {rule: value}, without its description."""
    path = root / f"{name}.toml"
    if not path.exists():
        raise ValueError(f"no rules called {name!r}; there are {', '.join(names(root)) or 'none'} (in {root})")
    data = tomllib.loads(path.read_text())
    data.pop("description", None)
    for key, value in data.items():
        if key not in RULES:
            raise ValueError(f"{path}: {key!r} isn't a rule ({', '.join(RULES)})")
        kind = RULES[key][0]
        if not isinstance(value, kind):
            raise ValueError(f"{path}: {key} should be {kind.__name__}, not {value!r}")
    if data.get("travel") not in (None, "walk", "run"):
        raise ValueError(f"{path}: travel should be walk or run, not {data['travel']!r}")
    return data


def describe(name: str, root: Path = RULES_DIR) -> str:
    return str(tomllib.loads((root / f"{name}.toml").read_text()).get("description", ""))


# A session's flags for each rule: the flag's attribute, and what it is set to for the rule's value.
FLAGS = {
    "travel": ("travel", lambda v: v),
    "walk": ("party_walk", bool),
    "pace": ("no_pace", lambda v: not v),
    "small_talk": ("no_small_talk", lambda v: not v),
    "roleplay": ("roleplay", bool),
    "callouts": ("no_callouts", lambda v: not v),
    "help": ("no_party_help", lambda v: not v),
}


def apply(args: Any, name: str, root: Path = RULES_DIR) -> dict[str, Any]:
    """A session's arguments with a set of rules under them: each flag left at its default takes the
    set's value, and one given on the command line stays. The rules applied, as {rule: value}."""
    applied = {}
    for key, value in load(name, root).items():
        attr, to = FLAGS[key]
        if getattr(args, attr, None) in (None, False):
            setattr(args, attr, to(value))
            applied[key] = value
    return applied
