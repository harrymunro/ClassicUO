// SPDX-License-Identifier: BSD-2-Clause

using System;
using System.Buffers;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using ClassicUO.Assets;

namespace ClassicUO.Agent
{
    // Long walks. The client's own pathfinder only sees the chunks it has loaded (about a
    // screen around the player), so a walk across town is planned here, coarsely, over the
    // raw map and static tiles, and then walked a leg at a time with that pathfinder, which
    // deals with creatures, items and the exact movement rules. Doors, houses and other items
    // are not in the map files: the walker adds what it has seen (TravelMemory), so a door is a
    // tile it can pass at a small cost and a house is somewhere it can't.
    internal sealed class NavGrid
    {
        public const int MAX_SURFACES = 3;
        public const int PERSON_HEIGHT = 16;
        public const int STEP_UP = 8;     // generous on the ground (slopes); the local pathfinder is exact
        // Onto a static's surface (a floor, a stair, a bridge, a tuft of grass) only from within this of its base, as
        // ModernUO's Movement.Check has it (StepHeight 2) and the client's pathfinder: a tuft floating 8 above the
        // ground at 1346,1512 was somewhere to stand in the plans and nobody could get onto it (cuo-5y8).
        public const int STEP_ONTO = 2;
        public const int STEP_DOWN = 20;
        public const int DOOR_COST = 4;   // extra steps for a door: stopping to open it

        public readonly int X0, Y0, Width, Height;

        // Up to three places to stand per tile (the z of the surface top), lowest first, and for each the lowest z
        // it can be stepped onto from.
        private readonly sbyte[] _z;
        private readonly sbyte[] _from;
        private readonly byte[] _count;
        private readonly bool[] _door;

        public NavGrid(int x0, int y0, int width, int height)
        {
            X0 = x0;
            Y0 = y0;
            Width = width;
            Height = height;
            _z = new sbyte[width * height * MAX_SURFACES];
            _from = new sbyte[width * height * MAX_SURFACES];
            _count = new byte[width * height];
            _door = new bool[width * height];
        }

        public bool Contains(int x, int y) => x >= X0 && y >= Y0 && x < X0 + Width && y < Y0 + Height;

        // A doorway with a door in it: walkable, once the door is open.
        public void MarkDoor(int x, int y)
        {
            if (Contains(x, y))
            {
                _door[(y - Y0) * Width + (x - X0)] = true;
            }
        }

        public bool IsDoor(int x, int y) => Contains(x, y) && _door[(y - Y0) * Width + (x - X0)];

        public int Count(int x, int y) => Contains(x, y) ? _count[(y - Y0) * Width + (x - X0)] : 0;

        public sbyte Z(int x, int y, int k) => _z[((y - Y0) * Width + (x - X0)) * MAX_SURFACES + k];

        // The lowest z the k-th surface at (x, y) can be stepped onto from.
        public sbyte From(int x, int y, int k) => _from[((y - Y0) * Width + (x - X0)) * MAX_SURFACES + k];

        // Ground: stepped onto from up to STEP_UP below it.
        public void AddSurface(int x, int y, int z) => AddSurface(x, y, z, z - STEP_UP);

        // A surface stepped onto from `from` up: a static's from STEP_ONTO below its base, the ground from STEP_UP
        // below its top.
        public void AddSurface(int x, int y, int z, int from)
        {
            int i = (y - Y0) * Width + (x - X0);
            int n = _count[i];

            if (n >= MAX_SURFACES)
            {
                return;
            }

            _z[i * MAX_SURFACES + n] = (sbyte) Math.Clamp(z, sbyte.MinValue, sbyte.MaxValue);
            _from[i * MAX_SURFACES + n] = (sbyte) Math.Clamp(from, sbyte.MinValue, sbyte.MaxValue);
            _count[i] = (byte) (n + 1);
        }

        public void Block(int x, int y)
        {
            if (Contains(x, y))
            {
                _count[(y - Y0) * Width + (x - X0)] = 0;
            }
        }

        // The surface at (x, y) one can step onto from height z, or -1.
        public int StepTo(int x, int y, int z)
        {
            int best = -1, bestDz = int.MaxValue;

            for (int k = 0, n = Count(x, y); k < n; k++)
            {
                int dz = Z(x, y, k) - z;

                if (z >= From(x, y, k) && -dz <= STEP_DOWN && Math.Abs(dz) < bestDz)
                {
                    best = k;
                    bestDz = Math.Abs(dz);
                }
            }

            return best;
        }

        public int Surface(int x, int y, int z)
        {
            int best = -1, bestDz = int.MaxValue;

            for (int k = 0, n = Count(x, y); k < n; k++)
            {
                int dz = Math.Abs(Z(x, y, k) - z);

                if (dz < bestDz)
                {
                    best = k;
                    bestDz = dz;
                }
            }

            return best;
        }
    }

    // What one search did: for timing, and to tell "nothing more to find" from "ran out of room".
    internal sealed class PlanStats
    {
        public int Expanded;
        public bool HitLimit;    // stopped at the node limit
        public bool TouchedEdge; // the search reached the edge of the grid: a wider one might find more
        public bool Partial;     // the path ends at the nearest point to the goal, not at it
    }

    internal static class AgentNav
    {
        private static readonly (int Dx, int Dy)[] Steps = { (0, -1), (1, 0), (0, 1), (-1, 0), (1, -1), (1, 1), (-1, 1), (-1, -1) };

        // A* over the grid, one tile per step (diagonals cost the same in UO; a door costs a few
        // more), never cutting a corner. Returns the tiles from start (excluded) to the first tile
        // within `distance` of the goal, or null. With `partialMin`, a search that can't get there
        // returns the way to the nearest tile it reached instead, if that is at least partialMin
        // tiles nearer the goal than the start.
        public static List<(int X, int Y, sbyte Z)> Plan(NavGrid g, int sx, int sy, int sz, int gx, int gy, int distance, int maxNodes = 400_000,
            int partialMin = 0, PlanStats stats = null)
        {
            int startK = g.Surface(sx, sy, sz);

            if (startK < 0)
            {
                // Standing somewhere the map files don't know (a house floor, a boat): plan from the
                // ground nearby.
                g.AddSurface(sx, sy, sz);
                startK = g.Surface(sx, sy, sz);
            }

            if (startK < 0)
            {
                return null; // outside the grid
            }

            int states = g.Width * g.Height * NavGrid.MAX_SURFACES;
            int[] cost = ArrayPool<int>.Shared.Rent(states);
            int[] parent = ArrayPool<int>.Shared.Rent(states);

            try
            {
                Array.Fill(cost, int.MaxValue, 0, states);
                var open = new PriorityQueue<int, int>();
                int start = Index(g, sx, sy, startK);
                cost[start] = 0;
                parent[start] = -1;
                int startH = Heuristic(sx, sy, gx, gy, distance);
                open.Enqueue(start, startH);
                int expanded = 0, best = start, bestH = startH;
                bool touched = false;

                while (open.TryDequeue(out int s, out int priority))
                {
                    (int x, int y, int k) = Unpack(g, s);
                    int c = cost[s];
                    int h = Heuristic(x, y, gx, gy, distance);

                    if (priority - h > c)
                    {
                        continue; // a stale entry
                    }

                    if (h == 0)
                    {
                        Report(stats, expanded, false, touched, false);

                        return Path(g, parent, s);
                    }

                    if (h < bestH)
                    {
                        best = s;
                        bestH = h;
                    }

                    if (++expanded > maxNodes)
                    {
                        Report(stats, expanded, true, touched, false);

                        return Partial(g, parent, best, startH - bestH, partialMin, stats);
                    }

                    int z = g.Z(x, y, k);

                    foreach ((int dx, int dy) in Steps)
                    {
                        int nx = x + dx, ny = y + dy;

                        if (!g.Contains(nx, ny))
                        {
                            touched = true;

                            continue;
                        }

                        int nk = g.StepTo(nx, ny, z);

                        if (nk < 0 || dx != 0 && dy != 0 && (g.StepTo(x + dx, y, z) < 0 || g.StepTo(x, y + dy, z) < 0))
                        {
                            continue;
                        }

                        int n = Index(g, nx, ny, nk);
                        int nc = c + (g.IsDoor(nx, ny) ? 1 + NavGrid.DOOR_COST : 1);

                        if (nc < cost[n])
                        {
                            cost[n] = nc;
                            parent[n] = s;
                            open.Enqueue(n, nc + Heuristic(nx, ny, gx, gy, distance));
                        }
                    }
                }

                Report(stats, expanded, false, touched, false);

                return Partial(g, parent, best, startH - bestH, partialMin, stats);
            }
            finally
            {
                ArrayPool<int>.Shared.Return(cost);
                ArrayPool<int>.Shared.Return(parent);
            }
        }

        private static void Report(PlanStats stats, int expanded, bool hitLimit, bool touched, bool partial)
        {
            if (stats != null)
            {
                stats.Expanded += expanded;
                stats.HitLimit |= hitLimit;
                stats.TouchedEdge |= touched;
                stats.Partial = partial;
            }
        }

        private static List<(int X, int Y, sbyte Z)> Partial(NavGrid g, int[] parent, int best, int gained, int partialMin, PlanStats stats)
        {
            if (partialMin <= 0 || gained < partialMin)
            {
                return null;
            }

            if (stats != null)
            {
                stats.Partial = true;
            }

            return Path(g, parent, best);
        }

        private static int Heuristic(int x, int y, int gx, int gy, int distance) => Math.Max(0, Math.Max(Math.Abs(x - gx), Math.Abs(y - gy)) - distance);

        private static int Index(NavGrid g, int x, int y, int k) => ((y - g.Y0) * g.Width + (x - g.X0)) * NavGrid.MAX_SURFACES + k;

        private static (int X, int Y, int K) Unpack(NavGrid g, int s)
        {
            int k = s % NavGrid.MAX_SURFACES;
            int cell = s / NavGrid.MAX_SURFACES;

            return (g.X0 + cell % g.Width, g.Y0 + cell / g.Width, k);
        }

        private static List<(int, int, sbyte)> Path(NavGrid g, int[] parent, int s)
        {
            var path = new List<(int, int, sbyte)>();

            for (; parent[s] != -1; s = parent[s])
            {
                (int x, int y, int k) = Unpack(g, s);
                path.Add((x, y, g.Z(x, y, k)));
            }

            path.Reverse();

            return path;
        }

        // The grid for a box of the map, read from the map and statics files.
        public static NavGrid Load(int map, int x0, int y0, int width, int height)
        {
            MapLoader maps = Client.Game.UO.FileManager.Maps;
            TileDataLoader tiles = Client.Game.UO.FileManager.TileData;
            maps.SanitizeMapIndex(ref map);

            int mapW = maps.MapsDefaultSize[map, 0], mapH = maps.MapsDefaultSize[map, 1];
            x0 = Math.Clamp(x0, 0, mapW - 1);
            y0 = Math.Clamp(y0, 0, mapH - 1);
            width = Math.Min(width, mapW - x0);
            height = Math.Min(height, mapH - y0);

            var g = new NavGrid(x0, y0, width, height);
            var cell = new List<(sbyte Z, byte Height, bool Surface, bool Solid)>[64];

            for (int i = 0; i < 64; i++)
            {
                cell[i] = new List<(sbyte, byte, bool, bool)>();
            }

            StaticsBlock[] buffer = Array.Empty<StaticsBlock>();
            var landZ = new sbyte[64];
            var landOk = new bool[64];

            for (int bx = x0 >> 3; bx <= (x0 + width - 1) >> 3; bx++)
            {
                for (int by = y0 >> 3; by <= (y0 + height - 1) >> 3; by++)
                {
                    ref IndexMap im = ref maps.GetIndex(map, bx, by);

                    if (!im.IsValid())
                    {
                        continue;
                    }

                    im.MapFile.Seek((long) im.MapAddress, SeekOrigin.Begin);
                    MapBlock block = im.MapFile.Read<MapBlock>();

                    for (int i = 0; i < 64; i++)
                    {
                        ushort id = (ushort) (block.Cells[i].TileID & 0x3FFF);
                        landZ[i] = block.Cells[i].Z;
                        // The "no draw" land tiles under caves and the void are not ground.
                        landOk[i] = !tiles.LandData[id].IsImpassable && id != 0x0002 && !(id >= 0x01AE && id <= 0x01B5) && id != 0x01DB;
                        cell[i].Clear();
                    }

                    if (im.StaticAddress != 0 && im.StaticCount > 0)
                    {
                        if (buffer.Length < im.StaticCount)
                        {
                            buffer = new StaticsBlock[im.StaticCount];
                        }

                        Span<StaticsBlock> statics = buffer.AsSpan(0, (int) im.StaticCount);
                        im.StaticFile.Seek((long) im.StaticAddress, SeekOrigin.Begin);
                        im.StaticFile.Read(MemoryMarshal.AsBytes(statics));

                        foreach (ref StaticsBlock sb in statics)
                        {
                            if (sb.Color == 0 || sb.Color == 0xFFFF || sb.X > 7 || sb.Y > 7)
                            {
                                continue;
                            }

                            ref StaticTiles t = ref tiles.StaticData[sb.Color];

                            // Roofs are surfaces in the tile data, but nobody walks on them; a
                            // gently sloping one would otherwise look like a way over the houses.
                            if (t.IsRoof)
                            {
                                continue;
                            }

                            bool solid = t.IsImpassable;
                            bool surface = (t.IsSurface || t.IsBridge) && !solid;

                            if (solid || surface)
                            {
                                byte h = t.IsBridge ? (byte) (t.Height / 2) : t.Height;
                                cell[(sb.Y << 3) + sb.X].Add((sb.Z, h, surface, solid));
                            }
                        }
                    }

                    for (int i = 0; i < 64; i++)
                    {
                        int x = (bx << 3) + (i & 7), y = (by << 3) + (i >> 3);

                        if (!g.Contains(x, y))
                        {
                            continue;
                        }

                        AddStandable(g, x, y, landOk[i], landZ[i], cell[i]);
                    }
                }
            }

            return g;
        }

        // A place to stand is a surface top with nothing solid, and no other surface, in the
        // person's height above it.
        private static void AddStandable(NavGrid g, int x, int y, bool landOk, sbyte landZ, List<(sbyte Z, byte Height, bool Surface, bool Solid)> objs)
        {
            Span<int> tops = stackalloc int[NavGrid.MAX_SURFACES + 8];
            Span<int> froms = stackalloc int[NavGrid.MAX_SURFACES + 8];
            int n = 0;

            if (landOk)
            {
                froms[n] = landZ - NavGrid.STEP_UP;
                tops[n++] = landZ;
            }

            foreach (var o in objs)
            {
                if (o.Surface && n < tops.Length)
                {
                    froms[n] = o.Z - NavGrid.STEP_ONTO;
                    tops[n++] = o.Z + o.Height;
                }
            }

            tops.Slice(0, n).Sort(froms.Slice(0, n));

            for (int i = 0; i < n; i++)
            {
                int top = tops[i];
                bool clear = true;

                foreach (var o in objs)
                {
                    int bottom = o.Z, objTop = o.Z + Math.Max((int) o.Height, 1);

                    if (o.Surface && objTop == top)
                    {
                        continue; // the surface itself
                    }

                    if (bottom < top + NavGrid.PERSON_HEIGHT && objTop > top + 2)
                    {
                        clear = false;

                        break;
                    }
                }

                if (clear && (i == 0 || top != tops[i - 1]))
                {
                    // One top made by several (the ground and a floor on it): stepped onto from the lowest any allows.
                    int from = froms[i];

                    for (int j = i + 1; j < n && tops[j] == top; j++)
                    {
                        from = Math.Min(from, froms[j]);
                    }

                    g.AddSurface(x, y, top, from);
                }
            }
        }

        // Every tile reachable from the start, for the debug view.
        public static HashSet<(int, int)> Reachable(NavGrid g, int sx, int sy, int sz, int limit = 200_000)
        {
            var seen = new HashSet<(int, int, int)>();
            var tilesSeen = new HashSet<(int, int)>();
            int k0 = g.Surface(sx, sy, sz);

            if (k0 < 0)
            {
                return tilesSeen;
            }

            var queue = new Queue<(int X, int Y, int K)>();
            queue.Enqueue((sx, sy, k0));
            seen.Add((sx, sy, k0));

            while (queue.Count > 0 && seen.Count < limit)
            {
                (int x, int y, int k) = queue.Dequeue();
                tilesSeen.Add((x, y));
                int z = g.Z(x, y, k);

                foreach ((int dx, int dy) in Steps)
                {
                    int nx = x + dx, ny = y + dy;

                    if (!g.Contains(nx, ny))
                    {
                        continue;
                    }

                    int nk = g.StepTo(nx, ny, z);

                    if (nk < 0 || dx != 0 && dy != 0 && (g.StepTo(x + dx, y, z) < 0 || g.StepTo(x, y + dy, z) < 0))
                    {
                        continue;
                    }

                    if (seen.Add((nx, ny, nk)))
                    {
                        queue.Enqueue((nx, ny, nk));
                    }
                }
            }

            return tilesSeen;
        }

        // Every n-th tile of a path, and the last, so the walker has legs it can see.
        public static int NextLeg(List<(int X, int Y, sbyte Z)> path, int from, int px, int py, int reach)
        {
            int best = -1;

            for (int i = from; i < path.Count; i++)
            {
                int d = Math.Max(Math.Abs(path[i].X - px), Math.Abs(path[i].Y - py));

                if (d > reach)
                {
                    break;
                }

                best = i;
            }

            return best;
        }

        // The path indexes to try a leg to, furthest first: the leg's end, every third tile back,
        // and the nearest tile of the path last (a path of one step, or the player beside its
        // end, has only that).
        public static IEnumerable<int> LegTries(int leg, int from)
        {
            for (int i = leg; i >= from; i = i > from && i - 3 < from ? from : i - 3)
            {
                yield return i;
            }
        }

        public static int Tiles(int ax, int ay, int bx, int by) => Math.Max(Math.Abs(ax - bx), Math.Abs(ay - by));

        // The first tile of the path the player has yet to step onto, given the tile of it nearest the
        // player. A plan starts at its first step, not under the player: until that step is taken the
        // nearest tile is itself the next one, and standing on it, the one after.
        public static int NextOnPath(List<(int X, int Y, sbyte Z)> path, int nearest, int px, int py)
        {
            return nearest < path.Count && path[nearest].X == px && path[nearest].Y == py ? nearest + 1 : nearest;
        }

        // The point `hop` tiles (in steps) from the start on the straight line to the goal.
        public static (int X, int Y) HopToward(int sx, int sy, int gx, int gy, int hop)
        {
            int dist = Tiles(sx, sy, gx, gy);

            if (dist <= hop)
            {
                return (gx, gy);
            }

            return (sx + (int) Math.Round((gx - sx) * (double) hop / dist), sy + (int) Math.Round((gy - sy) * (double) hop / dist));
        }

        // Felucca and Trammel keep their dungeons east of x 5120, apart from the land: no walk
        // crosses that line.
        public static bool SameLandmass(int map, int sx, int gx) => map > 1 || sx < 5120 == gx < 5120;

        // Whether the goal can be ruled out before a long search: nowhere within `distance` of it
        // to stand, or every place there lies in a pocket closed off inside the grid. It searches
        // backwards from the goal: when all that can walk to the goal is inside the grid, away
        // from its edge, and the start isn't among it, no route exists (a walled yard, a building
        // the files show no way into, a small island, a dungeon). False when it can't tell.
        public static bool Unreachable(NavGrid g, int sx, int sy, int gx, int gy, int distance, int limit = 60_000)
        {
            if (Tiles(sx, sy, gx, gy) <= distance)
            {
                return false;
            }

            if (!g.Contains(gx - distance, gy - distance) || !g.Contains(gx + distance, gy + distance))
            {
                return false;
            }

            var seen = new HashSet<(int, int, int)>();
            var queue = new Queue<(int X, int Y, int K)>();

            for (int x = gx - distance; x <= gx + distance; x++)
            {
                for (int y = gy - distance; y <= gy + distance; y++)
                {
                    for (int k = 0, n = g.Count(x, y); k < n; k++)
                    {
                        seen.Add((x, y, k));
                        queue.Enqueue((x, y, k));
                    }
                }
            }

            while (queue.Count > 0)
            {
                if (seen.Count > limit)
                {
                    return false;
                }

                (int x, int y, int k) = queue.Dequeue();

                foreach ((int dx, int dy) in Steps)
                {
                    // A step from (px, py) onto this tile and surface.
                    int px = x - dx, py = y - dy;

                    if (!g.Contains(px, py))
                    {
                        return false; // it goes on past the grid
                    }

                    for (int pk = 0, n = g.Count(px, py); pk < n; pk++)
                    {
                        int pz = g.Z(px, py, pk);

                        if (g.StepTo(x, y, pz) != k || dx != 0 && dy != 0 && (g.StepTo(px + dx, py, pz) < 0 || g.StepTo(px, py + dy, pz) < 0))
                        {
                            continue;
                        }

                        if (px == sx && py == sy)
                        {
                            return false;
                        }

                        if (seen.Add((px, py, pk)))
                        {
                            queue.Enqueue((px, py, pk));
                        }
                    }
                }
            }

            return true;
        }

        // A box round two points with `margin` tiles to spare, no wider or taller than maxSide.
        public static (int X0, int Y0, int W, int H) Box(int ax, int ay, int bx, int by, int margin, int maxSide)
        {
            int mx = Math.Max(0, Math.Min(margin, (maxSide - 1 - Math.Abs(ax - bx)) / 2));
            int my = Math.Max(0, Math.Min(margin, (maxSide - 1 - Math.Abs(ay - by)) / 2));

            return (Math.Min(ax, bx) - mx, Math.Min(ay, by) - my, Math.Abs(ax - bx) + 2 * mx + 1, Math.Abs(ay - by) + 2 * my + 1);
        }

        // Plans a trip, or the next hop of a long one. `load` gives the grid for a box (x0, y0, w,
        // h) with what travel knows applied. A trip up to `Direct` tiles is planned whole, in a box
        // round the start and the goal, widened when the way round something leaves it. A longer
        // one goes a hop at a time: to a point `Hop` tiles along the line to the goal, or the
        // nearest place to it the grid allows, and is planned again from there.
        public static TripPlan PlanTrip(Func<int, int, int, int, NavGrid> load, int sx, int sy, int sz, int gx, int gy, int distance, TripLimits lim)
        {
            var plan = new TripPlan();
            var clock = Stopwatch.StartNew();
            int dist = Tiles(sx, sy, gx, gy);

            try
            {
                // A goal in a closed pocket would cost a search of every tile the start can reach.
                if (distance <= 16 && dist > distance && dist > 24)
                {
                    int r = lim.Pocket;
                    NavGrid around = Timed(plan, () => load(gx - r, gy - r, 2 * r + 1, 2 * r + 1));

                    if (around != null && around.Contains(sx, sy) && around.Count(sx, sy) == 0)
                    {
                        around.AddSurface(sx, sy, sz); // as Plan does: the start is somewhere to stand
                    }

                    if (around != null && Unreachable(around, sx, sy, gx, gy, distance))
                    {
                        plan.Failure = "no route";
                        plan.Why = "nowhere to stand there, or closed off";

                        return plan;
                    }
                }

                plan.Hop = dist > lim.Direct;
                (int tx, int ty) = plan.Hop ? HopToward(sx, sy, gx, gy, lim.Hop) : (gx, gy);
                int reach = plan.Hop ? HOP_SLACK : distance;
                int span = Tiles(sx, sy, tx, ty);
                (int, int, int, int) last = default;

                foreach (int widen in new[] { 1, 3, 6 })
                {
                    (int x0, int y0, int w, int h) box = Box(sx, sy, tx, ty, (24 + span / 4) * widen, lim.MaxBox);

                    if (box == last)
                    {
                        break; // as wide as it can be
                    }

                    last = box;
                    NavGrid g = Timed(plan, () => load(box.x0, box.y0, box.w, box.h));

                    if (g == null)
                    {
                        break;
                    }

                    var stats = new PlanStats();
                    plan.Path = Plan(g, sx, sy, sz, tx, ty, reach, lim.MaxNodes - plan.Stats.Expanded, plan.Hop ? lim.Hop / 3 : 0, stats);
                    plan.Stats.Expanded += stats.Expanded;
                    plan.Stats.HitLimit |= stats.HitLimit;
                    plan.Stats.Partial = stats.Partial;

                    if (plan.Path != null || !stats.TouchedEdge && !stats.HitLimit || plan.Stats.Expanded >= lim.MaxNodes)
                    {
                        break; // found, searched all there is, or out of budget
                    }
                }

                if (plan.Path != null && plan.Hop)
                {
                    // A hop has to bring the goal nearer, or it is going nowhere.
                    (int ex, int ey, _) = plan.Path.Count > 0 ? plan.Path[^1] : (sx, sy, (sbyte) 0);

                    if (Tiles(ex, ey, gx, gy) > dist - lim.Hop / 4)
                    {
                        plan.Path = null;
                        plan.Why = "the way there leads no nearer";
                    }
                }

                if (plan.Path == null)
                {
                    plan.Failure = "no route";
                    plan.Why ??= plan.Stats.HitLimit ? "too long a search" : "no way there on the map";
                }

                return plan;
            }
            finally
            {
                plan.TotalMs = clock.Elapsed.TotalMilliseconds;
            }
        }

        private const int HOP_SLACK = 4; // a hop ends within this many tiles of its point

        private static NavGrid Timed(TripPlan plan, Func<NavGrid> load)
        {
            long t0 = Stopwatch.GetTimestamp();
            NavGrid g = load();
            plan.LoadMs += Stopwatch.GetElapsedTime(t0).TotalMilliseconds;
            plan.Grids++;

            return g;
        }
    }

    // Whether a walk is getting anywhere: the goal nearer than it has been, or the player further
    // along the path than before. The way round a mountain can lead away from the goal for
    // minutes (into Cove, round the north of the range west of it); only the path shows progress
    // there.
    internal sealed class TravelProgress
    {
        private int _left, _index;

        public void Restart(int left)
        {
            _left = left;
            _index = 0;
        }

        // True when the walk has got further than before: nearer the goal, or along the path.
        public bool Note(int left, int index)
        {
            bool further = left < _left || index > _index;
            _left = Math.Min(_left, left);
            _index = Math.Max(_index, index);

            return further;
        }
    }

    // The tiles the server has sent a step back from, each left alone until it lapses. A refusal
    // while one is still running starts its time again.
    internal sealed class RefusedTiles
    {
        private readonly Dictionary<(int, int), uint> _until = new Dictionary<(int, int), uint>();
        private readonly uint _ms;

        public RefusedTiles(uint ms)
        {
            _ms = ms;
        }

        public int Count => _until.Count;

        public void Refuse(int x, int y, uint now)
        {
            _until[(x, y)] = now + _ms;
        }

        public bool Refused(int x, int y, uint now)
        {
            if (_until.Count == 0 || !_until.TryGetValue((x, y), out uint until))
            {
                return false;
            }

            if (now < until)
            {
                return true;
            }

            _until.Remove((x, y));

            return false;
        }
    }

    internal sealed class TripLimits
    {
        public int MaxBox = 900;        // the largest grid side
        public int Direct = 360;        // trips up to this long are planned whole
        public int Hop = 300;           // longer ones a hop this long at a time
        public int MaxNodes = 400_000;  // per plan, shared by its searches: bounds the time it takes
        public int Pocket = 96;         // half the side of the box searched round the goal for a closed pocket
    }

    internal sealed class TripPlan
    {
        public List<(int X, int Y, sbyte Z)> Path;
        public bool Hop;        // the path ends at a stop on the way: plan again from there
        public string Failure;  // set when there is no path
        public string Why;
        public readonly PlanStats Stats = new PlanStats();
        public int Grids;
        public double LoadMs, TotalMs;
    }

    // What travel has seen that the map files don't show, kept while the client runs: doors
    // (each place one has stood: closed in its doorway, or swung open beside it), doors found
    // locked, and houses and other multis with the tiles they cover. The client forgets items
    // out of its view; these stay, so a plan made far from them still knows them.
    internal sealed class TravelMemory
    {
        private readonly Dictionary<(int Map, int X, int Y), uint> _doorTiles = new Dictionary<(int, int, int), uint>();
        private readonly Dictionary<uint, (int Map, int X, int Y)> _doorAt = new Dictionary<uint, (int, int, int)>();
        private readonly HashSet<uint> _locked = new HashSet<uint>();
        private readonly Dictionary<uint, HouseSeen> _houses = new Dictionary<uint, HouseSeen>();

        private sealed class HouseSeen
        {
            public int Map, MinX, MinY, MaxX, MaxY, Parts;
            public (int X, int Y)[] Tiles, Walls;
        }

        public int Doors => _doorAt.Count;
        public int LockedDoors => _locked.Count;
        public int Houses => _houses.Count;

        public void SeeDoor(int map, uint serial, int x, int y)
        {
            // A locked door seen somewhere else has been opened since.
            if (_doorAt.TryGetValue(serial, out var was) && was != (map, x, y))
            {
                _locked.Remove(serial);
            }

            _doorAt[serial] = (map, x, y);
            _doorTiles[(map, x, y)] = serial;
        }

        public void Lock(uint serial) => _locked.Add(serial);

        public bool IsLocked(uint serial) => _locked.Contains(serial);

        public bool KnowsHouse(uint serial, int parts) => _houses.TryGetValue(serial, out HouseSeen h) && h.Parts == parts;

        // A house: the tiles its parts cover, and of those the walls (impassable parts on its
        // ground floor).
        public void SeeHouse(int map, uint serial, int minX, int minY, int maxX, int maxY, (int X, int Y)[] tiles, int parts,
            (int X, int Y)[] walls = null)
        {
            _houses[serial] = new HouseSeen
            {
                Map = map, MinX = minX, MinY = minY, MaxX = maxX, MaxY = maxY, Tiles = tiles, Walls = walls ?? Array.Empty<(int, int)>(),
                Parts = parts
            };
        }

        public void ForgetHouse(uint serial) => _houses.Remove(serial);

        // Houses whose bounds come within `range` of (x, y): to notice one that has gone.
        public List<uint> HousesNear(int map, int x, int y, int range)
        {
            var near = new List<uint>();

            foreach ((uint serial, HouseSeen h) in _houses)
            {
                if (h.Map == map && x >= h.MinX - range && x <= h.MaxX + range && y >= h.MinY - range && y <= h.MaxY + range)
                {
                    near.Add(serial);
                }
            }

            return near;
        }

        public bool InHouse(int map, int x, int y)
        {
            foreach (HouseSeen h in _houses.Values)
            {
                if (h.Map == map && Inside(h, x, y))
                {
                    return true;
                }
            }

            return false;
        }

        private static bool Inside(HouseSeen h, int x, int y) => x >= h.MinX && x <= h.MaxX && y >= h.MinY && y <= h.MaxY;

        // Marks the doors in a grid and blocks locked doors and houses. Of a house the start or the
        // goal lies in, only the walls are blocked, so the way in or out is through its door.
        public void Apply(NavGrid g, int map, int sx, int sy, int gx, int gy)
        {
            foreach (((int m, int x, int y), uint serial) in _doorTiles)
            {
                if (m != map || !g.Contains(x, y))
                {
                    continue;
                }

                if (_locked.Contains(serial))
                {
                    g.Block(x, y);
                }
                else
                {
                    g.MarkDoor(x, y);
                }
            }

            foreach (HouseSeen h in _houses.Values)
            {
                if (h.Map != map || h.MaxX < g.X0 || h.MinX >= g.X0 + g.Width || h.MaxY < g.Y0 || h.MinY >= g.Y0 + g.Height)
                {
                    continue;
                }

                foreach ((int x, int y) in Inside(h, sx, sy) || Inside(h, gx, gy) ? h.Walls : h.Tiles)
                {
                    g.Block(x, y);
                }
            }
        }
    }
}
