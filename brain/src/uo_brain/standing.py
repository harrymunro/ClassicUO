"""A companion's settings, changed by the leader's word (cuo-cym.18).

A companion of a character a person plays (companion.py) took three orders from what the leader said, and
everything else about how it plays was fixed when the fleet started. Asked to loot, three ways ("feel free to
loot", "loot", "Please go ahead and loot"), nobody looted and nobody said a word: no reading had an answer for
it. Now each setting a companion has can be changed by what the leader says, and holds until the leader
changes it again:

| setting    | values                                              | what it changes                                   |
|------------|-----------------------------------------------------|---------------------------------------------------|
| looting    | nothing (as it starts), valuables, everything       | what it takes from corpses                        |
| fleeing    | allowed, never                                      | whether it runs from a fight that goes badly      |
| aggression | cautious, balanced, relentless                      | how early it pulls back                           |
| target     | current, weakest, closest, strongest (first)        | which creature it goes for                        |
| opening_spell, main_spell | an attack spell, or its own choice   | a caster's spells                                 |
| helping    | on, off                                             | healing and bandaging the others                  |
| raising    | on, off                                             | bringing back one who fell, where it fell         |
| callouts   | on, off                                             | calling out in a fight                            |
| talk       | silent, answers, chatty                             | how much it talks                                 |
| distance   | close, normal, loose                                | how near the leader it keeps                      |
| reach      | defend, near, wide                                  | how far it goes for a fight                       |

Two steps, both system one's (kind `order`), each companion for itself with the party's names in the state:

1. Beside the order question, on every line the leader says: which of this companion's settings does the line
   speak to, if any? One choice with the settings as its options (`which_question`), so a line of talk costs one
   more answer and no more.
2. For each setting it gives WHICH_CUT or more (a line can speak to several: "loot what you like, and no
   running"), that setting's own question (`questions`): what does the line make of it, or does it leave it as
   it is? "Wystan, you loot; the rest of you keep off" sets Wystan's looting one way and the others' the other.

Code turns the answers into the settings: those of the fight (`Standing.apply`, laid over the fight loop's
configuration at every decision) and those kept elsewhere (callouts, talk, raising, how close it keeps), which
the companion switches itself. Every reading and every change is logged, and the run's summary lists the
changes.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .judge import Answers
from .policy import PolicyConfig
from .spells import ATTACK_SPELLS

UNCHANGED = "unchanged"
# A line speaks to a setting when system one gives that setting at least this among them all (the options share
# one probability, so a line that changes two gives each about half), and the setting is then asked about. Both
# cuts were chosen on the first set of lines in scripts/settings_check.py.
WHICH_CUT = 0.25
# ...and the line changes it when the setting's own question gives a value at least this.
SET_CUT = 0.6
AGGRESSION = {"cautious": 0.2, "balanced": 0.5, "relentless": 0.9}   # strategy.Knobs.aggression
FOLLOW_TILES = {"close": 1, "normal": 2, "loose": 5}                 # how near the leader it keeps
REACH = {"defend": (1, 2), "near": (4, 6), "wide": (12, 12)}         # tiles from the leader, and from itself


@dataclass(frozen=True)
class Setting:
    name: str
    about: str                  # what the question is about, after "about"
    values: dict[str, str]      # value -> what the line says, for system one ({me}, {leader})
    does: dict[str, str]        # value -> what the companion then does, after its name
    plain: dict[str, str]       # value -> its word on taking it, when no line of its own can be written
    default: str
    casters: bool = False       # asked only of one who casts


def _spell(tell: str, does: str, own_does: str, word: str, own_word: str) -> tuple[dict[str, str], ...]:
    """A caster's spell setting: every attack spell by name, and "own" for no set spell."""
    return ({**{name: "{me} is to " + tell.format(spell=name) + "." for name in ATTACK_SPELLS},
             "own": "{me} is to choose the spell for themselves again: no set spell."},
            {**{name: does.format(spell=name) for name in ATTACK_SPELLS}, "own": own_does},
            {**{name: word.format(spell=name) for name in ATTACK_SPELLS}, "own": own_word})


_OPEN = _spell("open each fight with {spell}", "opens each fight with {spell}", "chooses the spell to open a fight with",
               "Opening with {spell}.", "My own choice of opener, then.")
_MAIN = _spell("keep casting {spell} once a fight is under way", "keeps casting {spell} once a fight is under way",
               "chooses its spells for itself once a fight is under way", "Casting {spell}.", "My own choice of spells, then.")

SETTINGS: dict[str, Setting] = {s.name: s for s in (
    Setting("looting", "taking things from the corpses of what the party kills",
            {"valuables": "{me} may or should loot: take what is useful or worth having. Leave to loot, or the bare "
                          "word, counts ('go ahead and loot', 'help yourself', 'loot').",
             "everything": "{me} may or should take everything that can be carried, not only what is worth having.",
             "nothing": "{me} is to leave the corpses alone: no looting, hands off, the loot is {leader}'s or "
                        "someone else's. Told to stop looting, or to leave what is left, counts ('stop looting', "
                        "'that's enough', 'leave the rest')."},
            {"valuables": "takes what is worth having from the corpses", "everything": "takes everything it can carry "
             "from the corpses", "nothing": "leaves the corpses alone"},
            {"valuables": "I'll loot what's worth taking.", "everything": "I'll take everything I can carry.",
             "nothing": "Leaving the loot alone."}, "nothing"),
    Setting("fleeing", "running from a fight that is going badly",
            {"allowed": "{me} may run or fall back when a fight goes badly: save yourself, don't die for it.",
             "never": "{me} is never to run: stand and fight whatever comes, no retreat."},
            {"allowed": "runs from a fight that is going badly", "never": "never runs from a fight"},
            {"allowed": "I'll run when it goes badly.", "never": "No running. Understood."}, "allowed"),
    Setting("aggression", "how hard to press a fight",
            {"cautious": "Play it safe: pull back early, take no risks.",
             "balanced": "Fight sensibly, neither reckless nor timid: back to normal.",
             "relentless": "Press hard: all out, keep attacking, hold on late."},
            {"cautious": "pulls back early when a fight turns", "balanced": "presses a fight as far as is sensible",
             "relentless": "presses every fight hard and holds on late"},
            {"cautious": "Playing it safe.", "balanced": "Back to normal, then.", "relentless": "All out. Got it."},
            "balanced"),
    Setting("target", "which creature to go for first in a fight",
            {"current_first": "Finish the creature already being fought before switching to another.",
             "weakest_first": "Go for the weakest or most wounded creature first.",
             "closest_first": "Go for whatever is closest.",
             "strongest_first": "Take on the most dangerous creature first."},
            {"current_first": "finishes the creature it is on before switching", "weakest_first": "goes for the weakest "
             "creature first", "closest_first": "goes for the closest creature first",
             "strongest_first": "goes for the most dangerous creature first"},
            {"current_first": "One at a time, then.", "weakest_first": "Weakest first.", "closest_first": "Closest first.",
             "strongest_first": "The biggest first."}, "current_first"),
    Setting("opening_spell", "the spell to open each fight with", *_OPEN, "own", casters=True),
    Setting("main_spell", "the spell to keep casting once a fight is under way (after any opening spell)", *_MAIN, "own",
            casters=True),
    Setting("helping", "healing and bandaging the others of the party",
            {"on": "{me} is to heal or bandage companions, and {leader}, when they are hurt.",
             "off": "{me} is not to heal or bandage the others: keep it for yourself, leave the healing to someone else."},
            {"on": "heals and bandages the others when they are hurt", "off": "leaves the others' wounds alone"},
            {"on": "I'll patch the others up.", "off": "No healing the others, then."}, "on"),
    Setting("raising", "bringing back a companion who has fallen dead",
            {"on": "{me} is to bring a fallen companion back to life where they fell.",
             "off": "{me} is to leave the dead alone: they find a healer themselves."},
            {"on": "tries to bring back a companion who falls, where they fell", "off": "leaves the fallen to find a healer"},
            {"on": "I'll raise whoever falls.", "off": "The dead can walk to a healer."}, "off"),
    Setting("callouts", "calling out in a fight (what is on whom, who is hurt, falling back)",
            {"on": "{me} is to call out in fights.",
             "off": "{me} is to stop calling out in fights: no shouting."},
            {"on": "calls out in fights", "off": "says nothing in fights"},
            {"on": "I'll call it out.", "off": "No shouting in fights."}, "on"),
    Setting("talk", "how much to talk",
            {"silent": "Be quiet: no talking, only a word on an order.",
             "answers": "Speak when spoken to, and no idle chatter otherwise.",
             "chatty": "Talk freely: chatter, small talk and banter are welcome (again)."},
            {"silent": "says nothing but a word on an order", "answers": "answers when spoken to and doesn't chatter",
             "chatty": "talks freely, small talk and all"},
            {"silent": "Not another word.", "answers": "I'll speak when spoken to.", "chatty": "Talking again, then."},
            "answers"),
    Setting("distance", "how close to keep to {leader} while following",
            {"close": "Stay tight: right at {leader}'s side.",
             "normal": "The usual couple of steps behind.",
             "loose": "Give {leader} room: hang back a few steps, don't crowd."},
            {"close": "keeps right at {leader}'s side", "normal": "keeps a couple of steps from {leader}",
             "loose": "hangs a few steps back from {leader}"},
            {"close": "Right at your side.", "normal": "The usual distance.", "loose": "Giving you room."}, "normal"),
    Setting("reach", "how far to go for a fight",
            {"defend": "Only fight what attacks {me} or is right on {leader}: don't go looking for more.",
             "near": "The usual: what is in a fighting mood close to {me} or to {leader}.",
             "wide": "Go for anything hostile in sight: clear the place."},
            {"defend": "fights only what attacks it or is right on {leader}", "near": "fights what is close to it or to "
             "{leader}", "wide": "goes for anything hostile it can see"},
            {"defend": "Only what comes at us.", "near": "The usual reach.", "wide": "I'll go for anything I see."}, "near"),
)}


NONE = "none"
# Which setting a line speaks to: what each is, for the question above...
WHICH = {
    "looting": "What {me} takes from the corpses of what the party kills: told to loot, given leave to, told to take "
               "everything, or told to keep off the loot.",
    "fleeing": "Whether {me} may run from a fight that goes badly, or is to stand whatever comes.",
    "aggression": "How hard {me} presses a fight in general: to play it safe, or to go all out.",
    "target": "Which creature {me} goes for first, as a rule.",
    "opening_spell": "The spell {me} opens each fight with.",
    "main_spell": "The spell {me} keeps casting once a fight is under way.",
    "helping": "Whether {me} heals and bandages the others of the party.",
    "raising": "Whether {me} brings a companion who has fallen dead back to life.",
    "callouts": "Whether {me} calls out in fights (who is hurt, what is on whom).",
    "talk": "How much {me} talks: to be quiet, to speak only when spoken to, or to talk freely again.",
    "distance": "How close {me} keeps to {leader} while following: tight at their side, or giving them room.",
    "reach": "How far {me} goes for a fight: only what attacks the party, or anything hostile in sight.",
}
# ...and how a person might put it.
EXAMPLES = {
    "looting": "('go ahead and loot', 'loot', 'help yourselves', 'hands off', 'leave the rest')",
    "fleeing": "('nobody runs', 'no retreat', 'run if it goes bad')",
    "aggression": "('play it safe', 'go all out')",
    "target": "('weakest first', 'biggest threat first', 'finish what you start')",
    "opening_spell": "('open with Explosion')",
    "main_spell": "('stick to Lightning')",
    "helping": "('heal each other', 'save your bandages', 'no healing')",
    "raising": "('get them back up if they drop', 'don't bother raising anyone')",
    "callouts": "('stop shouting in fights', 'call it out when you're hurt')",
    "talk": "('quiet, all of you', 'less chatter', 'you can talk again')",
    "distance": "('stay tight on me', 'give me some room', 'you're crowding me')",
    "reach": "('don't go chasing things', 'only what attacks us', 'kill everything you see')",
}


def which_question(me: str, leader: str, caster: bool = False) -> dict[str, Any]:
    """The question beside the order question: which of this companion's settings does the line speak to, if any?
    One choice with the settings as its options, and every setting it gives WHICH_CUT or more is then asked about
    by itself. A yes/no first and then a question for every setting was tried before: asked about each setting in
    turn the models found something in most lines for most of them ("Leave the rest." also switched helping and
    raising off; "Quiet, all of you." the callouts too), 39 changes nobody asked for in 156 readings."""
    criteria = {name: (text.format(me=me, leader=leader) + " " + EXAMPLES[name]) for name, text in WHICH.items()
                if caster or not SETTINGS[name].casters}
    criteria[NONE] = (f"None of them: the line tells {me} nothing about how to play. Talk, a joke, a question; a remark "
                      f"on what happened, on how things usually go, or on what {leader} or someone else does ('I never "
                      f"run, me', 'the weak ones always die first'); an order for this moment (come along, wait here, "
                      f"sit down, go on ahead to somewhere); a request for one thing now (heal me, kill that one, cover "
                      f"me); an instruction given to other companions by name and not to {me}; words for a shopkeeper, "
                      f"a banker or a pet.")
    return {
        "type": "choice",
        "instructions": {
            "role": f"You read what {leader}, who leads a party of adventurers in the game Ultima Online, has just said "
                    f"aloud (`line`), and decide whether it changes how {me}, one of the party's companions, plays from "
                    f"now on, until {leader} says otherwise. {leader} is played by a person and speaks as people do: "
                    f"short, loose, often joking. A line said to the whole party, to 'all of you', or to 'the rest of "
                    f"you' when another companion is the one named apart, is said to {me} too.",
            "question": f"With this line, which of {me}'s ways of playing is {leader} changing?"},
        "criteria": criteria}


class Standing:
    """What the leader's word has made of one companion's settings. A setting it hasn't spoken of is as the
    companion started with it (the fleet file, its strategy), and isn't touched."""

    def __init__(self, caster: bool = False, defaults: dict[str, str] | None = None):
        self.caster = caster
        self.defaults = {name: s.default for name, s in SETTINGS.items()} | (defaults or {})
        self.values: dict[str, str] = {}
        self.changes: list[dict[str, Any]] = []

    def asked(self) -> list[Setting]:
        return [s for s in SETTINGS.values() if self.caster or not s.casters]

    def now(self, name: str) -> str:
        return self.values.get(name, self.defaults[name])

    def words(self, name: str, me: str = "it", leader: str = "the leader", value: str | None = None) -> str:
        """What the companion does under a setting's value (its present one unless given), in words."""
        return SETTINGS[name].does[value or self.now(name)].format(me=me, leader=leader)

    def spoken_to(self, probabilities: dict[str, float], cut: float = WHICH_CUT) -> list[str]:
        """The settings a line speaks to, from the answer to which_question: each given `cut` or more."""
        return [s.name for s in self.asked() if probabilities.get(s.name, 0.0) >= cut]

    def questions(self, me: str, leader: str, only: list[str] | None = None) -> dict[str, dict[str, Any]]:
        """One question a setting (those in `only`, or all): what the line makes of it for this companion, or that
        it leaves it alone."""
        role = (f"You read what {leader}, who leads a party of adventurers in the game Ultima Online, has just said aloud "
                f"(`line`). It tells {me}, one of the party's companions, something about how to play from now on. "
                f"{leader} is played by a person and speaks as people do: short, loose, often joking. A line said to "
                f"the whole party, or to 'the rest of you' when another companion is the one named apart, is said to "
                f"{me} too. `settings_now` is how {me} plays at the moment.")
        out = {}
        for s in self.asked():
            if only is not None and s.name not in only:
                continue
            about = s.about.format(me=me, leader=leader)
            out[s.name] = {
                "type": "choice",
                "instructions": {"role": role, "question": f"With this line, what is {leader} telling {me} about {about}?"},
                "criteria": {**{v: text.format(me=me, leader=leader) for v, text in s.values.items()},
                             UNCHANGED: f"Nothing: the line doesn't speak of {about}, or what it says of it is for other "
                                        f"companions and not for {me}."}}
        return out

    def state(self, text: str, me: str, leader: str, others: list[str], earlier: list[str] | None = None,
              only: list[str] | None = None) -> dict[str, Any]:
        """The state that goes with `questions`: the party, the line, and how this companion now plays in the
        settings asked about."""
        return {"party": {"leader": leader, "companions": sorted({me, *others})}, "companion": me,
                "settings_now": {s.name: self.words(s.name, me, leader) for s in self.asked() if only is None or s.name in only},
                **({"said_before": earlier} if earlier else {}), "line": " ".join(text.split())[:200]}

    def take(self, ans: Answers, cut: float = SET_CUT) -> list[dict[str, Any]]:
        """The settings the answers change, each taken: [{"setting", "was", "now", "p"}], one for every setting
        given a value at `cut` or more. One told what it does already is in the list only when nothing else
        changed: the leader then hears that it already does ("Leaving the loot alone.")."""
        out = []
        for s in self.asked():
            pick = ans.choices.get(s.name)
            if pick is None or pick.choice == UNCHANGED or pick.choice not in s.values:
                continue
            p = pick.probabilities.get(pick.choice, pick.confidence)
            if p < cut:
                continue
            out.append({"setting": s.name, "was": self.now(s.name), "now": pick.choice, "p": round(p, 3)})
        new = [c for c in out if c["was"] != c["now"]]
        for c in out:
            # Kept also when it is what the companion starts with: the leader has now said so, and it holds over
            # whatever a strategy of the companion's own would have made of it (apply).
            self.values[c["setting"]] = c["now"]
        self.changes += new
        return new or out

    def apply(self, cfg: PolicyConfig) -> PolicyConfig:
        """The fight's settings the leader has spoken of, laid over the fight loop's configuration."""
        v = self.values
        if not v:
            return cfg
        base = PolicyConfig()
        out: dict[str, Any] = {}
        if "looting" in v:
            out |= {"looting": v["looting"],
                    "take_item": {"everything": 0.0, "valuables": base.take_item, "nothing": 2.0}[v["looting"]]}
        if "fleeing" in v:
            out["allow_flee"] = v["fleeing"] == "allowed"
        if "aggression" in v:
            flee_danger = 0.3 + 0.65 * AGGRESSION[v["aggression"]]  # as a strategy's aggression does (strategy.apply)
            out |= {"flee_danger": flee_danger, "panic_danger": max(base.panic_danger, flee_danger)}
        if "target" in v:
            out["target_priority"] = v["target"]
        for name in ("opening_spell", "main_spell"):
            if name in v:
                out[name] = "" if v[name] == "own" else v[name]
        if "helping" in v:
            out["help_mates"] = v["helping"] == "on"
        if "raising" in v:
            out["raise_fallen"] = v["raising"] == "on" and out.get("help_mates", cfg.help_mates)
        if "reach" in v:
            out["escort_tiles"], out["escort_own"] = REACH[v["reach"]]
        return replace(cfg, **out)

    def told(self, taken: list[dict[str, Any]], me: str, leader: str) -> tuple[str, str]:
        """What the companion's answer has to get across after a line that spoke to its settings, for whoever
        writes it, and the same in plain words of its own for when nobody does."""
        if not taken:
            return "", ""
        new = [c for c in taken if c["was"] != c["now"]]
        does = "; ".join(self.words(c["setting"], me, leader, c["now"]) for c in (new or taken))
        plain = " ".join(SETTINGS[c["setting"]].plain[c["now"]] for c in (new or taken))
        if new:
            return (f"{leader}'s line changed how {me} plays, and {me} has made the change. From now on {me}: {does}. "
                    f"{me} says so in their own way, plainly enough that {leader} can tell what {me} will now do.", plain)
        return (f"{leader}'s line asked for what {me} already does ({me}: {does}), so nothing changes. {me} says that "
                f"they already do, in their own way.", plain)

    def to_log(self) -> dict[str, Any]:
        return {"settings_changed": [{k: c[k] for k in ("setting", "was", "now")} for c in self.changes],
                **({"settings_now": dict(self.values)} if self.values else {})}
