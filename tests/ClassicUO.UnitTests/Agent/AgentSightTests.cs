using System.Collections.Generic;
using ClassicUO.Agent;
using FluentAssertions;
using Xunit;

namespace ClassicUO.UnitTests.Agent
{
    // The sight rule (Map.LineOfSight) on hand-built tiles. Heights and flags are the real tile
    // data's: a stone wall is NoShoot and 20 high, a window has Window, floors (wooden boards) are
    // NoShoot at height 0, a tile roof NoShoot 3 high, stone stairs a NoShoot bridge 5 high.
    //
    // Three servers' rules (cuo-2ad): ModernUO's, RunUO's from 2.3 on ("runuo") and RunUO's up to 2.2
    // ("runuo22"). They agree on everything here but a door or an item on an end of the line
    // (ModernUO's own clause) and a height that is off by one (the allowance RunUO 2.2 lacks).
    public class AgentSightTests
    {
        // Rules go by name: the enum is internal, and the names are what settings.json takes.
        private static SightRule Rule(string name)
        {
            AgentSight.TryParseRule(name, out SightRule rule).Should().BeTrue();

            return rule;
        }

        private sealed class Tiles : ISightTiles
        {
            private readonly Dictionary<(int, int), (ushort Id, int Z)> _land = new();
            private readonly Dictionary<(int, int), List<SightPiece>> _statics = new();
            private readonly Dictionary<(int, int), List<SightPiece>> _items = new();

            public Tiles Ground(int x, int y, int z, ushort id = 3)
            {
                _land[(x, y)] = (id, z);

                return this;
            }

            public Tiles Static(int x, int y, SightPiece p)
            {
                (_statics.TryGetValue((x, y), out var l) ? l : _statics[(x, y)] = new List<SightPiece>()).Add(p);

                return this;
            }

            public Tiles Item(int x, int y, SightPiece p)
            {
                (_items.TryGetValue((x, y), out var l) ? l : _items[(x, y)] = new List<SightPiece>()).Add(p);

                return this;
            }

            public void Land(int x, int y, out ushort id, out int z) =>
                (id, z) = _land.TryGetValue((x, y), out var t) ? t : ((ushort) 3, 0); // grass at z 0

            public void Pieces(int x, int y, List<SightPiece> statics, List<SightPiece> items)
            {
                if (_statics.TryGetValue((x, y), out var s))
                {
                    statics.AddRange(s);
                }

                if (_items.TryGetValue((x, y), out var i))
                {
                    items.AddRange(i);
                }
            }
        }

        private static SightPiece Wall(int z = 0) => new SightPiece(z, 20, true);
        private static SightPiece Window(int z = 0) => new SightPiece(z, 20, true); // Window flag: blocks as NoShoot does
        private static SightPiece Door(int z = 0) => new SightPiece(z, 20, true, door: true);
        private static SightPiece Floor(int z) => new SightPiece(z, 0, true);
        private static SightPiece Roof(int z) => new SightPiece(z, 3, true);
        private static SightPiece Stairs(int z) => new SightPiece(z, 5 / 2, true); // a bridge: half its height

        [Theory]
        [InlineData("modernuo")]
        [InlineData("runuo")]
        [InlineData("runuo22")]
        public void Open_ground_is_in_sight(string server)
        {
            SightRule rule = Rule(server);
            var t = new Tiles();

            AgentSight.MobileSeesMobile(t, 100, 100, 0, 108, 103, 0, rule).Should().BeTrue();
            AgentSight.MobileSeesMobile(t, 108, 103, 0, 100, 100, 0, rule).Should().BeTrue();
            AgentSight.MobileSeesGround(t, 100, 100, 0, 104, 100, rule).Should().BeTrue();
        }

        [Theory]
        [InlineData("modernuo")]
        [InlineData("runuo")]
        [InlineData("runuo22")]
        public void A_wall_between_blocks_both_ways(string server)
        {
            SightRule rule = Rule(server);
            var t = new Tiles().Static(104, 100, Wall());

            AgentSight.MobileSeesMobile(t, 100, 100, 0, 108, 100, 0, rule).Should().BeFalse();
            AgentSight.MobileSeesMobile(t, 108, 100, 0, 100, 100, 0, rule).Should().BeFalse();
            AgentSight.MobileSeesGround(t, 100, 100, 0, 106, 100, rule).Should().BeFalse();
        }

        [Theory]
        [InlineData("modernuo")]
        [InlineData("runuo")]
        [InlineData("runuo22")]
        public void A_wall_beside_the_line_does_not_block(string server)
        {
            SightRule rule = Rule(server);
            var t = new Tiles().Static(104, 102, Wall());

            AgentSight.MobileSeesMobile(t, 100, 100, 0, 108, 100, 0, rule).Should().BeTrue();
        }

        [Theory]
        [InlineData("modernuo")]
        [InlineData("runuo")]
        [InlineData("runuo22")]
        public void An_item_wall_blocks_as_a_static_does(string server)
        {
            SightRule rule = Rule(server);
            var t = new Tiles().Item(104, 100, Wall());

            AgentSight.MobileSeesMobile(t, 100, 100, 0, 108, 100, 0, rule).Should().BeFalse();
        }

        [Theory]
        [InlineData("modernuo")]
        [InlineData("runuo")]
        [InlineData("runuo22")]
        public void A_window_blocks(string server)
        {
            SightRule rule = Rule(server);
            var t = new Tiles().Static(103, 100, Window());

            AgentSight.MobileSeesMobile(t, 100, 100, 0, 106, 100, 0, rule).Should().BeFalse();
        }

        [Fact]
        public void A_door_where_the_line_starts_does_not_block()
        {
            // The viewer stands in a doorway (the lower x, so the line starts there).
            var t = new Tiles().Item(100, 100, Door());

            AgentSight.MobileSeesMobile(t, 100, 100, 0, 106, 100, 0).Should().BeTrue();

            // ...but a closed door between them does.
            var shut = new Tiles().Item(103, 100, Door());
            AgentSight.MobileSeesMobile(shut, 100, 100, 0, 106, 100, 0).Should().BeFalse();

            // ...and something that is not a door on the viewer's tile does too.
            var crate = new Tiles().Item(100, 100, Wall());
            AgentSight.MobileSeesMobile(crate, 100, 100, 0, 106, 100, 0).Should().BeFalse();
        }

        [Theory]
        [InlineData("runuo")]
        [InlineData("runuo22")]
        public void On_a_RunUO_shard_a_door_where_the_line_starts_blocks(string server)
        {
            // The same doorway: RunUO has no clause for it, so the door the viewer stands in is a wall
            // to it. This is the case where the client would aim and the shard refuse.
            SightRule rule = Rule(server);
            var t = new Tiles().Item(100, 100, Door());

            AgentSight.MobileSeesMobile(t, 100, 100, 0, 106, 100, 0, rule).Should().BeFalse();

            var shut = new Tiles().Item(103, 100, Door());
            AgentSight.MobileSeesMobile(shut, 100, 100, 0, 106, 100, 0, rule).Should().BeFalse();

            var crate = new Tiles().Item(100, 100, Wall());
            AgentSight.MobileSeesMobile(crate, 100, 100, 0, 106, 100, 0, rule).Should().BeFalse();
        }

        [Theory]
        [InlineData("modernuo", true)]
        [InlineData("runuo", false)]
        [InlineData("runuo22", false)]
        public void Only_ModernUO_lets_the_viewer_see_past_what_stands_on_the_tile_the_line_ends_on(string server, bool seen)
        {
            // The line runs from the lower x to the higher, so a viewer east of its target is where
            // it ends. ModernUO lets nothing there block, door or not; to RunUO the viewer's tile is
            // a tile like any other.
            SightRule rule = Rule(server);
            var crate = new Tiles().Item(106, 100, Wall());
            var door = new Tiles().Item(106, 100, Door());

            AgentSight.MobileSeesMobile(crate, 106, 100, 0, 100, 100, 0, rule).Should().Be(seen);
            AgentSight.MobileSeesMobile(door, 106, 100, 0, 100, 100, 0, rule).Should().Be(seen);
        }

        [Theory]
        [InlineData("modernuo")]
        [InlineData("runuo")]
        [InlineData("runuo22")]
        public void A_door_the_target_stands_in_never_blocks(string server)
        {
            // On the target's own tile, at its height: every server spares it, from either side.
            SightRule rule = Rule(server);
            var west = new Tiles().Item(100, 100, Door());
            var east = new Tiles().Item(106, 100, Door());

            AgentSight.MobileSeesMobile(west, 106, 100, 0, 100, 100, 0, rule).Should().BeTrue();
            AgentSight.MobileSeesMobile(east, 100, 100, 0, 106, 100, 0, rule).Should().BeTrue();
        }

        [Theory]
        [InlineData("modernuo", false)]
        [InlineData("runuo", false)]
        [InlineData("runuo22", true)]
        public void A_piece_whose_base_is_one_above_the_line_blocks_except_under_RunUO_2_2(string server, bool seen)
        {
            // Both at z 0, so the line runs at 14. A NoShoot piece between them with its base at 15:
            // within the one-unit allowance of ModernUO and of RunUO from 2.3, over the line to 2.2.
            SightRule rule = Rule(server);
            var piece = new Tiles().Static(103, 100, new SightPiece(15, 5, true));
            var floor = new Tiles().Static(103, 100, Floor(15));
            var item = new Tiles().Item(103, 100, new SightPiece(15, 5, true));

            AgentSight.MobileSeesMobile(piece, 100, 100, 0, 106, 100, 0, rule).Should().Be(seen);
            AgentSight.MobileSeesMobile(floor, 100, 100, 0, 106, 100, 0, rule).Should().Be(seen);
            AgentSight.MobileSeesMobile(item, 100, 100, 0, 106, 100, 0, rule).Should().Be(seen);

            // One higher is over the line for all of them, and at the line's height blocks for all.
            var higher = new Tiles().Static(103, 100, new SightPiece(16, 5, true));
            var level = new Tiles().Static(103, 100, Floor(14));
            AgentSight.MobileSeesMobile(higher, 100, 100, 0, 106, 100, 0, rule).Should().BeTrue();
            AgentSight.MobileSeesMobile(level, 100, 100, 0, 106, 100, 0, rule).Should().BeFalse();
        }

        [Theory]
        [InlineData("modernuo", true)]
        [InlineData("runuo", true)]
        [InlineData("runuo22", false)]
        public void A_piece_one_above_the_target_on_its_own_tile_is_spared_except_under_RunUO_2_2(string server, bool seen)
        {
            // Seen from z 20 one tile away, the line comes down through a NoShoot piece on the
            // target's tile whose base, at 15, is one above the target's 14. With the allowance it
            // counts as at the target's height and is spared; without, it blocks.
            SightRule rule = Rule(server);
            var piece = new Tiles().Static(101, 100, new SightPiece(15, 5, true));
            var item = new Tiles().Item(101, 100, new SightPiece(15, 5, true));

            AgentSight.MobileSeesMobile(piece, 100, 100, 20, 101, 100, 0, rule).Should().Be(seen);
            AgentSight.MobileSeesMobile(item, 100, 100, 20, 101, 100, 0, rule).Should().Be(seen);

            // Two above the target is spared by none.
            var higher = new Tiles().Static(101, 100, new SightPiece(16, 5, true));
            AgentSight.MobileSeesMobile(higher, 100, 100, 20, 101, 100, 0, rule).Should().BeFalse();
        }

        [Theory]
        [InlineData("modernuo", false)]
        [InlineData("runuo", false)]
        [InlineData("runuo22", true)]
        public void Land_one_above_the_line_blocks_except_under_RunUO_2_2(string server, bool seen)
        {
            // The same allowance for land: ground lying flat at 15 over a line at 14 (two below it,
            // as in a cellar dug under the map's own ground).
            SightRule rule = Rule(server);
            var t = new Tiles();

            for (int x = 98; x <= 109; x++)
            {
                for (int y = 98; y <= 102; y++)
                {
                    t.Ground(x, y, 15);
                }
            }

            AgentSight.MobileSeesMobile(t, 100, 100, 0, 106, 100, 0, rule).Should().Be(seen);
        }

        [Theory]
        [InlineData("modernuo", "ModernUO")]
        [InlineData("ModernUO", "ModernUO")]
        [InlineData("runuo", "RunUO")]
        [InlineData(" RunUO22 ", "RunUO22")]
        [InlineData("", "ModernUO")]
        [InlineData(null, "ModernUO")]
        public void A_rule_is_named_as_settings_json_names_it(string setting, string rule)
        {
            AgentSight.TryParseRule(setting, out SightRule parsed).Should().BeTrue();
            parsed.ToString().Should().Be(rule);
            AgentSight.TryParseRule(parsed.Name(), out SightRule back).Should().BeTrue();
            back.Should().Be(parsed);
        }

        [Theory]
        [InlineData("sphere")]
        [InlineData("runuo 2.2")]
        [InlineData("1")]
        public void An_unknown_rule_is_refused_and_leaves_ModernUOs(string setting)
        {
            AgentSight.TryParseRule(setting, out SightRule parsed).Should().BeFalse();
            parsed.Should().Be(SightRule.ModernUO);
        }

        [Theory]
        [InlineData("modernuo")]
        [InlineData("runuo")]
        [InlineData("runuo22")]
        public void Nothing_on_the_target_tile_at_its_height_blocks(string server)
        {
            SightRule rule = Rule(server);
            // A door on the target's own tile, spanning its height (the end of the line).
            var t = new Tiles().Item(106, 100, Door()).Static(106, 100, Wall());

            AgentSight.MobileSeesMobile(t, 100, 100, 0, 106, 100, 0, rule).Should().BeTrue();
        }

        [Theory]
        [InlineData("modernuo")]
        [InlineData("runuo")]
        [InlineData("runuo22")]
        public void A_low_fence_is_seen_over(string server)
        {
            SightRule rule = Rule(server);
            // A NoShoot fence 6 high: the line runs at eye height (14) over it.
            var t = new Tiles().Static(103, 100, new SightPiece(0, 6, true));

            AgentSight.MobileSeesMobile(t, 100, 100, 0, 106, 100, 0, rule).Should().BeTrue();

            // A fence of the same height without NoShoot never blocks whatever its height.
            var tall = new Tiles().Static(103, 100, new SightPiece(0, 20, false));
            AgentSight.MobileSeesMobile(tall, 100, 100, 0, 106, 100, 0, rule).Should().BeTrue();
        }

        [Theory]
        [InlineData("modernuo")]
        [InlineData("runuo")]
        [InlineData("runuo22")]
        public void Indoors_the_floor_and_roof_do_not_block(string server)
        {
            SightRule rule = Rule(server);
            // A room with a floor at z 20 and a roof at z 40, both inside at z 20: eyes at 34.
            var t = new Tiles();

            for (int x = 98; x <= 110; x++)
            {
                for (int y = 98; y <= 104; y++)
                {
                    t.Static(x, y, Floor(20)).Static(x, y, Roof(40));
                }
            }

            AgentSight.MobileSeesMobile(t, 100, 100, 20, 108, 102, 20, rule).Should().BeTrue();

            // A wall of the room, standing on its floor, between them does block.
            t.Static(104, 100, Wall(20)).Static(104, 101, Wall(20)).Static(104, 102, Wall(20));
            AgentSight.MobileSeesMobile(t, 100, 100, 20, 108, 101, 20, rule).Should().BeFalse();
        }

        [Theory]
        [InlineData("modernuo")]
        [InlineData("runuo")]
        [InlineData("runuo22")]
        public void A_target_on_stairs_at_another_height_is_in_sight(string server)
        {
            SightRule rule = Rule(server);
            // Stone stairs rising 5 a tile from x 104: the target stands at z 15 on the third step.
            var t = new Tiles().Static(104, 100, Stairs(0)).Static(105, 100, Stairs(5)).Static(106, 100, Stairs(10));

            AgentSight.MobileSeesMobile(t, 100, 100, 0, 106, 100, 15, rule).Should().BeTrue();
            AgentSight.MobileSeesMobile(t, 106, 100, 15, 100, 100, 0, rule).Should().BeTrue();
        }

        [Theory]
        [InlineData("modernuo")]
        [InlineData("runuo")]
        [InlineData("runuo22")]
        public void A_hill_between_blocks_and_no_draw_land_does_not(string server)
        {
            SightRule rule = Rule(server);
            var hill = new Tiles();

            for (int y = 98; y <= 102; y++)
            {
                hill.Ground(103, y, 30).Ground(104, y, 30);
            }

            AgentSight.MobileSeesMobile(hill, 100, 100, 0, 107, 100, 0, rule).Should().BeFalse();

            // The same hill of "no draw" land (as under a cave) is passed through: each tile the line
            // crosses next to it is no-draw land too.
            var cave = new Tiles();

            for (int y = 98; y <= 102; y++)
            {
                for (int x = 101; x <= 105; x++)
                {
                    cave.Ground(x, y, x == 103 || x == 104 ? 30 : 0, 0x1AE);
                }
            }

            AgentSight.MobileSeesMobile(cave, 100, 100, 0, 107, 100, 0, rule).Should().BeTrue();
        }

        [Theory]
        [InlineData("modernuo")]
        [InlineData("runuo")]
        [InlineData("runuo22")]
        public void Further_than_25_tiles_is_out_of_sight(string server)
        {
            SightRule rule = Rule(server);
            var t = new Tiles();

            AgentSight.MobileSeesMobile(t, 100, 100, 0, 125, 100, 0, rule).Should().BeTrue();
            AgentSight.MobileSeesMobile(t, 100, 100, 0, 126, 100, 0, rule).Should().BeFalse();
        }

        [Theory]
        [InlineData("modernuo")]
        [InlineData("runuo")]
        [InlineData("runuo22")]
        public void Aiming_at_the_ground_aims_low(string server)
        {
            SightRule rule = Rule(server);
            // The server takes a ground target one above the highest corner of the land there, so a
            // NoShoot piece 10 high hides the ground behind it but not a creature standing there.
            var t = new Tiles();

            for (int y = 95; y <= 105; y++)
            {
                t.Static(102, y, new SightPiece(0, 10, true));
            }

            AgentSight.MobileSeesMobile(t, 100, 100, 0, 104, 100, 0, rule).Should().BeTrue();
            AgentSight.MobileSeesGround(t, 100, 100, 0, 104, 100, rule).Should().BeFalse();

            // The highest corner: the tile itself and its neighbours south, east and south-east.
            t.Ground(105, 101, 40);
            AgentSight.LandTop(t, 104, 100).Should().Be(40);
            AgentSight.LandTop(t, 104, 98).Should().Be(0);
        }

        [Theory]
        [InlineData("modernuo")]
        [InlineData("runuo")]
        [InlineData("runuo22")]
        public void The_edge_of_a_plateau_hides_a_creature_further_back_on_it(string server)
        {
            SightRule rule = Rule(server);
            var t = new Tiles();

            for (int y = 98; y <= 102; y++)
            {
                for (int x = 104; x <= 108; x++)
                {
                    t.Ground(x, y, 30);
                }
            }

            AgentSight.MobileSeesMobile(t, 100, 100, 0, 104, 100, 30, rule).Should().BeTrue(); // at the edge
            AgentSight.MobileSeesMobile(t, 100, 100, 0, 106, 100, 30, rule).Should().BeFalse();
            AgentSight.MobileSeesMobile(t, 100, 100, 30, 106, 100, 30, rule).Should().BeTrue(); // from as high
        }
    }
}
