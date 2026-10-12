# Results

<sub>[← README](../README.md) · [all documentation](../README.md#documentation)</sub>

> [!NOTE]
> Most ran on the local ModernUO server; the routine-call test, the guide import and the
> review ran without the game. Apart from the judgment benchmark, which gives success rates
> over 5 or 10 rounds, these are single runs: treat them as a smoke test rather than a
> benchmark.

- **Fights:** [Arena](#arena), [Judgment benchmark](#judgment-benchmark), [Fewer fight calls](#fewer-fight-calls), [Plans for fights](#plans-for-fights), [Walls and summons in a swarm](#walls-and-summons-in-a-swarm), [Line of sight](#line-of-sight), [Where it runs](#where-it-runs), [When a leave stops](#when-a-leave-stops), [Real input](#real-input)
- **Models:** [Models for each role](#models-for-each-role), [Haiku or Sonnet for plans, reviews and guides](#haiku-or-sonnet-for-plans-reviews-and-guides), [Opus for the planner's one-off calls](#opus-for-the-planners-one-off-calls), [Sonnet for the planner's hard moments](#sonnet-for-the-planners-hard-moments), [Jev and Microsoft's Decision 1](#jev-and-microsofts-decision-1)
- **Sessions towards a goal:** [Unattended runs](#unattended-runs), [Travel and errands](#travel-and-errands), [Routine hunt calls](#routine-hunt-calls), [After a death](#after-a-death), [After-action review run](#after-action-review-run)
- **World knowledge and memory:** [World store and fact picking](#world-store-and-fact-picking), [UO Renaissance's map page](#uo-renaissances-map-page), [Map markers](#map-markers), [The planner with memories](#the-planner-with-memories), [Long-range memory over a long soak](#long-range-memory-over-a-long-soak)
- **Several agents:** [Several agents on one server](#several-agents-on-one-server), [Headless clients](#headless-clients), [A party on one plan](#a-party-on-one-plan), [A party that helps, calls out and talks](#a-party-that-helps-calls-out-and-talks), [NPCs with lives of their own](#npcs-with-lives-of-their-own)
- **Public shards:** [Renaissance rules rehearsal](#renaissance-rules-rehearsal)

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
  bandage buys failed ("the vendor did not show its goods", cuo-k0d), so it hunted 14 minutes on 30
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
