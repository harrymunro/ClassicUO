# Local ModernUO test server

The agent is tested against a local [ModernUO](https://github.com/modernuo/ModernUO)
server. These files are the custom parts; the rest is a plain ModernUO clone.

## Setup (macOS, Apple Silicon)

```bash
brew install libdeflate argon2                      # ModernUO prerequisites (icu4c too, if missing)
git clone https://github.com/modernuo/ModernUO ~/Workspace/ModernUO
cp AgentTestKit.cs ~/Workspace/ModernUO/Projects/UOContent/Custom/
cp start-agent-server.sh ~/Workspace/ModernUO/
cd ~/Workspace/ModernUO && ./publish.sh release osx arm64
```

Then edit `Distribution/Configuration/modernuo.json` (it is created on the first
start; stop the server, edit, start again):

| key | value | why |
|---|---|---|
| `dataDirectories` | `["/Users/<you>/Workspace/UOClassic"]` | the client files from `tools/uo-download` |
| `listeners` | `["127.0.0.1:2593"]` | local only |
| `accountHandler.enableAutoAccountCreation` | `true` | `warrior`/`warrior` is created on first login |
| `accountHandler.maxAccountsPerIP` | `10` | the default of 1 blocks a second account from 127.0.0.1 |
| `serverListing.autoDetect` | `false` | no public IP lookup |
| `pathfinding.prebakeMaps` | `false` | skips a first-boot prompt |
| `crashGuard.restartServer` | `false` | no stray restarts |
| `agentTestKit.ownerUsername` / `ownerPassword` | e.g. `admin` / a password | seeds the owner account at boot; the stock prompt fails without a terminal |
| `agentTestKit.commandAccessLevel` | `Player` | monsters ignore staff, so the test warrior is a normal player |
| `agentTestKit.location` / `map` | `(5445, 1153, 0)` / `Felucca` | Green Acres: flat, no guards, no spawns |

`Distribution/Configuration/expansion.json` uses Endless Journey (id 11), the newest,
with all maps. The server reads the client version (7.0.117.1) from `client.exe`.

## Running

```bash
~/Workspace/ModernUO/start-agent-server.sh          # foreground, logs to stdout
~/Workspace/ModernUO/start-agent-server.sh --fresh  # wipe the world save first
```

`UO_DATA_DIR` and `MODERNUO_DIR` override the default paths. Stop with Ctrl+C or
`pkill -f "dotnet ModernUO.dll"`; neither saves (autosave runs every 5 minutes).

## Commands (AgentTestKit.cs)

- **`[AgentGo`:** teleports you to the test location.
- **`[AgentKit [katana|broadsword|longsword|vikingsword]`:** warrior template.
  - Swords, Tactics, Healing and Anatomy at 80; stats 90/70/15, locked.
  - Ringmail armour, 200 bandages, 5 greater heal and 5 greater cure potions.
  - Removes Young status.
  - Wipes the old equipment and backpack first.
- **`[AgentArena [count] [mix|orc|ratman|headless|mongbat|zombie|skeleton|<type>]`:** clears your last arena, then spawns monsters in a ring 6–10 tiles out.
- **`[AgentReset`:** resurrects you if dead, restores vitals, cures poison, and removes your arena monsters.
