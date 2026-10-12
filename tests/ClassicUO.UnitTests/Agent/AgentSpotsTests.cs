using System;
using System.Collections.Generic;
using System.Linq;
using ClassicUO.Agent;
using FluentAssertions;
using Xunit;

namespace ClassicUO.UnitTests.Agent
{
    // The places to stand offered to Jev (cuo-fhf.1): coverage, snapping, unreachable spots dropped,
    // and the facts per spot, on hand-drawn grids. '#' is a wall, '@' the player, anything else ground.
    public class AgentSpotsTests
    {
        private const int X0 = 1000, Y0 = 2000;

        private static NavGrid Field(out int px, out int py, params string[] rows)
        {
            var g = new NavGrid(X0, Y0, rows[0].Length, rows.Length);
            px = py = -1;

            for (int y = 0; y < rows.Length; y++)
            {
                for (int x = 0; x < rows[y].Length; x++)
                {
                    char c = rows[y][x];

                    if (c == '@')
                    {
                        px = X0 + x;
                        py = Y0 + y;
                    }

                    if (c != '#')
                    {
                        g.AddSurface(X0 + x, Y0 + y, 0);
                    }
                }
            }

            return g;
        }

        // Open ground, 41 x 41, the player in the middle.
        private static NavGrid Open(out int px, out int py, Action<char[][]> draw = null)
        {
            char[][] rows = Enumerable.Range(0, 41).Select(_ => new string('.', 41).ToCharArray()).ToArray();
            rows[20][20] = '@';
            draw?.Invoke(rows);

            return Field(out px, out py, rows.Select(r => new string(r)).ToArray());
        }

        [Fact]
        public void Open_ground_has_stay_and_every_bearing_at_every_ring()
        {
            NavGrid g = Open(out int px, out int py);
            List<Spot> spots = AgentSpots.Find(g, px, py, 0, new[] { new SpotHostile(1, px, py + 2, 0) });

            spots[0].Kind.Should().Be("stay");
            spots[0].Steps.Should().Be(0);
            var rings = spots.Where(s => s.Kind == "ring").ToList();
            rings.Should().HaveCount(48);

            foreach (int ring in AgentSpots.Rings)
            {
                rings.Where(s => s.Ring == ring).Select(s => s.Bearing).Should().BeEquivalentTo(Enumerable.Range(0, 16));
                // On open ground each lies on its ring, and the walk there is as long as the ring.
                rings.Where(s => s.Ring == ring).Should().OnlyContain(s => Math.Max(Math.Abs(s.Dx), Math.Abs(s.Dy)) == ring && s.Steps == ring);
            }

            spots.Select(s => (s.X, s.Y)).Should().OnlyHaveUniqueItems();
            spots.Should().OnlyContain(s => s.Room == AgentSpots.OPEN_ROOM);
        }

        [Fact]
        public void Ring_points_follow_the_compass()
        {
            AgentSpots.RingPoint(8, 0).Should().Be((0, -8));   // north is -y
            AgentSpots.RingPoint(8, 4).Should().Be((8, 0));    // east
            AgentSpots.RingPoint(8, 2).Should().Be((8, -8));   // northeast, on the square ring
            AgentSpots.RingPoint(14, 12).Should().Be((-14, 0)); // west
            AgentSpots.Compass16(-8, 3).Should().Be("west-southwest");
            AgentSpots.Compass16(0, -5).Should().Be("north");
        }

        [Fact]
        public void A_point_in_a_wall_snaps_to_ground_beside_it()
        {
            // A wall 3 thick across the 8-tile ring to the north, 9 wide: north at 8 tiles snaps to a
            // tile within 2 of the ring point that the player can walk to.
            NavGrid g = Open(out int px, out int py, rows =>
            {
                for (int y = 11; y <= 13; y++)
                {
                    for (int x = 16; x <= 24; x++)
                    {
                        rows[y][x] = '#';
                    }
                }
            });
            List<Spot> spots = AgentSpots.Find(g, px, py, 0, Array.Empty<SpotHostile>());
            Spot north8 = spots.Single(s => s.Kind == "ring" && s.Ring == 8 && s.Bearing == 0);

            Math.Max(Math.Abs(north8.X - px), Math.Abs(north8.Y - (py - 8))).Should().BeLessOrEqualTo(2);
            g.Count(north8.X, north8.Y).Should().BeGreaterThan(0);
            north8.Steps.Should().BeGreaterThan(0);
        }

        [Fact]
        public void Spots_with_no_way_there_are_dropped()
        {
            // The player in a closed room 9 across: nothing outside it is offered, and nothing at 8 or
            // 14 tiles (their snapping reaches no further than 3 tiles from the ring).
            NavGrid g = Open(out int px, out int py, rows =>
            {
                for (int i = 15; i <= 25; i++)
                {
                    rows[15][i] = rows[25][i] = rows[i][15] = rows[i][25] = '#';
                }
            });
            List<Spot> spots = AgentSpots.Find(g, px, py, 0, Array.Empty<SpotHostile>());

            spots.Should().OnlyContain(s => s.X > X0 + 15 && s.X < X0 + 25 && s.Y > Y0 + 15 && s.Y < Y0 + 25);
            spots.Where(s => s.Kind == "ring").Should().OnlyContain(s => s.Ring == 3);
        }

        [Fact]
        public void A_pocket_with_the_creatures_at_its_mouth_has_little_room()
        {
            // Walls east, west and north of the player, 4 tiles off: a pocket open only to the south,
            // where two orcs stand in its mouth.
            NavGrid g = Open(out int px, out int py, rows =>
            {
                for (int y = 14; y <= 25; y++)
                {
                    rows[y][16] = rows[y][24] = '#';
                }

                for (int x = 16; x <= 24; x++)
                {
                    rows[14][x] = '#';
                }
            });
            var orcs = new[] { new SpotHostile(1, px - 1, py + 4, 0), new SpotHostile(2, px + 1, py + 4, 0) };
            List<Spot> spots = AgentSpots.Find(g, px, py, 0, orcs);
            Spot back = spots.Single(s => s.Kind == "ring" && s.Ring == 3 && s.Bearing == 0); // against the back wall

            back.Room.Should().BeLessThan(AgentSpots.OPEN_ROOM);
            spots[0].Room.Should().BeLessThan(AgentSpots.OPEN_ROOM); // where the player stands is closed in too
            // Out of the pocket past the orcs, open ground; the 14-tile spot north is a long walk round.
            spots.Where(s => s.Kind == "ring" && s.Ring == 14 && s.Bearing == 8).Should().OnlyContain(s => s.Room == AgentSpots.OPEN_ROOM);
            spots.Single(s => s.Kind == "ring" && s.Ring == 14 && s.Bearing == 0).Steps.Should().BeGreaterThan(20);
        }

        [Fact]
        public void A_closed_corner_is_a_dead_end()
        {
            // A corner 2 tiles across, walled on three sides, its open side towards an orc.
            NavGrid g = Field(out int px, out int py,
                "#####",
                "#@..#",
                "#...#",
                "#...#",
                ".....",
                ".....");
            List<Spot> spots = AgentSpots.Find(g, px, py, 0, new[] { new SpotHostile(1, px + 2, py + 4, 0) });

            spots[0].Room.Should().BeLessThan(AgentSpots.DEAD_END);
        }

        [Fact]
        public void Facts_give_each_hostile_distance_and_the_walks_that_pass_one()
        {
            NavGrid g = Open(out int px, out int py);
            // An orc 5 north of the player.
            List<Spot> spots = AgentSpots.Find(g, px, py, 0, new[] { new SpotHostile(7, px, py - 5, 0) });
            Spot north8 = spots.Single(s => s.Kind == "ring" && s.Ring == 8 && s.Bearing == 0);
            Spot south8 = spots.Single(s => s.Kind == "ring" && s.Ring == 8 && s.Bearing == 8);

            north8.Near.Should().Equal((7u, 3));
            north8.PassesBy.Should().Contain(7u);  // straight past the orc
            south8.Near.Should().Equal((7u, 13));
            south8.PassesBy.Should().BeEmpty();
            // Nobody walks through the orc's own tile.
            spots.Should().NotContain(s => s.X == px && s.Y == py - 5);
        }

        [Fact]
        public void Stepping_away_from_a_creature_in_melee_doesnt_pass_it()
        {
            NavGrid g = Open(out int px, out int py);
            // An orc on the player, east: any way leaves it within two steps, so no walk passes it.
            List<Spot> spots = AgentSpots.Find(g, px, py, 0, new[] { new SpotHostile(7, px + 1, py, 0) });

            spots.Should().OnlyContain(s => s.PassesBy.Count == 0);

            // Two tiles off, the walk east steps beside it.
            spots = AgentSpots.Find(Open(out px, out py), px, py, 0, new[] { new SpotHostile(7, px + 2, py, 0) });
            spots.Single(s => s.Kind == "ring" && s.Ring == 3 && s.Bearing == 4).PassesBy.Should().Contain(7u);
            spots.Single(s => s.Kind == "ring" && s.Ring == 8 && s.Bearing == 12).PassesBy.Should().BeEmpty();
        }

        [Theory]
        [InlineData("modernuo", true)]
        [InlineData("runuo", false)]
        public void Sight_facts_follow_the_servers_rule(string server, bool seen)
        {
            // A caster standing in a doorway 6 west of the player: the line from it starts at the
            // door, which ModernUO never lets block and RunUO does.
            AgentSight.TryParseRule(server, out SightRule rule).Should().BeTrue();
            NavGrid g = Open(out int px, out int py);
            var sight = new DoorSight(px - 6, py);
            List<Spot> spots = AgentSpots.Find(g, px, py, 0, new[] { new SpotHostile(9, px - 6, py, 0) }, null, sight, rule);

            spots[0].SeenBy.Contains(9u).Should().Be(seen);
        }

        [Fact]
        public void Sight_facts_say_which_hostiles_can_see_a_spot()
        {
            NavGrid g = Open(out int px, out int py);
            var sight = new WallSight(px - 1, px + 1, py + 2); // a wall south of the player, across x px-1..px+1
            // A caster 6 south of the player, behind the wall from the player's side.
            List<Spot> spots = AgentSpots.Find(g, px, py, 0, new[] { new SpotHostile(9, px, py + 6, 0) }, null, sight);
            Spot stay = spots[0];
            Spot east3 = spots.Single(s => s.Kind == "ring" && s.Ring == 3 && s.Bearing == 4);

            stay.SeenBy.Should().BeEmpty();
            east3.SeenBy.Should().Contain(9u);
        }

        [Fact]
        public void Landmarks_are_spots_of_their_own()
        {
            NavGrid g = Open(out int px, out int py);
            var marks = new[] { new SpotLandmark("doorway", "", px + 5, py + 1), new SpotLandmark("pet", "Bruno", px - 2, py - 6) };
            List<Spot> spots = AgentSpots.Find(g, px, py, 0, new[] { new SpotHostile(3, px, py + 4, 0) }, marks);

            spots.Should().Contain(s => s.Kind == "doorway" && s.X == px + 5 && s.Y == py + 1);
            Spot pet = spots.Single(s => s.Kind == "pet");
            pet.Name.Should().Be("Bruno");
            Math.Max(Math.Abs(pet.X - (px - 2)), Math.Abs(pet.Y - (py - 6))).Should().Be(1);
            pet.Y.Should().Be(py - 7); // beside the pet, on its side away from the creature
        }

        [Fact]
        public void The_mouth_of_a_narrow_way_is_a_spot()
        {
            // A corridor one tile wide running north from open ground 3 tiles north of the player.
            NavGrid g = Open(out int px, out int py, rows =>
            {
                for (int y = 8; y <= 16; y++)
                {
                    for (int x = 10; x <= 30; x++)
                    {
                        rows[y][x] = x == 20 ? '.' : '#';
                    }
                }
            });
            List<Spot> spots = AgentSpots.Find(g, px, py, 0, Array.Empty<SpotHostile>());

            spots.Should().Contain(s => s.Kind == "corridor" && s.X == px && (s.Y == Y0 + 16 || s.Y == Y0 + 8));
        }

        // A wall that blocks sight on given tiles of one row, everything else open grass at z 0.
        // Open ground with one door, an item, on a tile.
        private sealed class DoorSight : ISightTiles
        {
            private readonly int _x, _y;

            public DoorSight(int x, int y)
            {
                _x = x;
                _y = y;
            }

            public void Land(int x, int y, out ushort id, out int z)
            {
                id = 3;
                z = 0;
            }

            public void Pieces(int x, int y, List<SightPiece> statics, List<SightPiece> items)
            {
                if (x == _x && y == _y)
                {
                    items.Add(new SightPiece(0, 20, true, door: true));
                }
            }
        }

        private sealed class WallSight : ISightTiles
        {
            private readonly int _x0, _x1, _y;

            public WallSight(int x0, int x1, int y)
            {
                _x0 = x0;
                _x1 = x1;
                _y = y;
            }

            public void Land(int x, int y, out ushort id, out int z)
            {
                id = 3;
                z = 0;
            }

            public void Pieces(int x, int y, List<SightPiece> statics, List<SightPiece> items)
            {
                if (y == _y && x >= _x0 && x <= _x1)
                {
                    statics.Add(new SightPiece(0, 20, true));
                }
            }
        }
    }
}
