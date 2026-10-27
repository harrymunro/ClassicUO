using ClassicUO.Agent;
using FluentAssertions;
using Xunit;

namespace ClassicUO.UnitTests.Agent
{
    // A creature next to the character that no blow reaches is given up (2026-10-27: a resident stood by an unhurt zombie
    // in Cove's cemetery for 15 minutes, the server never swinging at it).
    public class AgentEngageTests
    {
        [Fact]
        public void In_melee_next_to_it_with_no_blow_landing_for_half_a_minute_it_is_given_up()
        {
            const uint since = 1_000_000;
            AgentController.NoBlowLands(1, 1, since + AgentController.ENGAGE_NO_BLOW_MS - 1, since).Should().BeFalse();
            AgentController.NoBlowLands(1, 1, since + AgentController.ENGAGE_NO_BLOW_MS + 1, since).Should().BeTrue();
            AgentController.NoBlowLands(1, 0, since + 60_000, since).Should().BeTrue("on its tile");
        }

        [Fact]
        public void Not_at_range_nor_while_walking_up_to_it()
        {
            const uint since = 1_000_000;
            AgentController.NoBlowLands(10, 1, since + 60_000, since).Should().BeFalse("a caster or an archer: their sight is checked");
            AgentController.NoBlowLands(1, 2, since + 60_000, since).Should().BeFalse("not next to it yet");
        }
    }
}
