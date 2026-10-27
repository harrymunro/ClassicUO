using ClassicUO.Agent;
using ClassicUO.Game;
using FluentAssertions;
using Xunit;

namespace ClassicUO.UnitTests.Agent
{
    // Attended play (agent_attended_minutes, cuo-49t): a public shard's rules can forbid killing monsters unattended.
    public class AgentAttendedTests
    {
        [Fact]
        public void With_attended_play_the_agent_is_away_after_the_minutes_without_input_and_back_on_input()
        {
            var world = new World();
            AgentController agent = world.Agent;
            Time.Ticks = 1_000_000;
            agent.AttendedMs = 180_000;
            agent.SetMode(AgentMode.Assist);
            agent.Away.Should().BeFalse("turning a mode on counts as input");
            Time.Ticks += 180_001;
            agent.Away.Should().BeTrue();
            agent.NoteHumanInput(false);
            agent.Away.Should().BeFalse();
            Time.Ticks += 179_000;
            agent.Away.Should().BeFalse();
        }

        [Fact]
        public void Without_the_setting_or_with_the_agent_off_it_is_never_away()
        {
            var world = new World();
            AgentController agent = world.Agent;
            Time.Ticks = 1_000_000;
            agent.SetMode(AgentMode.Auto);
            Time.Ticks += 100_000_000;
            agent.Away.Should().BeFalse("agent_attended_minutes is 0 unless a profile sets it");
            agent.AttendedMs = 180_000;
            agent.SetMode(AgentMode.Off);
            Time.Ticks += 100_000_000;
            agent.Away.Should().BeFalse();
        }
    }
}
