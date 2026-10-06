# Project Instructions for AI Agents

This file provides instructions and context for AI coding agents working on this project.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:1105d646 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/core-concepts/sync-concepts.md for details and anti-patterns.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `bd` for task tracking. Do not run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `bd prime`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close beads, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending a Beads implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create beads for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git/sync by active profile**:
   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**
- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.
<!-- END BEADS INTEGRATION -->


## Build & Test

Requires the .NET 10 SDK (`/usr/local/share/dotnet`) and Xcode command line tools (NativeAOT links with clang).

```bash
git submodule update --init --recursive   # FNA, FileEmbed, MP3Sharp
dotnet build                              # Debug build, output in bin/Debug
dotnet test                               # tests/ClassicUO.UnitTests

# Native Apple Silicon executable (standalone, no Mono/plugin host needed)
dotnet publish src/ClassicUO.Client/ClassicUO.Client.csproj -c Release -r osx-arm64 -o bin/osx-arm64
./bin/osx-arm64/cuo
```

`scripts/build-naot.sh` builds the upstream release layout instead: osx-x64, client as a shared library loaded by the net472 `ClassicUO.Bootstrap` host. That layout is only needed for managed assistant plugins such as Razor.

The client reads `settings.json` next to the executable. It needs `ultimaonlinedirectory` (a folder containing `tiledata.mul`) and `clientversion`. Game data lives in `~/Workspace/UOClassic` (client 7.0.117.1), fetched from EA's patch servers by `python3 tools/uo-download/download_uo.py --out <dir>`.

Agent brain (Python, uv): `cd brain && uv run pytest`. Agent C# tests: `dotnet test tests/ClassicUO.UnitTests --filter "FullyQualifiedName~Agent"`.

## Architecture Overview

- `src/ClassicUO.Client` builds the `cuo` executable. It is NativeAOT: serialize POCOs only through source-generated `JsonSerializerContext`s (or write JSON by hand with `Utf8JsonWriter`/`JsonDocument`, as the agent does); no reflection.
- Everything runs single-threaded on the main loop: `GameController.Update` then `GameScene.Update`. Work done off-thread must be handed back through a queue.
- Remotes: `origin` is the fork (harrymunro/ClassicUO) and `upstream` is ClassicUO/ClassicUO. `upstream/main-agent` has an unmerged JSON-RPC agent harness to use as a reference.

### Agent (Jev)

- `src/ClassicUO.Client/Agent/`: the in-client half. `AgentHost` runs a loopback JSON-lines RPC server (`-agent_port 5577` / `agent_port` in settings.json) and dispatches requests on the game thread from `GameController.Update`. `AgentController` (owned by `World`, ticked after `Macros.Update` in `GameScene.Update`) holds mode, per-behaviour authority (off/suggest/auto), reflexes (`ReflexPolicy`, pure), engagement/loot/flee execution and the human-input pause. `AgentSnapshot` writes the state JSON, `AgentJournal` keeps sequenced messages, `AgentLogin` drives the login screens, `AgentStatusGump` is the overlay. Hooks into existing code are small and marked by `_world.Agent` / `AgentHost` calls.
- `brain/`: the Python decision loop. `state.py` turns snapshots into worded state and candidate ids, `questions.py` builds one fan-out Jev request, `policy.py` masks, gates and composes actions, `loop.py` runs it and logs JSONL, `cli.py` is `uo-brain`.
- Test server: ModernUO in `~/Workspace/ModernUO` (start with `./start-agent-server.sh`), custom commands `[AgentGo`, `[AgentKit`, `[AgentArena`, `[AgentReset` in `Projects/UOContent/Custom/AgentTestKit.cs`. The copy kept in this repo, with setup notes, is `tools/modernuo/`.
