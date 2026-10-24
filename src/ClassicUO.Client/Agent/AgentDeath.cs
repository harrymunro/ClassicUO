// SPDX-License-Identifier: BSD-2-Clause

using System;
using System.Collections.Generic;
using ClassicUO.Game;
using ClassicUO.Game.Data;
using ClassicUO.Game.GameObjects;
using ClassicUO.Network;
using ClassicUO.Utility;

namespace ClassicUO.Agent
{
    // After a death (cuo-4b3): where it happened, the corpse that is the character's own, what it
    // was wearing, and the resurrection offer. The brain's resurrect and recover_corpse skills
    // (session.py) walk the ghost to a healer and back to the corpse; this side answers the
    // healer's gump in auto mode, lets the brain take from its own corpse, and puts back on what
    // was worn (pre-AOS servers leave that to the player; AOS-era ones re-equip on opening).
    internal sealed partial class AgentController
    {
        private const uint WORN_EVERY_MS = 2000;
        private const uint REWEAR_EVERY_MS = 700;

        private readonly List<(uint Serial, Layer Layer)> _wornNow = new List<(uint, Layer)>();
        private readonly List<(uint Serial, Layer Layer)> _wornAtDeath = new List<(uint, Layer)>();
        private readonly Queue<(uint Serial, Layer Layer)> _rewear = new Queue<(uint, Layer)>();
        private uint _wornAt, _rewearAt;

        public (int X, int Y, uint At, ushort Body) LastDeath { get; private set; }
        // The own corpse's serial once seen: it keeps it as bones, which IsCorpse doesn't count (cuo-m3n.6).
        public uint CorpseSerial { get; private set; }
        public int Resurrections { get; private set; }

        // Called each tick while alive: what is worn, so a death can tell what to put back on.
        private void NoteWorn(uint now)
        {
            PlayerMobile p = _world.Player;

            if (p.IsDead || now < _wornAt)
            {
                return;
            }

            _wornAt = now + WORN_EVERY_MS;
            _wornNow.Clear();

            for (Layer l = Layer.OneHanded; l <= Layer.Legs; l++)
            {
                if (l is Layer.Hair or Layer.Beard or Layer.Backpack or Layer.Face)
                {
                    continue;
                }

                if (p.FindItemByLayer(l) is Item it)
                {
                    _wornNow.Add((it.Serial, l));
                }
            }
        }

        // From the death check in Update.
        private void OnDied()
        {
            PlayerMobile p = _world.Player;
            LastDeath = (p.X, p.Y, Time.Ticks, _bodyAlive);
            CorpseSerial = 0;
            _wornAtDeath.Clear();
            _wornAtDeath.AddRange(_wornNow);
            _rewear.Clear();
        }

        private ushort _bodyAlive;

        private void NoteBody()
        {
            if (!_world.Player.IsDead)
            {
                _bodyAlive = _world.Player.Graphic;
            }
        }

        // The character's own corpse: one lying within two tiles of where it died, since then, with
        // its body. Other players' corpses stay out of reach of the brain's take.
        public bool OwnCorpse(Item c)
        {
            if (c == null)
            {
                return false;
            }

            bool own = IsOwnRemains(c.Serial, c.IsCorpse, c.Amount, c.X, c.Y, CorpseSerial, LastDeath);

            if (own && c.IsCorpse)
            {
                CorpseSerial = c.Serial;
            }

            return own;
        }

        // ...and once seen it is known by its serial, and stays its own as bones: on ModernUO and RunUO a corpse
        // turns to bones after 7 minutes with everything still on it for 7 more. Every walk back that came after
        // 7 minutes found "the corpse is gone" (cuo-m3n.6).
        internal static bool IsOwnRemains(uint serial, bool corpse, ushort amount, int x, int y, uint known,
                                          (int X, int Y, uint At, ushort Body) death)
        {
            if (death.At == 0)
            {
                return false;
            }

            if (known != 0 && serial == known)
            {
                return true;
            }

            return corpse && Math.Max(Math.Abs(x - death.X), Math.Abs(y - death.Y)) <= 2 && (death.Body == 0 || amount == death.Body);
        }

        // The healer's (or an ankh's) offer to bring the ghost back. Answered only in auto mode with
        // misc on auto: with the player driving it is theirs to answer. Generic layout: CONTINUE is
        // button 1. A priced healer's: the "pay" radio (switch 1) and the OK button 2.
        private bool OnResurrectGump(uint sender, uint gumpId, string layout)
        {
            if (layout == null || !_world.Player.IsDead || Mode != AgentMode.Auto || GetAuthority(AgentBehavior.Misc) != AgentAuthority.Auto)
            {
                return false;
            }

            if (layout.Contains("1060017"))
            {
                GameActions.ReplyGump(sender, gumpId, 2, new uint[] { 1 }, Array.Empty<Tuple<ushort, string>>());
                Journal.AddAgentEvent("accepting resurrection, paying the healer's price from the bank");

                return true;
            }

            if (layout.Contains("1011022") && layout.Contains("1011011"))
            {
                ReplyGump(sender, gumpId, 1);
                Journal.AddAgentEvent("accepting resurrection");

                return true;
            }

            return false;
        }

        // From the death check: alive again.
        private void OnResurrected()
        {
            Resurrections++;
            Journal.AddAgentEvent("resurrected");
        }

        // rewear: put back on what was worn at death, from the pack (after taking it from the corpse
        // on a pre-AOS server). One item at a time, as an assistant's dress macro does.
        private (string, string) Rewear()
        {
            Item pack = _world.Player.FindItemByLayer(Layer.Backpack);

            if (pack == null || _wornAtDeath.Count == 0)
            {
                return ("failed", "nothing remembered as worn");
            }

            _rewear.Clear();
            int n = 0;

            foreach ((uint serial, Layer layer) in _wornAtDeath)
            {
                if (_world.Items.Get(serial) is Item it && it.Container == pack.Serial && _world.Player.FindItemByLayer(layer) == null)
                {
                    _rewear.Enqueue((serial, layer));
                    n++;
                }
            }

            _rewearAt = 0;

            return n == 0 ? ("done", "nothing to put back on") : ("done", $"putting back on {n} items");
        }

        private void UpdateRewear(uint now)
        {
            if (_rewear.Count == 0 || now < _rewearAt || _world.Player.IsDead)
            {
                return;
            }

            _rewearAt = now + REWEAR_EVERY_MS;
            (uint serial, Layer layer) = _rewear.Dequeue();

            if (_world.Items.Get(serial) != null && _world.Player.FindItemByLayer(layer) == null)
            {
                NetClient.Socket.Send_PickUpRequest(serial, 1);
                NetClient.Socket.Send_EquipRequest(serial, layer, _world.Player.Serial);
            }
        }

        // Each tick from Update.
        private void UpdateDeath(uint now)
        {
            NoteBody();
            NoteWorn(now);
            UpdateRewear(now);
        }
    }
}
