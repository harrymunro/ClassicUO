<h1 align="center">ClassicUO with a Jev agent</h1>

<p align="center">
  <b>An AI agent inside the Ultima Online client.</b><br>
  It fights beside you, or plays on its own towards a goal you give it in words.
</p>

<p align="center">
  <img alt="macOS on Apple Silicon" src="https://img.shields.io/badge/macOS-Apple%20Silicon-111111?logo=apple&logoColor=white">
  <img alt=".NET 10, NativeAOT" src="https://img.shields.io/badge/.NET%2010-NativeAOT-512BD4?logo=dotnet&logoColor=white">
  <img alt="Python 3.13 or later" src="https://img.shields.io/badge/Python-3.13%2B-3776AB?logo=python&logoColor=white">
  <img alt="Tested on ModernUO" src="https://img.shields.io/badge/tested%20on-ModernUO-8B5A2B">
  <a href="LICENSE.md"><img alt="BSD 2-Clause licence" src="https://img.shields.io/badge/licence-BSD%202--Clause-2F6FB0"></a>
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="#playing-with-the-agent">Playing</a> ·
  <a href="#goals-and-the-planner">Goals</a> ·
  <a href="#results">Results</a> ·
  <a href="#reference">Reference</a>
</p>

<p align="center">
  <img src="docs/images/jev-mage-fight.png" width="900" alt="A test mage fighting in the arena. Above a headless one the client shows jev: target. The agent panel on the right shows the play state set to auto, the agent casting Explosion, and Jev's judgment: fight 99%, danger 5%, target a headless one at 100%, spell Explosion at 34%, answered in 250 ms.">
  <br>
  <sub>A test mage in the arena on auto. Jev picked the headless one ("jev: target") and Explosion; the panel shows its judgment, answered in 250 ms.</sub>
</p>

This fork of [ClassicUO](https://github.com/ClassicUO/ClassicUO) adds an AI agent that can
play alongside you or play on its own. Decisions come from [Jev](https://docs.typesafe.ai),
TypeSafe's "System One" model, which answers typed questions (pick one of these options, yes
or no, rate this) with probabilities in about 100 ms. Since 2026-10-12 most of those questions,
the fights among them, go to Microsoft's Decision 1, a model of the same kind that played the fights
better; Jev keeps the calls it measured better at ([which model answers](#which-model-answers)).
Ordinary code does everything that has a right answer: healing thresholds, pathfinding, looting mechanics, safety rules.

| Feature | What it does |
|---|---|
| **Combat assist** | You drive. It fights beside you, keeps you healed, and does Jev's next move on a key. [Play states](#play-states-and-keys) |
| **Auto, towards a goal** | "Hunt the undead at the Britain graveyard, bank the gold": a planner model (Claude Haiku) chooses each step (travel, hunt, bank, buy, sell) and Decision 1 fights, with Jev's calls on when to move on. [Goals](#goals-and-the-planner) |
| **Character types** | Warriors, mages, archers, tamers and bards, two hybrids, and on AOS-era shards necromancers and paladins, told apart from the skills. [Character types](#character-types) |
| **Plans for fights** | The planner can give the fights a shape for the place and the character (pull one at a time, back off to heal, hit a crowd with area spells) as a small state machine, and Jev runs it. [Plans](#plans-for-fights) |
| **Strategy in your words** | "Never flee, finish the weakest first": Jev weighs it in every decision, and code enforces what it compiles to. [Strategy](#strategy-in-your-own-words) |
| **The agent panel** | What Jev is thinking, in game: its probabilities, choices and actions for every decision, and a live view of every model call with the options and Jev's pick. [Panel](#the-agent-panel) |
| **World knowledge** | Places, spawns, creatures, routes and past hunts for each shard, with Jev picking the facts that matter. [World knowledge](#world-knowledge) |
| **Memory and long goals** | Each character keeps what happened to it and what it learned (leaves, deaths, refused goals, the planner's lessons) across sessions, and works towards long goals (a skill, gold to save, places to learn) with progress measured by code. [Memory](#the-characters-memory) |
| **Measured** | A judgment benchmark measures where Jev beats fixed rules, with scenarios built so the obvious rule gets them wrong. [Results](#judgment-benchmark) |

> [!NOTE]
> **Status:** works end to end against a local ModernUO server with Jev deciding through
> OpenRouter, for all of the character types. Not yet tried on a public shard.

## Quick start

You need macOS on Apple Silicon, the .NET 10 SDK, the Xcode command line tools (NativeAOT
links with clang), [uv](https://docs.astral.sh/uv/) (the brain needs Python 3.13 or later)
and an OpenRouter key, which Jev and the planner share. No Windows machine is needed.

```bash
# 1. Build the client
git submodule update --init --recursive
dotnet publish src/ClassicUO.Client/ClassicUO.Client.csproj -c Release -r osx-arm64 -o bin/osx-arm64

# 2. Game files: the official UO client, straight from EA's patch servers
python3 tools/uo-download/download_uo.py --out ~/Workspace/UOClassic

# 3. Local test server (ModernUO in ~/Workspace/ModernUO; setup in tools/modernuo/README.md)
~/Workspace/ModernUO/start-agent-server.sh

# 4. Model key, saved to brain/.env (gitignored). OpenRouter is used when present.
#    Or skip this: copy the key and click "paste key" in the agent panel.
brain/set-openrouter-key.sh          # prompts without echoing; or: pbpaste | brain/set-openrouter-key.sh

# 5. Play. Run the client from its folder: settings.json is read from the current directory.
cd brain && uv sync && cd ..
(cd bin/osx-arm64 && ./cuo -agent_port 5577 &)
# For an NPC or a benchmark lane, a client without a window: add -headless (see "Without a window")
```

> [!IMPORTANT]
> The first launch writes `bin/osx-arm64/settings.json` and stops, because it has no client
> version yet. Set these, then launch again:
>
> ```json
> "ultimaonlinedirectory": "/Users/<you>/Workspace/UOClassic",
> "clientversion": "7.0.117.1",
> "plugins": []
> ```

Accounts on the test server are created on first login (`admin`/`admin` is the owner). In
game, **Alt+A** or the panel turns the agent on, and that starts the brain for you.

### Driving it from a terminal

Instead of the panel, e.g. for the arena:

```bash
cd brain
uv run uo-brain login --account warrior --password warrior --create-warrior Brutus
uv run uo-brain say "[AgentGo"; uv run uo-brain say "[AgentKit"; uv run uo-brain say "[AgentArena 6"
uv run uo-brain run --mode auto --log logs/run.jsonl

# Or a mage, with a ready-made strategy
uv run uo-brain say "[AgentKit mage"; uv run uo-brain strategy template nuker
uv run uo-brain scenario --kit mage --monsters 4 --log logs/mage.jsonl
```

With no model key, `--judge heuristic` runs the same loop on fixed rules (the baseline). The
`[Agent…` commands come from the test server ([list](#test-server-commands)).

### The client starts the brain

When the agent is turned on and nothing is connected to
its port, the client runs `uv run uo-brain run` in the repository's `brain/` folder,
restarts it if it exits (waiting longer each time), and stops it when the agent is turned
off or the client closes. Its output goes to `brain/logs/client-brain.log`, its decisions
to `brain/logs/auto-<date>.jsonl`. A brain you start in a terminal takes precedence. In
settings.json, `agent_start_brain: false` turns this off (or `-agent_start_brain false`),
and `agent_brain_dir` points at a brain folder elsewhere. The *paste key* link saves the
clipboard to `brain/.env` (mode 600) and restarts the brain; the key is never shown.

**Razor and other plugins:** `scripts/build-naot.sh` builds upstream's release layout instead (osx-x64, the client as a
library loaded by the net472 `ClassicUO.Bootstrap` host). That is only needed for managed
assistant plugins such as Razor.

## How it works

```mermaid
flowchart LR
    client["<b>ClassicUO client</b> · C#<br/>src/ClassicUO.Client/Agent<br/><br/>reflexes every frame:<br/>bandage, heal, cure<br/>acts: pursue, loot, flee<br/>authority per behaviour<br/>pauses while the player<br/>moves or clicks<br/>agent panel, macros and<br/>-agent commands"]
    brain["<b>uo-brain</b> · Python<br/>brain/<br/><br/>state in words<br/>(badly wounded, adjacent)<br/>policy: mask, confidence<br/>gate, strategy<br/>reports to the panel, logs"]
    jev(["<b>Jev</b><br/>OpenRouter or TypeSafe"])
    client -- "snapshot every 250 ms" --> brain
    brain -- "actions, over RPC" --> client
    brain -- "one request, all questions:<br/>when something changed,<br/>or every 1–3 s (up to 8 s<br/>while all is quiet)" --> jev
    jev -- "answers +<br/>probabilities" --> brain
```

1. **The client snapshot** gives the player, the creatures and corpses within 18
   tiles, new journal lines and the agent's own state.
2. **The brain describes the situation in words.** Jev reads "badly wounded,
   adjacent, to the east" far more reliably than raw numbers and coordinates. How
   strong each creature is comes from its stats in the world store ("an ogre lord, far
   stronger than you: do not fight it alone", "a spellcaster", "rooted to the spot and can't
   follow you", "only fights back if attacked"), and supplies are worded
   too ("running low: 12 bandages and 1 heal potion left").
   Candidates get short ids (`t1`, `c1`, `i1`) so the model can only pick things
   code has already checked. Other players' speech is never sent to the model.
   Up to three facts from the world store that Jev picked for this place go in as
   `what_you_know_about_this_place` ([below](#which-facts-reach-the-decisions)).
3. **One Jev request asks every question that could matter**:
   - `intent`: fight, flee (for a moment), leave (the area: run until nothing is in sight), loot, seek or rest
   - `in_danger`: will the character die soon if it keeps fighting?
   - `leave_now`, only when there's a reason to (a far stronger creature within 12 tiles, a pack coming at it that together outweighs it, supplies nearly gone with two or more creatures close, or picked world facts with a creature in view): should it leave now? Jev judges this better as its own yes/no than as one of six intents: with an ogre lord adjacent it still gave fight 80%.
   - `target`: which creature to attack, or none of these. A "none" at 0.5 or more is taken as an answer (unless something is on the character): the character doesn't fight, and doesn't go looking for a creature either, however sure the intent is of seeking. A creature that two walks to seek it found no path to is left out for the rest of the hunt, unless it comes at the character (cuo-20l): a fleet NPC once sought a sewer rat under the street for six minutes, every walk "no path". The client gives up on attacks the same way (cuo-2ct).
   - `spell` (mages): which attack spell to cast next
   - `corpse`: which corpse to loot first
   - `take_iN`: is this item worth picking up?
   - `blessing` (paladins): which chivalry blessing to use in this fight
   - `stand`, with `--spots jev` and only when a run could come: where to go if it runs, leaves or steps back, from about 50 places the client offers ([where it runs](#where-it-runs))
   - with a [plan](#plans-for-fights), `go_<step>`: should it move on to that step now? Only the current step's moves are asked.

   Code uses the answers that apply and ignores the rest. Something new coming at the
   character counts as a change, so the next decision is asked at once rather than at the
   next second's tick.

   **Jev isn't asked when its answer can't matter or can't be new** (cuo-df8):
   - **Nothing to choose:** no creature, corpse or item in view, and nothing asked but what next
     and the danger. The policy could only rest whatever Jev said.
   - **The same state:** worded exactly as at the last call, with the same questions. That call's
     answers are used again, for up to a minute, but never after an unsure answer (that is asked
     again, as before). Jev's answers to the same state vary only a little (by 0.01 on average,
     at most 0.2, in a fleet's logs).
   - **All quiet:** not fighting, no pet fighting, nothing within 7 tiles or coming at the
     character, health not falling. Each decision on the clock that did nothing doubles the wait
     for the next, up to 8 s, and the quiet ending (a creature within 7 tiles, a fight, a fall in
     health) decides at once.
   - **Defending only** (the fight loop beside travel, rest and errands, which never seeks or
     loots): a creature that comes into view counts as a change once it is within 7 tiles or in
     war mode, and a corpse doesn't count. Walking through town, every cat, dog and bull that came
     into view was a call.

   The policy still decides each time, on the last answers or none, so its own rules and timers
   (leaving, songs, meditation, a plan's time limits) run as before, and the client's reflexes
   don't wait on the brain. `UO_BRAIN_ASK_EVERY_TICK=1` in the brain's environment asks Jev at
   every tick and never stretches the clock, as before.
4. **The policy is code.** It masks options the facts rule out (no looting with a
   monster adjacent), requires the intent and the danger judgment to agree before
   fleeing, keeps its current course when confidence is low, and applies your
   strategy settings. Its own emergency flee runs when the character is below 25% health,
   Jev is at least 0.9 sure of the danger and no heal potion can be drunk in the next two
   seconds; it keeps running while anything aggressive is within 8 tiles, rather than turning
   back once the first run has opened a gap.
5. **The client has the last word.** It checks the behaviour's authority before
   anything happens, and the brain may only ever attack monsters.

## Playing with the agent

### Play states and keys

There are two ways to play, and one key between them:

- **Combat assist:** you drive. The agent fights alongside you and never walks the character.
- **Auto:** the agent plays on its own.
- **Off:** nothing runs.

**Alt+A** switches between combat assist and auto (from off it starts combat assist), and
**Alt+D** does Jev's next move. Both are ordinary macros (*Agent: switch* and *Agent: next
move*) added once per character; rebind or delete them in Options → Macros. A key you
already use is left alone, and the macro is added without a key.

**Combat assist** follows your target: it attacks, or casts at, whatever you attacked or
targeted. A setting chooses what else it takes on by itself (`-agent engage`, or the
*engages* row in the panel):

| setting | it engages |
|---|---|
| `follow` | only what you attack |
| `defend` (default) | that, and anything attacking you |
| `nearby` | any monster within 8 tiles |

- **A warrior** keeps swinging and bandages between hits; this works with no brain running.
- **A mage** casts the next spell from your strategy, or Jev's pick, at your target, and says above your head when the target is out of spell range.
- **It never walks or flees for you:** when Jev judges the fight is going badly, it says so ("this fight is going badly, get out") and leaves the moving to you.
- **On your key:** set *fights* to *on your key* (`-agent set fight suggest`) and Jev only picks the move; Alt+D does it.

**The next-move key** (Alt+D, `-agent next`) does Jev's pending suggestion if there is one,
otherwise the combat move from its latest decision (a cast, a target switch), otherwise a
bandage if you're hurt. You decide when, Jev decides what.

**Auto** is fully autonomous play. Give it a goal and it works towards it
([Goals and the planner](#goals-and-the-planner)).

**Handing over control:**
- **In auto,** moving, clicking in the world or using the arrow keys is a short override: control comes back to you for 4 seconds, any walk the agent started stops, and only the healing reflexes keep running. The Alt+A key is the real switch.
- **In combat assist,** your input only pauses the agent's own movement (looting a corpse, if you accepted it). The fight goes on while you walk, and a spell it is casting still goes off at its target unless you clicked in the world.
- **Your target sticks:** if you attack a different monster yourself, the agent follows your choice.
- **Your spell cursors are yours:** if you click in the world while one of the agent's attack spells is being cast, its target cursor is left to you.
- **Panel clicks don't count:** clicking the panel doesn't pause the agent.

The hand-back and the keys were checked with real mouse and keyboard events ([results](#real-input)).

**Authority per behaviour.** Each behaviour has its own authority: `off`, `suggest` or
`auto`. The play states are presets:

| state | heal / cure / potion | fight | loot / misc | move |
|---|---|---|---|---|
| `off` | off | off | off | off |
| combat assist (`assist`) | auto | auto | suggest | off |
| `auto` | auto | auto | auto | auto |

- **Overrides:** change any single behaviour, e.g. `-agent set fight suggest` while in combat assist.
- **Behaviours:** `heal` (bandages), `cure` (cure potions), `potion` (heal potions), `fight`, `loot`, `move`, `misc`.
- **Saving:** settings are saved per character. A character saved in the old assist mode starts in combat assist.

### The agent panel

Jev's thinking is shown inside the game, in a UO-style panel that opens beside the game view
whenever the agent is on or a brain is connected. It shows:

- **Play state buttons:** off, combat assist and auto, with the keys that switch between them and do Jev's next move.
- **Combat assist settings:** what it engages (your target, also attackers, or anything near) and whether it fights on its own or waits for your next-move key.
- **The goal** (auto mode): what you asked for, the step the planner is on and why, *pause*/*resume* and *clear*, an entry box, and the goal templates (see [Goals and the planner](#goals-and-the-planner)).
- **The brain's state** in the title row ("brain starting", "brain running"), then the character type and judge it is playing with ("warrior · jev/openrouter") from the moment it connects, goal or no goal, and a *paste key* link while there is no model key.
- **Watch out:** a red, criminal or unknown player within 8 tiles.
- **What the agent is doing:** for example "fighting Vorgak", "casting Explosion" or "bandaging", and "you're driving" or "you have the controls" while you're playing.
- **Jev's judgment** for the latest decision:
  - a bar for each intent with Jev's probability, plus options the facts ruled out;
  - the danger judgment;
  - the target and spell it picked, with confidence, or "your strategy" when your strategy chose the spell;
  - what was done, and the latency, or why Jev wasn't asked ("not asked: same state as the last call", "nothing to choose").

  While the calls window (below) is open it has all of this, question by question, so the panel keeps one line for the decision ("fight a gargoyle with Explosion (0.83)") instead of showing the same bars twice.
- **A pending suggestion** with an *accept* link.
- **Your strategy and how Jev read it**, an entry box to add a line, and the template links (see [Templates](#templates)).
- **Earlier decisions**, with repeats collapsed ("fight an orc x5").
- **Kills, deaths, heals and casts.**

When Jev picks a new target, "jev: target" appears above that creature in the world. *less*
collapses the panel to a few lines; its position and whether it is collapsed are saved per character.

**Jev's calls, live.** The *calls* link in the panel's title row (or `-agent calls`) opens a
window beside it that shows every model call as it happens, not just the latest intent:

- **The latest fight decision, question by question:** what next, which creature, which spell or blessing, and so on, each option as a bar with Jev's probability, its pick marked *jev* in green and, where code went with something else (a code rule, your strategy's spell, an unsure answer), that marked *code* in gold. A yes/no (in danger?, leave now?, a [plan](#plans-for-fights)'s next step?) is one bar with the cut that decides it in red, and the verdict.
- **What was done about it:** each action of that decision in words, with the way a move went: "run 15 tiles northwest from all in sight", "step back 4 tiles south", "walk 12 tiles east", "attack a gargoyle", "cast Explosion at a lich", and the result when it didn't simply go through (refused, out of reach).
- **The latest of each other kind of call:** Jev's routine questions inside a hunt with its verdict, its pick of world facts (each fact with its score, the kept ones in green), a strategy read, and the planner's goals and world queries with their reason and cost.
- **How many calls of each kind** there have been, and what they have cost: the total, the rate an hour, the dearest kinds of call, and the budget if one is set (in red once it's over). Each call's header gives its own cost. A fight decision Jev wasn't asked for says why in its header instead, and doesn't count as a call.

<p align="center">
  <img src="docs/images/jev-calls.png" width="660" alt="The calls window beside the agent panel while a test mage fights six monsters. The fight decision: what next, fight at 99% picked by Jev; in danger at 21% and leave now at 23%, both under their red cuts; which creature, a ratman at 99%; which spell, Mass Curse at 44% picked by Jev over Explosion at 40%. Under actions: attack the ratman, and cast Mass Curse at another ratman in the middle of the crowd. The panel says now: casting Mass Curse, and gives Jev's judgment as one line, with the details in the calls window.">
</p>

<p align="center">
  <img src="docs/images/jev-calls-actions.png" width="660" alt="The calls window during a pack round, beside the agent panel, which says now: fleeing. What next: fight at 81% picked by Jev, leave at 6% marked code in gold, and the line 'jev picked fight; code went with leave'. In danger at 26%, no; leave now at 50%, over its red cut, yes. Which creature: an orc at 78%, picked by Jev. Under actions: run 15 tiles southeast from all in sight. Then: so: leave, away from all 4 in sight. The panel gives Jev's judgment as that one line.">
</p>

### Reflexes

The client runs these without the brain:

- **Bandages:** bandage yourself below 85% health, or when poisoned.
- **Heal potion:** drink one below 40%, with the 10-second cooldown respected.
- **Cure potion:** drink one when poisoned and hurt.
- **Healing spells** (characters with no bandages): Heal below 65%, Greater Heal below 50%, and Cure when poisoned if no cure potion is ready.
- **A paladin's prayers:** Close Wounds below 50%, bandages or not (a bandage takes seconds), and Cleanse by Fire when poisoned with no cure potion ready.

Adjust them with `-agent bandage 80` and `-agent potion 35`. They're also
available on their own: combat assist with no brain running heals you and keeps a warrior swinging.

### Safety rules

These are in code, whatever the model says:

- The brain only attacks monsters: grey or red, not human, not a pet.
- Other players' speech goes to neither the model nor the reflexes.
- Actions are a fixed list.
- Gold, bandages and potions are always taken; anything else needs a "worth taking" judgment.
- Other players are never attacked, never named to the model (they appear as "a red player", "a criminal player" or "another player") and never named in the world store.
- The brain only loots corpses of creatures the client saw die as monsters. Taking from anything else (an animal, a townsperson, another player) can be a crime: in a test, looting a rabbit's corpse in Britain made the character a criminal and the guards killed him.

### Leaving and packs

In auto mode the agent leaves a fight it can't win: it runs until nothing hostile is in
sight, keeps running from whatever is still there for 40 seconds, and for two minutes after
that doesn't go looking for creatures, only fights what comes at it (within 3 tiles for a
warrior, within its reach for a mage, archer, tamer or bard). In a pack round the
warrior got away untouched, went after the orc it had left once the 40 seconds were up, and
walked back into the gargoyles.

- **It runs.** Every move of a fight runs, as players do: a flee, a step back, a leave, a chase. The client's pathfinder only ran walks longer than 14 tiles, so a 10-tile flee, a step back from melee, and a 15-tile leave on a diagonal (11 tiles each way) walked at half the speed of the monsters chasing it. Only a trip or a follow asked to walk walks, and that runs too while there is danger ([pace](#travel-banking-shops-and-hunting)).
- **No idle gap.** A flee counts as running only while the character is moving: held for a fixed 6 seconds, a 3-second run left it standing for the rest, with nothing deciding and whatever followed hitting it.
- **Packs.** The state says what is coming at the character together (fighting, within 12 tiles, or already on it), weighed against it: a creature far stronger than it counts 4, stronger 2, weak 0.4, a fair fight 1 scaled by its hits against the character's (between 0.5 and 1.5, so two spectres don't weigh as two gargoyles), a spellcaster half as much again (it hurts from a distance, running or not), and each by its health (`coming_at_you`: "2 gargoyles (a fair fight and a spellcaster each); together far stronger than you: too many to fight at once"). From 2.5 on, Jev's `leave_now` is asked; and at that weight with two or more coming, code leaves while they are still coming (2.5 with no strategy, less for a cautious one, more for an aggressive one; never under a never-flee strategy, never in combat assist). The open-goal soak runs died to two gargoyles and a reaper while Jev's leave stayed at 0.39–0.44, and to a troll, a harpy and a ratman (2.5) when the cut was 3.
- **Not back too soon.** In a session, a hunt that ended in a leave from creatures stops the planner sending the character within 30 tiles of there for 30 minutes; the refusal says why and how long is left. With 15 minutes, the planner sent it back as soon as they were up, and it died there.
- **Away from all of them.** Any leave with two or more creatures in sight runs away from every creature in view taken together, each counting the same whatever its distance, not from one of them; with one in sight, from it. Only code's pack rule used to do this: in a pack round with that rule off, Jev's own leave with an orc adjacent ran from the orc alone, 15 tiles north into three gargoyles, and the warrior died there. It used to count only those within 10 tiles, so a pack still further off gave no direction and the run went north, whatever was there. A blocked way out is now tried turned up to about 100 degrees either side (it was 70), then at half the distance: in a round on another lane of the test field, the warrior ran into a corner, where nine runs in a row failed and three gargoyles caught it.
- **Cornered:** when two runs in a row fail ("no path away") with something within 3 tiles, or four in a row whatever the distance, it stops trying to leave for 15 seconds and fights what is close, whether code or Jev wanted to leave. With nothing close yet it keeps leaving, straight away from the nearest creature instead of from them all, which is another heading: in a soak run a warrior that had left three gargoyles three times found no path twice by the graveyard wall while they were still far off, was held in the fight for 15 s while Jev said leave at 0.8-0.9, and died (cuo-y4l).
- **Losing fast to one creature:** the state says when health has fallen 20 points or more in the last 6 seconds (`health_falling`), and a fall of 30 with a creature close and health under 70% is a reason to ask Jev's `leave_now`, whatever the creature's strength words. Near death the emergency flee then needs danger 0.8 rather than 0.9. A lone gargoyle, "a fair fight", took a soak run's warrior from 58% to 20% in a second and killed it with leaving never asked and danger at 0.86 (cuo-8vk).
- **When to stop, Jev's call** (`--leave-stop jev`, cuo-fhf.6; off by default: in the bench, leaves of 9 s instead of 32 s, but three times as many, and the same wins, [results](#when-a-leave-stops)). The direction stays code's line away; while a leave goes on, the request Jev already gets carries one more yes/no, `stop_running`: is it safe to stop running now (what is still in sight is falling behind, no spellcaster can still reach it, health holds)? For it, each creature in sight says how it has moved against the character over the last 4 s (`moving`: "closing in: 12 to 7 tiles in the last 2 s", "keeping pace at about 10 tiles: it is following", "falling behind: 9 to 15 tiles in the last 4 s"); with only "far away" and war mode to go on, Jev answered 0.3–0.5 nearly every time, and with them 0.7–0.8 for what was falling behind and 0.1–0.2 for what was closing in. At 0.6 or more the leave ends, but not before 4 s of running, never with a creature on the character, and never under half health with a spellcaster in war mode within 12 tiles that can see it; at 0.4 or less, near the end of the 40 s, it runs on 10 s more, up to 90 s in all. The 2-minute pause on seeking after a leave stays. The bench logs showed code's fixed rule wrong both ways: half the leaves ran the full 40 s with something still in sight, a quarter of their runs went out with nothing coming, and half were caught again within 30 s of stopping.
- **With companions** (a party, cuo-4ar): companions within 10 tiles count in the odds. A creature's strength words are worked out against the party's hits together, both said where they differ ("stronger than you: a hard fight, with your 3 companions beside you (far stronger than you alone)"), and what is coming weighs against the party ("together a fair fight for your party of 4"). The shoot's wave (a lich, two bone knights, four skeletons) weighs about 7.6 against one warrior and 1.5 against four together, so the pack rule no longer sends everyone away from a fight five can win.
- **Told apart by name.** Several kinds share a body graphic, and the strongest used to stand for all: a black bear read as "far stronger" because a named boss bear shares its graphic. Creatures are now matched by name among the kinds with their graphic.

In a session hunt, leaving ends the hunt (see [hunting](#travel-banking-shops-and-hunting)).

### Where it runs

By default code picks where every run, leave and step back goes: along the line away from the
creatures, the first point with a path. Jev only chooses whether to move. The runs that went
wrong were bad places Jev never saw: the warrior that ran from an orc into three gargoyles
(cuo-d28.11), the run into a dead end, the mage cornered for 40 seconds (cuo-y4l). With
`--spots jev` (`/spots` in the bench) Jev picks the place instead, from places the client offers;
with `--spots rule` code picks from the same places. It stays off by default: in the Jev bench it
lost to code's line away twice: 43/60 with 12 deaths against 47/60 with 3, and with places kept and
leaves sent far, 44/60 with 10 against 47/60 with 4. Code's own pick of the places (`--spots rule`)
tied with the line away, 48/60 against 49/60 ([results](#where-it-runs-1)).

- **The places (client, `AgentSpots`):** with a monster in view, a snapshot asked for `spots`
  offers about 50 places by coverage, not by judgment: staying put; 16 bearings at 3, 8 and 14
  tiles (UO's square rings), each moved to the nearest tile within 1 to 3 tiles that the player
  can walk to, preferring one that isn't a dead end, and dropped when there is none; doorways in
  view, the mouths of narrow ways, beside the pet, and beside a guard or healer. The walk is
  worked out over the travel planner's grid round the player (map files, doors, remembered
  houses, impassable items in view), never through a creature's tile.
- **The facts per place:** the steps to get there (not the straight-line distance), the
  direction, every creature's distance from it (the 8 nearest), which of them within 12 tiles
  could see it there (the [line-of-sight](#line-of-sight) rule), which the walk passes next to (a
  creature already in melee reach counts only from the walk's third step: any way out takes a
  step or two beside it), and the room beyond it: how much ground a flood from there reaches
  without getting nearer the closest creature (under 12 tiles is a dead end, 100 or the edge of
  the grid open ground). It takes about 2.5 ms on the game thread (8 ms at most so far), only with
  a monster in view and a snapshot asking for it, and is worked out again at most every 0.4 s while
  the player stands still.
- **The question (brain):** each place in words, "8 steps west-southwest: nearest hostile an
  orc 8 tiles away, none within 3; out of sight of the spellcaster; open ground beyond", and for a
  mage or archer also how far its target would be from there and whether it could see it. The
  `stand` question lists them by bearing from north, clockwise, nearest first, with staying last,
  since Jev leans towards the first option (`/spotsrev` lists them the other way round, to check
  its picks don't follow the order). Its guidance: no creature within 3 tiles once there, away
  from all of them, out of sight of spellcasters, open ground beyond, no dead ends, no walk past
  a creature; a caster or archer also wants its target in range and sight, a tamer stays near
  its pet. It is asked only when a run could come: leaving already, a reason to leave or a pack
  coming, a creature close with health under 50%, falling fast or two close, two on a mage or
  archer, or a tamer's pet losing. At about 35 tokens a place it adds 1,500 to 2,000 input tokens
  to those calls.
- **The run:** every run (`flee`, including leaving and code's own floors: the emergency flee,
  leaving from players and packs, a tamer's pull back) and every step back (`kite`) goes to the
  place Jev gave most weight among those allowed: not staying put, not beside a creature, and for
  a step back no more than 8 steps. Where Jev wasn't asked, code picks: no walk past a creature,
  no dead end, then furthest from the nearest creature (5 tiles is enough for a step back), not
  closed in, seen by the fewest spellcasters, the shortest walk. The client walks there
  (`flee {x, y}`, `kite {x, y}`), and only when its pathfinder finds no path there does it fall
  back to the line away ("no path to the spot; 15 tiles north"). The decision log has the place,
  who picked it and Jev's probability for it (`spot`), and the calls view the `stand` question.
- **Keeping to it** (cuo-fhf.5): a run or step back keeps going to the place it set off for, and
  isn't asked again every decision ("still to 1000,1008"). It picks again once the place is
  reached, after 12 s, or when a creature is nearer the place than the character is. A leave is
  offered only the 8- and 14-tile rings and landmarks at least 8 steps off; when there are none,
  anywhere. The first Jev bench had re-picked every decision, and its leaves zigzagged
  ([results](#where-it-runs-1)).

### Other players

The client watches for other players within 8 tiles: **red** (murderers), **criminals**,
and **unknown** players, meaning people without an NPC title ("Lucy the healer"). Titles come
from item properties, or, on shards without them (pre-AOS), from one single click per
person; until a title is known, only red and criminal players are flagged.

- **Combat assist:** a warning above their head, in the panel and in the journal, once a minute per player.
- **Auto:** it leaves: a red or criminal player within range makes the character run 15 tiles away from them, and a hunt ends ("a red or criminal player came close") so the planner can choose somewhere else.
- **Defensive only:** the agent never attacks a player, whatever the mode.
- **The world store** notes "A red player was seen here." for the area, never who.

### Line of sight

The server doesn't shoot an arrow through a wall, and doesn't take a spell's, a song's or a pet
order's target through one either: an archer just stands there, and a spell aimed through one is
lost. The client works out what the character can see by the server's own rule,
ModernUO's `Map.LineOfSight`, copied line for line in `AgentSight.cs` and run over the client's
map. A straight line goes from eye height (14 above where each one stands; a ground tile is aimed
at one above the highest corner of its land). Land rising through the line blocks it, and so does
any static, house piece or item flagged NoShoot or Window that spans its height on a tile, except
on the target's own tile. A door where the line starts doesn't block. Nothing past 25 tiles is in
sight. Floors and roofs (NoShoot in the tile data) only block a line that crosses their height, so
indoors they don't.

Servers differ in two places of that rule, so whose rule the client follows is a setting
(cuo-2ad): `agent_sight_rule` in settings.json, `-agent_sight_rule` on the command line for one
launch, or the `sight` method's `rule` while the client runs. The snapshot's `agent.sight_rule`
and the `sight` reply say which is in force.

- `modernuo`, the default, is the local test server's. ModernUO never lets a door on the tile the line starts from block, nor any item on the tile it ends on. The line is drawn from the lower point to the higher, so either can be the viewer's own tile.
- `runuo` is RunUO's from 2.3 on, which has no such clause: a character standing on a door's tile, which ModernUO lets see out, is behind a wall there. That is the one case where the client on ModernUO's rule would aim and a RunUO shard refuse, without a word for a weapon swing.
- `runuo22` is RunUO's up to 2.2, which also lacks a one-unit allowance the other two have: there a piece or land whose base is one above the line doesn't block, and one a unit above the target on the target's own tile does.

On everything else the three are the same, read from ModernUO's source and RunUO's (tags 2.0 to 2.4 and master).

- **The snapshot** has `in_sight` for each creature. Jev's state says "out of sight behind something" for one that isn't, and the target question then says it can still be fought by walking round, but one in sight comes first.
- **Target choice:** whatever fights from where it stands (a mage, an archer, a bard, a tamer sending its pet) takes on a creature out of sight only when none is in sight; Jev's pick of one out of sight is passed over otherwise. A warrior walks up to its target anyway, so its pick stands.
- **Spells:** a mage casts only at a creature in sight. Fields and summons go only on a tile beside the creature that the character can see, and an area spell is aimed only at a creature in sight. The client refuses a cast at a creature out of sight ("not in sight"); when a spell's cursor comes up with its creature gone out of sight, it waits up to 3 seconds while the character walks on, then lets the spell go.
- **Moving to see:** an archer or a caster engaged at range that can't see its creature walks towards it until it can, and stops there rather than walking into melee. A creature out of sight for 15 seconds is given up, as one out of reach is.
- **The server's word:** a "Target can not be seen" (or "Target cannot be seen. Try again.") after the agent's own target cursor marks that creature or tile out of sight for 4 seconds, in case the two rules ever part; a try-again cursor is let go. ModernUO sends it for songs, pet orders and bandages, and for Blade Spirits and Energy Vortex, but says nothing when another spell misses for sight, and an archer out of sight just doesn't shoot. So the client also counts, by its own rule and with the checks on or off, every aim at something out of sight (`agent.stats.aimed_unseen`) and the time spent engaged at range with the creature out of sight (`unseen_engaged_ms`), beside the server's messages (`not_in_sight`).
- **Checked against the server:** 2920 of 2920 pairs agree, around Britain and among test walls; in the line-of-sight benchmark the agent goes from 0 of 32 rounds won to 32 of 32 ([results](#line-of-sight-1)).

## Character types

The brain tells how to play a character from its skills and what it has in hand, checking in
this order, or you name it with `uo-brain run --archetype warrior|mage|archer|tamer|bard|necromancer|paladin`:

| plays as | when |
|---|---|
| paladin | a book of chivalry, Chivalry 50 or more, and a melee weapon in hand with a weapon skill of 50 or more |
| necromancer | a book of necromancy, Necromancy 50 or more and at least as high as Magery and any weapon skill |
| mage-tamer | a spellbook, and Magery and Animal Taming both 50 or more |
| warrior-mage | a spellbook, Magery and a weapon skill both 50 or more, and a melee weapon in hand |
| mage | a spellbook, Magery 50 or more and at least as high as any weapon skill |
| tamer | Animal Taming 50 or more, and at least as high as any weapon skill |
| bard | Musicianship 50 or more, and a bard skill 50 or more and at least as high as any weapon skill |
| archer | a bow or crossbow in hand |
| warrior | anything else: melee, bandaging between hits |

### Mages

- **Range:** it engages from up to 7 tiles away and casts Jev's pick from the spells it can cast right now: the attack spells (Flamestrike, Energy Bolt, Explosion, Lightning, Mind Blast, Fireball, Harm, Magic Arrow, Poison and Paralyze), the area spells below, and the rest of the book a fight can use.
- **The rest of the book:** Jev is offered every spell in the book that can help a fight, each described in words with its mana, and only where it can do something:
  - curses (Curse, Weaken, Clumsy), once on a creature while they last, and the spells on mana (Feeblemind, Mana Drain, Mana Vampire) only with a spellcaster in sight; Mass Curse with a crowd, like the area spells;
  - fields (Wall of Stone, Energy Field, Paralyze Field, Fire Field, Poison Field) with the creature 3 or more tiles off, so there is a tile between for them; never for a warrior-mage, who would wall itself off;
  - summons: Blade Spirits and Energy Vortex beside the creature, the elementals, Summon Daemon and Summon Creature beside the mage, while it has the follower slots for them;
  - blessings on itself (Bless, Strength, Agility, Cunning, Reactive Armor, Magic Reflection) while their buff icon is down;
  - Earthquake, which hits everything around the mage, with three or more monsters within 4 tiles and nobody else within 6.

  A spell that fizzles often at the character's Magery says how often it works. ModernUO casts from a book at a chance that climbs from nothing to certain over 40 skill points, from 80 for the eighth circle under Mondain's Legacy rules (70 before them), so a Magery 90 mage casts an eighth-circle spell 1 time in 4 and a seventh-circle one (Flamestrike, Chain Lightning, Energy Field) 6 times in 10; a fizzle costs the reagents and the time but not the mana. The attack spells get these words too.

  **Walls and summons first** (opt-in: `PolicyConfig.ward`, `jev/ward` in the bench): with three or more creatures coming at a mage and fewer than two on it, Jev is also asked whether to cast one of its fields or summons first, offered only those that work at least half the time, or "none". The options are alike, so its yes is spread over them: a swarm round put 0.77 on four fields against 0.16 on attacking, none above 0.3. So code takes it as yes when everything but "none" adds up to 0.5, casts the likeliest, and waits 15 s before the next, one wall a crowd. It isn't on by default: walls and summons didn't win swarm rounds ([results](#walls-and-summons-in-a-swarm)).

  Code aims each: a curse at the creature, a blessing at the mage, a summon with no target, and a field or Blade Spirits on the free tile beside the creature nearest the mage (the client answers the tile cursor). ModernUO shows a summoned monster red, as it would a murderer, so the client takes any creature that appears within 3 tiles of where a summon was cast, in the few seconds after, as the agent's own: it's never a target, and the state calls it "your summon". Each is held back for a while after it's cast: a curse 60 seconds for that creature, a blessing 90 (10 where the server shows buff icons, so a cast that failed is offered again), a field 12 and a summon 10. When Jev isn't sure, code still falls back on the strongest damage spell, and the rules judge only ever picks attack spells. Left out: healing and curing (the reflexes), Protection (code raises it), travel and escape (Recall, Gate Travel, Teleport, Invisibility), Dispel and Mass Dispel (nothing says which creatures are summoned), and spells that do nothing in a fight.
- **Your spell plan comes first:** the opener on a fresh creature, then the main spell. Jev picks when your strategy names none; when Jev isn't sure, code picks the strongest castable damage spell that works 9 casts in 10 at the character's Magery (an area spell for three or more together, unless it mostly fizzles).
- **Queued casts:** the brain queues the next spell and the client casts it the moment the current spell and its recovery allow. Healing reflexes go first.
- **Protection:** as soon as melee monsters come at it (in war mode within 10 tiles), it casts Protection first, since every hit otherwise interrupts a spell; cast once they were adjacent, it was broken too. It's asked for again until its buff icon shows, at most three times in 20 seconds, since it is a toggle and a server that sends no icon would see it turned off again. A queued Protection or heal isn't pushed out of the client's one-spell queue by an attack spell from the next decision. That's AOS: on older shards Protection only adds armour, so it's skipped (see [Older rules](#older-rules-pre-aos-shards-such-as-uo-renaissance)).
- **Crowds:** with three or more monsters within 2 tiles of one of them, and nobody else (a player, a pet, a townsperson) within a tile of the blast, Chain Lightning and Meteor Swarm are offered too, aimed at the creature that has the most others around it. Under AOS rules their damage is shared once more than two are hit, but twice over, so three creatures each take about two thirds of a single hit for 40 mana. Code casts one when Jev isn't sure, doesn't step back while it can, and with three or more on it goes for the creature with least health left: every kill is one fewer hitting it.
- **Kiting:** with two or more monsters in melee reach it steps back 5 tiles between spells, keeping its target; the next spell waits for the step, since casting roots the mage. It steps back once per attack spell cast: stepping back again before a spell went off kept one test mage from ever casting.
- **Cornered:** when two runs in a row fail ("no path away"), it stops trying to leave for 15 seconds and fights, as any character does (see [leaving and packs](#leaving-and-packs)): in swarm rounds a mage kept "leaving" for 40 s while six monsters hit it.
- **Meditation:** it meditates while resting with mana below 80%.
- **Seeing inside:** the server only says what's in a spellbook or a bag once it's opened, so the agent opens the spellbook and any unopened bags in the backpack once, and closes them again.
- **Snapshot:** mana, reagent counts, every spell in the book with its cost and why it can't be cast ("mana", "reagents"), and the cast timing.

### Archers

- **Range:** it engages from up to 8 tiles, or the weapon's own range if shorter (a bow reaches 10, a crossbow 8, a repeating crossbow 7), and the client keeps that distance while it shoots.
- **Ammunition:** the snapshot counts arrows and bolts and says which the weapon in hand needs. With none left it can't fight, so fighting is ruled out and it leaves. At 25 or fewer the supplies read "running low", and an empty quiver ends a hunt. A hit leaves some arrows in the monster's pack, so the client takes arrows and bolts from corpses as it does gold and bandages.
- **Kiting:** with two or more monsters in melee reach it steps back 5 tiles, once per shot fired (counted by arrows used), since a bow fires only once the archer has stood still for a moment.
- **Restocking:** the planner knows an archer wants 150 arrows or bolts, and `do buy arrow 200` finds a bowyer or provisioner in the world store.

### Tamers

The pet fights and the tamer stays back.

- **Finding the pet:** a pet looks like any other blue creature; only its status says the owner may rename it. The client asks the server for the status of each non-hostile creature in sight, once a minute, and lists the pets in the snapshot with their health.
- **Orders:** the `pet` verb says what a player would: `all kill` (the client answers the target cursor with the creature), `all follow me`, `all guard me`, `all stay`. The brain only sets a pet on monsters.
- **Fighting:** Jev picks the creature to set the pet on; anything attacking the tamer itself comes first.
- **Looking after the pet:** below 70% health, the tamer bandages it when it's within 2 tiles (Veterinary), or walks over when it's further off. It also walks back to a pet more than 7 tiles away, since a pet out of sight is lost.
- **Calling it back:** when the pet is losing, Jev is asked on its own whether to call it back ("the pet is badly wounded (45%), fighting an orc"). A yes at 0.6 or more, or the pet under 20%, sends `all follow me` and runs 8 tiles. When leaving, the tamer goes in short legs, calls the pet every few seconds and waits for it to catch up. Without a pet in sight it can't fight, so it leaves.
- **In combat assist** it says "call your pet back, it is losing" instead of moving.

### Bards

A bard fights with songs.

- **The songs:** provocation sets one monster on another; peacemaking calms one (aimed at the bard itself, it calms everyone around); discordance weakens one. Each is a skill use followed by target cursors. The `skill` verb takes `targets`, and the client answers each cursor in turn. If the server first asks "What instrument shall you play?", the client answers with an instrument from the pack.
- **Choosing:** Jev picks the song and the creature. For provocation it also picks whom the incited creature should attack. When Jev isn't sure, code incites the strongest against another if there are two, calms one that is close, and otherwise weakens it.
- **Pacing:** one song every 6 seconds, and a creature a song has just hit is left alone for 20.
- **No instrument, no fight:** the bard leaves instead.

### Necromancers and paladins (AOS-era shards)

Necromancy and Chivalry only exist on shards with the Age of Shadows rules, such as the local
ModernUO server; UO Renaissance has neither.

- **Necromancer:** plays as a mage does, from up to 7 tiles, with its own attack spells: Poison Strike (and some damage to whatever is next to the target), Strangle (damage over time) and Pain Spike (quick and cheap). With three or more monsters within 3 tiles and nobody else within 5, Wither (everything around the necromancer) is offered too. Its curses (Evil Omen, Corpse Skin, Blood Oath, and Mind Rot against a spellcaster) and Vengeful Spirit, a revenant that hunts the creature, are offered as a mage's curses and summons are. Curse Weapon isn't: it only works on weapon hits. It counts its own reagents (bat wing, grave dust, daemon blood, nox crystal, pig iron), and with no healing spell it bandages and drinks potions. It meditates while resting.
- **Paladin:** fights as a warrior, and blesses its fighting: Jev picks the blessing for the fight at hand, from those it can cast and that aren't already in effect. When Jev isn't sure: Holy Light with three or more within 3 tiles (and nobody else near), Divine Fury against several or a stronger creature, Enemy of One against a stronger one, otherwise Consecrate Weapon, renewed for each new creature or after 20 seconds (it lasts 3 to 11 and shows no icon). Divine Fury and Enemy of One wait for a quarter of the mana, which also pays for Close Wounds. One blessing every 2 seconds at most.
- **What the client reads:** each book is opened once, as a spellbook is; the snapshot lists every spell of each book with its school, mana and tithing cost and what's missing ("mana", "reagents", "tithing"), the character's tithing points, and a necromancer's reagents apart from a mage's.
- **Hands:** chivalry and necromancy keep the weapon in hand (only magery drops it), so a paladin's weapon isn't re-armed after a blessing.

### Hybrids

- **Mage-tamer:** sets the pet on the creature, casts at it from range as a mage does, and heals the pet with Greater Heal from up to 10 tiles away. It asks both the spell and the pull-back questions.
- **Warrior-mage:** opens with one spell on a creature that is still 2 to 7 tiles off, then fights in melee. ModernUO, like other RunUO-family servers, drops the weapon into the pack when a spell starts, so after each cast the client lifts it and puts it back on, as an assistant's arm macro does.

## Strategy, in your own words

You can tell the agent how you like to play, like an `AGENTS.md` for your character:

```
-agent strategy set Attack relentlessly and never flee.
-agent strategy add Finish off the weakest enemy first. Only pick up valuable things.
-agent strategy            (shows it)
-agent strategy clear
```

From the command line: `uo-brain strategy load my-warrior.md`, or `uo-brain run --strategy my-warrior.md`.

The strategy is saved per character and used in two ways:

- **As context:** the text goes into every decision question (intent, target,
  corpse, items), so Jev weighs it each time it chooses. It is kept out of the
  `in_danger` question, which is a factual judgment.
- **As settings:** when the text changes, Jev answers typed questions about the
  strategy itself, and code enforces the answers:

  | question | type | effect |
  |---|---|---|
  | Does it allow fleeing? | yes/no | Turns fleeing off entirely, including the code's own emergency flee |
  | How aggressive? | 5-level score | Moves the danger threshold for fleeing |
  | Which target first? | choice | Current / weakest / closest / strongest when Jev isn't sure |
  | How much to loot? | choice | Everything / valuables / nothing |
  | Which spell to open with? | choice | Mages cast it first at each new creature |
  | Which spell after that? | choice | Mages keep casting it once the fight is under way |

`uo-brain strategy explain` shows how the current text was read, and the panel shows the
reading under your strategy. Spell plans are settings as well as context because the text
alone wasn't enough ([results](#arena)).

### Templates

Ready-made strategies you can pull in instead of writing your own, and combine with your own lines:

| template | for | what it says |
|---|---|---|
| `relentless` | any | Never flee, press the attack, finish the weakest first, take only valuables |
| `survivor` | any | Play it safe, retreat early, fight what's closest |
| `farmer` | any | Clear everything nearby, loot every corpse completely |
| `champion` | any | Go for the most dangerous enemy first, aggressively |
| `no-loot` | any | Never stop to loot |
| `nuker` | mage | Open with Explosion, then keep casting Energy Bolt |
| `mana-saver` | mage | Open with Lightning, then cheap Magic Arrows; rest between fights |
| `flamestriker` | mage | Open with Flamestrike on the most dangerous enemy, finish with Lightning |

- **Pulling one in:** click it in the panel's *templates* row (hover for the full text; click a gold one to take it out). Or use `-agent template nuker`, or `uo-brain strategy template nuker`.
- **Adding to or replacing your strategy:** a template is added after your own lines; use `set` / `--replace` to start over from it. `-agent template` lists them.
- **Your own templates:** drop a `.md` file into an `AgentTemplates` folder next to the client. A file with the same name as a built-in one replaces it. The format is:

  ```
  # Title
  for: warrior | mage | any
  summary: one line for lists and tooltips

  One instruction per line.
  ```

Jev reads all eight built-in templates as intended: every flee, target, loot and spell
setting matches, tested by compiling each one with Jev and with the rule judge.

## Goals and the planner

In auto mode the agent can work towards a goal you give it in words, for example "hunt
the undead at the Britain graveyard, keep yourself supplied with bandages, bank your
gold". Type it into the panel's goal box, pick a goal template, or use `-agent goal`.

- **The planner** is a chat model: Claude Haiku 5.5 through OpenRouter by default, the same key as Jev (`--profile sonnet` plans with Sonnet 5.5, which follows more of a goal at about forty times the cost; [Results](#models-for-each-role)). Each time a goal ends, it sees the character's situation in words, your goal and what has happened so far, and answers with one tool call: a goal for code to carry out (`travel_to`, `hunt`, `bank`, `buy`, `sell`, `rest`, `set_strategy`, `set_machine`, `finish`) or a world-store query first (`place`, `find_place`, `hunting_spots`, `what_spawns`, `route`, `notes`, `outcomes`, `region_at`), or keeps a memory (`remember`, [below](#the-characters-memory)), searches it (`recall`), or changes a [long goal](#long-goals) (`long_goal`). Jev re-ranks the list answers against the planner's question ([below](#which-facts-reach-the-decisions)); the session summary counts those calls and their cost.
- **The character now:** what it plays as (warrior, mage, archer and so on, as the fights tell it), where it is, health, gold, weight, the supplies that matter for it, and mana. A mage isn't told of bandages, as it heals with spells. Reagents a bag holds that the client hasn't looked into yet are "not counted yet", and the first planner call waits up to 10 s for the client to open the pack's bags (it opens one a second; `unread_bags` in the snapshot). A new mage told of 0 bandages and no reagents was sent shopping for both before its party's fight (cuo-pz9).
- **No scripted loop:** the planner puts travel, hunting, banking and restocking together itself. Code carries out each goal ([below](#travel-banking-shops-and-hunting)); Jev keeps the fighting.
- **Routine calls are Jev's:** inside a hunt, whether to head back, stay or walk elsewhere in the spawn are quick Jev questions, not fixed thresholds and not planner calls ([Hunting](#travel-banking-shops-and-hunting)). The planner is called when a goal ends, including when Jev is unsure twice running, so open-ended choices (where next, what to buy) stay with it.
- **Cheap to call:** each step rebuilds a short prompt (a cached system prompt, the goal, the long goals, a summary of the session so far, the last 12 steps word for word, the memories Jev picked, the character now) instead of growing one long conversation. A call costs about $0.0005 with Haiku (about $0.01 an hour), $0.008-0.014 with Sonnet.
- **Sonnet for the hard moments:** Haiku plans, and a step goes to the stronger model (the profile's `escalate`, Sonnet 5.5 unless named) only when Haiku is stuck: no usable goal twice running, the same goal failing twice running (or any three in a row), code refusing to go back where it left (somewhere new to hunt is needed). Never two in a row, never over a budget. A death isn't escalated (since 2026-10-12, cuo-tyv): every model calls `resurrect` after one, 25 times of 25 in [the hard-moments comparison](#opus-for-the-planners-one-off-calls), and code picks the healer; in the memory soak 13 of 22 escalations, about $0.025 each, came after a death. A `resurrect` that fails twice running is escalated as any goal is. Each is logged with its reason (records of type `escalation`) and costed under its own kind, `escalate`; the session summary counts them by reason. `--no-escalate` turns it off; the `npc` profile names Haiku for `escalate`, which turns it off too.
- **A running summary:** steps that leave the 12-step window are folded, four at a time, into a summary of the whole session (at most about 200 words, under the headings Done, Going on, Failed and why, Left to do, with place names and counts copied exactly) by one Haiku call (kind `compact`), so a long session keeps its arc while the prompt stays the same size. The same call is a memory flush: it names up to three things from those steps worth keeping in the character's [memory](#the-characters-memory) (`source` `flush`). When the session ends, the rest is folded and the summary is kept as a memory. Each fold is logged (records of type `fold`, with its tokens and cost); the session summary counts them.
- **The panel** shows the step and why ("hunting at Britain Graveyard for up to 15 min: supplied with 80 bandages; time to hunt"). *pause* keeps the goal but stops work on it.
- **Handing over:** switching to combat assist (Alt+A) stops the agent walking at once and pauses the planner, so it stops costing tokens. Switching back to auto tells the planner you drove for a while, and it resumes from wherever the character is, with whatever it carries.
- **Danger seen on the way:** every goal's result names the stronger creatures seen during it, and they are kept in the world store as "danger" notes that the planner's `hunting_spots` shows for the area. An area with danger noted within the hour goes to the back of `hunting_spots` (`recent_danger`: "12 min ago: ..."), and the planner is told not to hunt where a hunt ended in a leave for at least 30 minutes.
- **Never standing still:** while the planner thinks, Jev fights what attacks, as it does during travel and errands; a hunt that ended in a leave hands over with the creatures possibly still about.
- **Its own strategy lines, never yours:** `set_strategy` puts the planner's lines in a block of their own at the end of the strategy, under "From the planner (it may change these):", and a later call replaces only that block (empty text takes it out). Your lines and templates stay as they are. It may change them at most once every 10 minutes, so a change gets a hunt to show; the session summary counts the changes (`strategy_changes`).
- **Plans for fights:** the planner can set one with `set_machine` ([Plans for fights](#plans-for-fights)); `--no-machines` hides the tool.
- **After a death** the session goes on ([below](#after-a-death)): the planner resurrects the character and goes back for its corpse.
- **Finishing:** when the planner calls `finish`, the goal is paused with the reason.

Goal templates (`-agent goal templates`, or the *goals* row in the panel):

| template | goal |
|---|---|
| `graveyard` | hunt the undead at the Britain graveyard from the West Britain bank; restock and bank |
| `earn-gold` | pick hunting spots near Britain that suit the character, keep supplied, bank the gold |
| `guard` | stay where you are, fight what comes near, rest in between |
| `restock` | bank gold and loot, buy supplies for a long hunt, then stop |

Your own go in an `AgentGoals` folder next to the client, in the same format as strategy
templates.

From the command line, `uo-brain session "GOAL" --hours 1 --log logs/session.jsonl` runs
the planner without the panel; `uo-brain run` does the same whenever the panel has a goal
and the agent is in auto mode. How an hour with nobody at the keyboard went:
[Unattended runs](#unattended-runs).

### After a death

A death no longer ends the session. Two goals for the planner, carried out by code:

- **`resurrect`:** as a ghost, walk to a healer in a guarded town (the nearest such; wandering
  healers out in the wilds only when no town healer is known) and step up to it. A ghost can't be
  hurt on the way, but a character resurrected in the wilds comes back with little health among
  what killed it: a soak character died five times running that way. Once alive it stands and
  heals, fighting back if attacked, until its health is back to 70%, and a caster's mana (a mage's, a necromancer's, a paladin's) to 50%, at most two minutes: resurrected with no mana to recall, every planner model sometimes set off on foot, and in the logs that walk died 3 times in 4 (cuo-tyv). A ModernUO healer offers only when a ghost steps to within 4 tiles of it from
  further out, in its sight, at most every 2 s, so the ghost stands off at 6 tiles and walks in, up
  to three times a healer and at most three healers. In auto mode the client accepts the offer
  itself (a priced healer's too, paid from the bank); with you driving, the gump is yours.
- **`recover_corpse`:** once alive, walk back to where it died, open its own corpse (the client
  knows it: the corpse that appeared there, with its body) and take everything back. On an AOS-era
  server opening your own corpse puts it all back and on; on an older one the client takes each
  item into the pack and puts back on what was worn (`rewear`), one at a time.
- **The planner decides:** it is told the character is dead, and once alive, where its corpse is
  and how long ago it died (`your_corpse`), until it has taken it back. It is told to rest after a
  resurrection (the character comes back with little health), to wait while what killed it is still
  there, and that corpses decay. The next call stays with the planner's own model: a death isn't
  a hard moment since 2026-10-12 ([escalation](#goals-and-the-planner)).
- **Counted:** the session summary and `soak-report` count deaths, resurrections and corpses taken
  back, and the minutes spent on them; each death is kept in the character's memory.
- **With you driving** (combat assist), the brain does nothing for you after a death; the panel says
  where the nearest healer is and where your corpse lies, and again once you are alive.
- **By hand:** `uo-brain do resurrect`, `uo-brain do recover-corpse`.

### Travel, banking, shops and hunting

Real play is a loop of travelling, hunting, banking and restocking. Each step is one
goal that code carries out, using the world store for places:

- **Travel** walks to a named place or a tile across the map. The client's own pathfinder
  only sees about a screen, so the walk is planned coarsely over the map and static tiles
  (`AgentNav`), with what the walker has seen added (`TravelMemory`), then walked a leg of up
  to 16 tiles at a time with that pathfinder, which handles creatures, items and the exact
  movement rules (`AgentTravel`). Roofs don't count as ground.
  - **Doors** are items, not map tiles. Travel remembers every door it sees, for as long as the client runs, and the plan passes through a door at the cost of 4 extra steps, so an open way no longer than that wins. A door standing on the path ahead is closed: the walker walks to the tile before it, opens it there and goes on (from two tiles away when it can't get beside it, as on a house's steps). A door the client's pathfinder can already step through is left alone, and one whose leaf has swung onto the path isn't closed again. A door that hasn't moved after two tries counts as locked: it's blocked and the walk goes round.
  - **Houses** and other multis aren't in the map files either. Travel remembers the tiles each house in view covers and blocks them in every plan, while out of view too. Of a house the trip starts or ends in, only the walls are blocked, so the way in or out is through its door. When a house or an impassable item comes into view on the path ahead, the walk is planned again round it at once.
  - **Long trips** over 360 tiles go a hop at a time: a plan to a point 300 tiles along the straight line to the goal, or to the nearest place to it the grid allows, planned again from near its end. A hop that doesn't bring the goal 75 tiles nearer ends the trip as "no route". No planning box is wider than 900 tiles.
  - **No route, at once:** before searching, travel looks backwards from the goal over a box 193 tiles across. When nowhere within the distance asked is somewhere to stand, or everything that can walk to the goal is closed off inside that box (the sea, a walled yard, a small island), the answer is "no route" with the reason, in under a millisecond. So is any walk between the land and Felucca's or Trammel's dungeons (east of x 5120).
  - **Stuck:** no progress for 8 seconds counts as stuck. Progress is getting nearer the goal or further along the path: a way round a mountain range can lead away from the goal for minutes. The walker blocks that stretch and plans again (up to 5 times), in a wider box when the way round leaves the first one. When someone stands on the path ahead (a companion, a creature in a doorway) only the tiles they stand on are blocked, and when no way round is left it plans again with nothing blocked, since people move: blocking the next four tiles closed a room two tiles wide, and a party's leader leaving an inn gave up with "no way there on the map" in two runs of three (cuo-0jz). After a second with someone on its next tile, a walker turns to face them, as people do in a doorway. Impassable items seen on the way (barricades, blockers) stay blocked for the rest of the trip. A plan of a single step is walked like any other; the walker used to skip it and stand still until it gave up as stuck, which was 42 of the 61 stuck spots in the logs before.
  - **On the game thread:** planning runs there, between frames. One plan searches at most 400,000 tiles; measured plans took 0.2–60 ms, most under 10 ([results](#travel-and-errands)).
  - **Pace:** a trip is run unless it is asked to walk (`act travel … pace=walk`). A trip at a walk still runs while there is danger: the agent fighting, fleeing or stepping back, a hostile monster in war mode within 12 tiles, or anything attacking the character. It goes back to a walk 3 seconds after the danger was last seen. The snapshot's `travel` says the `pace` asked and whether the trip is `running` now.
  - **Following** (`AgentFollow`): `act follow target=<serial>` keeps the character within a few tiles of another (2 unless `distance` says otherwise) as it moves: on a free tile beside or behind it, never one somebody else stands on, and round a tree by the client's own pathfinder. It walks unless `pace=run`; at a walk it runs in danger as a trip does, and to catch up from more than 4 tiles behind until it is back in its place. While the agent fights, loots or flees the follow waits (`waiting`) and carries on afterwards. With its target out of view (18 tiles, or gone) it stands still and says `lost`, and picks the target up again if it comes back. A door that has shut behind the target is opened by a trip the follow plans for itself. A follower a step from its target that has stood still facing it for 1.5 s is in its way, and steps off its line (`making way` in the journal); a walker stuck behind someone turns to face them, so a companion on a leader's way out of a room moves aside (cuo-0jz). It ends on `stop`, a trip, `walk_to`, `move`, an errand, a death, or the mode leaving auto.
  - **On the way** the brain fights only what comes close or attacks, and doesn't seek or loot. A creature that can't move (a reaper, a corpser) isn't fought on a trip at all: the walk goes on past it, and if the client was already fighting it, the brain lets it go. Leaving a fight on a trip keeps to the road unless something is ahead on it; the brain then lets the creature go (`disengage`), because the walk waits while the agent is engaged. Before that, a warrior on the way to Cove left an ettin and a reaper at 1.0 for 35 s, standing beside them, and died. The same goes for resting, banking and shopping: every goal other than a hunt runs with Jev fighting beside it in this defend-only way. A soak run found the gap: a character resting at the graveyard was killed by a lich, with nothing fighting back.
  - Each walk goes into the world store as a route, with the spots where it got stuck.
- **Recall and Gate Travel:** on trips over 40 tiles, travel first looks for a marked rune or a runebook entry that goes there (by name, or by a name the world store places within 20 tiles), recalls (a mage casts Recall; anyone else uses a runebook charge), then walks the rest. A failed cast is tried once more, then it walks. The client reads each runebook once through its gump, without showing it, and answers the gump's entry button to recall or open a gate; a moongate's "dost thou wish to step in" warning is answered too. Gate Travel is there for the planner and the CLI (`act recall … kind=gate`, then use the gate).
- **Banking** (`bank`): says "bank" near a banker and moves items one at a time, checking each arrives (the server refuses moves that come too fast). Deposit `gold`, `loot` (anything that isn't kit or supplies) or named items (`bandage:150`); withdraw named items. It says "bank" every time, even with the box seen before: the server closes the box once the character walks off, and before cuo-rn2 every deposit after a session's first was dropped into a closed box and refused.
- **Buying** (`buy`): walks up to the vendor (ModernUO answers "vendor buy" only next to it), reads its list and buys what's wanted and affordable. A guildmaster in the shop is passed over: it sells nothing (cuo-rn2: a soak run's buys at the Britain healer went to the healer guildmistress on the next tile). A vendor only stocks so many (Britain's healer has 20 bandages at a time), so it goes round the vendors in sight, then the next shop. It walks to no shop more than 200 tiles away. Past that it stops and names the next shop and how far it is ("the nearest other healer selling bandage is the Cove healer, 770 tiles away"), and the planner decides whether the trip is worth it. With Britain's healer sold out, a buy had walked on to Cove: 6.6 minutes past a bog thing and a plague beast (cuo-1kk).
- **Selling** (`sell`): the same, from the vendor's sell list: `loot`, or named items.
- **Hunting** (`hunt`): walks to a spawn area and lets Jev fight there until time is up or one of Jev's routine calls ends it (`routine.py`):
  - **The questions:** "should it head back to town now?" and "is this spot still worth hunting?", plus, while nothing is in sight, "walk to another part of the spawn rather than wait?". They're yes/no questions (Nouls) about the hunt so far in words: supplies and how many more kills they last at this hunt's rate (code works that out), the bag, kills and the usual time between them, the lowest health, flees, and your strategy text.
  - **When:** after a kill, when supplies or the bag cross a level ("running low", "getting heavy"), every 30 s while it's quiet, and otherwise once a minute; never more often than every 10 s, one at a time, beside the fight loop rather than in its way.
  - **What the answers do:** at 0.65 or more on "head back", or 0.35 or less on "worth it", the hunt ends with Jev's number and the facts in the reason ("Jev: time to head back (0.79): 9 bandages and 0 heal potions left, about 6.0 bandages a kill so far: enough for about 1 more kill; bag light (30% of what the character can carry)"), after the fight at hand (up to 30 s). A confident "walk elsewhere" sends it to another spot of the spawn, but four such walks in a row that find nothing hand the hunt to the planner: in a soak run Jev kept saying "walk elsewhere" (0.65–0.77) round an emptied graveyard for 7 minutes. Unsure (between 0.35 and 0.65) twice running ends the hunt too, and the planner decides with Jev's numbers in front of it.
  - **Floors in code**, no question asked: dead, no bandages and no heal potions, no reagents for any attack spell, no arrows or bolts for the bow in hand, a tamer's pet gone, a full bag (98%), 10 minutes with nothing to fight, a red or criminal player close.
  - **Left from danger:** when the fight loop leaves (a pack, a far stronger creature), the hunt ends once nothing is coming at the character any more (or after 45 s), so it doesn't walk back into what it left. The reason names what drove it off, goes into the world store as a "danger" note for the area, and when a rune or runebook entry goes to a guarded town (a bank first), it recalls there.
  - **Without Jev** (the rule judge, or two failed questions in a row) the old fixed rules apply: under 10 bandages, under 5 of a reagent, under 20 arrows, 85% weight, 4 quiet minutes, and a walk round the spawn every 25 s when quiet.
  - **Logged:** each question goes to the session log as a `routine` record (moment, the state in words, the questions, Jev's answers, the verdict and why); the `hunted` record and the session summary count them and their cost (`routine_cost_per_hunt_hour`).

```bash
cd brain
uv run uo-brain do travel "Britain graveyard"
uv run uo-brain do travel "Cove healer" --defend --timeout 1800 --log logs/walk.jsonl   # Jev fights what attacks on the way
uv run uo-brain do bank --deposit gold,loot --withdraw bandage:100
uv run uo-brain do buy bandage 50
uv run uo-brain do sell loot --vendor weaponsmith
uv run uo-brain do hunt "Britain Graveyard" --minutes 10 --log logs/hunt.jsonl
```

How these went on the local server: [Travel and errands](#travel-and-errands) and
[Routine hunt calls](#routine-hunt-calls).

## Plans for fights

By default every fight has the same shape: six intents, the same questions every decision,
and code's rules on top. A plan gives the fights a shape for the place and the character
instead, as a small state machine (`machine.py`). The planner model designs it, or it comes
from a file, and Jev runs it:

- **Steps** (states), each with what the character does there in plain words, which intents it may choose (`fight`, `flee`, `leave`, `loot`, `seek`, `rest`), and optionally who to target first, how much to loot, whether to step back from melee and which spell to keep casting: any spell a fight can use, a wall, curse or summon too, cast again once it has worn off, with Jev picking in between. A step can have a time limit and where to go after it.
- **Moves** (transitions) between steps, each a yes/no question with the planner's own criteria ("health is falling faster than the bandages bring it back"), answered by Jev in the same request as the fight questions. Only the current step's moves are asked, so a plan costs a few yes/no answers a decision and no extra round trip. A move can require conditions code checks first (`3+ close`, `2+ coming`, `pack`, `health below 60`, `mana above 50`, `none in sight`, `supplies low`, `corpse near`, `area spell ready`, ...), so it's only asked when it can matter, and needs Jev's yes at 0.6 (or its own `at`).
- **The plan reaches every choice:** each question's instructions carry the plan and the current step, so Jev's intent, target and spell follow it; the factual danger question doesn't see it, as it doesn't see the strategy.
- **Floors no plan can remove:** the client's healing reflexes, never attacking players, leaving from red or criminal players, the emergency flee near death, leaving from a pack, and fighting back when hit with no way out allowed.
- **Checked:** at most 8 steps and 4 moves a step; unknown intents, settings, steps or conditions are refused with what is wrong, so the planner can fix it.
- **The panel** shows the plan's steps with the current one marked, what it means, and the last move ("open -> blast (0.71)").
- **In sessions** the planner can set or clear one with `set_machine` between goals; it applies to hunts and stays until replaced. A hunt's result reports the time spent in each step, the evidence the planner is told to change it on.
- **Logged:** each decision records the step and any move; a run's summary has the time in each step.

```bash
cd brain
uv run uo-brain machine list                     # brain/machines: pull-one, mage-swarm, wall and summon tests, the planner's own
uv run uo-brain machine show pull-one
uv run uo-brain machine design "A mage hunting alone in open ground, rushed by groups of melee monsters" --archetype mage
uv run uo-brain run --machine pull-one           # also session, do hunt; the planner may replace it
uv run uo-brain bench --scenarios swarm --judges "jev,jev#mage-swarm"
```

`machine design` has the planner model write one for a situation in words, with the two shipped
plans as examples, and saves it to `brain/machines/`: Haiku by default, about $0.001 a plan
(Sonnet about $0.02, with `--planner-model anthropic/claude-sonnet-5.5`). How plans did against the
fixed policy: [results](#plans-for-fights-1); Haiku's plans against Sonnet's:
[results](#haiku-or-sonnet-for-plans-reviews-and-guides).

## World knowledge

The brain keeps what it knows about each shard in a SQLite file,
`brain/worlds/<shard>/world.sqlite` (`local` is the ModernUO test server). The planner
looks facts up with tools when it needs them, instead of having them pasted into every
prompt. The store holds:

- **Places:** banks, healers, vendors and what they sell, moongates, teleporters, dungeon entrances and levels, shrines and landmarks.
- **Regions:** towns, dungeons and other named areas, with their bounds and whether guards protect them.
- **Spawns:** which creatures spawn where, how many at once and how quickly they come back.
- **Creatures:** hits, damage, fame and karma, a difficulty word (trivial, weak, moderate, strong, deadly) used to rate hunting spots, whether it can move at all (`rooted`: a reaper or a corpser, from `DisallowAllMoves`), and how it picks a fight (`fight_mode`, from the constructor's `FightMode`: `Aggressor` and `None` only fight back, `Evil` only goes for characters of bad karma, the rest attack on sight). A store made before `rooted` or `fight_mode` gets the column when it is opened, and the value on the next `import-modernuo`.
- **Routes:** paths that worked, paths that got stuck and where, and teleporter links.
- **Outcomes:** what hunting somewhere gave, per area and kit: kills, deaths, supplies and gold per loop, and which creatures cost the most.
- **Notes:** free-text facts, searched by keyword.

The planner's queries are `place` (a loose name to coordinates: "britain bank", "Britain
graveyard"), `find_place` (the nearest bank or healer, or a vendor that sells bandages),
`hunting_spots` (for an archetype and level, towns left out, with past results there, and spots no walk reaches marked and listed last),
`what_spawns`, `route`, `notes`, `outcomes` (how earlier hunts in an area went) and
`region_at`. `world.tool_schemas()` gives them as tool definitions for a model
and `World.call_tool` runs them. Distances are in tiles, counted as max(|dx|, |dy|).
A place outside any region is named by its tile, "wilderness near 1800,1400", and a goal naming
one the store doesn't hold goes to that tile: the planner makes up names of that shape
("wilderness near 1693,1473"), and before, two soak hunts failed on them.

Every row records its source (`modernuo:<file>`, `seen`, `note`, `markers:<file>`,
`guide:<url>`, `model:unverified`, or `outcomes` for the notes that sum up an area's outcomes)
and when it was last seen. When something seen in game contradicts a stored fact, the old row
is marked stale instead of deleted, and queries skip it. A place seen in game also confirms
the same kind of place stored within 10 tiles from another source (its `last_seen` moves on).
When two sources put the same kind of place within 10 tiles of each other, queries list only
the more trusted one: what the agent saw, then the server's data and the player's notes, then
map markers, then guides, then the model's guesses. Other players' names and speech never go in:
a PK sighting is stored as "a red player was seen here", not who it was.

```bash
cd brain
uv run uo-brain world import-modernuo
uv run uo-brain world --shard uor import-markers ~/Downloads/uor-markers/ --maps Felucca
uv run uo-brain world find bank --near-place "Britain graveyard"
uv run uo-brain world hunt warrior new --near "West Britain bank"
uv run uo-brain world spawns "Britain Graveyard"
uv run uo-brain world note "Wraiths here are too much for a new mage." --area "Britain Graveyard"
uv run uo-brain world import-guide https://www.uoguide.com/Britain --area Britain
uv run uo-brain world outcomes logs/session.jsonl
```

### Which facts reach the decisions

The store holds far more than any one decision needs, so Jev chooses (`facts.py`, after
TypeSafe's [re-ranking cookbook](https://docs.typesafe.ai/cookbooks/rerank_typesafe.md)):
code shortlists generously, Jev answers one yes/no per candidate, and only the best are
used. Jev can only pick what code hands it, so the shortlist is wide on purpose.

- **In fights:** when the situation changes (a new area, a new kind of creature in view, a new goal), and never more than every 5 seconds, code shortlists up to 50 facts: notes about the region and the areas around it (outcome notes included), notes naming the creatures in view, what spawns within 40 tiles, and notes matching the goal and the archetype. Jev is asked of each one "would knowing this change what the character should do in the next minute?", all in one request (25 questions a request; more go out in parallel). The best three above 0.6 go into every decision's state as `what_you_know_about_this_place`, until the situation changes again; moving to another area drops them at once. The request runs beside the decision loop, which never waits for it. A picked fact also lets Jev's `leave_now` yes/no take the character away, as a far stronger creature does.
- **For the planner:** `notes`, `hunting_spots` and `what_spawns` take an optional `question`. The store is asked for three times the results wanted (at least 15, at most 30), Jev scores each one against the question and the player's goal, and the best come back first with a `relevance` from 0 to 1. Without a question, the query itself is the question.
- **Without Jev** (the rule judge, or an error): no facts in fights, and the store's own order for the planner.
- **Logged:** each choice is a `facts` record in the decision log (the trigger, every shortlisted fact with its score, what was kept, tokens and cost), and each planner re-ranking a `rerank` record. `uo-brain run --facts all` puts every shortlisted fact in instead, and `--facts none` leaves them out.

Whether picking facts pays: [fact-picking results](#world-store-and-fact-picking) and the
[wisp scenario](#judgment-benchmark).

### Filling the store

**From the ModernUO checkout.** The local store is built from it and is gitignored, so
rebuild it with `uo-brain world import-modernuo` (`--modernuo-dir` if it isn't in
`~/Workspace/ModernUO`). It reads what a Felucca server with the configured expansion loads:
`Locations/felucca.json`, `regions.json`, the spawn files in `Spawns/shared/felucca` and
`Spawns/post-uoml/felucca`, `teleporters.json` and the public moongates. Vendor spawns become
places with what they sell (a table written from ModernUO's `SB*Info` classes), and creature
stats are parsed from the C# in `Projects/UOContent/Mobiles`. Re-running it replaces the
`modernuo:` rows and keeps everything else. For example, the Britain graveyard spawner at
(1369, 1475) keeps up to 9 spectres, wraiths, skeletons and zombies alive and respawns them
in 5 to 10 minutes, and the bank nearest the graveyard is the West Britain bank at
(1425, 1690).

**From a shard's map markers.** A public shard's server data isn't available, but most
shards publish their banks, healers, moongates and dungeons as marker files for the world
map. `uo-brain world import-markers FILE|FOLDER… [--facets auto|uoam|client] [--maps Felucca]`
reads the two formats ClassicUO's world map loads from `Data/Client`:

- **UOAM `.map`** (UO Auto-Map): a first line `3`, then one marker a line, `+bank: 1425 1690 0 The First Bank of Britain`: `+` for a place or `-` for a town building, the type before the colon (it can have spaces: `+Body of Water:`), then x, y, the facet and the name (which can be empty).
- **The client's CSV:** `x,y,facet,name,icon,colour[,zoom]`, the icon standing for the type. ClassicUO keeps your own markers this way too (`userMarkers.usr`).

A folder gives every `.map`, `.csv` and `.usr` file in it. Each file's places are stored as
`markers:<file name>`, so importing it again replaces only its own rows. Lines that don't
read are listed with their line number and why, and the rest still import.

- **Types to kinds:** a table maps marker types to the store's kinds of place: `bank`, `healer`, `moongate`, `dungeon` (a dungeon entrance), `teleporter`, `stable`, `inn`, `tavern`, the shops by trade (`mage` and `reagents` are mage shops; UOAM's `arms` is an arms shop, which a search for a weapon or armour vendor also finds), and any `… guild` is a guildmaster. Player houses, boats, treasure pins and rares are skipped. Any other type is kept as a landmark with the type in its note (`exit`, `point of interest`, `champion`, `bridge` …), except that a vague type (`point of interest`, `other`, `landmark`) whose name says bank, healer, moongate, inn or stable gets that kind.
- **Facets:** the number means different things in the two formats. UOAM's own files count 0 for Felucca and Trammel both, 1 Felucca, 2 Trammel, 3 Ilshenar and 4 Malas (UOAM's FAQ gives 0 to 2; 3 and 4 are what its `Common.MAP` uses for the Ilshenar shrines and Luna). The client's CSV counts map indexes: 0 Felucca, 1 Trammel, 2 Ilshenar, 3 Malas, 4 Tokuno, 5 TerMur. ClassicUO reads both with map indexes, so a UOAM file's Felucca markers show on Trammel there. By default `.map` files are read the UOAM way and CSV the client's way; `--facets client` reads a `.map` file a shard wrote with map indexes. A marker off its map's edge is reported, which catches some mix-ups. `--maps` keeps only the facets named.
- **Names and regions:** a place's region comes from the store's regions where one contains it. Names stay unique within a map: a clash between types gets the type ("Covetous (dungeon)", "Covetous (exit)"), one within a type its region, and what still clashes its coordinates.
- **Agreement:** the import also says how the banks, healers, moongates and dungeon entrances line up with places the store already had from other sources: how many are within 10 tiles of one, and the ones that aren't.
- **Where to get them:** marker files are other people's work, so none are in the repo. UOAM's `Common.MAP` (the standard Britannia map: towns, shops, banks, moongates, dungeons, shrines) is inside the archive on [uoam.net/files.html](https://uoam.net/files.html); its author asks that it not be passed on, so download it from there and import your copy. Shards that publish markers usually link them from their website or forums.

A marker sits where its icon was drawn, which isn't always a tile you can stand on (8 of the
67 Britain markers in UOAM's file aren't). Travel settles within 2 tiles by default and
errands within 6 to 8, which reached every Britain bank, healer and shop marker; see
[map markers](#map-markers).

**From guides and the model's own knowledge.** `uo-brain world import-guide URL|FILE [--area A]`
has the planner model read a page (as plain text, up to 60,000 characters) and store one
note per fact, each linked to the page as `guide:<url>`. Importing the same page again
replaces its notes. `uo-brain world fill-gaps AREA` stores what the model knows about an
area from its training as `model:unverified`, tagged `unverified`, until the game confirms
it. The model is Claude Sonnet 5.5 through OpenRouter by default (`anthropic/claude-sonnet-5.5`,
about $0.03 for a 9,000-character page; `--planner-model` picks another): Haiku, system two's model
elsewhere, wrote the same facts but left most of those that hold only on Trammel unmarked
([results](#haiku-or-sonnet-for-plans-reviews-and-guides)). When a page says a fact holds only
on one facet, era or ruleset, the note says so ("Trammel only: ..."). It uses the same
`OPENROUTER_API_KEY` as Jev from `brain/.env`. `llm.py` is the small OpenRouter client behind it and
the planner: tool calling, Anthropic prompt caching on the system prompt, and the cost of every call.
Sonnet 5.5 refuses a forced tool choice, so the client asks again with `auto` and the
prompt names the tool to call.

**From what it sees** (`recorder.py`), on any shard, whenever the brain is connected,
whether the agent is playing or you are driving: townsfolk with titles become places
("Lucy the healer" is a healer), shop signs become places by their text ("The Healer's
Hut"), creatures that keep turning up in an area become spawns, travel adds routes and the
spots where the character got stuck, and hunts add outcomes (kills per hour, deaths;
`uo-brain world outcomes` adds the rest from the log). Jev keeps it clean: it says what an
unfamiliar title means (a choice), whether a creature is a regular of the area rather than
passing through (a yes/no), and whether a sighting shows a stored place has moved (a yes/no,
which marks the old fact stale). So attended sessions on a public shard double as mapping
runs.

**From session logs** (`outcomes.py`). `uo-brain world outcomes LOG [LOG...]` reads
session logs (a `uo-brain session` log is read together with its `.decisions.jsonl`) and
records each hunt, one loop, for its area and kit: kills a loop and an hour, deaths, minutes a
loop, gold a loop and an hour, and bandages and heal potions a kill and an hour. For each kind
of creature fought it adds what the fights cost: between two decisions at most 30 s apart,
health lost, bandages and heal potions go to the creature being fought, or else the nearest
one within 3 tiles. That is what fights with a kind cost, not what the kind did: health is net
of healing in between, and when several attack at once it all goes to the one being fought.
Each area then gets one note in words from every loop recorded there
([a real one](#world-store-and-fact-picking)); a kind is only named as costly after 3 fights. Rows are
named after the log, so importing it again replaces them (and the rows the live hunt wrote).
Logs without hunts (`run`, `scenario`, `bench`) need `--area`, and each run in them counts
as a loop there. The planner sees the note in `hunting_spots` and the figures through its
`outcomes` tool.

## The character's memory

The world store is the shard's knowledge, anyone's to use. Each character also has a memory
of its own, kept across sessions: what happened to it and what it learned. It lives in the
same file as the shard's store (`brain/worlds/<shard>/world.sqlite`, table `memories`), so it
is scoped by shard, and every memory names its character (`memory.py`).

- **Kinds:** `event` (something that happened), `lesson` (something learned that should change
  later choices) and `order` (a standing order from you, "never hunt in Despise").
- **Code writes** when a goal ends: every hunt (kills, gold, why it stopped), above all one that
  ended in a leave (importance 4) or a death (5); a goal code refused ("not going back near Britain
  Graveyard yet"); a travel, bank, buy or sell that failed; stronger creatures seen on the way. A
  death that ends the session is kept with where it happened, what was about and the goal.
  Errands that went to plan leave nothing.
- **The planner writes** with a `remember` tool (a lesson or an event, an area, an importance from 1
  to 5) when it concludes something worth carrying into later sessions. It is not a goal: the
  planner chooses its goal after.
- **The after-action review writes:** each line you accept with `review LOG --accept N` is also
  kept as a lesson, with its evidence.
- **You write** with `uo-brain memory --character NAME add "TEXT" [--kind lesson|event|order]`.
- **Repeats are counted, not stored again:** the same memory within the hour adds to its `times`,
  so a planner that keeps walking into the same refusal shows as one memory seen many times.
- **Forgetting** (`memory forget ID`) marks a memory stale: it stops being shown, the history stays.
- **Not for fights:** like danger sightings, memories are for the planner and never reach
  fight-time facts.
- **Every write is in the session log** (records of type `memory`), and the session summary
  counts them (`memories_written`). `session` and `run` keep memories by default.

**What the planner sees.** The planner's system prompt stays fixed (and cached); memories go in
the user turn, in a bounded section between the record of the session and the character now:

- **The player's standing orders,** always (up to 5).
- **The memories that bear on this moment,** picked by Jev as it picks world facts: code
  shortlists up to 20 (memories about the area the character is in, about the places in the
  last three steps, matching the goal's words, the most important, the newest), Jev answers one
  yes/no for each ("should the planner keep this in mind for the character's next goal?"), and
  the best 5 above 0.5 are shown, each with how long ago and how often ("[event, 18 min ago, 3
  times] Had to leave Britain Graveyard..."). Without a model judge, the most important and newest
  are shown instead. Jev picks again only after something happened (a hunt, a failed goal, a
  death, stronger creatures seen, a character it knows in view), in a new area, or every third
  step; otherwise the last pick stands. Each pick is logged (records of type `memories`: the shortlist, the scores,
  what was shown) and costed under its own kind, `memory` (about $0.00025 and 300-500 ms a pick
  on Jev; a Haiku planner call is about $0.0004).
- **`recall QUERY`:** a tool for the planner to search the memory itself, Jev re-ranking the
  matches against the query when there are more than asked for.
- **The last session:** until this session's first fold, the planner sees the summary kept at
  the end of the character's last session.
- **Earlier sessions:** `uo-brain memory --character NAME import LOG…` backfills a character's
  memory from older session logs, with what code would have kept as each goal ended, dated when
  it happened (importing a log again replaces its rows). The planner reaches earlier sessions
  through these memories rather than through the raw logs.

### Always on: heartbeat, daily digest, resume

For long unattended runs on the local server (`heartbeat.py`):

- **Resume after a restart:** after every step the planner's state (the session goal, the
  running summary, its last steps, its focus, and the goal under way) is saved in the shard's
  store (table `planner_state`, one row per character). A planner that starts for the same
  character and goal within 6 hours carries on from it: a restarted brain or client keeps the same
  goal and summary, and the planner is told the brain restarted, during which goal, and to go on
  from wherever the character is (records of type `resumed`). Another goal starts afresh, and so
  does `--fresh`.
- **Heartbeat:** every 30 minutes, and after events (the session starting or resuming, a death, a
  long goal's milestone, a first visit this session to an area), at least 10 minutes apart, a short
  reflection by the cheap model (kind `heartbeat`), on its own and not as a planner turn, so it never
  carries the planner's long prompt. It
  reads the long goals and their progress, the session's summary, the most important recent
  memories and the character now, keeps up to three lessons in memory (`source` `heartbeat`), and
  sets a focus for the planner ("push swordsmanship; avoid the graveyard at night"), shown at
  every planner call. Your goal stays as you gave it. Skipped while over a budget.
- **Daily digest** (OpenClaw's daily log): at the first heartbeat of a day, each earlier day of the
  character's memories not yet digested is summed up in one memory of that day (kind `daily`,
  `source` `daily`), so weeks of play stay findable by `recall` without reading every row.
- **Logged and costed:** records of type `heartbeat` and `daily`, with tokens and cost; the
  session summary counts heartbeats and digests and what they cost.

### Long goals

A session's goal is one sitting's. A long goal outlives it: "reach 90 swordsmanship", "save 20000
gold for a house", "learn the dungeons near Britain". They are kept with the character's memories
(`longgoals.py`, table `long_goals`), per character and shard.

- **Setting them:** type one in the panel's goal box and press *long* (*set* is this session's goal),
  `-agent longgoal add TEXT` in game, or `uo-brain goals --character NAME add TEXT`. The panel lists
  them with their progress and a *done* link; what you ask there waits until the brain picks it up
  (every few seconds while it runs) and is logged (records of type `long_goal`).
- **Progress measured by code** where the words allow: a skill's value from the snapshot ("reach 90
  swordsmanship", "get magery to 100"), gold banked since the goal was set, from the bank goals'
  before-and-after ("save 20,000 gold", "bank 25k gold"), and places visited, arrived at or hunted in
  ("visit Covetous and Despise", or "learn the dungeons near Britain": the three dungeon entrances
  nearest it in the world store, fixed when the goal is set). Anything else ("make friends in
  Vesper") isn't measured, and the planner says when it is done.
- **Milestones:** a quarter, half and three quarters of the way, and done; each is kept as a
  memory. A measured goal that reaches its target is marked done by code.
- **The planner** sees the active goals and their progress at every call, works towards them when
  your session goal allows, and chooses from them when you gave none (`uo-brain session` with no
  goal). With the `long_goal` tool it can add, reword, finish or drop one, with its reason logged.

## Seeing what it does

- **Agent panel:** Jev's probabilities, choices and actions for every decision, in game ([above](#the-agent-panel)).
- **Decision log:** every decision goes to a JSONL file with the state, all answers and probabilities, the actions and their results.
- **`uo-brain report`** summarises a log.
- **`uo-brain replay`** re-asks a log's questions to another judge offline, e.g. Jev against the rule baseline, and reports how often they agree.
- **`uo-brain soak-report`** reports on an unattended `session` run ([Reference](#uo-brain)).
- **Assist agreement:** with fight set to suggest, the brain records whether you attacked the creature it suggested.
- **`uo-brain costs`** says what the AI calls in a log cost ([below](#what-it-costs)).
- **Filming a run** (`tools/video/`, cuo-gka): `film.py` runs a command and records the client windows on the
  agent ports you name, one clip per window plus all of them side by side, into `~/Movies/ClassicUO-agent-clips/`
  as `YYYY-MM-DD <what> (<bead>) - <character>.mp4`, and prints the row for the vault's Footage log:

  ```bash
  python3 tools/video/layout.py film brain/fleets/party-lich.toml             # set the clients up for the camera
  python3 tools/video/film.py --ports 5661-5665 --what "a party on the lich scene" --bead cuo-4ar \
      -- uv run --project brain uo-brain fleet brain/fleets/party-lich.toml --windowed
  python3 tools/video/layout.py restore                                       # and put them back
  python3 tools/video/cut.py IN.mp4 OUT.mp4 --part 1:05:1:40 --speed 2   # keep a part, faster
  python3 tools/video/cut.py --grid OUT.mp4 A.mp4 B.mp4                  # side by side
  python3 tools/video/film.py --remake RAW_DIR --what "..." --bead ID         # the clips again from raw recordings
  ```

  Recording starts once each port's client is in game with a window, and stops when the command ends.
  The recorder (`wincaps`, ScreenCaptureKit, built by `tools/video/build.sh`) records every window from
  one process, since separate recorders break each other's streams, and records a window even when
  others cover it. A headless client has no window: `fleet --windowed` gives every client one. Needs
  macOS 15, ffmpeg, and the Screen Recording permission for the terminal. Recording loads the machine,
  so a filmed run's numbers aren't compared with unfilmed ones.

  `layout.py film FLEET` gives each of the fleet's characters a large game view (1004 by 920 at scale
  0.7 in a 1696 by 954 window), the agent panel beside it, no paperdoll or other saved gump, a fixed
  light and no sound, and keeps the files it changed for `restore`. As a client comes, its game view
  is 600 by 480 with the paperdoll open over the middle, which hides the characters and what they
  say. Run both with no client of that folder running (`--client DIR`, or `UO_CLIENT_DIR`, for a
  folder other than `bin/osx-arm64`).

### What it costs

Every model call is recorded with its cost: Jev's typed questions (system one: fight decisions,
routine hunt calls, picks of world facts, re-ranking the planner's queries, reading the strategy,
naming what the recorder saw) and the planner model's chat (system two: goals, after-action
review, guide import, plan design).

- **Recorded per call:** its kind and role, the model, input, output and cached tokens, latency,
  and the cost in dollars. The cost is the provider's own figure when it reports one: OpenRouter
  does, for Jev and the planner alike. Otherwise (TypeSafe direct, or a model OpenRouter doesn't
  price) it's estimated from a price table and marked `estimated`. `uo-brain costs --prices` fetches
  OpenRouter's list prices into `brain/prices.json` for that table.
- **A model nobody can price, or another model answering:** a call whose cost neither the provider nor
  the table knows is counted as $0, marked `unpriced`, and warned about once on stderr ("no price
  known for model ..."), and every summary lists such models under `unpriced_models` (`uo-brain
  costs` says the total is too low), so an id typed wrong or out of date shows in the costs instead
  of vanishing from them. And when the model that answered isn't the one asked for (a dated name
  for the plain id, or Jev's `-latest`, is fine), that is warned about too: "asked for X and Y
  answered" (cuo-znw).
- **In the logs:** one `ai_cost` record per call, in the log the call belongs to. Every run's
  `summary` record (and a session's `session_summary`) has a `costs` block: the total, cost an hour,
  a decision and a kill, split by role, kind of call and model. The one-off commands (`machine design`,
  `review`, `world import-guide` and `fill-gaps`) write theirs to the file `--cost-log FILE` names.
  A decision Jev wasn't asked for is logged with `not_asked` (why) and no `questions`; "a decision"
  in costs means one put to Jev.
- **Where people look:** the [calls window](#the-agent-panel) shows each call's cost and the
  running total; `uo-brain costs LOG` and `uo-brain report LOG` give the totals of a log;
  `soak-report` splits a session's cost between system one and system two, an hour and a kill,
  with the fight decisions put to Jev an hour (also in `fleet-report`, per character);
  bench results give each round's cost (`cost_usd`, `cost_by_kind`, the models used, the average
  latency), each judge's cost a round, and the whole run's `costs`.
- **Checked against OpenRouter:** `run`, `session` and `bench` write what the OpenRouter key has
  spent in all (its own count, from `/api/v1/key`) at the start and the end of the log. Another
  run on the same key at the same time counts in it too, and the key's count trails the calls, by
  2-3 minutes one evening and over half an hour one afternoon, so read it again once it has stopped
  moving (`uo-brain costs --key`). One bench run with nothing else on the key: $0.1002 recorded in
  1,305 calls, $0.1003 by OpenRouter's count once settled (2026-10-08, `bench/2026-10-08-fight-fixes.json`).
- **A budget:** `--budget-per-hour USD` caps what the AI calls cost an hour, judged over the last
  ten minutes (never measured over less than five, so one early planner call isn't a trend).
  `--over-budget` says what happens over the cap: `slow` (the default) decides a quarter as often
  and only on the clock, and the planner rests a minute instead of thinking; `cheaper` switches to
  the profile's cheaper models; `stop` ends the session. The panel says when it goes over and back
  under.

### Which model answers

Each kind of call can have its own model, named in one place: a profile in `brain/profiles/`.

```toml
[models]
system1 = "decision1"                      # fight, routine, facts, rerank, strategy, recorder, memory, chat
routine = "jev"                            # one kind's own line beats its system's
system2 = "anthropic/claude-sonnet-5.5"    # planner, review, guide, design, compact, heartbeat, daily
escalate = "anthropic/claude-sonnet-5.5"   # the planner's hard moments (Sonnet unless named,
                                           # as are review and guide)

[cheaper]                                  # while over a budget whose `over` is "cheaper"
system2 = "anthropic/claude-haiku-5.5"

[budget]
per_hour = 0.60
over = "cheaper"                           # slow, cheaper or stop

[aliases]                                  # short names for --use and bench labels
qwen = "qwen/qwen3.7-flash"
```

- **Profiles:** `default` (system one split by kind, [below](#the-default-system-one-split-by-kind); Haiku plans and designs fight plans, Sonnet reviews logs and
  imports guides, where Haiku [fell short](#haiku-or-sonnet-for-plans-reviews-and-guides); no
  budget), `jev` (as `default`, with Jev for every system-one call; the default before 2026-10-12),
  `sonnet` (system one as `default`, Sonnet plans, Haiku over a `cheaper` budget; the default before 2026-10-09),
  `opus` (as `default`, with Opus 5.5 for the planner's hard moments, plan design and review; measured
  offline, where it wasn't [better for its price](#opus-for-the-planners-one-off-calls)),
  `decision1` (as `default`, with Microsoft's Decision 1 for every system-one call, routine and
  strategy too) and `npc` (system one as `default`, Haiku for everything else, capped at $0.50 an hour since 2026-10-09; $0.15 before). `--profile NAME|FILE` picks one for `run`,
  `session`, `scenario`, `do hunt` and `bench`.
- **Order:** the built-in default, then `JEV_MODEL` / `PLANNER_MODEL` in the environment, then the
  profile, then the command line: `--use KIND=MODEL` (repeatable; a kind, `system1` or `system2`),
  `--model` (system one) and `--planner-model` (system two). Three kinds are Sonnet unless a profile or
  the command line names their own model, whatever the system's line or the environment says:
  `escalate`, `review` and `guide`. Two system-one kinds are Jev unless a profile, the environment or
  the command line names a model for system one or for them: `routine` and `strategy`. So
  `--model decision1`, `--use system1=jev` or a profile with one `system1` line puts every call on that model.
- **Any model in system one:** "jev" (or a `typesafe/jev` id) is Jev. Any other OpenRouter model
  is asked the same questions through a chat adapter (`models.ChatJudge`): it's told what each
  question type means and asked for a probability for every option, as JSON (with a schema where
  the model takes structured outputs), and its answer becomes the same probabilities Jev gives, so
  the policy, the panel and the logs don't change. A question left unanswered gets an even spread.
  Thinking is left off, except for models that always reason (gpt-oss, gpt-5), which are asked
  for as little as they allow.
- **Another decisions model:** Microsoft's Decision 1 (`microsoft/microsoft-decision-1`, alias
  `decision1`) is a model of Jev's own kind: it takes the same request and returns a probability
  for each option. It goes the way Jev does, through the same client with its own model id, not
  through the chat adapter: `--model decision1`, `--use system1=decision1`, or `--judges
  jev,decision1` on a bench. All three of Jev's question kinds work unchanged, several in one
  request, and its 32,768-token window holds the brain's largest request (9,842 tokens as it
  counts them). [How it compares](#jev-and-microsofts-decision-1).
- **Named in the logs:** each decision's `answers.model`, each `ai_cost` record's `model`, the
  bench results' `profile` and `aliases`, and each round's `models`.
- **Zero data retention:** this OpenRouter account only allows endpoints that keep no data, so a
  model without one is refused ("ZDR violation"); OpenRouter's `/api/v1/endpoints/zdr` lists those
  that have one.
- **The planner offline:** each planner call's log record keeps the messages it was sent (after
  the fixed system prompt), so `uo-brain replay-planner LOG --planner-model M` can put the same
  moments to another model without the game: how often it answers with a usable tool call, how
  often it picks the same tool, and what that costs and how long it takes.

### Several agents on one server

The aim is a server with smart NPCs playing beside people, each on its own models. A fleet file
in `brain/fleets/` names the characters, each with its own client (agent port), account, profile
and goal, and the test-server commands that prepare it:

```bash
cd brain
uv run uo-brain fleet fleets/graveyard-three.toml [--hours 0.5]   # the player's warrior and two NPCs
uv run uo-brain fleet-report logs/fleet/<time>-graveyard-three
```

- **What it does:** starts a client for each agent that hasn't one running (from
  `bin/osx-arm64`), logs it in (creating a warrior if the character doesn't exist), runs its prep
  commands, then runs `uo-brain session` for it as its own process, so each brain has its own
  ledger. While they play it samples the CPU and memory of every client, brain and the server.
- **The report:** per character, its profile, minutes, kills, deaths, gold banked, cost an hour
  and the models it used; together, the cost, the cost an hour and a kill, judge errors (where
  rate limits show) and the load per process.
- **An agent's environment:** an `env` table in its entry goes into its brain's environment, so
  `env = { UO_BRAIN_ASK_EVERY_TICK = "1" }` puts one agent on the fight loop as it was before
  cuo-df8, beside others on the new one (`fleets/fewer-calls-ab.toml`).
- **Shared knowledge:** the agents share the shard's world store; it runs in SQLite's WAL mode so
  one brain's writes don't hold up the others' reads.
- **NPCs with lives of their own:** an agent can have a `persona` (background, temperament, trade,
  what it wants) and its own `long_goals` instead of a `goal`. The persona goes at the end of its
  planner's system prompt (fixed for the character, so it is cached too), its long goals are added
  to its memory store at the start unless it has them already, and with no goal it chooses its own
  days from them: hunt, bank, shop, rest, walk about town. `fleets/britain-lives.toml` has three:
  a smith saving for a forge, a healer's apprentice learning the dungeons near Britain, and a
  sell-sword training his sword. `uo-brain session --persona FILE` does the same for one character.
- **They talk to each other** (`talk.py`, local server only): an NPC with a persona hears the fleet's
  other characters, by name, and nobody else; anyone else's speech stays out, as untrusted text.
  When a friend's line is unanswered, or a friend is close and they haven't talked for ten minutes,
  Jev answers one yes/no (kind `chat`): should it say something now? On a yes, Haiku writes one
  short line in the persona (kind `talk`), with what the NPC has been doing and its most important
  memories, and says what the exchange gave it: a warning, a spot, a plan, or nothing. At most a
  line every 20 s and 40 an hour, within the budget. Lines heard and said are logged (records of
  type `talk`) and shown in the calls window. Since cuo-cod they also chat when they have time,
  several in earshot share one conversation, and replies come at a human pace
  ([below](#a-party-in-a-fight-on-the-road-and-at-the-inn): small talk, a human pace).
- **Who a line is for:** a line that names another of the fleet's characters and not this one is
  heard (for the planner and memory) but not answered. A line that names nobody is put to Jev with
  who else is in earshot ("no one by name; 2 others in earshot"). In a party of five, "Well met,
  Thea!" had been answered by Thea, Isolde and Wystan, each as if spoken to (cuo-qzc).
- **What they say changes what they do:** the planner sees the last lines heard and said and what
  they gave, and a persona's prompt says to weigh a warning or a good spot as the character would.
  A conversation that goes quiet is kept as a `relationship` memory about the other character, and
  memories about a character in view are shortlisted for the planner, so the next meeting picks up
  where the last left off. `fleet-report` counts lines, what talk cost, and the goals that followed
  a conversation.
- **Lost connections:** every 30 s the fleet looks at each client: one back at the login screen (the
  server restarted, a kick) is logged in again and put back in auto mode (records of type
  `relogin`). Its session waits meanwhile instead of planning, and a brain whose client went away
  altogether finds the next one on its port (it waits up to two minutes). Checked 2026-10-09 by
  replacing a fleet NPC's client mid-run: logged in again within 30 s, and its planner went on.
- **A party on one plan** (`party = true`, `leader = "NAME"` at the top of a fleet file; `party.py`, cuo-4ar):
  - **One plan:** the leader runs the planner, and every goal it chooses is posted to the party (a table in the shared world store). The others follow, with code rather than a planner of their own: they travel, hunt and rest where the leader does (travelling to within 3 tiles), and fight beside it with Jev. A new goal from the leader cuts the one under way short.
  - **Supplies:** the leader's planner has a `restock` tool: everyone, the leader too, buys what it is short of for what it plays as (the levels the planner's prompt asks for: 50 bandages for a warrior, 30 of each reagent for a mage, 150 arrows for an archer...), drawing gold from the bank first when it carries too little. A leader's own bank, buy or sell is a restock for the others.
  - **The leader sees the party:** each member reports its state every 5 s (what it plays as, health, the supplies that matter for it, where it is, what it is doing), and the leader's prompt lists them with how far each is, and says to plan for all of them: hunt only where the party can win, restock when anyone runs low, wait for anyone dead or far behind.
  - **On their own:** a follower that dies is resurrected and goes back for its corpse by itself (whatever danger was seen there: the party is there), then rejoins, and the leader's new goals don't cut that short, nor its own restock; between goals, or before resting, it walks back to the leader when further than 10 tiles. A goal a follower can't read (the planner's model sent `minutes: null`) or that breaks under it is logged as that goal's failure and it takes the next one; before, it ended the follower's session. A member not heard from 2 minutes after the party set out is said to have not joined, so the leader goes on without it (in one run a client never logged in, and the leader waited the whole run for it).
  - **The leader's corpse** is fetched together. A corpse where danger was seen in the last 15 minutes is refused to a character on its own ([after a death](#after-a-death)); a party's leader goes anyway when companions are at the place (within 15 tiles of the corpse), or when two or more who came through that fight alive are beside it and none of the party had to leave the place, and then they go back there with it (its `recover_corpse` goal carries the place). Its planner is told which it is. Before this a resurrected leader waited in town "for the danger to age", the survivors beside it and the creatures long dead, until the corpse had decayed. A party that was wiped, or that lived by leaving (each member's report says where it last left creatures behind), has no such escort and waits as anyone would: a wiped party that went straight back all died there again, and a leader that went back with three who had been driven off met the same wave and died again.
  - **Not leaving alone:** with two or more companions within 10 tiles and health at 40% or more, a member's leave is held and it fights on with them ("held with the party: 3 companions beside it"); code's pack rule, weighed against the party, and a cautious strategy's badly hurt rule still leave (cuo-e1p). It fired 6 times in two runs and made no measurable difference: the party's deaths came in fights they were all in.
  - **Companions, not strangers:** in the fights each member's companions are named with what they play as (`your_party` in the state) and count in the odds ([leaving](#leaving-and-packs)); the planner no longer hears of them as "a unknown player 2 tiles away".
  - **The lich scene:** `fleets/party-lich.toml` (the party) and `fleets/party-lich-solo.toml` (a planner each), five classes staged south of the Britain graveyard with a lich, two bone knights and four skeletons waiting; `scripts/party_scene.py logs/fleet/RUN` says who reached the fight, who died, and how long the lich took to kill ([results](#a-party-on-one-plan)).
  - **One party a run:** a party's goals, states and callouts are kept under the run's own id (the fleet's name and the run's folder; a second run into the same `--out` folder gets a new one, recorded as `party_id` in `fleet.jsonl`), so a run never follows an earlier one's goals.
  - **Starting together:** a fleet starts each of a party's brains as soon as its character is logged in and prepared, so it fights back from then on, and the leader's planner waits for every member to be heard from (up to 90 s) before its first goal. Started together after the last login, a leader stood with its agent off for half a minute while a stray bone knight killed it and the archer beside it.
  - **More than one plan:** what a party does in a fight, on the road and at the inn is [below](#a-party-in-a-fight-on-the-road-and-at-the-inn).
- **Headless NPCs:** `headless = true` at the top of a fleet file starts every client without a
  window ([below](#without-a-window)); an agent's own `headless = false` keeps its window.
  `fleets/britain-five-headless.toml` is britain-five with the four NPCs headless, on its own
  accounts and ports. The report marks headless clients and adds each client's game-loop rate
  and its physical footprint (macOS's `footprint`, what Activity Monitor shows as memory; ps's
  resident size leaves out the GPU memory a client's textures take).

### Rules of play

How characters travel and behave can be set one rule at a time, or a named set at a time
(cuo-der: Harry's "realistic travel, where people walk" and "run always"). A set is a file in
`brain/rules/` whose keys are the fleet file's own settings; whatever is set on its own (a flag, a
key in the fleet file, `--set`) wins over the set.

| set | travel | walk | pace | small_talk | roleplay |
|---|---|---|---|---|---|
| `realistic` | walk | true | true | true | true |
| `run-always` | run | false | false | false | false |

- **Travel** (`travel`): `walk` puts every trip at a walk, a character's own errands too (a shop,
  the bank, a patrol round a spawn), and it runs only with danger in view, to catch up, as a ghost
  or for its corpse; a walking trip never recalls. `run` is every trip at a run. Unset, the brain
  leaves it to the client: `agent_travel_pace` in settings.json (`run` unless set), changed in game
  with `-agent pace walk|run` and shown in the snapshot as `agent.travel_pace`. A party's `walk` is
  the other thing: the trips it makes together, beside its leader.
- **Where:** `uo-brain session --travel walk|run` and `--rules NAME`; in a fleet file `travel` and
  `rules` at the top (`travel` per agent too); `-agent pace` and `agent_travel_pace` for the
  player's own character. A session that has rules or a travel pace logs them (a `rules` record).
- **Who gets which** (cuo-iqb, 2026-10-12):
  - **The player's own character** runs its trips, as players do; `-agent pace walk` if you'd rather walk.
  - **NPCs with lives of their own and parties that talk** play `realistic`: `britain-lives`,
    `party-inn` and `party-inn-exit` say `rules = "realistic"`. They're there to look like people, and
    their time costs nothing.
  - **Fleets that measure something** keep the settings their results were taken with (`party-lich`,
    `party-lich-solo`, `party-leader-down`, `party-walk`, `britain-talk`, `fewer-calls-ab`, ...), and a
    new fleet file with no `rules` has the defaults in the table [below](#a-party-in-a-fight-on-the-road-and-at-the-inn).
  - **Recall** isn't a rule of its own: a walking trip never recalls, a running one may.

### A party in a fight, on the road and at the inn

A party is more than one plan. Its members help each other in a fight and call out what is
happening, talk on the road, walk their trips together, and after a hunt sit down at the inn and
talk it over. All of the talk is for the local server only: nothing here speaks on a real shard.

A fleet file's top-level settings turn each part on or off, and `uo-brain fleet FILE --set
KEY=VALUE` overrides one for a run (repeatable), which is how the comparisons in
[Results](#a-party-that-helps-calls-out-and-talks) were run:

| setting | default | what it does |
|---|---|---|
| `help` | true | heal, bandage and take creatures off each other |
| `callouts` | true | call out in fights |
| `talk` | true | hear each other and talk (a party hears itself, persona or not) |
| `small_talk` | true | chat when there is time, not only when there is something to say |
| `pace` | true | answer at a human pace, one speaker at a time |
| `roleplay` | false | gestures and emotes on events |
| `walk` | false | walk trips together, run only in danger or to catch up |
| `plan` | "leader" | one plan: the leader's, or agreed in a huddle with a planner each ("huddle") |
| `memory` | true | false: every brain without its character's memory, so repeat runs are independent |
| `use` | none | a model for one kind of call in every brain of the run, over its profile: `--set 'use="table=anthropic/claude-sonnet-5.5"'` (a list for several) |
| `travel` | unset | `walk` or `run`: the pace of every trip of every brain, its own errands too (an agent's own `travel` wins); unset, each client's `agent_travel_pace` ([rules of play](#rules-of-play)) |
| `rules` | none | a named set of these settings, under the file's own: `realistic` or `run-always` ([rules of play](#rules-of-play)) |

`fleets/party-inn.toml` is the lich scene's five with personas and all of it on, against a lighter
wave (a lich and four skeletons, without the two bone knights), so that the hunt is usually won and
what follows it can be seen; `fleets/party-lich.toml` stays the baseline, with the full wave, no
personas and no talk.

- **Helping each other** (`policy.help_mates`, cuo-4ti). Whatever else a member is doing, unless it
  is running or under half its own health:
  - **A mage** casts Greater Heal on the worst hurt companion under 60% within 10 tiles and in sight.
  - **Anyone with bandages and Healing** bandages a companion under 60% within 2 tiles.
  - **One with Veterinary** bandages a companion's hurt pet.
  - **One help every 4 s**, and one already being healed by another is left to them unless it is
    under 30%.
  - **Nobody walks over to a hurt companion while a fight is on.** In the first run with this, the
    archer, the tamer and the bard all walked to the warrior at 51%, into the lich on him, and all
    five died. Once nothing hostile is within 8 tiles, one with bandages walks to a companion still
    hurt.
  - **Taking what is on the mage:** each creature attacking a companion says so in the fight state
    ("attacking your companion Isolde (mage, badly wounded)"), the target question says a creature
    on a companion who is weak in melee or badly hurt is worth taking off them, and when Jev's own
    pick isn't sure a warrior or paladin goes for it before the creature it was fighting.
- **Callouts** (`mates.py`, `voice.py`, cuo-cod.2). In a fight talk stops (a yes/no and a line take
  about 2 s, and the moment has passed), so code calls out from what the fight loop already knows,
  with no model call:
  - **When:** health under a half and under a third, and back over 60%; the creature it starts on;
    a dangerous one attacking it; leaving; out of bandages, reagents or arrows; a companion dead;
    the fight over. The last two are said by the first by name of those who saw it, not by all five.
  - **A companion's death** is known from its own report: the server doesn't send a ghost to the
    living, so a companion who was in view within the last 20 s and now reports itself dead has
    fallen where it was last seen (before this, eight runs had 17 deaths and 2 "down" callouts).
  - **A member's own death** ends its fight: what it was waiting to say is dropped, and it calls
    nothing until it is well again. Without that its first decision at the healer called "all
    clear" and "out of bandages" a hundred tiles from the fight.
  - **In its own voice:** at the start of a session the cheap planner model writes a voice pack in
    the character's persona, three or four lines for each kind with a slot for the creature or the
    companion ("{target} on me, get it off!"), kept in the shard's store so the next session doesn't
    pay again (a tenth of a cent a character: $0.005 for the five). Without a persona the lines are plain. Code
    fills the slot ("the lich", "a bone knight") and picks a line not said lately. Blunt and rude
    is asked for, swearing allowed, no slurs.
  - **Said aloud** (it shows over the head and in footage), one every 6 s for each member, except
    that "nearly dead" goes at once.
  - **What it means** goes into the party store the moment code decides on it (table
    `party_calls`), and each member's fight state carries the others' calls of the last 15 s in
    words (`party_calls`: "Isolde (mage), just now: near death, 18/60 health"), with what is on
    each companion (`your_party`), so Jev's choices can answer them.
- **Carrying on while the leader is down** (cuo-e1p): a hunt isn't cut short by the leader's own
  business (its resurrection, its corpse), and while the leader is dead the others carry on the
  hunt it last set, in spells of three minutes, keeping beside its deputy (the first of the others
  alive) instead of waiting where they stand. Once the leader is alive again and only away for its
  corpse, the ones within 30 tiles of the hunt carry on and the rest wait: three in town once set
  off for a graveyard 130 tiles away because the leader's corpse goal was the latest, though it
  had been refused in half a second.
- **Talk in a party** (`talk.py`, cuo-cod.1): every member hears all the others, each in its
  persona (background with a few old stories, temperament, way of talking, likes, and what it thinks
  of each of the others, so the banter has material). Followers have a memory of their own though
  they have no planner, so what they hear and say is kept as relationship memories, private to each.
  - **One conversation:** several in earshot share it. A line that names someone is for them; one
    that names nobody gets one answer, from whoever claims it first (the floor, a table in the
    shard's store that the brains share); and two who have traded six lines with nobody else
    speaking let it rest.
  - **Not talk:** a callout or an emote is heard and not answered, nor kept as a conversation (three
    shouts had become the memory "Talked with Roland: I've got the lich. / The lich on me! / I'm
    nearly dead!"). A tamer's orders to its pets ("all kill", "Bramble guard me") aren't heard as
    speech at all. A line is as old as the client's journal says: one more than 20 s old when it is
    first heard (a client left running, the minute a character was dead) is heard and left, and a
    ghost hears nothing to answer later.
  - **Heavy banter** is asked for outright in a party's line prompt (they rib each other hard, no
    apologies, swearing fine, no real-world slurs, in character and in the game world), because the
    cheap model softens it otherwise.
- **Small talk** (cuo-cod.6), for a party and for the NPC fleets: with a friend near and nobody
  having spoken for 45 to 120 s, Jev is asked whether a bit of small talk would be natural, and the
  line writer is told to make it that: the weather and the hour, the town, food and drink, an old
  story, a grumble, a question about the other's life, a running joke. A story told is remembered
  with who heard it, and the writer is shown "stories they have already told Thea", so it isn't
  told to her again as new. Small talk gives the planner nothing, and at most its last two lines
  are shown in the planner's "Lately said and heard", so a warning isn't crowded out.
- **A human pace** (cuo-cod.9): a reply is held until a person would have read the line and typed
  the answer (about 1.5 s plus 0.05 s a character heard, then 1 s plus 0.04 s a character said, a
  little quicker or slower each time), with the model's time inside that wait, not on top of it.
  Nobody starts a line while another's is still being read (1.5 s plus 0.06 s a character; the
  floor again). A reply that would come more than 20 s late, or after someone else has spoken, or
  into a fight, is dropped. Lines are asked for at 60 characters and cut at a sentence or a word
  when the model overruns. Callouts are not held back.
- **Walking together** (cuo-cod.7): with `walk = true` the party's trips are walked (`act travel
  pace=walk`): the client runs while there is danger and goes back to a walk after. The others
  walk beside the leader (`act follow`: within two tiles, running to catch up) instead of each on
  its own path, the leader waits up to 20 s for anyone more than 8 tiles behind it, and a walking
  party doesn't recall away from each other. A ghost's walk to a healer, a walk back to a corpse and
  catching up are still run. The leader waits once for each member: one still behind after the 20 s
  is on business of its own and isn't waited for again until it has caught up, and between goals a
  follower in a walking party closes up from 5 tiles, not 10 (an earlier revision made 40 waits on
  one trip and had 179 tiles unfinished after 9 minutes). `fleets/party-walk.toml` is a trip of
  about 260 tiles through Britain for measuring it; `scripts/party_walk.py` samples where everyone
  is and reports how far apart they got.
- **Roleplay** (`roleplay.py`, cuo-cod.8): with `roleplay = true`, a bow as the party comes
  together (the others salute their leader), and small things done in character, shown as emotes:
  on setting out, while resting, after a fight, and kneeling by a fallen companion once the fight is
  over. They come from the voice pack ("*checks the straps on Bramble's harness*"), so they cost no
  call. Each event has a chance of being acted on and one character does at most one such thing
  every 75 s, never in a fight.
- **R&R at the inn** (`table.py`, cuo-cod.3 to .5): the leader's planner has an `inn` goal, which it
  chooses as it chooses every goal (the tool says when it fits: a hunt over, after a death once
  everyone is on their feet, before ending a session).
  - **Knowing the hunt is won:** a leader's own hunt says "0 kills" when the others did the
    killing, so its result adds what the party left dead (by the corpses the leader saw), and once
    nothing hostile is left the leader's situation says so at every step (`the_hunt_here`: "won: you
    and your party killed a lich, a skeleton here 2 min ago, and nothing hostile has been seen
    since") until something hostile shows, it moves on, or the party has talked it over. Told only
    once, in a long result, the planner hunted an emptied graveyard for the rest of two sessions.
    `fleets/party-inn.toml`'s goal also asks for it ("When the hunt is won, take them to the inn to
    talk it over"), as a player would.
  - **The walk and the table:** the party walks to the nearest inn the world store knows, the
    others beside the leader, and the leader waits up to two minutes for everyone to be within 8
    tiles. The others wait three minutes for the table to open, longer than the leader can take.
  - **Indoors:** the leader walks onto the innkeeper's own tile, through the inn's doors, and the
    others come in after it until the leader is in their sight. Within two tiles of that tile can
    be the other side of the wall, and the first four tables were held standing against the inn
    from outside.
  - **The talk:** a group conversation of about four lines each (12 to 30): a toast, the stories,
    the banter, then what each would do differently. The leader's brain keeps the turns in the
    party store (the one the last line was aimed at, else the one who has said least, never the
    same twice running); each line is written by the speaker's own brain from its persona, its
    memories and its own story of the hunt, with what has been said so far.
  - **Its own story of the hunt,** gathered by its fight loop, in code: what it fought, the lowest
    its health got and what was on it then, who was beside it, whether it died, who healed whom,
    what it shouted and what it heard, who fell, whether it ran or ran out.
  - **Drinks are acted out:** an emote goes just before its line ("*has a swig of ale*"), at most
    on every other line of a speaker and never the same one twice. Nothing is bought or used.
  - **What is kept:** when the table closes each member writes its own memories of it, private like
    every memory and with no shared note: one or two lessons (importance 4), what it learned about
    its companions (relationship memories), and at most one line about how to fight next time. Only
    that last one reaches its fights (`your_own_lessons_for_fighting_with_this_party` in the fight
    state), and a line about whether to fight, when to leave or what to avoid is refused: a memory
    that says "leave" once made every later hunt end in a leave. The line reaches the fights of the
    same session as well as the next one's. The leader's next reflection takes the evening as an
    event.
- **The huddle** (`huddle.py`, `plan = "huddle"`, cuo-bih), the other way to one plan: every member
  keeps its own planner, and what they agreed is in front of each of them.
  - **The blackboard:** three lines (target, roles, rally point) in the party store, written by one
    call of the cheap planner model, which is shown why the party stopped, every member's state,
    and what each brings from its own memory (the two memories Jev picks as bearing on it most, in
    its own words). Memories stay private and there is no shared party note.
  - **When:** on events, never on a timer, and never with a hostile within 12 tiles or a fight on:
    the party has no plan yet; a member's goal for the plan failed or it had to leave the place;
    someone came near death or died since the plan was made; someone ran out of what it needs;
    something stronger than the plan had weighed was seen. Not more often than every 150 s, except
    after a death, and a death calls one huddle: the plan made after it has answered it.
  - **What each member does with it:** its planner reads the blackboard before choosing a goal, a
    goal under way is cut short when the plan changes (not in a fight), and it keeps its own memory
    of what was agreed.

### Without a window

NPCs and benchmark lanes don't need a window. A headless client hides its window and draws
nothing, while the game loop, network, world, agent and RPC run as usual:

```bash
(cd bin/osx-arm64 && ./cuo -agent_port 5578 -headless &)     # or "headless": true in settings.json
cd brain
uv run uo-brain --port 5578 client --headless --account npc9 --character Wren   # start one and log it in
uv run uo-brain --port 5578 window show       # watch it; `window hide` hides it again
```

- **What changes:** nothing is drawn, and there is no sound or music. On macOS it runs as a
  background app, with no Dock icon, and it never takes the focus. `window show` makes it a
  normal app with its window in front, drawing again; `window hide` hides it and stops drawing.
  The same works on a client started with a window. `-headless` on the command line is for that
  launch only; it isn't saved to settings.json.
- **The ground around the player:** the client loads the map near the player only while building
  what it draws, and its pathfinder reads only what is loaded, so a client that draws nothing
  stopped at the edge of the 8-by-8 block it stood in (in the empty test field). Headless, the map
  within 24 tiles of the player is kept loaded from the game loop instead: the view range, about
  what a window of the usual size loads, and more than the agent's 16-tile travel legs.
- **The frame cap still paces it:** the loop runs as before (about 800 updates a second with the
  1 ms sleeps between frames, 60 frames a second at `"fps": 60`), the frames just aren't drawn.
  `status` and `window` report the rates: `updates_per_s`, `frames_per_s`, `draws_per_s`.
- **Fleets and the bench:** `headless = true` in a fleet file ([above](#several-agents-on-one-server));
  `bench --headless` hides the client on `--port`, or starts a headless one there when none runs
  (logged in with `--account` and `--character`, closed at the end). Every bench result now records
  whether its client was headless and, per round, how many clients ran on the machine.
- **A lost connection:** a windowed client the player logged in says so in a message box and waits
  for a click. A headless one, or any the brain logged in (a fleet's), goes straight back to the
  login screen instead, so `status` shows it and the agent can log in again.
- **Screenshots:** `capture` (`uo-brain shot`) needs the window shown: nothing is drawn while it is hidden.

### After-action review

`uo-brain review LOG` has the planner model read a log and propose lines for your strategy,
each with its evidence. Code builds a small digest for it first: totals, each death with the
five decisions before it, flees, looting while a monster was within 3 tiles, low-health
moments, supplies used, judge errors, how sure Jev was of each intent, and the strategy in
use. The game logs no message when the character dies, so deaths are placed from the health
trail: a decision at 25% health or less, then the end of the run, or no decision for 10 s and
health back above 60%. The model answers by calling a `propose` tool. It is Claude Sonnet 5.5 by
default, about $0.02 a review (`--planner-model` picks another): on the same logs Haiku proposed
the same lines after a death but missed the biggest problem in a log that Sonnet found
([results](#haiku-or-sonnet-for-plans-reviews-and-guides)).

Nothing changes until you accept. The proposals are printed numbered and saved to
`<log>.review.json`, with the digest and the model's reply. `uo-brain review LOG --accept 2 3`
adds lines 2 and 3 to the character's strategy through the client, from the saved file,
without asking the model again; a line already in the strategy isn't added twice. Each line
added is also kept in the character's [memory](#the-characters-memory) as a lesson. Running
`review` again shows the saved review: `--again` asks the model again, and `--digest` prints
what it would read. What a first review proposed: [results](#after-action-review-run).

## Results

> [!NOTE]
> Most ran on the local ModernUO server; the routine-call test, the guide import and the
> review ran without the game. Apart from the judgment benchmark, which gives success rates
> over 5 or 10 rounds, these are single runs: treat them as a smoke test rather than a
> benchmark.

### Arena

Warrior arena, 6 mixed low-tier monsters per 90-second round, 2026-10-06 (`uo-brain scenario`,
with `--judge heuristic` for the rules and `--judge jev --log logs/jev.jsonl` for Jev):

| run | kills | deaths | notes |
|---|---|---|---|
| no agent (idle warrior) | 1 | 1 | dead after about 25 s |
| rule baseline, 3 rounds | 18 / 18 | 0 | `uo-brain scenario --judge heuristic` |
| rule baseline + "never flee" strategy | 5 / 5 | 0 | no flees, valuables picked up after looting |
| Jev, first run | 12 / 18 | 1 | see below |
| Jev, after the fix | 18 / 18 | 0 | 0 flees, average intent confidence 0.93 |

**Why the first Jev run had a death:** in round 3, all six monsters reached the
warrior at once. Health fell to 9% while Jev kept choosing to fight, and the code's
emergency flee didn't fire.
- **Code:** the emergency flee counted a heal potion on cooldown as available.
- **Wording:** the `flee` description implied a full pack of bandages was a reason to stay.

Both are fixed, and the state now says when the next potion can be drunk.

**Jev's cost:**
- **Latency:** 253 ms median, 362 ms at the 95th percentile, through OpenRouter.
- **Tokens:** about 150k input tokens per 90-second round.
- **Price:** about $0.25 an hour at Jev's $0.042 per million list price.
- **Recorded since 2026-10-08** ([what it costs](#what-it-costs)): two rounds each of the `priority` and `loot` scenarios cost $0.0106 in 148 fight decisions, $0.000072 a decision, 277 ms on average (`uo-brain bench --scenarios priority,loot --judges jev --rounds 2`, single run, `brain/bench/2026-10-08-costs-check.json`). OpenRouter reports Jev's cost as exactly its input tokens at $0.042 per million; output tokens are free.

After the mage, panel and template work, a regression run of the same warrior arena
with Jev scored 17 / 18 kills and 0 deaths.

**Mage** (`[AgentKit mage`), Jev with the `nuker` template, 2026-10-06
(`uo-brain scenario --kit mage`, with `--monsters` and `--round-seconds` as in each row):

| run | kills | deaths | notes |
|---|---|---|---|
| 4 monsters, no template, 60 s | 4 / 4 | 0 | Jev chose Explosion as the opener on its own |
| 5 monsters, `nuker`, before Protection | 1 / 10 | 1 | swarmed: every hit interrupted a spell, 34 of 43 casts were self-heals, and a throttled bag peek left round 2 without reagents |
| 4 monsters, `nuker`, 3 × 75 s | 12 / 12 | 0 | Explosion first, then Energy Bolt; Protection when monsters closed in |

Spell plans need to be settings as well as context: with only the text "open with Explosion,
then Lightning", Jev opened with Lightning in 3 of 4 offline tries. As a compiled
`opening_spell`, the opener is used every time.

To compare judges on the same logged states, run `uo-brain replay <log> --judge jev`.

### Judgment benchmark

The arena can't tell Jev from the rules: both win every round. `uo-brain bench` plays
scenarios with a known right behaviour, built so that the obvious rule gets them wrong, many
times per judge, and reports success rates with 95% Wilson intervals. Each scenario's setup is
test-kit commands; what counts as right is checked from the snapshots and the decision log.
Results are written to `brain/bench/<time>.json` after every round, and each round's decision
log to `brain/logs/bench/<time>/` (`<time>-lane<n>/` on another lane, so benches can run side by
side on several clients); compare runs with `uo-brain bench report A.json B.json`.

```bash
uv run uo-brain bench list
uv run uo-brain bench --scenarios core --judges heuristic,jev,jev+survivor --rounds 10 --lane 1
uv run uo-brain bench --scenarios archer-kite --judges jev,jev/nokite --rounds 10 --lane 2
uv run uo-brain bench --scenarios adherence --rounds 10
uv run uo-brain bench --scenarios world --judges heuristic,jev --rounds 10 --lane 2
```

**Core scenarios:**

| scenario | setup | right |
|---|---|---|
| `mismatch` | four weak monsters, then an ogre lord walks up | survive without engaging the ogre lord |
| `priority` | three zombies close, an orcish mage casting from 9 tiles | kill the mage first |
| `loot` | a corpse with valuables and junk, an orc arriving | take the valuables, skip the junk, stop looting when the orc arrives |
| `attrition` | eight monsters on six bandages and one heal potion | get out alive |
| `swarm` | six melee monsters on a mage | survive and kill at least four |

Results on the local server, 2026-10-07, 10 rounds per judge (`uo-brain bench report brain/bench/2026-10-07-*.json`).
The first run (`2026-10-07-core.json`) is the baseline; changes it led to were rerun the same day (`core-v2a`, `core-v2b`, `swarm-v3`):

| scenario | rules | Jev | Jev + survivor template | after the changes below |
|---|---|---|---|---|
| `mismatch` | 0/10, 10 deaths | 7/10, 3 deaths | 10/10, 0 deaths | Jev 10/10, survivor 10/10, no deaths |
| `priority` | 0/10 | 0/10 (old setup) | 0/10 | Jev 10/10 with zombies as the fodder |
| `loot` | 0/10 | 10/10 | 10/10 | |
| `attrition` | 5/10, 5 deaths | 4/10, 6 deaths | 7/10, 3 deaths | Jev 5/10, survivor 6/10 |
| `swarm` | 0/10 | 0/10 | 0/10 | rules 0/10 (9 deaths), Jev 0/10 (7 deaths) |

What changed, and why:
- **`priority` was retuned:** three orcs and the mage's spells killed the test warrior in about 60% of rounds whatever it targeted, so it measured luck. Jev also put 100% on the adjacent orc it was fighting, because the target guidance said to prefer the current and the closest creature. With zombies as the fodder and guidance that a spellcaster that is fighting comes first, Jev opens on the orcish mage. The survivor template says "fight whatever is closest", so it does, and fails this scenario by design.
- **Leaving:** on logged attrition decisions that went on to die, Jev put 0.24–0.30 on "leave now" when asked about "supplies nearly gone with several creatures still attacking". Asked whether there was enough healing left to outlast them, it put 0.40–0.60, against 0.2 where staying won, and the ogre-lord cases didn't change (the same 20 logged decisions re-asked offline). The cut on that answer was the strategy's danger threshold (0.625 with no strategy) and is now 0.2 below it. That is what took `mismatch` to 10/10.
- **Moved into code:** a mage or archer with four or more creatures adjacent leaves once Jev's danger judgment reaches 0.5, and so does any character with two or more stronger creatures close. In both cases Jev rated each creature an easy kill and kept its intent on fighting. An added "outnumbered" clause only moved its leave answer from 0.27 to 0.30.
- **Still open (then):** `attrition` and `swarm` were mostly lost by every judge with these kits. Eight monsters on six bandages, or six on a mage, killed the character whether it fought or ran, and every hit interrupted a mage's spells.

**Later the same day: running, packs and swarms** (cuo-d28.9, cuo-cvl.4; [above](#leaving-and-packs)
and [Mages](#mages)). The character now runs instead of walking, leaves a pack while it is
still coming and doesn't go back, and a mage keeps Protection up, uses area spells on a crowd
and finishes the weakest first. Rerun with 5 or 10 rounds (`bench/2026-10-07-swarm-v4.json`,
`core-v3.json`; `--scenarios swarm --rounds 10` and `--scenarios attrition,mismatch --rounds 5`):

| scenario | rules before | Jev before | rules now | Jev now |
|---|---|---|---|---|
| `swarm` | 0/10, 9 deaths, median 0 kills | 0/10, 7 deaths, median 0.5 | 0/10, 7 deaths, median 1.5 | 4/10, 1 death, median 3 kills |
| `attrition` | 5/10, 5 deaths | 5/10 (after the leave fix) | 4/5, 1 death | 5/5, no deaths |
| `mismatch` | 0/10, 10 deaths | 10/10 | 4/5, 1 death | 5/5, no deaths |

- **Swarm:** in the earlier logs, Protection cast once a creature was adjacent was broken, as was every spell after it, and the next decision's attack spell pushed a queued Protection out of the client's queue. Now it goes up while the monsters come and Chain Lightning lands on the crowd. The changes were rerun together, so which one mattered most isn't measured. The rules still lose: they cast the strongest single spell at whatever is in front of them.
- **Attrition:** Jev left in every round on its own yes/no, asked because supplies were nearly gone with creatures close; the rules got away by fleeing, which now runs.
- **Mismatch:** the code's pack rule left for both judges once the ogre lord came with company ("2 coming at once: a ratman (weak) and an ogre lord (far stronger than you)"), which is why the rules now pass a scenario built for them to fail.

**On the final code**, after the changes to running from packs, fleeing near death and fighting
after leaving, the same commands that evening (`core-v4.json`, `swarm-v5-alone.json`,
`swarm-v5.json`, `swarm-before-reach.json`; `swarm-v5-alone.json` is `--judges jev` with no other client running):
- **Attrition and mismatch** are unchanged: the rules 4/5 with one death each, Jev 5/5 each.
- **Swarm:** Jev 4/10 with 5 deaths (median 3 kills), against 4/10 with 1 death that afternoon; the rules 1/10 with 9 deaths. Run alongside three other benchmark clients, Jev won none (3 deaths, median 3 kills), so this scenario is sensitive to load as well as to chance. (On 2026-10-09 four clients at once did as well as one alone, windowed or headless: [headless clients](#headless-clients).)
- **A regression found on the way:** the rule that keeps a character from going back for what it left held every character to fighting what was within 3 tiles for two minutes after leaving. A mage stood resting while the swarm closed in and fought it only in melee range, where every hit interrupts a spell: Jev won none, with a median of 0 kills. Now what comes at the character within its own reach (10 tiles for a mage) is fought.

**Adherence:** each scenario fixes a strategy template and checks it is followed: `relentless`
never flees, `survivor` flees (at what health is recorded, to compare with relentless),
`no-loot` never loots, `nuker` opens on every creature with Explosion, and `champion`
attacks the troll before the mongbats and the orc. Jev with the template, the same day:
`relentless` 10/10 (never fled; 2 deaths), `survivor` 8/10, `no-loot` 10/10, `nuker` 9/10,
`champion` 10/10 (3 deaths).

**Archetypes**, the same day:

| scenario | result |
|---|---|
| `archer-kite`: four orcs on an archer, Jev with and without stepping back (`jev/nokite`) | 9/10 both. With kiting the lowest health had a median of 37.5% (20% without), 1 round in 10 went under 20% (5 in 10 without), and rounds were 15 s shorter |

Then 5 rounds each of the others (`brain/bench/2026-10-07-archetypes.json`, `--scenarios archetypes --judges heuristic,jev --rounds 5`):

| scenario | right | rules | Jev |
|---|---|---|---|
| `tamer-orcs` | three orcs on a tamer with a grizzly bear: kill them all without losing the bear | 2/5 | 4/5 |
| `tamer-ogre-lord` | an ogre lord and two orcs: the tamer gets away alive | 0/5, 5 deaths | 0/5, 5 deaths |
| `bard-provoke` | an ogre and two orcs on a bard: set them on each other, survive while two die | 4/5 | 4/5 |
| `warrior-mage-opener` | two orcs on a warrior-mage: open with a spell, fight in melee, sword back in hand | 4/5 | 4/5 |
| `mage-tamer-orcs` | three orcs on a mage-tamer: pet and spells, the bear kept alive | 5/5 | 4/5, 1 death |

- **The ogre lord isn't escapable on foot:** it runs as fast as the tamer, and the bear only holds it off for a while. Jev left within 4 seconds of it coming, and the tamer still died. The scenario's first version also asked for the bear back; every round of every judge lost both.
- **Tamers:** setting the pet on whatever attacks the tamer, and bandaging the pet, are code for both judges; what differs is the target Jev picks otherwise and when it calls the pet back. Jev's rounds also saw more bandaging (33 bandages on the bear over five rounds, against 9). Five rounds is too few to say which made the difference.
- **Bards:** both judges mostly provoke (the rules 22 of 29 songs, Jev 19 of 28); Jev used discordance more (4 against 1).

**Packs** (cuo-d28.9): two orcs fight a warrior while three gargoyles (`pack-gargoyles`) or two
bone knights (`pack-bone-knights`) come at it from 14 tiles; right is surviving. `jev/nopack`
turns the code's pack rule off, leaving it to Jev's own `leave_now`, which is now asked with the
pack in words. 2026-10-07, 5 rounds each on two lanes of the test field
(`bench/2026-10-07-packs-v5.json` and `packs-lane0-v2.json`,
`--scenarios packs --judges heuristic,jev,jev/nopack --rounds 5 --lane 3`, and `--lane 0`):

| scenario | rules | Jev | Jev without the pack rule |
|---|---|---|---|
| `pack-gargoyles` | 10/10 | 10/10 | 9/10, 1 death |
| `pack-bone-knights` | 10/10 | 10/10 | 10/10 |
| lowest health, median of each judge's 20 rounds | 55% | 65% | 54% |
| rounds under 20% health | 1 (15%) | none | 2 (the death, and 13%) |

- **Everyone got away:** every round of every judge left the fight. With the pack rule, code left while the pack was still coming ("3 coming at once: an orc (weak) and 2 gargoyles (a fair fight and a spellcaster each); together far stronger than you"). Without it, Jev's own `leave_now` left too, on the pack words against the gargoyles and on "2 creatures stronger than the character are near" against the bone knights.
- **The death without the pack rule:** Jev's leave ran from the nearest creature, an orc beside the warrior, which took it straight towards the gargoyles; a leave the pack rule chose ran from them all. Now every leave with two or more in sight does (cuo-d28.11).
- **Rerun after that change**, 2026-10-08, 5 rounds each on lane 3 (`bench/2026-10-08-packs-leave-all.json`, `--scenarios packs --judges jev,jev/nopack --rounds 5 --lane 3`): Jev without the pack rule 10/10 with no deaths (median lowest health 61.5%), Jev 9/10 (median 54.5%). All 190 leave runs with two or more creatures in sight ran from all of them. Jev's one death wasn't a leave: the warrior got away from the orc and the gargoyles untouched, then a gargoyle that came on its own a minute later took it from full health to 18% in 8 seconds. Five rounds a scenario is too few to call that a difference between the judges. The two warrior scenarios that end in leaving, `attrition` and `mismatch`, went 5/5 each for Jev the same day (`bench/2026-10-08-core-leave-all.json`, `--scenarios attrition,mismatch --judges jev --rounds 5 --lane 1`).
- **After the cornered and losing-fast fixes** (cuo-y4l, cuo-8vk), 2026-10-08, lane 0, one client
  (`--scenarios pack-gargoyles,pack-bone-knights,attrition,mismatch --judges jev --rounds 5`,
  `bench/2026-10-08-fight-fixes.json`): 20/20, no deaths (packs 10/10, lowest health 36-79%;
  attrition and mismatch 5/5 each). The state said health was falling in 100 decisions, and the fast
  fall was the reason `leave_now` was asked 31 times.
- **Earlier the same day:** with the cut at 3 (`packs-v2.json`), Jev died in one round of five against the gargoyles and the rules in one against the bone knights. With the final cut but before the changes to running and to fleeing near death (`packs-v3.json`, lane 3) all 30 rounds survived; a run between those changes (`packs-v4.json`) lost a Jev round to turning back near death and a rules round to looting with a gargoyle nearby, and a single round on lane 0 died cornered.
- **Cost:** Jev about $0.0045 a round.

**Necromancers and paladins** (cuo-cvl.5), 5 rounds each, 2026-10-07
(`bench/2026-10-07-necro-v2.json` and `aos-schools.json`, `--scenarios necro-orcs,paladin-orcs --judges heuristic,jev --rounds 5`):

| scenario | right | rules | Jev |
|---|---|---|---|
| `necro-orcs` | three orcs on a necromancer: kill them all, survive | 5/5 | 4/5, 1 death |
| `paladin-orcs` | four orcs on a paladin: kill them all with at least one blessing, survive | 4/5, 1 death | 5/5 |

- **Necromancers:** the first run had the rules at 0/5. They skipped any spell whose name started with "Poison" (meant for magery's Poison) and so never cast Poison Strike; and every caster began its rounds with no reagents, because the bench waited for the kit with the agent off and the client only opens a new reagent bag while it is on. Both fixed for the rerun.
- **Paladins:** Jev used Holy Light against the four orcs in every round and Divine Fury in three; the rule of thumb added Consecrate Weapon. Close Wounds is a reflex for both.

**The rest of the book** (cuo-ryt): every spell a fight can use offered to Jev, with the fizzle
odds in words ([Mages](#mages)). 2026-10-08, lane 1, one client running; "before" is the code just
before the change, with the change to leaving already in. `--scenarios swarm --judges jev --rounds 10 --lane 1`
and `--scenarios necro-orcs,mage-tamer-orcs,warrior-mage-opener --judges jev --rounds 5 --lane 1`
(`bench/2026-10-08-ryt-before-*.json` and `ryt-after-*.json`):

| scenario | before | after |
|---|---|---|
| `swarm` | 1/10, 3 deaths, median 2.5 kills | 3/10, 3 deaths, median 3 kills |
| `necro-orcs` | 5/5 | 5/5 |
| `mage-tamer-orcs` | 4/5 | 5/5 |
| `warrior-mage-opener` | 5/5 | 5/5 |

- **Jev hardly uses the new spells.** On average 6 to 10% of its spell answer went to them. In the swarm it picked Earthquake 11 times and Mass Curse 10 times in 918 spell questions, and never a field, a summon or a blessing; the necromancer and the hybrids cast none of them. So the wider choice didn't make its picks worse, and the swarm's 1 to 3 is within that scenario's swing (it went 4/10 on 2026-10-07).
- **The fizzle odds moved it.** The mage-tamer's top pick was Flamestrike 24 times before and once after (6 casts in 10 work at Magery 90), while code's fallback, used when Jev isn't sure, still cast it 40 times. The fallback now takes the strongest damage spell that works 9 times in 10 (cuo-x2x). Rerun the same day (`bench/2026-10-08-fallback-swarm.json`, `fallback-magetamer.json`, same commands): Flamestrike cast 0 times against 40, Energy Bolt in its place; `swarm` 2/10 with 4 deaths (median 3 kills, as before), `mage-tamer-orcs` 4/5 (the bear lost in one round). Neither is a measurable change at these sizes.
- **Live check** on the test field, by hand: Wall of Stone and Energy Field went up between the mage and an orc; Blade Spirits landed beside the orc and killed it; the client took the blade spirit (shown red) and a summoned pig as the mage's own. Every eighth-circle cast fizzled at Magery 90, as the odds say they mostly will. ModernUO casts Blade Spirits three times as slowly as other fifth-circle spells and Summon Creature five times, which the client now waits for.

**Line of sight** (cuo-ehm): `--scenarios sight` plays `sight-wall-archer` and `sight-wall-mage`
(an orc standing still behind a wall 6 tiles north, another in the open 8 east; kill both) and
`sight-hut-archer` and `sight-hut-mage` (an orc standing still in a hut 8 tiles north, its doorway
on the far side). With `/nosight` the client's sight checks are off, which is the "before".
Results [below](#line-of-sight-1): 0/32 before, 32/32 after, rules and Jev alike.

**World facts.** `wisp-leave-alone` checks that a stored fact changes the move: a wisp floats
7 tiles away while two orcs attack. In ModernUO a wisp only fights when attacked, and then it
hits for 17 to 18, casts spells and has about 130 hits, so the right move is to kill the orcs
and leave the wisp alone. The round seeds a throwaway store
(`logs/bench/<time>/worlds/wisp-leave-alone/`, never `brain/worlds/local`) with two notes
that say so, among 27 about the test field, orcs, warriors and other places that don't
decide anything, one of them a tempting "a wisp's corpse often holds gems". Each model judge
plays it three ways: `@none` (no world facts), `@all` (every shortlisted fact in the state)
and `@jev` (the few Jev picks). The rule judge, which can't read facts, plays it once.

Results, 2026-10-07, 10 rounds each (`--scenarios world --judges jev`; `world-v4`, and `world-v5` for `@jev` after the change below):

| judge | right | deaths |
|---|---|---|
| rules | 0/7 | 7: they attack the wisp once the orcs are dead |
| Jev, no facts | 5/10 | 3 |
| Jev, every fact | 10/10 | 0 |
| Jev, the facts it picks | 8/10 | 1 |

- **Setup:** the first run spawned the wisp first, so for the first seconds it was the only creature in sight and every judge walked up to it and died; the orcs now come first.
- **Caster priority:** the spellcaster-first target guidance from `priority` sent Jev at the idle wisp. Only a caster that is fighting now comes first, idle creatures are described as "not fighting", and Jev's "attack none of these" is respected.
- **Picking facts:** asked whether a fact "bears on a choice the character faces", Jev scored every fact about the place alike (0.41–0.58) and never picked the two decisive notes. Asked whether a fact "says what to do about a creature in view", they came first, and Jev picked one in 4 of 10 selections. Picking still trails putting every fact in, at this shortlist size (25).

### Models for each role

Which model to put in each role (cuo-m70.3), measured with [the chat adapter](#which-model-answers)
on the local server, one client, lane 0, 2026-10-08.

**System one on the core scenarios**, 3 rounds each of the five, all models interleaved
(`uv run uo-brain bench --scenarios core --judges jev,haiku,sonnet,llama,nemo --alias llama=meta-llama/llama-3.3-70b-instruct --alias nemo=mistralai/mistral-nemo --rounds 3`,
`bench/2026-10-08-models-core.json`):

| model | right | deaths | a round | a decision | an hour of fighting | latency | decisions a round |
|---|---|---|---|---|---|---|---|
| Jev | 12/15 [55-93%] | 1 | $0.0060 | $0.00010 | $0.26 | 279 ms | 60 |
| Claude Sonnet 5.5 | 9/15 [36-80%] | 3 | $0.162 | $0.0075 | $7.76 | 2.7 s | 22 |
| Claude Haiku 5.5 | 8/15 [30-75%] | 3 | $0.0085 | $0.00044 | $0.42 | 2.9 s | 20 |
| Llama 3.3 70B | 6/15 [20-64%] | 6 | $0.0032 | $0.00031 | $0.16 | 6.9 s | 11 |
| Mistral Nemo | 0/15 [0-20%] | 9 | $0.0003 | $0.00006 | $0.02 | 11 s | 6 |

| scenario | Jev | Sonnet | Haiku | Llama | Nemo |
|---|---|---|---|---|---|
| `mismatch` | 3/3 | 3/3 | 3/3 | 0/3, 3 deaths | 0/3, 3 deaths |
| `priority` | 3/3 | 0/3 | 0/3 | 0/3 | 0/3 |
| `loot` | 3/3 | 3/3 | 3/3 | 3/3 | 0/3 |
| `attrition` | 3/3 | 3/3 | 2/3, 1 death | 3/3 | 0/3, 3 deaths |
| `swarm` | 0/3, 1 death | 0/3, 3 deaths | 0/3, 2 deaths | 0/3, 3 deaths | 0/3, 3 deaths |

- **Jev is the cheapest per decision and the best:** a tenth of a cent per decision at 0.28 s, so it
  decides about three times as often in a round as the fastest chat model. Llama and Nemo cost
  less per round only because they decide so rarely; at 7 to 25 s a decision (25 s for Nemo in
  the swarm) the fight is over before they answer, and they died in most rounds.
- **Not only speed:** every chat model, Sonnet too, chose to fight a zombie before the orcish mage
  in `priority`, from the first decision (Sonnet 12% on the mage, Haiku 2%, Jev 64%). The
  questions and their wording were tuned with Jev; a chat model might do better with its own.
- **Answering the format:** every model answered every question with probabilities; none needed
  the plain-JSON fallback. Thinking is off: DeepSeek V4 Flash, which always thinks, took 100 s a
  decision offline and wasn't benchmarked.
- **Beyond the core:** Jev against Haiku, the best chat model per dollar, on the packs and the world-fact
  scenario (2 rounds each, Jev picking the facts; `--scenarios pack-gargoyles,pack-bone-knights,wisp-leave-alone --facts jev --judges jev,haiku --rounds 2`,
  `bench/2026-10-08-models-packs-world.json`) and the eight archetype scenarios (1 round each;
  `--scenarios archer-kite,tamer-orcs,tamer-ogre-lord,bard-provoke,warrior-mage-opener,mage-tamer-orcs,necro-orcs,paladin-orcs --judges jev,haiku --rounds 1`,
  `bench/2026-10-08-models-archetypes.json`): Jev 14/14 with no deaths; Haiku 10/14 with 3 deaths
  (one gargoyle pack, the archer and the necromancer), failing the tamer against orcs too, at about
  twice Jev's cost a round and 1.7-4.5 s a decision.
- **For system one, Jev for both the player and the NPCs:** no chat model kept the player's
  results, and the cheapest that came close (Haiku) costs over four times as much per decision.

**System two, the planner**, one 30-minute soak run each, one after another on the same client:
the graveyard goal with three gargoyles sent at the character at 8 minutes and a lich with two
bone knights at 25, prepared the same way each time
(`SOAK_PORT=5577 scripts/planner_compare.sh planners 0.5 anthropic/claude-sonnet-5.5 anthropic/claude-haiku-5.5 openai/gpt-oss-120b`,
`brain/logs/planners-1..3.jsonl`; single runs). Then the Sonnet run's 24 planner moments put to the
other two offline (`uo-brain replay-planner logs/planners-1.jsonl --planner-model M`):

| planner | how the run went | planner calls | a call | planner an hour | latency | offline: usable tool call / same tool as Sonnet |
|---|---|---|---|---|---|---|
| Claude Sonnet 5.5 | 34.7 min, 10 kills, no deaths, 520 gold banked | 24 | $0.0096 | $0.40 | 2.9 s | |
| Claude Haiku 5.5 | died at 8.4 min, 6 kills | 4 | $0.00044 | about $0.02 | 1.4 s | 96% / 33% |
| gpt-oss-120b | died at 8.7 min, no kills | 10 | $0.00015 | about $0.01 | 8.5 s | 92% / 42% |

- **Both deaths were in fights, not plans:** with Haiku planning, one of the three gargoyles came
  on alone, "a fair fight", and killed the warrior with no leave asked (cuo-8vk); with gpt-oss-120b
  it left the gargoyles three times, then two runs in a row found no path, and the cornered rule
  held it in the fight for 15 s while Jev said leave at 0.8-0.9 (cuo-y4l). Sonnet's run left the
  same gargoyles at once. One run each can't tell the planners apart on survival.
- **What the planners did differently:** Haiku's first goal was the East Yew healer, which has no
  route from Britain; gpt-oss-120b travelled to the bank it was standing at, then to a spot and
  back. Sonnet bought at the Britain healer, hunted, banked, waited out the graveyard's refusal and
  hunted the open country meanwhile.
- **Again after the fight fixes** (cuo-08x), 2026-10-08, two runs each: one on the main server and
  one on the soak copy, side by side, the models in turn
  (`SOAK_PORT=5577 scripts/planner_compare.sh planners-a 0.5 <sonnet> <haiku> <gpt-oss>` and
  `SOAK_PORT=5580 scripts/planner_compare.sh planners-b 0.5 <haiku> <gpt-oss> <sonnet>` against the copy on
  port 2595; `brain/logs/planners-a-*`, `planners-b-*`). On the copy a lich and two zombies were at
  the graveyard from the start, so every run there left in the first minutes and the rest was
  what the planner did next.

  | planner | main server | the copy | planner an hour | a call | latency |
  |---|---|---|---|---|---|
  | Claude Sonnet 5.5 | 31.6 min, 3 kills, 381 gold banked | 31.1 min, 1 kill (tried 9 spots in the open country, then waited out the refusal) | $0.55-0.68 | $0.0083-0.0088 | 3.0 s |
  | Claude Haiku 5.5 | 33.8 min, 12 kills, 923 gold gained, none banked | 34.0 min, 2 kills (rested out the refusal) | $0.012-0.014 | $0.00044 | 2.1-2.8 s |
  | gpt-oss-120b | 45.1 min, 22 kills, none banked; bought reagents for a warrior, 24 stuck spots on the way | 30.5 min, 1 kill, 250 banked (rested out the refusal) | $0.005-0.012 | $0.00016-0.0002 | 10-11 s |

  No deaths in the six runs (two of the three runs before the fixes died). Sonnet costs 40-70 times as
  much to plan with and made fewer kills here; it followed more of the goal (banking, restocking)
  and searched harder for somewhere else to hunt. Six runs are still few.
- **Verdict:** Haiku for NPCs (the `npc` profile): about a twentieth of Sonnet's cost a call,
  fast, and no worse at keeping a character alive and fighting in these runs. gpt-oss-120b is
  cheaper still but slow and made odd choices. Sonnet stayed the player's planner at first.
- **Again with whole turns (2026-10-09, cuo-kcv):** the runs above had the planner's replies cut at
  600 tokens, which hit Haiku 12 times in 144 turns (cuo-twd); the cap is now 2000. One 30-minute
  run each on the local server, Haiku then Sonnet, the same goal and packs, memory and escalation
  off so only the model differs (`SOAK_PORT=5652 SESSION_FLAGS="--no-memory --no-escalate --fresh"
  scripts/planner_compare.sh kcv 0.5 anthropic/claude-haiku-5.5 anthropic/claude-sonnet-5.5`;
  `brain/logs/kcv-1.jsonl`, `kcv-2.jsonl`). No turn came near the cap (longest replies 522 and 403
  tokens), none arrived empty, and neither set a fight plan. Haiku: 2 kills, 378 gold banked, 30
  bandages bought, planning $0.015 an hour; after the gargoyles drove it out it rested out the
  30-minute refusal (20 minutes resting). Sonnet: 19 kills, nothing banked, planning $0.49 an hour;
  it hunted five other spots instead. The difference is the one seen before: Haiku waits where
  Sonnet looks elsewhere, which is the moment escalation hands to Sonnet (a refused goal). Single runs.
- **Haiku for the player too (2026-10-09, Harry's decision, cuo-9zx):** the `default` profile now plans
  with Haiku, saving about $0.50 an hour; `--profile sonnet` keeps Sonnet for when following the whole
  goal matters more than the cost. How Haiku does at plan design, after-action review and guide
  import: [next](#haiku-or-sonnet-for-plans-reviews-and-guides).

### Haiku or Sonnet for plans, reviews and guides

Haiku has planned for the player's character since 2026-10-09 (cuo-9zx), measured then only on
choosing goals. The other three system-two calls, each put to Claude Haiku 5.5 and Claude Sonnet 5.5
on the same input, 2026-10-09 (cuo-twd). Each call's cost is its `ai_cost` record (`--cost-log FILE`,
summed with `uv run uo-brain costs FILE`).

| kind | Haiku | Sonnet | a call: Haiku / Sonnet | default now |
|---|---|---|---|---|
| plan design: 2 situations, each plan benched 5 rounds | swarm 2/5 (2 deaths), packs 10/10 | swarm 0/5 (3 deaths), packs 10/10 | $0.0006-0.0012 / $0.016-0.020 | Haiku |
| after-action review: 5 logs, 8 reviews each | the same first line after each death; missed the main problem of one log in 2 tries of 2 | found it both times | $0.0008-0.0011 / $0.013-0.022 | Sonnet |
| guide import: 2 pages | left the Trammel-only part of a page unmarked in 3 imports of 5 | marked it in 3 of 4 | $0.0012-0.0014 / $0.026-0.029 | Sonnet |

**Plan design.** The same situation, a paragraph about the character and what it meets, to each model
(`uv run uo-brain machine design "SITUATION" --archetype mage|warrior --planner-model anthropic/claude-<haiku|sonnet>-5.5 --out machines/<haiku|sonnet>-<swarm|pack>.json --cost-log logs/twd/design-<model>.jsonl`;
the situations and every attempt are in `bench/2026-10-09-haiku-sonnet-design.json`), then each plan
benched against Jev with no plan, judges interleaved, 5 rounds, lane 8
(`uv run uo-brain --port 5680 bench --scenarios swarm --judges "jev,jev#haiku-swarm,jev#sonnet-swarm" --rounds 5 --lane 8`,
`bench/2026-10-09-design-swarm.json`; `--scenarios packs --judges "jev,jev#haiku-pack,jev#sonnet-pack"`,
`bench/2026-10-09-design-packs.json`). Other agents' clients ran meanwhile: 10 at the start of the
swarm run and 16 at its end, 16 and 12 for the packs.

| scenario | Jev, no plan | Jev + Haiku's plan | Jev + Sonnet's plan |
|---|---|---|---|
| `swarm` (mage) | 2/5, 1 death | 2/5, 2 deaths | 0/5, 3 deaths |
| `pack-gargoyles` (warrior) | 5/5, 4 kills | 5/5, 4 kills | 5/5, 5 kills |
| `pack-bone-knights` (warrior) | 5/5, 9 kills | 5/5, 7 kills | 5/5, 9 kills |

- **Haiku's calls needed a fix first:** in 5 of its 6 attempts it sent the plan's states as a JSON
  string, with the outer object's closing brace inside it, and its swarm plan failed all three tries.
  `machine.parse` reads that now (every one of those attempts parses), which covers the planner's own
  `set_machine` too. Sonnet's two plans were right the first time.
- **Both stayed close to the examples** `machine design` shows the model (`pull-one`, `mage-swarm`).
  Sonnet's swarm plan added count conditions and a way out on health or mana from every step; it spent
  38-93 s of each round in "escape" and died 3 times. Haiku's kept `mage-swarm`'s shape; both its deaths
  came at 37 s, after 25-28 s standing in "blast", which has no way out on health (nor has
  `mage-swarm`'s). Against packs, Haiku's plan leaves on the code's `pack` condition; Sonnet's on
  `2+ coming` and a question about the pack's strength.
- **Verdict:** Haiku doesn't fall short at design: its plans did as well as Sonnet's (better in the
  swarm, though 5 rounds can't tell 2/5 from 0/5), so design stays with Haiku. Neither plan beat Jev
  with no plan: every judge got away from both packs in all 10 rounds, as code leaves from a pack
  whatever the plan, and in the swarm the plans did no better.
- **The planner's own plans were cut off:** at the planner's 600-token cap, 12 of 144 Haiku planner
  turns in the logs of 2026-10-08/09 stopped at the cap, most arriving with empty arguments, one
  `set_machine` among them (a plan takes 700 to 1,550 tokens). Sonnet's turns used at most 569. The
  cap is now 2,000, and an empty `set_machine` says it came without a machine.

**After-action review.** Five logs from `brain/logs`, copied: `arena-jev` (a death in a crowd),
`planners-2` (a death to a gargoyle in a session), `soak-goal1d` (62 minutes, 4 kills, 100 flees),
`soak-packs5` and `planners-a-3`. The first three were reviewed twice by each model, the others once
(`uv run uo-brain review <copy> --again --planner-model anthropic/claude-<haiku|sonnet>-5.5 --cost-log logs/twd/review-<model>.jsonl`;
the digests and proposals are in `bench/2026-10-09-haiku-sonnet-review.json`, to check against each other).

- **The same first line after a death:** in `arena-jev` and `planners-2` all eight reviews proposed
  fleeing when badly wounded with several adjacent, or near death with one adjacent, citing the right
  times and health.
- **Sonnet found more:** in `soak-goal1d` both Sonnet reviews named the session's biggest waste, a leave
  at full health from a ratman, a lizardman and an orc that lasted 94 decisions (111 leave decisions
  against 41 fights). Haiku's didn't: one cited that leave only as evidence for a line about fighting a
  lone skeleton, the other left it out. In `planners-2` both Sonnet reviews used the journal (the heal
  potion not ready, "Your fingers slip!"); neither Haiku review did. In `soak-packs5` both models saw
  that the strategy's flee rule leaves out gargoyles; Sonnet also that bone knights are skeletons,
  which the strategy says to prefer.
- **Haiku once proposed against the goal:** in `planners-a-3`, "rest where you stand instead of seeking
  one out", in a log where a 15-minute hunt that kept seeking made 19 kills.
- **Slips on both sides:** Sonnet once wrote 18:58:41 for 17:58:41 and counted five decisions near death
  where there were four; Haiku once proposed a line about gargoyles within 3 tiles whose own evidence
  said they were far away.
- **Cost:** 8 reviews each, Haiku $0.0077 in all ($0.0008-0.0011 a review, 5-7 s), Sonnet $0.1345
  ($0.013-0.022, 5-12 s).
- **Verdict:** Haiku falls short, so reviews are Sonnet's by default. A review is run by hand on a
  log: the difference is about 2 cents a review, not a cost an hour.

**Guide import.** Two UOGuide pages, Despise (8,936 characters) and Covetous (7,788), into scratch
stores (`uv run uo-brain world --root <scratch> --shard twd-<model> import-guide https://www.uoguide.com/<Page> --area <Page> --planner-model anthropic/claude-<haiku|sonnet>-5.5 --cost-log logs/twd/guide-<model>.jsonl`);
every note is in `bench/2026-10-09-haiku-sonnet-guide.json`.

- **The same facts:** Covetous gave 25 notes with Haiku and 24 with Sonnet, with the same levels,
  spawns, passages and rules; Despise 20-23 and 22-24.
- **Facets:** the Despise page has a Trammel section (a creature-training system with wisp orbs, armies
  and a final battle: 11 to 14 notes) and a Felucca section (each level's spawn). The prompt asked the
  model to say which era or ruleset a fact belongs to. Haiku marked none of the Trammel notes in either
  import (0 of 23); Sonnet all 13 in one and 5 of 13 in the other. A character on a Felucca shard reads
  an unmarked note as its own. With a firmer rule, now in the prompt ("say so in the note itself, for
  example 'Trammel only: ...'; every note in such a section needs it"), Haiku marked all of them in 2
  imports of 3 and none in the third; Sonnet all of them in 2 of 2.
- **Sonnet adds a little:** in 3 of 4 Despise imports it wrote which army fights which brother, which
  the page doesn't say.
- **Cost:** $0.0012-0.0014 a page with Haiku (6-7 s), $0.026-0.029 with Sonnet (10-12 s).
- **Verdict:** Haiku falls short on facets, which matter most on the Felucca and pre-AOS shards this
  agent plays, so guides are Sonnet's by default. An import is once a page.

What the comparison cost: $0.33 in 36 one-off calls (Sonnet $0.31 in 15, Haiku $0.02 in 21;
`uv run uo-brain costs logs/twd/design-*.jsonl logs/twd/review-*.jsonl logs/twd/guide-*.jsonl`), and
$0.38 of Jev's fight decisions in the 45 bench rounds.

### Opus for the planner's one-off calls

Whether a stronger model does better where the planner model's calls are one-off and not a cost an
hour: plans for fights, the after-action review and the planner's hard moments (asked on Reddit,
2026-10-10; cuo-vpi). Claude Opus 5.5 against Sonnet 5.5 and Haiku 5.5 on the same inputs, 2026-10-10,
offline: nothing in the tables was played in the game. One live death and recovery with the `opus`
profile followed on 2026-10-11 ([below](#one-live-death-with-the-opus-profile)). OpenRouter's prices that day, a million tokens in and out:
Haiku $0.10 and $0.50, Sonnet $2 and $10, Opus $4 and $20. Small samples, one answer an input unless
said. The `opus` profile (`--profile opus`) names Opus for these three kinds; no default has changed.

| kind | Haiku | Sonnet | Opus | a call: Haiku / Sonnet / Opus | time: Haiku / Sonnet / Opus | default stays |
|---|---|---|---|---|---|---|
| plan design: 2 situations; Opus's plans read, not benched | no way out of "blast" on health | a way out on health from every step; died most in the bench | a way out of "blast" on health in both swarm plans; the only plans that leave the examples' shape | $0.0006-0.0012 / $0.016-0.020 / $0.037-0.045 | 3-7 s / 5-7 s / 13-20 s | Haiku |
| after-action review: 5 logs, 8 reviews each | missed the main problem of one log in 2 tries of 2 | found it both times | found it in 2 tries of 3, as its last line; 2 reviews of 13 lost to a tool call code couldn't read | $0.0008-0.0011 / $0.013-0.022 / $0.019-0.036 | 5-7 s / 5-12 s / 9-23 s | Sonnet |
| hard moments: 78 recorded, each put once to each model | 61 right, 5 wrong | 58 right, 7 wrong | 61 right, 5 wrong | $0.0016 / $0.031 / $0.066 a step | 2.2 s / 2.9 s / 8.1 s a step | Sonnet |

**Plan design.** The two situations of cuo-twd through `machine design` again, twice each
(`uv run uo-brain machine design "SITUATION" --archetype mage|warrior --planner-model anthropic/claude-opus-5.5 --out machines/opus-<swarm|pack>.json --cost-log logs/vpi/design-opus.jsonl`;
the prompt, examples and tool are those of cuo-twd, 3,547 and 3,563 input tokens as then; every attempt
and plan is in `bench/2026-10-10-opus-design.json`). The plans were read against `machines/haiku-*.json`
and `machines/sonnet-*.json`; they haven't fought.

- **Valid at the first call**, all four, with the states sent as an object (Haiku sent a string in 5
  attempts of 6).
- **A way out of "blast" on health** in both swarm plans (below 55%; below 45% with two adjacent), which
  Haiku's and the hand-written `mage-swarm` lack and Sonnet's has (below 45%). Neither leaves "finish" on
  health alone, as Sonnet's does: both ask for two or more coming first.
- **Not the examples' shape:** `opus-swarm` can go from "recover" straight back to "blast" when the group
  catches up with it, and `opus-swarm-2` has a fifth step, "stall", that casts Paralyze Field for up to
  8 s before blasting. Both pack plans add a fourth step that rests away from the pack until health is
  above 85% or 90%. Haiku's and Sonnet's plans kept the examples' steps.
- **Two slips:** `opus-swarm` says it walls the group and no step casts a wall; `opus-swarm-2`'s "blast"
  says to stand and cast with stepping back switched on.
- **Whether they escape too often isn't known.** Sonnet's plan spent 38-93 s of each round in "escape"
  and died 3 times in 5. By its numbers `opus-swarm` would leave at least as readily: it leaves "blast"
  at 55% where Sonnet's leaves at 45%, and comes back at 70% where Sonnet's comes back at 60%.
- **Cost:** $0.022-0.040 a plan as billed, $0.037-0.045 with nothing cached, as Sonnet's $0.016-0.020
  were; 13-20 s.
- **Verdict:** design stays with Haiku. No designed plan has beaten Jev without one, the more careful
  plan did worst in the bench, and nothing here shows Opus's would do better. To find out:
  `uv run uo-brain bench --scenarios swarm --judges "jev,jev#haiku-swarm,jev#sonnet-swarm,jev#opus-swarm,jev#opus-swarm-2" --rounds 5`.

**After-action review.** The five logs of cuo-twd, copied, on its schedule (three reviewed twice, two
once), then all five once more to see how often a fault came back
(`uv run uo-brain review <copy> --again --planner-model anthropic/claude-opus-5.5 --cost-log logs/vpi/review-opus.jsonl`;
the digests are cuo-twd's to the token; proposals in `bench/2026-10-10-opus-review.json`). Each claim was
checked against the digest.

- **2 reviews of 13 came back empty:** Opus sent three `propose` calls for one, each with `lines` as a
  string holding `<parameter name="line">` and the line. `review.lines_from` read no lines and saved "No
  lines proposed", for $0.033 and $0.036. The lines are in the saved reply. Haiku's and Sonnet's 16
  reviews all read. Fixed since (cuo-0am): a `propose` call whose `lines` is a string is read as
  one line with the call's own evidence and confidence (both saved replies now give their three
  lines), and tool calls that can't be read are asked again instead of saved as an empty review.
- **What Sonnet found and Haiku missed:** the leave at full health that lasted 94 decisions
  (`soak-goal1d`): Opus named it in 2 tries of 3, each time as its last line (confidence 0.35 and 0.3;
  Sonnet's first and second), and one of the two is a review code couldn't read. The journal in
  `planners-2`: 3 tries of 3. That bone knights are skeletons, which the strategy says to prefer
  (`soak-packs5`): 0 of 2.
- **Two things neither found:** in `soak-packs5`, that the damage at 16:14 came from range, so its line
  is to leave when two spellcasters are in sight (the decisions log agrees: health 58% then 24% three
  seconds later with nothing adjacent, and two gargoyles marked as casting from a distance); in
  `planners-2`, all three tries, that the gargoyle being finished went from near death back to badly
  wounded.
- **Fewer lines, fewer slips:** 17 lines in the 8 scheduled reviews (Sonnet 25, Haiku 23) and no wrong
  number or time among them; one in the five extra ("0-3 bandages" over seven hunts that used none).
- **Cost:** $0.019-0.036 a review, $0.21 for the 8 (Sonnet $0.13, Haiku $0.008); 9-23 s, 13 at the median
  (Sonnet 8, Haiku 6).
- **Verdict:** review stays with Sonnet. Opus reads the digest more exactly, but it put the session's
  main problem last or left it out, costs about 1.6 times as much and takes nearly twice as long, and as
  the code stands 2 of its 13 reviews were paid for and lost.

**Hard moments.** `brain/scripts/hard_moments.py` takes the planner's hard moments from session logs and
plays each out as `Planner.choose` would, once per model: today's system prompt and tools, the user turn
as logged, and the model's world queries answered from a copy of the store until it names a goal
(`uv run python scripts/hard_moments.py ask logs/mem-a.jsonl logs/mem-a0.jsonl logs/mem-b.jsonl logs/4b3-run1.jsonl logs/4b3-run2.jsonl logs/4b3-run3.jsonl logs/esc-b-1.jsonl logs/video-death2.jsonl --models haiku,sonnet,opus --out logs/vpi/hard-moments.json --cost-logs logs/vpi`,
then `table logs/vpi/hard-moments.json --rows`; from `brain/`; every answer in
`bench/2026-10-10-opus-hard-moments.json`). The eight logs are the ones in `brain/logs` with an escalation
or a planner step while dead: one warrior around Britain, 2026-10-09 and 10. The 78 moments are every
step that was escalated (37), every other step right after code refused to go back (5), and the first
step after each resurrection (36). A move is right or wrong by rules in the script (a ghost resurrects;
on little health with the corpse still out it rests, and doesn't go for the corpse, hunt, or set off on
foot across open country without the mana to recall; after a refusal it goes somewhere else, or waits
when ten minutes or less are left; never back to the place the moment warns about). What the rules don't
settle is open. Fifteen moves were read by hand, listed with the reasons in the results file.

| moments | | Haiku: right / wrong / open | Sonnet | Opus |
|---|---|---|---|---|
| the character is dead | 25 | 25 / 0 / 0 | 25 / 0 / 0 | 25 / 0 / 0 |
| the first step after a resurrection | 36 | 29 / 5 / 2 | 29 / 5 / 2 | 31 / 4 / 1 |
| code refused to go back where it left | 9 | 5 / 0 / 4 | 3 / 2 / 4 | 4 / 0 / 5 |
| a goal that kept failing, or a hunt that ended in a leave | 8 | 2 / 0 / 6 | 1 / 0 / 7 | 1 / 1 / 6 |
| all | 78 | 61 / 5 / 12 | 58 / 7 / 13 | 61 / 5 / 12 |
| of them, the ones escalated in the logs | 37 | 29 / 0 / 8 | 27 / 1 / 9 | 27 / 1 / 9 |
| a goal code can carry out | 78 | 78 | 78 | 78 |
| back to a place the moment warns about | | 0 | 2 | 0 |
| the goal that had just failed, again | | 2 | 2 | 0 |
| asked the world store first | | 6 | 13 | 21 |
| a step, as billed here / with nothing cached | | $0.0005 / $0.0016 | $0.0093 / $0.031 | $0.023 / $0.066 |
| a step: median, and 9 in 10 within | | 2.2 s, 3.5 s | 2.9 s, 5.2 s | 8.1 s, 17.1 s |

- **Dead:** all three call `resurrect`, 25 times of 25. Of the 37 escalations in these logs, 23 were for
  "the character died": a question with one answer.
- **After a resurrection** the prompt says to rest until health is nearly full. On little health (33 of
  the 36) Haiku rested 25 times, Sonnet 25, Opus 28. The moments that separate them are the five at a
  wandering healer in open country on 10 health with no mana to recall, where in the logs the walk to
  town died 3 times in 4: Haiku walked in all 5, Sonnet in 4, Opus in 2 ("no mana to recall ... if I die,
  the healer is right here"). But Opus went straight for the corpse on 46 health twice, once with
  `go_anyway` because "waiting let the last one decay" (it had), where in the logs those trips died on
  83 and on full health. Sonnet did it once, with six hostiles in sight. Haiku never did. (The five
  come from `mem-a0`, before resurrection preferred a guarded town's healer and healed for two minutes
  first, so play meets them less often now.)
- **After a refusal with 28 or 29 minutes left** (4 moments) Sonnet and Opus chose to hunt elsewhere every
  time, and every time the Britain Sewer, which `hunting_spots` offers and no walk goes to: in the logs
  that goal failed 14 times of 14 ("no route: the dungeons lie apart from the land"), once at this very
  moment (cuo-432). Those 8 moves are counted open. Haiku asked to rest the block out (900 and 1,750 s)
  or went to deposit gold a banker had refused three times. Sonnet and Opus look for somewhere new, as
  Sonnet did in the soaks, and here the store sent them somewhere they can't go.
- **After a refusal with 2 to 8 minutes left** (5 moments) waiting is right: Haiku 5, Opus 4 (the fifth
  time it wrote a fight plan for the graveyard), Sonnet 3. Twice, with 2 minutes left, Sonnet read "it
  left there 28 min ago" as over and asked for the graveyard again, as the Sonnet in the log had.
- **Goals that kept failing:** little is settled. Opus never asked for the failed goal again with the same
  arguments and was the only one to try another bank when one kept refusing the gold, but it also called
  `finish` over a healer that, in the log, sold at the next try.
- **Cost:** the fixed prompt and tools are 8,972 tokens. Asked back to back they are cached; in play an
  escalation comes minutes after the last and pays to write them (in `mem-a` the Sonnet escalations cost
  $0.023 a call on average, $0.028 at the median). So a step is about 3 cents on Sonnet and 6.6 on Opus.
- **Verdict:** `escalate` stays with Sonnet. On the 37 moments that are escalated the three came out
  level (29, 27 and 27 right), at twice Sonnet's price and nearly three times its wait for Opus. Opus's
  closer reading shows in the steps after a resurrection, which aren't escalated, and so do its two worst
  moves. The sample is one warrior around Britain with one answer a moment, and it says as much about what
  is escalated as about which model: a death needs no stronger model (so since 2026-10-12 it isn't escalated, cuo-tyv), and "somewhere new"
  failed on the store's answer, not on the model's.

What the comparison cost: $2.99 in 298 calls (Opus $2.23 in 119, Sonnet $0.72 in 91, Haiku $0.04 in 88;
`uv run uo-brain costs logs/vpi/*.jsonl`).

#### One live death with the Opus profile

2026-10-11, one run, on 2593 with the party runs and a benchmark going on the same machine. A
warrior (Holt, headless) was killed by three daemons at the Vesper cemetery, 150 tiles from the
Vesper healer, and a 12-minute session started while it was dead; 88 s into its first hunt after
recovering it was killed again the same way, so that one death fell inside the session:

```bash
cd brain
uv run python scripts/death_scenario.py 5691 2786 867 900     # kills it, clears the daemons once it is resurrected
uv run uo-brain --port 5691 session "Hunt the undead at the Vesper Cemetery. Keep yourself supplied with bandages and bank your gold." \
    --hours 0.2 --profile opus --fresh --log logs/vpi-opus-death.jsonl
uv run python scripts/death_scenario.py 5691 2786 867 600     # again, during the hunt
```

| moment | who answered | goal chosen | how it went |
|---|---|---|---|
| dead at the start of the session | Haiku (nothing has failed yet, so nothing is escalated) | `resurrect` | at the Vesper healer in 63 s |
| alive, the corpse 150 tiles off | Haiku | `recover_corpse` | everything back in 66 s |
| killed again in the hunt | Opus, as the hard moment "the character died" | `resurrect` | at the healer in 62 s |
| alive again | Haiku | `recover_corpse`, then its own strategy lines ("Never engage daemons"), then `hunt` | the corpse back in 93 s; 10 minutes, 3 kills, no death, 181 gold |

- **Opus did what Haiku had done two steps earlier**, and what all three models did 25 times of 25
  offline: it called `resurrect`. Its one call took 4.6 s and cost $0.052 (9,988 tokens in, nothing
  cached); the session's eight Haiku planner calls cost $0.0047 together, 1.4 to 4.4 s each.
- **A death at the start of a session isn't escalated**, since the rule looks at how the last goal
  ended. That is the second reason the first death here went to Haiku, and it went as well.
- **So the live run agrees with the offline one:** a death needs no stronger model (since 2026-10-12
  it isn't escalated, cuo-tyv), and `escalate` stays with Sonnet.

### Jev and Microsoft's Decision 1

Decision 1 came to OpenRouter on 2026-10-09: the first model of Jev's own kind to put beside it
(cuo-79x). Both are priced at $0.042 a million input tokens. The earlier comparison
([models for each role](#models-for-each-role)) had only chat models as rivals for system one,
which took 2.7 to 11 s a decision.

**Does Jev's request work for it?** Yes, unchanged: the three question kinds (pick one, yes or no,
rate on levels), several in one request, through the same client with its own model id. Of 680
recorded requests put to both, it answered 680 with no error. The largest (a re-rank of world
query results) was 9,842 tokens as it counts them, of its 32,768.

**Offline, on recorded requests** (2026-10-10, no game: each request sent to both at the same
moment; `cd brain && uv run python scripts/judge_compare.py --out bench/2026-10-10-decision1-offline.json`,
which is `--a jev --b decision1 --fight 300 --routine 200 --facts 60 --rerank 40 --memory 40 --chat 40`;
the whole comparison cost $0.12):

| kind of call | requests | latency, Jev | latency, Decision 1 | cost a request, Jev | cost a request, Decision 1 |
|---|---|---|---|---|---|
| a fight decision | 300 | 222 ms | 320 ms | $0.000109 | $0.000076 |
| a routine in-hunt call | 200 | 210 ms | 291 ms | $0.000046 | $0.000030 |
| fight-time facts | 60 | 215 ms | 316 ms | $0.000091 | $0.000072 |
| a re-rank of a world query | 40 | 227 ms | 430 ms | $0.000282 | $0.000250 |
| a memory pick | 40 | 239 ms | 436 ms | $0.000217 | $0.000185 |
| should it speak | 40 | 209 ms | 250 ms | $0.000024 | $0.000011 |

- **Speed:** Decision 1 is about 100 ms slower on a fight decision and about 200 ms slower on the
  large requests. Both are far inside what a fight needs.
- **Cost:** the price a token is the same, and Decision 1 counts the same request as fewer tokens
  (1,813 against 2,583 on a fight decision), so a request costs about 30% less.

How often the two agree, on the same request:

| question | n | agree | where they differ |
|---|---|---|---|
| intent (fight, seek, loot, rest, leave, flee) | 300 | 89% | both ways, no pattern (fight or seek, leave or rest) |
| which creature | 288 | 92% | "none of them" against the first creature, both ways |
| in danger? (cut 0.625) | 300 | 91% same side | Decision 1 says yes more (17% against 12%); the two are close: means 0.22 and 0.23, correlation 0.90 |
| leave now? (cut 0.425) | 62 | 87% same side | Decision 1 says yes more (55% against 48%) |
| which spell | 70 | 53% | Decision 1 goes for the heavier spell (Flamestrike, Fireball) where Jev takes the quick cheap one, and is surer of it (0.64 against 0.51) |
| which song | 13 | 54% | too few to say |
| which corpse to loot | 11 | 9% | Decision 1 said "none is worth going to now" 10 times of 11, mostly with a fight still on |
| a place to stand | 19 | 68% | too few to say |
| move to another spot? (routine, cut 0.6) | 102 | 66% same side | Jev says yes 36% of the time, Decision 1 2% |
| stay here? (routine, cut 0.6) | 200 | 81% same side | the same yes rate (76% and 77%), on different moments |
| is this fact useful now? (cut 0.6) | 428 | 98% same side | Decision 1's numbers are much lower (mean 0.08 against 0.24); the same top three 67% of the time |
| does this query result help? (cut 0.5) | 774 | 87% same side | Decision 1 says yes more (58% against 48%) |
| is this memory worth showing? (cut 0.5) | 717 | 90% same side | the same yes rate (81%); the same top three 57% of the time |
| say something now? (cut 0.5) | 40 | 97.5% same side | |

- **Calibration** is the thing to watch: the policy's cuts were tuned on Jev's numbers. On the
  fight's own yes/no questions the two sit close (in danger: correlation 0.90, a mean difference
  of 0.10), so the gates mean nearly the same for both. On the routine "move to another spot" and
  on fact relevance Decision 1's numbers are far lower, so under Jev's cuts it would hardly ever
  move spot (2% against 36%), and would pass fewer facts where code takes those over a level.
- **Who is right where they differ** can't be read from agreement: the 151 fight disagreements are
  kept in the results file with their state and options. Read by hand, the spell picks are a
  difference of taste (heavier and slower against quicker and cheaper), Decision 1's "leave the
  corpse for now" is defensible with a creature still up, and its readier "in danger" would make
  for more leaves. The live benchmark below is the test of that.

**Live, on the judgment benchmark** (2026-10-10 21:58 to 2026-10-11 05:23, lane 2 on 2593, one
headless mage or its kit for each scenario, 10 rounds each, the two judges taking turns round by
round; `uv run uo-brain --port 5600 bench --scenarios mismatch,priority,loot,attrition,swarm,pack-gargoyles,pack-bone-knights,archer-kite,tamer-orcs,tamer-ogre-lord,bard-provoke,warrior-mage-opener,mage-tamer-orcs,necro-orcs,paladin-orcs
--judges jev,decision1 --rounds 10 --lane 2 --headless --account warrior --character Brutus
--server-port 2593 --out bench/2026-10-10-decision1.json`; $1.34 for the 14,941 calls):

| scenario | Jev | Decision 1 |
|---|---|---|
| mismatch | 9/10 (1 death) | 10/10 |
| priority | 9/10 | 6/10 |
| loot | 10/10 | 10/10 |
| attrition | 10/10 | 10/10 |
| swarm | 6/10 (1 death) | 6/10 (1 death) |
| pack-gargoyles | 9/10 (1 death) | 10/10 |
| pack-bone-knights | 10/10 | 10/10 |
| archer-kite | 6/10 | 9/10 |
| tamer-orcs | 9/10 | 10/10 |
| tamer-ogre-lord | 10/10 | 10/10 |
| bard-provoke | 3/10 | 9/10 |
| warrior-mage-opener | 10/10 | 10/10 |
| mage-tamer-orcs | 7/10 | 9/10 |
| necro-orcs | 10/10 | 10/10 |
| paladin-orcs | 10/10 | 10/10 |
| **all 15** | **128/150, 3 deaths** | **139/150, 1 death** |
| a call, on average | 239 ms, $0.000106 | 340 ms, $0.000073 |

- **Decision 1 won more rounds:** better on six scenarios, level on eight, worse on one. The big
  gaps are the bard (9 against 3: set an ogre and two orcs on each other and live while two die)
  and the archer's kite (9 against 6). On `priority` (three zombies close and an orcish mage
  casting from range: kill the mage first) it lost 4 of 10 where Jev lost 1; nobody died there, the
  rounds ran out of time or went for the zombies.
- **A loaded machine, the same for both.** The party runs of the same night were on this machine:
  6 or 7 clients were running in most rounds (1 to 9; 6.45 on average in Jev's rounds and 6.55 in
  Decision 1's), and the judges alternated, so the two met the same load. Benchmark results move
  with load, so the scores aren't for setting beside a quiet run's: Jev's 128 here says nothing
  about Jev on an idle machine.
- **Ten rounds a scenario, once.** A 3-round gap on ten rounds is within what a second run could
  turn round; six of them pointing the same way is more. The bard's 9 to 3 is the one to run again
  alone.
- **Slower, cheaper, no errors:** about 100 ms more a decision, two thirds the cost, and no failed
  request from either in 14,941.
- **Not the default then.** The benchmark is fights only, and its routine "move to another spot"
  (yes 2% of the time to Jev's 36%) and its fact relevance sit far from the numbers the cuts were
  tuned on; the soak pair below came next, and then [the split](#the-default-system-one-split-by-kind).

**The two gaps again, on a quiet machine** (2026-10-11 14:37 to 15:47, 2 clients running in every
round: the benchmark's and one idle; `uv run uo-brain --port 5600 bench --scenarios
bard-provoke,priority --judges jev,decision1 --rounds 10 --lane 2 --headless --account warrior
--character Brutus --server-port 2593 --out bench/2026-10-11-decision1-gaps.json`; $0.14 for
1,898 calls):

| scenario | Jev | Decision 1 | the night before (loaded), Jev against Decision 1 |
|---|---|---|---|
| bard-provoke | 3/10 | 7/10 | 3/10 against 9/10 |
| priority | 8/10 | 6/10 | 9/10 against 6/10 |

Both gaps held, the same way round: Decision 1 is the better bard (Jev's failures mostly ran the
two minutes out with one orc dead) and the worse at picking the caster out of a crowd. Nobody died
in either. Ten rounds a judge, once.

**On a hunt: a soak pair** (2026-10-11 17:14 to 19:20, an hour each, one after the other: Brutus,
a warrior, on 2593 with the same client, goal and packs sent at it at 23 and 40 minutes; memory
and escalation off, so only system one differs; from `brain/`, after `uv run python
scripts/soak_prep.py`: `SESSION_FLAGS="--profile P --no-memory --no-escalate --fresh"
scripts/soak_run.sh o6p2-P 1 yes "Earn gold hunting the open country north of Britain, not the
Britain graveyard (a lich is there). Keep yourself supplied with bandages from the Britain healer
and bank the gold at the West Britain bank."` with `P` `default` then `decision1`; `uv run
uo-brain soak-report logs/o6p2-P.jsonl`):

| | Jev (`default`) | Decision 1 (`decision1`) |
|---|---|---|
| minutes, hunts | 68.7, 12 | 61.0, 10 |
| kills, deaths | 31, 0 | 13, 0 |
| gold picked up, banked | 1,102, 736 | 811, 592 |
| fight decisions put to the model | 738 | 505 |
| routine questions; hunts it ended as "not worth staying" | 125; 0 | 84; 5 |
| "move to another spot?" over 0.6 | 8 of 87 | 6 of 59 |
| cost an hour (all calls) | $0.079 | $0.044 |

- **Decision 1 hunted less.** Five of its ten hunts ended 20 seconds in, on "is this spot still
  worth hunting?" at 0.12 to 0.27 with nothing in sight yet, under the cut Jev's numbers set
  (0.35); Jev never went below 0.35 in 125 questions. It also lost 2.5 minutes stuck twice at the
  same tile on one trip (1347,1512, a wall north of it; the same trip twice afterwards went
  through: cuo-4v1), and fought less (505 decisions to 738).
- **"Move to another spot" was close here** (6 of 59 over the cut, against 8 of 87), unlike the
  offline 2% against 36%. Fact relevance wasn't tested: with memory off nothing is put to that
  question, and the rerank keeps the top 8 whatever the scores.
- **Cheaper by half,** and neither died, both stood off the gargoyles and the lich.
- **One hour each, once:** the kills a hunt finds move with where it goes, and the two went to
  different spots. A first pair (`logs/o6p-*.jsonl`) was stopped: the client's bank and shop
  errands failed from minute 32 (cuo-rn2, fixed before this pair).
- **So not all of system one.** On fights Decision 1 won more rounds; on a hunt its routine answers
  sit lower than the cuts expect and it hunted less. Hence [the split](#the-default-system-one-split-by-kind).

#### The default: system one split by kind

Harry, 2026-10-12 (cuo-3ni): Jev for the calls it's measurably better at, Decision 1 for those
it's better at or where neither is. The built-in default and the `default`, `npc`, `sonnet` and
`opus` profiles are this split; `--profile jev` is Jev throughout, as before.

| kind of call | which is better, on what | goes to |
|---|---|---|
| `fight` | Decision 1: 139 rounds of 150 to 128 on the benchmark, and both its gaps held on a quiet machine | Decision 1 |
| `routine` (stay here, move spot, ...) | Jev: in the soak pair Decision 1's "stay here" ended 5 hunts of 10 under the cut, 13 kills to 31 | Jev |
| `strategy` (the player's strategy text to settings) | Jev: the eight templates, each compiled by both (2026-10-12, offline, a few cents), agreed on everything but one spell; told "finish the creature off with Lightning", Decision 1 said "no preference" (0.87, twice), Jev "Lightning" | Jev |
| `facts` (world facts for a fight) | level: wisp scenario, Jev 10/10, Decision 1 9/10; each picked the two deciding notes in all 10 selections, and the miss was a fight move (below) | Decision 1 |
| `rerank`, `memory`, `recorder`, `chat` | not measured beyond agreement (87% to 97.5% on the same side of the cut, [above](#jev-and-microsofts-decision-1)) | Decision 1 |

**Facts, in the wisp scenario** (2026-10-12, 10 rounds each, the two alternating, headless on lane 2,
2 clients running; `cd brain && UO_CLIENT_DIR=../bin/osx-arm64-next uv run uo-brain --port 5600 bench
--scenarios wisp-leave-alone --judges jev,decision1 --facts jev --rounds 10 --lane 2 --headless
--account warrior --character Brutus --server-port 2593 --out bench/2026-10-12-decision1-facts.json`;
$0.066): Jev 10/10, Decision 1 9/10, no deaths. Decision 1 picks facts fine where they matter,
whatever its low mean score across all of them. A single run.

**On a hunt: an hour of the split** (2026-10-12 08:48, set up as [the soak pair](#jev-and-microsofts-decision-1):
Brutus north of Britain, the same goal, the same two packs at minutes 23 and 40, memory and escalation
off; from `brain/`, after `uv run python scripts/soak_prep.py`: `SOAK_PORT=5580 SESSION_FLAGS="--profile
default --no-memory --no-escalate --fresh" scripts/soak_run.sh 3ni-split 1 yes "Earn gold hunting the
open country north of Britain, not the Britain graveyard (a lich is there). Keep yourself supplied with
bandages from the Britain healer and bank the gold at the West Britain bank."`, client
`bin/osx-arm64-next`; `uv run uo-brain soak-report logs/3ni-split.jsonl`). Not filmed. One hour, beside
the pair's two from the day before:

| | Jev | Decision 1 | the split |
|---|---|---|---|
| kills, deaths | 31, 0 | 13, 0 | 23, 0 |
| gold picked up, banked | 1,102, 736 | 811, 592 | 560, 504 |
| hunts ended under the "stay here" cut | 0 of 12 | 5 of 10 | 0 of 11 |
| "stay here", mean | 0.76 | | 0.77 |
| trips stuck | 0 | 2 | 0 |
| cost an hour | $0.079 | $0.044 | $0.058 |

- **The routine calls behave as Jev's again:** no hunt ended early, and "stay here" sat where Jev's
  did (0.77 against 0.76; never under the 0.35 cut in 95 questions).
- **Fewer kills and less gold than Jev's hour, for reasons outside system one:** the first three
  bandage buys failed ("the vendor did not show its goods", cuo-k0d), so it hunted 14 minutes on 30
  bandages; and after the gargoyles chased it out at minute 24 the planner took it east to goats,
  pigs and boars for the last half hour (7 kills and 53 gold a hunt), where Jev's hour had gone back to
  orcs and lizardmen.
- **A quarter cheaper than Jev's hour,** and no deaths. One hour, so a sign, not a measure.

On the benchmark the `jev` label still means Jev in every system-one call, so the results above stay
comparable; `profile` plays the active profile's own models, the split by default.

### Several agents on one server

The player's warrior on the `default` profile (Jev, and Sonnet: the default planner then) and NPC
warriors on the `npc` profile (Jev, Haiku, a budget), each with its own client and brain, on the
local server, 2026-10-08:

| run | agents | minutes | kills | deaths | together | an hour | player's character | each NPC |
|---|---|---|---|---|---|---|---|---|
| `uo-brain fleet fleets/graveyard-three.toml --hours 0.5` | 2 (the third refused) | 31-33 | 7 | 0 | $0.479 | $0.88 | $0.78/h | $0.108/h |
| `uo-brain fleet fleets/britain-five.toml` (0.33 h) | 5 | 20-24 | 20 | 0 | $0.306 | $0.78 | $0.41/h | $0.09-0.15/h |

Logs in `brain/logs/fleet/2026-10-08-*`; report: `uv run uo-brain fleet-report DIR`. Single runs.

- **What a character costs:** an NPC $0.09-0.15 an hour, three quarters of it Jev's fight decisions
  ($0.0001 each, 400-500 in 20 minutes) and most of the rest Haiku's planning ($0.0005 a call).
  That was before Jev was spared the calls that can't change anything: [fewer fight calls](#fewer-fight-calls) below. The
  player's character costs three to seven times as much, almost all of it Sonnet's planning ($0.007
  to $0.008 a call, 14 to 42 calls in a run).
- **The budget holds:** in the first run the `npc` cap was $0.10 an hour; the NPC sat just over it
  and spent most of the half hour resting "over budget", since Jev alone costs an active warrior
  about $0.08 an hour. The cap was then raised to $0.15, and in the second run the NPCs spent $0.09-0.15.
  With memory, reflections and talk on top it was raised again, to $0.50 (2026-10-09).
- **The machine:** a client takes 13-20% of one core and about 400 MB, a brain under 1% and 60-75 MB,
  the server about 1% and 300-400 MB, on a 12-core Mac with 24 GB. By those figures one machine
  could run several dozen agents before CPU or memory runs out; that hasn't been tried. (The 400 MB
  is ps's resident size; a windowed client's physical footprint, GPU memory included, is about
  740 MB, and a headless one's about 365 MB: [headless clients](#headless-clients).)
- **No rate limits:** no judge errors in either run, with five brains on one OpenRouter key.
- **Limits found:** ModernUO allows 10 accounts per address by default here; the third agent of the
  first run was refused ("ip already has 10 accounts") until `accountHandler.maxAccountsPerIP` was
  raised to 50 (`tools/modernuo/README.md`). Several NPCs at one spot share its spawns: in the first
  run the two at the graveyard made 7 kills in half an hour. The Haiku planner kept choosing the
  Britain sewer, which can't be walked to.

### Headless clients

On the local server, 2026-10-09 (cuo-1k7), with other agents' clients running on the same
machine (6 to 18 clients in all, counted with each result). Single runs.

**Load per client:** `uv run uo-brain fleet fleets/britain-five-headless.toml` (0.33 h; the
player's character windowed, the four NPCs headless; `uv run uo-brain fleet-report
logs/fleet/2026-10-09-britain-five-headless`), with the footprint sampled once a minute alongside:

| client | CPU, % of a core: avg (max) | resident max | physical footprint | updates/s avg (min) |
|---|---|---|---|---|
| Bram, windowed | 4.9 (17.0) | 402 MB | 739 MB avg, 764 max | 871 (772) |
| Mora, Wynn, Tess, headless | 1.8-3.2 (3.1-73.8) | 351-360 MB | 362-369 MB | 820-828 (530-760) |
| Holt, headless | 21.9 (62.6) | 348 MB | 354 MB | 658 (284) |

- **A third of the CPU, half the memory.** The resident size ps gives (the "about 400 MB" above)
  leaves out the GPU memory a windowed client's textures take: 342 MB of its 739 MB footprint.
  It also swings with memory compression (130 to 400 MB for one client within minutes), so the
  fleet now records the footprint too. The 2026-10-08 windowed clients took 13-20% of a core;
  this one took 4.9%. That was another day, window and load, so compare within one run.
- **Holt** kept engaging a rat it couldn't reach (under the street) and spent its CPU on failed
  path searches (cuo-2ct). A windowed client runs the same code; the map kept loaded headless was
  32 tiles then, bigger than a window's, which made each search dearer, and is now 24.
- **The rest of the run:** 20 kills, no deaths, $0.41 ($1.03 an hour). Bram's brain stopped after
  10 minutes on an SSL error from OpenRouter (cuo-15e). Vendors "did not show their goods" to
  windowed and headless clients alike, with five agents buying at one healer.
- **Idle and in fights:** at the bank for a minute, windowed 6.4% and headless 2.2%; side by side
  through 15 minutes of bench rounds, windowed 5.3% (max 8.7) and headless 2.0% (max 3.5).

**macOS App Nap didn't throttle it:** a hidden client left idle in game for 30 minutes, polled
every 3 minutes, ran 790-905 updates and 59-60 frames a second throughout at 1.2-1.9% CPU, and the
headless fleet and bench clients kept 790-830 updates a second on average. So the client takes no
App Nap assertion. Headless clients ran about 5% fewer updates a second than windowed ones
(792 against 842 in the bench), with no effect on play that showed.

**Same outcomes:** `uv run uo-brain --port 5610 bench --scenarios core --judges heuristic,jev
--rounds 3 --lane 4` (windowed) and, at the same time, `--port 5611 --lane 5 --headless`
(`bench/2026-10-09-headless-core-windowed.json`, `-headless.json`; with the 32-tile map):

| scenario | rules windowed | rules headless | Jev windowed | Jev headless |
|---|---|---|---|---|
| `mismatch` | 2/3, 1 death | 3/3 | 3/3 | 3/3 |
| `priority` | 0/3, 1 death | 0/3 | 3/3 | 3/3 |
| `loot` | 0/3 | 0/3 | 3/3 | 3/3 |
| `attrition` | 2/3, 1 death | 3/3 | 3/3 | 3/3 |
| `swarm` | 0/3, 3 deaths | 0/3, 3 deaths | 1/3 | 0/3, 1 death |

17 of 30 right windowed and 18 of 30 headless: the same within noise.

**The load effect** (the swarm went 0/10 with four bench clients at once and 4/10 alone on
2026-10-07): `swarm` with Jev, one client alone and four at once in lanes 4, 5, 6 and 9, headless
and then windowed, back to back on the final code (`uv run uo-brain --port P bench --scenarios swarm
--judges jev --rounds 10 --lane L [--headless]`; `bench/2026-10-09-headless-swarm-*.json`). Other
agents' clients ran throughout; the count is per round, all clients on the machine:

| clients | headless | windowed |
|---|---|---|
| one alone | 2/10, 3 deaths (9 on the machine) | 5/10, 1 death (13-16) |
| four at once | 17/40, 11 deaths (lanes 6, 6, 3, 2 of 10; 9-15) | 16/40, 8 deaths (lanes 5, 5, 3, 3; 12-18) |

- **No gap today to close:** four clients at once did as well as one alone, windowed (16/40
  against 5/10) and headless (17/40 against 2/10), and headless did as well as windowed. None of
  the differences is beyond chance (Fisher's exact test, p 0.28 to 1.0), and neither was
  2026-10-07's 0/10 against 4/10 (p 0.09). So these runs can't show that headless clients remove a
  load effect; they show that the effect didn't appear with four windowed clients either, and that
  a lane can go 6/10 or 2/10 in the same run. A load effect smaller than that spread would need
  many more rounds to see.
- **The load itself:** in the four-at-once runs each headless client took 2.1-2.2% of a core and
  each windowed one 4.8-5.0%, 790-890 updates a second either way.

**By hand, headless on the local server:** login; travel of 224 tiles from the bank to Britain
Cemetery in 52 s; banking 400 gold; buying 10 bandages; selling 40; reading a runebook and
recalling with a charge; a `[kick` sends it to the login screen and it logs in again. A test
build without the kept-loaded map stopped at the edge of its 8-by-8 block in the empty test field
(2 of 25 tiles); with it, the walk took 5 s.

### Fewer fight calls

The fight loop that asks Jev only when its answer can matter and can be new ([how](#how-it-works),
cuo-df8) against the loop as it was, which asked at every tick (`UO_BRAIN_ASK_EVERY_TICK=1`), side
by side on the local server, 2026-10-09.

**In a fleet:** four NPC warriors on the `npc` profile at once, two goals (hunting at the Britain
graveyard; earning gold near Britain), each goal played by one brain on the old loop and one on
the new (`uv run uo-brain fleet fleets/fewer-calls-ab.toml`, run twice, half an hour each; logs in
`brain/logs/fleet/2026-10-09-fewer-calls-ab` and `-ab-2`, report `uv run uo-brain fleet-report DIR`).
11 clients (other agents' and the benchmark's) were running at the start of the first run, 10 at
the second.

| run | goal | fight calls an hour, old → new | their cost an hour | all AI calls an hour |
|---|---|---|---|---|
| 1 | graveyard | 1,438 → 438 | $0.102 → $0.034 | $0.173 → $0.079 |
| 1 | near Britain | 1,161 → 409 | $0.081 → $0.029 | $0.123 → $0.053 |
| 2 | graveyard | 1,084 → 603 | $0.069 → $0.047 | $0.095 → $0.128 |
| 2 | near Britain | 1,249 → 374 | $0.082 → $0.028 | $0.134 → $0.074 |

- **Fight calls:** 63% fewer on average (1,233 an hour to 456), and 59% less spent on them ($0.084
  an hour to $0.035). The old loop was over the `npc` cap of $0.15 an hour at times in three of its
  four brains and decided less often then (5 to 68 decisions put off), so its real rate is higher
  than shown.
- **Everything together:** 36% less on average ($0.131 an hour to $0.084). In the one pair where it
  went up, Haiku's planning cost the new brain three times what it cost the old one ($0.061 an hour
  against $0.020), which is the planner, not the fight loop.
- **Where the calls went:** beside travel, banking, buying and resting the old loop asked 18-31
  times a minute and the new one 4-21; in hunts 25-33 against 6-13. Not asked in the new brains:
  211 to 825 decisions a run, two thirds of them the same state again in one run where a warrior
  spent minutes chasing a crossbill it couldn't reach.
- **Deaths:** 4 for the old loop, 2 for the new, all at the Britain graveyard, where a lich and
  bone knights walked that day. In each of the new loop's deaths the decisions before it were put
  to Jev; one came on a hunt's walk to the graveyard, which had no fight loop beside it in either
  loop at the time (cuo-sj2). Few kills (17 in all): the runs don't compare kill rates.
- Run 1 was on the first version (before the defend-only rule; [how it works](#how-it-works)), run 2
  on the final one. Two runs of four brains: the size of the cut is clear, its exact figure is not.

**In the benchmark**, `--judges jev/everytick,jev` plays the two loops round by round, interleaved,
on one lane (lane 7): `uv run uo-brain --port 5670 bench --scenarios core --judges jev/everytick,jev
--rounds 5 --lane 7` (11 clients running at the start; `bench/2026-10-09-fewer-calls-core.json`) and
the same with `--scenarios packs` (13 clients; `bench/2026-10-09-fewer-calls-packs.json`); the core
run on the first version, the packs on the final one. Right and deaths, fight calls and cost a
round, and the median of each round's lowest health:

| scenario | old loop | new loop | calls a round | cost a round | lowest health |
|---|---|---|---|---|---|
| `mismatch` | 5/5, 0 died | 5/5, 0 died | 55 → 42 | $0.0043 → $0.0034 | 59% / 60% |
| `priority` | 5/5, 0 | 4/5, 0 | 52 → 36 | $0.0038 → $0.0028 | 58% / 47% |
| `loot` | 5/5, 0 | 5/5, 0 | 34 → 19 | $0.0023 → $0.0014 | 89% / 93% |
| `attrition` | 5/5, 0 | 5/5, 0 | 88 → 70 | $0.0068 → $0.0057 | 85% / 88% |
| `swarm` (mage) | 1/5, 3 | 3/5, 1 | 57 → 85 | $0.0091 → $0.0133 | 0% / 45% |
| `pack-gargoyles` | 5/5, 0 | 5/5, 0 | 61 → 46 | $0.0046 → $0.0037 | 63% / 44% |
| `pack-bone-knights` | 5/5, 0 | 5/5, 0 | 60 → 45 | $0.0044 → $0.0035 | 55% / 52% |

- **Run again, the new loop first in each round:** the four warrior scenarios where its lowest
  health had come out lower (`uv run uo-brain --port 5670
  bench --scenarios mismatch,priority,pack-gargoyles,pack-bone-knights --judges jev,jev/everytick
  --rounds 5 --lane 7`, 4 clients at the start; `bench/2026-10-09-fewer-calls-warriors.json`): new
  18/20 with 1 death, old 19/20 with 1; 31.4 calls a minute of fighting against 41.2.
- **Right, all three runs together:** 50/55 for each loop, with 2 deaths for the new and 4 for the
  old. The 95% interval for the difference (Newcombe) is 12 points either way: the numbers rule out
  a large loss, not a small one. The rounds the new loop lost were decided by calls put to Jev: in
  `priority` it twice left an orcish mage nearly dead, which then ran off, so the round ended with
  the mage alive; in `pack-gargoyles` it died fleeing three gargoyles after a long fight with an
  orc, and in another round got away at 7% from gargoyles that had closed in while it looted (no
  decisions while looting, in either loop).
- **Calls:** 16% fewer a minute of fighting in the core set (42.7 to 35.8) and 24% fewer in the
  packs (40.0 to 30.3). In a fight the state changes nearly every second, so nearly every decision
  is still asked; the savings come before and after. In `swarm` the new loop's mage lived longer,
  so it made more calls.
- **Lowest health:** over the six warrior scenarios and all three runs, the new loop's lowest health
  averaged 2 points below the old's (a permutation test within each scenario: p = 0.45, no sign of a
  difference). It was lower in `pack-gargoyles` in both runs (42% against 58% on average), where the
  lowest point came in the opening melee with every decision put to Jev at the same times in both
  loops; that scenario's lowest health averaged between 42% and 67% in nine earlier runs of the old
  loop.

### Plans for fights

Plans against the fixed policy where the rules lost (cuo-6om), 5 rounds each, 2026-10-07
(`bench/2026-10-07-swarm-plans.json`, `attrition-plans.json`, `--judges "jev#<plan>"`). The
planner model wrote `planner-swarm` and `planner-attrition` from a one-paragraph description of
the character and the creatures it meets, not from the scenario (`uo-brain machine design`,
$0.018 and $0.015); `mage-swarm` and `pull-one` are the hand-written ones.

| scenario | Jev, no plan | Jev + hand-written plan | Jev + planner's plan |
|---|---|---|---|
| `swarm` | 4/10, 1 death | `mage-swarm` 2/5, 2 deaths | `planner-swarm` 1/5, 1 death |
| `attrition` | 5/5 | `pull-one` 5/5 | `planner-attrition` 5/5 |

- **No plan beat the fixed policy:** on `attrition` every judge got out alive once leaving packs and running were fixed, and on `swarm` the plans did worse. The planner's plan went from "blast" to "escape" early in every round and spent 12–68 s of its 120 escaping or recovering: four of five rounds survived, with 2–4 kills, short of the four needed. The hand-written plan left for 30 s whenever mana fell below 25%; of its two deaths, one came in its "blast" step and one in its first step after coming back.
- **The moves between steps work as written:** every round logged its moves with Jev's yes ("open -> blast (0.68)", "blast -> out (0.74)", "out -> open (after 30 s)").
- **What it costs:** a plan adds one to four yes/no questions to each fight request, about 300 input tokens.

### Walls and summons in a swarm

Jev put 6-10% of its spell answer on the spells that do no damage and never picked a field or a
summon, even with six monsters on the mage (cuo-ev9). Does a wall or a summon win swarm rounds?
Plans that keep one going whenever it can be cast (`brain/machines/mage-blades.json`,
`mage-paralyze-field.json`, `mage-energy-field.json`; Jev picks between casts) and the ward
question above, against plain Jev, 5 rounds each, lane 0, one client, 2026-10-08
(`uv run uo-brain bench --scenarios swarm --judges "jev,jev/ward,jev#mage-blades,jev#mage-paralyze-field,jev#mage-energy-field" --rounds 5`,
`bench/2026-10-08-ev9-swarm.json`; kills counted without the mage's own summons, which the
check counted then and doesn't now):

| | right | deaths | kills a round | walls and summons cast |
|---|---|---|---|---|
| Jev | 2/5 | 2 | 4, 1, 3, 0, 5 | none |
| Jev + keep Paralyze Field up | 1/5 | 2 | 3, 6, 1, 1, 2 | 1-14 a round |
| Jev + keep Energy Field up | 1/5 | 4 | 4, 6, 0, 1, 3 | 1-9 a round |
| Jev + keep Blade Spirits up | 1/5 | 4 | 6, 0, 2, 2, 5 | 4-7 a round |
| Jev + the ward question | 0/5 | 3 | 3, 3, 1, 1, 2 | 0-5 a round |

- **No wall or summon won rounds:** each cast is one that does no damage while six creatures hit
  the mage, and the crowd comes round a short wall. Jev rarely picking them looks right on this
  server. Energy Vortex wasn't tried: at Magery 90 an eighth-circle spell fizzles 3 times in 4.
- **The ward question** cast several fields of different kinds in a row in one round (each has its
  own recast timer); it now waits 15 s between them, which hasn't been measured. Five rounds is
  too few to tell small differences apart, but nothing here points to walls helping; it stays off.

### Line of sight

**The client's rule against the server's**, 2026-10-09 (cuo-ehm): at each of 14 spots around
Britain (streets, shop fronts, the bank, the river, the graveyard), the server's `[AgentSight
sample` picked tiles within 15 of the character and answered twice for each, a mobile standing
there and the ground there; the client's `sight` method answered the same points.

| run | pairs | agree | in sight (server) |
|---|---|---|---|
| `uv run uo-brain --port 5600 sight-check` (40 tiles a spot, seed 1), `bench/2026-10-09-sight-check.json` | 1120 | 1120 | 386 |
| `uv run uo-brain --port 5600 sight-check --seed 7 --per-spot 50`, `bench/2026-10-09-sight-check-seed7.json` | 1400 | 1400 | 506 |
| at the test field among `[AgentBuild` pieces (a stone wall, a window wall, a fence, LOS blockers, two huts): `uv run uo-brain --port 5600 sight-check --here --per-spot 200 --radius 12 --seed 3`, `bench/2026-10-09-sight-check-test-walls.json` | 400 | 400 | 116 |

All 2920 pairs agree, single runs.

**The benchmark**, before and after, 4 rounds each, 2026-10-09: the four `sight` scenarios,
with the rules and Jev, each with the client's sight checks on and off (`/nosight`, the
"before": no `in_sight`, aiming and walking as before), the four played back to back in every
round. Two clients at once, the archer scenarios on lane 0 and the mage ones on lane 1, with
other agents' clients running (9 to 14 clients in all):
`uv run uo-brain --port 5600 bench --scenarios sight-wall-archer,sight-hut-archer --judges heuristic,heuristic/nosight,jev,jev/nosight --rounds 4 --lane 0`
(`bench/2026-10-09-sight-archer.json`) and the same with `sight-wall-mage,sight-hut-mage`,
`--port 5601 --lane 1` (`bench/2026-10-09-sight-mage.json`). Each orc stands still, so only the
character's moving brings it into sight.

| scenario | judge | right | kills | aims at something out of sight, a round | seconds engaged with it out of sight, a round | last kill (s) |
|---|---|---|---|---|---|---|
| sight-wall-archer | rules, before | 0/4 | 4 of 8 | 0 | 89.8 | - |
| | rules | 4/4 | 8 of 8 | 0 | 1.6 | 34 |
| | Jev, before | 0/4 | 4 of 8 | 0 | 89.5 | - |
| | Jev | 4/4 | 8 of 8 | 0 | 1.6 | 30 |
| sight-hut-archer | rules, before | 0/4 | 0 of 4 | 0 | 89.9 | - |
| | rules | 4/4 | 4 of 4 | 0 | 3.1 | 21 |
| | Jev, before | 0/4 | 0 of 4 | 0 | 89.5 | - |
| | Jev | 4/4 | 4 of 4 | 0 | 3.1 | 21 |
| sight-wall-mage | rules, before | 0/4 | 0 of 8 | 20 | 90.0 | - |
| | rules | 4/4 | 8 of 8 | 0 | 1.4 | 42 |
| | Jev, before | 0/4 | 0 of 8 | 21 | 89.5 | - |
| | Jev | 4/4 | 8 of 8 | 0 | 1.4 | 24 |
| sight-hut-mage | rules, before | 0/4 | 0 of 4 | 20 | 89.8 | - |
| | rules | 4/4 | 4 of 4 | 0 | 3.1 | 16 |
| | Jev, before | 0/4 | 0 of 4 | 21 | 89.5 | - |
| | Jev | 4/4 | 4 of 4 | 0 | 3.1 | 14 |

- **Before**, every judge took on the orc nearest it, behind the wall or in the hut, and stayed on it for the whole 90 seconds: the archer never shot at it (in the wall rounds the orc in the open died without the agent ever targeting it: the server can make an attacker a player's combatant), and the mage cast at the hidden orc about 20 times a round, every spell lost. No round was won and no death in any.
- **After**, 32 of 32: the orc in sight first, then a walk round the wall (1.4 to 1.6 s out of sight) or to where the hut's doorway lines up (3.1 s), and no aim at anything out of sight.
- **The server's "Target can not be seen"** came 0 times in all 64 rounds, before as after: ModernUO says nothing when a spell misses for sight, and an archer out of sight just doesn't shoot. The aims out of sight above are counted by the client's rule instead (`stats.aimed_unseen`), which agreed with the server on every pair checked.
- **A first pass** (`bench/2026-10-09-sight-*-v1.json`, one round each) had Jev at 0/2 after: it killed the orc in sight, then rested by the one behind the wall. Its seek (0.8) was masked because the orc was within reach, and the target guidance as first worded ("can't be shot ... so prefer one in sight") read as "attack none" (0.94 to 0.98). Fixed before the run above: seeking is allowed when everything in reach is out of sight, and the words say it can still be fought by walking round.
- **The rule judge shoots from range now.** It used to fight only what was within 3 tiles (or, for a mage, in spell range), so an archer rested with orcs standing 6 to 8 tiles off; it now fights what is within shooting range too, before as after above. Earlier archer results for the rules (`archer-kite`) were with the old rule.
- **Cost:** Jev's rounds cost $0.19 in all (`costs` in the two files).

### Where it runs

Plumbing only: the rule judge, whose own `stand` pick is code's (`rule_spot`), so these runs
check that the places are offered, picked, walked to and logged, not whether Jev's places beat
the line away. Run on 2026-10-09 on lane 1 with one headless client, four clients on the machine,
single small runs:

```bash
uv run uo-brain --port 5600 bench --scenarios pack-pocket,pack-gargoyles --judges heuristic,heuristic/spots --rounds 2 --lane 1 --out bench/2026-10-09-spots-rule-plumbing.json
uv run uo-brain --port 5600 bench --scenarios archer-kite,swarm,sight-wall-mage --judges heuristic,heuristic/spots --rounds 1 --lane 1 --out bench/2026-10-09-spots-rule-plumbing-kite.json
uv run uo-brain --port 5600 bench --scenarios archer-kite --judges heuristic,heuristic/spots --rounds 2 --lane 1 --out bench/2026-10-09-spots-rule-plumbing-archer.json
```

| scenario | line away (`heuristic`) | code's place (`heuristic/spots`) |
|---|---|---|
| `pack-pocket` (new: a pocket of walls open towards the pack) | 2/2, 19 runs, contact broken in 3.5 and 3.8 s | 2/2, 20 runs all to a place, none with no path, 5.6 and 3.9 s |
| `pack-gargoyles` | 2/2, 23 runs, 3.8 and 4.3 s | 2/2, 26 runs all to a place, none with no path, 1.5 and 3.4 s |
| `archer-kite` (after the change below) | 2/2, 26 steps back, lowest health 45% and 24% | 2/2, 22 steps back all to a place, lowest health 41% and 48% |
| `swarm` | 0/1, died: no path away, cornered | 0/1, died: the place 14 steps southwest had no path for the client's pathfinder through the crowd, nor had any line away |
| `sight-wall-mage` | 1/1, no runs | 1/1, no runs |

- **The step back:** in the first `archer-kite` round with places, code's pick went to places 3
  steps off (4 tiles from the nearest orc was "far enough"), the orcs were on it again at once,
  and the archer left the fight at 26% with 2 of 4 orcs killed. Code now counts 5 tiles as far
  enough, as the client's own step back goes 5, and its steps back went to the 8-tile ring.
- **On the game thread:** the places took 2.4 ms (median), 5.0 ms at the 95th percentile and
  8.1 ms at most, over 255 decisions that had them; 30 to 49 places (median 49).

**Jev's places against the line away** (cuo-fhf.3), 2026-10-09, lane 2, one headless client.
Two other clients were running on the machine (a soak and a walk test), and the two judges took
turns round by round:

```bash
uv run uo-brain --port 5653 bench --scenarios spots --judges jev,jev/spots --rounds 10 --lane 2 --headless --out bench/2026-10-09-spots-jev.json
```

| scenario | line away (`jev`) | Jev's place (`jev/spots`) |
|---|---|---|
| `pack-gargoyles` | 10/10, no deaths, 124 runs, contact broken in 4.1 s (median) | 5/10, 5 deaths (4 while moving), 394 runs, 11 with no path to the place, 2 cornered, 5.8 s |
| `pack-bone-knights` | 10/10, 77 runs, 3.3 s | 10/10, 571 runs, 14.2 s |
| `pack-pocket` | 10/10, 86 runs, 4.1 s | 10/10, 526 runs, 11.8 s |
| `swarm` | 1/10, 3 deaths, 18 cornered, 8.6 s | 3/10, 6 deaths, 47 with no path to the place, 17 cornered, 42.9 s |
| `archer-kite` | 6/10, no deaths, 3.5 s | 5/10, 1 death (moving), 31.4 s |
| `sight-wall-mage` | 10/10, no runs | 10/10, no runs |
| all | **47/60, 3 deaths** | **43/60, 12 deaths** |
| tokens and cost a fight call | 1,800-3,800, $0.00008-0.00016 | 3,700-6,600, $0.00016-0.00028 (about twice) |

Jev's places lost, so they stay off, and moving while acting (cuo-fhf.4) doesn't go ahead. Why:

- **A new place every decision.** Each decision Jev picks again, and often somewhere else: 3 steps
  northeast, then 3 east, 14 north-northeast, 8 east. A run that code makes in one straight line
  became several short walks in turn. Breaking contact took 1.4 to 9 times as long, and a chaser
  in melee stayed beside the character the whole way.
- **Little conviction.** In the gargoyle death traced, Jev picked its places at 0.13 to 0.30. Among
  about 50 places that is close to a guess.
- **No path, then cornered.** 11 picks on the gargoyles and 47 in the swarm had no path for the
  client's pathfinder. Two failed runs count as cornered, and the warrior fought where it stood:
  4 of the 5 gargoyle deaths came while moving.
- **Twice the tokens:** the places add about 2,000 to 2,900 tokens to every fight call.

**Again with places kept and leaves sent far** (cuo-fhf.5), 2026-10-10, the same lane. This time
it was the only client on the machine, and both judges ran again:

```bash
uv run uo-brain --port 5653 bench --scenarios spots --judges jev,jev/spots --rounds 10 --lane 2 --headless --out bench/2026-10-10-spots-jev-kept.json
```

| scenario | line away (`jev`) | Jev's place (`jev/spots`) |
|---|---|---|
| `pack-gargoyles` | 10/10, no deaths, 121 runs, 3.9 s | 7/10, 3 deaths (all moving), 158 runs, 4.1 s |
| `pack-bone-knights` | 10/10, 76 runs, 3.0 s | 10/10, 236 runs, 6.2 s |
| `pack-pocket` | 10/10, 96 runs, 4.1 s | 10/10, 188 runs, 4.3 s |
| `swarm` | 1/10, 4 deaths, 5.3 s | 0/10, 7 deaths, 37 with no path to the place, 11.6 s |
| `archer-kite` | 7/10, no deaths, 2.8 s | 7/10, no deaths, 9.1 s |
| `sight-wall-mage` | 9/10 | 10/10 |
| all | **47/60, 4 deaths** | **44/60, 10 deaths** |

- **Sending leaves far** cut the runs to between a third and a half of before, and the time to
  break contact fell to within 0.2 to 6 s of the line away's.
- **Keeping to a place** hardly came up (24 times). While a run is under way the brain sends no
  new one, so the place was mostly reached before the next pick.
- **Still weaving.** In the gargoyle deaths, each leave of 8 or 14 steps pointed a new way:
  east-southeast, northeast, north-northeast, north, northwest in one round. The gargoyles' spells
  reached the warrior meanwhile. Code's line away keeps one heading, away from all of them.
- **Still off,** and moving while acting (cuo-fhf.4) doesn't go ahead. A next try would hold a
  leave to the heading it first took, which comes close to the line away.

**Code's pick of the places, with Jev deciding whether to move** (`jev/codespots`), 2026-10-10,
the same lane. Jev isn't asked where to go, so it costs no extra tokens. Rounds 1 to 26 (all of
`pack-gargoyles` and 6 of `pack-bone-knights`) ran with 3 to 5 windowed clients recording a video
on the same server; the judges took turns, so both had that load:

```bash
uv run uo-brain --port 5653 bench --scenarios spots --judges jev,jev/codespots --rounds 10 --lane 2 --headless --out bench/2026-10-10-spots-codespots.json
```

| scenario | line away (`jev`) | code's place (`jev/codespots`) |
|---|---|---|
| `pack-gargoyles`, `pack-bone-knights`, `pack-pocket` | 10/10 each, contact broken in 3.5, 3.8, 4.1 s | 10/10 each, 3.1, 3.4, 4.3 s |
| `swarm` | 2/10, 3 deaths, 13 cornered | 4/10, 4 deaths, 16 cornered, 45 places with no path (then the line away) |
| `archer-kite` | 7/10, no deaths, lowest health 3-48% | 4/10, no deaths, lowest health 28-64%, fewer kills in the time |
| `sight-wall-mage` | 10/10 | 10/10 |
| all | **49/60, 3 deaths** | **48/60, 4 deaths** |

A tie. Code's places kept the archer healthier but cost it shots, and the swarm's 4/10 against
2/10 is within chance at 10 rounds. With no clear gain they stay off. What's left of the idea is
Jev deciding when a leave may stop (cuo-fhf.6).

### When a leave stops

Jev saying when a leave may stop (`jev/jevstop`, cuo-fhf.6), against code's 40-second rule
(`jev`), 2026-10-10, lane 2, one headless bench client. Five or six other clients were running for
part of it (the party runs), and the judges took turns round by round:

```bash
uv run uo-brain --port 5653 bench --scenarios pack-gargoyles,pack-bone-knights,pack-pocket,mismatch,attrition,tamer-ogre-lord,swarm --judges jev,jev/jevstop --rounds 10 --lane 2 --headless --out bench/2026-10-10-leave-stop.json
```

| | code's rule (`jev`) | Jev's call (`jev/jevstop`) |
|---|---|---|
| right | 65 of 70, 4 deaths (all in the swarm) | 64 of 70, 5 deaths (all in the swarm) |
| leaves | 111, 32.2 s each on average | 332, 9.1 s each |
| all the time spent leaving | 3,570 s | 3,036 s |
| caught again within 30 s of a leave ending | 79 (71% of leaves) | 170 (51%) |
| input tokens a fight call | 1,830-3,980 | 1,950-3,530 |

- **What changed:** Jev ends leaves much sooner, often within the first run, and the time spent
  leaving fell by 15%. But stopping that soon brings the creatures back, and the character leaves
  again: three times as many leaves, and twice as many catches within 30 s. The wins didn't move.
- **A first try was stopped after 42 rounds.** Asked with only "far away" and war mode for each
  creature, Jev answered 0.3–0.5 nearly every time and stopped 8 leaves in 451 asks. The
  creatures' `moving` words were added, and the run above is with them.
- **So it stays off.** A stricter cut (0.75) or a "nothing closing in" floor might trade back some
  of the stopping, which nobody has tried yet.

### Real input

The client was driven with OS-level mouse and keyboard events posted through the macOS HID
event tap, the same path as a physical device.

**Hand-back**, 2026-10-06: 7 checks, all passed, one after a fix.

<details>
<summary>The checks</summary>

| check | result |
|---|---|
| Right-mouse movement stops a walk the agent started | pass |
| Brain actions are deferred while the player is in control | pass |
| The agent takes over again 4 s after the last input | pass |
| Arrow keys and a click in the world hand control back | pass |
| A war-mode double-click on another monster: the agent follows the player's choice | pass, after a fix (below) |
| Clicking the panel (mode, templates, typing a strategy line) doesn't pause the agent | pass |
| Right-clicking a gump closed it without pausing the agent | pass |

</details>

It found two gaps, both fixed:
- **War mode:** the agent fought without war mode on, so a player's double-click on another monster opened its paperdoll instead of attacking. It now turns war mode on when it engages.
- **Peek throttling:** the server throttles use requests, so a peek right after re-equipping could fail silently. It now retries after 2 s.

**Combat assist and the keys**, 2026-10-06, single runs, the same tool: 9 checks, all passed, three after a fix.

<details>
<summary>The checks</summary>

| check | result |
|---|---|
| Alt+A switches combat assist and auto, and back | pass, after a fix (below) |
| Two orcs attack: the warrior fights them without the character moving | pass |
| The player double-clicks the other orc: the agent switches to it | pass |
| The player walks 7 tiles with the right mouse button mid-fight: the fight goes on, no fight action deferred | pass |
| In auto, walking hands control back, and the agent takes over again within 6 s | pass |
| Fights set to "on your key": Alt+D does the pending suggestion (attack) | pass, after a fix (below) |
| A mage with a monster 11 tiles away: "out of spell range" above its head, nothing cast | pass |
| The same monster 6 tiles away: the mage casts at it until it dies (17 s), without moving | pass |
| The panel dragged, logged out from the paperdoll, logged back in: it reopens where it was | pass, after a fix (below) |

</details>

It found three gaps, all fixed:
- **Alt+letter macros on a Mac:** SDL3 reports the key with modifiers applied, and Option composes a character ("a" becomes "å"), so no Alt+letter macro could match, ClassicUO's default Alt+P paperdoll included. Macros are now matched by the key itself, and a key that ran a macro no longer types its character into the chat line.
- **Option+N is a dead key** on a Mac (it starts an accented letter), so the next-move key is Alt+D, not Alt+N.
- **The panel covered the quit dialog:** it was drawn on top of everything, including modal dialogs, so their buttons couldn't be clicked. It now sits with the other gumps.

### Unattended runs

A warrior on the real Felucca map with nobody at the keyboard, given the `graveyard` goal
(hunt the undead at the Britain graveyard from the Britain bank, keep supplied with bandages,
bank the gold), starting at the bank with 30 bandages and 400 gold. A script applied disruptions
from outside, quietly so the agent couldn't read about them (`[AgentDisrupt ... quiet`):
- at 15 minutes, the graveyard's spawners were emptied and stopped for 12 minutes;
- at 30 minutes, or once the character was away, a lich and two bone knights went in;
- at 40 minutes, the Britain healers were sold out of bandages.

Report: `uo-brain soak-report logs/soak-goal1d.jsonl --disruptions logs/soak-goal1d.disruptions.jsonl`.

It took four runs on 2026-10-07 to get through the hour. Each of the first three found
something, fixed before the next:

| run | how it ended | what it found |
|---|---|---|
| 1 | died at 36 min | killed by the lich while resting at the graveyard: only hunts ran the fight loop, so rest, travel and errands had nothing but the healing reflexes. Also: an errand the session had given up on took over the next trip; wandering healers were tried for bandages; a purchase's cost was miscounted; the planner banked every coin and then tried to buy; Jev said "walk elsewhere in the spawn" round an emptied graveyard for 7 minutes |
| 2 | died at 31 min | the new defend-only loop chased a harmless crossbill, and the walk waited 10 minutes; two bone knights took the warrior from 100% to 25% in 10 s while Jev's intent stayed on fighting |
| 3 | died at 33 min | Jev said leave (0.43–0.45) as the bone knights came, but a fact check overruled it; the failed purchase message ("none of that on sale, or not enough gold") sent the planner back to a sold-out healer three times |
| 4 | the full hour, no deaths | |

Run 4: 62 minutes and 22 goals: 10 hunts, 5 rests, 4 trips, 2 bankings and a purchase. It
had 2 kills, banked 332 gold and spent $0.58 on the planner (44 calls) and $0.07 on Jev. It
waited out the emptied graveyard and moved between its spawners. The stronger undead went in
while it was banking; on its return it saw 11 creatures and headed back to the bank, where the
hour ran out on the way. Kills were few because earlier runs had thinned the graveyard's
spawns, which come back over minutes. Most hunts ended on "Jev unsure twice running" after a
minute with nothing in sight, which is why it called the planner so often.

The open goal, `earn gold hunting near Britain` with no place named, the same character, no
disruptions (`logs/soak-goal2b.jsonl`), 25 minutes:
- **Where to hunt:** the planner asked `hunting_spots` and tried the Britain sewer first. It is underground and can't be walked to, so it tried the graveyard ("good past gold results") and, once that was empty, the open country north of the Britain suburbs.
- **Results:** 19 kills, then the character died there to a group of black bears with a corpser nearby, before it banked anything. The gold it carried stayed on its corpse.
- **Cost:** 18 planner calls ($0.58 an hour).
- **What changed after it:** the run before it had died the same way at the graveyard, sent there while a lich was still about. So every goal's result now names the stronger creatures seen during it, as "danger" notes the planner's `hunting_spots` shows for the area.

**Packs, unattended** (cuo-d28.9): the graveyard goal again, on a copy of the server, a
warrior with a runebook to the bank and the graveyard, and two packs sent at it quietly by
`brain/scripts/soak_packs.py`: three gargoyles 14 tiles from it at 8 minutes, and a lich with
two bone knights at the graveyard at 25 minutes, each once it is hunting there (or 15 minutes
later regardless). From `brain/`, prepared with `uv run python scripts/soak_prep.py runes` and run with
`scripts/soak_run.sh soak-packs5 0.85 yes "Hunt the undead at the Britain graveyard, starting from the West Britain bank. Keep yourself supplied: bandages for a warrior, reagents for a mage. Bank your gold and loot when the bag gets heavy."`;
report: `uo-brain soak-report logs/soak-packs5.jsonl --disruptions logs/soak-packs5.disruptions.jsonl`.
Five runs on 2026-10-07, each fixing what the one before found:

| run | how it ended | what it found |
|---|---|---|
| 1 | died at 16 min, no kills | a runebook recall is a magery cast on ModernUO, so it dropped the katana into the pack and the warrior fought bare-handed; the healers were still sold out from earlier runs |
| 2 | died at 1 min | after leaving two ghouls and a zombie it stopped to recall to the bank with them still on it |
| 3 | died at 11 min | the "had to leave" note was picked as a fight fact on the next hunt, which left at once and wrote another; the planner then sent it back to the gargoyles |
| 4 | died at 24 min | a bank errand banked the katana; and after leaving two ghouls twice it was sent back once a 15-minute refusal ran out |
| 5 | 56 minutes, no deaths | |

The fixes: the weapon goes back in hand after a recall (and, in auto mode, whenever it is in the
pack), and errands never move the held weapon or anything worn; the retreat to town recalls only
with nothing within 10 tiles and nothing in war mode, and runs with the fight loop beside it;
danger notes are for the planner only; and code refuses a hunt within 30 tiles of a place it
left for 30 minutes, telling the planner why.

Run 5: 23 goals, 14 kills, 830 gold banked, $0.27 on the planner (32 calls) and $0.08 on Jev
(1,092 fight decisions, 39 routine questions). It left the graveyard twice: at 12 minutes from
two of the gargoyles ("2 coming at once: 2 gargoyles (a fair fight and a spellcaster each);
together far stronger than you"), and at 43 minutes, back after the refusal, from six ("2
gargoyles, 3 skeletons and a ghoul"); the lich and bone knights had gone in at 40 minutes while
it rested in town. It then hunted the weaker spawns in the open country to the north. The cost of
staying alive was time: 31.7 of the 56 minutes were rests, the planner choosing to wait out the
refusal at the graveyard rather than hunt somewhere else.

**The open goal with packs about**: the same open goal on the main server, a warrior, no
disruptions, where the open country north of Britain has gazers, reapers, ettins and trolls.
`uv run uo-brain --port 5579 session "Earn gold hunting near Britain. Choose hunting spots that suit the character, and move on if one is empty or too dangerous. Keep yourself supplied and bank the gold." --hours 0.67 --log logs/soak-open5.jsonl`, 2026-10-07:

| run | how it ended | what it found |
|---|---|---|
| 3 | died at 16.5 min, 24 kills | it left two gargoyles and ran off the road into the wilds, among gazers, reapers and an ettin; leaving while travelling now keeps to the route |
| 4 | died at 7.7 min, 2 kills | a troll, a harpy and a ratman together weighed 2.5, under the pack cut of 3 then; the cut is now 2.5 |
| 5 | died at 40 min, 19 kills | see below |

Run 5: 10 hunts, 452 gold banked, $0.25 on the planner (33 calls) and $0.06 on Jev. It left three
packs (3 ghouls; a panther and 2 ogres; a dire wolf, a gazer, a bull and a corpser) and lived
through all three. It died to a single gargoyle, a fair fight for it: with no heal potions left,
a slipped bandage and poison, it went from 76% to 11% health in about 2.5 seconds. Code fled at
11%, but one decision later the gargoyle was no longer close, flee wasn't on offer, and it went
back in to fight. Fixed since: near death, fleeing now stays on offer while anything aggressive
is within 8 tiles (cuo-d28.10).

### Travel and errands

On the local server, 2026-10-06, single runs with a warrior (the `uo-brain do` commands
[above](#travel-banking-shops-and-hunting)):

| goal | result |
|---|---|
| West Britain bank to the Britain graveyard | arrived in 57 s (226 tiles); one stop at the bank door, opened |
| and back | arrived in 63 s |
| the same with an invisible wall across the route (`[AgentWall`) | stuck twice, planned round it, arrived in 42 s |
| deposit gold, loot and 150 bandages, then withdraw 100 | done, the pack went from 200 bandages to 50 to 150 |
| buy 50 bandages, starting at the bank | bought 50 from the Britain healer for 250 gold |
| sell loot to a weaponsmith, gems to a jeweller | +54 and +426 gold; the junk stayed |
| a mage at the graveyard travels to the West Britain bank with a runebook in the pack | recalled, 11.5 s (63 s walking) |
| Gate Travel from the runebook at the bank, then through the gate | at the graveyard; the town-exit warning answered |

**Doors, houses and long trips** (cuo-d3i). First, how travel had gone in the 39 session,
soak and fleet logs of 2026-10-06 to 08 (`uv run python scripts/travel_counts.py <logs> --until 1791518400`):

| | count |
|---|---|
| set-offs | 257: 222 walked, 31 "too far" (the Cove and East Yew healers, the Britain sewer and Sanctuary), 2 "no route", 2 refused (move off) |
| walks | 216: 202 arrived, 7 stuck, 3 timed out (fleeing or fighting, not travel), 2 stopped, 1 no route, 1 too far |
| stuck spots | 61 in 26 walks, at least 8 s each (488 s); 42 of them at two spots, by the Britain healer and in the graveyard, where the plan was a single step that the walker never took: 7 walks gave up as stuck after 48 s each |
| doors opened | 54, at 6 doors, 3 of them a retry within a minute |

Then routes on the local server, 2026-10-09, a warrior, the client before the change and after
it side by side, taking turns on each route (6 to 8 clients running on the machine). Each run is
`uv run python scripts/travel_trip.py PORT X,Y,DISTANCE --go X,Y --park X,Y`; results in
`brain/bench/2026-10-09-travel.json`:

| route | before | after |
|---|---|---|
| West Britain bank to the Britain healer, two closed doors (3 runs each) | arrived in 26.2–27.0 s, standing still 2.3–2.9 s | arrived in 26.0–26.1 s, standing still 2.3–2.8 s |
| one step at the healer (from 1471,1609 to within 2 of 1474,1610), single runs | stuck 6 times, gave up after 48 s | arrived in 0.5 s |
| one step in the graveyard (from 1371,1479 to within 3 of 1369,1475), single runs | stood still 9.3 s, stuck once | arrived in 0.5 s |
| 85 tiles north to south through a stone house placed with its deed (2 runs) | arrived in 20.2 s both times | 20.0 s, planned round it when it came into view; then 20.5 s, round it from the start |
| 65 tiles past a tower that fills a valley (2 runs) | 15.3–15.5 s | 14.2–14.7 s, the same way: on sight, then from the start |
| into that stone house, whose door is locked to the character, single runs | clicked the door every few seconds until the 90 s timeout | "no route" after 6.3 s: the door stayed shut after two tries, so it was blocked |
| West Britain bank to the Cove healer, 820 tiles, single runs | "too far" | set off in hops (5, plans of at most 60 ms), then stuck 5 times in the hills west of Cove: 246 tiles short after 237 s (cuo-06u, fixed: below) |

Planning, from the bank (the `act travel` round trip; the plan runs inside it, on the game thread):

| goal | before | after |
|---|---|---|
| the Britain graveyard, 245 tiles | 3.6 ms | 6.9 ms |
| 1650,2150, 460 tiles away | 670 tiles, 20–34 ms | the first hop, 6 ms |
| the sea at 1700,1700 and three other places nowhere to stand | "no route" in 22–66 ms | "no route" in 0.3–0.5 ms |
| near the Orc Cave entrance, 371 tiles | "no route", 26–43 ms | the first hop, 28 ms |
| the Cove healer; East Yew healer | "too far", 1 ms | the first hop, 7 ms; 28 ms |
| the Britain sewer | "too far", 1 ms | "no route: the dungeons lie apart from the land", under 1 ms |

The longest plan seen after the change was 60 ms (the last stretch to Cove, through 252,000
tiles); one plan's search is capped at 400,000 tiles, so planning stays on the game thread.

Then fresh soaks with the new client, 2026-10-09, on the local server (`SOAK_PORT=5641 scripts/soak_run.sh d3i-soak3 0.5 no "Earn gold hunting the open country north of Britain, not the Britain graveyard (a lich is there). Keep yourself supplied with bandages from the Britain healer and bank the gold at the West Britain bank."`,
and two runs with the open goal that died to a lich at the graveyard within 2 minutes), counted
with `uv run python scripts/travel_counts.py logs`: 12 set-offs, 10 walked and 2 "no route" at
once (the Britain sewer); 8 walks, all arrived, two of them in hops (376 and 409 tiles); no stuck
spots; 4 doors opened, no retries; planning at most 10.3 ms. Eight walks is a small sample next to
the 216 before. The longer run lasted 22 minutes and died on the way to the bank, leaving an
ettin, a reaper and a gargoyle (`uo-brain soak-report logs/d3i-soak3.jsonl`).

**The walk to Cove** (cuo-06u), 2026-10-09, local server. The walk from the West Britain bank
to the Cove healer had been stuck 5 times in the hills west of Cove. The `nav` view's new `check`
walks the client's own pathfinder out from the player and marks where it and the planner disagree. Over
360 by 360 tiles west of Cove (radius 180 from 2060,1200) they agreed on 65,502 tiles and
differed on 75 scattered ones (creatures and items), none of them a way through. The mountains
west of Cove are closed to both, and the narrow strip at x 2170–2178 is a closed canyon. Cove
opens to the north and through its east gate, so the planner's way round the north of the range
(803 tiles from the forest west of Cove) is right.

The stuck spots came from the walker. It counted progress only as the straight-line distance to
the goal going down, and on that detour the distance grows for minutes, so every 8 s spent walking
north or west counted as stuck. Progress now also counts getting further along the path.

`uv run python scripts/travel_trip.py PORT 2245,1231,2 --go X,Y --timeout 600 --defend`, the client
before this change (base, with cuo-d3i) and after it, 13 to 15 clients running; results in
`brain/bench/2026-10-09-travel-cove-and-house-rows.json`:

| route | base | after |
|---|---|---|
| from the forest west of Cove (2105,1235) to the Cove healer, single runs | stuck at 2070,1220, 2040,1185 and 2016,1148 within 27 s, the spots the bead found, and 3 more going north; gave up as stuck after 83 s, 300 tiles short | arrived in 190 s, round the north and in by the east gate: 803 tiles planned in 47 ms, no stuck spots, one fight, two doors |
| from 2079,1226, single runs | stuck 3 times in 56 s, then killed by a bog thing at 2099,986 | no stuck spots in 64 s, then killed at 2098,986 |

The first run went through the fight loop in a wrapper; the second used `--defend`. No try at the
whole trip from the bank got as far as the hills: four tries died or were held in fights on the
way east of Britain (two ettins and an ogre, reapers, something that paralysed), with the rule
judge's fight loop or none.

**The whole walk to Cove under Jev** (cuo-1ts), 2026-10-09, local server. A fresh warrior
(`[AgentReset`, `[AgentKit warrior`, 50 bandages) walked from the West Britain bank to the Cove
healer with Jev's defend-only fight loop beside it:
`uv run uo-brain --port 5666 do travel "Cove healer" --defend --timeout 1800 --log logs/1ts-walk-N.jsonl`.
It was a headless client, with a bench client and a soak running at the same time. Single runs:

| walk | outcome | time | walked | lowest health | Jev's fight decisions |
|---|---|---|---|---|---|
| 1, before the fix below | died at 1800,1524, east of Britain | 125 s | | 17% | 87, $0.0059 |
| 2 | arrived | 357 s | 1,514 tiles | 54% | 161, $0.0087 (6 runs, 1 attack) |
| 3 | arrived | 392 s | 1,500 tiles | 43% | 221, $0.0116 |
| 4 | arrived | 379 s | 1,496 tiles | 5% | 196, $0.0111 (one `disengage`) |

Each walk that arrived had no stuck spots and passed 3 doors in 2 hops. "Walked" is the length of
the route the world store kept.

Walk 1 found a bug. Jev chose to leave an ettin and a reaper at 1.0 for 35 s, and the leave kept
to the road ("the trip goes on") because nothing was ahead. But it sent nothing while the client
was still engaged on the reaper, and travel waits while the agent is engaged. So the warrior stood
beside them until it died. A road leave now sends `disengage` (a new act verb) first.

Walk 4 used it. The warrior fought the reaper by the road for 40 s, down to 5% health. Then the
leave let the reaper go, the walk went on out of its reach, and the warrior healed on the way and
arrived.

The same stall was behind the long soak's deaths ([below](#long-range-memory-over-a-long-soak)):
12 of run A's 15 deaths, and 9 of run B's 11, came after at least three road leaves among the
last twelve decisions, all while still engaged.

**Walking past what can't move** (cuo-evh), the same day and walk, with the store re-imported so
that it knows a reaper never moves. This ran on a copy of the store, so the soak running beside it
was unchanged:

```bash
uv run uo-brain --port 5666 do --shard evh-check travel "Cove healer" --defend --timeout 1800 --log logs/evh-walk-N.jsonl
```

| walk | outcome | time | walked | lowest health | the reaper by the road | Jev's fight decisions |
|---|---|---|---|---|---|---|
| 5 | arrived | 527 s | 1,936 tiles | 51% | in view 6 s, not fought | 268, $0.014 (19 runs, 1 stuck spot) |
| 6 | arrived | 381 s | 1,482 tiles | 48% | in view 7 s, not fought | 206, $0.011 |

Before this, walks 2 to 4 fought the reaper every time, for up to 40 s. Walk 5's runs from other
creatures took it a longer way round. Single runs.

**Rows of houses** (cuo-ql6), 2026-10-09, local server. Seven small houses (each 8 tiles
square) placed with deeds by a staff character in a row across y 1327–1334 in the forest north of Britain, removed afterwards.
Two routes: across the row (1445,1280 to within 1 of 1445,1385; the row comes into view on the
way) and just behind it (1445,1312 to within 1 of 1445,1345; the row in view from the start).
Three clients took turns: before cuo-d3i (4f653589f), base (with cuo-d3i) and after (this
change), 9 to 14 clients running. Each run is
`uv run python scripts/travel_trip.py PORT 1445,1385,1 --go 1445,1280 --park X,Y --timeout 180`
(and `1445,1345,1 --go 1445,1312`), with the rule judge's fight loop beside it; results in
`brain/bench/2026-10-09-travel-cove-and-house-rows.json`. Two rounds each; in round 1 the d3i
clients see the row for the first time.

| row | route | before cuo-d3i | base | after |
|---|---|---|---|---|
| none | across | 24.0 s | 25.3 s (still planning round the row it remembered) | — |
| none | behind | 7.6 s | 8.6 s | — |
| solid, 50 tiles (neighbours touching) | across | stuck 6 times at the row's east end, gave up after 67 s (both rounds) | arrived, no stuck spots: 166 s (134 s of it standing in a fight), then 25.3 s | arrived, no stuck spots: 141 s (116 s of it in a fight), then 25.3 s |
| solid | behind | stuck 3 times, then clicked the end house's locked door until the 180 s timeout (both rounds) | 15.2 s, 15.2 s | 14.9 s, 14.9 s |
| a one-tile alley between each pair, 62 tiles | across | arrived: 71.8 s (a fight), then 24.5 s | 23.5 s, 24.0 s | 23.5 s, 24.0 s |
| alleys | behind | arrived: 58.7 s (a fight), then 8.3 s | 7.6 s, 7.6 s | 7.6 s, 7.6 s |

A solid row stops the walker from before cuo-d3i: its plan runs through the houses it can't see
in the map files. With cuo-d3i the walk plans round the row once it comes into view (once or twice
on the way, as more of the row is sent) and from the start once known; going round costs the
route behind the row about 7 s. Alleys one tile wide are walked through, as fast as with no houses.
This change leaves houses as they were (base and after match).

### Routine hunt calls

Jev's routine hunt calls, 2026-10-07, one run over 10 hand-made situations with no game
connected (`cd brain && uv run python tests/smoke_routine.py`): every situation got the
right verdict (stay, head back, leave the spot, walk elsewhere, wait), and 13 of 14 answers
fell on the expected side of 0.5. The miss was "is this spot still worth it?" at 0.48 after
two quiet minutes with steady kills; the confident "walk elsewhere" (0.69) decided it.
Several right answers were close to the line (0.66 for a 93% full bag, 0.64 and 0.71 for
staying), so the 0.65 and 0.35 thresholds need a live hunt to settle. A question is about
1,000 input tokens, $0.00004, answered in about 250 ms; at a question every 30 to 60 s that
is well under a cent an hour, against $0.014 for one planner call. The first wording got 7
of 10: it gave a mage's reagents and the bag without what they meant, so code now works out
how many kills the scarcest reagent lasts, the usual time between kills, and says when the
bag leaves little room for loot.

### World store and fact picking

**Import:** `uo-brain world import-modernuo` on 2026-10-06 gave 1,152 places, 91 regions,
8,742 spawn rows (one per creature type per spawner), 403 creatures and 230 teleporter routes.

**Picking facts for a fight,** single smoke run on 2026-10-07 with real Jev, on the
world-fact benchmark's store, a warrior at the test field with two orcs adjacent:
- **A wisp also in view:** a 25-fact shortlist. The two notes saying wisps never attack first and kill this kit scored 0.74 and 0.73, and the third pick was "A wisp's corpse often holds gems and plenty of gold." at 0.66: a tempting fact, not a misleading one. Nothing else reached 0.6. The request took 504 ms for 6,086 input tokens ($0.00026).
- **Only the orcs:** 22 facts, none above 0.50, so nothing went in; the wisp note scored 0.13. 362 ms, 5,399 tokens ($0.00023).
- **A planner query,** `notes` for Britain with the question "Where can a new warrior buy bandages near Britain?", over 15 hand-written notes: the healer's bandages came first at 0.96, then the provisioner who sells none (0.73), tailors' cloth (0.70) and the healer running out (0.69). The store's own order (newest first) started with bards' instruments. 325 ms, 3,228 tokens ($0.00014).

**Guide import,** single smoke run on 2026-10-06:
`uo-brain world import-guide https://www.uoguide.com/Britain --area Britain` read 4,657
characters and stored 28 notes (banks, healer, shops, inns, guild halls, bridges and gates)
for $0.033: 3,497 prompt and 2,557 completion tokens, 14 s. UOGuide gives positions in
sextant degrees, and the notes keep them as written rather than converting them to tile
coordinates.

**Outcomes,** single run on 2026-10-07 over 18 bench logs (warrior arena rounds):
`uo-brain world outcomes logs/bench/20261006-233040/relentless-never-flees-jev_relentless-*.jsonl logs/bench/20261006-233040/mismatch-jev-0*.jsonl --area "Test field"`
wrote "Test field: 5.1 kills a loop for the warrior kit over 18 loops of about 1.4 min, 4
deaths in 18 loops, 0.8 bandages and 0.2 heal potions a kill; orcs cost the most bandages, 1
a fight over 25 fights, then ratmen at 0.8 a fight."

### UO Renaissance's map page

2026-10-10, with the shard's website still down: the Wayback Machine's copy of its map page of
2026-04-12 (`http://web.archive.org/web/20260412162235id_/http://www.uorenaissance.com/map/index.php`),
read by `python3 -I tools/uor/uor_map_markers.py PAGE.html --out brain/worlds/uor/uor-map-page.map`
and imported with `uv run uo-brain world --shard uor import-markers worlds/uor/uor-map-page.map --maps Felucca`
(cuo-49t.8). A single import.

- **128 markers, all read:** 20 towns, 9 shrines, 9 dungeon entrances, 8 moongates, 7 graveyards and
  75 landmarks (20 points of interest, 55 named stretches of country). Left out: 26 lines the page
  itself has commented out, and its 17 houses.
- **Against the standard map** (the local store, from ModernUO's data), to check the page's numbers
  are game coordinates: all 8 moongates within 3 to 6 tiles of the standard ones, all 9 shrines
  within 2 to 6, 8 of 9 dungeon entrances within 10 (Wrong 13: the pin sits on the cave mouth), and
  the graveyards 11 to 22 (the pin sits at an edge, the store's point in the middle). A marker sits
  where its icon was drawn, so these are places to head for, not tiles to stand on.
- **The shard's own places:** 14 of the 20 towns are the standard ones. The other six aren't on
  the standard map: Adventure Time, Pecuniary Pointe, Stormhold and the Eternal Realm of the True
  Knights (players' towns), the Orc Fort and the entrance to Wind.
- **Not there:** banks, healers, shops. And not done: looking at the markers on the client's world
  map with the shard's own map files, which needs those files (cuo-49t.4); it is on the first
  session's list (cuo-49t.7).

### Map markers

Single runs on 2026-10-09 with UOAM's `Common.MAP` (870 markers; [where to get it](#filling-the-store)).
The numbers are in `brain/bench/2026-10-09-markers.json`.

**Import beside the server's places**, in a scratch store:
`uv run uo-brain world --shard markers-check import-modernuo`, then
`uv run uo-brain world --shard markers-check import-markers Common.MAP --maps Felucca`. All 870
lines read; 579 Felucca places went in (181 landmarks, 44 guildmasters, 28 teleporters, 24 inns,
23 mage shops, 21 dungeon entrances, 20 arms shops, 18 banks, 17 healers, 10 moongates and the
rest); one marble patio (a house) was skipped. On every facet the file gives 1,374 places (579
Felucca, 549 Trammel, 198 Ilshenar, 48 Malas). The other files in UOAM's archive: `Atlas.MAP`
gives 216 landmarks, `Dungeons.MAP` 383 places inside dungeons, and `Treasure.MAP` and
`Rares.MAP` are skipped whole (treasure pins and rares).

How the markers line up with the places `import-modernuo` gave, within 10 tiles:

| kind | markers | within 10 tiles of a server place | server places with a marker | tiles apart when they match (median, most) |
|---|---|---|---|---|
| bank | 18 | 18 | 18 of 20 | 0, 5 |
| healer | 17 | 17 | 17 of 199 | 0, 8 |
| moongate | 10 | 9 | 9 of 9 | 1, 2 |
| dungeon entrance | 21 | 13 | 13 of 25 | 3, 8 |

The mismatches:
- **Banks:** none among the markers. Of the server's banks, the Magincia bank from its locations list is 12 tiles from the "Bank of Magincia" marker (which matches the server's banker), and a banker the server spawns at (4551, 2323), outside any town, has no marker.
- **Healers:** the 182 server healers without a marker are all wandering healers out in the land; the marker file lists shops.
- **Moongates:** "Standard-Moongate" at (5153, 1760) in the Lost Lands; the server has no public moongate there.
- **Dungeon entrances:** Wrong's marker is 12 tiles from the server's "Wrong Entrance" location, but 11 from the teleporter into the dungeon, which the server's location is 23 from. Terathan Keep's is 25 tiles off. Wind, the Britain sewer entrance and four Lost Lands passages aren't in the server's list. The server's entrances without a marker are dungeons newer than the file (the Solen Hives, Blighted Grove, Sanctuary, Painted Caves, Prism of Light, Palace of Paroxysmus), plus Wrong and Terathan Keep.

**Travel to imported places** on the local server, from a store holding nothing but the
markers (as on a new shard): `uv run uo-brain world --shard markers-only import-markers Common.MAP --maps Felucca`,
then for each, `[AgentGo` to a start and `uv run uo-brain --port 5630 do --shard markers-only travel NAME`
(settles within 2 tiles; 6 clients running). A new warrior, walking:

| marker | kind | marker to the server's place | start to the marker (straight line), time | ended, tiles from the marker | from the server's place | nearest people |
|---|---|---|---|---|---|---|
| Heavy Metal Armorer | arms shop | 10 | 41 tiles, 19 s | 2 | 8 | weaponsmith 9 tiles, armourer 11 |
| Healer of Britain | healer | 4 | 81 tiles, 27 s | 2 | 3 | healer guildmistress 2, healer 5 |
| East Britain Bank | bank | 5 | 230 tiles, 55 s | 2 | 6 | minter 4, banker 5 |
| Britain Moongate | moongate | 0 | 309 tiles, 82 s | 2 | 2 | |
| Orc Cave | dungeon entrance | 5 | 36 tiles, 9 s | 2 | 3 | |
| Despise (dungeon) | dungeon entrance | 2 | 34 tiles, 8 s | 2 | 1 | |
| Wrong (dungeon) | dungeon entrance | 12 | 39 tiles, 9 s | 2 | 10 | |

All seven arrived. Orc Cave's marker is next to the teleporter into the cave; the walk stopped
3 tiles from it, so the character didn't fall in. A first try at Despise found no route because
the start, not the marker, was on a mountain.

**Which markers you can stand on:** from Britain's centre the client planned a walk to each of
the 67 markers in and around Britain at distance 0, then 1, 2 and so on, without walking. 59
are on a tile it can reach; East Britain Bank, Healer of Britain, The Saw Horse, A Girl's
Best Friend, Britannia Animal Care and the sewer entrance need 1; Heavy Metal Armorer needs 2;
East Side Park (a landmark) needs 7. So the default 2 reaches every bank, healer and shop marker
there.

**An errand on markers alone:** `uv run uo-brain --port 5630 do --shard markers-only bank --deposit gold`
from Britain's centre walked 71 tiles to The First Bank of Britain in 17 s and deposited 1,050 gold.

### The planner with memories

Offline, 2026-10-09: the first planner call of every step in six 30-minute soak logs of
2026-10-08 (98 moments, the warrior Brutus at the Britain graveyard), each put to Haiku twice,
as logged and with the memories Jev picked from what Brutus remembered at that moment. His
memory was his 27 earlier session logs, imported (`uo-brain memory --character Brutus import
…`, 200 memories) into a copy of the store; a moment only sees memories from before it
(`uv run uo-brain replay-planner logs/planners-a-1.jsonl --planner-model anthropic/claude-haiku-5.5 --memories Brutus --root <copy> --limit 60`,
and the same for `planners-b-1`, `a-2`, `b-2`, `a-3`, `b-3`; `brain/bench/2026-10-09-planner-memories.json`).

| | without memories | with memories |
|---|---|---|
| goals back to a place code would refuse | 21 (17 and 19 in two more samples) | 11 |
| same tool as the other answer to the moment | 0.80 (two samples, both without) | 0.52 |
| prompt tokens added | | 380-540 |
| a Jev pick | | $0.00025, 300-500 ms |
| a Haiku planner call | $0.0004 | $0.0004 |

- **Counted again after a review:** the first count measured how old a memory was from now, not
  from the replayed moment, and could count a place as bad from a repeat that came after the
  moment (13 without, 16 and 18 in the two noise samples, 9 with). Both are fixed; the table is the
  second run.
- **"Back to a place code would refuse":** a hunt, travel or purchase naming an area the character
  left, died at or was refused at in the 30 minutes before (code refuses those), or a place it
  found no route to. A rough measure: the planner names areas loosely, and a `buy` doesn't name
  its vendor.
- **What changed:** with memories the planner hunted 13 times instead of 23 and banked 11 times
  instead of 7. In the first run, before `set_strategy` said it may change only the planner's own
  lines and at most every 10 minutes, it had also rewritten the whole strategy 12 times (never
  without memories), twice with no text; in the second, never. The planner's lines now go in a
  block of their own that never touches the player's ([Goals and the planner](#goals-and-the-planner)).
- **Cost:** the picks added about 30% to the planner's cost ($0.025 against $0.083 for the 98
  moments), about $0.005 an hour at one planner call every three minutes.
- **Live:** in a 5-minute session on the soak server (`logs/6w8-3-check.jsonl`) every planner call
  showed four or five memories, all about the graveyard leaves of the last day, and later ones the
  refusal it had just hit.

### Long-range memory over a long soak

Two 4-hour sessions on the soak server (port 2595), 2026-10-09, one after the other from a frozen
copy of the brain. Each had the same goal, Brutus's soak character, and packs sent in during the
first half. The brain was killed (SIGKILL) at 2 hours and started again with the same goal:

```bash
GOAL="Hunt the undead at the Britain graveyard, starting from the West Britain bank. Keep yourself supplied: bandages for a warrior, reagents for a mage. Bank your gold and loot when the bag gets heavy."
uv run python scripts/soak_prep.py runes
SOAK_PORT=5580 SESSION_FLAGS="" scripts/restart_soak.sh mem-a 4 "$GOAL"                                  # 15:29-19:30
SOAK_PORT=5580 SESSION_FLAGS="--no-memory --no-escalate" scripts/restart_soak.sh mem-b 4 "$GOAL"          # 19:30-23:33
uv run python scripts/memory_soak_report.py logs/mem-a.jsonl logs/mem-b.jsonl
```

Run A has today's planner: memory, long goals, the running summary, heartbeats, resuming after a
restart, and Sonnet for hard moments. Run B has neither memory (nor the long goals, heartbeats and
resuming that go with it) nor escalation. The running summary is on in both. Single runs.

| | A: memory and escalation | B: neither |
|---|---|---|
| planner steps | 155 | 117 |
| prompt tokens a step, mean (by hour) | 11,373 (11,263 / 11,487 / 11,310 / 11,374) | 9,800 (9,383 / 10,189 / 9,384 / 10,016) |
| cost an hour, every AI call | $0.32 | $0.21 |
| of which | fights $0.60, Sonnet's hard moments $0.55 (22: 13 deaths, 4 failure runs, 3 refusals, 2 repeats), planner $0.10, folds $0.023, memory picks $0.021, heartbeats $0.008 | fights $0.76, planner $0.08, folds $0.013 |
| after the restart | resumed: 15 steps, the summary and the focus; first goal "At 12/95 health with a hostile in sight, recovering before any travel or fighting" | started over: "needs a bandage stock first; I have only 7" |
| long goals in the planner's prompt | in all 193 calls, 76 before the restart and 117 after; "save 2000 gold" went from 1,113 to 1,688 and passed 75% after the restart | none |
| memories written | 62 | none |
| hunts, kills | 5, 1 | 8, 5 |
| deaths | 15 | 11 |
| goals refused (back near a fresh leave) | 4 | 3 |
| the same goal failing again straight after | 2 | 5 |
| hunts back where it had died in the last 30 minutes | 0 | 3 |

- **The prompt stays bounded** over 4 hours in both runs: the running summary folds old steps
  away. Memory, long goals and the focus add about 1,600 tokens a step.
- **What memory costs:** memory picks, heartbeats and their folds come to about $0.013 an hour.
  Most of the difference is Sonnet's hard moments, at about $0.025 each; 13 of the 22 came after a
  death, so they rise with the deaths.
- **Repeated mistakes** were fewer with memory: 2 against 5 goals failing again straight away,
  and no hunts back where it had just died, against 3. These are single runs, so it's a
  direction, not a measurement.
- **Not comparable: deaths and kills.** The soak world was hostile: a lich roamed Britain,
  probably left over from earlier soaks' packs, and the Britain healer didn't sell. Both runs also
  had the stall that the walk to Cove found ([Travel and errands](#travel-and-errands)): 12 of
  A's deaths and 9 of B's came after the character stood still "leaving by the road" while still
  engaged. That has been fixed since.
- **B stopped early:** its first half ended 5.5 minutes before the restart, when Haiku called
  `finish`.
- **Files:** `brain/logs/mem-a.jsonl` and `mem-b.jsonl`, with their `.decisions.jsonl`. An
  earlier run A (`mem-a0`) was stopped after 70 minutes: a ghost resurrected at a wandering
  healer in the wilds died 10 times among what had killed it, so resurrection now prefers a
  guarded town's healer and heals to 70%.

### Sonnet for the planner's hard moments

Two 30-minute soak runs on the soak copy, 2026-10-09, one after the other from a frozen copy of the
brain, the graveyard goal with the usual two packs (`SOAK_PORT=5580 scripts/flags_compare.sh esc-b 0.5
"-" "--no-escalate"`; `brain/logs/esc-b-1.jsonl`, `esc-b-2.jsonl`). Haiku planned both; in the first,
hard moments went to Sonnet.

| | with escalation | Haiku alone |
|---|---|---|
| minutes, hunts, kills, deaths | 33, 2, 7, 0 | 30, 2, 5, 0 |
| gold banked | 300 | 0 |
| escalations | 3 (2 after a leave, 1 after a refusal) | |
| system two an hour | $0.137 ($0.060 of it 3 Sonnet steps) | $0.045 |
| all AI an hour | $0.23 | $0.14 |

- **What Sonnet did:** after each leave it banked and restocked; after the refusal it chose "a nearby
  undead spot with fewer at once". Haiku alone did much the same after its leave (it rested out the
  refusal and banked nothing).
- **The cost:** a Sonnet step cost about $0.02, fifty Haiku calls: its prompt isn't in Sonnet's
  cache and a step can hold world queries. Two of the three were for leaves that Haiku handles well,
  so a leave by itself no longer escalates; a refusal to go back still does.
- **Read with care:** single runs, and the second run started from the first's saved planner state
  and memories (its summary counted the first run's hunts; summaries now count only their own
  session's steps, and the comparison script starts each run `--fresh`).

### A party on one plan

The lich scene from the update video, 2026-10-10, on 2593. Five classes (Roland the warrior,
Isolde the mage, Wystan the archer, Thea the tamer, Corwin the bard) were staged south of the
Britain graveyard, headless and without personas or talk. A lich, two bone knights and four
skeletons waited 30 tiles north, spawned by the last member's prep once all were in. All five had
the same goal: "Hunt the undead at the Britain graveyard with your party ... Stay together." The
party (cuo-4ar) and the same five each planning alone took turns:

```bash
uv run uo-brain fleet fleets/party-lich.toml        # party = true, leader = "Roland"
uv run uo-brain fleet fleets/party-lich-solo.toml   # a planner each
uv run python scripts/party_scene.py logs/fleet/RUN
```

| | the party (Roland leads) | a planner each |
|---|---|---|
| reached the lich (attacked it, or came within 4 tiles) | 5, 5, 5 of 5 | 3, 2, 2 of 5 |
| deaths | 3, 0, 1 | 1, 1, 2 |
| lich killed, from first sight | no; 15.0 s; 247.5 s | no, no, no |

- **Every member reached the fight**, 15 of 15 against 7 of 15. A planner each sent them
  elsewhere: Isolde travelled and rested in town, Thea finished and stood still, Roland banked and
  finished.
- **The lich died in two of the three party runs** and in none of the solo runs. In the first party
  run, made before the follower fixes below, all five reached it, Wystan died, and it died 16.8 s
  after first sight.
- **Deaths:** 4 in both conditions.
  - **Party run 3:** Roland's own memories from earlier runs ("the character keeps dying in open
    country") had him lead the party to Britain and the sewers first. At the graveyard, members
    who left the fight ran off in different directions and lost the companions that made the odds
    good.
  - **Party run 5:** Roland's client still remembered his corpse from the run before, so his first
    goal was to fetch it. The party met the lich on the way and killed it only 248 s later.
- **For comparison, the video's takes:** a planner each, with personas, had 3 of 5 reach the lich,
  3 die, and the lich dead in 36 s. One `do hunt` each, with no planner, had 5 of 5 reach it,
  none die, and the lich dead in 12.7 s. A party gets close to the second while still planning.
- **Fixed during these runs:**
  - **The leader's new goals cut a ghost's walk to the healer short** every 2 s: it took
    2.5 minutes to come back. A follower's resurrection and corpse are now its own business.
  - **The corpse was refused**, because danger had been seen there, so Wystan played on without
    his bow. A follower now goes back for its corpse with the party there.
  - **A follower rested 50–90 tiles behind** while the leader waited for it. It now catches up first.
  - **The wave was spawned in the leader's prep,** and the leader died during the others' logins,
    its agent still off. One run was dropped for that, and the wave is now spawned last.
- **Single runs, three of each,** with each character's memories carried over from earlier runs.
  The leave-stop bench (lane 2) ran on the same server at the same time.
- **Not leaving alone** (cuo-e1p), added after: three more party runs. One was dropped because
  Corwin's client never logged in, so there was no wave. In the other two, 5 of 5 reached the lich
  each time, with 3 and 2 deaths and the lich killed once (19.0 s). The hold fired 6 times, and the
  difference from the runs before is nothing that two runs can show.
- **Filmed:** one more party run, windowed and recorded with `tools/video/film.py` (so not counted
  above): all five reached the lich, nobody died, and it died 19.6 s after first sight. The
  50-second cut of the fight is `~/Movies/ClassicUO-agent-clips/2026-10-10 a party of five kills the
  lich (cuo-4ar).mp4`.
- **Read with the next section:** the day after, with every brain's memory off, a planner each
  reached the lich 9 times of 10. The 7 of 15 above was each character's own memory of dying at
  this graveyard, not the planners pulling apart ([one plan, three ways](#a-party-that-helps-calls-out-and-talks)).
  From 2026-10-11 the scene also clears the graveyard's own spawns, so these numbers and those
  aren't for setting side by side.

### A party that helps, calls out and talks

What a party does beyond following one plan ([how it works](#a-party-in-a-fight-on-the-road-and-at-the-inn)),
measured 2026-10-11 on 2593 with headless clients. Every run is one run unless a count is given, and
the Decision 1 benchmark (one more client, lane 2) ran on the same machine throughout, so read the
times as from a loaded machine. The talk, the callouts and the gestures are for the local server
only.

```bash
cd brain
uv run uo-brain fleet fleets/party-inn.toml                     # personas, talk, callouts, help, roleplay, walking, the inn
uv run python scripts/party_scene.py logs/fleet/RUN             # who reached the fight, who died, how long it took
uv run python scripts/party_calls.py logs/fleet/RUN             # callouts, who heard them, who helped whom
uv run python scripts/party_talk.py logs/fleet/RUN --transcript # everything said, its pace and its cost
uv run python scripts/party_walk.py sample --ports 5661-5665 --out logs/fleet/RUN/where.jsonl   # beside the run
uv run python scripts/party_walk.py trips logs/fleet/RUN        # each trip the party made together
```

**A hunt, the walk to the inn and the table** (`logs/fleet/20261011-020654-party-inn`, 27 minutes,
the five with personas against a lich and four skeletons):

| | |
|---|---|
| the fight | 5 of 5 reached the lich, nobody died, the lich dead 22.7 s after first sight and the last skeleton 2 s later; the lowest anyone's health fell was 44% |
| the planner chose the inn | by itself, 4.4 minutes into the session, after four more hunts of the emptied graveyard found nothing ("the party regroups and talks it over before going in again") |
| the walk there | 65 s at a walk (0.46 s a tile; a run is 0.2), all five; the furthest member 6 tiles from the leader at the median and 12 at most; the leader stood 7 s in all waiting for someone |
| gathered | all five within 8 tiles of the table 2 s after the leader arrived |
| the table | 20 lines in 3.3 minutes, 3 to 5 each, nobody passed over: a toast, the stories, the banter, a lesson each; 12 emotes between the lines |
| what it cost | $0.0096 for the table: 25 calls of Haiku (20 lines and 5 reflections), about 1.5 s each |
| what was kept | 6 memories each: 2 lessons, 3 about companions, 1 line on how to fight next time |
| after it | the planner called restock, then the hunt again |

Part of the table, as said (emotes shown in asterisks):

```
Corwin  *raises his tankard high and grins at the table*
Corwin  A toast! To the Britain Graveyard, where Corwin stood bravely behind a bush and wrote a ballad!
Thea    *sets her tankard down hard and glares at Corwin*
Thea    Bravely behind a bush, Corwin? I was the one with a skeleton on me while you hummed.
Isolde  Your arrows finished what my fire started, Wystan, and my tally says so. Fizzles are your lies.
Roland  Enough, archer. The lich is dead, and so is the stupid argument, and Corwin's ballad still hums.
Roland  Corwin's ballad hid behind a bush, same as Corwin. Captain Vane would have docked his pay.
Isolde  Next time I open with the lich while the rest of you stand in front of it, not behind my heels.
Thea    Next time I shout for help sooner, before the skeleton has me down to seventy-eight.
```

- **It stays on the hunt that happened:** who stood where, who was hurt and how far (Thea's 78%),
  who healed whom. Two things in it were not true: Corwin's and Roland's count of bandages (thrown
  back and forth as banter), and "my health hit a hundred percent low", said by two who were never
  hurt, because their story of the hunt read "their health fell to 100% at its lowest". That line
  now reads "they were not hurt at all".
- **What each kept** is its own: Roland, "I should hold the skeleton off the back line before it
  reaches the casters"; Thea, "Roland was the one who got the skeleton off me ... he is steady when
  it matters"; Isolde's line for her fights, "Open on the lich from behind Roland's line, heal Roland
  before he drops below half, and keep my spells out of Wystan's lane." All five lines on how to
  fight passed the check (none says whether to fight or when to leave).

**The next hunt, with what the table left** (`logs/fleet/20261011-023751-party-inn`, the same fleet
run again straight after, each character's memory carried over; one run each, so the numbers show
that nothing got worse and no more):

| | the first hunt | the next, after the inn |
|---|---|---|
| reached the lich | 5 of 5 | 5 of 5 |
| deaths | 0 | 0 |
| the lich dead, from first sight | 22.7 s | 18.8 s |
| the last of the wave dead | 24.7 s | 26.1 s |
| lowest health of each | 44, 78, 81, 100, 100% | 47, 94, 100, 100, 100% |
| help given to a companion (a heal, a bandage, a walk over) | 5 | 1 |
| the mage's decisions with the lich "nearby" and not "far away" | 9 of 23 | 3 of 18 |

- **The planner picked the lessons:** all 14 of the leader's planner calls in the second run had
  memories from the table in front of them, 40 of the 70 memories shown: its line on how to fight
  ("Hold the skeletons off the back line with me in front, Isolde opens on the lich only once the
  front is set ...") and its two lessons in 13 of the 14.
- **Each member's own line reached its fights:** from the first decision with something hostile in
  view, every member's fight state carried the line it wrote for itself at the table (24 to 34
  decisions each in the first hunt of the run).
- **What changed:** the mage stood back more, as her line says ("open on the lich from behind
  Roland's line"), and two of the three who had been hurt weren't. One run each can't say the table
  did it.
- **The inn again?** Not in this run: with the graveyard emptied, the leader hunted it four times
  more, then waited for it to refill until the session ended. In the first run the same four empty
  hunts ended in the inn. So the planner chose the inn by itself in one run of two; its own hunt's
  result said "0 kills", the others having done the killing. A third run (`20261011-030625`), with
  the result saying once "this hunt is won", went the same way as the second.
- **With the won hunt kept in front of it** (and the fleet's goal asking for the inn once the hunt
  is won), the leader called the inn within 2 s of its first hunt's end in three runs of three (the
  filmed runs below).

**Tables written by Sonnet, filmed** (three runs of 12 to 18 minutes, windowed and recorded, so
their times aren't for comparing with the runs above; `uo-brain fleet fleets/party-inn.toml
--windowed --hours 0.2 --set 'use="table=anthropic/claude-sonnet-5.5"'` under
`tools/video/film.py`). In all three the fleet's goal asked for the inn once the hunt was won, and
the leader had the won hunt in front of it:

| run | the fight | the inn called | the walk | the table | Sonnet's bill for it |
|---|---|---|---|---|---|
| `20261011-032556` | 5 of 5, no deaths, the lich dead in 22.0 s | 2 s after the hunt ended | 88 s, gathered in 4 s | 19 lines in 3.5 min | $0.186 |
| `20261011-040100` | 5 of 5, no deaths, 16.1 s | 2 s after | 61 s, all there with the leader | 20 lines in 3.1 min | $0.177 |
| `20261011-043208` | 5 of 5, no deaths, 18.7 s; the warrior down to 28% and healed by four companions within 5 s | 2 s after | 88 s, gathered in 2 s | 18 lines in 4.1 min | $0.183 |

| | Haiku's table (above) | Sonnet's (three) |
|---|---|---|
| a call | about 1.5 s, $0.0004 | 2.4 to 2.7 s, $0.007 |
| the table (lines and five reflections) | $0.0096 | $0.18 |
| turns passed over | none | 1, 0 and 2 |

- **The inn was called in three runs of three,** within 2 s of the hunt's end: "The hunt at the
  Britain graveyard is won, a skeleton and a lich are dead, so the party sits down to talk it over."
- **Sonnet's banter is sharper** and stays closer to what happened. In the third run the warrior had
  shouted "Going down, damn it! Heal me, now!" and three bandages and a Greater Heal reached him
  within 5 s; at the table: "Stitched? Four of you healed me, archer and bard included. I was at 28%
  with a skeleton chewing me." (Roland), "Four healers, Roland, and mine alone came with a chorus!"
  (Corwin). Others: "Falling back is running with a name on it, tamer."; "Two hundred paces? Earlier
  it was forty, Wystan; your arrow's range grows with the wine."; "Roland keeps a list, I keep a
  tally, and Corwin keeps a bush; only one of us is winning." Nobody boasted of health they hadn't
  lost.
- **Nineteen times the price** for one table, and still under 20 cents. Haiku's is good enough to
  follow and to learn from; Sonnet's is the one to film.
- **Slips, all fixed since:** a turn was passed over when Sonnet's reply was cut off at 500 tokens
  and came back with no line (the turn then stood empty for 40 s; a line now has 1,000 and a second
  try); an emote cut off on a dangling word ("*... and passes it down to*"); a line on how to fight
  kept in memory as Sonnet sent it, written like a list (`["Hold the front ..."][0]`).
- **The first four takes were lost as footage,** two to a planner that never called the inn and
  two to the camera: the clients came up with a 600 by 480 game view and the paperdoll open over the
  characters, and in the fourth the table was held standing outside the inn's wall.
  `tools/video/layout.py` now sets the clients up, and the walk ends indoors.
- **The clips** are in `~/Movies/ClassicUO-agent-clips/`: "2026-10-11 the lich, the walk to the inn
  and the table inside it, Isolde's window (cuo-cod.4).mp4" (6:35, the two empty turns cut out) and
  "2026-10-11 four companions heal the warrior on the lich, Roland's window (cuo-4ti).mp4" (0:40),
  both from the third run above, with the whole of each take beside them.

**Talk, its pace and the small things done** in three runs of `fleets/party-inn.toml` (27 minutes
each, five characters, so about 2.3 character-hours a run; the first two against the full wave of
the lich scene, where the party lost two members and then all five):

| run | lines of talk (and at the table) | a character-hour | small talk | from a line to its answer, median for each speaker (shortest) | started before the last could be read | talk cost a character-hour | emotes and gestures a character-hour |
|---|---|---|---|---|---|---|---|
| `20261011-004713` (2 deaths, no inn) | 46 | 20 | 20% | 10.2 to 12.6 s (9.3) | 0 of 46 | $0.010 | 9 to 13 |
| `20261011-013055` (all five died, no inn) | 19 | 9 | 26% | 10.2 to 12.9 s (8.5) | 0 of 19 | $0.005 | 11 to 18 |
| `20261011-020654` (the run above) | 34 (and 20) | 21 | 32% | 10.5 to 12.8 s (8.8) | 0 of 54 | $0.010 | 4 to 8 |

- **A human pace:** a reply came 10 to 13 s after the line it answered, never sooner than 8.5 s, and
  no line started before the one before it could have been read. About half the lines started while
  the last one was still over its speaker's head (the client keeps a line up 4 s for each 32
  characters), which is meant: the next line is over another head. At the table the lines came 8 to
  10 s apart.
- **One conversation:** "left to another" (someone else had claimed the answer) dropped 8 lines in
  the three runs, "the exchange had run its course" 16, "too late" 3 and "a fight began" 2. In the
  first run Corwin and Wystan still said 35 of the 46 lines between them; the others spoke when
  spoken to.
- **Small talk** was a fifth to a third of the lines (Thea reporting what her bear thinks of each of
  them, Corwin's ballad titles, Isolde on Wystan's whittling). Three stories were told in the three
  runs, one of them an old one from a persona (Wystan's apple on the sheriff's hat); none was told
  twice to the same listener.
- **On the NPC fleet** (`uv run uo-brain fleet fleets/britain-lives.toml --set headless=true`, three
  NPCs with lives of their own for 23 to 29 minutes, `logs/fleet/20261011-083711-britain-lives`):
  4 lines, two of them small talk ("Torv, the bank is quiet today. Has the weather turned cold on
  you?"), 2 to 4 lines a character-hour and about a tenth of a cent a character-hour for the talk,
  of the 7 cents a character-hour the run cost and the profile's cap of 50. They go their own ways
  and talk when they meet, so there is far less of it than in a party that travels together.
- **What the talk gave the planners:** nothing in four party runs but three lines marked as a plan
  ("sing it from the front line"), and no warning was spoken in any of them, so "a warning still
  reaches the planner past the small talk" rests on its unit test, not on a run.
- **What it costs:** half a cent to a cent a character-hour for the talk (Jev's yes/no at $0.00004
  and Haiku's line at $0.0003), and the table's $0.0096 once. The voice packs, written once, were
  $0.005 for the five. A whole run of five was $0.11 ($0.044 a character-hour), the leader's planner
  most of it.
- **Roleplay:** a bow and four salutes as the party came together, then 4 to 18 emotes a
  character-hour from each one's voice pack ("*keeps one hand on his blade while he eats*", "*picks
  burrs out of Bramble's coat, humming low*"), none in a fight.
- **Callouts in a persona's voice:** "Someone heal me before I become a cautionary tale!" (Isolde,
  near death), "Bramble, have a go at the lich!" (Thea). In the three runs 27, 56 and 33 were said
  aloud, and 88 to 95% of them were in a companion's fight state within 2 s.

**Helping and calling out, on the lich scene** (cuo-4ti, cuo-cod.2; `fleets/party-lich.toml`: the
full wave of a lich, two bone knights and four skeletons, no personas, so the callouts are the
plain lines; every brain without its memory, so the nine runs are independent; three rounds of the
three, in turn):

```bash
uv run uo-brain fleet fleets/party-lich.toml --set memory=false                                         # help and callouts
uv run uo-brain fleet fleets/party-lich.toml --set memory=false --set callouts=false                    # help only
uv run uo-brain fleet fleets/party-lich.toml --set memory=false --set help=false --set callouts=false   # neither
uv run python scripts/party_scene.py logs/fleet/RUN ; uv run python scripts/party_calls.py logs/fleet/RUN
```

| three runs each | help and callouts | help only | neither |
|---|---|---|---|
| reached the lich | 5, 5, 5 of 5 | 5, 5, 5 | 5, 5, 5 |
| deaths | 3, 1, 1 | 1, 0, 2 | 2, 0, 3 |
| the lich dead, from first sight | 9.8, 12.7, 15.4 s | 37.1, 19.6, 27.0 s | 34.2 s, 12.0 s, not killed |
| the last of the wave seen | 148, 62, 48 s | 78, 71, 406 s | 60, 52, 506 s |
| members who fell under a third of their health | 5, 2, 2 | 3, 1, 4 | 3, 2, 3 |
| callouts made (said aloud) | 76 (62), 44 (35), 31 (25) | none | none |
| in a companion's fight state within 2 s, of those in view | 83%, 90%, 88% (median 0.6 to 0.8 s) | | |
| help given (a heal, a bandage, a walk over) | 44, 7, 5 | 3, 1, 18 | none |

- **Deaths didn't move:** 5, 3 and 5 in three runs each. The warrior goes in first and takes the
  whole wave: it died in 4 of the 9 runs, 9 to 46 s after setting out, in the first of them 4 s
  after a companion's Greater Heal. Against this wave the party is at the edge of what it can
  win, and three runs can't tell these three apart on deaths.
- **With callouts the lich died sooner, in all three runs** (10 to 15 s, against 20 to 37 s with
  help alone, and 12 s, 34 s and never with neither). Each member says which creature it has and
  which is on it, and the others' target question has that in front of it. Three runs each, so a
  lead to follow and not a result.
- **Neither made anything worse,** which is what the bead asked of the callouts: no more deaths, no
  slower kill.
- **Callouts reach the others' decisions:** 83 to 90% of them were in the fight state of a
  companion who had the caller in view within 2 s, at a median of 0.6 to 0.8 s. They cost
  nothing: code decides them, and the voice pack (for a persona) is written once.
- **The help is real when there is time for it:** in the filmed run above four companions healed
  the warrior from 28% within 5 s. Of the help counted here, the mage's Greater Heal was 2, 7 and
  3 in the three runs with callouts and the bandages 9, 0 and 2; the other 33 in the first run were
  walks over to a hurt companion once nothing hostile was near.
- The first of the nine ran on the callout code as it was before a resurrected member was
  silenced until its next fight; the fight rules were the same in all nine.

**One plan, three ways** (cuo-bih; the lich scene's full wave, 2026-10-11): a planner each
(`fleets/party-lich-solo.toml`), the leader's plan (`fleets/party-lich.toml`) and a planner each with
a huddle (`fleets/party-lich.toml --set plan=huddle`), first with every brain's memory off
(`--set memory=false`), then with each character's own memory, which by then held a day of deaths
at this graveyard:

| | a planner each | the leader's plan | a planner each, with the huddle |
|---|---|---|---|
| **memory off:** reached the lich | 5, 4 of 5 | 5, 5, 5 | 5, 5 |
| deaths | 0, 1 | 3, 1, 1 | 3, 1 |
| the lich dead, from first sight | 9.1 s, 96.1 s | 9.8, 12.7, 15.4 s | no, 22.5 s |
| errands before the fight | 0, 0 | 0, 0, 0 | 0, 1 (a restock that bought nothing) |
| planner calls, cost of the run | 57 and 66, $0.03 and $0.09 | 7 to 10, $0.004 to $0.03 | 45 and 48, $0.05 and $0.08 |
| **memory on:** reached the lich | 2, 3 of 5 | 5 | 3, 3 |
| deaths | 2, 2 | 3 | 0, 0 |
| the lich dead | no, no | no | no, 14.5 s |
| errands before the fight | 2, 1 | 0 | 1, 0 |

- **Without memory, a planner each gets the party there too.** Yesterday's 7 of 15 for "a planner
  each" ([above](#a-party-on-one-plan)) wasn't the planners pulling apart: it was each character's
  own memory of dying at this graveyard. With memory off, 9 of 10 reached the lich and it died in
  both runs.
- **With memory, a planner each scatters as before:** in the first run the bard walked to the bank
  and finished, the tamer went to the bank and rested, the mage went shopping twice and arrived
  late, and two of the three who went at once died; the second run went the same way (three in, two
  dead, the bard resting in town).
- **The leader's plan holds five together whatever they remember,** and that isn't always a
  kindness: in the memory-on run all five walked in, the warrior died 14 s in, three died in all,
  and the lich lived.
- **The huddle does what it was built to do:** with memory on, each member brought its own lessons
  ("Corwin died four times there on 2026-10-10"; "three hunts ended in deaths with zero kills") and
  the plan answered them: "single or paired undead only, all five together, starting with the walk
  in from the west road"; after three came near death, a second plan; after a lich was seen, a
  third: "Stop the Britain Graveyard hunt now ... Hold at open ground on the west road". Nobody
  died in either run, and in the second the three who went in killed the lich in 14.5 s. It also
  shows the huddle's weakness: a plan in words doesn't make five planners act at the same moment
  ("Nobody walks in until all five are in sight", said the second run's plan; three walked in). In
  the first run the warrior rested first and the tamer finished her session;
  in one memory-off run the others were in the fight 35 s before the warrior, who then died, and in
  the other the mage died 29 s before he arrived.
- **Cost:** a huddle is one call of Haiku, $0.0004; three to five a run. Five planners cost what
  five planners cost: 38 to 48 planner calls a run against the leader's 7 to 10.
- **Where that leaves it:** the leader's plan stays the default for a party. The huddle is the
  better fit where the members have lives and memories of their own (NPCs who happen to travel
  together), and it needs one more thing before it can fight as a party: setting out together.
- **Fixed before these runs** (found by review, not by play): every goal after the first change
  of plan ended at once, because nothing cleared the interrupt the huddle sets; and a ghost in the
  party called a new huddle at every planner step.
- Small numbers: two or three runs a cell with memory off, one or two with it on, and the party's
  memories grew from run to run (solo, leader, huddle, then three walking runs without memory, then
  solo and huddle again).

**Walking a trip together, or running it** (cuo-cod.7; `fleets/party-walk.toml`: the five from the
graveyard's edge to the East Britain bank, about 300 tiles through the town; one run each, 2026-10-11,
with `scripts/party_walk.py sample` beside it and `trips` after):

```bash
uv run uo-brain fleet fleets/party-walk.toml --set memory=false --hours 0.1                    # walked, side by side
uv run uo-brain fleet fleets/party-walk.toml --set memory=false --hours 0.1 --set walk=false   # run, each on its own path
```

| | walked together | run |
|---|---|---|
| the leader's trip | 132 s, 0.43 s a tile | 68 s, 0.23 s a tile |
| the furthest member from the leader on the way, median (most) | 4 tiles (10) | 16 tiles (39) |
| everyone within 4 tiles of the leader | 57% of the way | never |
| the leader stood waiting | 3 s, in three stops | none on the way; 60 s at the bank for the last one, then 42 tiles behind |
| all five at the bank | 125 s after setting out, within 2 s of each other | within about 130 s |
| lines said on the way | 7 | 3 |

- **Walking costs the party no time on a trip it makes together:** the leader takes twice as long,
  and the party is whole when it arrives. Run, the leader was there in half the time and then waited
  a minute for the others.
- **The walks to the inn** took 61 to 88 s at the same pace. In the one sampled (133 tiles of
  path) the furthest member was 6 tiles from the leader at the median and 12 at most, and the leader
  stood 7 s in all for someone.
- **Into a fight** (the lich scene's full wave, memory off, help and callouts on, three runs with
  `--set walk=true` beside the three run above): the party set out at a walk and was running 3 to
  6 s later, as soon as the wave came into view, the furthest member 4 to 6 tiles from the leader
  at the median. Deaths 1, 0 and 2 against 3, 1 and 1 running; the lich dead in 20.4 s and 22.6 s
  and once not at all, against 9.8, 12.7 and 15.4 s; "falling back" called 10 times against 2,
  six of them in the run where the lich lived. So no more deaths, a slower lich and more leaves, in
  three runs each: not enough to say walking in costs anything, and not ruled out.
- **An earlier revision crawled:** 40 waits on one trip and 179 tiles unfinished after 9 minutes,
  the leader waiting 20 s at a time for a follower that was resting 9 tiles behind it. The leader
  now waits once for each member until it has caught up, and a follower in a walking party closes
  up from 5 tiles.

**While the leader is down** (cuo-e1p; `uv run uo-brain fleet fleets/party-leader-down.toml --set
memory=false`, the leader staged 26 tiles ahead of the others so that it meets the full wave alone;
one run, `logs/fleet/20261011-052950-party-leader-down`):

- **The leader died 11 s into its hunt,** alone, as staged. Before cuo-e1p the others then waited
  through its resurrection and its walk back.
- **They carried on at once:** the tamer was on the hunt again within the second of the leader's
  `resurrect` goal, and the mage fought on through it. The archer and the bard had just left ("6
  coming at once", "4 coming at once") and their own leave refused them the carry-on ("not going back
  near Britain Graveyard yet: it left there 0 min ago"), which is the leave rule doing its work.
- **Four without their warrior didn't win this wave:** the tamer died, the lich was never killed,
  and the wave was still there five minutes on. The leader was back on its feet 80 s after dying
  (38 s to the healer, 40 s of healing).
- **What it changed:** the leader then went back for its corpse with the three beside it, who had
  lived by leaving, and died on the way. The rule now asks that none of the party was driven off
  from the place ([the leader's corpse](#several-agents-on-one-server)); not measured again since.

**Leaving the inn** (cuo-0jz, 2026-10-11, the new client in `bin/osx-arm64-party`, two runs, not filmed,
nothing else running; `uv run uo-brain fleet fleets/party-inn-exit.toml --hours 0.25 --set memory=false`
with `UO_CLIENT_DIR` pointing at that client; `logs/fleet/20261011-135545-party-inn-exit`, `-142002`).
Before the fix, the first walk after a table failed with "no way there on the map" in two runs of three
(`logs/fleet/20261011-032556-party-inn`, `-040100`): each time the leader had a companion on its next path
tile, and the replan round the stuck spot blocked the next four tiles, which closed a room two tiles wide.

| | the first walk after the table | on the way |
|---|---|---|
| before, North Britain inn at 1493,1616 | no route, 145 tiles short, after 33 s | stuck at 1496,1619 and 1500,1620 |
| before, North Britain inn at 1458,1525 | no route, 99 tiles short, three goals running, then 172 short to the bank | stuck at 1468,1518 each time, Isolde on its next tile |
| after, 1493,1616 | arrived at the graveyard in 137 s | walked round a companion once ("someone stands in the way"); Corwin made way |
| after, 1458,1525 | arrived at the graveyard in 155 s | no stuck spot; on the walk in, a replan round a stuck spot at the door found no way and the plain plan got there (before: "no route") |

Over the two runs the leader walked round someone 8 times and followers made way 6 times. Two runs; the
failure came in two of three before.

### NPCs with lives of their own

`uv run uo-brain fleet fleets/britain-lives.toml` (0.33 h) on the local server, 2026-10-09: three NPC
warriors on the `npc` profile (then capped at $0.15 an hour), no session goals, each with a persona
and one long goal, hearing only each other. Logs: `brain/logs/fleet/20261009-130813-britain-lives`;
report: `uv run uo-brain fleet-report DIR`. A single run.

| NPC | persona, long goal | what it chose | kills | deaths | $/h | lines said |
|---|---|---|---|---|---|---|
| Garrick | a careful journeyman smith; save 5000 gold for a forge | banked its 300 gold "for the forge fund", then short low-risk hunts near Britain | 0 | 2 | 0.128 | 3 |
| Ilsa | a cautious healer's apprentice; learn the dungeons near Britain | set off to look at the Orc Cave and then Despise ("long goal 4: scout the dungeon entrances") | 0 | 3 | 0.130 | 4 |
| Torv | a bold sell-sword from Minoc; reach 85 swordsmanship | bought bandages, then hunted "for swordsmanship practice toward 85" | 7 | 4 | 0.115 | 3 |

- **Their own days:** every first goal followed from the persona and its long goal, with nothing
  from the player.
- **Talk:** 10 lines said in 20 minutes, in character. Torv: "Garrick. You want something from
  me, or are you just going to stand there staring at my sword?" Garrick: "Neither, Torv. Just
  looking at a good blade and wondering who made it." Ilsa to Torv: "I keep failing to find a road
  to that cave near the orc country. Do you know a safe way in?" Talk cost $0.002 an hour an NPC
  (Jev's yes/no and Haiku's line). In this run the names shown over heads were also heard as
  lines, which inflated "heard"; fixed since.
- **Costs** an NPC, per kind: planner $0.007-0.013, heartbeat $0.0013-0.0019, memory picks
  $0.0009-0.0015, folds $0.0004-0.0008, talk $0.0005-0.0007 in 20 minutes, besides Jev's fights.
  The three sat at the old $0.15 cap and rested over budget at times; Harry raised it to $0.50.
- **Deaths:** 9 in all, mostly to the lich of the Britain graveyard on this server; each was
  resurrected ([after a death](#after-a-death)).
- **A second run** (`logs/fleet/20261009-133134-britain-lives`, the $0.50 cap, names over heads no
  longer heard): 25 kills and 1 death in 22-28 minutes. Their memories of the first run kept them out
  of the graveyard: Garrick and Torv went for weak sewer creatures "after the graveyard death" and
  "the near-deaths in the open country", Ilsa banked "away from the graveyard lich". Each NPC cost
  $0.18-0.28 an hour, $0.63 together. They talked little (3 lines; they rarely met), and one line
  was an answer to another's "bank" said to the banker; errand words aren't heard since.

**Does what they say change what they do?** A designed test, 2026-10-09: two new NPCs at the West
Britain bank. Kessa, cautious, starts out remembering a lich and two bone knights at the Britain
graveyard; Bran, bold, wants to test his blade on the undead there and knows nothing of it. The same
two personas under other names (Kesse, Brann) ran at the same time with nobody hearing anybody, as
the control (`uv run uo-brain fleet fleets/britain-talk.toml` and `fleets/britain-talk-control.toml`,
0.17 h; `logs/fleet/20261009-140151-britain-talk`, `20261009-140153-britain-talk-control`). A single run.

- **With talk:** Kessa warned Bran as they met ("Keep clear of the Britain Graveyard, a lich and two
  bone knights killed a warrior there"); he thanked her. His first goal had been chosen the same
  second, before he heard it, and went to the graveyard. After that hunt his planner chose "Get out of
  the graveyard, away from the bone knights and the lich Kessa warned about", then trained on weak
  sewer creatures "avoiding the graveyard until stronger". `fleet-report` counts 2 of his goals as
  following the conversation.
- **Without:** Brann went to the graveyard, then hunted north of Britain's suburbs, without a word
  about danger. No deaths in either.
- **Cost:** 3 lines in 16 minutes, talk $0.001 an hour an NPC; the NPCs $0.18-0.22 an hour in all,
  inside the $0.50 cap.

### After a death

2026-10-09, the local server: a warrior (Mortis) killed by three `still` daemons at the Britain
graveyard (1380, 1520), unguarded and 90 tiles from the Britain healer, then a 12-minute session
with the graveyard goal started while it was dead. Once it was resurrected the scenario cleared the
daemons from afar (`[AgentReset` keeps corpses more than 32 tiles away), as killers wandering off.
The graveyard's own lich stayed. Script: `uv run python scripts/death_scenario.py PORT 1380 1520 900` (from `brain/`), then
`uv run uo-brain --port 5650 session "Hunt the undead at the Britain graveyard. Keep yourself
supplied with bandages and bank your gold." --hours 0.2 --log logs/4b3-runN.jsonl`.

| run | deaths | resurrected | corpses taken back | what happened |
|---|---|---|---|---|
| 1 | 2 | 2 | 1 | resurrect, rest, recover, hunt; died at once to the lich (resurrected with little health); resurrected again but never went back for the second corpse |
| 2 | 3 | 3 | 1 | resurrect, recover, rest, hunt; died to the lich; walked back for the corpse with the lich still there and died on the way; then rested |
| 3 | 1 | 1 | 1 | resurrect, recover, then two hunts (4 kills) and a bank |

- **The skills work:** a resurrection took 32-44 s from the graveyard (once 188 s, when the
  healer took several walk-ins to offer), taking the corpse back 31-43 s; on this AOS-era server
  opening its own corpse put everything back and on ("You quickly gather all of your
  belongings"): the katana in hand and the 200 bandages back.
- **What the runs changed:** after run 1 the planner is told where its corpse is until it takes
  it back (`your_corpse`), and to rest after a resurrection; after run 2 `recover_corpse` refuses
  while danger was seen there in the last 15 minutes, unless `go_anyway`. Run 3 had both.
- Single runs, one character. The pre-AOS path (taking each item and `rewear`) hasn't run live.

### After-action review run

Single smoke run on 2026-10-07: `uo-brain review logs/arena-jev.jsonl` (the first Jev arena
run [above](#arena), with the round-3 death) read a 4 KB digest and cost $0.0136 (3,466 prompt
and 603 completion tokens, 6 s). It proposed four lines. The first was "When badly wounded or
worse with three or more enemies adjacent, flee or leave the area instead of fighting on,
even if the target is nearly dead.", with the evidence "Died at 18:00:01 surrounded by 6
adjacent hostiles. Jev chose fight on 5 straight decisions from 39% to 9% health". One line
asked for positioning ("fight from a spot where fewer can reach me"), which Jev can't
choose. All four came back at confidence 0.50.

### Renaissance rules rehearsal

Rehearsal on the local server switched to Renaissance rules
(`tools/modernuo/expansion.renaissance.json`), 2026-10-07:

| test | result |
|---|---|
| warrior, `uo-brain scenario` | 12 of 12 kills, no deaths; all 51 bandage messages read by cliloc number |
| mage, 2 rounds | 8 of 8 kills, no deaths (before the Protection fix: 120 casts of Protection, no kills) |
| item and rune names | resolved by single click within a few seconds of the corpse opening |
| mage at the Britain graveyard travels to the West Britain bank | recalled by runebook |

## Playing on public shards

> [!WARNING]
> Unattended play (auto mode) is against the rules on most shards, and some object to
> modified clients. Check each shard's rules, and keep auto mode to the local server or
> shards that allow it.

Combat assist is the same kind of tool as Razor or UOSteam, which many shards allow.

### Older rules (pre-AOS shards such as UO Renaissance)

Shards that play the rules from before Age of Shadows send less: no item properties and no
buff icons. The client tells which kind it is on from the server's features (item
properties on means AOS) and reports it as `era` in the snapshot ("aos" or "pre-aos").

- **Names:** without item properties, the client single-clicks things once each (about one a second, as the server allows) and uses the name the server shows over them: items in open corpses within 2 tiles, runes in the pack, and people nearby. Until then it uses the tile name ("Bones", "diamonds"), so loot judgments get "a supremely accurate longsword of vanquishing" a few seconds after the corpse opens.
- **Protection:** under the older rules it only adds armour and doesn't stop hits interrupting spells, so the mage doesn't cast it. Under AOS rules it casts it at most every 20 seconds.
- **Runebooks** look like a spellbook with hue 0x461 on older servers, rather than the AOS runebook graphic; both are recognised.

A rehearsal on the local server under Renaissance rules passed ([results](#renaissance-rules-rehearsal)). Not
yet tried on UO Renaissance itself.

### Connecting to UO Renaissance

What the shard needs, from its own pages and its launcher (checked 2026-10-08, when its website was
down and the game server up; the page quotes are from the Wayback Machine):

- **Server:** `login.uorenaissance.com`, port 2593, no encryption.
- **Client data:** client version 5.0.8.3 with the shard's own patched files (art, gumps, maps and statics, `tiledata.mul`, `cliloc.enu`; its maps add islands and towns). The 7.0.117.1 data from EA's patch servers won't do, and the shard's launcher, installer and patch are only on its website.
- **Accounts** are made on first login with a name and password of your choosing. The shard allows two or three per person (its pages disagree).
- **Profile:** `tools/uor/settings.uor.json` has the settings, with the agent off. Copy it next to `cuo`, fill in `ultimaonlinedirectory` with the folder holding the shard's files, and start `./cuo -settings settings.uor.json` from `bin/osx-arm64`.
- **Line of sight:** the profile asks for RunUO's rule (`"agent_sight_rule": "runuo"`), because the shard runs a RunUO of its own: no door clause, the one-unit allowance kept ([what differs](#line-of-sight)). Its staff reworked "the RunUO LOS code" without publishing what they changed, so the shard's exact rule can only be measured there. In a first session watch the client's counters: `agent.stats.not_in_sight` (the shard refusing a target) against `aimed_unseen` (aims the client's own rule counted as out of sight). A refusal with no such aim is a place where the shard is stricter than the rule in force. The other way round, the client holding back where the shard would allow, doesn't show in them.
- **Places:** the shard publishes no marker file for towns and dungeons (only `house.txt`, the players' houses), but its map page draws them from lines in its own script. `tools/uor/uor_map_markers.py PAGE.html --out brain/worlds/uor/uor-map-page.map` turns a saved copy of the page into a UOAM file, and `uo-brain world --shard uor import-markers brain/worlds/uor/uor-map-page.map --maps Felucca` makes them places ([what it gave](#uo-renaissances-map-page)). The page has no banks, healers or shops: those still need the shard's own files or a first walk round a town. The page and the file made from it are the shard's work and stay out of the repo (`brain/worlds/` is gitignored).
- **Rules:** the shard's own launcher installs ClassicUO and staff recommend it, but its Code of Conduct still lists "Non Electronic Arts (EA) or Origin Systems (OSI) Clients" as not allowed, names Razor as the only approved third-party program, says no software other than Razor may be used for repetitive tasks, and that "you may not kill monsters unattended using any means". Ask staff before connecting with the agent on.

## Known limits

- Results are single small runs, apart from the benchmark's 4 to 10 rounds.
- A character's memories don't age yet: a lesson stays until forgotten by hand (`memory forget`), however old or wrong. Jev's pick keeps the prompt bounded, not the store. The offline replay that measured the memories' effect is 98 moments of one character on one goal, and the long soak that compared memory with none is one run each, in a hostile soak world, before the road-leave stall was fixed ([results](#long-range-memory-over-a-long-soak)).
- Talk is for the local server only: NPCs hear the fleet's characters by name, so a player who took one of those names would be heard too. NPCs talk only when they meet; in a 20-minute run of three spread-out NPCs that was 3 lines.
- After a death, the corpse is taken back by opening it, which re-equips on AOS-era servers; the pre-AOS path (taking each item, `rewear`) hasn't run live. A corpse lying where danger was seen within 15 minutes is left until `go_anyway`, and corpses decay in the meantime.
- Against six melee monsters at once a mage now wins 4 rounds in 10 (`swarm`), up from none, but on the final code with 5 deaths in 10; the rules win at most one. On 2026-10-09 it won 2 to 6 rounds in 10 depending on the lane and run, with one client or four at once. Jev is offered summons, fields and curses but hardly ever picks them.
- Necromancy and chivalry are tested only under the local server's AOS rules. ClassicUO's chivalry table has OSI's mana costs (Divine Fury 10 where ModernUO asks 15), used only for the client's own check before casting. The agent can't restock tithing points.
- Plans for fights, the planner's or hand-written, haven't beaten Jev with no plan on any scenario tried (`swarm`, `attrition`, the two packs), and haven't been soak-tested in sessions.
- A pack's weight comes from the store's creature stats; a creature it doesn't know counts 0.5.
- Bandage timing and spell failures are read by cliloc number where the server sends one (ModernUO does, under both rule sets), and from the English text otherwise. They have not yet been checked against what UO Renaissance itself sends.
- The two RunUO line-of-sight rules (`agent_sight_rule`: `runuo`, `runuo22`) are unit-tested against RunUO's source, not checked against a live RunUO server; only `modernuo` has been compared with a server's answers. UO Renaissance's own changes to the rule aren't known.
- On pre-AOS shards, item names take a few seconds to learn (one single click each), so the first loot judgment on a corpse can see tile names.
- To read a spellbook or a bag, the agent opens it once, so its gump flashes briefly.
- Archers don't pick up arrows that missed and fell on the ground (before Samurai Empire servers drop them there; from it on they come back on their own once the archer stops fighting).
- A tamer knows its pet only while it's in sight: a pet left behind out of sight is lost to the agent even if it lives. Taming new pets isn't done.
- The client can't see whom a pet is actually fighting, only whom it was told to: a pet that switched to another attacker still shows its order.
- Bards don't use peacemaking on themselves (calming everyone), and songs aren't scored for difficulty: Jev only knows a song "can fail, more often against strong creatures".
- Map markers: only UOAM `.map` and the client's CSV are read, not Ultima Mapper's `.xml` (which ClassicUO also loads). A `.map` file that a shard wrote with the client's map indexes rather than UOAM's facet numbers needs `--facets client`; nothing in the file tells them apart. UO Renaissance's marker files haven't been found yet: its website was down on 2026-10-08 and 2026-10-09 (cuo-49t.8).
- Underground areas such as the Britain sewer can't be walked to (a dungeon's floor is a part of the map apart from the land, and travel can't use the entrances yet). `hunting_spots` and `what_spawns` now say so (`reach`: "not on foot ...") and list them after every place a walk reaches (cuo-432); before that the planner learned it only by failing, 14 times of 14 in the logs.
- Travel's memory of doors, locked doors and houses lasts while the client runs; it isn't saved. A door is judged locked when it hasn't moved after two tries, so one refused for another reason counts as locked for the session too.
- A long trip to a place walking can't reach, when the goal isn't closed off within 96 tiles of it (a large island), walks hop by hop until a hop brings it no nearer, then stops with "no route".
- A walk pace needs the profile's Always Run option off (it is off unless the player turned it on): with it on, the client runs every step. Mounted paces haven't been tried.
- A follower whose target is in view with no way to it (walled in, across water) stays `following` with a `why`, and looks for a way again every 8 seconds; each look that fails holds the game thread for about 0.15 s (measured in open ground, 2026-10-10). Following has run with one follower only: two followers taking different tiles is covered by unit tests, not by a live run. The follow doesn't know which way its target faces, so "beside" is whichever free tile is nearest.
- Houses and rows of houses were tried on the local server only, placed by a staff character (a single house, a tower, and rows of seven, solid or with one-tile alleys); UO Renaissance's countryside hasn't been walked.
- The walk from Britain to Cove is about 1,500 tiles and 6 to 9 minutes, round the north of the mountains west of Cove. Under Jev's defend loop a lone warrior got there in five walks out of five (after the stall fix), with lowest health between 5% and 54%: ettins, ogres and a bog thing are on the way. Recall is the way to Cove when a rune or runebook entry for it is carried.
- A leave that Jev chooses, rather than the pack rule, runs from the nearest creature only, which can be towards the rest (cuo-d28.11).
- Where it runs ([above](#where-it-runs)): Jev's places lost to code's line away in two benches (43/60 and 44/60 against 47/60, with 12 and 10 deaths against 3 and 4), and code's pick of the same places tied with it (48/60 against 49/60), so places are off by default ([why](#where-it-runs-1)). The places come from the planner's grid (map files, doors, houses, items in view); the client's own pathfinder can disagree, and then the run falls back to the line away. Whether a walk passes next to a creature is judged on the shortest walk found, which may not be the one the pathfinder takes. Moving and acting in one decision (a mage casting while it walks to a doorway) isn't done (cuo-fhf.4).
- Unattended, a lone warrior now leaves packs and lived through an hour with two sent at it, but it pays in time: after leaving, the planner tends to wait out the 30-minute refusal in town rather than hunt elsewhere (31.7 of 56 minutes resting in that run). The 30 tiles and 30 minutes are as first set.
- Jev's routine hunt calls have run live only in the soak runs (2026-10-07); the yes and no thresholds (0.65, 0.35) are as first set. In a thinly spawned area most hunts end on "unsure twice running" after about a minute, which hands back to the planner often.
- Sparing Jev calls (cuo-df8): the 8 s longest wait while quiet, the 7 tiles that end the quiet and the minute an answer is used again are as first set. Beside other goals, creatures within 7 tiles still make calls, so walking through a town with animals about costs 4-21 calls a minute.
- A headless client still opens its graphics device and loads the game files, so it takes about half a windowed client's memory, not next to none, and it needs a logged-in macOS session like any other.
- Outside a fleet, nothing logs an agent in again after a lost connection: the client waits at the login screen until `uo-brain login` or `client` logs it in. A fleet does it itself ([above](#several-agents-on-one-server)).
- Line of sight reads what the client has of the map: an item it hasn't been sent can't block in its rule.
- Only an archer or a caster engaged at range walks round to see its creature. A bard sings only at what it can see, and a tamer's pet order at a creature out of sight is refused by the server; neither walks to get sight, so with nothing in sight they wait.
- The sight check around Britain looked from where `[AgentGo` put the character, on the land; targets on upper floors came up only where a floor was the standing place picked.
- World facts for fights are shortlisted by area and keyword: a fact stored under another area name, or that names a creature differently, can't be picked. Jev's picks trail putting every shortlisted fact in (8/10 against 10/10 in the wisp scenario).
- A party has been measured on one scene, two or three runs a condition ([results](#a-party-that-helps-calls-out-and-talks)). Against its full wave the same code loses one, three or all five, and neither helping nor callouts changed deaths that three runs can show. The warrior walks in ahead of the others and takes the whole wave: nothing yet makes a party arrive together, or holds the warrior at the edge until the others are up.
- A party's leader goes back for its corpse with its companions only when the fight was won (companions at the place, or beside it with nobody driven off). The last part of that rule was written after the runs that showed the need and is unit-tested, not run live.
- A party that walks its trips sets out for a fight at a walk and runs once danger is in view. In three runs each on the full wave it had no more deaths than a running party (3 against 5) but called a leave more often (10 against 2) and killed the lich later: too few runs to tell, and worth more.
- The huddle's planners agree a plan in words and then each sets out when its own planner says: in the runs, half a minute apart. With memory on, three of five went in and one ended its session.
- The talk, the table, the callouts and the gestures are for the local server only. Callouts are overhead speech; UO's own party chat isn't used. A table written by Haiku invents small things now and then (a count of bandages); the story of the hunt it is written from is gathered in code, and Sonnet kept to it.
- Which creatures only fight back (birds, deer, rabbits) comes from ModernUO's `FightMode`; a shard whose store wasn't imported from ModernUO (UO Renaissance's) doesn't know it, so there every grey creature still counts as a hostile in the planner's situation (cuo-s9p).
- A shop errand can fail with "the vendor did not show its goods" where the same errand works minutes later: the first three bandage buys of a soak hour on 2026-10-12, not yet explained (cuo-k0d).
- Decision 1, the default for most of system one since 2026-10-12, has played the fight benchmark, the wisp scenario and soak hours, but no party run, and no cuts are tuned for it. Its re-ranks, memory picks, recorder and chat calls are measured only by agreement with Jev.
- Jev deciding when a leave stops (`--leave-stop jev`, cuo-fhf.6) is off by default: in the bench it made leaves far shorter (9 s against 32 s) but three times as many, with the wins unchanged ([results](#when-a-leave-stops)).

## Reference

### In game

- **`-agent off|combat|auto`:** set the play state (`assist` also means combat assist).
- **`-agent switch`:** switch between combat assist and auto (Alt+A).
- **`-agent next`:** do Jev's next move (Alt+D).
- **`-agent calls`:** open or close the live view of Jev's calls.
- **`-agent engage follow|defend|nearby`:** what combat assist takes on by itself.
- **`-agent pace walk|run`:** the pace of trips that don't ask for one (saved as `agent_travel_pace`; [rules of play](#rules-of-play)).
- **`-agent status`:** show the play state, authorities and thresholds.
- **`-agent accept`:** accept the pending suggestion.
- **`-agent set <behaviour> <off|suggest|auto>`:** override one behaviour.
- **`-agent bandage <pct>`, `-agent potion <pct>`:** reflex thresholds.
- **`-agent strategy [set|add|clear] <text>`:** edit the strategy.
- **`-agent template [list|<name>|set <name>|remove <name>]`:** pull in, replace with or take out a strategy template.
- **`-agent goal [<text>|clear|pause|resume|templates|template <name>]`:** set, show or change the auto-mode goal.
- **`-agent longgoal [list|add <text>|done <id>|drop <id>]`:** the character's [long goals](#long-goals); the brain applies the change at its next look (every few seconds while it runs).
- **Macros** (bindable in Options → Macros): *AgentOff*, *AgentAssist* (combat assist), *AgentAuto*, *AgentAccept*, *AgentSwitch* and *AgentNext*.

### uo-brain

Run from `brain/` as `uv run uo-brain …`; `--port` (before the command) picks the client's agent port, 5577 by default.

- **`run`:** play: fights, and in auto mode works towards the panel's goal. Options: `--mode`, `--judge jev|heuristic`, `--provider auto|openrouter|typesafe`, `--model`, `--archetype auto|warrior|mage|archer|tamer|bard|necromancer|paladin`, `--strategy FILE`, `--template NAME` (repeatable), `--duration`, `--log`, `--min-confidence`, `--shard`, `--planner-model`, `--facts jev|all|none` (which world facts reach the fights), `--machine NAME|FILE` (follow a [plan](#plans-for-fights) in fights), `--no-machines` (the planner can't set plans), `--no-escalate` (no Sonnet for the planner's hard moments), `--no-memory` (plan without the character's memory and long goals, for comparing), `--fresh` (don't carry on from the planner's saved state), `--persona FILE` (an NPC's persona, JSON), `--hear NAMES` (the fleet's other characters, whose speech an NPC hears and answers), `--budget-per-hour USD` and `--over-budget slow|cheaper|stop` ([a budget](#what-it-costs)), `--spots off|jev|rule` ([where runs go](#where-it-runs)), `--leave-stop rule|jev` ([when a leave stops](#leaving-and-packs)), `--profile NAME|FILE` and `--use KIND=MODEL` ([the model for each kind of call](#which-model-answers)). `session` and `do hunt` take the same options.
- **`machine list|show NAME|design "SITUATION" [--archetype A] [--out FILE] [--planner-model M] [--cost-log FILE]`:** plans for fights ([above](#plans-for-fights)); doesn't connect to the game.
- **`scenario`:** arena rounds with metrics; `--kit warrior|mage`, `--rounds`, `--round-seconds`, `--monsters`, `--kind`, and the `run` options.
- **`do travel|bank|buy|sell|hunt|rest|resurrect|recover-corpse …`:** one session goal ([above](#travel-banking-shops-and-hunting), [after a death](#after-a-death)); uses the world store. `do [--shard NAME] travel PLACE|X,Y [--distance N] [--timeout S] [--defend]`: `--defend` runs Jev's defend-only fight loop beside the walk, as the planner's trips do (its decisions go to `LOG.decisions.jsonl` with `--log`).
- **`session ["GOAL"] [--hours] [--party ID --party-members A,B,… --leader NAME [--party-plan leader|huddle] [--no-callouts] [--no-party-help] [--roleplay] [--party-walk]] [--hear NAMES [--no-small-talk] [--no-pace]] [--travel walk|run] [--rules NAME]`:** the planner towards a goal, from the command line; with no goal, it chooses from the character's [long goals](#long-goals). With `--party`, one of a party ([one plan](#several-agents-on-one-server)): the leader plans, any other member follows its goals. A party member's own options ([a party in a fight, on the road and at the inn](#a-party-in-a-fight-on-the-road-and-at-the-inn)): `--party-plan huddle` gives every member its own planner and a plan agreed in a huddle; `--no-callouts` and `--no-party-help` turn off calling out and helping companions; `--roleplay` turns on gestures and emotes; `--party-walk` walks the party's trips together. With `--hear`, `--no-small-talk` keeps talk to what needs saying and `--no-pace` says each line the moment it is written. `--travel walk|run` sets the pace of every trip and `--rules NAME` takes a set of rules from `brain/rules/` for every flag not given ([rules of play](#rules-of-play)). A fleet file sets all of these (`uo-brain fleet`). A party leader's planner has two goals a lone character's hasn't: `restock` (everyone buys what it is short of) and, in a party that talks, `inn` (the party walks to the nearest inn the world store knows, goes in, talks the hunt over round a table, and each keeps its own memories of it).
- **`bench [list|report FILES]`:** the judgment benchmark ([Results](#judgment-benchmark)). Options: `--scenarios core|adherence|archetypes|world|packs|sight|spots|all|NAME,…`, `--judges heuristic,jev,profile,jev+<template>` (`jev` is Jev in every system-one call, `profile` the active profile's models; each optionally `/nokite`, for no stepping back, `/nopack`, for code not leaving from packs, `/everytick`, for Jev asked at every tick as before cuo-df8, `/nosight`, for the client's sight checks off as before them, `/spots`, `/spotsrev` or `/codespots`, for runs going to the place Jev picks, the same listed the other way round, or code's pick ([where it runs](#where-it-runs)), `/jevstop`, for Jev saying when a leave stops ([leaving](#leaving-and-packs)), or `#<machine>`, to follow that plan; instead of `jev`, a model alias plays system one: `haiku`, `sonnet`, or one named with `--alias NAME=MODEL` or in the profile), `--alias NAME=MODEL` (repeatable), `--profile`, `--use KIND=MODEL`, `--facts none,all,jev` (world-fact scenarios: the conditions per judge), `--rounds`, `--lane`, `--out`, `--headless` (hide the client on `--port`, or start a headless one there if none runs, logged in with `--account`, `--password`, `--character`, `--server-port`, and closed at the end). The results record `headless` and `clients_at_start`, and each round the clients running on the machine.
- **`sight-check [--spots N] [--per-spot N] [--radius N] [--seed N] [--out FILE]`:** moves the character to spots around Britain and compares the client's line of sight with the server's `[AgentSight sample` there ([results](#line-of-sight-1)); local test server only.
- **`strategy [show|set|add|clear|load|explain]`, `strategy templates`, `strategy template NAME [--replace]`, `strategy drop NAME`:** show or change the strategy, see how Jev read it, and list, pull in or take out templates.
- **`costs [LOG…] [--json] [--prices] [--key]`:** what the AI calls in the logs cost ([above](#what-it-costs)): the total, an hour, a decision, by kind of call and model, and OpenRouter's own count for the key over the same run. `--prices` updates `brain/prices.json` from OpenRouter's list prices; `--key` shows what the key has spent in all.
- **`fleet FILE [--hours H] [--out DIR] [--no-talk] [--windowed] [--set KEY=VALUE …]`, `fleet-report DIR [--json]`:** several agents on one server ([above](#several-agents-on-one-server)). In the fleet file, `headless = true` (at the top for every agent, or per agent) starts clients without a window; `party = true` with `leader = "NAME"` plays the agents as one party on the leader's plan (only the leader needs a `goal`); `--no-talk` has nobody hear anybody (the control for a talk test). `--set KEY=VALUE` (repeatable) overrides one of the file's top-level settings for this run, read as TOML would (`--set help=false --set callouts=false`, `--set plan=huddle`, `--set memory=false`); a party's settings are `help`, `callouts`, `talk`, `small_talk`, `pace`, `roleplay`, `walk`, `plan` and `memory` ([what each does](#a-party-in-a-fight-on-the-road-and-at-the-inn)), any fleet's `travel` and `rules` ([rules of play](#rules-of-play)), and `use` gives every brain of the run a model for one kind of call (`--set 'use="table=anthropic/claude-sonnet-5.5"'`). A login that sticks is tried once more; a client that doesn't answer in time sits the run out while the others play (its login record says why), and a fleet closes the clients it started however the run ends.
- **`client [--headless] [--account A] [--password P] [--character NAME] [--host H] [--server-port N] [--log FILE]`:** start a client on `--port` from `bin/osx-arm64` unless one runs there (with `--headless`, a running one is hidden instead), and log it in, making the character a warrior if the account has none by that name ([without a window](#without-a-window)). The client keeps running after the command ends.
- **`window [show|hide]`:** show a client's window and draw again, or hide it and stop drawing; with neither, report whether it is hidden and the loop's rates.
- **`replay-planner LOG --planner-model M [--limit N]`:** a session's planner moments put to another model, offline ([above](#which-model-answers)). With `--memories CHARACTER [--shard S] [--root DIR] [--out FILE]`, each moment is asked twice, as logged and with the memories Jev picks from what the character remembered then ([Results](#the-planner-with-memories)).
- **`soak-report LOG [--disruptions FILE] [--json]`:** a report on an unattended `session` run from its log: the goals the planner chose and why, hunts, kills and deaths, where the time went, what was bought and banked, and what system one (Jev) and system two (the planner) cost, an hour and a kill. With `--disruptions` (JSONL of `{t, what}`), the goals that followed each disruption.
- **`review LOG [--accept N…] [--again] [--digest] [--planner-model M] [--cost-log FILE] [--shard S]`:** the planner model proposes strategy lines from a log, with the evidence ([above](#after-action-review)); `--accept` adds saved ones to the character's strategy, the only part that connects to the game, and keeps each in the character's memory as a lesson.
- **`goals [--shard local] [--character NAME] list [--all] [--json] | add TEXT | done ID [--why W] | drop ID [--why W]`:** a character's [long goals](#long-goals) and their progress (doesn't connect to the game; everyone's when no character is named).
- **`memory [--shard local] [--character NAME] list|add|forget|import`** (a character's own memories; doesn't connect to the game): `list [--kind event|lesson|order] [--area A] [--source S] [--search WORDS] [--stale] [--limit N] [--json]` (everyone's when no character is named), `add TEXT [--kind] [--area A] [--tag T] [--importance 1-5]`, `forget ID…`, `import LOG…` (backfill from earlier session logs, oldest first) ([above](#the-characters-memory)).
- **`world [--shard local] [--map Felucca] …`** (the world store; doesn't connect to the game): `note TEXT [--area A] [--tag T]`, `notes [KEYWORDS] [--area A]`, `place NAME`, `find KIND [--near X,Y | --near-place NAME]`, `spawns [AREA] [--near …] [--radius N]`, `hunt ARCHETYPE LEVEL [--near …]`, `route FROM TO`, `stats`, `import-modernuo [--modernuo-dir DIR] [--maps Felucca]`, `import-markers FILE|FOLDER… [--facets auto|uoam|client] [--maps Felucca,…]` (places from a shard's world-map markers, UOAM `.map` or the client's CSV; each file replaces its own rows), `import-guide URL|FILE [--area A] [--planner-model M] [--cost-log FILE]`, `fill-gaps AREA [--planner-model M] [--cost-log FILE]`, `outcomes LOG… [--area A]` (record what each hunt in the logs gave, per area and kit; importing a log again replaces its rows).
- **`login`, `status`, `snapshot [--semantic]`, `act <verb> k=v`, `accept`, `mode`, `cmd "-agent …"`, `say`, `shot FILE`, `report LOG`, `replay LOG [--model M] [--limit N]` (a log's questions asked again of Jev or any OpenRouter model: agreement with the logged answers, latency, cost; decisions Jev wasn't asked for are left out).**

### Client RPC

Newline-delimited JSON on 127.0.0.1, one object per line each way, enabled by `-agent_port`
or `agent_port` in settings.json. Replies carry the request id, and several connections can
be open at once (the brain and one-off CLI calls).

The agent's settings.json keys: `agent_port`; `agent_start_brain` and `agent_brain_dir`
([the client starts the brain](#the-client-starts-the-brain)); `headless`
([without a window](#without-a-window)); and `agent_sight_rule`, whose
[line of sight](#line-of-sight) the client follows: `modernuo` (the default), `runuo` or
`runuo22`; and `agent_travel_pace`, the pace of trips that don't ask for one (`run`, or `walk`;
[rules of play](#rules-of-play)). Each has a command-line option of the same name except `agent_brain_dir` and `agent_travel_pace`;
`-headless` and `-agent_sight_rule` hold for that launch only and aren't saved.

| method | params | what it does |
|---|---|---|
| `ping`, `status` | | liveness; the client's state, with `headless` and the loop's `updates_per_s`, `frames_per_s` and `draws_per_s` over the last second |
| `window` | `visible` | `true` shows the window and draws again, `false` hides it and stops drawing ([headless](#without-a-window)); without params, only reports. Answers like `status`'s window fields |
| `login` | `account, password, host, port, server, character, create` | drives the login screens, optionally creating the character |
| `snapshot` | `since, radius, pack, spots` | the game state ([below](#the-snapshot)) |
| `act` | `verb, …` | one action ([verbs](#act-verbs)) |
| `mode` | `mode` | set the play state: `off`, `assist` or `auto` |
| `accept` | | accept the pending suggestion |
| `strategy` | `text \| add \| clear \| template`, `replace \| remove_template` | show or change the strategy |
| `templates` | `kind: "goal"` for goal templates | list strategy or goal templates |
| `goal` | `text \| clear \| pause \| template` | set or change the auto-mode goal |
| `goal_status` | `step, why` | the planner's current step, for the panel |
| `long_goals` | `goals: [{id, text, progress}], ack: seq` | the long goals for the panel, from the brain; replies with the player's requests (`seq, action, id, text`) until they are acknowledged |
| `decision` | `{…}` | what the brain decided, for the panel; `next` is the move for the next-move key, `machine` the plan and its current step, `cost` what the call cost and `spent` the running totals, `not_asked` why Jev wasn't asked when it wasn't (not counted as a call) |
| `brain_info` | `judge, archetype, strategy_reading` | what the brain is running, for the panel |
| `ai_call` | `kind, title, model, latency_ms, cost, spent, questions, note` | a model call other than a fight decision (routine, facts, strategy, planner), for the live view of calls; fight decisions carry their `questions` in `decision`. `spent` is the brain's running cost: `total`, `per_hour`, `calls`, `by_kind`, and `budget_per_hour`, `over_budget` with a budget |
| `note` | `text` | a line from the brain, shown in the panel for 10 s |
| `command` | `text` | run a client command as if typed, e.g. `agent status` |
| `nav` | `radius` (up to 200), `goal_x, goal_y, reach, check` | travel debugging: the planner's map with doors (`d`) and remembered houses (`h`), and the path it would walk (the first hop of a long trip), with `hop`, `plan_ms`, `load_ms`, `nodes` and `why`. `reach` marks what the planner can walk to from the player (`,`); `check` also walks the client's own pathfinder out from the player and marks where the two disagree (`-` only the planner gets there, `+` only the client) |
| `items` | `radius, parts` | travel debugging: the items lying around; with `parts`, the pieces of houses in range too (`part_of`, impassable, surface) |
| `sight` | `checks, rule, pairs, serial, view` | line of sight: `checks: false` turns the agent's sight checks off (a benchmark's "before"); `rule: "modernuo" | "runuo" | "runuo22"` sets whose rule they follow for as long as the client runs (anything else is refused); `pairs: [[x1, y1, z1, x2, y2, z2], …]` answers the client's rule for raw points (no eye height added); `serial` whether the player sees that mobile; `view: radius` which ground tiles around the player it can aim at. The reply has `checks` and `rule` as they now stand |
| `capture` | `path` | save a screenshot (an error while the window is hidden) |

#### The snapshot

The player, creatures and corpses within `radius` (18 by default), new journal lines since
`since`, and the agent's own state. `pack: true` adds everything in the backpack. It also has
recent deaths, `travel` (state, the `pace` asked and whether its steps are `running` now, goal, tiles `left`, `replans`, `stuck_at` and beside each the next tile of the path and who stood on or beside it (`stuck_why`, also in the session's `travelled` log line), whether the path is a `hop`, `hops` finished, `doors` opened, `plans`, `plan_ms` and `plan_ms_max`, `why` a walk failed, and the doors, locked doors and houses `known`), `follow` while following (`target`, its `name`, `state`: `following`, `waiting` or `lost`, `distance` in tiles or -1 when the client doesn't have the mobile, `running`, the `pace` asked, `within`, `x` and `y` where the target is or was last seen, and `why` when it is in view with no way to it; there is no `travel` while following, nor after it until the next trip) and `errand` progress, each creature's full `label` with its title,
whether each corpse is a monster's, `player.ranged` (kind, ammunition and range of a bow or
crossbow in hand), arrows, bolts, whether an instrument is in the pack and `unread_bags` (the
backpack's bags not looked into yet, whose contents aren't counted) under `player.supplies`, `pets` (the character's pets in sight, with health), `agent.pet_order` and
`pet_target`, `travel_items` (marked runes and runebook entries) and `era` (`aos` or
`pre-aos`), and `player.followers` and `followers_max` (follower slots, which pets and summons take), each creature's `in_sight` (left out with the sight checks off), `agent.sight_checks`, `agent.sight_rule` (`modernuo`, `runuo` or `runuo22`), and under `agent.stats` `not_in_sight` (the server's "Target can not be seen" messages), `aimed_unseen` and `unseen_engaged_ms` ([line of sight](#line-of-sight)). `magic` covers every book carried (magery, necromancy, chivalry): `schools`, each
spell with its `school`, mana, tithing cost and what is missing, and `tithing` points; a
necromancer's reagents are under `player.supplies.pagan_reagents`.
`spots: true` adds the places to stand ([where it runs](#where-it-runs)), only with a monster in
view: each place's `kind` (`stay`, `ring`, `doorway`, `corridor`, `pet`, `npc`), `name` (a
landmark's), `x`, `y`, `dx`, `dy`, `ring` and `bearing` (0 to 15 from north, clockwise), `steps`,
`dir` (16 points), `room`, `near` (`[serial, tiles]` for the 8 nearest monsters, nearest first),
`seen_by` and `passes` (serials), and `spots_ms`, the time they took. `agent.stats` has
`spot_walks`, `spot_fallbacks` (no path there) and `spots_computed`.

#### Act verbs

- **Fighting:** `attack {target, range}`, `disengage` (let the creature being fought go, leaving the rest as it is: a trip waits while the agent is engaged), `war_mode`, `stop`, `cast {spell, target, queue}` (a field or a placed summon aimed at a creature goes on the free tile beside it nearest the character), `skill {name, targets}` (with `targets`, a bard's song: the client answers each target cursor in turn, and the instrument prompt), `pet {kind: kill|follow|guard|stay, target}`, `flee {target, tiles, x, y}`, `kite {tiles, x, y}` (step back from melee, 2 to 8 tiles). With `x, y` (a place from the snapshot's `spots`), a run goes there, and a step back if it is within 8 tiles; with no path there, away from the creatures as without.
- **Healing:** `bandage_self`, `bandage`, `drink {kind}`.
- **Loot and items:** `loot`, `take` (from a monster's corpse, or the character's own after a death), `use`, `target`, `rewear` (put back on what was worn at the last death, from the pack).
- **Moving:** `walk_to {x, y, distance, run}` (`run: false` walks it), `move`, `travel {x, y, distance, pace}` (`pace`: `run`, as it is without, or `walk`, which still runs while there is danger), `follow {target, distance, pace}` (keep within `distance` tiles, 2 without, of that mobile as it moves; `pace`: `walk`, as it is without, or `run`; asked again for the same target it only takes the new distance and pace), `recall {target: rune or runebook, distance: entry, kind: spell|charge|gate}`. A pace that is neither `walk` nor `run` is refused.
- **Errands:** `bank {deposit, withdraw}`, `buy {target, items}` and `sell {target, items}` (`items` like `"bandage:50"` or `"loot"`).
- **Other:** `say {text, kind}` (`kind: emote` says it as an emote, in the profile's emote colour and between asterisks, added when the text doesn't have them: `*has a swig of ale*`), `bow` and `salute` (the gestures of the client's own Bow and Salute macros), `wait`, `hint {text}` (text above your head, client-side only, never refused).

Rules:
- **Cast authority:** healing spells count as `heal`, Cure as `cure`, attack spells as `fight`, anything else as `misc`.
- **Authority:** an act request is subject to authority unless it has `"source": "manual"`. In combat assist, an attack or harmful cast at a creature the engage setting doesn't allow is refused ("not your target").

### Test server commands

These come from `Projects/UOContent/Custom/AgentTestKit.cs` in ModernUO (the copy in this repo is
`tools/modernuo/`) and work for normal player characters. Accounts are created on first login;
`admin`/`admin` is the owner.

- **`[AgentGo [lane | x y]`:** go to the test field in Felucca, Green Acres (5445, 1153). It has no guards and no spawns. Lanes 1–9 are copies 60 tiles apart, so several test characters can run at once. With `x y`, go to that tile instead (for travel tests in town).
- **`[AgentKit`:** warrior template. Swords, Tactics, Healing and Anatomy at 80, katana, ringmail, 200 bandages, 5 heal and 5 cure potions.
- **`[AgentKit mage`:** mage template. Magery 90; Evaluating Intelligence, Meditation and Wrestling 80; Resisting Spells 60. A full spellbook, a bag of 100 of each reagent, leather armour, and 5 heal and 5 cure potions.
- **`[AgentKit archer [bow|crossbow|heavycrossbow]`:** Archery, Tactics, Healing and Anatomy 80; dexterity 85; the bow (or crossbow) with 200 arrows (or bolts), studded leather, bandages and potions.
- **`[AgentKit tamer [bear|wolf|hound|drake]`:** Animal Taming and Animal Lore 90, Veterinary 90, Healing and Anatomy 70, no weapon, and a tamed pet following (a grizzly bear unless another is named). Each kit replaces the last kit's pet.
- **`[AgentKit bard`:** Musicianship, Provocation, Peacemaking and Discordance 90, Healing and Anatomy 60, a lute and no weapon.
- **`[AgentKit warriormage`, `[AgentKit magetamer`:** the warrior kit plus Magery 80, a spellbook and reagents; the mage kit plus Animal Taming and Lore 85, Veterinary 60, bandages and a pet.
- **`[AgentKit necro`:** Necromancy 90, Spirit Speak 80, Meditation 70, Healing 70, Anatomy 60, Wrestling 70; a full book of necromancy, 100 of each necromancer's reagent, 100 bandages, leather and potions.
- **`[AgentKit paladin`:** the warrior kit plus Chivalry 90, intelligence 60, a full book of chivalry and 10,000 tithing points.
- **`[AgentArena [count] [kind]`:** spawns monsters in a ring 6–10 tiles out (orc, ratman, headless one and mongbat by default).
- **`[AgentReset`:** resurrects and heals you, cancels pending spawns, and removes the arena, the corpses and what you built with `[AgentBuild` around you.
- **`[AgentSpawn <kind> [count] [distance] [direction] [delay] [still]`:** spawns creatures of a kind (`orc`, `ratman`, or any ModernUO type such as `OgreLord` or `OrcishMage`) at a distance and compass direction, optionally after a delay. `still` anywhere in the line makes them stay where they are (they fight what comes to them but never walk). They belong to your arena.
- **`[AgentSupplies [bandages N] [heal N] [cure N] [reagents N] [arrows N] [bolts N] [gold N] [loot N]`:** sets your supplies; `gold` and `loot` add coins and sets of the loot items below to your pack.
- **`[AgentLoot [distance] [direction]`:** lays an orc's corpse holding three valuables (diamonds, a gold ring, a magic longsword) and five pieces of junk (bones, a head, a shirt, kindling, raw ribs).
- **`[AgentWall x1 y1 x2 y2 | clear`:** an invisible wall along a line, for stuck tests. Its blockers don't block sight.
- **`[AgentBuild wall <distance> <direction> [length] [stone|window|fence|los] | hut <distance> <direction> [size] | clear`:** real wall pieces, for line-of-sight tests. `wall` lays a straight wall (7 long by default) across the way, `distance` tiles off in a compass direction: stone (the default) and window pieces block sight, a wooden fence (6 high) doesn't, and `los` is ModernUO's invisible LOSBlocker. `hut` builds a square room of stone walls (5 across by default) with a doorway in the middle of its far side. `clear` (and `[AgentReset`) removes them.
- **`[AgentSight x1 y1 z1 x2 y2 z2 | sample [count] [radius] [seed]`:** the server's line of sight. With two points, whether the first sees the second (the points as given; a mobile's eye is its z + 14). `sample` tests `count` random tiles (100 by default) within `radius` (15) of you, each twice from your eye: a mobile standing there (on a floor or the ground where one fits) and the ground there as a field is aimed. The answers go to your journal 40 to a line, `AgentSight <x> <y> <z>: x,y,zm,m,zg,g;…` (1 in sight, 0 not), for `uo-brain sight-check`.
- **`[AgentRestock [amount]`:** stocks the vendors within 12 tiles with at least that many of everything.
- **`[AgentRunes`:** a runebook full of charges marked to the test field, the West Britain bank, the Britain graveyard and the Britain healer, and loose runes to the first two. (Green Acres itself can't be recalled out of.)
- **`[AgentDisrupt despawn x y radius [minutes] | strong x y kind count | sellout x y radius item | restore [quiet]`:** disruptions at a place, for unattended runs. `despawn` empties and stops the spawners there (20 minutes by default); `strong` spawns creatures there; `sellout` empties the vendors' stock of an item; `restore` undoes all three. A `quiet` last word keeps the replies out of the caller's journal, so an agent under test can't read about it.

## Where things are

<details>
<summary><b>The client</b>: <code>src/ClassicUO.Client/Agent/</code></summary>

| file | what it does |
|---|---|
| `AgentHost` | RPC dispatch, login, screenshots, showing and hiding the window |
| `MacApp` | the AppKit calls a headless client needs: a background app while hidden, a normal one while shown |
| `AgentServer` | the loopback JSON-lines socket |
| `AgentController` | modes, reflexes, actions, casting, human pause, strategy, templates |
| `AgentBrain` | starts and restarts the brain process |
| `AgentNav` | long-walk planning over the map files: door tiles, hops, goals ruled out early, and `TravelMemory` (doors, locked doors and houses seen) |
| `AgentTravel` | walking a plan: legs for the client's pathfinder, doors opened on the way, replanning round houses and when stuck |
| `AgentFollow` | the pace of a walk (a walk that runs while there is danger) and following another character: where to stand beside it, catching up, waiting through a fight, lost and found |
| `AgentSight` | line of sight: the server's rule (ModernUO's, or one of RunUO's two by `agent_sight_rule`) over a tile reader, and the reader for the client's map |
| `AgentSpots` | places to stand for the `stand` question: coverage, snapping, the facts per place, and runs to one |
| `AgentErrands` | bank, buy, sell |
| `AgentRunebook` | reading runebook gumps |
| `AgentWeapons` | bows and crossbows |
| `AgentPets` | finding pets, pet orders |
| `AgentBard` | songs and their target cursors |
| `AgentRearm` | a warrior-mage's weapon back after a cast |
| `ReflexPolicy` | the reflex rules, as pure functions |
| `AgentSpells` | magery, necromancy and chivalry: costs, reagents, tithing, cast timings, books |
| `AgentMessages` | server messages by cliloc number, English as fallback |
| `AgentSnapshot` | writes the state JSON |
| `AgentJournal` | keeps the journal lines in sequence for the brain |
| `AgentLogin` | drives the login screens |
| `AgentGump` | the panel |
| `AgentLongGoals` | the long goals as the panel shows them, the player's requests, `-agent longgoal` |
| `AgentDecision` | one decision as the brain reports it, for the panel |
| `AgentCalls`, `AgentCallsGump` | every model call with its options and Jev's pick, and the live view of them |
| `AgentAction`, `AgentTypes` | one requested action; behaviours, authorities and play states |
| `AgentTemplates`, `Templates/*.md`, `Goals/*.md` | strategy and goal templates |

</details>

<details>
<summary><b>The brain</b>: <code>brain/src/uo_brain/</code></summary>

| file | what it does |
|---|---|
| `state.py` | snapshot to words |
| `questions.py` | the Jev request |
| `policy.py` | decisions to actions |
| `spells.py` | attack spells |
| `strategy.py` | your strategy to settings |
| `judge.py` | Jev or rules |
| `loop.py` | the fight loop, and when it spares Jev a call |
| `autopilot.py` | what `run` does: the fight loop, or the planner when auto mode has a goal |
| `cli.py` | `uo-brain` |
| `rpc.py` | the client's RPC |
| `bench.py` | judgment benchmark |
| `sightcheck.py` | the client's line of sight against the server's (`sight-check`) |
| `session.py` | travel, bank, buy, sell, hunt |
| `routine.py` | Jev's routine calls inside a hunt: head back, stay, walk elsewhere |
| `planner.py` | the slow planner |
| `calls.py` | every model call, with its options and Jev's pick, for the client's live view |
| `machine.py` | plans for fights as state machines: checking, running, and the planner designing them |
| `world.py` | world store and the planner's query tools |
| `memory.py` | a character's own memories: the store, what code keeps when a goal ends, the planner's `remember` and `recall`, Jev picking what each planner call sees |
| `compact.py` | the planner's running summary of a session, with a memory flush first |
| `heartbeat.py` | always on: reflections every 30 minutes and on events, daily digests, resuming after a restart |
| `talk.py` | characters hearing the fleet's others and answering in their persona: one conversation for those in earshot (the floor), small talk, stories told, a human pace |
| `party.py` | a party on one plan: the leader's goals posted for the others to follow, each member's state and fight row, callouts' meanings, the follower loop (the inn, carrying on while the leader is down) |
| `mates.py` | the party in a fight: what is on each companion, the others' callouts in the fight state, this member's callouts, its own story of the hunt |
| `voice.py` | a character's voice pack: its callouts and emotes, written once in its persona and kept in the store |
| `table.py` | R&R at the inn: the table's turns, each member's lines from its own story of the hunt, and the memories each keeps of the evening |
| `roleplay.py` | gestures and emotes on events (a bow on meeting, setting out, resting, after a fight, by a fallen companion) |
| `huddle.py` | the party huddle: the blackboard (target, roles, rally point), when one is called, what each member brings and does with it |
| `longgoals.py` | long goals that outlive a session: progress measured by code, milestones, the planner's `long_goal`, the panel's view |
| `facts.py` | Jev picks the world facts for fights and re-ranks the planner's queries |
| `recorder.py` | the world store from what the agent sees |
| `world_import.py` | fills the local store from ModernUO |
| `markers.py` | places from a shard's world-map markers (UOAM `.map`, the client's CSV) |
| `guides.py` | guide pages and model knowledge to notes |
| `outcomes.py` | session logs to outcomes per area and kit |
| `soak.py` | reports on unattended runs |
| `logs.py` | reads the brain's logs back |
| `review.py` | after-action review: strategy lines from a log |
| `llm.py` | OpenRouter chat client for the planner model |
| `costs.py` | what every AI call costs: the ledger, its log records, totals and the budget |
| `fleet.py` | several agents on one server: clients, logins, sessions, load and the report |
| `client.py` | starting a client, windowed or headless, and logging it in (`client`, the fleet, `bench --headless`) |
| `models.py` | which model answers each kind of call (profiles), and the adapter that puts Jev's questions to any chat model |

</details>

| path | what's there |
|---|---|
| `brain/worlds/<shard>/` | the world store, `world.sqlite` (gitignored) |
| `brain/machines/` | plans for fights: written by hand (`pull-one`, `mage-swarm`, the wall and summon tests), and the planner's (Haiku's, Sonnet's and Opus's from the same situations) |
| `brain/profiles/` | which model answers each kind of call, and budgets: `default` (Decision 1 and Jev by kind, Haiku), `jev`, `sonnet`, `opus`, `decision1`, `npc` |
| `brain/rules/` | named sets of rules of play (`realistic`, `run-always`); `rules.py` loads and applies them |
| `brain/fleets/` | several agents on one server: `graveyard-three` (the player's warrior and two NPCs), `britain-five` (and four NPCs), `britain-five-headless` (the four NPCs without windows), `britain-lives` (three NPCs with lives of their own), `britain-talk` and `britain-talk-control` (the talk test), `fewer-calls-ab` (the fight loop old and new side by side), `party-lich` and `party-lich-solo` (the lich scene: one plan, and a planner each), `party-inn` (the same five with personas, talk, roleplay, walking together and the inn), `party-leader-down` (the leader staged ahead so it dies first), `party-walk` (the same five on a long walk through Britain, no fight), `party-inn-exit` (the inn first, so the walk out of it comes early) |
| `brain/scripts/` | soak runs: preparing the character (`soak_prep.py`), sending packs at it (`soak_packs.py`), both with a session (`soak_run.sh`), the planner on several models one after another (`planner_compare.sh`), the same run with different options (`flags_compare.sh`), a long run with the brain killed halfway (`restart_soak.sh`) and its report (`memory_soak_report.py`); a death to recover from (`death_scenario.py`); travel trips and counts (`travel_trip.py`, with `--defend` the rule judge's fight loop beside it; `travel_counts.py`); the planner's hard moments put to several models, offline (`hard_moments.py`); a party's runs: who reached the fight, who died and how long the lich took (`party_scene.py`), its callouts, who heard them and who helped whom (`party_calls.py`), its talk with a transcript, pace and cost (`party_talk.py`), and where every member was second by second on a trip (`party_walk.py`); two system-one models on the same recorded requests, offline (`judge_compare.py`) |
| `tools/video/` | filming a test run: the window recorder (`wincaps`), `film.py` around a command, `cut.py`, and `layout.py`, which sets a fleet's clients up for the camera and puts them back ([seeing what it does](#seeing-what-it-does)) |
| `tools/uo-download/` | official client downloader (EA patch protocol, UOP rebuild) |
| `tools/modernuo/` | test server commands (`AgentTestKit.cs`), start script, setup and config notes |
| `tools/uor/` | the settings profile for UO Renaissance ([connecting](#connecting-to-uo-renaissance)), and `uor_map_markers.py`, which turns the shard's map page into a marker file to import |
| `docs/` | upstream ClassicUO's README, and the images for this one |
| `tests/ClassicUO.UnitTests/Agent/`, `brain/tests/` | `dotnet test tests/ClassicUO.UnitTests --filter "FullyQualifiedName~Agent"`, `cd brain && uv run pytest` |

## Credits and licence

- **ClassicUO.** This is a fork of [ClassicUO](https://github.com/ClassicUO/ClassicUO), the open
  source Ultima Online Classic Client by andreakarasho and its contributors, built on
  [FNA](https://github.com/FNA-XNA/FNA). Its own README, with its downloads, support links and
  the projects it drew on, is kept in [docs/classicuo.md](docs/classicuo.md). If the client is
  useful to you, support it on [Patreon](http://www.patreon.com/classicuo).
- **Models.** [Jev](https://docs.typesafe.ai) is TypeSafe's; the planner is Claude Haiku (or
  Sonnet), reached through [OpenRouter](https://openrouter.ai).
- **Licence.** BSD 2-Clause, as upstream ([LICENSE.md](LICENSE.md)).
- **Game files.** No copyrighted game assets are distributed. You need the official client
  files, which `tools/uo-download` fetches from EA's patch servers. Using a custom client to
  connect to official UO servers is forbidden.

Ultima Online® © Electronic Arts Inc. All rights reserved.
