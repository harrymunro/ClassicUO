using System;
using System.Collections.Generic;
using Server.Accounting;
using Server.Collections;
using Server.Commands;
using Server.Items;
using Server.Logging;
using Server.Misc;
using Server.Mobiles;
using Server.Targeting;

namespace Server.Custom;

/// <summary>
/// Commands for a repeatable warrior test bed driven by an AI agent:
/// [AgentKit, [AgentArena, [AgentReset and [AgentGo.
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

    private static readonly Type[] _defaultMix =
        [typeof(Orc), typeof(Ratman), typeof(HeadlessOne), typeof(Mongbat)];

    private static readonly Dictionary<Mobile, List<BaseCreature>> _arenaSpawns = new();

    // Every creature in _arenaSpawns, so the orphan sweep can tell live arenas from leftovers of a
    // previous boot (the tracking itself is not persisted).
    private static readonly HashSet<BaseCreature> _tracked = new();

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

    [Usage("AgentKit [katana|broadsword|longsword|vikingsword] [target]")]
    [Description(
        "Resets a warrior test kit: Swords/Tactics/Healing/Anatomy 80 (others 0), 90/70/15 stats, weapon, ring/leather armor, bandages and potions, full hits/stam/mana. Wipes previous equipment and backpack contents. 'target' (GameMaster+) applies it to another player."
    )]
    public static void AgentKit_OnCommand(CommandEventArgs e)
    {
        var from = e.Mobile;
        var weapon = typeof(Katana);
        var useTarget = false;

        for (var i = 0; i < e.Length; i++)
        {
            var arg = e.GetString(i);
            if (arg.InsensitiveEquals("target"))
            {
                useTarget = true;
                continue;
            }

            var type = GetWeaponType(arg);
            if (type == null)
            {
                from.SendMessage("Usage: [AgentKit [katana|broadsword|longsword|vikingsword] [target]");
                return;
            }

            weapon = type;
        }

        if (!useTarget)
        {
            ApplyKit(from, weapon);
            return;
        }

        if (from.AccessLevel < AccessLevel.GameMaster)
        {
            from.SendMessage("Only GameMasters can apply the kit to another player.");
            return;
        }

        from.SendMessage("Target the player to equip.");
        from.Target = new KitTarget(weapon);
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

        if (!_arenaSpawns.TryGetValue(from, out var list))
        {
            list = [];
            _arenaSpawns[from] = list;
        }

        for (var i = 0; i < count; i++)
        {
            if (!TryFindRingSpot(map, center, i, count, out var spot))
            {
                continue;
            }

            var type = kind ?? _defaultMix[i % _defaultMix.Length];
            var creature = type.CreateInstance<BaseCreature>();
            if (creature == null)
            {
                continue;
            }

            creature.Home = center;
            creature.RangeHome = ArenaHomeRange;

            // Marks the creature as an arena spawn that survives restarts: the loyalty timer removes
            // spawner-less RemoveIfUntamed creatures on its own, and the orphan sweep keys on it.
            creature.RemoveIfUntamed = true;
            creature.MoveToWorld(spot, map);

            list.Add(creature);
            _tracked.Add(creature);
            spawned++;
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
    [Description("Resurrects you if dead, restores hits/stam/mana, cures poison and removes your arena monsters.")]
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

    [Usage("AgentGo")]
    [Description("Teleports you to the agent test location.")]
    public static void AgentGo_OnCommand(CommandEventArgs e)
    {
        var from = e.Mobile;
        from.MoveToWorld(TestLocation, TestMap);
        from.SendMessage($"Moved to the agent test location {TestLocation} on {TestMap}.");
        logger.Information("AgentGo: {Mobile} moved to {Location} on {Map}", from, TestLocation, TestMap);
    }

    public static void ApplyKit(Mobile m, Type weaponType)
    {
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

        var skills = m.Skills;
        for (var i = 0; i < skills.Length; i++)
        {
            skills[i].Base = 0;
        }

        skills[SkillName.Swords].Base = 80;
        skills[SkillName.Tactics].Base = 80;
        skills[SkillName.Healing].Base = 80;
        skills[SkillName.Anatomy].Base = 80;

        // Locked so stat gain/atrophy cannot drift a run away from the baseline.
        m.StrLock = StatLockType.Locked;
        m.DexLock = StatLockType.Locked;
        m.IntLock = StatLockType.Locked;
        m.RawStr = 90;
        m.RawDex = 70;
        m.RawInt = 15;

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

        Equip(m, weaponType.CreateInstance<Item>());
        Equip(m, new RingmailChest());
        Equip(m, new RingmailArms());
        Equip(m, new RingmailLegs());
        Equip(m, new RingmailGloves());
        Equip(m, new LeatherGorget());
        Equip(m, new LeatherCap());
        Equip(m, new Boots());

        m.AddToBackpack(new Bandage(200));

        for (var i = 0; i < 5; i++)
        {
            m.AddToBackpack(new GreaterHealPotion());
            m.AddToBackpack(new GreaterCurePotion());
        }

        RestoreVitals(m);

        m.SendMessage("Agent warrior kit applied.");
        logger.Information(
            "AgentKit: applied to {Mobile} (str {Str} dex {Dex} int {Int}, hits {Hits}/{HitsMax}, weapon {Weapon})",
            m,
            m.RawStr,
            m.RawDex,
            m.RawInt,
            m.Hits,
            m.HitsMax,
            weaponType.Name
        );
    }

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
