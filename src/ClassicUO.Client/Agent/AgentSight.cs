// SPDX-License-Identifier: BSD-2-Clause

using System;
using System.Collections.Generic;
using ClassicUO.Assets;
using ClassicUO.Game;
using ClassicUO.Game.GameObjects;
using ClassicUO.Game.Map;

namespace ClassicUO.Agent
{
    // One thing standing on a tile, as the sight rule sees it.
    internal struct SightPiece
    {
        public int Z;
        public int Height;  // the tile data height, halved for a bridge (ModernUO's CalcHeight)
        public bool Blocks; // Window or NoShoot in the tile data
        public bool Door;

        public SightPiece(int z, int height, bool blocks, bool door = false)
        {
            Z = z;
            Height = height;
            Blocks = blocks;
            Door = door;
        }
    }

    // What the sight rule reads of the map. The client's map is one; tests build their own tiles.
    internal interface ISightTiles
    {
        // The land tile at (x, y): its id and height.
        void Land(int x, int y, out ushort id, out int z);

        // Statics and house (multi) pieces at (x, y), added to `statics`; items lying on the ground
        // there (not houses), added to `items`.
        void Pieces(int x, int y, List<SightPiece> statics, List<SightPiece> items);
    }

    // Whose line of sight the client follows (the `agent_sight_rule` setting). ModernUO's
    // Map.LineOfSight came from RunUO's, and they differ in two places (cuo-2ad):
    //  - ModernUO never lets a door on the tile the line starts from block, nor any item on the tile
    //    it ends on (the line runs from the lower point to the higher, so either may be the viewer's):
    //    its fix for doors "bugging monsters". RunUO has no such clause.
    //  - RunUO up to 2.2 takes heights exactly; from 2.3 on, and in ModernUO, a piece or land whose
    //    base is one above the line still blocks, and one above the target still counts as at it.
    internal enum SightRule
    {
        ModernUO, // the local test server
        RunUO,    // RunUO 2.3 and later: the one-unit allowance, no door clause
        RunUO22   // RunUO 2.0 to 2.2: neither
    }

    // Whether one point can see another, by the server's rule: Map.LineOfSight, followed line for
    // line so the client and the server agree. A straight 3D line is drawn between the points, a tile
    // at a time. Land that rises through it blocks, and so does any static, house piece or item
    // flagged Window or NoShoot that spans the line's height on a tile, except on the target's own
    // tile, where only something wholly above or below the target blocks. Further than 25 tiles is
    // out of sight. A mobile's eye, and the point it is seen at, are 14 above where it stands.
    internal static class AgentSight
    {
        public const int MAX_DISTANCE = 25; // Map.MaxLOSDistance
        public const int EYE = 14;          // Map.LineOfSight(Mobile, Mobile): both ends at z + 14
        private const int INVALID_LAND = 0x244; // Map.InvalidLandTiles: blocks unless something stands on it

        [ThreadStatic] private static List<(int X, int Y, int Z)> _path;
        [ThreadStatic] private static List<SightPiece> _statics, _items;

        public static string Name(this SightRule rule) => rule.ToString().ToLowerInvariant();

        // The setting's value ("modernuo", "runuo", "runuo22"), or ModernUO for none.
        public static bool TryParseRule(string value, out SightRule rule)
        {
            rule = SightRule.ModernUO;

            return string.IsNullOrWhiteSpace(value) || AgentModes.TryParse(value.Trim(), out rule);
        }

        // One mobile seeing another: both at eye height.
        public static bool MobileSeesMobile(ISightTiles map, int x, int y, int z, int tx, int ty, int tz, SightRule rule = SightRule.ModernUO) =>
            InSight(map, x, y, z + EYE, tx, ty, tz + EYE, rule);

        // A mobile aiming at a ground tile (a field, a summon): the server takes the highest corner of
        // the land there, plus one.
        public static bool MobileSeesGround(ISightTiles map, int x, int y, int z, int tx, int ty, SightRule rule = SightRule.ModernUO) =>
            InSight(map, x, y, z + EYE, tx, ty, LandTop(map, tx, ty) + 1, rule);

        // Map.GetAverageZ's lowest and highest corners: the land tile and its neighbours south, east and
        // south-east.
        public static void LandSpan(ISightTiles map, int x, int y, out int low, out int top)
        {
            map.Land(x, y, out _, out int zTop);
            map.Land(x, y + 1, out _, out int zLeft);
            map.Land(x + 1, y, out _, out int zRight);
            map.Land(x + 1, y + 1, out _, out int zBottom);
            low = Math.Min(Math.Min(zTop, zLeft), Math.Min(zRight, zBottom));
            top = Math.Max(Math.Max(zTop, zLeft), Math.Max(zRight, zBottom));
        }

        public static int LandTop(ISightTiles map, int x, int y)
        {
            LandSpan(map, x, y, out _, out int top);

            return top;
        }

        // LandTile.Ignored: the "no draw" land under caves and the void.
        private static bool IgnoredLand(int id) => id == 2 || id == 0x1DB || id >= 0x1AE && id <= 0x1B5;

        // Utility.NumberBetween.
        private static bool Between(double num, int bound1, int bound2, double allowance)
        {
            if (bound1 > bound2)
            {
                (bound1, bound2) = (bound2, bound1);
            }

            return num < bound2 + allowance && num > bound1 - allowance;
        }

        public static bool InSight(ISightTiles map, int ox, int oy, int oz, int dx, int dy, int dz, SightRule rule = SightRule.ModernUO)
        {
            if (Math.Abs(ox - dx) > MAX_DISTANCE || Math.Abs(oy - dy) > MAX_DISTANCE)
            {
                return false;
            }

            if (ox == dx && oy == dy && oz == dz)
            {
                return true;
            }

            (int X, int Y, int Z) end = (dx, dy, dz);

            // The line is always drawn from the lower point to the higher (by x, then y, then z).
            if (ox > dx || ox == dx && oy > dy || ox == dx && oy == dy && oz > dz)
            {
                (ox, dx) = (dx, ox);
                (oy, dy) = (dy, oy);
                (oz, dz) = (dz, oz);
            }

            List<(int X, int Y, int Z)> path = _path ??= new List<(int, int, int)>();
            path.Clear();

            int xd = dx - ox, yd = dy - oy, zd = dz - oz;
            double zslp = Math.Sqrt(xd * xd + yd * yd);
            double sq3d = zd != 0 ? Math.Sqrt(zslp * zslp + zd * zd) : zslp;
            double rise = yd / sq3d, run = xd / sq3d;
            zslp = zd / sq3d;

            double x = ox, y = oy, z = oz;

            while (Between(x, dx, ox, 0.5) && Between(y, dy, oy, 0.5) && Between(z, dz, oz, 0.5))
            {
                int ix = (int) Math.Round(x), iy = (int) Math.Round(y), iz = (int) Math.Round(z);

                if (path.Count == 0 || path[path.Count - 1] != (ix, iy, iz))
                {
                    path.Add((ix, iy, iz));
                }

                x += run;
                y += rise;
                z += zslp;
            }

            if (path.Count == 0)
            {
                return true;
            }

            if (path[path.Count - 1] != (dx, dy, dz))
            {
                path.Add((dx, dy, dz));
            }

            // The height allowance: what stands one above the line, or one above the target on its
            // own tile, counts as at it. RunUO 2.2 and before had none.
            int slack = rule == SightRule.RunUO22 ? 0 : 1;
            int endTop = end.Z + slack;
            List<SightPiece> statics = _statics ??= new List<SightPiece>();
            List<SightPiece> items = _items ??= new List<SightPiece>();

            foreach ((int px, int py, int pz) in path)
            {
                int pointTop = pz + slack;
                bool atEnd = px == end.X && py == end.Y;

                map.Land(px, py, out ushort landId, out _);
                LandSpan(map, px, py, out int landZ, out int landTop);

                if (landZ <= pointTop && landTop >= pz && (!atEnd || landZ > endTop || landTop < end.Z) && !IgnoredLand(landId))
                {
                    return false;
                }

                statics.Clear();
                items.Clear();
                map.Pieces(px, py, statics, items);

                foreach (SightPiece t in statics)
                {
                    if (t.Blocks && t.Z <= pointTop && t.Z + t.Height >= pz && (!atEnd || t.Z > endTop || t.Z + t.Height < end.Z))
                    {
                        return false;
                    }
                }

                // A tile of the invalid land with nothing on it blocks.
                if (landId == INVALID_LAND && statics.Count == 0 && items.Count == 0)
                {
                    return false;
                }

                foreach (SightPiece it in items)
                {
                    if (!it.Blocks || it.Z > pointTop || it.Z + it.Height < pz)
                    {
                        continue;
                    }

                    // As ModernUO has it, and RunUO doesn't: a door where the line starts, and anything
                    // where it ends (the higher point, which may be either mobile), never blocks.
                    if (rule == SightRule.ModernUO && (it.Door && px == ox && py == oy || px == dx && py == dy))
                    {
                        continue;
                    }

                    if (!atEnd || it.Z > endTop || it.Z + it.Height < end.Z)
                    {
                        return false;
                    }
                }
            }

            return true;
        }
    }

    // The sight rule over the client's own map: land and statics from the loaded chunks (loading any
    // that are missing), house pieces from the multis, and the items on the ground.
    internal sealed class ClientSightTiles : ISightTiles
    {
        private const ushort LOS_BLOCKER = 0x21A2; // LOSBlocker, ModernUO's and RunUO's: tile data says nothing; the server makes it NoShoot, 20 high

        private readonly World _world;

        public ClientSightTiles(World world)
        {
            _world = world;
        }

        public void Land(int x, int y, out ushort id, out int z)
        {
            id = 0;
            z = 0;
            Chunk chunk = _world.Map?.GetChunk(x, y);

            for (GameObject o = chunk?.GetHeadObject(x & 7, y & 7); o != null; o = o.TNext)
            {
                if (o is Land land)
                {
                    id = land.OriginalGraphic;
                    z = land.Z;

                    return;
                }
            }
        }

        public void Pieces(int x, int y, List<SightPiece> statics, List<SightPiece> items)
        {
            Chunk chunk = _world.Map?.GetChunk(x, y);
            TileDataLoader tiles = Client.Game.UO.FileManager.TileData;

            for (GameObject o = chunk?.GetHeadObject(x & 7, y & 7); o != null; o = o.TNext)
            {
                switch (o)
                {
                    case Static s:
                        statics.Add(Piece(tiles, s.OriginalGraphic, s.Z));

                        break;

                    case Multi m:
                        statics.Add(Piece(tiles, m.Graphic, m.Z));

                        break;

                    case Item it when !it.IsMulti && it.OnGround && !it.IsDestroyed:
                        items.Add(Piece(tiles, it.Graphic, it.Z));

                        break;
                }
            }
        }

        private static SightPiece Piece(TileDataLoader tiles, ushort graphic, int z)
        {
            if (graphic == LOS_BLOCKER)
            {
                return new SightPiece(z, 20, true);
            }

            if (graphic >= tiles.StaticData.Length)
            {
                return new SightPiece(z, 0, false);
            }

            ref StaticTiles t = ref tiles.StaticData[graphic];
            int height = t.IsBridge ? t.Height / 2 : t.Height;

            return new SightPiece(z, height, t.IsWindow || t.IsNoShoot, t.IsDoor);
        }
    }
}
