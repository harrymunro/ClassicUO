// SPDX-License-Identifier: BSD-2-Clause

using System;
using System.Buffers;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using ClassicUO.Assets;

namespace ClassicUO.Agent
{
    // Long walks. The client's own pathfinder only sees the chunks it has loaded (about a
    // screen around the player), so a walk across town is planned here, coarsely, over the
    // raw map and static tiles, and then walked a leg at a time with that pathfinder, which
    // deals with creatures, items and the exact movement rules. Doors and other items are not
    // in the map files, so a doorway counts as open; if it isn't, the walker gets stuck there,
    // marks the spot and plans again.
    internal sealed class NavGrid
    {
        public const int MAX_SURFACES = 3;
        public const int PERSON_HEIGHT = 16;
        public const int STEP_UP = 8;     // generous: stairs and slopes; the local pathfinder is exact
        public const int STEP_DOWN = 20;

        public readonly int X0, Y0, Width, Height;

        // Up to three places to stand per tile (the z of the surface top), lowest first.
        private readonly sbyte[] _z;
        private readonly byte[] _count;

        public NavGrid(int x0, int y0, int width, int height)
        {
            X0 = x0;
            Y0 = y0;
            Width = width;
            Height = height;
            _z = new sbyte[width * height * MAX_SURFACES];
            _count = new byte[width * height];
        }

        public bool Contains(int x, int y) => x >= X0 && y >= Y0 && x < X0 + Width && y < Y0 + Height;

        public int Count(int x, int y) => Contains(x, y) ? _count[(y - Y0) * Width + (x - X0)] : 0;

        public sbyte Z(int x, int y, int k) => _z[((y - Y0) * Width + (x - X0)) * MAX_SURFACES + k];

        public void AddSurface(int x, int y, int z)
        {
            int i = (y - Y0) * Width + (x - X0);
            int n = _count[i];

            if (n >= MAX_SURFACES)
            {
                return;
            }

            _z[i * MAX_SURFACES + n] = (sbyte) Math.Clamp(z, sbyte.MinValue, sbyte.MaxValue);
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

                if (dz <= STEP_UP && -dz <= STEP_DOWN && Math.Abs(dz) < bestDz)
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

    internal static class AgentNav
    {
        private static readonly (int Dx, int Dy)[] Steps = { (0, -1), (1, 0), (0, 1), (-1, 0), (1, -1), (1, 1), (-1, 1), (-1, -1) };

        // A* over the grid, one tile per step (diagonals cost the same in UO), never cutting a
        // corner. Returns the tiles from start (excluded) to the first tile within `distance`
        // of the goal, or null.
        public static List<(int X, int Y, sbyte Z)> Plan(NavGrid g, int sx, int sy, int sz, int gx, int gy, int distance, int maxNodes = 400_000)
        {
            int startK = g.Surface(sx, sy, sz);

            if (startK < 0)
            {
                // Standing somewhere the map files don't know (a house floor, a boat): plan from the
                // ground nearby.
                g.AddSurface(sx, sy, sz);
                startK = g.Surface(sx, sy, sz);
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
                open.Enqueue(start, Heuristic(sx, sy, gx, gy, distance));
                int expanded = 0;

                while (open.TryDequeue(out int s, out int priority))
                {
                    (int x, int y, int k) = Unpack(g, s);
                    int c = cost[s];

                    if (priority - Heuristic(x, y, gx, gy, distance) > c)
                    {
                        continue; // a stale entry
                    }

                    if (Math.Max(Math.Abs(x - gx), Math.Abs(y - gy)) <= distance)
                    {
                        return Path(g, parent, s);
                    }

                    if (++expanded > maxNodes)
                    {
                        return null;
                    }

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

                        int n = Index(g, nx, ny, nk);

                        if (c + 1 < cost[n])
                        {
                            cost[n] = c + 1;
                            parent[n] = s;
                            open.Enqueue(n, c + 1 + Heuristic(nx, ny, gx, gy, distance));
                        }
                    }
                }

                return null;
            }
            finally
            {
                ArrayPool<int>.Shared.Return(cost);
                ArrayPool<int>.Shared.Return(parent);
            }
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
            int n = 0;

            if (landOk)
            {
                tops[n++] = landZ;
            }

            foreach (var o in objs)
            {
                if (o.Surface && n < tops.Length)
                {
                    tops[n++] = o.Z + o.Height;
                }
            }

            tops.Slice(0, n).Sort();

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
                    g.AddSurface(x, y, top);
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
    }
}
