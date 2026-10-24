# Several agents on one server

<sub>[← README](../README.md) · [all documentation](../README.md#documentation)</sub>

The aim is a server with smart NPCs playing beside people, each on its own models. A fleet file
in `brain/fleets/` names the characters, each with its own client (agent port), account, profile
and goal, and the test-server commands that prepare it:

```bash
cd brain
uv run uo-brain fleet fleets/graveyard-three.toml [--hours 0.5]   # the player's warrior and two NPCs
uv run uo-brain fleet-report logs/fleet/<time>-graveyard-three
uv run uo-brain party-report logs/fleet/<time>-party-lich        # a party's run, as a party
```

- **What it does:** starts a client for each agent that hasn't one running (from
  `bin/osx-arm64`), logs it in (creating a warrior if the character doesn't exist), runs its prep
  commands, then runs `uo-brain session` for it as its own process, so each brain has its own
  ledger. While they play it samples the CPU and memory of every client, brain and the server.
- **Stopping it:** Ctrl-C, a closed terminal or a `kill` ends the run in order: the fleet asks its brains
  to stop, each writes its summary (a companion's and a follower's say `finished: "stopped"`, or `"time
  up"` when the hours ran out), and then it closes the clients it started. A brain asked to stop cuts the goal
  under way short (a long rest, a walk to a bank) rather than finishing it first (cuo-7z4); one that hasn't gone
  after 15 s is sent SIGTERM, and killed 5 s after that. The fleet's log gets a `stopped` record.
- **The report:** per character, its profile, minutes, kills, deaths, gold banked, cost an hour
  and the models it used; together, the cost, the cost an hour and a kill, judge errors (where
  rate limits show) and the load per process.
- **A party's report** (`party-report DIR…`, `--json`; `partyreport.py`, cuo-cym.7): what the party did as a
  party, for any party run.
  - **The run:** its settings, who never logged in (such a run is void for a measurement), and the leader's
    goals in order with how each ended.
  - **Each fight,** found from the decisions themselves (a stretch in which some member acted against a
    creature or had one on it, with no more than 20 s of quiet inside it): what was fought and what lay dead
    after it, who joined and how many seconds after the first of them, who fell and how low the others got,
    the leaves, flees and fall backs begun, the leaves held back with the party beside it, who healed
    whom and who received it, and what was called out.
  - **How together it was,** when the members' positions were sampled through the run
    (`scripts/party_walk.py sample --ports … --out RUN/where.jsonl`): how far those who fought stood from the
    leader as the fight began, and for how many of its seconds one of them stood more than 10 tiles from
    every other.
  - **Deaths** (who, in which fight, how long until each was on its feet again, and whether at a healer or
    raised where it fell), **forming up** (how often, how long the leader waited, whom it went in without),
    each **talk round the table** (who spoke how often, how long it took, the memories kept of it), and
    what the run **cost** by member and by kind of call.
  - `scripts/party_scene.py` stays for the lich scene's own measures, which the results cite.
- **An agent's environment:** an `env` table in its entry goes into its brain's environment, so
  `env = { UO_BRAIN_ASK_EVERY_TICK = "1" }` puts one agent on the fight loop as it was before
  cuo-df8, beside others on the new one (`fleets/fewer-calls-ab.toml`).
- **The strategy a character starts with:** a character keeps its strategy between runs (it is the
  player's own words, saved with its client's profile). A fleet file's `strategy = "…"` at the top sets
  every character's at login, `strategy = ""` clears it, an agent's own `strategy` wins, and a file that
  names none leaves each as its client has it. A fleet that measures something should say: the lich
  scene's five had carried a video shoot's "never flee: the party stands together" into every party run
  of 2026-10-10 and 11, some with old planner lines after it ("flee if health drops below 50%"), so who
  could leave a fight depended on which client folder a run used. `party-lich` and `party-lich-solo`
  say `strategy = ""` since 2026-10-12.
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
  - **The leader's corpse** is fetched together. A corpse where danger was seen in the last 15 minutes is refused to a character on its own ([after a death](goals.md#after-a-death)); a party's leader goes anyway when companions are at the place (within 15 tiles of the corpse), or when two or more who came through that fight alive are beside it and none of the party had to leave the place, and then they go back there with it (its `recover_corpse` goal carries the place). Its planner is told which it is. Before this a resurrected leader waited in town "for the danger to age", the survivors beside it and the creatures long dead, until the corpse had decayed. A party that was wiped, or that lived by leaving (each member's report says where it last left creatures behind), has no such escort and waits as anyone would: a wiped party that went straight back all died there again, and a leader that went back with three who had been driven off met the same wave and died again.
  - **Not leaving alone:** with two or more companions within 10 tiles and health at 40% or more, a member's leave is held and it fights on with them ("held with the party: 3 companions beside it"); code's pack rule, weighed against the party, and a cautious strategy's badly hurt rule still leave (cuo-e1p). It fired 6 times in two runs and made no measurable difference: the party's deaths came in fights they were all in.
  - **Companions, not strangers:** in the fights each member's companions are named with what they play as (`your_party` in the state) and count in the odds ([leaving](playing.md#leaving-and-packs)); the planner no longer hears of them as "a unknown player 2 tiles away".
  - **The lich scene:** `fleets/party-lich.toml` (the party) and `fleets/party-lich-solo.toml` (a planner each), five classes staged south of the Britain graveyard with a lich, two bone knights and four skeletons waiting; `scripts/party_scene.py logs/fleet/RUN` says who reached the fight, who died, and how long the lich took to kill ([results](results.md#a-party-on-one-plan)).
  - **A second scene, casters and archers from two sides:** `fleets/party-casters.toml`, the same five against three skeletal mages and two skeletons where the lich's wave stands, and two ratman archers and a bone magus 18 tiles east of them (cuo-cym.11); measure a run with `scripts/party_scene.py --wave "skeletal mage,bone magi,ratman archer,skeleton" --boss "" logs/fleet/RUN` ([results](results.md#a-party-that-goes-in-together): formed up, nobody died in six runs).
  - **A clean scene after a restart:** the wave a run spawns is removed by the next run's `[AgentDisrupt
    restore`, from a list the server keeps in memory. A wave left standing when the server stopped was
    in the save and stayed: on 2026-10-12 the first run after a restart met two liches and five bone
    knights for a scene of one and two, and all five died within a minute. `restore` now also removes
    what `strong` spawned before a restart.
  - **One party a run:** a party's goals, states and callouts are kept under the run's own id (the fleet's name and the run's folder; a second run into the same `--out` folder gets a new one, recorded as `party_id` in `fleet.jsonl`), so a run never follows an earlier one's goals.
  - **Starting together:** a fleet starts each of a party's brains as soon as its character is logged in and prepared, so it fights back from then on, and the leader's planner waits for every member to be heard from (up to 90 s) before its first goal. Started together after the last login, a leader stood with its agent off for half a minute while a stray bone knight killed it and the archer beside it.
  - **More than one plan:** what a party does in a fight, on the road and at the inn is [below](#a-party-in-a-fight-on-the-road-and-at-the-inn).
- **Headless NPCs:** `headless = true` at the top of a fleet file starts every client without a
  window ([below](#without-a-window)); an agent's own `headless = false` keeps its window.
  `fleets/britain-five-headless.toml` is britain-five with the four NPCs headless, on its own
  accounts and ports. The report marks headless clients and adds each client's game-loop rate
  and its physical footprint (macOS's `footprint`, what Activity Monitor shows as memory; ps's
  resident size leaves out the GPU memory a client's textures take).

## Rules of play

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

## A party in a fight, on the road and at the inn

A party is more than one plan. Its members help each other in a fight and call out what is
happening, talk on the road, walk their trips together, and after a hunt sit down at the inn and
talk it over. All of the talk is for the local server only: nothing here speaks on a real shard.

A fleet file's top-level settings turn each part on or off, and `uo-brain fleet FILE --set
KEY=VALUE` overrides one for a run (repeatable), which is how the comparisons in
[Results](results.md#a-party-that-helps-calls-out-and-talks) were run:

| setting | default | what it does |
|---|---|---|
| `help` | true | heal, bandage and take creatures off each other |
| `callouts` | true | call out in fights |
| `talk` | true | hear each other and talk (a party hears itself, persona or not) |
| `small_talk` | true | chat when there is time, not only when there is something to say |
| `pace` | true | answer at a human pace, one speaker at a time; a number is the share of that wait (`pace = 0.7`: seven tenths; false or 0: each line as soon as it is written). A person who leads is answered in four fifths of it ([below](#a-party-for-the-player)) |
| `persuade` | true | with `plan = "player"`: a companion sent somewhere without the leader goes if it is willing, and may say no; false: it goes when told |
| `roleplay` | false | gestures and emotes on events |
| `walk` | false | walk trips together, run only in danger or to catch up |
| `plan` | "leader" | one plan: the leader's, agreed in a huddle with a planner each ("huddle"), or a person's, whose character leads and isn't one of the agents ("player": [below](#a-party-for-the-player)) |
| `form_up` | false | form up at the edge of a hunt, go in as one, keep to the line and fall back to the rally point |
| `rally_map` | false | with `form_up`: the rally point is chosen from the client's map, a few tiles back the way the party came with a way out behind it, and a second point further back for one badly hurt at the first |
| `game_party` | false | make the party in the game too: invitations, the right to loot the party's kills, party chat |
| `callouts_to` | "say" | where callouts are said: aloud (`say`), in the game's party chat (`party`, with `game_party`), or `both` |
| `raise` | false | raise a fallen companion where it fell, with bandages or Resurrection, before the walk to a healer |
| `strategy` | unset | every character's standing strategy for the run, `""` for none ([above](#several-agents-on-one-server)); unset, each keeps what its client has |
| `memory` | true | false: every brain without its character's memory, so repeat runs are independent |
| `use` | none | a model for one kind of call in every brain of the run, over its profile: `--set 'use="table=anthropic/claude-sonnet-5.5"'` (a list for several) |
| `travel` | unset | `walk` or `run`: the pace of every trip of every brain, its own errands too (an agent's own `travel` wins); unset, each client's `agent_travel_pace` ([rules of play](#rules-of-play)) |
| `rules` | none | a named set of these settings, under the file's own: `realistic` or `run-always` ([rules of play](#rules-of-play)) |

`fleets/party-inn.toml` is the lich scene's five with personas and all of it on, against a lighter
wave (a lich and four skeletons, without the two bone knights), so that the hunt is usually won and
what follows it can be seen; `fleets/party-lich.toml` stays the baseline, with the full wave, no
personas and no talk.

- **Going in together** (`rally.py`, `form_up = true`, cuo-cym.1). A party forms up before a hunt, goes in
  as one, and falls back to the place it formed up at.
  - **Why:** the logs of 2026-10-11 showed where the lich scene's deaths came from. The leader's hunt
    walked to the centre of the spawn, through the wave, each follower two seconds behind it on a path of
    its own, and the warrior met the lich and the bone knights with the others 8 tiles and more behind
    (100% to 42% in half a second in one run). The ones who then left (a mage at 89%, an archer at 94%)
    ran each along its own line away, out of the 10 tiles that make a party in the odds, and their hunts
    ended "left the area", which refuses them that place for half an hour.
  - **Forming up:** the leader's hunt walks to the edge of the hunting area (8 tiles outside the spawn's
    own spread), or stops sooner when something hostile comes within 13 tiles of it there (within 30
    tiles of the edge: a creature by the road on the way is the walk's own fight loop's). The others
    close up on it (the client's follow) instead of each walking to the centre. It waits there until
    everyone alive, heard from, within 60 tiles and not on business of its own (a resurrection, a
    corpse, a restock) is within 4 tiles of it, for 25 s at most; a fight that starts ends the wait.
    One who doesn't come is gone in without, and when it does come goes first to where the party went
    in from. A leader already inside the area (the second hunt of a place) gathers the others where it
    stands and keeps the rally point the party went in from.
  - **The word:** the leader calls it in its own voice ("Hold here. Form up on me.", then "In we go.
    Together."), and the word itself is a row in the party store (`party_rally`) that every brain reads.
    Everyone sets off at once. The party's point, its first warrior or paladin (the leader when it has
    neither), waits 1.2 s first: the others hear the word up to half a second late and each fight loop
    takes a moment to start, which at a run was eight tiles of lead.
  - **Where the hunt starts:** with creatures in view from where the party formed up, the hunt starts
    there. Nobody walks on to the centre of the spawn through them.
  - **With the point:** a member whose own pick of a target is nobody goes for what the point is going
    for (each member's fight row says its aim). "Seek only what Jev would attack" keeps a lone character
    off an idle creature it was told to leave; live, it had four of five rest for four seconds after the
    word, until the creatures turned on the warrior.
  - **Keeping to the line:** one who fights hand to hand goes only for what is on it or within 4 tiles of
    a companion, for as long as anything but spellcasters stands in war mode within 12 tiles. With nothing
    on the line it holds it ("holding the line with the party") and walks back to the nearest companion
    when it is more than 5 tiles from all of them. Live, the warrior had gone on to the lich at the back
    of the wave while the others stopped at their own reach, stood among six creatures, and was dead in
    thirteen seconds; the four behind him then won without losing anyone. A spellcaster that stands off
    is the mage's and the archer's to kill, and once only spellcasters are left there is no line to hold.
  - **Not ahead of the others:** it walks to a creature a leg of 3 tiles at a time, looking again after
    each, and never to a place more than 6 tiles from the nearest companion unless that brings it nearer
    to them: it waits for them to come up, its aim still said, so that they do. A walk under way is
    stopped when it holds or waits (the client's `halt`): a walk asked for once goes all the way, and
    in one run the warrior's decisions said "waiting for the party to close up" while the walk begun a
    second earlier carried him 13 tiles to the creature.
  - **Behind the point:** one who fights from where it stands (a mage, an archer, a tamer, a bard) comes
    no nearer to a creature than a tile further from it than the party's point stands, when the point is
    one who takes the blows. A creature out of its reach from there is waited for ("keeping behind
    Roland"), and it engages at the range that keeps it there, not its usual one. Live, with the warrior
    holding the line at the rally point, the tamer, the mage and the archer each walked forward to their
    own reach of the lich, 5 to 8 tiles from it, and were the party's front: the tamer was at 45% in
    eight seconds, and the fall backs began. An archer's reach from there is its bow's own range (10),
    not the 8 it shoots from by choice. A tamer's pet is not sent ahead of the point either, and the
    tamer doesn't walk "back to the pet" when that is a walk past the point; what is on the tamer itself
    comes first, wherever the point stands.
  - **Falling back:** a member's leave is a fall back while companions are still in the fight, or while
    the rally point is within 40 tiles. It runs to the rally point, a leg of 12 tiles at a time, whatever
    stands on the way (the client walks round it), and its hunt goes on. A first version kept the line
    away from the creatures whenever one stood between the member and the rally point, which in a fight
    at the line is always: three members fell back north, west and north-east through the graveyard,
    and somebody stood apart from the others for 70 of that fight's 86 seconds. The shorter runs go
    there too: a flee from what is on a member, and a tamer's run when it calls a hurt pet back (its pet
    following). A tamer's didn't until the fourth version's run, in which one ran from a bone knight
    three times in nine seconds, each time further from the party, and died where nobody could reach her.
  - **Coming back in:** at 60% health with nothing on it, once it has been back 5 s and is beside a
    companion (or has no way to the rally point); or at the rally point with two or more companions
    within 10 tiles, where it stands with them. Live, a warrior that left the lich at 38% was at 86%
    five seconds later and fighting beside the others again. After either it doesn't leave again for
    15 s unless it is under 40% or its health is falling fast, and at the rally point it stays while
    any companion still fights: the reason it left for (a pack coming) is often still there, and
    without this a leave decided at good health was undone and decided again at every decision.
    One that is badly hurt and at the rally point stands there too, and runs on only once something
    is on it: from the rally point there is no way "to the rally", and an archer at 37% ran 15 tiles
    on away from everything in sight, then turned round.
  - **When it is a leave after all:** with nobody left in the fight, nobody else still falling back,
    and nobody at the rally point to stand with or no way there. Who is still in the fight is each
    member's own fight row, in which one that is running says so; the first of several falling back
    together waits at the rally point for the rest. "Not leaving alone" no longer holds a member whose health is falling fast
    (30 points in 6 s) with three or more on it: held at 69%, 66% and 49%, a warrior was at 14% a
    second later with no way out. Nor one with nothing to fight with (a tamer whose pet is gone, an
    archer out of arrows) that has something beside it: held, all it can do is stand, and a tamer
    stood at the rally point for eight seconds beside a bone knight, from all of her health to 22%.
  - **To a companion that is set upon** (cuo-cym.10): a fight isn't over while something is on one of
    the party. A member with nothing close to it, who would loot or rest, goes instead for a creature
    that is on a companion in view: one the companion's own fight row names, or one in war mode within
    2 tiles of it (the row alone misses a chaser, which is "attacking" only as it hits). In reach it
    is fought from where the member stands; out of reach it is walked to, the warrior the whole way
    and not a leg at a time, the others behind him. A companion out of view whose row says something
    is on it is walked toward, 12 tiles at a time, by where its last report put it (within 40 tiles),
    when nothing in war mode is in view where the member stands. Not by one under half its own
    health, one whose rest is how it gets back what it fights with (a mage under 30% of her mana, a
    tamer whose pet is called back or under half its health), or one that fell back and stands at
    the rally point. Live, before this: a tamer ran from a bone knight 15 tiles from
    the others and in view of all four, who had killed their own and looted; one of their thirty
    decisions in those nine seconds said the bone knight was on her. With the rule, a bard who was
    looting walked to an archer out of his view with a bone knight on him and played Discordance on
    it; and in a staged run the warrior, the mage and the bard, all three looting, walked to an
    archer put 24 tiles off with a bone knight on him, and it was dead 18 s after it reached him
    ([results](results.md#a-party-that-goes-in-together)). The limits came from its first runs, in which a mage at 5% mana was kept from her trance
    for 40 seconds, the mage and the bard walked after a tamer who was merely running, away from the
    bone knight the archer beside them was shooting, and a tamer bandaging a pet at 2% was walked
    up to a lich.
  - **The hunt goes on:** a hunt's stop (Jev's "not worth staying here", supplies, a full bag) waits up
    to 90 s while a companion is still fighting. The leader's hunt is the party's: live, its "not worth
    staying" at a kill, with four creatures about, had its planner choose the next goal and cut the
    other four off in the middle of a fight they were winning. A death doesn't wait.
  - **A rally point that is chosen** (`rally_map = true`, cuo-cym.13; three live runs on the lich
    scene, [results](results.md#a-party-that-goes-in-together): no deaths, as without it). Without it the rally point is where the leader stood when it reached the
    edge or first saw something hostile, and formed up the party fights there: in the five runs looked
    at the warrior stood 1 to 8 tiles from it, so a fall back was a few steps to the place the wave
    arrives at, and he died there twice, from over half his health in about a second. With it the
    party still forms up where the leader stopped, and the leader then picks where it falls back to:
    - **The rally point** is 5 tiles back the way the leader came, by its own steps on the way in
      (kept as it walks; not across a recall). With no steps to go by, or none that lead to ground
      that will do, it is 5 tiles straight away from the hunt's centre, then an eighth of a circle to
      either side of that. Five is just behind those gathered within 4 tiles of the leader, and near
      enough to be one place with them.
    - **The ground** is the client's own map of what its travel planner can walk to (the `nav` RPC,
      32 tiles round the leader). The point may be put up to 3 tiles from the place aimed at. It has
      to be further from the hunt than where the party formed up, a short walk from there that never
      comes nearer the hunt, and to have a way out: a walk of at most 16 steps that gets 8 tiles
      further from the hunt without coming nearer to it on the way, and a walk that leads on from
      there to the edge of the map the client gave, again without coming nearer the hunt. A walled
      yard whose gate is shut has none (a door is not ground to fall back through), nor has a shore,
      nor a yard whose only opening is toward the hunt, however deep. Of the tiles that will
      do, one clear of the other spawns the world store knows comes first (outside how far a
      spawner's creatures walk, and 13 tiles more), then one with room to stand (five of the nine
      tiles round it), then the nearest to the place aimed at.
    - **The second point** is 12 tiles further back the same way (one leg of a run), found the same
      way: reached from the first by a walk that never comes nearer the hunt, at least 6 tiles
      further from the hunt than the first, and with a way out of its own. A rally point with no
      second point behind it is still used.
    - **With no map** (an older client, the call failing or taking more than 3 s), or no ground
      behind with a way out, the rally point is where the leader stopped, as before, and the `rally`
      record says why (`unchosen`). Chosen, the record has the rally point (`at`), where the party
      formed up (`formed_at`), the second point (`back`) and how long the map and the choice took
      (`map_ms`). A later hunt begun inside the area keeps
      both points, as it kept the one.
    - **In the fight:** every member's run is for the rally point, as before, wherever it stands:
      ahead of it, beside it or behind it. Only one that is at the rally point under 40% of its
      health while companions still fight, or with something on it there, runs on to the second
      point. Its runs of the next 10 s are for the second point too, so that it doesn't turn round
      half way. At the second point it stands, and comes back in at 60% as from the first. When the
      fight behind it is over and nothing is on it, its run is over where it is and its hunt goes
      on: the party is out of its ten tiles there, and the rule for one with nobody left to go back
      to would have ended its hunt as a leave whenever the others won before it had healed. Back at
      the rally point with two companions beside it and something after it, a member that is not
      badly hurt still stands with them and fights it there. The run's action says which point it
      is for (`spot`: `rally` or `back`), and its note names the second point.
    - **What it doesn't choose:** cover. Nothing in the choice asks what blocks sight (the client
      could say which ground the leader sees from where it stands, the `sight` RPC's `view`); a point
      out of sight of the line is out of its healers' sight too, and no run has shown which matters
      more. Nor does it use the world store's routes: the leader's own steps are the better record
      of the road, and the routes stored for a hunting place are mostly hops inside it. The store's
      sightings of creatures are left out on purpose: the wave a party fought last time is among
      them, where it was met and not where it lives.
  - **With a planner each:** a party that plans by huddle forms up too (the huddle, below).
  - **Measured** ([results](results.md#a-party-that-goes-in-together)): on the lich scene a party on the
    defaults had somebody standing apart from all the others for 17 to 50 of the first 60 seconds in
    each of six runs, and lost four members. Formed up, nobody stood apart in twelve of fifteen runs,
    the warrior was the first one hurt in all ten runs of the third and fifth versions, and three
    members died (none in the first seven runs). The code changed between those runs, so they are a sign and
    not a sample. Walking together (below) keeps a party together too.
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
- **Raising the fallen** (`raise = true`, cuo-cym.4). A member that dies was out of the fight for the walk
  to a healer in town and back for its corpse, alone, and a dead leader took the party's plan with it.
  - **The ghost** stays where it fell while a companion who could raise it is alive and within 20
    tiles (as far as a raiser walks), in war mode, which on ModernUO is when the living can see a
    ghost. Nobody raises anyone in a fight, so its wait is counted from when the party's fight is over:
    60 s of quiet, not counted while a raiser stands within 3 tiles of it, 4 minutes in all, and no
    more than 6 s once nobody who could is near. A mage short of mana for the moment still counts. It accepts the offer as it accepts a healer's
    and takes its things back from the corpse it is standing on. Then, or with nobody to do it, the
    walk to a healer as before.
  - **The raiser:** once nothing in war mode or on it is within 10 tiles and it isn't fighting, one with Healing and
    Anatomy at 80 and a bandage bandages the ghost from within two tiles, and a mage with Resurrection in
    its book, 50 mana and the reagents casts it from the next tile; either walks over first (up to 20
    tiles). The fallen come before the corpses: it doesn't walk off to loot, or round the spawn, while a
    companion lies there or its bandage is on. Each member's report says whether it could raise
    (`raises`), which is what the ghost waits on. With helping off (`help = false`) nobody raises.
  - **Again, and by everyone who can:** at the test kit's skills a try works about one time in four
    (ModernUO: (Healing − 68) / 50 for a bandage, which takes about 6.5 s on the dead; Resurrection at
    Magery 90 the same), so it is tried again every 7 s (a cast about every 10), and by every companion who
    can: a bandage and a spell on the same ghost don't get in each other's way. A raiser says so once
    ("Hold on, Wystan. I'll bring you back."), not at every try, and the one raised names whoever said so.
    In the first live run only one raiser at a time was allowed: the mage's fifth cast brought the archer
    back 63 s after his death, with the warrior and his bandages standing by.
  - **Measured** ([results](results.md#a-party-that-goes-in-together)): raised, the archer had his things
    back 77 s and 104 s after a staged death; the four members who walked to the Britain healer in the
    same night's runs, 40 tiles away, took 193 s to 237 s.
- **The game's own party** (`gameparty.py`, `game_party = true`, cuo-cym.3). A fleet's party was a party
  only in the brain's store, and to the game its five were strangers.
  - **Why it matters:** on ModernUO a monster's corpse is a crime to open for anyone without a right to
    it, unless they are in the party of someone who has one. In the party runs of 2026-10-10 and 11
    the five were told "Looting this monster corpse will be a criminal act!" 788 times between them,
    at each other's kills. The party also shares fame and karma, is told of a member's death by the
    server, and has its own chat; and the client keeps a member of its party out of the "red or criminal
    player" threats it leaves from.
  - **The leader invites** each companion it can see within 8 tiles (the server's reach for the
    invitation's target) who isn't in the game's party yet: one at a time, never with something on it
    (the invitation is a target cursor), and again after 35 s if nothing came of it.
  - **A member accepts its own leader's invitation and no other.** The leader is known by the serial
    its own brain reports to the party store, not by its name: names aren't unique on a shard. Anyone
    else's invitation is declined, and a member found in somebody else's party leaves it.
  - **Callouts in party chat** (`callouts_to = "party"`, or `"both"`): on a real shard a party's shouts
    are for the party, not for everyone on the screen. Until the member is in the game's party they
    are said aloud as before; `"say"`, aloud, stays the default, because that is what shows in footage.
    Party chat is never answered as talk, and like guild and alliance chat the client never takes it
    for the server's own word.
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
  Into a fight it is the difference between a party and five characters
  ([results](results.md#a-party-that-goes-in-together), six runs each on the lich scene): walking
  together the furthest member was 3 or 4 tiles from the leader as the fight began, against 9 to 11,
  and the party called 3 leaves in six runs against 28 and lost one member against four.
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
    memories and its own story of the hunt, with what has been said so far. One who sits down after
    the table opened is given turns too; one named twice without answering is passed over, and a
    keeper left with nobody else answering closes the table.
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
    its own words). Memories stay private and there is no shared party note. With the three lines
    the plan names who goes into the hunt (`going`, chosen from the party's own names; nobody when
    the plan is not to fight now), and each planner sees that list with the rest.
  - **When:** on events, never on a timer, and never with a hostile within 12 tiles or a fight on:
    the party has no plan yet; a member's goal for the plan failed or it had to leave the place;
    someone came near death or died since the plan was made; someone ran out of what it needs;
    something stronger than the plan had weighed was seen. Not more often than every 150 s, except
    after a death, and a death calls one huddle: the plan made after it has answered it.
  - **What each member does with it:** its planner reads the blackboard before choosing a goal, a
    goal under way is cut short when the plan changes (not in a fight), and it keeps its own memory
    of what was agreed.
  - **Setting out together** (`form_up = true`, cuo-cym.5): a plan in words doesn't start five planners
    at the same moment, and in the runs of 2026-10-11 the mage and the bard were in the fight half a
    minute before the warrior. With form-up on, the first member to reach the edge of the hunt the plan
    names holds the rally there, the others close up on it, and it waits (25 s at most) for those whose
    own reports say they are on their way to the same hunt and for those the plan sends there (below);
    anyone else isn't waited for. A member says it has set out the moment its hunt begins, not at its next report (they are
    5 s apart), and with nobody on the way yet the first there gives the others 4 s to say so: in the
    first live run the warrior reached the edge before any report said so and went in at once. One whose
    next hunt of the place begins while others are still on theirs joins the rally they went in from:
    each planner chooses the same hunt again every minute or so, and in the first runs each time one of
    them held a new rally where it stood, three tiles from the spawn's centre. One who arrives while the others are fighting from it joins them, and a rally that went
    more than 20 s ago with no fight on is held anew by the next to arrive. The warrior goes first
    whoever held the rally, and the rest is as for a leader's party: with the point, keeping to the
    line, falling back.
  - **Whom the first there waits for** (cuo-cym.14; built and unit-tested, not yet played in a live
    run): those who have said they are on the hunt, and those the plan sends there (the
    blackboard's `going`, when its target names the place), whatever each has said yet. Of them,
    as for a leader's party: alive, heard from in the last 30 s, within 60 tiles and not on business
    of its own. A member's planner says so while it is banking, buying, selling, restocking, being
    resurrected or going back for its corpse. Live, the bard was first at the edge with the warrior
    a second behind on a walk to the graveyard, which his own report didn't call a hunt, and the
    bard was made the point; the warrior died. The wait is 25 s at most, as it was. With nobody
    beside the rally who can take the blows (a warrior, a paladin or a warrior-mage) and one of
    those waited for who can still coming, it goes on to 45 s: under the 60 s the others wait for
    the word.
  - **A trip of its own into the hunt stops at the rally point** (cuo-cym.14, after the first live
    runs; unit-tested, not yet played live). Live (2026-10-14) the mage's planner chose to walk to
    the graveyard rather than hunt it: she walked through the rally point toward the spawn's
    centre, the warrior holding the rally took her for one who had walked on and went in after
    her alone, two died and the lich lived. Now a member's own trip (its planner's `travel_to`, by
    a place's name or a tile) that would end within the edge of the hunt its plan sends it to
    (the area the plan's target names, by the world store's spawners: the longest name in it, the
    Britain Graveyard and not Britain) goes no further than the party's rally point. It closes up
    on whoever holds the rally, or holds it there itself when it is the first, and is one of those
    gathered until the word to go in; its report says the hunt meanwhile. Then the trip ends ("the
    word to go in has been given: hunt it now"), and its planner chooses its next goal, the hunt
    as a rule. The edge counts, not the spawn's spread: the place a planner names for an area can
    be further from the spawn's centre than that (the Britain Graveyard's by 17 tiles, the spread
    15). A trip to a place it left creatures at lately is refused, as any trip there is.
  - **One who walks on past the rally anyway** (a walk the code didn't stop: more than 4 tiles
    nearer the hunt's centre than the rally point, not than the one holding it, who may have run
    from something meanwhile; and its report names no hunt) ends the wait only when the party has
    one beside the rally who can take the blows, or no fighter is coming: then the word is given
    and the party goes in after it. With a fighter on his way, the one holding the rally waits for
    him, within the 25 and 45 s, and he goes in first. One whose report does say the hunt is still
    waited for when it passes: it walks to its own stop at the edge and then closes up on whoever
    holds the rally. The point is a fighter beside the rally first, then one
    still coming. The `rally` record of whoever held it says whom the plan sent (`plan_sends`), the
    fighter the wait went on for (`waited_for_fighter`) and who had walked on (`gone_ahead`). There is no "ready" word from each planner before the hunt: what it would give
    (the first there not going in before the others have chosen) the plan's list and the wait at
    the edge give without another round of calls before every hunt, and it would not make a
    planner that chose the shop choose the hunt.
  - **The plan sends its members in** (cuo-cym.14, 2026-10-19, at Harry's word): a member the plan names
    for its hunt (its `going`, the target naming an area of the world store), in a party that forms up,
    goes into that hunt as its next goal, once for each plan, as a follower goes on the leader's plan:
    its planner isn't asked (`Planner.sent_in`; the planner record says `steered`). The first plan of a
    run is agreed inside the members' first planning step, and the plan is looked at again after the
    huddle there, so they go at once. Not one who is dead, under half its health, or out of bandages
    or reagents: its planner sees to that first, and the rally waits for it a while. A hunt it chose
    itself under the plan counts as the plan's, and it isn't sent in again after it. Live with memory
    on, the planners asked again had chosen rest 18 to 27 times a run, the healer and the shops.
    And only a plan that sends the party in now: each member puts the plan to system one once (kind
    `order`, `huddle.go_now_question`): does it send the party into the fight there now, or hold it
    back first (scout, regroup, go in only if no lich is in view, only once someone is raised)? Being
    together at the edge, or a pet ready, is the rally's business and counts as now. Live, the plans
    said "hold at the western edge, no entry yet" and listed all five as going, and the five sent in by
    the list walked into the lich: two died. Without system one, nobody is sent in by the plan.
  - **...and holds them back:** while the plan holds the party back from its hunt, a member's own hunt of
    that area is refused ("the party's plan holds the party back from the Britain Graveyard for now: nobody
    goes in on their own"), and its planner chooses something else. Live with memory on, the plan said to
    enter only after a scout and the bard's planner hunted the graveyard alone, 15 s ahead of the rest.
  - **Measured** ([results](results.md#a-party-that-goes-in-together)): with memory off, when every
    planner chooses the hunt, all five were in the fight within 7.0, 5.2 and 3.6 s of each other in
    three runs. With memory on, in three more, those who came were in within 6.4, 4.5 and 15.4 s of
    each other, but one or two members never came in every run, and once the bard, first at the edge with nobody on the way yet, went
    in as the point with the mage and the archer 9 and 15 s behind him. The gathering brings together
    those who have set out; it doesn't make five planners set out. The leader's plan, formed up, had
    all five in within 3.4 to 3.6 s in each of its three runs. All of these runs are from before the
    first there waited for whom the plan sends. With the plan sending its members in (2026-10-19): memory
    off, all five were sent at once and in the fight within 5.0, 14.0 and 6.6 s, the lich dead in 19 to
    35 s (three died in one run); memory on, every plan held the party back until a scout nobody made, and
    nobody went in.

## A party for the player

A party's leader can be a character a person plays (`plan = "player"`, `companion.py`, cuo-cym.6). The
leader is not one of the fleet's agents and has no brain: you log it in and play it, and the fleet's
agents are its companions.

```bash
cd brain
# log your own character in (here Roland) and stand south of the Britain graveyard, then:
uv run uo-brain fleet fleets/party-player.toml --windowed
# or companions with personas, who talk with you and each other and sit down for a drink when told:
uv run uo-brain fleet fleets/party-player-talk.toml --windowed
# without a person: a script plays the leader's part with walks, attacks and three lines of speech
uv run python scripts/party_player.py
# the same with its orders said as a person might ("Hold up a moment, I'll scout ahead.")
uv run python scripts/party_player.py --own-words
# the same with companions who talk, and the table at the end
uv run python scripts/party_player.py --fleet fleets/party-player-talk.toml --table
# and with another character inviting two companions first, which they should decline
uv run python scripts/party_player.py --stranger talk2:5665:Corwin
# what a person tried in the first five minutes: loot and stop, off to the inn, a question to all of them
uv run python scripts/party_player.py --fleet fleets/party-player-talk.toml --playtest --own-words
```

- **What a companion goes by:** a character someone plays posts no goals and reports nothing, so each
  companion reads the leader from what its own client sees of it.
- **Keeping beside it:** the client's follow, two tiles off, at the leader's own pace (a walk while it
  walks, a run to catch up). With the leader out of view the companion goes to where it last saw it and
  waits there.
- **Fighting beside it:** the fight loop runs the whole time. A companion takes on what attacks it, and
  anything in war mode within 6 tiles of itself or 4 tiles of the leader: a client isn't told whom
  another character attacks, so what stands in war mode beside the leader is taken to be what it is
  fighting, or what is on it. It walks to those and goes after nothing else, and it loots nothing until
  you say it may: the kills are the player's.
  - **What you fight from range** (cuo-cym.25, `escort.py`): when a creature in war mode is in view that
    the rule leaves out, the companion puts each creature in view to system one (kind `order`, at most
    every 3 s): is it part of your fight? With what its client sees: how far it is from you and from the
    companion, whether it is in war mode, which way it faces and whether that is towards you, whether it
    has come nearer you in the last few seconds, whether it is wounded with no companion on it, how you
    stand (war mode, which way you face) and what you said lately ("leave the troll alone"). One judged
    yours is fought for 12 s after, as if it stood beside you; the rule stays the floor. `judge_reach =
    false` in the fleet file (or `--no-judge-reach`) is the rule alone, as before
    ([measured](results.md#a-party-that-goes-in-together)).
- **Helping it:** the leader is a companion like any other to [the helping](#a-party-in-a-fight-on-the-road-and-at-the-inn):
  a mage heals it under 60%, one with bandages bandages it, by what its health bar shows. Callouts work
  as in any party.
- **What you say to them,** in your own speech and nobody else's, and in your own words (cuo-cym.15,
  cuo-cym.18 to .21). Each companion puts every line you say to system one (calls of kind `order`,
  Decision 1 unless the [profile](seeing-what-it-does.md#which-model-answers) says otherwise), for
  itself, with the party's names and the talk just before the line in front of it. It asks: is this
  an order to me, does it change how I play (the settings, below), with creatures about is it an order
  for the fight itself (below), and, in a party that talks, is it asking me for something and who is it
  for (further down).
- **Orders.** There are four: come along ("follow me", "right, on we go", "keep up, you lot"), wait
  where you are ("stay here", "hold up a moment, I'll scout ahead"), go somewhere without you ("off to
  the inn with you, see you there": the next point) and, in a party that talks, sit down for a drink
  (below).
  - **An order is taken when the model gives it 0.6 or more.** Most of what a person says is none of
    them: "you should have stayed back, Isolde" and "I could follow that road for days" are talk, and
    so is a plan put as a question ("who wants to go off to the inn?").
  - **Sure it is an order to move, unsure whether you are coming too,** it is taken as being sent
    when that has 0.3 or more ("Right - off to the inn for you all - see you there" had being sent at
    0.50 to 0.65 and coming along at 0.35 to 0.50 for three companions in one live run, so one went
    and two stood still; in a later one two had coming along at 0.64 and 0.67). Sent to a place you
    walk to as well, a companion gets there first and you find it there; left to follow a leader who
    stays, nobody moves. With no place in the line it is an order to come along.
  - **Who an order is for** is the model's reading too: at "Wystan stay, the rest of you with me"
    Wystan waits and the others come, and at "everyone but Thea, come on" Thea waits.
  - **Each says a word when it takes one,** so a line misread shows at once, and the next order puts
    it right: "Staying here.", or with a persona a line of its own, written once with its
    [callouts](#a-party-in-a-fight-on-the-road-and-at-the-inn). The others hear it and don't answer it.
  - **An order it is already carrying out gets a word too** ("Still with you.", "Still here.", "On my
    way."), from each at most every 15 s. Without it a line misread as the order in force showed
    nothing: "off you go then" was read as "come along" by companions who were following, and the
    leader couldn't tell it had been read as an order at all.
  - **Every reading is logged** and shown in the calls window. `order_read`: the line, the order or
    none, the probability of each, the settings it speaks to (`settings`), who it is for (`for`),
    whether it asks for something (`asked`, and `left`: "asked" or "unsure" when that is answered) and,
    with creatures in view, the order for the fight it holds (`fight`: the order or none, the
    probability of each, the creature picked and how sure, whom to heal); `order` when one is taken,
    with the word said, and for a trip its `place`, `arrived`, `refused` or `gave_up`, and `already`
    for an order in force; `setting_read` and `setting` for a line that speaks to a setting and each
    change it made; `place_read` for where a companion was sent and how willing it was; `fight_order`
    for an order for the fight taken (the creature, whom to heal, the word said), one it couldn't carry
    out (`none`, `cannot`), and each one's end (`ended` and `why`); `whom_read` for whom a heal is for.
  - **Without a model** (the rule judge), or for a minute after two questions in a row got no answer,
    a short list of phrases reads the orders as before: "stay here", "wait here", "hold here"; "follow
    me", "come on", "let's go", "stay close"; "sit down", "drinks", "take a seat"; and "off you go",
    "go on ahead", "see you at", "wait for me at" with the place's word in the line ("the inn", "the
    bank"). Settings and who a line is for aren't read without a model.
  - **How well it reads** ([measured](results.md#a-party-that-goes-in-together), each line for three
    companions): on 34 lines it hadn't been tried on, written to test being sent somewhere, Decision 1
    read 92 of 102 as written, Jev 90, the phrases 47; on the 53 unseen lines of the first three orders,
    148 of 159 (149 before the fourth order was added). A reading takes about 0.3 s and costs $0.00006
    with the question about settings in it, and $0.00002 more for the one about requests.
  - The leader is known by its name and then by the serial first seen under that name, so another
    character of the same name changes nothing; this is for the local server.
- **Going somewhere without you** (cuo-cym.19): "off to the inn for you all, see you there", "go on
  ahead to the bank, I'll catch you up", "Wystan, wait for me at the stables".
  - **Where:** the nearest of its kind that the world store knows, no further than 400 tiles: the inn,
    the tavern, the bank, the healer's, the stables, the smithy, a shop by its trade, town, the
    graveyard, the docks, the moongate, a dungeon's entrance, a shrine; or a place you name by its own
    name (cuo-cym.27): "the east bank", "Yew", "the inn in Skara Brae", "Jhelom's docks", found in the
    world store from the line and the talk just before it (`named_places`: up to four words with a word
    of a place's own name in them, the store's best match and of several as good the nearest; a town
    with the kind of place said beside it, "the healer in Yew" or "go to Yew and wait at the inn", that
    town's nearest of that kind; a quarter with a kind, "the west inn", only a place within 400 tiles),
    and offered beside the kinds, as far as the land is wide. System one picks by
    what you said, or by the talk just before when the line names none ("off you go then"). In a line
    with several places it picks the companion's own ("I'm going to the bank, you go to the healer";
    "Isolde to the tailor's, the rest of you to the stables"). With no place it can tell, the companion
    asks where and stays.
  - **An errand** (cuo-cym.27): "run to the healer and get me 10 bandages, then come back", "go bank this
    gold", "take the loot to the smith and sell it". In the same request system one is asked what the
    companion is sent to do there: only to go and wait, or to buy, bank or sell; for a buy, what
    (bandages, heal, cure or refresh potions, arrows, bolts, or reagents, which is a restock of what it is
    short of) and how many (5, 10, 20, 30, 50 or 100; 20 when no number is said). It goes on the errand
    with the session's own buy, bank or sell (the shop the line names, or the nearest that sells it),
    its fight loop running as always, and comes back to you with a word on how it went ("Got 10 bandages, coming
    back."). Sent to buy without its being said what, it goes and waits, as before.
  - **One of you** (cuo-cym.27): "one of you go and find a healer", "someone fetch me bandages". In the
    same request, whether just one of the companions is asked, whichever; then the first that will go
    claims the line on the party's floor and goes (as one answers a line meant for nobody in particular),
    and the others carry on. One that won't go says so and claims nothing.
  - **Whether:** going is the companion's to agree to (`persuade`, on unless the fleet file says
    otherwise). A yes/no for system one on the moment: its own health and yours, whether it is in a
    fight, hostile creatures close, whether it has said no already. On a no it stays with you and
    says why in its own way; ask again, or give a reason, and it goes. `persuade = false` has every
    companion go when told.
  - **The trip:** it runs there by itself with the fight loop beside it as always, says a word on
    setting off ("Off to the inn, then.") and one on arriving, and waits there. A walk that gets stuck
    four times is given up: it comes back to you and says so.
  - **Yours again** when you come within 8 tiles of where it waits, or when you call it back ("come
    back", "back to me"; in party chat when it is out of earshot). Called back with you out of view,
    it goes to where it last saw you.
  - **How well it reads where** ([measured](results.md#a-party-that-goes-in-together), one companion):
    the kind of place meant on 17 of 20 lines, and on 9 of 10 written afterwards; whether to go on 12
    moments of 12 (Jev 7). A place by its name on 6 of 6 lines written after the wording was settled
    (the phrases: 2), a line with several places on 3 of 4 (the phrases: 1), errands on 8 of 8 held back
    with how many right, and "one of you" on 6 of 6 held back (Decision 1, which reads these).
- **Orders for the fight itself** (cuo-cym.24): "kill the mage first", "get the one on me", "fall back!",
  "heal me". With creatures in view (one at least that could start a fight: a deer alone isn't one), a
  companion also asks of every line you say, in a request of its own beside the order's: is this an order
  for the fight, to carry out now? One choice: go for one creature, fall back, heal someone, or none; in
  the same request, which creature, with the six nearest as the options, each with how far it is from the
  companion and from you, whether it is in war mode and whom it is on. "That one" with nothing more is
  the one on you, or the one nearest you. An order is taken at 0.6 or more, the creature at 0.5 or more.
  Each is said back at once, as an order is ("On the evil mage.", "Falling back.", "Healing you.", or with
  a persona a line of its own from its callouts for going for a creature, coming along and healing
  someone), and the next line puts a misreading right. An order for the fight is each companion's unless
  the line is plainly for others ("Wystan, shoot the lich"): one with nobody named split the question of
  whom it was for live, and nobody took it. Live, "Get the one on me!" was taken by all three and "Fall
  back!" brought all three away with you ([results](results.md#a-party-that-goes-in-together)).
  - **Go for one:** the fight loop goes for that creature before anything else,
    whatever Jev would pick and whether or not it is in war mode beside either of you (so you can name the
    one to pull), until it dies, leaves view or 30 seconds pass. A tamer sends its pet at it before what
    is on the tamer itself. Running and leaving still come first: nearly dead with something after it, a
    companion runs as it always does.
  - **Fall back:** it lets go of what it is fighting and comes to you (the client's
    follow, which runs while there is danger); a trip you sent it on ends, and one told to wait comes
    too. On the way it fights nothing; within 3 tiles of you it fights only what is on it or right on you,
    and goes after nothing. It holds for the fight: until nothing has been in war mode within 12 tiles of
    it for 5 seconds, or a minute, or until you name a creature to go for. A tamer calls its pet back.
  - **Heal someone now:** one heal or bandage on you, or on the companion named
    ("Isolde, heal Thea"), whatever their health (the helping's 60% doesn't apply to it), walking over in
    the fight if nothing is on the healer itself. Whom is a question of its own, asked once a heal is read;
    when it can't tell and the line names nobody of the party ("heal pls"), it is you. One who can't heal
    (no Greater Heal with the mana and reagents for it, no bandages and Healing) says so when the line is
    for it alone (as a party that talks reads who a line is for), and otherwise leaves it to a healer.
    Good for 15 seconds.
  - **What it can't tell, it asks:** "Which one?" when no creature in view fits ("kill the dragon"),
    "Heal whom?". In a fight, or without talk, in those plain words.
  - **What stays what it was:** a line that sends it away or sits it down is that order, whatever the
    fight question made of it ("off to the inn with you, I'll deal with these" is a trip it may refuse);
    a request for no one creature ("cover me", "hold them off", "kill them all") is answered as a request,
    and talk, a command to your own pets ("all kill") and words shouted at a creature are none of these.
  - **Without a model** nothing reads these: the phrases know only the four orders. Each line read with
    creatures about costs one request more, about $0.00005 on Decision 1, and 0.3 s, beside the order's.
  - **How well it reads** ([measured](results.md#a-party-that-goes-in-together), each line for three
    companions): on 42 lines held back until the wording was settled, Decision 1 read 112 of 126 as
    written and Jev 117; each took talk, or an order for another companion, for an order in 3 readings,
    and Decision 1 never sent a companion at the wrong creature (Jev once: "Wystan, pull the near one"
    went for the deer nearer Wystan, not the orc nearer you). What Decision 1 missed it missed safely:
    "help Thea with that ghoul" (0.12 to 0.32) and "leave it, leave it!" were no order, and the companion
    fought on.
- **Settings, by your word** (cuo-cym.18; `standing.py`). Everything about how a companion plays that
  the fleet file or a strategy sets, you can change by saying so, and it holds until you say otherwise:

  | setting | what you might say | values |
  |---|---|---|
  | looting | "feel free to loot", "take everything", "leave the rest" | nothing (as they start), what is worth having, everything |
  | fleeing | "nobody runs", "run if it goes bad" | may run, never runs |
  | aggression | "play it safe", "go all out" | cautious, balanced, relentless |
  | target | "weakest first", "biggest threat first" | the one it is on, the weakest, the closest, the strongest first |
  | opening spell, main spell (a caster) | "Isolde, open with Explosion", "stick to Lightning" | an attack spell, or its own choice |
  | helping | "heal each other", "no healing, save your mana" | on, off |
  | raising | "if someone drops, get them back up" | on, off |
  | callouts | "stop shouting in fights" | on, off |
  | talk | "quiet, all of you", "less chatter", "you can talk again" | silent, answers when spoken to, talks freely |
  | distance | "stay tight on me", "give me some room" | at your side, two tiles, five tiles back |
  | reach | "only what attacks us", "kill everything you see" | only what is on it or on you, what is close to either, anything hostile in sight |

  - **Two steps.** Beside the order question, on every line: which of my settings does this speak to,
    if any? One choice with the settings as its options. For each it gives a quarter or more (a line
    can speak to two: "loot what you like, and no running"), that setting's own question: what does
    the line make of it, or does it leave it as it is? A value is taken at 0.6 or more.
  - **Each reads it for itself,** so "Wystan, you loot; the rest of you keep off" sets Wystan's
    looting one way and the others' the other.
  - **Each says what it has changed,** in a line of its own that has to say what it will now do
    ("Fine. I'll take what's worth carrying."), without the wait of an answer and whatever the yes/no
    on answering would say; in a fight, or without talk, in plain words ("I'll loot what's worth
    taking."). Told what it already does, it says so. A misreading shows, and the next line puts it
    right.
  - **A change is acted on at once:** told to loot in the quiet after a fight, a companion goes to the
    corpses then, not when the fight loop next looks.
  - **How close to keep isn't read from a line that is itself an order:** "stay close" is an order.
  - **How well it reads** ([measured](results.md#a-party-that-goes-in-together), each line for three
    companions): all 171 readings on the lines the questions were written with, and 134 of 156 on 52
    lines written afterwards (Jev 154 and 132). Most of what went wrong there was a remark or a request
    for one thing now taken as a rule: "I always go for the biggest one", "run for it!", "heal pls".
    None of it is taken silently: each change is said.
- **Talking with them** (`talk = true`, a persona each; cuo-cym.16, cuo-cym.20 to .22): what you say is
  heard as a companion's is, in the two steps of [all talk here](#several-agents-on-one-server). System
  one answers a yes/no on your line (kind `chat`): should this companion say something now? On a yes
  the cheap planner model (kind `talk`, Haiku) writes one line in the companion's persona, with what it
  is doing and what it remembers. What is different for you from how they talk among themselves:
  - **Who a line is for is read, not matched.** One question for system one with the party as its
    options: one companion, all of them, all but one, or none of them, from the line, what each plays
    as ("our archer") and the talk before it. "And your reagents?" straight after Isolde's answer is
    hers; "what about the rest of you?" is the other two's; "Wystan shot it first" is said to
    everyone. Those it isn't for leave it without asking anything.
  - **Each it is for answers,** in turn: the first as soon as its line is written, the next once it
    has heard that answer, about 2.5 s after it. "How are you all today?" gets an answer from each of
    them. (Among themselves a line for nobody in particular still gets one answer, from whoever claims
    it first.)
  - **What it says agrees with what it does.** The writer of the line is told how the same line was
    read: an order taken has had its word and gets no second line; a setting changed has to be said;
    anything else is told to the writer as talk that changes nothing, with what the companion is doing
    now.
  - **Asked for what it doesn't do at a word, it says so.** "Cover me", "somebody res me", "go and buy
    me some reagents" are no orders it takes, and nor are "heal me" and "kill that one for me" with no
    creature in view: it fights what attacks the party and heals whoever is hurt without being told, and
    in a fight goes for the creature you name, falls back or heals at once (the orders for the fight,
    above), and nothing else is done on command. A yes/no of its own for
    system one, beside the order's: is this line asking me to do something? With no order taken, the
    line for this companion and a yes at 0.7 or more, it owes an answer whatever the yes/no on
    answering says, and the writer is told what it will and won't do, so it says no in its own way and
    promises nothing ("Cover you? I'm a mage, Roland, not your bloody shield."). A line that was
    nearly an order (0.35 or more for one of them) gets a question back in place of a no: what do you
    want? Not in a fight, where nobody answers. Before this "cover me" got one answer in three live
    runs: the yes/no on answering gave it 0.13 to 0.29.
  - **How well a request is read** ([measured](results.md#a-party-that-goes-in-together), each line
    for three companions): on 19 lines written after the question was settled and run once, 16 of 21
    readings of a request, 24 of 27 of talk left as talk, and all 9 of an order with a request in it
    ("With me, and somebody watch the rear."). "Can anyone gate us home?" was a question to it and
    not a request, and "all guard me", said to your own pets, a request to all three.
  - **An answer owed to you isn't dropped because someone spoke.** It waits out the others' lines, and
    your next line too unless that one is for the same companion and wants an answer itself, which
    then takes its place.
  - **You are answered sooner.** Among themselves a reply waits as long as a person takes to read the
    line and type the answer; to you it is set at four fifths of that, and `pace = 0.7` in the fleet
    file makes every wait seven tenths of what it was. Measured live, first answers came about 6 s
    after a line of 30 to 50 characters (5.6 to 8.7 s), about a tenth sooner than before for the same
    lengths ([results](results.md#a-party-that-goes-in-together)).
  - **No gap between your lines' answers, and no end to an exchange.** Among themselves each speaks at
    most every 20 s and two who have traded six lines let it rest; neither holds for a line that is
    for the companion you spoke to.
  - **The yes/no is asked differently** (`talk.talk_question`). Among agents saying nothing is the
    safe answer; to a person it is being ignored.
  - **Told to be quiet** (the talk setting above) it answers nothing but a word on an order and what
    it owes on a setting, until you say it can talk again.
  - **How well who it is for is read** ([measured](results.md#a-party-that-goes-in-together)): right
    on all 21 lines of a set held back, and each line answered by exactly those it was for on 20 of
    them. On the 39 lines the yes/no alone was measured with, 38 as wanted against 34, and "the rest of
    you" 4 of 5 against 0 of 5.

  Nobody answers in a fight, where the callouts speak, and each says at most 40 lines an hour.
- **Through walls** (cuo-cym.28, `relay.py`): ModernUO sends speech only to those in line of sight of
  the speaker, so a companion on the other side of a wall (inside an inn, on the way out of one) never
  got a line you said a few tiles off, and nobody read or answered it. Each companion's brain now also
  reads what you say in your own client (our client has an agent port: the one on this machine whose
  character is the leader, found by its name, or `leader_port = N` in the fleet file), and a line its
  own client hasn't heard 1.5 s after you said it, though it stood within the line's earshot (15 tiles,
  a yell 18, a whisper 1, the server's own), is put into its client's journal (the `hear` RPC) and read
  like any other. A wall is all it gets past: a companion 20 tiles off still doesn't hear. `hear_leader
  = false` in the fleet file (or `--no-hear-leader`) turns it off. Each brain logs `relay` records
  (`found`, `relayed` with the line, the tiles and how late, `lost`) and its summary has `relay`:
  `relayed`, `heard_directly`, `out_of_earshot`. Party chat reaches them wherever they are, as before.
  It works one way: their answers go out as the server sends speech, so from behind a wall you don't
  see what they said until they come into sight ([measured](results.md#a-party-that-goes-in-together)).
- **Resting:** when you stand still they stand with you, and each heals itself as it always does (the
  client's reflexes bandage). There is no rest of their own to call.
- **The table** (`talk = true`, a persona each): walk them to an inn and tell them to sit down ("who
  fancies an ale? Take the weight off."), and they hold [the party's talk over the hunt](#a-party-in-a-fight-on-the-road-and-at-the-inn)
  where they stand. No planner hosts it: each companion who takes the order takes a seat, and the first
  of those in the file keeps the turns (one in a fight at that moment doesn't sit). What you say while it
  is open is on the table like anyone's line and the next to speak answers it (a line with a companion's
  name goes to that one), you are never named for a turn, and an order to move on ("right, we should
  get moving", "on your feet", "wait here, I'm going to stretch my legs") ends the evening; nothing else
  you say there is an order ("stay a while, the night is young" is an offer). One killed
  at the table gets up from it. Each keeps its own memories of it. "Follow me to the inn" is a walk:
  ask for the table once you are there. Not asked for in a fight.
- **Its own death:** a companion is resurrected and goes back for its corpse by itself, then comes back
  to the leader.
- **The game's own party** (`game_party = true` in the file): you invite the companions from the party
  gump as you would anyone, and each accepts your invitation and no other.
- **What isn't here:** forming up and the huddle are a planner's, and a companion has none. It goes
  nowhere but where you send it, to wait: it doesn't restock, bank, buy or hunt by itself, and doesn't
  walk into a fight you haven't started unless you name the creature. Of the orders for the fight, three
  are carried out (go for that one, fall back, heal someone now); "cover me" and "hold them off", which
  name no creature, raising the dead at a word ("res him now") and orders with a place in the
  fight ("stand behind the bear", "go round the left") aren't: it reads them as requests, and says what it
  will and won't do. "Follow me" in a fight is the order to come along and doesn't pull it out of the
  fight: the client's follow waits while it is engaged. None of the three has been tried live yet.

## A hamlet

NPCs with lives of their own choose their days anywhere on the map, so they scatter and meet by chance.
A hamlet gives a fleet's characters a place that is theirs: a home each, neighbours they see every day,
work that keeps them there, and a reason to come back (`hamlet.py`, epic cuo-m3n). Local server only, like
all talk between agents.

```bash
cd brain
uv run uo-brain fleet fleets/hamlet-cove.toml                       # four residents of Cove for an hour
uv run uo-brain fleet fleets/hamlet-cove.toml --set hamlet=false    # the control: told nothing, still measured
uv run uo-brain hamlet-report logs/fleet/<time>-hamlet-cove
```

- **The fleet file:** a `[hamlet]` table names a region of the world store (`name = "Cove"`) and how many
  tiles round it still count as in it (`edge = 10`). Each agent has a `home` (a place of the store, or
  `"x,y"`), `home_words` (how it speaks of it: "the healer's house") and `work` in words, made of the goals
  that exist today: hunting near the hamlet and selling to its shops, keeping the healer's house stocked,
  carrying coin to a town's bank. Gathering and crafting stay out (2026-10-06).
- **What a resident is told:** its planner's system prompt says where it lives, its home (and that
  `travel_to "home"` goes there), its work, its neighbours by name, trade and home, and that its days begin
  and end in the hamlet: work out, then home, rest at home, free time where the neighbours are, a long trip
  only for a reason. Each turn's situation says where it is against home and the hamlet ("at home (the
  healer's house), in Cove", "away from Cove: 131 tiles from it and 171 from home"). Its talk knows where
  it lives and who its neighbours are. Nothing is scripted: the planner still chooses every goal, but a
  resident with no player's goal can't `finish`: code answers "not finishing: you live in Cove and your days
  there go on" and the planner chooses again (cuo-m3n.3: the watchman, sure every way out was too
  dangerous, finished half an hour into an hour). Over budget it still stops.
- **The control:** `--set hamlet=false` tells the residents nothing (their personas don't name the place,
  so they are britain-lives NPCs who happen to start in Cove) and the fleet still measures them against it.
- **Measured:** the fleet samples where every resident is every 30 s (records of type `where` in
  `fleet.jsonl`). `hamlet-report DIR…` (`--json`) says for each: the share of the run in the hamlet (or
  within its edge) and at home (within 6 tiles), how often it came home, its longest time away and how far
  it went, spells of 10 minutes or more standing still away from home with the goal it was on, its work
  (goals by kind, kills, gold from sales, things bought, gold banked, trips by where to), lines said to and
  heard from each neighbour, deaths and cost an hour, and what its memory keeps of each neighbour from the
  run.
- **`fleets/hamlet-cove.toml`:** four residents of Cove (Felucca), a guarded hamlet with a healer, a
  provisioner, an armourer and weaponsmith and a fisherman, and no bank or inn: Brannoc the watchman at the
  east gate (the orcs near the Cove Orc Fort to the south), Wenna the hunter in the small house on the
  square (the beasts south-west of the Cove Cemetery), Maren the healer's apprentice at the healer's house,
  and Tamsin the carrier above the provisioner's (coin to a bank in Vesper or Britain). On the npc profile,
  headless, walking (`rules = "realistic"`), on their own accounts and ports (headless3-6, 5671-5674), so
  they can run beside a party's fleet. The fisherman's own spot is on the jetty, with no walking route out,
  so Wenna lives on the square.
- **Measured** ([results](results.md#a-hamlet-of-ai-residents), one run each way, then
  [three each way](results.md#three-runs-each-way-with-the-savages-fought-cuo-m3n4)): told where they live,
  the four came home 8 to 10 times an hour in every run, the control 0 to 3, and were in Cove a third to over
  half the hour (the control 1% to 20%). The talk didn't follow: 46 lines against 8 in the first pair, then
  23 to 41 against 3 to 37, as they talk wherever they meet. $0.06 to $0.07 a resident an hour.
- **Its lands** (cuo-m3n.3): `lands = ["south-west of Cove Cemetery", "near Cove Orc Fort"]` in `[hamlet]`
  names the grounds its residents work, each a hunting area of the world store (the square round its
  spawns, plus 10 tiles). The planner is told them as the hamlet's lands ("working there is part of life in
  Cove; between one piece of work and the next, come home"); each turn's situation says when a resident is on
  them ("on Cove's lands (south-west of Cove Cemetery), 115 tiles from Cove and 165 from home") and, after 5
  minutes away, how long it has been away from home. `hamlet-report` adds the share of the run in the hamlet
  or its lands, and how many of a resident's pieces of work (hunts, sales, purchases, banking, trips but the
  trip home) had it at home between one and the next. `fleets/hamlet-cove-lands.toml` is the four with their
  lands and their work put as short pieces with home between. Measured twice
  ([results](results.md#the-hamlets-lands-two-runs-cuo-m3n3)): in Cove or its lands 72% of the hour, as
  without them (66% to 81%), and home between pieces of work 4 of 15, with eight deaths to the savages by
  the orc fort; then, with the night's fixes, 81% (every resident 72% or more) and 9 of 20, with three deaths.
- **Its evenings** (cuo-m3n.5): `gather = "Cove healer"`, `gather_words = "the healer's house"`,
  `day_minutes = 60` and `evening_minutes = 15` in `[hamlet]` make a run days of an hour whose last quarter
  is the evening, on one clock for every resident (from the run's start). Each turn says the time of day ("day
  2, afternoon; evening (the gathering at the healer's house) in about 12 minutes"), and of an evening the
  planner is offered the `evening` goal (from 3 minutes before it begins: a resident that sets off early waits
  there for it, since asked a minute early every resident was told "it isn't evening yet" three to seven
  times running): the resident walks to the gathering place; the first there keeps the
  turns (a claim in the shared store, so two brains don't both host) once a neighbour has come for the evening
  (waiting up to 150 s, or 60 s once one has), and the others sit as they come. Only a neighbour who came for
  the evening since the last table closed counts, not one who lives at the house or sat at an earlier table
  and stayed. One who sits after the table opened is given turns and a line on tomorrow, and a keeper left
  with nobody answering closes the table; in the first run three tables of seven were one resident talking
  alone. It is the party's table
  ([the inn](#a-party-in-a-fight-on-the-road-and-at-the-inn)) in a hamlet scene: neighbours talking the day
  over, each from its own day (its planner's goals since the last evening), news, worries and tomorrow, a
  greeting to open and what each means to do tomorrow to close. Each keeps its own memories of it: up to three
  things learned about a neighbour (relationship memories) and a plan or two for tomorrow (events); nothing of
  it reaches the fights. Nobody is sent: one out hunting can miss it. `hamlet-report` counts each resident's
  evening goals, tables kept, lines said at them and memories kept. `fleets/hamlet-cove-evenings.toml` is the
  four with their lands and evenings, memory on, for three hours. Measured once
  ([results](results.md#evenings-at-the-healers-house-one-run-of-three-hours-cuo-m3n5)): every resident came
  to every evening; what they kept of each other was the day's news and each other's needs, and some of it was
  acted on (bread bought for two who hadn't eaten, bandages offered at the table and fetched).

## Without a window

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
