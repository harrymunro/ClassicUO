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

To rehearse for a pre-AOS shard such as UO Renaissance, copy `expansion.renaissance.json`
over `expansion.json` (keep the original) and restart. It sets Renaissance (id 2): T2A and
UOR on, AOS off, so there are no item properties and no buff icons, and ModernUO's
pre-AOS combat, spell and runebook rules apply. Use a second copy of `Distribution` on
another port for this, so the main test server stays on the newest rules.

## Populating Felucca

A fresh ModernUO world is empty apart from the map's own buildings: no doors, signs,
vendors or monsters. Travel, banking and shopping tests need the towns as players know
them, so generate them once, as the owner (`admin`), from any character on that account:

```
[DoorGen
[SignGen
[TelGen
[MoonGen
[Decorate
[ImportSpawners Data/Spawns/shared/felucca/*.json
[ImportSpawners Data/Spawns/post-uoml/felucca/*.json
[Save
```

On 2026-10-06 that gave 1,911 Felucca doors, 1,510 teleporters, 32 moongates, 55,253
decoration items and 2,122 spawners, the Britain bankers, healers and shops included.
Green Acres stays empty, so the arena tests are unaffected.

## Running

```bash
~/Workspace/ModernUO/start-agent-server.sh          # foreground, logs to stdout
~/Workspace/ModernUO/start-agent-server.sh --fresh  # wipe the world save first
```

`UO_DATA_DIR` and `MODERNUO_DIR` override the default paths. Stop with Ctrl+C or
`pkill -f "dotnet ModernUO.dll"`; neither saves (autosave runs every 5 minutes).

## Commands (AgentTestKit.cs)

- **`[AgentKit [warrior|mage|archer|tamer|bard|warriormage|magetamer|necro|paladin] [katana|broadsword|longsword|vikingsword]`:** test character templates.
  - Warrior (default): Swords, Tactics, Healing and Anatomy at 80; stats 90/70/15. Ringmail armour, 200 bandages.
  - Mage: Magery 90; Evaluating Intelligence, Meditation and Wrestling 80; Resisting Spells 60; stats 70/35/100. Leather armour (it allows meditation), a wizard's hat, a full spellbook and a bag of 100 of each reagent.
  - Necro (AOS rules): Necromancy 90, Spirit Speak 80, Meditation 70, Healing 70, Anatomy 60, Wrestling 70; a full book of necromancy, 100 of each necromancer's reagent, 100 bandages, leather.
  - Paladin (AOS rules): the warrior kit with Chivalry 90, intelligence 60, a full book of chivalry and 10,000 tithing points.
  - All: stats locked, 5 greater heal and 5 greater cure potions. Removes Young status.
  - Wipes the old equipment and backpack first.
- **`[AgentArena [count] [mix|orc|ratman|headless|mongbat|zombie|skeleton|<type>]`:** clears your last arena, then spawns monsters in a ring 6–10 tiles out.
- **`[AgentReset`:** resurrects you if dead, restores vitals, cures poison, cancels pending spawns, and removes your arena monsters and the corpses around you.
- **`[AgentGo [lane | x y]`:** lanes 1-9 are copies of the test spot 60 tiles east of each other; `x y` goes to any tile of the test map.
- **`[AgentSpawn <kind> [count] [distance] [direction] [delay]`:** creatures at a bearing and distance, optionally after a delay.
- **`[AgentSupplies [bandages N] [heal N] [cure N] [reagents N] [gold N] [loot N]`:** sets supplies.
- **`[AgentLoot [distance] [direction]`:** an orc's corpse with three valuables and five pieces of junk.
- **`[AgentWall x1 y1 x2 y2 | clear`:** an invisible wall of blockers, for stuck tests.
- **`[AgentRestock [amount]`:** stocks nearby vendors.
- **`[AgentRunes`:** a runebook and two runes marked to the test field and Britain.
