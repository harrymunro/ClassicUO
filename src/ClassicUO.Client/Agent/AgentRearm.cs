// SPDX-License-Identifier: BSD-2-Clause

using ClassicUO.Game.Data;
using ClassicUO.Game.GameObjects;
using ClassicUO.Network;

namespace ClassicUO.Agent
{
    // A warrior-mage's weapon. Servers in the RunUO family take a weapon out of the caster's
    // hands into the pack when a spell starts, so after each cast the agent lifts it and puts it
    // back on, as an assistant's "arm" macro does.
    internal sealed partial class AgentController
    {
        private const uint REARM_RETRY_MS = 1200;

        private uint _rearmWeapon, _rearmAt;
        private Layer _rearmLayer;
        private int _rearmTries;

        // The last weapon seen in hand, so auto mode can put it back on if it ends up in the pack,
        // and errands never bank or sell it.
        private uint _heldWeapon, _nextHandsCheck;
        private Layer _heldLayer;

        public uint HeldWeapon => _heldWeapon;

        // Called as a spell starts. A recall through a runebook's gump is a magery cast the client
        // doesn't time, and equipping during a cast breaks it, so that waits until it is through.
        private void NoteWeaponForRearm(uint afterMs = 400)
        {
            PlayerMobile p = _world.Player;
            Item weapon = p.FindItemByLayer(Layer.OneHanded) ?? p.FindItemByLayer(Layer.TwoHanded);

            if (weapon == null || weapon.Graphic == AgentSpells.SPELLBOOK_GRAPHIC)
            {
                return;
            }

            _rearmWeapon = weapon.Serial;
            _rearmLayer = weapon.Layer;
            _rearmTries = 0;
            _rearmAt = Time.Ticks + afterMs;
        }

        // In auto mode the weapon goes back in hand whenever it is found in the pack with the hands
        // empty: a soak run's warrior recalled by runebook at the start (magery drops the weapon,
        // even from a runebook) and fought bare-handed for 16 minutes.
        private void KeepWeaponInHand(uint now)
        {
            PlayerMobile p = _world.Player;
            Item held = p.FindItemByLayer(Layer.OneHanded) ?? p.FindItemByLayer(Layer.TwoHanded);

            if (held != null && held.Graphic != AgentSpells.SPELLBOOK_GRAPHIC && !AgentSpells.IsBook(held))
            {
                _heldWeapon = held.Serial;
                _heldLayer = held.Layer;

                return;
            }

            if (held != null || _heldWeapon == 0 || _rearmWeapon != 0 || now < _nextHandsCheck || Mode != AgentMode.Auto
                || HumanActive || _castSpell != 0 || _world.TargetManager.IsTargeting || p.IsDead)
            {
                return;
            }

            _nextHandsCheck = now + 3000;
            Item weapon = _world.Items.Get(_heldWeapon);

            if (weapon != null && p.FindItemByLayer(Layer.Backpack) is Item pack && weapon.Container == pack.Serial)
            {
                NetClient.Socket.Send_PickUpRequest(weapon.Serial, 1);
                NetClient.Socket.Send_EquipRequest(weapon.Serial, _heldLayer, p.Serial);
                Stats.Rearms++;
            }
        }

        private void UpdateRearm(uint now)
        {
            KeepWeaponInHand(now);

            if (_rearmWeapon == 0 || now < _rearmAt || _castSpell != 0 || _world.TargetManager.IsTargeting)
            {
                return;
            }

            PlayerMobile p = _world.Player;
            Item weapon = _world.Items.Get(_rearmWeapon);

            if (weapon == null || weapon.Container == p.Serial || _rearmTries >= 3)
            {
                _rearmWeapon = 0;

                return;
            }

            if (p.FindItemByLayer(Layer.Backpack) is Item pack && weapon.Container == pack.Serial)
            {
                NetClient.Socket.Send_PickUpRequest(weapon.Serial, 1);
                NetClient.Socket.Send_EquipRequest(weapon.Serial, _rearmLayer, p.Serial);
                Stats.Rearms++;
            }

            _rearmTries++;
            _rearmAt = now + REARM_RETRY_MS;
        }
    }
}
