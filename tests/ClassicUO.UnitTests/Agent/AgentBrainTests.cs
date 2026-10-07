using System.IO;
using ClassicUO.Agent;
using FluentAssertions;
using Xunit;

namespace ClassicUO.UnitTests.Agent
{
    public class AgentBrainTests
    {
        [Fact]
        public void Writes_the_key_keeping_other_lines_and_only_for_the_user()
        {
            string dir = Directory.CreateTempSubdirectory().FullName;
            string env = Path.Combine(dir, ".env");
            File.WriteAllLines(env, new[] { "JEV_MODEL=x", "OPENROUTER_API_KEY=old" });

            AgentBrain.WriteKey(env, "sk-or-v1-new");

            File.ReadAllLines(env).Should().Equal("JEV_MODEL=x", "OPENROUTER_API_KEY=sk-or-v1-new");

            if (!System.OperatingSystem.IsWindows())
            {
                File.GetUnixFileMode(env).Should().Be(UnixFileMode.UserRead | UnixFileMode.UserWrite);
            }
        }

        [Theory]
        [InlineData("")]
        [InlineData("short")]
        [InlineData("two words that are long enough together")]
        public void A_clipboard_without_a_key_is_not_saved(string clip)
        {
            AgentBrain.SaveKey(clip).Should().StartWith("the clipboard doesn't hold a key");
        }
    }
}
