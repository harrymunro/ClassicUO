// SPDX-License-Identifier: BSD-2-Clause

using System;
using System.Collections.Generic;
using System.Diagnostics;
using ClassicUO.Game;
using ClassicUO.Game.Data;
using ClassicUO.Game.GameObjects;

namespace ClassicUO.Agent
{
    // A hostile creature as the spot finder sees it.
    internal readonly struct SpotHostile
    {
        public readonly uint Serial;
        public readonly int X, Y, Z;

        public SpotHostile(uint serial, int x, int y, int z)
        {
            Serial = serial;
            X = x;
            Y = y;
            Z = z;
        }
    }

    // Something worth standing by, found in view by the caller: a doorway, the pet, a guard.
    internal readonly struct SpotLandmark
    {
        public readonly string Kind; // "doorway", "pet", "npc"
        public readonly string Name;
        public readonly int X, Y;

        public SpotLandmark(string kind, string name, int x, int y)
        {
            Kind = kind;
            Name = name ?? string.Empty;
            X = x;
            Y = y;
        }
    }

    // A place the player could walk to, with the facts the brain words for Jev (cuo-fhf.1).
    internal sealed class Spot
    {
        public string Kind = "ring"; // "stay", "ring", "doorway", "corridor", "pet", "npc"
        public string Name = string.Empty; // a landmark's name
        public int X, Y, Z, Dx, Dy;
        public int Ring;           // a ring spot: the ring's distance from the player
        public int Bearing = -1;   // a ring spot: its bearing, 0-15 clockwise from north
        public int Steps;          // the walk there, in steps (not the straight-line distance)
        public int Room;           // ground to go on to from there without getting nearer the creatures, up to OPEN_ROOM tiles
        public readonly List<(uint Serial, int Distance)> Near = new List<(uint, int)>(); // every hostile's distance from it
        public readonly List<uint> SeenBy = new List<uint>();   // hostiles within SIGHT_RANGE that can see it
        public readonly List<uint> PassesBy = new List<uint>(); // hostiles the walk there passes next to (one in melee reach: from its third step)
    }

    // Where the player could stand, offered to Jev by coverage, never by judgment (cuo-fhf): stay; 16
    // bearings at 3, 8 and 14 tiles, each snapped to a reachable tile nearby, preferring one with ground
    // to go on to, and dropped when there is none; landmarks (doorways, the mouths of narrow ways, beside
    // the pet or a guard). The walk is worked out over the travel planner's grid (map files, doors,
    // houses, items in view). The facts per spot are hard ones: steps, direction, how far each hostile
    // is from it, which hostiles can see it, whether the walk passes next to one, and whether there is
    // somewhere to go on to from there.
    internal static class AgentSpots
    {
        public static readonly int[] Rings = { 3, 8, 14 };
        public const int BEARINGS = 16;
        public const int RADIUS = 20;          // the grid round the player: the outer ring, its snapping, and room beyond it
        public const int MAX_STEPS = 48;       // a walk longer than this is no place to run to
        public const int SIGHT_RANGE = 12;     // further from a spot than this, a creature's spells and arrows don't reach it
        public const int CORRIDOR_STEPS = 12;  // narrow ways are looked for this far
        public const int MAX_CORRIDORS = 3;
        public const int OPEN_ROOM = 100;      // this much room beyond a spot (or the grid's edge) is open ground
        public const int DEAD_END = 12;        // less than this is a dead end

        private static readonly (int Dx, int Dy)[] Steps8 = { (0, -1), (1, 0), (0, 1), (-1, 0), (1, -1), (1, 1), (-1, 1), (-1, -1) };

        private static readonly string[] Compass16Names =
        {
            "north", "north-northeast", "northeast", "east-northeast", "east", "east-southeast", "southeast", "south-southeast",
            "south", "south-southwest", "southwest", "west-southwest", "west", "west-northwest", "northwest", "north-northwest"
        };

        // Which of 16 bearings (dx, dy) points along, 0 north, clockwise; UO's y grows southwards.
        public static int Bearing16(int dx, int dy)
        {
            double a = Math.Atan2(dx, -dy) * 180 / Math.PI;

            return (int) Math.Round(((a + 360) % 360) / 22.5) % 16;
        }

        public static string Compass16(int dx, int dy) => dx == 0 && dy == 0 ? "here" : Compass16Names[Bearing16(dx, dy)];

        // The ideal point on a ring: rings are square, as UO counts distance (the larger of the two).
        public static (int Dx, int Dy) RingPoint(int ring, int bearing)
        {
            double t = bearing * Math.PI / 8;
            double sx = Math.Sin(t), sy = -Math.Cos(t);
            double m = Math.Max(Math.Abs(sx), Math.Abs(sy));

            return ((int) Math.Round(ring * sx / m), (int) Math.Round(ring * sy / m));
        }

        private static int SnapRadius(int ring) => ring <= 3 ? 1 : ring <= 8 ? 2 : 3;

        public static List<Spot> Find(NavGrid g, int px, int py, int pz, IReadOnlyList<SpotHostile> hostiles,
            IReadOnlyList<SpotLandmark> landmarks = null, ISightTiles sight = null, SightRule rule = SightRule.ModernUO)
        {
            var spots = new List<Spot>();
            int k0 = g.Surface(px, py, pz);

            if (k0 < 0 && g.Contains(px, py))
            {
                // Standing somewhere the map files don't know (a house floor): walk from the ground here, as Plan does.
                g.AddSurface(px, py, pz);
                k0 = g.Surface(px, py, pz);
            }

            if (k0 < 0)
            {
                return spots;
            }

            var walk = new Walk(g, px, py, k0, hostiles);
            var taken = new HashSet<(int, int)>();

            Spot stay = walk.MakeSpot(px, py, "stay");
            spots.Add(stay);
            taken.Add((px, py));

            for (int b = 0; b < BEARINGS; b++)
            {
                foreach (int ring in Rings)
                {
                    (int dx, int dy) = RingPoint(ring, b);

                    (int X, int Y)? snapped = Snap(walk, px + dx, py + dy, SnapRadius(ring), taken);

                    if (!snapped.HasValue)
                    {
                        continue; // nowhere to stand near this point, or no way there
                    }

                    (int sx, int sy) = snapped.Value;
                    Spot s = walk.MakeSpot(sx, sy, "ring");
                    s.Ring = ring;
                    s.Bearing = b;
                    spots.Add(s);
                    taken.Add((sx, sy));
                }
            }

            foreach ((int cx, int cy) in walk.CorridorMouths(MAX_CORRIDORS))
            {
                AddLandmark(spots, taken, walk.MakeSpot(cx, cy, "corridor"));
            }

            if (landmarks != null)
            {
                foreach (SpotLandmark l in landmarks)
                {
                    (int X, int Y)? tile = LandmarkTile(walk, l, hostiles);

                    if (tile.HasValue)
                    {
                        Spot s = walk.MakeSpot(tile.Value.X, tile.Value.Y, l.Kind);
                        s.Name = l.Name;
                        AddLandmark(spots, taken, s);
                    }
                }
            }

            foreach (Spot s in spots)
            {
                walk.Facts(s, hostiles, sight, rule);
            }

            return spots;
        }

        // A landmark on a ring spot's tile takes its place: "the doorway" says more than "8 steps north".
        private static void AddLandmark(List<Spot> spots, HashSet<(int, int)> taken, Spot s)
        {
            if (taken.Add((s.X, s.Y)))
            {
                spots.Add(s);

                return;
            }

            Spot there = spots.Find(o => o.X == s.X && o.Y == s.Y);

            if (there != null && there.Kind == "ring")
            {
                there.Kind = s.Kind;
                there.Name = s.Name;
            }
        }

        // The tile to stand on nearest (x, y), within `radius` of it: one the player can walk to and
        // not taken already, preferring one that isn't a dead end (one only when there is nothing
        // else there). Null when none.
        public static (int X, int Y)? Snap(Walk walk, int x, int y, int radius, HashSet<(int, int)> taken)
        {
            var candidates = new List<(int X, int Y, int D2, int Steps)>();

            for (int dx = -radius; dx <= radius; dx++)
            {
                for (int dy = -radius; dy <= radius; dy++)
                {
                    int tx = x + dx, ty = y + dy;
                    int steps = walk.StepsTo(tx, ty);

                    if (steps >= 1 && !taken.Contains((tx, ty)))
                    {
                        candidates.Add((tx, ty, dx * dx + dy * dy, steps));
                    }
                }
            }

            if (candidates.Count == 0)
            {
                return null;
            }

            candidates.Sort((a, b) => a.D2 != b.D2 ? a.D2.CompareTo(b.D2) : a.Steps.CompareTo(b.Steps));

            foreach ((int cx, int cy, _, _) in candidates)
            {
                if (walk.Room(cx, cy) >= DEAD_END)
                {
                    return (cx, cy);
                }
            }

            return (candidates[0].X, candidates[0].Y);
        }

        // Where to stand for a landmark: a doorway's own tile, or the reachable tile beside it with the
        // shortest walk; beside the pet or a person, the tile next to it furthest from the hostiles.
        private static (int X, int Y)? LandmarkTile(Walk walk, SpotLandmark l, IReadOnlyList<SpotHostile> hostiles)
        {
            if (l.Kind == "doorway" && walk.StepsTo(l.X, l.Y) >= 1)
            {
                return (l.X, l.Y);
            }

            (int X, int Y)? best = null;
            int bestFar = -1, bestSteps = int.MaxValue;

            foreach ((int dx, int dy) in Steps8)
            {
                int x = l.X + dx, y = l.Y + dy;
                int steps = walk.StepsTo(x, y);

                if (steps < 1)
                {
                    continue;
                }

                int far = l.Kind == "doorway" ? 0 : Nearest(hostiles, x, y);

                if (far > bestFar || far == bestFar && steps < bestSteps)
                {
                    best = (x, y);
                    bestFar = far;
                    bestSteps = steps;
                }
            }

            return best;
        }

        private static int Nearest(IReadOnlyList<SpotHostile> hostiles, int x, int y)
        {
            int best = int.MaxValue;

            foreach (SpotHostile h in hostiles)
            {
                best = Math.Min(best, AgentNav.Tiles(h.X, h.Y, x, y));
            }

            return best;
        }

        // Every tile the player can walk to, by the fewest steps, a tile at a time as the travel planner
        // does (no corner cutting, steps up and down within reach), never through a hostile's tile.
        public sealed class Walk
        {
            private readonly NavGrid _g;
            private readonly int _px, _py;
            private readonly IReadOnlyList<SpotHostile> _hostiles;
            private readonly HashSet<(int, int)> _occupied = new HashSet<(int, int)>();
            private readonly int[] _steps;  // -1: no way there
            private readonly int[] _parent; // the tile the shortest walk comes from
            private readonly sbyte[] _z;
            private readonly int[] _seen;   // Room's flood: the call that last reached each tile
            private readonly sbyte[] _floodZ;
            private int _floods;

            public Walk(NavGrid g, int px, int py, int k0, IReadOnlyList<SpotHostile> hostiles)
            {
                _g = g;
                _px = px;
                _py = py;
                _hostiles = hostiles;
                int n = g.Width * g.Height;
                _steps = new int[n];
                _parent = new int[n];
                _z = new sbyte[n];
                _seen = new int[n];
                _floodZ = new sbyte[n];
                Array.Fill(_steps, -1);
                HashSet<(int, int)> occupied = _occupied;

                foreach (SpotHostile h in hostiles)
                {
                    occupied.Add((h.X, h.Y));
                }

                int start = Index(px, py);
                _steps[start] = 0;
                _parent[start] = -1;
                _z[start] = g.Z(px, py, k0);
                var queue = new Queue<int>();
                queue.Enqueue(start);

                while (queue.Count > 0)
                {
                    int i = queue.Dequeue();
                    int x = g.X0 + i % g.Width, y = g.Y0 + i / g.Width;
                    int z = _z[i], reach = g.ReachAt(x, y, z);

                    if (_steps[i] >= MAX_STEPS)
                    {
                        continue;
                    }

                    foreach ((int dx, int dy) in Steps8)
                    {
                        int nx = x + dx, ny = y + dy;

                        if (!g.Contains(nx, ny) || occupied.Contains((nx, ny)))
                        {
                            continue;
                        }

                        int nk = g.StepTo(nx, ny, z, reach);

                        if (nk < 0 || dx != 0 && dy != 0 && (g.StepTo(x + dx, y, z, reach) < 0 || g.StepTo(x, y + dy, z, reach) < 0))
                        {
                            continue;
                        }

                        int j = Index(nx, ny);

                        if (_steps[j] >= 0)
                        {
                            continue;
                        }

                        _steps[j] = _steps[i] + 1;
                        _parent[j] = i;
                        _z[j] = g.Z(nx, ny, nk);
                        queue.Enqueue(j);
                    }
                }
            }

            private int Index(int x, int y) => (y - _g.Y0) * _g.Width + (x - _g.X0);

            public int StepsTo(int x, int y) => _g.Contains(x, y) ? _steps[Index(x, y)] : -1;

            public int ZAt(int x, int y) => _z[Index(x, y)];

            // How near the closest creature is to (x, y); with none, how near the player is.
            private int Threat(int x, int y)
            {
                if (_hostiles.Count == 0)
                {
                    return AgentNav.Tiles(_px, _py, x, y);
                }

                int best = int.MaxValue;

                foreach (SpotHostile h in _hostiles)
                {
                    best = Math.Min(best, AgentNav.Tiles(h.X, h.Y, x, y));
                }

                return best;
            }

            // The ground there is to go on to from (x, y) without getting nearer the creatures: the tiles
            // a flood from there reaches over tiles no nearer the closest creature than (x, y) is, up to
            // OPEN_ROOM (or OPEN_ROOM when it reaches the grid's edge: more lies beyond). Few is a dead end
            // or a closed pocket, where the only way out is back towards them; OPEN_ROOM is open ground.
            public int Room(int x, int y)
            {
                if (!_g.Contains(x, y) || _steps[Index(x, y)] < 0)
                {
                    return 0;
                }

                int floor = Threat(x, y);
                int id = ++_floods;
                int start = Index(x, y);
                _seen[start] = id;
                _floodZ[start] = _z[start];
                var queue = new Queue<int>();
                queue.Enqueue(start);
                int count = 0;

                while (queue.Count > 0 && count < OPEN_ROOM)
                {
                    int i = queue.Dequeue();
                    count++;
                    int cx = _g.X0 + i % _g.Width, cy = _g.Y0 + i / _g.Width, z = _floodZ[i];
                    int reach = _g.ReachAt(cx, cy, z);

                    foreach ((int dx, int dy) in Steps8)
                    {
                        int nx = cx + dx, ny = cy + dy;

                        if (!_g.Contains(nx, ny))
                        {
                            return OPEN_ROOM;
                        }

                        int j = Index(nx, ny);

                        if (_seen[j] == id || _occupied.Contains((nx, ny)) || Threat(nx, ny) < floor)
                        {
                            continue;
                        }

                        int nk = _g.StepTo(nx, ny, z, reach);

                        if (nk < 0 || dx != 0 && dy != 0 && (_g.StepTo(cx + dx, cy, z, reach) < 0 || _g.StepTo(cx, cy + dy, z, reach) < 0))
                        {
                            continue;
                        }

                        _seen[j] = id;
                        _floodZ[j] = _g.Z(nx, ny, nk);
                        queue.Enqueue(j);
                    }
                }

                return Math.Min(count, OPEN_ROOM);
            }

            // The tiles of the walk to (x, y), from the first step on.
            public List<(int X, int Y)> PathTo(int x, int y)
            {
                var path = new List<(int, int)>();

                for (int i = _g.Contains(x, y) ? Index(x, y) : -1; i >= 0 && _steps[i] > 0; i = _parent[i])
                {
                    path.Add((_g.X0 + i % _g.Width, _g.Y0 + i / _g.Width));
                }

                path.Reverse();

                return path;
            }

            public Spot MakeSpot(int x, int y, string kind) =>
                new Spot { Kind = kind, X = x, Y = y, Z = ZAt(x, y), Dx = x - _px, Dy = y - _py, Steps = StepsTo(x, y) };

            // The facts about a spot: the room beyond it, every hostile's distance from it, which of them within
            // SIGHT_RANGE can see it there, and which the walk there passes next to.
            public void Facts(Spot s, IReadOnlyList<SpotHostile> hostiles, ISightTiles sight, SightRule rule)
            {
                s.Room = Room(s.X, s.Y);

                foreach (SpotHostile h in hostiles)
                {
                    int d = AgentNav.Tiles(h.X, h.Y, s.X, s.Y);
                    s.Near.Add((h.Serial, d));

                    if (sight != null && d <= SIGHT_RANGE && AgentSight.MobileSeesMobile(sight, h.X, h.Y, h.Z, s.X, s.Y, s.Z, rule))
                    {
                        s.SeenBy.Add(h.Serial);
                    }
                }

                s.Near.Sort((a, b) => a.Distance.CompareTo(b.Distance));
                List<(int X, int Y)> path = PathTo(s.X, s.Y);

                foreach (SpotHostile h in hostiles)
                {
                    // Leaving a creature already in melee reach takes a step or two beside it whichever way:
                    // only a walk still beside it from the third step on passes it.
                    int from = AgentNav.Tiles(h.X, h.Y, _px, _py) <= 1 ? 2 : 0;

                    for (int i = from; i < path.Count; i++)
                    {
                        if (AgentNav.Tiles(h.X, h.Y, path[i].X, path[i].Y) <= 1)
                        {
                            s.PassesBy.Add(h.Serial);

                            break;
                        }
                    }
                }
            }

            private bool Open(int x, int y, int z) => _g.Contains(x, y) && _g.StepTo(x, y, z) >= 0;

            // Walled on two opposite sides and open on the other two: a tile of a narrow way.
            private bool Narrow(int x, int y, int z)
            {
                bool n = Open(x, y - 1, z), s = Open(x, y + 1, z), e = Open(x + 1, y, z), w = Open(x - 1, y, z);

                return n && s && !e && !w || e && w && !n && !s;
            }

            // The mouths of narrow ways near the player: a narrow tile opening onto wider ground, nearest
            // first, at least 4 tiles apart.
            public List<(int X, int Y)> CorridorMouths(int max)
            {
                var found = new List<(int X, int Y, int Steps)>();

                for (int i = 0; i < _steps.Length; i++)
                {
                    int steps = _steps[i];

                    if (steps < 1 || steps > CORRIDOR_STEPS)
                    {
                        continue;
                    }

                    int x = _g.X0 + i % _g.Width, y = _g.Y0 + i / _g.Width, z = _z[i];

                    if (!Narrow(x, y, z))
                    {
                        continue;
                    }

                    bool vertical = Open(x, y - 1, z);
                    (int ax, int ay) = vertical ? (0, 1) : (1, 0);

                    if (Open(x - ax, y - ay, z) && !Narrow(x - ax, y - ay, z) || Open(x + ax, y + ay, z) && !Narrow(x + ax, y + ay, z))
                    {
                        found.Add((x, y, steps));
                    }
                }

                found.Sort((a, b) => a.Steps.CompareTo(b.Steps));
                var mouths = new List<(int X, int Y)>();

                foreach ((int x, int y, _) in found)
                {
                    if (mouths.Count >= max)
                    {
                        break;
                    }

                    if (!mouths.Exists(m => AgentNav.Tiles(m.X, m.Y, x, y) < 4))
                    {
                        mouths.Add((x, y));
                    }
                }

                return mouths;
            }
        }
    }

    // The spots round the player for the snapshot, and walks to one (cuo-fhf).
    internal sealed partial class AgentController
    {
        private const uint SPOTS_MS = 400;     // worked out again at most this often while the player stands still
        private const int SPOT_HOSTILES = 8;   // the nearest this many monsters count
        private const int SPOT_LANDMARKS = 14; // landmarks within this many tiles

        private List<Spot> _spots = new List<Spot>();
        private uint _spotsAt;
        private (int X, int Y) _spotsFrom;

        public double SpotsMs { get; private set; } // the time the last spots took to work out
        public int SpotsComputed { get; private set; }

        // Only with a monster in view: otherwise there is nothing to stand away from, and nothing is
        // worked out (planning on the game thread stays cheap).
        public IReadOnlyList<Spot> Spots(int radius)
        {
            PlayerMobile p = _world.Player;
            var hostiles = new List<(Mobile M, int D)>();

            foreach (Mobile m in _world.Mobiles.Values)
            {
                if (m != p && !m.IsDead && !m.IsDestroyed && (m.Serial & 0x80000000) == 0 && m.Distance <= radius && IsMonsterTarget(m))
                {
                    hostiles.Add((m, m.Distance));
                }
            }

            if (hostiles.Count == 0)
            {
                _spots.Clear();

                return _spots;
            }

            if (Time.Ticks - _spotsAt < SPOTS_MS && _spotsFrom == (p.X, p.Y) && _spots.Count != 0)
            {
                return _spots;
            }

            long t0 = Stopwatch.GetTimestamp();
            hostiles.Sort((a, b) => a.D.CompareTo(b.D));
            var near = new List<SpotHostile>();

            for (int i = 0; i < hostiles.Count && i < SPOT_HOSTILES; i++)
            {
                Mobile m = hostiles[i].M;
                near.Add(new SpotHostile(m.Serial, m.X, m.Y, m.Z));
            }

            _spots = AgentSpots.Find(SpotGrid(p.X, p.Y, AgentSpots.RADIUS), p.X, p.Y, p.Z, near, Landmarks(p), SightChecks ? SightTiles : null, SightRule);
            _spotsAt = Time.Ticks;
            _spotsFrom = (p.X, p.Y);
            SpotsMs = Stopwatch.GetElapsedTime(t0).TotalMilliseconds;
            SpotsComputed++;

            return _spots;
        }

        // The travel planner's grid round the player, with what it knows of doors and houses, and the
        // impassable items in view (a built wall, a barricade) blocked; doors are ways through.
        private NavGrid SpotGrid(int px, int py, int radius)
        {
            int map = _world.MapIndex;
            NavGrid g = AgentNav.Load(map, px - radius, py - radius, 2 * radius + 1, 2 * radius + 1);
            _travelMemory.Apply(g, map, px, py, px, py);

            foreach (Item it in _world.Items.Values)
            {
                if (!it.OnGround || it.IsDestroyed || it.IsMulti || !g.Contains(it.X, it.Y))
                {
                    continue;
                }

                if (it.ItemData.IsDoor)
                {
                    g.MarkDoor(it.X, it.Y);
                }
                else if (it.ItemData.IsImpassable)
                {
                    g.Block(it.X, it.Y);
                }
            }

            return g;
        }

        // Doorways, the pet, and guards or healers in view.
        private List<SpotLandmark> Landmarks(PlayerMobile p)
        {
            var marks = new List<SpotLandmark>();

            foreach (Item it in _world.Items.Values)
            {
                if (it.OnGround && !it.IsDestroyed && it.ItemData.IsDoor && AgentNav.Tiles(p.X, p.Y, it.X, it.Y) <= SPOT_LANDMARKS)
                {
                    marks.Add(new SpotLandmark("doorway", string.Empty, it.X, it.Y));
                }
            }

            foreach (Mobile m in Pets())
            {
                if (m.Distance <= SPOT_LANDMARKS)
                {
                    marks.Add(new SpotLandmark("pet", m.Name?.Trim() ?? "the pet", m.X, m.Y));

                    break;
                }
            }

            foreach (Mobile m in _world.Mobiles.Values)
            {
                if (m == p || m.IsDead || m.Distance > SPOT_LANDMARKS || m.NotorietyFlag != NotorietyFlag.Invulnerable)
                {
                    continue;
                }

                string name = LabelOf(m) ?? m.Name?.Trim() ?? string.Empty;

                if (name.Contains("guard", StringComparison.OrdinalIgnoreCase) || name.Contains("healer", StringComparison.OrdinalIgnoreCase))
                {
                    marks.Add(new SpotLandmark("npc", name, m.X, m.Y));
                }
            }

            return marks;
        }

        // A run or a step back to the spot the brain picked: true when the walk started.
        private bool WalkToSpot(int x, int y, int maxTiles)
        {
            PlayerMobile p = _world.Player;

            if (x == 0 && y == 0 || AgentNav.Tiles(p.X, p.Y, x, y) > maxTiles || (x == p.X && y == p.Y))
            {
                return false;
            }

            if (WalkTo(x, y, p.Z, 0))
            {
                Stats.SpotWalks++;

                return true;
            }

            Stats.SpotFallbacks++;

            return false;
        }
    }
}
