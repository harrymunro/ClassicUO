// SPDX-License-Identifier: BSD-2-Clause

using System;
using System.Buffers;
using System.Collections.Generic;
using System.IO;
using System.Text.Json;
using ClassicUO.Configuration;
using ClassicUO.Game;
using ClassicUO.Game.Scenes;
using ClassicUO.Utility.Logging;
using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;

namespace ClassicUO.Agent
{
    // RPC entry point. GameController calls Update every frame (any scene) and
    // OnDraw after the frame is drawn. Requests are JSON objects, one per line:
    //   {"id": 1, "method": "snapshot", "params": {...}}
    // and replies carry the same id with "result" or "error".
    internal static class AgentHost
    {
        private static AgentServer _server;
        private static AgentLogin _login;
        private static AgentConnection _captureConn;
        private static string _captureId, _capturePath;

        // The eight steps in the order of the game's directions (0 is north, clockwise), for the nav view.
        private static readonly string[] Compass8 = { "north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west" };

        public static bool BrainConnected => _server?.HasClient == true;

        public static void Update(GameController game)
        {
            if (_server == null)
            {
                if (Settings.GlobalSettings.AgentPort == 0)
                {
                    return;
                }

                _server = new AgentServer(Settings.GlobalSettings.AgentPort);

                try
                {
                    _server.Start();
                }
                catch (Exception ex)
                {
                    Log.Error($"[agent] could not listen on port {Settings.GlobalSettings.AgentPort}: {ex.Message}");
                    Settings.GlobalSettings.AgentPort = 0;
                    _server = null;

                    return;
                }
            }

            while (_server.Inbox.TryDequeue(out (AgentConnection Connection, string Line) req))
            {
                Handle(game, req.Connection, req.Line);
            }

            _login?.Update(game);
        }

        public static void OnDraw(GraphicsDevice device)
        {
            if (_capturePath == null)
            {
                return;
            }

            AgentConnection conn = _captureConn;
            string id = _captureId, path = _capturePath;
            _captureConn = null;
            _captureId = _capturePath = null;

            try
            {
                int w = device.PresentationParameters.BackBufferWidth;
                int h = device.PresentationParameters.BackBufferHeight;
                var pixels = new Color[w * h];
                device.GetBackBufferData(pixels);

                using var tex = new Texture2D(device, w, h);
                tex.SetData(pixels);
                Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(path)));

                using (FileStream fs = File.Create(path))
                {
                    tex.SaveAsPng(fs, w, h);
                }

                Reply(conn, id, r =>
                {
                    r.WriteStartObject();
                    r.WriteString("path", Path.GetFullPath(path));
                    r.WriteNumber("width", w);
                    r.WriteNumber("height", h);
                    r.WriteEndObject();
                });
            }
            catch (Exception ex)
            {
                ReplyError(conn, id, -32000, $"capture failed: {ex.Message}");
            }
        }

        private static void Handle(GameController game, AgentConnection conn, string line)
        {
            string id = "null";

            try
            {
                using JsonDocument doc = JsonDocument.Parse(line);
                JsonElement root = doc.RootElement;

                if (root.TryGetProperty("id", out JsonElement idEl))
                {
                    id = idEl.GetRawText();
                }

                string method = root.GetProperty("method").GetString();
                JsonElement p = root.TryGetProperty("params", out JsonElement pe) && pe.ValueKind == JsonValueKind.Object ? pe : default;
                Dispatch(game, conn, id, method, p);
            }
            catch (AgentRpcException ex)
            {
                ReplyError(conn, id, ex.Code, ex.Message);
            }
            catch (Exception ex)
            {
                ReplyError(conn, id, -32603, ex.Message);
            }
        }

        private static void Dispatch(GameController game, AgentConnection conn, string id, string method, JsonElement p)
        {
            World world = game.UO.World;

            switch (method)
            {
                case "ping":
                    Reply(conn, id, w =>
                    {
                        w.WriteStartObject();
                        w.WriteBoolean("pong", true);
                        w.WriteEndObject();
                    });

                    break;

                case "status":
                    Reply(conn, id, w => WriteStatus(w, game, world));

                    break;

                case "login":
                    if (game.GetScene<LoginScene>() == null)
                    {
                        throw new AgentRpcException(-32001, "not at the login screen");
                    }

                    _login = AgentLogin.Start(game, p);
                    Reply(conn, id, w => WriteStatus(w, game, world));

                    break;

                case "snapshot":
                    long since = Get(p, "since", out JsonElement s) ? s.GetInt64() : 0;
                    int radius = Get(p, "radius", out JsonElement r) ? Math.Clamp(r.GetInt32(), 1, 24) : 18;
                    bool pack = Get(p, "pack", out JsonElement pk) && pk.GetBoolean();
                    bool spots = Get(p, "spots", out JsonElement sp) && sp.GetBoolean(); // places to stand (cuo-fhf)
                    Reply(conn, id, w => AgentSnapshot.Write(w, world, since, radius, pack, spots));

                    break;

                case "act":
                    RequireInGame(world);
                    AgentAction action = AgentAction.FromJson(p);
                    (string status, string detail) = world.Agent.Request(action);

                    if (status == "done" && !action.Manual)
                    {
                        world.Agent.Journal.AddAgentEvent(action.Describe(world));
                    }

                    Reply(conn, id, w =>
                    {
                        w.WriteStartObject();
                        w.WriteString("status", status);
                        w.WriteString("detail", detail);
                        w.WriteEndObject();
                    });

                    break;

                case "mode":
                    RequireInGame(world);

                    if (Get(p, "mode", out JsonElement m))
                    {
                        if (!AgentModes.TryParse(m.GetString(), out AgentMode mode))
                        {
                            throw new AgentRpcException(-32602, "mode must be off, assist or auto");
                        }

                        world.Agent.SetMode(mode);
                    }

                    if (Get(p, "authority", out JsonElement auth) && auth.ValueKind == JsonValueKind.Object)
                    {
                        foreach (JsonProperty a in auth.EnumerateObject())
                        {
                            if (!AgentModes.TryParse(a.Name, out AgentBehavior b) || !AgentModes.TryParse(a.Value.GetString(), out AgentAuthority level))
                            {
                                throw new AgentRpcException(-32602, $"bad authority {a.Name}={a.Value}");
                            }

                            world.Agent.SetAuthority(b, level);
                        }
                    }

                    if (Get(p, "bandage_below", out JsonElement bb) || Get(p, "heal_potion_below", out _))
                    {
                        world.Agent.SetReflexThresholds
                        (
                            Get(p, "bandage_below", out bb) ? bb.GetInt32() : null,
                            Get(p, "heal_potion_below", out JsonElement hp) ? hp.GetInt32() : null
                        );
                    }

                    Reply(conn, id, w => WriteStatus(w, game, world));

                    break;

                // {text: "..."} replaces, {add: "..."} appends a line, {clear: true} empties,
                // {template: "name", replace: bool} pulls a template in, {remove_template: "name"} takes it out.
                case "strategy":
                    RequireInGame(world);

                    if (Get(p, "template", out JsonElement tp) || Get(p, "remove_template", out tp))
                    {
                        AgentTemplate template = AgentTemplates.Find(tp.GetString());

                        if (template == null)
                        {
                            throw new AgentRpcException(-32602, $"no template '{tp.GetString()}'");
                        }

                        if (Get(p, "remove_template", out _))
                        {
                            world.Agent.DropTemplate(template);
                        }
                        else
                        {
                            world.Agent.PullTemplate(template, Get(p, "replace", out JsonElement rp) && rp.GetBoolean());
                        }
                    }
                    else if (Get(p, "text", out JsonElement st))
                    {
                        world.Agent.SetStrategy(st.GetString());
                    }
                    else if (Get(p, "add", out JsonElement sa))
                    {
                        world.Agent.AddStrategy(sa.GetString());
                    }
                    else if (Get(p, "clear", out JsonElement sc) && sc.GetBoolean())
                    {
                        world.Agent.SetStrategy(string.Empty);
                    }

                    Reply(conn, id, w =>
                    {
                        w.WriteStartObject();
                        w.WriteString("strategy", world.Agent.Strategy);
                        w.WriteNumber("revision", world.Agent.StrategyRevision);
                        w.WriteEndObject();
                    });

                    break;

                // {text: "..."} sets the goal, {clear: true}, {pause: true|false}, {template: "name"}.
                case "goal":
                    RequireInGame(world);

                    if (Get(p, "template", out JsonElement gt))
                    {
                        AgentTemplate goal = AgentTemplates.FindGoal(gt.GetString()) ?? throw new AgentRpcException(-32602, $"no goal template '{gt.GetString()}'");
                        world.Agent.SetGoal(goal.Text);
                    }
                    else if (Get(p, "text", out JsonElement gtext))
                    {
                        world.Agent.SetGoal(gtext.GetString());
                    }
                    else if (Get(p, "clear", out JsonElement gc) && gc.GetBoolean())
                    {
                        world.Agent.SetGoal(string.Empty);
                    }

                    if (Get(p, "pause", out JsonElement gp))
                    {
                        world.Agent.PauseGoal(gp.GetBoolean());
                    }

                    Reply(conn, id, w =>
                    {
                        w.WriteStartObject();
                        w.WriteString("goal", world.Agent.Goal);
                        w.WriteNumber("rev", world.Agent.GoalRevision);
                        w.WriteBoolean("paused", world.Agent.GoalPaused);
                        w.WriteEndObject();
                    });

                    break;

                // {goals: [{id, text, progress}], ack: seq}: the character's long goals for the panel, from the
                // brain, which keeps them. Replies with what the player asked in the panel since (add, done,
                // drop); the brain sends ack with the last seq it applied.
                case "long_goals":
                    RequireInGame(world);

                    if (Get(p, "goals", out JsonElement lgs) && lgs.ValueKind == JsonValueKind.Array)
                    {
                        var goals = new List<AgentController.LongGoal>();

                        foreach (JsonElement g in lgs.EnumerateArray())
                        {
                            goals.Add(new AgentController.LongGoal
                            {
                                Id = g.TryGetProperty("id", out JsonElement gi) && gi.ValueKind == JsonValueKind.Number ? gi.GetInt32() : 0,
                                Text = g.TryGetProperty("text", out JsonElement gt2) ? gt2.GetString() ?? string.Empty : string.Empty,
                                Progress = g.TryGetProperty("progress", out JsonElement gpr) ? gpr.GetString() ?? string.Empty : string.Empty
                            });
                        }

                        world.Agent.SetLongGoals(goals);
                    }

                    if (Get(p, "ack", out JsonElement lack) && lack.ValueKind == JsonValueKind.Number)
                    {
                        world.Agent.AckLongGoalRequests(lack.GetInt32());
                    }

                    Reply(conn, id, w =>
                    {
                        w.WriteStartObject();
                        w.WriteStartArray("requests");

                        foreach (AgentController.LongGoalRequest r in world.Agent.LongGoalRequests)
                        {
                            w.WriteStartObject();
                            w.WriteNumber("seq", r.Seq);
                            w.WriteString("action", r.Action);
                            w.WriteNumber("id", r.Id);
                            w.WriteString("text", r.Text);
                            w.WriteEndObject();
                        }

                        w.WriteEndArray();
                        w.WriteEndObject();
                    });

                    break;

                // {step, why}: what the planner is doing about the goal, for the panel.
                case "goal_status":
                    RequireInGame(world);
                    world.Agent.SetGoalStatus(Get(p, "step", out JsonElement gs) ? gs.GetString() : string.Empty,
                                              Get(p, "why", out JsonElement gw) ? gw.GetString() : string.Empty);
                    Reply(conn, id, w => w.WriteBooleanValue(true));

                    break;

                case "templates":
                    Reply(conn, id, w =>
                    {
                        w.WriteStartArray();

                        foreach (AgentTemplate t in Get(p, "kind", out JsonElement tk) && tk.GetString() == "goal" ? AgentTemplates.Goals() : AgentTemplates.All())
                        {
                            w.WriteStartObject();
                            w.WriteString("name", t.Name);
                            w.WriteString("title", t.Title);
                            w.WriteString("for", t.For);
                            w.WriteString("summary", t.Summary);
                            w.WriteString("text", t.Text);
                            w.WriteBoolean("user", t.User);

                            if (world.InGame && world.Player != null)
                            {
                                w.WriteBoolean("in_use", world.Agent.TemplateInUse(t));
                            }

                            w.WriteEndObject();
                        }

                        w.WriteEndArray();
                    });

                    break;

                // Runs a client command as if typed after "-", e.g. {"text": "agent strategy show"}.
                case "command":
                    RequireInGame(world);
                    string[] parts = (Get(p, "text", out JsonElement ct) ? ct.GetString() : string.Empty)
                        .TrimStart('-').Split(' ', StringSplitOptions.RemoveEmptyEntries);

                    if (parts.Length == 0)
                    {
                        throw new AgentRpcException(-32602, "text is required");
                    }

                    world.CommandManager.Execute(parts[0], parts);
                    Reply(conn, id, w => w.WriteBooleanValue(true));

                    break;

                case "note":
                    RequireInGame(world);
                    world.Agent.NoteBrain(Get(p, "text", out JsonElement t) ? t.GetString() : string.Empty);
                    Reply(conn, id, w => w.WriteBooleanValue(true));

                    break;

                // What the brain decided and why, for the agent gump.
                case "decision":
                    RequireInGame(world);
                    world.Agent.RecordDecision(AgentDecision.FromJson(p));
                    Reply(conn, id, w => w.WriteBooleanValue(true));

                    break;

                // {judge, archetype, strategy_reading}: any may be left out.
                // One model call other than a fight decision, for the live view of calls.
                case "ai_call":
                    RequireInGame(world);
                    world.Agent.RecordCall(AgentCall.FromJson(p));
                    Reply(conn, id, w => w.WriteBooleanValue(true));

                    break;

                case "brain_info":
                    RequireInGame(world);
                    world.Agent.SetBrainInfo
                    (
                        Get(p, "judge", out JsonElement bj) ? bj.GetString() : null,
                        Get(p, "archetype", out JsonElement ba) ? ba.GetString() : null,
                        Get(p, "strategy_reading", out JsonElement bs) ? bs.GetString() : null
                    );
                    Reply(conn, id, w => w.WriteBooleanValue(true));

                    break;

                // The travel planner's view around the player, for debugging routes: '.' somewhere to
                // stand, '#' nowhere, 'x' an impassable item, 'd' a door ('D' one blocked), 'h' a house's blocked tile (all of
                // a house, or its walls when the player or the goal is in it), '@' the player. With goal_x/goal_y it
                // plans the walk exactly as travel would (the first hop of a long one) and draws
                // the path ('o'). With reach, ',' is what the planner can walk to from the player;
                // with check, '-' marks tiles only the planner reaches and '+' tiles only the
                // client's own pathfinder does.
                case "nav":
                    RequireInGame(world);
                    int rad = Get(p, "radius", out JsonElement nr) ? Math.Clamp(nr.GetInt32(), 4, 200) : 20;
                    int px = world.Player.X, py = world.Player.Y;
                    int goalX = Get(p, "goal_x", out JsonElement gx) ? gx.GetInt32() : px;
                    int goalY = Get(p, "goal_y", out JsonElement gy) ? gy.GetInt32() : py;
                    (NavGrid grid, TripPlan trip) = world.Agent.TravelView(goalX, goalY, rad);
                    var route = trip?.Path;
                    var onPath = new System.Collections.Generic.HashSet<(int, int)>();

                    foreach ((int rx, int ry, sbyte _) in route ?? new System.Collections.Generic.List<(int, int, sbyte)>())
                    {
                        onPath.Add((rx, ry));
                    }

                    bool check = Get(p, "check", out JsonElement cf) && cf.GetBoolean();
                    var reach = check || Get(p, "reach", out JsonElement rf) && rf.GetBoolean()
                        ? AgentNav.Reachable(grid, px, py, world.Player.Z)
                        : new System.Collections.Generic.HashSet<(int, int)>();
                    var clientReach = check ? world.Agent.ClientReachable(rad) : null;
                    var items = new System.Collections.Generic.HashSet<(int, int)>();

                    foreach (Game.GameObjects.Item it in world.Items.Values)
                    {
                        if (it.OnGround && !it.IsMulti && it.ItemData.IsImpassable && !it.ItemData.IsDoor)
                        {
                            items.Add((it.X, it.Y));
                        }
                    }

                    int navMap = world.MapIndex;

                    // With steps: each step from the player's tile as the client's own walker judges it
                    // (the tile and height it would land on, or not `ok`), the land under the player and
                    // round it, and how many steps the server has refused since login. A walk the map
                    // allows and the game doesn't shows here (cuo-4v1).
                    var steps = new System.Collections.Generic.List<(string Dir, bool Ok, int X, int Y, int Z)>();
                    var lands = new System.Collections.Generic.List<(int X, int Y, int Z, int Min, int Avg, bool Stretched, int Graphic)>();
                    var things = new System.Collections.Generic.List<(int X, int Y, int Z, int Height, int Graphic, bool Impassable, bool Surface, bool Bridge)>();

                    if (Get(p, "steps", out JsonElement sf) && sf.GetBoolean())
                    {
                        for (int d = 0; d < 8; d++)
                        {
                            var dir = (Game.Data.Direction) d;
                            int nx = px, ny = py;
                            sbyte nz = world.Player.Z;
                            bool ok = world.Player.Pathfinder.CanWalk(ref dir, ref nx, ref ny, ref nz) && (int) dir == d;
                            steps.Add((Compass8[d], ok, nx, ny, nz));
                        }

                        for (int ly = py - 1; ly <= py + 1; ly++)
                        {
                            for (int lx = px - 1; lx <= px + 1; lx++)
                            {
                                Game.GameObjects.GameObject o = world.Map.GetTile(lx, ly, false);

                                while (o?.TPrevious != null)
                                {
                                    o = o.TPrevious;
                                }

                                for (; o != null; o = o.TNext)
                                {
                                    if (o is Game.GameObjects.Land land)
                                    {
                                        lands.Add((lx, ly, land.Z, land.MinZ, land.AverageZ, land.IsStretched, land.Graphic));
                                    }
                                    else if (o is Game.GameObjects.Static || o is Game.GameObjects.Multi || o is Game.GameObjects.Item)
                                    {
                                        // As the walker reads it: a static by what the map says it is, whatever the season draws.
                                        ushort own = o is Game.GameObjects.Static st ? st.OriginalGraphic : o.Graphic;
                                        ref Assets.StaticTiles data = ref Client.Game.UO.FileManager.TileData.StaticData[own];

                                        if (data.IsImpassable || data.IsSurface || data.IsBridge)
                                        {
                                            things.Add((lx, ly, o.Z, data.Height, own, data.IsImpassable, data.IsSurface, data.IsBridge));
                                        }
                                    }
                                }
                            }
                        }
                    }

                    int denied = world.Player.Walker.Denied;
                    int playerZ = world.Player.Z;

                    Reply(conn, id, w =>
                    {
                        w.WriteStartObject();
                        w.WriteNumber("x0", px - rad);
                        w.WriteNumber("y0", py - rad);
                        w.WriteNumber("path", route?.Count ?? -1);

                        if (steps.Count != 0)
                        {
                            w.WriteNumber("z", playerZ);
                            w.WriteNumber("denied", denied);
                            w.WriteStartArray("steps");

                            foreach ((string dir, bool ok, int sx, int sy, int sz) in steps)
                            {
                                w.WriteStartObject();
                                w.WriteString("dir", dir);
                                w.WriteBoolean("ok", ok);

                                if (ok)
                                {
                                    w.WriteNumber("x", sx);
                                    w.WriteNumber("y", sy);
                                    w.WriteNumber("z", sz);
                                }

                                w.WriteEndObject();
                            }

                            w.WriteEndArray();
                            w.WriteStartArray("land");

                            foreach ((int lx, int ly, int lz, int min, int avg, bool stretched, int graphic) in lands)
                            {
                                w.WriteStartObject();
                                w.WriteNumber("x", lx);
                                w.WriteNumber("y", ly);
                                w.WriteNumber("z", lz);
                                w.WriteNumber("min", min);
                                w.WriteNumber("avg", avg);
                                w.WriteBoolean("stretched", stretched);
                                w.WriteNumber("graphic", graphic);
                                w.WriteEndObject();
                            }

                            w.WriteEndArray();
                            w.WriteStartArray("things");

                            foreach ((int tx, int ty, int tz, int height, int graphic, bool impassable, bool surface, bool bridge) in things)
                            {
                                w.WriteStartObject();
                                w.WriteNumber("x", tx);
                                w.WriteNumber("y", ty);
                                w.WriteNumber("z", tz);
                                w.WriteNumber("height", height);
                                w.WriteNumber("graphic", graphic);
                                w.WriteBoolean("impassable", impassable);
                                w.WriteBoolean("surface", surface);
                                w.WriteBoolean("bridge", bridge);
                                w.WriteEndObject();
                            }

                            w.WriteEndArray();
                        }

                        if (trip != null)
                        {
                            w.WriteBoolean("hop", trip.Hop);
                            w.WriteNumber("plan_ms", Math.Round(trip.TotalMs, 1));
                            w.WriteNumber("load_ms", Math.Round(trip.LoadMs, 1));
                            w.WriteNumber("nodes", trip.Stats.Expanded);
                            w.WriteNumber("grids", trip.Grids);

                            if (trip.Why != null)
                            {
                                w.WriteString("why", trip.Why);
                            }
                        }

                        w.WriteStartArray("rows");

                        for (int y = py - rad; y <= py + rad; y++)
                        {
                            var row = new char[2 * rad + 1];

                            for (int x = px - rad; x <= px + rad; x++)
                            {
                                row[x - px + rad] = x == px && y == py ? '@' : onPath.Contains((x, y)) ? 'o'
                                    : items.Contains((x, y)) ? 'x' : grid.IsDoor(x, y) ? grid.Count(x, y) > 0 ? 'd' : 'D'
                                    : grid.Count(x, y) == 0 && world.Agent.TravelMemory.InHouse(navMap, x, y) ? 'h'
                                    : clientReach != null && reach.Contains((x, y)) != clientReach.Contains((x, y)) ? reach.Contains((x, y)) ? '-' : '+'
                                    : reach.Contains((x, y)) ? ',' : grid.Count(x, y) > 0 ? '.' : '#';
                            }

                            w.WriteStringValue(new string(row));
                        }

                        w.WriteEndArray();
                        w.WriteEndObject();
                    });

                    break;

                // Items lying on the ground nearby with their tile flags: signs, doors, blockers.
                case "items":
                    RequireInGame(world);
                    int ir = Get(p, "radius", out JsonElement irr) ? Math.Clamp(irr.GetInt32(), 1, 24) : 8;

                    Reply(conn, id, w =>
                    {
                        w.WriteStartArray();

                        foreach (Game.GameObjects.Item it in world.Items.Values)
                        {
                            if (!it.OnGround || it.IsDestroyed || it.Distance > ir)
                            {
                                continue;
                            }

                            w.WriteStartObject();
                            w.WriteNumber("serial", it.Serial);
                            w.WriteNumber("graphic", it.Graphic);
                            w.WriteString("name", AgentSnapshot.NameOf(world, it));
                            w.WriteNumber("x", it.X);
                            w.WriteNumber("y", it.Y);
                            w.WriteNumber("z", it.Z);
                            w.WriteNumber("height", it.ItemData.Height);
                            w.WriteBoolean("impassable", it.ItemData.IsImpassable);
                            w.WriteBoolean("surface", it.ItemData.IsSurface);
                            w.WriteBoolean("door", it.ItemData.IsDoor);
                            w.WriteBoolean("multi", it.IsMulti);
                            w.WriteEndObject();
                        }

                        // With parts: the pieces of the houses in range too (part_of the house).
                        if (Get(p, "parts", out JsonElement ip) && ip.GetBoolean())
                        {
                            foreach (Game.GameObjects.House house in world.HouseManager.Houses)
                            {
                                foreach (Game.GameObjects.Multi m in house.Components)
                                {
                                    if (m.IsDestroyed || Math.Max(Math.Abs(m.X - world.Player.X), Math.Abs(m.Y - world.Player.Y)) > ir)
                                    {
                                        continue;
                                    }

                                    w.WriteStartObject();
                                    w.WriteNumber("part_of", house.Serial);
                                    w.WriteNumber("graphic", m.Graphic);
                                    w.WriteString("name", m.Name ?? string.Empty);
                                    w.WriteNumber("x", m.X);
                                    w.WriteNumber("y", m.Y);
                                    w.WriteNumber("z", m.Z);
                                    w.WriteNumber("height", m.ItemData.Height);
                                    w.WriteBoolean("impassable", m.ItemData.IsImpassable);
                                    w.WriteBoolean("surface", m.ItemData.IsSurface);
                                    w.WriteBoolean("door", m.ItemData.IsDoor);
                                    w.WriteEndObject();
                                }
                            }
                        }

                        w.WriteEndArray();
                    });

                    break;

                // Line of sight (cuo-ehm). {checks: bool} turns the agent's sight checks on or off (for
                // before-and-after benchmarks). {rule: "modernuo" | "runuo" | "runuo22"} sets whose rule
                // they follow (cuo-2ad) for as long as the client runs; the setting is agent_sight_rule.
                // {pairs: [[x1, y1, z1, x2, y2, z2], ...]} answers the sight rule for raw points, no eye
                // height added, to compare with the server's [AgentSight. {serial: n}: whether the player
                // sees that mobile. {view: radius}: which ground tiles around the player it can aim at
                // ('.' yes, '#' no, '@' the player).
                case "sight":
                    RequireInGame(world);

                    if (Get(p, "checks", out JsonElement checksOn))
                    {
                        world.Agent.SightChecks = checksOn.GetBoolean();
                    }

                    if (Get(p, "rule", out JsonElement ruleName))
                    {
                        string asked = ruleName.ValueKind == JsonValueKind.String ? ruleName.GetString() : null;

                        if (string.IsNullOrWhiteSpace(asked) || !AgentSight.TryParseRule(asked, out SightRule asRule))
                        {
                            throw new AgentRpcException(-32602, "rule is modernuo, runuo or runuo22");
                        }

                        world.Agent.SightRule = asRule;
                    }

                    var answers = new System.Collections.Generic.List<bool>();

                    if (Get(p, "pairs", out JsonElement pairs) && pairs.ValueKind == JsonValueKind.Array)
                    {
                        foreach (JsonElement pair in pairs.EnumerateArray())
                        {
                            if (pair.ValueKind != JsonValueKind.Array || pair.GetArrayLength() != 6)
                            {
                                throw new AgentRpcException(-32602, "each pair is [x1, y1, z1, x2, y2, z2]");
                            }

                            answers.Add(AgentSight.InSight(world.Agent.SightTiles, pair[0].GetInt32(), pair[1].GetInt32(), pair[2].GetInt32(),
                                                           pair[3].GetInt32(), pair[4].GetInt32(), pair[5].GetInt32(), world.Agent.SightRule));
                        }
                    }

                    bool? seen = null;

                    if (Get(p, "serial", out JsonElement ss))
                    {
                        seen = world.Mobiles.TryGetValue(ss.GetUInt32(), out Game.GameObjects.Mobile sm) && world.Agent.InSight(sm);
                    }

                    var view = new System.Collections.Generic.List<string>();

                    if (Get(p, "view", out JsonElement vr))
                    {
                        int vrad = Math.Clamp(vr.GetInt32(), 2, 24), vx = world.Player.X, vy = world.Player.Y;

                        for (int y = vy - vrad; y <= vy + vrad; y++)
                        {
                            var row = new char[2 * vrad + 1];

                            for (int x = vx - vrad; x <= vx + vrad; x++)
                            {
                                row[x - vx + vrad] = x == vx && y == vy ? '@' : world.Agent.SeesGround(x, y) ? '.' : '#';
                            }

                            view.Add(new string(row));
                        }
                    }

                    Reply(conn, id, w =>
                    {
                        w.WriteStartObject();
                        w.WriteBoolean("checks", world.Agent.SightChecks);
                        w.WriteString("rule", world.Agent.SightRule.Name());
                        w.WriteStartArray("in_sight");

                        foreach (bool a in answers)
                        {
                            w.WriteBooleanValue(a);
                        }

                        w.WriteEndArray();

                        if (seen.HasValue)
                        {
                            w.WriteBoolean("mobile", seen.Value);
                        }

                        if (view.Count != 0)
                        {
                            w.WriteStartArray("view");

                            foreach (string row in view)
                            {
                                w.WriteStringValue(row);
                            }

                            w.WriteEndArray();
                        }

                        w.WriteEndObject();
                    });

                    break;

                case "accept":
                    RequireInGame(world);
                    bool accepted = world.Agent.AcceptSuggestion();
                    Reply(conn, id, w => w.WriteBooleanValue(accepted));

                    break;

                // {visible: true} shows the window and draws again, {visible: false} hides it and
                // stops drawing (headless); without params, only reports. For watching an NPC.
                case "window":
                    if (Get(p, "visible", out JsonElement vis))
                    {
                        game.SetHeadless(!vis.GetBoolean());
                    }

                    Reply(conn, id, w =>
                    {
                        w.WriteStartObject();
                        WriteWindow(w, game);
                        w.WriteEndObject();
                    });

                    break;

                case "capture":
                    if (!Get(p, "path", out JsonElement path) || string.IsNullOrEmpty(path.GetString()))
                    {
                        throw new AgentRpcException(-32602, "path is required");
                    }

                    if (game.Headless)
                    {
                        throw new AgentRpcException(-32003, "nothing is drawn while the window is hidden; show it first (window visible=true)");
                    }

                    // Answered from OnDraw once this frame has been drawn.
                    _captureConn = conn;
                    _captureId = id;
                    _capturePath = path.GetString();

                    break;

                default:
                    throw new AgentRpcException(-32601, $"unknown method '{method}'");
            }
        }

        private static void WriteStatus(Utf8JsonWriter w, GameController game, World world)
        {
            w.WriteStartObject();
            LoginScene login = game.GetScene<LoginScene>();
            w.WriteString("scene", login != null ? "login" : game.GetScene<GameScene>() != null ? "game" : "other");
            w.WriteBoolean("in_game", world.InGame && world.Player != null);
            WriteWindow(w, game);

            if (login != null)
            {
                w.WriteString("login_step", login.CurrentLoginStep.ToString());
            }

            if (_login != null)
            {
                w.WriteString("login_state", _login.State);

                if (_login.Error != null)
                {
                    w.WriteString("login_error", _login.Error);
                }
            }

            if (world.InGame && world.Player != null)
            {
                w.WriteString("player", world.Player.Name ?? string.Empty);
                w.WriteNumber("serial", world.Player.Serial);
                w.WriteString("mode", world.Agent.Mode.Name());
                w.WriteStartObject("authority");

                foreach (AgentBehavior b in AgentModes.AllBehaviors)
                {
                    w.WriteString(b.Name(), world.Agent.GetAuthority(b).Name());
                }

                w.WriteEndObject();
                w.WriteNumber("bandage_below", world.Agent.Reflexes.BandageBelowPercent);
                w.WriteNumber("heal_potion_below", world.Agent.Reflexes.HealPotionBelowPercent);
            }

            w.WriteEndObject();
        }

        // Whether the window is shown, and the loop's rates over the last second: updates, frames
        // the frame cap let through, and frames drawn (0 headless).
        private static void WriteWindow(Utf8JsonWriter w, GameController game)
        {
            w.WriteBoolean("headless", game.Headless);
            w.WriteNumber("updates_per_s", game.UpdatesPerSecond);
            w.WriteNumber("frames_per_s", game.FramesPerSecond);
            w.WriteNumber("draws_per_s", game.DrawsPerSecond);
        }

        private static void RequireInGame(World world)
        {
            if (!world.InGame || world.Player == null)
            {
                throw new AgentRpcException(-32002, "not in game");
            }
        }

        private static bool Get(JsonElement p, string name, out JsonElement value)
        {
            value = default;

            return p.ValueKind == JsonValueKind.Object && p.TryGetProperty(name, out value) && value.ValueKind != JsonValueKind.Null;
        }

        private static void Reply(AgentConnection conn, string id, Action<Utf8JsonWriter> writeResult)
        {
            var buffer = new ArrayBufferWriter<byte>(1024);

            using (var w = new Utf8JsonWriter(buffer))
            {
                w.WriteStartObject();
                w.WritePropertyName("id");
                w.WriteRawValue(id);
                w.WritePropertyName("result");
                writeResult(w);
                w.WriteEndObject();
            }

            Send(conn, buffer);
        }

        private static void ReplyError(AgentConnection conn, string id, int code, string message)
        {
            var buffer = new ArrayBufferWriter<byte>(256);

            using (var w = new Utf8JsonWriter(buffer))
            {
                w.WriteStartObject();
                w.WritePropertyName("id");
                w.WriteRawValue(id);
                w.WriteStartObject("error");
                w.WriteNumber("code", code);
                w.WriteString("message", message);
                w.WriteEndObject();
                w.WriteEndObject();
            }

            Send(conn, buffer);
        }

        private static void Send(AgentConnection conn, ArrayBufferWriter<byte> buffer)
        {
            byte[] line = new byte[buffer.WrittenCount + 1];
            buffer.WrittenSpan.CopyTo(line);
            line[^1] = (byte) '\n';
            conn.Send(line);
        }
    }

    internal sealed class AgentRpcException : Exception
    {
        public AgentRpcException(int code, string message) : base(message)
        {
            Code = code;
        }

        public int Code { get; }
    }
}
