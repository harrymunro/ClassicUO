# Reference

<sub>[← README](../README.md) · [all documentation](../README.md#documentation)</sub>

## In game

- **`-agent off|combat|auto`:** set the play state (`assist` also means combat assist).
- **`-agent switch`:** switch between combat assist and auto (Alt+A).
- **`-agent next`:** do Jev's next move (Alt+D).
- **`-agent calls`:** open or close the live view of Jev's calls.
- **`-agent engage follow|defend|nearby`:** what combat assist takes on by itself.
- **`-agent pace walk|run`:** the pace of trips that don't ask for one (saved as `agent_travel_pace`; [rules of play](fleets.md#rules-of-play)).
- **`-agent status`:** show the play state, authorities and thresholds.
- **`-agent accept`:** accept the pending suggestion.
- **`-agent set <behaviour> <off|suggest|auto>`:** override one behaviour.
- **`-agent bandage <pct>`, `-agent potion <pct>`:** reflex thresholds.
- **`-agent strategy [set|add|clear] <text>`:** edit the strategy.
- **`-agent template [list|<name>|set <name>|remove <name>]`:** pull in, replace with or take out a strategy template.
- **`-agent goal [<text>|clear|pause|resume|templates|template <name>]`:** set, show or change the auto-mode goal.
- **`-agent longgoal [list|add <text>|done <id>|drop <id>]`:** the character's [long goals](world-and-memory.md#long-goals); the brain applies the change at its next look (every few seconds while it runs).
- **Macros** (bindable in Options → Macros): *AgentOff*, *AgentAssist* (combat assist), *AgentAuto*, *AgentAccept*, *AgentSwitch* and *AgentNext*.

## uo-brain

Run from `brain/` as `uv run uo-brain …`; `--port` (before the command) picks the client's agent port, 5577 by default.

- **`run`:** play: fights, and in auto mode works towards the panel's goal. Options: `--mode`, `--judge jev|heuristic`, `--provider auto|openrouter|typesafe`, `--model`, `--archetype auto|warrior|mage|archer|tamer|bard|necromancer|paladin`, `--strategy FILE`, `--template NAME` (repeatable), `--duration`, `--log`, `--min-confidence`, `--shard`, `--planner-model`, `--facts jev|all|none` (which world facts reach the fights), `--machine NAME|FILE` (follow a [plan](goals.md#plans-for-fights) in fights), `--no-machines` (the planner can't set plans), `--no-escalate` (no Sonnet for the planner's hard moments), `--no-memory` (plan without the character's memory and long goals, for comparing), `--fresh` (don't carry on from the planner's saved state), `--persona FILE` (an NPC's persona, JSON), `--hear NAMES` (the fleet's other characters, whose speech an NPC hears and answers), `--budget-per-hour USD` and `--over-budget slow|cheaper|stop` ([a budget](seeing-what-it-does.md#what-it-costs)), `--spots off|jev|rule` ([where runs go](playing.md#where-it-runs)), `--leave-stop rule|jev` ([when a leave stops](playing.md#leaving-and-packs)), `--profile NAME|FILE` and `--use KIND=MODEL` ([the model for each kind of call](seeing-what-it-does.md#which-model-answers)). `session` and `do hunt` take the same options.
- **`machine list|show NAME|design "SITUATION" [--archetype A] [--out FILE] [--planner-model M] [--cost-log FILE]`:** plans for fights ([more](goals.md#plans-for-fights)); doesn't connect to the game.
- **`scenario`:** arena rounds with metrics; `--kit warrior|mage`, `--rounds`, `--round-seconds`, `--monsters`, `--kind`, and the `run` options.
- **`do travel|bank|buy|sell|hunt|rest|resurrect|recover-corpse …`:** one session goal ([travel, banking, shops and hunting](goals.md#travel-banking-shops-and-hunting), [after a death](goals.md#after-a-death)); uses the world store. `do [--shard NAME] travel PLACE|X,Y [--distance N] [--timeout S] [--defend]`: `--defend` runs Jev's defend-only fight loop beside the walk, as the planner's trips do (its decisions go to `LOG.decisions.jsonl` with `--log`).
- **`session ["GOAL"] [--hours] [--party ID --party-members A,B,… --leader NAME [--party-plan leader|huddle] [--no-callouts] [--no-party-help] [--roleplay] [--party-walk]] [--hear NAMES [--no-small-talk] [--no-pace]] [--travel walk|run] [--rules NAME]`:** the planner towards a goal, from the command line; with no goal, it chooses from the character's [long goals](world-and-memory.md#long-goals). With `--party`, one of a party ([one plan](fleets.md#several-agents-on-one-server)): the leader plans, any other member follows its goals. A party member's own options ([a party in a fight, on the road and at the inn](fleets.md#a-party-in-a-fight-on-the-road-and-at-the-inn)): `--party-plan huddle` gives every member its own planner and a plan agreed in a huddle; `--no-callouts` and `--no-party-help` turn off calling out and helping companions; `--roleplay` turns on gestures and emotes; `--party-walk` walks the party's trips together. With `--hear`, `--no-small-talk` keeps talk to what needs saying and `--no-pace` says each line the moment it is written. `--travel walk|run` sets the pace of every trip and `--rules NAME` takes a set of rules from `brain/rules/` for every flag not given ([rules of play](fleets.md#rules-of-play)). A fleet file sets all of these (`uo-brain fleet`). A party leader's planner has two goals a lone character's hasn't: `restock` (everyone buys what it is short of) and, in a party that talks, `inn` (the party walks to the nearest inn the world store knows, goes in, talks the hunt over round a table, and each keeps its own memories of it).
- **`bench [list|report FILES]`:** the judgment benchmark ([Results](results.md#judgment-benchmark)). Options: `--scenarios core|adherence|archetypes|world|packs|sight|spots|all|NAME,…`, `--judges heuristic,jev,profile,jev+<template>` (`jev` is Jev in every system-one call, `profile` the active profile's models; each optionally `/nokite`, for no stepping back, `/nopack`, for code not leaving from packs, `/everytick`, for Jev asked at every tick as before cuo-df8, `/nosight`, for the client's sight checks off as before them, `/spots`, `/spotsrev` or `/codespots`, for runs going to the place Jev picks, the same listed the other way round, or code's pick ([where it runs](playing.md#where-it-runs)), `/jevstop`, for Jev saying when a leave stops ([leaving](playing.md#leaving-and-packs)), or `#<machine>`, to follow that plan; instead of `jev`, a model alias plays system one: `haiku`, `sonnet`, or one named with `--alias NAME=MODEL` or in the profile), `--alias NAME=MODEL` (repeatable), `--profile`, `--use KIND=MODEL`, `--facts none,all,jev` (world-fact scenarios: the conditions per judge), `--rounds`, `--lane`, `--out`, `--headless` (hide the client on `--port`, or start a headless one there if none runs, logged in with `--account`, `--password`, `--character`, `--server-port`, and closed at the end). The results record `headless` and `clients_at_start`, and each round the clients running on the machine.
- **`sight-check [--spots N] [--per-spot N] [--radius N] [--seed N] [--out FILE]`:** moves the character to spots around Britain and compares the client's line of sight with the server's `[AgentSight sample` there ([results](results.md#line-of-sight)); local test server only.
- **`strategy [show|set|add|clear|load|explain]`, `strategy templates`, `strategy template NAME [--replace]`, `strategy drop NAME`:** show or change the strategy, see how Jev read it, and list, pull in or take out templates.
- **`costs [LOG…] [--json] [--prices] [--key]`:** what the AI calls in the logs cost ([what it costs](seeing-what-it-does.md#what-it-costs)): the total, an hour, a decision, by kind of call and model, and OpenRouter's own count for the key over the same run. `--prices` updates `brain/prices.json` from OpenRouter's list prices; `--key` shows what the key has spent in all.
- **`fleet FILE [--hours H] [--out DIR] [--no-talk] [--windowed] [--set KEY=VALUE …]`, `fleet-report DIR [--json]`:** several agents on one server ([more](fleets.md#several-agents-on-one-server)). In the fleet file, `headless = true` (at the top for every agent, or per agent) starts clients without a window; `party = true` with `leader = "NAME"` plays the agents as one party on the leader's plan (only the leader needs a `goal`); `--no-talk` has nobody hear anybody (the control for a talk test). `--set KEY=VALUE` (repeatable) overrides one of the file's top-level settings for this run, read as TOML would (`--set help=false --set callouts=false`, `--set plan=huddle`, `--set memory=false`); a party's settings are `help`, `callouts`, `talk`, `small_talk`, `pace`, `roleplay`, `walk`, `plan` and `memory` ([what each does](fleets.md#a-party-in-a-fight-on-the-road-and-at-the-inn)), any fleet's `travel` and `rules` ([rules of play](fleets.md#rules-of-play)), and `use` gives every brain of the run a model for one kind of call (`--set 'use="table=anthropic/claude-sonnet-5.5"'`). A login that sticks is tried once more; a client that doesn't answer in time sits the run out while the others play (its login record says why), and a fleet closes the clients it started however the run ends.
- **`client [--headless] [--account A] [--password P] [--character NAME] [--host H] [--server-port N] [--log FILE]`:** start a client on `--port` from `bin/osx-arm64` unless one runs there (with `--headless`, a running one is hidden instead), and log it in, making the character a warrior if the account has none by that name ([without a window](fleets.md#without-a-window)). The client keeps running after the command ends.
- **`window [show|hide]`:** show a client's window and draw again, or hide it and stop drawing; with neither, report whether it is hidden and the loop's rates.
- **`replay-planner LOG --planner-model M [--limit N]`:** a session's planner moments put to another model, offline ([which model answers](seeing-what-it-does.md#which-model-answers)). With `--memories CHARACTER [--shard S] [--root DIR] [--out FILE]`, each moment is asked twice, as logged and with the memories Jev picks from what the character remembered then ([Results](results.md#the-planner-with-memories)).
- **`soak-report LOG [--disruptions FILE] [--json]`:** a report on an unattended `session` run from its log: the goals the planner chose and why, hunts, kills and deaths, where the time went, what was bought and banked, and what system one (Jev) and system two (the planner) cost, an hour and a kill. With `--disruptions` (JSONL of `{t, what}`), the goals that followed each disruption.
- **`review LOG [--accept N…] [--again] [--digest] [--planner-model M] [--cost-log FILE] [--shard S]`:** the planner model proposes strategy lines from a log, with the evidence ([after-action review](seeing-what-it-does.md#after-action-review)); `--accept` adds saved ones to the character's strategy, the only part that connects to the game, and keeps each in the character's memory as a lesson.
- **`goals [--shard local] [--character NAME] list [--all] [--json] | add TEXT | done ID [--why W] | drop ID [--why W]`:** a character's [long goals](world-and-memory.md#long-goals) and their progress (doesn't connect to the game; everyone's when no character is named).
- **`memory [--shard local] [--character NAME] list|add|forget|import`** (a character's own memories; doesn't connect to the game): `list [--kind event|lesson|order] [--area A] [--source S] [--search WORDS] [--stale] [--limit N] [--json]` (everyone's when no character is named), `add TEXT [--kind] [--area A] [--tag T] [--importance 1-5]`, `forget ID…`, `import LOG…` (backfill from earlier session logs, oldest first) ([the character's memory](world-and-memory.md#the-characters-memory)).
- **`world [--shard local] [--map Felucca] …`** (the world store; doesn't connect to the game): `note TEXT [--area A] [--tag T]`, `notes [KEYWORDS] [--area A]`, `place NAME`, `find KIND [--near X,Y | --near-place NAME]`, `spawns [AREA] [--near …] [--radius N]`, `hunt ARCHETYPE LEVEL [--near …]`, `route FROM TO`, `stats`, `import-modernuo [--modernuo-dir DIR] [--maps Felucca]`, `import-markers FILE|FOLDER… [--facets auto|uoam|client] [--maps Felucca,…]` (places from a shard's world-map markers, UOAM `.map` or the client's CSV; each file replaces its own rows), `import-guide URL|FILE [--area A] [--planner-model M] [--cost-log FILE]`, `fill-gaps AREA [--planner-model M] [--cost-log FILE]`, `outcomes LOG… [--area A]` (record what each hunt in the logs gave, per area and kit; importing a log again replaces its rows).
- **`login`, `status`, `snapshot [--semantic]`, `act <verb> k=v`, `accept`, `mode`, `cmd "-agent …"`, `say`, `shot FILE`, `report LOG`, `replay LOG [--model M] [--limit N]` (a log's questions asked again of Jev or any OpenRouter model: agreement with the logged answers, latency, cost; decisions Jev wasn't asked for are left out).**

## Client RPC

Newline-delimited JSON on 127.0.0.1, one object per line each way, enabled by `-agent_port`
or `agent_port` in settings.json. Replies carry the request id, and several connections can
be open at once (the brain and one-off CLI calls).

The agent's settings.json keys: `agent_port`; `agent_start_brain` and `agent_brain_dir`
([the client starts the brain](../README.md#the-client-starts-the-brain)); `headless`
([without a window](fleets.md#without-a-window)); and `agent_sight_rule`, whose
[line of sight](playing.md#line-of-sight) the client follows: `modernuo` (the default), `runuo` or
`runuo22`; and `agent_travel_pace`, the pace of trips that don't ask for one (`run`, or `walk`;
[rules of play](fleets.md#rules-of-play)). Each has a command-line option of the same name except `agent_brain_dir` and `agent_travel_pace`;
`-headless` and `-agent_sight_rule` hold for that launch only and aren't saved.

| method | params | what it does |
|---|---|---|
| `ping`, `status` | | liveness; the client's state, with `headless` and the loop's `updates_per_s`, `frames_per_s` and `draws_per_s` over the last second |
| `window` | `visible` | `true` shows the window and draws again, `false` hides it and stops drawing ([headless](fleets.md#without-a-window)); without params, only reports. Answers like `status`'s window fields |
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

### The snapshot

The player, creatures and corpses within `radius` (18 by default), new journal lines since
`since`, and the agent's own state. `pack: true` adds everything in the backpack. It also has
recent deaths, `travel` (state, the `pace` asked and whether its steps are `running` now, goal, tiles `left`, `replans`, `stuck_at` and beside each the next tile of the path and who stood on or beside it (`stuck_why`, also in the session's `travelled` log line), whether the path is a `hop`, `hops` finished, `doors` opened, `plans`, `plan_ms` and `plan_ms_max`, `why` a walk failed, and the doors, locked doors and houses `known`), `follow` while following (`target`, its `name`, `state`: `following`, `waiting` or `lost`, `distance` in tiles or -1 when the client doesn't have the mobile, `running`, the `pace` asked, `within`, `x` and `y` where the target is or was last seen, and `why` when it is in view with no way to it; there is no `travel` while following, nor after it until the next trip) and `errand` progress, each creature's full `label` with its title,
whether each corpse is a monster's, `player.ranged` (kind, ammunition and range of a bow or
crossbow in hand), arrows, bolts, whether an instrument is in the pack and `unread_bags` (the
backpack's bags not looked into yet, whose contents aren't counted) under `player.supplies`, `pets` (the character's pets in sight, with health), `agent.pet_order` and
`pet_target`, `travel_items` (marked runes and runebook entries) and `era` (`aos` or
`pre-aos`), and `player.followers` and `followers_max` (follower slots, which pets and summons take), each creature's `in_sight` (left out with the sight checks off), `agent.sight_checks`, `agent.sight_rule` (`modernuo`, `runuo` or `runuo22`), and under `agent.stats` `not_in_sight` (the server's "Target can not be seen" messages), `aimed_unseen` and `unseen_engaged_ms` ([line of sight](playing.md#line-of-sight)). `magic` covers every book carried (magery, necromancy, chivalry): `schools`, each
spell with its `school`, mana, tithing cost and what is missing, and `tithing` points; a
necromancer's reagents are under `player.supplies.pagan_reagents`.
`spots: true` adds the places to stand ([where it runs](playing.md#where-it-runs)), only with a monster in
view: each place's `kind` (`stay`, `ring`, `doorway`, `corridor`, `pet`, `npc`), `name` (a
landmark's), `x`, `y`, `dx`, `dy`, `ring` and `bearing` (0 to 15 from north, clockwise), `steps`,
`dir` (16 points), `room`, `near` (`[serial, tiles]` for the 8 nearest monsters, nearest first),
`seen_by` and `passes` (serials), and `spots_ms`, the time they took. `agent.stats` has
`spot_walks`, `spot_fallbacks` (no path there) and `spots_computed`.

### Act verbs

- **Fighting:** `attack {target, range}`, `disengage` (let the creature being fought go, leaving the rest as it is: a trip waits while the agent is engaged), `war_mode`, `stop`, `cast {spell, target, queue}` (a field or a placed summon aimed at a creature goes on the free tile beside it nearest the character), `skill {name, targets}` (with `targets`, a bard's song: the client answers each target cursor in turn, and the instrument prompt), `pet {kind: kill|follow|guard|stay, target}`, `flee {target, tiles, x, y}`, `kite {tiles, x, y}` (step back from melee, 2 to 8 tiles). With `x, y` (a place from the snapshot's `spots`), a run goes there, and a step back if it is within 8 tiles; with no path there, away from the creatures as without.
- **Healing:** `bandage_self`, `bandage`, `drink {kind}`.
- **Loot and items:** `loot`, `take` (from a monster's corpse, or the character's own after a death), `use`, `target`, `rewear` (put back on what was worn at the last death, from the pack).
- **Moving:** `walk_to {x, y, distance, run}` (`run: false` walks it), `move`, `travel {x, y, distance, pace}` (`pace`: `run`, as it is without, or `walk`, which still runs while there is danger), `follow {target, distance, pace}` (keep within `distance` tiles, 2 without, of that mobile as it moves; `pace`: `walk`, as it is without, or `run`; asked again for the same target it only takes the new distance and pace), `recall {target: rune or runebook, distance: entry, kind: spell|charge|gate}`. A pace that is neither `walk` nor `run` is refused.
- **Errands:** `bank {deposit, withdraw}`, `buy {target, items}` and `sell {target, items}` (`items` like `"bandage:50"` or `"loot"`).
- **Other:** `say {text, kind}` (`kind: emote` says it as an emote, in the profile's emote colour and between asterisks, added when the text doesn't have them: `*has a swig of ale*`), `bow` and `salute` (the gestures of the client's own Bow and Salute macros), `wait`, `hint {text}` (text above your head, client-side only, never refused).

Rules:
- **Cast authority:** healing spells count as `heal`, Cure as `cure`, attack spells as `fight`, anything else as `misc`.
- **Authority:** an act request is subject to authority unless it has `"source": "manual"`. In combat assist, an attack or harmful cast at a creature the engage setting doesn't allow is refused ("not your target").

## Test server commands

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
| `tools/video/` | filming a test run: the window recorder (`wincaps`), `film.py` around a command, `cut.py`, and `layout.py`, which sets a fleet's clients up for the camera and puts them back ([seeing what it does](seeing-what-it-does.md)) |
| `tools/uo-download/` | official client downloader (EA patch protocol, UOP rebuild) |
| `tools/modernuo/` | test server commands (`AgentTestKit.cs`), start script, setup and config notes |
| `tools/uor/` | the settings profile for UO Renaissance ([connecting](public-shards.md#connecting-to-uo-renaissance)), and `uor_map_markers.py`, which turns the shard's map page into a marker file to import |
| `docs/` | upstream ClassicUO's README, and the images for this one |
| `tests/ClassicUO.UnitTests/Agent/`, `brain/tests/` | `dotnet test tests/ClassicUO.UnitTests --filter "FullyQualifiedName~Agent"`, `cd brain && uv run pytest` |
