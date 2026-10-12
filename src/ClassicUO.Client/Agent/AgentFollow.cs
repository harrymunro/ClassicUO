// SPDX-License-Identifier: BSD-2-Clause

using System;
using System.Collections.Generic;
using ClassicUO.Game;
using ClassicUO.Game.Data;
using ClassicUO.Game.GameObjects;

namespace ClassicUO.Agent
{
    // The pace of a walk, and following: the decisions, apart from the world, so they can be tested.
    internal static class AgentPace
    {
        public const int DANGER_RANGE = 12;  // a hostile monster in war mode this near is danger
        public const uint CALM_MS = 3000;    // back to a walk this long after the danger was last seen
        public const int CATCH_UP = 4;       // a follower further behind than this runs until it is back with its target
        public const int VIEW = 18;          // a target further off than this is lost
        public const int MAX_DISTANCE = 8;   // the most a follower can be told to keep within
        public const uint MAKE_WAY_MS = 1500; // a target standing still this long, facing its follower a step away, wants by

        // Whether a trip or a follow steps at a run now. Asked to run, always. Asked to walk, while
        // there is danger (a fight or a run of its own, something hostile near) and until CALM_MS
        // after it was last seen.
        public static bool Runs(bool walk, bool fighting, bool dangerNear, uint msSinceDanger) =>
            !walk || fighting || dangerNear || msSinceDanger < CALM_MS;

        // A follower's next step, `tiles` from its target and told to keep within `n`: whether to
        // move, and whether at a run. At a walk it runs in danger (`hurry`: Runs, above) and to
        // catch up: from more than CATCH_UP tiles behind (two more than its place, when it keeps
        // further back than 2) until it is a step from its place again. Walking behind someone who
        // walks it is n or n + 1 behind as it goes, and both walk as fast: without the run, a
        // follower that lost a few tiles round a tree stayed that far back for the rest of the walk.
        public static (bool Move, bool Run, bool CatchUp) Follow(int tiles, int n, bool walk, bool hurry, bool catchingUp)
        {
            bool catchUp = tiles > Math.Max(CATCH_UP, n + 2) || catchingUp && tiles > n + 1;

            return (tiles > n, !walk || hurry || catchUp, catchUp);
        }

        // Whether a follower at (px, py), a step from its target, stands in its way: the target has
        // stood still a moment (`stillMs`) facing the follower's tile. A walker whose way on is blocked
        // turns to face who blocks it (AgentController.FaceWhoeverIsInTheWay), as people do in a
        // doorway: a leader leaving an inn with the party round it had a companion on its next tile,
        // waited for it, and gave up with "no way there" (cuo-0jz).
        public static bool InTheWay(int px, int py, int tx, int ty, Direction facing, uint stillMs) =>
            stillMs >= MAKE_WAY_MS && AgentNav.Tiles(px, py, tx, ty) == 1 && Step(facing) == (px - tx, py - ty);

        // The tiles ahead of someone at (x, y) facing `d`, the nearest first: the way a follower that
        // makes way keeps off.
        public static IEnumerable<(int X, int Y)> Ahead(int x, int y, Direction d, int n)
        {
            (int dx, int dy) = Step(d);

            for (int i = 1; i <= n; i++)
            {
                yield return (x + i * dx, y + i * dy);
            }
        }

        public static (int Dx, int Dy) Step(Direction d) => (d & Direction.Mask) switch
        {
            Direction.North => (0, -1),
            Direction.Right => (1, -1),
            Direction.East => (1, 0),
            Direction.Down => (1, 1),
            Direction.South => (0, 1),
            Direction.Left => (-1, 1),
            Direction.West => (-1, 0),
            _ => (-1, -1)
        };

        // Where a follower at (px, py) stands: the tiles one can stand on within n of the target
        // (`standable`), without the target's own and those taken by someone else, the nearest to the
        // follower first. Of two as near, the straighter line; then by `salt` (the follower's
        // serial) and the side of the target the tile is on, so that a follower keeps to one side as
        // they walk, and two followers on one tile don't both step to the same one.
        public static List<(int X, int Y, sbyte Z)> Spots(IEnumerable<(int X, int Y, sbyte Z)> standable, int px, int py, int tx, int ty, int n,
            ICollection<(int, int)> taken, int salt)
        {
            var spots = new List<(int X, int Y, sbyte Z)>();

            foreach ((int x, int y, sbyte z) in standable)
            {
                if ((x != tx || y != ty) && AgentNav.Tiles(x, y, tx, ty) <= n && !taken.Contains((x, y)))
                {
                    spots.Add((x, y, z));
                }
            }

            long Key((int X, int Y, sbyte Z) s)
            {
                int dx = s.X - px, dy = s.Y - py;
                uint mix = unchecked((uint) ((s.X - tx) * 73856093) ^ (uint) ((s.Y - ty) * 19349663) ^ (uint) (salt * 83492791));
                mix = unchecked((mix ^ mix >> 16) * 0x85EBCA6B);
                mix = unchecked((mix ^ mix >> 13) * 0xC2B2AE35);
                mix ^= mix >> 16;

                return ((long) AgentNav.Tiles(s.X, s.Y, px, py) << 48) | ((long) Math.Min(dx * dx + dy * dy, 0xFFFF) << 32) | mix;
            }

            spots.Sort((a, b) => Key(a).CompareTo(Key(b)));

            return spots;
        }
    }

    // Following: keeping within a few tiles of another character as it moves, at a walk beside it
    // or a run to catch up. And danger, which makes a trip or a follow at a walk run.
    internal sealed partial class AgentController
    {
        private const uint FOLLOW_STEP_MS = 200;
        private const uint FOLLOW_RETRY_MS = 8000;   // no way to the target: this long before looking again (a search that fails is a slow one)
        private const uint FOLLOW_REPLAN_MS = 1000;
        private const int FOLLOW_REPLAN_TILES = 8;   // a trip to where the target was is planned again once it is this far from there

        private uint _followTarget, _nextFollowStep, _followFailedAt, _followPlannedAt, _dangerAt, _followTargetStill;
        private int _followDistance, _followSeenX, _followSeenY;
        private bool _followWalk, _followManual, _followRuns, _followCatchUp, _followTrip, _followHasSpot, _followMadeWay;
        private (int X, int Y) _followSpot, _followTargetWas, _followTargetAt;
        private string _followName = string.Empty;
        private readonly HashSet<(int, int)> _followTaken = new HashSet<(int, int)>();

        public bool Following => _followTarget != 0;
        public uint FollowTarget => _followTarget;
        public string FollowName => _followName;
        public string FollowState { get; private set; } = string.Empty;   // "following", "waiting" (a fight, the player's hands), "lost"
        public string FollowWhy { get; private set; } = string.Empty;     // in view but with no way to it
        public string FollowPace => _followWalk ? "walk" : "run";
        public int FollowWithin => _followDistance;
        public bool FollowRunning => _followRuns;
        public (int X, int Y) FollowSeenAt => (_followSeenX, _followSeenY);

        // Tiles to the target, or -1 when the client doesn't have it.
        public int FollowTiles => _world.Mobiles.Get(_followTarget) is Mobile t && !t.IsDestroyed ? t.Distance : -1;

        // Danger, to a walk: the agent fighting, fleeing or stepping back, a hostile monster in war
        // mode within DANGER_RANGE tiles, or anything attacking the player. True while a walk should
        // run for it, which is until CALM_MS after it was last seen.
        private bool Hurry(uint now)
        {
            PlayerMobile p = _world.Player;
            bool fighting = _engaged != 0 || Fleeing || Kiting, near = false;

            if (!fighting)
            {
                foreach (Mobile m in _world.Mobiles.Values)
                {
                    if (m != p && !m.IsDead && (m.Distance <= AgentPace.DANGER_RANGE && m.InWarMode && IsMonsterTarget(m) || IsAttackingMe(m)))
                    {
                        near = true;

                        break;
                    }
                }
            }

            if (fighting || near)
            {
                _dangerAt = now;
            }

            return AgentPace.Runs(true, fighting, near, _dangerAt == 0 ? uint.MaxValue : now - _dangerAt);
        }

        private (string, string) StartFollow(uint target, int distance, bool walk, bool manual)
        {
            if (target == 0 || target == uint.MaxValue || target == _world.Player.Serial || !SerialHelper.IsMobile(target))
            {
                return ("failed", "no one to follow");
            }

            Mobile t = _world.Mobiles.Get(target);
            bool seen = t != null && !t.IsDestroyed && t.Distance <= AgentPace.VIEW;

            // Asked again of the one it follows: the distance and the pace may have changed, no more.
            if (target != _followTarget)
            {
                // A follow is the one walk going: it ends a trip and an errand, as a new trip does.
                Errands.Cancel();
                StopFollow();
                StopTravel("stopped");
                TravelState = string.Empty;
                _followTarget = target;
                _followCatchUp = _followTrip = _followHasSpot = false;
                _followFailedAt = _nextFollowStep = 0;
                _followName = t?.Name?.Trim() ?? string.Empty;
                (_followSeenX, _followSeenY) = t != null ? (t.X, t.Y) : (0, 0);
                _followRuns = !walk;
                FollowWhy = string.Empty;
                FollowState = seen ? "following" : "lost";
                Journal.AddAgentEvent(seen ? $"following {FollowLabel}" : $"to follow {FollowLabel}, who is not in view");
            }

            _followDistance = Math.Clamp(distance, 1, AgentPace.MAX_DISTANCE);
            _followWalk = walk;
            _followManual = manual;

            return ("done", seen ? $"{t.Distance} tiles away" : "lost: not in view");
        }

        private string FollowLabel => _followName.Length != 0 ? _followName : $"0x{_followTarget:X8}";

        public void StopFollow()
        {
            if (_followTarget == 0)
            {
                return;
            }

            Journal.AddAgentEvent($"no longer following {FollowLabel}");
            StopFollowWalk();
            _followTarget = 0;
            _followRuns = _followCatchUp = false;
            FollowState = FollowWhy = string.Empty;
            TravelState = string.Empty; // a trip made for the follow was never the brain's
        }

        // Stands still: the follow's own walk, and a trip it had to make, end here.
        private void StopFollowWalk()
        {
            if (_followTrip)
            {
                StopTravel("stopped");
                _followTrip = false;
            }

            if (_agentWalking && _pacedWalk && _world.Player.Pathfinder.AutoWalking)
            {
                _world.Player.Pathfinder.StopAutoWalk();
            }

            _followHasSpot = false;
        }

        private void UpdateFollow(uint now)
        {
            if (_followTarget == 0 || now < _nextFollowStep)
            {
                return;
            }

            _nextFollowStep = now + FOLLOW_STEP_MS;
            PlayerMobile p = _world.Player;
            Mobile t = _world.Mobiles.Get(_followTarget);

            // Out of view: stand still, and say so. What then is the brain's to decide.
            if (t == null || t.IsDestroyed || t.Distance > AgentPace.VIEW)
            {
                if (FollowState != "lost")
                {
                    FollowState = "lost";
                    FollowWhy = string.Empty;
                    Journal.AddAgentEvent($"lost sight of {FollowLabel}");
                    StopFollowWalk();
                }

                _followRuns = _followCatchUp = false;

                return;
            }

            if (FollowState == "lost")
            {
                Journal.AddAgentEvent($"{FollowLabel} is back in view");
            }

            if (!string.IsNullOrWhiteSpace(t.Name))
            {
                _followName = t.Name.Trim();
            }

            (_followSeenX, _followSeenY) = (t.X, t.Y);
            int n = _followDistance;

            // Where the two will be when the steps in progress end: it is from there that the
            // follower is near enough or not, and from there that its next walk is planned and goes.
            // Aimed at where the target was drawn, it walked a tile further behind.
            p.GetEndPosition(out int px, out int py, out _, out _);
            t.GetEndPosition(out int tx, out int ty, out sbyte tz, out _);
            (bool move, bool run, bool catchUp) = AgentPace.Follow(AgentNav.Tiles(px, py, tx, ty), n, _followWalk, _followWalk && Hurry(now),
                _followCatchUp);
            _followRuns = run;
            _followCatchUp = catchUp;

            // Fighting, looting, fleeing or the player's hands on the controls: wait, as a trip does,
            // and carry on afterwards. Those walks are the fight's and run.
            if (_engaged != 0 || _lootCorpse != 0 || Fleeing || Kiting || _takeQueue.Count != 0 || Paused(AgentBehavior.Move)
                || GetAuthority(AgentBehavior.Move) != AgentAuthority.Auto && !_followManual)
            {
                FollowState = "waiting";
                _followHasSpot = false;

                return;
            }

            FollowState = "following";

            // The tiles others stand on round the target, and its own.
            _followTaken.Clear();
            _followTaken.Add((tx, ty));

            foreach (Mobile m in _world.Mobiles.Values)
            {
                if (m != p && m != t && !m.IsDead && m.Steps.Count == 0 && AgentNav.Tiles(m.X, m.Y, tx, ty) <= n + 1)
                {
                    _followTaken.Add((m.X, m.Y));
                }
            }

            // In its way: the target, standing still, faces this follower a step away. It steps off
            // the target's line, which is kept clear of the spots to stand on for now.
            if ((tx, ty) != _followTargetAt || t.Steps.Count != 0)
            {
                _followTargetAt = (tx, ty);
                _followTargetStill = now;
            }

            bool makeWay = AgentPace.InTheWay(px, py, tx, ty, t.Direction, now - _followTargetStill);

            if (makeWay)
            {
                foreach ((int X, int Y) tile in AgentPace.Ahead(tx, ty, t.Direction, 2))
                {
                    _followTaken.Add(tile);
                }

                if (!_followMadeWay)
                {
                    Journal.AddAgentEvent($"making way for {FollowLabel}");
                }
            }

            _followMadeWay = makeWay;

            // In place: near enough, on a tile of its own once it has stopped (someone walking
            // across it doesn't count either).
            bool walking = _agentWalking && _pacedWalk && p.Pathfinder.AutoWalking;
            bool inPlace = !makeWay && !move && (walking || p.Steps.Count != 0 || !_followTaken.Contains((px, py)));

            // A way round that the client's pathfinder didn't find (below) is walked as a trip to
            // where the target was, and from there it is followed as before. Planned again only
            // when the target has gone a long way on, and dropped once in place.
            if (_followTrip)
            {
                if (_travelPath != null && !inPlace)
                {
                    if (AgentNav.Tiles(_travelGoalX, _travelGoalY, tx, ty) > FOLLOW_REPLAN_TILES && now - _followPlannedAt > FOLLOW_REPLAN_MS)
                    {
                        (_travelGoalX, _travelGoalY) = (tx, ty);
                        _followPlannedAt = now;

                        if (!PlanTravel())
                        {
                            FollowNoWay(now);
                        }
                    }

                    return;
                }

                // Over: arrived where the target was, or stuck on the way.
                bool arrived = _travelPath == null && TravelState == "arrived";
                StopFollowWalk();
                walking = false;

                if (!arrived && !inPlace)
                {
                    FollowNoWay(now);
                }
            }

            // The spot it is walking to is still good: within n of the target, and nobody has taken it.
            bool spotGood = _followHasSpot ? AgentNav.Tiles(_followSpot.X, _followSpot.Y, tx, ty) <= n && !_followTaken.Contains(_followSpot)
                : _followTargetWas == (tx, ty);

            if (inPlace)
            {
                // A walk still going to where the target was has come far enough.
                if (walking && !spotGood)
                {
                    p.Pathfinder.StopAutoWalk();
                }

                FollowWhy = string.Empty;

                return;
            }

            if (walking && spotGood && _walkRuns == run || _followFailedAt != 0 && now - _followFailedAt < FOLLOW_RETRY_MS)
            {
                return;
            }

            _followTargetWas = (tx, ty);
            _followHasSpot = false;
            List<(int X, int Y, sbyte Z)> spots = AgentPace.Spots(StandableAround(tx, ty, tz, n), px, py, tx, ty, n, _followTaken, unchecked((int) p.Serial));
            bool started;

            if (spots.Count != 0)
            {
                (int x, int y, sbyte z) = spots[0];
                started = WalkTo(x, y, z, 0, run, true, true);
                _followSpot = (x, y);
                _followHasSpot = started;
            }
            else if (!move)
            {
                return; // near enough, with nowhere of its own to stand: stay
            }
            else
            {
                started = WalkTo(tx, ty, tz, n, run, true, true); // nowhere free beside it: as near as there is a way
            }

            if (started)
            {
                _followFailedAt = 0;
                FollowWhy = string.Empty;

                return;
            }

            if (!move)
            {
                return;
            }

            // No way the client's pathfinder finds (a door shut behind the target): a trip planned
            // over the map, which opens doors.
            _travelManual = _followManual;
            _followPlannedAt = now;

            if (StartTravel(tx, ty, n, _followWalk).Item1 == "done")
            {
                _followTrip = true;
                _followFailedAt = 0;
                FollowWhy = string.Empty;
            }
            else
            {
                FollowNoWay(now);
            }
        }

        private void FollowNoWay(uint now)
        {
            _followTrip = false;
            _followFailedAt = now;
            FollowWhy = _travelWhy.Length != 0 ? _travelWhy : "no way to it";
            StopTravel("stopped");
        }

        // The tiles someone standing where the target does could step to without going further
        // than n from it, by the client's own walking rules: places to stand beside it, and none
        // across a wall or a river from it.
        private List<(int X, int Y, sbyte Z)> StandableAround(int tx, int ty, sbyte tz, int n)
        {
            Pathfinder walker = _world.Player.Pathfinder;
            var tiles = new List<(int X, int Y, sbyte Z)>();
            var seen = new HashSet<(int, int)> { (tx, ty) };
            var queue = new Queue<(int X, int Y, sbyte Z)>();
            queue.Enqueue((tx, ty, tz));

            while (queue.Count > 0)
            {
                (int x, int y, sbyte z) = queue.Dequeue();

                for (int d = 0; d < 8; d++)
                {
                    var dir = (Direction) d;
                    int nx = x, ny = y;
                    sbyte nz = z;

                    // CanWalk turns a diagonal it can't take into a step to the side: not this step.
                    if (!walker.CanWalk(ref dir, ref nx, ref ny, ref nz) || (int) dir != d || AgentNav.Tiles(nx, ny, tx, ty) > n || !seen.Add((nx, ny)))
                    {
                        continue;
                    }

                    tiles.Add((nx, ny, nz));
                    queue.Enqueue((nx, ny, nz));
                }
            }

            return tiles;
        }
    }
}
