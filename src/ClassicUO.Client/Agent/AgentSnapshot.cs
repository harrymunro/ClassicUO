// SPDX-License-Identifier: BSD-2-Clause

using System;
using System.Collections.Generic;
using System.Text.Json;
using ClassicUO.Game;
using ClassicUO.Game.Data;
using ClassicUO.Game.GameObjects;

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
            "Anatomy", "Healing", "Parrying", "Focus", "Resisting Spells", "Magery"
        };

        private static readonly AgentJournal.Entry[] _journalBuf = new AgentJournal.Entry[64];
        private static readonly List<Mobile> _mobiles = new List<Mobile>();
        private static readonly List<Item> _corpses = new List<Item>();

        public static void Write(Utf8JsonWriter w, World world, long journalSince, int radius)
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
            w.WriteString("strategy", agent.Strategy);
            w.WriteNumber("strategy_rev", agent.StrategyRevision);
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
            w.WriteNumber("looting", agent.LootCorpse);
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
