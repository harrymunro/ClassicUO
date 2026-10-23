# Goals and the planner

<sub>[← README](../README.md) · [all documentation](../README.md#documentation)</sub>

In auto mode the agent can work towards a goal you give it in words, for example "hunt
the undead at the Britain graveyard, keep yourself supplied with bandages, bank your
gold". Type it into the panel's goal box, pick a goal template, or use `-agent goal`.

- **The planner** is a chat model: Claude Haiku 5.5 through OpenRouter by default, the same key as Jev (`--profile sonnet` plans with Sonnet 5.5, which follows more of a goal at about forty times the cost; [Results](results.md#models-for-each-role)). Each time a goal ends, it sees the character's situation in words, your goal and what has happened so far, and answers with one tool call: a goal for code to carry out (`travel_to`, `hunt`, `bank`, `buy`, `sell`, `rest`, `set_strategy`, `set_machine`, `finish`) or a world-store query first (`place`, `find_place`, `hunting_spots`, `what_spawns`, `route`, `notes`, `outcomes`, `region_at`), or keeps a memory (`remember`, [the character's memory](world-and-memory.md#the-characters-memory)), searches it (`recall`), or changes a [long goal](world-and-memory.md#long-goals) (`long_goal`). Jev re-ranks the list answers against the planner's question ([which facts reach the decisions](world-and-memory.md#which-facts-reach-the-decisions)); the session summary counts those calls and their cost.
- **The character now:** what it plays as (warrior, mage, archer and so on, as the fights tell it), where it is, health, gold, weight, the supplies that matter for it, and mana. A mage isn't told of bandages, as it heals with spells. Reagents a bag holds that the client hasn't looked into yet are "not counted yet", and the first planner call waits up to 10 s for the client to open the pack's bags (it opens one a second; `unread_bags` in the snapshot). A new mage told of 0 bandages and no reagents was sent shopping for both before its party's fight (cuo-pz9).
- **No scripted loop:** the planner puts travel, hunting, banking and restocking together itself. Code carries out each goal ([below](#travel-banking-shops-and-hunting)); Jev keeps the fighting.
- **Routine calls are Jev's:** inside a hunt, whether to head back, stay or walk elsewhere in the spawn are quick Jev questions, not fixed thresholds and not planner calls ([Hunting](#travel-banking-shops-and-hunting)). The planner is called when a goal ends, including when Jev is unsure twice running, so open-ended choices (where next, what to buy) stay with it.
- **Cheap to call:** each step rebuilds a short prompt (a cached system prompt, the goal, the long goals, a summary of the session so far, the last 12 steps word for word, the memories Jev picked, the character now) instead of growing one long conversation. A call costs about $0.0005 with Haiku (about $0.01 an hour), $0.008-0.014 with Sonnet.
- **Sonnet for the hard moments:** Haiku plans, and a step goes to the stronger model (the profile's `escalate`, Sonnet 5.5 unless named) only when Haiku is stuck: no usable goal twice running, the same goal failing twice running (or any three in a row), code refusing to go back where it left (somewhere new to hunt is needed). Never two in a row, never over a budget. A death isn't escalated (since 2026-10-12, cuo-tyv): every model calls `resurrect` after one, 25 times of 25 in [the hard-moments comparison](results.md#opus-for-the-planners-one-off-calls), and code picks the healer; in the memory soak 13 of 22 escalations, about $0.025 each, came after a death. A `resurrect` that fails twice running is escalated as any goal is. Each is logged with its reason (records of type `escalation`) and costed under its own kind, `escalate`; the session summary counts them by reason. `--no-escalate` turns it off; the `npc` profile names Haiku for `escalate`, which turns it off too.
- **A running summary:** steps that leave the 12-step window are folded, four at a time, into a summary of the whole session (at most about 200 words, under the headings Done, Going on, Failed and why, Left to do, with place names and counts copied exactly) by one Haiku call (kind `compact`), so a long session keeps its arc while the prompt stays the same size. The same call is a memory flush: it names up to three things from those steps worth keeping in the character's [memory](world-and-memory.md#the-characters-memory) (`source` `flush`). When the session ends, the rest is folded and the summary is kept as a memory. Each fold is logged (records of type `fold`, with its tokens and cost); the session summary counts them.
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
[Unattended runs](results.md#unattended-runs).

## After a death

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
- **In a party that raises its fallen** (`raise = true` in a fleet file, off unless asked for), the
  ghost of a member whose companions are winning the fight waits by its corpse for one of them to
  bring it back there, and goes to the healer only if nobody can or nobody manages it:
  [raising the fallen](fleets.md#a-party-in-a-fight-on-the-road-and-at-the-inn).
- **Counted:** the session summary and `soak-report` count deaths, resurrections and corpses taken
  back, and the minutes spent on them; each death is kept in the character's memory.
- **With you driving** (combat assist), the brain does nothing for you after a death; the panel says
  where the nearest healer is and where your corpse lies, and again once you are alive.
- **By hand:** `uo-brain do resurrect`, `uo-brain do recover-corpse`.

## Travel, banking, shops and hunting

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
  - **Stuck:** no progress for 8 seconds counts as stuck. Progress is getting nearer the goal or further along the path: a way round a mountain range can lead away from the goal for minutes. The walker blocks that stretch and plans again (up to 5 times), in a wider box when the way round leaves the first one. When someone stands on the path ahead (a companion, a creature in a doorway) only the tiles they stand on are blocked, and when no way round is left it plans again with nothing blocked, since people move: blocking the next four tiles closed a room two tiles wide, and a party's leader leaving an inn gave up with "no way there on the map" in two runs of three (cuo-0jz). After a second with someone on its next tile, a walker turns to face them, as people do in a doorway. Impassable items seen on the way (barricades, blockers) stay blocked for the rest of the trip. A plan of a single step is walked like any other; the walker used to skip it and stand still until it gave up as stuck, which was 42 of the 61 stuck spots in the logs before. What a stuck walk rules out starts at the first tile it has yet to step onto: a plan begins at its first step, not under the character, and a walk stuck before that step used to rule out the tiles after it and plan through it again (cuo-4v1).
  - **Steps the server sends back:** the client's walker and the server don't always agree on a tile. In Felucca the client draws some plants as the season's dead wood and used to judge them by that art: a tuft of grass that is a surface a little above the ground, which the server lets nobody stand under, and every small palm, which the server lets nobody through, looked like open ground. The walker stepped at one such tuft below the Britain graveyard (1346,1512) about 60 times a second and was sent back each time, whichever way round the planner chose, since each leg is walked by the client's own pathfinder; trips there gave up as stuck on three days (cuo-4v1; [results](results.md#a-tile-the-server-lets-nobody-onto)). The walker now judges a static by what the map says it is, and the planner's own map steps onto a static's surface as the server does (cuo-5y8): onto a stair from within 2 of its base and onto anything else from within 2 of its top: it had taken the tuft for something to step up onto, 8 above the ground. A step is measured from the full top of what one stands on, though one stands half way up a stair (cuo-u4j: without that, nothing planned up the step into the Sweet Dreams inn in Britain). And whatever the cause, a tile the server sends a step back from counts as blocked to the client's pathfinder for 15 seconds while the agent is on, so the next leg goes round it after one refusal; the snapshot's `travel` counts them (`refused`), and a stuck walk says whose refusal it was (`stuck_why`: "the server refused 3 steps", or "the client's walker won't step there").
  - **On the game thread:** planning runs there, between frames. One plan searches at most 400,000 tiles; measured plans took 0.2–60 ms, most under 10 ([results](results.md#travel-and-errands)).
  - **Pace:** a trip is run unless it is asked to walk (`act travel … pace=walk`). A trip at a walk still runs while there is danger: the agent fighting, fleeing or stepping back, a hostile monster in war mode within 12 tiles, or anything attacking the character. It goes back to a walk 3 seconds after the danger was last seen. The snapshot's `travel` says the `pace` asked and whether the trip is `running` now.
  - **Following** (`AgentFollow`): `act follow target=<serial>` keeps the character within a few tiles of another (2 unless `distance` says otherwise) as it moves: on a free tile beside or behind it, never one somebody else stands on, and round a tree by the client's own pathfinder. It walks unless `pace=run`; at a walk it runs in danger as a trip does, and to catch up from more than 4 tiles behind until it is back in its place. While the agent fights, loots or flees the follow waits (`waiting`) and carries on afterwards. With its target out of view (18 tiles, or gone) it stands still and says `lost`, and picks the target up again if it comes back. A door that has shut behind the target is opened by a trip the follow plans for itself. A follower a step from its target that has stood still facing it for 1.5 s is in its way, and steps off its line (`making way` in the journal); a walker stuck behind someone turns to face them, so a companion on a leader's way out of a room moves aside (cuo-0jz). It ends on `stop`, a trip, `walk_to`, `move`, an errand, a death, or the mode leaving auto.
  - **On the way** the brain fights only what comes close or attacks, and doesn't seek or loot. A creature that can't move (a reaper, a corpser) isn't fought on a trip at all: the walk goes on past it, and if the client was already fighting it, the brain lets it go. Leaving a fight on a trip keeps to the road unless something is ahead on it; the brain then lets the creature go (`disengage`), because the walk waits while the agent is engaged. Before that, a warrior on the way to Cove left an ettin and a reaper at 1.0 for 35 s, standing beside them, and died. The same goes for resting, banking and shopping: every goal other than a hunt runs with Jev fighting beside it in this defend-only way. A soak run found the gap: a character resting at the graveyard was killed by a lich, with nothing fighting back.
  - Each walk goes into the world store as a route, with the spots where it got stuck.
- **Recall and Gate Travel:** on trips over 40 tiles, travel first looks for a marked rune or a runebook entry that goes there (by name, or by a name the world store places within 20 tiles), recalls (a mage casts Recall; anyone else uses a runebook charge), then walks the rest. A failed cast is tried once more, then it walks. The client reads each runebook once through its gump, without showing it, and answers the gump's entry button to recall or open a gate; a moongate's "dost thou wish to step in" warning is answered too. Gate Travel is there for the planner and the CLI (`act recall … kind=gate`, then use the gate).
- **Banking** (`bank`): says "bank" near a banker and moves items one at a time, checking each arrives (the server refuses moves that come too fast). Deposit `gold`, `loot` (anything that isn't kit or supplies) or named items (`bandage:150`); withdraw named items. It says "bank" every time, even with the box seen before: the server closes the box once the character walks off, and before cuo-rn2 every deposit after a session's first was dropped into a closed box and refused.
- **Buying** (`buy`): walks up to the vendor (ModernUO's vendors answer words only next to them; when two walks up don't end beside it, a townsperson in the one-tile way to it, the vendor's context menu is used instead, from up to 8 tiles: cuo-cgy), asks it by name ("Minerva buy"), reads its list and buys what's wanted and affordable. By name, because the server gives "vendor buy" to whichever vendor stands nearest the speaker within a tile, whatever it sells, and to that one only: at the Britain healer the healer guildmistress took the words whenever she stood nearer than the healer and showed nothing, and a soak run's first three buys failed with "the vendor did not show its goods" (cuo-k0d; [results](results.md#asking-a-vendor-by-name)). It asks by name twice (a vendor that steps away as it is asked doesn't hear), then once as "vendor buy", for a server that doesn't know its vendors by name; a name with "sell" in it isn't used to buy, since the server would read it as selling. A guildmaster in the shop is also passed over when choosing whom to ask: it sells nothing (cuo-rn2: a soak run's buys at the Britain healer went to the healer guildmistress on the next tile). A vendor only stocks so many (Britain's healer has 20 bandages at a time), so it goes round the vendors in sight, then the next shop. It walks to no shop more than 200 tiles away. Past that it stops and names the next shop and how far it is ("the nearest other healer selling bandage is the Cove healer, 770 tiles away"), and the planner decides whether the trip is worth it. With Britain's healer sold out, a buy had walked on to Cove: 6.6 minutes past a bog thing and a plague beast (cuo-1kk).
- **Selling** (`sell`): the same, from the vendor's sell list: `loot`, or named items.
- **Hunting** (`hunt`): walks to a spawn area and lets Jev fight there until time is up or one of Jev's routine calls ends it (`routine.py`). An area named by a place's name that is in several areas' names ("Cove Orc Fort": near, south of, west of it) is the one nearest that place, not the one with the most creatures; a hunt of the Cove Orc Fort had walked 813 tiles west to boars and bears (cuo-821):
  - **How long:** the minutes the planner asks for, 3 to 20, and never past the end of the session's own hours (`session --hours`): the planner looks at the clock between goals, and a ten-minute hunt chosen as a nine-minute run's time ran out once doubled the run. A party's followers get the same number, and each caps it by its own session too.
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

How these went on the local server: [Travel and errands](results.md#travel-and-errands) and
[Routine hunt calls](results.md#routine-hunt-calls).

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
fixed policy: [results](results.md#plans-for-fights); Haiku's plans against Sonnet's:
[results](results.md#haiku-or-sonnet-for-plans-reviews-and-guides).
