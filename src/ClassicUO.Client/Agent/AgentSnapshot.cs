// SPDX-License-Identifier: BSD-2-Clause

using System;
using System.Collections.Generic;
using System.Text.Json;
using ClassicUO.Game;
using ClassicUO.Game.Data;
using ClassicUO.Game.GameObjects;
using ClassicUO.Game.Managers;

namespace ClassicUO.Agent
{
    // Writes what the brain needs to decide: the player, nearby mobiles and corpses,
    // new journal lines, and the agent's own state. Raw numbers go out as numbers;
    // the brain turns them into words for the model.
    internal static class AgentSnapshot
    {
        private static readonly string[] CombatSkills =
        {
            "Swordsmanship", "Mace Fighting", "Fencing", "Archery", "Wrestling", "Tactics",
            "Anatomy", "Healing", "Parrying", "Focus", "Resisting Spells", "Magery",
            "Evaluating Intelligence", "Meditation"
        };

        private static readonly AgentJournal.Entry[] _journalBuf = new AgentJournal.Entry[64];
        private static readonly List<Mobile> _mobiles = new List<Mobile>();
        private static readonly List<Item> _corpses = new List<Item>();

        public static void Write(Utf8JsonWriter w, World world, long journalSince, int radius, bool pack = false)
        {
            AgentController agent = world.Agent;
            w.WriteStartObject();
            w.WriteNumber("time_ms", Time.Ticks);
            w.WriteBoolean("in_game", world.InGame && world.Player != null);

            if (!world.InGame || world.Player == null)
            {
                w.WriteEndObject();

                return;
            }

            PlayerMobile p = world.Player;
            WritePlayer(w, world, p, agent);
            WriteMobiles(w, world, p, radius);
            WriteCorpses(w, world, radius);
            WriteJournal(w, agent.Journal, journalSince);
            WriteMagic(w, p, agent);
            WriteDeaths(w, agent);
            WriteSigns(w, world, radius);
            WriteRunes(w, world, p, agent);

            if (pack)
            {
                WritePack(w, world, p);
            }

            WriteAgent(w, world, agent);
            w.WriteEndObject();
        }

        private static void WritePlayer(Utf8JsonWriter w, World world, PlayerMobile p, AgentController agent)
        {
            w.WriteStartObject("player");
            w.WriteNumber("serial", p.Serial);
            w.WriteString("name", p.Name ?? string.Empty);
            w.WriteNumber("x", p.X);
            w.WriteNumber("y", p.Y);
            w.WriteNumber("z", p.Z);
            w.WriteNumber("map", world.MapIndex);
            w.WriteNumber("hits", p.Hits);
            w.WriteNumber("hits_max", p.HitsMax);
            w.WriteNumber("stam", p.Stamina);
            w.WriteNumber("stam_max", p.StaminaMax);
            w.WriteNumber("mana", p.Mana);
            w.WriteNumber("mana_max", p.ManaMax);
            w.WriteNumber("str", p.Strength);
            w.WriteNumber("dex", p.Dexterity);
            w.WriteNumber("int", p.Intelligence);
            w.WriteNumber("weight", p.Weight);
            w.WriteNumber("weight_max", p.WeightMax);
            w.WriteNumber("gold", p.Gold);
            w.WriteBoolean("poisoned", p.IsPoisoned);
            w.WriteBoolean("paralyzed", p.IsParalyzed);
            w.WriteBoolean("dead", p.IsDead);
            w.WriteBoolean("hidden", p.IsHidden);
            w.WriteBoolean("war_mode", p.InWarMode);
            w.WriteBoolean("walking", p.Pathfinder.AutoWalking);

            Item weapon = p.FindItemByLayer(Layer.OneHanded) ?? p.FindItemByLayer(Layer.TwoHanded);
            w.WriteString("weapon", weapon == null ? string.Empty : NameOf(world, weapon));

            w.WriteStartObject("supplies");
            w.WriteNumber("bandages", agent.CountByGraphic(AgentController.BANDAGE_GRAPHIC));
            w.WriteNumber("heal_potions", agent.CountByGraphic(AgentController.HEAL_POTION_GRAPHIC));
            w.WriteNumber("cure_potions", agent.CountByGraphic(AgentController.CURE_POTION_GRAPHIC));
            w.WriteNumber("refresh_potions", agent.CountByGraphic(AgentController.REFRESH_POTION_GRAPHIC));
            w.WriteStartObject("reagents");

            foreach ((_, string name, ushort graphic) in AgentSpells.ReagentGraphics)
            {
                w.WriteNumber(name, agent.CountByGraphic(graphic));
            }

            w.WriteEndObject();
            w.WriteEndObject();

            w.WriteStartObject("skills");

            foreach (Skill s in p.Skills)
            {
                if (s != null && s.Value > 0 && Array.IndexOf(CombatSkills, s.Name) >= 0)
                {
                    w.WriteNumber(s.Name, Math.Round(s.Value, 1));
                }
            }

            w.WriteEndObject();

            w.WriteStartArray("buffs");

            foreach (BuffIconType t in p.BuffIcons.Keys)
            {
                w.WriteStringValue(t.ToString());
            }

            w.WriteEndArray();
            w.WriteEndObject();
        }

        private static void WriteMobiles(Utf8JsonWriter w, World world, PlayerMobile p, int radius)
        {
            _mobiles.Clear();

            foreach (Mobile m in world.Mobiles.Values)
            {
                // Mobiles playing their death animation keep a |0x80000000 serial; they are corpses now.
                if (m != p && !m.IsDestroyed && (m.Serial & 0x80000000) == 0 && m.Distance <= radius)
                {
                    _mobiles.Add(m);
                }
            }

            _mobiles.Sort((a, b) => a.Distance.CompareTo(b.Distance));

            w.WriteStartArray("mobiles");

            for (int i = 0; i < _mobiles.Count && i < 40; i++)
            {
                Mobile m = _mobiles[i];
                w.WriteStartObject();
                w.WriteNumber("serial", m.Serial);
                w.WriteString("name", m.Name?.Trim() ?? string.Empty);

                // The full name a vendor or banker shows, title included ("Lucy the healer"),
                // when the server sends item properties.
                if (world.Agent.LabelOf(m) is string label)
                {
                    w.WriteString("label", label);
                }

                w.WriteNumber("body", m.Graphic);
                w.WriteString("notoriety", m.NotorietyFlag.ToString().ToLowerInvariant());
                w.WriteBoolean("human", m.IsHuman);
                w.WriteBoolean("pet", m.IsRenamable);
                w.WriteBoolean("monster", AgentController.IsMonsterTarget(m));

                if (m.HitsMax > 0)
                {
                    w.WriteNumber("hits_pct", Math.Clamp(m.Hits * 100 / m.HitsMax, 0, 100));
                }
                else
                {
                    w.WriteNull("hits_pct");
                }

                w.WriteBoolean("dead", m.IsDead);
                w.WriteBoolean("poisoned", m.IsPoisoned);
                w.WriteBoolean("war_mode", m.InWarMode);
                w.WriteNumber("distance", m.Distance);
                w.WriteNumber("dx", m.X - p.X);
                w.WriteNumber("dy", m.Y - p.Y);
                w.WriteString("dir", Compass(m.X - p.X, m.Y - p.Y));
                w.WriteBoolean("my_target", m.Serial == world.TargetManager.LastAttack);
                w.WriteBoolean("player_target", m.Serial == world.Agent.PlayerTarget);
                w.WriteBoolean("attacking_me", world.Agent.IsAttackingMe(m));

                if (AgentController.ThreatKind(m, world) is string threat)
                {
                    w.WriteString("threat", threat);
                }
                w.WriteEndObject();
            }

            w.WriteEndArray();
        }

        private static void WriteCorpses(Utf8JsonWriter w, World world, int radius)
        {
            _corpses.Clear();

            foreach (Item it in world.Items.Values)
            {
                if (it.IsCorpse && it.OnGround && !it.IsDestroyed && it.Distance <= radius)
                {
                    _corpses.Add(it);
                }
            }

            _corpses.Sort((a, b) => a.Distance.CompareTo(b.Distance));

            w.WriteStartArray("corpses");

            for (int i = 0; i < _corpses.Count && i < 10; i++)
            {
                Item c = _corpses[i];
                w.WriteStartObject();
                w.WriteNumber("serial", c.Serial);
                w.WriteString("name", NameOf(world, c));
                w.WriteNumber("distance", c.Distance);
                w.WriteString("dir", Compass(c.X - world.Player.X, c.Y - world.Player.Y));
                w.WriteBoolean("opened", c.Opened || c.Items != null);

                bool? monster = world.Agent.CorpseOfMonster(c.Serial);

                if (monster.HasValue)
                {
                    w.WriteBoolean("monster", monster.Value);
                }
                else
                {
                    w.WriteNull("monster");
                }

                w.WriteStartArray("items");

                for (LinkedObject o = c.Items; o != null; o = o.Next)
                {
                    var it = (Item) o;
                    w.WriteStartObject();
                    w.WriteNumber("serial", it.Serial);
                    w.WriteString("name", NameOf(world, it));
                    w.WriteNumber("amount", Math.Max((int) it.Amount, 1));
                    w.WriteNumber("graphic", it.Graphic);

                    if (world.OPL.TryGetNameAndData(it.Serial, out _, out string data) && !string.IsNullOrEmpty(data))
                    {
                        w.WriteString("props", data);
                    }

                    w.WriteBoolean("auto_loot", AgentController.IsAlwaysLoot(it));
                    w.WriteEndObject();
                }

                w.WriteEndArray();
                w.WriteEndObject();
            }

            w.WriteEndArray();
        }

        // Magery: every spell in the book with its cost, and why it cannot be cast now
        // ("" when it can). Only written for characters with a spellbook.
        private static void WriteMagic(Utf8JsonWriter w, PlayerMobile p, AgentController agent)
        {
            Item book = AgentSpells.FindSpellbook(p);

            if (book == null)
            {
                return;
            }

            w.WriteStartObject("magic");
            w.WriteBoolean("book_known", AgentSpells.ContentKnown(book));
            w.WriteString("casting", agent.CastingSpell);
            w.WriteString("queued", agent.QueuedSpell);
            w.WriteNumber("cast_ready_ms", agent.CastReadyInMs);
            w.WriteStartArray("spells");

            foreach (SpellDefinition s in SpellsMagery.GetAllSpells.Values)
            {
                if (!AgentSpells.InBook(book, s.ID))
                {
                    continue;
                }

                w.WriteStartObject();
                w.WriteNumber("id", s.ID);
                w.WriteString("name", s.Name);
                w.WriteNumber("circle", AgentSpells.Circle(s.ID));
                w.WriteNumber("mana", AgentSpells.Mana(s.ID));
                w.WriteString("target", AgentSpells.Kind(s));
                w.WriteString("missing", AgentSpells.Missing(agent, p, book, s));
                w.WriteEndObject();
            }

            w.WriteEndArray();

            w.WriteEndObject();
        }

        // Marked runes and runebooks in the pack, by the name of where they go.
        private static void WriteRunes(Utf8JsonWriter w, World world, PlayerMobile p, AgentController agent)
        {
            w.WriteStartObject("travel_items");
            w.WriteStartArray("runes");
            Item pack = p.FindItemByLayer(Layer.Backpack);

            for (LinkedObject o = pack?.Items; o != null; o = o.Next)
            {
                if (o is Item it && AgentSpells.IsRune(it))
                {
                    // The destination is in the rune's properties ("Britain bank (Felucca)"), or
                    // in its name on shards that rename runes instead.
                    string name = NameOf(world, it);

                    if (world.OPL.TryGetNameAndData(it.Serial, out _, out string data) && !string.IsNullOrWhiteSpace(data))
                    {
                        name = $"{name}: {data.Replace('\n', ' ').Trim()}";
                    }

                    if (!name.Contains("unmarked", StringComparison.OrdinalIgnoreCase))
                    {
                        w.WriteStartObject();
                        w.WriteNumber("serial", it.Serial);
                        w.WriteString("name", name);
                        w.WriteEndObject();
                    }
                }
            }

            w.WriteEndArray();
            w.WriteStartArray("runebooks");

            foreach ((uint serial, List<string> entries) in agent.Runebooks)
            {
                if (world.Items.Get(serial) is Item book && book.RootContainer == p.Serial)
                {
                    w.WriteStartObject();
                    w.WriteNumber("serial", serial);
                    w.WriteStartArray("entries");

                    foreach (string e in entries)
                    {
                        w.WriteStringValue(e);
                    }

                    w.WriteEndArray();
                    w.WriteEndObject();
                }
            }

            w.WriteEndArray();
            w.WriteEndObject();
        }

        // Shop signs in view ("The Healer's Hut"), for recording where shops are.
        private static void WriteSigns(Utf8JsonWriter w, World world, int radius)
        {
            w.WriteStartArray("signs");
            int n = 0;

            foreach (Item it in world.Items.Values)
            {
                if (n >= 10 || !it.OnGround || it.IsDestroyed || it.Distance > radius
                    || it.ItemData.Name == null || !it.ItemData.Name.Contains("sign", StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                string name = NameOf(world, it);

                if (name.Length == 0 || name.Equals(it.ItemData.Name, StringComparison.OrdinalIgnoreCase))
                {
                    continue; // a bare "sign" says nothing
                }

                w.WriteStartObject();
                w.WriteString("text", name);
                w.WriteNumber("x", it.X);
                w.WriteNumber("y", it.Y);
                w.WriteEndObject();
                n++;
            }

            w.WriteEndArray();
        }

        // Creatures that died in the last minute, oldest first.
        private static void WriteDeaths(Utf8JsonWriter w, AgentController agent)
        {
            w.WriteStartArray("deaths");

            foreach ((uint serial, string name, ushort body, uint time) in agent.RecentDeaths)
            {
                if (Time.Ticks - time > 60_000)
                {
                    continue;
                }

                w.WriteStartObject();
                w.WriteNumber("serial", serial);
                w.WriteString("name", name);
                w.WriteNumber("body", body);
                w.WriteNumber("time_ms", time);
                w.WriteEndObject();
            }

            w.WriteEndArray();
        }

        // Everything in the backpack, bags included (asked for with "pack": true).
        private static void WritePack(Utf8JsonWriter w, World world, PlayerMobile p)
        {
            w.WriteStartArray("pack");
            Item backpack = p.FindItemByLayer(Layer.Backpack);
            int written = 0;

            void Walk(Item container)
            {
                for (LinkedObject o = container.Items; o != null && written < 200; o = o.Next)
                {
                    var it = (Item) o;
                    w.WriteStartObject();
                    w.WriteNumber("serial", it.Serial);
                    w.WriteNumber("container", container.Serial);
                    w.WriteString("name", NameOf(world, it));
                    w.WriteNumber("graphic", it.Graphic);
                    w.WriteNumber("amount", Math.Max((int) it.Amount, 1));
                    w.WriteEndObject();
                    written++;

                    if (it.Items != null && it.Graphic != AgentSpells.SPELLBOOK_GRAPHIC)
                    {
                        Walk(it);
                    }
                }
            }

            if (backpack != null)
            {
                Walk(backpack);
            }

            w.WriteEndArray();
        }

        private static void WriteJournal(Utf8JsonWriter w, AgentJournal journal, long since)
        {
            int n = journal.CopySince(since, _journalBuf);
            w.WriteNumber("journal_seq", journal.LastSeq);
            w.WriteStartArray("journal");

            for (int i = 0; i < n; i++)
            {
                ref AgentJournal.Entry e = ref _journalBuf[i];
                w.WriteStartObject();
                w.WriteNumber("seq", e.Seq);
                w.WriteNumber("time_ms", e.Time);
                w.WriteString("name", e.Name ?? string.Empty);
                w.WriteNumber("serial", e.Serial);
                w.WriteString("text", e.Text ?? string.Empty);
                w.WriteString("type", e.Type.ToString().ToLowerInvariant());
                w.WriteString("source", e.Agent ? "agent" : e.FromSelf ? "self" : e.TextType == TextType.SYSTEM ? "system" : e.TextType == TextType.CLIENT ? "client" : "speech");
                w.WriteEndObject();
            }

            w.WriteEndArray();
        }

        private static void WriteAgent(Utf8JsonWriter w, World world, AgentController agent)
        {
            w.WriteStartObject("agent");
            w.WriteString("mode", agent.Mode.Name());
            w.WriteString("engage", agent.Engage.Name());
            w.WriteNumber("player_target", agent.PlayerTarget);
            w.WriteString("strategy", agent.Strategy);
            w.WriteNumber("strategy_rev", agent.StrategyRevision);
            w.WriteStartObject("goal");
            w.WriteString("text", agent.Goal);
            w.WriteNumber("rev", agent.GoalRevision);
            w.WriteBoolean("paused", agent.GoalPaused);
            w.WriteString("step", agent.GoalStep);
            w.WriteEndObject();
            w.WriteStartObject("authority");

            foreach (AgentBehavior b in AgentModes.AllBehaviors)
            {
                w.WriteString(b.Name(), agent.GetAuthority(b).Name());
            }

            w.WriteEndObject();
            w.WriteBoolean("human_active", agent.HumanActive);
            w.WriteNumber("ms_since_human", agent.LastHumanInput == 0 ? -1 : (long) (Time.Ticks - agent.LastHumanInput));
            w.WriteBoolean("bandaging", agent.Bandaging);
            w.WriteNumber("heal_potion_ready_ms", agent.HealPotionReadyInMs);
            w.WriteBoolean("fleeing", agent.Fleeing);
            w.WriteNumber("engaged", agent.Engaged);
            w.WriteNumber("engaged_range", agent.EngagedRange);
            w.WriteString("casting", agent.CastingSpell);
            w.WriteNumber("cast_ready_ms", agent.CastReadyInMs);
            w.WriteNumber("looting", agent.LootCorpse);
            w.WriteStartArray("threats");

            foreach ((uint serial, string kind, int distance) in agent.Threats)
            {
                w.WriteStartObject();
                w.WriteNumber("serial", serial);
                w.WriteString("kind", kind);
                w.WriteNumber("distance", distance);
                w.WriteEndObject();
            }

            w.WriteEndArray();

            if (agent.Errands.Kind.Length != 0)
            {
                w.WriteStartObject("errand");
                w.WriteString("kind", agent.Errands.Kind);
                w.WriteString("state", agent.Errands.State);
                w.WriteString("detail", agent.Errands.Detail);
                w.WriteNumber("moved", agent.Errands.Moved);
                w.WriteNumber("gold_change", agent.Errands.GoldChange);
                w.WriteEndObject();
            }

            if (agent.TravelState.Length != 0)
            {
                w.WriteStartObject("travel");
                w.WriteString("state", agent.TravelState);
                w.WriteNumber("x", agent.TravelGoal.X);
                w.WriteNumber("y", agent.TravelGoal.Y);
                w.WriteNumber("left", agent.TravelLeft);
                w.WriteNumber("replans", agent.TravelReplans);
                w.WriteNumber("elapsed_ms", agent.TravelElapsedMs);
                w.WriteStartArray("stuck_at");

                foreach ((int x, int y) in agent.TravelStuckAt)
                {
                    w.WriteStartArray();
                    w.WriteNumberValue(x);
                    w.WriteNumberValue(y);
                    w.WriteEndArray();
                }

                w.WriteEndArray();
                w.WriteEndObject();
            }
            w.WriteBoolean("targeting", world.TargetManager.IsTargeting);

            if (agent.Suggestion != null)
            {
                w.WriteString("suggestion", agent.Suggestion.Describe(world));
            }

            w.WriteStartObject("stats");
            AgentStats s = agent.Stats;
            w.WriteNumber("kills", s.Kills);
            w.WriteNumber("deaths", s.Deaths);
            w.WriteNumber("attacks", s.Attacks);
            w.WriteNumber("bandages", s.Bandages);
            w.WriteNumber("heal_potions", s.HealPotions);
            w.WriteNumber("cure_potions", s.CurePotions);
            w.WriteNumber("reflexes", s.Reflexes);
            w.WriteNumber("flees", s.Flees);
            w.WriteNumber("corpses_looted", s.Looted);
            w.WriteNumber("items_taken", s.ItemsTaken);
            w.WriteNumber("brain_actions", s.BrainActions);
            w.WriteNumber("suggestions", s.Suggestions);
            w.WriteNumber("accepted", s.Accepted);
            w.WriteNumber("blocked", s.Blocked);
            w.WriteNumber("deferred", s.Deferred);
            w.WriteNumber("casts", s.Casts);
            w.WriteNumber("spell_heals", s.SpellHeals);
            w.WriteNumber("threats", s.Threats);
            w.WriteNumber("kites", s.Kites);
            w.WriteNumber("cliloc_messages", s.ClilocMessages);
            w.WriteNumber("text_messages", s.TextMessages);
            w.WriteEndObject();
            w.WriteEndObject();
        }

        public static string NameOf(World world, Item it)
        {
            if (world.OPL.TryGetNameAndData(it.Serial, out string name, out _) && !string.IsNullOrEmpty(name))
            {
                return name.Trim();
            }

            return (!string.IsNullOrEmpty(it.Name) ? it.Name : it.ItemData.Name ?? string.Empty).Trim();
        }

        // UO's y axis grows southwards.
        public static string Compass(int dx, int dy)
        {
            if (dx == 0 && dy == 0)
            {
                return "here";
            }

            double angle = Math.Atan2(-dy, dx) * 180 / Math.PI;
            string[] names = { "east", "northeast", "north", "northwest", "west", "southwest", "south", "southeast" };
            int idx = (int) Math.Round(((angle + 360) % 360) / 45) % 8;

            return names[idx];
        }
    }
}
