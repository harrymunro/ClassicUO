// SPDX-License-Identifier: BSD-2-Clause

using System;
using System.Buffers;
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
                    Reply(conn, id, w => AgentSnapshot.Write(w, world, since, radius, pack));

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
                // stand, '#' nowhere, 'x' an impassable item, '@' the player. With goal_x/goal_y it
                // plans the walk exactly as travel would and draws the path ('o').
                case "nav":
                    RequireInGame(world);
                    int rad = Get(p, "radius", out JsonElement nr) ? Math.Clamp(nr.GetInt32(), 4, 60) : 20;
                    int px = world.Player.X, py = world.Player.Y;
                    int goalX = Get(p, "goal_x", out JsonElement gx) ? gx.GetInt32() : px;
                    int goalY = Get(p, "goal_y", out JsonElement gy) ? gy.GetInt32() : py;
                    NavGrid grid = world.Agent.TravelGrid(goalX, goalY) ?? throw new AgentRpcException(-32602, "too far for one plan");
                    var route = goalX == px && goalY == py ? null : AgentNav.Plan(grid, px, py, world.Player.Z, goalX, goalY, 0);
                    var onPath = new System.Collections.Generic.HashSet<(int, int)>();

                    foreach ((int rx, int ry, sbyte _) in route ?? new System.Collections.Generic.List<(int, int, sbyte)>())
                    {
                        onPath.Add((rx, ry));
                    }

                    var reach = Get(p, "reach", out JsonElement rf) && rf.GetBoolean()
                        ? AgentNav.Reachable(grid, px, py, world.Player.Z)
                        : new System.Collections.Generic.HashSet<(int, int)>();
                    var items = new System.Collections.Generic.HashSet<(int, int)>();

                    foreach (Game.GameObjects.Item it in world.Items.Values)
                    {
                        if (it.OnGround && !it.IsMulti && it.ItemData.IsImpassable && !it.ItemData.IsDoor)
                        {
                            items.Add((it.X, it.Y));
                        }
                    }

                    Reply(conn, id, w =>
                    {
                        w.WriteStartObject();
                        w.WriteNumber("x0", px - rad);
                        w.WriteNumber("y0", py - rad);
                        w.WriteNumber("path", route?.Count ?? -1);
                        w.WriteStartArray("rows");

                        for (int y = py - rad; y <= py + rad; y++)
                        {
                            var row = new char[2 * rad + 1];

                            for (int x = px - rad; x <= px + rad; x++)
                            {
                                row[x - px + rad] = x == px && y == py ? '@' : onPath.Contains((x, y)) ? 'o'
                                    : items.Contains((x, y)) ? 'x' : reach.Contains((x, y)) ? ',' : grid.Count(x, y) > 0 ? '.' : '#';
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

                        w.WriteEndArray();
                    });

                    break;

                case "accept":
                    RequireInGame(world);
                    bool accepted = world.Agent.AcceptSuggestion();
                    Reply(conn, id, w => w.WriteBooleanValue(accepted));

                    break;

                case "capture":
                    if (!Get(p, "path", out JsonElement path) || string.IsNullOrEmpty(path.GetString()))
                    {
                        throw new AgentRpcException(-32602, "path is required");
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
