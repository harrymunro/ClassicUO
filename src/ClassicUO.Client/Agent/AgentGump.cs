// SPDX-License-Identifier: BSD-2-Clause

using System;
using System.Collections.Generic;
using System.Text;
using ClassicUO.Configuration;
using ClassicUO.Game;
using ClassicUO.Game.GameObjects;
using ClassicUO.Game.Managers;
using ClassicUO.Game.UI.Controls;
using ClassicUO.Game.UI.Gumps;
using ClassicUO.Input;
using Microsoft.Xna.Framework;

namespace ClassicUO.Agent
{
    // The agent's panel: mode, what the agent is doing, and what Jev is thinking:
    // its probabilities for each intent, the danger judgment, the target and spell
    // it picked, what was done about it, the player's strategy and how Jev read it.
    // Collapses to a few lines. Rebuilt a few times a second when something changes;
    // the strategy entry box is kept across rebuilds so typing is not lost.
    internal sealed class AgentGump : Gump
    {
        private const int WIDTH = 310;
        private const int PAD = 16;
        private const int INNER = WIDTH - PAD * 2;
        private const int BAR_X = PAD + 62;
        private const int BAR_W = 150;

        private const ushort BACKGROUND = 0x0A28;
        private const ushort ENTRY_BACKGROUND = 0x0BB8;
        private const ushort RADIO_OFF = 0x00D0;
        private const ushort RADIO_ON = 0x00D1;
        private const byte FONT = 0xFF;

        // Unicode hues
        private const ushort WHITE = 0xFFFF;
        private const ushort GOLD = 0x0035;
        private const ushort GREY = 0x03B2;
        private const ushort DIM = 0x0386;
        private const ushort GREEN = 0x0044;
        private const ushort RED = 0x0021;
        private const ushort BLUE = 0x0058;
        private const ushort LINK = 0x0481;
        private const ushort BAR_BACK = 0x0001;

        private readonly AgentController _agent;
        private readonly ResizePic _background;
        private readonly AlphaBlendControl _shade;
        private readonly DataBox _content;
        private readonly DataBox _entry;
        private readonly StbTextBox _strategyBox;

        private uint _nextUpdate;
        private string _signature = string.Empty;
        private List<AgentTemplate> _templates;
        private uint _templatesAt;

        public AgentGump(World world, AgentController agent) : base(world, 0, 0)
        {
            _agent = agent;
            CanMove = true;
            CanCloseWithEsc = false;
            CanCloseWithRightClick = false;
            AcceptMouseInput = true;
            AcceptKeyboardInput = false;
            LayerOrder = UILayer.Over;
            WantUpdateSize = false;

            // First time: beside the game view when the window has room, else over its corner.
            Profile profile = ProfileManager.CurrentProfile;
            Rectangle view = Client.Game.Scene?.Camera.Bounds ?? Rectangle.Empty;
            bool roomBeside = view.Right + 12 + WIDTH <= Client.Game.Window.ClientBounds.Width;
            X = profile != null && profile.AgentGumpX >= 0 ? profile.AgentGumpX : roomBeside ? view.Right + 12 : 20;
            Y = profile != null && profile.AgentGumpY >= 0 ? profile.AgentGumpY : roomBeside ? Math.Max(view.Y, 30) : 60;
            Width = WIDTH;

            // A stone frame with a dark pane inside, so text stays readable.
            Add(_background = new ResizePic(BACKGROUND) { Width = WIDTH, Height = 100 });
            Add(_shade = new AlphaBlendControl(0.78f) { X = 9, Y = 9, Width = WIDTH - 18, Height = 82 });
            Add(_content = new DataBox(0, 0, WIDTH, 100));

            // Strategy entry: a text field with "add" and "clear".
            _entry = new DataBox(PAD, 0, INNER, 24);
            _entry.Add(new ResizePic(ENTRY_BACKGROUND) { Width = INNER - 78, Height = 22 });
            _entry.Add
            (
                _strategyBox = new StbTextBox(FONT, AgentController.MAX_STRATEGY_LENGTH, INNER - 88, true, FontStyle.None, 0x0386)
                {
                    X = 5,
                    Y = 2,
                    Width = INNER - 88,
                    Height = 18
                }
            );
            _entry.Add(new ClickLabel("add", LINK, AddStrategy) { X = INNER - 70, Y = 2 });
            _entry.Add(new ClickLabel("clear", LINK, () => SetStrategy(string.Empty)) { X = INNER - 36, Y = 2 });
            Add(_entry);

            Rebuild();
        }

        private static bool Expanded
        {
            get => ProfileManager.CurrentProfile?.AgentGumpExpanded ?? true;
            set
            {
                if (ProfileManager.CurrentProfile != null)
                {
                    ProfileManager.CurrentProfile.AgentGumpExpanded = value;
                }
            }
        }

        public override void Update()
        {
            base.Update();

            if (IsDisposed || Time.Ticks < _nextUpdate)
            {
                return;
            }

            _nextUpdate = Time.Ticks + 200;
            string sig = Signature();

            if (sig != _signature)
            {
                _signature = sig;
                Rebuild();
            }
        }

        // Everything the panel shows, cheaply; a change triggers a rebuild.
        private string Signature()
        {
            AgentDecision d = _agent.LastDecision;
            AgentStats s = _agent.Stats;

            return string.Concat
            (
                _agent.Mode.Name(), _agent.Engage.Name(), _agent.GetAuthority(AgentBehavior.Fight).Name(), "|", Expanded ? "x" : "c", "|",
                _agent.BrainActive ? "b" : "-", _agent.HumanActive ? "h" : "-", "|",
                Doing(), "|", _agent.DecisionSeq.ToString(), "|", _agent.StrategyRevision.ToString(), "|",
                _agent.Suggestion?.Describe(World) ?? "", "|", d == null ? "" : Ago(d.Time), "|",
                $"{s.Kills},{s.Deaths},{s.Bandages + s.HealPotions + s.SpellHeals},{s.Casts}"
            );
        }

        private void Rebuild()
        {
            _content.Clear();
            int y = 12;

            // Title row: name, brain state, collapse toggle.
            AddText("Jev", PAD, y, GOLD);
            string brain = !_agent.BrainActive ? "brain off"
                : string.Join(" · ", new[] { _agent.BrainArchetype, _agent.BrainJudge }).Trim(' ', '·');
            AddText(brain, PAD + 34, y + 1, _agent.BrainActive ? GREEN : GREY);
            _content.Add(new ClickLabel(Expanded ? "less" : "more", LINK, () => { Expanded = !Expanded; _signature = string.Empty; }) { X = WIDTH - PAD - 28, Y = y });
            y += 22;

            // Play state: three radio buttons, and the key that switches between the two ways to play.
            int x = PAD;

            foreach (AgentMode mode in new[] { AgentMode.Off, AgentMode.Assist, AgentMode.Auto })
            {
                bool on = _agent.Mode == mode;
                AgentMode m = mode;
                var radio = new ClickPic(on ? RADIO_ON : RADIO_OFF, () => SetMode(m)) { X = x, Y = y - 2 };
                _content.Add(radio);
                var label = new ClickLabel(mode.Title(), on ? GOLD : WHITE, () => SetMode(m)) { X = x + radio.Width + 4, Y = y };
                _content.Add(label);
                x = label.X + label.Width + 18;
            }

            y += 20;
            string keys = KeyHelp();

            if (keys.Length != 0)
            {
                y = AddWrapped(keys, PAD, y, INNER, DIM);
            }

            if (_agent.Mode == AgentMode.Assist)
            {
                y = AddAssistSettings(y + 2);
            }

            y += 4;

            string doing = Doing();
            string status = !_agent.HumanActive ? "now: " + doing
                : _agent.Mode == AgentMode.Assist ? "you're driving; " + doing
                : "you have the controls";
            y = AddWrapped(status, PAD, y, INNER, _agent.HumanActive ? GOLD : WHITE) + 4;

            AgentDecision d = _agent.LastDecision;

            if (Expanded)
            {
                y = AddThinking(d, y);
            }
            else if (d != null && !string.IsNullOrEmpty(d.Note))
            {
                y = AddWrapped("jev: " + d.Note, PAD, y, INNER, GREY) + 2;
            }
            else if (!string.IsNullOrEmpty(_agent.BrainNote) && Time.Ticks - _agent.BrainNoteTime < 10000)
            {
                y = AddWrapped("jev: " + _agent.BrainNote, PAD, y, INNER, GREY) + 2;
            }

            if (_agent.Suggestion != null)
            {
                AgentAction sug = _agent.Suggestion;
                string conf = sug.Confidence >= 0 ? $" {Pct(sug.Confidence)}" : string.Empty;
                y = AddWrapped($"suggests: {sug.Describe(World)}?{conf}", PAD, y + 2, INNER - 50, GOLD);
                _content.Add(new ClickLabel("accept", LINK, () => _agent.AcceptSuggestion()) { X = WIDTH - PAD - 40, Y = y - 16 });
                y += 4;
            }

            if (Expanded)
            {
                y = AddStrategy(y);
                _entry.IsVisible = true;
                _entry.Y = y;
                y += _entry.Height + 6;
                y = AddRecent(y);
            }
            else
            {
                _entry.IsVisible = false;
            }

            AgentStats s = _agent.Stats;
            string stats = $"kills {s.Kills}   deaths {s.Deaths}   heals {s.Bandages + s.HealPotions + s.SpellHeals}";

            if (s.Casts > 0)
            {
                stats += $"   casts {s.Casts}";
            }

            AddText(stats, PAD, y + 2, GREY);
            y += 26;

            _background.Height = Height = y;
            _shade.Height = y - 18;
            _content.Height = y;
        }

        // Combat assist: what it takes on by itself, and whether it fights on its own or waits for
        // the next-move key.
        private int AddAssistSettings(int y)
        {
            Label head = AddText("engages:", PAD, y, GREY);
            int x = PAD + head.Width + 8;

            foreach ((AgentEngage e, string text) in new[] { (AgentEngage.Follow, "your target"), (AgentEngage.Defend, "+ attackers"), (AgentEngage.Nearby, "anything near") })
            {
                AgentEngage engage = e;
                var link = new ClickLabel(text, _agent.Engage == e ? GOLD : LINK, () => _agent.SetEngage(engage)) { X = x, Y = y };
                link.SetTooltip(e.Title(), 200);
                _content.Add(link);
                x += link.Width + 10;
            }

            y += 17;
            bool own = _agent.GetAuthority(AgentBehavior.Fight) == AgentAuthority.Auto;
            head = AddText("fights:", PAD, y, GREY);
            x = PAD + head.Width + 8;
            var auto = new ClickLabel("on its own", own ? GOLD : LINK, () => _agent.SetAuthority(AgentBehavior.Fight, AgentAuthority.Auto)) { X = x, Y = y };
            _content.Add(auto);
            var onKey = new ClickLabel("on your key", !own ? GOLD : LINK, () => _agent.SetAuthority(AgentBehavior.Fight, AgentAuthority.Suggest)) { X = x + auto.Width + 10, Y = y };
            onKey.SetTooltip("jev picks the move, you press the next-move key to do it", 200);
            _content.Add(onKey);

            return y + 17;
        }

        // "Alt+A switches · Alt+N next move", from the player's macros.
        private string KeyHelp()
        {
            string switchKey = MacroKey(MacroType.AgentSwitch), nextKey = MacroKey(MacroType.AgentNext);
            var parts = new List<string>();

            if (switchKey != null)
            {
                parts.Add($"{switchKey} switches");
            }

            if (nextKey != null)
            {
                parts.Add($"{nextKey} next move");
            }

            return string.Join(" · ", parts);
        }

        private string MacroKey(MacroType type)
        {
            if (World.Macros == null)
            {
                return null;
            }

            foreach (Macro m in World.Macros.GetAllMacros())
            {
                if (m.Key == 0)
                {
                    continue;
                }

                for (LinkedObject o = m.Items; o != null; o = o.Next)
                {
                    if (o is MacroObject mo && mo.Code == type)
                    {
                        int k = (int) m.Key;
                        string key = k > 32 && k < 127 ? ((char) k).ToString().ToUpperInvariant() : $"key {k}";

                        return (m.Ctrl ? "Ctrl+" : "") + (m.Alt ? "Alt+" : "") + (m.Shift ? "Shift+" : "") + key;
                    }
                }
            }

            return null;
        }

        private int AddThinking(AgentDecision d, int y)
        {
            y = AddHeader(d == null ? "jev's judgment" : $"jev's judgment · {Latency(d)}{Ago(d.Time)} ago", y);

            if (d == null)
            {
                return AddWrapped(_agent.BrainActive ? "waiting for the first decision" : "no brain running: start uo-brain run", PAD, y, INNER, GREY) + 4;
            }

            foreach ((string name, float p) in d.Intents)
            {
                bool chosen = name == d.Intent;
                bool masked = d.Masked.Contains(name);
                y = masked ? AddMasked(name, y) : AddBar(name, p, chosen ? GREEN : BLUE, chosen ? WHITE : GREY, y);
            }

            if (d.Danger >= 0)
            {
                y = AddBar("danger", d.Danger, d.Danger >= 0.5f ? RED : GOLD, d.Danger >= 0.5f ? RED : GREY, y);
            }

            y += 2;

            if (!string.IsNullOrEmpty(d.TargetName))
            {
                y = AddPair("target", d.TargetName + Conf(d.TargetConfidence), y);
            }

            if (!string.IsNullOrEmpty(d.SpellName))
            {
                string why = d.SpellWhy == "strategy" ? "  (your strategy)" : d.SpellWhy == "fallback" ? "  (strongest ready)" : Conf(d.SpellConfidence);
                y = AddPair("spell", d.SpellName + why, y);
            }

            if (d.Actions.Count == 0)
            {
                y = AddPair("did", d.Gated ? $"nothing: unsure ({Pct(d.Confidence)}), keeping course" : d.Note, y);
            }
            else
            {
                for (int i = 0; i < d.Actions.Count; i++)
                {
                    string result = i < d.Results.Count ? d.Results[i] : string.Empty;
                    y = AddPair(i == 0 ? "did" : string.Empty, d.Actions[i].Describe(World) + (result.Length == 0 || result == "done" ? "" : $" ({result})"), y);
                }
            }

            return y + 4;
        }

        private int AddStrategy(int y)
        {
            y = AddHeader("strategy, in your words", y);
            string strategy = _agent.Strategy;

            if (string.IsNullOrEmpty(strategy))
            {
                y = AddWrapped("none yet: type how jev should play, e.g. \"never flee, finish the weakest first\"", PAD, y, INNER, GREY);
            }
            else
            {
                string[] lines = strategy.Split('\n');

                for (int i = 0; i < lines.Length && i < 6; i++)
                {
                    y = AddWrapped("\"" + lines[i].Trim() + "\"", PAD, y, INNER, WHITE);
                }

                if (lines.Length > 6)
                {
                    y = AddWrapped($"... and {lines.Length - 6} more lines", PAD, y, INNER, GREY);
                }

                if (!string.IsNullOrEmpty(_agent.StrategyReading))
                {
                    y = AddWrapped("jev reads it as: " + _agent.StrategyReading, PAD, y + 2, INNER, GREY);
                }
            }

            return AddTemplates(y + 4) + 4;
        }

        // Ready-made strategies: click one to pull it in, click again (gold, in use) to take it out.
        private int AddTemplates(int y)
        {
            if (_templates == null || Time.Ticks - _templatesAt > 5000)
            {
                _templates = AgentTemplates.All();
                _templatesAt = Time.Ticks;
            }

            Label head = AddText("templates:", PAD, y, GREY);
            int x = PAD + head.Width + 8;

            foreach (AgentTemplate t in _templates)
            {
                if (!t.Suits(_agent.BrainArchetype))
                {
                    continue;
                }

                bool inUse = _agent.TemplateInUse(t);
                AgentTemplate template = t;
                var link = new ClickLabel(t.Name, inUse ? GOLD : LINK, () =>
                {
                    if (!_agent.DropTemplate(template))
                    {
                        _agent.PullTemplate(template, false);
                    }
                });

                if (x + link.Width > PAD + INNER)
                {
                    x = PAD;
                    y += 16;
                }

                link.X = x;
                link.Y = y;
                link.SetTooltip((inUse ? "in use, click to take it out\n" : "click to pull it in\n") + t.Summary + "\n\n" + t.Text, 260);
                _content.Add(link);
                x += link.Width + 10;
            }

            return y + 18;
        }

        private int AddRecent(int y)
        {
            IReadOnlyList<AgentDecision> all = _agent.Decisions;

            if (all.Count < 2)
            {
                return y;
            }

            y = AddHeader("earlier", y);

            // Runs of the same decision (same intent, target and spell) show once, with a count.
            int shown = 0;

            for (int i = all.Count - 2; i >= 0 && shown < 5; shown++)
            {
                AgentDecision d = all[i];
                int run = 1;

                while (i - run >= 0 && SameChoice(all[i - run], d))
                {
                    run++;
                }

                AddText(Ago(d.Time), PAD, y, DIM);
                string text = (string.IsNullOrEmpty(d.Note) ? d.Intent : d.Note) + (run > 1 ? $"  x{run}" : string.Empty);
                y = AddWrapped(text, PAD + 36, y, INNER - 36, GREY);
                i -= run;
            }

            return y + 2;
        }

        private static bool SameChoice(AgentDecision a, AgentDecision b) =>
            a.Intent == b.Intent && a.TargetSerial == b.TargetSerial && a.SpellName == b.SpellName && a.Gated == b.Gated;

        // ---------------------------------------------------------------- pieces

        private int AddHeader(string text, int y)
        {
            y += 4;
            _content.Add(new ColorBox(INNER, 1, DIM) { X = PAD, Y = y });
            AddText(text, PAD, y + 3, GOLD);

            return y + 21;
        }

        private int AddBar(string name, float p, ushort barHue, ushort textHue, int y)
        {
            p = Math.Clamp(p, 0f, 1f);
            AddText(name, PAD, y, textHue);
            _content.Add(new ColorBox(BAR_W, 9, BAR_BACK) { X = BAR_X, Y = y + 4 });

            if (p > 0.005f)
            {
                _content.Add(new ColorBox(Math.Max(2, (int) (BAR_W * p)), 9, barHue) { X = BAR_X, Y = y + 4 });
            }

            AddText(Pct(p), BAR_X + BAR_W + 8, y, textHue);

            return y + 17;
        }

        // An option the facts ruled out before Jev's answer was used.
        private int AddMasked(string name, int y)
        {
            AddText(name, PAD, y, DIM);
            AddText("ruled out", BAR_X, y, DIM);

            return y + 17;
        }

        private int AddPair(string key, string value, int y)
        {
            AddText(key, PAD, y, GREY);

            return AddWrapped(value, BAR_X, y, INNER - (BAR_X - PAD), WHITE);
        }

        private Label AddText(string text, int x, int y, ushort hue)
        {
            var label = new Label(text, true, hue, 0, FONT) { X = x, Y = y };
            _content.Add(label);

            return label;
        }

        private int AddWrapped(string text, int x, int y, int width, ushort hue)
        {
            var label = new Label(text, true, hue, width, FONT) { X = x, Y = y };
            _content.Add(label);

            return y + Math.Max(label.Height, 16);
        }

        private string Doing()
        {
            PlayerMobile p = World.Player;

            if (p == null)
            {
                return string.Empty;
            }

            if (p.IsDead)
            {
                return "dead";
            }

            if (!string.IsNullOrEmpty(_agent.CastingSpell))
            {
                return "casting " + _agent.CastingSpell;
            }

            if (_agent.Bandaging)
            {
                return "bandaging";
            }

            if (_agent.Fleeing)
            {
                return "fleeing";
            }

            if (_agent.Engaged != 0)
            {
                Mobile m = World.Mobiles.Get(_agent.Engaged);
                string name = m == null || string.IsNullOrEmpty(m.Name) ? "?" : m.Name.Trim();

                return "fighting " + name;
            }

            return _agent.LootCorpse != 0 ? "looting" : _agent.Mode == AgentMode.Off ? "off" : "idle";
        }

        private static string Pct(float p) => $"{Math.Round(p * 100)}%";

        private static string Conf(float c) => c >= 0 ? $"  {Pct(c)}" : string.Empty;

        private static string Latency(AgentDecision d) => d.LatencyMs > 0 ? $"{Math.Round(d.LatencyMs)} ms · " : string.Empty;

        private static string Ago(uint time)
        {
            uint s = (Time.Ticks - time) / 1000;

            return s < 60 ? $"{s}s" : $"{s / 60}m";
        }

        // ---------------------------------------------------------------- actions

        private void SetMode(AgentMode mode)
        {
            _agent.SetMode(mode);
            _agent.Print($"mode {mode.Title()}");
        }

        private void AddStrategy()
        {
            string text = _strategyBox.Text?.Trim();

            if (string.IsNullOrEmpty(text))
            {
                return;
            }

            _agent.AddStrategy(text);
            _strategyBox.SetText(string.Empty);
        }

        private void SetStrategy(string text)
        {
            _agent.SetStrategy(text);
            _strategyBox.SetText(string.Empty);
        }

        public override void OnKeyboardReturn(int textID, string text)
        {
            AddStrategy();
        }

        protected override void OnDragEnd(int x, int y)
        {
            base.OnDragEnd(x, y);

            if (ProfileManager.CurrentProfile != null)
            {
                ProfileManager.CurrentProfile.AgentGumpX = ScreenCoordinateX;
                ProfileManager.CurrentProfile.AgentGumpY = ScreenCoordinateY;
            }
        }

        private sealed class ClickPic : GumpPic
        {
            private readonly Action _onClick;

            public ClickPic(ushort graphic, Action onClick) : base(0, 0, graphic, 0)
            {
                _onClick = onClick;
                AcceptMouseInput = true;
                CanMove = false;
            }

            protected override void OnMouseDown(int x, int y, MouseButtonType button)
            {
                if (button == MouseButtonType.Left)
                {
                    _onClick();
                }
            }
        }

        // Text that acts on mouse down: the panel may be rebuilt between press and release.
        private sealed class ClickLabel : Label
        {
            private readonly Action _onClick;

            public ClickLabel(string text, ushort hue, Action onClick) : base(text, true, hue, 0, FONT, FontStyle.None)
            {
                _onClick = onClick;
                AcceptMouseInput = true;
                CanMove = false;
            }

            protected override void OnMouseDown(int x, int y, MouseButtonType button)
            {
                if (button == MouseButtonType.Left)
                {
                    _onClick();
                }
            }
        }
    }
}
