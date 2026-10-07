// SPDX-License-Identifier: BSD-2-Clause

using System;

namespace ClassicUO.Agent
{
    internal enum AgentMessage
    {
        None,
        BandageStarted,
        BandageEnded,
        CastFailed,       // the spell never started, or was interrupted before its cursor
        CastNotRecovered, // too soon after the last spell
        InstrumentPrompt  // a bard's song asks which instrument to play
    }

    // The server messages the agent acts on. Servers send most of them as cliloc numbers,
    // which are the same on every RunUO-family shard whatever the language, so those are
    // checked first; the English text is the fallback for servers that send plain text.
    internal static class AgentMessages
    {
        public static AgentMessage FromCliloc(uint cliloc)
        {
            switch (cliloc)
            {
                case 500956: // You begin applying the bandages.
                    return AgentMessage.BandageStarted;

                case 500969:  // You finish applying the bandages.
                case 500968:  // You apply the bandages, but they barely help.
                case 1010395: // You heal what little damage your patient had.
                case 1010058: // You have cured the target of all poisons.
                case 1010059: // You have been cured of all poisons.
                case 1010060: // You have failed to cure your target!
                case 500963:  // You did not stay close enough to heal your target.
                case 500955:  // That being is not damaged!
                case 500970:  // Bandages can not be used on that.
                case 500951:  // You cannot heal that.
                case 500961:  // Your fingers slip!
                case 500962:  // You were unable to finish your work before you died.
                case 1005000: // You cannot heal yourself in your current state.
                case 1010398: // You cannot heal that target in their current state.
                case 500965:  // You are able to resurrect your patient.
                case 500966:  // You are unable to resurrect your patient.
                    return AgentMessage.BandageEnded;

                case 502644: // You have not yet recovered from casting a spell.
                    return AgentMessage.CastNotRecovered;

                case 500617: // What instrument shall you play?
                    return AgentMessage.InstrumentPrompt;

                case 502642: // You are already casting a spell.
                case 502625: // Insufficient mana (for this spell).
                case 502630: // More reagents are needed for this spell.
                case 502643: // You can not cast a spell while frozen.
                case 502646: // You cannot cast a spell while frozen.
                case 500641: // Your concentration is disturbed, thus ruining thy spell.
                    return AgentMessage.CastFailed;

                default:
                    return AgentMessage.None;
            }
        }

        private static readonly string[] BandageEnd =
        {
            "finish applying the bandages", "barely help", "heal what little damage", "have been cured", "have cured",
            "did not stay close enough", "unable to", "not damaged", "stop applying", "bandages are not",
            "You cannot heal", "failed to cure", "be used on that", "resurrect"
        };

        private static readonly string[] CastFail =
        {
            "already casting a spell", "Insufficient mana", "More reagents are needed", "can not cast a spell while frozen",
            "cannot cast a spell", "concentration is disturbed"
        };

        public static AgentMessage FromText(string text)
        {
            if (string.IsNullOrEmpty(text))
            {
                return AgentMessage.None;
            }

            if (text.Contains("not yet recovered from casting", StringComparison.OrdinalIgnoreCase))
            {
                return AgentMessage.CastNotRecovered;
            }

            if (text.Contains("begin applying the bandages", StringComparison.OrdinalIgnoreCase))
            {
                return AgentMessage.BandageStarted;
            }

            if (text.Contains("What instrument shall you play", StringComparison.OrdinalIgnoreCase))
            {
                return AgentMessage.InstrumentPrompt;
            }

            foreach (string s in CastFail)
            {
                if (text.Contains(s, StringComparison.OrdinalIgnoreCase))
                {
                    return AgentMessage.CastFailed;
                }
            }

            foreach (string s in BandageEnd)
            {
                if (text.Contains(s, StringComparison.OrdinalIgnoreCase))
                {
                    return AgentMessage.BandageEnded;
                }
            }

            return AgentMessage.None;
        }
    }
}
