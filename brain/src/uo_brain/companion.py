"""A party for the player (cuo-cym.6): companions that follow and fight beside a character a person plays.

A party was five agents, and its leader a planner. Every follower took its goals from that planner
through the party store, so a person could not lead one: a character someone plays posts no goals
and reports nothing. Here the leader is read from what each companion's own client sees of it:

- **Where it walks:** the companion keeps beside it (the client's follow: at a walk, running to
  catch up), and when it is out of view goes to where it was last seen and waits there.
- **What it fights:** the fight loop runs the whole time, as a companion's (policy `escort`): it
  takes on what attacks it, and anything in war mode close to itself or within a few tiles of the
  leader, which is what the leader is fighting or what is on the leader (a client is not told whom
  another character attacks). It walks to those, never off after anything else, and loots nothing
  until the leader says it may: the kills are the player's.
- **Its health:** the leader is a companion like any other to the helping (policy.help_mates): a
  mage heals it, one with bandages bandages it, by what its health bar shows.
- **What the leader says, in its own words** (cuo-cym.15, .18 to .21). Every line is put to system one
  (kind `order`), each companion for itself with the party's names in the state:
  - *an order?* Come along, wait where you are, sit down, or go somewhere without the leader ("off to
    the inn, I'll meet you there"), or none of them (`order_question`);
  - *a setting?* Does it change how this companion plays from now on: what it loots, whether it runs,
    how much it talks (standing.py)? A yes/no here, and on a yes a question for each setting;
  - *for whom?* In a party that talks: one companion, some, all, or none of them (`for_question`), so
    each it is for answers and the rest stay out of it (talk.py);
  - *an order for the fight itself?* With creatures in view (cuo-cym.24): go for one creature now ("kill
    the mage first", "get the one on me"), fall back to the leader, or heal someone now, or none of them
    (`fight_question`), with which creature (`target_question`, the creatures in view as its options) and
    whom to heal (`whom_question`). Carried out at once, through the fight loop's configuration (`live`:
    policy's focus, fall_back and heal_now), and said back as an order is ("On the evil mage.").
  Heard from the leader alone, by name and then by the serial first seen under that name: what anyone
  else says changes nothing, and is never put to a model. Without a model, or when it doesn't answer,
  a short list of phrases reads the orders ("stay here", "follow me", "sit down"; with a companion's
  name, for that one only).
- **A word on what it took.** On an order it says a short line of its own (voice.py), also when the
  order is the one already in force ("Still with you."), so a line misread as an order shows. How each
  line was read is kept (`Reading`) for whoever writes this companion's answer, so that what it says
  agrees with what it does: an order taken is its answer; a setting changed is said in its own words;
  anything else is told to the writer as talk that changes nothing.
- **Going somewhere without the leader** (cuo-cym.19): the place is the nearest of its kind the world
  store knows (the inn, the bank, the healer's), picked by system one from the leader's words. Going
  is the companion's to agree to (`persuade`): a yes/no on the moment, and a no is said with its
  reason, and may become a yes when the leader asks again. It travels there with the fight loop
  beside it, waits, and is the leader's again when the leader arrives or calls it back.
- **Rest:** when the leader stands still its companions stand with it, and each heals itself as it
  always does (the client's reflexes bandage). There is no rest of their own to call.
- **The table** (table.py), in a party that talks: at "sit down" (or "drinks", "take a seat") they
  hold the party's talk over the hunt where they stand, so the leader walks them to the inn first, or
  sends them. No planner hosts it: the first companion on the party's list who is there keeps the
  turns, and puts on the table what the leader says while it is open, so the next to speak answers
  it. The leader's next order ("follow me") ends the evening; each keeps its own memories of it.
- **Its own death:** resurrected and back for its corpse by itself, as a follower is, then back
  beside the leader.

The callouts, the talk (a companion with a persona answers the leader as it answers the others)
and the game's own party work as in any party; forming up is a planner's and is not here. The talk is
for the local server only. A fleet file asks for it with `party = true`, `plan = "player"` and
`leader = "NAME"`, the character the person plays, which is not one of its agents (fleet.py).
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from . import calls, costs, loop, policy, standing, state as states
from .party import Party
from .spells import GREATER_HEAL
from .world import compass, tiles

FOLLOW_TILES = 2     # how close a companion keeps to the leader
LOST_TILES = 3       # out of view: it goes to within this of where the leader was last seen
LOOK_S = 1.0
SPOKEN = ("regular", "yell", "whisper", "party")  # party chat too: an order needn't be said to the whole screen
# The phrases: what reads the leader's lines when system one doesn't (no model, or no answer). Before cuo-cym.15
# they were all there was, and of 53 lines a person might say they read 88 of 159 readings as written.
# An order is a phrase of whole words in the leader's line, the earliest of them when there are several. Bare
# "stay", "with me" and "to me" were matched anywhere in a line once: "stay close" stopped the companions,
# "you should have stayed back" left one standing, and "drink with me!" ended the evening at the table.
STAY = ("stay here", "stay put", "stay there", "wait here", "hold here", "stop here", "hold position")
FOLLOW = ("follow me", "come with me", "come on", "let's go", "lets go", "move out", "stay with me", "stay close")
TABLE = ("sit down", "take a seat", "have a seat", "a drink", "drinks", "the inn", "the table")
# Sent somewhere without the leader. These come before the table's phrases: "off you go to the inn" is a walk.
GO = ("go on ahead", "go ahead to", "off you go", "meet you there", "meet you at", "see you there", "see you at",
      "wait for me at", "i'll catch you up", "ill catch you up")
# ...or the whole of the line, once names and small words are out of it: "Wystan, follow.", "With me, all of you!"
SHORT = {"stay": "stay", "wait": "stay", "halt": "stay", "hold": "stay", "follow": "follow", "come": "follow",
         "with me": "follow", "to me": "follow"}
SMALL = ("please", "now", "all", "of", "you", "everyone", "and")
UP = ("follow me", "come with me", "let's go", "lets go", "move out", "on your feet")  # what ends an evening at the table
ACK = {"stay": "Staying here.", "follow": "Right behind you.", "table": "Aye, I'll drink to that.", "go": "On my way."}
ACK_KIND = {"stay": "staying", "follow": "coming", "table": "sitting", "go": "going"}  # the same in its own voice (voice.KINDS)
# Its word on an order that is the one already in force (cuo-cym.20): kind in its own voice, and the plain line.
STILL = {"follow": ("still_coming", "Still with you."), "stay": ("still_staying", "Still here."),
         "go": ("still_going", "On my way.")}
STILL_GAP_S = 15.0   # ...at most this often: "keep up", "this way", "come on" on a walk aren't each answered by all
HOST_WAITS_S = 3.0   # how long each gives the others to take the order (a look of their own, LOOK_S) before it counts seats
SEAT_S = 8.0         # a seat taken this long before the count is still one for this table
SIT_WAIT_S = 45.0    # one who isn't the keeper waits this long for the table to open
# System one's reading of a line is an order when it gives one at least this. Chosen on the first set of lines in
# scripts/orders_check.py: at 0.5 Decision 1 took "Hold on, what did you just say?" for an order to stay (0.52).
ORDER_CUT = 0.6
DEAF_S = 60.0        # two questions failed in a row: the phrases read the leader's lines for this long
HEAR_MAX = 4         # at most this many of the leader's lines are read at one look, the last of them
ORDERS = ("follow", "stay", "table", "go")
# Sure that a line is an order to move, and unsure whether the leader comes too: sent, when that has this much.
GO_SHARE = 0.3
# An order the model gave this much and not the cut: not taken, and whoever writes the answer is told of the doubt.
NEAR_CUT = 0.35
ORDER_WORDS = {"follow": "come along", "stay": "wait here", "go": "go somewhere without {leader}",
               "table": "sit down for a drink"}
# A line asks this companion to do something when the yes/no on that gives this. On the lines it was settled on,
# requests got 0.93 and more and talk 0.35 and less, but for "anyone need to wait for mana?" (0.59 to 0.68) and "On
# my way. Order me an ale." (0.62 to 0.76): under the cut a line is left to the yes/no on answering, which is the
# lesser harm (a refusal nobody asked for is worse than an answer that doesn't refuse).
ASK_CUT = 0.7
# Who a line is for (cuo-cym.21): a companion is one of those when the options with it in them add up to this.
FOR_CUT = 0.5
# ...and an order for the fight is this companion's unless the line is plainly for others: one with nobody named
# ("Get the one on me!") split the question live, 0.35 to 0.50 for each of the three, and nobody took it.
FIGHT_FOR_CUT = 0.3
EARLIER_S = 90.0     # what was said this lately goes with a line, to make sense of it ("off you go then")
EARLIER_MAX = 6
# Going somewhere without the leader (cuo-cym.19).
PLACE_CUT = 0.5      # the place is the one system one gives at least this
WILLING_CUT = 0.5    # ...and the companion goes when the yes/no on whether it would gives this
ARRIVE_TILES = 3     # this near the place it has arrived
FALL_IN_TILES = 8    # the leader this near one that waits where it was sent: it is the leader's again
GO_MAX_TILES = 400   # a place further off than this isn't offered
SAME_INN_TILES = 20  # an inn from the shard's data this little further than one only seen is taken for it
GO_TRIES = 4         # walks begun toward the place before it gives the trip up and comes back
REFUSED_S = 180.0    # a refusal this lately is told to the next asking: asked again, it is more ready to go
READINGS_KEPT = 60
# Orders for the fight itself (cuo-cym.24): a creature to go for, falling back to the leader, a heal now. Read
# only with creatures in view (one of them able to start a fight), by a question of their own.
FIGHT_CUT = 0.6       # the line is such an order when the fight question gives it this or more
TARGET_CUT = 0.5      # ...and the creature meant is the one the question on which gives this or more
WHOM_CUT = 0.5        # ...and whom to heal, the same
CREATURES_MAX = 6     # the creatures offered: the nearest, as many as the fight loop weighs (state.build)
FOCUS_S = 30.0        # a creature named is gone for until it dies or leaves view, or for this long
FALL_BACK_S = 60.0    # a fall back holds for the fight: until nothing is in war mode near for FALL_BACK_CALM_S, or this long
FALL_BACK_CALM_S = 5.0
FALL_BACK_CLEAR = 12  # ...near: within this many tiles of the companion
HEAL_S = 15.0         # an order to heal someone stands this long, if it can't be carried out sooner (out of reach)
FIGHT_ORDERS = ("attack", "fall_back", "heal")
# The kinds of place a companion can be sent to, and what it calls each. The nearest of each kind the world
# store knows is offered to system one; a place by its own name ("Yew") isn't.
PLACE_KINDS = {"inn": "the inn", "tavern": "the tavern", "bank": "the bank", "healer": "the healer's",
               "stable": "the stables", "blacksmith": "the smithy", "provisioner": "the provisioner's",
               "mage_shop": "the mage shop", "alchemist": "the alchemist's", "tailor": "the tailor's",
               "bowyer": "the bowyer's", "moongate": "the moongate", "town": "town", "graveyard": "the graveyard",
               "docks": "the docks", "dungeon_entrance": "the dungeon's entrance", "shrine": "the shrine"}
# For the phrases: the word in a line that names a kind of place.
PLACE_WORDS = {"inn": "inn", "tavern": "tavern", "bank": "bank", "healer": "healer", "stables": "stable", "stable": "stable",
               "smithy": "blacksmith", "blacksmith": "blacksmith", "moongate": "moongate", "town": "town",
               "graveyard": "graveyard", "cemetery": "graveyard", "docks": "docks", "shrine": "shrine"}


def order_of(text: str, me: str, others: list[str], at_table: bool = False) -> str | None:
    """The leader's order in a line of its speech, for this companion, by the phrases: "stay", "follow", "table" or
    "go", or None. A line that names another companion and not this one is for that one. "Follow me to the inn" is
    a walk: the table is asked for once they are there. at_table: only what ends an evening counts ("follow
    me", "stay here"); everything else said there is talk."""
    words = text.lower()
    for ch in ",.!?;:":
        words = words.replace(ch, " ")
    words = words.split()
    names = {n.lower() for n in others}
    if any(w in names for w in words) and me.lower() not in words:
        return None
    line = f" {' '.join(words)} "
    found: list[tuple[int, str]] = []
    for order, phrases in (("follow", UP), ("stay", STAY)) if at_table else \
            (("go", GO), ("follow", FOLLOW), ("stay", STAY), ("table", TABLE)):
        at = [line.find(f" {ph} ") for ph in phrases]
        if any(a >= 0 for a in at):
            found.append((min(a for a in at if a >= 0), order))
    if found:
        # Sent to a place, whatever else the line has in it: "see you at the inn" has the table's "the inn" too.
        return "go" if any(order == "go" for _, order in found) else min(found)[1]
    rest = " ".join(w for w in words if w not in names and w not in SMALL)
    return None if at_table else SHORT.get(rest) or SHORT.get(" ".join(w for w in words if w not in names))


def order_in(probabilities: dict[str, float], cut: float = ORDER_CUT) -> tuple[str | None, float, bool]:
    """The order in system one's answer to order_question: (the order or None, how sure, whether it was in
    doubt). It is the likeliest, at `cut` or more. One case apart: sure that it is an order to move, and unsure
    whether the leader comes too. Live, in three runs, "Right - off to the inn for you all - see you there" had
    being sent there at 0.32 to 0.80 and coming along at 0.18 to 0.67 for the three companions (the two always
    0.98 or more together): one went and two stood still, twice. Then it is being sent, when that has GO_SHARE
    or more, also where coming along has the cut: a companion sent to a place the leader walks to as well gets
    there first and is found there, and one left to follow a leader who stays doesn't move. With no place named
    it comes along after all (Companion.send_off)."""
    if not probabilities:
        return None, 0.0, False
    pick = max(probabilities, key=lambda k: probabilities[k])
    p = probabilities[pick]
    go, follow = probabilities.get("go", 0.0), probabilities.get("follow", 0.0)
    if go + follow >= cut and go >= GO_SHARE and not (pick == "go" and p >= cut):
        return "go", go + follow, True
    if pick in ORDERS and p >= cut:
        return pick, p, False
    if go + follow >= cut:
        return "follow", go + follow, True
    return None, p, False


def asked_question(me: str, leader: str) -> dict[str, Any]:
    """Whether a line asks this companion to do something, as a yes/no beside the order question (cuo-cym.20).
    A line that is no order it can take and asks for something all the same ("cover me", "heal me") gets a no in
    its own words: left to the yes/no on answering, which gave "cover me" 0.13 to 0.29, a person who asked for
    something heard nothing back. As a sixth answer of the order question it took real orders with it ("Get
    yourselves to the bank and wait." was a request at 0.8), so it is a question of its own, and a request of
    its own sent beside the order's: only a companion that talks has a use for it, and the order's request
    stays the one that was measured."""
    return {"type": "noul",
            "instructions": {
                "role": f"You read what {leader}, who leads a party of adventurers in the game Ultima Online, has just "
                        f"said aloud (`line`). {leader} is played by a person and speaks as people do: short, loose, "
                        f"often joking. {me} is one of the party's companions.",
                "question": f"With this line, is {leader} asking or telling {me} to do something in the game now, "
                            f"other than to speak?"},
            "criteria": {
                "true": f"An order or a request to act now, given to {me}, to the whole party with {me} in it, or to "
                        f"whoever of the party can ('somebody', 'one of you'), whatever the act: come along, wait, go "
                        f"somewhere, attack or kill something, heal or resurrect someone, cover or protect someone, "
                        f"fetch, buy, carry or loot something, scout or lead the way ('cover me', 'heal me', 'kill "
                        f"that one', 'somebody res me').",
                "false": f"Talk: a joke, a question, a remark, thanks, a cheer or a curse ('come on, you beauty!'), a "
                         f"saying ('heal thyself, as they say'), words that build up to something ('wait for "
                         f"it...'), a plan put as a question, something about what happened or what {leader} will do; "
                         f"asking {me} to say, tell or explain something; an order or a request given to other "
                         f"companions by name and not to {me}; a command to {leader}'s own pets ('all kill', 'all "
                         f"follow me', 'all stay'); words for a shopkeeper or a banker ('vendor buy', 'bank').",
            }}


def left_over(probabilities: dict[str, float], asked: float, cut: float = ASK_CUT) -> str:
    """What a line that gave no order is, for the answer to it: "unsure" when it was nearly an order (NEAR_CUT)
    and asks for something, "asked" when it asks for something that is none of the orders, else "" (talk)."""
    if asked < cut:
        return ""
    near = max((probabilities.get(o, 0.0) for o in ORDERS), default=0.0)
    return "unsure" if near >= NEAR_CUT else "asked"


def place_of(text: str) -> str | None:
    """The kind of place a line names, by its word ("the inn", "the bank"): what the phrases can tell of where."""
    words = text.lower()
    for ch in ",.!?;:'":
        words = words.replace(ch, " ")
    return next((PLACE_WORDS[w] for w in words.split() if w in PLACE_WORDS), None)


def order_question(me: str, leader: str, at_table: bool = False) -> dict[str, Any]:
    """What a line of the leader's asks of this companion, as a question for system one (kind `order`)."""
    role = (f"You read what {leader}, who leads a party of adventurers in the game Ultima Online, has just said aloud "
            f"(`line`), and decide what it tells {me}, one of the party's companions, to do. {leader} is played by a "
            f"person and speaks as people do: short, loose, often joking. At {leader}'s word {me} can do four "
            f"things: come along, wait where they are, go somewhere without {leader}, or sit down with the party for "
            f"a drink. Most of what {leader} says is talk and asks for none of them. `said_before`, when it is there, "
            f"is the talk just before the line, oldest first: it only helps to make sense of a line that points back "
            f"at it ('off you go then', 'yes, there'), and nothing in it is an order now. A line that is nothing more "
            f"than an acknowledgement (a bare 'ok', 'fine' or 'lol') is talk.")
    none = (f"Anything else. Talk, a joke, a question, thanks, a remark on a fight or on what someone did or should "
            f"have done; something for later, or that {leader} will do alone without telling anyone to wait; a plan "
            f"put as a question ('shall we head to the inn?'); an order that {me} can't carry out by following, "
            f"waiting, going somewhere or sitting down (heal me, kill that, loot); an order given to other companions "
            f"by name and not to {me}; words for a shopkeeper, a banker or a pet.")
    if at_table:
        return {"type": "choice",
                "instructions": {"role": role + f" The party is sitting at a table together now, talking the day over, "
                                                f"and {leader} sits with them.",
                                 "question": f"With this line, is {leader} telling {me} to get up from the table?"},
                "criteria": {
                    "follow": f"{leader} is ending the evening for {me}: time to get up and go, back on the road, "
                              f"come along.",
                    "stay": f"{leader} is getting up and telling {me} to wait here until they are back.",
                    "none": none + " Anything said at the table that isn't an order to leave it: another round, a "
                                   "toast, an offer to stay a while, to sit down or to drink.",
                }}
    return {"type": "choice",
            "instructions": {"role": role, "question": f"With this line, what is {leader} telling {me} to do now?"},
            "criteria": {
                "follow": f"{leader} is telling {me} to come along now, where {leader} goes: follow, keep up, come here, "
                          f"come back to me, come and find me, get moving, set off again, stop waiting. A walk to "
                          f"somewhere counts when {leader} walks there too ('follow me to the inn', 'to the bank, "
                          f"with me').",
                "stay": f"{leader} is telling {me} to stop and wait where they are now, on this spot ('wait here', 'wait "
                        f"for me by this tree'), while {leader} goes on or does something without them.",
                "go": f"{leader} is sending {me} somewhere else and is not coming along now: {me} is to go there by "
                      f"themselves and wait. The tell is that {leader} stays behind or comes later: 'off to the inn "
                      f"with you', 'see you there', 'go on ahead', 'off you go', 'I'll meet you there', 'I'll catch "
                      f"you up', 'I'll follow you', 'wait for me at the bank' (a place away from here). A bare word "
                      f"said to a banker or a shopkeeper ('bank', 'vendor buy') is not this.",
                "table": f"{leader} is telling the party to sit down together now, where they stand: take a seat, "
                         f"have a drink, rest and talk.",
                "none": none,
            }}


def order_state(text: str, me: str, leader: str, others: list[str], doing: str,
                earlier: list[str] | None = None) -> dict[str, Any]:
    """The state that goes with order_question: who is in the party, what this one is doing, and the line."""
    return {"party": {"leader": leader, "companions": sorted({me, *others})},
            "companion": me, "doing_now": doing, **({"said_before": earlier} if earlier else {}),
            "line": " ".join(text.split())[:200]}


def for_question(leader: str, companions: list[str], plays: dict[str, str] | None = None) -> dict[str, Any]:
    """Who a line of the leader's is said to, as one question for system one with the party as its options
    (cuo-cym.21). Every companion asks it with the same state, so they agree on the answer. A yes/no from each
    couldn't tell: told whose answer an unnamed line followed, the one who had answered took "What did the rest of
    you make of it?" too, and told to judge it from the line all three said yes or nobody did. plays: what each
    companion plays as (a mage, an archer), so that "our archer" names one."""
    def who(n: str) -> str:
        kind = (plays or {}).get(n, "")
        return f"{n}, the party's {kind}" if kind and kind != "companion" else n

    role = (f"You read what {leader}, who leads a party of adventurers in the game Ultima Online, has just said aloud "
            f"(`line`), and decide who it is said to. {leader} is played by a person, talking with their companions "
            f"({'; '.join(who(n) for n in companions)}) as people do with friends in a game. `said_before` is the talk "
            f"just before the line, oldest first.")
    criteria = {}
    for n in companions:
        criteria[n] = (f"To {who(n)}, and no other companion: {n} is named or plainly meant (by what {n} plays, a "
                       f"nickname, what {n} keeps or carries), or the line follows straight on from what {n} has just "
                       f"said to {leader} and is about that ('And your reagents?', 'Why north?').")
    criteria["everyone"] = ("To the whole party, or to nobody in particular: a question, a remark, a greeting or an "
                            "order that any of them might take up ('how are you all?', 'anyone hurt?', 'which way "
                            "now?', 'yay or nay?').")
    if len(companions) > 2:
        for n in companions:
            rest = " and ".join(c for c in companions if c != n)
            criteria[f"all_but_{n}"] = (f"To {rest} and not to {n}: they are named, or {n} has just answered and the "
                                        f"line turns to the others ('the rest of you', 'and you two?', 'anyone else?').")
    criteria["nobody"] = (f"To none of them: {leader} is talking to themselves or about their own keyboard, or the "
                          f"words are for a shopkeeper, a banker, a pet or someone outside the party.")
    return {"type": "choice", "instructions": {"role": role, "question": f"Who is {leader} saying this line to?"},
            "criteria": criteria}


def for_state(text: str, leader: str, companions: list[str], earlier: list[str] | None = None) -> dict[str, Any]:
    return {"party": {"leader": leader, "companions": companions}, "said_before": earlier or [],
            "line": " ".join(text.split())[:200]}


def for_whom(probabilities: dict[str, float], companions: list[str]) -> dict[str, float]:
    """How likely the line is for each companion: its own option, "everyone", and every "all but" another."""
    return {n: round(probabilities.get(n, 0.0) + probabilities.get("everyone", 0.0)
                     + sum(probabilities.get(f"all_but_{o}", 0.0) for o in companions if o != n), 4)
            for n in companions}


def place_question(me: str, leader: str, places: list[dict[str, Any]]) -> dict[str, Any]:
    """Where the leader is sending this companion: the places it knows, as the options."""
    # The options are kinds of place, each with how far the nearest is, and not the places by their names. Given
    # "The inn: North Britain inn at (1458, 1525), 66 tiles to the east." the model wasn't sure that "the inn" in
    # "off to the inn for you all - see you there" was that inn: with the talk of a live run before the line it
    # said the place was unclear at 0.63 to 0.79 for all three companions, and each asked "Go where?".
    role = (f"{leader}, who leads a party of adventurers in the game Ultima Online, has just sent {me}, one of the "
            f"party's companions, off somewhere without them (`line`). Decide what kind of place {me} is sent to: {me} "
            f"goes to the nearest place of that kind. It is the place {me} is sent to, not one {leader} says they are "
            f"going to themselves. `said_before` is the talk just before the line, oldest first: when the line names "
            f"no place itself ('off you go then', 'yes, there'), the place is the one that talk was about.")
    return {"type": "choice", "instructions": {"role": role, "question": f"Where is {leader} sending {me}?"},
            "criteria": {**{p["kind"]: f"{p['says'][:1].upper()}{p['says'][1:]}: the nearest is {p['tiles']} tiles to the "
                                       f"{p['way']}." for p in places},
                         "unclear": "None of these: no place is named in the line or in the talk before it, or the place "
                                    "named is of no kind listed, or lies in another town ('see you in Yew', 'the inn at "
                                    "Skara Brae')."}}


def places_near(world: Any, here: tuple[int, int]) -> list[dict[str, Any]]:
    """The places a companion standing `here` could be sent to: the nearest of each kind the world store knows,
    with what it calls each ("the inn") and where it lies. An inn from the shard's own data comes before one
    where an innkeeper happened to be seen, as the planner's inn does (session.find_inn)."""
    if world is None:
        return []
    out = []
    for kind, says in PLACE_KINDS.items():
        try:
            found = world.find_place(kind, near=list(here), limit=4)
        except Exception:  # noqa: BLE001 - a store that can't say: no place of this kind
            continue
        found = [p for p in found if p["kind"] == kind and (p.get("distance") or 0) <= GO_MAX_TILES]
        if not found:
            continue
        # The nearest. An inn or tavern "seen" is where an innkeeper or a bard happened to stand, so one from the
        # shard's own data is taken before it when that is hardly further. Not whenever there is one: once a
        # companion had seen the innkeeper of the inn 64 tiles off, the store listed that inn as seen, and "the
        # inn" became one 108 tiles off (and with it the model no longer knew which inn was meant).
        near = found[0]
        known = [q for q in found if q.get("source") != "seen" and (q.get("distance") or 0) <= (near.get("distance") or 0) + SAME_INN_TILES]
        p = known[0] if kind in ("inn", "tavern") and near.get("source") == "seen" and known else near
        out.append({"kind": kind, "says": says, "name": p["name"], "x": p["x"], "y": p["y"], "tiles": p.get("distance", 0),
                    "way": compass(p["x"] - here[0], p["y"] - here[1])})
    return out


def willing_question(me: str, leader: str) -> dict[str, Any]:
    """Whether this companion goes off without the leader when asked, as a yes/no on the moment: going is its own
    to agree to (Harry, 2026-10-13: "they need to be able to act independently if I can persuade them to do so")."""
    return {"type": "noul",
            "instructions": {
                "role": f"{leader} leads a party of adventurers in the game Ultima Online and is played by a person. "
                        f"{leader} has just asked {me}, a companion with a mind of their own (`who`), to go somewhere "
                        f"without {leader} and wait there (`line`). `moment` is how things stand.",
                "question": f"Does {me} agree and go?"},
            "criteria": {
                "true": f"{me} goes. It is a reasonable thing to ask of a companion and nothing in the moment speaks "
                        f"hard against it; or {me} has refused already (`refused_before`) and {leader} asks again, "
                        f"insists or gives a reason.",
                "false": f"{me} has a real reason of their own not to go right now: {me} or {leader} is badly hurt or "
                         f"still in a fight, or hostile creatures are close, so that going off alone or leaving "
                         f"{leader} alone would be dangerous.",
            }}


def fight_question(me: str, leader: str) -> dict[str, Any]:
    """Whether a line gives this companion an order for the fight itself (cuo-cym.24): a creature to go for, to fall
    back to the leader, or someone to heal now. A question of its own, sent beside the order's while there are
    creatures in view: as one more answer of the order question it would have cost real orders, as the request's did
    (asked_question), and the order question and the request's yes/no stay the ones measured."""
    role = (f"You read what {leader}, who leads a party of adventurers in the game Ultima Online, has just said aloud "
            f"(`line`) with creatures about (`creatures_in_view`), and decide whether it gives {me}, one of the party's "
            f"companions, an order for the fight itself, to carry out now. {leader} is played by a person and speaks as "
            f"people do: short, loose, often joking. {me} already fights whatever is on the party and heals whoever is "
            f"badly hurt without being told; an order here is one thing more, now. `said_before`, when it is there, is "
            f"the talk just before the line, oldest first: it only helps to make sense of a line that points back at it.")
    return {"type": "choice",
            "instructions": {"role": role, "question": f"With this line, what is {leader} telling {me} to do in the fight now?"},
            "criteria": {
                "attack": f"Go for one creature in particular now, before anything else: attack, kill, hit, shoot or cast "
                          f"at it, take it on, tank it, send a pet at it, get it off someone ('kill the mage first', 'get "
                          f"the one on me', 'that one!', 'shoot the lich', 'tank that'). Said to {me} by name, to the "
                          f"whole party, or to whoever can; not an order given to another companion by name.",
                "fall_back": f"Stop fighting and get out of the fight, back to {leader}: fall back, pull back, get out, "
                             f"back off, retreat, run, leave it and come here ('fall back!', 'get out of there', 'back to "
                             f"me, now'). Being sent off somewhere else without {leader}, to the inn or to town, while "
                             f"{leader} stays to fight is not this.",
                "heal": f"Heal, bandage or patch up someone now: {leader} or one of the party ('heal me', 'bandage me, "
                        f"quick', 'Thea needs a heal').",
                "none": f"Anything else. Talk, a cheer, a curse, thanks, a joke, a question, a saying ('nice shot', 'kill "
                        f"me now, I forgot the reagents', 'any bandages left?', 'heal thyself, as they say'); words "
                        f"shouted at a creature and not to the party; {leader} taking a creature on alone ('leave this "
                        f"one to me'); a request for no one creature ('cover me', 'watch my back', 'hold them off', 'kill "
                        f"them all'); an order to come along, to wait, or to go somewhere without {leader}; a rule for "
                        f"every fight ('weakest first', 'nobody runs'); an order given to other companions by name and "
                        f"not to {me}; a command to {leader}'s own pets ('all kill', 'all guard me'); words for a "
                        f"shopkeeper or a banker.",
            }}


def target_question(me: str, leader: str, creatures: list[dict[str, Any]]) -> dict[str, Any]:
    """Which creature in view the leader means, asked with the fight question: the creatures as the options."""
    role = (f"{leader}, who leads a party of adventurers in the game Ultima Online, may have just told {me}, one of the "
            f"party's companions, to go for a creature in this fight (`line`). Decide which of the creatures in view "
            f"{leader} means. {leader} is played by a person and speaks as people do. A creature is meant by what it is "
            f"called, and a word of its name is enough: a name means the creature with that word in its name, not "
            f"another of the same sort ('the mage' is one called a mage, not a lich that casts spells too). Or by whom "
            f"it is on ('the one on me' is the one on {leader}, 'the one on Thea'), or by pointing: 'that one', 'this "
            f"one' or 'it' with nothing more said is the one on {leader}, or else the one nearest {leader}. A pet sent "
            f"at a creature: that creature is the one meant.")
    criteria = {c["id"]: c["words"] for c in creatures}
    criteria["unclear"] = ("None of these: the creature named isn't in view here, or the line means them all or none in "
                           "particular ('kill them all').")
    return {"type": "choice", "instructions": {"role": role, "question": f"Which creature does {leader} mean?"},
            "criteria": criteria}


def whom_question(me: str, leader: str, companions: list[str]) -> dict[str, Any]:
    """Whom the leader wants healed, asked once the fight question has read a heal: the party as the options."""
    role = (f"{leader}, who leads a party of adventurers in the game Ultima Online, has just told {me}, one of the "
            f"party's companions, to heal someone now (`line`). Decide whom. {leader} is played by a person and speaks "
            f"as people do: 'me' in the line is {leader}.")
    criteria = {leader: f"{leader} themselves ('heal me', 'patch me up', 'I need a heal here')."}
    for n in companions:
        if n != me:
            criteria[n] = f"{n}: named, or plainly meant ('heal {n}', '{n}'s hurt, see to them')."
    criteria["unclear"] = f"Someone else, {me} themselves, or nobody that can be told."
    return {"type": "choice", "instructions": {"role": role, "question": f"Whom is {leader} telling {me} to heal?"},
            "criteria": criteria}


def heal_whom(text: str, me: str, leader: str, companions: list[str]) -> str:
    """Whom a heal is for when whom_question couldn't tell: the leader when the line names nobody of the party
    ("heal pls": on the held-back lines both models called it unclear, 0.6 to 0.8, and all three would have asked
    whom); this companion itself when it is the only one named ("someone heal Wystan", read by Wystan); else ""."""
    words = set(re.findall(r"[a-z]+", text.lower()))
    named = [n for n in companions if n.lower() in words]
    return leader if not named else me if named == [me] else ""


def fight_state(text: str, me: str, leader: str, others: list[str], doing: str, creatures: list[dict[str, Any]],
                earlier: list[str] | None = None) -> dict[str, Any]:
    """The state that goes with fight_question and target_question: the party, the creatures in view, the line."""
    return {"party": {"leader": leader, "companions": sorted({me, *others})}, "companion": me, "doing_now": doing,
            "creatures_in_view": [c["words"] for c in creatures], **({"said_before": earlier} if earlier else {}),
            "line": " ".join(text.split())[:200]}


def creatures_in_view(snap: dict[str, Any], me: str, leader: str, companions: list[str],
                      limit: int = CREATURES_MAX) -> list[dict[str, Any]]:
    """The creatures a companion could be told to go for, from what its own client sees: the nearest `limit`, each
    with an id for the question, its serial, what it is called in a word back ("the lich", or "a skeleton" with more
    than one), and in words how far it is from this companion and the leader, whether it is in war mode and whom it
    is on (in war mode a tile from someone, the leader first; or attacking this companion)."""
    mobiles = snap.get("mobiles") or []
    people = {m["name"]: m for m in mobiles if m.get("name") in (leader, *companions) and not m.get("monster")
              and not m.get("dead")}
    found = [m for m in mobiles if m.get("monster") and not m.get("dead") and not m.get("pet") and not m.get("summon")]
    found = found[:limit]

    def kind(m: dict[str, Any]) -> str:
        return (m.get("name") or "a creature").removeprefix("an ").removeprefix("a ").strip() or "creature"

    out = []
    for i, m in enumerate(found):
        dx, dy = m.get("dx", 0), m.get("dy", 0)
        near = {n: max(abs(dx - o.get("dx", 0)), abs(dy - o.get("dy", 0))) for n, o in people.items()}
        fighting = bool(m.get("war_mode") or m.get("attacking_me"))
        on = me if m.get("attacking_me") else next(
            (n for n in (leader, me, *[c for c in companions if c != me]) if fighting
             and (m.get("distance", 99) if n == me else near.get(n, 99)) <= 1), "")
        name = m.get("name") or "a creature"
        lead = near.get(leader)
        d = m.get("distance", 0)
        words = (f"{name[:1].upper()}{name[1:]}: {d} tile{'' if d == 1 else 's'} from {me}"
                 + (f", {lead} from {leader}" if lead is not None else f"; {leader} is out of view")
                 + ("; in war mode" if fighting else "; not in war mode")
                 + (f", on {on}" if on else "")
                 + (f"; {m['hits_pct']}% health" if m.get("hits_pct") is not None and m["hits_pct"] < 100 else "") + ".")
        same = sum(1 for o in found if kind(o) == kind(m))
        says = name if m.get("name") and not name.lower().startswith(("a ", "an ")) else \
            f"the {kind(m)}" if same <= 1 else name
        out.append({"id": f"c{i + 1}", "serial": m["serial"], "name": name, "says": says, "words": words, "on": on})
    return out


def can_heal(snap: dict[str, Any], plays: str) -> str:
    """How this companion can heal someone else now, as the fight loop would (policy.help_mates): "spell" (Greater
    Heal, with the mana and reagents for it), "bandage" (Healing and bandages), or "" when it can't."""
    p = snap.get("player") or {}
    spells = (snap.get("magic") or {}).get("spells") or []
    if plays in policy.HEALERS and any(s.get("name") == GREATER_HEAL and not s.get("missing") for s in spells):
        return "spell"
    if (p.get("skills") or {}).get("Healing", 0) >= 30 and (p.get("supplies") or {}).get("bandages", 0) > 0:
        return "bandage"
    return ""


@dataclass
class Reading:
    """How this companion took one line of the leader's: what it does about it and what it has said, for whoever
    writes its answer (talk.Ears), so that the two agree (cuo-cym.20)."""
    seq: int
    text: str
    t: float
    order: str | None = None
    to: list[str] | None = None   # the companions the line is for; None when that wasn't judged
    said: str = ""                # its word on taking the line as an order: that was its answer
    owed: str = ""                # what its answer has to get across (a setting changed, a refusal), whatever else
    plain: str = ""               # ...in plain words of its own, said as they are when no line can be written
    note: str = ""                # how the line was taken, for a writer who answers it as talk
    doubt: bool = False           # an order to move, taken without being sure whether the leader comes too (order_in)
    # An order for the fight itself (cuo-cym.24): "attack", "fall_back" or "heal", how sure, the creature meant (from
    # creatures_in_view; None when it couldn't be told) and whom to heal.
    fight: str = ""
    fight_p: float = 0.0
    target: dict[str, Any] | None = None
    whom: str = ""


class Companion:
    """One companion of a party whose leader a person plays."""

    def __init__(self, session: Any, party: Party, log: Callable[[dict[str, Any]], None] | None = None,
                 game: Any = None, acknowledge: bool = True, table: Any = None, voice: Any = None, floor: Any = None,
                 link: Any = None, persuade: bool = True):
        self.session, self.party = session, party
        self.leader = party.leader
        self.log = log or (lambda _: None)
        self.game = game                # gameparty.GameParty: told the leader's serial once it is known
        self.table = table              # table.Table, in a party that talks: "sit down" holds it
        self.stopping: asyncio.Event | None = None
        self.tables = 0
        self.acknowledge = acknowledge  # a word aloud when an order is taken (local server only, as all talk)
        self.voice = voice              # voice.Voice: that word in its own voice, where it has one
        self.floor = floor              # talk.Floor: the word is on it, so the others hear it and don't answer it
        self.link = link                # mates.Mates: its callouts are a setting the leader can switch
        self.ears: Any = None           # talk.Ears, in a party that talks (hears)
        self.persuade = persuade        # going off without the leader is this companion's to agree to
        self.mode = "follow"
        self.serial = 0                 # the leader's, from the first character seen under its name
        self.seen_at: tuple[int, int] | None = None
        self.seq = -1                   # the journal read so far
        self.doing = f"looking for {self.leader}"
        self.own = False                # on business of its own (its resurrection, its corpse)
        self.orders = 0
        self.followed = self.lost = 0
        self._following = 0
        self._going: tuple[int, int] | None = None
        self.read = self.read_cost = 0  # the leader's lines put to system one, and what that cost
        self._failed = 0
        self._deaf_until = 0.0
        self.follow_tiles = FOLLOW_TILES
        # What the leader's word has made of its settings (cuo-cym.18); its starting ones are the fleet's.
        cfg = getattr(session, "pcfg", None)
        self.settings = standing.Standing(defaults={
            "helping": "on" if getattr(cfg, "help_mates", True) else "off",
            "raising": "on" if getattr(session, "raise_fallen", False) else "off",
            "callouts": "on" if getattr(link, "callouts", True) else "off"})
        self.readings: dict[int, Reading] = {}
        self.spoken: deque[tuple[float, str]] = deque(maxlen=12)  # the leader's lines, with when each was read
        self._still_at = -1e9
        # Sent somewhere (cuo-cym.19): the place, whether it has got there, the walks begun, the refusals.
        self.dest: dict[str, Any] | None = None
        self.arrived = False
        self.sent = self.refused = 0
        self._tries = 0
        self._refused_at = -1e9
        self._plays: str | None = None  # what it plays as, from its first look
        # The leader's word for the fight going on (cuo-cym.24): the creature it goes for, a fall back, a heal. Laid
        # over the fight loop's configuration with its settings (live), and ended by the fight's end or the clock.
        self.focus: dict[str, Any] | None = None
        self.falling_back: dict[str, Any] | None = None
        self.heal: dict[str, Any] | None = None
        self._heals = 0
        self.fight_orders: dict[str, int] = {}
        self._bestiary: dict[int, dict[str, Any]] | None = None

    def hears(self, ears: Any) -> None:
        """Its talk (talk.Ears): how much it talks is a setting, and what it says of a line goes by this reading."""
        self.ears = ears
        ears.reader = self.reading
        self.settings.defaults["talk"] = "chatty" if getattr(ears, "small_talk", False) else "answers"

    # ---- its word ---------------------------------------------------------------------

    async def utter(self, kind: str, plain: str, **slots: str) -> str:
        """A word aloud, of the kind in its own voice where it has one; on the floor as a callout, so the others
        hear it and don't answer it. The word, or "" when it says none."""
        if not self.acknowledge:
            return ""
        line = (self.voice.pick(kind, **slots) if self.voice is not None and kind else "") or plain
        await self.session.act("say", text=line)
        if self.floor is not None:
            self.floor.say(line, kind="callout")
        return line

    async def ack(self, order: str, **slots: str) -> str:
        """A word aloud on taking an order, so the leader sees at once how its line was read; the word, or ""."""
        return await self.utter(ACK_KIND[order], ACK[order], **slots)

    async def again(self, order: str) -> str:
        """A word on an order that is the one already in force (cuo-cym.20): without it a line misread as that
        order showed nothing, and the leader couldn't tell it had been read as an order at all. Not every time:
        on a walk "keep up" and "this way" come often."""
        if order not in STILL or time.monotonic() - self._still_at < STILL_GAP_S:
            return ""
        self._still_at = time.monotonic()
        return await self.utter(*STILL[order])

    # ---- hearing ----------------------------------------------------------------------

    def said(self, snap: dict[str, Any]) -> list[tuple[int, str]]:
        """What the leader has said in the journal since the last look: (the line's number there, the line)."""
        seq = snap.get("journal_seq", self.seq)
        if self.seq < 0 or seq < self.seq:  # the first look, or a client restarted: nothing said before now counts
            self.seq = seq
            return []
        lines = []
        for e in snap.get("journal") or []:
            if e.get("seq", 0) <= self.seq or e.get("source") != "speech" or e.get("type") not in SPOKEN:
                continue
            if e.get("name") != self.leader or (self.serial and e.get("serial") not in (0, self.serial)):
                continue  # only the leader, and once it is known, only that one of the name
            lines.append((e.get("seq", 0), e.get("text") or ""))
        self.seq = max(self.seq, seq)
        return lines

    def reads(self) -> bool:
        """Whether system one reads the leader's lines: there is a model, and its last two questions didn't both fail."""
        judge = getattr(self.session, "judge", None)
        return judge is not None and getattr(judge, "name", "") != "heuristic" and time.monotonic() >= self._deaf_until

    def reading(self, seq: int) -> Reading | None:
        """How the leader's line with this number in the journal was read, once it has been."""
        return self.readings.get(seq)

    async def keep(self, r: Reading) -> None:
        """A line read and acted on: from now its reading is there for whoever writes this companion's answer.
        In a party that doesn't talk nobody writes one: what it owes on the line (a setting changed, a refusal)
        it says now, in its plain words."""
        if r.owed and self.ears is None:
            r.said, r.owed = await self.utter("", r.plain), ""
        self.readings[r.seq] = r
        while len(self.readings) > READINGS_KEPT:
            del self.readings[next(iter(self.readings))]

    def earlier(self) -> list[str]:
        """The talk just before a line: the leader's last lines, and in a party that talks the companions' too."""
        now = time.time()
        lines = [(t, self.leader, text) for t, text in self.spoken if now - t <= EARLIER_S]
        for ln in getattr(self.ears, "lines", None) or []:
            if ln["who"] != self.leader and not ln.get("aside") and now - ln["t"] <= EARLIER_S:
                lines.append((ln["t"], ln["who"], ln["text"]))
        return [f"{who}: {text}" for _, who, text in sorted(lines)][-EARLIER_MAX:]

    def doing_now(self, at_table: bool = False) -> str:
        if at_table:
            return "sitting at the table with the party"
        if self.mode == "stay":
            return f"waiting where {self.leader} said to"
        if self.mode == "go" and self.dest is not None:
            return (f"waiting at {self.dest['says']} for {self.leader}" if self.arrived else
                    f"on the way to {self.dest['says']}, sent there by {self.leader}")
        return f"following {self.leader}"

    async def interpret(self, text: str, seq: int = 0, at_table: bool = False,
                        snap: dict[str, Any] | None = None) -> Reading:
        """One line of the leader's, read: the order in it for this companion ("stay", "follow", "table", "go") if
        any, taken at ORDER_CUT or more; the settings it changes, changed; in a party that talks, who it is for;
        and with creatures in view (in `snap`), an order for the fight itself (fight_question), which step()
        carries out. System one reads it (kind `order`); without a model, or for DEAF_S after two questions failed
        in a row, the phrases read the order (order_of) and nothing else is read."""
        me = self.party.me
        others = [m for m in self.party.members if m != self.leader]
        r = Reading(seq, text, time.time())
        if not text.strip():
            return r
        earlier = self.earlier()
        self.spoken.append((r.t, " ".join(text.split())[:200]))
        if not self.reads():
            r.order = order_of(text, me, others, at_table=at_table)
            return r
        judge = self.session.judge
        qs = {"order": order_question(me, self.leader, at_table)}
        if not at_table:
            qs["setting"] = standing.which_question(me, self.leader, self.settings.caster)

        async def ask(state: dict[str, Any], questions: dict[str, Any]) -> Any:
            with costs.kind("order"):
                return await judge.ask(state, questions)

        state = order_state(text, me, self.leader, others, self.doing_now(at_table), earlier)
        jobs = {"order": ask(state, qs)}
        companions = sorted({me, *others})
        plays = {**getattr(self.party, "companions", {}), me: self._plays or ""}
        whom = {"for": for_question(self.leader, companions, plays)}
        if self.ears is not None and not at_table:
            # What only its talk makes use of, each a request of its own beside the order's: whether the line asks
            # it for something, and (with others in the party) who the line is for.
            jobs["asked"] = ask(state, {"asked": asked_question(me, self.leader)})
            if len(companions) > 1:
                jobs["for"] = ask(for_state(text, self.leader, companions, earlier), whom)
        creatures = self.creatures(snap) if snap is not None and not at_table else []
        fqs: dict[str, Any] = {}
        if creatures:
            # An order for the fight itself, a request of its own beside the order's (cuo-cym.24), and which creature.
            fqs = {"fight": fight_question(me, self.leader), "target": target_question(me, self.leader, creatures)}
            agent = snap.get("agent") or {}
            doing = f"fighting beside {self.leader}" if self.mode == "follow" and (agent.get("engaged") or agent.get("fleeing")) \
                else self.doing_now()
            jobs["fight"] = ask(fight_state(text, me, self.leader, others, doing, creatures, earlier), fqs)
        got = dict(zip(jobs, await asyncio.gather(*jobs.values(), return_exceptions=True)))
        ans = got.pop("order")
        got = {k: v for k, v in got.items() if not isinstance(v, BaseException)}
        if isinstance(ans, BaseException):  # no answer: the phrases read this line
            self._failed += 1
            if self._failed >= 2:
                self._deaf_until = time.monotonic() + DEAF_S
            self.log({"type": "order_read", "t": time.time(), "from": self.leader, "text": text[:200],
                      "error": f"{type(ans).__name__}: {ans}"[:200]})
            r.order = order_of(text, me, others, at_table=at_table)
            return r
        self._failed = 0
        pick = ans.choices["order"]
        r.order, p, r.doubt = order_in(pick.probabilities or {pick.choice: pick.confidence})
        near = max(ORDERS, key=lambda o: pick.probabilities.get(o, 0.0))
        asked = float(got["asked"].nouls.get("asked", 0.0)) if "asked" in got else 0.0
        to_p: dict[str, float] = {}
        if "for" in got and "for" in got["for"].choices:
            to_p = for_whom(got["for"].choices["for"].probabilities, companions)
            r.to = [n for n in companions if to_p[n] >= FOR_CUT]
        # What a line with no order in it leaves this companion owing an answer for, when the line is for it (a
        # word for a vendor, "buy", was a request to all three by the yes/no, and for none of them).
        mine = r.to is None or me in r.to
        ours = mine or to_p.get(me, 0.0) >= FIGHT_FOR_CUT
        fight = self.fight_reading(r, got["fight"], creatures) if "fight" in got and ours else {}
        if r.fight == "heal":
            if len(companions) > 1:
                await self.whom_to_heal(r, text, earlier)
            if not r.whom:
                r.whom = heal_whom(text, me, self.leader, companions)
                if r.whom == me:
                    r.fight, r.whom = "", ""  # "someone heal Wystan", read by Wystan: no order it can carry out
            fight["whom"] = r.whom or None
        what = left_over(pick.probabilities, asked) if r.order is None and mine else ""
        if what == "asked" and not r.fight:
            # Asked for what it doesn't do at a word: its answer says so, whatever the yes/no on answering would
            # say. No plain word for it: in a fight, or without talk, it says nothing, and does what it does. The
            # yes/no takes a question for a request now and then ("any bandages left?"), so the writer is told
            # what to do with one. An order for the fight itself is answered by its carrying out (fight_order).
            r.owed = (f"{self.leader}'s line seems to ask {me} to do something. {me} fights what attacks the party and "
                      f"heals whoever is hurt without being told, and at {self.leader}'s word comes along, waits, goes "
                      f"on ahead to a place, or changes how they play, and in a fight goes for the creature "
                      f"{self.leader} names, falls back to {self.leader}, or heals someone at once; nothing else is "
                      f"done on command. If the line asks for something {me} doesn't do at a word, {me} says in their "
                      f"own way what they will and won't do, and promises nothing they won't do. If it only asks {me} "
                      f"a question, {me} answers it.")
        elif what == "unsure":
            # Asked for something, and nearly an order: it asks what is wanted. One read "off to the inn for you
            # all" as talk at 0.56 for being sent there, said "Off to the inn, is it? Fine", and stood where it was.
            r.owed = (f"{me} isn't sure what {self.leader} wants: it may have been the word to "
                      f"{ORDER_WORDS[near].format(leader=self.leader)}, and {me} hasn't taken it as that. {me} asks "
                      f"{self.leader} what they want, in their own way, and agrees to nothing meanwhile.")
        elif r.order is None and pick.probabilities.get(near, 0.0) >= NEAR_CUT:
            r.note = (f"unclear. It may have been the word to {ORDER_WORDS[near].format(leader=self.leader)}, and "
                      f"{me} didn't take it as that: {me} carries on as before and agrees to nothing")
        # The settings the line speaks to, each then asked about by itself (set_by). How close it keeps isn't one
        # of them when the line is itself an order: "stay close", "hang back here" and "everyone on me" were read
        # as both, and are orders.
        which = self.settings.spoken_to(ans.choices["setting"].probabilities) if "setting" in ans.choices else []
        which = [name for name in which if not (name == "distance" and r.order is not None)]
        self.read += 1
        self.read_cost += ans.cost + sum(a.cost for a in got.values())
        self.log({"type": "order_read", "t": time.time(), "from": self.leader, "text": text[:200], "order": r.order,
                  "p": round(p, 3), **({"at": "the table"} if at_table else {}), **({"settings": which} if which else {}),
                  **({"asked": round(asked, 3), "left": what} if "asked" in got else {}),
                  **({"for": r.to, "for_p": to_p} if r.to is not None else {}), **({"fight": fight} if fight else {}),
                  "answers": ans.to_log()})
        calls.emit({"kind": "order", "title": f"{self.leader}: {text}"[:80], "model": ans.model,
                    "latency_ms": round(ans.latency_ms, 1), "cost": round(ans.cost, 7),
                    "questions": calls.view(qs, ans, used={"order": r.order or "none",
                                                           "setting": which[0] if which else standing.NONE}),
                    "note": f"{r.order or 'no order'} ({p:.2f})" + (f", speaks to {', '.join(which)}" if which else "")
                            + (f", for {', '.join(r.to) or 'nobody'}" if r.to is not None else "")})
        if "fight" in got:
            fa = got["fight"]
            calls.emit({"kind": "order", "title": f"{self.leader}: {text}"[:80], "model": fa.model,
                        "latency_ms": round(fa.latency_ms, 1), "cost": round(fa.cost, 7),
                        "questions": calls.view(fqs, fa, used={"fight": r.fight or "none",
                                                               "target": (r.target or {}).get("id", "unclear")}),
                        "note": f"fight: {r.fight or 'no order'}" + (f" ({r.fight_p:.2f})" if r.fight else "")
                                + (f", {r.target['says']}" if r.target else "") + (f", {r.whom}" if r.whom else "")})
        if which and not await self.set_by(text, r, earlier, which) and what == "asked" and not r.fight:
            # It spoke to something the leader can change with a word, and changed nothing: that is no request for
            # what it doesn't do ("loot" got that), so it asks what is wanted.
            does = ", ".join(standing.SETTINGS[name].about for name in which)
            r.owed = (f"{me} isn't sure what {self.leader} wants: the line may have been about how {me} plays "
                      f"({does}), and {me} has changed nothing. {me} asks {self.leader} what they want, in their own "
                      f"way, and agrees to nothing meanwhile.")
        return r

    async def understand(self, text: str, at_table: bool = False) -> str | None:
        """The leader's order for this companion in a line of its speech, or None."""
        return (await self.interpret(text, at_table=at_table)).order

    async def set_by(self, text: str, r: Reading, earlier: list[str], which: list[str]) -> list[dict[str, Any]]:
        """A line that speaks to how this companion plays: a question for each setting it speaks to (`which`), and
        those it changes changed. Returns what it took from the line."""
        me = self.party.me
        others = [m for m in self.party.members if m != self.leader]
        qs = self.settings.questions(me, self.leader, only=which)
        try:
            with costs.kind("order"):
                ans = await self.session.judge.ask(self.settings.state(text, me, self.leader, others, earlier, which), qs)
        except Exception as e:  # noqa: BLE001 - no answer: nothing changes, and the log says so
            self.log({"type": "setting_read", "t": time.time(), "from": self.leader, "text": text[:200],
                      "error": f"{type(e).__name__}: {e}"[:200]})
            return []
        self.read_cost += ans.cost
        taken = self.settings.take(ans)
        for c in taken:
            self.switch(c["setting"], c["now"])
        if taken:  # what it owes the line is the change; with none made, what the line owed already stands
            r.owed, r.plain = self.settings.told(taken, me, self.leader)
        self.log({"type": "setting_read", "t": time.time(), "from": self.leader, "text": text[:200], "taken": taken,
                  "answers": ans.to_log()})
        for c in taken:
            if c["was"] != c["now"]:
                self.log({"type": "setting", "t": time.time(), "from": self.leader, **c})
        calls.emit({"kind": "order", "title": f"{self.leader}: {text}"[:80], "model": ans.model,
                    "latency_ms": round(ans.latency_ms, 1), "cost": round(ans.cost, 7),
                    "questions": calls.view(qs, ans, used={c["setting"]: c["now"] for c in taken}),
                    "note": ", ".join(f"{c['setting']}: {c['now']}" for c in taken) or "no setting changed"})
        return taken

    def switch(self, name: str, value: str) -> None:
        """The settings that aren't the fight loop's (those go through standing.Standing.apply)."""
        if name == "callouts" and self.link is not None:
            self.link.callouts = value == "on"
        elif name == "talk" and self.ears is not None:
            self.ears.muted, self.ears.small_talk = value == "silent", value == "chatty"
        elif name == "raising":
            self.session.raise_fallen = value == "on"
        elif name == "distance":
            self.follow_tiles = standing.FOLLOW_TILES[value]
            self._following = 0  # the follow is asked for again, at the new distance

    async def hear(self, snap: dict[str, Any]) -> list[Reading]:
        """The leader's lines in the journal since the last look, each read: the last HEAR_MAX of them."""
        return [await self.interpret(text, seq, snap=snap) for seq, text in self.said(snap)[-HEAR_MAX:]]

    # ---- orders for the fight itself (cuo-cym.24) ---------------------------------------

    def creatures(self, snap: dict[str, Any]) -> list[dict[str, Any]]:
        """The creatures in view this companion could be told to go for (creatures_in_view); none unless one of them
        could start a fight (state.threatening): with only a bird or a deer about, a line is no order for a fight."""
        if self._bestiary is None:
            try:
                self._bestiary = self.session.world.bestiary()
            except Exception:  # noqa: BLE001 - no store (a test's session): every kind counts
                self._bestiary = {}
        if not any(states.threatening(m, self._bestiary) and not m.get("pet") and not m.get("summon")
                   for m in snap.get("mobiles") or []):
            return []
        others = [m for m in self.party.members if m not in (self.leader, self.party.me)]
        return creatures_in_view(snap, self.party.me, self.leader, others)

    def fight_reading(self, r: Reading, fa: Any, creatures: list[dict[str, Any]]) -> dict[str, Any]:
        """The order for the fight in the answer to fight_question, into the reading: the kind at FIGHT_CUT or more,
        and for "attack" the creature target_question gives TARGET_CUT or more (None: it couldn't be told). Returns
        what the log keeps of it."""
        kind = fa.choices.get("fight")
        if kind is None:
            return {}
        p = kind.probabilities.get(kind.choice, kind.confidence)
        if kind.choice in FIGHT_ORDERS and p >= FIGHT_CUT:
            r.fight, r.fight_p = kind.choice, p
        out: dict[str, Any] = {"order": r.fight or None, "p": round(p, 3),
                               "probabilities": {k: round(v, 3) for k, v in kind.probabilities.items()}}
        if (t := fa.choices.get("target")) is not None:
            tp = t.probabilities.get(t.choice, t.confidence)
            c = next((c for c in creatures if c["id"] == t.choice), None) if tp >= TARGET_CUT else None
            out["target"] = {"pick": t.choice, "p": round(tp, 3), **({"name": c["name"], "serial": c["serial"]} if c else {})}
            if r.fight == "attack":
                r.target = c
        return out

    async def whom_to_heal(self, r: Reading, text: str, earlier: list[str]) -> None:
        """Whom a heal read in a line is for (whom_question): r.whom, or "" when it can't be told."""
        me = self.party.me
        companions = sorted(m for m in self.party.members if m != self.leader)
        q = {"whom": whom_question(me, self.leader, companions)}
        st = {"party": {"leader": self.leader, "companions": companions}, "companion": me,
              **({"said_before": earlier} if earlier else {}), "line": " ".join(text.split())[:200]}
        try:
            with costs.kind("order"):
                ans = await self.session.judge.ask(st, q)
        except Exception as e:  # noqa: BLE001 - no answer: the leader, who is the one who asks for a heal mostly
            r.whom = self.leader
            self.log({"type": "whom_read", "t": time.time(), "from": self.leader, "text": text[:200],
                      "error": f"{type(e).__name__}: {e}"[:200]})
            return
        self.read_cost += ans.cost
        pick = ans.choices["whom"]
        p = pick.probabilities.get(pick.choice, pick.confidence)
        r.whom = pick.choice if pick.choice != "unclear" and p >= WHOM_CUT else ""
        self.log({"type": "whom_read", "t": time.time(), "from": self.leader, "text": text[:200], "whom": r.whom or None,
                  "p": round(p, 3), "answers": ans.to_log()})

    async def fight_order(self, r: Reading, snap: dict[str, Any]) -> None:
        """An order for the fight itself, carried out and said back at once as the other orders are: "attack", the
        creature named gone for before anything else until it dies or leaves view, or for FOCUS_S; "fall_back", out
        of the fight and back to the leader, fighting only beside it, until the fight is over; "heal", one heal on
        someone whatever their health. What can't be carried out (which creature? whom? nothing to heal with) is
        owed an answer: a question back, or a no from one the line was for alone."""
        me, now = self.party.me, time.monotonic()
        rec: dict[str, Any] = {"type": "fight_order", "t": time.time(), "from": self.leader, "order": r.fight,
                               "p": round(r.fight_p, 3)}
        if r.fight == "attack":
            c = r.target
            if c is None or not any(m.get("serial") == c["serial"] and not m.get("dead") for m in snap.get("mobiles") or []):
                r.owed = (f"{self.leader} told {me} to go for a creature in this fight, and {me} can't tell which one "
                          f"from what was said. {me} asks which, in their own way, and fights on as before meanwhile.")
                r.plain = "Which one?"
                self.log({**rec, "none": "no creature it could tell"})
                return
            self.end_fight_order("fall_back", "another order")
            self.focus = {"serial": c["serial"], "name": c["name"], "says": c["says"], "t": now}
            if self.link is not None:
                self.link.target_called = c["serial"]  # its word is the callout: not "I've got the lich" again
            r.said = await self.utter("on_target", f"On {c['says']}.", target=c["says"])
            r.note = f"taken as the word to go for {c['says']} now, before anything else in this fight"
            rec |= {"target": c["serial"], "name": c["name"]}
        elif r.fight == "fall_back":
            self.end_fight_order("attack", "another order")
            self.falling_back = {"t": now, "calm": None}
            if self.mode == "go":
                await self.session.act("stop")  # a trip without the leader ends: it comes to the leader
            self.mode, self.dest, self._going, self._following = "follow", None, None, 0
            # In the voice it comes along in: its word for running from a fight ("leaving") read live as leaving the
            # leader ("I am leaving, and I am taking the good loot with me!", "Off you go, then. Good riddance").
            r.said = await self.utter("coming", "Falling back.")
            r.note = f"taken as the word to fall back out of the fight to {self.leader}"
        elif r.fight == "heal":
            whom, how = r.whom, can_heal(snap, self._plays or "")
            if not whom:
                r.owed = (f"{self.leader} told {me} to heal someone now, and {me} can't tell whom. {me} asks whom, in "
                          f"their own way.")
                r.plain = "Heal whom?"
                self.log({**rec, "none": "whom it couldn't tell"})
                return
            if not how:
                why = "no healing spell with the mana and reagents for it, nor bandages and the skill to use them"
                r.note = f"asked to heal {whom} now, and {me} can't: {why}"
                if r.to == [me]:  # for it alone: it says so; a line for all of them is a healer's to answer
                    r.owed = f"{self.leader} asked {me} to heal {whom} now, and {me} can't: {why}. {me} says so in their own way."
                    r.plain = "I can't heal."
                self.log({**rec, "whom": whom, "cannot": True})
                return
            self._heals += 1
            self.heal = {"name": whom, "id": self._heals, "t": now}
            r.said = await self.utter("helping", "Healing you." if whom == self.leader else f"Healing {whom}.", name=whom)
            r.note = f"taken as the word to heal {whom} now, whatever their health"
            rec |= {"whom": whom, "how": how}
        self.fight_orders[r.fight] = self.fight_orders.get(r.fight, 0) + 1
        self.log({**rec, **({"said": r.said} if r.said else {})})

    def end_fight_order(self, kind: str, why: str) -> None:
        held = {"attack": self.focus, "fall_back": self.falling_back, "heal": self.heal}[kind]
        if held is None:
            return
        if kind == "attack":
            self.focus = None
        elif kind == "fall_back":
            self.falling_back = None
        else:
            self.heal = None
        self.log({"type": "fight_order", "t": time.time(), "ended": kind, "why": why,
                  **({"name": held["name"]} if "name" in held else {}), "after_s": round(time.monotonic() - held["t"], 1)})

    def hold_fight_orders(self, snap: dict[str, Any]) -> None:
        """The leader's orders for the fight, ended once done with: the creature named dead or out of view, or after
        FOCUS_S; a fall back once nothing has been in war mode within FALL_BACK_CLEAR tiles for FALL_BACK_CALM_S (the
        fight is over), or after FALL_BACK_S; a heal after HEAL_S (the fight loop gives it once, Memory.heals_done)."""
        now = time.monotonic()
        mobiles = snap.get("mobiles") or []
        if self.focus is not None:
            if not any(m.get("serial") == self.focus["serial"] and not m.get("dead") for m in mobiles):
                self.end_fight_order("attack", "dead or out of view")
            elif now - self.focus["t"] >= FOCUS_S:
                self.end_fight_order("attack", "time")
        if (fb := self.falling_back) is not None:
            near = any(m.get("monster") and not m.get("dead") and not m.get("pet") and m.get("distance", 99) <= FALL_BACK_CLEAR
                       and (m.get("war_mode") or m.get("attacking_me")) for m in mobiles)
            fb["calm"] = None if near else fb["calm"] if fb["calm"] is not None else now
            if fb["calm"] is not None and now - fb["calm"] >= FALL_BACK_CALM_S:
                self.end_fight_order("fall_back", "the fight is over")
            elif now - fb["t"] >= FALL_BACK_S:
                self.end_fight_order("fall_back", "time")
        if self.heal is not None and now - self.heal["t"] >= HEAL_S:
            self.end_fight_order("heal", "time")

    def live(self, cfg: policy.PolicyConfig) -> policy.PolicyConfig:
        """The fight loop's configuration as the leader's word has made it: its settings (standing.py), and the
        orders for the fight going on, laid over at every decision (loop.run)."""
        cfg = self.settings.apply(cfg)
        if self.focus is None and self.falling_back is None and self.heal is None:
            return cfg
        return replace(cfg, focus=self.focus["serial"] if self.focus else 0, fall_back=self.falling_back is not None,
                       heal_now=(self.heal["name"], self.heal["id"]) if self.heal else ("", 0))

    # ---- the table --------------------------------------------------------------------

    def seats(self) -> list[str]:
        """Who sits at the table, in the party's order: this one, and every companion who took the leader's
        order for it in the last few seconds (each says so in the party store as it takes it). Who is near by
        the reports isn't who is sitting: one in a fight at that look doesn't take the order, and a line said
        to two of three leaves the third out."""
        took = {c["who"] for c in self.party.calls(within_s=HOST_WAITS_S + SEAT_S, mine=True, kind="seat")}
        return [n for n in self.party.members if n != self.leader and (n == self.party.me or n in took)]

    async def leader_at_table(self, up: asyncio.Event, keeper: bool) -> None:
        """While the table is open: the leader's order to move on ends this companion's evening (and its own
        death does), and the keeper of the turns puts whatever else the leader says on the table as anyone's line."""
        t = self.table
        others = [m for m in self.party.members if m != self.leader]
        while not up.is_set():
            if self.stopping is not None and self.stopping.is_set():
                break
            try:
                snap = await self.session.snap()
                lines = self.said(snap)
            except Exception:  # noqa: BLE001 - a look that failed: the next one
                snap, lines = {}, []
            if (snap.get("player") or {}).get("dead"):
                break  # killed at the table: its resurrection is its next step's
            for _, text in lines:
                if (order := await self.understand(text, at_table=True)) is not None:
                    self.mode = order
                    self.log({"type": "order", "t": time.time(), "from": self.leader, "order": order, "at": "the table"})
                    up.set()
                elif keeper and text.strip() and (tid := t.current()) is not None:
                    # Aimed at whoever it names, who answers next (table.Table.next_speaker).
                    words = text.lower().replace(",", " ").replace(".", " ").replace("!", " ").replace("?", " ").split()
                    to = next((n for n in others if n.lower() in words), "")
                    t.add(tid, "line", " ".join(text.split())[:200], to=to, who=self.leader)
            await asyncio.sleep(1.0)
        up.set()

    async def sit_down(self, snap: dict[str, Any], by: Reading | None = None) -> None:
        """The leader's word for the table: the party's talk over the hunt, where they stand."""
        from . import table as tables
        s, t = self.session, self.table
        me = self.party.me
        self.orders += 1
        self._following = 0
        self.dest = None
        self.party.call("seat")  # it took the order: the count of seats, and who keeps the turns, come from these
        await s.act("stop")
        said = await self.ack("table")
        if by is not None:
            by.said = said
            by.note = "taken as the word to sit down with the party for a drink"
            await self.keep(by)
        self.log({"type": "order", "t": time.time(), "from": self.leader, "order": "table", **({"said": said} if said else {})})
        self.doing = f"at the table with {self.leader}"
        self.mode = "follow"  # after the table, unless the leader's order that ended it said otherwise
        await t.arrive()
        await asyncio.sleep(HOST_WAITS_S)
        seats = self.seats()
        if len(seats) < 2:
            self.log({"type": "table", "t": time.time(), "none": "nobody else of the party took a seat"})
            return
        up = asyncio.Event()
        # The first of those sitting keeps the turns; one whose look came late finds the table open and sits at it.
        keeper = seats[0] == me and t.current() is None
        ears = asyncio.get_running_loop().create_task(self.leader_at_table(up, keeper))
        tid, held = None, {}
        try:
            if keeper:
                seat = asyncio.get_running_loop().create_task(t.sit(up, wait_s=20))
                try:
                    held = await t.host(seats, up)
                finally:
                    tid = await seat
            else:
                tid = await t.sit(up, wait_s=SIT_WAIT_S)
        finally:
            up.set()
            ears.cancel()
        if tid is None:
            self.log({"type": "table", "t": time.time(), "none": "no table was opened"})
            return
        self.tables += 1
        kept = await t.reflect(tid, t.area)
        t.story.reset()
        tables.learn(s, t)
        self.log({"type": "goal_result", "t": time.time(), "tool": "inn", "party": True,
                  "result": {"ok": True, "result": f"sat at the table with {self.leader}: said {t.spoken} lines, kept "
                                                   f"{len(kept)} memories of it", **held}})

    # ---- going somewhere without the leader -------------------------------------------

    def places(self, here: tuple[int, int]) -> list[dict[str, Any]]:
        """The places this companion could be sent to (places_near), by its own world store."""
        return places_near(getattr(self.session, "world", None), here)

    def moment(self, snap: dict[str, Any], place: dict[str, Any] | None) -> dict[str, Any]:
        """How things stand, for the yes/no on whether this companion would go off without the leader now."""
        p = snap.get("player") or {}
        agent = snap.get("agent") or {}
        lead = next((m for m in snap.get("mobiles") or [] if m.get("name") == self.leader and not m.get("monster")), None)
        hostile = [m for m in snap.get("mobiles") or [] if m.get("monster") and not m.get("dead") and not m.get("pet")
                   and m.get("war_mode") and m.get("distance", 99) <= 12]
        return {"own_health": f"{round(100 * p.get('hits', 0) / max(1, p.get('hits_max', 1)))}%",
                "leader": f"health {lead.get('hits_pct', 100)}%, {lead.get('distance', 0)} tiles away" if lead else "out of view",
                "in_a_fight": bool(agent.get("engaged") or agent.get("fleeing")),
                "hostile_creatures_close": len(hostile),
                **({"the_way_there": f"{place['tiles']} tiles to {place['says']}"} if place else {})}

    async def send_off(self, by: Reading, snap: dict[str, Any]) -> str | None:
        """The leader's word to go somewhere without it: where, whether this companion will, and off it goes.
        Comes back with "follow" when the line was taken as this in doubt (order_in) and names no place to be sent
        to: then it is an order to come along."""
        s, me = self.session, self.party.me
        here = s.where(snap)
        places = self.places(here)
        earlier = [ln for ln in self.earlier() if ln != f"{self.leader}: {' '.join(by.text.split())[:200]}"]
        place, willing, told = None, 1.0, False
        if places and self.reads():
            qs = {"place": place_question(me, self.leader, places)}
            if self.persuade:
                qs["willing"] = willing_question(me, self.leader)
            persona = getattr(self.ears, "persona", None) or {}
            state = {"party": {"leader": self.leader, "companions": sorted(m for m in self.party.members if m != self.leader)},
                     "companion": me, "who": "; ".join(f"{k} {v}" for k, v in persona.items() if k != "name") or "a companion",
                     "doing_now": self.doing_now(), "moment": self.moment(snap, None),
                     "refused_before": self.refused if time.monotonic() - self._refused_at < REFUSED_S else 0,
                     "said_before": earlier, "line": " ".join(by.text.split())[:200]}
            try:
                with costs.kind("order"):
                    ans = await s.judge.ask(state, qs)
            except Exception as e:  # noqa: BLE001 - no answer: the phrases say where, and it goes
                self.log({"type": "place_read", "t": time.time(), "from": self.leader, "text": by.text[:200],
                          "error": f"{type(e).__name__}: {e}"[:200]})
            else:
                told = True  # asked and answered: where it couldn't tell, the phrases aren't asked after it
                self.read_cost += ans.cost
                pick = ans.choices["place"]
                p = pick.probabilities.get(pick.choice, pick.confidence)
                place = next((x for x in places if x["kind"] == pick.choice and p >= PLACE_CUT), None)
                willing = float(ans.nouls.get("willing", 1.0))
                self.log({"type": "place_read", "t": time.time(), "from": self.leader, "text": by.text[:200],
                          "place": place["name"] if place else None, "p": round(p, 3),
                          **({"willing": round(willing, 3)} if self.persuade else {}), "answers": ans.to_log()})
                calls.emit({"kind": "order", "title": f"{self.leader}: {by.text}"[:80], "model": ans.model,
                            "latency_ms": round(ans.latency_ms, 1), "cost": round(ans.cost, 7),
                            "questions": calls.view(qs, ans, used={"place": place["kind"] if place else "unclear"}),
                            "note": (f"to {place['says']} ({p:.2f})" if place else "where to isn't clear")
                                    + (f", willing {willing:.2f}" if self.persuade else "")})
        if not told:  # no model, or no answer: the kind of place the line (or the talk before it) names
            kind = place_of(by.text) or next((k for ln in reversed(earlier) if (k := place_of(ln.split(": ", 1)[-1]))), None)
            place = next((x for x in places if x["kind"] == kind), None)
        going = self.mode == "go" and self.dest is not None
        if going and (place is None or self.dest["kind"] == place["kind"]):
            # Told again to go, to the same place or with none named ("off you go then"): it is going, and says so.
            by.said = await self.again("go")
            by.note = (f"told again to go, and {me} " + (f"already waits at {self.dest['says']}" if self.arrived else
                                                        f"is already on the way to {self.dest['says']}"))
            self.log({"type": "order", "t": time.time(), "from": self.leader, "order": "go", "already": True,
                      **({"said": by.said} if by.said else {})})
            return None
        if place is None and by.doubt:
            return "follow"  # an order to move with no place to be sent to in it: to come along, then
        if place is None:
            by.owed = (f"{self.leader} is sending {me} somewhere without them, and {me} can't tell where from what was "
                       f"said (or knows no such place near). {me} asks where, in their own way.")
            by.plain = "Go where?"
            self.log({"type": "order", "t": time.time(), "from": self.leader, "order": "go", "none": "no place it knows"})
            return
        if self.persuade and willing < WILLING_CUT:
            self.refused += 1
            self._refused_at = time.monotonic()
            by.owed = (f"{self.leader} asked {me} to go off to {place['says']} without them, and {me} won't, for now, "
                       f"for a reason of their own in how things stand ({self.words_of(self.moment(snap, place))}). {me} "
                       f"says no in their own way and says why. {me} stays with {self.leader}; asked again, {me} may go.")
            by.plain = "I'd rather stay with you for now."
            self.log({"type": "order", "t": time.time(), "from": self.leader, "order": "go", "refused": True,
                      "place": place["name"], "willing": round(willing, 3)})
            return
        self.mode, self.dest, self.arrived = "go", place, False
        self.orders += 1
        self.sent += 1
        self._following, self._tries, self._going = 0, 0, None
        by.said = await self.ack("go", place=place["says"])
        by.note = f"taken as the word to go to {place['says']} without {self.leader} and wait there"
        self.log({"type": "order", "t": time.time(), "from": self.leader, "order": "go", "place": place["name"],
                  "at": [place["x"], place["y"]], "tiles": place["tiles"], **({"said": by.said} if by.said else {})})

    @staticmethod
    def words_of(moment: dict[str, Any]) -> str:
        return ", ".join(f"{k.replace('_', ' ')}: {str(v).lower() if isinstance(v, bool) else v}" for k, v in moment.items())

    async def on_the_way(self, snap: dict[str, Any], here: tuple[int, int], lead: dict[str, Any] | None) -> None:
        """Sent somewhere: the walk there (the fight loop runs beside it), the wait, and the leader's coming."""
        s, d = self.session, self.dest
        agent = snap.get("agent") or {}
        if tiles(*here, d["x"], d["y"]) <= ARRIVE_TILES:
            if not self.arrived:
                self.arrived = True
                self._tries = 0
                said = await self.utter("arrived", "Here.", place=d["says"])
                self.log({"type": "order", "t": time.time(), "order": "go", "arrived": d["name"], **({"said": said} if said else {})})
            if lead is not None and lead.get("distance", 99) <= FALL_IN_TILES:
                # The leader has come: this companion is its again.
                self.mode, self.dest = "follow", None
                self._following = 0
                said = await self.ack("follow")
                self.log({"type": "order", "t": time.time(), "from": self.leader, "order": "follow",
                          "why": "the leader came to where it waited", **({"said": said} if said else {})})
            else:
                self.doing = f"waiting at {d['says']} for {self.leader}"
            return
        if agent.get("engaged") or agent.get("fleeing"):
            self.doing = f"fighting on the way to {d['says']}"
            return
        self.doing = f"on the way to {d['says']}, sent there by {self.leader}"
        travel = agent.get("travel") or {}
        mine = self._going == (d["x"], d["y"])
        if travel.get("state") == "walking" and mine:
            return
        # A walk that ended short of the place with no way on counts against the trip; one a fight broke off doesn't.
        self._tries += mine and travel.get("state") in ("stuck", "no route")
        if self._tries >= GO_TRIES:
            # It can't get there: back to the leader, and it says so.
            self.mode, self.dest = "follow", None
            self._going = None
            said = await self.utter("", f"Couldn't get to {d['says']}.")
            self.log({"type": "order", "t": time.time(), "order": "go", "gave_up": d["name"], "after": self._tries,
                      "travel": {k: travel.get(k) for k in ("state", "why", "stuck_at") if travel.get(k)},
                      **({"said": said} if said else {})})
            return
        self._going = (d["x"], d["y"])
        self._following = 0
        res = await s.act("travel", x=d["x"], y=d["y"], distance=ARRIVE_TILES - 1, pace="run")
        self._tries += res.get("status") != "done"

    # ---- a look -----------------------------------------------------------------------

    async def step(self) -> None:
        s = self.session
        snap = await s.snap()
        if not snap.get("in_game"):
            await asyncio.sleep(5)
            return
        if snap["player"].get("dead"):
            # Its own business, as a follower's: resurrected, back for its corpse, then back to the leader.
            self.doing, self.own = "dead: being resurrected", True
            self._following = 0
            for kind in FIGHT_ORDERS:
                self.end_fight_order(kind, "it died")
            try:
                self.log({"type": "goal", "t": time.time(), "tool": "resurrect", "args": {}, "party": True})
                r = await s.resurrect()
                self.log({"type": "goal_result", "t": time.time(), "tool": "resurrect", "result": r.to_tool(), "party": True})
                if r.ok:
                    self.doing = "going back for its corpse"
                    c = await s.recover_corpse(go_anyway=True)
                    self.log({"type": "goal_result", "t": time.time(), "tool": "recover_corpse", "result": c.to_tool(),
                              "party": True})
            finally:
                self.own = False
                # What was said while it was dead and away isn't an order for now: the last "sit down" among them
                # would have sat it down on its return.
                try:
                    self.seq = (await s.snap()).get("journal_seq", self.seq)
                except Exception:  # noqa: BLE001 - the next look sets it
                    pass
            return
        if self._plays is None:
            self._plays = getattr(s, "archetype", None) or self.plays(snap)
            self.settings.caster = "mage" in self._plays  # a spell to open with is asked only of one who casts
        self.hold_fight_orders(snap)
        readings = await self.hear(snap)
        by = next((r for r in reversed(readings) if r.order is not None), None)
        # An order for the fight itself (cuo-cym.24), the last of them, unless its line sends this one away or sits
        # it down: those orders are the line's, and the fight isn't its business there.
        fight = next((r for r in reversed(readings) if r.fight), None)
        if fight is not None and fight.order in ("go", "table"):
            fight = None
        for r in readings:
            if r is not by and r is not fight:
                await self.keep(r)
        if fight is not None:
            try:
                await self.fight_order(fight, snap)
            finally:
                if fight is not by:
                    await self.keep(fight)
        order = by.order if by is not None else None
        try:
            if order == "table":
                if self.table is not None and not (snap.get("agent") or {}).get("engaged"):
                    await self.sit_down(snap, by)
                    by = None  # kept as it sat down, before the evening
                else:
                    by.note = "asked to sit down for a drink: not in a fight, and not without a table to hold"
                return  # with no talk there is no table to hold; in a fight, not now
            if order == "go":
                order = await self.send_off(by, snap)
                by.order = order or "go"
            if order is None:
                pass  # no order; or sent somewhere, and that is seen to
            elif order != self.mode:
                if order == "stay" or self.mode == "go":
                    await s.act("stop")  # a wait begins where it stands; a walk somewhere without the leader ends
                self.mode, self.dest = order, None
                self.orders += 1
                self._following = 0
                self._going = None
                by.said = await self.ack(order)
                by.note = f"taken as the word to {'wait here' if order == 'stay' else 'come along'}"
                self.log({"type": "order", "t": time.time(), "from": self.leader, "order": order,
                          **({"said": by.said} if by.said else {})})
            elif by is fight and by.said:
                pass  # "back to me!": the word on falling back (or going for one) was its answer to the line
            else:
                by.said = await self.again(order)
                by.note = f"taken as the word to {'wait here' if order == 'stay' else 'come along'}, which it was doing already"
                self.log({"type": "order", "t": time.time(), "from": self.leader, "order": order, "already": True,
                          **({"said": by.said} if by.said else {})})
        finally:
            if by is not None:
                await self.keep(by)
        here = s.where(snap)
        lead = next((m for m in snap.get("mobiles") or [] if m.get("name") == self.leader and not m.get("monster")
                     and not m.get("dead") and (not self.serial or m["serial"] == self.serial)), None)
        if lead is not None:
            if not self.serial:
                self.serial = lead["serial"]
                if self.game is not None:
                    self.game.leader_serial = self.serial
                self.log({"type": "companion", "t": time.time(), "leader": self.leader, "serial": self.serial})
            self.seen_at = (here[0] + lead.get("dx", 0), here[1] + lead.get("dy", 0))
        agent = snap.get("agent") or {}
        if self.mode == "stay":
            self.doing = f"staying where {self.leader} said to"
        elif self.mode == "go" and self.dest is not None:
            await self.on_the_way(snap, here, lead)
        elif agent.get("engaged") or agent.get("fleeing"):
            self.doing = f"fighting beside {self.leader}"
            self._following = 0  # a fight's own walking ends the follow: taken up again after it
        elif lead is not None:
            self.doing = f"with {self.leader}"
            self._going = None
            state = (agent.get("follow") or {}).get("state")
            if self._following != lead["serial"] or state in (None, "lost"):
                res = await s.act("follow", target=lead["serial"], distance=self.follow_tiles)
                self._following = lead["serial"] if res.get("status") == "done" else 0
                self.followed += bool(self._following)
        elif self.seen_at is not None and tiles(*here, *self.seen_at) > LOST_TILES:
            self.doing = f"going to where {self.leader} was last seen"
            walking = (agent.get("travel") or {}).get("state") == "walking"
            if self._going != self.seen_at or not walking:
                self._going = self.seen_at
                self._following = 0
                self.lost += 1
                await s.act("travel", x=self.seen_at[0], y=self.seen_at[1], distance=LOST_TILES - 1, pace="run")
        else:
            self.doing = f"waiting for {self.leader}"

    @staticmethod
    def plays(snap: dict[str, Any]) -> str:
        """What the character plays as, for the settings only a caster has (a spell to open with)."""
        from . import state
        try:
            return state.archetype_of(snap) or ""
        except Exception:  # noqa: BLE001 - a snapshot without skills (a test's): no caster
            return ""

    async def run(self, stop: asyncio.Event, hours: float) -> dict[str, Any]:
        s = self.session
        began = time.monotonic()
        self.stopping = stop
        fight = None
        if s.judge is not None:
            # The fight loop the whole time, as a companion's: what attacks it, and what is on the leader. What
            # the leader's word has changed in its settings, and its orders for the fight going on, are laid over that
            # at every decision.
            fight = asyncio.get_running_loop().create_task(loop.run(
                s.rpc, s.judge, loop.LoopConfig(),
                replace(s.pcfg, escort=self.leader, looting="nothing", live=self.live),
                s.decisions_log, stop, archetype=s.archetype, on_snapshot=s.seen, bestiary=s.world.bestiary()))
        try:
            while not stop.is_set() and time.monotonic() - began < hours * 3600:
                try:
                    await self.step()
                except Exception as e:  # noqa: BLE001 - a step that broke (a lost client, a busy store): the next one
                    self.log({"type": "companion", "t": time.time(), "error": f"step: {type(e).__name__}: {e}"[:300]})
                    await asyncio.sleep(2.0)
                await asyncio.sleep(LOOK_S)
        finally:
            # Which it was: a run stopped by hand two minutes into its hour said "time up" (cuo-cym.23).
            finished = "stopped" if stop.is_set() else "time up"
            stop.set()
            if fight is not None:
                try:
                    await asyncio.wait_for(fight, 5)
                except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                    fight.cancel()
        return {"orders_taken": self.orders, "follows_begun": self.followed, "lost_the_leader": self.lost,
                "tables_sat_at": self.tables, "leader_serial": self.serial, "lines_read": self.read,
                "lines_read_cost_usd": round(self.read_cost, 5), "sent_off": self.sent, "refused_to_go": self.refused,
                "fight_orders_taken": dict(self.fight_orders), **self.settings.to_log(), "finished": finished}
