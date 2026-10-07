// SPDX-License-Identifier: BSD-2-Clause

using System.Collections.Generic;
using ClassicUO.Game;
using ClassicUO.Game.Data;
using ClassicUO.Game.GameObjects;

namespace ClassicUO.Agent
{
    // Pets, for a tamer whose pet does the fighting. The client can only tell a pet from any
    // other blue creature by the "can be renamed" flag in its status, which the server sends
    // when asked, so each non-hostile creature in sight is asked about once a minute. Orders go
    // out as the speech every player uses ("all kill", "all follow me"); the kill order's target
    // cursor is answered here with the creature the order named.
    internal sealed partial class AgentController
    {
        private const uint PET_TARGET_WINDOW_MS = 3000;
        private const uint STATUS_ASK_EVERY_MS = 60_000;

        private readonly Dictionary<uint, uint> _statusAsked = new Dictionary<uint, uint>();
        private readonly List<Mobile> _pets = new List<Mobile>();
        private uint _petKillTarget, _petKillUntil, _nextStatusAsk;

        public string PetOrder { get; private set; } = string.Empty;
        public uint PetOrderTarget { get; private set; }

        // The player's pets in sight, nearest first.
        public List<Mobile> Pets()
        {
            _pets.Clear();
            PlayerMobile p = _world.Player;

            foreach (Mobile m in _world.Mobiles.Values)
            {
                if (m != p && m.IsRenamable && !m.IsDead && !m.IsDestroyed && (m.Serial & 0x80000000) == 0)
                {
                    _pets.Add(m);
                }
            }

            _pets.Sort((a, b) => a.Distance.CompareTo(b.Distance));

            return _pets;
        }

        private void UpdatePets(uint now)
        {
            if (_petKillUntil != 0)
            {
                if (now > _petKillUntil)
                {
                    _petKillUntil = 0;
                }
                else if (_world.TargetManager.IsTargeting)
                {
                    _world.TargetManager.Target(_petKillTarget);
                    _petKillUntil = 0;
                }
            }

            // A kill order is done when its creature is.
            if (PetOrder == "kill" && _world.Mobiles.Get(PetOrderTarget) is not { IsDead: false })
            {
                PetOrder = "follow";
                PetOrderTarget = 0;
            }

            if (now < _nextStatusAsk)
            {
                return;
            }

            _nextStatusAsk = now + 500;

            foreach (Mobile m in _world.Mobiles.Values)
            {
                if (m == _world.Player || m.IsHuman || m.IsDead || m.IsRenamable || m.Distance > 12
                    || m.NotorietyFlag is not (NotorietyFlag.Innocent or NotorietyFlag.Ally)
                    || _statusAsked.TryGetValue(m.Serial, out uint at) && now - at < STATUS_ASK_EVERY_MS)
                {
                    continue;
                }

                _statusAsked[m.Serial] = now;
                GameActions.RequestMobileStatus(_world, m.Serial);

                break;
            }
        }

        // kill (a monster), follow, guard or stay. The brain may only set a pet on a monster.
        private (string, string) PetCommand(string order, uint target, bool manual)
        {
            if (Pets().Count == 0)
            {
                return ("failed", "no pet in sight");
            }

            switch (order)
            {
                case "kill":
                    Mobile m = _world.Mobiles.Get(target);

                    if (m == null || m.IsDead)
                    {
                        return ("failed", "no such creature");
                    }

                    if (!manual && !IsMonsterTarget(m))
                    {
                        Stats.Blocked++;

                        return ("blocked", "not a monster");
                    }

                    GameActions.Say("all kill");
                    _petKillTarget = target;
                    _petKillUntil = Time.Ticks + PET_TARGET_WINDOW_MS;

                    break;

                case "follow":
                    GameActions.Say("all follow me");

                    break;

                case "guard":
                    GameActions.Say("all guard me");

                    break;

                case "stay":
                    GameActions.Say("all stay");

                    break;

                default:
                    return ("failed", $"unknown pet order '{order}'");
            }

            PetOrder = order;
            PetOrderTarget = order == "kill" ? target : 0;
            Stats.PetOrders++;

            return ("done", string.Empty);
        }
    }
}
