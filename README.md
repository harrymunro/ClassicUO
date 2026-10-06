# ClassicUO with a Jev agent

This fork adds an AI agent to ClassicUO that can play alongside you or play on its
own. Decisions come from [Jev](https://docs.typesafe.ai), TypeSafe's "System One"
model, which answers typed questions (pick one of these options, yes or no, rate
this) with probabilities in about 100 ms. Ordinary code does everything that has a
right answer: healing thresholds, pathfinding, looting mechanics, safety rules.

Status: works end to end against a local ModernUO server with a warrior character, with Jev
deciding through OpenRouter.
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
   overlay, -agent command, macros            └─ act, log
```

1. **The client snapshot** gives the player, the creatures and corpses within 18
   tiles, new journal lines and the agent's own state.
2. **The brain describes the situation in words.** Jev reads "badly wounded,
   adjacent, to the east" far more reliably than raw numbers and coordinates.
   Candidates get short ids (`t1`, `c1`, `i1`) so the model can only pick things
   code has already checked. Other players' speech is never sent to the model.
3. **One Jev request asks every question that could matter**:
   - `intent`: fight, flee, loot, seek or rest
   - `in_danger`: will the warrior die soon if it keeps fighting?
   - `target`: which creature to attack
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

`uo-brain strategy explain` shows how the current text was read. Spell preferences
("open with an explosion") need a caster with spell actions, which the warrior
doesn't have yet.

### Reflexes

The client runs these without the brain:

- **Bandages:** bandage yourself below 85% health, or when poisoned.
- **Heal potion:** drink one below 40%, with the 10-second cooldown respected.
- **Cure potion:** drink one when poisoned and hurt.

Adjust them with `-agent bandage 80` and `-agent potion 35`. They're also
available on their own: assist mode with no brain running is an auto-healer.

### Safety rules

These are in code, whatever the model says:

- The brain only attacks monsters: grey or red, not human, not a pet.
- Other players' speech goes to neither the model nor the reflexes.
- Actions are a fixed list.
- Gold, bandages and potions are always taken; anything else needs a "worth taking" judgment.

### Seeing what it does

- **Overlay:** an in-game panel shows the mode, what the agent is doing, the brain's last decision with its confidence, any pending suggestion, and kills, deaths and heals.
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
```

With no model key, `--judge heuristic` runs the same loop on fixed rules (the baseline).

### Test server commands

These come from `Projects/UOContent/Custom/AgentTestKit.cs` in ModernUO and work for normal player characters:

- **`[AgentGo`:** go to the test field in Felucca, Green Acres (5445, 1153). It has no guards and no spawns.
- **`[AgentKit`:** warrior template. Swords, Tactics, Healing and Anatomy at 80, katana, ringmail, 200 bandages, 5 heal and 5 cure potions.
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
- **Macros** (bindable in Options → Macros): *AgentOff*, *AgentAssist*, *AgentAuto* and *AgentAccept*.

**uo-brain**
- **`run`:** play. Options: `--mode`, `--judge jev|heuristic`, `--provider auto|openrouter|typesafe`, `--model`, `--strategy FILE`, `--duration`, `--log`, `--min-confidence`.
- **`scenario`:** arena rounds with metrics.
- **`login`, `status`, `snapshot [--semantic]`, `act <verb> k=v`, `accept`, `mode`, `strategy …`, `cmd "-agent …"`, `say`, `shot FILE`, `report LOG`, `replay LOG`.**

**Client RPC:** newline-delimited JSON on 127.0.0.1, enabled by `-agent_port` or `agent_port` in settings.json.
- **Methods:** `ping`, `status`, `login`, `snapshot {since, radius}`, `act {verb, …}`, `mode`, `strategy`, `accept`, `note`, `command`, `capture {path}`.
- **Act verbs:** `attack`, `war_mode`, `stop`, `bandage_self`, `bandage`, `drink {kind}`, `loot`, `take`, `flee`, `walk_to`, `move`, `say`, `use`, `target`, `wait`.
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
| `src/ClassicUO.Client/Agent/` | `AgentHost` (RPC, login, screenshots)<br>`AgentController` (modes, reflexes, actions, human pause, strategy)<br>`ReflexPolicy` (pure)<br>`AgentSnapshot`<br>`AgentJournal`<br>`AgentLogin`<br>`AgentStatusGump` |
| `brain/src/uo_brain/` | `state.py` (snapshot to words)<br>`questions.py` (the Jev request)<br>`policy.py` (decisions to actions)<br>`strategy.py` (your strategy to settings)<br>`judge.py` (Jev or rules)<br>`loop.py`<br>`cli.py` |
| `tools/uo-download/` | official client downloader (EA patch protocol, UOP rebuild) |
| `tools/modernuo/` | test server commands (`AgentTestKit.cs`), start script, setup and config notes |
| `tests/ClassicUO.UnitTests/Agent/`, `brain/tests/` | `dotnet test --filter "FullyQualifiedName~Agent"`, `cd brain && uv run pytest` |

Known limits:
- Results above are single small runs.
- Warrior only.
- Bandage timing is read from English server messages.
- The hand-back to the player is wired to real mouse and keyboard input but has only been exercised by code paths, not by a person at the keyboard yet.

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
