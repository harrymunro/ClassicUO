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

    // The three play states. Assist is "combat assist": you drive, the agent fights alongside
    // you and never walks the character. Auto plays on its own. The wire and profile name of
    // Assist stays "assist", so saved modes carry over; "combat" is accepted too.
    internal enum AgentMode
    {
        Off,
        Assist,
        Auto
    }

    // What combat assist takes on by itself: only what you attack, also anything attacking
    // you, or any monster nearby.
    internal enum AgentEngage
    {
        Follow,
        Defend,
        Nearby
    }

    internal static class AgentModes
    {
        public static readonly AgentBehavior[] AllBehaviors = Enum.GetValues<AgentBehavior>();

        // Combat assist: survival and fighting run on their own, moving never does, looting and
        // the rest are suggested. Auto: the agent plays.
        public static AgentAuthority PresetAuthority(AgentMode mode, AgentBehavior behavior)
        {
            switch (mode)
            {
                case AgentMode.Auto:
                    return AgentAuthority.Auto;

                case AgentMode.Assist:
                    switch (behavior)
                    {
                        case AgentBehavior.Heal:
                        case AgentBehavior.Cure:
                        case AgentBehavior.Potion:
                        case AgentBehavior.Fight:
                            return AgentAuthority.Auto;

                        case AgentBehavior.Move:
                            return AgentAuthority.Off;

                        default:
                            return AgentAuthority.Suggest;
                    }

                default:
                    return AgentAuthority.Off;
            }
        }

        public static string Name(this AgentMode mode) => mode.ToString().ToLowerInvariant();

        // For people: "combat assist" rather than the wire name.
        public static string Title(this AgentMode mode) => mode == AgentMode.Assist ? "combat assist" : mode.Name();

        public static string Name(this AgentEngage e) => e.ToString().ToLowerInvariant();

        public static string Title(this AgentEngage e) =>
            e switch
            {
                AgentEngage.Follow => "your target only",
                AgentEngage.Defend => "your target and attackers",
                _ => "anything nearby"
            };
        public static string Name(this AgentAuthority a) => a.ToString().ToLowerInvariant();
        public static string Name(this AgentBehavior b) => b.ToString().ToLowerInvariant();

        public static bool TryParse<T>(string value, out T result) where T : struct, Enum
        {
            if (typeof(T) == typeof(AgentMode) && string.Equals(value, "combat", StringComparison.OrdinalIgnoreCase))
            {
                value = nameof(AgentMode.Assist);
            }

            // Numbers parse as any enum value; only names are accepted.
            result = default;

            return !string.IsNullOrEmpty(value) && !char.IsDigit(value[0]) && Enum.TryParse(value, true, out result) && Enum.IsDefined(result);
        }
    }
}
