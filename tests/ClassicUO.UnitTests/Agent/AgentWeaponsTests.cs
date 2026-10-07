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

        [Theory]
        [InlineData("kill", "Fight")]
        [InlineData("follow", "Move")]
        [InlineData("stay", "Move")]
        public void Setting_the_pet_on_a_creature_is_fighting_calling_it_back_is_moving(string order, string behaviour)
        {
            new AgentAction { Verb = "pet", Kind = order }.Behavior.ToString().Should().Be(behaviour);
        }

        [Fact]
        public void A_song_carries_its_targets_and_answers_to_fight()
        {
            using var doc = System.Text.Json.JsonDocument.Parse("{\"verb\":\"skill\",\"name\":\"Provocation\",\"targets\":[256,\"0x101\"]}");
            AgentAction a = AgentAction.FromJson(doc.RootElement);
            a.Targets.Should().Equal(256u, 0x101u);
            a.Behavior.ToString().Should().Be("Fight");
            new AgentAction { Verb = "skill", Name = "Meditation" }.Behavior.ToString().Should().Be("Misc");
        }

        [Theory]
        [InlineData((ushort) 0x0EB3, true)]  // lute
        [InlineData((ushort) 0x0E9C, true)]  // drum
        [InlineData((ushort) 0x0EFA, false)] // spellbook
        public void Instruments(ushort graphic, bool instrument)
        {
            AgentBard.IsInstrument(graphic).Should().Be(instrument);
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
