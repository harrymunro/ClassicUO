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
   adjacent, to the east" far more reliably than raw numbers and coordinates.
   Candidates get short ids (`t1`, `c1`, `i1`) so the model can only pick things
   code has already checked. Other players' speech is never sent to the model.
3. **One Jev request asks every question that could matter**:
   - `intent`: fight, flee, loot, seek or rest
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

- **Mode buttons:** off, assist and auto.
- **What the agent is doing:** for example "fighting Vorgak", "casting Explosion" or "bandaging", and "you have the controls" while you're playing.
- **Jev's judgment** for the latest decision:
  - a bar for each intent with Jev's probability, plus options the facts ruled out;
  - the danger judgment;
  - the target and spell it picked, with confidence, or "your strategy" when your strategy chose the spell;
  - what was done, and the latency.
- **A pending suggestion** with an *accept* link (assist mode).
- **Your strategy and how Jev read it**, an entry box to add a line, and the template links (see below).
- **Earlier decisions**, with repeats collapsed ("fight an orc x5").
- **Kills, deaths, heals and casts.**

When Jev picks a new target, "jev: target" appears above that creature in the world. *less*
collapses the panel to a few lines; its position and whether it is collapsed are saved per character.

### Modes and authority

Each behaviour has its own authority: `off`, `suggest` or `auto`. Modes are presets:

| mode | heal / cure / potion | fight / loot / move / misc |
|---|---|---|
| `off` | off | off |
| `assist` | auto | suggest |
| `auto` | auto | auto |

- **Overrides:** change any single behaviour, e.g. `-agent set fight auto` while staying in assist.
- **Behaviours:** `heal` (bandages), `cure` (cure potions), `potion` (heal potions), `fight`, `loot`, `move`, `misc`.
- **Saving:** settings are saved per character.

**Assist** is for playing yourself. The agent heals you, and suggests fights and
loot as text above your head and in the overlay. Accept a suggestion with
`-agent accept` or the *AgentAccept* macro.

**Auto** is fully autonomous play.

**Handing over control:** moving, clicking in the world or using the arrow keys
hands control back to you for 4 seconds, and stops any walk the agent started.
Healing reflexes keep running while you're in control.
- **Your target sticks:** if you attack a different monster yourself, the agent keeps fighting that one when it takes over again.
- **Your spell cursors are yours:** if you touch the controls while one of the agent's attack spells is being cast, its target cursor is left to you.
- **Panel clicks don't count:** clicking the panel doesn't pause the agent.

This was verified with real mouse and keyboard events; see [Results](#results-so-far).

### Mages

The agent plays a mage when the character has a spellbook and its Magery is at least as
high as any weapon skill, or with `uo-brain run --archetype mage`.

- **Range:** it engages from up to 7 tiles away and casts Jev's pick from the attack spells it can cast right now: Flamestrike, Energy Bolt, Explosion, Lightning, Mind Blast, Fireball, Harm, Magic Arrow, Poison and Paralyze.
- **Your spell plan comes first:** the opener on a fresh creature, then the main spell. Jev picks when your strategy names none; code picks the strongest castable spell when Jev isn't sure.
- **Queued casts:** the brain queues the next spell and the client casts it the moment the current spell and its recovery allow. Healing reflexes go first.
- **Protection:** with a monster in melee reach it casts Protection first, since every hit otherwise interrupts a spell.
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
available on their own: assist mode with no brain running is an auto-healer.

### Safety rules

These are in code, whatever the model says:

- The brain only attacks monsters: grey or red, not human, not a pet.
- Other players' speech goes to neither the model nor the reflexes.
- Actions are a fixed list.
- Gold, bandages and potions are always taken; anything else needs a "worth taking" judgment.

### Seeing what it does

- **Agent panel:** Jev's probabilities, choices and actions for every decision, in game (see above).
- **Decision log:** every decision goes to a JSONL file with the state, all answers and probabilities, the actions and their results.
- **`uo-brain report`** summarises a log.
- **`uo-brain replay`** re-asks a log's questions to another judge offline, e.g. Jev against the rule baseline, and reports how often they agree.
- **Assist agreement:** in assist mode the brain records whether you attacked the creature it suggested.

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
brain/set-openrouter-key.sh          # prompts without echoing; or: pbpaste | brain/set-openrouter-key.sh

# 5. Play
./bin/osx-arm64/cuo -agent_port 5577 &
cd brain && uv sync
uv run uo-brain login --account warrior --password warrior --create-warrior Brutus
uv run uo-brain say "[AgentGo"; uv run uo-brain say "[AgentKit"; uv run uo-brain say "[AgentArena 6"
uv run uo-brain run --mode auto --log logs/run.jsonl

# Or a mage, with a ready-made strategy
uv run uo-brain say "[AgentKit mage"; uv run uo-brain strategy template nuker
uv run uo-brain scenario --kit mage --monsters 4 --log logs/mage.jsonl
```

With no model key, `--judge heuristic` runs the same loop on fixed rules (the baseline).

### Test server commands

These come from `Projects/UOContent/Custom/AgentTestKit.cs` in ModernUO and work for normal player characters:

- **`[AgentGo`:** go to the test field in Felucca, Green Acres (5445, 1153). It has no guards and no spawns.
- **`[AgentKit`:** warrior template. Swords, Tactics, Healing and Anatomy at 80, katana, ringmail, 200 bandages, 5 heal and 5 cure potions.
- **`[AgentKit mage`:** mage template. Magery 90; Evaluating Intelligence, Meditation and Wrestling 80; Resisting Spells 60. A full spellbook, a bag of 100 of each reagent, leather armour, and 5 heal and 5 cure potions.
- **`[AgentArena [count] [kind]`:** spawns monsters in a ring 6–10 tiles out (orc, ratman, headless one and mongbat by default).
- **`[AgentReset`:** resurrects and heals you, and removes the arena.

Accounts are created on first login. `admin`/`admin` is the owner.

## Reference

**In game**
- **`-agent off|assist|auto`:** set the mode.
- **`-agent status`:** show the mode, authorities and thresholds.
- **`-agent accept`:** accept the pending suggestion.
- **`-agent set <behaviour> <off|suggest|auto>`:** override one behaviour.
- **`-agent bandage <pct>`, `-agent potion <pct>`:** reflex thresholds.
- **`-agent strategy [set|add|clear] <text>`:** edit the strategy.
- **`-agent template [list|<name>|set <name>|remove <name>]`:** pull in, replace with or take out a strategy template.
- **Macros** (bindable in Options → Macros): *AgentOff*, *AgentAssist*, *AgentAuto* and *AgentAccept*.

**uo-brain**
- **`run`:** play. Options: `--mode`, `--judge jev|heuristic`, `--provider auto|openrouter|typesafe`, `--model`, `--archetype auto|warrior|mage`, `--strategy FILE`, `--template NAME` (repeatable), `--duration`, `--log`, `--min-confidence`.
- **`scenario`:** arena rounds with metrics; `--kit warrior|mage`.
- **`strategy templates`, `strategy template NAME [--replace]`, `strategy drop NAME`:** list, pull in or take out templates.
- **`login`, `status`, `snapshot [--semantic]`, `act <verb> k=v`, `accept`, `mode`, `strategy …`, `cmd "-agent …"`, `say`, `shot FILE`, `report LOG`, `replay LOG`.**

**Client RPC:** newline-delimited JSON on 127.0.0.1, enabled by `-agent_port` or `agent_port` in settings.json.
- **Methods:** `ping`, `status`, `login`, `snapshot {since, radius}`, `act {verb, …}`, `mode`, `strategy {text|add|clear|template, replace|remove_template}`, `templates`, `accept`, `note`, `decision {…}` (what the brain decided, for the panel), `brain_info {judge, archetype, strategy_reading}`, `command`, `capture {path}`.
- **Act verbs:** `attack {target, range}`, `war_mode`, `stop`, `bandage_self`, `bandage`, `drink {kind}`, `cast {spell, target, queue}`, `skill {name}`, `loot`, `take`, `flee`, `walk_to`, `move`, `say`, `use`, `target`, `wait`.
- **Cast authority:** healing spells count as `heal`, Cure as `cure`, attack spells as `fight`, anything else as `misc`.
- **Authority:** an act request is subject to authority unless it has `"source": "manual"`.

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

These are single runs, so treat them as a smoke test rather than a benchmark.

To reproduce, run `uo-brain scenario --judge jev --log logs/jev.jsonl`. To compare
judges on the same logged states, run `uo-brain replay <log> --judge jev`.

## Playing on public shards

Assist mode is the same kind of tool as Razor or UOSteam, which many shards allow.
Unattended play (auto mode) is against the rules on most shards, and some object to
modified clients. Check each shard's rules, and keep auto mode to the local server or
shards that allow it.

## Where things are

| | |
|---|---|
| `src/ClassicUO.Client/Agent/` | `AgentHost` (RPC, login, screenshots)<br>`AgentController` (modes, reflexes, actions, casting, human pause, strategy, templates)<br>`ReflexPolicy` (pure)<br>`AgentSpells` (magery costs, reagents, spellbook)<br>`AgentSnapshot`<br>`AgentJournal`<br>`AgentLogin`<br>`AgentGump` (the panel)<br>`AgentDecision`<br>`AgentTemplates` + `Templates/*.md` |
| `brain/src/uo_brain/` | `state.py` (snapshot to words)<br>`questions.py` (the Jev request)<br>`policy.py` (decisions to actions)<br>`spells.py` (attack spells)<br>`strategy.py` (your strategy to settings)<br>`judge.py` (Jev or rules)<br>`loop.py`<br>`cli.py` |
| `tools/uo-download/` | official client downloader (EA patch protocol, UOP rebuild) |
| `tools/modernuo/` | test server commands (`AgentTestKit.cs`), start script, setup and config notes |
| `tests/ClassicUO.UnitTests/Agent/`, `brain/tests/` | `dotnet test --filter "FullyQualifiedName~Agent"`, `cd brain && uv run pytest` |

Known limits:
- Results above are single small runs.
- Mages use Magery only, with single-target attack spells. A mage doesn't kite: it stands and casts, so a swarm of five or more melee monsters is hard for it.
- Bandage timing and spell failures are read from English server messages.
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
