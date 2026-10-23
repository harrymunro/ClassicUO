# Seeing what it does

<sub>[← README](../README.md) · [all documentation](../README.md#documentation)</sub>

- **Agent panel:** Jev's probabilities, choices and actions for every decision, in game ([the agent panel](playing.md#the-agent-panel)).
- **Decision log:** every decision goes to a JSONL file with the state, all answers and probabilities, the actions and their results.
- **`uo-brain report`** summarises a log.
- **`uo-brain replay`** re-asks a log's questions to another judge offline, e.g. Jev against the rule baseline, and reports how often they agree.
- **`uo-brain soak-report`** reports on an unattended `session` run ([Reference](reference.md#uo-brain)).
- **Assist agreement:** with fight set to suggest, the brain records whether you attacked the creature it suggested.
- **`uo-brain costs`** says what the AI calls in a log cost ([below](#what-it-costs)).
- **Filming a run** (`tools/video/`, cuo-gka): `film.py` runs a command and records the client windows on the
  agent ports you name, one clip per window plus all of them side by side, into `~/Movies/ClassicUO-agent-clips/`
  as `YYYY-MM-DD <what> (<bead>) - <character>.mp4`, and prints the row for the vault's Footage log:

  ```bash
  python3 tools/video/layout.py film brain/fleets/party-lich.toml             # set the clients up for the camera
  python3 tools/video/film.py --ports 5661-5665 --what "a party on the lich scene" --bead cuo-4ar \
      -- uv run --project brain uo-brain fleet brain/fleets/party-lich.toml --windowed
  python3 tools/video/layout.py restore                                       # and put them back
  python3 tools/video/cut.py IN.mp4 OUT.mp4 --part 1:05:1:40 --speed 2   # keep a part, faster
  python3 tools/video/cut.py --grid OUT.mp4 A.mp4 B.mp4                  # side by side
  python3 tools/video/film.py --remake RAW_DIR --what "..." --bead ID         # the clips again from raw recordings
  ```

  Recording starts once each port's client is in game with a window, and stops when the command ends.
  The recorder (`wincaps`, ScreenCaptureKit, built by `tools/video/build.sh`) records every window from
  one process, since separate recorders break each other's streams, and records a window even when
  others cover it. A headless client has no window: `fleet --windowed` gives every client one. Needs
  macOS 15, ffmpeg, and the Screen Recording permission for the terminal. Recording loads the machine,
  so a filmed run's numbers aren't compared with unfilmed ones.

  **With the screen locked** ScreenCaptureKit records nothing: it sees no display, and every window
  fails with "Failed due to failure to process first sample buffer" (error -5822), which is how
  unattended runs go. Then each client films itself (cuo-cym.32, `--source auto`, the default: `wincaps
  --displays` says 0): `film.py` makes a FIFO per client and asks it to `record` into it, and ffmpeg
  makes `raw/PORT.mp4` of the frames, with `PORT.mp4.start` as before. A client reads its back buffer
  after a frame is drawn, `--client-fps` times a second (15 by default), and repeats the last frame for
  any slot it missed, so the clip runs as long as the run did (100.1 s for 100.1 s in the first check).
  A frame read takes the game thread about 2 ms (1419 by 906, 15 a second: 3% of a second). `--source
  screen` or `client` chooses either.

  `layout.py film FLEET` gives each of the fleet's characters a large game view (1004 by 920 at scale
  0.7 in a 1696 by 954 window), the agent panel beside it, no paperdoll or other saved gump, a fixed
  light and no sound, and keeps the files it changed for `restore`. As a client comes, its game view
  is 600 by 480 with the paperdoll open over the middle, which hides the characters and what they
  say. Run both with no client of that folder running (`--client DIR`, or `UO_CLIENT_DIR`, for a
  folder other than `bin/osx-arm64`).

## What it costs

Every model call is recorded with its cost: Jev's typed questions (system one: fight decisions,
routine hunt calls, picks of world facts, re-ranking the planner's queries, reading the strategy,
naming what the recorder saw) and the planner model's chat (system two: goals, after-action
review, guide import, plan design).

- **Recorded per call:** its kind and role, the model, input, output and cached tokens, latency,
  and the cost in dollars. The cost is the provider's own figure when it reports one: OpenRouter
  does, for Jev and the planner alike. Otherwise (TypeSafe direct, or a model OpenRouter doesn't
  price) it's estimated from a price table and marked `estimated`. `uo-brain costs --prices` fetches
  OpenRouter's list prices into `brain/prices.json` for that table.
- **A model nobody can price, or another model answering:** a call whose cost neither the provider nor
  the table knows is counted as $0, marked `unpriced`, and warned about once on stderr ("no price
  known for model ..."), and every summary lists such models under `unpriced_models` (`uo-brain
  costs` says the total is too low), so an id typed wrong or out of date shows in the costs instead
  of vanishing from them. And when the model that answered isn't the one asked for (a dated name
  for the plain id, or Jev's `-latest`, is fine), that is warned about too: "asked for X and Y
  answered" (cuo-znw).
- **In the logs:** one `ai_cost` record per call, in the log the call belongs to. Every run's
  `summary` record (and a session's `session_summary`) has a `costs` block: the total, cost an hour,
  a decision and a kill, split by role, kind of call and model. The one-off commands (`machine design`,
  `review`, `world import-guide` and `fill-gaps`) write theirs to the file `--cost-log FILE` names.
  A decision Jev wasn't asked for is logged with `not_asked` (why) and no `questions`; "a decision"
  in costs means one put to Jev.
- **Where people look:** the [calls window](playing.md#the-agent-panel) shows each call's cost and the
  running total; `uo-brain costs LOG` and `uo-brain report LOG` give the totals of a log;
  `soak-report` splits a session's cost between system one and system two, an hour and a kill,
  with the fight decisions put to Jev an hour (also in `fleet-report`, per character);
  bench results give each round's cost (`cost_usd`, `cost_by_kind`, the models used, the average
  latency), each judge's cost a round, and the whole run's `costs`.
- **Checked against OpenRouter:** `run`, `session` and `bench` write what the OpenRouter key has
  spent in all (its own count, from `/api/v1/key`) at the start and the end of the log. Another
  run on the same key at the same time counts in it too, and the key's count trails the calls, by
  2-3 minutes one evening and over half an hour one afternoon, so read it again once it has stopped
  moving (`uo-brain costs --key`). One bench run with nothing else on the key: $0.1002 recorded in
  1,305 calls, $0.1003 by OpenRouter's count once settled (2026-10-08, `bench/2026-10-08-fight-fixes.json`).
- **A budget:** `--budget-per-hour USD` caps what the AI calls cost an hour, judged over the last
  ten minutes (never measured over less than five, so one early planner call isn't a trend).
  `--over-budget` says what happens over the cap: `slow` (the default) decides a quarter as often
  and only on the clock, and the planner rests a minute instead of thinking; `cheaper` switches to
  the profile's cheaper models; `stop` ends the session. The panel says when it goes over and back
  under.

## Which model answers

Each kind of call can have its own model, named in one place: a profile in `brain/profiles/`.

```toml
[models]
system1 = "decision1"                      # fight, routine, facts, rerank, strategy, recorder, memory, chat, order
routine = "jev"                            # one kind's own line beats its system's
system2 = "anthropic/claude-sonnet-5.5"    # planner, review, guide, design, compact, heartbeat, daily
escalate = "anthropic/claude-sonnet-5.5"   # the planner's hard moments (Sonnet unless named,
                                           # as are review and guide)

[cheaper]                                  # while over a budget whose `over` is "cheaper"
system2 = "anthropic/claude-haiku-5.5"

[budget]
per_hour = 0.60
over = "cheaper"                           # slow, cheaper or stop

[aliases]                                  # short names for --use and bench labels
qwen = "qwen/qwen3.7-flash"
```

- **Profiles:** `default` (system one split by kind, [the default: system one split by kind](results.md#the-default-system-one-split-by-kind); Haiku plans and designs fight plans, Sonnet reviews logs and
  imports guides, where Haiku [fell short](results.md#haiku-or-sonnet-for-plans-reviews-and-guides); no
  budget), `jev` (as `default`, with Jev for every system-one call; the default before 2026-10-12),
  `sonnet` (system one as `default`, Sonnet plans, Haiku over a `cheaper` budget; the default before 2026-10-09),
  `opus` (as `default`, with Opus 5.5 for the planner's hard moments, plan design and review; measured
  offline, where it wasn't [better for its price](results.md#opus-for-the-planners-one-off-calls)),
  `decision1` (as `default`, with Microsoft's Decision 1 for every system-one call, routine and
  strategy too) and `npc` (system one as `default`, Haiku for everything else, capped at $0.50 an hour since 2026-10-09; $0.15 before). `--profile NAME|FILE` picks one for `run`,
  `session`, `scenario`, `do hunt` and `bench`.
- **Order:** the built-in default, then `JEV_MODEL` / `PLANNER_MODEL` in the environment, then the
  profile, then the command line: `--use KIND=MODEL` (repeatable; a kind, `system1` or `system2`),
  `--model` (system one) and `--planner-model` (system two). Three kinds are Sonnet unless a profile or
  the command line names their own model, whatever the system's line or the environment says:
  `escalate`, `review` and `guide`. Two system-one kinds are Jev unless a profile, the environment or
  the command line names a model for system one or for them: `routine` and `strategy`. So
  `--model decision1`, `--use system1=jev` or a profile with one `system1` line puts every call on that model.
- **Any model in system one:** "jev" (or a `typesafe/jev` id) is Jev. Any other OpenRouter model
  is asked the same questions through a chat adapter (`models.ChatJudge`): it's told what each
  question type means and asked for a probability for every option, as JSON (with a schema where
  the model takes structured outputs), and its answer becomes the same probabilities Jev gives, so
  the policy, the panel and the logs don't change. A question left unanswered gets an even spread.
  Thinking is left off, except for models that always reason (gpt-oss, gpt-5), which are asked
  for as little as they allow.
- **Another decisions model:** Microsoft's Decision 1 (`microsoft/microsoft-decision-1`, alias
  `decision1`) is a model of Jev's own kind: it takes the same request and returns a probability
  for each option. It goes the way Jev does, through the same client with its own model id, not
  through the chat adapter: `--model decision1`, `--use system1=decision1`, or `--judges
  jev,decision1` on a bench. All three of Jev's question kinds work unchanged, several in one
  request, and its 32,768-token window holds the brain's largest request (9,842 tokens as it
  counts them). [How it compares](results.md#jev-and-microsofts-decision-1).
- **Named in the logs:** each decision's `answers.model`, each `ai_cost` record's `model`, the
  bench results' `profile` and `aliases`, and each round's `models`.
- **Zero data retention:** this OpenRouter account only allows endpoints that keep no data, so a
  model without one is refused ("ZDR violation"); OpenRouter's `/api/v1/endpoints/zdr` lists those
  that have one.
- **The planner offline:** each planner call's log record keeps the messages it was sent (after
  the fixed system prompt), so `uo-brain replay-planner LOG --planner-model M` can put the same
  moments to another model without the game: how often it answers with a usable tool call, how
  often it picks the same tool, and what that costs and how long it takes.

## After-action review

`uo-brain review LOG` has the planner model read a log and propose lines for your strategy,
each with its evidence. Code builds a small digest for it first: totals, each death with the
five decisions before it, flees, looting while a monster was within 3 tiles, low-health
moments, supplies used, judge errors, how sure Jev was of each intent, and the strategy in
use. The game logs no message when the character dies, so deaths are placed from the health
trail: a decision at 25% health or less, then the end of the run, or no decision for 10 s and
health back above 60%. The model answers by calling a `propose` tool. It is Claude Sonnet 5.5 by
default, about $0.02 a review (`--planner-model` picks another): on the same logs Haiku proposed
the same lines after a death but missed the biggest problem in a log that Sonnet found
([results](results.md#haiku-or-sonnet-for-plans-reviews-and-guides)).

Nothing changes until you accept. The proposals are printed numbered and saved to
`<log>.review.json`, with the digest and the model's reply. `uo-brain review LOG --accept 2 3`
adds lines 2 and 3 to the character's strategy through the client, from the saved file,
without asking the model again; a line already in the strategy isn't added twice. Each line
added is also kept in the character's [memory](world-and-memory.md#the-characters-memory) as a lesson. Running
`review` again shows the saved review: `--again` asks the model again, and `--digest` prints
what it would read. What a first review proposed: [results](results.md#after-action-review-run).
