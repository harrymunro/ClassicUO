# Playing on public shards

<sub>[← README](../README.md) · [all documentation](../README.md#documentation)</sub>

> [!WARNING]
> Unattended play (auto mode) is against the rules on most shards, and some object to
> modified clients. Check each shard's rules, and keep auto mode to the local server or
> shards that allow it.

Combat assist is the same kind of tool as Razor or UOSteam, which many shards allow.

## Older rules (pre-AOS shards such as UO Renaissance)

Shards that play the rules from before Age of Shadows send less: no item properties and no
buff icons. The client tells which kind it is on from the server's features (item
properties on means AOS) and reports it as `era` in the snapshot ("aos" or "pre-aos").

- **Names:** without item properties, the client single-clicks things once each (about one a second, as the server allows) and uses the name the server shows over them: items in open corpses within 2 tiles, runes in the pack, and people nearby. Until then it uses the tile name ("Bones", "diamonds"), so loot judgments get "a supremely accurate longsword of vanquishing" a few seconds after the corpse opens.
- **Protection:** under the older rules it only adds armour and doesn't stop hits interrupting spells, so the mage doesn't cast it. Under AOS rules it casts it at most every 20 seconds.
- **Runebooks** look like a spellbook with hue 0x461 on older servers, rather than the AOS runebook graphic; both are recognised.

A rehearsal on the local server under Renaissance rules passed ([results](results.md#renaissance-rules-rehearsal)). Not
yet tried on UO Renaissance itself.

## Connecting to UO Renaissance

What the shard needs, from its own pages and its launcher (checked 2026-10-08, when its website was
down and the game server up; the page quotes are from the Wayback Machine):

- **Server:** `login.uorenaissance.com`, port 2593, no encryption.
- **Client data:** client version 5.0.8.3 with the shard's own patched files (art, gumps, maps and statics, `tiledata.mul`, `cliloc.enu`; its maps add islands and towns). The 7.0.117.1 data from EA's patch servers won't do, and the shard's launcher, installer and patch are only on its website.
- **Accounts** are made on first login with a name and password of your choosing. The shard allows two or three per person (its pages disagree).
- **Profile:** `tools/uor/settings.uor.json` has the settings, with the agent off. Copy it next to `cuo`, fill in `ultimaonlinedirectory` with the folder holding the shard's files, and start `./cuo -settings settings.uor.json` from `bin/osx-arm64`.
- **Line of sight:** the profile asks for RunUO's rule (`"agent_sight_rule": "runuo"`), because the shard runs a RunUO of its own: no door clause, the one-unit allowance kept ([what differs](playing.md#line-of-sight)). Its staff reworked "the RunUO LOS code" without publishing what they changed, so the shard's exact rule can only be measured there. In a first session watch the client's counters: `agent.stats.not_in_sight` (the shard refusing a target) against `aimed_unseen` (aims the client's own rule counted as out of sight). A refusal with no such aim is a place where the shard is stricter than the rule in force. The other way round, the client holding back where the shard would allow, doesn't show in them.
- **Places:** the shard publishes no marker file for towns and dungeons (only `house.txt`, the players' houses), but its map page draws them from lines in its own script. `tools/uor/uor_map_markers.py PAGE.html --out brain/worlds/uor/uor-map-page.map` turns a saved copy of the page into a UOAM file, and `uo-brain world --shard uor import-markers brain/worlds/uor/uor-map-page.map --maps Felucca` makes them places ([what it gave](results.md#uo-renaissances-map-page)). The page has no banks, healers or shops: those still need the shard's own files or a first walk round a town. The page and the file made from it are the shard's work and stay out of the repo (`brain/worlds/` is gitignored).
- **Rules:** the shard's own launcher installs ClassicUO and staff recommend it, but its Code of Conduct still lists "Non Electronic Arts (EA) or Origin Systems (OSI) Clients" as not allowed, names Razor as the only approved third-party program, says no software other than Razor may be used for repetitive tasks, and that "you may not kill monsters unattended using any means". Ask staff before connecting with the agent on.
