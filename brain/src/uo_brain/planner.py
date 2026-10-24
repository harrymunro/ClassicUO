"""The slow planner: a chat model chooses the session's goals.

The player gives the session one goal in words ("hunt the undead at the Britain graveyard,
keep yourself supplied with bandages, bank your gold"). Whenever the current goal ends, and
in any case every few minutes because hunts are time-boxed, the planner model (Claude Haiku
5.5 through OpenRouter by default; --profile sonnet for Sonnet) is shown the character's
situation in words, the session goal and what happened so far, and answers by calling one tool: a goal for code to carry out
(travel_to, hunt, bank, buy, sell, rest, set_strategy, finish), or a world-store query first.
There are no scripted routes: the planner puts the loop together itself. Jev keeps the
tactical decisions inside each goal.

Each planning step rebuilds the conversation from a fixed, cached system prompt plus a short
record of the session, instead of growing one transcript for hours, so a call stays a few
thousand tokens. Tokens and cost of every call go to the JSONL log.
"""

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import calls, compact, costs, heartbeat, llm, longgoals, memory as memories, world as worlds
from . import machine as machines
from .facts import Ranker
from .logs import session_name
from .party import Party
from .session import Result, Session, describe

SYSTEM = """You plan a character's session in the game Ultima Online. The player gave you a goal in
their own words. You choose what the character does next, one goal at a time, by calling
exactly one tool. Code carries the goal out and fights, heals and loots on its own, so you
only decide where to go and what to do there; you never control single steps or attacks.

How a session usually goes, which you should arrange yourself as the player's goal needs:
buy supplies (bandages from a healer, reagents from a mage shop), travel to a hunting area,
hunt for a while, come back when supplies run low or the bag gets heavy, bank gold and loot,
restock, and go again. Before hunting, make sure there are enough supplies: a warrior wants
at least 50 bandages, a mage at least 30 of each reagent, an archer at least 50 bandages and
150 arrows or bolts for the bow in hand (a bowyer or a provisioner sells them), a tamer at least
80 bandages, for itself and its pet, a necromancer at least 30 of each of its own reagents (bat wing,
grave dust, daemon blood, nox crystal, pig iron) and 30 bandages, a paladin what a warrior wants plus
tithing points for its blessings (it fights on without them). A tamer whose pet has died can't hunt: finish the goal and say so. Selling loot is optional; banking
it is enough. Vendors take gold from the backpack, not the bank: buy before banking, or keep
some back when banking (withdraw 'gold:200').

Rules:
- Answer with one tool call. Give `why` in a short sentence; the player sees it.
- Use the world tools (place, find_place, hunting_spots, what_spawns, route, notes, outcomes)
  when you need a fact, rather than guessing coordinates. Places can be named loosely ("Britain
  bank"). outcomes says how earlier hunts in an area went for this character.
- Give notes, hunting_spots and what_spawns a `question` (what you want to find out): their
  results then come back best answer first.
- A hunt is time-boxed (`minutes`, at most 20) and also ends early when supplies run low,
  the bag gets heavy, the character is in danger, or nothing has shown up for a while. Its
  result says why it ended; plan the next goal from that.
- If a hunt ended because the character left the area (stronger creatures or a pack came at it;
  the result names them), or because a red or criminal player came close, don't hunt there again
  for at least 30 minutes: they are still about,
  and another spot of the same place counts as there. Code refuses goals within 30 tiles of it
  for 30 minutes. Bank, restock or hunt somewhere else. hunting_spots marks areas with danger
  seen recently.
- A hunt that ended with nothing to fight for a while found its ground empty: its creatures come
  back one at a time (the result says how often), so code refuses another hunt there for 15
  minutes. Hunt another ground, or do other work and come back. A refused hunt names the nearest
  grounds where nothing is stronger than the character; another ground may hold creatures that
  are (savages, liches), so ask what_spawns before going to one it doesn't name.
- If a goal fails, read why and try something different (another vendor, another area,
  waiting for a respawn) rather than repeating the same thing. A vendor that is sold out
  restocks in about an hour: buy elsewhere, or go on with what you have if it is enough.
- If the character is dead, call resurrect: as a ghost it walks to the nearest healer. Once alive,
  call recover_corpse to go back and take its things from its corpse, unless what killed it is still
  standing there (then wait or rest first, or leave the corpse if it is lost). A corpse decays in
  some minutes (your_corpse says how many are left), so don't put it off long. Call finish when the player's goal is done or impossible,
  or the character can't be resurrected. A resurrected character has little health: rest until it
  is nearly full before going back to the corpse with creatures about, and before hunting.
- The character may have long goals that outlive this session (skills to raise, gold to save,
  places to learn), shown with their progress. Work towards them when the player's goal allows,
  and choose from them when the player gave none. Code measures skills, gold banked and places
  visited; mark a goal done with long_goal when you judge it done and code can't measure it.
- The character has a memory of its earlier goals and sessions. The few memories that bear on
  this moment, and the player's standing orders, are shown with the situation; follow the orders,
  and don't repeat a remembered mistake without a reason. recall searches the memory for more.
- When you conclude something worth carrying into later sessions (a spot that keeps going wrong,
  a vendor that is often sold out, what worked), keep it with remember, then choose the goal.
  Code already keeps hunts, leaves, deaths and refusals.

How it fights: by default code plays every fight the same way. With set_machine you can give it
a plan for its fights instead, as a small state machine (states in words, which intents each
allows, yes/no transitions Jev judges; see the tool). Use it when the fights call for a shape the
default doesn't have, for example pulling one creature at a time, backing off to heal before
going on, or a mage keeping its spells landing against a crowd. It applies to hunts, and stays
until replaced or cleared. Change it only on evidence: a hunt's result reports the time spent in
each of its states and how the hunt went.
"""


def goal_tools() -> list[dict[str, Any]]:
    def fn(name: str, description: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
        props = {**props, "why": {"type": "string", "description": "One short sentence for the player: why this goal now."}}
        return {"type": "function", "function": {"name": name, "description": description,
                                                 "parameters": {"type": "object", "properties": props,
                                                                "required": [*required, "why"]}}}

    s = {"type": "string"}
    return [
        fn("travel_to", "Walk to a named place (a town, a bank, a graveyard, a vendor) or an x,y tile. "
           "Fights what attacks on the way. Returns when there or stuck.",
           {"place": {**s, "description": "A place name, or 'x,y'."}}, ["place"]),
        fn("hunt", "Go to a hunting area (walking there first if needed) and fight there for a while. "
           "Returns kills, supplies used and why it stopped.",
           {"area": {**s, "description": "A spawn area or place name, e.g. 'Britain Graveyard'."},
            "minutes": {"type": "number", "description": "How long at most, 3 to 20."}}, ["area", "minutes"]),
        fn("bank", "At the nearest bank, or the one in a town you name (walking there first): deposit gold "
                   "and/or loot, withdraw supplies. Every bank opens the same box.",
           {"deposit": {**s, "description": "Comma list from gold, loot, all; '' for none."},
            "withdraw": {**s, "description": "Comma list of item:count, e.g. 'bandage:100' or 'gold:200'; '' for none."},
            "place": {**s, "description": "Optional: the town or place whose bank, e.g. 'Vesper'; the nearest when not given."}},
           []),
        fn("buy", "Buy an item from the nearest vendor that sells it (walking there first, if it is within 200 "
                  "tiles; a further shop is named in the result, for a travel_to first if the trip is worth it).",
           {"item": {**s, "description": "e.g. bandage, black pearl, heal potion."},
            "count": {"type": "integer"},
            "vendor_kind": {**s, "description": "Optional vendor kind, e.g. healer, mage_shop."}}, ["item", "count"]),
        fn("sell", "Sell loot (or named items) to the nearest vendor of a kind (walking there first).",
           {"items": {**s, "description": "'loot' or a comma list of item words."},
            "vendor_kind": {**s, "description": "e.g. weaponsmith, armourer, jeweler."}}, ["vendor_kind"]),
        fn("rest", "Stay put to recover health and mana, or wait for monsters to respawn.",
           {"seconds": {"type": "number"}}, ["seconds"]),
        fn("set_strategy", "Set your own lines in the character's fighting strategy, in plain words (how to fight, "
           "when to flee, what to loot). Jev follows the whole strategy inside fights. Your lines replace your "
           "earlier ones and never the player's; empty text takes yours out. At most once every 10 minutes: give a "
           "change time to show in a hunt.", {"text": s}, ["text"]),
        fn("resurrect", "As a ghost, walk to the nearest healer and be resurrected (the healer offers when the "
           "ghost steps up to it). Only when the character is dead.", {}, []),
        fn("recover_corpse", "After being resurrected, walk back to where the character died, open its corpse "
           "and take back everything (equipment goes back on). Fails when the corpse has decayed or is gone. Refuses "
           "while danger was seen there or close by in the last 15 minutes, unless go_anyway.",
           {"go_anyway": {"type": "boolean", "description": "Go even though danger was seen there lately."}}, []),
        fn("finish", "End the session: the goal is done or impossible, or the character can't be resurrected.",
           {"summary": s}, ["summary"]),
    ]


def machine_tool() -> dict[str, Any]:
    return {"type": "function", "function": {
        "name": "set_machine",
        "description": "Give the character a plan for its fights in hunts, as a state machine, or clear it to go "
                       "back to the default way of fighting. Code checks it and says what is wrong if it can't be used.",
        "parameters": {"type": "object", "properties": {
            "machine": machines.schema(),
            "clear": {"type": "boolean", "description": "Go back to the default way of fighting."},
            "why": {"type": "string", "description": "One short sentence for the player: why this plan now."}},
            "required": ["why"]}}}


GOALS = {"travel_to", "hunt", "bank", "buy", "sell", "rest", "set_strategy", "set_machine", "resurrect",
         "recover_corpse", "finish", "restock", "inn", "evening"}
# Goals that are a member's own business: a party forming up for a hunt doesn't wait for one who is on these
# (rally.expected; a follower says the same of its resurrection, its corpse and its restock, party.Follower.own).
OWN_BUSINESS = ("bank", "buy", "sell", "restock", "resurrect", "recover_corpse")


def inn_tool() -> dict[str, Any]:
    """A party leader's call for R&R at the inn (cuo-cod.3): the walk there, the table and the talk."""
    return {"type": "function", "function": {
        "name": "inn",
        "description": "Take the whole party to the nearest inn for some rest: you walk there together, sit down, have "
                       "a few drinks and talk the hunt over, each from their own part of it, and each remembers what "
                       "was said. Choose it when a hunt is over (what you came for is dead, or the party has had "
                       "enough), after a death once everyone is back on their feet, and before ending a session: an "
                       "inn is a safe place to finish. It takes a few minutes. Not with creatures still about, not "
                       "twice running, and not before the party has done anything worth talking over.",
        "parameters": {"type": "object", "properties": {
            "why": {"type": "string", "description": "One short sentence for the player: why this goal now."}},
            "required": ["why"]}}}


def evening_tool(place: str) -> dict[str, Any]:
    """A hamlet resident's evening (cuo-m3n.5): the neighbours' gathering, the talk over the day, and memories of it."""
    return {"type": "function", "function": {
        "name": "evening",
        "description": f"Of an evening, go to {place} where the neighbours gather, sit with whoever comes and talk the "
                       "day over: what each did, news, worries, tomorrow. Each remembers what was said. In the evening, "
                       "or up to 3 minutes before it to be there when it begins (each turn says the time of day); it "
                       "takes a few minutes, and it is the hamlet's way of ending a day.",
        "parameters": {"type": "object", "properties": {
            "why": {"type": "string", "description": "One short sentence for the player: why this goal now."}},
            "required": ["why"]}}}


def restock_tool(own: bool = False) -> dict[str, Any]:
    """A party leader's call for everyone to buy their own supplies (cuo-4ar); `own`: for a member that
    plans for itself (a party that plans by huddle), its own supplies only."""
    what = ("Buy what the character is short of for what it plays as, at the nearest shops: bandages, reagents, "
            "arrows, drawing gold from the bank first when it carries too little. Only when it can't fight without "
            "them: the party goes on meanwhile.") if own else (
        "The whole party, you too, buys its own supplies at the nearest shops: bandages, reagents, "
        "arrows, whatever each is short of for what it plays as, drawing gold from the bank first "
        "when it carries too little. Call it when anyone in the party runs low.")
    return {"type": "function", "function": {
        "name": "restock",
        "description": what,
        "parameters": {"type": "object", "properties": {
            "why": {"type": "string", "description": "One short sentence for the player: why this goal now."}},
            "required": ["why"]}}}

# A huddle member the plan sends into its hunt goes for this long (the hunt tool's own range is 3 to 20 minutes),
# from this much health up (cuo-cym.14).
STEER_MINUTES = 15
STEER_HP = 50

# The planner's own lines go in a block of their own at the end of the strategy, under this line,
# so a change of plan replaces only them and never the player's lines (cuo-dax).
PLANNER_HEAD = "From the planner (it may change these):"
STRATEGY_EVERY_S = 600  # the planner may change its lines at most this often
# Room for a whole turn. At 600, 12 of 144 Haiku turns in the logs of 2026-10-08/09 were cut off, most
# arriving with empty arguments (a set_machine among them: a plan takes 700 to 1,550 tokens); Sonnet's
# turns used at most 569 (cuo-twd). Only what a turn writes is paid for.
MAX_TOKENS = 2000


def with_planner_lines(strategy: str, lines: str) -> str:
    """The player's part of a strategy, then the planner's block with `lines` ("" drops the block)."""
    player = strategy.split(PLANNER_HEAD, 1)[0].rstrip()
    lines = lines.strip()
    if not lines:
        return player
    return f"{player}\n\n{PLANNER_HEAD}\n{lines}" if player else f"{PLANNER_HEAD}\n{lines}"


@dataclass
class PlanStep:
    tool: str
    args: dict[str, Any]
    result: dict[str, Any]
    t: float = field(default_factory=time.time)

    def line(self) -> str:
        args = ", ".join(f"{k}={v!r}" for k, v in self.args.items() if k != "why")
        return f"{self.tool}({args}) -> {json.dumps(self.result, separators=(',', ':'))[:400]}"


class Planner:
    def __init__(self, session: Session, goal: str, model: str | None = None,
                 chat_fn: llm.ChatFn = llm.chat, log: Callable[[dict[str, Any]], None] | None = None,
                 max_queries: int = 6, on_goal: Callable[[str, str, str], Any] | None = None,
                 machines_allowed: bool = True, memory: "memories.Memory | bool | None" = None,
                 escalate: bool = True, persona: dict[str, str] | None = None, resume: bool = True,
                 party: "Party | None" = None, table: Any = None, huddle: Any = None):
        self.session = session
        # A party this planner leads (cuo-4ar): its goals are posted for the others to follow. With a huddle
        # (huddle.Huddle, cuo-bih) nobody leads: every member has a planner like this one, and they agree a
        # plan that each reads before choosing its own goals.
        self.party = party
        self.huddle = huddle
        self.died_at_wall: float | None = None  # when this character last died, by the clock (for the party's reports)
        self.table = table  # table.Table: the party's table at the inn, which the leader keeps (cuo-cod.3, .4)
        self.goal = goal
        self.model = model  # None: the active profile's, chosen at each call (models.py), cheaper over budget
        self.chat = chat_fn
        self.log = log or (lambda _: None)
        self.max_queries = max_queries
        self.on_goal = on_goal  # (goal, step, why) for the in-game panel
        self.history: list[PlanStep] = []
        self.usage = llm.LlmUsage(calls=0)
        # An NPC's persona (cuo-x2q.1) joins the system prompt: fixed for the character, so it caches too.
        self.system = SYSTEM + persona_words(persona) if persona else SYSTEM
        # A resident of a hamlet (cuo-m3n): where it lives, its home, work and neighbours, fixed for the run too.
        if (hamlet := getattr(session, "hamlet", None)) is not None:
            self.system += hamlet.words()
        self.ears = None  # talk.Ears: what an NPC hears from and says to the fleet's other characters
        self.deaths = 0                # deaths seen by the planner, resurrections and corpses taken back (cuo-4b3)
        self.resurrections = 0
        self.corpses_recovered = 0
        self.dead_since: float | None = None
        self.corpse_at: tuple[int, int, float] | None = None  # the corpse still to take back: x, y, when
        # A party's hunt that is won: what it killed at the place, when and where (x, y). Said to the leader at
        # every step until something hostile shows, it moves on, or the party has talked it over at the inn.
        self.won: tuple[list[str], float, int, int] | None = None
        self.time_dead_s = 0.0
        self.pack_checked = False  # the first call waits for the pack's bags to be looked into (cuo-pz9)
        self.strategy_set_at = -1e9   # when the planner last changed its strategy lines (monotonic)
        self.strategy_changes = 0
        # Hard moments go to a stronger model (the profile's `escalate`, Sonnet by default; cuo-d4j).
        self.escalate = escalate
        self.escalations: list[str] = []
        self.escalation_usage = llm.LlmUsage(calls=0)
        self.no_tool = 0               # planner calls in a row that ended without a usable goal
        self.escalated_last = False
        self.finished: str | None = None
        self.ends_at: float | None = None  # when the session's hours are up (run): no hunt is begun to outlast it
        self.on_errand = False  # carrying out a goal of its own (OWN_BUSINESS): a huddle's rally doesn't wait for it
        self.steered: set[int] = set()  # the huddle's plans (revisions) whose hunt this member was sent on (cuo-cym.14)
        self.party_waited = party is None  # a party's planner waits once, at the start, for the others to join
        self.started = time.monotonic()
        # The character's own memory (memory.py), in the shard's store: by default whenever the session has
        # one; False runs without (for comparing, cuo-6w8.7).
        world = getattr(session, "world", None)
        if memory is None or memory is True:
            dlog = getattr(session, "decisions_log", None)
            memory = memories.Memory(world, session=session_name(dlog) if dlog else None) \
                if isinstance(world, worlds.World) else None
        self.memory: memories.Memory | None = memory or None
        # Steps that leave the planner's view are folded into a running summary, with a memory flush
        # first (compact.py).
        self.folder = compact.Folder(self.memory, chat_fn, self.log)
        # Reflections every so often and on events, a daily digest, and the state saved after each step so
        # a restarted brain carries on (heartbeat.py). Only with the character's memory.
        self.heart = heartbeat.Heartbeat(self.memory, chat_fn, self.log) if self.memory else None
        self.resumed = not resume  # resume=False (--fresh): never pick up a saved state
        self.restored = 0          # steps at the start of history carried over from a saved state
        self._last_session: str | None = None
        self._picked: tuple[str | None, int, str] | None = None  # area, step and section of the last memory pick
        # The character's long goals (longgoals.py), kept beside its memories in the store, and kept with memory off
        # too: without them an NPC with no goal of its own had nothing to do, and finished at once (cuo-rsw).
        store = self.memory.world if self.memory else getattr(session, "world", None)
        self.long = longgoals.LongGoals(store) if store is not None else None
        self.tools = goal_tools() + ([machine_tool()] if machines_allowed else []) + worlds.tool_schemas() \
            + ([memories.remember_tool(), memories.recall_tool()] if self.memory else []) \
            + ([longgoals.tool()] if self.long is not None else []) \
            + ([restock_tool(own=huddle is not None)] if party is not None else []) \
            + ([inn_tool()] if party is not None and table is not None else []) \
            + ([evening_tool(hamlet.gather["words"])] if party is None and table is not None
               and (hamlet := getattr(session, "hamlet", None)) is not None and hamlet.gather else [])
        # Jev picks the few memories each call sees (memory.Picker), and answers the recall tool.
        self.picker = memories.Picker(self.memory, getattr(session, "judge", None), log=self.log) \
            if self.memory else None
        # The plan for its fights (cuo-6om), kept across goals and handovers until replaced.
        self.machine: machines.Runner | None = getattr(session, "machine", None)
        # Jev re-ranks list answers against the planner's question (facts.py); the rule judge doesn't.
        self.ranker = Ranker(getattr(session, "judge", None), log=self.log)

    def messages(self, situation: dict[str, Any], remembered: str = "") -> list[dict[str, Any]]:
        # The steps not yet folded into the summary, word for word (12, up to 15 between folds); with
        # folding off (or failing), the last 15.
        first = max(self.folder.folded, len(self.history) - compact.KEEP_RECENT - compact.BATCH + 1)
        recent = self.history[first:]
        record = "\n".join(f"{first + i + 1}. {s.line()}" for i, s in enumerate(recent)) or "(nothing yet)"
        earlier = first - self.folder.folded
        if summary := self.folder.section():
            record = f"{summary}\n\nThe goals since, word for word:\n" + record
        elif last := self.last_session():
            record = f"The last session, summarised: {last}\n\nThis session so far:\n" + record
        if earlier > 0:
            record = f"({earlier} earlier goals not shown)\n" + record
        long = self.long.section(self.goal) if self.long else ""
        if self.party is not None:
            pos = situation.get("position") or {}
            team = self.party.section((pos["x"], pos["y"]) if "x" in pos else None, leading=self.huddle is None)
            long = f"{long}\n\n{team}" if long else team
        if self.huddle is not None:
            long = f"{long}\n\n{self.huddle.section()}" if long else self.huddle.section()
        if self.heart and (focus := self.heart.section()):
            long = f"{long}\n\n{focus}" if long else focus
        user = (f"The player's goal: {self.goal or '(none for this session)'}\n\n"
                + (f"{long}\n\n" if long else "")
                + f"What has happened so far, oldest first:\n{record}\n\n"
                + (f"{remembered}\n\n" if remembered else "")
                + (f"{heard}\n\n" if (heard := self.ears.section() if self.ears else "") else "")
                + f"The character now: {json.dumps(situation)}\n\n"
                "Choose the next goal by calling one tool.")
        return [{"role": "system", "content": self.system}, {"role": "user", "content": user}]

    async def step(self) -> PlanStep | None:
        """One planning decision, with any world queries it needs, then the goal carried out. Jev
        fights what attacks while the model thinks (Session.defended): a hunt that ended in a leave
        hands over with the creatures possibly still about."""
        if (budget := costs.ledger.budget) and costs.ledger.over_budget():
            rate = f"${costs.ledger.rate():.3f}/h against a cap of ${budget.per_hour:.3f}/h"
            if budget.action == "stop":
                return await self.carry_out("finish", {"summary": f"stopped: over budget ({rate})",
                                                       "why": "the session is over its budget"})
            if budget.action == "slow":
                # Thinking is the dear part: rest a minute where it stands instead, until the spending
                # of the last ten minutes is back under the cap.
                return await self.carry_out("rest", {"seconds": 60, "why": f"over budget ({rate}): waiting"})
        if self.huddle is not None:
            # A new plan cut the last goal short (huddle.Huddle.watch): this step chooses with it. Left set,
            # every goal after the first change of plan ended the moment it began.
            self.session.interrupt.clear()
        snap = await self.session.snap()
        if not snap.get("in_game"):
            # Out of game (a lost connection; a fleet logs its agents in again, cuo-m12): wait, don't plan.
            self.log({"type": "planner", "t": time.time(), "waiting": "not in game"})
            await asyncio.sleep(5)
            return None
        if not self.party_waited:
            self.party_waited = True
            await self.wait_for_party()
            snap = await self.session.snap()
        self.know(snap)
        if snap.get("in_game") and snap["player"]["dead"]:
            self.died(snap)
        elif self.dead_since is not None:
            self.time_dead_s += time.monotonic() - self.dead_since
            self.dead_since = None
        await self.track_long_goals(snap)
        if not self.pack_checked:
            # The first call waits for the client to look into the pack's bags (cuo-pz9): a new mage's
            # reagents are in one, and told of none it went shopping for them.
            self.pack_checked = True
            if not await self.session.pack_read():
                self.log({"type": "planner", "t": time.time(), "note": "some bags in the pack not looked into after 10 s"})
            snap = await self.session.snap()
        situation = describe(snap, self.session.world, self.party.companions if self.party else None)
        if (hamlet := getattr(self.session, "hamlet", None)) is not None:
            situation["home"] = hamlet.where(snap["player"]["x"], snap["player"]["y"])
            if hamlet.gather:
                situation["time_of_day"] = hamlet.when(time.time())
        if self.corpse_at and snap.get("in_game") and not snap["player"]["dead"]:
            x, y, t = self.corpse_at
            ago, lasts = time.monotonic() - t, getattr(self.session, "CORPSE_LASTS_S", 14 * 60)
            situation["your_corpse"] = (f"at {x},{y}, from a death {ago / 60:.0f} min ago, with your things on it until "
                                        f"you recover_corpse: it lasts about {max(0.0, lasts - ago) / 60:.0f} more minutes"
                                        if ago < lasts else f"at {x},{y}, from a death {ago / 60:.0f} min ago: it will "
                                        f"have decayed by now, with your things")
            if held := self.party_at(x, y):
                situation["your_corpse"] += (f"; {' and '.join(held)} of your party {'is' if len(held) == 1 else 'are'} "
                                             f"there now, so the place is held: go back for it")
            elif (off := [n for n, st in (self.party.statuses().items() if self.party else [])
                          if self.driven_off(st, x, y)]):
                situation["your_corpse"] += (f"; {', '.join(off)} of your party had to leave that place, so it has "
                                             f"not been won: the danger seen there stands")
            elif len(escort := self.escort(snap)) >= 2:
                situation["your_corpse"] += (f"; {', '.join(escort)} of your party came through that fight alive and are "
                                             f"with you: they go back there with you when you call recover_corpse, so "
                                             f"don't wait for the danger seen there to age, the corpse decays")

        if self.won and snap.get("in_game"):
            dead, t, wx, wy = self.won
            p = snap["player"]
            # Something that means harm: in war mode, or on the character (a bird in sight is counted as a hostile).
            harm = any(m.get("monster") and not m.get("dead") and (m.get("war_mode") or m.get("attacking_me"))
                       for m in snap.get("mobiles") or [])
            if harm or max(abs(p["x"] - wx), abs(p["y"] - wy)) > 40:
                self.won = None
            else:
                situation["the_hunt_here"] = (f"won: you and your party killed {', '.join(dead)} here "
                                              f"{(time.monotonic() - t) / 60:.0f} min ago, and nothing hostile has "
                                              f"been seen since")

        if (sent := await self.sent_in(snap, situation)) is not None:
            # The huddle's plan names this member for its hunt: it goes, as a follower goes on the leader's plan,
            # instead of its planner being asked again (cuo-cym.14).
            return await self.carry_out(*self.steer(*sent))

        hard = self.hard_moment()
        model = self.escalation_model() if hard else None
        if model:
            self.escalations.append(hard)
            self.log({"type": "escalation", "t": time.time(), "reason": hard, "model": model})
        self.escalated_last = bool(model)

        if self.heart:
            self.heart.saw_area(situation["position"]["area"])

        async def think() -> tuple[str, dict[str, Any]]:
            if self.huddle is not None:
                # The party's huddle (cuo-bih): take part in one that is open, or call one when it is due, so
                # the goal chosen next has the plan agreed in front of it.
                try:
                    await self.huddle.maybe(snap, situation, self.history[-1] if self.history else None)
                except Exception as e:  # noqa: BLE001 - no huddle rather than no plan
                    self.log({"type": "huddle", "t": time.time(), "error": f"{type(e).__name__}: {e}"[:300]})
                if (sent := await self.sent_in(snap, situation)) is not None:
                    # The plan agreed in this huddle sends it in: the first plan of a run is agreed here, inside the
                    # members' first step, and asked again two of five rested a minute and went in 18 s behind.
                    return self.steer(*sent)
            # Housekeeping never costs the session: a fold or a reflection that breaks is logged and skipped.
            try:
                await self.folder.maybe_fold(self.history, self.goal)
                if self.heart:
                    await self.heart.beat(situation, self.goal, self.long.section(self.goal) if self.long else "",
                                          self.folder.summary)
            except Exception as e:  # noqa: BLE001
                self.log({"type": "housekeeping", "t": time.time(), "error": f"{type(e).__name__}: {e}"[:300]})
            msgs = self.messages(situation, await self.remembered(situation))
            return await (self.choose(msgs, kind="escalate", model=model) if model else self.choose(msgs))

        defended = getattr(self.session, "defended", None)
        tool, args = await (defended(think()) if defended else think())
        return await self.carry_out(tool, args, chosen=True)

    def steer(self, area: str, rev: int) -> tuple[str, dict[str, Any]]:
        """The hunt goal a huddle's plan sends this member on (sent_in), noted as the plan's, not its planner's."""
        self.steered.add(rev)
        # Read as the member's own: the plan's watcher (Huddle.watch) cuts a goal short when a plan it hasn't read is on
        # the board, and cut the hunt it was sent on two seconds in, 51 tiles short (run 20261019-154246).
        self.huddle.read()
        self.log({"type": "planner", "t": time.time(), "steered": {"rev": rev, "area": area}})
        return "hunt", {"area": area, "minutes": STEER_MINUTES, "why": f"the party's plan sends me into {area} with the others"}

    async def held_back(self, area: str) -> str:
        """Why a hunt of its own choosing is refused, or "": a huddle member whose party forms up doesn't hunt the area
        the plan names while the plan holds the party back from it (go_now_question). Live with memory on, the plans
        said to enter only after a scout, and the bard's planner hunted the graveyard alone, the first in by 15 s
        (cuo-cym.14, run 20261019-162307)."""
        if self.huddle is None or getattr(self.session, "rally", None) is None:
            return ""
        plan = self.huddle.hunt_area()
        spot = getattr(self.session, "hunt_spot", None)
        try:
            here = spot(area) if spot else None
        except Exception:  # noqa: BLE001 - an area it can't place: left to the hunt itself
            here = None
        if plan is None or here is None or here[2].lower() != plan.lower():
            return ""
        if await self.huddle.sends_in_now(getattr(self.session, "judge", None), plan):
            return ""
        board = self.huddle.board() or {}
        return (f"the party's plan holds the party back from {plan} for now ({str(board.get('target', ''))[:240]}): "
                f"nobody goes in on their own; choose something else, or rest until the plan changes")

    async def sent_in(self, snap: dict[str, Any], situation: dict[str, Any]) -> tuple[str, int] | None:
        """The hunt the huddle's plan sends this member into, once for each plan, when the party forms up: (area,
        revision), or None for its planner to choose. Live with memory on, the planners asked again after a plan
        that named all five chose rest 18 to 27 times a run, the healer and the shops: their own lessons about the
        lich made each cautious, and the plan's list of who goes bound nobody (cuo-cym.14). Not for one who is
        dead, under STEER_HP, or a mage without reagents: its planner sees to that first, and the rally waits
        a while for it. Not when the plan itself holds the party back (system one reads it, once a plan)."""
        if self.huddle is None or self.party is None or getattr(self.session, "rally", None) is None:
            return None
        board = self.huddle.board()
        if board is None or board["rev"] in self.steered:
            return None
        area = self.huddle.hunt_area()
        if area is None or self.party.me not in (self.huddle.going(area) or []):
            return None
        p = snap.get("player") or {}
        if p.get("dead") or (p.get("hits_max") and 100 * p.get("hits", 0) / p["hits_max"] < STEER_HP):
            return None
        if situation.get("reagents") == "none":
            return None  # a mage with nothing to cast with; bandages are healing, and a fighter fights without them
        # ...and only a plan that sends the party in now: one that says hold, scout first or go in only on a sighting
        # named all five as going all the same (huddle.go_now_question).
        if not await self.huddle.sends_in_now(getattr(self.session, "judge", None), area):
            return None
        return area, board["rev"]

    JOIN_WAIT_S = 90.0

    async def wait_for_party(self) -> float:
        """Before a party's first goal: wait where it stands, fighting what attacks, until every member
        has been heard from (a fleet logs them in one after another and starts each brain as its
        character comes in), or JOIN_WAIT_S have passed. The seconds waited."""
        began = time.monotonic()

        async def wait() -> None:
            others = [m for m in self.party.members if m != self.party.me]
            while time.monotonic() - began < self.JOIN_WAIT_S:
                if all(m in self.party.statuses() for m in others):
                    return
                await asyncio.sleep(1.0)

        defended = getattr(self.session, "defended", None)
        await (defended(wait()) if defended else wait())
        waited = time.monotonic() - began
        self.log({"type": "planner", "t": time.time(), "waited_for_party_s": round(waited, 1),
                  "joined": sorted(self.party.statuses())})
        return waited

    def last_session(self) -> str:
        """Before this session's first fold: the summary kept at the end of the character's last one."""
        if self.memory is None or not self.memory.character:
            return ""
        if self._last_session is None:
            row = self.memory.db.execute(
                "SELECT text FROM memories WHERE character = ? AND source = 'flush' AND stale = 0 "
                "AND text LIKE 'Session of %' ORDER BY t DESC LIMIT 1", (self.memory.character,)).fetchone()
            self._last_session = row["text"][:1500] if row else ""
        return self._last_session

    async def remembered(self, situation: dict[str, Any]) -> str:
        """The memories this call sees, picked by Jev (memory.Picker), as a section of the user turn."""
        if self.picker is None:
            return ""
        try:
            return await self._remembered(situation)
        except Exception as e:  # noqa: BLE001 - no memories rather than no plan
            self.log({"type": "memories", "t": time.time(), "error": f"{type(e).__name__}: {e}"[:300]})
            return ""

    async def _remembered(self, situation: dict[str, Any]) -> str:
        area = situation.get("position", {}).get("area")
        last = self.history[-1] if self.history else None
        eventful = last is None or last.tool in ("hunt", "resurrect", "recover_corpse", "restart", "handover") \
            or not last.result.get("ok") or "stronger creatures seen" in str(last.result.get("result", ""))
        if self._picked is None or eventful or area != self._picked[0] or len(self.history) - self._picked[1] >= 3 \
                or (self.ears and self.ears.in_view):
            chosen = await self.picker.pick(situation, self.goal, [s.line() for s in self.history[-3:]],
                                            people=self.ears.in_view if self.ears else ())
            self._picked = (area, len(self.history), memories.section(chosen))
        return self._picked[2]

    def hard_moment(self) -> str:
        """Why this step should go to the stronger model, or "": the planner gave no usable goal twice
        running, the same goal failed twice running (or any three in a row), or code refused to go back
        where it left (somewhere new to hunt is needed). Never two escalations in a row, and never over a
        budget. A death isn't one (cuo-tyv, 2026-10-12): every model calls resurrect, 25 times of 25, and
        code picks the healer; a resurrect that fails twice is escalated as any goal is."""
        if not self.escalate or self.escalated_last or costs.ledger.over_budget():
            return ""
        if self.no_tool >= 2:
            return "no usable goal from the planner twice running"
        h = [st for st in self.history if st.tool not in ("handover",)]
        if not h:
            return ""
        last = h[-1]
        text = str(last.result.get("result", ""))
        if text.startswith("not going back near "):
            return "code refused to go back where it left: somewhere new is needed"
        # A hunt that ended in a leave isn't escalated by itself (2026-10-09: two of three Sonnet calls in a
        # soak, $0.02 each, both just banked): if Haiku then tries to go back, code's refusal escalates.
        failed = [st for st in h[-3:] if not st.result.get("ok")]
        if len(h) >= 2 and not h[-1].result.get("ok") and not h[-2].result.get("ok") and \
                h[-1].tool == h[-2].tool and target_of(h[-1]) == target_of(h[-2]):
            return f"the same goal failed twice running: {h[-1].tool} {target_of(h[-1])}"
        if len(failed) == 3:
            return "three goals failed in a row"
        return ""

    def escalation_model(self) -> str | None:
        """The model for a hard moment, or None when it is the planner's own (no escalation)."""
        from . import models
        model = models.active.model_for("escalate", cheap=False)
        return None if model == llm.planner_model(self.model) else model

    async def choose(self, msgs: list[dict[str, Any]], kind: str = "planner",
                     model: str | None = None) -> tuple[str, dict[str, Any]]:
        """The model's next goal (tool, arguments), after the world queries it asks first. A hard
        moment (kind "escalate") goes to the stronger model for the whole step."""
        for _ in range(self.max_queries + 1):
            try:
                with costs.kind(kind):
                    res = await self.chat(msgs, tools=self.tools, tool_choice="auto",
                                          model=model or llm.planner_model(self.model), max_tokens=MAX_TOKENS)
            except llm.LlmError as e:
                # A failed call (after the client's own retries) costs a short rest, not the session.
                self.log({"type": "planner", "t": time.time(), "kind": kind, "error": str(e)[:300]})
                self.no_tool += 1
                return "rest", {"seconds": 30, "why": f"the planner model failed: {str(e)[:120]}"}
            if kind == "escalate":
                self.escalation_usage = self.escalation_usage + res.usage
            self.usage = self.usage + res.usage
            if res.tool_calls:
                c = res.tool_calls[0]
                args = {k: v for k, v in (c.arguments or {}).items() if k != "why"}
                calls.emit({"kind": "planner", "title": f"planner: {c.name}" + (" (a world query)" if c.name not in GOALS else ""),
                            "model": res.usage.model or self.model, "latency_ms": round(res.usage.latency_ms or 0, 1),
                            "cost": round(res.usage.cost or 0.0, 5),
                            "questions": [{"q": c.name, "kind": "text",
                                           "title": ", ".join(f"{k}: {str(v)[:60]}" for k, v in args.items())[:200]}],
                            "note": str((c.arguments or {}).get("why", ""))[:200]})
            # What it was sent after the fixed system prompt, so `uo-brain replay-planner` can put the same
            # moment to another model (cuo-m70.3).
            self.log({"type": "planner", "t": time.time(), "kind": kind, "usage": res.usage.to_log(), "messages": msgs[1:],
                      "content": (res.content or "")[:500], "calls": [{"name": c.name, "args": c.arguments} for c in res.tool_calls]})
            if not res.tool_calls:
                msgs += [res.message, {"role": "user", "content": "Answer by calling exactly one tool."}]
                continue
            call = res.tool_calls[0]
            if call.name not in GOALS:
                if call.name == "remember":
                    answer = memories.call_remember(self.memory, call.arguments)
                    self.log({"type": "memory", "t": time.time(), "source": "planner", **(call.arguments or {}),
                              **answer})
                elif call.name == "long_goal":
                    answer = longgoals.call_tool(self.long, call.arguments)
                    self.log({"type": "long_goal", "t": time.time(), "by": "planner", **(call.arguments or {}), **answer})
                elif call.name == "recall":
                    a = call.arguments or {}
                    answer = await self.picker.recall(str(a.get("query", "")), self.goal, a.get("limit") or 5) \
                        if self.picker else {"error": "no memory store in this session"}
                else:
                    answer = await self.ranker.call_tool(self.session.world, call.name, call.arguments, goal=self.goal,
                                                         context=res.content or "")
                msgs += [{**res.message, "tool_calls": res.message.get("tool_calls", [])[:1]},
                         llm.tool_result(call, answer)]
                continue
            self.no_tool = 0
            return call.name, call.arguments
        self.no_tool += 1
        return "rest", {"seconds": 30, "why": "the planner gave no goal"}

    async def carry_out(self, tool: str, args: dict[str, Any], chosen: bool = False) -> PlanStep:
        """Carry out a goal: the planner's own choice (`chosen`), or one code sends (a huddle's plan, over budget)."""
        why = str(args.get("why", ""))
        if self.on_goal:
            await self.on_goal(self.goal, describe_goal(tool, args), why)
        self.save_state(current=describe_goal(tool, args))
        s = self.session
        inn = None
        if tool == "inn" and self.table is not None and (inn := await s.find_inn()):
            # Which inn, for the others to walk to with the leader.
            args = {**args, "place": inn["name"], "x": inn["x"], "y": inn["y"]}
        if tool == "recover_corpse" and self.party is not None and self.corpse_at:
            # Where, for the others to come back with the leader (party.Follower.follow).
            args = {**args, "x": self.corpse_at[0], "y": self.corpse_at[1]}
        if tool == "hunt":
            try:
                minutes = max(3.0, min(20.0, float(args.get("minutes") or 10)))
            except (TypeError, ValueError):
                minutes = 10.0
            if self.ends_at is not None:
                # Not past the session's own end: the run loop looks at the clock between goals, and a
                # ten-minute hunt begun in a nine-minute run's last second doubled the run (live, a
                # huddle's tamer hunted on for 9.4 minutes after the other four had finished). Worked out
                # before the goal is posted, so that a party's followers get the same number.
                minutes = min(minutes, max(0.5, (self.ends_at - time.monotonic()) / 60))
            args = {**args, "minutes": int(minutes) if float(minutes).is_integer() else round(minutes, 1)}
        self.log({"type": "goal", "t": time.time(), "tool": tool, "args": args})
        if tool == "hunt" and self.huddle is not None and (board := self.huddle.board()) is not None:
            # A hunt under a plan, of its own choosing or the plan's: it isn't sent on the plan's hunt again after it.
            self.steered.add(board["rev"])
        rev = 0
        if self.party is not None and self.huddle is None and tool in GOALS:
            # The party follows: travel, hunt and rest with the leader; restock (or the leader's own errands)
            # each buys its own (party.Follower).
            rev = self.party.post(tool, args, why)
            self.log({"type": "party_goal", "t": time.time(), "rev": rev, "tool": tool})
        before = {a: dict(v) for a, v in getattr(s, "dangers", {}).items()}
        self.on_errand = tool in OWN_BUSINESS
        try:
            match tool:
                case "travel_to":
                    place = str(args.get("place", ""))
                    xy = place.split(",")
                    # A party's trip is made together (session.travel_to: walked, where the party walks).
                    together = {"together": True} if self.party is not None and self.huddle is None else {}
                    if (hunt := self.into_the_hunt(place)) is not None:
                        # Into the hunt the party's plan sends this member to: as far as the party's rally point,
                        # with those gathered there (cuo-cym.14).
                        r = await s.to_the_rally(*hunt)
                    elif len(xy) == 2 and all(v.strip().lstrip("-").isdigit() for v in xy):
                        r = await s.defended(s.travel_to(x=int(xy[0]), y=int(xy[1]), **together))
                    else:
                        r = await s.defended(s.travel_to(place, **together))
                case "hunt" if (held := await self.held_back(str(args.get("area", "")))):
                    r = Result(False, held)
                case "hunt":
                    link = getattr(getattr(s, "pcfg", None), "link", None) if self.party is not None else None
                    seen = len(link.story.dead_seen) if link is not None else 0
                    # A party's hunt is formed up for at the edge, under the goal's own number (rally.py).
                    forming = {}
                    if getattr(s, "rally", None) is not None and (rev or self.huddle is not None):
                        # With a planner each, the rally is for the place, by the name the store knows it under:
                        # keyed by each planner's own words for it, two of them held two rallies for one hunt.
                        forming = {"rally_key": f"goal:{rev}" if rev else "area"}
                    r = await s.hunt(str(args.get("area", "")), float(args["minutes"]), **forming)
                    if link is not None and (dead := link.story.dead_seen[seen:]):
                        # What the party killed, by the corpses its leader saw: its own hunt says "0 kills" when
                        # the others did the killing, and a leader told only that hunted an emptied graveyard
                        # five times over instead of calling the hunt done.
                        won = "; nothing hostile is left here: this hunt is won" if "nothing hostile in sight" in r.summary else ""
                        if won:
                            me = (await s.snap()).get("player") or {}
                            self.won = ([*(self.won[0] if self.won else []), *dead], time.monotonic(),
                                        me.get("x", 0), me.get("y", 0))
                        r = Result(r.ok, f"{r.summary}; dead here now, killed by you and your party: {', '.join(dead)}{won}",
                                   r.data)
                case "bank":
                    r = await s.defended(s.bank(str(args.get("deposit", "gold,loot")), str(args.get("withdraw", "")),
                                                **({"place": str(args["place"])} if args.get("place") else {})))
                case "buy":
                    r = await s.defended(s.buy(str(args.get("item", "")), int(args.get("count", 1)),
                                               args.get("vendor_kind") or None))
                case "sell":
                    r = await s.defended(s.sell(str(args.get("items", "loot")), str(args.get("vendor_kind", "weaponsmith"))))
                case "rest":
                    r = await s.defended(s.rest(max(5.0, min(300.0, float(args.get("seconds", 30))))))
                case "restock":
                    r = await s.defended(s.restock())
                case "inn":
                    if self.table is None or inn is None:
                        r = Result(False, "no inn known near here" if self.table is not None else "no party to sit with")
                    else:
                        from . import table as tables
                        last = next((h for h in reversed(self.history) if h.tool == "hunt"), None)
                        self.table.area = str(last.args.get("area")) if last else None
                        r = await s.defended(tables.evening(s, self.table, inn))
                        if r.ok:
                            self.won = None
                        if r.ok and self.heart:
                            self.heart.note("an evening at the inn with the party, talking the hunt over")
                case "evening":
                    hamlet = getattr(s, "hamlet", None)
                    if self.table is None or hamlet is None or not hamlet.gather:
                        r = Result(False, "no evening gathering here")
                    else:
                        from . import hamlet as hamlets
                        r = await s.defended(hamlets.gathering(s, self.table, hamlet))
                        if r.ok and self.heart:
                            self.heart.note("an evening with the neighbours, talking the day over")
                case "set_strategy":
                    r = await self.set_strategy(str(args.get("text") or ""))
                case "resurrect":
                    r = await s.resurrect()
                    self.resurrections += r.ok and r.summary != "already alive"
                    if r.ok and self.dead_since is not None:  # alive again: a later death is a new one
                        self.time_dead_s += time.monotonic() - self.dead_since
                        self.dead_since = None
                case "recover_corpse":
                    # With the party at the place the danger seen there is theirs to deal with, as it is for a
                    # follower (party.Follower.step). A leader refused its corpse rested ten minutes "to let
                    # the danger age" while the others stood by it, the wave long dead, and the corpse decayed.
                    held = self.party_at(*self.corpse_at[:2]) if self.corpse_at else []
                    # ...and a party beside its leader goes back with it (two or more of them, in a party it leads).
                    escort = self.escort(await s.snap()) if self.party is not None and not held else []
                    r = await s.defended(s.recover_corpse(
                        go_anyway=bool(args.get("go_anyway")) or bool(held) or len(escort) >= 2))
                    self.corpses_recovered += r.ok
                    if r.ok or "gone" in r.summary:
                        self.corpse_at = None
                case "set_machine":
                    if args.get("clear"):
                        self.machine = None
                        r = Result(True, "back to the default way of fighting")
                    elif not args.get("machine"):
                        r = Result(False, "plan not used: the call came without a machine (it may have been cut "
                                          "off); call set_machine again with the whole machine")
                    else:
                        try:
                            self.machine = machines.Runner(machines.parse(args["machine"]))
                            r = Result(True, "plan set: " + self.machine.machine.describe())
                        except machines.MachineError as e:
                            r = Result(False, f"plan not used: {e}")
                    s.machine = self.machine
                case "finish" if chosen and not self.goal and (hamlet := getattr(s, "hamlet", None)) is not None:
                    # A resident has no player's goal to be done with, and a finish would only end its days in the
                    # hamlet early: the watchman, sure every way out of Cove was too dangerous, finished half an hour
                    # into an hour (cuo-m3n.3).
                    r = Result(False, f"not finishing: you live in {hamlet.name} and your days there go on; choose "
                                      "what to do next (rest at home, your work, the neighbours)")
                case _:
                    self.finished = str(args.get("summary", "finished"))
                    r = Result(True, self.finished)
        except Exception as e:  # a goal that crashed is reported to the planner like a failure
            r = Result(False, f"error: {type(e).__name__}: {e}"[:300])
        self.on_errand = False
        if hasattr(s, "danger_words") and (seen := s.danger_words(before)):
            r = Result(r.ok, f"{r.summary}; stronger creatures seen: {seen}", r.data)
            s.record_dangers(seen)
        step = PlanStep(tool, args, r.to_tool())
        self.history.append(step)
        self.log({"type": "goal_result", "t": time.time(), "tool": tool, "result": step.result})
        for note in memories.from_goal(tool, args, step.result):
            self.keep(note)
        if self.long is not None:
            self.long_events(self.long.note_result(tool, args, step.result))
        self.save_state()
        return step

    def into_the_hunt(self, place: str) -> tuple[str, int, int, int] | None:
        """The hunt a trip of this member's own goes into, when it is one the party's plan sends it to and the
        party forms up (a planner each, form_up): the area's name, centre and spread. A trip ends there when it
        ends within the edge the party forms up at (the place a planner names for an area can be further from
        its spawn's centre than the spawn's spread: the Britain Graveyard by 17 tiles). Live (2026-10-14) a mage
        whose planner chose to walk to the graveyard rather than hunt it walked through the rally point to the
        spawn's centre, the warrior who held the rally went in after her alone, and two died."""
        s = self.session
        if self.huddle is None or self.party is None or getattr(s, "rally", None) is None:
            return None
        from .rally import EDGE_TILES
        area = self.huddle.hunt_area()
        if area is None or self.party.me not in (self.huddle.going(area) or []):
            return None
        xy = place.split(",")
        to = s.resolve(None, int(xy[0]), int(xy[1])) if len(xy) == 2 and all(
            v.strip().lstrip("-").isdigit() for v in xy) else s.resolve(place, None, None)
        spot = s.hunt_spot(area)
        if to is None or spot is None or worlds.tiles(to[0], to[1], spot[0], spot[1]) > spot[3] + EDGE_TILES:
            return None
        return spot[2], spot[0], spot[1], spot[3]

    HELD_TILES = 15

    def party_at(self, x: int, y: int) -> list[str]:
        """The companions alive within HELD_TILES of a place, by their own reports of the last half minute."""
        if self.party is None:
            return []
        return [n for n, st in self.party.statuses().items()
                if n != self.party.me and not st.get("dead") and st["age_s"] < 30
                and max(abs(st["x"] - x), abs(st["y"] - y)) <= self.HELD_TILES]

    def escort(self, snap: dict[str, Any]) -> list[str]:
        """The companions beside the leader of a party who came through the fight it died in: none of them
        has died since it did. They go back for its corpse with it (party.Follower.follow). A party that was
        wiped has no such escort, and waits as anyone would: sent straight back, all five died there again."""
        if self.party is None or self.huddle is not None or self.died_at_wall is None:
            return []
        me = snap.get("player") or {}
        stats = self.party.statuses()
        if self.corpse_at and any(self.driven_off(st, *self.corpse_at[:2]) for st in stats.values()):
            # Some of them lived by leaving: the place wasn't won. A leader that went back with three who had
            # been driven off met the same wave and died again.
            return []
        return [n for n in self.party_at(me.get("x", 0), me.get("y", 0))
                if (stats[n].get("last_death") or 0) < self.died_at_wall - 5]

    DRIVEN_OFF_S = 15 * 60

    def driven_off(self, st: dict[str, Any], x: int, y: int) -> bool:
        """A member's report says it left creatures behind near (x, y) since this character died there."""
        left = st.get("left") or {}
        return bool(left) and time.time() - left.get("t", 0) < self.DRIVEN_OFF_S \
            and left.get("t", 0) > (self.died_at_wall or 0) - 120 \
            and max(abs(left.get("x", 0) - x), abs(left.get("y", 0) - y)) <= 40

    async def set_strategy(self, text: str) -> Result:
        """The planner's lines in the strategy's planner block; the player's lines stay as they are."""
        since = time.monotonic() - self.strategy_set_at
        if since < STRATEGY_EVERY_S:
            return Result(False, f"your lines were changed {since / 60:.0f} min ago: give them at least "
                                 f"{STRATEGY_EVERY_S // 60} min in a hunt before changing them again")
        current = (await self.session.rpc.call("strategy")).get("strategy") or ""
        had = PLANNER_HEAD in current
        if not text.strip() and not had:
            return Result(False, "no text given and no lines of yours to take out: the strategy is unchanged")
        new = with_planner_lines(current, text)
        await self.session.rpc.call("strategy", text=new)
        self.strategy_set_at = time.monotonic()
        self.strategy_changes += 1
        player = new.split(PLANNER_HEAD, 1)[0].strip()
        what = "your lines taken out" if not text.strip() else "your lines replaced" if had else "your lines added"
        return Result(True, f"{what}; the player's {len(player.splitlines()) if player else 0} lines kept")

    def know(self, snap: dict[str, Any]) -> None:
        """Whose memories these are: the character in game, once it is known."""
        if self.memory is not None and not self.memory.character and snap.get("in_game"):
            self.memory.character = (snap.get("player") or {}).get("name") or None
        if self.long is not None and self.memory is not None:
            self.long.character = self.memory.character
        elif self.long is not None and not self.long.character and snap.get("in_game"):
            self.long.character = (snap.get("player") or {}).get("name") or None  # memory off: the name in game
        if not self.resumed and self.memory is not None and self.memory.character:
            self.resumed = True
            self.resume()

    def resume(self) -> None:
        """Carry on from a saved state for this character and goal (heartbeat.py): its last steps, the
        summary and the focus, with a step saying the brain restarted."""
        state = heartbeat.load(self.memory.world, self.memory.character, self.goal)
        if not state or self.history:
            return
        self.history = [PlanStep(h["tool"], h.get("args") or {}, h.get("result") or {}, h.get("t", time.time()))
                        for h in state["history"]]
        self.restored = len(self.history) + 1  # those and the restart step aren't this session's own
        self.folder.summary = state["summary"]
        self.folder.folded = 0
        if self.heart:
            self.heart.focus, self.heart.focus_t = state["focus"], state["focus_t"]
            self.heart.note("the brain restarted")
        during = f", during: {state['current']}" if state["current"] else ""
        self.history.append(PlanStep("restart", {}, {"ok": True, "result": (
            f"the brain restarted {state['age_min']:.0f} min after its last step{during}; it carries on from "
            f"wherever the character is now")}))
        self.log({"type": "resumed", "t": time.time(), "steps": len(state["history"]), "age_min": state["age_min"],
                  "current": state["current"], "summary_words": len(state["summary"].split())})

    def save_state(self, current: str = "") -> None:
        if self.memory is None or not self.memory.character or self.heart is None:
            return
        tail = self.history[self.folder.folded:]
        heartbeat.save(self.memory.world, self.memory.character, self.goal, self.folder.summary, 0,
                       [{"tool": s.tool, "args": s.args, "result": s.result, "t": s.t} for s in tail],
                       self.heart.focus, self.heart.focus_t, current)

    async def track_long_goals(self, snap: dict[str, Any]) -> None:
        """Skill progress from the snapshot, and the panel's view of the long goals (with what the
        player asked there)."""
        if self.long is None or not self.long.character:
            return
        self.long_events(self.long.observe(snap))
        await self.long.sync(self.session.rpc, self.log)

    def long_events(self, events: list[dict[str, Any]]) -> None:
        for e in events:
            if self.heart:
                self.heart.note(f"a long goal {'done' if e.get('done') else 'milestone'}: {e['text']}")
            self.log({"type": "long_goal", "t": time.time(), "by": "code", **e})
            self.keep(memories.Note(longgoals.milestone_note(e), tags=("long goal",), source="goal",
                                    importance=4 if e.get("done") else 3))

    def keep(self, note: memories.Note) -> int | None:
        """Store a memory code decided on, and log it. Nothing before the character is known."""
        if self.memory is None or not self.memory.character:
            return None
        mid = self.memory.keep(note)
        self.log({"type": "memory", "t": time.time(), "id": mid, "kind": note.kind, "source": note.source,
                  "importance": note.importance, "area": note.area, "text": note.text})
        return mid

    def died(self, snap: dict[str, Any]) -> None:
        """The character is dead: remember where, what was about, and the goal, once per death. The
        session goes on: the planner resurrects it and takes back its corpse (cuo-4b3)."""
        if self.dead_since is not None:
            return
        self.dead_since = time.monotonic()
        self.died_at_wall = time.time()
        self.deaths += 1
        if self.heart:
            self.heart.note("a death")
        self.know(snap)
        p0 = snap.get("player") or {}
        death = p0.get("last_death") or {"x": p0.get("x"), "y": p0.get("y")}
        self.corpse_at = (death["x"], death["y"], time.monotonic() - (death.get("ago_s") or 0))
        p = snap.get("player") or {}
        here = self.session.world.region_at(p.get("x", 0), p.get("y", 0)) if isinstance(
            getattr(self.session, "world", None), worlds.World) else None
        last = self.history[-1] if self.history else None
        self.keep(memories.death(snap, here["name"] if here else f"open country near {p.get('x')},{p.get('y')}",
                                 describe_goal(last.tool, last.args) if last else ""))

    async def run(self, hours: float = 1.0, stop: Any = None) -> dict[str, Any]:
        began = time.monotonic()
        self.ends_at = began + hours * 3600
        try:
            while not self.finished and time.monotonic() - began < hours * 3600:
                if stop is not None and stop.is_set():
                    break
                await self.step()
        finally:
            await self.close()
        return self.summary(time.monotonic() - began)

    async def close(self) -> None:
        """The session is over: fold the rest into the summary and keep it as a memory."""
        try:
            await self.folder.close(self.history, self.goal)
        except Exception as e:  # never lose the session's summary record over this
            self.log({"type": "fold", "t": time.time(), "error": f"closing: {type(e).__name__}: {e}"[:300]})

    def summary(self, seconds: float | None = None) -> dict[str, Any]:
        seconds = time.monotonic() - self.started if seconds is None else seconds
        hours = max(seconds / 3600, 1e-9)
        own = self.history[self.restored:]
        hunts = [s for s in own if s.tool == "hunt"]
        return {
            "minutes": round(seconds / 60, 1),
            "goals": sum(1 for s in own if s.tool in GOALS),
            "goals_by_kind": {k: sum(1 for s in own if s.tool == k) for k in sorted(GOALS)},
            "kills": sum(s.result.get("kills", 0) for s in hunts),
            "deaths": max(self.deaths, sum(s.result.get("deaths", 0) for s in hunts)),
            "resurrections": self.resurrections,
            "corpses_recovered": self.corpses_recovered,
            "minutes_dead": round((self.time_dead_s + (time.monotonic() - self.dead_since if self.dead_since else 0)) / 60, 1),
            "failed_goals": sum(1 for s in own if not s.result.get("ok")),
            "finished": self.finished,
            "planner_calls": self.usage.calls,
            "planner_cost_usd": round(self.usage.cost or 0.0, 4),
            "planner_cost_per_hour": round((self.usage.cost or 0.0) / hours, 4),
            "reranked_queries": self.ranker.calls,
            "rerank_cost_usd": round(self.ranker.cost, 5),
            "strategy_changes": self.strategy_changes,
            "escalations": len(self.escalations),
            "escalation_reasons": {r: self.escalations.count(r) for r in dict.fromkeys(self.escalations)},
            "escalation_cost_usd": round(self.escalation_usage.cost or 0.0, 4),
            "memories_written": self.memory.written if self.memory else None,
            "memories_shown": self.picker.shown if self.picker else None,
            "memory_calls": self.picker.calls if self.picker else None,
            "memory_cost_usd": round(self.picker.cost, 5) if self.picker else None,
            **self.folder.to_log(),
            **(self.heart.to_log() if self.heart else {}),
            **(self.ears.to_log() if self.ears else {}),
            **(self.huddle.to_log() if self.huddle else {}),
        }


async def replay(records: list[dict[str, Any]], model: str, chat_fn: llm.ChatFn = llm.chat,
                 machines_allowed: bool = True) -> dict[str, Any]:
    """The planner moments of a log (records with the `messages` they were sent) put to another
    model: how often it picks the same tool, how often it answers with a usable tool call at all,
    and what that costs and how long it takes. Offline: the game isn't needed."""
    tools = goal_tools() + ([machine_tool()] if machines_allowed else []) + worlds.tool_schemas() \
        + [memories.remember_tool(), memories.recall_tool(), longgoals.tool()]
    known = {t["function"]["name"] for t in tools}
    moments = [r for r in records if r.get("type") == "planner" and r.get("messages") and r.get("calls")]
    same = usable = goals_same = goals = 0
    pairs: dict[str, int] = {}
    latencies: list[float] = []
    mark = costs.ledger.mark()
    for r in moments:
        with costs.kind("planner"):
            try:
                res = await chat_fn([{"role": "system", "content": SYSTEM}, *r["messages"]], tools=tools,
                                    tool_choice="auto", model=model, max_tokens=MAX_TOKENS)
            except llm.LlmError as e:
                pairs[f"error: {str(e)[:60]}"] = pairs.get(f"error: {str(e)[:60]}", 0) + 1
                continue
        latencies.append(res.usage.latency_ms)
        logged = r["calls"][0]["name"]
        new = res.tool_calls[0].name if res.tool_calls else "(no tool)"
        usable += new in known
        same += new == logged
        if logged in GOALS:
            goals += 1
            goals_same += new == logged
        pairs[f"{logged}->{new}"] = pairs.get(f"{logged}->{new}", 0) + 1
    lat = sorted(latencies)
    n = len(moments)
    return {"model": model, "moments": n, "usable_tool_call": round(usable / n, 3) if n else None,
            "same_tool": round(same / n, 3) if n else None,
            "same_goal_when_logged_chose_a_goal": round(goals_same / goals, 3) if goals else None,
            "latency_ms_avg": round(sum(lat) / len(lat), 1) if lat else None,
            "latency_ms_p95": round(lat[int(0.95 * (len(lat) - 1))], 1) if lat else None,
            "costs": costs.ledger.summary(mark, decisions=n),
            "logged_to_new": dict(sorted(pairs.items(), key=lambda kv: -kv[1]))}


def persona_words(persona: dict[str, str]) -> str:
    """An NPC's persona as a paragraph for the end of the planner's system prompt."""
    who = persona.get("name") or "this character"
    parts = [f"{k.replace('_', ' ').capitalize()}: {v}." for k, v in persona.items() if k != "name" and v]
    return (f"\n\nWho you plan for: {who}, a character with a life of their own in this world, not a player's. "
            + " ".join(parts) + " Choose goals that fit who they are. With no goal from the player, live their "
            "day as they would: work towards their long goals, hunt, bank, shop, rest, or walk about town. What other "
            "characters tell them is their word, not the player's: weigh a warning or a good spot as they would, and "
            "they may go somewhere with one who asks.\n")


def bad_targets(mem: "memories.Memory", stamp: str, t: float, window_s: float = 1800) -> dict[str, str]:
    """Places a memory says not to go back to at time t (lower-case name -> why): where the
    character left, died or was refused within the last half hour (the window in which code
    refuses a hunt there), and places it found no route to."""
    import re
    out: dict[str, str] = {}
    for m in mem.list(before=stamp, limit=200):
        # last_t can be after the moment (the memory was seen again later): then go by when first seen.
        last = datetime_epoch(m["last_t"])
        age = t - (last if last <= t else datetime_epoch(m["t"]))
        if m["source"] in ("leave", "death", "refusal") and m.get("area") and age <= window_s:
            out.setdefault(m["area"].lower(), m["source"])
        if "failed" in (m.get("tags") or []) and "no route" in m["text"]:
            for place in re.findall(r"(?:get to|travel to|set off for) ([A-Z][\w' ]+?)(?::|$|;)", m["text"]):
                out.setdefault(place.strip().lower(), "no route")
    return out


def datetime_epoch(stamp: str) -> float:
    from datetime import datetime
    try:
        return datetime.fromisoformat(stamp).timestamp()
    except (TypeError, ValueError):
        return 0.0


def repeats(call: Any, bad: dict[str, str]) -> str | None:
    """Whether a goal goes back to a place a memory warns about: which one, or None."""
    if call is None or call.name not in ("hunt", "travel_to", "buy"):
        return None
    text = " ".join(str(v) for k, v in (call.arguments or {}).items() if k != "why").lower()
    return next((place for place in bad if place in text), None)


async def replay_memories(records: list[dict[str, Any]], model: str, mem: "memories.Memory", judge: Any = None,
                          chat_fn: llm.ChatFn = llm.chat, machines_allowed: bool = True) -> dict[str, Any]:
    """Offline: each logged planner moment asked of one model twice, as logged and with the
    memories Jev picks from what the character remembered at that moment (memories written
    before it). How often the answer changes, how often each goes back to a place a memory
    warns about (left, died or refused there in the hour before, or failed to reach), how many
    tokens the memories add, and what picking them cost."""
    tools = goal_tools() + ([machine_tool()] if machines_allowed else []) + worlds.tool_schemas() \
        + [memories.remember_tool(), memories.recall_tool(), longgoals.tool()]
    picker = memories.Picker(mem, judge)
    moments = [r for r in records if r.get("type") == "planner" and r.get("messages") and r.get("calls")
               and len(r["messages"]) == 1]  # the first call of a step: before any world query
    mark = costs.ledger.mark()
    rows = []
    for r in moments:
        stamp = memories.stamp_of(r["t"])
        user = r["messages"][0]["content"]
        head, sep, tail = user.partition("The character now: ")
        try:
            situation = json.loads(tail.split("\n\n", 1)[0]) if sep else {}
        except ValueError:
            situation = {}
        goal = head.split("\n", 1)[0].removeprefix("The player's goal: ")
        steps = [ln.split(". ", 1)[-1] for ln in head.splitlines() if ln[:1].isdigit()]
        chosen = await picker.pick(situation, goal, steps[-3:], before=stamp)
        from datetime import datetime, timezone
        section = memories.section(chosen, at=datetime.fromtimestamp(r["t"], timezone.utc))
        with_mem = head + (section + "\n\n" if section else "") + sep + tail
        answers = {}
        for label, content in (("without", user), ("with", with_mem)):
            with costs.kind("planner"):
                try:
                    res = await chat_fn([{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}],
                                        tools=tools, tool_choice="auto", model=model, max_tokens=MAX_TOKENS)
                except llm.LlmError as e:
                    answers[label] = (None, 0, f"error: {str(e)[:80]}")
                    continue
            c = res.tool_calls[0] if res.tool_calls else None
            answers[label] = (c, res.usage.prompt_tokens, None)
        bad = bad_targets(mem, stamp, r["t"])
        row = {"t": r["t"], "logged": r["calls"][0]["name"], "shown": len(chosen["picked"]) + len(chosen["orders"]),
               "bad_places": sorted(bad)}
        for label, (c, tokens, err) in answers.items():
            row[label] = err or (f"{c.name}({', '.join(f'{k}={v!r}' for k, v in (c.arguments or {}).items() if k != 'why')})"
                                 if c else "(no tool)")
            row[f"{label}_tokens"] = tokens
            row[f"{label}_repeats"] = repeats(c, bad)
        rows.append(row)
    n = len(rows)

    def share(pred) -> float | None:
        return round(sum(1 for x in rows if pred(x)) / n, 3) if n else None

    return {"model": model, "character": mem.character, "moments": n,
            "with_memories_shown": share(lambda x: x["shown"] > 0),
            "same_tool": share(lambda x: x["without"].split("(")[0] == x["with"].split("(")[0]),
            "same_call": share(lambda x: x["without"] == x["with"]),
            "repeats_without": sum(1 for x in rows if x["without_repeats"]),
            "repeats_with": sum(1 for x in rows if x["with_repeats"]),
            "tokens_added_avg": round(sum(x["with_tokens"] - x["without_tokens"] for x in rows) / n) if n else None,
            "memory_picks": picker.calls, "memory_cost_usd": round(picker.cost, 5),
            "costs": costs.ledger.summary(mark, decisions=n), "rows": rows}


def target_of(step: PlanStep) -> str:
    """What a goal was about, to tell the same goal failing again: its area, place, item or vendor."""
    a = step.args
    return str(a.get("area") or a.get("place") or a.get("item") or a.get("vendor_kind") or "").lower()


def describe_goal(tool: str, args: dict[str, Any]) -> str:
    match tool:
        case "travel_to":
            return f"travelling to {args.get('place')}"
        case "hunt":
            return f"hunting at {args.get('area')} for up to {args.get('minutes')} min"
        case "bank":
            parts = [p for p in (f"deposit {args.get('deposit')}" if args.get("deposit") else "",
                                 f"withdraw {args.get('withdraw')}" if args.get("withdraw") else "") if p]
            return "banking: " + (", ".join(parts) or "looking") + (f" at {args['place']}" if args.get("place") else "")
        case "buy":
            return f"buying {args.get('count')} {args.get('item')}"
        case "sell":
            return f"selling {args.get('items', 'loot')}"
        case "rest":
            return f"resting {args.get('seconds')} s"
        case "restock":
            return "the party restocks: each buys what it is short of"
        case "inn":
            return f"R&R: the party goes to {args.get('place') or 'the inn'} to talk the hunt over"
        case "evening":
            return "the evening: to the neighbours' gathering, to talk the day over"
        case "set_strategy":
            return "changing the strategy"
        case "resurrect":
            return "walking to a healer to be resurrected"
        case "recover_corpse":
            return "going back for its corpse"
        case "set_machine":
            m = args.get("machine")
            return "dropping its fight plan" if args.get("clear") else \
                f"new fight plan: {m.get('name', 'a plan') if isinstance(m, dict) else 'a plan'}"
    return "finishing"
