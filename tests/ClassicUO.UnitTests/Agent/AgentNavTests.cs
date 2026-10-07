using System.Linq;
using ClassicUO.Agent;
using FluentAssertions;
using Xunit;

namespace ClassicUO.UnitTests.Agent
{
    public class AgentNavTests
    {
        // A flat field at z 0, with '#' for walls and digits for raised ground (z = digit * 5).
        private static NavGrid Field(params string[] rows)
        {
            var g = new NavGrid(100, 200, rows[0].Length, rows.Length);

            for (int y = 0; y < rows.Length; y++)
            {
                for (int x = 0; x < rows[y].Length; x++)
                {
                    char c = rows[y][x];

                    if (c != '#')
                    {
                        g.AddSurface(100 + x, 200 + y, char.IsDigit(c) ? (c - '0') * 5 : 0);
                    }
                }
            }

            return g;
        }

        [Fact]
        public void Walks_straight_across_open_ground()
        {
            NavGrid g = Field("..........", "..........", "..........");
            var path = AgentNav.Plan(g, 100, 201, 0, 109, 201, 0);

            path.Should().NotBeNull();
            path.Should().HaveCount(9);
            path.Last().Should().Be((109, 201, (sbyte) 0));
        }

        [Fact]
        public void Goes_round_a_wall_through_its_gap()
        {
            NavGrid g = Field(
                "....#.....",
                "....#.....",
                "....#.....",
                "..........",
                "....#.....");
            var path = AgentNav.Plan(g, 100, 200, 0, 109, 200, 0);

            path.Should().NotBeNull();
            path.Should().Contain(p => p.X == 104 && p.Y == 203);
            path.Should().NotContain(p => p.X == 104 && p.Y != 203);
        }

        [Fact]
        public void Never_cuts_a_corner_diagonally()
        {
            NavGrid g = Field(
                ".#...",
                "#....",
                ".....");
            // From the top-left corner the only way out is blocked on both sides.
            AgentNav.Plan(g, 100, 200, 0, 104, 202, 0).Should().BeNull();
        }

        [Fact]
        public void Climbs_gentle_steps_but_not_cliffs()
        {
            NavGrid gentle = Field("0123456", "0123456");
            AgentNav.Plan(gentle, 100, 200, 0, 106, 200, 0).Should().NotBeNull();

            NavGrid cliff = Field("00999", "00999");
            AgentNav.Plan(cliff, 100, 200, 0, 104, 200, 0).Should().BeNull();
        }

        [Fact]
        public void Stops_within_the_distance_asked()
        {
            NavGrid g = Field("..........");
            var path = AgentNav.Plan(g, 100, 200, 0, 109, 200, 2);

            path.Last().X.Should().Be(107);
        }

        [Fact]
        public void A_blocked_tile_is_planned_around()
        {
            NavGrid g = Field("....", "....", "....");
            g.Block(101, 200);
            g.Block(101, 201);
            var path = AgentNav.Plan(g, 100, 200, 0, 103, 200, 0);

            path.Should().NotBeNull();
            path.Should().NotContain(p => p.X == 101 && p.Y < 202);
        }

        [Fact]
        public void Next_leg_is_the_furthest_point_in_reach()
        {
            var path = Enumerable.Range(1, 40).Select(i => (100 + i, 200, (sbyte) 0)).ToList();

            AgentNav.NextLeg(path, 0, 100, 200, 16).Should().Be(15);
            AgentNav.NextLeg(path, 20, 120, 200, 16).Should().Be(35);
        }
    }
}
