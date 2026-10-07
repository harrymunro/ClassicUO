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

        // Called as a spell starts.
        private void NoteWeaponForRearm()
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
            _rearmAt = Time.Ticks + 400;
        }

        private void UpdateRearm(uint now)
        {
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
