"""Plans as state machines: the planner designs one, Jev runs it (cuo-6om).

The fixed policy has one shape for every fight: six intents, the same questions every tick and
code's rules on top. A machine gives the shape to the situation instead. The planner (or the
player, or a file) writes named states, each with what the character does there in words, which
intents it may choose and a few settings (who to target, whether to loot, whether to step back,
which spell), and transitions between them. A transition is a yes/no question with the
planner's own criteria; Jev answers the current state's transitions in the same request as the
fight questions, so a machine costs a few Nouls a decision and no extra round trip. Code can
put conditions on a transition (`requires`: "3+ close", "health below 40", ...) that are checked
before Jev is asked, and a state can have a time limit.

What no machine can remove, because the policy applies it whatever the state allows: the
client's healing reflexes, never attacking players, leaving when a red or criminal player comes
close, the emergency flee near death, and leaving when a pack outweighs the character.

    {"name": "pull one at a time", "why": "...", "start": "pull",
     "states": {"pull": {"says": "...", "allow": ["fight", "rest"], "target": "closest_first",
                         "transitions": [{"to": "regroup", "when": "...", "requires": ["health below 50"]}]},
                ...}}
"""

import json
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .policy import PolicyConfig
from .spells import AREA_SPELLS, ATTACK_SPELLS
from .state import PACK_FAR, Situation

INTENTS = ("fight", "flee", "leave", "loot", "seek", "rest")
TARGETS = ("current_first", "weakest_first", "closest_first", "strongest_first")
LOOTING = ("everything", "valuables", "nothing")
MACHINES_DIR = Path(__file__).resolve().parents[2] / "machines"
MAX_STATES = 8
MAX_TRANSITIONS = 4  # per state: every one is a Noul in each decision while there

# What code checks before a transition is asked. Numbers are whole; health and mana in percent.
CONDITIONS: dict[str, str] = {
    r"(\d+)\+ in sight": "at least N hostile creatures in sight",
    r"(\d+)\+ close": "at least N hostile creatures within 3 tiles",
    r"(\d+)\+ adjacent": "at least N hostile creatures next to the character",
    r"(\d+)\+ coming": "at least N creatures coming at the character (fighting, within 12 tiles)",
    r"none in sight": "no hostile creature in sight",
    r"none close": "no hostile creature within 3 tiles",
    r"pack": "the creatures coming at the character together outweigh it (too many to fight at once)",
    r"health below (\d+)": "health below N percent",
    r"health above (\d+)": "health above N percent",
    r"mana below (\d+)": "mana below N percent (casters)",
    r"mana above (\d+)": "mana above N percent (casters)",
    r"supplies low": "bandages, potions or arrows running low or nearly gone",
    r"corpse near": "an unlooted corpse within 10 tiles",
    r"area spell ready": "an area spell can be cast at three or more creatures close together (mages)",
}


class MachineError(ValueError):
    pass


@dataclass
class Transition:
    to: str
    when: str                      # Jev's criteria for yes, in the planner's words
    unless: str = ""               # ...and for no (default: keep to the current state)
    requires: list[str] = field(default_factory=list)  # code-checked first (CONDITIONS)
    at: float = 0.6                # Jev's yes at or above this takes it
    min_s: float = 0.0             # seconds in the state before it may be taken


@dataclass
class State:
    name: str
    says: str                      # what the character does here, in words: Jev reads it with every question
    allow: tuple[str, ...] = INTENTS
    target: str | None = None      # target priority while here (TARGETS)
    loot: str | None = None        # LOOTING
    kite: bool | None = None       # casters and archers step back from melee
    spell: str | None = None       # casters: the spell to keep casting
    transitions: list[Transition] = field(default_factory=list)
    max_s: float | None = None     # a time limit, then `then`
    then: str | None = None


@dataclass
class Machine:
    name: str
    why: str
    start: str
    states: dict[str, State]

    def to_json(self) -> dict[str, Any]:
        def state(s: State) -> dict[str, Any]:
            out: dict[str, Any] = {"says": s.says, "allow": list(s.allow)}
            for k in ("target", "loot", "kite", "spell", "max_s", "then"):
                if getattr(s, k) is not None:
                    out[k] = getattr(s, k)
            out["transitions"] = [{k: v for k, v in t.__dict__.items() if v not in ("", [], 0.0) or k in ("to", "when")}
                                  | ({"at": t.at} if t.at != 0.6 else {}) for t in s.transitions]
            return out
        return {"name": self.name, "why": self.why, "start": self.start,
                "states": {n: state(s) for n, s in self.states.items()}}

    def describe(self) -> str:
        """One line per state, for logs, the planner and `uo-brain machine show`."""
        lines = [f"{self.name}: {self.why}"]
        for n, s in self.states.items():
            moves = ", ".join(f"{t.to}{' if ' + ' and '.join(t.requires) if t.requires else ''}" for t in s.transitions)
            lines.append(f"  {'*' if n == self.start else ' '} {n} [{', '.join(s.allow)}]: {s.says}"
                         + (f" -> {moves}" if moves else "")
                         + (f"; after {s.max_s:.0f} s -> {s.then}" if s.max_s else ""))
        return "\n".join(lines)


def parse(data: dict[str, Any] | str) -> Machine:
    """A machine from JSON, checked: every state reachable by name, allowed intents and settings
    from the fixed lists, conditions code knows how to check, at most MAX_STATES states and
    MAX_TRANSITIONS transitions each. Raises MachineError saying what is wrong."""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError as e:
            raise MachineError(f"not JSON: {e}") from None
    if not isinstance(data, dict):
        raise MachineError("a machine is a JSON object")
    raw_states = data.get("states")
    if not isinstance(raw_states, dict) or not raw_states:
        raise MachineError("'states' must be an object of named states")
    if len(raw_states) > MAX_STATES:
        raise MachineError(f"at most {MAX_STATES} states")
    errors: list[str] = []
    states: dict[str, State] = {}
    for name, s in raw_states.items():
        if not isinstance(s, dict):
            errors.append(f"state {name!r} must be an object")
            continue
        allow = tuple(s.get("allow") or INTENTS)
        if bad := [a for a in allow if a not in INTENTS]:
            errors.append(f"{name}: unknown intents {bad} (allowed: {', '.join(INTENTS)})")
        if s.get("target") not in (None, *TARGETS):
            errors.append(f"{name}: target must be one of {', '.join(TARGETS)}")
        if s.get("loot") not in (None, *LOOTING):
            errors.append(f"{name}: loot must be one of {', '.join(LOOTING)}")
        if s.get("spell") not in (None, *ATTACK_SPELLS, *AREA_SPELLS):
            errors.append(f"{name}: unknown spell {s.get('spell')!r}")
        if not str(s.get("says") or "").strip():
            errors.append(f"{name}: 'says' (what the character does here) is missing")
        trans = []
        for i, t in enumerate(s.get("transitions") or []):
            if not isinstance(t, dict) or not t.get("to") or not str(t.get("when") or "").strip():
                errors.append(f"{name}: transition {i + 1} needs 'to' and 'when'")
                continue
            reqs = [str(r).strip().lower() for r in t.get("requires") or []]
            for r in reqs:
                if not any(re.fullmatch(p, r) for p in CONDITIONS):
                    errors.append(f"{name}: unknown condition {r!r}")
            trans.append(Transition(str(t["to"]), str(t["when"]).strip(), str(t.get("unless") or "").strip(), reqs,
                                    float(t.get("at", 0.6)), float(t.get("min_s", 0))))
        if len(trans) > MAX_TRANSITIONS:
            errors.append(f"{name}: at most {MAX_TRANSITIONS} transitions")
        states[name] = State(name, str(s.get("says") or "").strip(), allow, s.get("target"), s.get("loot"),
                             s.get("kite"), s.get("spell"), trans,
                             float(s["max_s"]) if s.get("max_s") else None, s.get("then"))
    start = data.get("start") or next(iter(raw_states))
    if start not in states:
        errors.append(f"start {start!r} is not a state")
    for s in states.values():
        for t in s.transitions:
            if t.to not in states:
                errors.append(f"{s.name}: transition to unknown state {t.to!r}")
            if not 0.3 <= t.at <= 0.95:
                errors.append(f"{s.name} -> {t.to}: 'at' must be between 0.3 and 0.95")
        if s.max_s and s.then not in states:
            errors.append(f"{s.name}: max_s needs 'then', a state to go to")
    if errors:
        raise MachineError("; ".join(errors))
    return Machine(str(data.get("name") or "plan"), str(data.get("why") or ""), start, states)


def load(name_or_path: str) -> Machine:
    """A machine from a file, or by name from brain/machines/."""
    p = Path(name_or_path)
    if not p.exists():
        p = MACHINES_DIR / f"{name_or_path.removesuffix('.json')}.json"
    if not p.exists():
        raise MachineError(f"no machine {name_or_path!r} (files in {MACHINES_DIR}: "
                           f"{', '.join(sorted(f.stem for f in MACHINES_DIR.glob('*.json')))})")
    return parse(p.read_text())


def holds(cond: str, sit: Situation) -> bool:
    """Whether a code-checked condition holds now."""
    close = [h for h in sit.hostiles if h.distance <= 3]
    if m := re.fullmatch(r"(\d+)\+ in sight", cond):
        return len(sit.hostiles) >= int(m[1])
    if m := re.fullmatch(r"(\d+)\+ close", cond):
        return len(close) >= int(m[1])
    if m := re.fullmatch(r"(\d+)\+ adjacent", cond):
        return sum(1 for h in sit.hostiles if h.distance <= 1) >= int(m[1])
    if m := re.fullmatch(r"(\d+)\+ coming", cond):
        return len(sit.pack) >= int(m[1])
    if cond == "none in sight":
        return not sit.hostiles
    if cond == "none close":
        return not close
    if cond == "pack":
        return len(sit.pack) >= 2 and sit.pack_weight >= PACK_FAR
    if m := re.fullmatch(r"health (below|above) (\d+)", cond):
        return sit.hp_pct < int(m[2]) if m[1] == "below" else sit.hp_pct > int(m[2])
    if m := re.fullmatch(r"mana (below|above) (\d+)", cond):
        return sit.mana_pct < int(m[2]) if m[1] == "below" else sit.mana_pct > int(m[2])
    if cond == "supplies low":
        return str(sit.state["you"].get("supplies", "")).startswith(("running low", "nearly gone"))
    if cond == "corpse near":
        return bool(sit.corpses)
    if cond == "area spell ready":
        return bool(sit.area_center and sit.area_spells)
    return False


@dataclass
class Runner:
    """Runs a machine across decisions: which state it is in, what to ask, what the state allows."""
    machine: Machine
    clock: Any = time.monotonic
    state: str = ""
    entered: float = 0.0
    last: str = ""                 # the last transition, in words
    seconds: dict[str, float] = field(default_factory=dict)  # time spent in each state
    moves: int = 0

    def __post_init__(self) -> None:
        self.state = self.state or self.machine.start
        self.entered = self.clock()

    def reset(self) -> None:
        """Back to the start, keeping the time counts: each hunt starts the plan afresh."""
        if self.state != self.machine.start:
            self.go(self.machine.start, "a new hunt")
            self.moves -= 1

    @property
    def current(self) -> State:
        return self.machine.states[self.state]

    def plan_words(self) -> dict[str, str]:
        """For every question's instructions: the plan and what the current step means."""
        return {"plan": f"{self.machine.name}: {self.machine.why}".strip(": "),
                "plan_step": f"{self.state}: {self.current.says}"}

    def questions(self, sit: Situation, who: str, role: str) -> dict[str, dict[str, Any]]:
        """The current state's transitions whose conditions hold, as Nouls keyed `go_<state>`."""
        here = self.clock() - self.entered
        out: dict[str, dict[str, Any]] = {}
        for t in self.current.transitions:
            if here < t.min_s or not all(holds(c, sit) for c in t.requires):
                continue
            nxt = self.machine.states[t.to]
            instr: dict[str, Any] = {"role": role, **self.plan_words()}
            if sit.strategy:
                instr["player_strategy"] = sit.strategy
            instr["question"] = (f"The {who} is following the plan above and is at the step '{self.state}'. Should it "
                                 f"move on to '{t.to}' ({nxt.says}) now?")
            out[f"go_{t.to}"] = {"type": "noul", "instructions": instr, "criteria": {
                "true": t.when,
                "false": t.unless or f"Otherwise: keep to '{self.state}' ({self.current.says})"}}
        return out

    def advance(self, nouls: dict[str, float]) -> str | None:
        """Take the transition Jev is surest of, at or above its own cut, or the state's time limit.
        Returns what happened in words, or None."""
        now = self.clock()
        best = None
        for t in self.current.transitions:
            p = nouls.get(f"go_{t.to}")
            if p is not None and p >= t.at and (best is None or p > best[1]):
                best = (t, p)
        if best:
            return self.go(best[0].to, f"{self.state} -> {best[0].to} ({best[1]:.2f})")
        s = self.current
        if s.max_s and now - self.entered >= s.max_s and s.then:
            return self.go(s.then, f"{self.state} -> {s.then} (after {s.max_s:.0f} s)")
        return None

    def go(self, to: str, why: str) -> str:
        now = self.clock()
        self.seconds[self.state] = self.seconds.get(self.state, 0.0) + now - self.entered
        self.state, self.entered, self.last = to, now, why
        self.moves += 1
        return why

    def config(self, cfg: PolicyConfig) -> PolicyConfig:
        """The policy settings in the current state: its allowed intents and its settings."""
        s = self.current
        out = replace(cfg, allowed_intents=s.allow)
        if s.target:
            out = replace(out, target_priority=s.target)
        if s.loot:
            out = replace(out, looting=s.loot, take_item={"everything": 0.0, "valuables": cfg.take_item,
                                                          "nothing": 2.0}[s.loot])
        if s.kite is not None:
            out = replace(out, kite=s.kite)
        if s.spell:
            out = replace(out, main_spell=s.spell, opening_spell="")
        return out

    def summary(self) -> dict[str, Any]:
        seconds = dict(self.seconds)
        seconds[self.state] = seconds.get(self.state, 0.0) + self.clock() - self.entered
        return {"machine": self.machine.name, "state": self.state, "moves": self.moves,
                "seconds_in": {k: round(v, 1) for k, v in seconds.items()}}

    def panel(self) -> dict[str, Any]:
        """For the client's panel: the plan, where it is, and the last move."""
        return {"name": self.machine.name, "state": self.state, "says": self.current.says,
                "states": list(self.machine.states), "last": self.last,
                "since_s": round(self.clock() - self.entered, 1)}


def schema() -> dict[str, Any]:
    """The JSON schema of a machine, for the planner's set_machine tool."""
    s = {"type": "string"}
    return {
        "type": "object",
        "properties": {
            "name": {**s, "description": "A short name for the plan."},
            "why": {**s, "description": "One sentence: what the plan is for."},
            "start": {**s, "description": "The state it starts in."},
            "states": {
                "type": "object",
                "description": f"Named states, at most {MAX_STATES}.",
                "additionalProperties": {
                    "type": "object",
                    "properties": {
                        "says": {**s, "description": "What the character does in this state, in plain words. Jev "
                                                     "reads it with every fight question."},
                        "allow": {"type": "array", "items": {"type": "string", "enum": list(INTENTS)},
                                  "description": "Intents allowed here (default all). 'rest' is always allowed."},
                        "target": {"type": "string", "enum": list(TARGETS)},
                        "loot": {"type": "string", "enum": list(LOOTING)},
                        "kite": {"type": "boolean", "description": "Casters and archers step back from melee."},
                        "spell": {"type": "string", "enum": [*ATTACK_SPELLS, *AREA_SPELLS],
                                  "description": "Casters: the spell to keep casting here."},
                        "max_s": {"type": "number", "description": "Time limit in seconds, then go to 'then'."},
                        "then": s,
                        "transitions": {
                            "type": "array", "description": f"At most {MAX_TRANSITIONS}; each is a yes/no question "
                                                             "Jev answers every decision while in this state.",
                            "items": {"type": "object", "properties": {
                                "to": s,
                                "when": {**s, "description": "When to move on, in words: Jev's criteria for yes."},
                                "unless": {**s, "description": "Optional: Jev's criteria for no."},
                                "requires": {"type": "array", "items": s, "description":
                                             "Optional conditions code checks before asking: "
                                             + "; ".join(f"'{p.replace(chr(92) + 'd+', 'N').replace('(', '').replace(')', '')}'"
                                                         f" ({d})" for p, d in CONDITIONS.items())},
                                "at": {"type": "number", "description": "Jev's yes at or above this moves (default 0.6)."},
                                "min_s": {"type": "number", "description": "Seconds in the state before it may move."},
                            }, "required": ["to", "when"]}},
                    },
                    "required": ["says"],
                },
            },
        },
        "required": ["name", "states"],
    }


DESIGN_SYSTEM = """You design how a character in the game Ultima Online plays a kind of fight, as a small state
machine. Code carries out each state: it fights, heals and loots on its own, and Jev, a fast
judgment model, chooses the move within what the state allows and answers your transition
questions every second or so. You decide the shape: which steps there are, what each step
means in plain words, which of the six intents it allows (fight, flee: run a short way, leave:
run until nothing is in sight, loot, seek: walk to a creature further off, rest), a few settings,
and when to move from one step to another.

Write transitions as clear yes/no criteria a model can judge from what the character sees:
health in words, creatures by kind with how strong each is against the character, how far away
they are, how many are coming at it at once, mana, supplies. Use `requires` for anything code can
check exactly (counts, health and mana percentages), so the question is only asked when it can
matter. Keep it small: 2 to 5 states, 1 to 3 transitions each. Give a state that should not last
long a time limit (max_s, then). Code keeps the floors whatever you write: healing, never
attacking players, leaving when a red player comes, the emergency flee near death, and leaving
when a pack far stronger than the character comes at it.

Answer by calling the machine tool once."""


def design_tool() -> dict[str, Any]:
    return {"type": "function", "function": {"name": "machine", "description": "The state machine.",
                                             "parameters": schema()}}


async def design(situation: str, archetype: str = "warrior", chat_fn: Any = None, model: str | None = None,
                 examples: bool = True, log: Any = None) -> tuple[Machine, Any]:
    """Have the planner model write a machine for a situation, in words. Validation errors go back
    to it twice before giving up (MachineError). Returns the machine and the usage."""
    from . import llm
    chat_fn = chat_fn or llm.chat
    shots = ""
    if examples:
        shots = "\n\nTwo examples:\n" + "\n".join(json.dumps(load(n).to_json()) for n in ("pull-one", "mage-swarm"))
    msgs: list[dict[str, Any]] = [
        {"role": "system", "content": DESIGN_SYSTEM + shots},
        {"role": "user", "content": f"The character plays as: {archetype}.\nThe situation: {situation}\n\n"
                                    "Design the machine by calling the machine tool."}]
    usage = llm.LlmUsage(calls=0)
    for _ in range(3):
        res = await chat_fn(msgs, tools=[design_tool()], tool_choice="auto", model=llm.planner_model(model),
                            max_tokens=3000)
        usage = usage + res.usage
        if log:
            log({"type": "machine_design", "t": time.time(), "usage": res.usage.to_log(),
                 "calls": [c.arguments for c in res.tool_calls], "content": (res.content or "")[:500]})
        if not res.tool_calls:
            msgs += [res.message, {"role": "user", "content": "Answer by calling the machine tool."}]
            continue
        call = res.tool_calls[0]
        try:
            return parse(call.arguments), usage
        except MachineError as e:
            msgs += [{**res.message, "tool_calls": res.message.get("tool_calls", [])[:1]},
                     llm.tool_result(call, f"Not valid: {e}. Call the machine tool again with it fixed.")]
    raise MachineError("the model gave no valid machine in three tries")
