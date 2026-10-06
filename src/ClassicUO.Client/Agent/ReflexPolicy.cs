// SPDX-License-Identifier: BSD-2-Clause

namespace ClassicUO.Agent
{
    internal enum ReflexAction
    {
        None,
        BandageSelf,
        DrinkHeal,
        DrinkCure,
        CastHeal,
        CastGreaterHeal,
        CastCure
    }

    // Everything the reflex layer looks at, captured once per tick so the
    // decision itself is a pure function that tests can drive directly.
    internal struct ReflexInput
    {
        public uint Now;
        public bool Dead;
        public int HitsPercent;
        public bool Poisoned;
        public int Bandages;
        public int HealPotions;
        public int CurePotions;
        public bool Bandaging;
        public uint LastBandageAttempt;
        public uint LastHealPotion;
        public uint LastCurePotion;
        public AgentAuthority Heal;
        public AgentAuthority Cure;
        public AgentAuthority Potion;

        // Spells: whether each can be cast now (in the book, mana, reagents), and
        // whether the agent is free to start a spell (not casting, recovered, no cursor up).
        public bool CanCastHeal;
        public bool CanCastGreaterHeal;
        public bool CanCastCure;
        public bool CastReady;
    }

    internal sealed class ReflexSettings
    {
        public int BandageBelowPercent = 85;
        public int HealPotionBelowPercent = 40;
        public int CurePotionBelowPercent = 70;
        public uint HealPotionCooldownMs = 10_500;
        public uint CurePotionCooldownMs = 2_000;
        public uint BandageRetryMs = 1_500;

        // Healing spells are for characters without bandages: Greater Heal when badly
        // hurt, Heal for lighter wounds (cheaper, and quick to cast).
        public int SpellHealBelowPercent = 65;
        public int GreaterHealBelowPercent = 50;
    }

    internal static class ReflexPolicy
    {
        // Returns the action to take this tick, and the authority it was decided
        // under: Auto means do it, Suggest means only tell the player.
        public static (ReflexAction Action, AgentAuthority Authority) Decide(in ReflexInput s, ReflexSettings cfg)
        {
            if (s.Dead)
            {
                return (ReflexAction.None, AgentAuthority.Off);
            }

            // A heal potion is the emergency button: it does not wait for bandages.
            if (s.Potion != AgentAuthority.Off
                && s.HitsPercent <= cfg.HealPotionBelowPercent
                && s.HealPotions > 0
                && Elapsed(s.Now, s.LastHealPotion) >= cfg.HealPotionCooldownMs)
            {
                return (ReflexAction.DrinkHeal, s.Potion);
            }

            // Poison stops bandages healing, so cure first once it starts to hurt,
            // or straight away when there are no bandages to cure it with.
            if (s.Cure != AgentAuthority.Off
                && s.Poisoned
                && s.CurePotions > 0
                && (s.HitsPercent <= cfg.CurePotionBelowPercent || s.Bandages == 0)
                && Elapsed(s.Now, s.LastCurePotion) >= cfg.CurePotionCooldownMs)
            {
                return (ReflexAction.DrinkCure, s.Cure);
            }

            if (s.Heal != AgentAuthority.Off
                && (s.HitsPercent < cfg.BandageBelowPercent || s.Poisoned)
                && !s.Bandaging
                && s.Bandages > 0
                && Elapsed(s.Now, s.LastBandageAttempt) >= cfg.BandageRetryMs)
            {
                return (ReflexAction.BandageSelf, s.Heal);
            }

            if (!s.CastReady)
            {
                return (ReflexAction.None, AgentAuthority.Off);
            }

            // Poison with no cure potion to hand (none, or on cooldown) and no bandage
            // working on it: cast Cure. Heal spells do not work on a poisoned target.
            if (s.Cure != AgentAuthority.Off && s.Poisoned && s.CanCastCure && !s.Bandaging)
            {
                return (ReflexAction.CastCure, s.Cure);
            }

            if (s.Heal != AgentAuthority.Off && !s.Poisoned && !s.Bandaging && s.Bandages == 0)
            {
                if (s.HitsPercent < cfg.GreaterHealBelowPercent && s.CanCastGreaterHeal)
                {
                    return (ReflexAction.CastGreaterHeal, s.Heal);
                }

                if (s.HitsPercent < cfg.SpellHealBelowPercent && (s.CanCastHeal || s.CanCastGreaterHeal))
                {
                    return (s.CanCastHeal ? ReflexAction.CastHeal : ReflexAction.CastGreaterHeal, s.Heal);
                }
            }

            return (ReflexAction.None, AgentAuthority.Off);
        }

        // Time.Ticks starts near zero, so "never" is stored as 0 and treated as long ago.
        private static uint Elapsed(uint now, uint since) => since == 0 ? uint.MaxValue : now - since;
    }
}
