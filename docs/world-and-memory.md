# World knowledge and memory

<sub>[← README](../README.md) · [all documentation](../README.md#documentation)</sub>

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

Whether picking facts pays: [fact-picking results](results.md#world-store-and-fact-picking) and the
[wisp scenario](results.md#judgment-benchmark).

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
[map markers](results.md#map-markers).

**From guides and the model's own knowledge.** `uo-brain world import-guide URL|FILE [--area A]`
has the planner model read a page (as plain text, up to 60,000 characters) and store one
note per fact, each linked to the page as `guide:<url>`. Importing the same page again
replaces its notes. `uo-brain world fill-gaps AREA` stores what the model knows about an
area from its training as `model:unverified`, tagged `unverified`, until the game confirms
it. The model is Claude Sonnet 5.5 through OpenRouter by default (`anthropic/claude-sonnet-5.5`,
about $0.03 for a 9,000-character page; `--planner-model` picks another): Haiku, system two's model
elsewhere, wrote the same facts but left most of those that hold only on Trammel unmarked
([results](results.md#haiku-or-sonnet-for-plans-reviews-and-guides)). When a page says a fact holds only
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
([a real one](results.md#world-store-and-fact-picking)); a kind is only named as costly after 3 fights. Rows are
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
