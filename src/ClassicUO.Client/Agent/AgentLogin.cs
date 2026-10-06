// SPDX-License-Identifier: BSD-2-Clause

using System;
using System.Collections.Generic;
using System.Text.Json;
using ClassicUO.Configuration;
using ClassicUO.Game.Data;
using ClassicUO.Game.GameObjects;
using ClassicUO.Game.Scenes;

namespace ClassicUO.Agent
{
    // Drives the login screens without the mouse: connect, pick the server, pick the
    // named character or create it. Advanced once per frame by AgentHost.
    //
    // params: {account, password, host?, port?, server?: index, character?: name,
    //          create?: {name, female?, str, dex, int, skills: {"Swordsmanship": 30, ...}}}
    internal sealed class AgentLogin
    {
        private string _character;
        private int _server;
        private CreateSpec _create;
        private bool _created;

        public string State { get; private set; } = "connecting";
        public string Error { get; private set; }
        public bool Active { get; private set; } = true;

        private sealed class CreateSpec
        {
            public string Name;
            public bool Female;
            public int Str = 50, Dex = 30, Int = 10;
            public readonly Dictionary<string, int> Skills = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        }

        public static AgentLogin Start(GameController game, JsonElement p)
        {
            var login = new AgentLogin
            {
                _character = Str(p, "character"),
                _server = p.TryGetProperty("server", out JsonElement s) ? s.GetInt32() : 0
            };

            if (p.TryGetProperty("create", out JsonElement c) && c.ValueKind == JsonValueKind.Object)
            {
                var spec = new CreateSpec { Name = Str(c, "name") ?? login._character };
                spec.Female = c.TryGetProperty("female", out JsonElement f) && f.GetBoolean();
                spec.Str = c.TryGetProperty("str", out JsonElement st) ? st.GetInt32() : spec.Str;
                spec.Dex = c.TryGetProperty("dex", out JsonElement dx) ? dx.GetInt32() : spec.Dex;
                spec.Int = c.TryGetProperty("int", out JsonElement it) ? it.GetInt32() : spec.Int;

                if (c.TryGetProperty("skills", out JsonElement sk) && sk.ValueKind == JsonValueKind.Object)
                {
                    foreach (JsonProperty prop in sk.EnumerateObject())
                    {
                        spec.Skills[prop.Name] = prop.Value.GetInt32();
                    }
                }

                login._create = spec;
                login._character ??= spec.Name;
            }

            string host = Str(p, "host");

            if (!string.IsNullOrEmpty(host))
            {
                Settings.GlobalSettings.IP = host;
            }

            if (p.TryGetProperty("port", out JsonElement port))
            {
                Settings.GlobalSettings.Port = port.GetUInt16();
            }

            LoginScene scene = game.GetScene<LoginScene>();
            scene.Connect(Str(p, "account") ?? string.Empty, Str(p, "password") ?? string.Empty);

            return login;
        }

        public void Update(GameController game)
        {
            if (!Active)
            {
                return;
            }

            LoginScene scene = game.GetScene<LoginScene>();

            if (scene == null)
            {
                if (game.UO.World.InGame)
                {
                    State = "in_game";
                    Active = false;
                }

                return;
            }

            switch (scene.CurrentLoginStep)
            {
                case LoginSteps.ServerSelection when scene.Servers != null && scene.Servers.Length != 0:
                    int idx = Math.Clamp(_server, 0, scene.Servers.Length - 1);
                    scene.SelectServer((byte) scene.Servers[idx].Index);
                    State = "server_selected";

                    break;

                // An account with no characters goes straight to creation.
                case LoginSteps.CharacterSelection when scene.Characters != null:
                case LoginSteps.CharacterCreation when scene.Characters != null && !_created:
                    SelectOrCreate(game, scene);

                    break;

                case LoginSteps.PopUpMessage:
                    Error = scene.PopupMessage ?? "login failed";
                    State = "failed";
                    Active = false;

                    break;
            }
        }

        private void SelectOrCreate(GameController game, LoginScene scene)
        {
            string[] chars = scene.Characters;
            int found = -1;

            for (int i = 0; i < chars.Length; i++)
            {
                if (!string.IsNullOrEmpty(chars[i]) && (_character == null || chars[i].Equals(_character, StringComparison.OrdinalIgnoreCase)))
                {
                    found = i;

                    break;
                }
            }

            if (found >= 0)
            {
                scene.SelectCharacter((uint) found);
                State = "entering";

                return;
            }

            if (_create == null || _created)
            {
                Error = _created ? "character creation did not complete" : $"no character named '{_character}'";
                State = "failed";
                Active = false;

                return;
            }

            _created = true;
            State = "creating";

            var ch = new PlayerMobile(game.UO.World, 1)
            {
                Name = _create.Name,
                Race = RaceType.HUMAN,
                IsFemale = _create.Female,
                Strength = (ushort) _create.Str,
                Dexterity = (ushort) _create.Dex,
                Intelligence = (ushort) _create.Int,
                Hue = 0x03EA
            };

            if (_create.Female)
            {
                ch.Flags |= Flags.Female;
            }

            foreach (Skill skill in ch.Skills)
            {
                if (_create.Skills.TryGetValue(skill.Name, out int value))
                {
                    skill.ValueFixed = (ushort) (value * 10);
                }
            }

            scene.CreateCharacter(ch, 0, 0);
        }

        private static string Str(JsonElement e, string name)
        {
            return e.ValueKind == JsonValueKind.Object && e.TryGetProperty(name, out JsonElement v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;
        }
    }
}
