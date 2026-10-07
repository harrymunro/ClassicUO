// SPDX-License-Identifier: BSD-2-Clause

using System;
using ClassicUO.Game;
using ClassicUO.Game.Data;
using ClassicUO.Game.GameObjects;
using ClassicUO.Game.Managers;

namespace ClassicUO.Agent
{
    // Magery facts the agent needs to cast: costs, reagents, timings and what is in
    // the spellbook. ClassicUO's spell table has names, reagents and target types
    // but no mana costs, so those come from the circle.
    internal static class AgentSpells
    {
        public const ushort SPELLBOOK_GRAPHIC = 0x0EFA;

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

        public const int HEAL = 4, CURE = 11, GREATER_HEAL = 29, RECALL = 32, GATE_TRAVEL = 52;
        public const ushort RUNEBOOK_GRAPHIC = 0x22C5;

        // Marked and unmarked recall runes.
        public static bool IsRune(Item it) => it.Graphic >= 0x1F14 && it.Graphic <= 0x1F17;

        public static int Circle(int id) => (id - 1) / 8 + 1;

        public static int Mana(int id) => CircleMana[Math.Clamp(Circle(id), 1, 8) - 1];

        // ModernUO magery: (3 + circle index) ticks of 0.25s, with no faster casting.
        public static uint CastDelayMs(int id) => (uint) (Circle(id) + 2) * 250;

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
                SpellDefinition byId = SpellsMagery.GetSpell(id);

                return byId.ID == 0 ? null : byId;
            }

            string want = Normalize(nameOrId);

            foreach (SpellDefinition s in SpellsMagery.GetAllSpells.Values)
            {
                if (Normalize(s.Name) == want)
                {
                    return s;
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
        public static Item FindSpellbook(PlayerMobile p)
        {
            Item hand = p.FindItemByLayer(Layer.OneHanded);

            if (hand != null && hand.Graphic == SPELLBOOK_GRAPHIC)
            {
                return hand;
            }

            Item pack = p.FindItemByLayer(Layer.Backpack);

            return pack == null ? null : FindIn(pack, 2);
        }

        private static Item FindIn(Item container, int depth)
        {
            for (LinkedObject i = container.Items; i != null; i = i.Next)
            {
                var it = (Item) i;

                if (it.Graphic == SPELLBOOK_GRAPHIC)
                {
                    return it;
                }

                if (depth > 1 && it.Items != null && FindIn(it, depth - 1) is Item found)
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

            for (LinkedObject i = book.Items; i != null; i = i.Next)
            {
                if (((Item) i).Amount == id)
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

            foreach (Reagents r in s.Regs)
            {
                if (agent.CountByGraphic(ReagentGraphic(r)) == 0)
                {
                    return "reagents";
                }
            }

            return string.Empty;
        }

        public static double MagerySkill(PlayerMobile p)
        {
            foreach (Skill s in p.Skills)
            {
                if (s != null && s.Name == "Magery")
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
