using ClassicUO.Agent;
using FluentAssertions;
using Xunit;

namespace ClassicUO.UnitTests.Agent
{
    public class AgentWeaponsTests
    {
        [Theory]
        [InlineData((ushort) 0x13B2, "bow", "arrows", 10)]
        [InlineData((ushort) 0x13B1, "bow", "arrows", 10)]
        [InlineData((ushort) 0x26C2, "bow", "arrows", 10)]
        [InlineData((ushort) 0x0F50, "crossbow", "bolts", 8)]
        [InlineData((ushort) 0x13FC, "crossbow", "bolts", 8)]
        [InlineData((ushort) 0x26C3, "crossbow", "bolts", 7)]
        public void Bows_and_crossbows(ushort graphic, string kind, string ammo, int range)
        {
            AgentWeapons.TryGetRanged(graphic, out AgentWeapons.Ranged r).Should().BeTrue();
            (r.Kind, r.AmmoName, r.Range).Should().Be((kind, ammo, range));
        }

        [Fact]
        public void A_katana_is_not_ranged()
        {
            AgentWeapons.TryGetRanged(0x13FF, out _).Should().BeFalse();
        }

        [Fact]
        public void Arrows_and_bolts_are_ammo()
        {
            AgentWeapons.IsAmmo(AgentWeapons.ARROW_GRAPHIC).Should().BeTrue();
            AgentWeapons.IsAmmo(AgentWeapons.BOLT_GRAPHIC).Should().BeTrue();
            AgentWeapons.IsAmmo(0x0E21).Should().BeFalse();
        }
    }
}
