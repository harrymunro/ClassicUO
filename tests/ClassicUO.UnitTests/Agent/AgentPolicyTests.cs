using System.Linq;
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

    public class SpellReflexTests
    {
        private static readonly ReflexSettings Cfg = new ReflexSettings();

        // A mage: no bandages or potions, every healing spell castable.
        private static ReflexInput Mage() => new ReflexInput
        {
            Now = 100_000,
            HitsPercent = 100,
            Heal = AgentAuthority.Auto,
            Cure = AgentAuthority.Auto,
            Potion = AgentAuthority.Auto,
            CastReady = true,
            CanCastHeal = true,
            CanCastGreaterHeal = true,
            CanCastCure = true
        };

        [Fact]
        public void Heals_light_wounds_with_heal()
        {
            var s = Mage();
            s.HitsPercent = Cfg.SpellHealBelowPercent - 1;

            ReflexPolicy.Decide(s, Cfg).Should().Be((ReflexAction.CastHeal, AgentAuthority.Auto));
        }

        [Fact]
        public void Heals_bad_wounds_with_greater_heal()
        {
            var s = Mage();
            s.HitsPercent = Cfg.GreaterHealBelowPercent - 1;

            ReflexPolicy.Decide(s, Cfg).Action.Should().Be(ReflexAction.CastGreaterHeal);
        }

        [Fact]
        public void Falls_back_to_greater_heal_when_heal_cannot_be_cast()
        {
            var s = Mage();
            s.HitsPercent = Cfg.SpellHealBelowPercent - 1;
            s.CanCastHeal = false;

            ReflexPolicy.Decide(s, Cfg).Action.Should().Be(ReflexAction.CastGreaterHeal);
        }

        [Fact]
        public void Cures_before_healing_since_heals_fail_on_poison()
        {
            var s = Mage();
            s.HitsPercent = 40;
            s.Poisoned = true;

            ReflexPolicy.Decide(s, Cfg).Action.Should().Be(ReflexAction.CastCure);
        }

        [Fact]
        public void Prefers_a_cure_potion_to_the_spell()
        {
            var s = Mage();
            s.HitsPercent = 60;
            s.Poisoned = true;
            s.CurePotions = 2;

            ReflexPolicy.Decide(s, Cfg).Action.Should().Be(ReflexAction.DrinkCure);
        }

        [Fact]
        public void Waits_while_a_spell_is_being_cast()
        {
            var s = Mage();
            s.HitsPercent = 30;
            s.CastReady = false;

            ReflexPolicy.Decide(s, Cfg).Action.Should().Be(ReflexAction.None);
        }

        [Fact]
        public void Bandages_come_first_when_there_are_any()
        {
            var s = Mage();
            s.HitsPercent = 50;
            s.Bandages = 10;

            ReflexPolicy.Decide(s, Cfg).Action.Should().Be(ReflexAction.BandageSelf);
        }

        [Fact]
        public void Heal_potion_still_comes_first_when_critical()
        {
            var s = Mage();
            s.HitsPercent = Cfg.HealPotionBelowPercent;
            s.HealPotions = 1;

            ReflexPolicy.Decide(s, Cfg).Action.Should().Be(ReflexAction.DrinkHeal);
        }

        [Fact]
        public void Heal_authority_off_stops_heal_spells()
        {
            var s = Mage();
            s.HitsPercent = 30;
            s.Heal = AgentAuthority.Off;

            ReflexPolicy.Decide(s, Cfg).Action.Should().Be(ReflexAction.None);
        }
    }

    public class AgentSpellTests
    {
        [Theory]
        [InlineData("Energy Bolt", 42)]
        [InlineData("energy bolt", 42)]
        [InlineData("energybolt", 42)]
        [InlineData("42", 42)]
        [InlineData("Greater Heal", 29)]
        [InlineData("magic arrow", 5)]
        public void Finds_spells_by_name_or_id(string text, int id)
        {
            AgentSpells.Find(text).ID.Should().Be(id);
        }

        [Fact]
        public void Unknown_spells_are_null()
        {
            AgentSpells.Find("fireworks").Should().BeNull();
            AgentSpells.Find("").Should().BeNull();
        }

        [Theory]
        [InlineData(5, 1, 4, 750)]      // magic arrow
        [InlineData(29, 4, 11, 1500)]   // greater heal
        [InlineData(42, 6, 20, 2000)]   // energy bolt
        [InlineData(51, 7, 40, 2250)]   // flamestrike
        public void Costs_and_delays_follow_the_circle(int id, int circle, int mana, uint delayMs)
        {
            AgentSpells.Circle(id).Should().Be(circle);
            AgentSpells.Mana(id).Should().Be(mana);
            AgentSpells.CastDelayMs(id).Should().Be(delayMs);
        }

        [Theory]
        [InlineData("Energy Bolt", "fight")]
        [InlineData("Greater Heal", "heal")]
        [InlineData("Heal", "heal")]
        [InlineData("Cure", "cure")]
        [InlineData("Teleport", "misc")]
        public void Cast_authority_follows_the_spell(string spell, string behavior)
        {
            new AgentAction { Verb = "cast", Spell = spell }.Behavior.Name().Should().Be(behavior);
        }

        [Fact]
        public void Parses_a_brain_decision()
        {
            using var doc = System.Text.Json.JsonDocument.Parse(
                "{\"judge\": \"jev/openrouter\", \"archetype\": \"mage\", \"latency_ms\": 250.5, \"intent\": \"fight\"," +
                " \"confidence\": 0.9, \"gated\": false, \"masked\": [\"loot\"], \"intents\": {\"fight\": 0.9, \"rest\": 0.1}," +
                " \"danger\": 0.2, \"target\": {\"serial\": 4660, \"name\": \"an orc\", \"confidence\": 0.8}," +
                " \"spell\": {\"name\": \"Energy Bolt\", \"confidence\": 0.7}," +
                " \"actions\": [{\"verb\": \"cast\", \"spell\": \"Energy Bolt\", \"target\": 4660}], \"results\": [\"done\"], \"note\": \"x\"}");

            AgentDecision d = AgentDecision.FromJson(doc.RootElement);

            d.Intent.Should().Be("fight");
            d.Intents.Should().HaveCount(2);
            d.Masked.Should().ContainSingle().Which.Should().Be("loot");
            d.TargetSerial.Should().Be(4660u);
            d.SpellName.Should().Be("Energy Bolt");
            d.Actions.Should().ContainSingle().Which.Spell.Should().Be("Energy Bolt");
            d.Results.Should().Equal("done");
            d.Archetype.Should().Be("mage");
        }
    }

    public class AgentTemplateTests
    {
        [Fact]
        public void Parses_header_and_text()
        {
            AgentTemplate t = AgentTemplates.Parse
            (
                "Nuker",
                "# Nuker\r\nfor: Mage\nsummary: Opens with Explosion.\n\nOpen every fight with Explosion.\nBe cautious: rest between fights.\n",
                true
            );

            t.Name.Should().Be("nuker");
            t.Title.Should().Be("Nuker");
            t.For.Should().Be("mage");
            t.Summary.Should().Be("Opens with Explosion.");
            t.Text.Should().Be("Open every fight with Explosion.\nBe cautious: rest between fights.");
            t.User.Should().BeTrue();
            t.Suits("mage").Should().BeTrue();
            t.Suits("warrior").Should().BeFalse();
            t.Suits(string.Empty).Should().BeTrue();
        }

        [Fact]
        public void Built_in_templates_are_embedded()
        {
            var all = AgentTemplates.All();

            all.Select(t => t.Name).Should().Contain(new[] { "relentless", "survivor", "farmer", "champion", "no-loot", "nuker", "mana-saver", "flamestriker" });
            all.Should().OnlyContain(t => t.Summary.Length > 0 && t.Text.Length > 0);
            AgentTemplates.Find("Mana saver").Name.Should().Be("mana-saver");
            AgentTemplates.Find("nope").Should().BeNull();
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
