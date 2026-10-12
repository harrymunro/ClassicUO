using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using ClassicUO.Agent;
using FluentAssertions;
using Xunit;

namespace ClassicUO.UnitTests.Agent
{
    public class AgentFollowTests
    {
        private const uint LONG_AGO = uint.MaxValue;

        [Fact]
        public void A_trip_asked_to_run_always_runs()
        {
            AgentPace.Runs(false, false, false, LONG_AGO).Should().BeTrue();
        }

        [Fact]
        public void A_trip_at_a_walk_walks_while_all_is_quiet()
        {
            AgentPace.Runs(true, false, false, LONG_AGO).Should().BeFalse();
        }

        [Theory]
        [InlineData(true, false)]  // engaged, fleeing or stepping back
        [InlineData(false, true)]  // something hostile near, or attacking
        public void A_trip_at_a_walk_runs_in_danger(bool fighting, bool near)
        {
            AgentPace.Runs(true, fighting, near, 0).Should().BeTrue();
        }

        [Fact]
        public void A_trip_at_a_walk_goes_back_to_a_walk_three_seconds_after_the_danger_has_gone()
        {
            AgentPace.Runs(true, false, false, 500).Should().BeTrue();
            AgentPace.Runs(true, false, false, AgentPace.CALM_MS - 1).Should().BeTrue();
            AgentPace.Runs(true, false, false, AgentPace.CALM_MS).Should().BeFalse();
        }

        [Theory]
        [InlineData(0, false)]
        [InlineData(2, false)]
        [InlineData(3, true)]
        [InlineData(9, true)]
        public void A_follower_moves_only_when_further_than_asked(int tiles, bool move)
        {
            AgentPace.Follow(tiles, 2, true, false, false).Move.Should().Be(move);
        }

        [Fact]
        public void A_follower_at_a_walk_walks_while_no_more_than_four_tiles_behind()
        {
            AgentPace.Follow(3, 2, true, false, false).Should().Be((true, false, false));
            AgentPace.Follow(AgentPace.CATCH_UP, 2, true, false, false).Should().Be((true, false, false));
        }

        [Fact]
        public void A_follower_further_behind_runs_until_it_is_a_step_from_its_place()
        {
            AgentPace.Follow(AgentPace.CATCH_UP + 1, 2, true, false, false).Should().Be((true, true, true));
            AgentPace.Follow(10, 2, true, false, false).Should().Be((true, true, true));

            // Still catching up at four tiles, where it would walk had it not fallen behind.
            AgentPace.Follow(4, 2, true, false, true).Should().Be((true, true, true));
            AgentPace.Follow(3, 2, true, false, true).Should().Be((true, false, false));
            AgentPace.Follow(2, 2, true, false, true).Should().Be((false, false, false));
        }

        [Fact]
        public void A_follower_at_a_walk_runs_in_danger()
        {
            AgentPace.Follow(3, 2, true, true, false).Should().Be((true, true, false));
        }

        [Fact]
        public void A_follower_asked_to_run_runs()
        {
            AgentPace.Follow(3, 2, false, false, false).Should().Be((true, true, false));
            AgentPace.Follow(1, 2, false, false, false).Move.Should().BeFalse();
        }

        [Fact]
        public void A_follower_told_to_keep_far_back_does_not_catch_up_from_inside_its_distance()
        {
            AgentPace.Follow(7, 8, true, false, false).Should().Be((false, false, false));
            AgentPace.Follow(10, 8, true, false, false).Should().Be((true, false, false));
            AgentPace.Follow(11, 8, true, false, false).Should().Be((true, true, true));
        }

        // Every tile of the square within n of (tx, ty), all at z 0.
        private static List<(int X, int Y, sbyte Z)> Around(int tx, int ty, int n)
        {
            var tiles = new List<(int, int, sbyte)>();

            for (int x = tx - n; x <= tx + n; x++)
            {
                for (int y = ty - n; y <= ty + n; y++)
                {
                    tiles.Add((x, y, 0));
                }
            }

            return tiles;
        }

        [Fact]
        public void A_follower_stands_on_the_nearest_tile_within_reach_of_its_target()
        {
            // The target at 110,100 and the follower ten tiles west of it: two tiles short, on the line.
            var spots = AgentPace.Spots(Around(110, 100, 2), 100, 100, 110, 100, 2, new HashSet<(int, int)>(), 7);

            spots[0].Should().Be((108, 100, (sbyte) 0));
            spots.Should().HaveCount(24).And.NotContain((110, 100, (sbyte) 0));
        }

        [Fact]
        public void A_taken_tile_is_left_to_whoever_stands_on_it()
        {
            var taken = new HashSet<(int, int)> { (110, 100), (108, 100) };
            var spots = AgentPace.Spots(Around(110, 100, 2), 100, 100, 110, 100, 2, taken, 7);

            spots.Should().NotContain((108, 100, (sbyte) 0));
            new[] { (108, 99, (sbyte) 0), (108, 101, (sbyte) 0) }.Should().Contain(spots[0]); // beside it
            spots.Should().HaveCount(23);
        }

        [Fact]
        public void A_follower_on_someone_elses_tile_steps_to_a_free_one_beside_it()
        {
            // Both stand at 108,100, two west of the target.
            var taken = new HashSet<(int, int)> { (110, 100), (108, 100) };
            var spots = AgentPace.Spots(Around(110, 100, 2), 108, 100, 110, 100, 2, taken, 7);

            AgentNav.Tiles(spots[0].X, spots[0].Y, 108, 100).Should().Be(1);
            (spots[0].X == 108 || spots[0].Y == 100).Should().BeTrue("a step straight to the side comes before a diagonal one");
            AgentNav.Tiles(spots[0].X, spots[0].Y, 110, 100).Should().BeLessThanOrEqualTo(2);
        }

        [Fact]
        public void Two_followers_on_one_tile_do_not_all_step_to_the_same_one()
        {
            var taken = new HashSet<(int, int)> { (110, 100), (108, 100) };
            var first = Enumerable.Range(1, 16)
                .Select(serial => AgentPace.Spots(Around(110, 100, 2), 108, 100, 110, 100, 2, taken, serial)[0])
                .Distinct()
                .ToList();

            first.Count.Should().BeGreaterThan(1);
        }

        [Fact]
        public void A_follower_keeps_to_its_side_as_the_target_moves()
        {
            // The tile behind the target is taken each time; the follower's pick of the two beside
            // it is the same side at every step east.
            var sides = new HashSet<int>();

            for (int step = 0; step < 6; step++)
            {
                int tx = 110 + step;
                var taken = new HashSet<(int, int)> { (tx, 100), (tx - 2, 100) };
                (int x, int y, sbyte _) = AgentPace.Spots(Around(tx, 100, 2), tx - 3, 100, tx, 100, 2, taken, 7)[0];
                x.Should().Be(tx - 2);
                sides.Add(y - 100);
            }

            sides.Should().HaveCount(1);
        }

        [Fact]
        public void Only_tiles_one_can_stand_on_are_spots()
        {
            var standable = new List<(int X, int Y, sbyte Z)> { (109, 100, 5), (112, 100, 0), (120, 100, 0) };
            var spots = AgentPace.Spots(standable, 100, 100, 110, 100, 2, new HashSet<(int, int)>(), 7);

            spots.Should().Equal((109, 100, (sbyte) 5), (112, 100, (sbyte) 0)); // nearest first; 120,100 is too far from the target
        }

        private static AgentAction Parse(string json)
        {
            using JsonDocument doc = JsonDocument.Parse(json);

            return AgentAction.FromJson(doc.RootElement);
        }

        [Fact]
        public void A_trip_runs_unless_a_walk_is_asked()
        {
            Parse("{\"verb\":\"travel\",\"x\":1460,\"y\":1690}").WalkPace(false).Should().BeFalse();
            Parse("{\"verb\":\"travel\",\"x\":1460,\"y\":1690,\"pace\":\"run\"}").WalkPace(false).Should().BeFalse();
            Parse("{\"verb\":\"travel\",\"x\":1460,\"y\":1690,\"pace\":\"walk\"}").WalkPace(false).Should().BeTrue();
        }

        [Fact]
        public void A_follow_walks_unless_a_run_is_asked_and_keeps_within_two_unless_told()
        {
            AgentAction a = Parse("{\"verb\":\"follow\",\"target\":\"0x1A2B\"}");
            (a.Target, a.HasDistance, a.WalkPace(true), a.BadPace).Should().Be((0x1A2Bu, false, true, false));
            a.Behavior.ToString().Should().Be("Move");

            AgentAction b = Parse("{\"verb\":\"follow\",\"target\":6699,\"distance\":3,\"pace\":\"run\"}");
            (b.Target, b.HasDistance, b.Distance, b.WalkPace(true)).Should().Be((6699u, true, 3, false));
        }

        [Fact]
        public void A_pace_that_is_neither_is_refused()
        {
            Parse("{\"verb\":\"travel\",\"pace\":\"trot\"}").BadPace.Should().BeTrue();
        }

        [Theory]
        [InlineData("{\"verb\":\"walk_to\",\"x\":1,\"y\":2}", true)]
        [InlineData("{\"verb\":\"walk_to\",\"x\":1,\"y\":2,\"run\":true}", true)]
        [InlineData("{\"verb\":\"walk_to\",\"x\":1,\"y\":2,\"run\":false}", false)]
        [InlineData("{\"verb\":\"walk_to\",\"x\":1,\"y\":2,\"run\":\"false\"}", false)] // as a command line may send it
        [InlineData("{\"verb\":\"walk_to\",\"x\":1,\"y\":2,\"run\":0}", false)]
        [InlineData("{\"verb\":\"walk_to\",\"x\":1,\"y\":2,\"run\":\"yes\"}", true)]
        public void Walk_to_runs_unless_told_not_to(string json, bool run)
        {
            Parse(json).Run.Should().Be(run);
        }

        [Fact]
        public void Say_carries_its_kind()
        {
            AgentAction a = Parse("{\"verb\":\"say\",\"text\":\"has a swig of ale\",\"kind\":\"emote\"}");
            (a.Kind, a.Text).Should().Be(("emote", "has a swig of ale"));
            Parse("{\"verb\":\"say\",\"text\":\"hello\"}").Kind.Should().BeEmpty();
        }

        [Theory]
        [InlineData("has a swig of ale", "*has a swig of ale*")]
        [InlineData("*has a swig of ale*", "*has a swig of ale*")]
        [InlineData("  checks his bowstring ", "*checks his bowstring*")]
        [InlineData("*kneels by the corpse", "*kneels by the corpse*")]
        [InlineData("* sighs *", "* sighs *")]
        public void An_emote_goes_between_asterisks_when_it_is_not_already(string text, string said)
        {
            AgentAction.EmoteText(text).Should().Be(said);
        }

        [Theory]
        [InlineData("bow")]
        [InlineData("salute")]
        public void Gestures_answer_to_misc(string verb)
        {
            new AgentAction { Verb = verb }.Behavior.ToString().Should().Be("Misc");
        }

        [Fact]
        public void A_follower_on_the_tile_its_still_target_faces_is_in_the_way()
        {
            // cuo-0jz: the leader at 1468,1518 leaving the inn, a companion on its next tile to the east.
            AgentPace.InTheWay(1469, 1518, 1468, 1518, ClassicUO.Game.Data.Direction.East, 2000).Should().BeTrue();
            AgentPace.InTheWay(1469, 1517, 1468, 1518, ClassicUO.Game.Data.Direction.Right, 2000).Should().BeTrue();
        }

        [Theory]
        [InlineData(1469, 1518, 500)]   // the target only just stopped: it may walk on
        [InlineData(1467, 1518, 2000)]  // behind it
        [InlineData(1469, 1519, 2000)]  // beside its line
        [InlineData(1470, 1518, 2000)]  // on its line, but two tiles off: room to step
        public void A_follower_off_its_targets_line_or_a_target_on_the_move_is_not_in_the_way(int px, int py, uint stillMs)
        {
            AgentPace.InTheWay(px, py, 1468, 1518, ClassicUO.Game.Data.Direction.East, stillMs).Should().BeFalse();
        }

        [Fact]
        public void The_way_ahead_runs_from_the_next_tile_in_the_direction_faced()
        {
            AgentPace.Ahead(100, 100, ClassicUO.Game.Data.Direction.Up, 2).Should().Equal((99, 99), (98, 98));
            AgentPace.Ahead(100, 100, ClassicUO.Game.Data.Direction.South | ClassicUO.Game.Data.Direction.Running, 1).Should().Equal((100, 101));
        }
    }
}
