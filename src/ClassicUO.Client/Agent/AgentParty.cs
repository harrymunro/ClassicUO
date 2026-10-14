// SPDX-License-Identifier: BSD-2-Clause

using ClassicUO.Game;
using ClassicUO.Game.GameObjects;
using ClassicUO.Game.Managers;
using ClassicUO.Game.UI.Gumps;
using ClassicUO.Network;

namespace ClassicUO.Agent
{
    // UO's own party (cuo-cym.3). A fleet's party was a party only in the brain's store: to the game
    // its five were strangers, so each was warned "Looting this monster corpse will be a criminal
    // act!" at a companion's kill, their callouts were overhead speech for the whole screen, and the
    // client's party list was empty. These are the party's own acts, as a player does them from the
    // party gump: invite someone, answer an invitation, leave, talk in party chat, and say whether
    // the party may loot one's corpse. Who to invite and whose invitation to accept is the brain's
    // to decide (a real shard's strangers are declined there); the client only does as asked.
    internal sealed partial class AgentController
    {
        private const uint PARTY_TARGET_WINDOW_MS = 3000;
        private const uint PARTY_WHO_CLILOC = 1005454; // "Who would you like to add to your party?"

        private uint _partyInvite, _partyInviteUntil;
        private bool _partyAsked;

        // The server's question that comes with the invitation's target cursor: only after it is a
        // cursor the invitation's (a Recall begun in the same seconds has a neutral cursor too).
        private void OnPartyCliloc(uint cliloc)
        {
            if (cliloc == PARTY_WHO_CLILOC && _partyInvite != 0)
            {
                _partyAsked = true;
            }
        }

        private (string, string) PartyAct(AgentAction a)
        {
            PartyManager party = _world.Party;
            PlayerMobile p = _world.Player;

            switch (a.Kind)
            {
                case "invite":
                    if (a.Target == 0 || a.Target == p.Serial || _world.Mobiles.Get(a.Target) is not { IsHuman: true })
                    {
                        return ("failed", "nobody there to invite");
                    }

                    if (party.Contains(a.Target))
                    {
                        return ("done", "already in the party");
                    }

                    if (party.Leader != 0 && party.Leader != p.Serial)
                    {
                        return ("failed", "only the party's leader invites");
                    }

                    if (_world.TargetManager.IsTargeting)
                    {
                        return ("failed", "a target cursor is up");
                    }

                    // The server answers with a target cursor ("Who would you like to add to your
                    // party?", 8 tiles); UpdateParty gives it the one asked for.
                    _partyInvite = a.Target;
                    _partyInviteUntil = Time.Ticks + PARTY_TARGET_WINDOW_MS;
                    _partyAsked = false;
                    GameActions.RequestPartyInviteByTarget();

                    return ("done", string.Empty);

                case "accept":
                case "decline":
                    if (party.Inviter == 0)
                    {
                        return ("failed", "no invitation");
                    }

                    // With a target: only that one's invitation.
                    if (a.Target != 0 && a.Target != party.Inviter)
                    {
                        return ("failed", "the invitation is from someone else");
                    }

                    if (a.Kind == "accept")
                    {
                        GameActions.RequestPartyAccept(party.Inviter);
                    }
                    else
                    {
                        NetClient.Socket.Send_PartyDecline(party.Inviter);
                        UIManager.GetGump<PartyInviteGump>()?.Dispose();
                    }

                    party.Inviter = 0;

                    return ("done", string.Empty);

                case "leave":
                    if (party.Leader == 0)
                    {
                        return ("done", "not in a party");
                    }

                    GameActions.RequestPartyQuit(p);

                    return ("done", string.Empty);

                case "say":
                    if (string.IsNullOrWhiteSpace(a.Text))
                    {
                        return ("failed", "nothing to say");
                    }

                    if (party.Leader == 0)
                    {
                        return ("failed", "not in a party");
                    }

                    GameActions.SayParty(a.Text);

                    return ("done", string.Empty);

                case "loot":
                    if (party.Leader == 0)
                    {
                        return ("failed", "not in a party");
                    }

                    GameActions.RequestPartyLootState(a.On);
                    party.CanLoot = a.On;

                    return ("done", string.Empty);

                default:
                    return ("failed", $"unknown party act '{a.Kind}': invite, accept, decline, leave, say or loot");
            }
        }

        // The invitation's target cursor, answered with the one the act named: the neutral cursor
        // that came with the server's question, and no other (a spell's would aim it at a companion).
        private void UpdateParty(uint now)
        {
            if (_partyInvite == 0)
            {
                return;
            }

            if (now > _partyInviteUntil)
            {
                _partyInvite = 0;
            }
            else if (_partyAsked && _world.TargetManager.IsTargeting && _world.TargetManager.TargetingType == TargetType.Neutral)
            {
                _world.TargetManager.Target(_partyInvite);
                _partyInvite = 0;
            }
        }
    }
}
