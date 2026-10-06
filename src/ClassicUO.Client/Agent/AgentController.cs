// SPDX-License-Identifier: BSD-2-Clause

using System;
using System.Collections.Generic;
using System.Text;
using ClassicUO.Configuration;
using ClassicUO.Game;
using ClassicUO.Game.Data;
using ClassicUO.Game.GameObjects;
using ClassicUO.Game.Managers;
using ClassicUO.Game.UI.Gumps;
using ClassicUO.Network;
using ClassicUO.Utility;
using Microsoft.Xna.Framework;

namespace ClassicUO.Agent
{
    // The in-client half of the agent. Runs on the game thread from GameScene.Update.
    //
    // It owns the deterministic parts: reflexes (bandage, potions), carrying out
    // actions the brain picked (pursuing a target, looting a corpse, fleeing), the
    // per-behaviour authority that decides whether a brain action runs, is suggested
    // to the player or is refused, and pausing when the player takes the controls.
    // Choosing *what* to do is left to the brain over RPC.
    internal sealed class AgentController
    {
        public const ushort BANDAGE_GRAPHIC = 0x0E21;
        public const ushort HEAL_POTION_GRAPHIC = 0x0F0C;
        public const ushort CURE_POTION_GRAPHIC = 0x0F07;
        public const ushort REFRESH_POTION_GRAPHIC = 0x0F0B;
        public const ushort STRENGTH_POTION_GRAPHIC = 0x0F09;
        public const ushort AGILITY_POTION_GRAPHIC = 0x0F08;

        private const uint SUGGESTION_TTL_MS = 8000;
        private const uint REFLEX_INTERVAL_MS = 100;
        private const uint PURSUIT_INTERVAL_MS = 350;
        private const uint GRAB_INTERVAL_MS = 1000;
        private const int GRAB_TRIES = 4;
        private const uint LOOT_TIMEOUT_MS = 15000;

        private static readonly string[] BandageEndMessages =
        {
            "finish applying the bandages",
            "barely help",
            "heal what little damage",
            "have been cured",
            "have cured",
            "did not stay close enough",
            "unable to",
            "not damaged",
            "stop applying",
            "bandages are not",
            "You cannot heal",
            "failed to cure",
            "be used on that",
            "resurrect"
        };

        private readonly World _world;
        private readonly AgentAuthority[] _authority = new AgentAuthority[AgentModes.AllBehaviors.Length];
        private readonly Queue<uint> _takeQueue = new Queue<uint>();
        private readonly Dictionary<uint, int> _takeTries = new Dictionary<uint, int>();
        private bool _profileLoaded;
        private bool _wasDead;

        private uint _lastHumanInput;
        private uint _nextReflex;
        private uint _bandagingUntil;
        private uint _lastBandageAttempt, _lastHealPotion, _lastCurePotion;
        private ReflexAction _lastHintedReflex;

        private uint _engaged;
        private uint _nextPursuit, _lastAttackSent;
        private int _engagedLastX, _engagedLastY;

        private uint _lootCorpse;
        private uint _lootStarted, _lootOpenedAt, _nextLootStep, _lootIdleSince;
        private uint _nextGrab;

        private uint _fleeUntil;
        private bool _agentWalking;

        private AgentStatusGump _gump;

        public AgentController(World world)
        {
            _world = world;
            Journal = new AgentJournal(world);
            SetMode(AgentMode.Off);
        }

        public AgentMode Mode { get; private set; }
        public ReflexSettings Reflexes { get; } = new ReflexSettings();
        public AgentJournal Journal { get; }
        public AgentStats Stats { get; } = new AgentStats();
        public uint HumanPauseMs { get; set; } = 4000;

        public AgentAction Suggestion { get; private set; }
        public uint SuggestionTime { get; private set; }

        // Latest decision summary the brain reported, shown in the overlay.
        public string BrainNote { get; private set; } = string.Empty;
        public uint BrainNoteTime { get; private set; }

        // The player's own words on how to play ("never flee", "loot only valuables").
        // The brain reads it with every decision and turns it into policy settings.
        public string Strategy { get; private set; } = string.Empty;
        public int StrategyRevision { get; private set; }

        public uint Engaged => _engaged;
        public uint LootCorpse => _lootCorpse;
        public bool Bandaging => _bandagingUntil > Time.Ticks;

        // Heal potions share a cooldown, so having one is not the same as being able to drink it.
        public uint HealPotionReadyInMs =>
            _lastHealPotion == 0 || Time.Ticks - _lastHealPotion >= Reflexes.HealPotionCooldownMs
                ? 0
                : Reflexes.HealPotionCooldownMs - (Time.Ticks - _lastHealPotion);
        public bool Fleeing => _fleeUntil > Time.Ticks;
        public bool HumanActive => _lastHumanInput != 0 && Time.Ticks - _lastHumanInput < HumanPauseMs;
        public uint LastHumanInput => _lastHumanInput;

        public AgentAuthority GetAuthority(AgentBehavior b) => _authority[(int) b];

        public void SetMode(AgentMode mode)
        {
            EnsureProfileLoaded();
            Mode = mode;

            foreach (AgentBehavior b in AgentModes.AllBehaviors)
            {
                _authority[(int) b] = AgentModes.PresetAuthority(mode, b);
            }

            if (mode == AgentMode.Off)
            {
                ClearTasks();
                Suggestion = null;
            }

            SaveToProfile();
        }

        public void SetAuthority(AgentBehavior b, AgentAuthority a)
        {
            EnsureProfileLoaded();
            _authority[(int) b] = a;
            SaveToProfile();
        }

        // The player touched the controls: hand them back. Survival reflexes keep running.
        public void NoteHumanInput()
        {
            _lastHumanInput = Time.Ticks;

            if (_agentWalking && _world.Player != null && _world.Player.Pathfinder.AutoWalking)
            {
                _world.Player.Pathfinder.StopAutoWalk();
            }

            _agentWalking = false;
        }

        private bool WalkTo(int x, int y, int z, int distance)
        {
            bool ok = _world.Player.Pathfinder.WalkTo(x, y, z, distance);
            _agentWalking |= ok;

            return ok;
        }

        public void NoteBrain(string text)
        {
            BrainNote = text ?? string.Empty;
            BrainNoteTime = Time.Ticks;
        }

        // Called by the 0xAF death packet before the mobile is turned into a corpse.
        public void OnMobileDied(uint serial)
        {
            if (serial != 0 && (serial == _engaged || serial == _world.TargetManager.LastAttack))
            {
                Stats.Kills++;
                Journal.AddAgentEvent($"killed 0x{serial:X8}");

                if (serial == _engaged)
                {
                    _engaged = 0;
                }
            }
        }

        public void OnMessage(string text)
        {
            if (string.IsNullOrEmpty(text))
            {
                return;
            }

            if (text.Contains("begin applying the bandages", StringComparison.OrdinalIgnoreCase))
            {
                _bandagingUntil = Time.Ticks + 15000;

                return;
            }

            if (_bandagingUntil != 0)
            {
                foreach (string end in BandageEndMessages)
                {
                    if (text.Contains(end, StringComparison.OrdinalIgnoreCase))
                    {
                        _bandagingUntil = 0;

                        break;
                    }
                }
            }
        }

        public void Update()
        {
            if (!_world.InGame || _world.Player == null)
            {
                return;
            }

            EnsureProfileLoaded();

            uint now = Time.Ticks;
            PlayerMobile player = _world.Player;

            if (player.IsDead != _wasDead)
            {
                _wasDead = player.IsDead;

                if (_wasDead)
                {
                    Stats.Deaths++;
                    ClearTasks();
                    Journal.AddAgentEvent("player died");
                }
            }

            if (_agentWalking && !player.Pathfinder.AutoWalking)
            {
                _agentWalking = false;
            }

            if (Suggestion != null && now - SuggestionTime > SUGGESTION_TTL_MS)
            {
                Suggestion = null;
            }

            UpdateGump();

            if (Mode == AgentMode.Off && _engaged == 0 && _lootCorpse == 0 && _takeQueue.Count == 0)
            {
                return;
            }

            if (now >= _nextReflex)
            {
                _nextReflex = now + REFLEX_INTERVAL_MS;
                RunReflexes(now);
            }

            UpdateEngagement(now);
            UpdateLoot(now);
            UpdateTakes(now);
        }

        // ---------------------------------------------------------------- actions

        // Entry point for actions from RPC. Applies authority and the human pause,
        // then runs the action. Returns (status, detail) for the reply.
        public (string Status, string Detail) Request(AgentAction a)
        {
            if (!_world.InGame || _world.Player == null)
            {
                return ("failed", "not in game");
            }

            if (!a.Manual)
            {
                Stats.BrainActions++;
                AgentAuthority auth = GetAuthority(a.Behavior);

                if (auth == AgentAuthority.Off)
                {
                    Stats.Blocked++;

                    return ("blocked", $"{a.Behavior.Name()} is off");
                }

                if (auth == AgentAuthority.Suggest)
                {
                    Suggest(a);

                    return ("suggested", a.Describe(_world));
                }

                if (HumanActive && a.Behavior != AgentBehavior.Heal && a.Behavior != AgentBehavior.Cure && a.Behavior != AgentBehavior.Potion)
                {
                    Stats.Deferred++;

                    return ("deferred", "player is in control");
                }
            }

            return Execute(a);
        }

        public bool AcceptSuggestion()
        {
            AgentAction a = Suggestion;

            if (a == null)
            {
                return false;
            }

            Suggestion = null;
            a.Manual = true;
            Stats.Accepted++;
            (string status, string detail) = Execute(a);
            Print($"{a.Describe(_world)}: {status}{(string.IsNullOrEmpty(detail) ? "" : " (" + detail + ")")}");

            return true;
        }

        private void Suggest(AgentAction a)
        {
            bool isNew = Suggestion == null || Suggestion.Verb != a.Verb || Suggestion.Target != a.Target;
            Suggestion = a;
            SuggestionTime = Time.Ticks;

            if (isNew)
            {
                Stats.Suggestions++;
                string conf = a.Confidence >= 0 ? $" ({a.Confidence:0.00})" : string.Empty;
                _world.Player.AddMessage(MessageType.Regular, $"agent: {a.Describe(_world)}?{conf}", 3, 0x0035, true, TextType.CLIENT);
            }
        }

        private (string, string) Execute(AgentAction a)
        {
            PlayerMobile player = _world.Player;

            switch (a.Verb)
            {
                case "attack":
                    return Attack(a.Target, a.Manual);

                case "war_mode":
                    GameActions.RequestWarMode(player, a.On);

                    return ("done", string.Empty);

                case "stop":
                    ClearTasks();
                    player.Pathfinder.StopAutoWalk();

                    return ("done", string.Empty);

                case "bandage_self":
                    return BandageOn(player.Serial) ? ("done", string.Empty) : ("failed", "no bandages");

                case "bandage":
                    return BandageOn(a.Target) ? ("done", string.Empty) : ("failed", "no bandages");

                case "drink":
                    return Drink(a.Kind) ? ("done", string.Empty) : ("failed", $"no {a.Kind} potion");

                case "loot":
                    return StartLoot(a.Target);

                case "take":
                    if (_world.Items.Get(a.Target) == null)
                    {
                        return ("failed", "no such item");
                    }

                    _takeQueue.Enqueue(a.Target);

                    return ("done", string.Empty);

                case "flee":
                    return Flee(a.Target, Math.Clamp(a.Tiles, 3, 15));

                case "walk_to":
                    _engaged = 0;

                    return WalkTo(a.X, a.Y, player.Z, Math.Max(0, a.Distance)) ? ("done", string.Empty) : ("failed", "no path");

                case "move":
                    return Move(a.Direction, Math.Clamp(a.Tiles, 1, 15));

                case "say":
                    GameActions.Say(a.Text);

                    return ("done", string.Empty);

                case "use":
                    GameActions.DoubleClick(_world, a.Target);

                    return ("done", string.Empty);

                case "target":
                    if (!_world.TargetManager.IsTargeting)
                    {
                        return ("failed", "no target cursor");
                    }

                    _world.TargetManager.Target(a.Target == uint.MaxValue ? player.Serial : a.Target);

                    return ("done", string.Empty);

                case "wait":
                    return ("done", string.Empty);

                default:
                    return ("failed", $"unknown verb '{a.Verb}'");
            }
        }

        private (string, string) Attack(uint serial, bool manual)
        {
            Mobile m = _world.Mobiles.Get(serial);

            if (m == null || m == _world.Player || m.IsDead)
            {
                return ("failed", "no such living mobile");
            }

            // The brain only fights monsters. Players, NPCs and anything blue are
            // off limits unless the player asked for it themselves.
            if (!manual && !IsMonsterTarget(m))
            {
                Stats.Blocked++;

                return ("blocked", "not a monster");
            }

            _fleeUntil = 0;
            _lootCorpse = 0;
            _engaged = serial;
            _engagedLastX = m.X;
            _engagedLastY = m.Y;
            _nextPursuit = 0;
            _lastAttackSent = Time.Ticks;
            Stats.Attacks++;
            GameActions.Attack(_world, serial);
            GameActions.RequestMobileStatus(_world, serial);

            return ("done", string.Empty);
        }

        public static bool IsMonsterTarget(Mobile m)
        {
            if (m.IsHuman || m.IsRenamable || m.IsDead || (m.Serial & 0x80000000) != 0)
            {
                return false;
            }

            switch (m.NotorietyFlag)
            {
                case NotorietyFlag.Gray:
                case NotorietyFlag.Criminal:
                case NotorietyFlag.Enemy:
                case NotorietyFlag.Murderer:
                    return true;

                default:
                    return false;
            }
        }

        private bool BandageOn(uint target)
        {
            Item bandage = _world.Player.FindBandage();

            if (bandage == null)
            {
                return false;
            }

            _lastBandageAttempt = Time.Ticks;
            // Hold off retries until the server says the bandage started (or 2.5s pass).
            _bandagingUntil = Math.Max(_bandagingUntil, Time.Ticks + 2500);
            Stats.Bandages++;
            NetClient.Socket.Send_TargetSelectedObject(bandage.Serial, target);

            return true;
        }

        private bool Drink(string kind)
        {
            ushort graphic;

            switch (kind)
            {
                case "heal": graphic = HEAL_POTION_GRAPHIC; break;
                case "cure": graphic = CURE_POTION_GRAPHIC; break;
                case "refresh": graphic = REFRESH_POTION_GRAPHIC; break;
                case "strength": graphic = STRENGTH_POTION_GRAPHIC; break;
                case "agility": graphic = AGILITY_POTION_GRAPHIC; break;
                default: return false;
            }

            Item potion = _world.Player.FindItemByGraphic(graphic);

            if (potion == null)
            {
                return false;
            }

            if (kind == "heal")
            {
                _lastHealPotion = Time.Ticks;
                Stats.HealPotions++;
            }
            else if (kind == "cure")
            {
                _lastCurePotion = Time.Ticks;
                Stats.CurePotions++;
            }

            GameActions.DoubleClick(_world, potion);

            return true;
        }

        private (string, string) StartLoot(uint serial)
        {
            Item corpse = _world.Items.Get(serial);

            if (corpse == null || !corpse.IsCorpse)
            {
                return ("failed", "no such corpse");
            }

            _engaged = 0;
            _fleeUntil = 0;
            _lootCorpse = serial;
            _takeTries.Clear();
            _lootStarted = Time.Ticks;
            _lootOpenedAt = 0;
            _lootIdleSince = 0;
            _nextLootStep = 0;

            return ("done", string.Empty);
        }

        private (string, string) Flee(uint from, int tiles)
        {
            PlayerMobile p = _world.Player;
            Vector2 away = Vector2.Zero;

            if (from != 0 && _world.Mobiles.Get(from) is Mobile threat)
            {
                away = new Vector2(p.X - threat.X, p.Y - threat.Y);
            }
            else
            {
                foreach (Mobile m in _world.Mobiles.Values)
                {
                    if (m != p && !m.IsDead && m.Distance <= 10 && IsMonsterTarget(m))
                    {
                        away += new Vector2(p.X - m.X, p.Y - m.Y) / Math.Max(1, m.Distance);
                    }
                }
            }

            if (away == Vector2.Zero)
            {
                away = new Vector2(0, -1);
            }

            away.Normalize();
            _engaged = 0;
            _lootCorpse = 0;

            // Try straight away from the threat first, then fan out.
            foreach (float turn in new[] { 0f, 0.6f, -0.6f, 1.2f, -1.2f })
            {
                float cos = MathF.Cos(turn), sin = MathF.Sin(turn);
                var dir = new Vector2(away.X * cos - away.Y * sin, away.X * sin + away.Y * cos);
                int x = p.X + (int) MathF.Round(dir.X * tiles);
                int y = p.Y + (int) MathF.Round(dir.Y * tiles);

                if (WalkTo(x, y, p.Z, 0))
                {
                    _fleeUntil = Time.Ticks + 6000;
                    Stats.Flees++;

                    return ("done", string.Empty);
                }
            }

            return ("failed", "no path away");
        }

        private (string, string) Move(string dir, int tiles)
        {
            int dx = 0, dy = 0;

            foreach (char c in dir.ToLowerInvariant())
            {
                switch (c)
                {
                    case 'n': dy = -1; break;
                    case 's': dy = 1; break;
                    case 'e': dx = 1; break;
                    case 'w': dx = -1; break;
                }
            }

            if (dx == 0 && dy == 0)
            {
                return ("failed", $"bad direction '{dir}'");
            }

            PlayerMobile p = _world.Player;
            _engaged = 0;

            return WalkTo(p.X + dx * tiles, p.Y + dy * tiles, p.Z, 0) ? ("done", string.Empty) : ("failed", "no path");
        }

        private void ClearTasks()
        {
            _engaged = 0;
            _lootCorpse = 0;
            _fleeUntil = 0;
            _takeQueue.Clear();
        }

        // ---------------------------------------------------------------- per-tick work

        private void RunReflexes(uint now)
        {
            PlayerMobile p = _world.Player;

            if (_bandagingUntil != 0 && now >= _bandagingUntil)
            {
                _bandagingUntil = 0;
            }

            var input = new ReflexInput
            {
                Now = now,
                Dead = p.IsDead,
                HitsPercent = p.HitsMax == 0 ? 100 : p.Hits * 100 / p.HitsMax,
                Poisoned = p.IsPoisoned,
                Bandages = CountByGraphic(BANDAGE_GRAPHIC),
                HealPotions = CountByGraphic(HEAL_POTION_GRAPHIC),
                CurePotions = CountByGraphic(CURE_POTION_GRAPHIC),
                Bandaging = Bandaging,
                LastBandageAttempt = _lastBandageAttempt,
                LastHealPotion = _lastHealPotion,
                LastCurePotion = _lastCurePotion,
                Heal = GetAuthority(AgentBehavior.Heal),
                Cure = GetAuthority(AgentBehavior.Cure),
                Potion = GetAuthority(AgentBehavior.Potion)
            };

            (ReflexAction action, AgentAuthority auth) = ReflexPolicy.Decide(input, Reflexes);

            if (action == ReflexAction.None)
            {
                _lastHintedReflex = ReflexAction.None;

                return;
            }

            AgentAction a = action switch
            {
                ReflexAction.BandageSelf => new AgentAction { Verb = "bandage_self", Reason = "reflex" },
                ReflexAction.DrinkHeal => new AgentAction { Verb = "drink", Kind = "heal", Reason = "reflex" },
                _ => new AgentAction { Verb = "drink", Kind = "cure", Reason = "reflex" }
            };

            if (auth == AgentAuthority.Auto)
            {
                Stats.Reflexes++;
                Execute(a);
            }
            else if (auth == AgentAuthority.Suggest && action != _lastHintedReflex)
            {
                _lastHintedReflex = action;
                Suggest(a);
            }
        }

        private void UpdateEngagement(uint now)
        {
            if (_engaged == 0)
            {
                return;
            }

            Mobile m = _world.Mobiles.Get(_engaged);

            if (m == null || m.IsDead || m.Distance > _world.ClientViewRange)
            {
                _engaged = 0;

                return;
            }

            if (HumanActive || Fleeing || now < _nextPursuit)
            {
                return;
            }

            _nextPursuit = now + PURSUIT_INTERVAL_MS;
            PlayerMobile p = _world.Player;

            if (_world.TargetManager.LastAttack != _engaged && now - _lastAttackSent > 2000)
            {
                _lastAttackSent = now;
                GameActions.Attack(_world, _engaged);
            }

            if (m.Distance > 1)
            {
                bool moved = m.X != _engagedLastX || m.Y != _engagedLastY;

                if (!p.Pathfinder.AutoWalking || moved)
                {
                    _engagedLastX = m.X;
                    _engagedLastY = m.Y;
                    WalkTo(m.X, m.Y, m.Z, 1);
                }
            }
        }

        private void UpdateLoot(uint now)
        {
            if (_lootCorpse == 0 || now < _nextLootStep)
            {
                return;
            }

            _nextLootStep = now + 300;
            Item corpse = _world.Items.Get(_lootCorpse);

            if (corpse == null || now - _lootStarted > LOOT_TIMEOUT_MS)
            {
                FinishLoot(corpse);

                return;
            }

            PlayerMobile p = _world.Player;

            if (corpse.Distance > 2)
            {
                if (!p.Pathfinder.AutoWalking && !HumanActive)
                {
                    WalkTo(corpse.X, corpse.Y, corpse.Z, 1);
                }

                return;
            }

            bool opened = corpse.Opened || corpse.Items != null;

            if (_lootOpenedAt == 0 || (!opened && now - _lootOpenedAt > 2000))
            {
                _lootOpenedAt = now;
                GameActions.DoubleClick(_world, corpse.Serial);

                return;
            }

            if (!opened)
            {
                return;
            }

            // Always take the obvious things; the brain asks for anything else by "take".
            bool queued = false;

            for (LinkedObject i = corpse.Items; i != null; i = i.Next)
            {
                if (i is Item it && IsAlwaysLoot(it) && !_takeQueue.Contains(it.Serial) && !_takeTries.ContainsKey(it.Serial))
                {
                    _takeQueue.Enqueue(it.Serial);
                    queued = true;
                }
            }

            if (!queued && _takeQueue.Count == 0)
            {
                if (_lootIdleSince == 0)
                {
                    _lootIdleSince = now;
                }
                else if (now - _lootIdleSince > 2500)
                {
                    FinishLoot(corpse);
                }
            }
            else
            {
                _lootIdleSince = 0;
            }
        }

        private void FinishLoot(Item corpse)
        {
            if (corpse != null)
            {
                UIManager.GetGump<ContainerGump>(corpse.Serial)?.Dispose();
                UIManager.GetGump<GridLootGump>(corpse.Serial)?.Dispose();
                Stats.Looted++;
            }

            _lootCorpse = 0;
        }

        // One grab per second; each item is re-checked until it is ours. Mid-fight the
        // server often answers "You must wait to perform another action", so retry a few times.
        private void UpdateTakes(uint now)
        {
            if (_takeQueue.Count == 0 || now < _nextGrab || Client.Game.UO.GameCursor?.ItemHold.Enabled == true)
            {
                return;
            }

            uint serial = _takeQueue.Dequeue();
            Item it = _world.Items.Get(serial);
            int tries = _takeTries.GetValueOrDefault(serial);

            if (it == null || it.RootContainer == _world.Player)
            {
                if (tries > 0)
                {
                    Stats.ItemsTaken++;
                    _takeTries.Remove(serial);
                }

                return;
            }

            // Out of reach or out of tries: leave it, and remember so looting does not re-queue it.
            if (tries >= GRAB_TRIES || !(_world.Get(it.RootContainer) is Item root && root.OnGround && root.Distance <= 2))
            {
                _takeTries[serial] = GRAB_TRIES;

                return;
            }

            _takeTries[serial] = tries + 1;
            _nextGrab = now + GRAB_INTERVAL_MS;
            GameActions.GrabItem(_world, serial, it.Amount);
            _takeQueue.Enqueue(serial);
        }

        public static bool IsAlwaysLoot(Item it)
        {
            ushort g = it.Graphic;

            return it.IsCoin || g == BANDAGE_GRAPHIC || g >= 0x0F06 && g <= 0x0F0D && g != 0x0F0A && g != 0x0F0D;
        }

        // ---------------------------------------------------------------- helpers

        public int CountByGraphic(ushort graphic)
        {
            Item backpack = _world.Player?.FindItemByLayer(Layer.Backpack);

            return backpack == null ? 0 : CountIn(backpack, graphic);
        }

        private static int CountIn(Item container, ushort graphic)
        {
            int total = 0;

            for (LinkedObject i = container.Items; i != null; i = i.Next)
            {
                var it = (Item) i;

                if (it.Graphic == graphic)
                {
                    total += Math.Max((int) it.Amount, 1);
                }

                if (it.Items != null)
                {
                    total += CountIn(it, graphic);
                }
            }

            return total;
        }

        public void Print(string text)
        {
            GameActions.Print(_world, AgentJournal.AGENT_PREFIX + text, 0x0035);
        }

        private void UpdateGump()
        {
            bool want = Mode != AgentMode.Off || AgentHost.BrainConnected;

            if (want && (_gump == null || _gump.IsDisposed))
            {
                _gump = new AgentStatusGump(_world, this);
                UIManager.Add(_gump);
            }
            else if (!want && _gump != null && !_gump.IsDisposed)
            {
                _gump.Dispose();
                _gump = null;
            }
        }

        // ---------------------------------------------------------------- persistence

        private void EnsureProfileLoaded()
        {
            if (_profileLoaded || ProfileManager.CurrentProfile == null || !_world.InGame)
            {
                return;
            }

            _profileLoaded = true;
            Profile profile = ProfileManager.CurrentProfile;

            Reflexes.BandageBelowPercent = Math.Clamp(profile.AgentBandageBelowPercent, 1, 100);
            Reflexes.HealPotionBelowPercent = Math.Clamp(profile.AgentHealPotionBelowPercent, 1, 100);
            Strategy = profile.AgentStrategy ?? string.Empty;
            StrategyRevision++;

            AgentModes.TryParse(profile.AgentMode, out AgentMode mode);
            Mode = mode;

            foreach (AgentBehavior b in AgentModes.AllBehaviors)
            {
                _authority[(int) b] = AgentModes.PresetAuthority(mode, b);
            }

            // Per-behaviour overrides, stored as "fight=auto,loot=suggest".
            foreach (string pair in (profile.AgentAuthority ?? string.Empty).Split(',', StringSplitOptions.RemoveEmptyEntries))
            {
                string[] kv = pair.Split('=');

                if (kv.Length == 2 && AgentModes.TryParse(kv[0], out AgentBehavior b) && AgentModes.TryParse(kv[1], out AgentAuthority a))
                {
                    _authority[(int) b] = a;
                }
            }
        }

        private void SaveToProfile()
        {
            Profile profile = ProfileManager.CurrentProfile;

            if (profile == null || !_profileLoaded)
            {
                return;
            }

            profile.AgentMode = Mode.Name();
            profile.AgentBandageBelowPercent = Reflexes.BandageBelowPercent;
            profile.AgentHealPotionBelowPercent = Reflexes.HealPotionBelowPercent;
            profile.AgentStrategy = Strategy;

            var sb = new StringBuilder();

            foreach (AgentBehavior b in AgentModes.AllBehaviors)
            {
                if (_authority[(int) b] != AgentModes.PresetAuthority(Mode, b))
                {
                    sb.Append(sb.Length == 0 ? "" : ",").Append(b.Name()).Append('=').Append(_authority[(int) b].Name());
                }
            }

            profile.AgentAuthority = sb.ToString();
        }

        public const int MAX_STRATEGY_LENGTH = 4000;

        public void SetStrategy(string text)
        {
            EnsureProfileLoaded();
            text = (text ?? string.Empty).Trim();
            Strategy = text.Length > MAX_STRATEGY_LENGTH ? text.Substring(0, MAX_STRATEGY_LENGTH) : text;
            StrategyRevision++;
            SaveToProfile();
        }

        public void AddStrategy(string line)
        {
            SetStrategy(string.IsNullOrEmpty(Strategy) ? line : Strategy + "\n" + line);
        }

        public void SetReflexThresholds(int? bandage, int? healPotion)
        {
            EnsureProfileLoaded();

            if (bandage.HasValue)
            {
                Reflexes.BandageBelowPercent = Math.Clamp(bandage.Value, 1, 100);
            }

            if (healPotion.HasValue)
            {
                Reflexes.HealPotionBelowPercent = Math.Clamp(healPotion.Value, 1, 100);
            }

            SaveToProfile();
        }

        // ---------------------------------------------------------------- chat command

        // "-agent [off|assist|auto|status|accept|set <behaviour> <off|suggest|auto>|bandage <pct>|potion <pct>
        //         |strategy [set <text>|add <text>|clear]]"
        public void OnCommand(string[] args)
        {
            string sub = args.Length > 1 ? args[1].ToLowerInvariant() : "status";

            switch (sub)
            {
                case "off":
                case "assist":
                case "auto":
                    AgentModes.TryParse(sub, out AgentMode mode);
                    SetMode(mode);
                    Print($"mode {Mode.Name()}");

                    break;

                case "accept":
                    if (!AcceptSuggestion())
                    {
                        Print("nothing to accept");
                    }

                    break;

                case "set" when args.Length > 3 && AgentModes.TryParse(args[2], out AgentBehavior b) && AgentModes.TryParse(args[3], out AgentAuthority a):
                    SetAuthority(b, a);
                    Print($"{b.Name()} = {a.Name()}");

                    break;

                case "bandage" when args.Length > 2 && int.TryParse(args[2], out int pct):
                    SetReflexThresholds(pct, null);
                    Print($"bandage below {Reflexes.BandageBelowPercent}%");

                    break;

                case "potion" when args.Length > 2 && int.TryParse(args[2], out int pct):
                    SetReflexThresholds(null, pct);
                    Print($"heal potion below {Reflexes.HealPotionBelowPercent}%");

                    break;

                case "strategy":
                    string verb = args.Length > 2 ? args[2].ToLowerInvariant() : "show";
                    string text = args.Length > 3 ? string.Join(" ", args, 3, args.Length - 3) : string.Empty;

                    if (verb == "set" && text.Length != 0)
                    {
                        SetStrategy(text);
                    }
                    else if (verb == "add" && text.Length != 0)
                    {
                        AddStrategy(text);
                    }
                    else if (verb == "clear")
                    {
                        SetStrategy(string.Empty);
                    }
                    else if (verb != "show")
                    {
                        Print("usage: -agent strategy [set <text>|add <text>|clear]");

                        break;
                    }

                    if (string.IsNullOrEmpty(Strategy))
                    {
                        Print("strategy: (none)");
                    }
                    else
                    {
                        foreach (string line in Strategy.Split('\n'))
                        {
                            Print("strategy: " + line);
                        }
                    }

                    break;

                case "status":
                    var sb = new StringBuilder($"mode {Mode.Name()}:");

                    foreach (AgentBehavior beh in AgentModes.AllBehaviors)
                    {
                        sb.Append(' ').Append(beh.Name()).Append('=').Append(GetAuthority(beh).Name());
                    }

                    sb.Append($" | bandage<{Reflexes.BandageBelowPercent}% potion<{Reflexes.HealPotionBelowPercent}% | brain {(AgentHost.BrainConnected ? "connected" : "not connected")}");
                    Print(sb.ToString());

                    break;

                default:
                    Print("usage: -agent off|assist|auto|status|accept|set <behaviour> <off|suggest|auto>|bandage <pct>|potion <pct>|strategy [set|add|clear] <text>");

                    break;
            }
        }
    }

    internal sealed class AgentStats
    {
        public int Kills, Deaths, Attacks, Bandages, HealPotions, CurePotions, Reflexes, Flees, Looted, ItemsTaken;
        public int BrainActions, Suggestions, Accepted, Blocked, Deferred;
    }
}
