// SPDX-License-Identifier: BSD-2-Clause

using System;
using System.Collections.Generic;
using ClassicUO.Game;
using ClassicUO.Game.Data;
using ClassicUO.Game.GameObjects;
using ClassicUO.Game.Managers;

namespace ClassicUO.Agent
{
    // The schools of spells the agent casts from: Magery, and on AOS-era shards Necromancy and
    // Chivalry (cuo-cvl.5).
    internal enum AgentSchool
    {
        None,
        Magery,
        Necromancy,
        Chivalry
    }

    // Spell facts the agent needs to cast: costs, reagents, timings and what is in each
    // book. ClassicUO's magery table has names, reagents and target types but no mana costs,
    // so those come from the circle; the necromancy and chivalry tables carry their own.
    internal static class AgentSpells
    {
        public const ushort SPELLBOOK_GRAPHIC = 0x0EFA;
        public const ushort NECRO_BOOK_GRAPHIC = 0x2253, CHIVALRY_BOOK_GRAPHIC = 0x2252;
        public const int CLOSE_WOUNDS = 202, CLEANSE_BY_FIRE = 201;

        // Spells that need no target but are part of a fight: Consecrate Weapon, Divine Fury, Enemy
        // of One, Dispel Evil (chivalry); Curse Weapon, Wither (necromancy). They answer to fight.
        public static readonly HashSet<int> FightBlessings = new HashSet<int> { 203, 204, 205, 206, 104, 116 };

        // Mana per circle, first to eighth.
        private static readonly int[] CircleMana = { 4, 6, 9, 11, 14, 20, 40, 50 };

        // Recovery after a spell's cast delay ends, before the next can start (ModernUO:
        // CastRecoveryBase 6 / CastRecoveryPerSecond 4, with no faster cast recovery).
        public const uint RECOVERY_MS = 1500;

        public static readonly (Reagents Reagent, string Name, ushort Graphic)[] ReagentGraphics =
        {
            (Reagents.BlackPearl, "black_pearl", 0x0F7A),
            (Reagents.Bloodmoss, "blood_moss", 0x0F7B),
            (Reagents.Garlic, "garlic", 0x0F84),
            (Reagents.Ginseng, "ginseng", 0x0F85),
            (Reagents.MandrakeRoot, "mandrake_root", 0x0F86),
            (Reagents.Nightshade, "nightshade", 0x0F88),
            (Reagents.SulfurousAsh, "sulfurous_ash", 0x0F8C),
            (Reagents.SpidersSilk, "spiders_silk", 0x0F8D)
        };

        // A necromancer's reagents, kept apart so a mage's reagent counts don't read "out of bat wing".
        public static readonly (Reagents Reagent, string Name, ushort Graphic)[] PaganReagentGraphics =
        {
            (Reagents.BatWing, "bat_wing", 0x0F78),
            (Reagents.GraveDust, "grave_dust", 0x0F8F),
            (Reagents.DaemonBlood, "daemon_blood", 0x0F7D),
            (Reagents.NoxCrystal, "nox_crystal", 0x0F8E),
            (Reagents.PigIron, "pig_iron", 0x0F8A)
        };

        // ModernUO's cast delays for necromancy and chivalry, per spell (CastDelayBase), in ms.
        private static readonly Dictionary<int, uint> SchoolCastDelayMs = new Dictionary<int, uint>
        {
            [101] = 1750, [102] = 1500, [103] = 1500, [104] = 750, [105] = 750, [106] = 2000, [107] = 2000,
            [108] = 1500, [109] = 1000, [110] = 1750, [111] = 2000, [112] = 2000, [113] = 2000, [114] = 1500,
            [115] = 2000, [116] = 2000, [117] = 2000,
            [201] = 1000, [202] = 1500, [203] = 500, [204] = 250, [205] = 1000, [206] = 500, [207] = 1750,
            [208] = 1500, [209] = 1750, [210] = 1500
        };

        public static AgentSchool SchoolOf(int id) => id >= 1 && id <= 64 ? AgentSchool.Magery
            : id >= 101 && id <= 117 ? AgentSchool.Necromancy
            : id >= 201 && id <= 210 ? AgentSchool.Chivalry : AgentSchool.None;

        public static ushort BookGraphic(AgentSchool s) => s switch
        {
            AgentSchool.Necromancy => NECRO_BOOK_GRAPHIC,
            AgentSchool.Chivalry => CHIVALRY_BOOK_GRAPHIC,
            _ => SPELLBOOK_GRAPHIC
        };

        public static string SkillOf(AgentSchool s) => s switch
        {
            AgentSchool.Necromancy => "Necromancy",
            AgentSchool.Chivalry => "Chivalry",
            _ => "Magery"
        };

        public static IEnumerable<SpellDefinition> SpellsOf(AgentSchool s) => s switch
        {
            AgentSchool.Necromancy => SpellsNecromancy.GetAllSpells.Values,
            AgentSchool.Chivalry => SpellsChivalry.GetAllSpells.Values,
            _ => SpellsMagery.GetAllSpells.Values
        };

        public static readonly AgentSchool[] Schools = { AgentSchool.Magery, AgentSchool.Necromancy, AgentSchool.Chivalry };

        public const int HEAL = 4, CURE = 11, GREATER_HEAL = 29, RECALL = 32, GATE_TRAVEL = 52;
        public const ushort RUNEBOOK_GRAPHIC = 0x22C5;

        // Before AOS a runebook has the spellbook's graphic, told apart by its hue.
        public static bool IsRunebook(Item it) => it.Graphic == RUNEBOOK_GRAPHIC || it.Graphic == SPELLBOOK_GRAPHIC && it.Hue == 0x461;

        // Marked and unmarked recall runes.
        public static bool IsRune(Item it) => it.Graphic >= 0x1F14 && it.Graphic <= 0x1F17;

        public static int Circle(int id) => (id - 1) / 8 + 1;

        public static int Mana(int id) => SchoolOf(id) switch
        {
            AgentSchool.Necromancy => SpellsNecromancy.GetSpell(id - 100).ManaCost,
            AgentSchool.Chivalry => SpellsChivalry.GetSpell(id - 200).ManaCost,
            _ => CircleMana[Math.Clamp(Circle(id), 1, 8) - 1]
        };

        public static int Tithing(int id) => SchoolOf(id) == AgentSchool.Chivalry ? SpellsChivalry.GetSpell(id - 200).TithingCost : 0;

        // ModernUO magery: (3 + circle index) ticks of 0.25s, with no faster casting; necromancy
        // and chivalry by spell.
        public static uint CastDelayMs(int id) => SchoolCastDelayMs.TryGetValue(id, out uint ms) ? ms : (uint) (Circle(id) + 2) * 250;

        // Chivalry recovers more slowly (CastRecoveryBase 7 ticks of 0.25 s, against 6).
        public static uint RecoveryMs(int id) => SchoolOf(id) == AgentSchool.Chivalry ? 1750u : RECOVERY_MS;

        public static bool IsMagery(int id) => id >= 1 && id <= 64;

        // "Energy Bolt", "energy bolt", "energybolt", "42".
        public static SpellDefinition Find(string nameOrId)
        {
            if (string.IsNullOrWhiteSpace(nameOrId))
            {
                return null;
            }

            if (int.TryParse(nameOrId, out int id))
            {
                SpellDefinition byId = SchoolOf(id) switch
                {
                    AgentSchool.Magery => SpellsMagery.GetSpell(id),
                    AgentSchool.Necromancy => SpellsNecromancy.GetSpell(id - 100),
                    AgentSchool.Chivalry => SpellsChivalry.GetSpell(id - 200),
                    _ => null
                };

                return byId == null || byId.ID == 0 ? null : byId;
            }

            string want = Normalize(nameOrId);

            foreach (AgentSchool school in Schools)
            {
                foreach (SpellDefinition s in SpellsOf(school))
                {
                    if (Normalize(s.Name) == want)
                    {
                        return s;
                    }
                }
            }

            return null;
        }

        private static string Normalize(string s)
        {
            var chars = new char[s.Length];
            int n = 0;

            foreach (char c in s)
            {
                if (char.IsLetterOrDigit(c))
                {
                    chars[n++] = char.ToLowerInvariant(c);
                }
            }

            return new string(chars, 0, n);
        }

        public static string Kind(SpellDefinition s)
        {
            switch (s.TargetType)
            {
                case TargetType.Harmful: return "harmful";
                case TargetType.Beneficial: return "beneficial";
                default: return "neutral";
            }
        }

        // The magery spellbook in the pack (one level of bags deep) or in hand.
        public static Item FindSpellbook(PlayerMobile p) => FindBook(p, AgentSchool.Magery);

        // A school's book in the pack (one level of bags deep) or in hand.
        public static Item FindBook(PlayerMobile p, AgentSchool school)
        {
            ushort graphic = BookGraphic(school);
            Item hand = p.FindItemByLayer(Layer.OneHanded);

            if (hand != null && hand.Graphic == graphic && !IsRunebook(hand))
            {
                return hand;
            }

            Item pack = p.FindItemByLayer(Layer.Backpack);

            return pack == null ? null : FindIn(pack, 2, graphic);
        }

        public static bool IsBook(Item it) => (it.Graphic == SPELLBOOK_GRAPHIC || it.Graphic == NECRO_BOOK_GRAPHIC
                                               || it.Graphic == CHIVALRY_BOOK_GRAPHIC) && !IsRunebook(it);

        private static Item FindIn(Item container, int depth, ushort graphic)
        {
            for (LinkedObject i = container.Items; i != null; i = i.Next)
            {
                var it = (Item) i;

                if (it.Graphic == graphic && !IsRunebook(it))
                {
                    return it;
                }

                if (depth > 1 && it.Items != null && FindIn(it, depth - 1, graphic) is Item found)
                {
                    return found;
                }
            }

            return null;
        }

        // The server only sends a book's contents when it is opened. Until then the
        // book's children are unknown and every spell is assumed present.
        public static bool ContentKnown(Item book) => book != null && book.Items != null;

        public static bool InBook(Item book, int id)
        {
            if (book == null)
            {
                return false;
            }

            if (book.Items == null)
            {
                return true;
            }

            // The new spellbook packet numbers a book's spells from 1 (necromancy 101 is 1 there);
            // the old container packet sends the spell's own number.
            int offset = SchoolOf(id) switch { AgentSchool.Necromancy => 100, AgentSchool.Chivalry => 200, _ => 0 };

            for (LinkedObject i = book.Items; i != null; i = i.Next)
            {
                int amount = ((Item) i).Amount;

                if (amount == id || offset != 0 && amount == id - offset)
                {
                    return true;
                }
            }

            return false;
        }

        public static ushort ReagentGraphic(Reagents r)
        {
            foreach ((Reagents reagent, _, ushort graphic) in ReagentGraphics)
            {
                if (reagent == r)
                {
                    return graphic;
                }
            }

            foreach ((Reagents reagent, _, ushort graphic) in PaganReagentGraphics)
            {
                if (reagent == r)
                {
                    return graphic;
                }
            }

            return 0;
        }

        // Why this spell cannot be cast right now ("" when it can), leaving timing aside.
        public static string Missing(AgentController agent, PlayerMobile p, Item book, SpellDefinition s)
        {
            if (book == null)
            {
                return "spellbook";
            }

            if (!InBook(book, s.ID))
            {
                return "not in book";
            }

            if (p.Mana < Mana(s.ID))
            {
                return "mana";
            }

            if (p.TithingPoints < Tithing(s.ID))
            {
                return "tithing";
            }

            foreach (Reagents r in s.Regs)
            {
                // Chivalry's table lists Reagents.None: it is paid in tithing points instead.
                if (r != Reagents.None && agent.CountByGraphic(ReagentGraphic(r)) == 0)
                {
                    return "reagents";
                }
            }

            return string.Empty;
        }

        public static double MagerySkill(PlayerMobile p) => SkillValue(p, "Magery");

        public static double SkillValue(PlayerMobile p, string name)
        {
            foreach (Skill s in p.Skills)
            {
                if (s != null && s.Name == name)
                {
                    return s.Value;
                }
            }

            return 0;
        }

        public static int SkillIndex(PlayerMobile p, string name)
        {
            string want = Normalize(name);

            foreach (Skill s in p.Skills)
            {
                if (s != null && Normalize(s.Name) == want)
                {
                    return s.Index;
                }
            }

            return -1;
        }
    }
}
