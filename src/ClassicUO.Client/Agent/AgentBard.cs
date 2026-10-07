// SPDX-License-Identifier: BSD-2-Clause

using System;
using System.Collections.Generic;
using ClassicUO.Game;
using ClassicUO.Game.Data;
using ClassicUO.Game.GameObjects;
using ClassicUO.Utility;

namespace ClassicUO.Agent
{
    // A bard's songs: provocation (one monster against another), peacemaking (calm one, or
    // everyone around when aimed at the bard) and discordance (weaken one). Each is a skill use
    // followed by target cursors: provocation asks for two creatures, the others for one, and the
    // server first asks which instrument to play when none is chosen yet.
    internal static class AgentBard
    {
        public static readonly string[] Songs = { "Provocation", "Peacemaking", "Discordance" };

        public static bool IsSong(string skill) => Array.IndexOf(Songs, skill) >= 0;

        // Drums, tambourines, harps, lutes and the bamboo flute, both facings where they have two.
        public static bool IsInstrument(ushort graphic) =>
            graphic is 0x0E9C or 0x0E9D or 0x0E9E or 0x0EB1 or 0x0EB2 or 0x0EB3 or 0x0EB4 or 0x2805 or 0x2807;
    }

    internal sealed partial class AgentController
    {
        private const uint SONG_CURSOR_WINDOW_MS = 4000;

        private readonly Queue<uint> _songTargets = new Queue<uint>();
        private uint _songUntil;
        private bool _instrumentAsked;

        // Only monsters, except peacemaking aimed at the bard itself (it calms everyone around).
        private (string, string) Song(int skill, AgentAction a)
        {
            if (FindInstrument() == null)
            {
                return ("failed", "no instrument in the pack");
            }

            foreach (uint t in a.Targets)
            {
                bool self = t == uint.MaxValue && a.Name == "Peacemaking";

                if (!a.Manual && !self && (_world.Mobiles.Get(t) is not Mobile m || !IsMonsterTarget(m)))
                {
                    Stats.Blocked++;

                    return ("blocked", "a song may only be aimed at monsters");
                }
            }

            _songTargets.Clear();

            foreach (uint t in a.Targets)
            {
                _songTargets.Enqueue(t);
            }

            _instrumentAsked = false;
            _songUntil = Time.Ticks + SONG_CURSOR_WINDOW_MS;
            Stats.Songs++;
            GameActions.UseSkill(skill);

            return ("done", string.Empty);
        }

        private void UpdateSong(uint now)
        {
            if (_songTargets.Count == 0)
            {
                return;
            }

            if (now > _songUntil)
            {
                _songTargets.Clear();

                return;
            }

            if (!_world.TargetManager.IsTargeting)
            {
                return;
            }

            if (_instrumentAsked && FindInstrument() is Item instrument)
            {
                _instrumentAsked = false;
                _world.TargetManager.Target(instrument.Serial);
            }
            else
            {
                uint t = _songTargets.Dequeue();
                _world.TargetManager.Target(t == uint.MaxValue ? _world.Player.Serial : t);
            }

            _songUntil = now + SONG_CURSOR_WINDOW_MS;
        }

        public Item FindInstrument()
        {
            Item backpack = _world.Player?.FindItemByLayer(Layer.Backpack);

            for (LinkedObject i = backpack?.Items; i != null; i = i.Next)
            {
                if (i is Item it && AgentBard.IsInstrument(it.Graphic))
                {
                    return it;
                }
            }

            return null;
        }
    }
}
