using ClassicUO.Agent;
using FluentAssertions;
using Xunit;

namespace ClassicUO.UnitTests.Agent
{
    public class AgentMessageTests
    {
        [Theory]
        [InlineData(500956u, "BandageStarted")]
        [InlineData(500969u, "BandageEnded")]
        [InlineData(500968u, "BandageEnded")]
        [InlineData(500963u, "BandageEnded")]
        [InlineData(500641u, "CastFailed")]
        [InlineData(502625u, "CastFailed")]
        [InlineData(502644u, "CastNotRecovered")]
        [InlineData(1008078u, "None")]
        public void Clilocs(uint cliloc, string expected)
        {
            AgentMessages.FromCliloc(cliloc).ToString().Should().Be(expected);
        }

        [Theory]
        [InlineData("You begin applying the bandages.", "BandageStarted")]
        [InlineData("You finish applying the bandages.", "BandageEnded")]
        [InlineData("Your concentration is disturbed, thus ruining thy spell.", "CastFailed")]
        [InlineData("You have not yet recovered from casting a spell.", "CastNotRecovered")]
        [InlineData("Welcome to Britannia", "None")]
        public void Text_is_the_fallback(string text, string expected)
        {
            AgentMessages.FromText(text).ToString().Should().Be(expected);
        }
    }

    public class AgentProfileTextTests
    {
        [Theory]
        [InlineData("Never flee.\\nLoot everything.", "Never flee.\nLoot everything.")]
        [InlineData("Don\\u0027t loot", "Don't loot")]
        [InlineData("plain", "plain")]
        public void Undoes_the_profile_loaders_backslash_doubling(string saved, string expected)
        {
            AgentController.ProfileText(saved).Should().Be(expected);
        }
    }
}
