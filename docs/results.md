# Results

<sub>[← README](../README.md) · [all documentation](../README.md#documentation)</sub>

> [!NOTE]
> Most ran on the local ModernUO server; the routine-call test, the guide import and the
> review ran without the game. Apart from the judgment benchmark, which gives success rates
> over 5 or 10 rounds, these are single runs: treat them as a smoke test rather than a
> benchmark.

- **Fights:** [Arena](#arena), [Judgment benchmark](#judgment-benchmark), [Fewer fight calls](#fewer-fight-calls), [Plans for fights](#plans-for-fights), [Walls and summons in a swarm](#walls-and-summons-in-a-swarm), [Line of sight](#line-of-sight), [Where it runs](#where-it-runs), [When a leave stops](#when-a-leave-stops), [Real input](#real-input)
- **Models:** [Models for each role](#models-for-each-role), [Haiku or Sonnet for plans, reviews and guides](#haiku-or-sonnet-for-plans-reviews-and-guides), [Opus for the planner's one-off calls](#opus-for-the-planners-one-off-calls), [Sonnet for the planner's hard moments](#sonnet-for-the-planners-hard-moments), [Jev and Microsoft's Decision 1](#jev-and-microsofts-decision-1)
- **Sessions towards a goal:** [Unattended runs](#unattended-runs), [Travel and errands](#travel-and-errands), [Routine hunt calls](#routine-hunt-calls), [Calling the guards](#calling-the-guards-cuo-m3n14), [After a death](#after-a-death), [After-action review run](#after-action-review-run)
- **World knowledge and memory:** [World store and fact picking](#world-store-and-fact-picking), [UO Renaissance's map page](#uo-renaissances-map-page), [Map markers](#map-markers), [The planner with memories](#the-planner-with-memories), [Long-range memory over a long soak](#long-range-memory-over-a-long-soak)
- **Several agents:** [Several agents on one server](#several-agents-on-one-server), [Headless clients](#headless-clients), [A party on one plan](#a-party-on-one-plan), [A party that helps, calls out and talks](#a-party-that-helps-calls-out-and-talks), [A party that goes in together](#a-party-that-goes-in-together), [NPCs with lives of their own](#npcs-with-lives-of-their-own), [A hamlet of AI residents](#a-hamlet-of-ai-residents)
- **Public shards:** [Renaissance rules rehearsal](#renaissance-rules-rehearsal), [Attended play](#attended-play-cuo-49t)

## Arena

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
- **Recorded since 2026-10-08** ([what it costs](seeing-what-it-does.md#what-it-costs)): two rounds each of the `priority` and `loot` scenarios cost $0.0106 in 148 fight decisions, $0.000072 a decision, 277 ms on average (`uo-brain bench --scenarios priority,loot --judges jev --rounds 2`, single run, `brain/bench/2026-10-08-costs-check.json`). OpenRouter reports Jev's cost as exactly its input tokens at $0.042 per million; output tokens are free.

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

## Judgment benchmark

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

**Later the same day: running, packs and swarms** (cuo-d28.9, cuo-cvl.4; [above](playing.md#leaving-and-packs)
and [Mages](playing.md#mages)). The character now runs instead of walking, leaves a pack while it is
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
odds in words ([Mages](playing.md#mages)). 2026-10-08, lane 1, one client running; "before" is the code just
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
Results [below](#line-of-sight): 0/32 before, 32/32 after, rules and Jev alike.

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

## Models for each role

Which model to put in each role (cuo-m70.3), measured with [the chat adapter](seeing-what-it-does.md#which-model-answers)
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

## Haiku or Sonnet for plans, reviews and guides

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

## Opus for the planner's one-off calls

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

### One live death with the Opus profile

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

## Jev and Microsoft's Decision 1

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

### The default: system one split by kind

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
  bandage buys failed ("the vendor did not show its goods", cuo-k0d: the healer guildmistress
  took the words, [found and fixed since](#asking-a-vendor-by-name)), so it hunted 14 minutes on 30
  bandages; and after the gargoyles chased it out at minute 24 the planner took it east to goats,
  pigs and boars for the last half hour (7 kills and 53 gold a hunt), where Jev's hour had gone back to
  orcs and lizardmen.
- **A quarter cheaper than Jev's hour,** and no deaths. One hour, so a sign, not a measure.

On the benchmark the `jev` label still means Jev in every system-one call, so the results above stay
comparable; `profile` plays the active profile's own models, the split by default.

## Several agents on one server

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

## Headless clients

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

## Fewer fight calls

The fight loop that asks Jev only when its answer can matter and can be new ([how](../README.md#how-it-works),
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
- Run 1 was on the first version (before the defend-only rule; [how it works](../README.md#how-it-works)), run 2
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

## Plans for fights

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

## Walls and summons in a swarm

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

## Line of sight

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

## Where it runs

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

## When a leave stops

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

## Real input

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

## Unattended runs

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

## Travel and errands

On the local server, 2026-10-06, single runs with a warrior (the `uo-brain do` commands
[above](goals.md#travel-banking-shops-and-hunting)):

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

**From Cove to Britain, and back from the East Britain bank** (cuo-m3n.7), 2026-10-24. In this month's
fleet logs no trip from Cove to a place in Britain arrived but one: 7 stopped with "no route (no way
there on the map)" after their first hop, among them every trip from Brannoc's gatehouse by the east
gate. A long trip's hop heads for a point 300 tiles along the straight line, and from the gatehouse the
first box held only the Cove Orc Fort's valley, 142 tiles nearer that point and a dead end; the hop
took it, and from the valley no box holds the way out. From the square (2228,1191) the valley was only
85 nearer, not enough to take, so the box was widened and the hop went round the north (969 tiles): the
one trip that arrived, Tamsin's of 2026-10-19. A hop now takes the nearest place only when no wider box
holds a way to its point. The way back had its own fault: the first hop from the East Britain bank ends
at 1947,1419, and from there the last 299 tiles are planned whole. The second box held no way round (it
lies 30 tiles north of it), and searching all of that box spent most of the 400,000 tiles, so the
widest, which holds it, ran out ("too long a search"; the client before this change, the same). The
widest box now has a budget of its own. On the soak server (port 2595), one headless client, the plan
from each place by the `nav` RPC (`goal_x`, `goal_y`) after `[AgentGo` there, with the client before
the change and after:

| from, to | before | after |
|---|---|---|
| the gatehouse (2285,1209), the East Britain bank | first hop 203 tiles, into the valley | first hop 904 tiles round the north, 95 to 101 ms |
| the gatehouse, the West Britain bank | first hop 203 tiles, into the valley | 883 tiles, 82 ms |
| the square (2228,1191), the East Britain bank | 969 tiles | the same |
| 1947,1419 (the first hop's end from the East bank), the Cove healer | "too long a search", 113 ms | 971 tiles, 107 ms, 493,389 tiles searched |
| 2000,1380 and 1975,1400, the Cove healer | "too long a search" | 932 and 952 tiles, 106 and 128 ms |
| the forest west of Cove (2105,1235), the Cove healer | 808 tiles | the same |
| the West and East Britain banks, the Cove healer (first hops) | 322 and 300 tiles | the same |

From inside the valley (2143,1338) toward Britain there is still no way (known limits). Walked with
the brain's defend-only fight loop, on a copy of the world store, single runs:

```bash
uv run uo-brain --port 5580 do --shard m3n7-check travel "East Britain bank" --defend --mode auto --timeout 1500
uv run uo-brain --port 5580 do --shard m3n7-check travel "Cove healer" --defend --mode auto --timeout 1500
```

| trip | outcome | hops, plans | stuck spots | longest plan | Jev's fight calls |
|---|---|---|---|---|---|
| the gatehouse to the East Britain bank | arrived in 332 s | 1, 3 | 1 | 54 ms | 103, $0.0050 |
| the East Britain bank to the Cove healer | arrived in 322 s | 1, 2 | 0 | 79 ms | 98, $0.0047 |

Before the second change the trip back stopped with "too long a search" at 1947,1419 after 91 s. Two
walks with only the client's reflexes (no brain) went the same way round and died to what lives on it,
at 2084,996 (the bog thing north of the hills) and 1805,1389.

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

### Asking a vendor by name

2026-10-13, a copy of the local server (port 2595), a warrior, one to three clients running
(cuo-k0d). A soak hour's first three buys at the Britain healer had failed with "the vendor did
not show its goods", and the same buy worked 16 minutes later
([above](#the-default-system-one-split-by-kind)). The checks are `brain/scripts/buy_check.py`;
"before" is a copy of the client that soak ran (`bin/osx-arm64-next`). Every "before" run and the
first run after of each check were made with the script's first draft, the same steps under
another name; the later runs with the script as it is kept.

**Who answers which words** (`uv run python scripts/buy_check.py words logs/k0d-final-words.jsonl`):
the character is put on a tile within one of both the healer and the healer guildmistress, who
stand a tile apart, and says the words. Three tries each, and the same three of three in an
earlier run of it (`logs/k0d-who-answers.jsonl`):

| the character stands | says | the healer's list opened |
|---|---|---|
| nearer the guildmistress (beside her, the healer on the diagonal) | "vendor buy" | 0 of 3 |
| on the same tiles | "Minerva buy" | 3 of 3 |
| nearer the healer | "vendor buy" | 3 of 3 |

ModernUO hands what is said to those in earshot nearest first (`Mobile.DoSpeech` sorts them by
`LocationComparer`, in which a tile straight on is nearer than a tile on the diagonal), and the
first vendor within a tile to hear "vendor buy" marks it handled (`VendorAI.OnSpeech`). A
guildmaster is a vendor to the server that sells nothing (`BaseGuildmaster`, `IsActiveVendor`
false): it takes the words and shows no list. Words that begin with a vendor's name are answered
by that vendor alone.

**The client's buy errand from such a tile** (`... buy_check.py staged logs/k0d-staged-before.jsonl`
with the client before; after, five runs on each of four builds of the fix, the last of them
the client as it is now, `logs/k0d-last-staged.jsonl`): 5 bandages from the healer, the
character put where the guildmistress hears it first.

| | before | after |
|---|---|---|
| bought | 2 of 5 | 20 of 20 |
| the first ask answered | 0 of 5 | 20 of 20 ("Minerva buy") |
| time | 8.3 to 12.6 s | 1.8 s each |

The two the old client bought came on its second ask, 6 s on, after someone had moved. In 19 of
the 20 runs after, the client's own count had the guildmistress as near as the healer when it
asked (`nearer` in the errand); in the other she had stepped off as it asked. At another shop,
the Cove healer, with its guildmistress as near as the healer, "Abby sell" sold 52 bandages and
"Abby buy" bought 10, each on the first ask (`act sell` and `act buy` over the RPC, single runs).

**A soak's prep and first buy, over and over** (`... buy_check.py soak logs/k0d-before.jsonl --runs 5 --shard k0d`
twice with the client before; after, `logs/k0d-after.jsonl --runs 15`, `logs/k0d-final-soak.jsonl --runs 10`
and `logs/k0d-last-soak.jsonl --runs 5` on three builds of the fix, the last the client as it is
now): the prep of `soak_prep.py`, then the session's buy of 30 bandages, which walks the 100 tiles
from the bank to the shop. `k0d` is a copy of the world store, so other runs' stores were left
alone. Results in `brain/bench/2026-10-13-buy-check.json`.

| | before | after |
|---|---|---|
| bought all 30 | 8 of 10 | 30 of 30 (15, 10 and 5) |
| asks in a run | 1 in the eight that bought, 2 in the two that didn't | 1 in every run |
| seconds a buy, median (least to most) | 33.5 (30.8 to 81.0) | 31.8 (30.7 to 33.7) |

In both failures the guildmistress stood within a tile of the character, and nearer than the
healer, at each of the two asks; in every run that bought, the healer was the nearer or the
guildmistress out of reach (read from where the watcher saw them; the old client didn't say).
After, the client had the guildmistress as near as the healer at the ask in 4 of the 30 runs, and
all four bought. One of the two failures before also spent 42 s unable to get next to the
healer, the guildmistress standing in the one-tile way to her ([known limits](known-limits.md),
cuo-cgy); that wasn't seen after. The session's `buy` log line now names
each vendor it asked, how, and who else was as near (`vendors`).

**A vendor nobody can stand next to is asked by its context menu** (cuo-cgy), 2026-10-19 15:20. After two
walks up that don't end beside the vendor, an errand within 8 tiles of it asks for the vendor's context
menu and chooses Buy or Sell there (ModernUO's vendors answer those from 8 tiles); the menu isn't shown.
Staged at the Vesper healer: the character teleported 3 tiles from Derrick the healer and walled in with
`[AgentWall` (blockers on the eight tiles round it), so no walk could take it next to him. One run each,
`uv run uo-brain --port P do buy bandage 5 --vendor healer` (and `do sell bandage --vendor healer`):

| client | buy 5 bandages | sell bandages |
|---|---|---|
| before | failed after six walks up: "could not get next to the vendor" | |
| after | bought 5, one ask ("Buy from Derrick The Healer's context menu") | sold 205, one ask (by the menu) |

A shard with no context menus (a client older than they are, a server that turns them off) never sends
the menu, and the errand fails as before once its asks run out.

### A tile the server lets nobody onto

2026-10-13, the same server (cuo-4v1). In a soak on 2026-10-11 two trips west from Britain ended
"stuck" after six replans, every one at 1347,1512, below the wall of the Britain graveyard. The
world store's routes had the same from two more days: ten trips in all, at 2026-10-09 20:05 to
20:09 and 2026-10-11 01:32 and 21:42 UTC, each stuck six times on that tile
(`sqlite3 worlds/local/world.sqlite "SELECT from_name, to_name, stuck_at, duration_s, last_seen FROM routes WHERE stuck_at LIKE '%1347, 1512%'"`).

**The cause:** a tuft of grass. The map has a static 0x0CBA ("grasses") at 1346,1512 at height
18, over ground at 10, and the tile data flags it a surface. To the server (ModernUO's movement
rules) a surface between a walker's feet and head closes the tile, and this one is too high to
step up onto: every one of the eight steps onto 1346,1512 was sent back, and the two diagonal
steps past it from 1347,1512 (`act walk_to` from each neighbour, the `nav` view's `denied`
counting). In Felucca's season the client draws those grasses as dead wood (0x1B8D, in its
`seasons.txt`), which is no surface, and its walker went by what it draws: all eight steps from
1347,1512 were `ok` to it (`nav` with `steps`). Of that season's 72 swaps of statics, 8 change
whether a thing blocks or bears a walker: five grasses and a flower (a surface, drawn as wood or
twigs), the small palm (impassable, drawn as wood) and the sapling (open, drawn as a gravestone).
The map has 2,076 small palms outside the dungeons; at each of three tried (1130,2879, 1309,2863
and 1203,2931) the walker that read the season's art had the step onto it `ok` and the server
sent it back.

So the walker stepped west, was sent back, and stepped again: 496 to 512 refused steps in each 8
seconds before a replan (counted on the second client of the table below). No replan helped. The
planner's map takes the tuft for something to step up onto as well, each leg of a plan is walked
by the client's own pathfinder, which went through the tile whatever the plan, and the tiles a
stuck walk ruled out began one after the tile it couldn't step onto.

`uv run python scripts/travel_trip.py 5581 1250,1450,2 --go 1347,1512 --defend`, the second of
the soak's two trips, a client at each stage of the change (results in
`brain/bench/2026-10-13-stuck-tile.json`):

| client | trips | outcome |
|---|---|---|
| before | 2 | stuck six times in 48.1 s, never off the tile, both |
| with the stuck handling's fix alone | 2 | the same (48.1 and 48.3 s): the planner tried a first step north, south and east, and the legs still went west |
| and a refused tile left alone for 15 s (the season's art still read) | 3 | arrived in 23.0 s, one step refused, no stuck spot, all three |
| and a static read as the map has it (three builds, the last the client as it is now) | 9 | all arrived, no stuck spot: 22.8 s in the six with no fight on the way and no step refused; 33.1, 46.6 and 98.2 s with fights |

The soak's first trip, `... 1294,1482,3 --go 1425,1688 --defend` (the bank to the hunting ground,
272 tiles), arrived both times with the client before (70.6 and 74.2 s) and six times of six
after (73.4 to 74.2 s): its plan didn't cross the tile on these runs, so its failure on the day
wasn't repeated, only the tile's. Four more routes after, single runs, all arrived with no stuck
spot and no step refused: the bank to the graveyard (1384,1492) in 52.2 s and back in 50.7 s, and
the bank to 1300,1494 and to 1231,1494, the goals of the stuck trips of 2026-10-09, in 70.9 and
85.3 s.

After, the `nav` view at 1347,1512 has the steps west, north-west and south-west not `ok` and
the grass under `things`; within 40 tiles the planner and the client's pathfinder differ on 3
tiles, 12 before. The client's own `walk_to` from 1348,1512 to 1340,1512, which had stopped on
1347,1512, goes along the row above.

**The planner's map steps onto a static as the server does** (cuo-5y8), 2026-10-19 15:20. The planner's
grid let a walker step up 8 onto any surface, so the tuft (its base 8 above the ground) was somewhere to
stand in its plans. A static's surface is now stepped onto only from within 2 of its base, as ModernUO's
`Movement.Check` (`StepHeight` 2) and the client's pathfinder have it; the ground keeps its 8, for slopes.
The `nav` view's check (the grid's reach against the client's own walker's, 30 tiles round) at ten places,
the client from before and after, one headless client each, teleported there with `[AgentGo`. A `-` is a
tile the grid reaches and the walker can't; a `+` one the walker reaches and the grid can't.

| place (where the teleport put it) | `-` before | `-` after | `+` before and after |
|---|---|---|---|
| the tuft west of Britain (1347,1512, z 10) | 1 | 0 | 0 |
| the Britain inn (upstairs, z 17) | 79 | 0 | 0 |
| the Britain healer (z 20) | 3,018 | 2,812 | 0 |
| West Britain bank (inside, z 0) | 2,076 | 2,076 | 0 |
| Cove | 123 | 123 | 0 |
| the Britain graveyard | 3 | 3 | 0 |
| Minoc | 0 | 0 | 2 |
| Castle British (z 50), Britain Mine 1 (a mountainside), a spot by a Vesper bridge | 0 | 0 | 0 |

**...but from the top of a stair** (cuo-u4j), 2026-10-23. The rule above measured a step from where one
stands, and on a stair (a bridge tile) one stands half way up it: on the step at the Sweet Dreams inn's
south door (a stair block at z 10, 10 high, under one at z 15, 5 high), stood on at 15 or 17 and 20 at its
top, the floor at 20 was out of reach, so nothing planned into the inn ("no way there on the map", from
the street at 1487,1621; the client's walker went in). The server (`Movement.Check`: the top of what one
stands on, its full height) and the client's walker (`Pathfinder.CalculateMinMaxZ`: a bridge stood on
gives its base plus its full height) both measure from the top, and both step onto a stair from 2 below
its base and onto anything else from 2 below its top. The planner now does the same (`NavGrid.Reach`).
`uv run python scripts/nav_check.py 5679 bench/2026-10-23-nav-check-u4j.json`, one headless client,
with an eleventh place, the street west of that inn: there 4 tiles the walker reaches and the grid
doesn't, and 2,642 both reach (the whole inn among them). The Britain inn's 79 and the healer's 3,018 are
back: they are behind doors, which the check's walker doesn't open and a trip does. The first is the
building east of the Britain inn (1494,1656), up a step (z 22, 5 high: 27 at its top) and through a door onto a floor at
27; a trip there arrived (27 tiles, one door opened), as did trips from the street into the Sweet Dreams
inn, on to its innkeeper and back out. Every other place as above.

No tile the walker reaches was lost anywhere (`+` the same everywhere), stairs and the mountainside
included. The large `-` counts inside the bank and upstairs at the healer's are the walker's own reach
stopping at closed doors, which the grid passes at a cost; the change doesn't touch them.

**...and the foot of Vesper's bridge** (cuo-m3n.8), 2026-10-24. In the control runs of cuo-m3n.4 every trip
to the Vesper bowyer (2861,812; 9 trips) and to the armourer and weaponsmith beside it (2835,805; 3) stopped
41 to 63 tiles short with "no route: nowhere to stand there, or closed off". Those shops are reached over
a bridge from the south (x 2848 to 2852, from y 849), a ramp: a piece at z 0, 5 high (a bridge, stood on at
2), then each row a piece at the last one's base under one 5 higher. The planner's map had the ground under
the first piece as somewhere to stand too, nearer the street behind it than the piece's top, so every
step planned onto that tile went to the ground, and from the ground the next piece is out of reach. Its
only way on was past a lamp post at 2847,850 (an item, not in the map files); once the lamp post was in
view, a trip planned again and the goal's surroundings were a closed pocket. The client's walker climbed
it: on the soak server, `walk_to` up the bridge went z 2, 12, 21 and down to 2 at 818, none refused. Ground
with a surface lying on it that rises above it is now no place to stand. `uv run python scripts/nav_check.py
5580 bench/2026-10-24-nav-check-m3n8.json`, one headless client on the soak server (port 2595), and the
client before the change (`...-m3n8-base.json`), with a twelfth place, the foot of that bridge:

| place | `+` before (the walker only) | `+` after | `,` before | `,` after |
|---|---|---|---|---|
| Vesper, the foot of the bridge to its shops (2848,851) | 116 | 3 | 1,259 | 1,370 |
| the other eleven | as above | the same | as above | the same |

Single trips on the soak server after the lamp post was seen: from 2858,869 to the bowyer in 24 s and to the
armourer and weaponsmith's spot in 18 s, from 2864,846 to the bowyer in 22 s, each through one door, none stuck.

## Routine hunt calls

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

## World store and fact picking

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

## UO Renaissance's map page

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

## Map markers

Single runs on 2026-10-09 with UOAM's `Common.MAP` (870 markers; [where to get it](world-and-memory.md#filling-the-store)).
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

## The planner with memories

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
  block of their own that never touches the player's ([Goals and the planner](goals.md)).
- **Cost:** the picks added about 30% to the planner's cost ($0.025 against $0.083 for the 98
  moments), about $0.005 an hour at one planner call every three minutes.
- **Live:** in a 5-minute session on the soak server (`logs/6w8-3-check.jsonl`) every planner call
  showed four or five memories, all about the graveyard leaves of the last day, and later ones the
  refusal it had just hit.

## Long-range memory over a long soak

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

## Sonnet for the planner's hard moments

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

## A party on one plan

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

## A party that helps, calls out and talks

What a party does beyond following one plan ([how it works](fleets.md#a-party-in-a-fight-on-the-road-and-at-the-inn)),
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
  lead to follow and not a result. Followed at six runs each on 2026-10-13, with Decision 1 in the
  fights, it [didn't hold](#a-party-that-goes-in-together).
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
  three runs each: not enough to say walking in costs anything, and not ruled out. At six runs each
  on 2026-10-13 it was the other way round: [fewer leaves and fewer deaths](#a-party-that-goes-in-together)
  for the party that walks together.
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
  from the place ([the leader's corpse](fleets.md#several-agents-on-one-server)); not measured again since.

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

## A party that goes in together

The lich scene again ([how a party goes in together](fleets.md#a-party-in-a-fight-on-the-road-and-at-the-inn)),
2026-10-12 and 13 on 2593, headless, no personas or talk, every brain's memory off. Two things about
the scene changed first, so nothing here is for setting beside the sections above:

- **No standing strategy.** The five had carried a video shoot's "never flee: the party stands
  together" in their client profiles into every earlier party run, some with old planner lines after
  it. `party-lich.toml` now says `strategy = ""`.
- **A clean scene after a restart.** The first run after the server was restarted met two liches and
  five bone knights (waves left in the save) and all five died within a minute; it is not counted.
  `[AgentDisrupt restore` now removes them.

```bash
cd brain
uv run uo-brain fleet fleets/party-lich.toml --set memory=false                                # today's defaults
uv run uo-brain fleet fleets/party-lich.toml --set memory=false --set 'use="system1=jev"'      # Jev in every system-one call
uv run uo-brain fleet fleets/party-lich.toml --set memory=false --set form_up=true             # going in together
uv run python scripts/party_walk.py sample --ports 5661-5665 --out logs/fleet/RUN/where.jsonl  # beside each run
uv run python scripts/party_scene.py logs/fleet/RUN
uv run uo-brain party-report logs/fleet/RUN
```

The runs took turns (baseline, form-up, Jev, three rounds), nothing else on the machine but one idle
client, and a run in which a member never logged in was stopped and started again.

**Today's defaults against Jev** (cuo-cym.2). Decision 1 has made the fight calls since 2026-10-12 and
had never played a party run:

| three runs each | the default (Decision 1 fights) | Jev in every system-one call |
|---|---|---|
| reached the fight | 5, 5, 5 of 5 | 5, 4, 5 of 5 |
| members who died | 0, 0, 1 | 1, 1, 0 |
| the lich dead, from first sight | 14.1 s, 26.1 s, 17.2 s | 13.2 s, never, 12.3 s |
| the wave last seen, from first sight | 88 s, 112 s, 119 s | 74 s, 357 s, 207 s |
| leaves called in the run | 1, 3, 6 | 2, 15, 11 |
| seconds of the fight's first minute somebody stood apart from all the others | 23, 50, 17 | 33, 49, 51 |
| members who fell under a third of their health | 0, 0, 3 | 2, 2, 3 |
| what the run cost | $0.047, $0.029, $0.046 | $0.068, $0.056, $0.058 |

"Apart" is more than 10 tiles from every other member, the distance at which the odds stop counting a
companion as beside you.

- **A party plays at least as well on the default,** at about two thirds of the cost ($0.041 a run
  against $0.061; nine minutes, five characters). One death in three runs against two, and the lich
  dead in all three against two. Three runs each: a sign, not a measure.
- **Jev leaves more:** 2, 15 and 11 leaves a run against 1, 3 and 6. In its second run the lich was
  never killed, the wave was still there six minutes on and the archer died twice.
- **Both scatter.** With no standing strategy a member may leave, and in every one of the six runs
  somebody stood apart from all the others for 17 to 51 seconds of the fight's first minute.
- **The default stays as it is** for a party: nothing here argues for Jev in the fights.

**Going in together** (cuo-cym.1): `--set form_up=true` against the default, which by the end of the
night had six runs. The code changed between runs, each time for what the runs before it had shown, so
the form-up runs are given by version and no two versions are one sample:

| | runs | members who died | the lich dead, from first sight | first under 90% of health | seconds of the first minute somebody stood apart | leaves called | runs back to the rally point | members under a third of health |
|---|---|---|---|---|---|---|---|---|
| `form_up` off (22:26 to 04:02) | 6 | 0, 0, 1, 1, 0, 2 | 14.1 s, 26.1 s, 17.2 s, 17.0 s, 35.7 s, never | the warrior in 3, the archer in 2, the mage in 1 | 23, 50, 17, 34, 48, 44 | 1, 3, 6, 6, 6, 6 | none | 0, 0, 3, 3, 1, 3 |
| first version (22:40) | 1 | 0 | 16.7 s | the mage | 0 | 0 | 0 | 0 |
| second (23:24 to 00:19) | 3 | 0, 0, 0 | 24.0 s, 295.9 s, 24.7 s | the warrior, the tamer, the archer | 0, 45, 0 | 1, 10, 0 | 1, 9, 0 | 1, 2, 0 |
| third (00:40 to 03:30) | 3 | 0, 0, 0 | 26.7 s, 16.1 s, 20.3 s | the warrior in all 3 | 0, 0, 0 | 2, 0, 0 | 3, 0, 1 | 3, 0, 1 |
| fourth (04:43) | 1 | 1 (the tamer) | 16.1 s | the tamer | 7 | 0 | 3 | 2 |
| fifth (06:15 to 06:58) | 4 | 0, 0, 0, 1 (the warrior) | 22.3 s, 26.7 s, 26.6 s, 23.6 s | the warrior in all 4 | 0, 0, 0, 0 | 0, 0, 0, 0 | 0, 4, 1, 0 | 0, 2, 0, 2 |
| fifth, as the night ended (09:32 to 10:04) | 3 | 0, 0, 1 (the tamer) | 25.4 s, 14.1 s, 52.5 s | the warrior in all 3 | 4, 0, 0 | 1, 0, 2 | 4, 0, 2 | 3, 0, 1 |

- **What each version was.** First: the rally point, the word, the warrior first, a leave turned into a
  fall back. Second, after a review of the first found that "waiting" sent no action and a walk begun a
  second earlier carried on: holding stops the feet, and a hunt doesn't end under a party still fighting.
  Third, after the second's worst run (the lich alive for five minutes while the tamer, the mage and the
  archer walked forward to their own reach and were the party's front): the ranged members keep behind
  the warrior until he is in it. Fourth: the twelve findings of a second review. Fifth, after the
  fourth's run: a tamer's runs go to the rally point too, and a member with nothing of its own to fight
  goes to a companion that is set upon (below). The fifth's first run had that rule as first written,
  the next three as narrowed after its staged check, and the last three as narrowed once more (a tamer
  tending a hurt pet stays with it)
  ([what it does now](fleets.md#a-party-in-a-fight-on-the-road-and-at-the-inn)).
- **The party stays in one place.** Somebody stood apart from all the others for 17 to 50 of the first
  60 seconds in every run without it, and for none in twelve of the fifteen with it (the others: 45
  s in the second version's worst run, 7 s and 4 s). As the fight began the furthest member was 2 to 8
  tiles from the leader in twelve of them and 11 or 12 in three, against 9 to 11.
- **Who is hit first.** The warrior in three of six runs without it, and in all ten runs of the
  third and fifth versions, in which the others wait for him.
- **Three deaths in fifteen runs with it, against four in six without:** the tamer twice (below), and
  the warrior once, at the line with a bone knight and skeletons on him, from 56% of his health to 7%
  in half a second. The fights of the four staged checks below, which are ordinary form-up fights on
  the fifth version's code until the staging, lost the warrior twice more, the same way.
- **The danger is the warrior's now.** From the third version on the wave lands on him, as it is meant
  to: over a fight's first half minute 1.3 creatures stood beside him on average, against 0.7 in the
  first two versions, when the ranged members walked forward and were the front. Formed up, the party
  fights within a few tiles of its rally point (he stood 1 to 8 tiles from it), so falling back
  takes him a few steps and no further.
- **The fourth version's run lost the tamer,** to something every version had: a tamer's short runs
  (from a creature beside her, or when she calls a hurt pet back) went away from the creature and not
  toward the rally point. She ran three times in nine seconds with a bone knight after her, each time
  further from the party, and died out of everyone's reach while the others, their own fight over,
  looted. A tamer's runs go to the rally point now, her pet following.
- **The night's last run lost her again, another way.** Her pet was gone, so she had nothing to fight
  with, and the rule that keeps a member from leaving a party that is still fighting kept her standing
  at the rally point for eight seconds with a bone knight beside her, from all of her health to 22%.
  One with nothing to fight with and something on it isn't held now; that change is later than every
  run here.
- **It costs no more:** $0.030 to $0.052 a run against $0.029 to $0.054.
- **The first to act and the last are further apart with it** (2.1 to 8.5 s against 1.0 to 3.3 s):
  the others hold their fire until the warrior is in it. And fewer members get to the lich at all
  before it is dead (2 of 5 in the fourth version's run): the rest are on the skeletons.

**To a companion that is set upon** (cuo-cym.10), part of the fifth version, 2026-10-13:

```bash
cd brain
uv run uo-brain fleet fleets/party-lich.toml --set memory=false --set form_up=true &
uv run python scripts/party_stray.py --port 5663      # the archer's client; 5661 for the leader's
# the fifth staged run: out of the others' view, held there, and moved only once it has stopped looting
uv run python scripts/party_stray.py --port 5663 --hold 40 --tiles 24 --quiet 6 --idle
```

- **In the three runs of the rule as it is now** it decided 4, 7 and 2 of a run's decisions: the bard
  turning to the bone knight that was on the warrior (5 times), the tamer keeping or sending her pet
  at what was on the warrior (6), and once the case it was written for. In the third run's second
  fight the bard was looting, the archer out of his view with a bone knight on him. He walked toward
  where the archer had last reported, had the bone knight in view a second and a half later, closed
  to his song's reach and played Discordance on it. The archer, down to 32%, lived.
- **And once wrongly,** in the fight of the second staged run below: the tamer, bandaging a pet at 2%
  that she had just called back, was walked to within a few steps of the lich that was on the
  warrior, with nothing to send at it. A tamer whose pet is called back or under half its health
  keeps at it now, which the night's last three form-up runs had (the rule decided 2, 0 and 1 of
  their decisions).
- **The staged check couldn't make the case it was written for.** Once the fight was over one member
  was put 14 tiles from the others with a bone knight two tiles beyond it
  ([`[AgentGo` and `[AgentSpawn`](reference.md#test-server-commands)). The archer, and in a second
  run the leader, were among the others again within 2.5 s, well ahead of the bone knight: each was
  looting when it was moved, and its client walked it back to the corpse. The bone knight followed
  into the whole party and was dead 25 s and 17 s after it appeared; nobody fell. A third run, with
  the archer's own walking switched off, went the same way (looting is not walking, to the client).
- **A fourth held him** (looting off too), though only after he was back beside the bard. The bone
  knight reached him 9 s after it appeared and had him at 54% of his health six seconds later. The
  mage walked up from 10 tiles, cast at it and healed him twice, the tamer sent her pet, and it was
  dead 14 s after it reached him. They did that by their own pick, which was to fight: the rule acts
  only where a member would loot or rest, so none of those four put it to work. The bard walked away: the warrior had died in the fight, the party's goal had just
  become his resurrection, and a member on a trip is not sent anywhere by this rule.
- **A fifth made the case** (2026-10-13 23:56, `logs/fleet/20261013-235608-party-lich`, one run,
  staged). The archer was moved only once he had stopped looting, 24 tiles south of the others, and
  held there for 40 s with a bone knight two tiles beyond him. It was on him 10.6 s after the move
  and had him at 83% at 13.2 s. The bard, looting a skeleton's corpse, turned to him at 15.1 s ("to
  Wystan: set upon out of view"), the warrior, looting the lich's, at 16.8 s, and the mage at 17.4 s,
  each with a walk toward where he had last reported. The warrior was beside him at 20.8 s and
  fighting it, the mage's Explosion and the bard's Discordance landed at 22.2 s and 22.4 s, the
  warrior bandaged him and the mage healed him at 26.7 s, and the bone knight was dead at 28.4 s. His
  lowest was 55%. The tamer had him in view at 15 tiles throughout, so the rule wasn't hers: she
  went for the bone knight by her own pick and kept behind the warrior until he went.
- **What the first staged run did show** was the rule as first written doing harm in the fight before
  the staging: the mage, at 2% to 10% of her mana, had her rest (a trance, to have mana again) turned
  into a fight at every decision for 40 seconds, eleven Magic Arrows and seven decisions that cast
  nothing. And after it, the mage and the bard walked after the tamer, who was out of view and merely
  running, away from the bone knight the archer beside them was shooting. Both are excluded now.

**Callouts, and walking together, at six runs each** (cuo-cym.8). Two leads from 2026-10-11 rested on
three runs each, taken with Jev in the fights: with callouts the lich died sooner in all three, and a
party that walks its trips together called a leave more often. On today's defaults, `form_up` off, the
three conditions in turn through the night of 2026-10-13 (02:19 to 05:41; the baseline's first three
runs are the ones above):

```bash
cd brain
uv run uo-brain fleet fleets/party-lich.toml --set memory=false                        # callouts on, each walks its own trips
uv run uo-brain fleet fleets/party-lich.toml --set memory=false --set callouts=false
uv run uo-brain fleet fleets/party-lich.toml --set memory=false --set walk=true
```

| six runs each | the default | callouts off | walking together |
|---|---|---|---|
| members who died | 0, 0, 1, 1, 0, 2 | 1, 0, 1, 0, 2, 0 | 0, 0, 1, 0, 0, 0 |
| the lich dead, from first sight | 14.1 s, 26.1 s, 17.2 s, 17.0 s, 35.7 s, never | 12.6 s, 49.7 s, 10.5 s, 82.6 s, 12.1 s, 99.7 s | 15.2 s, 15.0 s, 227.5 s, 13.7 s, 24.5 s, 31.1 s |
| leaves called in the run | 1, 3, 6, 6, 6, 6 | 1, 3, 6, 5, 4, 3 | 0, 0, 3, 0, 0, 0 |
| the furthest member from the leader as the fight began | 9, 9, 9, 10, 9, 11 tiles | 9, 11, 9, 10, 9, 10 | 3, 4, 4, 3, 4, 4 |
| seconds of the first minute somebody stood apart | 23, 50, 17, 34, 48, 44 | 25, 43, 47, 53, 46, 27 | 0, 2, 28, 0, 0, 3 |
| first under 90% of health | the warrior in 3, the archer in 2, the mage in 1 | the archer in 3, the warrior in 2, the mage in 1 | the warrior in all 6 |
| members under a third of health | 0, 0, 3, 3, 1, 3 | 1, 1, 3, 1, 3, 3 | 1, 0, 2, 1, 0, 1 |
| what a run cost (mean) | $0.044 | $0.045 | $0.039 |

- **Callouts don't show in the lich's time to die.** The three fastest kills of the twelve runs were
  without callouts, and so were the three slowest; the one run in which the lich wasn't killed had them
  on. Four members died with them and four without. The lead of 2026-10-11 doesn't hold on today's
  defaults at six runs.
- **Walking together is the other way round from the lead.** 3 leaves in six runs against 28, one
  death against four, and nobody apart for more than 3 seconds in five runs of six. A party that walks
  its trips together is together when the fight begins, and a member with the party beside it doesn't
  leave.
- **It does most of what forming up does, on this scene.** The two are different ways to the same
  place: one walks the whole trip as a group, the other gathers at the edge of the hunt however each
  got there, and gives the party a place to fall back to. They can be on together.
- **One frozen copy of the code** (as it was at 22:26 on 2026-10-12) played all three conditions, so
  that they differ in the one setting. Two later fixes that reach a party with the new settings off are
  not in them: a hunt capped at the session's time, and which creatures count as coming when a member
  thinks of helping.

**One plan, three ways, with every brain's memory on** (cuo-cym.5), 2026-10-13, 05:41 to 08:44, three
runs each in turn (with other runs between them), on the fourth form-up version's code. By now each
character's own memory holds three days of deaths at this graveyard. The two party plans are formed up;
on 2026-10-11 the same three-way, without forming up, had one or two runs a cell
([above](#a-party-that-helps-calls-out-and-talks)).

```bash
cd brain
uv run uo-brain fleet fleets/party-lich-solo.toml                                  # a planner each
uv run uo-brain fleet fleets/party-lich.toml --set form_up=true                    # the leader's plan
uv run uo-brain fleet fleets/party-lich.toml --set plan=huddle --set form_up=true  # a planner each, with the huddle
```

| three runs each | a planner each | the leader's plan, formed up | the huddle, formed up |
|---|---|---|---|
| in the fight within 45 s of the first one | 3, 1, 2 of 5 | 5, 5, 5 | 4, 4, 3 |
| the first of those to the last | 14.1 s, alone, 4.7 s | 3.4 s, 3.6 s, 3.5 s | 6.4 s, 38.5 s (three within 4.5 s), 15.4 s |
| never came | 0, 2, 3 | 0, 0, 0 | 1, 1, 2 |
| members who died | 2, 1, 1 | 1, 0, 2 | 2, 0, 2 |
| the lich dead, from first sight | 157 s, never, never | 21.5 s, 17.3 s, 19.4 s | 18.9 s, 17.4 s, never |
| seconds of the first minute somebody stood apart | 55, 60, 51 | 2, 0, 11 | 60, 46, 60 |
| planner calls in the run | 38, 30, 22 | 8, 12, 10 | 47, 50, 44 |
| what the run cost | $0.152, $0.104, $0.071 | $0.053, $0.067, $0.055 | $0.075, $0.076, $0.072 |

- **The leader's plan, formed up, is the one that brings five to the fight:** all five within 3.4 to
  3.6 s of each other in every run, whatever each remembers, and the lich dead in about 20 s each time.
  Its one memory-on run of 2026-10-11, not formed up, had all five walk in, three die and the lich
  live. Memory still shows in what the planner does around the fight: in the second run it had the
  party rest three times at the healer before hunting, and in the third it ended the session after six
  minutes and two deaths.
- **The huddle's gathering works for those who set out together, and doesn't make them set out
  together.** Those who came were in the fight within 6.4, 4.5 and 15.4 s of each other, where on
  2026-10-11 the first were in half a minute before the warrior. But in every run one or two never
  came (shopping, or resting in town), and in the second the archer came 38 s behind the rest. In the
  third the bard reached the edge first, heard of nobody on the way in the four seconds the first one
  there allows, and went in as the party's point; the mage and the archer followed 9 and 15 s behind
  him, the warrior and the tamer not at all, and two of the three died with the lich alive.
- **A planner each scatters, as before:** 3, 1 and 2 of 5 in the fight in its first 45 s, the lich
  killed once in three runs, and the dearest of the three at $0.109 a run against $0.074 and $0.058
  (its planners ask Sonnet at their hard moments).
- **Where that leaves it:** as on 2026-10-11, with more behind it. The leader's plan is the one for a
  party that fights. What the huddle still lacks is a way to make five planners choose the same
  moment: the first at the edge waits for those who have said they are on their way, and a planner
  that chooses the hunt ten seconds later hasn't said so yet.
- **With memory off every planner chooses the hunt, and the huddle formed up has all five in the fight
  within seconds:** 7.0, 5.2 and 3.6 s from first to last in three runs
  (`--set memory=false --set plan=huddle --set form_up=true`; 01:09 and 01:20, before two fixes to the
  gathering, and 09:22 on the night's last code), the lich dead in 62, 22 and 31.5 s, deaths 0, 0 and
  1. On 2026-10-11, not formed up, the others were in half a minute before the warrior. In the last of
  the three the bard was first at the edge and was made the party's point, with the warrior, whose
  planner had him walking there, a second behind.
- Three runs a cell, on the fourth form-up version's code (the fifth's changes are in none of them),
  and every run added to the memories the next one read.

**Everything on at once,** one run, 2026-10-13 08:52: the talking party (`fleets/party-inn.toml`:
personas, talk, walking together, roleplay, the inn, memory on, 28 minutes) with forming up, the game's
own party, raising and callouts both aloud and in party chat, on the code as it stood at the end of
the night.

```bash
cd brain
uv run uo-brain fleet fleets/party-inn.toml --set form_up=true --set game_party=true --set raise=true --set 'callouts_to="both"'
```

- The party was made in the game (four invited, four accepted) and formed up for both its hunts, all
  four others closed up on the leader each time.
- Four fights, the second a lich and three skeletons at the graveyard: nobody hurt below 85% of their
  health, nobody dead, and somebody apart for 5 seconds of one ten-second fight.
- 30 callouts (where each was heard wasn't checked in this run), then the inn: 20 lines from all five
  at the table and 30 memories kept of it.
- $0.12 for the run. Nobody fell, so nothing was raised: the settings were on together and nothing
  got in anything else's way, which is all one run says.

**The game's own party** (cuo-cym.3), single runs on 2026-10-13, headless, memory off:

| what | command (beside `fleets/party-lich.toml --set memory=false --set form_up=true`) | result |
|---|---|---|
| the party is made | `--set game_party=true` | the leader invited the four one at a time and each accepted: all five in the game's party 32.5 s after the first invitation |
| looting a companion's kill | the same run, against the run before it without | "Looting this monster corpse will be a criminal act!" was among the recent events of no decision in the run; in the run before it, of 88 decisions, by all five members |
| callouts in party chat | `--set game_party=true --set 'callouts_to="party"'` | the five logged 29 callouts; a probe on the mage's client heard 29 party-chat lines from them and none aloud |
| a stranger's invitation | `uv run python scripts/party_player.py --stranger talk2:5665:Corwin` | the two companions it invited declined ("Thea : Does not wish to join the party."), then all three accepted their leader's |

- **A party outlives a logout into the next run.** A character whose client is closed stays in the world
  for a while, so the party made at 00:50 was still there in the four runs started straight after it,
  loot rights and all (no such warning in any of them; the measured runs above were all earlier). `[AgentReset` now
  takes a character out of any party, so every run starts without one.

**Raising the fallen** (cuo-cym.4), 2026-10-13, two runs, memory off, the death staged
([`[AgentSlay`](reference.md#test-server-commands) said by the archer 20 s and 9 s into the fight;
nothing in the fight killed him). The second was run windowed with the screen recorder on, though it
recorded nothing.

```bash
cd brain
uv run uo-brain fleet fleets/party-raise.toml --set memory=false &   # form_up and raise are on in the file
uv run python scripts/party_slay.py --port 5663                      # the archer's client
```

| from the archer's death | walked to the healer (four deaths in the runs above) | raised where he fell (two runs) |
|---|---|---|
| on his feet again | 37 s, 41 s, 57 s, 58 s | 63 s, 73 s |
| healed enough to go on | 120 s more each time (the wait's limit: alone, his bandages on his corpse) | 12 s, 30 s |
| his things back from his corpse | 193 s, 205 s, 235 s, 237 s | 77 s, 104 s |

- **Less than half the time to be back with everything,** with a healer 40 tiles from the fight; the
  healer is rarely that near a hunt.
- **The ghost waited by his corpse in war mode** for the fight to end (40 s and 44 s long), and the
  party won it without him both times.
- **First run, one raiser at a time:** the mage cast Resurrection as the fight ended and four times
  more, and her fifth cast worked; the warrior, who could have bandaged him, stood by because she had
  called it. Changed after that run: everyone who can raise tries.
- **Second run, both at it:** seven casts and four bandages before one worked ("raised where it fell by
  Isolde and Roland": the ghost can't tell whose try it was). A try is said to work one time in four at
  these skills; 2 of 16 did.
- **The first try at staging it failed:** the archer placed 26 tiles ahead to meet the wave alone walked
  back to his leader in six seconds, before the wave was spawned, and nobody died.

**A party for the player** (cuo-cym.6), 2026-10-13: `uv run python scripts/party_player.py`, twice (the second
with `--stranger`). A script plays the leader's part with walks, attacks and three lines of speech; the
three companions are agents.

| | first run | second run |
|---|---|---|
| companions beside the leader | 29 s after it logged in | 29 s |
| furthest companion while the leader walked (18 samples) | median 4 tiles, most 5 | median 4, most 6 |
| a bone knight and three skeletons spawned ahead | all three in the fight within 0.1 s of each other; over in 18 s | within 0.3 s; 25 s |
| the leader (no brain, bandaging itself by reflex) | down to 22% of its health; bandaged by them 3 times | 46%; healed or bandaged twice |
| "Stay here.", then 12 tiles away | all three stayed | all three |
| "Follow me.", then back to the start | all three beside it | all three |
| cost | $0.006 | $0.008 |

**The player's table**, one run, 2026-10-13 08:44 (`uv run python scripts/party_player.py --fleet
fleets/party-player-talk.toml --table`: the same script with personas and talk, and after "Follow
me." the leader says "Sit down, drinks are on me.", puts one question to a companion half a minute
in, and two minutes later says "Right. Follow me."). The tamer's client never got past the login
screen, so this is two companions, the mage and the archer, and the run is void for any measure:

- **Both took "sit down" as the table,** 3 s after it was said, and the mage kept it: 13 lines in 1.6
  minutes, six from each of them and the leader's question, through toast, stories, banter and a
  lesson each.
- **They talked to each other about the fight they had just had with it,** and to the leader by name
  once it had spoken ("Roland, I'd have shot the skeleton again just to watch Isolde wave her hands
  at it."). Each kept six memories of the table, a lesson among them ("Stay behind Roland's wall and
  shoot only the single skeleton that's already pulled").
- **"Right. Follow me." had both beside it again.** Before the table: in the fight within 0.1 s of each
  other, the leader down to 33% and healed or bandaged five times, both stayed at "Stay here.", both
  came at "Follow me.".
- The run cost $0.016, the table $0.005 of it.

**Orders in the leader's own words** (cuo-cym.15), 2026-10-13. A companion took its leader's three
orders by matching a short list of phrases; now each line the leader says is a question for system
one ([how](fleets.md#a-party-for-the-player)). First without the game, one run:

```bash
cd brain
uv run python scripts/orders_check.py --out bench/2026-10-13-orders.json
```

Lines a leader might say, each read for three companions (Isolde, Wystan and Thea, led by Roland): by
the phrases, and by each model through the question a companion asks, an order at 0.6 or more. A
reading is right when it is the order written down for that companion beforehand: come along, wait,
sit down, or none.

| | the phrases | Jev | Decision 1 |
|---|---|---|---|
| first set, 84 lines, 252 readings: written with the question, and the cut chosen on it | 150 (59.5%) | 245 (97.2%) | 252 (100%) |
| **second set, 53 lines, 159 readings: written after that, put to the models once** | 88 (55.3%) | 144 (90.6%) | 149 (93.7%) |
| second set: orders read as talk | 62 | 0 | 3 |
| second set: talk taken for an order | 6 | 15 | 4 |
| second set: one order taken for another | 3 | 0 | 3 |
| a question (median) | none | 0.22 s, $0.00003, 722 tokens in | 0.27 s, $0.000016, 387 tokens in |

- **The second set is the one to go by:** shorter and looser, as people type in a game ("w/me", "lets
  roll", "hold here pls"), some said to companions who are waiting. The first set shaped the cut.
- **The phrases read an order said any other way as talk:** none of the first set's twelve ways to
  say "come along" (0 of 36), and "Wystan stay, the rest of you with me" as an order for Wystan alone.
- **Decision 1 got four lines of the 53 wrong:** "w/me" was talk to all three; "Park yourselves, I'm
  buying." an order to wait where it meant the table; "wait for it..." an order to wait; and "Come
  on, you beauty!" an order to come along for one of the three.
- **Jev missed no order in either set and took talk for an order more often:** "Follow the money, as
  they say.", "wait for it...", "Come on, you beauty!", "Still there? Good." (said to companions
  who were waiting) and, for the two it wasn't said to, "Not you, Wystan. You wait.".
- **So the kind is Decision 1's,** as system one is by default; `--profile jev` reads with Jev.
- **The cut:** at 0.5 Decision 1 took "Hold on, what did you just say?" for an order to wait (0.52)
  in the first set, so 0.6. On the second set 0.5 and 0.6 give it the same 149, 0.7 gives 147 and 0.8
  gives 144.
- **One answer was changed after the first run.** "Everyone but Thea, come on." had "none" written
  down for Thea, and both models read "wait" for her, which is what the line means: left to follow as
  before, she comes along. Counted as first written, the first set is 251 for Decision 1 and 244 for Jev.

Then live, the leader's part played by the script with its orders said as a person might
(`uv run python scripts/party_player.py --own-words`, 11:13, the second of two runs; three companions
without talk, Decision 1 reading):

| the leader said | read by | what they did |
|---|---|---|
| "Right, on we go. Keep up." | two as come along (1.00); the third's brain started after it | all three beside it 18 tiles north |
| "You'd have stayed in bed if I'd let you." | all three as no order (0.99) | went on with it, and into the fight (a bone knight and three skeletons, 26 s) |
| "Hold up a moment, I'll scout ahead." | all three as wait (1.00) | all three stayed while it walked 12 tiles off and back |
| "All clear. Come along, you lot." | all three as come along (1.00) | all three beside it back at the start |
| "Wystan, hang back here a moment." | Wystan as wait (1.00), the other two as no order (0.99) | Wystan stayed; Isolde and Thea walked the 12 tiles with it |
| "Wystan, catch up!" | Wystan as come along (1.00), the other two as no order | all three beside it 3 s later |

- **Every reading was the one meant,** 17 of 17, each within 0.8 s of the first companion's (a look a
  second); a question took 0.39 s at the median with the three clients running, 0.54 s at most.
- **Reading cost $0.0003** of the run's $0.0069; the rest was the fight.
- **The run before it (11:09) read its 18 lines the same way,** and the script was wrong, not the
  companions: it told Wystan to hang back while the other two were still waiting from "Hold up a
  moment", so nobody came along. The order to follow again now comes first.
- **The third companion missed the first line** because the run began three minutes after the last:
  her character was still standing there from it, the script counted her as beside the leader, and her
  brain started after the line was said.

The same with the companions who talk, and the table (`uv run python scripts/party_player.py --fleet
fleets/party-player-talk.toml --table --own-words`, 11:20, one run): all 27 readings as meant.

- **"Who fancies an ale? Take the weight off." was the table** for all three (0.98 and 0.99): 12 lines
  in two minutes, with the leader's question to Wystan half a minute in read as no order (0.99).
- **"Right, we should get moving." ended it** (come along, 1.00, asked as the question for the table),
  and all three were beside the leader 8 tiles on.
- The run cost $0.016: reading the lines $0.0004, the table $0.005, the fight $0.007.

**Talking with the companions** (cuo-cym.16), 2026-10-13. In that run nobody answered any of the four
things the leader said to the party on the road: the yes/no before a line (kind `chat`, Decision 1)
gave "You'd have stayed in bed if I'd let you." 0.25 to 0.41 and "All clear. Come along, you lot."
0.07 to 0.11. One answer came, to an order by name, 9 s after it: "Hang back? Fine by me, Roland.
Wall's between me and those bones." "Wystan, catch up!", said four seconds after that answer, was never
considered: he had spoken within 20 s. The yes/no and the gap were made for agents talking to agents. The
yes/no put to the models without the game, as it was and as it now is for a leader a person plays:

```bash
cd brain
uv run python scripts/answer_check.py --out bench/2026-10-13-answer.json
```

39 lines the leader might say, each asked as the three companions of `fleets/party-player-talk.toml`
would ask it, a yes at 0.5 or more. Lines as wanted, of those in each group:

| | Jev, as it was | Jev, for a person | Decision 1, as it was | Decision 1, for a person |
|---|---|---|---|---|
| said to the party (12): somebody says yes | 10 | 12 | 7 | 12 |
| said to one by name (8): that one says yes | 8 | 8 | 8 | 8 |
| following on from one's answer, no name in it (6): that one says yes and nobody else | 6 | 6 | 5 | 6 |
| nothing to answer (8: "brb", "lol", "hmm"): nobody says yes | 4 | 7 | 8 | 8 |
| meant for the others, straight after one's answer (5): another says yes, that one doesn't | 0 | 0 | 0 | 0 |
| **all 39** | 28 | 33 | 28 | **34** |
| a question (median) | 0.23 s, $0.00003 | 0.23 s, $0.00004 | 0.28 s, $0.00002 | 0.27 s, $0.00002 |

- **As it was, Decision 1 left five of twelve lines to the party with no yes from anyone:** "Anyone
  hurt?", "Good evening, all.", "That was a good fight. Well done, all of you.", "I've never seen so
  many skeletons in one place." and "This graveyard gives me the creeps."
- **For a person, every line to the party had all three at 0.97 or more,** and one answer is given:
  the first to claim it.
- **A line that follows on is its own companion's.** "And your reagents?" after Isolde's answer:
  Isolde 1.00, the other two 0.04 and 0.02. The yes/no is told whose answer the line follows, in both
  columns; before, such a line was anyone's.
- **A line meant for the others goes to the wrong one, 0 of 5.** "What did the rest of you make of
  it?" after Isolde's answer: Isolde 0.99, the others 0.38 and 0.20. The same words that give her
  "And your reagents?" give her this. Told instead to judge it from the line
  (`bench/2026-10-13-answer-third.json`), the yes/no got these five wrong another way (the one who
  had just answered still said yes, or nobody did) and one of the six that follow on besides, so the
  plain words stayed. Who a
  line is for wants one question with the party as its options, not a yes/no from each (cuo-cym.17).
- **Jev says yes to some of what has no answer:** "oops, wrong key" (about 0.6) with either
  question, and its answers near 0.5 move from run to run (7, 6, 6 and 4 of these eight as wanted
  with the old question, over the four runs). The kind stays Decision 1's.
- **Four of the eight lines with nothing to answer are the question's own examples** ("brb", "lol",
  "ok", "one sec"); Decision 1 gave the other four 0.02 to 0.15.
- **Orders** (three lines, not counted: each companion has already said its word): two or three of
  the three now say yes to each, so an order may get a line in character after the word.
- **Run four times as the lines grew.** 28 lines first (25, 25, 23 and 28:
  `bench/2026-10-13-answer-first.json`); then the six that follow on, with the words about them in
  the question (30, 33, 28 and 34 of 34: `-second.json`); then the five meant for the others, with
  the wording that judged them from the line (`-third.json`), and last with the plain wording back,
  which is the table above. Nothing else in the question was changed after its first run.
- **The bench's states are barer than a live one's.** Live, the old question gave "You'd have stayed
  in bed if I'd let you." 0.25 to 0.41; here, with nothing said before it, about 0.75.

Then live (`uv run python scripts/party_player.py --fleet fleets/party-player-talk.toml --table
--own-words`, 11:45, the second of two runs with the new talk; the script now also talks with them):

| the leader said | who answered, and after how long |
|---|---|
| "Isolde, how are you holding up?" | Isolde, 9 s: "Upright, Roland, and still annoyingly unsinged. Ask me again after the bones." |
| "And what did you make of that bone knight?" (2 s after her answer, no name) | Isolde, 9 s: "That knight took three Fireballs and a bear's worth of nerve, Roland." The other two gave it 0.05 and 0.04. |
| "Isolde, run back to town and buy me some bandages, would you?" | Isolde, 10 s: "Buy your own bandages, Roland. I have a reputation to keep and reagents to count." |
| "What did the rest of you make of it?" | Isolde again, 9 s (0.99; the other two 0.12 and 0.18): the line meant for the others, as measured above |

- **Ten answers to the leader in the run, 8.2 s after its line at the median** (6.5 to 9.9 s): the
  time a person takes to read and type, with the model's 1.3 s inside it.
- **The errand was turned down both times it was asked** (11:30: "Buy your own bandages, Roland. I'm
  not your errand girl, though I'd stay for the wall."), and read as no order by all three (0.98 to
  1.00).
- **An order now gets a word from each in its own voice,** and often a line after it. To "Hold up a
  moment, I'll scout ahead." (11:30): "Stay here? Me? Only because you asked nicely." (Wystan),
  "Fine, I will wait here and count the bruises I did not get." (Isolde), then Wystan again 7 s on:
  "Scouting's your business, Roland. Mine's the arrows, so I'll cover you here."
- **All 39 readings of 13 lines were the ones meant,** the table and the order to leave it included.
- **The run cost $0.018:** the fight $0.007, the table $0.005, 15 lines of talk $0.004, 43 yes/no
  $0.0012, 39 readings for orders $0.0006. The voice packs with the new words were written once in the
  run before, $0.0014 a character.
- **Companions had been looting the player's kills since they were built** (10 to 16 loot decisions a
  run in six runs, last night's included), though the guide said they take nothing: the fight loop
  lays the strategy's settings over its own, and with no strategy those say "valuables". Seen here
  because two who had been told to wait walked five tiles to a corpse. Fixed after this run
  (`strategy.apply`).
- **The script's "stayed" was wrong too:** it counted who was no longer within 6 tiles of the leader,
  and one standing where the leader walked to was beside it without having moved (this run's report
  says only Wystan stayed). It now measures how far each moved.
- **Run once more with both put right** (11:57, the same command): told to wait, none of the three
  moved a tile while the leader walked 12 off; with Wystan alone told to hang back he moved none and
  the other two 11 and 10. No loot decision in 196. All 39 readings as meant, ten answers to the
  leader 8 s after its line at the median (5.9 to 12 s), the errand turned down a third time ("Buy
  your bandages yourself, Roland. I'm not your errand-girl, and I'm staying put."), and "What did the
  rest of you make of it?" answered by Isolde again. $0.018.

**Played by a person for the first time,** 2026-10-13, 21:18, one run of five minutes: Harry as Roland
in a client with a window (`bin/osx-arm64-cym3`), the three companions without one. The command as it
was given to him: `UO_CLIENT_DIR=bin/osx-arm64-cym3 uv run uo-brain fleet fleets/party-player-talk.toml --hours 1`
(`logs/fleet/20261013-211845-party-player-talk`; its record has the hour and the windowless clients,
not which build they ran from). The run ended without its summaries, so these are counted from the
records. What he found is in beads cuo-cym.18 to .22.

- **He said 16 lines and 8 were answered, each by one companion:** Wystan the first three, Thea one,
  Wystan one, Isolde the last three. "So crew how are you all today?" and "So, what do you all fancy
  doing next?" got one answer each, the other two giving way. Three more answers were written and
  dropped because he had spoken again before they were due.
- **The answers came 6.3 s after his line at the median** (4.6 to 9.0 s), against 8 s in the scripted
  runs: the pace counts the length of the line heard and of the answer, and his lines were shorter
  than the script's. He asked for about a fifth less.
- **Asked to loot, nobody did or said anything:** "feel free to loo", "loot" and "Please go ahead and
  loot" were each read as no order by all three (0.90 to 0.97), and the yes/no on answering gave 0.10
  to 0.22. There is no such order. "cover me" was read the same way (0.85 to 0.89).
- **Sent to the inn without him, nobody went, and what was said didn't match:** "Right - off to the
  inn for you all - see you there" was read as come along by all three (0.99), which they were already
  doing, and Isolde answered "Off to the inn, Roland? Finally, a sensible order from you. Lead on."
  "I'll follow you" was read as no order, and she said "Following me, are you? Wise. Someone in this
  party ought to know the way." and stood still. "Off you go then" was come along again (0.96).
- **In the fights they did as in the scripted runs:** 14 callouts over a zombie, a spectre and
  another zombie, and each of the three called that it was seeing to his wounds ("Hold still, Roland,
  this'll sting a bit.").
- **Two of his 16 lines were read as an order, both wrongly** (the two above). The 48 readings took
  0.40 s each at the median (0.25 to 0.69 s).

**What the playtest asked for** (cuo-cym.18 to .23), 2026-10-13, 21:50 to midnight: a companion's
settings changed by the leader's word, a fourth order (go somewhere without the leader), a word on an
order already in force, an answer to a request it doesn't carry out, an answer from each a line is
for, and answers a fifth sooner ([how](fleets.md#a-party-for-the-player)). Each of a companion's readings of a line is a question for
system one, so each was put to the models without the game first, as the day's first two were:
lines with the reading wanted written down beforehand, a set the question was written with, and a
set written after that and run once. Decision 1 and Jev both, each line as three companions
(Isolde, Wystan and Thea, led by Roland) would ask it.

```bash
cd brain
uv run python scripts/orders_check.py --out bench/2026-10-13-orders-asked.json
uv run python scripts/settings_check.py --out bench/2026-10-13-settings.json
uv run python scripts/answer_check.py --no-yes-no --out bench/2026-10-13-answer-asked.json
uv run python scripts/go_check.py --out bench/2026-10-13-go.json
```

Two earlier runs of the evening are kept beside them: `bench/2026-10-13-orders-go.json` (the orders
before requests were read) and `bench/2026-10-13-answer-for.json` (who a line is for, with the yes/no
alone beside it).

*Orders, with the one to go somewhere, and requests.* The order question has a fifth option now (go
somewhere without the leader), and the question about settings rides in the same request. Beside it,
in a request of its own, goes a yes/no: is this line asking this companion to do something? A line
with no order in it that asks for something all the same ("cover me") is a request, and the companion
answers it with what it will and won't do. Readings as written: an order at 0.6 or more; a request
when no order is taken and the yes/no gives 0.7 or more; a line that speaks to a setting is left to
the settings' questions below. The run of 23:40:

| | lines, readings | the phrases | Jev | Decision 1 |
|---|---|---|---|---|
| first set, as on the morning of 2026-10-13 | 84, 252 | 146 | 248 | 250 |
| second set, unseen that morning | 53, 159 | 84 | 148 | 148 |
| third set: being sent somewhere and the playtest's own lines; the new wording was settled on it | 40, 120 | 69 | 113 | 115 |
| **fourth set: written after that; the order question wasn't changed on it** | 34, 102 | 47 (46.1%) | 90 (88.2%) | **92 (90.2%)** |
| fourth set: orders read as talk; talk taken for an order; one order taken for another | | 37; 9; 9 | 4; 8; 0 | 4; 3; 3 |
| fifth set: requests; the yes/no's wording was settled on it | 14, 42 | 21 | 38 | 39 |
| sixth set: requests, written before the yes/no was a question of its own | 16, 48 | 25 | 42 | 45 |
| **seventh set: requests, talk that sounds like one, orders with a request in them; written after the last change and run once** | 19, 57 | 34 (59.6%) | 49 (86.0%) | **49 (86.0%)** |
| seventh set: requests read as requests; talk left as talk; orders | | | 17 of 21; 23 of 27; 9 of 9 | 16 of 21; 24 of 27; 9 of 9 |
| a reading (median): the order with the settings question; the yes/no beside it | none | 0.22 s, $0.000075; $0.000029 | 0.31 s, $0.000056; $0.000016 |

- **The fourth order didn't cost the first three:** Decision 1 read the two sets of the morning 250 and
  148 as written, against 252 and 149 then. Not the same lines: "wait for it..." and "Come on, you
  beauty!" are talk now, and "lets roll" (0.29 to 0.47), "w/me" (0.30 to 0.45) and, for two of three,
  "ok lets move" (0.53 and 0.56) fall under the cut. Lines near the cut move a reading or two between
  runs. One that is nearly an order and asks for something ("ok lets move": the yes/no 0.93) gets a
  question back from the companion, what is wanted, in place of silence.
- **Decision 1 on the fourth set:** all 39 readings of talk right ("We should go to the bank at some
  point.", "He went off to the inn and never came back.", "ok", "right"), 30 of 33 of being sent.
  Wrong: "yes, there" (after "Go and wait for me at the bank." / "The west one?") was talk to all three;
  "Nearly there, save me a seat.", said to companions waiting at the inn, was an order to go to all
  three; "come back to the graveyard" was being sent there for all three (which takes them to the
  leader all the same); "Off we go again." was talk for one (0.50).
- **The fourth set has been put to the models five times,** as the rule for taking an order changed
  (next point) and not the question: 91, 89, 92, 92 and 92 on Decision 1, and 88, 87, 92, 92 and 90 on
  Jev.
- **A line that points back at what was said is the weak place:** the state now carries the talk just
  before the line (`said_before`), which reads "Off you go then" and "Go on, then." after the inn was
  named, and not "yes, there".
- **The first wording read the playtest's line wrongly.** "Right - off to the inn for you all - see you
  there" was come along at 0.94 to 0.96 on Decision 1 until the option said what the tell is (the
  leader stays behind or comes later); the bare word "bank" was an order to go to the bank (0.84) until
  the option said a word for a banker isn't one. Four wordings on the third set: 92, 104, 107, 108 of
  the 108 readings it then had.
- **And live it was still on the edge:** see the live runs below, and the rule that came of them (sure
  of an order to move and unsure which: sent, when that has 0.3 or more). The table has it.
- **The two models are two readings apart on the fourth set and level on the seventh,** so the kind
  stays Decision 1's by the rule of 2026-10-12. Jev misses fewer orders and takes more talk for one
  ("I'll wait.", "right", and on the second set 11 readings to none).
- **A request was first a sixth option of the order question, and took orders with it:** "Stay here
  and cover the gate." and "Get yourselves to the bank and wait." were requests at 0.7 to 0.9, and
  Decision 1 fell to 245 and 143 on the first two sets. As a yes/no of its own the order question is
  the one measured above, unchanged.
- **Requests on the lines the yes/no was settled on** (the third and fifth sets): 0.93 and more for
  every request but one, 0.35 and less for talk but for "anyone need to wait for mana?" (0.59 to
  0.68). Hence a cut of 0.7.
- **Requests on the seventh set, Decision 1:** "someone pull that ogre off me", "give me a hand with
  this door", "bandage me, quick" and "Wystan, pick that lock for me." (for Wystan alone) were requests.
  "can anyone gate us home?" wasn't (0.20 to 0.38: a question, to the yes/no), nor "Thea, lend me fifty
  gold." (0.68, under the cut), and "hold this for me a sec" was an order to wait for one of three
  (0.67).
- **Talk taken for a request:** "all guard me", a command to the leader's own pets, by all three (0.87
  to 0.89; "all kill" is in the question's wording and "all guard me" isn't). "Tell me again how you
  lost that horse.", "Go on, laugh." and "Someone ought to fix that bridge." were talk.
- **An order with a request in it keeps its order:** 9 of 9 on the seventh set ("With me, and somebody
  watch the rear."). One on the sixth didn't: "Come on, and kill anything that moves." was no order to
  any of the three (0.99) and a request.
- **An errand is read as being sent:** "go and get me some bandages from town" is an order to go for
  all three (0.97 and more), and "someone go and buy me some reagents" for two. The companion then asks
  where, or walks to the nearest such place and waits there.

*Settings.* Two steps: which settings a line speaks to (one choice, each setting given 0.25 or more),
then each of those settings' own question (a value at 0.6 or more). A reading is right when exactly the
settings written down for that companion were changed, to those values:

| | Jev | Decision 1 |
|---|---|---|
| first set, 57 lines, 171 readings: written with the questions | 154 (90.1%) | 171 (100%) |
| **second set, 52 lines, 156 readings: written after that** | 132 (84.6%) | **134 (85.9%)** |
| second set: a setting the line changes that wasn't changed | 9 | 3 |
| second set: a change nobody asked for | 15 | 19 |
| second set: a wrong value | 0 | 3 |
| the first step with the order question; a setting's question (median) | 0.22 s, $0.000075; 0.21 s, $0.000027 | 0.32 s, $0.000056; 0.29 s, $0.000014 |

- **Looting, the thing asked for, was read right on every unseen line by Decision 1** (21 of 21: "go
  on, grab the loot", "nobody touches the loot but me", "Thea, you can loot now." for Thea alone, "ok
  stop looting"), as were target, helping, raising, callouts, distance and reach.
- **What went wrong on the unseen set was mostly a remark or a request for one thing now taken as a
  rule** (12 of the 42 readings that should change nothing): "I always go for the biggest one" set
  strongest first, "nice and safe, that one" cautious, "run for it!" may run and "heal pls" helping
  on (the last two are what a companion starts with, so nothing changes but the word it says). "We
  fight to the death here" set relentless as well as never running, "be careful in there, pull back
  early" may run and not cautious, "only speak when I ask you something" silent and not answers, and
  "Isolde, just Fireball them once it's started." her opening spell as well as the one after it.
- **A yes/no and then a question for every setting was the first design, and it over-read:** asked
  about each setting in turn the models found something for most of them in most lines. "Leave the
  rest." also switched helping and raising off; "Quiet, all of you." the callouts too. 39 changes
  nobody asked for in 156 readings on Decision 1 (105 right), 32 on Jev (117 right). The yes/no also
  missed lines outright on Decision 1 ("You can talk again." 0.09, "the rest of you keep off" 0.03
  for the two it was for). With one choice among the settings first: 149, then 155 of 156 once the
  option for none said that a remark on how things go isn't an instruction.
- **The second set was run twice,** before and after two changes the first live run asked for: a
  setting's question is told how the companion now plays in that setting only, and "leave the rest"
  is in looting's option for nothing. 133 before, 134 after on Decision 1 (Jev 137 and 132). The 15
  readings "after a change" in the first set are from that run too, all right on both models.
- **Cuts:** on the second set Decision 1 has 134 at 0.25 and 0.6, 136 at 0.3 and 0.6, 133 at 0.15; the
  cuts were left where the first set put them.

*Who a line is for.* One choice with the party as its options, then the yes/no of each it is for;
and one of those it is for who reads a request in it answers whatever its yes/no says. Lines read as
for whom they are for, and lines answered by exactly those (the run of 23:46; the held-back lines were
first run once earlier in the evening, with the same counts on Decision 1):

| | Jev | Decision 1 |
|---|---|---|
| the 39 lines of the morning, as wanted by that count (somebody answers a line to the party; nobody answers "brb") | 37 | **38** (the yes/no alone: 34) |
| of those, the five meant for the others ("What did the rest of you make of it?") | 4 | 4 (the yes/no alone: 0) |
| 16 more lines the question was settled on, four requests among them: for whom right | 11 | 13 |
| the four requests ("cover me", "heal me", "Isolde, kill that one for me.", "someone go and buy me some reagents"): answered | 4 | **4** (the yes/no alone: 1) |
| **24 lines held back: for whom right, of the 21 with one answer** | 19 | **21** |
| held back: answered by each it is for and nobody else | 19 | 20 |

- **A request gets its answer from the reading and not from the yes/no:** on Decision 1 the yes/no on
  answering gave "cover me" 0.11 to 0.18, "heal me" 0.15 and "Isolde, kill that one for me." 0.25, and
  each of those the line was for read a request in it (0.93 and more) and so answers. "heal me" was
  read as Isolde's alone, the healer's.
- **The request's yes/no takes a question for one now and then:** on the held-back lines "Not you,
  Wystan. The other two: any bandages left?" for both it was for, and "Hey archer, save some for the
  rest of us." for Wystan. They would have answered anyway (0.62 to 0.99), and the writer of the line
  is told to answer a question as a question. A word for a vendor ("buy") was a request by the yes/no
  to all three and is for none of them, so only those a line is for are left owing an answer.
- **"The rest of you" goes to the others now:** after Isolde's answer, "What did the rest of you make
  of it?" is Thea's and Wystan's and both say yes (0.97, 0.92); "And your reagents?" stays hers. Four
  of the five were read for the right two ("Right, come along, you lot." is everyone's, being an
  order), and two of those were answered by one of the two only: "Anyone else?" and "What about the
  others, how are your supplies?" (the other's yes/no gave 0.35 and 0.44).
- **A line to the party is every companion's:** all 12 of the morning's and all 6 held back had all
  three answering ("everyone alright?", "who's hungry", "morning all").
- **By what one plays:** "Hey archer, save some for the rest of us." is Wystan's (0.99) once the
  question says what each plays as; before that, "Scholar, what is that thing?" was for nobody (0.92).
  "And what does the bear's mother think?" still is: the question knows a tamer, not her bear.
- **Words for a banker or a vendor are for none of them** on Decision 1 ("bank", "vendor buy", "buy",
  "withdraw 500", "guards!": 5 of 5; "all kill" was for everyone, and nobody's yes/no answered it).
  Jev read "brb", "lol" and "ok" as said to everyone.
- **One held-back line was read right and not answered:** "Tamer, keep that bear off my boots." (Thea's
  yes/no 0.44).

*Where it is sent, and whether it goes* (one companion, the kinds of place a character south of the
Britain graveyard is offered: 14, the nearest inn 66 tiles east):

| | Jev | Decision 1 |
|---|---|---|
| the kind of place meant, 20 lines the question was reworded on | 18 | 17 (15 with the first wording) |
| **the kind of place meant, 10 lines written after the first rewording** | 8 | 9 (8 before the second) |
| whether it goes, 12 moments, written before any run and not touched | 7 | **12** |

- **Whether to go is where the two differ:** Decision 1 said go in the four quiet moments (0.89 to
  0.96) and when asked again after a refusal (0.91 to 0.94), and no when badly hurt (0.32), with the
  leader near death (0.32), in a fight (0.03), with three hostile creatures close (0.17) and with the
  leader fighting alone (0.32). Jev said go to three of those five (four in an earlier run), and no to
  two of the three askings again.
- **The options are kinds of place, with how far the nearest is, and not places by name.** That is the
  second rewording, made for the second live run below and not on these lines: given "The inn: North
  Britain inn at (1458, 1525), 66 tiles to the east." and the talk of a live run before the line, the
  model called "off to the inn for you all - see you there" unclear at 0.63 to 0.79 for all three; given
  "The inn: the nearest is 64 tiles to the east." it is the inn at 0.79 to 0.91. The ten later lines
  were run before and after it (8, then 9).
- **Wrong places:** a line with two places in it sends the companion to the first ("I'm going to the
  bank, you go to the healer.": the bank, 0.94). "go get a drink, I'll find you" names no place it can
  tell, and it asks; the moongate is 469 tiles off and isn't offered.
- **The talk before the line gives the place** once the question says so (the first rewording): "Off
  you go then" after "who wants to go off to the inn?" was unclear (0.78) and is the inn; "That one."
  after "Where? The bank?" is the bank.
- **Another town's inn isn't the nearest inn:** "see you at the inn in Skara Brae" is unclear on
  Decision 1 with the options as kinds (it was the nearest inn at 0.85 before), and still the inn on Jev.

*Sent by a place's name, among several places, on an errand, or one of several* (cuo-cym.27), 2026-10-19
15:20 to 15:30, one companion as above. The place question now also offers the places a line names by
their own names (`named_places`), and the same request asks what the companion is sent to do (only to go,
or to buy, bank or sell; for a buy, what and how many) and whether just one of the companions is asked.
Each case has lines written before any model saw them: a first set, and a second run once with the first
wording. The place question was then reworded once on the first sets (a place named or singled out is
that one, not the nearest of its kind; whose place it is in a line with several), and a third set of
places, written after that, was run once. The phrases are what a companion did before: the nearest of the
kind its word names, no errand ever (it walked to the place and waited), everyone going.

```bash
cd brain
uv run python scripts/errand_check.py --out bench/2026-10-19-errands.json   # the first wording: -errands-first-wording.json
```

| | the phrases | Jev | Decision 1 |
|---|---|---|---|
| a place by its name: first set (8), first wording | 1 | 6 | 5 |
| a place by its name: first set, reworded | 1 | 8 | 8 |
| a place by its name: second set (7), first wording, then reworded | 2 | 5, 6 | 5, 6 |
| **a place by its name: third set (6), run once** | 2 | **6** | **6** |
| several places, whose is it: first set (6), first wording, then reworded | 2 | 4, 4 | 6, 6 |
| several places: second set (5), first wording, then reworded | 1 | 4, 4 | 4, 5 |
| **several places: third set (4), run once** | 1 | **2** | **3** |
| an errand, and what to buy: first set (10), second (8) | 2, 2 | 10, 8 | 10, 8 |
| ...and how many | | 10, 8 | 10, 8 |
| one of several: first set (6), second (6) | 3, 3 | 6, 6 | 6, 6 |
| a request (median) | | 0.19 s, $0.00009 | 0.34 s, $0.00006 |

- **Decision 1 reads these** (kind `order`). It is as good as Jev on names, errands and one of several, and
  better on whose place it is: Jev sent the companion to the place the leader said it was going to itself
  ("I'm going to the bank, you go to the healer": the bank; "I'll get the drinks at the inn, you go to the
  bank": the inn). Both missed "I'm off to the smith's, you lot head to the healer's" (the smithy, 0.98).
- **A name and a kind:** before the rewording, "the east bank" was the nearest bank, or unclear; after, the
  East Britain bank. "wait at the east inn" is still the nearest inn on both (0.76, 0.77), the one miss of
  the second set after the rewording.
- **What is offered:** the store's fuzzy match on stretches of up to four words, best match first and of
  several as good the nearest, gave the place wanted for every line meant to name one: "See you in Yew"
  had been Yew Cave and "Vesper" the Vesper weaver before that rule, and the town of Britain had been
  found in "the East Britain inn" before the longest stretch took its words. A quarter with a kind and no
  name of its own ("the west inn") is offered only within 400 tiles: it had found the West Moonglow inn.
- **The errands and one of several were right on every line on both models,** counts included ("five heal
  potions" 5, "30 bandages" 30, "some bolts" no number, so 20).
- **The lines of 2026-10-13 on the reworded question** (`scripts/go_check.py --out
  bench/2026-10-19-go-reworded.json`, kinds only, as that script offers): Decision 1 18 of 20 and 9 of 10
  (17 and 9 before), whether to go 12 of 12; Jev 16 and 8 (18 and 8 before).
- **Live, one errand at a scripted leader's word** (2026-10-19 16:58, one run, headless, a frozen copy of the
  code): `uv run python scripts/party_player.py --fleet fleets/party-player-talk.toml --errand`. Roland says
  "Isolde, run to the healer and get me 10 bandages, then come back." south of the Britain graveyard. Isolde
  read it as a buy of 10 bandages at the healer's (0.999), said "I am off to the healer's. Try not to get
  lost.", was on her way 3 s after the line, bought 10 of 10 at the Britain healer (85 tiles off) for 50 gold,
  said "Got 10 bandages, coming back." and was beside Roland again 70 s after the line. Wystan and Thea stayed
  with him (`logs/fleet/20261019-165802-party-player-errand`).

**Live, with a script saying the playtest's lines** (cuo-cym.18 to .22), 2026-10-13 22:49 to 2026-10-14
00:24. `scripts/party_player.py --playtest` plays Roland, with no brain, and says what Harry said
in his five minutes (and a few lines more), while the three companions with personas play as they
would for a person: Isolde the mage, Wystan the archer, Thea the tamer. Each run from a frozen copy of
the code as it then stood, headless, single runs. The run of 23:43 was windowed to be filmed and the
recorder got no frames (cuo-cym.9); its seconds are a loaded machine's. Two more, at 00:06 and 01:42,
are void: the scripted leader died in the first fight (95 to 0 health in four or five seconds under
the staged bone knight and skeletons, its reflexes bandaging and two companions healing it), and a
ghost's speech reaches nobody. It carries heal potions since, which its reflexes drink when low. One
at 01:24, on the same code with the fight orders' code added (their step came after the fight was
over), went as the 00:24 run on every row: loot on and off, three answers to each question, "cover
me" answered by all three, all three staying in the fight and all three going to the inn.

```bash
cd brain
uv run python scripts/party_player.py --fleet fleets/party-player-talk.toml --playtest --own-words
```

| | 22:49 | 23:04 | 23:17 | 23:43, windowed | 00:24, the code as it is now |
|---|---|---|---|---|---|
| "feel free to loot": looting on; loot decisions after | 3 of 3; 4, 4, 3 | 3 of 3; 4, 4, 4 | 3 of 3; 4, 3, 3 | 3 of 3; 5, 4, 4 | 3 of 3; 4, 4, 3 |
| "Leave the rest.": looting off; loot decisions after | 0 of 3 (read as: what is worth having); 0 | 3 of 3; 0 | 3 of 3; 0 | 3 of 3; 0 | 3 of 3; 0 |
| "Keep up, you lot.", already following: a word from each | (not built) | 3, at 1 to 3 s | 3, at 1 to 2 s | 3, at 2 s | 3, at 1 to 2 s |
| "So crew how are you all today?" and "Shall we head to the graveyard?": answered by | (not built) | 3 and 3 | 3 and 3 | 3 and 3 | 3 and 3 |
| "cover me": answered by | (not built) | Isolde | nobody | all three, each a no ("Cover you? I'm a mage, Roland, not your bloody shield.") | all three, at 3, 6 and 9 s |
| "Off to the inn with you, I'll deal with these.", with three skeletons on the leader | (not in the script) | | | all three said no (0.03, 0.03, 0.09) and fought on | all three said no (0.02, 0.03, 0.08) and fought on |
| "Right - off to the inn for you all - see you there": read as | go 0.65; none 0.56, 0.50 | go 0.80, 0.67, 0.61 | go 0.72; follow 0.64, 0.67 | go 0.88, 0.88, 0.89 | go 0.99, 0.81, 0.75 |
| ...and went | 1 of 3 | none: no place it could tell (an innkeeper seen once, 108 tiles off, outranked the inn 64 tiles away) | 1 of 3 | 3 of 3, there 26, 27 and 53 s later | 3 of 3, there 26, 28 and 28 s later |
| one sent back to the inn, then called back in party chat | went, came back | (not reached) | went, came back | not heard: said aloud on the way out of the inn, walls between | went (told in party chat), came back |
| first answers to a line (not a setting's word): wait over a person's reading and typing time for those lines | | | 0.87 (n 5) | 0.92 (n 5) | 1.01 (n 5) |

- **Each fix came of the run before it.** After 22:49: "leave the rest" in looting's option for
  nothing, and the place given as a kind ("The inn: the nearest is 64 tiles to the east.") with the
  canonical inn preferred over an innkeeper seen once. After 23:04: an order to move that the model is
  sure of and unsure which (go and come along) is being sent when go has 0.3 or more (it was 0.4), as
  two companions there read "off to the inn for you all" as come along at 0.64 and 0.67. After 23:17:
  a request that is no order gets an answer (asked by a yes/no of its own; "cover me" went unanswered
  at 23:17), and the scripted leader sends them off with a fight on.
- **The context helps the inn line:** at 23:43 the companions had just been sent to the inn with a
  fight on and said no, and the next line was read as go at 0.88 for all three, against 0.61 to 0.80
  in the runs where it came first.
- **Answers to a person about a tenth sooner, not the fifth asked for.** In Harry's playtest the
  first answer to each of his lines waited 1.04 times what a person takes to read the line and type
  the answer, at the median (n 8; 5.2 to 9.1 s). In the four runs since the change (these three and
  one at 01:24 with the same code for this), 0.925 (n 20; 0.74 to 1.17; 5.6 to 8.7 s), run by run
  0.87, 0.92, 1.01 and 1.03. The wait is set at 0.8 of that time with a random share of 0.85 to
  1.30, so no answer should come later than 1.04 of it: three did (1.08, 1.10, 1.17), so something
  else holds some answers back by up to a second (cuo-cym.30). Each answer's own record (`delay_s`)
  against `talk.reply_delay` for its two lines. `pace = 0.7` in the fleet file shortens every wait
  further. A setting's word is said without the wait: 3.4 to 4.1 s after the line.
- **Each of three answers in turn,** about 3 s apart: 6, 9 and 12 s after "So crew how are you all
  today?" at 23:04; the second and third answer what the one before said as well ("Smell of ale, Thea?
  Then the bear has finally found something it can identify.").
- **"cover me" gets an answer from each since requests are read** (23:43 and 00:24), in character and
  mostly a no ("Cover you? I'm a mage, Roland, not your bloody shield. Fireballs only."). One promised
  more than a companion does at a word: "I'll cover you from the bone knight, not from your bad jokes."
- **Sent off with a fight on they stay, and say so** (23:43 and 00:24): whether to go was 0.02 to 0.09
  for each, each said "I'd rather stay with you for now." and fought on, and the next line sending them
  to the inn, with the fight over, sent all three.
- **A run costs about $0.025 in AI calls** (23:43 and 00:24, seven minutes, three companions): Decision
  1 $0.017 and $0.019 for the fights, the readings and the yes/no's, Haiku $0.007 for the lines.

**Orders for the fight itself, without the game** (cuo-cym.24), 2026-10-14 00:54 to 00:58. With
creatures in view a companion puts each of the leader's lines to a question of its own beside the order's
(`companion.fight_question`: go for one creature, fall back, heal someone, or none), with which creature in
the same request (`target_question`, the six nearest as options, from a snapshot of the scene as that
companion's client sees it) and on a heal whom (`whom_question`). `scripts/fight_orders_check.py` puts a
set of lines to both models in written scenes (a lich on the leader, a bone knight on the mage, a skeleton
on the archer, an evil mage further off; one ogre; three idle orcs and a deer; for the second set a crypt
with a wraith, two zombies, a ghoul, a lich lord and a skeletal mage, and an ettin on the tamer), for each
of three companions (Isolde the mage, Wystan the archer, Thea the tamer, led by Roland). A reading is right
when the kind is right at 0.6 and the creature at 0.5, or whom. The first set was written before any model
saw it and the wording was settled on it in three runs; the second was written after that and run once,
and its numbers are the ones to go by.

```bash
cd brain
uv run python scripts/fight_orders_check.py --out bench/2026-10-14-fight-orders.json
uv run python scripts/fight_orders_check.py --sets second --out bench/2026-10-14-fight-orders-held-back.json
```

| | Jev | Decision 1 |
|---|---|---|
| first set, 51 lines, 153 readings: the first wording (`-first-wording.json`) | 145 | 138 |
| first set: the wording settled on it | 150 | 149 |
| **second set, 42 lines, 126 readings, held back and run once** | **117** | **112** |
| second set by group: go for one, fall back, heal, stays what it is | 38/39, 19/21, 11/15, 49/51 | 34/39, 18/21, 9/15, **51/51** |
| second set: an order missed (it fights on as before) | 2 | 8 |
| second set: talk or another's order taken for an order | 3 | 3 |
| second set: **the wrong creature gone for** | 1 | **0** |
| second set: a heal whose whom it couldn't tell (it asks "Heal whom?") | 3 | 3 |
| second set at a cut of 0.5 for the kind | 116 | 115 |
| a line, the fight question with which creature (median) | 0.21 s, $0.000068 | 0.32 s, $0.000048 |

- **The reader stays Decision 1.** Jev read five more of the held-back readings right, and was the one to
  send a companion at the wrong creature: "Wystan, pull the near one" went for the deer (0.80), the
  creature nearest Wystan, where the line meant the orc nearest the leader (a line that could be read
  either way). Decision 1's errors were the safe kind: an order missed is no order, and the companion
  fights on as before. Nothing it should have left alone did it take on the held-back set but heals for
  others ("patch me up Thea", read by Isolde and Wystan as a heal for Roland, 0.98; in a party that talks
  who a line is for keeps it Thea's), and "someone heal Wystan", read by Wystan as a heal.
- **What Decision 1 missed:** "help Thea with that ghoul" (0.12 to 0.32 for all three: the creature was
  right, 0.99, the order wasn't taken), "leave it, leave it!" (0.38 to 0.56), "Thea, sic Bramble on the
  lord" for Thea (0.55) and "Wystan, pull the near one" (0.59, with fall back at 0.40). At a cut of 0.5
  the last two would have been taken; the cut stays where the first set put it.
- **What the first set's wording missed, and what changed it:** Decision 1 took "Kill the mage first!" for
  the lich (0.85 and 0.60): the target question now says a name means the creature with that word in its
  name, "not a lich that casts spells too". Both took "Off to the inn with you, I'll deal with these." for a
  fall back (0.63 to 0.75) and Decision 1 still does (0.85 to 0.88); live the line is the order to go,
  which takes it before any fight order (a trip the companion may refuse). Decision 1 took "Heal thyself,
  as they say.", "Get off me, you brute!" and "Ogre's mine, you lot keep out of it." for orders (0.83 to
  0.98); a saying, words shouted at a creature and the leader taking one on alone are now in the option
  for none, and none of the three was taken after. Jev's "Let's take them." (0.67 to 0.73 to attack, no
  creature told) stayed: a companion would ask "Which one?".
- **A heal with nobody named:** "heal pls" was a heal for all three on both models, and whom the
  question couldn't tell (unclear, 0.62 to 0.78), so each would have asked "Heal whom?". A rule came after
  the held-back run, from it (`companion.heal_whom`): a heal whose whom isn't told is the leader's when the
  line names nobody of the party, and no order for a companion that is the only one named ("someone heal
  Wystan", read by Wystan). Applied to the same answers it makes the held-back set 120 for Jev and 116 for
  Decision 1; that is not a measurement on unseen lines. "heal up when you can" (Jev, Thea, 0.61) becomes a
  heal for Roland by it, where it would have asked.
- **The requests that must stay what they are stayed,** with the settled wording: "cover me", "Nice
  shot!", "kill me now, I forgot the reagents", "all kill", "all guard me", "any bandages left?", "Follow
  me.", "Hold them off!", "watch my back", "kill anything that moves", "Kill them all!" were no order on
  both models; on the held-back set "keep them busy", "protect Isolde", "all follow me", "come on then,
  you bag of bones!", "leave the lord to me" and "Wait here." the same.
- **Cost:** $0.016, $0.017 and $0.017 for the three runs on the first set, $0.015 for the one on the
  second, both models together, all of it kind `order`. Live it is one request more for each line said
  with creatures about.

**Orders for the fight itself, live** (cuo-cym.24), 2026-10-14 02:01, 02:20 and 03:50, three single runs of the
scripted playtest (`party_player.py --fleet fleets/party-player-talk.toml --playtest --own-words`, headless,
each from a frozen copy). Its last step: two bone knights spawned 5 tiles north of the leader; 3 s into the
fight it says "Get the one on me!", at 14 s "Fall back!" and runs 8 tiles south (logs
`20261014-020102-…` and `20261014-022046-party-player-talk-playtest`; the step was first in the skeleton
fight, where it came too late: those are dead in about six seconds).

- **"Get the one on me!" was taken by nobody at 02:01.** Who the line is for split (about 0.5 for Wystan
  alone, 0.35 to 0.47 for each of the others; Wystan's own reading put him at 0.48), and an order for the
  fight was taken only by those a line is for. All three were on that bone knight already by their own pick.
  Since then an order for the fight is each one's unless the line is plainly for others (0.3 or more for it
  will do, `companion.FIGHT_FOR_CUT`). At 02:20 all three took it ("at Roland's word: fight a bone knight",
  0.0 to 1.5 s after the line), each said so in its own voice ("A bone knight is mine, and I'll have it in
  two shots!"), and the order ended a second or two later with that bone knight dead.
- **"Fall back!" was taken by all three in both runs,** and they came with the leader as it ran: 2 tiles
  off the mage and the tamer, 6 the archer, for the 12 s watched. The mage let go of her fight both times,
  the archer once, the tamer neither time (her pet fought on; she stayed by the leader). Their word was the
  voice they run from a fight in, which read as leaving the leader ("Off you go, then. Good riddance to
  you.", "I am leaving, and I am taking the good loot with me!"); since then it is the voice they come
  along in, and at 03:50 they said "Yes, yes, I am coming. Do keep up, Roland.", "Aye, I'm coming, and
  I'll shoot twice as well for it." and "Coming. Bramble's already up and ready.", and kept 2 to 5 tiles
  from the leader as it ran.
- **At 03:50 the target order was taken by the mage and the tamer** (the archer, already shooting that
  bone knight, didn't take it) and held 7 s, until it died.
- **The leader lived through all three** (lowest 95%, 53% and 47%), carrying heal potions since 01:52.
- **The rest of the 03:50 run, on the final code,** went as the 00:24 run: loot on and off for all three,
  a word from each on an order in force, three answers to a line to all of them, all three staying in
  the fight and then all three going to the inn, one sent back and called in. "cover me" got one answer
  this time (three in the two runs before). 16 of 19 answers were said within 0.1 s of their moment.

**A rally point that is chosen** (cuo-cym.13), 2026-10-14 00:13 to 01:24: the lich scene formed up,
memory off, without and with `rally_map`, in turn, three runs each, headless, one frozen copy of the
code (f5). Measured with `scripts/party_scene.py` and the rally records.

```bash
cd brain
uv run uo-brain fleet fleets/party-lich.toml --set memory=false --set form_up=true
uv run uo-brain fleet fleets/party-lich.toml --set memory=false --set form_up=true --set rally_map=true
```

| three runs each | formed up where the leader stopped | `rally_map` |
|---|---|---|
| members who died | 0, 0, 0 | 0, 0, 0 |
| the lich dead, from first sight | 16.4 s, 17.8 s, 19.9 s | 18.2 s, 11.9 s, 37.0 s |
| falls back to the rally point (members) | 0, 1, 1 | 2, 1, 3 |
| runs to the second point | (none) | 0, 0, 0 |
| seconds with someone apart, of the first 60 | 0, 0, 0 | 0, 0, 0 |
| the client's map, asked at the edge | (not asked) | 15 to 39 ms |

- **Where it put the point:** at the first hunt of every run 5 tiles back along the leader's way in
  (1393,1522, the party formed up at 1393,1517), and the second point 12 tiles behind that
  (1395,1534). For the hunts after it, begun inside the graveyard, 5 tiles out from where the party
  formed up and away from the centre (1355,1475 and 1366,1461, say), the second points further out
  again.
- **No worse, and nothing shown better:** nobody died either way, so these runs can't say whether a
  member at the chosen point lives where one at the leader's stop would have died. Members fell back
  more often with it (6 against 2), each to the first point; one of those was the warrior, the point,
  at 63% of his health, in the run in which the lich took 37 s.
- **The second point wasn't used:** a member runs on to it only from the first point, under 40% with
  the others still fighting or with something on it there, and none was.

**Helping a hurt companion, weighed by a question, without the game** (cuo-cym.26), 2026-10-14 02:46 to
02:49. Whether a member heals or bandages a companion is decided by thresholds (`policy.help_mates`: under
60%, a bandage within 2 tiles, no walk over while anything hostile is within 8 tiles, nobody helped by a
member under 50%, one being healed by another left to them above 30%). `helping.help_question` puts the
moment to system one instead: the helper's health and how fast it fell, its mana or bandages and what it is
fighting; each companion's health, the points it lost in the last few seconds, what is on it and whether
another is healing it; the creatures and whom each is on; and as the options the companions it could help,
each by the one way it has (a Greater Heal from where it stands, a bandage within reach, or a walk over and
a bandage), or nobody. Code keeps the floors (`helping.ways`): nobody dead, unhurt or out of every way, no
bandage while one is on, no heal without the mana. `scripts/helping_check.py` puts fight moments to the
thresholds (`help_mates` itself, on the moment rebuilt as a fight state) and to both models, and counts how
often each does what is wanted: the action Claude judged right, written before any model saw the moment. A
model's help is its likeliest option at 0.5 or more. The first set: 22 decisions drawn at random from the
party runs of 2026-10-13 00:08 to 04:59 among those where the helper had someone it could help, rebuilt
from the logs (the member's decision, the others' own health, `where.jsonl`), and 20 written, mostly
cuo-4ti's cases (ranged members walking into the melee to the warrior, a mage at low mana, two healers on
one companion, a warrior leaving the line). The wording was settled on it in three runs. The second set
(14 drawn the same way from the runs of 2026-10-13 05:00 to 2026-10-14, and 14 written) was written after
that and run once: its numbers are the ones to go by.

```bash
cd brain
uv run python scripts/helping_check.py --out bench/2026-10-14-helping.json
uv run python scripts/helping_check.py --sets second --out bench/2026-10-14-helping-held-back.json
```

| | the thresholds | Jev | Decision 1 |
|---|---|---|---|
| first set, 42 moments: the first wording (`-first-wording.json`) | 29 | 28 | 29 |
| first set: the wording settled on it | 29 | 38 | 37 |
| first set by part: drawn from the logs, written | 19/22, 10/20 | 21/22, 17/20 | 20/22, 17/20 |
| **second set, 28 moments, held back and run once** | **24** | **26** | **25** |
| second set by part: drawn from the logs, written | 14/14, 10/14 | 14/14, 12/14 | 13/14, 12/14 |
| second set: a help missed | 3 | 2 | 2 |
| second set: a needless help, or the wrong companion | 1 | 0 | 0 |
| second set: a walk over that wasn't wanted | 0 | 0 | 1 |
| a moment (median) | | 0.21 s, $0.000049 | 0.28 s, $0.000032 |

- **Not switched: the thresholds stay.** On the held-back moments Jev did what was wanted in two more than
  the thresholds and Decision 1 in one more: no clear gain for a request more at every decision with
  someone hurt. The question is in `helping.py`; the fight loop doesn't ask it.
- **Where the question did better:** a companion losing health fast above 60% (a tamer at 70%, down 30
  points in a few seconds with a bone knight on her, beside the archer; an archer at 70% under a bone knight,
  for a mage at 95% mana), which the thresholds leave until it is under 60%; and a mage with one heal left
  choosing the bard losing health to two skeletons over the steady archer the thresholds pick for being
  lower. On the first set the same kinds, and, for Jev, one being healed by another but still falling fast,
  which the thresholds leave to the healer above 30%. Code could weigh all three (cuo-cym.31); live, each
  needs a companion's fall in health, which the party's fight rows don't keep yet.
- **Where it did worse: walks.** Both turned down walks that were wanted: to the mage at 40% once the fight
  was over (0.40 and 0.38; the thresholds walk), and to a bard near death 4 tiles from the fight with
  nothing on him or on the way (0.16 and 0.43; the thresholds don't walk while anything hostile is within 8
  tiles). Decision 1 sent the warrior 6 tiles to the mage at 68% with a bone knight on her (0.73), and on
  the first set the bard 4 tiles to the warrior with a skeleton on him (0.65).
- **The moments drawn from the logs mostly want nobody helped:** 14 of the 14 held back and 17 of the first
  set's 22 (three of those take either answer), and the thresholds did what was wanted in 33 of the 36.
  Nobody being near death or losing health fast within reach is the usual moment.
- **The first wording** said a help costs something, and that a companion hurt but not losing health
  mostly needs nobody: both models chose nobody almost every time (14 and 13 helps missed, none given that
  wasn't wanted). The settled wording weighs a bandage within reach as nearly free and a heal as cheap with
  mana to spare, and says when a walk costs little.
- **Cost:** $0.0030 to $0.0035 a run of the first set (84 requests, both models), $0.0023 for the held-back
  run, all of kind `fight`.

**Helping weighs how fast a companion is losing health, in code** (cuo-cym.31), 2026-10-19 14:57: the three
kinds of moment the question did better on, as rules in `policy.help_mates`. Each member now keeps a
six-second trail of each companion's health as it sees it (`mates.Mates.fall`, the same measure as its own
`health_drop`). A companion that lost 15 points or more in that time is helped from under 75%, not only
under 60%; one another is healing is left to them only above 30% and while it isn't falling so; and a mage
with mana for fewer than two Greater Heals heals the falling one first and keeps its last heal for one
falling or near death. The same moments as above, the rule alone (no model calls), before and after:

```bash
cd brain
uv run python scripts/helping_check.py --models "" --sets first,second --out bench/2026-10-19-helping-rule-falling.json
```

| the rule | first set, 42 moments | second set, 28 held back |
|---|---|---|
| the thresholds (before) | 29 (19/22 drawn, 10/20 written) | 24 (14/14, 10/14) |
| **with the fall weighed** | **36** (22/22, 14/20) | **27** (14/14, 13/14) |
| Jev with the question, for comparison | 38 | 26 |
| Decision 1 with the question | 37 | 25 |

- **The held-back set had been seen once before,** by the question's run of 2026-10-14; the rule was written
  from the bead's description of what the question did better, not tuned on it, and run on both sets once.
- **Still missed:** a walk to a companion near death with nothing on him while the fight goes on (the
  first set's w11, the second's x08: the walk rule stands, as both models turned wanted walks down), and two
  written moments where the one falling should come before a lower one who is steady for a bandager or a
  mage with mana to spare (w13, w17): with mana or bandages to spare the lowest still goes first.
- **Live:** in the six lich-scene runs of 2026-10-19 16:06 to 18:20 (a huddle, formed up, three with memory
  on and three with it off; below), 16 of the 31 heals and bandages went to a companion at 60% or more and
  falling (61% to 74%), which the thresholds wouldn't have helped; nobody died in the five measured by then.

**What the leader a person plays is fighting, judged per creature** (cuo-cym.25), 2026-10-19 15:30, without
the game. A companion of a person's character fights what attacks it and what stands in war mode within 4
tiles of the leader or 6 of itself. `escort.theirs_questions` puts every creature in view to system one
instead, a yes/no each: is it part of the party's fight? With what the companion's client sees of it and of
the leader (distances, war mode, which way each faces and whether at the other, whether it came nearer the
leader in the last 4 s, whether it is wounded with no companion on it, what the leader said lately).
`scripts/escort_check.py` puts written fight moments to the rule and to both models and counts, per
creature, how often each gives the answer wanted, written before any model saw the moment. The first set
(16 moments, 21 creatures) had the question reworded on it once (a leader's "leave the troll alone"); the
held-back set (12 moments, 15 creatures) was written before any model ran and run once, after that.

```bash
cd brain
uv run python scripts/escort_check.py --out bench/2026-10-19-escort.json   # the first wording: -escort-first-wording.json
```

| creatures right | the rule | Jev | Decision 1 |
|---|---|---|---|
| first set, first wording | 14 of 21 | 19 | 20 |
| first set, reworded | 14 | 20 | 21 |
| **held back, run once** | **8 of 15** | **14** | **14** |
| held back: a creature of the fight missed | 7 | 0 | 0 |
| held back: one taken for the fight that wasn't | 0 | 1 | 1 |
| a moment (median) | | 0.18 s, $0.00003 | 0.32 s, $0.00002 |

- **What the rule misses is the leader fighting at range:** an archer's skeleton 10 tiles off, wounded and
  coming at it; a skeletal mage or a ratman archer shooting at the leader from 10 tiles; a bone knight
  charging from 7; "kill the ogre" with the ogre 9 off. It never took a creature for the fight that wasn't.
- **What the models got wrong:** a lizardman in war mode walking away north, 9 tiles off with the leader in
  war mode facing west (0.61 Jev, 0.85 Decision 1), held back; on the first set, before the rewording, the
  troll the leader said to leave alone (both), and on Jev an orc far off facing away (0.63, 0.59 after).
- **Switched on, with the rule as the floor:** a companion asks when a creature in war mode is in view that
  the rule leaves out, at most every 3 s, and fights one judged the party's for 12 s; `judge_reach = false`
  is the rule alone. The moments are written ones, biased to fights at range by design.
- **Live, the leader shooting from range** (2026-10-19 17:20 and 17:28, one run each, headless, a frozen copy
  of the code): `uv run python scripts/party_player.py --fleet fleets/party-player.toml --range` (and `--set
  judge_reach=false`). Roland, kitted as an archer and held where he stands, shoots a ratman archer spawned 12
  tiles north; the three companions follow him.

  | | judged per creature | the rule alone |
  |---|---|---|
  | each companion went for it, seconds after the first shot | 1.5, 2.0, 2.5 | never, in the 60 s it stood there |
  | how far it was from Roland then | 10 tiles | |
  | the creature | dead at 10 s | alive at 60 s |

  The ratman archer kept its distance (it was never within 4 tiles of Roland, so the rule never took it); each
  companion's first question on it said it was his fight. Two runs before these, with Roland free to walk,
  showed nothing either way: his client walked up to what he shot (10 tiles in 3 s), so a skeleton and a
  ratman archer were beside him within 2 s and the rule took them too.

**Through the inn's wall** (cuo-cym.28), 2026-10-23 13:28 and 13:37, one run each way, windowed and filmed
(each client recording itself, cuo-cym.32), the `party-player-talk` companions and a scripted leader. The
party starts in the street west of the Sweet Dreams inn in Britain; the leader says "Follow me, in we go.",
walks them onto the inn's ground floor (1496,1618), says "Wait here a moment, I'll be right back.", walks
out to the street at 1488,1616 and says "Come on out here, all of you." aloud.

```bash
cd brain
uv run python scripts/party_player.py --fleet fleets/party-player-inn.toml --walls --windowed
uv run python scripts/party_player.py --fleet fleets/party-player-inn.toml --walls --windowed --set hear_leader=false
```

| | passed on from the leader's client | as before (`hear_leader = false`) |
|---|---|---|
| inside when called, tiles from the leader, in its sight | 8, 8, 9; none in sight | 8, 9, 9; none in sight |
| the call read by | all three, as "come along" | nobody |
| passed on, after the line was said | 1.5 s, to each | |
| came out to the leader (within 3 tiles, 30 s) | all three | none |

- **The server sent the line to nobody inside;** each brain found it in the leader's client and passed it
  on. Their answers ("Right, I'm coming.") went out as speech does, so the leader saw none of them until
  they came out.
- **Where the leader stands matters:** in an earlier take from 1487,1621, five tiles further south, the
  server sent the call to all three directly (the client's sight rule sees 28 of the ground floor's tiles
  from there, through the west wall), and nothing was passed on. In the first take the walk out never
  started: `[AgentGo` had put everyone under the inn's floor, and the planner couldn't plan up the inn's
  steps from the street, a fault of 2026-10-19's change (cuo-u4j, [above](#a-tile-the-server-lets-nobody-onto)).

**The huddle names who goes in** (cuo-cym.14), 2026-10-14 01:30 to 03:50: the lich scene, `plan=huddle`
(a planner each and a plan the five agree), formed up, memory on, headless; three runs on the code as
built, then three after a fix, each set from one frozen copy. Measured with `scripts/party_scene.py` and the
rally records.

```bash
cd brain
uv run uo-brain fleet fleets/party-lich.toml --set plan=huddle --set form_up=true
```

| three runs each | as built | after the fix |
|---|---|---|
| members who died | 1, 2, 0 | 1, 1, 0 |
| the lich dead, from first sight | never, never, never | 69.3 s, never, never |
| seconds with someone apart, of the first 60 | 56, 59, 60 | 58, 0, 59 |
| the plan sent | all five, every run | all five, every run |
| the members' own goals in the run (the four who aren't the host) | | rest 22, 18, 27; to the healer 4, 5, 5; hunt 0, 2, 0 |

- **The acceptance isn't met.** The plan sent all five every time, and in most runs the warrior stood at the
  rally point with one or nobody beside him.
- **As built, one walked through the rally point into the spawn** (014959): the mage's own planner chose a
  trip to the graveyard itself, not a hunt, and the rule for one who has gone ahead sent the warrior in after
  her alone; two died. The fix stops a member's own trip into the hunt the plan sends it to at the rally
  point, and doesn't let one who walked on take a non-fighter in first.
- **What fails after it is the members' planners:** with their memories on they rest, or walk to the healer
  138 tiles off, rather than hunt; their lessons about the lich ("only enter when pulls can be taken one or
  two at a time") make each cautious, and the plan's list of who goes doesn't bind them.
- **And one more rule was wrong:** who has gone ahead was measured from where the one holding the rally
  stood, and the warrior holding it ran from a lich during the wait, so an archer standing still 13 tiles
  behind the point counted as ahead (031534). It is measured from the point now; not yet in a live run.

**The plan sends its members in, and holds them back** (cuo-cym.14, 2026-10-19 15:06 to 18:20): a member the
huddle's plan names for its hunt goes into it without its planner being asked, once a plan, when system one
reads the plan as sending the party in now (`huddle.go_now_question`, Decision 1); while the plan holds the
party back, a member's own hunt of that area is refused. The lich scene, `plan=huddle`, formed up, headless,
from frozen copies of the code. Two runs before the plan was read (the list of who goes alone) are counted
apart; a third was void (a login stuck at the server list).

```bash
cd brain
uv run uo-brain fleet fleets/party-lich.toml --set plan=huddle --set form_up=true                      # memory on
uv run uo-brain fleet fleets/party-lich.toml --set plan=huddle --set form_up=true --set memory=false
uv run python scripts/plan_check.py --out bench/2026-10-19-plans-asked.json                             # the plans, offline
```

| | sent by the list alone (2 runs) | memory on, the plan read (3 runs) | memory off, the plan read (3 runs) |
|---|---|---|---|
| the plans | all 6 said hold or scout first; 4 listed all five as going | all 5 held the party back (go now 0.002 to 0.007) | all 3 sent it in (0.993 to 0.995) |
| members sent in by the plan | all it listed, held or not | none | all five, within 1 s of each other |
| formed up at the edge, the warrior the point | 1 and 2 times, with members missing | not at all | every time |
| in the fight within, of each other | 37, 93 s | 19, 16, 18 s (with what came at them) | **5.0, 14.0, 6.6 s** |
| members who died | 0, 2 | 0, 0, 0 | 0, 0, 3 |
| the lich dead, from first sight | 53, 106 s | never, 145 s, never | 25, 35, 19 s |

- **Sent by the list alone, it went wrong:** the plans said "hold at the western edge, no entry yet" and
  listed all five as going, and the five sent in walked into the lich (two died, 154246). And a member sent
  in had its hunt cut short two seconds later by the plan's own watcher (51 tiles short), because the plan it
  was sent on counted as unread: it now counts as read.
- **The recorded plans, offline:** of the 21 the huddles agreed with a list of who goes (2026-10-14 and 19),
  the list alone sent the party in against the plan 13 times; Decision 1, which reads it now, once (19 of
  21 right; Jev 18).
- **With memory on, the plans hold the party back:** each said to enter only after a fresh scout shows no
  lich, so nobody was sent in. In the first of the three the bard's own planner hunted the graveyard anyway,
  15 s ahead of the rest; after that run a member's own hunt of the plan's area is refused while the plan
  holds back, and in the third the bard's was. The lich was killed once, by the bard and those who came to
  him.
- **With memory off, the plan sends all five at once and they go in together:** in each run all five were
  sent within a second, formed up on the warrior, and the lich died 19 to 35 s after first sight. The
  archer, the bard and the mage open fire from the edge as soon as they can: the warrior's own first blow came
  5.0, 8.9 and 6.6 s after the first of theirs, and in the second run the last in was 14 s after the first.
  In the third, three died (the warrior fled at 3% with skeletons on him, then the mage and the archer).
- **The acceptance isn't met as written:** with memory on the plans send nobody in, so "those the plan sends
  are in within a few seconds" can't be shown there; with it off the five were in within 5 and 7 s in two
  runs and 14 s in one.

**The party looks from the edge** (cuo-cym.14, 2026-10-27 15:18 to 16:45): a plan that holds the party back for
a sighting sends those it names to the edge together; the one holding the rally looks, and they go in only if
system one says what they see meets the plan's condition, or when the fight comes to them. The lich scene,
`plan=huddle`, formed up, memory on (the members remember every earlier run's deaths to the lich), on the brain
frozen at `src-frozen-27b`, the client in `bin/osx-arm64-next`, seven minutes between runs for the accounts.
Run 1 was filmed (the clients filming themselves); the others headless. Runs 1 and 2 had the first wording of the
question whether a plan waits for a look; run 3 on, the reworded one; run 5 also ends the look when something comes
at the party within 8 tiles.

```bash
cd brain
PYTHONPATH=$PWD/src-frozen-27b uv run uo-brain fleet fleets/party-lich.toml --set plan=huddle --set form_up=true \
  --out logs/fleet/20261027-155733-party-lich-huddle-look-3
uv run python scripts/party_scene.py logs/fleet/20261027-155733-party-lich-huddle-look-3
uv run python scripts/look_check.py --models decision1,jev --out bench/2026-10-27-look-asked.json   # the question, offline
```

| run | the plan's condition | read as a look | at the edge | first in | all in within | deaths | the lich dead |
|---|---|---|---|---|---|---|---|
| 1 (filmed) | "Scout ... first. Enter only if no lich is seen and only single or paired undead are in view" | 1.0 | no, no, then yes with a bone knight and a skeleton left in view (9 s) | Roland | 4.7 s | 0 | 24.5 s |
| 2 | "Before entering, a fresh scout from the western edge ... must show no lich" | 0.22 to 0.29 | nobody sent: the wave came to them resting at the start | Corwin | 11.1 s | 1 | no |
| 3 | "Hold at the western edge until a fresh scout confirms no lich in sight, then enter" | 1.0 | no, no as skeletons came, then yes (0.85) | Roland | 4.8 s | 0 | 41.3 s |
| 4 | "Scout first from the western edge ... no lich" | yes | the lich in view: no for 30 s while the wave came at them | Roland | 3.9 s | 1 (Roland) | 35.0 s |
| 5 | "Enter only after a fresh scout ... shows no lich and no pack of three or more" | yes | the lich in view: no; skeletons came at them, in as one at 4.6 s | Roland | 4.6 s | 1 (Roland) | 35.9 s |

- **The first time a memory-on huddle fought the lich.** In the three runs of 2026-10-19 every plan waited for a
  scout nobody made, and nobody went in. In four runs of five today the party went to the edge, looked and went
  in together behind the warrior, every member in within 3.9 to 4.8 s of the first, and the lich died every time.
  The acceptance (nobody in ahead of a fighter the plan expects; those it sends in within a few seconds of each
  other) holds in the three runs on the current wording, 3 to 5.
- **Run 2 is why the question was reworded:** "Before entering, a fresh scout ... must show no lich" was read as
  waiting for something other than a look. On 29 held-back plans labelled beforehand (26 from 2026-10-11 to 19 and
  three of today's), the first wording had Decision 1 right on 24, the reworded 27 (Jev 27 and 23); a choice
  question was 27 too but twice sent members to look while a dead companion was also waited for. A small set,
  and two of its cases are the ones the change was made for.
- **Run 4 is why the look ends when the fight comes:** the bard looked on for 30 s, saying no each time, while the
  lich and two bone knights walked up, and the party fought them at the edge with only what attacked each
  (defend-only) until he gave up. Since run 5, something in war mode within 8 tiles, or anyone in the party
  fighting, ends the look and they go in as one: in run 5 that came 4.6 s in.
- **The warrior died in runs 4 and 5,** taking the wave as the point (11% and 15% before the end in runs 3 and
  5 too). That is the formed-up fight's old weakness on this scene (three of fifteen fights before), not the look's.
- **Plans that wait for more than a look aren't looked at:** in runs 3 and 5 the later plans waited for Roland to be
  healed and restocked as well, and were read as no look (rightly); the party rested.

**A new person's words, by script** (cuo-cym.29, 2026-10-27 16:52, 17:08 and 17:24): what Harry was to play, played by
`scripts/party_player.py --fresh-words` as the leader: 17 lines a new person might type, in lower case and loosely,
none of them from the earlier playtests, each written down with what it means before the first run (memory:
a script asks what its builder thought of, so these come from a player's side). Three companions, `fleets/party-
player-talk.toml` (its run now 21 minutes), Decision 1 reading, Haiku writing the answers. The second run is on the
fixes the first asked for. No person has played it.

```bash
cd brain
PYTHONPATH=$PWD/src-frozen-27d UO_CLIENT_DIR=../bin/osx-arm64-next uv run python scripts/party_player.py \
  --fleet fleets/party-player-talk.toml --fresh-words --out logs/fleet/RUN       # writes RUN/fresh-words.json
```

| the line | meant | first run | second run (the fixes) |
|---|---|---|---|
| stick close to me lads | keep close | read as the follow they were on; distance unchanged | **close** ("Right at your elbow, Roland.") |
| spread out a bit, youre crowding me | further off | loose | loose |
| go easy, dont go chasing stuff | only what comes at us | reach: defend | defend |
| ok go hard, clear the place out | fight everything | aggression only; reach stayed at defend | aggression, and reach wide for 2 of 3 |
| shh keep it down for a bit | quiet | silent, but each said a sentence about it | **"Not another word."** from each |
| ok you can talk again | talk | chatty | chatty |
| grab any gold you see | loot | valuables (there is no gold-only level; gold is always taken) | valuables |
| leave the loot | no looting | nothing | nothing |
| anyone hurt? | a question to all | no order; three answers, 5 to 11 s | the same, 6 to 11 s |
| tamer, hows the bear? | Thea, by what she plays | Thea alone | Thea, then Wystan answering her |
| Isolde and Wystan wait here, then a walk of 12 tiles | those two stay, Thea comes | as meant (moved 0, 0, 6 tiles) | as meant |
| everyone with me | all follow | all follow | all follow |
| go wait for me at the healer | go there | all three to the Britain healer, willing 0.95 to 0.97 | the same |
| actually nah, come back | call back | said aloud from out of earshot: nobody heard (the script's mistake: the guide says party chat) | in party chat: all three came; **but they answered aloud**, out of the leader's hearing |
| what are you lot up to? / cheers guys, good work today / lol | talk; talk; nothing | said to nobody: they were at the healer | three answers; two answers; nothing |

- **What it asked for, built:** told to be quiet, what a companion still owes (the word on that order, a refusal)
  is said in its few plain words, not a written line; "stick close" to one already following is how close (a
  follow to one following was taken for the follow it was on); and a line in party chat is answered in party chat,
  both the word on an order and a written answer (the talk didn't hear the leader's party chat at all before; it now
  hears the person's, not the agents' own, whose callouts can go there).
- **A third run on all of it** (17:24, one more line, "where are you lot?" in party chat while they waited at the
  healer): each answered in party chat, 6, 10 and 14 s after ("At the healer's, Roland."), and "come back" in party
  chat brought all three back with answers the leader could read. The rest read as in the second run. One answer to
  "what are you lot up to?", just back beside the leader, still said they were at the healer's.
- **Left as it is:** one line is mostly read as speaking to one setting, so "go hard, clear the place out" can leave
  how far they go for a fight as it was ([known limits](known-limits.md)).

**A second scene: casters and archers from two sides** (cuo-cym.11), 2026-10-14 02:52 to 06:58. Every
party measure before was the lich scene. `fleets/party-casters.toml` stages the same five south of the
Britain graveyard against three skeletal mages and two skeletons where the lich's wave stood, north-west,
and two ratman archers and a bone magus 18 tiles east of them: six of the eight cast or shoot from range.
The three conditions in turn, six rounds, memory off, headless, one frozen copy of the code (f5); measured
with `scripts/party_scene.py --wave "skeletal mage,bone magi,ratman archer,skeleton" --boss ""`. The two
groups were first placed 26 tiles apart, and in two runs nobody saw the eastern one (the party fights the
first about 23 tiles from it, beyond the 18 tiles a client sees); those two runs aren't counted.

```bash
cd brain
uv run uo-brain fleet fleets/party-casters.toml --set memory=false
uv run uo-brain fleet fleets/party-casters.toml --set memory=false --set walk=true
uv run uo-brain fleet fleets/party-casters.toml --set memory=false --set form_up=true
```

| six runs each | the default | walking together | formed up |
|---|---|---|---|
| members who died | 0, 1, 2, 0, 2, 0 (5) | 0, 1, 0, 3, 1, 0 (5) | **0, 0, 0, 0, 0, 0** |
| who | the mage 4 times, the warrior twice | the mage 4 times, the warrior and the bard once | nobody |
| seconds with someone apart, of the first 60 | 33, 45, 45, 45, 52, 48 | 2, 0, 37, 36, 12, 0 | 0, 0, 0, 0, 47, 1 |
| leaves called in the run | 2, 12, 21, 2, 12, 3 | 2, 0, 2, 12, 1, 1 | 0, 1, 1, 1, 7, 2 |
| falls back to the rally point | (no rally) | (no rally) | 0, 1, 3, 1, 5, 2 |
| the wave's last creature seen, from first sight (median) | 217 s | 38 s | 44.5 s |
| runs in which the archers' group was fought | 2 | 4 | 5 |

- **Formed up nobody died in six runs, against five deaths each for the other two.** On the lich scene the
  three were closer (0 to 2 deaths a run each way); against creatures that stand off and cast, a party
  that goes in together and falls back to one place keeps its mage alive: she died in four of six runs on
  the default and in four of six walking together, never formed up.
- **Walking together didn't help here as it did on the lich scene:** the party arrives together and then
  spreads in the fight as on the default (someone apart for 36 and 37 s in two runs).
- **On the default the wave outlasted the party's stay in three runs** (its last creature seen 360 to
  463 s after first sight): leaves were called 12 to 21 times and the party left with the archers' group
  untouched. Formed up, the wave was dead within 26 to 47 s every time.
- **Formed up, a member fell back to the rally point in five runs of six,** and once (061515) the party
  was apart for 47 of the first 60 s and called 7 leaves; nobody died in it either.
- **What doesn't hold beyond the lich scene:** walking together is no help against casters; and the
  rally point was where the leader stopped (`rally_map` was off), so this says nothing of a chosen point.

## NPCs with lives of their own

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
  resurrected ([after a death](goals.md#after-a-death)).
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

## A hamlet of AI residents

**Four residents of Cove for an hour, told they live there, and the same four told nothing** (cuo-m3n),
2026-10-19 15:02 to 16:03 and 16:12 to 17:16, one run each, headless, on the npc profile, walking
(`rules = "realistic"`), memory on and cleared before each run, the same client build for both (from before
the savage fix: savages were not fought in either). Brannoc the watchman (home the east gate; work the orcs
near the Cove Orc Fort, 130 tiles south), Wenna the hunter (the small house on the square; the beasts
south-west of the Cove Cemetery, 120 tiles east), Maren the healer's apprentice (the healer's house) and
Tamsin the carrier (above the provisioner's; coin to a town's bank, the nearest 550 tiles off). In the
hamlet `edge = 10`; at home is within 6 tiles of it; measured from where each was every 30 s.

```bash
cd brain
uv run uo-brain fleet fleets/hamlet-cove.toml                       # logs/fleet/20261019-150247-hamlet-cove
uv run uo-brain fleet fleets/hamlet-cove.toml --set hamlet=false    # logs/fleet/20261019-161211-hamlet-cove-control
uv run uo-brain hamlet-report logs/fleet/RUN
```

| one run each | told of the hamlet | told nothing (the control) |
|---|---|---|
| share of the hour in Cove: Brannoc, Wenna, Maren, Tamsin | 79%, 17%, 55%, 74% | 61%, 38%, 1%, 49% |
| ...at home | 59%, 11%, 52%, 3% | 1%, 0%, 0%, 0% |
| homecomings (all four) | 10 (2, 2, 5, 1) | 1 |
| furthest from Cove, tiles | 222, 136, 128, 556 | 196, 727, 546, 643 |
| lines said to a neighbour (all four) | 46 (6, 21, 14, 5) | 8 (3, 2, 0, 3) |
| neighbours each talked with, of 3 | 3, 3, 2, 2 | 2, 1, 0, 2 |
| kills, deaths | 21, 4 | 28, 6 |
| cost together | $0.252, $0.246 an hour ($0.06 a resident) | $0.281, $0.268 an hour |

- **Told where they live, they live there:** they came home ten times to the control's once, spent a third to
  three fifths of the hour at home where the control spent none, and talked: 46 lines among themselves
  against 8. Maren and Wenna had 25 between them (Maren asking after everyone's health, Wenna's twenty
  arrows that "will not feed the hamlet through winter"). Brannoc, back from the dead at the healer's,
  told Maren to stay off the Fort road, and later Wenna; each kept it as a memory of the other.
- **Told nothing, they went where their long goals took them:** the control's Maren went to Britain to hunt and
  bank (1% of the hour in Cove, 546 tiles off), and Tamsin died three times by the orc fort.
- **The bar of half the hour in the hamlet, met by three of four:** Wenna, whose hunting ground is 120 tiles
  out, was in Cove 17% of the hour, less than the control's Wenna, who hunted Cove's own streets for a while.
  At that distance a hunt of 10 minutes and the walk back can't come to half an hour in, however often she
  comes home; the bar fits Cove's geography badly for a hunter (cuo-m3n.3).
- **The deaths are mostly savages:** the orc fort's spawns include savages, which the client took for red
  players and never fought (fixed after these runs, cuo-ea4). Brannoc died there three times in the first
  half hour, Tamsin three times in the control.
- **Work, as it is:** Wenna hunted and bought arrows, Maren practised her healing on hunts and stocked
  bandages, Tamsin hunted Cove's own vermin and took 347 gold to a bank, Brannoc spent the second half hour
  at the gatehouse with no gold or supplies after his deaths. No resident sold anything in either run.

### Three runs each way, with the savages fought (cuo-m3n.4)

2026-10-23, 15:30 to 22:23, the same file and measures as above, alternating told and control with 400 s
between runs, headless, memory on, and before each run the four residents' earlier memories set aside
(`stale = 1`, nothing deleted) so each starts from none. The client had the savage fix (cuo-ea4); the brain
was a copy frozen at 15:00 (1e6d6a417, before the lands and evenings). Not filmed.

```bash
cd brain
uv run uo-brain fleet fleets/hamlet-cove.toml --out logs/fleet/<time>-hamlet-cove-told
uv run uo-brain fleet fleets/hamlet-cove.toml --out logs/fleet/<time>-hamlet-cove-control --set hamlet=false
uv run uo-brain hamlet-report logs/fleet/20261023-153023-hamlet-cove-told logs/fleet/20261023-175327-hamlet-cove-told \
  logs/fleet/20261023-200959-hamlet-cove-told logs/fleet/20261023-164255-hamlet-cove-control \
  logs/fleet/20261023-190128-hamlet-cove-control logs/fleet/20261023-212031-hamlet-cove-control
```

| three runs each, in order | told of the hamlet | told nothing (the control) |
|---|---|---|
| homecomings (all four) | 10, 10, 8 | 3, 0, 0 |
| share of the hour in Cove (all four, by minutes) | 35%, 56%, 52% | 20%, 1%, 1% |
| ...Brannoc, Wenna, Maren, Tamsin, first run | 79%, 18%, 8%, 33% | 42%, 1%, 37%, 1% |
| ...second run | 62%, 88%\*, 4%, 96% | 0%, 1%, 1%, 1% |
| ...third run | 63%, 81%\*, 7%, 68% | 0%, 1%, 2%, 1% |
| home between two pieces of work (all four) | 6 of 16, 5 of 12, 5 of 14 | 2 of 36, 0 of 35, 0 of 38 |
| lines said to a neighbour (all four) | 41, 29, 23 | 25, 37, 3 |
| kills, deaths | 27 and 5, 14 and 3, 16 and 6 | 13 and 3, 39 and 0, 35 and 5 |
| cost together | $0.248, $0.241, $0.278 | $0.290, $0.275, $0.281 |

\* Wenna stopped her session after 13 and 34 minutes: the shares are of her minutes.

- **Coming home holds up:** told where they live, the four came home 8 to 10 times an hour in every run, and
  the control 0 to 3 times (2026-10-19: 10 and 1). Told, they were in Cove a third to over half the hour;
  the control's last two runs spent 1% of it there, gone to Britain, Vesper and the orc fort for their long
  goals. Brannoc the watchman was in Cove 62% to 79% of every told hour and came home 4 to 6 times.
- **Talking doesn't:** the 46 lines against 8 of 2026-10-19 were one pair of runs. Here the told runs said 23 to
  41 lines and the control 3 to 37: the control's second run said 37, at the hunting grounds they happened to
  share (Tamsin and Maren north of Britain, Brannoc and Tamsin by the orc fort). They talk where they meet,
  and being told of the hamlet makes them meet at home only as often as it brings them there.
- **Maren isn't at the healer's:** told her place is the healer's house, she was there 2% to 6% of the hour and
  in Cove 4% to 8%, out on hunts in the woods 120 tiles east to practise her healing and twice to a town's
  bank 580 tiles off (2026-10-19: 55%). The work she was given takes her away; the hamlet's lands
  (cuo-m3n.3) count the woods as at work.
- **A hunter who stopped for want of arrows she had:** in the second and third told runs Wenna stopped (the
  `finish` goal) after 13 and 34 minutes, with "Archery training is blocked: Cove has no arrow seller". She
  had 300 arrows: the planner was never told an archer's arrows, so it set out to buy some, and the world
  store had lost Cove's provisioner's wares when a sighting 2 tiles off replaced the imported shop (53
  sightings had lost theirs). Told nothing, she went to Vesper for arrows and never stopped. Both fixed
  after these runs (541e5d115). The arrows ran through the talk too: Tamsin offered to "carry your order if
  you pay".
- **Deaths with the savages fought:** 3 to 6 a told run, 0 to 5 a control, most of them Brannoc by the orc
  fort (3 or 4 in each told run), whose work is thinning out its orcs; none was a savage left unfought.
- **What they kept of each other:** every resident kept memories of its neighbours in the told runs (7 to 23
  between them; the control 2 to 16), most of them talks ("Not hurt, just tired of walking to Vesper for
  arrows"), and a few warnings passed on ("Told Wenna: the four at Cove Orc Fort").

### The hamlet's lands, five runs (cuo-m3n.3, cuo-m3n.11, cuo-m3n.15)

2026-10-23/24, headless, memory on, the residents' memories set aside before each run, on a copy of the brain
frozen at 541e5d115 (the arrows and the shop's wares fixed, before the savages' bestiary fix and the store
not yet re-imported) and the client of cuo-m3n.4. One told run of `hamlet-cove.toml` (22:40) as the same
code's baseline, then `hamlet-cove-lands.toml` (23:53), whose `[hamlet]` names the two grounds as its lands
and whose work is put as short pieces with home between. Every run measured against those lands, the three
told runs of cuo-m3n.4 too. Not filmed.

```bash
cd brain
uv run uo-brain fleet fleets/hamlet-cove.toml --out logs/fleet/20261023-224011-hamlet-cove-told-fixed
uv run uo-brain fleet fleets/hamlet-cove-lands.toml --out logs/fleet/20261023-235315-hamlet-cove-lands
uv run uo-brain hamlet-report RUN --lands "south-west of Cove Cemetery" --lands "near Cove Orc Fort" --json
```

| | m3n.4's told runs (1e6d6a417) | told, 541e5d115 | with its lands, 541e5d115 | with its lands again, c72c1e616 | and the empty ground, 4d93f759d | and grounds named, dffc4b370 |
|---|---|---|---|---|---|---|
| in Cove or its lands (all four, by minutes) | 66%, 81%, 76% | 71% | 72% | 81% | 64% | 63% |
| ...Brannoc, Wenna, Maren, Tamsin | first: 80%, 92%, 57%, 33% | 66%, 87%, 92%, 42% | 92%, 92%, 86%, 28% | 72%, 89%, 88%, 75% | 48%, 83%, 85%, 38% | 72%, 52%, 90%, 40% |
| in Cove itself | 35%, 56%, 52% | 31% | 36% | 35% | 53% | 54% |
| home between two pieces of work | 6 of 16, 5 of 12, 5 of 14 | 11 of 24 | 4 of 15 | 9 of 20 | 10 of 20 | 8 of 22 |
| homecomings | 10, 10, 8 | 8 | 8 | 10 | 15 | 14 |
| lines said to a neighbour | 41, 29, 23 | 43 | 36 | 56 | 29 | 42 |
| kills, deaths | 27 and 5, 14 and 3, 16 and 6 | 19 and 1 | 14 and 8 | 28 and 3 | 7 and 6 | 24 and 4 |
| cost together | $0.248, $0.241, $0.278 | $0.328 | $0.252 | $0.297 | $0.299 | $0.315 |

- **Naming the lands changed nothing measured:** told only that they live in Cove, the four were in Cove or
  on its two grounds 66% to 81% of the hour; told of the lands as well, 72%. Wenna and Maren were there 86%
  to 92% of the hour either way. Not the acceptance (every resident most of the hour, and home between
  pieces of work): Tamsin, the carrier, was there 28%, her work being the banks of Vesper and Britain.
- **Home between pieces of work went down,** 4 of 15 against 5 to 11 of 12 to 24, with the deaths: Brannoc
  and Tamsin both died within two minutes of setting out for the orc fort, to named savages ("Olaufee,
  Cordee, Arda, Nasha, Vaype"). Brannoc then stayed at the gate, resting, and stopped the session after 30
  minutes ("the route past 2150,1350 to Cove Orc Fort killed him, and Cove Cemetery skeletons and a lich are
  too"). Tamsin died 5 times: resurrected out west by the wandering healer by the fort, she died twice more
  there, was told "no route: too long a search" walking home from 2000,1465 (cuo-m3n.7, fixed in the client
  since), died on the way back for her corpse, and as a ghost walked to five healers over 15 minutes before
  one offered (cuo-m3n.9).
- **The baseline run's watchman didn't die:** in the told run of 22:40 Brannoc was driven off the fort without
  a death, then waited at home for the 30-minute rule and the liches (59% of the hour at home, 1 kill). One
  run; m3n.4's told runs had him die 3 or 4 times.
- **Again, with what was fixed overnight** (2026-10-24 05:26 to 06:32, `--out logs/fleet/20261024-052658-hamlet-cove-lands-b`,
  the brain frozen at c72c1e616, the store re-imported at 05:20 with the savages and the horses known, the
  client with the route fixes): every resident was in Cove or its lands most of the hour (72% to 89%), Tamsin
  too, and Brannoc came through it alive with 13 kills. Home between pieces of work, the acceptance's other
  half, was 9 of 20. One run.
- **Who still died:** Tamsin twice and Maren once. Tamsin went to hunt the orc fort for coin and died in
  90 s, then died walking home from a corpse 20 minutes gone (no walk is begun after 14 minutes since
  850b60c62).
- **Wenna stood 14.6 minutes on her ground among horses, eagles and bulls,** now known as only fighting
  back. With nothing hostile in sight the routine asked whether to walk elsewhere in the spawn, and Jev said
  no each time (0.2): the spawn spreads over 15 tiles, inside what she sees. The boars and cougars she hunts
  had been killed and hadn't come back. Her planner sent her back to the same ground after each hunt.
- **And with the empty ground** (2026-10-24 09:31 to 10:36, `--out logs/fleet/20261024-093147-hamlet-cove-lands-c`,
  the brain frozen at 4d93f759d: a hunt told its spawn's real spread and patrolling across it, a ground found
  empty not hunted again for 15 minutes; the client that keeps its bones). It began 7 minutes after the told
  hour of 08:20, in which Wenna and Maren had killed 22 on the cemetery ground. One run. Not filmed.
- **The hunters moved on from an empty ground** (cuo-m3n.11): Wenna's five hunts on the cemetery ground and
  the wilds by it ended in 2.9 to 7.4 minutes, four on "Jev was unsure twice running" and one, after 4
  kills, on "walked round the spawn 4 times and found nothing", against two of 10 minutes or more standing
  still the time before. Twice, going straight back, she was told the ground had been found empty and to come back in 14
  minutes. Maren's two there ended in 3.9 and 4.9 minutes.
- **But sent elsewhere, they went where they couldn't fight.** Wenna and Maren were both refused the
  cemetery ground and went to the orc fort ("I'll shoot at the orcs near the fort"): Wenna left at once with a
  kill, and Maren died on the way, to two savage riders. Maren later hunted the liches' ground east of Cove,
  left it in 12 s, and a lich followed her 173 tiles home and killed her at the healer's house, "under the
  protection of the town guards": no guard came, and nobody called one. Tamsin hunted by Cove Cemetery and
  died to a lich there. The refusal says "hunt another ground", and nothing in it, or in the planner's choice,
  keeps that ground one the character can fight (cuo-m3n.15; the guards, cuo-m3n.14).
- **Six deaths:** Brannoc twice at the fort (his first sweep, 0.2 minutes in, and the corpse walk), Maren
  twice and Tamsin twice as above, Tamsin's second on the bank road at 1931,1341, west of the fort (the
  travel before cuo-m3n.13). In Cove or its lands 64% of the hour, Brannoc 48% and Tamsin 38%: not the
  acceptance.
- **And with the grounds named** (2026-10-24 11:53 to 12:56, `--out logs/fleet/20261024-115335-hamlet-cove-lands-d`,
  the brain frozen at dffc4b370: a refused hunt names the nearest grounds where nothing is stronger than the
  character; the priced travel too). It began 7 minutes after the told hour of 10:42. One run. Not filmed.
- **Told where else, she went there** (cuo-m3n.15): the refusal came once, to Wenna, back at the emptied cemetery
  ground: "Grounds within 250 tiles where nothing is stronger than you: south-west of Covetous Entrance (190 tiles:
  boar, cougar, goat, horse); north-east of Cove Guard Post (200 tiles: ...)". She waited out its last minute and
  hunted the cemetery ground again (empty, 3.4 minutes), then went to the second ground named: 10 kills in 7.7
  minutes, then 5 more. Nobody sent off an emptied ground died at the next hunt. But after a hunt that found its
  ground empty, before any refusal, both Wenna and Maren chose the orc fort, and left it alive within 1.2 minutes.
- **Four deaths:** Brannoc twice (on the fort road, his own sweep, then on a `go_anyway` walk back to the corpse);
  Wenna at her door in Cove, to a savage rider that followed her home from the fort and took her from full health
  to a fifth in one blow, nobody calling the guards; Tamsin to the liches, setting out for the bank with 537 gold
  from their ground east of Cove. Brannoc and Tamsin also walked out four times between them to hunt with no
  bandages or potions, and Brannoc to the bank for gold it doesn't hold (all since fixed, cuo-m3n.16). Wenna's best
  ground is 200 tiles out and not in the file's lands, and Tamsin hunted the liches' ground: in Cove or its lands
  63%, Wenna 52% and Tamsin 40%. Not the acceptance (cuo-m3n.3).

### Evenings at the healer's house, two runs (cuo-m3n.5)

2026-10-24 01:05 to 04:10, headless, memory on, the residents' memories set aside first, on the same frozen
copy as the lands run (541e5d115: before the savages' fix, the early evening and the table's fixes below) and
the client of cuo-m3n.4. `hamlet-cove-evenings.toml`: the four with their lands, days of an hour whose last
quarter is the evening, the gathering at the healer's house (Maren's home). One run. Not filmed.

```bash
cd brain
uv run uo-brain fleet fleets/hamlet-cove-evenings.toml --out logs/fleet/20261024-010548-hamlet-cove-evenings
uv run uo-brain hamlet-report logs/fleet/20261024-010548-hamlet-cove-evenings
```

| | Brannoc | Wenna | Maren | Tamsin |
|---|---|---|---|---|
| in Cove / Cove or its lands | 54% / 63% | 33% / 72% | 60% / 86% | 79% / 80% |
| homecomings | 13 | 8 | 8 | 8 |
| evening goals (refused as early) | 12 (8) | 8 (3) | 10 (7) | 14 (11) |
| tables kept, sat at | 1, 3 | 3, 2 | 1, 2 | 2, 1 |
| memories kept at them | 19 | 20 | 13 | 13 |
| later planner calls that saw one | 22 | 25 | 23 | 27 |
| deaths, minutes resting | 5, 72 | 3, 43 | 3, 91 | 3, 110 |

All four, three hours: 10 kills, 14 deaths, $0.897 ($0.292 an hour).

- **Three evenings, seven tables, 72 lines.** Every resident came to every evening. Four tables were a real
  talk between neighbours: evening 1 had Wenna, Maren and Brannoc (12 lines), then Brannoc and Tamsin (9).
  Evening 2 had Maren and Wenna (12). Evening 3 had Tamsin, Maren and Wenna (12).
- **Three tables were one resident talking alone,** 27 lines in all, from two faults (fixed since,
  2026-10-24). The keeper counted as present anyone standing within 8 tiles of the healer's house. That
  included Maren, who lives there and was resting, and the turns named for her went unanswered: Wenna kept two
  tables "with" her, nine lines of her own each, down to "Maren, I've asked four times now. Look at the bite,
  or I'll go find Tamsin." And one who sat down after a table opened was never named: Brannoc sat through
  tables 3 and 5 without a line, and kept memories of both.
- **What they remember of each other** is the day's news and each other's needs. Maren: "Brannoc had only bread
  at dawn and went hungry most of the day, so he needs tea and a warm seat by the fire." Wenna: "Tamsin got
  twenty Clean Bandages from Vesper, has fifty in mind, and is short on gold; she will help fetch my corpse
  tomorrow." Tamsin: "Tomorrow I'll see about fetching Wenna's corpse from the open ground east of Minoc, but
  only with company." Brannoc: "Maren cooks for the hamlet and made sure I sat by the fire and got tea."
- **Some of it was acted on.** After the first evening, Maren bought bread and then meat because "Wenna and
  Brannoc have had little to eat since dawn". She also walked to the gate to look in on Brannoc. Tamsin went to
  the gatehouse twice for the bandages Brannoc had offered her at the table. Wenna planned the trees north of
  the mill for the next day three times, then hunted south-west of the cemetery again.
- **The deaths emptied their pockets.** Five of the 14 deaths were to the savages by the orc fort and four on
  long trips north (to the East Britain bank, Minoc). Of 12 tries at a corpse one got it back (Maren's); the
  rest found it gone or died again on the way. From the second day Tamsin, Brannoc, Wenna and then Maren had no
  gold or bandages and rested at home "until the evening gathering": Tamsin rested 110 of her 184 minutes. The store didn't yet know the savages, and the corpse
  walk's rule was not yet in (cuo-m3n.6).
- **29 evening goals were refused as early,** most a minute before the evening began. Since c72c1e616 the goal
  goes from 3 minutes before it, and the resident waits there.

**The check of the table's fixes.** 2026-10-24 06:40 to 08:13, the same four on a copy frozen at aac874ee3 (the
table for those who came, the late-comer's turns, the early evening), the client of cuo-m3n.4, headless, memory
on, the residents' memories set aside first. Shorter days, to have three evenings in an hour and a half:
`hamlet-cove-evenings-check.toml` is the same file with `hours = 1.5`, and in `[hamlet]` `day_minutes = 30` and
`evening_minutes = 10`. One run. Not filmed.

```bash
cd brain
uv run uo-brain fleet fleets/hamlet-cove-evenings-check.toml --out logs/fleet/20261024-064014-hamlet-cove-evenings-check
uv run uo-brain hamlet-report logs/fleet/20261024-064014-hamlet-cove-evenings-check
```

| | Brannoc | Wenna | Maren | Tamsin |
|---|---|---|---|---|
| in Cove / Cove or its lands | 75% / 86% | 59% / 95% | 75% / 95% | 8% / 8% |
| evening goals (refused as early) | 6 (2) | 11 (4) | 5 (2) | 1 (0) |
| tables kept, sat at | 1, 3 | 2, 2 | 1, 2 | 0, 1 |
| lines at them, memories kept | 19, 18 | 15, 20 | 12, 15 | 3, 5 |
| deaths | 2 | 0 | 0 | 3 |

All four, an hour and a half: 8 kills, 5 deaths, $0.448 ($0.289 an hour).

- **Four tables, 49 lines, every one a talk.** Evening 1: Brannoc kept it with Maren and Wenna, the three who
  had come (13 lines). Evening 2: Wenna, Brannoc and Maren (12). Evening 3: Maren, Brannoc and Wenna (12), and
  then Wenna and Brannoc again, who came back after it closed (12). Nobody was named who wasn't there and no
  turn was skipped; the most one resident said at a table was half its lines, at the table of two.
- **The late-comer was given turns.** Tamsin sat at the first table after it opened and said three lines: the
  liches she had seen by the cemetery road, twice, and her plan for tomorrow ("I'll run the Yew road with the
  early carts"). In the first run Brannoc sat through two tables like that without a word.
- **Tamsin came to one evening of three.** She spent the run on the road to Britain and back (8% of it in Cove),
  died three times and banked 719 gold. Nobody is sent to the evening, and a carrier is often away.
- **8 evening goals of 23 were refused as early,** against 29 of 44 in the first run. Six said "in about 3
  minutes": the turn's words floor the minutes, so 3:01 to 3:59 read as 3 and the rule refused them; Wenna asked
  four times in six seconds. Since then the goal goes while the words say 3 or fewer, and a refusal says from
  when it will.

### The watchman's hour with the savages known (cuo-m3n.6)

2026-10-24 04:17 to 05:20, one told run of `hamlet-cove.toml`, headless, memory on, the residents' memories set
aside first, the store re-imported just before (savages known by both bodies: a savage "strong", 23 to 27 a
blow), the brain frozen at c72c1e616 (the corpse walk's danger rule in the wilds, the healer stand-off) and the
client with the route fixes. Not filmed.

```bash
cd brain
uv run uo-brain world import-modernuo
uv run uo-brain fleet fleets/hamlet-cove.toml --out logs/fleet/20261024-041754-hamlet-cove-told-m3n6
uv run uo-brain hamlet-report logs/fleet/20261024-041754-hamlet-cove-told-m3n6 --lands "south-west of Cove Cemetery" --lands "near Cove Orc Fort"
```

All four: 21 kills, 3 deaths, $0.323. Brannoc died 3 times (m3n.4's told runs: 3 to 6 among the four, most of
them his; the told run of 22:40: none), and nobody else died. In Cove 67% of the hour, at home 40%.

- **04:26, back to the fort two minutes after leaving it.** His first sweep ended as a red player came close,
  savages stronger than him in view; he went home, bought 10 bandages and went straight back, and died in 50 s
  to three savages and a rider. A hunt ended by a red or criminal player now keeps the character off that spot
  for 30 minutes, as a leave did.
- **04:38, `go_anyway`.** At 04:32 the corpse walk was refused ("danger was seen by the corpse ... 7 min ago");
  five minutes later his planner went anyway, "at full health, so I'll go now, carefully", and he died on the
  way. Left to the planner, which is now told the minutes the corpse has left.
- **05:06, a corpse long gone.** He walked back 27 minutes after the death, once the danger had aged past 15
  minutes, found the corpse gone, and died among the savages by it. A corpse lasts 14 minutes; no walk is now
  begun after that ([after a death](#after-a-death)).
- Next: a told run with these changes and the client that keeps its bones.

### The watchman's hour with his bones kept (cuo-m3n.6)

2026-10-24 08:20 to 09:24, one told run of `hamlet-cove.toml`, headless, memory on, the residents' memories set
aside first, the brain frozen at 850b60c62 (a corpse kept as bones and no walk to it after 14 minutes, the
planner told the minutes it has left, a spot a red player drove the character off kept for 30 minutes) and the
client that keeps its own corpse as bones. Not filmed.

```bash
cd brain
uv run uo-brain fleet fleets/hamlet-cove.toml --out logs/fleet/20261024-082030-hamlet-cove-told-m3n6b
uv run uo-brain hamlet-report logs/fleet/20261024-082030-hamlet-cove-told-m3n6b --lands "south-west of Cove Cemetery" --lands "near Cove Orc Fort"
```

All four: 22 kills, 3 deaths, $0.285. Brannoc died once (the hour before: 3; m3n.4's told runs: 3 or 4), Tamsin
twice, nobody else. Brannoc was in Cove 83% of the hour and at home 72%, and killed nothing.

- **His one death was a lich that followed him home.** Driven off the fort by two savages and then off the
  wilderness east by liches, he walked home at 59 of 95 and set off for the healer's, 43 tiles, for bandages. A
  lich had come in after him (seen by 2250,1200); fleeing it he left the guards' ground east of the gate, was
  paralysed ("You cannot move!") and died in 14 s at 2323,1227. Four minutes later the corpse walk was refused
  for the lich seen by it, his planner went anyway, and he took everything back in 20 s.
- **He did no work.** Both his sweeps at the fort ended inside half a minute, "2 coming at once: 2 savages
  (stronger than you each)", and the 30-minute rule kept him off it in between; he rested at home 5 to 10
  minutes at a time for most of the hour. Both spawners at the fort hold savages among the orcs, so the work
  the fleet file gives him isn't one a lone warrior of his kit can do (cuo-m3n.12).
- **Tamsin died twice on the road to Britain's bank.** Her road from Cove crosses the swamp at about 2000 to
  2080, 1000 to 1070 (plague beasts, bog things, slimes): she walked through it to bank 300 gold, died in it
  walking home (09:00, at 2078,1068), and again going back for the gold (09:19, at 2055,1009). Travel takes the
  shortest road and doesn't go round what it saw on it (cuo-m3n.13).
- cuo-m3n.6 is closed: the watchman died less than twice in the hour.

### The road round the swamp (cuo-m3n.13)

2026-10-24, the soak server (2595), one headless client, a warrior of 95 hits (Brutus), the client with the priced
ground and the store as the hamlet runs left it. The plans come from the `nav` view, hop by hop, without and with
the squares the session would send (`World.dangers_between`: 48 between Cove and Britain, the swamp's eleven
spawners among them); the walk is one `do travel`, at a run, with the fight loop beside it. Single runs.

```bash
cd brain
uv run uo-brain --port 5580 client --headless --account warrior --character Brutus --server-port 2595
uv run python scripts/route_check.py 5580 2216 1194 1650 1608    # and 5580 1650 1608 2216 1194
uv run uo-brain --port 5580 say "[AgentGo 2216 1194"
uv run uo-brain --port 5580 do travel "East Britain bank" --defend --judge jev --timeout 900
```

| plan | steps | on priced ground | sampled points on far-stronger ground | planning, the longest hop |
|---|---|---|---|---|
| Cove to East Britain bank, as before | 1260 | (not priced) | 5 of 40 (about 160 steps) | 44 ms, 133,038 nodes |
| the same, priced | 1380 | 42 | 2 of 44 | 75 ms, 135,456 nodes (a second plan: 1381 steps, 60 ms) |
| East Britain bank to Cove, as before | 1272 | (not priced) | 6 of 41 | 117 ms, 530,442 nodes |
| the same, priced | 1387 | 43 | 1 of 44 | 133 ms, 543,658 nodes |

- **Round the west of the swamp:** both ways the old road ran south-west through the swamp's east side (2094,972
  to 1999,1100); the priced one keeps along its north edge at y 905 to 920 and goes down its west side at x 1915
  to 1960, through ground of fair fights for a new warrior (earth elementals, ettins, ogres, gargoyles). The 42
  steps left are at its north-west corner. 120 steps longer.
- **The walk** arrived in 337 s (the hamlet's walks through the swamp: 495 to 562 s, at a walk), lowest health
  71 of 95, 40 leave decisions, nothing fought. In view on the way: reapers and an ogre east of Britain, a corpser,
  a gazer, zombies, a dire wolf, and a bog thing from the swamp's edge. Its `travelled` record: `avoiding` 48,
  `priced_steps` 42 on the first hop and 0 on the second.
- **The road ahead:** the snapshot's `travel.ahead` gave the plan's tile 10 steps on all the way (at 2258,933 it
  pointed to 2262,925, north-west with the road, while Britain lay south-west).

#### In a told hour, then the ground widened

2026-10-24, the test server (2593), one told hour of the hamlet on that brain (90c3f5dbd) and client, headless,
memory on, the residents' memories set aside. Single run.

```bash
cd brain
uv run uo-brain fleet fleets/hamlet-cove.toml --out logs/fleet/20261024-104251-hamlet-cove-told-m3n13
uv run uo-brain hamlet-report logs/fleet/20261024-104251-hamlet-cove-told-m3n13 \
  --lands "south-west of Cove Cemetery" --lands "near Cove Orc Fort"
```

- **The hour:** 13 kills, 6 deaths counted (Brannoc 4, Tamsin 2, and a third of hers in the hour's last
  minute), $0.352. Wenna 9 kills and Maren 4, neither dying; in Cove or its lands Brannoc 43%, Wenna 83%, Maren
  89%, Tamsin 52%; home between work 13 of 25.
- **The bank road:** Tamsin banked 300 gold at Vesper (298 s there, 324 s back, `priced_steps` 6 and 0), and
  Brannoc made three round trips to it, all arrived. Told to draw from "the Vesper bank", she was sent to
  Britain's, as near from where she stood in Cove, and died on the way twice: once at 1905,1266 to two reapers,
  a gazer, a gargoyle and a corpser together, each a fair fight and so not priced, from the spawners west of the
  swamp (89% to dead in 5 s); once at 2042,880, the plan's line along the swamp's north edge, where a bog thing
  came out to her from its spawn (100% to 18% in 3 s). The swamp itself was gone round.
- **Brannoc** died on the fort road (his own hunt there), then went back for his corpse with `go_anyway` three
  times and died each time; then three trips to Vesper's bank, 10 minutes each, for "moved 0 items" (the box
  holds no gold), and one to hunt by Vesper with no bandages or potions, which stopped at once.

So the ground priced grew (cuo-m3n.13): each square reaches `NOTICE_RANGE` (12) beyond the walk range, as far as
a monster notices a character from (10 for most of ModernUO's), and a spawner of fair fights is priced as
stronger when those it holds at once have three times the character's hits between them. The bank goal takes the
town whose bank (`place`), and says when a withdrawal of gold brought none; a hunt with nothing to heal by is
refused before the walk; and two deaths by a corpse end the going back for it, `go_anyway` or not. The plans again,
from the Cove healer and from Cove, for Brutus (95 hits), single runs:

```bash
uv run python scripts/route_check.py 5580 2245 1229 1653 1610    # to East Britain bank
uv run python scripts/route_check.py 5580 2217 1192 2880 683     # to the Vesper minter
```

| plan | steps | on priced ground | sampled points on far-stronger / stronger ground |
|---|---|---|---|
| the Cove healer to East Britain bank, as before | 1266 | (not priced) | 6 / 13 of 41 |
| the same, priced (77 squares) | 1478 | 208 | 3 / 4 of 47 |
| Cove to the Vesper minter, as before | 765 | (not priced) | 0 / 10 of 26 |
| the same, priced (80 squares) | 872 | 22 | 0 / 0 of 28 |

The Britain road can't go round all of it: from Cove the only way west passes between the swamp and the fields of
fair fights, so the Vesper bank is the safer one by far, and the planner can now name it.

#### A second told hour, on the widened ground

2026-10-24, the test server (2593), one told hour of the hamlet on the brain of 3c9cfa38e (the widened ground, the
bank's town, the hunt with no supplies and the third corpse run refused, the guards called), the same client,
headless, memory on, the residents' memories set aside. Single run.

```bash
cd brain
uv run uo-brain fleet fleets/hamlet-cove.toml --out logs/fleet/20261024-130311-hamlet-cove-told-m3n16
uv run uo-brain hamlet-report logs/fleet/20261024-130311-hamlet-cove-told-m3n16 \
  --lands "south-west of Cove Cemetery" --lands "near Cove Orc Fort"
```

- **The hour:** 25 kills, no deaths, $0.289 ($0.271 an hour). Wenna 16 kills, Brannoc 6, Tamsin 2, Maren 1; in
  Cove or its lands Brannoc 28%, Wenna 70%, Maren 94%, Tamsin 63% (64% of the four hours together); home between
  work 7 of 23.
- **The bank road:** Tamsin's first trip, to the nearest bank with no town named, was Britain's east bank: a plan
  of 1176 steps, 107 on priced ground, walked at a resident's pace and cut off at 600 s, 62 tiles short (bog things,
  a plague beast, a wisp and gorillas seen on the way). Told "Britain" from there, she was told it had no bank (its
  west bank is 70 tiles from the town's middle); told "West Britain", she walked 282 tiles to it in 120 s and banked
  300 gold. The way home was cut off at 600 s too, 246 tiles short north of the swamp, and the next trip arrived in
  151 s. Nothing hit her on either road.
- **Brannoc** bought bandages and hunted the fort and then the wilderness 155 tiles east, 6 kills and no deaths;
  no corpse run, no trip to the bank, no hunt with nothing to heal by. He stood still there for 10 minutes on his
  last hunt and was still away at the end.
- **No call for the guards:** nothing turned criminal near a resident in Cove.

So a bank "at" a town is the nearest of the town's own banks, and a trip still on its way at its time limit goes
on ([travel](goals.md#travel-banking-shops-and-hunting)). From the east bank, "Britain" is that bank now, and from
Cove too; the Vesper bank is not taken unless named, though its road is the safer.

### The bank chosen by its road (cuo-m3n.17)

2026-10-27, the test server (2593), one headless client as Tamsin (the hamlet's carrier, a warrior of 95 hits) with
the client that plans from a given tile (`nav`'s `from_x`, `from_y`, `from_z`) and the brain that compares the
nearer banks' roads before a bank trip with no town named. Each road is planned hop by hop from where the last
hop ends, with the squares the trip would send (`World.dangers_between`), and costs its steps plus five for each
step on priced ground. The trips are `do bank`, with no fight loop beside them. Single runs.

```bash
cd brain
uv run uo-brain --port 5674 client --headless --account headless6 --password headless6 --character Tamsin --server-port 2593
uv run uo-brain --port 5674 say "[AgentGo 2216 1194"          # her home in Cove; then [AgentKit warrior, [AgentSupplies
uv run uo-brain --port 5674 do bank --deposit gold
uv run uo-brain --port 5674 say "[AgentGo 1475 1645"          # a street in Britain
uv run uo-brain --port 5674 do bank --deposit gold
```

| from | East Britain bank (566 tiles from her home) | Minoc's bank (642) | Vesper's bank (663) | chosen |
|---|---|---|---|---|
| her home, 2216,1194 | 1406 steps, 272 priced, cost 2766 | 946, 41, 1151 | 882, 63, 1197 | Minoc's |
| the healer's house, 2244,1229 | 1480, 241, 2685 | 978, 41, 1183 | 864, 41, 1069 | Vesper's |
| the gatehouse, 2283,1209 | 1426, 214, 2496 | 960, 20, 1060 | 811, 20, 911 | Vesper's |
| Wenna's house, 2235,1187 | 1458, 234, 2628 | 953, 41, 1158 | 847, 63, 1162 | Minoc's |

- **Never Britain's:** from four spots in Cove the East Britain bank's road cost 2.2 to 2.7 times the chosen one's,
  with 214 to 272 steps on priced ground against 20 to 63. Minoc's bank, 21 tiles nearer than Vesper's from her
  home, was a third bank in the margin (1.25 times the nearest), and between Minoc's and Vesper's it came down to a
  few steps (1158 against 1162 from Wenna's). The bead asked for Vesper's; it is either of the two safe ones.
- **The walk** from her home to Minoc's bank arrived in 215 s (418 tiles a hop, two hops), 300 gold banked, lowest
  health 79 of 95 (a panther, wolves and an eagle on the way, bandaged). From the street in Britain the Britain
  banker, 62 tiles off, was gone to without comparing anything (the nearest is within 150 tiles), in 15 s.
- **Planning the three roads** took 144 to 404 ms of the game's time in all, over 6 to 8 calls (20 to 192 ms a road),
  and is kept for the session: a second trip from the same 16-tile cell plans nothing.

### Home between work, recounted (cuo-m3n.3)

`hamlet-report` measured where a resident was from the fleet's look at each one every 30 s, so a resident who got
home and set out again within 30 s wasn't seen there. In the lands hour of 2026-10-24 (lands-d) Wenna's trip home
arrived at 9.75 minutes and her next hunt set out 3 s later; the report had her not home between them. Since
2026-10-27 a trip home that arrived (the session's `travelled` record) counts as being at home then, for home
between work and homecomings; time at home is still the looks. Every earlier run recounted, the same command:

```bash
cd brain
uv run uo-brain hamlet-report logs/fleet/RUN --lands "south-west of Cove Cemetery" --lands "near Cove Orc Fort" --json
```

| run | home between work, as reported | recounted | homecomings, recounted |
|---|---|---|---|
| m3n.4's three told runs | 6 of 16, 5 of 12, 5 of 14 | 12 of 16, 8 of 12, 8 of 14 | 16, 12, 10 |
| m3n.4's three control runs | 2 of 36, 0 of 35, 0 of 38 | the same | 3, 0, 0 |
| told, 541e5d115 | 11 of 24 | 16 of 24 | 14 |
| the lands, 541e5d115 | 4 of 15 | 11 of 15 | 16 |
| the lands again, c72c1e616 | 9 of 20 | 11 of 20 | 12 |
| and the empty ground, 4d93f759d | 10 of 20 | 13 of 20 | 19 |
| and grounds named, dffc4b370 | 8 of 22 | 16 of 22 | 21 |
| told, on the priced travel, 90c3f5dbd | 13 of 25 | 17 of 25 | 15 |
| a second told hour, 3c9cfa38e | 7 of 23 | 11 of 23 | 14 |

- **Told where they live, residents mostly do come home between pieces of work:** about half to three quarters
  of the time in each told and lands hour (11 of 23 to 12 of 16; 123 of 191 in the ten), against 0 to 2 of 35 to
  38 in the control, which never goes home; the old count had it at half or under in every told run but one. What
  is left is mostly Tamsin, 18 of 49 in the ten hours against 105 of 142 for the other three (hunt after hunt out
  east, and the bank straight from a hunt), and pieces broken by a death (a resurrection and a corpse walk between).

### The lands hour with the bank chosen by its road (cuo-m3n.3, cuo-m3n.17, cuo-m3n.18, cuo-m3n.19)

2026-10-27, the test server (2593), `hamlet-cove-lands.toml` on the brain frozen at ba83a7c7d (the bank by its road,
the recount) and the client that plans from a given tile, headless, memory on, the residents' memories set aside.
Single run. Not filmed.

```bash
cd brain
PYTHONPATH=$PWD/src-frozen-m3n17 uv run uo-brain fleet fleets/hamlet-cove-lands.toml \
  --out logs/fleet/20261027-072845-hamlet-cove-lands-m3n17
uv run uo-brain hamlet-report logs/fleet/20261027-072845-hamlet-cove-lands-m3n17
```

| | Brannoc | Wenna | Maren | Tamsin | the four |
|---|---|---|---|---|---|
| in Cove or its lands | 77% | 52% | 93% | 37% | 65% |
| in Cove itself | 46% | 34% | 69% | 36% | 46% |
| home between pieces of work | 3 of 4 | 3 of 3 | 3 of 3 | 4 of 5 | 13 of 15 |
| kills, deaths | 3, 2 | 10, 3 | 4, 0 | 4, 2 | 21, 7 |

$0.327 together ($0.315 an hour), 57 lines to a neighbour. Home between work 13 of 15 counts Maren's walks to her
neighbours' homes as no pieces of work (they had made 22 of 26 before that change in the report).

- **Six deaths of seven were the orc fort's:** Brannoc twice and Wenna twice at the fort or going back for a corpse
  there, Tamsin twice on the road east of Cove by it (savages, and liches seen). The watchman's work there waits on
  cuo-m3n.12.
- **The bank by its road:** Tamsin banked 460 gold at Vesper's bank from her hunt by the cemetery (the nearest from
  there by more than the margin, so nothing compared), 280 s there. Wenna, with no gold, bandages or potions after
  losing her corpse at the fort, asked the bank for 200 gold from her house: East Britain 1457 steps with 234 priced,
  Minoc 953 with 41, Vesper 847 with 63, so Minoc's, by 4 of 1158. South-west of Minoc, on ground priced for crowds of
  fair fights (trolls, orcish mages, ettins, ogres), a troll and an orcish mage took her to 34% at 2489,803; she fled
  400 tiles west, healed, and the trip walked her back the same way: dead at 2431,726 (cuo-m3n.19). The same walk
  for Tamsin with nothing to heal by, afterwards, met nobody and arrived unhurt in 221 s.
- **Maren's visits:** sent to look at Tamsin's arm, she walked to Tamsin's home four times in four minutes, each time
  told only "arrived at Tamsin's home in 19 s"; Tamsin was out at the fort, and dead for the last two (cuo-m3n.18).
- **Away:** Wenna's 52% and Tamsin's 37% are mostly the fort's deaths (resurrections, corpse walks) and their trips to
  the banks of Minoc and Vesper; Wenna's farthest was 508 tiles, on the Minoc road.

So a trip that is beaten below half its hits on the way, and is then 20 tiles back from there and further from the goal
with nothing threatening in sight, stops and says so, and for half an hour a bank road near that spot isn't chosen
while another is ([travel](goals.md#travel-banking-shops-and-hunting)); a trip begun hurt isn't taken for one driven back.
A visit to a neighbour's home says whether they are there. Checked live on the test server, single runs:

- **Driven back, staged:** with the character below half its hits and moved 30 tiles back 6 s into a walk east
  (`[AgentGo`, standing in for a flight), the trip stopped after 6 s: "driven back on the way to 5845,1100: down to
  32% of health at 5747,1100 by creatures, now 32 tiles back from there; the way to 5845,1100 goes past it again".
  That was before the rule left out a trip begun hurt; the rule as it stands is checked by tests only. A real flight
  on the way didn't come off in eleven stagings on the test field: against creatures that stand still or come at a
  character on a straight road, it ran past them (lowest 72% to 100%), pushed through to the goal (28%), or died (two
  ogre lords, 100% to 9% in one exchange; six creatures in 19 s).
- **A neighbour's home:** with Maren's hamlet, "arrived at Brannoc's home in 17 s; Brannoc isn't here (not in
  sight)" with him logged out, and as a ghost (the living don't see ghosts); "Brannoc is here, 0 tiles away,
  unhurt" with him at his gate (now "beside you"); "Wenna isn't here (not in sight)".

#### A second lands hour, with the trip driven back and the neighbour's home

2026-10-27 09:13 to 10:16, the same file, server and client, on the brain frozen at 2c8c7c3e8 (the driven-back trip,
the neighbour's home; not the guards' call on savages, which came after), headless, memory on, the residents'
memories set aside. Single run. Not filmed.

```bash
cd brain
PYTHONPATH=$PWD/src-frozen-m3n19 uv run uo-brain fleet fleets/hamlet-cove-lands.toml \
  --out logs/fleet/20261027-091308-hamlet-cove-lands-m3n19
uv run uo-brain hamlet-report logs/fleet/20261027-091308-hamlet-cove-lands-m3n19
uv run uo-brain hamlet-report RUN --json --lands "south-west of Cove Cemetery" --lands "near Cove Orc Fort" \
  --lands "wilderness near 2400,1100" --lands "north-east of Cove Guard Post"     # with the far grounds as lands
```

| | Brannoc | Wenna | Maren | Tamsin | the four |
|---|---|---|---|---|---|
| in Cove or its lands | 63% | 80% | 75% | 12% | 57% |
| ...with the two far grounds as lands | 84% | 88% | 76% | 76% | 81% |
| in Cove itself | 34% | 48% | 68% | 7% | 39% |
| home between pieces of work | 5 of 5 | 7 of 8 | 4 of 6 | 3 of 6 | 19 of 25 |
| kills, deaths | 7, 1 | 15, 0 | 0, 1 | 9, 0 | 31, 2 |

$0.291 together ($0.280 an hour), 22 lines to a neighbour.

- **Two deaths, both in the first three minutes,** to savage riders just east of Cove (Brannoc on his first sweep,
  Maren on her way home from the woods); none after. Nothing turned criminal near a resident in the guards' ground.
- **Tamsin** hunted the wilderness near 2400,1100, 159 tiles east and outside the lands, for 15 minutes three times,
  coming home between but setting straight out again (never seen at home in a 30 s look); between them she banked
  503 gold at Vesper's, chosen by its road (654 steps with 47 priced, against Minoc's 790 with 47), 284 s there and
  413 s home. Maren, with her corpse lost to the riders, named Vesper's bank herself for 200 gold, 372 s there.
- **Neither fix was called on:** no trip was driven back and nobody walked to a neighbour's home in this hour.
- **With the far grounds as lands** (Tamsin's wilderness near 2400,1100 and Wenna's ground north-east of Cove Guard
  Post, 159 and 200 tiles out), every resident was in Cove or on them most of the hour, as in the lands hour of
  2026-10-24 (76% to 94%); the first hour of 2026-10-27 comes to 77% that way, Wenna 61% with her three deaths.
  Whether those grounds are Cove's land is Harry's call (cuo-m3n.3).

### A lands hour with the far grounds as lands and the watch offered (cuo-m3n.3, cuo-m3n.12, cuo-m3n.21)

2026-10-27 15:18 to 16:20, `fleets/hamlet-cove-lands.toml` with its four lands (the two far grounds named since the
design call that they are Cove's land, each within 150 tiles of it) and Brannoc's posts (the `watch` goal offered),
on the brain frozen at `src-frozen-27b` (before the lands were told with what is too strong there, and before the
watch was put first in Brannoc's long goals), headless, memory on, the residents' memories set aside. Single run.
Not filmed.

```bash
cd brain
PYTHONPATH=$PWD/src-frozen-27b uv run uo-brain fleet fleets/hamlet-cove-lands.toml --out logs/fleet/20261027-151752-hamlet-cove-lands-27b
uv run uo-brain hamlet-report logs/fleet/20261027-151752-hamlet-cove-lands-27b
```

| | Brannoc | Wenna | Maren | Tamsin | the four |
|---|---|---|---|---|---|
| in Cove or its lands | 98% | 85% | 74% | 65% | 81% |
| ...counting only the first two lands | 1% | 69% | 73% | 40% | |
| in Cove itself | 1% | 45% | 56% | 24% | |
| home between pieces of work | 1 of 4 | 4 of 7 | 3 of 4 | 3 of 5 | 11 of 20 |
| kills, deaths | 9, 0 | 14, 1 | 0, 1 | 18, 1 | 41, 3 |

$0.308 together ($0.297 an hour), 24 lines to a neighbour.

- **Every resident was in Cove or its lands most of the hour,** the first half of cuo-m3n.3's acceptance. Not
  the second: Brannoc came home once between four hunts.
- **The three deaths were all at the Cove Orc Fort in the first 12 minutes,** to savages and savage riders:
  Wenna and Maren went there when their own ground was empty ("I'll try the orc fort lands to keep working
  towards archery 85"), Tamsin for gold. Told only "Cove's lands, where its people work", they took the fort for
  work like any other: filed and fixed as cuo-m3n.21 (each land is now told with what is too strong there for one
  alone). Nobody died after.
- **Brannoc never kept watch.** His planner's reflection pushed his first long goal, swordsmanship 80 to 85, and
  every goal was a 15-minute hunt in the wilderness near 2400,1100, one of Cove's lands (9 kills, no deaths, 98% of
  the hour on it). A watch gives few kills. The watch is now his first long goal and his work's first words.
- **Tamsin banked 722 gold at Minoc's,** chosen by its road (949 steps, 41 priced, against Vesper's 873 and 63),
  and hunted the north-east of Cove Guard Post for 15 kills.

### A second lands hour, the lands told with their dangers and the watch first (cuo-m3n.3, cuo-m3n.12, cuo-m3n.21)

2026-10-27 16:28 to 17:31, the same file, on the brain frozen at `src-frozen-27c`: each land told with what is too
strong there for one alone (cuo-m3n.21), and the watch Brannoc's first long goal and his work's first words.
Headless, memory on, the residents' memories set aside. Single run. Not filmed.

```bash
cd brain
PYTHONPATH=$PWD/src-frozen-27c uv run uo-brain fleet fleets/hamlet-cove-lands.toml --out logs/fleet/20261027-162812-hamlet-cove-lands-27c
uv run uo-brain hamlet-report logs/fleet/20261027-162812-hamlet-cove-lands-27c
```

| | Brannoc | Wenna | Maren | Tamsin | the four |
|---|---|---|---|---|---|
| in Cove or its lands | 92% | 84% | 87% | 91% | 89% |
| in Cove itself | 70% | 30% | 44% | 13% | |
| home between pieces of work | 4 of 6 | 4 of 6 | 5 of 5 | 2 of 6 | 15 of 23 |
| kills, deaths | 1, 0 | 15, 0 | 1, 0 | 6, 0 | 23, 0 |

$0.271 together ($0.257 an hour), 41 lines to a neighbour.

- **No deaths, and nobody hunted the orc fort,** against three deaths there in the first 12 minutes of the hour
  before; the residents now name their grounds as "a fair fight for one alone" when they choose them.
- **Brannoc kept watch three times, 34 minutes** (15, 10 and 9 minutes, walking his round), and hunted four times
  between; nothing came into Cove this hour, so the watches killed nothing. That is the watchman's acceptance: his
  work, without dying more than once.
- **Every resident was in Cove or its lands most of the hour again,** the first half of cuo-m3n.3's acceptance.
  Home between pieces of work is three of four: Tamsin went from an empty ground straight on to the next twice ("The
  last ground was empty, so I'll try a fair hunting ground near Cove"), 2 of 6. The grounds near Cove are thin for
  four: most hunts ended within five minutes with nothing in sight.

### A trip over several grounds as one piece of work (cuo-m3n.3)

Harry's call of 2026-10-27, made after the hour above came out 15 of 23 with the carrier 2 of 6: a trip over
several grounds is one piece of work. Since then `hamlet-report` counts a hunt that follows a hunt with no home
between them as the same trip (the same ground twice in a row too); a sale, a purchase, banking, a watch or a trip
to a town still parts two pieces, and the report says how many hunts it joined. Goal by goal, the pairs were the
trip's pairs plus those, with the same number home between. Every earlier run recounted:

```bash
cd brain
uv run uo-brain hamlet-report logs/fleet/RUN --json
```

| run | home between work, goal by goal | a trip as one piece | the carrier, a trip as one piece |
|---|---|---|---|
| m3n.4's three told runs | 12 of 16, 8 of 12, 8 of 14 | 12 of 15, 8 of 10, 8 of 12 | 2 of 4, 2 of 3, 1 of 2 |
| m3n.4's three control runs | 2 of 36, 0 of 35, 0 of 38 | 2 of 27, 0 of 25, 0 of 28 | 0 of 2, 0 of 6, 0 of 12 |
| told, 541e5d115 | 16 of 24 | 16 of 22 | 1 of 5 |
| the lands, 541e5d115 | 11 of 15 | 11 of 15 | 0 of 4 |
| the lands again, c72c1e616 | 11 of 20 | 11 of 15 | 1 of 2 |
| and the empty ground, 4d93f759d | 13 of 20 | 13 of 17 | 3 of 5 |
| and grounds named, dffc4b370 | 16 of 22 | 16 of 21 | 4 of 6 |
| told, on the priced travel, 90c3f5dbd | 17 of 25 | 17 of 23 | 3 of 5 |
| a second told hour, 3c9cfa38e | 11 of 23 | 11 of 20 | 1 of 4 |
| the lands, the bank by its road | 13 of 15 | 13 of 14 | 4 of 5 |
| the lands, the trip driven back | 19 of 25 | 19 of 24 | 3 of 5 |
| the lands, the far grounds named | 11 of 20 | 11 of 16 | 3 of 5 |
| the lands, told their dangers | 15 of 23 | 15 of 19 | 2 of 3 |

- **It still tells the told from the control:** told nothing of Cove, the residents were home between 0 to 2 of 25
  to 28 pieces, against 55% to 93% in each told and lands hour (181 of 243 in the fourteen).
- **Recounted, the hour above meets cuo-m3n.3's acceptance** (every resident in Cove or its lands most of the
  hour, and home between most pieces of work: 4 of 6, 4 of 5, 5 of 5, 2 of 3). The check is a fresh hour, not the
  recount: below.

#### The lands hour to check it

2026-10-27 18:38 to 19:40, the same file, on the brain frozen at `src-frozen-27d` (a0ce34432: the code of the hour
above plus the playtest's fixes to talk), the client in `bin/osx-arm64` (attended play; before the fixes this hour
led to, cuo-m3n.22 and cuo-m3n.23), headless, memory on, the residents' memories set aside (62). Single run. Not
filmed.

```bash
cd brain
PYTHONPATH=$PWD/src-frozen-27d uv run uo-brain fleet fleets/hamlet-cove-lands.toml --out logs/fleet/20261027-183826-hamlet-cove-lands-27d
uv run uo-brain hamlet-report logs/fleet/20261027-183826-hamlet-cove-lands-27d
```

| | Brannoc | Wenna | Maren | Tamsin | the four |
|---|---|---|---|---|---|
| in Cove or its lands | 81% | 59% | 90% | 93% | 81% |
| in Cove itself | 37% | 8% | 41% | 7% | 23% |
| home between pieces of work, a trip as one piece | 6 of 8 | 2 of 3 | 3 of 3 | 3 of 4 | 14 of 18 |
| ...goal by goal | 6 of 8 | 2 of 6 | 3 of 3 | 3 of 4 | 14 of 21 |
| kills, deaths | 18, 0 | 21, 0 | 9, 0 | 5, 0 | 53, 0 |

$0.300 together ($0.290 an hour), 47 lines to a neighbour.

- **cuo-m3n.3's acceptance, met:** every resident in Cove or its lands most of the hour, and home between most
  pieces of work, a trip over several grounds counted as one (Harry's call). Goal by goal it would not be: Wenna's
  first trip went over two grounds for 34 minutes (the ground south-west of the cemetery, empty, a rest out there,
  then north-east of the guard post twice) before she came home. No deaths, as in the hour before.
- **Wenna was on Cove's lands least (59%):** her last two hunts were north-east of the Cove Cemetery, a ground of
  boars and cougars that isn't one of the four named.
- **Brannoc kept watch twice, 20 minutes,** and hunted six times between; he left one hunt when a creature far
  stronger than him came at him with a pig, and nothing came into Cove.
- **Tamsin's two hunts named "Cove Cemetery" went to the lich's ground by it,** though her planner meant "the fair
  ground south-west of Cove Cemetery" (its own words): the name is part of both grounds' names and the hunt took
  the one at the place (fixed: cuo-m3n.23). On the first, a zombie stood next to her, unhurt, for 14 minutes,
  while she used 33 of her 40 bandages and killed nothing until her hunt's time was up (fixed: cuo-m3n.22).

#### What the hour led to (cuo-m3n.22, cuo-m3n.23)

2026-10-27 19:41 to 20:14, the test server, Tess (a warrior, 95 hits) alone where Tamsin had stood (2452,1093, a
fence to her east), on the client before the fix (`bin/osx-arm64` then) and after it (`bin/osx-arm64-27d`, a scratch
build of the fix, in `bin/osx-arm64` since). Single runs. Not filmed.

```bash
cd brain
UO_CLIENT_DIR=../bin/osx-arm64 uv run uo-brain --port 5590 client --headless --account headless7 --character Tess
uv run uo-brain --port 5590 say "[AgentGo 2452 1093"
uv run uo-brain --port 5590 say "[AgentSpawn Zombie 1 1 w still"     # then also "[AgentBuild wall 1 w 1 los"
uv run uo-brain --port 5590 do hunt "near Cove Cemetery" --minutes 4 --profile npc --log logs/m3n22/old.jsonl
# the same on UO_CLIENT_DIR=../bin/osx-arm64-27d, --minutes 12, logs/m3n22/new.jsonl; then the planner:
uv run uo-brain --port 5590 session "earn some gold hunting at Cove Cemetery, then come home to Cove" --hours 0.1 \
  --profile npc --no-memory --log logs/m3n23/session.jsonl
```

- **The stall, cuo-m3n.22:** ten earlier hamlet logs of 96 have a melee target next to the character with its
  health unchanged for over a minute (6 to 17 minutes in five of them), so it isn't rare. It wasn't the ground or
  the sight between: a still zombie set down on the tile beside hers died within 5 s on the old client, and so did
  one with ModernUO's invisible LOS blocker on its tile. What is left is that the client's picture of that zombie
  (next to her, unhurt, never moving for 14 minutes) wasn't the server's, or the attack was refused. The client now
  gives such a creature up after 30 s with none of its health taken, unless it swung at the character in the last
  8 s. On the lich's ground neither hunt stalled (2 kills each, in 4 and 12 minutes) and the new client gave
  nothing up; the give-up itself is shown by unit tests only, not yet in a run.
- **The name, cuo-m3n.23:** the planner's first goal was a hunt of "Cove Cemetery", refused before any walk with
  the four grounds whose names hold it ("near Cove Cemetery (spectre, shade, wraith, lich; stronger than you:
  lich)"); it chose "east of Cove Cemetery" ("Cove Cemetery's nearby ground has a lich, so I'll hunt ... where
  nothing is stronger than a new warrior"), and later the lich's ground by its full name, for its gold.

### The watchman's watch (cuo-m3n.12)

2026-10-27, the test server, Brannoc (95 hits, a warrior's kit) alone at the east gate, the `watch` goal from the
command line with his hamlet as the lands hour's fleet writes it (posts: the east gate, the healer's house, the
guard post to the north), Decision 1 fighting. An orc set down 6 or 7 tiles west of him with `[AgentSpawn Orc` at
70, 150 and 40 s in. Single runs. Not filmed.

```bash
cd brain
uv run uo-brain --port 5671 do watch --hamlet logs/m3n12/Brannoc.hamlet.json --minutes 6 --judge jev --log logs/m3n12/watch1.jsonl
uv run uo-brain --port 5671 say "[AgentSpawn Orc 1 7 w"
```

| | watch 1 (6 min) | watch 2 (7 min) | watch 3 (3 min) |
|---|---|---|---|
| the code | as first built | only what means harm counts as on the ground | ...and only what isn't a peaceful kind is fought |
| posts walked | the gate only | the gate, the healer's house, the guard post, the gate | the gate, the healer's house |
| the orc | came in war mode; the guards called once, killed | stood in the street, not in war mode: let be | gone for 1 s after it was set down, killed in 10 s |
| what else it went for | the town's dogs and a magpie (seek and fight, 0.68 to 0.93) | nothing | nothing |
| health at the end | 95/95 | 95/95 | 95/95 |

- **First, the town's animals:** Cove's dogs, cats, rats and magpies are always about, and on the first watch
  they counted as on the ground, so the watch never left its first post, and the fight loop went after a dog
  and a magpie. Then only what was in war mode counted, and the second watch walked the whole round in 7
  minutes but let an orc stand in the street. Now the watch takes on what the bestiary doesn't call a
  peaceful kind, in war mode or not, and leaves the animals be: the third watch went for the orc in a second.
- **Cost:** about 0.6 fight questions a second with animals in view, $0.002 in the first two minutes.

## Calling the guards (cuo-m3n.14)

2026-10-24, the soak server (2595), one headless warrior (Brutus, 95 hits) in Cove's street at 2262,1212, inside the
guards' ground and 9 tiles from the nearest townsperson, a lich spawned 5 tiles east. Single runs.

```bash
cd brain
uv run uo-brain --port 5580 client --headless --account warrior --character Brutus --server-port 2595
uv run python scripts/guards_check.py 5580 Lich --say     # "Guards!" at the lich's first strike; without --say, the control
uv run uo-brain --port 5580 say "[AgentSpawn Lich 1 5 e 15"; uv run uo-brain --port 5580 do rest 75 --defend --judge jev
```

| run | the lich | the character |
|---|---|---|
| agent off, "Guards!" said at the first strike | a guard came within a second and killed it | 67 of 95 after the one strike |
| the control: agent off, nothing said | struck for 8 s, no guard; called at 25 hits, a guard came at once | 25 of 95 |
| the session's call, resting with the fight loop beside it | turned criminal as it began to cast; "Guards!" then, and a guard killed it | unhurt |
| agent off, `scripts/guards_check.py` again | a guard came | 88 of 95 |

- The lich's notoriety in the snapshot turned from grey to criminal two seconds before its first spell struck, which
  is when the session called: ModernUO makes a creature that harms an innocent a guard candidate for 15 s, and sends a
  guard for any within 14 tiles of a line with "guards" in it. In the staged spot no townsperson was within 8 tiles of
  the lich, so none came of itself.
- A lich that wasn't the staff's, led in from the cemetery, couldn't be staged: none was there, and the undead that
  were didn't follow the warrior 160 tiles into town.
- In a told hour of the hamlet after it ([above](#a-second-told-hour-on-the-widened-ground)), nothing turned criminal near a resident in Cove, and nothing was called.

### Savages, red whatever they do (cuo-m3n.20)

2026-10-27, the same server, spot and warrior; a savage rider spawned 5 tiles east. The session's own call runs at
each look (`Session.seen`), with the agent on but fighting, moving and looting off, so the rider strikes first (with
the agent off the session's "Guards!" is refused: "misc is off"). Single runs.

```bash
cd brain
uv run python scripts/guards_check.py 5580 SavageRider --say    # the line at the first strike, agent off
```

| run | the rider | the character |
|---|---|---|
| agent off, "Guards!" said at the first strike (`guards_check.py`) | killed by a guard a second later | 17 of 95 after the one strike |
| the session's call with the agent off (twice) | called at the first blow, but the line was refused ("misc is off"): no guard | dead in 14 to 16 s |
| the session's call, agent on but not fighting | went into war mode beside him at 11 s: "Guards!" then; killed by a guard a second later | unhurt |
| the same again | the same, at 11 s | unhurt |
| the control: nobody calls until he is below 40 hits | one blow at 12 s; called then, killed by a guard at 13 s | 23 of 95 |

- A savage rider's notoriety stays "murderer" throughout (it is AlwaysMurderer); the server makes it criminal at its
  first swing on an innocent, so a guard comes for it from then. The session now calls on a red creature in war mode
  within 13 tiles, which it was a second before its first blow both times.
- In the lands hour of 2026-10-27 ([above](#the-lands-hour-with-the-bank-chosen-by-its-road-cuo-m3n3-cuo-m3n17-cuo-m3n18-cuo-m3n19)) savages killed all seven who died; the guards
  were called twice, both times on a lich.

## After a death

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

**A healer who never offered** (cuo-m3n.9), 2026-10-24. In the lands run of the hamlet Tamsin's ghost walked
to five healers over 15 minutes, three of them twice, and none offered; the journal held no refusal. A
ModernUO healer offers only on the step that brings a ghost within 4 tiles of it, in its sight. The Cove
healer stands in her house's east doorway between its doors and walks about inside, and from the north
the step to within 4 of her fell in the doorway or behind her wall, and every later walk in went back the
same way. The stand-off is now a spot 6 tiles off (or 5, in a small house) that she sees, with the step
to within 4 on the way in in her sight too, asked of the client (`sight` pairs), our own side first and
each try the next. On the soak server (port 2595), one headless client, a warrior killed with
`[AgentSlay` where Tamsin died (2157,958), then `uv run uo-brain --port 5580 do --shard m3n7-check resurrect`
(a copy of the world store), single runs one after another:

| | before (541e5d115) | after |
|---|---|---|
| raised by the Cove healer | 2 of 5 (116 s, 100 s) | 6 of 6 (100 to 143 s) |
| not raised at all (Cove healer, then the two wandering healers by Cove Guard Post) | 3 of 5 | 0 |

The wandering healers by Cove Guard Post were not where the store has them on any of the tries: they walk
about their spawner. Three of the six after were on a first version that wanted only the step in seen;
one of those walked in from outside the healer's west wall by her door, out of her sight, before a later
try worked, and the spot itself is now seen too.

**A corpse lasts 14 minutes, and its bones were no corpse to the client** (cuo-m3n.6), 2026-10-24. On ModernUO
(and RunUO) a corpse turns to bones 7 minutes after the death, with everything still on it, and is deleted 7
minutes later. The client counted no bones as a corpse, so every walk back that arrived after 7 minutes found
"the corpse is gone". Every `recover_corpse` that walked, in four hamlet runs (2026-10-23/24: the told run of
22:40, the lands run, the evenings run, the watchman's run), by minutes from the death to the result:

| minutes after the death | took everything back | the corpse is gone | died on the way |
|---|---|---|---|
| under 7.1 | 4 (5.6, 5.6, 6.8, 7.0) | 0 | 1 |
| 7.1 to 14 | 0 | 4 (7.2, 10.7, 13.9, 14.0) | 3 |
| over 14 | 0 | 7 (15.8 to 40.0) | 1 |

The client now keeps its own corpse by its serial once seen, so it stays its own as bones (`own` and `bones` in
the snapshot), and the brain begins no walk once the 14 minutes are up. The danger refusal and `your_corpse`
say how many minutes are left. Live on the soak server (port 2595), one headless client: a warrior killed with
`[AgentSlay` in Cove's street carrying 300 gold, 40 bandages and 3 potions, resurrected by the Cove healer, then
`uv run uo-brain --port 5580 do --shard m3n7-check recover-corpse` 529 s after the death: its bones were listed
as its own, opened, "You quickly gather all of your belongings.", everything back in 8 s. One run; the client
before the change wasn't tried live (the table above is its record).

## After-action review run

Single smoke run on 2026-10-07: `uo-brain review logs/arena-jev.jsonl` (the first Jev arena
run [above](#arena), with the round-3 death) read a 4 KB digest and cost $0.0136 (3,466 prompt
and 603 completion tokens, 6 s). It proposed four lines. The first was "When badly wounded or
worse with three or more enemies adjacent, flee or leave the area instead of fighting on,
even if the target is nearly dead.", with the evidence "Died at 18:00:01 surrounded by 6
adjacent hostiles. Jev chose fight on 5 straight decisions from 39% to 9% health". One line
asked for positioning ("fight from a spot where fewer can reach me"), which Jev can't
choose. All four came back at confidence 0.50.

## Renaissance rules rehearsal

Rehearsal on the local server switched to Renaissance rules
(`tools/modernuo/expansion.renaissance.json`), 2026-10-07:

| test | result |
|---|---|
| warrior, `uo-brain scenario` | 12 of 12 kills, no deaths; all 51 bandage messages read by cliloc number |
| mage, 2 rounds | 8 of 8 kills, no deaths (before the Protection fix: 120 casts of Protection, no kills) |
| item and rune names | resolved by single click within a few seconds of the corpse opening |
| mage at the Britain graveyard travels to the West Britain bank | recalled by runebook |


## Attended play (cuo-49t)

2026-10-27, the test server, Tess (a warrior, 95 hits) alone on a client with `agent_attended_minutes` set to 1,
in combat assist, nobody at the keyboard; once the agent said it was away, an orc set down 2 tiles off; 20 s later
a mode turned on again from the command line. Two single runs, on the first build and on the one that also takes
the character out of war mode. Not filmed.

```bash
cd brain
UO_CLIENT_DIR=../bin/osx-arm64-27c uv run uo-brain --port 5590 client --headless --account headless7 --character Tess
uv run uo-brain --port 5590 mode assist
uv run python scripts/attended_check.py 5590   # waits for agent.away, sets an orc on her, then turns auto mode on
```

| | first build | out of war mode when away |
|---|---|---|
| away after | 60 s without input | 60 s |
| the panel's journal | "you have been away 1 min: the agent waits until you are back" | the same |
| the agent while away | attacked nothing, bandaged nothing, engaged 0 | the same |
| the orc's health while away | 100% to 14% in 18 s | 100% to 20%, then gone, in 16 s |
| the character's health | 95/95 throughout | 95 to 75 |
| when a mode was turned on | back at once, bandaging and fighting as usual | back at once, bandaged to 95/95 in 6 s |

- **The agent stops; the server doesn't:** on ModernUO a character that is attacked has its attacker set as its
  combatant and swings back, war mode or not (`Mobile.AggressiveAction`, `CheckCombatTime`). That is the game's own
  auto-defence, the same for any player who steps away from the keyboard, and the agent can't turn it off without
  acting while the player is away (walking off, for one). What attended play promises is that the agent itself
  does nothing then: no attack, no heal, no walk, no brain action ("the player is away from the keyboard").
