using System.Linq;
using ClassicUO.Agent;
using ClassicUO.Game.Data;
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
        public void A_paladin_heals_with_close_wounds_when_badly_hurt_even_with_bandages()
        {
            var s = Healthy();
            s.CastReady = true;
            s.CanCastCloseWounds = true;
            s.Bandaging = true;
            s.HitsPercent = Cfg.GreaterHealBelowPercent - 1;
            ReflexPolicy.Decide(s, Cfg).Should().Be((ReflexAction.CastCloseWounds, AgentAuthority.Auto));
            s.HitsPercent = Cfg.GreaterHealBelowPercent + 5;
            ReflexPolicy.Decide(s, Cfg).Action.Should().Be(ReflexAction.None);
        }

        [Fact]
        public void A_paladin_cures_with_cleanse_by_fire_without_a_potion()
        {
            var s = Healthy();
            s.CastReady = true;
            s.CanCastCleanse = true;
            s.Poisoned = true;
            s.CurePotions = 0;
            s.Bandages = 0;
            ReflexPolicy.Decide(s, Cfg).Should().Be((ReflexAction.CastCleanse, AgentAuthority.Auto));
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

        [Theory]
        [InlineData("Pain Spike", 109, "Necromancy", 5, 0, 1000u)]
        [InlineData("poison strike", 110, "Necromancy", 17, 0, 1750u)]
        [InlineData("Strangle", 111, "Necromancy", 29, 0, 2000u)]
        [InlineData("Consecrate Weapon", 203, "Chivalry", 10, 10, 500u)]
        [InlineData("Close Wounds", 202, "Chivalry", 10, 10, 1500u)]
        [InlineData("202", 202, "Chivalry", 10, 10, 1500u)]
        public void Necromancy_and_chivalry_have_their_own_costs_and_delays(string text, int id, string school, int mana,
            int tithing, uint delayMs)
        {
            SpellDefinition s = AgentSpells.Find(text);
            s.ID.Should().Be(id);
            AgentSpells.SchoolOf(id).ToString().Should().Be(school);
            AgentSpells.Mana(id).Should().Be(mana);
            AgentSpells.Tithing(id).Should().Be(tithing);
            AgentSpells.CastDelayMs(id).Should().Be(delayMs);
        }

        [Theory]
        [InlineData(33, true)]
        [InlineData(58, true)]
        [InlineData(62, true)]
        [InlineData(114, true)]
        [InlineData(59, false)]  // resurrection
        [InlineData(24, false)]  // wall of stone
        public void Summons_are_known(int id, bool summon)
        {
            AgentSpells.IsSummon(id).Should().Be(summon);
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
        [InlineData(33, 5, 14, 4500)]   // blade spirits: ModernUO casts it three times as slowly
        [InlineData(40, 5, 14, 7500)]   // summon creature: five times
        [InlineData(58, 8, 50, 2500)]   // energy vortex: as its circle
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
        [InlineData("Close Wounds", "heal")]
        [InlineData("Cleanse by Fire", "cure")]
        [InlineData("Consecrate Weapon", "fight")]
        [InlineData("Divine Fury", "fight")]
        [InlineData("Pain Spike", "fight")]
        [InlineData("Wither", "fight")]
        [InlineData("Sacred Journey", "misc")]
        [InlineData("Wall of Stone", "fight")]
        [InlineData("Blade Spirits", "fight")]
        [InlineData("Earth Elemental", "fight")]
        [InlineData("Bless", "fight")]
        [InlineData("Recall", "misc")]
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

        [Fact]
        public void Parses_the_calls_view_of_a_decision_and_of_other_calls()
        {
            using var doc = System.Text.Json.JsonDocument.Parse(
                "{\"intent\": \"fight\", \"questions\": [" +
                "{\"q\": \"target\", \"title\": \"which creature?\", \"kind\": \"choice\", \"picked\": \"t2\", \"used\": \"t1\"," +
                " \"confidence\": 0.6, \"options\": [{\"id\": \"t2\", \"label\": \"an orc captain\", \"p\": 0.6}," +
                " {\"id\": \"t1\", \"label\": \"an orc\", \"p\": 0.3}]}," +
                "{\"q\": \"leave_now\", \"title\": \"leave now?\", \"kind\": \"yesno\", \"p\": 0.44, \"cut\": 0.425, \"verdict\": \"yes\"}]}");
            AgentDecision d = AgentDecision.FromJson(doc.RootElement);
            d.Questions.Should().HaveCount(2);
            d.Questions[0].LabelOf(d.Questions[0].Used).Should().Be("an orc");
            d.Questions[1].Verdict.Should().Be("yes");
            d.Questions[1].Cut.Should().BeApproximately(0.425f, 0.001f);

            using var did = System.Text.Json.JsonDocument.Parse(
                "{\"did\": [{\"what\": \"run 15 tiles north-west from the pack\", \"result\": \"\"}]}");
            AgentDecision.FromJson(did.RootElement).Did.Should().ContainSingle()
                .Which.What.Should().Be("run 15 tiles north-west from the pack");

            using var call = System.Text.Json.JsonDocument.Parse(
                "{\"kind\": \"facts\", \"title\": \"which facts matter here?\", \"latency_ms\": 300, \"questions\": [" +
                "{\"q\": \"facts\", \"kind\": \"many\", \"options\": [{\"label\": \"Wisps never attack first\", \"p\": 0.74, \"kept\": true}]}]}");
            AgentCall c = AgentCall.FromJson(call.RootElement);
            c.Kind.Should().Be("facts");
            c.Questions[0].Options[0].Kept.Should().BeTrue();
        }

        [Fact]
        public void Parses_what_a_call_cost_and_the_running_total()
        {
            using var doc = System.Text.Json.JsonDocument.Parse(
                "{\"intent\": \"fight\", \"cost\": 0.0001234, \"spent\": {\"total\": 0.0213, \"per_hour\": 0.41," +
                " \"calls\": 57, \"by_kind\": {\"planner\": 0.02, \"fight\": 0.0013}, \"budget_per_hour\": 0.5," +
                " \"over_budget\": false}}");
            AgentDecision d = AgentDecision.FromJson(doc.RootElement);

            d.Cost.Should().BeApproximately(0.0001234f, 1e-7f);
            d.Spent.Total.Should().BeApproximately(0.0213f, 1e-6f);
            d.Spent.Calls.Should().Be(57);
            d.Spent.ByKind.Should().HaveCount(2);
            d.Spent.ByKind[0].Kind.Should().Be("planner");
            d.Spent.Budget.Should().BeApproximately(0.5f, 1e-6f);
            d.Spent.OverBudget.Should().BeFalse();

            using var call = System.Text.Json.JsonDocument.Parse("{\"kind\": \"routine\", \"cost\": 0.00004, \"spent\": {\"total\": 0.03}}");
            AgentCall c = AgentCall.FromJson(call.RootElement);
            c.Cost.Should().BeApproximately(0.00004f, 1e-8f);
            c.Spent.Budget.Should().Be(-1);
        }

        [Fact]
        public void Parses_the_plan_a_decision_follows()
        {
            using var doc = System.Text.Json.JsonDocument.Parse(
                "{\"intent\": \"fight\", \"machine\": {\"name\": \"mage against a crowd\", \"state\": \"blast\"," +
                " \"says\": \"Cast area spells at the bunch.\", \"states\": [\"open\", \"blast\", \"finish\"]," +
                " \"last\": \"open -> blast (0.71)\", \"since_s\": 3.5}}");

            AgentDecision d = AgentDecision.FromJson(doc.RootElement);

            d.PlanName.Should().Be("mage against a crowd");
            d.PlanStep.Should().Be("blast");
            d.PlanSteps.Should().Equal("open", "blast", "finish");
            d.PlanLast.Should().Be("open -> blast (0.71)");
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
        [InlineData("assist", "fight", "auto")]
        [InlineData("assist", "move", "off")]
        [InlineData("assist", "loot", "suggest")]
        [InlineData("combat", "fight", "auto")]
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
        [InlineData("combat", true)]
        [InlineData("7", false)]
        [InlineData("1", false)]
        [InlineData("chaos", false)]
        public void Parses_mode_names(string text, bool ok)
        {
            AgentModes.TryParse(text, out AgentMode _).Should().Be(ok);
        }

        [Fact]
        public void Combat_is_the_assist_mode_and_keeps_its_saved_name()
        {
            AgentModes.TryParse("combat", out AgentMode m).Should().BeTrue();
            m.Should().Be(AgentMode.Assist);
            m.Name().Should().Be("assist");
            m.Title().Should().Be("combat assist");
        }

        [Theory]
        [InlineData("follow", "Follow")]
        [InlineData("Defend", "Defend")]
        [InlineData("nearby", "Nearby")]
        public void Parses_engage_settings(string text, string expected)
        {
            AgentModes.TryParse(text, out AgentEngage e).Should().BeTrue();
            e.ToString().Should().Be(expected);
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
