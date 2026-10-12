using System;
using System.Collections.Generic;
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

        [Fact]
        public void Legs_are_tried_back_to_the_nearest_tile_of_the_path()
        {
            AgentNav.LegTries(10, 0).Should().Equal(10, 7, 4, 1, 0);
            AgentNav.LegTries(9, 0).Should().Equal(9, 6, 3, 0);
            AgentNav.LegTries(14, 5).Should().Equal(14, 11, 8, 5);
            // A path of one step: its only tile is tried (before, no leg was, and the walker
            // stood still until it gave up as stuck).
            AgentNav.LegTries(0, 0).Should().Equal(0);
            AgentNav.LegTries(-1, 0).Should().BeEmpty();
        }

        // ---------------------------------------------------------------- doors

        // A building: walls round it, one doorway in the south wall at (105, 204).
        private static readonly string[] Building =
        {
            "..........",
            "..#######.",
            "..#.....#.",
            "..#.....#.",
            "..###.###.",
            "..........",
        };

        [Fact]
        public void Plans_through_a_door_tile()
        {
            NavGrid g = Field(Building);
            g.MarkDoor(105, 204);
            var path = AgentNav.Plan(g, 105, 205, 0, 105, 202, 0);

            path.Should().NotBeNull();
            path.Should().Contain(p => p.X == 105 && p.Y == 204, "the doorway is the only way in");
            g.IsDoor(105, 204).Should().BeTrue();
        }

        [Fact]
        public void A_door_costs_a_few_steps_so_a_short_way_round_wins()
        {
            // Two ways through a wall, equally far: a door, and an open gap.
            NavGrid g = Field(
                "..........",
                "..........",
                "####.#.###",
                "..........",
                "..........");
            g.MarkDoor(104, 202);
            var path = AgentNav.Plan(g, 105, 200, 0, 105, 204, 0);

            path.Should().NotContain(p => p.X == 104 && p.Y == 202);
            path.Should().Contain(p => p.X == 106 && p.Y == 202);
        }

        [Fact]
        public void A_locked_door_is_blocked_and_planned_round()
        {
            var memory = new TravelMemory();
            memory.SeeDoor(0, 0x4000_0001, 105, 204);
            memory.Lock(0x4000_0001);
            NavGrid g = Field(Building);
            memory.Apply(g, 0, 105, 205, 105, 202);

            AgentNav.Plan(g, 105, 205, 0, 105, 202, 0).Should().BeNull("the only door is locked");

            // Seen somewhere else later, it has been opened: no longer locked.
            memory.SeeDoor(0, 0x4000_0001, 104, 205);
            memory.IsLocked(0x4000_0001).Should().BeFalse();
        }

        [Fact]
        public void Doors_are_remembered_and_marked_in_any_grid_that_holds_them()
        {
            var memory = new TravelMemory();
            memory.SeeDoor(0, 7, 105, 204);
            memory.SeeDoor(1, 8, 105, 204); // another map

            NavGrid g = Field(Building);
            memory.Apply(g, 0, 105, 205, 105, 202);
            g.IsDoor(105, 204).Should().BeTrue();
            memory.Doors.Should().Be(2);

            NavGrid other = Field(Building);
            memory.Apply(other, 2, 105, 205, 105, 202);
            other.IsDoor(105, 204).Should().BeFalse();
        }

        // ---------------------------------------------------------------- houses

        private static (int, int)[] Square(int x0, int y0, int x1, int y1) =>
            (from x in Enumerable.Range(x0, x1 - x0 + 1) from y in Enumerable.Range(y0, y1 - y0 + 1) select (x, y)).ToArray();

        [Fact]
        public void A_house_is_blocked_and_planned_round()
        {
            // Open ground the map files show; a house stands in the middle of the straight line.
            NavGrid g = Field("....................", "....................", "....................", "....................",
                "....................", "....................", "....................");
            var memory = new TravelMemory();
            memory.SeeHouse(0, 0x4000_0100, 106, 201, 112, 205, Square(106, 201, 112, 205), 35);
            memory.Apply(g, 0, 100, 203, 119, 203);
            var path = AgentNav.Plan(g, 100, 203, 0, 119, 203, 0);

            path.Should().NotBeNull();
            path.Should().NotContain(p => p.X >= 106 && p.X <= 112 && p.Y >= 201 && p.Y <= 205);
            memory.InHouse(0, 109, 203).Should().BeTrue();
        }

        [Fact]
        public void A_house_the_goal_or_the_start_is_in_stays_open()
        {
            var memory = new TravelMemory();
            memory.SeeHouse(0, 0x4000_0100, 106, 201, 112, 205, Square(106, 201, 112, 205), 35);

            NavGrid toHouse = Field("....................", "....................", "....................", "....................",
                "....................", "....................", "....................");
            memory.Apply(toHouse, 0, 100, 203, 109, 203);
            AgentNav.Plan(toHouse, 100, 203, 0, 109, 203, 0).Should().NotBeNull();

            NavGrid fromHouse = Field("....................", "....................", "....................", "....................",
                "....................", "....................", "....................");
            memory.Apply(fromHouse, 0, 109, 203, 119, 203);
            AgentNav.Plan(fromHouse, 109, 203, 0, 119, 203, 0).Should().NotBeNull();
        }

        [Fact]
        public void A_trip_into_a_house_goes_through_its_door_not_its_walls()
        {
            // The map files show open ground; the house's walls run round 106..112 x 201..205,
            // with a door at (109, 205).
            var walls = Square(106, 201, 112, 205).Where(t => (t.Item1 == 106 || t.Item1 == 112 || t.Item2 == 201 || t.Item2 == 205)
                && t != (109, 205)).ToArray();
            var memory = new TravelMemory();
            memory.SeeHouse(0, 0x4000_0100, 106, 201, 112, 205, Square(106, 201, 112, 205), 35, walls);
            memory.SeeDoor(0, 0x4000_0101, 109, 205);
            NavGrid g = Field("....................", "....................", "....................", "....................",
                "....................", "....................", "....................", "....................");
            memory.Apply(g, 0, 100, 203, 109, 203);
            var path = AgentNav.Plan(g, 100, 203, 0, 109, 203, 0);

            path.Should().NotBeNull();
            path.Should().Contain(p => p.X == 109 && p.Y == 205, "the door is the way in");
            path.Should().NotContain(p => walls.Any(w => w.Item1 == p.X && w.Item2 == p.Y));

            // Locked, the house can't be walked into.
            memory.Lock(0x4000_0101);
            NavGrid locked = Field("....................", "....................", "....................", "....................",
                "....................", "....................", "....................", "....................");
            memory.Apply(locked, 0, 100, 203, 109, 203);
            AgentNav.Plan(locked, 100, 203, 0, 109, 203, 0).Should().BeNull();
        }

        [Fact]
        public void A_house_seen_again_with_other_parts_is_remembered_afresh()
        {
            var memory = new TravelMemory();
            memory.SeeHouse(0, 9, 106, 201, 108, 203, Square(106, 201, 108, 203), 9);

            memory.KnowsHouse(9, 9).Should().BeTrue();
            memory.KnowsHouse(9, 12).Should().BeFalse("a customised house changes its parts");
            memory.HousesNear(0, 100, 200, 6).Should().Equal(9u);
            memory.HousesNear(0, 90, 200, 6).Should().BeEmpty();

            memory.ForgetHouse(9);
            memory.Houses.Should().Be(0);
        }

        // ---------------------------------------------------------------- long trips

        // A long field with a river across it that has one ford, and a loader that cuts the grid
        // for a box out of it, as AgentNav.Load cuts one out of the map. The boxes asked for are
        // kept to check their size.
        private static (Func<int, int, int, int, NavGrid> Load, List<(int W, int H)> Boxes) LongField(int width, int height, int riverX, int fordY)
        {
            var boxes = new List<(int, int)>();

            NavGrid Load(int x0, int y0, int w, int h)
            {
                boxes.Add((w, h));
                var g = new NavGrid(x0, y0, w, h);

                for (int x = Math.Max(x0, 0); x < Math.Min(x0 + w, width); x++)
                {
                    for (int y = Math.Max(y0, 0); y < Math.Min(y0 + h, height); y++)
                    {
                        if (x != riverX || y == fordY)
                        {
                            g.AddSurface(x, y, 0);
                        }
                    }
                }

                return g;
            }

            return (Load, boxes);
        }

        [Fact]
        public void A_long_trip_goes_in_hops_no_box_over_the_limit()
        {
            (var load, var boxes) = LongField(1200, 120, 700, 110);
            var limits = new TripLimits { MaxBox = 200, Direct = 120, Hop = 100, Pocket = 20 };
            int x = 10, y = 20, hops = 0;

            while (true)
            {
                TripPlan plan = AgentNav.PlanTrip(load, x, y, 0, 1150, 30, 2, limits);
                plan.Path.Should().NotBeNull($"hop {hops} from {x},{y}: {plan.Why}");
                (x, y, _) = plan.Path[^1];

                if (!plan.Hop)
                {
                    break;
                }

                hops++;
                hops.Should().BeLessThan(30);
            }

            AgentNav.Tiles(x, y, 1150, 30).Should().BeLessOrEqualTo(2);
            hops.Should().BeGreaterThan(5);
            boxes.Should().OnlyContain(b => b.W <= 200 && b.H <= 200);
        }

        [Fact]
        public void A_trip_that_fits_one_box_is_planned_whole()
        {
            (var load, _) = LongField(300, 60, 1000, 0);
            TripPlan plan = AgentNav.PlanTrip(load, 10, 30, 0, 110, 30, 0, new TripLimits { Direct = 120 });

            plan.Hop.Should().BeFalse();
            plan.Path.Should().NotBeNull();
            plan.Path[^1].Should().Be((110, 30, (sbyte) 0));
        }

        [Fact]
        public void A_hop_point_lies_on_the_line_to_the_goal()
        {
            AgentNav.HopToward(0, 0, 1000, 500, 300).Should().Be((300, 150));
            AgentNav.HopToward(1000, 0, 0, 0, 300).Should().Be((700, 0));
            AgentNav.HopToward(0, 0, 100, 20, 300).Should().Be((100, 20));
        }

        [Fact]
        public void A_box_is_clamped_to_the_largest_side()
        {
            AgentNav.Box(0, 0, 100, 0, 50, 900).Should().Be((-50, -50, 201, 101));
            (int _, int _, int w, int h) = AgentNav.Box(0, 0, 800, 300, 400, 900);
            w.Should().BeLessOrEqualTo(900);
            h.Should().BeLessOrEqualTo(900);
        }

        [Fact]
        public void The_dungeons_east_of_5120_are_apart_from_the_land()
        {
            AgentNav.SameLandmass(0, 1425, 6052).Should().BeFalse();
            AgentNav.SameLandmass(1, 5200, 5900).Should().BeTrue();
            AgentNav.SameLandmass(2, 100, 2000).Should().BeTrue("Ilshenar has no such line");
        }

        // ---------------------------------------------------------------- unreachable goals

        [Fact]
        public void A_goal_in_a_walled_yard_is_ruled_out_without_a_long_search()
        {
            // The building with no doorway.
            NavGrid g = Field(
                "..............",
                "..#######.....",
                "..#.....#.....",
                "..#.....#.....",
                "..#######.....",
                "..............");

            AgentNav.Unreachable(g, 112, 205, 105, 202, 0).Should().BeTrue();
            AgentNav.Unreachable(g, 112, 205, 112, 200, 0).Should().BeFalse("open ground");

            // With a doorway the files show, the yard can be walked into.
            g.AddSurface(105, 204, 0);
            AgentNav.Unreachable(g, 112, 205, 105, 202, 0).Should().BeFalse();
        }

        [Fact]
        public void Nowhere_to_stand_at_the_goal_is_ruled_out()
        {
            NavGrid g = Field("..........", "..........", "....##....", "....##....", "..........");

            AgentNav.Unreachable(g, 100, 200, 104, 202, 0).Should().BeTrue();
            AgentNav.Unreachable(g, 100, 200, 104, 202, 1).Should().BeFalse("ground within a tile of it");
        }

        [Fact]
        public void A_pocket_that_reaches_the_grid_edge_is_not_ruled_out()
        {
            // The yard's wall stops at the edge of the grid: what lies beyond is unknown.
            NavGrid g = Field(
                "..#.....",
                "..#.....",
                "..#####.",
                "........");

            AgentNav.Unreachable(g, 107, 203, 104, 200, 0).Should().BeFalse();
        }

        [Fact]
        public void A_plan_keeps_to_its_node_budget()
        {
            // A goal behind a ring too wide for the pocket check: the search runs out of budget
            // and gives up rather than search every box it could widen to.
            (var load, _) = LongField(700, 700, 10_000, 0);

            NavGrid Ringed(int x0, int y0, int w, int h)
            {
                NavGrid g = load(x0, y0, w, h);

                for (int i = 200; i <= 500; i++)
                {
                    g.Block(i, 200);
                    g.Block(i, 500);
                    g.Block(200, i);
                    g.Block(500, i);
                }

                return g;
            }

            var limits = new TripLimits { MaxNodes = 50_000, Pocket = 40 };
            TripPlan plan = AgentNav.PlanTrip(Ringed, 100, 350, 0, 350, 350, 0, limits);

            plan.Path.Should().BeNull();
            plan.Stats.Expanded.Should().BeLessOrEqualTo(limits.MaxNodes + 1);
            plan.Stats.HitLimit.Should().BeTrue();
        }

        [Fact]
        public void An_unreachable_goal_fails_fast_as_no_route()
        {
            (var load, var boxes) = LongField(400, 100, 1000, 0);

            NavGrid Walled(int x0, int y0, int w, int h)
            {
                NavGrid g = load(x0, y0, w, h);

                // A walled square round (300, 50).
                for (int i = 290; i <= 310; i++)
                {
                    g.Block(i, 40);
                    g.Block(i, 60);
                    g.Block(290, i - 250);
                    g.Block(310, i - 250);
                }

                return g;
            }

            TripPlan plan = AgentNav.PlanTrip(Walled, 20, 50, 0, 300, 50, 1, new TripLimits { Pocket = 30 });

            plan.Path.Should().BeNull();
            plan.Failure.Should().Be("no route");
            plan.Why.Should().Contain("closed off");
            plan.Stats.Expanded.Should().Be(0, "the pocket check settled it before any search");
        }

        [Fact]
        public void A_way_round_that_leads_away_from_the_goal_still_counts_as_progress()
        {
            var progress = new TravelProgress();
            progress.Restart(160);

            progress.Note(160, 0).Should().BeFalse("standing at the start");
            progress.Note(162, 5).Should().BeTrue("further along the path, though further from the goal");
            progress.Note(170, 20).Should().BeTrue();
            progress.Note(170, 20).Should().BeFalse("standing still");
            progress.Note(150, 20).Should().BeTrue("nearer the goal than ever");
            progress.Note(155, 18).Should().BeFalse("neither nearer nor further along");

            progress.Restart(150);
            progress.Note(150, 3).Should().BeTrue("a new plan counts its path from the start");
        }
    }
}
