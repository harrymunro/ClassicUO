# Several agents on one server

<sub>[← README](../README.md) · [all documentation](../README.md#documentation)</sub>

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
  - **The leader's corpse** is fetched together. A corpse where danger was seen in the last 15 minutes is refused to a character on its own ([after a death](goals.md#after-a-death)); a party's leader goes anyway when companions are at the place (within 15 tiles of the corpse), or when two or more who came through that fight alive are beside it and none of the party had to leave the place, and then they go back there with it (its `recover_corpse` goal carries the place). Its planner is told which it is. Before this a resurrected leader waited in town "for the danger to age", the survivors beside it and the creatures long dead, until the corpse had decayed. A party that was wiped, or that lived by leaving (each member's report says where it last left creatures behind), has no such escort and waits as anyone would: a wiped party that went straight back all died there again, and a leader that went back with three who had been driven off met the same wave and died again.
  - **Not leaving alone:** with two or more companions within 10 tiles and health at 40% or more, a member's leave is held and it fights on with them ("held with the party: 3 companions beside it"); code's pack rule, weighed against the party, and a cautious strategy's badly hurt rule still leave (cuo-e1p). It fired 6 times in two runs and made no measurable difference: the party's deaths came in fights they were all in.
  - **Companions, not strangers:** in the fights each member's companions are named with what they play as (`your_party` in the state) and count in the odds ([leaving](playing.md#leaving-and-packs)); the planner no longer hears of them as "a unknown player 2 tiles away".
  - **The lich scene:** `fleets/party-lich.toml` (the party) and `fleets/party-lich-solo.toml` (a planner each), five classes staged south of the Britain graveyard with a lich, two bone knights and four skeletons waiting; `scripts/party_scene.py logs/fleet/RUN` says who reached the fight, who died, and how long the lich took to kill ([results](results.md#a-party-on-one-plan)).
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
