# ClassicUO with a Jev agent

This fork adds an AI agent to ClassicUO that can play alongside you or play on its
own. Decisions come from [Jev](https://docs.typesafe.ai), TypeSafe's "System One"
model, which answers typed questions (pick one of these options, yes or no, rate
this) with probabilities in about 100 ms. Ordinary code does everything that has a
right answer: healing thresholds, pathfinding, looting mechanics, safety rules.

Status: works end to end against a local ModernUO server with a warrior or a mage, with Jev
deciding through OpenRouter, and an in-game panel that shows what Jev is thinking.
The original ClassicUO README follows [further down](#classicuo).

## How it works

```
 ClassicUO client (C#)                      uo-brain (Python)                 Jev (OpenRouter
 src/ClassicUO.Client/Agent                 brain/                            or TypeSafe)
 ─────────────────────────                  ──────────────────                ─────────────
 every frame:                               every 250 ms: snapshot
   reflexes: bandage, heal/cure potions       │
   carry out actions: pursue a target,        │ when something changed, or every 1-3 s:
   loot a corpse, flee                        ├─ state in words ("badly wounded, adjacent")
   authority check per behaviour   ◄─ RPC ──  ├─ one request, all questions ────────────►  answers +
   pause while the player moves/clicks        ├─ policy: mask, confidence gate, strategy ◄─  probabilities
   agent panel, -agent command, macros        └─ act, report the decision to the panel, log
```

1. **The client snapshot** gives the player, the creatures and corpses within 18
   tiles, new journal lines and the agent's own state.
2. **The brain describes the situation in words.** Jev reads "badly wounded,
   adjacent, to the east" far more reliably than raw numbers and coordinates. How
   strong each creature is comes from its stats in the world store ("an ogre lord, far
   stronger than you: do not fight it alone", "a spellcaster"), and supplies are worded
   too ("running low: 12 bandages and 1 heal potion left").
   Candidates get short ids (`t1`, `c1`, `i1`) so the model can only pick things
   code has already checked. Other players' speech is never sent to the model.
3. **One Jev request asks every question that could matter**:
   - `intent`: fight, flee (for a moment), leave (the area: run until nothing is in sight), loot, seek or rest
   - `in_danger`: will the character die soon if it keeps fighting?
   - `target`: which creature to attack
   - `spell` (mages): which attack spell to cast next
   - `corpse`: which corpse to loot first
   - `take_iN`: is this item worth picking up?

   Code uses the answers that apply and ignores the rest.
4. **The policy is code.** It masks options the facts rule out (no looting with a
   monster adjacent), requires the intent and the danger judgment to agree before
   fleeing, keeps its current course when confidence is low, and applies your
   strategy settings.
5. **The client has the last word.** It checks the behaviour's authority before
   anything happens, and the brain may only ever attack monsters.

## Features

### The agent panel

Jev's thinking is shown inside the game, in a UO-style panel that opens beside the game view
whenever the agent is on or a brain is connected. It shows:

- **Play state buttons:** off, combat assist and auto, with the keys that switch between them and do Jev's next move.
- **Combat assist settings:** what it engages (your target, also attackers, or anything near) and whether it fights on its own or waits for your next-move key.
- **The goal** (auto mode): what you asked for, the step the planner is on and why, *pause*/*resume* and *clear*, an entry box, and the goal templates (see [Goals and the planner](#goals-and-the-planner)).
- **The brain's state** in the title row ("brain starting", "brain running"), and a *paste key* link while there is no model key.
- **Watch out:** a red, criminal or unknown player within 8 tiles.
- **What the agent is doing:** for example "fighting Vorgak", "casting Explosion" or "bandaging", and "you're driving" or "you have the controls" while you're playing.
- **Jev's judgment** for the latest decision:
  - a bar for each intent with Jev's probability, plus options the facts ruled out;
  - the danger judgment;
  - the target and spell it picked, with confidence, or "your strategy" when your strategy chose the spell;
  - what was done, and the latency.
- **A pending suggestion** with an *accept* link.
- **Your strategy and how Jev read it**, an entry box to add a line, and the template links (see below).
- **Earlier decisions**, with repeats collapsed ("fight an orc x5").
- **Kills, deaths, heals and casts.**

When Jev picks a new target, "jev: target" appears above that creature in the world. *less*
collapses the panel to a few lines; its position and whether it is collapsed are saved per character.

### Play states and authority

There are two ways to play, and one key between them:

- **Combat assist:** you drive. The agent fights alongside you and never walks the character.
- **Auto:** the agent plays on its own.
- **Off:** nothing runs.

**Alt+A** switches between combat assist and auto (from off it starts combat assist), and
**Alt+D** does Jev's next move. Both are ordinary macros (*Agent: switch* and *Agent: next
move*) added once per character; rebind or delete them in Options → Macros. A key you
already use is left alone, and the macro is added without a key.

Each behaviour has its own authority: `off`, `suggest` or `auto`. The play states are presets:

| state | heal / cure / potion | fight | loot / misc | move |
|---|---|---|---|---|
| `off` | off | off | off | off |
| combat assist (`assist`) | auto | auto | suggest | off |
| `auto` | auto | auto | auto | auto |

- **Overrides:** change any single behaviour, e.g. `-agent set fight suggest` while in combat assist.
- **Behaviours:** `heal` (bandages), `cure` (cure potions), `potion` (heal potions), `fight`, `loot`, `move`, `misc`.
- **Saving:** settings are saved per character. A character saved in the old assist mode starts in combat assist.

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

**Auto** is fully autonomous play.

**Handing over control:**
- **In auto,** moving, clicking in the world or using the arrow keys is a short override: control comes back to you for 4 seconds, any walk the agent started stops, and only the healing reflexes keep running. The Alt+A key is the real switch.
- **In combat assist,** your input only pauses the agent's own movement (looting a corpse, if you accepted it). The fight goes on while you walk, and a spell it is casting still goes off at its target unless you clicked in the world.
- **Your target sticks:** if you attack a different monster yourself, the agent follows your choice.
- **Your spell cursors are yours:** if you click in the world while one of the agent's attack spells is being cast, its target cursor is left to you.
- **Panel clicks don't count:** clicking the panel doesn't pause the agent.

The auto-mode hand-back was verified with real mouse and keyboard events; see [Results](#results-so-far).

### Mages

The agent plays a mage when the character has a spellbook and its Magery is at least as
high as any weapon skill, or with `uo-brain run --archetype mage`.

- **Range:** it engages from up to 7 tiles away and casts Jev's pick from the attack spells it can cast right now: Flamestrike, Energy Bolt, Explosion, Lightning, Mind Blast, Fireball, Harm, Magic Arrow, Poison and Paralyze.
- **Your spell plan comes first:** the opener on a fresh creature, then the main spell. Jev picks when your strategy names none; code picks the strongest castable spell when Jev isn't sure.
- **Queued casts:** the brain queues the next spell and the client casts it the moment the current spell and its recovery allow. Healing reflexes go first.
- **Protection:** with a monster in melee reach it casts Protection first, since every hit otherwise interrupts a spell.
- **Kiting:** with two or more monsters in melee reach it steps back 5 tiles between spells, keeping its target; the next spell waits for the step, since casting roots the mage.
- **Meditation:** it meditates while resting with mana below 80%.
- **Seeing inside:** the server only says what's in a spellbook or a bag once it's opened, so the agent opens the spellbook and any unopened bags in the backpack once, and closes them again.
- **Snapshot:** mana, reagent counts, every spell in the book with its cost and why it can't be cast ("mana", "reagents"), and the cast timing.

### Strategy, in your own words

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
reading under your strategy.

#### Templates

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

### Reflexes

The client runs these without the brain:

- **Bandages:** bandage yourself below 85% health, or when poisoned.
- **Heal potion:** drink one below 40%, with the 10-second cooldown respected.
- **Cure potion:** drink one when poisoned and hurt.
- **Healing spells** (characters with no bandages): Heal below 65%, Greater Heal below 50%, and Cure when poisoned if no cure potion is ready.

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

### Other players

The client watches for other players within 8 tiles: **red** (murderers), **criminals**,
and **unknown** players, meaning people without an NPC title ("Lucy the healer"). Titles come
from item properties, or, on shards without them (pre-AOS), from one single click per
person; until a title is known, only red and criminal players are flagged.

- **Combat assist:** a warning above their head, in the panel and in the journal, once a minute per player.
- **Auto:** it leaves: a red or criminal player within range makes the character run 15 tiles away from them, and a hunt ends ("a red or criminal player came close") so the planner can choose somewhere else.
- **Defensive only:** the agent never attacks a player, whatever the mode.
- **The world store** notes "A red player was seen here." for the area, never who.

### Seeing what it does

- **Agent panel:** Jev's probabilities, choices and actions for every decision, in game (see above).
- **Decision log:** every decision goes to a JSONL file with the state, all answers and probabilities, the actions and their results.
- **`uo-brain report`** summarises a log.
- **`uo-brain replay`** re-asks a log's questions to another judge offline, e.g. Jev against the rule baseline, and reports how often they agree.
- **Assist agreement:** with fight set to suggest, the brain records whether you attacked the creature it suggested.

### World knowledge

The brain keeps what it knows about each shard in a SQLite file,
`brain/worlds/<shard>/world.sqlite` (`local` is the ModernUO test server). The planner
looks facts up with tools when it needs them, instead of having them pasted into every
prompt. The store holds:

- **Places:** banks, healers, vendors and what they sell, moongates, teleporters, dungeon entrances and levels, shrines and landmarks.
- **Regions:** towns, dungeons and other named areas, with their bounds and whether guards protect them.
- **Spawns:** which creatures spawn where, how many at once and how quickly they come back.
- **Creatures:** hits, damage, fame and karma, and a difficulty word (trivial, weak, moderate, strong, deadly) used to rate hunting spots.
- **Routes:** paths that worked, paths that got stuck and where, and teleporter links.
- **Outcomes:** what hunting somewhere gave, per area and kit: kills, deaths, supplies and gold per loop, and which creatures cost the most.
- **Notes:** free-text facts, searched by keyword.

The planner's queries are `place` (a loose name to coordinates: "britain bank", "Britain
graveyard"), `find_place` (the nearest bank or healer, or a vendor that sells bandages),
`hunting_spots` (for an archetype and level, towns left out, with past results there),
`what_spawns`, `route`, `notes`, `outcomes` (how earlier hunts in an area went) and
`region_at`. `world.tool_schemas()` gives them as tool definitions for a model
and `World.call_tool` runs them. Distances are in tiles, counted as max(|dx|, |dy|).

Every row records its source (`modernuo:<file>`, `seen`, `note`, `guide:<url>`,
`model:unverified`, or `outcomes` for the notes that sum up an area's outcomes) and when it was last seen. When something seen in game contradicts a
stored fact, the old row is marked stale instead of deleted, and queries skip it. Other
players' names and speech never go in: a PK sighting is stored as "a red player was seen
here", not who it was.

**The local store** is built from the ModernUO checkout and is gitignored, so rebuild it
with `uo-brain world import-modernuo` (`--modernuo-dir` if it isn't in `~/Workspace/ModernUO`).
It reads what a Felucca server with the configured expansion loads: `Locations/felucca.json`,
`regions.json`, the spawn files in `Spawns/shared/felucca` and `Spawns/post-uoml/felucca`,
`teleporters.json` and the public moongates. Vendor spawns become places with what they
sell (a table written from ModernUO's `SB*Info` classes), and creature stats are parsed
from the C# in `Projects/UOContent/Mobiles`. Re-running it replaces the `modernuo:` rows
and keeps everything else. On 2026-10-06 it gave 1,152 places, 91 regions, 8,742 spawn
rows (one per creature type per spawner), 403 creatures and 230 teleporter routes. For
example, the Britain graveyard spawner at (1369, 1475) keeps up to 9 spectres, wraiths,
skeletons and zombies alive and respawns them in 5 to 10 minutes, and the bank nearest the
graveyard is the West Britain bank at (1425, 1690).

**Guides and the model's own knowledge.** `uo-brain world import-guide URL|FILE [--area A]`
has the planner model read a page (as plain text, up to 60,000 characters) and store one
note per fact, each linked to the page as `guide:<url>`. Importing the same page again
replaces its notes. `uo-brain world fill-gaps AREA` stores what the model knows about an
area from its training as `model:unverified`, tagged `unverified`, until the game confirms
it. The model is Claude Sonnet 5.5 through OpenRouter (`anthropic/claude-sonnet-5.5`;
change it with `--planner-model` or `PLANNER_MODEL`), using the same `OPENROUTER_API_KEY`
as Jev from `brain/.env`. `llm.py` is the small OpenRouter client behind it, which the
planner will use as well: tool calling, Anthropic prompt caching on the system prompt, and
the cost of every call. Sonnet 5.5 refuses a forced tool choice, so the client asks again
with `auto` and the prompt names the tool to call.

Single smoke run on 2026-10-06: `uo-brain world import-guide https://www.uoguide.com/Britain --area Britain`
read 4,657 characters and stored 28 notes (banks, healer, shops, inns, guild halls, bridges
and gates) for $0.033: 3,497 prompt and 2,557 completion tokens, 14 s. UOGuide gives
positions in sextant degrees, and the notes keep them as written rather than converting
them to tile coordinates.

**Recording what it sees** (`recorder.py`), on any shard, whenever the brain is connected,
whether the agent is playing or you are driving: townsfolk with titles become places
("Lucy the healer" is a healer), shop signs become places by their text ("The Healer's
Hut"), creatures that keep turning up in an area become spawns,
travel adds routes and the spots where the character got stuck, and hunts add outcomes
(kills per hour, deaths; `uo-brain world outcomes` adds the rest from the log). Jev keeps it clean: it says what an unfamiliar title means
(a choice), whether a creature is a regular of the area rather than passing through (a
yes/no), and whether a sighting shows a stored place has moved (a yes/no, which marks the
old fact stale). So attended sessions on a public shard double as mapping runs.

**Outcomes from session logs** (`outcomes.py`). `uo-brain world outcomes LOG [LOG...]` reads
session logs (a `uo-brain session` log is read together with its `.decisions.jsonl`) and
records each hunt, one loop, for its area and kit: kills a loop and an hour, deaths, minutes a
loop, gold a loop and an hour, and bandages and heal potions a kill and an hour. For each kind
of creature fought it adds what the fights cost: between two decisions at most 30 s apart,
health lost, bandages and heal potions go to the creature being fought, or else the nearest
one within 3 tiles. That is what fights with a kind cost, not what the kind did: health is net
of healing in between, and when several attack at once it all goes to the one being fought.
Each area then gets one note in words from every loop recorded there (a real one is
below); a kind is only named as costly after 3 fights. Rows are named after the log, so importing it again
replaces them (and the rows the live hunt wrote). Logs without hunts (`run`, `scenario`,
`bench`) need `--area`, and each run in them counts as a loop there. The planner sees the
note in `hunting_spots` and the figures through its `outcomes` tool.

Single run on 2026-10-07 over 18 bench logs (warrior arena rounds):
`uo-brain world outcomes logs/bench/20261006-233040/relentless-never-flees-jev_relentless-*.jsonl logs/bench/20261006-233040/mismatch-jev-0*.jsonl --area "Test field"`
wrote "Test field: 5.1 kills a loop for the warrior kit over 18 loops of about 1.4 min, 4
deaths in 18 loops, 0.8 bandages and 0.2 heal potions a kill; orcs cost the most bandages, 1
a fight over 25 fights, then ratmen at 0.8 a fight."

```bash
cd brain
uv run uo-brain world import-modernuo
uv run uo-brain world find bank --near-place "Britain graveyard"
uv run uo-brain world hunt warrior new --near "West Britain bank"
uv run uo-brain world spawns "Britain Graveyard"
uv run uo-brain world note "Wraiths here are too much for a new mage." --area "Britain Graveyard"
uv run uo-brain world import-guide https://www.uoguide.com/Britain --area Britain
uv run uo-brain world outcomes logs/session.jsonl
```

### Goals and the planner

In auto mode the agent can work towards a goal you give it in words, for example "hunt
the undead at the Britain graveyard, keep yourself supplied with bandages, bank your
gold". Type it into the panel's goal box, pick a goal template, or use `-agent goal`.

- **The planner** is a larger model (Claude Sonnet 5.5 through OpenRouter, the same key as Jev). Each time a goal ends, it sees the character's situation in words, your goal and what has happened so far, and answers with one tool call: a goal for code to carry out (`travel_to`, `hunt`, `bank`, `buy`, `sell`, `rest`, `set_strategy`, `finish`) or a world-store query first (`place`, `find_place`, `hunting_spots`, `what_spawns`, `route`, `notes`, `outcomes`, `region_at`).
- **No scripted loop:** the planner puts travel, hunting, banking and restocking together itself. Code carries out each goal ([below](#getting-around-travel-banking-and-shops)); Jev keeps the fighting.
- **Cheap to call:** each step rebuilds a short prompt (a cached system prompt, the goal, the last 12 steps, the character now) instead of growing one long conversation. A call costs about $0.014.
- **The panel** shows the step and why ("hunting at Britain Graveyard for up to 15 min: supplied with 80 bandages; time to hunt"). *pause* keeps the goal but stops work on it.
- **Handing over:** switching to combat assist (Alt+A) stops the agent walking at once and pauses the planner, so it stops costing tokens. Switching back to auto tells the planner you drove for a while, and it resumes from wherever the character is, with whatever it carries.
- **Finishing:** when the planner calls `finish`, or the character dies, the goal is paused with the reason.

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
and the agent is in auto mode.

### Getting around: travel, banking and shops

Real play is a loop of travelling, hunting, banking and restocking. Each step is one
goal that code carries out, using the world store for places:

- **Travel** walks to a named place or a tile across the map. The client's own pathfinder
  only sees about a screen, so the walk is planned coarsely over the map and static tiles
  (`AgentNav`), then walked a leg of up to 16 tiles at a time with that pathfinder, which
  handles creatures, items and the exact movement rules. Roofs don't count as ground.
  - **Doors** are items, not map tiles, so the plan treats a doorway as open; when a leg won't plan, the walker opens a closed door standing on the path.
  - **Stuck:** no progress for 8 seconds counts as stuck. The walker tries a door, then blocks that stretch and plans again (up to 5 times), in a wider box when the way round leaves the first one. Impassable items seen on the way (barricades, blockers) stay blocked for the rest of the trip.
  - **On the way** the brain fights only what comes close or attacks, and doesn't seek or loot.
  - Each walk goes into the world store as a route, with the spots where it got stuck.
- **Banking** (`bank`): says "bank" near a banker and moves items one at a time, checking each arrives (the server refuses moves that come too fast). Deposit `gold`, `loot` (anything that isn't kit or supplies) or named items (`bandage:150`); withdraw named items.
- **Buying** (`buy`): walks up to the vendor (ModernUO answers "vendor buy" only next to it), reads its list and buys what's wanted and affordable. A vendor only stocks so many (Britain's healer has 20 bandages at a time), so it goes round the vendors in sight, then the next shop.
- **Selling** (`sell`): the same, from the vendor's sell list: `loot`, or named items.
- **Hunting** (`hunt`): walks to a spawn area and lets Jev fight there until time is up, bandages or reagents run low, the bag gets heavy, or nothing shows up for 4 minutes. When it's quiet it walks to another spot of the spawn.

```bash
cd brain
uv run uo-brain do travel "Britain graveyard"
uv run uo-brain do bank --deposit gold,loot --withdraw bandage:100
uv run uo-brain do buy bandage 50
uv run uo-brain do sell loot --vendor weaponsmith
uv run uo-brain do hunt "Britain Graveyard" --minutes 10 --log logs/hunt.jsonl
```

On the local server, 2026-10-06, single runs with a warrior:

| goal | result |
|---|---|
| West Britain bank to the Britain graveyard | arrived in 57 s (226 tiles); one stop at the bank door, opened |
| and back | arrived in 63 s |
| the same with an invisible wall across the route (`[AgentWall`) | stuck twice, planned round it, arrived in 42 s |
| deposit gold, loot and 150 bandages, then withdraw 100 | done, the pack went from 200 bandages to 50 to 150 |
| buy 50 bandages, starting at the bank | bought 50 from the Britain healer for 250 gold |
| sell loot to a weaponsmith, gems to a jeweller | +54 and +426 gold; the junk stayed |

## Quick start

```bash
# 1. Client (macOS Apple Silicon; needs the .NET 10 SDK)
git submodule update --init --recursive
dotnet publish src/ClassicUO.Client/ClassicUO.Client.csproj -c Release -r osx-arm64 -o bin/osx-arm64

# 2. Game files: the official UO client, straight from EA's patch servers (no Windows needed)
python3 tools/uo-download/download_uo.py --out ~/Workspace/UOClassic
#    then set in bin/osx-arm64/settings.json (created on first launch):
#    "ultimaonlinedirectory": "/Users/<you>/Workspace/UOClassic", "clientversion": "7.0.117.1", "plugins": []

# 3. Local test server (ModernUO in ~/Workspace/ModernUO; setup in tools/modernuo/README.md)
~/Workspace/ModernUO/start-agent-server.sh

# 4. Model key, saved to brain/.env (gitignored). OpenRouter is used when present.
#    Or skip this: copy the key and click "paste key" in the agent panel.
brain/set-openrouter-key.sh          # prompts without echoing; or: pbpaste | brain/set-openrouter-key.sh

# 5. Play. Run the client from its folder: settings.json is read from the current directory.
#    Turning the agent on (Alt+A or the panel) starts the brain for you.
cd brain && uv sync && cd ..
(cd bin/osx-arm64 && ./cuo -agent_port 5577 &)

# Or drive it from a terminal, e.g. for the arena:
cd brain
uv run uo-brain login --account warrior --password warrior --create-warrior Brutus
uv run uo-brain say "[AgentGo"; uv run uo-brain say "[AgentKit"; uv run uo-brain say "[AgentArena 6"
uv run uo-brain run --mode auto --log logs/run.jsonl

# Or a mage, with a ready-made strategy
uv run uo-brain say "[AgentKit mage"; uv run uo-brain strategy template nuker
uv run uo-brain scenario --kit mage --monsters 4 --log logs/mage.jsonl
```

With no model key, `--judge heuristic` runs the same loop on fixed rules (the baseline).

**The client starts the brain.** When the agent is turned on and nothing is connected to
its port, the client runs `uv run uo-brain run` in the repository's `brain/` folder,
restarts it if it exits (waiting longer each time), and stops it when the agent is turned
off or the client closes. Its output goes to `brain/logs/client-brain.log`, its decisions
to `brain/logs/auto-<date>.jsonl`. A brain you start in a terminal takes precedence. In
settings.json, `agent_start_brain: false` turns this off (or `-agent_start_brain false`),
and `agent_brain_dir` points at a brain folder elsewhere. The *paste key* link saves the
clipboard to `brain/.env` (mode 600) and restarts the brain; the key is never shown.

### Test server commands

These come from `Projects/UOContent/Custom/AgentTestKit.cs` in ModernUO and work for normal player characters:

- **`[AgentGo [lane | x y]`:** go to the test field in Felucca, Green Acres (5445, 1153). It has no guards and no spawns. Lanes 1–9 are copies 60 tiles apart, so several test characters can run at once. With `x y`, go to that tile instead (for travel tests in town).
- **`[AgentKit`:** warrior template. Swords, Tactics, Healing and Anatomy at 80, katana, ringmail, 200 bandages, 5 heal and 5 cure potions.
- **`[AgentKit mage`:** mage template. Magery 90; Evaluating Intelligence, Meditation and Wrestling 80; Resisting Spells 60. A full spellbook, a bag of 100 of each reagent, leather armour, and 5 heal and 5 cure potions.
- **`[AgentArena [count] [kind]`:** spawns monsters in a ring 6–10 tiles out (orc, ratman, headless one and mongbat by default).
- **`[AgentReset`:** resurrects and heals you, cancels pending spawns, and removes the arena and the corpses around you.
- **`[AgentSpawn <kind> [count] [distance] [direction] [delay]`:** spawns creatures of a kind (`orc`, `ratman`, or any ModernUO type such as `OgreLord` or `OrcishMage`) at a distance and compass direction, optionally after a delay. They belong to your arena.
- **`[AgentSupplies [bandages N] [heal N] [cure N] [reagents N] [gold N] [loot N]`:** sets your supplies; `gold` and `loot` add coins and sets of the loot items below to your pack.
- **`[AgentLoot [distance] [direction]`:** lays an orc's corpse holding three valuables (diamonds, a gold ring, a magic longsword) and five pieces of junk (bones, a head, a shirt, kindling, raw ribs).
- **`[AgentWall x1 y1 x2 y2 | clear`:** an invisible wall along a line, for stuck tests.
- **`[AgentRestock [amount]`:** stocks the vendors within 12 tiles with at least that many of everything.

Accounts are created on first login. `admin`/`admin` is the owner.

## Reference

**In game**
- **`-agent off|combat|auto`:** set the play state (`assist` also means combat assist).
- **`-agent switch`:** switch between combat assist and auto (Alt+A).
- **`-agent next`:** do Jev's next move (Alt+D).
- **`-agent engage follow|defend|nearby`:** what combat assist takes on by itself.
- **`-agent status`:** show the play state, authorities and thresholds.
- **`-agent accept`:** accept the pending suggestion.
- **`-agent set <behaviour> <off|suggest|auto>`:** override one behaviour.
- **`-agent bandage <pct>`, `-agent potion <pct>`:** reflex thresholds.
- **`-agent strategy [set|add|clear] <text>`:** edit the strategy.
- **`-agent template [list|<name>|set <name>|remove <name>]`:** pull in, replace with or take out a strategy template.
- **`-agent goal [<text>|clear|pause|resume|templates|template <name>]`:** set, show or change the auto-mode goal.
- **Macros** (bindable in Options → Macros): *AgentOff*, *AgentAssist* (combat assist), *AgentAuto*, *AgentAccept*, *AgentSwitch* and *AgentNext*.

**uo-brain**
- **`run`:** play: fights, and in auto mode works towards the panel's goal. Options: `--mode`, `--judge jev|heuristic`, `--provider auto|openrouter|typesafe`, `--model`, `--archetype auto|warrior|mage`, `--strategy FILE`, `--template NAME` (repeatable), `--duration`, `--log`, `--min-confidence`.
- **`scenario`:** arena rounds with metrics; `--kit warrior|mage`.
- **`do travel|bank|buy|sell|hunt|rest …`:** one session goal (above); uses the world store.
- **`session "GOAL" [--hours]`:** the planner towards a goal, from the command line.
- **`bench [list|report FILES]`:** the judgment benchmark (below). Options: `--scenarios core|adherence|all|NAME,…`, `--judges heuristic,jev,jev+<template>`, `--rounds`, `--lane`, `--out`.
- **`strategy templates`, `strategy template NAME [--replace]`, `strategy drop NAME`:** list, pull in or take out templates.
- **`login`, `status`, `snapshot [--semantic]`, `act <verb> k=v`, `accept`, `mode`, `strategy …`, `cmd "-agent …"`, `say`, `shot FILE`, `report LOG`, `replay LOG`.**
- **`world [--shard local] [--map Felucca] …`** (the world store; doesn't connect to the game): `note TEXT [--area A] [--tag T]`, `notes [KEYWORDS] [--area A]`, `place NAME`, `find KIND [--near X,Y | --near-place NAME]`, `spawns [AREA] [--near …] [--radius N]`, `hunt ARCHETYPE LEVEL [--near …]`, `route FROM TO`, `stats`, `import-modernuo [--modernuo-dir DIR] [--maps Felucca]`, `import-guide URL|FILE [--area A] [--planner-model M]`, `fill-gaps AREA [--planner-model M]`, `outcomes LOG… [--area A]` (record what each hunt in the logs gave, per area and kit; importing a log again replaces its rows).

**Client RPC:** newline-delimited JSON on 127.0.0.1, enabled by `-agent_port` or `agent_port` in settings.json.
- **Methods:** `ping`, `status`, `login`, `snapshot {since, radius, pack}` (`pack: true` adds everything in the backpack; the snapshot also has recent deaths, `travel` and `errand` progress, each creature's full `label` with its title, and whether each corpse is a monster's), `act {verb, …}`, `nav {radius, goal_x, goal_y, reach}` and `items {radius}` (travel debugging: the planner's map with a planned path, and the items lying around), `mode`, `strategy {text|add|clear|template, replace|remove_template}`, `templates`, `accept`, `note`, `decision {…}` (what the brain decided, for the panel; `next` is the move for the next-move key), `brain_info {judge, archetype, strategy_reading}`, `goal {text|clear|pause|template}`, `goal_status {step, why}` (from the planner, for the panel), `templates {kind: "goal"}`, `command`, `capture {path}`.
- **Act verbs:** `attack {target, range}`, `war_mode`, `stop`, `bandage_self`, `bandage`, `drink {kind}`, `cast {spell, target, queue}`, `skill {name}`, `loot`, `take`, `flee`, `walk_to`, `move`, `say`, `use`, `target`, `wait`, `hint {text}` (text above your head, client-side only, never refused), `travel {x, y, distance}`, `bank {deposit, withdraw}`, `buy {target, items}` and `sell {target, items}` (`items` like `"bandage:50"` or `"loot"`).
- **Cast authority:** healing spells count as `heal`, Cure as `cure`, attack spells as `fight`, anything else as `misc`.
- **Authority:** an act request is subject to authority unless it has `"source": "manual"`. In combat assist, an attack or harmful cast at a creature the engage setting doesn't allow is refused ("not your target").

## Results so far

Local arena, 6 mixed low-tier monsters per 90-second round:

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
- **Price:** about $0.25 an hour at Jev's $0.042 per million list price. Check your OpenRouter bill for the actual rate.

After the mage, panel and template work, a regression run of the same warrior arena
with Jev scored 17 / 18 kills and 0 deaths.

**Mage** (`[AgentKit mage`), Jev with the `nuker` template:

| run | kills | deaths | notes |
|---|---|---|---|
| 4 monsters, no template, 60 s | 4 / 4 | 0 | Jev chose Explosion as the opener on its own |
| 5 monsters, `nuker`, before Protection | 1 / 10 | 1 | swarmed: every hit interrupted a spell, 34 of 43 casts were self-heals, and a throttled bag peek left round 2 without reagents |
| 4 monsters, `nuker`, 3 × 75 s | 12 / 12 | 0 | Explosion first, then Energy Bolt; Protection when monsters closed in |

Spell plans need to be settings as well as context: with only the text "open with Explosion,
then Lightning", Jev opened with Lightning in 3 of 4 offline tries. As a compiled
`opening_spell`, the opener is used every time.

**Hand-back with real input:** the client was driven with OS-level mouse and keyboard
events posted through the macOS HID event tap, the same path as a physical device.

| check | result |
|---|---|
| Right-mouse movement stops a walk the agent started | pass |
| Brain actions are deferred while the player is in control | pass |
| The agent takes over again 4 s after the last input | pass |
| Arrow keys and a click in the world hand control back | pass |
| A war-mode double-click on another monster: the agent follows the player's choice | pass, after a fix (below) |
| Clicking the panel (mode, templates, typing a strategy line) doesn't pause the agent | pass |
| Right-clicking a gump closed it without pausing the agent | pass |

The real-input test found two gaps, both fixed:
- **War mode:** the agent fought without war mode on, so a player's double-click on another monster opened its paperdoll instead of attacking. It now turns war mode on when it engages.
- **Peek throttling:** the server throttles use requests, so a peek right after re-equipping could fail silently. It now retries after 2 s.

**Combat assist and the keys, with real input** (2026-10-06, single runs, the same tool):

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

That test found three gaps, all fixed:
- **Alt+letter macros on a Mac:** SDL3 reports the key with modifiers applied, and Option composes a character ("a" becomes "å"), so no Alt+letter macro could match, ClassicUO's default Alt+P paperdoll included. Macros are now matched by the key itself, and a key that ran a macro no longer types its character into the chat line.
- **Option+N is a dead key** on a Mac (it starts an accented letter), so the next-move key is Alt+D, not Alt+N.
- **The panel covered the quit dialog:** it was drawn on top of everything, including modal dialogs, so their buttons couldn't be clicked. It now sits with the other gumps.

These are single runs, so treat them as a smoke test rather than a benchmark.

To reproduce, run `uo-brain scenario --judge jev --log logs/jev.jsonl`. To compare
judges on the same logged states, run `uo-brain replay <log> --judge jev`.

### Judgment benchmark

The arena can't tell Jev from the rules: both win every round. `uo-brain bench` plays
scenarios with a known right behaviour, built so that the obvious rule gets them wrong, many
times per judge, and reports success rates with 95% Wilson intervals. Each scenario's setup is
test-kit commands; what counts as right is checked from the snapshots and the decision log.
Results are written to `brain/bench/<time>.json` after every round; compare runs with
`uo-brain bench report A.json B.json`.

| scenario | setup | right |
|---|---|---|
| `mismatch` | four weak monsters, then an ogre lord walks up | survive without engaging the ogre lord |
| `priority` | three orcs close, an orcish mage casting from 9 tiles | kill the mage first |
| `loot` | a corpse with valuables and junk, an orc arriving | take the valuables, skip the junk, stop looting when the orc arrives |
| `attrition` | eight monsters on six bandages and one heal potion | get out alive |
| `swarm` | six melee monsters on a mage | survive and kill at least four |

The adherence scenarios fix a strategy template and check it is followed: `relentless`
never flees, `survivor` flees (at what health is recorded, to compare with relentless),
`no-loot` never loots, `nuker` opens on every creature with Explosion, and `champion`
attacks the troll before the mongbats and the orc.

```bash
uv run uo-brain bench list
uv run uo-brain bench --scenarios core --judges heuristic,jev,jev+survivor --rounds 10 --lane 1
uv run uo-brain bench --scenarios adherence --rounds 10
```

## Playing on public shards

Assist mode is the same kind of tool as Razor or UOSteam, which many shards allow.
Unattended play (auto mode) is against the rules on most shards, and some object to
modified clients. Check each shard's rules, and keep auto mode to the local server or
shards that allow it.

## Where things are

| | |
|---|---|
| `src/ClassicUO.Client/Agent/` | `AgentHost` (RPC, login, screenshots)<br>`AgentController` (modes, reflexes, actions, casting, human pause, travel, strategy, templates)<br>`AgentNav` (long-walk planning over the map files)<br>`AgentErrands` (bank, buy, sell)<br>`ReflexPolicy` (pure)<br>`AgentSpells` (magery costs, reagents, spellbook)<br>`AgentSnapshot`<br>`AgentJournal`<br>`AgentLogin`<br>`AgentGump` (the panel)<br>`AgentDecision`<br>`AgentTemplates` + `Templates/*.md` |
| `brain/src/uo_brain/` | `state.py` (snapshot to words)<br>`questions.py` (the Jev request)<br>`policy.py` (decisions to actions)<br>`spells.py` (attack spells)<br>`strategy.py` (your strategy to settings)<br>`judge.py` (Jev or rules)<br>`loop.py`<br>`cli.py`<br>`bench.py` (judgment benchmark)<br>`session.py` (travel, bank, buy, sell, hunt)<br>`planner.py` (the slow planner)<br>`world.py` (world store and the planner's query tools)<br>`world_import.py` (fills the local store from ModernUO)<br>`guides.py` (guide pages and model knowledge to notes)<br>`outcomes.py` (session logs to outcomes per area and kit)<br>`logs.py` (reads the brain's logs back)<br>`llm.py` (OpenRouter chat client for the planner model) |
| `brain/worlds/<shard>/` | the world store, `world.sqlite` (gitignored) |
| `tools/uo-download/` | official client downloader (EA patch protocol, UOP rebuild) |
| `tools/modernuo/` | test server commands (`AgentTestKit.cs`), start script, setup and config notes |
| `tests/ClassicUO.UnitTests/Agent/`, `brain/tests/` | `dotnet test --filter "FullyQualifiedName~Agent"`, `cd brain && uv run pytest` |

Known limits:
- Results above are single small runs.
- Mages use Magery only, with single-target attack spells. A mage kites by stepping back from melee, but most monsters run as fast as a character, so a swarm of five or more is still hard for it.
- Bandage timing and spell failures are read by cliloc number where the server sends one (ModernUO does), and from the English text otherwise. They have not yet been checked against what UO Renaissance sends.
- To read a spellbook or a bag, the agent opens it once, so its gump flashes briefly.

---

# ClassicUO

<p align="center">
    <img src="https://i.imgur.com/CgpwyIQ.png" width="190" height="200" >
</p>

An open source implementation of the Ultima Online Classic Client.

Individuals/hobbyists: support continued maintenance and development via the monthly Patreon:
<br>&nbsp;&nbsp;[![Patreon](https://raw.githubusercontent.com/wiki/ocornut/imgui/web/patreon_02.png)](http://www.patreon.com/classicuo)

Individuals/hobbyists: support continued maintenance and development via PayPal:
<br>&nbsp;&nbsp;[![PayPal](https://www.paypalobjects.com/en_US/i/btn/btn_donate_LG.gif)](https://www.paypal.com/cgi-bin/webscr?cmd=_s-xclick&hosted_button_id=9ZWJBY6MS99D8)

<a href="https://discord.gg/VdyCpjQ">
<img src="https://img.shields.io/discord/458277173208547350.svg?logo=discord"
alt="chat on Discord"></a>

[![GitHub Actions Status](https://github.com/ClassicUO/ClassicUO/workflows/Build-Test/badge.svg)](https://github.com/ClassicUO/ClassicUO/actions)
[![GitHub Actions Status](https://github.com/ClassicUO/ClassicUO/workflows/Deploy/badge.svg)](https://github.com/ClassicUO/ClassicUO/actions)

# Introduction
ClassicUO is an open source implementation of the Ultima Online Classic Client. This client is intended to emulate all standard client versions and is primarily tested against Ultima Online free shards.

The client is currently under heavy development but is functional. The code is based on the [FNA-XNA](https://fna-xna.github.io/) framework. C# is chosen because there is a large community of developers working on Ultima Online server emulators in C#, because FNA-XNA exists and seems reasonably suitable for creating this type of game.

![screenshot_2020-07-06_12-29-02](https://user-images.githubusercontent.com/20810422/208747312-04f6782f-3dc8-4951-b0a0-73d2305bbfca.png)


ClassicUO is natively cross platform and supports:
* Browser [Chrome]
* Windows [DirectX 11, OpenGL, Vulkan]
* Linux   [OpenGL, Vulkan]
* macOS   [Metal, OpenGL, MoltenVK]

# Download & Play!
| Platform | Link |
| --- | --- |
| Browser | [Play!](https://play.classicuo.org) |
| Windows x64 | [Download](https://www.classicuo.eu/launcher/win-x64/ClassicUOLauncher-win-x64-release.zip) |
| Linux x64 | [Download](https://www.classicuo.eu/launcher/linux-x64/ClassicUOLauncher-linux-x64-release.zip) |
| macOS x64 | [Download](https://www.classicuo.eu/launcher/osx/ClassicUOLauncher-osx-x64-release.zip) |

Or visit the [ClassicUO Website](https://www.classicuo.eu/)

# How to generate a release build
```
git clone --recursive https://github.com/ClassicUO/ClassicUO.git
cd ClassicUO/scripts
bash build-naot.sh
```
Binaries available in `bin/dist` folder
> [!WARNING] 
> To execute .sh scripts on Windows, use Git Bash which can be installed with Git itself: https://git-scm.com/download/win

# Contribute
Everyone is welcome to contribute! The GitHub issues and project tracker are kept up to date with tasks that need work.

# Legal
The code itself has been written using the following projects as a reference:

* [OrionUO](https://github.com/hotride/orionuo)
* [Razor](https://github.com/msturgill/razor)
* [UltimaXNA](https://github.com/ZaneDubya/UltimaXNA)
* [ServUO](https://github.com/servuo/servuo)

Backend:
* [FNA](https://github.com/FNA-XNA/FNA)

This work is released under the BSD 4 license. This project does not distribute any copyrighted game assets. In order to run this client you'll need to legally obtain a copy of the Ultima Online Classic Client.
Using a custom client to connect to official UO servers is strictly forbidden. We do not assume any responsibility of the usage of this client.

Ultima Online(R) © 2024 Electronic Arts Inc. All Rights Reserved.

# Code Signing Policy
Free code signing provided by [SignPath.io](https://signpath.io/), certificate by [SignPath Foundation](https://signpath.org/).

This program will not transfer any information to other networked systems unless specifically requested by the user or the person installing or operating it.

People with direct push access:
* [andreakarasho](https://github.com/andreakarasho)
