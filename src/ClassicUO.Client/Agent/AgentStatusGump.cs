// SPDX-License-Identifier: BSD-2-Clause

using System.Text;
using ClassicUO.Game;
using ClassicUO.Game.GameObjects;
using ClassicUO.Game.UI.Controls;
using ClassicUO.Game.UI.Gumps;
using ClassicUO.Renderer;
using ClassicUO.Game.Scenes;
using Microsoft.Xna.Framework;

namespace ClassicUO.Agent
{
    // Small always-on-top panel: mode, what the agent is doing, the brain's last
    // decision and any suggestion waiting for the player.
    internal sealed class AgentStatusGump : Gump
    {
        private static Point _lastPosition = new Point(-1, -1);

        private readonly AgentController _agent;
        private readonly AlphaBlendControl _background;
        private uint _nextUpdate;
        private string _text = string.Empty;

        public AgentStatusGump(World world, AgentController agent) : base(world, 0, 0)
        {
            _agent = agent;
            CanMove = true;
            CanCloseWithEsc = false;
            CanCloseWithRightClick = false;
            AcceptMouseInput = true;
            AcceptKeyboardInput = false;
            LayerOrder = UILayer.Over;

            X = _lastPosition.X < 0 ? 20 : _lastPosition.X;
            Y = _lastPosition.Y < 0 ? 60 : _lastPosition.Y;
            Width = 200;
            Height = 40;

            Add(_background = new AlphaBlendControl(.6f) { Width = Width, Height = Height });
            WantUpdateSize = true;
        }

        public override void Update()
        {
            base.Update();

            if (IsDisposed || Time.Ticks < _nextUpdate)
            {
                return;
            }

            _nextUpdate = Time.Ticks + 200;

            var sb = new StringBuilder();
            sb.Append("Agent: ").Append(_agent.Mode.Name().ToUpperInvariant());
            sb.Append(AgentHost.BrainConnected ? "  brain: on" : "  brain: off");

            if (_agent.HumanActive)
            {
                sb.Append("  (you)");
            }

            sb.Append('\n');

            string doing = _agent.Bandaging ? "bandaging"
                : _agent.Fleeing ? "fleeing"
                : _agent.Engaged != 0 ? "fighting " + NameOf(_agent.Engaged)
                : _agent.LootCorpse != 0 ? "looting"
                : "idle";
            sb.Append(doing);

            if (!string.IsNullOrEmpty(_agent.BrainNote) && Time.Ticks - _agent.BrainNoteTime < 10000)
            {
                sb.Append('\n').Append("brain: ").Append(_agent.BrainNote);
            }

            if (_agent.Suggestion != null)
            {
                sb.Append('\n').Append("suggest: ").Append(_agent.Suggestion.Describe(World));

                if (_agent.Suggestion.Confidence >= 0)
                {
                    sb.Append($" ({_agent.Suggestion.Confidence:0.00})");
                }

                sb.Append("  [-agent accept]");
            }

            AgentStats s = _agent.Stats;
            sb.Append('\n').Append($"kills {s.Kills}  deaths {s.Deaths}  heals {s.Bandages + s.HealPotions}");

            _text = sb.ToString();
            Vector2 size = Fonts.Bold.MeasureString(_text);
            _background.Width = Width = (int) size.X + 16;
            _background.Height = Height = (int) size.Y + 12;
            WantUpdateSize = true;
        }

        private string NameOf(uint serial)
        {
            Mobile m = World.Mobiles.Get(serial);

            return m == null || string.IsNullOrEmpty(m.Name) ? "?" : m.Name.Trim();
        }

        public override bool AddToRenderLists(RenderLists renderLists, int x, int y, ref float layerDepthRef)
        {
            if (!base.AddToRenderLists(renderLists, x, y, ref layerDepthRef))
            {
                return false;
            }

            float layerDepth = layerDepthRef;
            Vector3 hue = ShaderHueTranslator.GetHueVector(0);
            string text = _text;

            renderLists.AddGumpNoAtlas
            (
                batcher =>
                {
                    batcher.DrawString(Fonts.Bold, text, x + 8, y + 6, hue, layerDepth);

                    return true;
                }
            );

            return true;
        }

        protected override void OnDragEnd(int x, int y)
        {
            base.OnDragEnd(x, y);
            _lastPosition = new Point(ScreenCoordinateX, ScreenCoordinateY);
        }
    }
}
