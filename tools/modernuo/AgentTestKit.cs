using System;
using System.Collections.Generic;
using Server.Accounting;
using Server.Collections;
using Server.Commands;
using Server.Engines.Spawners;
using Server.Items;
using Server.Logging;
using Server.Misc;
using Server.Mobiles;
using Server.Targeting;

namespace Server.Custom;

/// <summary>
/// Commands for a repeatable warrior or mage test bed driven by an AI agent:
/// [AgentKit, [AgentArena, [AgentReset and [AgentGo, plus the scenario pieces
/// [AgentSpawn, [AgentSupplies and [AgentLoot.
/// Monsters ignore staff (BaseAI skips AccessLevel > Player), so the test character must be a
/// Player-level account; the commands therefore default to Player access on this local test shard.
/// </summary>
public static class AgentTestKit
{
    private static readonly ILogger logger = LogFactory.GetLogger(typeof(AgentTestKit));

    private const int DefaultArenaCount = 6;
    private const int MaxArenaCount = 30;
    private const int ArenaMinRadius = 6;
    private const int ArenaMaxRadius = 10;
    private const int ArenaHomeRange = 10;
    private const int OrphanSweepRange = 32;
    private const int LaneSpacing = 60;
    private const int MaxLane = 9;

    private static readonly Type[] _defaultMix =
        [typeof(Orc), typeof(Ratman), typeof(HeadlessOne), typeof(Mongbat)];

    private static readonly Dictionary<Mobile, List<BaseCreature>> _arenaSpawns = new();

    // Spawns waiting for their delay ([AgentSpawn ... delay]); cancelled by a reset.
    private static readonly Dictionary<Mobile, List<TimerExecutionToken>> _pending = new();

    // Invisible walls laid with [AgentWall, removed by [AgentWall clear.
    private static readonly List<Item> _walls = [];

    // Every creature in _arenaSpawns, so the orphan sweep can tell live arenas from leftovers of a
    // previous boot (the tracking itself is not persisted).
    private static readonly HashSet<BaseCreature> _tracked = new();

    // The pet each tamer kit gave, replaced by the next kit.
    private static readonly Dictionary<Mobile, BaseCreature> _kitPets = new();

    private static AccessLevel _accessLevel;

    public static Point3D TestLocation { get; private set; }

    public static Map TestMap { get; private set; }

    public static void Configure()
    {
        _accessLevel = ServerConfiguration.GetOrUpdateSetting("agentTestKit.commandAccessLevel", AccessLevel.Player);

        var locationSetting = ServerConfiguration.GetOrUpdateSetting("agentTestKit.location", "(5445, 1153, 0)");
        TestLocation = Point3D.TryParse(locationSetting, null, out var location) ? location : new Point3D(5445, 1153, 0);

        var mapSetting = ServerConfiguration.GetOrUpdateSetting("agentTestKit.map", "Felucca");
        TestMap = Map.TryParse(mapSetting, null, out var map) && map != null && map != Map.Internal ? map : Map.Felucca;

        CommandSystem.Register("AgentKit", _accessLevel, AgentKit_OnCommand);
        CommandSystem.Register("AgentArena", _accessLevel, AgentArena_OnCommand);
        CommandSystem.Register("AgentReset", _accessLevel, AgentReset_OnCommand);
        CommandSystem.Register("AgentGo", _accessLevel, AgentGo_OnCommand);
        CommandSystem.Register("AgentSpawn", _accessLevel, AgentSpawn_OnCommand);
        CommandSystem.Register("AgentSupplies", _accessLevel, AgentSupplies_OnCommand);
        CommandSystem.Register("AgentLoot", _accessLevel, AgentLoot_OnCommand);
        CommandSystem.Register("AgentWall", _accessLevel, AgentWall_OnCommand);
        CommandSystem.Register("AgentRestock", _accessLevel, AgentRestock_OnCommand);
        CommandSystem.Register("AgentRunes", _accessLevel, AgentRunes_OnCommand);
        CommandSystem.Register("AgentDisrupt", _accessLevel, AgentDisrupt_OnCommand);
    }

    // Runs before AccountPrompt.Initialize (default priority 50) so a headless first boot finds an
    // account and never reaches the interactive owner prompt, which throws when stdin is redirected.
    [CallPriority(40)]
    public static void Initialize()
    {
        SeedOwnerAccount();

        var z = TestMap.GetAverageZ(TestLocation.X, TestLocation.Y);
        logger.Information(
            "AgentTestKit: commands at {AccessLevel}; test location {Location} on {Map} (land z {Z}, spawnable {CanSpawn}, region {Region})",
            _accessLevel,
            TestLocation,
            TestMap,
            z,
            TestMap.CanSpawnMobile(TestLocation),
            Region.Find(TestLocation, TestMap)?.Name ?? "none"
        );
    }

    private static void SeedOwnerAccount()
    {
        var username = ServerConfiguration.GetSetting("agentTestKit.ownerUsername", (string)null);
        var password = ServerConfiguration.GetSetting("agentTestKit.ownerPassword", (string)null);

        if (string.IsNullOrWhiteSpace(username) || string.IsNullOrEmpty(password) || Accounts.GetAccount(username) != null)
        {
            return;
        }

        var account = new Account(username, password) { AccessLevel = AccessLevel.Owner };
        ServerAccess.AddProtectedAccount(account, true);
        logger.Information("AgentTestKit: seeded owner account {Username}", username);
    }

    [Usage("AgentKit [warrior|mage|archer|tamer|bard|warriormage|magetamer] [katana|broadsword|longsword|vikingsword|bow|crossbow|heavycrossbow] [bear|wolf|hound|drake] [target]")]
    [Description(
        "Resets a test kit and wipes previous equipment and backpack contents. warrior (default): Swords/Tactics/Healing/Anatomy 80, 90/70/15 stats, weapon, ring/leather armor, bandages and potions. mage: Magery 90, Eval Int/Meditation/Wrestling 80, Resisting Spells 60, 70/35/100 stats, full spellbook, reagents, leather armor and potions. archer: Archery/Tactics/Healing/Anatomy 80, 75/85/15 stats, a bow (or the crossbow named) with 200 arrows or bolts, studded leather, bandages and potions. tamer: Animal Taming/Lore/Veterinary 90, Healing/Anatomy 70, no weapon, and a tamed pet following (a grizzly bear, or the wolf, hell hound or drake named) that replaces the last kit's pet. bard: Musicianship/Provocation/Peacemaking/Discordance 90, Healing/Anatomy 60, a lute and no weapon. warriormage: the warrior kit plus Magery 80, Eval Int 60, a spellbook and reagents. magetamer: the mage kit plus Animal Taming/Lore 85, Veterinary 60, bandages and a pet. 'target' (GameMaster+) applies it to another player."
    )]
    public static void AgentKit_OnCommand(CommandEventArgs e)
    {
        var from = e.Mobile;
        var weapon = typeof(Katana);
        var mage = false;
        var useTarget = false;
        Type pet = null;
        var bard = false;
        var hybrid = string.Empty;

        for (var i = 0; i < e.Length; i++)
        {
            var arg = e.GetString(i);
            if (arg.InsensitiveEquals("target"))
            {
                useTarget = true;
                continue;
            }

            if (arg.InsensitiveEquals("mage") || arg.InsensitiveEquals("warrior"))
            {
                mage = arg.InsensitiveEquals("mage");
                continue;
            }

            if (arg.InsensitiveEquals("archer"))
            {
                mage = false;
                weapon = typeof(Bow);
                continue;
            }

            if (arg.InsensitiveEquals("tamer"))
            {
                pet ??= typeof(GrizzlyBear);
                continue;
            }

            if (arg.InsensitiveEquals("bard"))
            {
                bard = true;
                continue;
            }

            if (arg.InsensitiveEquals("warriormage") || arg.InsensitiveEquals("magetamer"))
            {
                hybrid = arg.ToLowerInvariant();
                continue;
            }

            if (GetPetType(arg) is { } petType)
            {
                pet = petType;
                continue;
            }

            var type = GetWeaponType(arg);
            if (type == null)
            {
                from.SendMessage(
                    "Usage: [AgentKit [warrior|mage|archer|tamer|bard|warriormage|magetamer] [katana|broadsword|longsword|vikingsword|bow|crossbow|heavycrossbow] [bear|wolf|hound|drake] [target]"
                );
                return;
            }

            weapon = type;
        }

        if (hybrid == "warriormage")
        {
            ApplyWarriorMageKit(from, weapon);
            return;
        }

        if (hybrid == "magetamer")
        {
            ApplyMageTamerKit(from, pet ?? typeof(GrizzlyBear));
            return;
        }

        if (pet != null)
        {
            ApplyTamerKit(from, pet);
            return;
        }

        if (bard)
        {
            ApplyBardKit(from);
            return;
        }

        if (!useTarget)
        {
            ApplyKit(from, mage ? null : weapon);
            return;
        }

        if (from.AccessLevel < AccessLevel.GameMaster)
        {
            from.SendMessage("Only GameMasters can apply the kit to another player.");
            return;
        }

        from.SendMessage("Target the player to equip.");
        from.Target = new KitTarget(mage ? null : weapon);
    }

    [Usage("AgentArena [count] [mix|orc|ratman|headless|mongbat|zombie|skeleton|<creature type>]")]
    [Description(
        "Removes your previous arena monsters, then spawns count (default 6) hostile monsters in a ring 6-10 tiles around you, homed to that spot."
    )]
    public static void AgentArena_OnCommand(CommandEventArgs e)
    {
        var from = e.Mobile;
        var map = from.Map;

        if (map == null || map == Map.Internal)
        {
            return;
        }

        var count = DefaultArenaCount;
        Type kind = null;

        for (var i = 0; i < e.Length; i++)
        {
            var arg = e.GetString(i);
            if (int.TryParse(arg, out var n))
            {
                count = Math.Clamp(n, 1, MaxArenaCount);
                continue;
            }

            if (arg.InsensitiveEquals("mix"))
            {
                kind = null;
                continue;
            }

            kind = GetCreatureType(arg);
            if (kind == null)
            {
                from.SendMessage($"Unknown creature kind '{arg}'.");
                return;
            }
        }

        var removed = ClearArena(from);
        var center = from.Location;
        var spawned = 0;

        for (var i = 0; i < count; i++)
        {
            if (!TryFindRingSpot(map, center, i, count, out var spot))
            {
                continue;
            }

            // Arena spawns are marked RemoveIfUntamed, so they survive restarts only until the
            // loyalty timer or the orphan sweep removes them.
            if (SpawnInArena(from, kind ?? _defaultMix[i % _defaultMix.Length], spot, map, center, ArenaHomeRange))
            {
                spawned++;
            }
        }

        from.SendMessage($"Arena: spawned {spawned}/{count} {(kind == null ? "mixed" : kind.Name)} monsters, removed {removed} old ones.");
        logger.Information(
            "AgentArena: {Mobile} spawned {Spawned}/{Count} {Kind} around {Location} on {Map}, removed {Removed}",
            from,
            spawned,
            count,
            kind?.Name ?? "mix",
            center,
            map,
            removed
        );
    }

    [Usage("AgentReset")]
    [Description("Resurrects you if dead, restores hits/stam/mana, cures poison, cancels pending spawns and removes your arena monsters and nearby corpses.")]
    public static void AgentReset_OnCommand(CommandEventArgs e)
    {
        var from = e.Mobile;
        var wasDead = !from.Alive;

        if (wasDead)
        {
            from.Resurrect();
        }

        RestoreVitals(from);
        var removed = ClearArena(from);

        from.SendMessage($"Reset: {(wasDead ? "resurrected, " : "")}vitals restored, removed {removed} arena monsters.");
        logger.Information(
            "AgentReset: {Mobile} reset (resurrected {Resurrected}), removed {Removed} arena monsters",
            from,
            wasDead,
            removed
        );
    }

    [Usage("AgentGo [lane | x y]")]
    [Description(
        "Teleports you to the agent test location. Lanes 1-9 are copies of it 60 tiles apart (eastwards), so several test characters can run scenarios at once without meeting. With x y, teleports you to that tile on the test map (e.g. a town, for travel tests)."
    )]
    public static void AgentGo_OnCommand(CommandEventArgs e)
    {
        var from = e.Mobile;

        if (e.Length >= 2)
        {
            int x = e.GetInt32(0), y = e.GetInt32(1);
            var to = new Point3D(x, y, TestMap.GetAverageZ(x, y));
            from.MoveToWorld(to, TestMap);
            from.SendMessage($"Moved to {to} on {TestMap}.");
            return;
        }

        var lane = e.Length > 0 ? Math.Clamp(e.GetInt32(0), 0, MaxLane) : 0;
        var spot = LaneLocation(lane);
        from.MoveToWorld(spot, TestMap);
        from.SendMessage($"Moved to the agent test location {spot} on {TestMap} (lane {lane}).");
        logger.Information("AgentGo: {Mobile} moved to {Location} on {Map} (lane {Lane})", from, spot, TestMap, lane);
    }

    private static Point3D LaneLocation(int lane)
    {
        var x = TestLocation.X + lane * LaneSpacing;
        var y = TestLocation.Y;

        // Nearest spawnable tile, spiralling out a little if the exact spot is blocked.
        for (var r = 0; r < 6; r++)
        {
            for (var dx = -r; dx <= r; dx++)
            {
                for (var dy = -r; dy <= r; dy++)
                {
                    var z = TestMap.GetAverageZ(x + dx, y + dy);

                    if (TestMap.CanSpawnMobile(x + dx, y + dy, z))
                    {
                        return new Point3D(x + dx, y + dy, z);
                    }
                }
            }
        }

        return TestLocation;
    }

    [Usage("AgentSpawn <kind> [count] [distance] [direction] [delay seconds]")]
    [Description(
        "Spawns count (default 1) creatures of a kind (orc, ratman, a ModernUO type such as Dragon, Lich, OrcishMage) distance tiles (default 8) from you in a compass direction (n, ne, e, se, s, sw, w, nw; default n), after an optional delay. They belong to your arena, so [AgentReset removes them."
    )]
    public static void AgentSpawn_OnCommand(CommandEventArgs e)
    {
        var from = e.Mobile;
        var map = from.Map;

        if (map == null || map == Map.Internal || e.Length < 1)
        {
            from.SendMessage("Usage: [AgentSpawn <kind> [count] [distance] [direction] [delay seconds]");
            return;
        }

        var kind = GetCreatureType(e.GetString(0));
        if (kind == null)
        {
            from.SendMessage($"Unknown creature kind '{e.GetString(0)}'.");
            return;
        }

        var count = e.Length > 1 ? Math.Clamp(e.GetInt32(1), 1, MaxArenaCount) : 1;
        var distance = e.Length > 2 ? Math.Clamp(e.GetInt32(2), 1, 20) : 8;
        var angle = e.Length > 3 ? CompassAngle(e.GetString(3)) : CompassAngle("n");
        var delay = e.Length > 4 ? Math.Clamp(e.GetDouble(4), 0, 300) : 0;

        if (double.IsNaN(angle))
        {
            from.SendMessage($"Unknown direction '{e.GetString(3)}': use n, ne, e, se, s, sw, w or nw.");
            return;
        }

        var center = from.Location;

        void Spawn()
        {
            if (from.Deleted || from.Map != map)
            {
                return;
            }

            var spawned = 0;

            for (var i = 0; i < count; i++)
            {
                // Spread a group sideways around the bearing so they do not stack on one tile.
                var a = angle + (i - (count - 1) / 2.0) * (Math.PI / 16);

                if (TrySpot(map, center, a, distance, out var spot) && SpawnInArena(from, kind, spot, map, center, distance + 4))
                {
                    spawned++;
                }
            }

            from.SendMessage($"Spawn: {spawned}/{count} {kind.Name} at {distance} tiles.");
        }

        if (delay <= 0)
        {
            Spawn();
            return;
        }

        if (!_pending.TryGetValue(from, out var tokens))
        {
            tokens = [];
            _pending[from] = tokens;
        }

        Timer.StartTimer(TimeSpan.FromSeconds(delay), Spawn, out var token);
        tokens.Add(token);
        from.SendMessage($"Spawn: {count} {kind.Name} in {delay:0.#}s.");
    }

    [Usage("AgentSupplies [bandages N] [heal N] [cure N] [reagents N] [arrows N] [bolts N] [gold N] [loot N]")]
    [Description(
        "Sets how many bandages, greater heal and greater cure potions, reagents (of each kind), arrows and bolts are in your backpack, replacing what is there. Only the kinds named change. gold N adds N gold coins; loot N adds N sets of the [AgentLoot items (valuables and junk) to the pack."
    )]
    public static void AgentSupplies_OnCommand(CommandEventArgs e)
    {
        var from = e.Mobile;
        var pack = from.Backpack;

        if (pack == null || e.Length < 2 || e.Length % 2 != 0)
        {
            from.SendMessage("Usage: [AgentSupplies [bandages N] [heal N] [cure N] [reagents N]");
            return;
        }

        for (var i = 0; i + 1 < e.Length; i += 2)
        {
            var what = e.GetString(i).ToLowerInvariant();
            var n = Math.Clamp(e.GetInt32(i + 1), 0, 1000);

            switch (what)
            {
                case "bandages" or "bandage":
                    {
                        DeleteAll<Bandage>(pack);
                        if (n > 0)
                        {
                            pack.DropItem(new Bandage(n));
                        }

                        break;
                    }
                case "heal":
                    {
                        DeleteAll<BaseHealPotion>(pack);
                        for (var k = 0; k < n; k++)
                        {
                            pack.DropItem(new GreaterHealPotion());
                        }

                        break;
                    }
                case "cure":
                    {
                        DeleteAll<BaseCurePotion>(pack);
                        for (var k = 0; k < n; k++)
                        {
                            pack.DropItem(new GreaterCurePotion());
                        }

                        break;
                    }
                case "reagents" or "regs":
                    {
                        DeleteAll<BaseReagent>(pack);
                        if (n > 0)
                        {
                            pack.DropItem(new BagOfReagents(n));
                        }

                        break;
                    }
                case "gold":
                    {
                        if (n > 0)
                        {
                            pack.DropItem(new Gold(n));
                        }

                        break;
                    }
                case "arrows" or "arrow":
                    {
                        DeleteAll<Arrow>(pack);
                        if (n > 0)
                        {
                            pack.DropItem(new Arrow(n));
                        }

                        break;
                    }
                case "bolts" or "bolt":
                    {
                        DeleteAll<Bolt>(pack);
                        if (n > 0)
                        {
                            pack.DropItem(new Bolt(n));
                        }

                        break;
                    }
                case "loot":
                    {
                        for (var k = 0; k < Math.Min(n, 5); k++)
                        {
                            foreach (var item in LootItems())
                            {
                                pack.DropItem(item);
                            }
                        }

                        break;
                    }
                default:
                    from.SendMessage($"Unknown supply '{what}': use bandages, heal, cure, reagents, arrows, bolts, gold or loot.");
                    return;
            }
        }

        from.SendMessage("Supplies set.");
    }

    [Usage("AgentWall <x1> <y1> <x2> <y2> | clear")]
    [Description(
        "Lays an invisible wall (blockers) along the line between two tiles, to test getting stuck and finding another way. [AgentWall clear removes all of them."
    )]
    public static void AgentWall_OnCommand(CommandEventArgs e)
    {
        var from = e.Mobile;

        if (e.Length == 1 && e.GetString(0).InsensitiveEquals("clear"))
        {
            foreach (var wall in _walls)
            {
                wall.Delete();
            }

            from.SendMessage($"Wall: removed {_walls.Count} blockers.");
            _walls.Clear();
            return;
        }

        if (e.Length < 4 || from.Map == null || from.Map == Map.Internal)
        {
            from.SendMessage("Usage: [AgentWall <x1> <y1> <x2> <y2> | clear");
            return;
        }

        int x1 = e.GetInt32(0), y1 = e.GetInt32(1), x2 = e.GetInt32(2), y2 = e.GetInt32(3);
        var steps = Math.Max(Math.Abs(x2 - x1), Math.Abs(y2 - y1));

        for (var i = 0; i <= steps && i <= 60; i++)
        {
            var x = x1 + (int)Math.Round((x2 - x1) * (double)i / Math.Max(steps, 1));
            var y = y1 + (int)Math.Round((y2 - y1) * (double)i / Math.Max(steps, 1));
            var z = from.Map.GetAverageZ(x, y);

            // Two blockers high, so nothing steps over them.
            for (var dz = 0; dz < 40; dz += 20)
            {
                var blocker = new Blocker();
                blocker.MoveToWorld(new Point3D(x, y, z + dz), from.Map);
                _walls.Add(blocker);
            }
        }

        from.SendMessage($"Wall: {steps + 1} tiles from ({x1}, {y1}) to ({x2}, {y2}).");
    }

    [Usage("AgentRunes")]
    [Description(
        "Gives you a runebook full of charges marked to the test field, the West Britain bank, the Britain graveyard and the Britain healer, and two loose runes marked to the bank and the test field."
    )]
    public static void AgentRunes_OnCommand(CommandEventArgs e)
    {
        var from = e.Mobile;
        (string Name, Point3D Location)[] spots =
        [
            ("Green Acres test field", TestLocation),
            ("West Britain bank", new Point3D(1425, 1690, 0)),
            ("Britain graveyard", new Point3D(1386, 1494, 10)),
            ("Britain healer", new Point3D(1471, 1609, 20))
        ];

        var book = new Runebook(10);
        book.CurCharges = book.MaxCharges;

        foreach (var (name, location) in spots)
        {
            book.Entries.Add(new RunebookEntry(book, location, Map.Felucca, name));
        }

        from.AddToBackpack(book);

        foreach (var (name, location) in spots[..2])
        {
            from.AddToBackpack(new RecallRune { Marked = true, Target = location, TargetMap = Map.Felucca, Description = name });
        }

        from.SendMessage($"Runes: a runebook with {spots.Length} entries and 2 loose runes.");
    }

    [Usage("AgentRestock [amount]")]
    [Description(
        "Stocks every vendor within 12 tiles with at least amount (default 100) of each thing it sells, so buying tests aren't limited by what is left on the shelf."
    )]
    public static void AgentRestock_OnCommand(CommandEventArgs e)
    {
        var from = e.Mobile;
        var amount = e.Length > 0 ? Math.Clamp(e.GetInt32(0), 1, 999) : 100;
        var vendors = 0;

        foreach (var vendor in from.GetMobilesInRange<BaseVendor>(12))
        {
            foreach (var info in vendor.GetBuyInfo())
            {
                if (info is GenericBuyInfo buy)
                {
                    buy.MaxAmount = Math.Max(buy.MaxAmount, amount);
                    buy.Amount = Math.Max(buy.Amount, amount);
                }
            }

            vendors++;
        }

        from.SendMessage($"Restock: {vendors} vendors stocked with at least {amount} of everything.");
    }

    // Spawners [AgentDisrupt despawn stopped, and the vendors it sold out, for restore.
    private static readonly List<BaseSpawner> _stoppedSpawners = [];
    private static readonly List<BaseVendor> _soldOut = [];
    private static readonly List<BaseCreature> _strong = [];

    [Usage("AgentDisrupt despawn x y radius [minutes] | strong x y kind count | sellout x y radius item | restore [quiet]")]
    [Description(
        "Disruptions for unattended runs, at a place rather than around you. despawn: removes what the spawners within radius of x y have spawned and stops them (for minutes, default 20). strong: spawns count creatures of kind around x y. sellout: empties the stock of item (by name) at the vendors within radius of x y. restore: restarts the stopped spawners, restocks the sold-out vendors and removes what strong spawned. quiet (last) leaves the caller's journal alone, so an agent under test can't read about it."
    )]
    public static void AgentDisrupt_OnCommand(CommandEventArgs e)
    {
        var from = e.Mobile;
        var what = e.Length > 0 ? e.GetString(0).ToLowerInvariant() : "";
        var map = TestMap;
        // "quiet" last: nothing in the caller's journal, so an agent under test can't read it.
        var quiet = e.Length > 0 && e.GetString(e.Length - 1).InsensitiveEquals("quiet");
        void Say(string text)
        {
            if (!quiet)
            {
                from.SendMessage(text);
            }
        }

        switch (what)
        {
            case "despawn" when e.Length >= 4:
                {
                    var at = new Point3D(e.GetInt32(1), e.GetInt32(2), 0);
                    var radius = Math.Clamp(e.GetInt32(3), 1, 60);
                    var minutes = e.Length > 4 ? Math.Clamp(e.GetInt32(4), 1, 240) : 20;
                    var stopped = new List<BaseSpawner>();

                    foreach (var item in map.GetItemsInRange(at, radius))
                    {
                        if (item is BaseSpawner spawner)
                        {
                            stopped.Add(spawner);
                        }
                    }

                    foreach (var spawner in stopped)
                    {
                        spawner.RemoveSpawns();
                        spawner.Stop();
                        _stoppedSpawners.Add(spawner);
                    }

                    Timer.StartTimer(TimeSpan.FromMinutes(minutes), () =>
                        {
                            foreach (var spawner in stopped)
                            {
                                if (!spawner.Deleted)
                                {
                                    spawner.Start();
                                }
                            }
                        }
                    );
                    Say($"Disrupt: {stopped.Count} spawners near {at.X},{at.Y} emptied and stopped for {minutes} min.");
                    logger.Information("AgentDisrupt: despawned {Count} spawners near {At} for {Minutes} min", stopped.Count, at, minutes);
                    return;
                }
            case "strong" when e.Length >= 5:
                {
                    var center = new Point3D(e.GetInt32(1), e.GetInt32(2), map.GetAverageZ(e.GetInt32(1), e.GetInt32(2)));
                    var kind = GetCreatureType(e.GetString(3));
                    var count = Math.Clamp(e.GetInt32(4), 1, 10);
                    var made = 0;

                    for (var i = 0; kind != null && i < count; i++)
                    {
                        if (TrySpot(map, center, i * 2 * Math.PI / count, 3, out var spot) &&
                            kind.CreateInstance<BaseCreature>() is { } creature)
                        {
                            creature.Home = center;
                            creature.RangeHome = 12;
                            creature.MoveToWorld(spot, map);
                            _strong.Add(creature);
                            made++;
                        }
                    }

                    Say($"Disrupt: {made} {e.GetString(3)} near {center.X},{center.Y}.");
                    logger.Information("AgentDisrupt: {Made} {Kind} near {At}", made, e.GetString(3), center);
                    return;
                }
            case "sellout" when e.Length >= 5:
                {
                    var at = new Point3D(e.GetInt32(1), e.GetInt32(2), 0);
                    var radius = Math.Clamp(e.GetInt32(3), 1, 60);
                    var word = e.GetString(4).ToLowerInvariant().TrimEnd('s');
                    var emptied = 0;

                    foreach (var vendor in map.GetMobilesInRange<BaseVendor>(at, radius))
                    {
                        foreach (var info in vendor.GetBuyInfo())
                        {
                            if (info is GenericBuyInfo buy && buy.Type.Name.ToLowerInvariant().Contains(word))
                            {
                                buy.Amount = 0;
                                emptied++;

                                if (!_soldOut.Contains(vendor))
                                {
                                    _soldOut.Add(vendor);
                                }
                            }
                        }
                    }

                    Say($"Disrupt: {emptied} stocks of {word} emptied near {at.X},{at.Y}.");
                    logger.Information("AgentDisrupt: sold out {Count} stocks of {Item} near {At}", emptied, word, at);
                    return;
                }
            case "restore":
                {
                    foreach (var spawner in _stoppedSpawners)
                    {
                        if (!spawner.Deleted)
                        {
                            spawner.Start();
                        }
                    }

                    foreach (var vendor in _soldOut)
                    {
                        foreach (var info in vendor.GetBuyInfo())
                        {
                            if (info is GenericBuyInfo buy)
                            {
                                buy.Amount = Math.Max(buy.Amount, buy.MaxAmount);
                            }
                        }
                    }

                    var removed = 0;
                    foreach (var creature in _strong)
                    {
                        if (!creature.Deleted)
                        {
                            creature.Delete();
                            removed++;
                        }
                    }

                    Say($"Disrupt: {_stoppedSpawners.Count} spawners restarted, {_soldOut.Count} vendors restocked, {removed} creatures removed.");
                    _stoppedSpawners.Clear();
                    _soldOut.Clear();
                    _strong.Clear();
                    return;
                }
            default:
                Say("Usage: [AgentDisrupt despawn x y radius [minutes] | strong x y kind count | sellout x y radius item | restore");
                return;
        }
    }

    [Usage("AgentLoot [distance] [direction]")]
    [Description(
        "Lays a fresh corpse distance tiles away (default 2, north) holding three valuable items (a diamond, a gold ring, a magic longsword) and five junk ones (bones, a head, a plain shirt, kindling, raw ribs)."
    )]
    public static void AgentLoot_OnCommand(CommandEventArgs e)
    {
        var from = e.Mobile;
        var map = from.Map;

        if (map == null || map == Map.Internal)
        {
            return;
        }

        var distance = e.Length > 0 ? Math.Clamp(e.GetInt32(0), 1, 12) : 2;
        var angle = e.Length > 1 ? CompassAngle(e.GetString(1)) : CompassAngle("n");

        if (double.IsNaN(angle) || !TrySpot(map, from.Location, angle, distance, out var spot))
        {
            from.SendMessage("No room for the corpse there.");
            return;
        }

        // A monster's corpse, as the agent only loots those. The orc stands still for a moment so
        // the client sees it before it dies, then its own loot is swapped for the test items.
        var carrier = new Orc { Paralyzed = true, Frozen = true };
        carrier.MoveToWorld(spot, map);

        if (!_pending.TryGetValue(from, out var tokens))
        {
            tokens = [];
            _pending[from] = tokens;
        }

        Timer.StartTimer(
            TimeSpan.FromSeconds(1.5),
            () =>
            {
                if (carrier.Deleted)
                {
                    return;
                }

                carrier.Kill();

                if (carrier.Corpse is not Container corpse)
                {
                    from.SendMessage("The corpse did not appear.");
                    return;
                }

                using var own = PooledRefQueue<Item>.Create();
                foreach (var item in corpse.Items)
                {
                    own.Enqueue(item);
                }

                while (own.Count > 0)
                {
                    own.Dequeue().Delete();
                }

                foreach (var item in LootItems())
                {
                    corpse.DropItem(item);
                }

                from.SendMessage($"Loot: a corpse with 3 valuables and 5 junk items at {distance} tiles.");
            },
            out var token
        );
        tokens.Add(token);
    }

    private static Item[] LootItems() =>
    [
        new Diamond(2), new GoldRing(), MagicLongsword(),
        new Bone(3), new Head(), new Shirt(), new Kindling(5), new RawRibs(2)
    ];

    private static Item MagicLongsword()
    {
        var sword = new Longsword();

        if (Core.AOS)
        {
            sword.Attributes.WeaponDamage = 35;
            sword.Attributes.WeaponSpeed = 20;
            sword.WeaponAttributes.HitLightning = 40;
        }
        else
        {
            sword.DamageLevel = WeaponDamageLevel.Vanq;
            sword.AccuracyLevel = WeaponAccuracyLevel.Supremely;
        }

        sword.Identified = true;
        return sword;
    }

    private static void DeleteAll<T>(Container pack) where T : Item
    {
        using var queue = PooledRefQueue<Item>.Create();

        foreach (var item in pack.FindItemsByType<T>())
        {
            queue.Enqueue(item);
        }

        while (queue.Count > 0)
        {
            queue.Dequeue().Delete();
        }
    }

    private static bool SpawnInArena(Mobile from, Type type, Point3D spot, Map map, Point3D home, int homeRange)
    {
        var creature = type.CreateInstance<BaseCreature>();
        if (creature == null)
        {
            return false;
        }

        creature.Home = home;
        creature.RangeHome = homeRange;
        creature.RemoveIfUntamed = true;
        creature.MoveToWorld(spot, map);

        if (!_arenaSpawns.TryGetValue(from, out var list))
        {
            list = [];
            _arenaSpawns[from] = list;
        }

        list.Add(creature);
        _tracked.Add(creature);
        return true;
    }

    // Radians, east = 0, growing clockwise on screen (UO's y axis points south). NaN when unknown.
    private static double CompassAngle(string dir) =>
        dir.ToLowerInvariant() switch
        {
            "e" or "east"       => 0,
            "se" or "southeast" => Math.PI / 4,
            "s" or "south"      => Math.PI / 2,
            "sw" or "southwest" => 3 * Math.PI / 4,
            "w" or "west"       => Math.PI,
            "nw" or "northwest" => 5 * Math.PI / 4,
            "n" or "north"      => 3 * Math.PI / 2,
            "ne" or "northeast" => 7 * Math.PI / 4,
            _                   => double.NaN
        };

    // A spawnable tile near the given bearing and distance, widening the search a little.
    private static bool TrySpot(Map map, Point3D center, double angle, int distance, out Point3D spot)
    {
        for (var attempt = 0; attempt < 16; attempt++)
        {
            var a = angle + (attempt + 1) / 2 * (attempt % 2 == 0 ? 1 : -1) * (Math.PI / 32);
            var r = distance + attempt / 8;
            var x = center.X + (int)Math.Round(Math.Cos(a) * r);
            var y = center.Y + (int)Math.Round(Math.Sin(a) * r);
            var z = map.GetAverageZ(x, y);

            if (map.CanSpawnMobile(x, y, z))
            {
                spot = new Point3D(x, y, z);
                return true;
            }
        }

        spot = Point3D.Zero;
        return false;
    }

    // A null weapon type gives the mage kit, a bow or crossbow the archer kit.
    public static void ApplyKit(Mobile m, Type weaponType)
    {
        var mage = weaponType == null;
        var archer = weaponType?.IsAssignableTo(typeof(BaseRanged)) == true;

        if (!m.Alive)
        {
            m.Resurrect();
        }

        if (m is PlayerMobile pm)
        {
            if (pm.Account is Account { Young: true } account)
            {
                // Young players are shielded from monsters in Trammel-ruleset areas.
                account.RemoveYoungStatus(0);
            }

            pm.Young = false;
        }

        // A tamer kit's pet goes with any new kit (the tamer kit then makes a new one).
        if (_kitPets.Remove(m, out var oldPet) && !oldPet.Deleted)
        {
            oldPet.Delete();
        }

        var skills = m.Skills;
        for (var i = 0; i < skills.Length; i++)
        {
            skills[i].Base = 0;
        }

        if (mage)
        {
            skills[SkillName.Magery].Base = 90;
            skills[SkillName.EvalInt].Base = 80;
            skills[SkillName.Meditation].Base = 80;
            skills[SkillName.Wrestling].Base = 80;
            skills[SkillName.MagicResist].Base = 60;
        }
        else
        {
            skills[archer ? SkillName.Archery : SkillName.Swords].Base = 80;
            skills[SkillName.Tactics].Base = 80;
            skills[SkillName.Healing].Base = 80;
            skills[SkillName.Anatomy].Base = 80;
        }

        // Locked so stat gain/atrophy cannot drift a run away from the baseline.
        m.StrLock = StatLockType.Locked;
        m.DexLock = StatLockType.Locked;
        m.IntLock = StatLockType.Locked;
        // An archer trades strength for dexterity: dexterity sets how fast a bow fires.
        m.RawStr = mage ? 70 : archer ? 75 : 90;
        m.RawDex = mage ? 35 : archer ? 85 : 70;
        m.RawInt = mage ? 100 : 15;

        using var toDelete = PooledRefQueue<Item>.Create();
        foreach (var item in m.Items)
        {
            if (item.Layer is not (Layer.Backpack or Layer.Bank or Layer.Hair or Layer.FacialHair or Layer.Mount) &&
                item.Layer <= Layer.LastUserValid)
            {
                toDelete.Enqueue(item);
            }
        }

        var pack = m.Backpack;
        if (pack == null)
        {
            pack = new Backpack { Movable = false };
            m.AddItem(pack);
        }
        else
        {
            foreach (var item in pack.Items)
            {
                toDelete.Enqueue(item);
            }
        }

        while (toDelete.Count > 0)
        {
            toDelete.Dequeue().Delete();
        }

        if (mage)
        {
            // Leather allows meditation; the spellbook stays in the pack so casting never clears hands.
            Equip(m, new LeatherChest());
            Equip(m, new LeatherArms());
            Equip(m, new LeatherLegs());
            Equip(m, new LeatherGloves());
            Equip(m, new LeatherGorget());
            Equip(m, new WizardsHat());
            Equip(m, new Sandals());

            m.AddToBackpack(new Spellbook(ulong.MaxValue));
            m.AddToBackpack(new BagOfReagents(100));
        }
        else if (archer)
        {
            // Studded leather: archers move and dodge, and a bow takes both hands.
            var bow = weaponType.CreateInstance<BaseRanged>();
            Equip(m, bow);
            Equip(m, new StuddedChest());
            Equip(m, new StuddedArms());
            Equip(m, new StuddedLegs());
            Equip(m, new StuddedGloves());
            Equip(m, new StuddedGorget());
            Equip(m, new LeatherCap());
            Equip(m, new Boots());

            m.AddToBackpack(new Bandage(200));
            m.AddToBackpack(bow.AmmoType == typeof(Bolt) ? new Bolt(200) : new Arrow(200));
        }
        else
        {
            Equip(m, weaponType.CreateInstance<Item>());
            Equip(m, new RingmailChest());
            Equip(m, new RingmailArms());
            Equip(m, new RingmailLegs());
            Equip(m, new RingmailGloves());
            Equip(m, new LeatherGorget());
            Equip(m, new LeatherCap());
            Equip(m, new Boots());

            m.AddToBackpack(new Bandage(200));
        }

        for (var i = 0; i < 5; i++)
        {
            m.AddToBackpack(new GreaterHealPotion());
            m.AddToBackpack(new GreaterCurePotion());
        }

        RestoreVitals(m);

        m.SendMessage($"Agent {(mage ? "mage" : archer ? "archer" : "warrior")} kit applied.");
        logger.Information(
            "AgentKit: applied to {Mobile} (str {Str} dex {Dex} int {Int}, hits {Hits}/{HitsMax}, weapon {Weapon})",
            m,
            m.RawStr,
            m.RawDex,
            m.RawInt,
            m.Hits,
            m.HitsMax,
            weaponType?.Name ?? "spellbook"
        );
    }

    // A tamer: Animal Taming, Animal Lore and Veterinary 90, weak in a fight itself, with a
    // pet already tamed and following. The previous kit's pet goes first.
    public static void ApplyTamerKit(Mobile m, Type petType)
    {
        ApplyKit(m, typeof(Katana));

        var skills = m.Skills;
        for (var i = 0; i < skills.Length; i++)
        {
            skills[i].Base = 0;
        }

        skills[SkillName.AnimalTaming].Base = 90;
        skills[SkillName.AnimalLore].Base = 90;
        skills[SkillName.Veterinary].Base = 90;
        skills[SkillName.Healing].Base = 70;
        skills[SkillName.Anatomy].Base = 70;
        skills[SkillName.Wrestling].Base = 50;
        m.RawStr = 70;
        m.RawDex = 60;
        m.RawInt = 40;

        // No weapon: the pet does the fighting.
        m.FindItemOnLayer(Layer.OneHanded)?.Delete();
        m.FindItemOnLayer(Layer.TwoHanded)?.Delete();

        if (GivePet(m, petType) is { } pet)
        {
            RestoreVitals(m);
            m.SendMessage($"Agent tamer kit applied, with {pet.Name}.");
        }
    }

    // A pet tamed and following, replacing the last kit's.
    private static BaseCreature GivePet(Mobile m, Type petType)
    {
        var pet = petType.CreateInstance<BaseCreature>();
        if (!pet.SetControlMaster(m))
        {
            pet.Delete();
            m.SendMessage("AgentKit: too many followers for the pet.");
            return null;
        }

        pet.IsBonded = false;
        pet.Loyalty = BaseCreature.MaxLoyalty;
        pet.MoveToWorld(m.Location, m.Map);
        pet.IssueOrder(OrderType.Follow, m, m);
        pet.Hits = pet.HitsMax;
        _kitPets[m] = pet;
        logger.Information("AgentKit: pet {Pet} ({Hits} hits) for {Mobile}", pet.GetType().Name, pet.HitsMax, m);
        return pet;
    }

    // A warrior-mage: the warrior kit with Magery 80 and Evaluating Intelligence 60, a spellbook
    // and reagents in the pack. Casting takes the weapon out of its hands.
    public static void ApplyWarriorMageKit(Mobile m, Type weaponType)
    {
        ApplyKit(m, weaponType);
        m.Skills[SkillName.Magery].Base = 80;
        m.Skills[SkillName.EvalInt].Base = 60;
        m.RawInt = 50;
        m.AddToBackpack(new Spellbook(ulong.MaxValue));
        m.AddToBackpack(new BagOfReagents(100));
        RestoreVitals(m);
        m.SendMessage("Agent warrior-mage kit applied.");
    }

    // A mage-tamer: the mage kit with Animal Taming and Animal Lore 85, Veterinary 60, bandages,
    // and a pet.
    public static void ApplyMageTamerKit(Mobile m, Type petType)
    {
        ApplyKit(m, null);
        m.Skills[SkillName.AnimalTaming].Base = 85;
        m.Skills[SkillName.AnimalLore].Base = 85;
        m.Skills[SkillName.Veterinary].Base = 60;
        m.Skills[SkillName.Wrestling].Base = 0;
        m.AddToBackpack(new Bandage(100));

        if (GivePet(m, petType) is { } pet)
        {
            RestoreVitals(m);
            m.SendMessage($"Agent mage-tamer kit applied, with {pet.Name}.");
        }
    }

    // A bard: Musicianship, Provocation, Peacemaking and Discordance 90, a lute that won't wear
    // out during a test, and no weapon: the songs do the fighting.
    public static void ApplyBardKit(Mobile m)
    {
        ApplyKit(m, typeof(Katana));

        var skills = m.Skills;
        for (var i = 0; i < skills.Length; i++)
        {
            skills[i].Base = 0;
        }

        skills[SkillName.Musicianship].Base = 90;
        skills[SkillName.Provocation].Base = 90;
        skills[SkillName.Peacemaking].Base = 90;
        skills[SkillName.Discordance].Base = 90;
        skills[SkillName.Healing].Base = 60;
        skills[SkillName.Anatomy].Base = 60;
        skills[SkillName.Wrestling].Base = 50;
        m.RawStr = 60;
        m.RawDex = 50;
        m.RawInt = 70;

        m.FindItemOnLayer(Layer.OneHanded)?.Delete();
        m.FindItemOnLayer(Layer.TwoHanded)?.Delete();
        m.AddToBackpack(new Lute { UsesRemaining = 1000 });

        RestoreVitals(m);
        m.SendMessage("Agent bard kit applied.");
        logger.Information("AgentKit: bard kit for {Mobile}", m);
    }

    private static Type GetPetType(string name) =>
        name.ToLowerInvariant() switch
        {
            "bear" or "grizzly" => typeof(GrizzlyBear),
            "wolf" or "direwolf" => typeof(DireWolf),
            "hound" or "hellhound" => typeof(HellHound),
            "drake" => typeof(Drake),
            _ => null
        };

    private static void Equip(Mobile m, Item item)
    {
        if (item != null && !m.EquipItem(item))
        {
            m.AddToBackpack(item);
        }
    }

    private static void RestoreVitals(Mobile m)
    {
        m.Poison = null;
        m.Paralyzed = false;
        m.Frozen = false;
        m.Criminal = false;
        m.Combatant = null;
        m.Hits = m.HitsMax;
        m.Stam = m.StamMax;
        m.Mana = m.ManaMax;
    }

    private static int ClearArena(Mobile from)
    {
        var removed = 0;

        if (_pending.Remove(from, out var tokens))
        {
            foreach (var token in tokens)
            {
                token.Cancel();
            }
        }

        if (_arenaSpawns.Remove(from, out var list))
        {
            foreach (var creature in list)
            {
                _tracked.Remove(creature);

                if (!creature.Deleted)
                {
                    creature.Delete();
                    removed++;
                }
            }
        }

        var map = from.Map;
        if (map == null || map == Map.Internal)
        {
            return removed;
        }

        // Leftovers from before a restart, when the in-memory tracking was lost.
        using var orphans = PooledRefQueue<BaseCreature>.Create();
        foreach (var bc in map.GetMobilesInRange<BaseCreature>(from.Location, OrphanSweepRange))
        {
            if (bc.RemoveIfUntamed && bc.Spawner == null && !bc.Controlled && !bc.Summoned && !_tracked.Contains(bc))
            {
                orphans.Enqueue(bc);
            }
        }

        while (orphans.Count > 0)
        {
            orphans.Dequeue().Delete();
            removed++;
        }

        // Corpses left by earlier rounds would be looted again; the player's own corpse
        // too, since the kit is re-applied after a reset anyway.
        using var corpses = PooledRefQueue<Item>.Create();
        foreach (var item in map.GetItemsInRange<Corpse>(from.Location, OrphanSweepRange))
        {
            corpses.Enqueue(item);
        }

        while (corpses.Count > 0)
        {
            corpses.Dequeue().Delete();
        }

        return removed;
    }

    private static bool TryFindRingSpot(Map map, Point3D center, int index, int count, out Point3D spot)
    {
        var baseAngle = 2 * Math.PI * index / count;

        // Deterministic so repeated runs place monsters the same way; widen the search around the
        // ideal angle and radius until a spawnable tile turns up.
        for (var attempt = 0; attempt < 24; attempt++)
        {
            var offset = (attempt + 1) / 2 * (attempt % 2 == 0 ? 1 : -1) * (Math.PI / 24);
            var angle = baseAngle + offset;
            var radius = ArenaMinRadius + (index + attempt) % (ArenaMaxRadius - ArenaMinRadius + 1);

            var x = center.X + (int)Math.Round(Math.Cos(angle) * radius);
            var y = center.Y + (int)Math.Round(Math.Sin(angle) * radius);
            var z = map.GetAverageZ(x, y);

            if (map.CanSpawnMobile(x, y, z))
            {
                spot = new Point3D(x, y, z);
                return true;
            }

            if (map.CanSpawnMobile(x, y, center.Z))
            {
                spot = new Point3D(x, y, center.Z);
                return true;
            }
        }

        spot = Point3D.Zero;
        return false;
    }

    private static Type GetWeaponType(string name) =>
        name.ToLowerInvariant() switch
        {
            "katana"                => typeof(Katana),
            "broadsword"            => typeof(Broadsword),
            "longsword"             => typeof(Longsword),
            "viking" or "vikingsword" => typeof(VikingSword),
            "bow"                   => typeof(Bow),
            "crossbow"              => typeof(Crossbow),
            "heavycrossbow" or "heavy" => typeof(HeavyCrossbow),
            _                       => null
        };

    private static Type GetCreatureType(string name)
    {
        var type = name.ToLowerInvariant() switch
        {
            "orc"                  => typeof(Orc),
            "rat" or "ratman"      => typeof(Ratman),
            "headless" or "headlessone" => typeof(HeadlessOne),
            "bat" or "mongbat"     => typeof(Mongbat),
            "zombie"               => typeof(Zombie),
            "skeleton"             => typeof(Skeleton),
            _                      => AssemblyHandler.FindTypeByName(name)
        };

        return type?.IsSubclassOf(typeof(BaseCreature)) == true && !type.IsAbstract ? type : null;
    }

    private class KitTarget : Target
    {
        private readonly Type _weapon;

        public KitTarget(Type weapon) : base(-1, false, TargetFlags.None) => _weapon = weapon;

        protected override void OnTarget(Mobile from, object targeted)
        {
            if (targeted is PlayerMobile pm)
            {
                ApplyKit(pm, _weapon);
                from.SendMessage($"Agent kit applied to {pm.Name}.");
            }
            else
            {
                from.SendMessage("That is not a player.");
            }
        }
    }
}
