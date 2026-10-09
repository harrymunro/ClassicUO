// SPDX-License-Identifier: BSD-2-Clause

using System;
using System.Collections.Generic;
using System.Text;
using ClassicUO.Game;
using ClassicUO.Game.UI.Controls;
using ClassicUO.Game.UI.Gumps;
using ClassicUO.Input;

namespace ClassicUO.Agent
{
    // A live view of the model's calls (cuo-2e5), opened from the panel's "calls" link. For the
    // latest fight decision, every question Jev answered: its options as bars with Jev's pick in
    // green and, where code went with something else, that in gold; a yes/no as one bar with the
    // cut marked and the verdict. Under it, the latest of each other kind of call: Jev's routine
    // hunt questions, its picks of world facts, a strategy read, and the planner's goals. Redrawn
    // as each call comes in.
    internal sealed class AgentCallsGump : Gump
    {
        public const int WIDTH = 340;
        private const int PAD = 16;
        private const int INNER = WIDTH - PAD * 2;
        private const int LABEL_W = 116;
        private const int BAR_X = PAD + LABEL_W + 6;
        private const int BAR_W = 120;
        private const byte FONT = 0xFF;

        private const ushort WHITE = 0xFFFF;
        private const ushort GOLD = 0x0035;
        private const ushort GREY = 0x03B2;
        private const ushort DIM = 0x0386;
        private const ushort GREEN = 0x0044;
        private const ushort RED = 0x0021;
        private const ushort BLUE = 0x0058;
        private const ushort LINK = 0x0481;
        private const ushort BAR_BACK = 0x0001;

        // The other kinds of call, in the order shown.
        private static readonly (string Kind, string Name)[] Kinds =
        {
            ("routine", "hunt"), ("facts", "facts"), ("strategy", "strategy"), ("planner", "planner")
        };

        private readonly AgentController _agent;
        private readonly ResizePic _background;
        private readonly AlphaBlendControl _shade;
        private readonly DataBox _content;
        private uint _nextUpdate;
        private string _signature = string.Empty;

        public AgentCallsGump(World world, AgentController agent, int x, int y) : base(world, 0, 0)
        {
            _agent = agent;
            CanMove = true;
            CanCloseWithEsc = false;
            CanCloseWithRightClick = true;
            AcceptMouseInput = true;
            LayerOrder = UILayer.Default;
            WantUpdateSize = false;
            X = x;
            Y = y;
            Width = WIDTH;

            Add(_background = new ResizePic(0x0A28) { Width = WIDTH, Height = 100 });
            Add(_shade = new AlphaBlendControl(0.78f) { X = 9, Y = 9, Width = WIDTH - 18, Height = 82 });
            Add(_content = new DataBox(0, 0, WIDTH, 100));
            Rebuild();
        }

        public override void Update()
        {
            base.Update();

            if (IsDisposed || Time.Ticks < _nextUpdate)
            {
                return;
            }

            _nextUpdate = Time.Ticks + 250;
            AgentDecision d = _agent.LastDecision;
            string sig = $"{_agent.CallSeq}|{_agent.DecisionSeq}|{(d == null ? 0 : (Time.Ticks - d.Time) / 1000)}";

            if (sig != _signature)
            {
                _signature = sig;
                Rebuild();
            }
        }

        private void Rebuild()
        {
            _content.Clear();
            int y = 12;
            AddText("jev's calls, live", PAD, y, GOLD);
            _content.Add(new ClickLabel("close", LINK, Dispose) { X = WIDTH - PAD - 30, Y = y });
            y += 20;

            // How many of each kind so far.
            var counts = new StringBuilder();

            foreach (KeyValuePair<string, int> kv in _agent.CallCounts)
            {
                counts.Append(counts.Length == 0 ? "" : " · ").Append(Name(kv.Key)).Append(' ').Append(kv.Value);
            }

            y = AddWrapped(counts.Length == 0 ? "no calls yet" : counts.ToString(), PAD, y, INNER, DIM);

            // What they have cost: the total, an hourly rate, the dearest kinds, and the budget.
            if (_agent.Spent is AgentSpent spent)
            {
                var line = new StringBuilder($"spent {Dollars(spent.Total)} · {Dollars(spent.PerHour)}/h");

                for (int i = 0; i < spent.ByKind.Count && i < 3; i++)
                {
                    line.Append(" · ").Append(Name(spent.ByKind[i].Kind)).Append(' ').Append(Dollars(spent.ByKind[i].Cost));
                }

                if (spent.Budget >= 0)
                {
                    line.Append($" · cap {Dollars(spent.Budget)}/h").Append(spent.OverBudget ? ", over" : "");
                }

                y = AddWrapped(line.ToString(), PAD, y, INNER, spent.OverBudget ? RED : DIM);
            }

            y += 6;

            AgentDecision d = _agent.LastDecision;

            if (d != null)
            {
                string head = $"fight · {Ago(d.Time)} ago" + (d.LatencyMs > 0 ? $" · {Math.Round(d.LatencyMs)} ms" : "")
                              + (d.Cost > 0 ? $" · {Dollars(d.Cost)}" : "");
                y = AddHeader(head, y);

                foreach (AgentCallQuestion q in d.Questions)
                {
                    y = AddQuestion(q, y);
                }

                // What was done about it: each action, with where a move went.
                if (d.Did.Count != 0)
                {
                    y = AddWrapped("actions", PAD, y, INNER, DIM);

                    foreach ((string what, string result) in d.Did)
                    {
                        y = AddWrapped("· " + what + (result.Length != 0 ? $"  ({result})" : ""), PAD + 6, y, INNER - 6,
                                       result.Length != 0 ? GREY : WHITE);
                    }
                }

                if (!string.IsNullOrEmpty(d.Note))
                {
                    y = AddWrapped("so: " + d.Note, PAD, y, INNER, GREY) + 4;
                }
            }

            foreach ((string kind, string _) in Kinds)
            {
                if (!_agent.LatestCalls.TryGetValue(kind, out AgentCall c))
                {
                    continue;
                }

                string head = $"{c.Title} · {Ago(c.Time)} ago" + (c.LatencyMs > 0 ? $" · {Math.Round(c.LatencyMs)} ms" : "")
                              + (c.Cost > 0 ? $" · {Dollars(c.Cost)}" : "");
                y = AddHeader(head, y);

                foreach (AgentCallQuestion q in c.Questions)
                {
                    y = AddQuestion(q, y);
                }

                if (c.Note.Length != 0)
                {
                    y = AddWrapped(c.Note, PAD, y, INNER, GREY) + 4;
                }
            }

            y += 8;
            _background.Height = Height = y;
            _shade.Height = y - 18;
            _content.Height = y;
        }

        private int AddQuestion(AgentCallQuestion q, int y)
        {
            switch (q.Kind)
            {
                case "yesno":
                {
                    // One bar: Jev's yes, the cut that decides it, and the verdict.
                    bool yes = q.Verdict == "yes";
                    AddText(Trim(q.Title, 22), PAD, y, WHITE);
                    Bar(q.P, yes ? GREEN : BLUE, y);

                    if (q.Cut >= 0)
                    {
                        _content.Add(new ColorBox(2, 13, RED) { X = BAR_X + (int) (BAR_W * Math.Clamp(q.Cut, 0f, 1f)), Y = y + 2 });
                    }

                    AddText($"{Pct(q.P)} {(yes ? "yes" : "no")}", BAR_X + BAR_W + 6, y, yes ? GREEN : GREY);

                    return y + 18;
                }

                case "score":
                    AddText(Trim(q.Title, 22), PAD, y, WHITE);
                    Bar(q.P, BLUE, y);
                    AddText(Pct(q.P), BAR_X + BAR_W + 6, y, GREY);

                    return y + 18;

                case "text":
                    return AddWrapped(q.Title, PAD, y, INNER, WHITE) + 2;

                case "many":
                    // A pick of several (world facts): each with Jev's yes, the kept ones green.
                    y = AddWrapped(q.Title, PAD, y, INNER, DIM);

                    foreach (AgentCallOption o in q.Options)
                    {
                        y = AddWrapped(o.Label, PAD, y, INNER, o.Kept ? WHITE : GREY);
                        Bar(o.P, o.Kept ? GREEN : BLUE, y - 2, PAD);
                        AddText(Pct(o.P) + (o.Kept ? "  kept" : ""), PAD + BAR_W + 6, y - 2, o.Kept ? GREEN : GREY);
                        y += 16;
                    }

                    return y + 2;

                default:
                {
                    // A choice: each option with Jev's probability; its pick green, code's gold.
                    y = AddWrapped(q.Title + (q.Confidence >= 0 ? $"  (sure {Pct(q.Confidence)})" : ""), PAD, y, INNER, DIM);

                    foreach (AgentCallOption o in q.Options)
                    {
                        bool picked = o.Id == q.Picked, used = q.Used.Length != 0 && o.Id == q.Used;
                        ushort hue = used ? GOLD : picked ? GREEN : BLUE;
                        AddText(Trim(o.Label, 22), PAD + 6, y, picked || used ? WHITE : GREY);
                        Bar(o.P, hue, y);
                        AddText(Pct(o.P) + (picked ? "  jev" : "") + (used ? "  code" : ""), BAR_X + BAR_W + 6, y, picked || used ? hue : GREY);
                        y += 16;
                    }

                    if (q.Used.Length != 0 && q.Used != q.Picked)
                    {
                        y = AddWrapped($"jev picked {q.LabelOf(q.Picked)}; code went with {q.LabelOf(q.Used)}", PAD + 6, y, INNER - 6, GOLD);
                    }

                    return y + 4;
                }
            }
        }

        private void Bar(float p, ushort hue, int y, int x = BAR_X)
        {
            p = Math.Clamp(p, 0f, 1f);
            _content.Add(new ColorBox(BAR_W, 9, BAR_BACK) { X = x, Y = y + 4 });

            if (p > 0.005f)
            {
                _content.Add(new ColorBox(Math.Max(2, (int) (BAR_W * p)), 9, hue) { X = x, Y = y + 4 });
            }
        }

        private int AddHeader(string text, int y)
        {
            _content.Add(new ColorBox(INNER, 1, DIM) { X = PAD, Y = y + 2 });

            return AddWrapped(text, PAD, y + 6, INNER, GOLD) + 2;
        }

        private void AddText(string text, int x, int y, ushort hue) => _content.Add(new Label(text, true, hue, 0, FONT) { X = x, Y = y });

        private int AddWrapped(string text, int x, int y, int width, ushort hue)
        {
            var label = new Label(text, true, hue, width, FONT) { X = x, Y = y };
            _content.Add(label);

            return y + Math.Max(label.Height, 16);
        }

        private static string Name(string kind) => kind == "routine" ? "hunt" : kind;

        private static string Trim(string s, int n) => s.Length <= n ? s : s.Substring(0, n - 1) + "…";

        private static string Pct(float p) => $"{Math.Round(Math.Clamp(p, 0f, 1f) * 100)}%";

        // Dollars to four places, or six for the fractions of a cent a Jev question costs.
        private static string Dollars(float d) => d >= 0.01f || d <= 0 ? $"${d:0.0000}" : $"${d:0.000000}";

        private static string Ago(uint time)
        {
            uint s = (Time.Ticks - time) / 1000;

            return s < 60 ? $"{s}s" : $"{s / 60}m";
        }

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
