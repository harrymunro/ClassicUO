// SPDX-License-Identifier: BSD-2-Clause

using System;

namespace ClassicUO.Agent
{
    // What an agent action is for. Each behaviour has its own authority so the
    // player can, for example, let the agent heal on its own but only suggest fights.
    internal enum AgentBehavior
    {
        Heal,   // bandages
        Cure,   // cure potions
        Potion, // heal potions
        Fight,  // attack, engage, war mode
        Loot,   // open corpses, take items
        Move,   // walk, flee
        Misc    // say, use, target
    }

    internal enum AgentAuthority
    {
        Off,
        Suggest,
        Auto
    }

    internal enum AgentMode
    {
        Off,
        Assist,
        Auto
    }

    internal static class AgentModes
    {
        public static readonly AgentBehavior[] AllBehaviors = Enum.GetValues<AgentBehavior>();

        // Assist: survival runs on its own, everything else is suggested.
        // Auto: the agent plays.
        public static AgentAuthority PresetAuthority(AgentMode mode, AgentBehavior behavior)
        {
            switch (mode)
            {
                case AgentMode.Auto:
                    return AgentAuthority.Auto;

                case AgentMode.Assist:
                    return behavior == AgentBehavior.Heal || behavior == AgentBehavior.Cure || behavior == AgentBehavior.Potion
                        ? AgentAuthority.Auto
                        : AgentAuthority.Suggest;

                default:
                    return AgentAuthority.Off;
            }
        }

        public static string Name(this AgentMode mode) => mode.ToString().ToLowerInvariant();
        public static string Name(this AgentAuthority a) => a.ToString().ToLowerInvariant();
        public static string Name(this AgentBehavior b) => b.ToString().ToLowerInvariant();

        public static bool TryParse<T>(string value, out T result) where T : struct, Enum
        {
            return Enum.TryParse(value, true, out result) && Enum.IsDefined(result);
        }
    }
}
