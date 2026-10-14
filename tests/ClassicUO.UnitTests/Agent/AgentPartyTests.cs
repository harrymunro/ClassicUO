// SPDX-License-Identifier: BSD-2-Clause

using System.Text.Json;
using ClassicUO.Agent;
using FluentAssertions;
using Xunit;

namespace ClassicUO.UnitTests.Agent
{
    // The game's own party (cuo-cym.3): the act as the brain sends it. What it does with the server
    // (the invitation's target cursor, the member list) is checked live, on the test server.
    public class AgentPartyTests
    {
        private static AgentAction Parse(string json)
        {
            using JsonDocument doc = JsonDocument.Parse(json);

            return AgentAction.FromJson(doc.RootElement);
        }

        [Fact]
        public void A_party_act_carries_its_kind_whom_and_what()
        {
            AgentAction invite = Parse("{\"verb\":\"party\",\"kind\":\"invite\",\"target\":\"0x1A2B\"}");
            (invite.Verb, invite.Kind, invite.Target).Should().Be(("party", "invite", 0x1A2Bu));

            AgentAction say = Parse("{\"verb\":\"party\",\"kind\":\"say\",\"text\":\"The lich is on me!\"}");
            (say.Kind, say.Text).Should().Be(("say", "The lich is on me!"));

            Parse("{\"verb\":\"party\",\"kind\":\"loot\",\"on\":true}").On.Should().BeTrue();
            Parse("{\"verb\":\"party\",\"kind\":\"accept\",\"target\":4660}").Target.Should().Be(4660u);
        }

        [Fact]
        public void A_party_act_answers_to_no_fighting_or_moving_authority()
        {
            // Making a party isn't fighting, healing or walking: it is done in any mode the brain runs in.
            new AgentAction { Verb = "party", Kind = "invite" }.Behavior.ToString().Should().Be("Misc");
            new AgentAction { Verb = "party", Kind = "say" }.Behavior.ToString().Should().Be("Misc");
        }
    }
}
