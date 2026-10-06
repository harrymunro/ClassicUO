using ClassicUO.Agent;
using FluentAssertions;
using Xunit;

namespace ClassicUO.UnitTests.Agent
{
    public class ReflexPolicyTests
    {
        private static readonly ReflexSettings Cfg = new ReflexSettings();

        private static ReflexInput Healthy() => new ReflexInput
        {
            Now = 100_000,
            HitsPercent = 100,
            Bandages = 50,
            HealPotions = 3,
            CurePotions = 3,
            Heal = AgentAuthority.Auto,
            Cure = AgentAuthority.Auto,
            Potion = AgentAuthority.Auto
        };

        [Fact]
        public void Does_nothing_when_healthy()
        {
            ReflexPolicy.Decide(Healthy(), Cfg).Action.Should().Be(ReflexAction.None);
        }

        [Fact]
        public void Bandages_below_threshold()
        {
            var s = Healthy();
            s.HitsPercent = Cfg.BandageBelowPercent - 1;

            ReflexPolicy.Decide(s, Cfg).Should().Be((ReflexAction.BandageSelf, AgentAuthority.Auto));
        }

        [Fact]
        public void Does_not_rebandage_while_bandaging()
        {
            var s = Healthy();
            s.HitsPercent = 50;
            s.Bandaging = true;

            ReflexPolicy.Decide(s, Cfg).Action.Should().Be(ReflexAction.None);
        }

        [Fact]
        public void Waits_before_retrying_a_bandage()
        {
            var s = Healthy();
            s.HitsPercent = 50;
            s.LastBandageAttempt = s.Now - 500;

            ReflexPolicy.Decide(s, Cfg).Action.Should().Be(ReflexAction.None);
        }

        [Fact]
        public void Drinks_heal_potion_when_critical_even_while_bandaging()
        {
            var s = Healthy();
            s.HitsPercent = Cfg.HealPotionBelowPercent;
            s.Bandaging = true;

            ReflexPolicy.Decide(s, Cfg).Action.Should().Be(ReflexAction.DrinkHeal);
        }

        [Fact]
        public void Respects_heal_potion_cooldown()
        {
            var s = Healthy();
            s.HitsPercent = 20;
            s.Bandaging = true;
            s.LastHealPotion = s.Now - 5_000;

            ReflexPolicy.Decide(s, Cfg).Action.Should().Be(ReflexAction.None);
        }

        [Fact]
        public void Cures_poison_once_it_hurts()
        {
            var s = Healthy();
            s.Poisoned = true;
            s.HitsPercent = Cfg.CurePotionBelowPercent;

            ReflexPolicy.Decide(s, Cfg).Action.Should().Be(ReflexAction.DrinkCure);
        }

        [Fact]
        public void Bandages_mild_poison_instead_of_drinking()
        {
            var s = Healthy();
            s.Poisoned = true;
            s.HitsPercent = 95;

            ReflexPolicy.Decide(s, Cfg).Action.Should().Be(ReflexAction.BandageSelf);
        }

        [Fact]
        public void Off_authority_disables_the_reflex()
        {
            var s = Healthy();
            s.HitsPercent = 10;
            s.Potion = AgentAuthority.Off;
            s.Heal = AgentAuthority.Off;

            ReflexPolicy.Decide(s, Cfg).Action.Should().Be(ReflexAction.None);
        }

        [Fact]
        public void Suggest_authority_is_reported()
        {
            var s = Healthy();
            s.HitsPercent = 50;
            s.Heal = AgentAuthority.Suggest;

            ReflexPolicy.Decide(s, Cfg).Should().Be((ReflexAction.BandageSelf, AgentAuthority.Suggest));
        }

        [Fact]
        public void Dead_players_do_nothing()
        {
            var s = Healthy();
            s.HitsPercent = 0;
            s.Dead = true;

            ReflexPolicy.Decide(s, Cfg).Action.Should().Be(ReflexAction.None);
        }

        [Fact]
        public void First_attempt_is_not_throttled_at_startup()
        {
            var s = Healthy();
            s.Now = 200;
            s.HitsPercent = 50;

            ReflexPolicy.Decide(s, Cfg).Action.Should().Be(ReflexAction.BandageSelf);
        }
    }

    public class AgentModeTests
    {
        [Theory]
        [InlineData("assist", "heal", "auto")]
        [InlineData("assist", "potion", "auto")]
        [InlineData("assist", "fight", "suggest")]
        [InlineData("assist", "move", "suggest")]
        [InlineData("auto", "loot", "auto")]
        [InlineData("off", "heal", "off")]
        public void Presets(string mode, string behavior, string expected)
        {
            AgentModes.TryParse(mode, out AgentMode m).Should().BeTrue();
            AgentModes.TryParse(behavior, out AgentBehavior b).Should().BeTrue();

            AgentModes.PresetAuthority(m, b).Name().Should().Be(expected);
        }

        [Theory]
        [InlineData("ASSIST", true)]
        [InlineData("auto", true)]
        [InlineData("7", false)]
        [InlineData("chaos", false)]
        public void Parses_mode_names(string text, bool ok)
        {
            AgentModes.TryParse(text, out AgentMode _).Should().Be(ok);
        }

        [Theory]
        [InlineData(0, -5, "north")]
        [InlineData(3, 3, "southeast")]
        [InlineData(-4, 0, "west")]
        [InlineData(-2, -2, "northwest")]
        [InlineData(0, 0, "here")]
        public void Compass_uses_uo_axes(int dx, int dy, string expected)
        {
            AgentSnapshot.Compass(dx, dy).Should().Be(expected);
        }
    }
}
