using ClassicUO.Agent;
using FluentAssertions;
using Xunit;

namespace ClassicUO.UnitTests.Agent
{
    // cuo-k0d: "vendor buy" goes to the nearest vendor within a tile of the speaker, whatever it sells,
    // and a soak run's first three buys at the Britain healer went to the healer guildmistress.
    public class AgentErrandsTests
    {
        [Theory]
        [InlineData(true, 0, "Minerva buy")]
        [InlineData(true, 1, "Minerva buy")]
        [InlineData(true, 2, "vendor buy")]
        [InlineData(false, 0, "Minerva sell")]
        [InlineData(false, 2, "vendor sell")]
        public void A_vendor_is_asked_by_its_name_first_and_as_a_vendor_last(bool buy, int asked, string words)
        {
            AgentErrands.VendorWords(buy, "Minerva", "Minerva The Healer", asked).Should().Be(words);
        }

        [Fact]
        public void The_asks_by_name_come_before_the_one_any_vendor_answers()
        {
            AgentErrands.VendorWords(true, "Minerva", null, AgentErrands.NAMED_ASKS - 1).Should().Be("Minerva buy");
            AgentErrands.VendorWords(true, "Minerva", null, AgentErrands.NAMED_ASKS).Should().Be("vendor buy");
        }

        [Theory]
        [InlineData(null, "Minerva The Healer", "Minerva")]
        [InlineData("", "Lucy the healer", "Lucy")]
        [InlineData("  Minerva ", null, "Minerva")]
        [InlineData("Minerva the healer", null, "Minerva")] // a name learnt from a label
        [InlineData(null, null, "")]
        [InlineData("the smith", null, "the smith")]
        public void A_vendor_answers_to_its_name_without_the_title(string name, string label, string called)
        {
            AgentErrands.CalledBy(name, label).Should().Be(called);
        }

        [Fact]
        public void A_vendor_whose_name_isnt_known_is_asked_as_a_vendor()
        {
            AgentErrands.VendorWords(true, null, null, 0).Should().Be("vendor buy");
            AgentErrands.VendorWords(false, " ", "", 0).Should().Be("vendor sell");
        }

        [Fact]
        public void A_name_the_server_would_read_as_selling_isnt_used_to_buy()
        {
            // ModernUO's VendorAI looks for *sell* before *buy* in what a vendor is told by name.
            AgentErrands.VendorWords(true, "Giselle", "Giselle The Mage", 0).Should().Be("vendor buy");
            AgentErrands.VendorWords(false, "Giselle", "Giselle The Mage", 0).Should().Be("Giselle sell");
        }

        [Fact]
        public void A_tile_straight_on_hears_before_a_tile_diagonally()
        {
            // The guildmistress beside the character took "vendor buy" from the healer on the diagonal.
            AgentErrands.HearingOrder(0, -1, 0).Should().BeLessThan(AgentErrands.HearingOrder(-1, 1, 0));
            AgentErrands.HearingOrder(1, 0, 0).Should().Be(AgentErrands.HearingOrder(0, 1, 0));
            AgentErrands.HearingOrder(0, 0, 0).Should().BeLessThan(AgentErrands.HearingOrder(1, 0, 0));
            AgentErrands.HearingOrder(1, 0, 5).Should().BeLessThan(AgentErrands.HearingOrder(1, 1, 0));
        }
    }
}
