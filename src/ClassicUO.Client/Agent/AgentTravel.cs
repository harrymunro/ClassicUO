// SPDX-License-Identifier: BSD-2-Clause

using System;
using System.Collections.Generic;
using ClassicUO.Game;
using ClassicUO.Game.Data;
using ClassicUO.Game.GameObjects;
using Microsoft.Xna.Framework;

namespace ClassicUO.Agent
{
    // Travel: a long walk planned over the map files (AgentNav) with what the walker has seen
    // added (doors, houses, items in the way), then walked a leg at a time with the client's own
    // pathfinder. A closed door on the path is opened as the walker reaches it; a trip too long
    // for one plan goes a hop at a time. A trip is run unless a walk is asked for, and a trip at
    // a walk runs while there is danger (AgentFollow: Hurry).
    internal sealed partial class AgentController
    {
        private const int TRAVEL_LEG = 16;           // tiles per leg handed to the client's pathfinder
        private const uint TRAVEL_STUCK_MS = 8000;   // no progress for this long: stuck
        private const uint TRAVEL_FACE_MS = 1000;    // no progress for this long with someone on the next tile: face them
        private const int TRAVEL_MAX_REPLANS = 5;
        private const int TRAVEL_MAX_HOPS = 40;
        private const uint DOOR_SWING_MS = 1200;     // time a door gets to open before it's tried again
        private const uint STEP_REFUSED_MS = 15_000; // a tile the server sent a step back from is left alone this long
        private const int TRAVEL_AHEAD = 10;         // how far along the path "ahead" looks
        private const int AVOID_GOAL_SLACK = 3;      // a square this near the goal holds it

        private static readonly TripLimits TravelLimits = new TripLimits();

        // The planned path (or the current hop of it), how far along it the player is, and when
        // the walk last got further (nearer the goal or along the path). Paused while fighting,
        // looting or fleeing.
        private List<(int X, int Y, sbyte Z)> _travelPath;
        private int _travelIndex, _travelGoalX, _travelGoalY, _travelDistance, _travelReplans;
        private readonly TravelProgress _travelProgress = new TravelProgress();
        private uint _travelStarted, _travelProgressAt, _nextTravelStep;
        private bool _travelHop, _travelManual;
        private bool _travelWalk, _travelRuns; // the pace asked, and the pace of its steps now
        private int _travelHops, _travelDoors, _travelPlans;
        private double _travelPlanMs, _travelPlanMsMax;
        private string _travelWhy = string.Empty;
        private readonly List<(int X, int Y)> _travelStuck = new List<(int, int)>();
        private readonly List<string> _travelStuckWhy = new List<string>();
        private readonly HashSet<(int, int)> _travelBlocked = new HashSet<(int, int)>(); // impassable items seen on this trip
        private readonly HashSet<(int, int)> _travelRefused = new HashSet<(int, int)>(); // tiles the walker couldn't step onto on this trip, nobody on them
        private int _travelDenied;                                                       // the server's refused steps (Walker.Denied) at the last plan
        private int _travelStepsRefused0;                                                // StepsRefused when the trip set off
        private readonly TravelMemory _travelMemory = new TravelMemory();
        private readonly Dictionary<(int, int), Item> _doorsInView = new Dictionary<(int, int), Item>();
        private readonly HashSet<(int, int)> _travelNewlyBlocked = new HashSet<(int, int)>();

        // Ground the trip goes round where the way round isn't too long (the act's `avoid`), less any square that
        // holds the goal: a hunt's walk to its own ground isn't priced off it. And the steps of the plan now that
        // lie on priced ground all the same.
        private (int X, int Y, int R, int Cost)[] _travelAvoid = Array.Empty<(int, int, int, int)>();
        private int _travelPriced;

        // The door being opened: what it looked like when clicked, and how often it was tried.
        private uint _doorSerial, _doorClickAt, _doorApproach, _doorApproachAt;
        private (int X, int Y) _doorApproachFrom;
        private int _doorTries;
        private (int X, int Y, ushort Graphic) _doorWas;

        // How each door looks once open (after a click that changed it), so an open door whose
        // leaf has swung onto the path isn't closed again.
        private readonly Dictionary<uint, (int X, int Y, ushort Graphic)> _doorOpen = new Dictionary<uint, (int, int, ushort)>();

        public string TravelState { get; private set; } = string.Empty;
        public (int X, int Y) TravelGoal => (_travelGoalX, _travelGoalY);
        public int TravelReplans => _travelReplans;
        public IReadOnlyList<(int X, int Y)> TravelStuckAt => _travelStuck;
        public IReadOnlyList<string> TravelStuckWhy => _travelStuckWhy;
        public uint TravelElapsedMs => _travelStarted == 0 ? 0 : Time.Ticks - _travelStarted;
        public int TravelLeft => _world.Player == null ? 0 : AgentNav.Tiles(_world.Player.X, _world.Player.Y, _travelGoalX, _travelGoalY);
        public bool TravelHop => _travelHop;
        public int TravelHops => _travelHops;      // hops finished on this trip
        public int TravelDoors => _travelDoors;
        public int TravelPlans => _travelPlans;
        public double TravelPlanMs => _travelPlanMs;
        public double TravelPlanMsMax => _travelPlanMsMax;
        public string TravelWhy => _travelWhy;
        public TravelMemory TravelMemory => _travelMemory;
        public string TravelPace => _travelWalk ? "walk" : "run";
        public bool TravelRunning => _travelPath != null && _travelRuns;
        public int TravelAvoiding => _travelAvoid.Length;
        public int TravelPricedSteps => _travelPriced;

        // Where the road goes next: the tile of the path TRAVEL_AHEAD steps on from the player's place on it. The
        // way to the goal in a line can point elsewhere: from the swamp west of Cove the road home runs north, round
        // the hills (cuo-m3n.13).
        public (int X, int Y)? TravelAhead => _travelPath == null || _travelPath.Count == 0 ? null
            : ((int, int)?) (_travelPath[Math.Min(_travelIndex + TRAVEL_AHEAD, _travelPath.Count - 1)].X,
                             _travelPath[Math.Min(_travelIndex + TRAVEL_AHEAD, _travelPath.Count - 1)].Y);

        private (string, string) StartTravel(int x, int y, int distance, bool walk = false, (int X, int Y, int R, int Cost)[] avoid = null)
        {
            _travelGoalX = x;
            _travelGoalY = y;
            _travelDistance = distance;
            _travelAvoid = AvoidFor(avoid, x, y);
            _travelWalk = walk;
            _travelRuns = !walk;
            _travelReplans = _travelHops = _travelDoors = _travelPlans = 0;
            _travelPlanMs = _travelPlanMsMax = 0;
            _travelWhy = string.Empty;
            _travelStuck.Clear();
            _travelStuckWhy.Clear();
            _travelBlocked.Clear();
            _travelRefused.Clear();
            _travelStepsRefused0 = StepsRefused;
            _doorSerial = 0;
            _travelStarted = Time.Ticks;

            if (!PlanTravel())
            {
                return ("failed", _travelWhy.Length != 0 ? $"{TravelState}: {_travelWhy}" : TravelState);
            }

            Journal.AddAgentEvent($"travelling to {x},{y}");

            return ("done", _travelHop ? $"{_travelPath.Count} tiles, the first hop of {TravelLeft}" : $"{_travelPath.Count} tiles");
        }

        // The planning grid for a box of the map, with what travel knows that the map files
        // don't: doors, locked doors and houses (TravelMemory), the impassable items seen on this
        // trip (barricades, blockers; doors are opened instead), the spots found stuck, and any
        // tiles to avoid.
        private NavGrid LoadTravelGrid(int x0, int y0, int w, int h, HashSet<(int, int)> avoid)
        {
            PlayerMobile p = _world.Player;
            NavGrid grid = AgentNav.Load(_world.MapIndex, x0, y0, w, h);
            _travelMemory.Apply(grid, _world.MapIndex, p.X, p.Y, _travelGoalX, _travelGoalY);

            foreach ((int bx, int by) in _travelBlocked)
            {
                grid.Block(bx, by);
            }

            foreach ((int bx, int by) in _travelStuck)
            {
                grid.Block(bx, by);
            }

            if (avoid != null)
            {
                foreach ((int bx, int by) in avoid)
                {
                    grid.Block(bx, by);
                }
            }

            foreach ((int ax, int ay, int r, int cost) in _travelAvoid)
            {
                grid.Price(ax, ay, r, cost);
            }

            return grid;
        }

        // The squares to price for a trip to (gx, gy): those that don't hold it.
        private static (int X, int Y, int R, int Cost)[] AvoidFor((int X, int Y, int R, int Cost)[] avoid, int gx, int gy)
        {
            if (avoid == null || avoid.Length == 0)
            {
                return Array.Empty<(int, int, int, int)>();
            }

            var keep = new List<(int, int, int, int)>();

            foreach ((int x, int y, int r, int cost) a in avoid)
            {
                if (a.cost > 0 && a.r >= 0 && AgentNav.Tiles(a.x, a.y, gx, gy) > a.r + AVOID_GOAL_SLACK)
                {
                    keep.Add(a);
                }
            }

            return keep.ToArray();
        }

        private bool OnPriced(int x, int y)
        {
            foreach ((int ax, int ay, int r, _) in _travelAvoid)
            {
                if (AgentNav.Tiles(ax, ay, x, y) <= r)
                {
                    return true;
                }
            }

            return false;
        }

        // For the `nav` view: the grid round the player, and the plan travel would make to (gx,
        // gy) from here, without setting off.
        public (NavGrid Grid, TripPlan Plan) TravelView(int gx, int gy, int radius, (int X, int Y, int R, int Cost)[] avoid = null)
        {
            PlayerMobile p = _world.Player;
            (int ogx, int ogy) = (_travelGoalX, _travelGoalY);
            var oavoid = _travelAvoid;
            _travelGoalX = gx;
            _travelGoalY = gy;
            _travelAvoid = AvoidFor(avoid, gx, gy);

            try
            {
                RememberWhatIsInView();
                NavGrid grid = LoadTravelGrid(p.X - radius, p.Y - radius, 2 * radius + 1, 2 * radius + 1, null);
                TripPlan plan = gx == p.X && gy == p.Y ? null
                    : AgentNav.PlanTrip((x0, y0, w, h) => LoadTravelGrid(x0, y0, w, h, null), p.X, p.Y, p.Z, gx, gy, 0, TravelLimits,
                        _travelAvoid.Length != 0 ? OnPriced : null);

                return (grid, plan);
            }
            finally
            {
                (_travelGoalX, _travelGoalY) = (ogx, ogy);
                _travelAvoid = oavoid;
            }
        }

        // For the `nav` view's check: the tiles the client's own pathfinder can walk to from the
        // player within `radius`, a step at a time by its rules, the map's chunks loaded first.
        public HashSet<(int, int)> ClientReachable(int radius, int limit = 200_000)
        {
            PlayerMobile p = _world.Player;

            for (int cx = Math.Max(0, p.X - radius) >> 3; cx <= (p.X + radius) >> 3; cx++)
            {
                for (int cy = Math.Max(0, p.Y - radius) >> 3; cy <= (p.Y + radius) >> 3; cy++)
                {
                    _world.Map.GetChunk2(cx, cy);
                }
            }

            var seen = new HashSet<(int, int, int)> { (p.X, p.Y, p.Z) };
            var tiles = new HashSet<(int, int)>();
            var queue = new Queue<(int X, int Y, sbyte Z)>();
            queue.Enqueue((p.X, p.Y, p.Z));

            while (queue.Count > 0 && seen.Count < limit)
            {
                (int x, int y, sbyte z) = queue.Dequeue();
                tiles.Add((x, y));

                for (int d = 0; d < 8; d++)
                {
                    var dir = (Direction) d;
                    int nx = x, ny = y;
                    sbyte nz = z;

                    // CanWalk turns a diagonal it can't take into a step to the side: not this step.
                    if (!p.Pathfinder.CanWalk(ref dir, ref nx, ref ny, ref nz) || (int) dir != d || AgentNav.Tiles(nx, ny, p.X, p.Y) > radius)
                    {
                        continue;
                    }

                    if (seen.Add((nx, ny, nz)))
                    {
                        queue.Enqueue((nx, ny, nz));
                    }
                }
            }

            return tiles;
        }

        // Plans from where the player stands: the whole way, or the next hop of a long trip.
        private bool PlanTravel(HashSet<(int, int)> avoid = null)
        {
            PlayerMobile p = _world.Player;
            int map = _world.MapIndex;
            _travelPath = null;
            _travelIndex = 0;
            _travelProgress.Restart(TravelLeft);
            _travelProgressAt = Time.Ticks;
            _travelDenied = p.Walker.Denied;
            RememberWhatIsInView();

            if (!AgentNav.SameLandmass(map, p.X, _travelGoalX))
            {
                TravelState = "no route";
                _travelWhy = "the dungeons lie apart from the land; no walk goes there";

                return false;
            }

            TripPlan plan = AgentNav.PlanTrip((x0, y0, w, h) => LoadTravelGrid(x0, y0, w, h, avoid), p.X, p.Y, p.Z, _travelGoalX, _travelGoalY,
                _travelDistance, TravelLimits, _travelAvoid.Length != 0 ? OnPriced : null);

            _travelPlans++;
            _travelPlanMs += plan.TotalMs;
            _travelPlanMsMax = Math.Max(_travelPlanMsMax, plan.TotalMs);

            if (plan.Path == null)
            {
                TravelState = "no route";
                _travelWhy = plan.Why ?? string.Empty;

                return false;
            }

            _travelPath = plan.Path;
            _travelHop = plan.Hop;
            _travelPriced = plan.PricedSteps;
            _travelWhy = string.Empty; // a way round that failed before this plan is no reason any more
            TravelState = "walking";

            return true;
        }

        public void StopTravel(string state)
        {
            if (_travelPath != null)
            {
                TravelState = state;
            }

            _travelPath = null;
        }

        // Doors and impassable items in view, and houses, into the travel memory: the client
        // forgets them out of view. What was newly found in the way (an item, the tiles of a house
        // the trip doesn't start or end in) goes into _travelNewlyBlocked.
        private void RememberWhatIsInView()
        {
            int map = _world.MapIndex;
            _doorsInView.Clear();
            _travelNewlyBlocked.Clear();

            foreach (Item it in _world.Items.Values)
            {
                if (!it.OnGround || it.IsDestroyed || it.IsMulti)
                {
                    continue;
                }

                if (it.ItemData.IsDoor)
                {
                    _travelMemory.SeeDoor(map, it.Serial, it.X, it.Y);
                    _doorsInView[(it.X, it.Y)] = it;
                }
                else if (it.ItemData.IsImpassable && _travelBlocked.Add((it.X, it.Y)))
                {
                    _travelNewlyBlocked.Add((it.X, it.Y));
                }
            }

            foreach (House house in _world.HouseManager.Houses)
            {
                if (house.Serial == 0 || house.Components.Count == 0 || _travelMemory.KnowsHouse(house.Serial, house.Components.Count))
                {
                    continue; // 0 is a placement preview
                }

                Item multi = _world.Items.Get(house.Serial);

                if (multi == null || !multi.MultiInfo.HasValue)
                {
                    continue;
                }

                var tiles = new HashSet<(int X, int Y)>();
                var walls = new HashSet<(int X, int Y)>();

                foreach (Multi m in house.Components)
                {
                    if (!m.IsDestroyed)
                    {
                        tiles.Add((m.X, m.Y));

                        // A wall: an impassable part in the way of someone on the ground floor, which
                        // stands on the foundation, 7 up. Not the foundation itself under a doorway, nor
                        // an upper floor's wall over it.
                        int floor = multi.Z + 7;

                        if (m.ItemData.IsImpassable && !m.ItemData.IsDoor && m.Z < floor + NavGrid.PERSON_HEIGHT
                            && m.Z + Math.Max((int) m.ItemData.Height, 1) > floor)
                        {
                            walls.Add((m.X, m.Y));
                        }
                    }
                }

                Rectangle b = multi.MultiInfo.Value; // offsets from the multi: X, Y the least, Width, Height the greatest
                int x0 = multi.X + b.X, y0 = multi.Y + b.Y, x1 = multi.X + b.Width, y1 = multi.Y + b.Height;
                _travelMemory.SeeHouse(map, house.Serial, x0, y0, x1, y1, new List<(int, int)>(tiles).ToArray(), house.Components.Count,
                    new List<(int, int)>(walls).ToArray());
                PlayerMobile pm = _world.Player;
                bool inIt = InBox(pm.X, pm.Y, x0, y0, x1, y1) || InBox(_travelGoalX, _travelGoalY, x0, y0, x1, y1);
                _travelNewlyBlocked.UnionWith(inIt ? walls : tiles);
            }

            // A remembered house close by that the client no longer has is gone.
            PlayerMobile p = _world.Player;

            foreach (uint serial in _travelMemory.HousesNear(map, p.X, p.Y, 6))
            {
                if (!_world.HouseManager.Exists(serial) && _world.Items.Get(serial) == null)
                {
                    _travelMemory.ForgetHouse(serial);
                }
            }
        }

        private static bool InBox(int x, int y, int x0, int y0, int x1, int y1) => x >= x0 && x <= x1 && y >= y0 && y <= y1;

        // Whether something just found in the way (a house come into view, an item) stands on
        // the path ahead.
        private bool NewlyBlockedAhead()
        {
            if (_travelNewlyBlocked.Count == 0)
            {
                return false;
            }

            for (int i = _travelIndex; i < _travelPath.Count && i <= _travelIndex + 64; i++)
            {
                if (_travelNewlyBlocked.Contains((_travelPath[i].X, _travelPath[i].Y)))
                {
                    return true;
                }
            }

            return false;
        }

        private void UpdateTravel(uint now)
        {
            if (_travelPath == null || now < _nextTravelStep)
            {
                return;
            }

            _nextTravelStep = now + 250;
            PlayerMobile p = _world.Player;

            // The pace of the steps from here: a follow's own, when the trip is its way round.
            bool run = _travelRuns = _followTrip ? _followRuns : !_travelWalk || Hurry(now);

            // Fighting, looting, fleeing or the player's hands on the controls: wait, without
            // counting it against progress.
            if (_engaged != 0 || _lootCorpse != 0 || Fleeing || _takeQueue.Count != 0 || Paused(AgentBehavior.Move)
                || GetAuthority(AgentBehavior.Move) != AgentAuthority.Auto && !_travelManual)
            {
                _travelProgressAt = now;

                return;
            }

            int left = TravelLeft;

            if (left <= _travelDistance)
            {
                TravelState = "arrived";
                _travelPath = null;
                Journal.AddAgentEvent($"arrived at {_travelGoalX},{_travelGoalY}");

                return;
            }

            // Where along the path the player is now.
            int nearest = _travelIndex, nearestD = int.MaxValue;

            for (int i = _travelIndex; i < _travelPath.Count && i < _travelIndex + 48; i++)
            {
                int d = AgentNav.Tiles(_travelPath[i].X, _travelPath[i].Y, p.X, p.Y);

                if (d < nearestD)
                {
                    nearest = i;
                    nearestD = d;
                }
            }

            _travelIndex = nearest;

            if (_travelProgress.Note(left, nearest))
            {
                _travelProgressAt = now;
            }

            RememberWhatIsInView();
            NoteDoorSwung();

            // A house or a barricade come into view on the way: plan round it now, rather than
            // walk up to it and get stuck.
            if (NewlyBlockedAhead())
            {
                Journal.AddAgentEvent("something in the way ahead; planning round it");
                PlanTravel();

                return;
            }

            // Near the end of a hop: plan the next one from here.
            if (_travelHop && _travelIndex >= _travelPath.Count - 8 && nearestD <= 2)
            {
                if (++_travelHops > TRAVEL_MAX_HOPS)
                {
                    _travelWhy = $"still {left} tiles away after {TRAVEL_MAX_HOPS} hops";
                    StopTravel("no route");
                }
                else if (PlanTravel())
                {
                    Journal.AddAgentEvent($"next hop, {TravelLeft} tiles to go");
                }

                return;
            }

            FaceWhoeverIsInTheWay(p, now);

            if (now - _travelProgressAt > TRAVEL_STUCK_MS)
            {
                TravelStuck(p);

                return;
            }

            // A leg being walked goes on, unless the pace has changed (danger come or gone): then
            // the rest of it is asked for again.
            if (p.Pathfinder.AutoWalking && !(_agentWalking && _pacedWalk && _walkRuns != run) || WorkDoorAhead(p, now))
            {
                return;
            }

            // The furthest point of the path the client's pathfinder can reach from here; if it
            // finds no way there, nearer ones, down to the nearest tile of the path itself (a
            // path of a single step, or the player beside its last tile).
            int leg = AgentNav.NextLeg(_travelPath, _travelIndex, p.X, p.Y, TRAVEL_LEG);

            foreach (int i in AgentNav.LegTries(leg, _travelIndex))
            {
                (int x, int y, sbyte z) = _travelPath[i];

                // The path already ends within the distance asked of the goal, so every leg,
                // the last too, walks to its tile.
                if ((x != p.X || y != p.Y) && WalkTo(x, y, z, 0, run, true))
                {
                    return;
                }
            }
        }

        // The door the walker clicked: once it has changed, how it looks now is how it looks
        // open (swung aside, or its graphic turned in place). If it has moved back along the path
        // towards the player instead, it was open (its leaf in the way) and is now shut in its
        // doorway: how it looked before is how it looks open, and it's opened again.
        private void NoteDoorSwung()
        {
            if (_doorSerial == 0 || !(_world.Items.Get(_doorSerial) is Item door))
            {
                return;
            }

            if ((door.X, door.Y, door.Graphic) == _doorWas)
            {
                return;
            }

            int was = PathIndexAhead(_doorWas.X, _doorWas.Y), now = PathIndexAhead(door.X, door.Y);
            bool shut = was >= 0 && now >= 0 && now < was;
            _doorOpen[_doorSerial] = shut ? _doorWas : (door.X, door.Y, door.Graphic);
            _doorSerial = 0;
        }

        private int PathIndexAhead(int x, int y)
        {
            for (int i = _travelIndex; i < _travelPath.Count && i <= _travelIndex + TRAVEL_LEG; i++)
            {
                if (_travelPath[i].X == x && _travelPath[i].Y == y)
                {
                    return i;
                }
            }

            return -1;
        }

        // A closed door standing on the path ahead: walk up to the tile before it, open it there,
        // then go on. One the client's pathfinder can step into is open already. A door that
        // stays as it was after two tries is locked: the memory blocks it and the walk goes round.
        private bool WorkDoorAhead(PlayerMobile p, uint now)
        {
            int end = Math.Min(_travelPath.Count - 1, _travelIndex + TRAVEL_LEG);

            for (int i = _travelIndex; i <= end; i++)
            {
                (int x, int y, sbyte z) = _travelPath[i];

                // A door the player stands on is open (some open in place, without moving).
                if (x == p.X && y == p.Y || !_doorsInView.TryGetValue((x, y), out Item door) || door.IsDestroyed || Math.Abs(door.Z - z) > 15
                    || _doorOpen.TryGetValue(door.Serial, out var open) && open == (door.X, door.Y, door.Graphic))
                {
                    continue;
                }

                int dx = x - p.X, dy = y - p.Y;
                bool beside = Math.Abs(dx) + Math.Abs(dy) == 1;

                // How long the player has stood where it is, on the way to this door.
                if (_doorApproach != door.Serial || _doorApproachFrom != (p.X, p.Y))
                {
                    _doorApproach = door.Serial;
                    _doorApproachFrom = (p.X, p.Y);
                    _doorApproachAt = now;
                }

                // Not beside it yet: up to the path tile before it. Within reach (2 tiles) but
                // standing still there a moment (steps the map files don't show, someone in the
                // way), it's opened from where the player is.
                if (!beside && !(door.Distance <= 2 && now - _doorApproachAt > DOOR_SWING_MS))
                {
                    for (int j = i - 1; j >= _travelIndex; j--)
                    {
                        (int bx, int by, sbyte bz) = _travelPath[j];

                        if (bx == p.X && by == p.Y)
                        {
                            break;
                        }

                        if (WalkTo(bx, by, bz, 0, _travelRuns, true))
                        {
                            return true;
                        }
                    }

                    if (door.Distance > 2)
                    {
                        return false; // can't get near it: the legs and the stuck rules deal with it
                    }
                }
                else if (CanStepTo(p, x, y))
                {
                    return false; // open, or a door one can walk through
                }

                return OpenDoor(door, now);
            }

            return false;
        }

        private static bool CanStepTo(PlayerMobile p, int x, int y)
        {
            Direction dir = DirectionHelper.GetDirectionAB(p.X, p.Y, x, y);
            int nx = p.X, ny = p.Y;
            sbyte nz = p.Z;

            return p.Pathfinder.CanWalk(ref dir, ref nx, ref ny, ref nz) && nx == x && ny == y;
        }

        private bool OpenDoor(Item door, uint now)
        {
            if (_doorSerial == door.Serial)
            {
                if (now - _doorClickAt < DOOR_SWING_MS)
                {
                    return true; // give it a moment to swing
                }

                if (++_doorTries >= 2)
                {
                    _doorSerial = 0;
                    _travelMemory.Lock(door.Serial);
                    Journal.AddAgentEvent($"the door at {door.X},{door.Y} stays shut; going round");
                    PlanTravel();

                    return true;
                }
            }
            else
            {
                _doorSerial = door.Serial;
                _doorTries = 0;
                _travelDoors++;
                Journal.AddAgentEvent($"opening a door at {door.X},{door.Y}");
            }

            _doorWas = (door.X, door.Y, door.Graphic);
            _doorClickAt = now;
            _travelProgressAt = now;
            GameActions.DoubleClick(_world, door.Serial);

            return true;
        }

        // No progress: remember where, block the next stretch of the path and plan around it.
        private void TravelStuck(PlayerMobile p)
        {
            _travelStuck.Add((p.X, p.Y));
            _travelStuckWhy.Add(NextTileAndWhoIsOnIt());
            Journal.AddAgentEvent($"stuck at {p.X},{p.Y} on the way to {_travelGoalX},{_travelGoalY}; {_travelStuckWhy[^1]}");

            if (++_travelReplans > TRAVEL_MAX_REPLANS)
            {
                StopTravel("stuck");

                return;
            }

            // Someone standing on the way (a companion up from the table, a creature in a doorway) isn't
            // the map: plan round the tiles they stand on, not the next few of the path, which in a room
            // two tiles wide closed it off. Leaving an inn with the party round, the leader stopped with
            // "no way there on the map" in two runs of three, a companion on its next tile (cuo-0jz).
            var avoid = PeopleOnPathAhead(4);
            bool someone = avoid.Count != 0;
            int next = AgentNav.NextOnPath(_travelPath, _travelIndex, p.X, p.Y);

            // Nobody there: a step the map allows and the game doesn't. The tile the walker couldn't
            // step onto stays out of this trip's plans, as well as the few after it out of the next
            // one. Below the wall of the Britain graveyard (1347,1512) no step west is taken, and
            // the plans from there went west by one of two ways turn and turn about, each stuck
            // forgetting what the one before had ruled out, and never ruling out the first step
            // itself: six times stuck on the one tile and the trip given up, on three days (cuo-4v1).
            if (!someone && next < _travelPath.Count)
            {
                _travelRefused.Add((_travelPath[next].X, _travelPath[next].Y));
            }

            for (int i = next; !someone && i < _travelPath.Count && i < next + 4; i++)
            {
                avoid.Add((_travelPath[i].X, _travelPath[i].Y));
            }

            avoid.UnionWith(_travelRefused);

            if (someone)
            {
                Journal.AddAgentEvent("someone stands in the way; going round");
            }

            // No way round: plan again with nothing avoided, since what is in the way may move, until the
            // replans run out.
            if (!PlanTravel(avoid) && !PlanTravel())
            {
                StopTravel("stuck");
            }
        }

        // Someone on the next tile of the path, a step away, with no progress for a moment: face them,
        // as one does in a doorway. A follower its leader faces that way makes way (AgentPace.InTheWay).
        private void FaceWhoeverIsInTheWay(PlayerMobile p, uint now)
        {
            int next = AgentNav.NextOnPath(_travelPath, _travelIndex, p.X, p.Y);

            if (now - _travelProgressAt < TRAVEL_FACE_MS || next >= _travelPath.Count || p.Steps.Count != 0 || p.Pathfinder.AutoWalking)
            {
                return;
            }

            (int x, int y, sbyte _) = _travelPath[next];

            if (AgentNav.Tiles(p.X, p.Y, x, y) != 1 || PeopleOnPathAhead(1).Count == 0)
            {
                return;
            }

            Direction dir = DirectionHelper.GetDirectionAB(p.X, p.Y, x, y);

            if ((p.Direction & Direction.Mask) != dir)
            {
                p.Walk(dir, false); // facing another way, a step turns the walker on the spot
            }
        }

        // The tiles of the path ahead, up to `steps` on, that someone alive stands on.
        // What a stuck walk was stepping onto, for the log: a soak trip stuck six times on one tile in
        // west Britain with townsfolk about, and nothing said whether one stood in the way (cuo-4v1).
        private string NextTileAndWhoIsOnIt()
        {
            PlayerMobile p = _world.Player;
            int next = AgentNav.NextOnPath(_travelPath, _travelIndex, p.X, p.Y);

            if (next >= _travelPath.Count)
            {
                return "at the end of the path";
            }

            (int x, int y, sbyte _) = _travelPath[next];
            var who = new List<string>();

            foreach (Mobile m in _world.Mobiles.Values)
            {
                if (m != _world.Player && !m.IsDestroyed && !m.IsDead && AgentNav.Tiles(m.X, m.Y, x, y) <= 1)
                {
                    who.Add($"{(string.IsNullOrEmpty(m.Name) ? "someone" : m.Name.Trim())} {(m.X == x && m.Y == y ? "on it" : "beside it")}");
                }
            }

            // Whose refusal it was: the client's own walker (its rules for heights and what stands
            // there), or the server's, which sends the step back.
            int denied = p.Walker.Denied - _travelDenied;
            string refusal = denied > 0 ? $"; the server refused {denied} step{(denied == 1 ? "" : "s")}"
                : AgentNav.Tiles(p.X, p.Y, x, y) == 1 && !CanStepTo(p, x, y) ? "; the client's walker won't step there" : string.Empty;

            return $"next {x},{y}: {(who.Count == 0 ? "nobody near" : string.Join(", ", who))}{refusal}";
        }

        private HashSet<(int, int)> PeopleOnPathAhead(int steps)
        {
            var ahead = new HashSet<(int, int)>();
            var people = new HashSet<(int, int)>();
            int next = AgentNav.NextOnPath(_travelPath, _travelIndex, _world.Player.X, _world.Player.Y);

            for (int i = next; i < _travelPath.Count && i < next + steps; i++)
            {
                ahead.Add((_travelPath[i].X, _travelPath[i].Y));
            }

            foreach (Mobile m in _world.Mobiles.Values)
            {
                if (m != _world.Player && !m.IsDestroyed && !m.IsDead && ahead.Contains((m.X, m.Y)))
                {
                    people.Add((m.X, m.Y));
                }
            }

            return people;
        }

        // Tiles the server sent a step back from, and until when the walker leaves each alone. The
        // client's walker and the server don't always agree on a tile (a static the client reads
        // differently, something hidden standing there), and a walker that thinks a step is fine
        // takes it again the moment it is sent back: below the Britain graveyard it stepped at one
        // tuft of grass 60 times a second for 48 s, whatever way round the planner chose, since each
        // leg is walked by the client's own pathfinder (cuo-4v1). A refused tile counts as blocked to
        // that pathfinder until it lapses, so the next leg goes round it. Only while the agent is on:
        // a player's own walking is as it was.
        private readonly RefusedTiles _stepRefused = new RefusedTiles(STEP_REFUSED_MS);

        public int StepsRefused { get; private set; }
        public int TravelStepsRefused => StepsRefused - _travelStepsRefused0;

        public void StepRefused(int x, int y)
        {
            if (Mode == AgentMode.Off)
            {
                return;
            }

            StepsRefused++;
            _stepRefused.Refuse(x, y, Time.Ticks);
        }

        public bool StepIsRefused(int x, int y) => Mode != AgentMode.Off && _stepRefused.Refused(x, y, Time.Ticks);

        // An errand walking up to a vendor: travel without needing the move authority, since the
        // errand itself was allowed.
        public void TravelForErrand(int x, int y)
        {
            _travelManual = true;
            StartTravel(x, y, 1, TravelWalks);
        }
    }
}
