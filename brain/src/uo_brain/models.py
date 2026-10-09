"""Which model answers each AI role (cuo-m70.2).

Every call has a kind (costs.py): fight, routine, facts, rerank, strategy and recorder are
system one, Jev's typed questions; planner, review, guide and design are system two, the
planner model's chat. A profile names the model for each, in one place:

    [models]
    system1 = "jev"                            # every system-one kind...
    routine = "anthropic/claude-haiku-5.5"     # ...but this one
    system2 = "anthropic/claude-sonnet-5.5"

    [cheaper]                                  # used while over a budget whose action is "cheaper"
    system2 = "anthropic/claude-haiku-5.5"

    [budget]
    per_hour = 0.60
    over = "cheaper"                           # slow, cheaper or stop

    [aliases]                                  # short names for the command line and bench labels
    haiku = "anthropic/claude-haiku-5.5"

Profiles live in brain/profiles/<name>.toml (or any file). The model for a kind is, from
weakest to strongest: the built-in default, JEV_MODEL / PLANNER_MODEL in the environment, the
profile, then the command line (`--use KIND=MODEL`, `--model` for system one, `--planner-model`
for system two). One profile is active in a process (`active`), as one ledger is.

"jev" (or any typesafe/jev id) is Jev through TypeSafe's system-one API. Any other model in a
system-one role is asked the same questions through `ChatJudge`, which has the chat model state
a probability for every option and reads its answer into the same Answers, so policy, the bench
and the logs don't change. System two already speaks chat: its model is a per-kind setting.
"""

import json
import os
import re
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import costs, llm
from . import judge as judges
from .judge import Answers, ChoiceResult

PROFILES_DIR = Path(__file__).resolve().parents[2] / "profiles"
JEV = "jev"
DEFAULTS = {"system1": JEV, "system2": llm.PLANNER_MODEL}
ALIASES = {
    "jev": JEV,
    "sonnet": "anthropic/claude-sonnet-5.5",
    "haiku": "anthropic/claude-haiku-5.5",
    "opus": "anthropic/claude-opus-5.5",
}


def is_jev(model: str) -> bool:
    m = model.removeprefix("~").lower()
    return m == JEV or m.startswith(("typesafe/jev", "jev-"))


@dataclass
class Profile:
    name: str = "default"
    models: dict[str, str] = field(default_factory=dict)   # kind, "system1" or "system2" -> model
    cheaper: dict[str, str] = field(default_factory=dict)  # the same, while over a "cheaper" budget
    aliases: dict[str, str] = field(default_factory=dict)
    budget: costs.Budget | None = None

    def resolve(self, model: str) -> str:
        """An alias to its model id ("haiku" -> "anthropic/claude-haiku-5.5"); ids pass through."""
        return self.aliases.get(model) or ALIASES.get(model) or model

    def model_for(self, kind: str, cheap: bool | None = None) -> str:
        """The model for a kind of call. Over a budget whose action is "cheaper" (or with cheap=True),
        the profile's cheaper model for it, where it names one."""
        role = costs.role_of(kind)
        if cheap is None:
            b = costs.ledger.budget
            cheap = bool(b and b.action == "cheaper" and costs.ledger.over_budget())
        if cheap and (m := self.cheaper.get(kind) or self.cheaper.get(role)):
            return self.resolve(m)
        env = os.environ.get("JEV_MODEL" if role == "system1" else "PLANNER_MODEL")
        return self.resolve(self.models.get(kind) or self.models.get(role) or env or DEFAULTS[role])

    def use(self, spec: str) -> None:
        """Apply one command-line setting, KIND=MODEL (a kind, system1 or system2)."""
        kind, _, model = spec.partition("=")
        kind, model = kind.strip(), model.strip()
        if not model or kind not in (*costs.SYSTEM_ONE, *costs.SYSTEM_TWO, "system1", "system2", "cheaper1",
                                     "cheaper2"):
            raise ValueError(f"--use wants KIND=MODEL with KIND one of system1, system2, "
                             f"{', '.join(costs.SYSTEM_ONE + costs.SYSTEM_TWO)}; not {spec!r}")
        if kind in ("cheaper1", "cheaper2"):
            self.cheaper["system" + kind[-1]] = model
        else:
            self.models[kind] = model

    def system_one(self) -> dict[str, str]:
        return {k: self.model_for(k, cheap=False) for k in costs.SYSTEM_ONE}

    def to_log(self) -> dict[str, Any]:
        out: dict[str, Any] = {"profile": self.name,
                               "models": {k: self.model_for(k, cheap=False)
                                          for k in (*costs.SYSTEM_ONE, *costs.SYSTEM_TWO)}}
        if self.cheaper:
            out["cheaper"] = {k: self.resolve(v) for k, v in self.cheaper.items()}
        if self.budget:
            out["budget"] = {"per_hour": self.budget.per_hour, "over": self.budget.action}
        return out


def load(name_or_path: str | None) -> Profile:
    """A profile by name (brain/profiles/<name>.toml) or path; the built-in defaults for None."""
    if not name_or_path:
        return Profile()
    path = Path(name_or_path)
    if not path.suffix:
        path = PROFILES_DIR / f"{name_or_path}.toml"
    if not path.exists():
        known = ", ".join(sorted(p.stem for p in PROFILES_DIR.glob("*.toml"))) or "none"
        raise ValueError(f"no profile {name_or_path!r} (profiles: {known})")
    data = tomllib.loads(path.read_text())
    b = data.get("budget") or {}
    budget = costs.Budget(float(b["per_hour"]), str(b.get("over", "slow"))) if "per_hour" in b else None
    return Profile(path.stem, {k: str(v) for k, v in (data.get("models") or {}).items()},
                   {k: str(v) for k, v in (data.get("cheaper") or {}).items()},
                   {k: str(v) for k, v in (data.get("aliases") or {}).items()}, budget)


active = Profile()


# ---------------------------------------------------------------- system one through a chat model

CHAT_SYSTEM = """You answer typed questions about a state, for a game-playing agent. The user message
has the state (JSON) and the questions, keyed by name. Each question has a type:

- "noul": a yes/no question. Its criteria say what "true" and "false" mean. Answer with your
  probability that "true" holds, a number from 0 to 1.
- "choice": pick one of the options in its criteria (option id -> what it means). Answer with an
  object giving your probability for every option id; they sum to 1.
- "score": rate on the ordered levels in its criteria (a list; the first is level 0). Answer with
  an object giving your probability for each level index ("0", "1", ...); they sum to 1.

Probabilities, not just a pick: say how sure you are. Follow each question's instructions and
criteria, and judge from the state alone.

Reply with one JSON object and nothing else: question name -> answer, for every question."""


def chat_prompt(state: Any, questions: dict[str, dict[str, Any]]) -> str:
    return ("State:\n" + json.dumps(state, separators=(",", ":")) + "\n\nQuestions:\n"
            + json.dumps(questions, separators=(",", ":")) + "\n\nAnswer every question: "
            + ", ".join(questions) + ".")


def answer_schema(questions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """A JSON schema for the reply, for models that take structured outputs."""
    props: dict[str, Any] = {}
    for name, q in questions.items():
        if q.get("type") == "noul":
            props[name] = {"type": "number"}
        else:
            keys = list(q.get("criteria") or {}) if q.get("type") == "choice" else \
                [str(i) for i in range(len(q.get("criteria") or []))]
            props[name] = {"type": "object", "properties": {k: {"type": "number"} for k in keys},
                           "required": keys, "additionalProperties": False}
    return {"type": "json_schema", "json_schema": {"name": "answers", "strict": True, "schema": {
        "type": "object", "properties": props, "required": list(props), "additionalProperties": False}}}


def first_json(text: str) -> Any:
    """The JSON object in a reply, also when it is fenced or has words around it."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except ValueError:
        pass
    start, depth = text.find("{"), 0
    for i in range(max(start, 0), len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0 and start >= 0 and i > start:
            try:
                return json.loads(text[start:i + 1])
            except ValueError:
                return None
    return None


def as_prob(v: Any) -> float | None:
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return min(1.0, max(0.0, float(v) / 100 if v > 1 else float(v)))
    if isinstance(v, str):
        if v.strip().lower() in ("true", "yes"):
            return 1.0
        if v.strip().lower() in ("false", "no"):
            return 0.0
        try:
            return as_prob(float(v.strip().rstrip("%")))
        except ValueError:
            return None
    if isinstance(v, dict):
        for k in ("true", "yes", "probability", "p"):
            if k in v:
                return as_prob(v[k])
    return None


def distribution(v: Any, keys: list[str]) -> dict[str, float]:
    """The stated probabilities over the keys, normalised. A bare pick counts as certain; nothing
    usable gives an even spread."""
    if isinstance(v, str) and v in keys:
        return {k: 1.0 if k == v else 0.0 for k in keys}
    if isinstance(v, dict):
        for inner in ("probabilities", "options"):
            if isinstance(v.get(inner), dict):
                v = v[inner]
        raw = {k: as_prob(v.get(k)) or 0.0 for k in keys}
        total = sum(raw.values())
        if total > 0:
            return {k: p / total for k, p in raw.items()}
    return {k: 1.0 / len(keys) for k in keys} if keys else {}


def parse_answers(data: Any, questions: dict[str, dict[str, Any]]) -> tuple[Answers, list[str]]:
    """The reply as Answers, and the questions it left unanswered (given an even spread or 0.5)."""
    out, missing = Answers(), []
    data = data if isinstance(data, dict) else {}
    for name, q in questions.items():
        v = data.get(name)
        kind = q.get("type")
        if kind == "noul":
            p = as_prob(v)
            if p is None:
                missing.append(name)
                p = 0.5
            out.nouls[name] = p
            continue
        keys = list(q.get("criteria") or {}) if kind == "choice" else \
            [str(i) for i in range(len(q.get("criteria") or []))]
        if v is None:
            missing.append(name)
        dist = distribution(v, keys)
        if kind == "choice":
            pick = max(dist, key=dist.get) if dist else ""
            out.choices[name] = ChoiceResult(pick, dist, dist.get(pick, 0.0))
        else:
            # Jev's scores come back as the level from 0 (first) to 1 (last): the expected level here.
            span = max(len(keys) - 1, 1)
            out.scores[name] = sum(int(k) * p for k, p in dist.items()) / span
    return out, missing


REASONS_ALWAYS = re.compile(r"gpt-oss|gpt-5|deepseek-r1|thinking|/o\d")


class ChatJudge:
    """Jev's questions put to any chat model on OpenRouter (cuo-m70.2). Uses structured outputs
    where the model takes them; else asks for JSON and reads the first object in the reply."""

    def __init__(self, model: str, timeout: float = 20.0, max_tokens: int = 2000, reasoning: str | None = None):
        self.model = model
        self.name = f"chat/{model}"
        self.timeout = timeout
        self.max_tokens = max_tokens
        # Thinking costs time a decision tick doesn't have: off unless the model always reasons, and
        # then as little as it allows. (Asking a hybrid model for "low" turns its thinking on.)
        self.reasoning = reasoning or ("low" if REASONS_ALWAYS.search(model) else None)
        self.schema_ok = True  # until the model refuses a schema
        self.missing = 0       # questions left unanswered, over all calls

    async def ask(self, state, questions) -> Answers:
        msgs = [{"role": "system", "content": CHAT_SYSTEM}, {"role": "user", "content": chat_prompt(state, questions)}]
        extra: dict[str, Any] = {}
        if self.reasoning:
            extra["reasoning"] = {"effort": self.reasoning, "exclude": True}
        t = time.perf_counter()
        try:
            res = await llm.chat(msgs, model=self.model, max_tokens=self.max_tokens, temperature=0,
                                 cache=self.model.startswith("anthropic/"), timeout=self.timeout, retries=1,
                                 response_format=answer_schema(questions) if self.schema_ok else None, extra=extra)
        except llm.LlmError as e:
            if not (self.schema_ok and e.status == 400):
                raise
            self.schema_ok = False  # no structured outputs here: plain JSON in the reply
            res = await llm.chat(msgs, model=self.model, max_tokens=self.max_tokens, temperature=0,
                                 timeout=self.timeout, retries=1, extra=extra)
        out, missing = parse_answers(first_json(res.content or ""), questions)
        self.missing += len(missing)
        out.model = res.usage.model or self.model
        out.input_tokens = res.usage.prompt_tokens
        out.output_tokens = res.usage.completion_tokens
        out.cost = res.usage.cost or 0.0
        out.latency_ms = (time.perf_counter() - t) * 1000
        return out

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------- one judge, a model per kind

class RoutedJudge:
    """The judge the brain uses: each system-one call goes to the model the active profile names
    for its kind (costs.current_kind), Jev or a chat model."""

    def __init__(self, profile: Profile | None = None, provider: str = "auto"):
        self.profile = profile or active
        self.provider = provider
        self._judges: dict[str, Any] = {}
        one = set(self.profile.system_one().values())
        self.name = self.judge_for(next(iter(one))).name if len(one) == 1 else \
            "mixed: " + ", ".join(f"{k}={v}" for k, v in self.profile.system_one().items())

    def judge_for(self, model: str):
        if model not in self._judges:
            if is_jev(model):
                self._judges[model] = judges.JevJudge(self.provider, None if model == JEV else model)
            else:
                self._judges[model] = ChatJudge(model)
        return self._judges[model]

    async def ask(self, state, questions) -> Answers:
        return await self.judge_for(self.profile.model_for(costs.current_kind("fight"))).ask(state, questions)

    async def close(self) -> None:
        for j in self._judges.values():
            await j.close()


def make_judge(kind: str = "jev", provider: str = "auto", profile: Profile | None = None):
    """The rule judge, or the routed one for the active (or given) profile. `kind` may also be an
    alias or model id: every system-one kind then goes to that model."""
    judges.load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    if kind == "heuristic":
        return judges.HeuristicJudge()
    p = profile or active
    if kind != "jev":
        p = Profile(p.name, {**p.models, "system1": p.resolve(kind)}, p.cheaper, p.aliases, p.budget)
        p.models = {k: v for k, v in p.models.items() if k not in costs.SYSTEM_ONE}
    return RoutedJudge(p, provider)


def from_args(args) -> Profile:
    """The profile for a command: --profile, then --model, --planner-model and each --use."""
    p = load(getattr(args, "profile", None))
    if getattr(args, "model", None):
        p.models["system1"] = args.model
    if getattr(args, "planner_model", None):
        p.models["system2"] = args.planner_model
    for spec in getattr(args, "use", None) or []:
        p.use(spec)
    return p
