// SPDX-License-Identifier: BSD-2-Clause

namespace ClassicUO.Agent
{
    // Bows and crossbows: what they shoot and how far, by item graphic (both facings). A
    // ranged weapon fires only with its ammunition in the pack, and only once the archer has
    // stood still for a moment (a second before AOS, half a second under AOS, a quarter from
    // Samurai Empire), so stepping back costs shots.
    internal static class AgentWeapons
    {
        public const ushort ARROW_GRAPHIC = 0x0F3F;
        public const ushort BOLT_GRAPHIC = 0x1BFB;

        public readonly struct Ranged
        {
            public Ranged(string kind, ushort ammo, int range)
            {
                Kind = kind;
                Ammo = ammo;
                Range = range;
            }

            public readonly string Kind;
            public readonly ushort Ammo;
            public readonly int Range;

            public string AmmoName => Ammo == BOLT_GRAPHIC ? "bolts" : "arrows";
        }

        public static bool TryGetRanged(ushort graphic, out Ranged ranged)
        {
            switch (graphic)
            {
                case 0x13B2: case 0x13B1: // bow
                case 0x26C2: case 0x26CC: // composite bow
                case 0x27A5: case 0x27F0: // yumi
                case 0x2D1E: case 0x2D2A: // elven composite longbow
                case 0x2D2B: case 0x2D1F: // magical shortbow
                    ranged = new Ranged("bow", ARROW_GRAPHIC, 10);

                    return true;

                case 0x0F50: case 0x0F4F: // crossbow
                case 0x13FD: case 0x13FC: // heavy crossbow
                    ranged = new Ranged("crossbow", BOLT_GRAPHIC, 8);

                    return true;

                case 0x26C3: case 0x26CD: // repeating crossbow
                    ranged = new Ranged("crossbow", BOLT_GRAPHIC, 7);

                    return true;
            }

            ranged = default;

            return false;
        }

        public static bool IsAmmo(ushort graphic) => graphic == ARROW_GRAPHIC || graphic == BOLT_GRAPHIC;
    }
}
