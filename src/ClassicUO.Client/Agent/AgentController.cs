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
using ClassicUO.Utility.Logging;
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

        private readonly World _world;
        private readonly AgentAuthority[] _authority = new AgentAuthority[AgentModes.AllBehaviors.Length];
        private readonly Queue<uint> _takeQueue = new Queue<uint>();
        private readonly Dictionary<uint, int> _takeTries = new Dictionary<uint, int>();
        private bool _profileLoaded;
        private bool _wasDead;

        private uint _lastHumanInput, _lastHumanMove, _lastHumanClick;

        // Combat assist: the creature the player is fighting (their attack, or a creature
        // they targeted with a cursor), and the creatures that have swung at the player.
        private uint _playerTarget, _seenTargetInfo, _agentTargeted, _nextAssistLook;
        private readonly Dictionary<uint, uint> _attackers = new Dictionary<uint, uint>();
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

        private uint _fleeUntil, _kiteUntil;
        private bool _agentWalking;

        // A long walk (travel): the planned path, how far along it the player is, and when the
        // distance to the goal last went down. Paused while fighting, looting or fleeing.
        private List<(int X, int Y, sbyte Z)> _travelPath;
        private int _travelIndex, _travelGoalX, _travelGoalY, _travelDistance, _travelBest, _travelReplans;
        private uint _travelStarted, _travelProgressAt, _nextTravelStep;
        private readonly List<(int X, int Y)> _travelStuck = new List<(int, int)>();
        private readonly HashSet<(int, int)> _travelBlocked = new HashSet<(int, int)>(); // impassable items seen on this trip
        public string TravelState { get; private set; } = string.Empty;
        public (int X, int Y) TravelGoal => (_travelGoalX, _travelGoalY);
        public int TravelReplans => _travelReplans;
        public IReadOnlyList<(int X, int Y)> TravelStuckAt => _travelStuck;
        public uint TravelElapsedMs => _travelStarted == 0 ? 0 : Time.Ticks - _travelStarted;
        public int TravelLeft => _world.Player == null ? 0 : Math.Max(Math.Abs(_world.Player.X - _travelGoalX), Math.Abs(_world.Player.Y - _travelGoalY));

        // One spell at a time: the spell the agent started and whose target cursor it
        // will answer, when its cast delay ends, and when the next spell may start.
        private int _castSpell;
        private uint _castTarget, _castStarted, _castUntil, _castCursorBy, _nextCastAt;
        private bool _castSurvival;

        // A spell the brain wants cast as soon as the current one allows ("queue": true).
        private AgentAction _queuedCast;
        private uint _queuedCastAt;
        private int _engagedRange = 1;
        private uint _agentAttack, _seenLastAttack;
        private uint _peekSerial, _peekAt, _nextPeek;
        private readonly Dictionary<uint, uint> _peekAgainAt = new Dictionary<uint, uint>();
        private readonly Dictionary<uint, int> _peekFailures = new Dictionary<uint, int>();
        private bool _wasTargeting;
        private uint _cursorUpSince;

        // Other players close enough to matter: red (murderers), criminals, and players the agent
        // can't place (no NPC title, not in the party). Defensive only: the agent never attacks
        // players, and their names stay out of the brain's model questions and the world store.
        private readonly List<(uint Serial, string Kind, int Distance)> _threats = new List<(uint, string, int)>();
        private readonly Dictionary<(uint, string), uint> _threatWarned = new Dictionary<(uint, string), uint>();
        private uint _nextThreatLook;

        // Names with titles ("Lucy the healer") learnt from single clicks, for shards that send
        // no item properties (pre-AOS): the title is how townsfolk are told from players.
        private readonly Dictionary<uint, string> _labels = new Dictionary<uint, string>();
        private readonly HashSet<uint> _clicked = new HashSet<uint>();
        private uint _clickedOne, _clickedAt, _nextNameClick;

        public string LabelOf(Mobile m)
        {
            if (_world.OPL.TryGetNameAndData(m.Serial, out string name, out _) && !string.IsNullOrWhiteSpace(name))
            {
                return name.Trim();
            }

            return _labels.TryGetValue(m.Serial, out string learnt) ? learnt : null;
        }

        // The overhead name a single click brings back (AgentJournal routes Label messages here).
        public void OnLabel(uint serial, string text)
        {
            if (serial != 0 && serial == _clickedOne && Time.Ticks - _clickedAt < 3000 && !string.IsNullOrWhiteSpace(text))
            {
                _labels[serial] = text.Trim();
                _clickedOne = 0;
            }
        }

        private void UpdateNames(uint now)
        {
            if (_world.ClientFeatures.TooltipsEnabled || Mode == AgentMode.Off || HumanActive || now < _nextNameClick
                || _engaged != 0 || _castSpell != 0)
            {
                return;
            }

            _nextNameClick = now + 1500;

            foreach (Mobile m in _world.Mobiles.Values)
            {
                if (m != _world.Player && m.IsHuman && !m.IsDead && m.Distance <= 12 && _clicked.Add(m.Serial))
                {
                    _clickedOne = m.Serial;
                    _clickedAt = now;
                    GameActions.SingleClick(_world, m.Serial);

                    return;
                }
            }
        }
        public IReadOnlyList<(uint Serial, string Kind, int Distance)> Threats => _threats;
        public const int THREAT_RANGE = 8;

        // Creatures seen dying, newest last, so a benchmark can tell what died in which order.
        private readonly List<(uint Serial, string Name, ushort Body, uint Time)> _deaths = new List<(uint, string, ushort, uint)>();

        private readonly List<AgentDecision> _decisions = new List<AgentDecision>();
        private int _decisionSeq;

        private AgentGump _gump;

        public AgentController(World world)
        {
            _world = world;
            Journal = new AgentJournal(world);
            Errands = new AgentErrands(world, this);
            SetMode(AgentMode.Off);
        }

        public AgentMode Mode { get; private set; }
        public AgentEngage Engage { get; private set; } = AgentEngage.Defend;
        public ReflexSettings Reflexes { get; } = new ReflexSettings();
        public AgentJournal Journal { get; }
        public AgentErrands Errands { get; }
        public AgentStats Stats { get; } = new AgentStats();
        public uint HumanPauseMs { get; set; } = 4000;

        public AgentAction Suggestion { get; private set; }
        public uint SuggestionTime { get; private set; }

        // Latest decision summary the brain reported, shown in the overlay.
        public string BrainNote { get; private set; } = string.Empty;
        public uint BrainNoteTime { get; private set; }

        // What the brain told us about itself: which judge, which archetype it plays
        // and how it read the player's strategy.
        public string BrainJudge { get; private set; } = string.Empty;
        public string BrainArchetype { get; private set; } = string.Empty;
        public string StrategyReading { get; private set; } = string.Empty;

        // A decision loop is attached and has reported recently (a bare CLI connection is not a brain).
        public bool BrainActive => AgentHost.BrainConnected && _lastBrainContact != 0 && Time.Ticks - _lastBrainContact < 15000;
        private uint _lastBrainContact;

        // The brain's structured decisions, oldest first. Changes bump DecisionSeq.
        public IReadOnlyList<AgentDecision> Decisions => _decisions;
        public AgentDecision LastDecision => _decisions.Count == 0 ? null : _decisions[_decisions.Count - 1];
        public int DecisionSeq => _decisionSeq;

        // The player's own words on how to play ("never flee", "loot only valuables").
        // The brain reads it with every decision and turns it into policy settings.
        public string Strategy { get; private set; } = string.Empty;
        public int StrategyRevision { get; private set; }

        // The session goal for auto mode, in the player's words ("hunt the Britain graveyard,
        // keep stocked, bank gold"). The brain's planner works towards it and reports the step
        // it is on and why. Paused, the goal is kept but nothing works on it.
        public string Goal { get; private set; } = string.Empty;
        public int GoalRevision { get; private set; }
        public bool GoalPaused { get; private set; }
        public string GoalStep { get; private set; } = string.Empty;
        public string GoalWhy { get; private set; } = string.Empty;
        public uint GoalStepTime { get; private set; }

        public uint Engaged => _engaged;
        public uint LootCorpse => _lootCorpse;
        public bool Bandaging => _bandagingUntil > Time.Ticks;

        // Heal potions share a cooldown, so having one is not the same as being able to drink it.
        public uint HealPotionReadyInMs =>
            _lastHealPotion == 0 || Time.Ticks - _lastHealPotion >= Reflexes.HealPotionCooldownMs
                ? 0
                : Reflexes.HealPotionCooldownMs - (Time.Ticks - _lastHealPotion);
        public bool Fleeing => _fleeUntil > Time.Ticks;
        public int EngagedRange => _engagedRange;

        public string CastingSpell => _castSpell == 0 ? string.Empty : SpellsMagery.GetSpell(_castSpell).Name;
        public string QueuedSpell => _queuedCast == null ? string.Empty : AgentSpells.Find(_queuedCast.Spell)?.Name ?? string.Empty;
        public bool CastReady => _castSpell == 0 && Time.Ticks >= _nextCastAt && !_world.TargetManager.IsTargeting;
        public uint CastReadyInMs =>
            _castSpell != 0 ? Math.Max(_nextCastAt > Time.Ticks ? _nextCastAt - Time.Ticks : 0, 250)
            : _nextCastAt > Time.Ticks ? _nextCastAt - Time.Ticks : 0;
        public bool HumanActive => _lastHumanInput != 0 && Time.Ticks - _lastHumanInput < HumanPauseMs;
        public uint LastHumanInput => _lastHumanInput;
        public uint PlayerTarget => _playerTarget;

        // Whether the player's recent input holds back a brain action of this behaviour. In
        // auto, touching the controls is a short override of everything but healing. In combat
        // assist the fight goes on and only the agent's own walking (and looting, which walks)
        // waits; moving is off there anyway unless overridden.
        public bool Paused(AgentBehavior b)
        {
            if (!HumanActive || b == AgentBehavior.Heal || b == AgentBehavior.Cure || b == AgentBehavior.Potion)
            {
                return false;
            }

            return Mode != AgentMode.Assist || b == AgentBehavior.Move || b == AgentBehavior.Loot;
        }

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
            else if (mode != AgentMode.Auto)
            {
                // Handing over to the player: stop walking at once (the goal is kept).
                StopTravel("stopped: you took over");
                Errands.Cancel();
            }

            SaveToProfile();
        }

        public void SetAuthority(AgentBehavior b, AgentAuthority a)
        {
            EnsureProfileLoaded();
            _authority[(int) b] = a;
            SaveToProfile();
        }

        // The one key between the two ways to play: combat assist (you drive) and auto.
        public void SwitchPlayState()
        {
            SetMode(Mode == AgentMode.Assist ? AgentMode.Auto : AgentMode.Assist);
            Print($"mode {Mode.Title()}");
        }

        public void SetEngage(AgentEngage e)
        {
            EnsureProfileLoaded();
            Engage = e;
            SaveToProfile();
        }

        // The player touched the controls: hand them back. Survival reflexes keep running.
        // movement: arrow keys or the right mouse button walking the character; otherwise a
        // click in the world.
        public void NoteHumanInput(bool movement)
        {
            uint now = Time.Ticks;
            _lastHumanInput = now;

            if (movement)
            {
                _lastHumanMove = now;
            }
            else
            {
                _lastHumanClick = now;
            }

            // In auto, any touch drops the spell waiting to be cast; in combat assist it is cast.
            if (Mode != AgentMode.Assist)
            {
                _queuedCast = null;
            }

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
            BrainNoteTime = _lastBrainContact = Time.Ticks;
        }

        public void RecordDecision(AgentDecision d)
        {
            // Mark a newly chosen target in the world, where the player is looking.
            if (Mode != AgentMode.Off && d.TargetSerial != 0 && d.TargetSerial != (LastDecision?.TargetSerial ?? 0)
                && _world.Mobiles.Get(d.TargetSerial) is Mobile target)
            {
                target.AddMessage(MessageType.Regular, "jev: target", 3, 0x0035, true, TextType.CLIENT);
            }

            d.Seq = ++_decisionSeq;
            d.Time = Time.Ticks;
            _decisions.Add(d);

            if (_decisions.Count > 12)
            {
                _decisions.RemoveAt(0);
            }

            if (!string.IsNullOrEmpty(d.Judge))
            {
                BrainJudge = d.Judge;
            }

            if (!string.IsNullOrEmpty(d.Archetype))
            {
                BrainArchetype = d.Archetype;
            }

            NoteBrain(d.Note);
        }

        public void SetBrainInfo(string judge, string archetype, string strategyReading)
        {
            BrainJudge = judge ?? BrainJudge;
            BrainArchetype = archetype ?? BrainArchetype;
            StrategyReading = strategyReading ?? StrategyReading;
            _lastBrainContact = Time.Ticks;
            _decisionSeq++;
        }

        public IReadOnlyList<(uint Serial, string Name, ushort Body, uint Time)> RecentDeaths => _deaths;

        // Whose corpse is this: true for a creature the agent saw die as a monster, false for
        // anything else it saw die (an animal, a townsperson, a player), null when it didn't see
        // the death. Looting anything but a monster's corpse can be a crime (guards in town).
        private readonly Dictionary<uint, bool> _corpseOfMonster = new Dictionary<uint, bool>();

        public bool? CorpseOfMonster(uint corpse) => _corpseOfMonster.TryGetValue(corpse, out bool m) ? m : null;

        // Called by the 0xAF death packet before the mobile is turned into a corpse.
        public void OnMobileDied(uint serial, uint corpse = 0)
        {
            if (_world.Mobiles.Get(serial) is Mobile dying)
            {
                _deaths.Add((serial, dying.Name?.Trim() ?? string.Empty, dying.Graphic, Time.Ticks));

                if (corpse != 0)
                {
                    if (_corpseOfMonster.Count > 500)
                    {
                        _corpseOfMonster.Clear();
                    }

                    _corpseOfMonster[corpse] = IsMonsterTarget(dying);
                }

                if (_deaths.Count > 32)
                {
                    _deaths.RemoveAt(0);
                }
            }

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

        // From the cliloc message packets (0xC1, 0xCC) for the player or the system, just before
        // the same message arrives as text: the number wins, so the text is skipped.
        public void OnCliloc(uint cliloc)
        {
            AgentMessage m = AgentMessages.FromCliloc(cliloc);

            if (m != AgentMessage.None)
            {
                Apply(m);
                Stats.ClilocMessages++;
                _clilocHandledAt = Time.Ticks;
            }
        }

        // The same packet's text follows within the same frame; anything later is a new message.
        private uint _clilocHandledAt;

        // Server text and the player's own lines (AgentJournal filters out everyone else's).
        public void OnMessage(string text)
        {
            if (_clilocHandledAt != 0 && _clilocHandledAt == Time.Ticks)
            {
                _clilocHandledAt = 0;

                return;
            }

            AgentMessage m = AgentMessages.FromText(text);

            if (m != AgentMessage.None)
            {
                Stats.TextMessages++;
                Apply(m);
            }
        }

        private void Apply(AgentMessage m)
        {
            switch (m)
            {
                // The spell never started, or was interrupted before its cursor: try again soon.
                case AgentMessage.CastFailed:
                case AgentMessage.CastNotRecovered:
                    if (_castSpell != 0)
                    {
                        _castSpell = 0;
                        _nextCastAt = Math.Min(_nextCastAt, Time.Ticks + 250);
                    }

                    if (m == AgentMessage.CastNotRecovered)
                    {
                        _nextCastAt = Math.Max(_nextCastAt, Time.Ticks + 500);
                    }

                    break;

                case AgentMessage.BandageStarted:
                    _bandagingUntil = Time.Ticks + 15000;

                    break;

                case AgentMessage.BandageEnded:
                    _bandagingUntil = 0;

                    break;
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
            AgentBrain.Update(this);
            UpdateThreats(now);
            UpdateNames(now);
            UpdateCast(now);

            if (Mode == AgentMode.Off && _engaged == 0 && _lootCorpse == 0 && _takeQueue.Count == 0)
            {
                return;
            }

            if (now >= _nextReflex)
            {
                _nextReflex = now + REFLEX_INTERVAL_MS;
                RunReflexes(now);
                FireQueuedCast(now);
            }

            UpdatePeek(now);
            UpdatePlayerTarget();
            UpdateAssist(now);
            UpdateEngagement(now);
            UpdateLoot(now);
            UpdateTakes(now);
            UpdateTravel(now);
            Errands.Update(now);
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

            // A hint is client-side text for the player and nothing else.
            if (a.Verb == "hint")
            {
                return Execute(a);
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

                if (Paused(a.Behavior))
                {
                    Stats.Deferred++;

                    return ("deferred", "player is in control");
                }

                // Combat assist takes on only what its engage setting allows.
                if (a.Behavior == AgentBehavior.Fight && a.Target != 0 && a.Target != uint.MaxValue && !MayEngage(a.Target))
                {
                    Stats.Blocked++;

                    return ("blocked", "not your target");
                }
            }

            return Execute(a);
        }

        // Combat assist: whether the agent may take this creature on by itself. Your own
        // target always; attackers too under "defend"; any monster under "nearby".
        public bool MayEngage(uint serial)
        {
            if (Mode != AgentMode.Assist || serial == _playerTarget)
            {
                return true;
            }

            Mobile m = _world.Mobiles.Get(serial);

            return m != null && (Engage == AgentEngage.Nearby || Engage == AgentEngage.Defend && IsAttackingMe(m));
        }

        // Swung at the player in the last 8 s, or a monster in war mode standing next to them.
        public bool IsAttackingMe(Mobile m) =>
            _attackers.TryGetValue(m.Serial, out uint at) && Time.Ticks - at < 8000
            || m.InWarMode && m.Distance <= 1 && IsMonsterTarget(m);

        // From the swing packet (0x2F) when the player is the defender.
        public void NoteAttacker(uint serial)
        {
            if (serial != 0)
            {
                _attackers[serial] = Time.Ticks;
            }
        }

        // The next-action key: Jev's pending suggestion if there is one, else the move its last
        // decision made or wanted (a cast, a target switch), else a bandage when hurt.
        public void DoNext()
        {
            if (AcceptSuggestion())
            {
                return;
            }

            AgentDecision d = LastDecision;
            AgentAction next = d != null && Time.Ticks - d.Time < 6000 ? d.Next : null;

            if (next != null)
            {
                var a = new AgentAction
                {
                    Verb = next.Verb, Target = next.Target, Spell = next.Spell, Range = next.Range, Kind = next.Kind,
                    Manual = true, Reason = "next"
                };

                (string status, string detail) = Execute(a);
                Print($"{a.Describe(_world)}: {status}{(string.IsNullOrEmpty(detail) ? "" : " (" + detail + ")")}");

                return;
            }

            PlayerMobile p = _world.Player;

            if (p != null && p.Hits < p.HitsMax && !Bandaging && BandageOn(p.Serial))
            {
                Print("bandage self");

                return;
            }

            Print("jev has nothing to suggest right now");
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
                    return Attack(a.Target, a.Manual, a.Range);

                case "cast":
                    (string castStatus, string castDetail) = Cast(a, false);

                    if (a.Queue && castStatus == "failed" && castDetail == "not ready to cast")
                    {
                        _queuedCast = a;
                        _queuedCastAt = Time.Ticks;

                        return ("queued", a.Describe(_world));
                    }

                    if (castStatus == "done" && a.Queue)
                    {
                        _queuedCast = null;
                    }

                    return (castStatus, castDetail);

                case "skill":
                    int skill = AgentSpells.SkillIndex(player, a.Name);

                    if (skill < 0)
                    {
                        return ("failed", $"unknown skill '{a.Name}'");
                    }

                    GameActions.UseSkill(skill);

                    return ("done", string.Empty);

                case "war_mode":
                    GameActions.RequestWarMode(player, a.On);

                    return ("done", string.Empty);

                case "stop":
                    ClearTasks();
                    StopTravel("stopped");
                    player.Pathfinder.StopAutoWalk();

                    return ("done", string.Empty);

                case "bandage_self":
                    return BandageOn(player.Serial) ? ("done", string.Empty) : ("failed", "no bandages");

                case "bandage":
                    return BandageOn(a.Target) ? ("done", string.Empty) : ("failed", "no bandages");

                case "drink":
                    return Drink(a.Kind) ? ("done", string.Empty) : ("failed", $"no {a.Kind} potion");

                case "loot":
                    // The brain only loots monsters it saw die; the player may loot anything.
                    if (!a.Manual && CorpseOfMonster(a.Target) != true)
                    {
                        Stats.Blocked++;

                        return ("blocked", "not a monster's corpse");
                    }

                    return StartLoot(a.Target);

                case "take":
                    if (_world.Items.Get(a.Target) is not Item wanted)
                    {
                        return ("failed", "no such item");
                    }

                    if (!a.Manual && _world.Get(wanted.RootContainer) is Item root && root.IsCorpse && CorpseOfMonster(root.Serial) != true)
                    {
                        Stats.Blocked++;

                        return ("blocked", "not a monster's corpse");
                    }

                    _takeQueue.Enqueue(a.Target);

                    return ("done", string.Empty);

                case "flee":
                    return Flee(a.Target, Math.Clamp(a.Tiles, 3, 15));

                case "kite":
                    return Kite(Math.Clamp(a.Tiles, 2, 8));

                case "bank":
                    return Errands.StartBank(a.Deposit, a.Withdraw);

                case "buy":
                case "sell":
                    return Errands.StartShop(a.Verb == "buy", a.Target, a.Items);

                case "travel":
                    _travelManual = a.Manual;

                    return StartTravel(a.X, a.Y, Math.Max(0, a.Distance));

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

                case "hint":
                    if (!string.IsNullOrEmpty(a.Text))
                    {
                        player.AddMessage(MessageType.Regular, "jev: " + a.Text, 3, 0x0035, true, TextType.CLIENT);
                    }

                    return ("done", string.Empty);

                default:
                    return ("failed", $"unknown verb '{a.Verb}'");
            }
        }

        private (string, string) Attack(uint serial, bool manual, int range = 1)
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
            _engagedRange = Math.Clamp(range, 1, 10);
            _engagedLastX = m.X;
            _engagedLastY = m.Y;
            _nextPursuit = 0;
            _lastAttackSent = Time.Ticks;
            Stats.Attacks++;
            _agentAttack = serial;

            // Fight in war mode, as a player would: then the player's own double-click on
            // another creature is an attack too, which the agent follows (UpdateEngagement).
            if (!_world.Player.InWarMode)
            {
                GameActions.RequestWarMode(_world.Player, true);
            }

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

        // Starts a magery spell. Its target cursor is answered from UpdateCast once the
        // cast delay ends. Survival casts (reflex heals and cures) are answered even if
        // the player has touched the controls since; anything else is left to the player.
        private (string, string) Cast(AgentAction a, bool survival)
        {
            PlayerMobile p = _world.Player;
            SpellDefinition spell = AgentSpells.Find(a.Spell);

            if (spell == null || !AgentSpells.IsMagery(spell.ID))
            {
                return ("failed", $"unknown spell '{a.Spell}'");
            }

            uint now = Time.Ticks;

            if (_castSpell != 0 || now < _nextCastAt)
            {
                return ("failed", "not ready to cast");
            }

            if (_world.TargetManager.IsTargeting)
            {
                return ("failed", "a target cursor is up");
            }

            string missing = AgentSpells.Missing(this, p, AgentSpells.FindSpellbook(p), spell);

            if (missing.Length != 0)
            {
                return ("failed", missing == "spellbook" ? "no spellbook" : missing == "mana" ? "not enough mana" : missing == "reagents" ? "no reagents" : missing);
            }

            uint target = a.Target == uint.MaxValue ? p.Serial : a.Target;

            if (spell.TargetType != TargetType.Neutral)
            {
                if (target == 0)
                {
                    target = spell.TargetType == TargetType.Beneficial ? p.Serial : _engaged;
                }

                Mobile m = _world.Mobiles.Get(target);

                if (m == null || m.IsDead)
                {
                    return ("failed", "no such living mobile");
                }

                if (spell.TargetType == TargetType.Harmful && !a.Manual && !IsMonsterTarget(m))
                {
                    Stats.Blocked++;

                    return ("blocked", "not a monster");
                }

                if (m != p && m.Distance > 10)
                {
                    return ("failed", "out of spell range");
                }
            }

            uint delay = AgentSpells.CastDelayMs(spell.ID);
            _castSpell = spell.ID;
            _castTarget = spell.TargetType == TargetType.Neutral ? 0 : target;
            _castSurvival = survival;
            _castStarted = now;
            _castUntil = now + delay;
            // Spells without a cursor (Protection is a self toggle) free the slot soon after.
            _castCursorBy = now + delay + 1200;
            _nextCastAt = now + delay + AgentSpells.RECOVERY_MS;
            Stats.Casts++;
            GameActions.CastSpell(spell.ID);

            return ("done", string.Empty);
        }

        // Runs right after the reflexes, so a heal the reflexes needed has already taken the slot.
        private void FireQueuedCast(uint now)
        {
            if (_queuedCast == null)
            {
                return;
            }

            if (now - _queuedCastAt > 4000 || Paused(AgentBehavior.Fight))
            {
                _queuedCast = null;
            }
            else if (CastReady && !Kiting)
            {
                AgentAction a = _queuedCast;
                _queuedCast = null;
                Cast(a, false);
            }
        }

        private void UpdateCast(uint now)
        {
            TargetManager tm = _world.TargetManager;

            if (tm.IsTargeting && !_wasTargeting)
            {
                _cursorUpSince = now;
            }

            _wasTargeting = tm.IsTargeting;

            if (_castSpell == 0)
            {
                return;
            }

            // Our spell's cursor appears once the cast delay is over. One that was already
            // up before then belongs to something else the player is doing.
            if (tm.IsTargeting && _cursorUpSince + 300 >= _castUntil)
            {
                // In combat assist only a click hands the cursor over: walking while it casts is normal.
                uint touched = Mode == AgentMode.Assist ? _lastHumanClick : _lastHumanInput;
                bool playerTookOver = !_castSurvival && touched != 0 && touched >= _castStarted;
                Entity e = _castTarget == 0 ? null : _world.Get(_castTarget);

                if (playerTookOver)
                {
                    // The player has the controls: the cursor is theirs to use or cancel.
                }
                else if (e != null && !(e is Mobile m && m.IsDead) && (tm.TargetingState == CursorTarget.Object || tm.TargetingState == CursorTarget.Position))
                {
                    _agentTargeted = _castTarget;
                    tm.Target(_castTarget);
                }
                else
                {
                    tm.CancelTarget();
                }

                _nextCastAt = Math.Max(_nextCastAt, now + AgentSpells.RECOVERY_MS);
                _castSpell = 0;

                return;
            }

            if (now > _castCursorBy)
            {
                _castSpell = 0;
            }
        }

        // The server only sends what is inside a spellbook or a bag once it is opened, and a
        // caster needs both (spells known, reagent counts). Open each one once, quietly
        // closing the gump again. An empty bag still looks unseen afterwards, so look again
        // only after 5 min. One that did not open (the server throttles use requests: "You
        // must wait to perform another action") is retried after 2 s, then after 30 s.
        private void UpdatePeek(uint now)
        {
            if (_peekSerial != 0)
            {
                Gump g = (Gump) UIManager.GetGump<SpellbookGump>(_peekSerial) ?? UIManager.GetGump<ContainerGump>(_peekSerial);

                if (g != null)
                {
                    g.Dispose();
                    _peekAgainAt[_peekSerial] = now + 300_000;
                    _peekSerial = 0;
                }
                else if (now - _peekAt > 3000)
                {
                    int failures = _peekFailures[_peekSerial] = _peekFailures.GetValueOrDefault(_peekSerial) + 1;
                    Log.Trace($"[agent] 0x{_peekSerial:X8} did not open ({failures})");
                    _peekAgainAt[_peekSerial] = now + (failures < 4 ? 2_000u : 30_000u);
                    _peekSerial = 0;
                }

                return;
            }

            if (now < _nextPeek)
            {
                return;
            }

            _nextPeek = now + 1000;
            PlayerMobile p = _world.Player;

            if (HumanActive || Mode == AgentMode.Off)
            {
                return;
            }

            bool Due(Item it) => !_peekAgainAt.TryGetValue(it.Serial, out uint at) || now >= at;

            // Every character: supplies, loot and banking all need the backpack's contents.
            Item pack = p.FindItemByLayer(Layer.Backpack);
            Item target = pack != null && pack.Items == null && !pack.Opened && Due(pack) ? pack : null;
            Item book = AgentSpells.FindSpellbook(p);

            if (target == null && (book == null || AgentSpells.MagerySkill(p) <= 0))
            {
                return;
            }

            target ??= book != null && !AgentSpells.ContentKnown(book) && Due(book) ? book : null;

            for (LinkedObject i = p.FindItemByLayer(Layer.Backpack)?.Items; i != null && target == null; i = i.Next)
            {
                if (i is Item it && it.Items == null && !it.Opened && it.ItemData.IsContainer && it.Graphic != AgentSpells.SPELLBOOK_GRAPHIC && Due(it))
                {
                    target = it;
                }
            }

            if (target == null)
            {
                return;
            }

            _peekSerial = target.Serial;
            _peekAt = now;
            Log.Trace($"[agent] looking inside 0x{target.Serial:X8} (graphic 0x{target.Graphic:X4})");
            GameActions.DoubleClick(_world, target.Serial);
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

        // ---------------------------------------------------------------- travel

        private const int TRAVEL_LEG = 16;           // tiles per leg handed to the client's pathfinder
        private const uint TRAVEL_STUCK_MS = 8000;   // no progress for this long: stuck
        private const int TRAVEL_MAX_REPLANS = 5;

        private (string, string) StartTravel(int x, int y, int distance)
        {
            _travelGoalX = x;
            _travelGoalY = y;
            _travelDistance = distance;
            _travelReplans = 0;
            _travelStuck.Clear();
            _travelBlocked.Clear();
            _travelStarted = Time.Ticks;

            if (!PlanTravel())
            {
                return ("failed", "no route");
            }

            Journal.AddAgentEvent($"travelling to {x},{y}");

            return ("done", $"{_travelPath.Count} tiles");
        }

        // The planning grid for a walk from the player to (gx, gy): a box around both, with the
        // impassable items in view (barricades, blockers; doors are opened instead) and the spots
        // already found stuck blocked. Null when the walk is too long for one plan.
        public NavGrid TravelGrid(int gx, int gy, HashSet<(int, int)> avoid = null, int widen = 1)
        {
            PlayerMobile p = _world.Player;
            int dist = Math.Max(Math.Abs(p.X - gx), Math.Abs(p.Y - gy));
            int margin = (24 + dist / 4) * widen;
            int x0 = Math.Min(p.X, gx) - margin, y0 = Math.Min(p.Y, gy) - margin;
            int w = Math.Abs(p.X - gx) + 2 * margin, h = Math.Abs(p.Y - gy) + 2 * margin;

            if (w > 900 || h > 900)
            {
                return null;
            }

            NavGrid grid = AgentNav.Load(_world.MapIndex, x0, y0, w, h);

            // The client forgets items out of view, so what was seen earlier on the trip is kept.
            foreach (Item it in _world.Items.Values)
            {
                if (it.OnGround && !it.IsMulti && it.ItemData.IsImpassable && !it.ItemData.IsDoor)
                {
                    _travelBlocked.Add((it.X, it.Y));
                }
            }

            foreach ((int bx, int by) in _travelBlocked)
            {
                grid.Block(bx, by);
            }

            foreach ((int bx, int by) in _travelStuck)
            {
                grid.Block(bx, by);
            }

            if (avoid != null)
            {
                foreach ((int bx, int by) in avoid)
                {
                    grid.Block(bx, by);
                }
            }

            return grid;
        }

        // Plans from where the player stands, in a box around the start and the goal; when the
        // way round something leaves that box, in a wider one.
        private bool PlanTravel(HashSet<(int, int)> avoid = null)
        {
            PlayerMobile p = _world.Player;
            int dist = Math.Max(Math.Abs(p.X - _travelGoalX), Math.Abs(p.Y - _travelGoalY));
            _travelPath = null;

            foreach (int widen in new[] { 1, 3, 6 })
            {
                NavGrid grid = TravelGrid(_travelGoalX, _travelGoalY, avoid, widen);

                if (grid == null)
                {
                    break;
                }

                _travelPath = AgentNav.Plan(grid, p.X, p.Y, p.Z, _travelGoalX, _travelGoalY, _travelDistance, 1_500_000);

                if (_travelPath != null)
                {
                    break;
                }
            }

            if (_travelPath == null && TravelGrid(_travelGoalX, _travelGoalY, avoid) == null)
            {
                TravelState = "too far";

                return false;
            }

            _travelIndex = 0;
            _travelBest = dist;
            _travelProgressAt = Time.Ticks;

            if (_travelPath == null)
            {
                TravelState = "no route";

                return false;
            }

            TravelState = "walking";

            return true;
        }

        public void StopTravel(string state)
        {
            if (_travelPath != null)
            {
                TravelState = state;
            }

            _travelPath = null;
        }

        private void UpdateTravel(uint now)
        {
            if (_travelPath == null || now < _nextTravelStep)
            {
                return;
            }

            _nextTravelStep = now + 250;
            PlayerMobile p = _world.Player;

            // Fighting, looting, fleeing or the player's hands on the controls: wait, without
            // counting it against progress.
            if (p.IsDead || _engaged != 0 || _lootCorpse != 0 || Fleeing || _takeQueue.Count != 0 || Paused(AgentBehavior.Move)
                || GetAuthority(AgentBehavior.Move) != AgentAuthority.Auto && !_travelManual)
            {
                _travelProgressAt = now;

                return;
            }

            int left = TravelLeft;

            if (left <= _travelDistance)
            {
                TravelState = "arrived";
                _travelPath = null;
                Journal.AddAgentEvent($"arrived at {_travelGoalX},{_travelGoalY}");

                return;
            }

            if (left < _travelBest)
            {
                _travelBest = left;
                _travelProgressAt = now;
            }

            // Where along the path the player is now.
            int nearest = _travelIndex, nearestD = int.MaxValue;

            for (int i = _travelIndex; i < _travelPath.Count && i < _travelIndex + 48; i++)
            {
                int d = Math.Max(Math.Abs(_travelPath[i].X - p.X), Math.Abs(_travelPath[i].Y - p.Y));

                if (d < nearestD)
                {
                    nearest = i;
                    nearestD = d;
                }
            }

            _travelIndex = nearest;

            if (now - _travelProgressAt > TRAVEL_STUCK_MS)
            {
                TravelStuck(p);

                return;
            }

            if (p.Pathfinder.AutoWalking)
            {
                return;
            }

            // The furthest point of the path the client's pathfinder can reach from here; if it
            // finds no way there, try nearer ones.
            int leg = AgentNav.NextLeg(_travelPath, _travelIndex, p.X, p.Y, TRAVEL_LEG);

            for (int i = leg; i > _travelIndex; i -= 3)
            {
                (int x, int y, sbyte z) = _travelPath[i];

                // The path already ends within the distance asked of the goal, so every leg,
                // the last too, walks to its tile.
                if (WalkTo(x, y, z, 0))
                {
                    return;
                }
            }

            // No leg plans: most often a closed door on the path, which the map files don't know.
            if (now - _doorTriedAt > 2500)
            {
                OpenDoorAhead(p, now);
            }
        }

        private uint _doorTriedAt;

        private void OpenDoorAhead(PlayerMobile p, uint now)
        {
            for (int i = _travelIndex; i < _travelPath.Count && i <= _travelIndex + 4; i++)
            {
                (int x, int y, sbyte z) = _travelPath[i];

                foreach (Item it in _world.Items.Values)
                {
                    // A closed door stands in the doorway, on the path; an open one has swung aside,
                    // and clicking it would close it.
                    if (it.OnGround && it.ItemData.IsDoor && it.X == x && it.Y == y && Math.Abs(it.Z - z) <= 20 && it.Distance <= 2)
                    {
                        _doorTriedAt = now;
                        Journal.AddAgentEvent($"opening a door at {it.X},{it.Y}");
                        GameActions.DoubleClick(_world, it.Serial);

                        return;
                    }
                }
            }
        }

        // No progress: try a door first; otherwise remember where, block the next stretch of the
        // path and plan around it.
        private void TravelStuck(PlayerMobile p)
        {
            uint now = Time.Ticks;

            if (now - _doorTriedAt > 6000)
            {
                OpenDoorAhead(p, now);

                if (_doorTriedAt == now)
                {
                    _travelProgressAt = now;

                    return;
                }
            }

            _travelStuck.Add((p.X, p.Y));
            Journal.AddAgentEvent($"stuck at {p.X},{p.Y} on the way to {_travelGoalX},{_travelGoalY}");

            if (++_travelReplans > TRAVEL_MAX_REPLANS)
            {
                StopTravel("stuck");

                return;
            }

            var avoid = new HashSet<(int, int)>();

            for (int i = _travelIndex + 1; i < _travelPath.Count && i <= _travelIndex + 4; i++)
            {
                avoid.Add((_travelPath[i].X, _travelPath[i].Y));
            }

            if (!PlanTravel(avoid))
            {
                StopTravel("stuck");
            }
        }

        private bool _travelManual;

        // An errand walking up to a vendor: travel without needing the move authority, since the
        // errand itself was allowed.
        public void TravelForErrand(int x, int y)
        {
            _travelManual = true;
            StartTravel(x, y, 1);
        }

        // A caster stepping back from the creatures in melee reach, between spells, keeping its
        // target: spells are interrupted by every hit. Casting roots the caster, so it only
        // goes once the current spell is off; the next queued spell waits for the step.
        private (string, string) Kite(int tiles)
        {
            PlayerMobile p = _world.Player;

            if (_castSpell != 0 && Time.Ticks < _castUntil)
            {
                return ("failed", "casting");
            }

            Vector2 away = Vector2.Zero;
            int near = 0;

            foreach (Mobile m in _world.Mobiles.Values)
            {
                if (m != p && !m.IsDead && m.Distance <= 2 && IsMonsterTarget(m))
                {
                    away += new Vector2(p.X - m.X, p.Y - m.Y);
                    near++;
                }
            }

            if (near == 0)
            {
                return ("done", "nothing close");
            }

            if (away == Vector2.Zero)
            {
                away = new Vector2(0, -1);
            }

            away.Normalize();

            foreach (float turn in new[] { 0f, 0.7f, -0.7f, 1.4f, -1.4f })
            {
                float cos = MathF.Cos(turn), sin = MathF.Sin(turn);
                var dir = new Vector2(away.X * cos - away.Y * sin, away.X * sin + away.Y * cos);

                if (WalkTo(p.X + (int) MathF.Round(dir.X * tiles), p.Y + (int) MathF.Round(dir.Y * tiles), p.Z, 0))
                {
                    _kiteUntil = Time.Ticks + 2500;
                    Stats.Kites++;

                    return ("done", string.Empty);
                }
            }

            return ("failed", "no room to step back");
        }

        public bool Kiting => _kiteUntil > Time.Ticks && _agentWalking;

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
            StopTravel("stopped");
            Errands.Cancel();
            _castSpell = 0;
            _queuedCast = null;
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
                Potion = GetAuthority(AgentBehavior.Potion),
                CastReady = CastReady && !p.IsParalyzed
            };

            Item book = AgentSpells.FindSpellbook(p);

            if (book != null && input.CastReady)
            {
                input.CanCastHeal = AgentSpells.Missing(this, p, book, SpellsMagery.GetSpell(AgentSpells.HEAL)).Length == 0;
                input.CanCastGreaterHeal = AgentSpells.Missing(this, p, book, SpellsMagery.GetSpell(AgentSpells.GREATER_HEAL)).Length == 0;
                input.CanCastCure = AgentSpells.Missing(this, p, book, SpellsMagery.GetSpell(AgentSpells.CURE)).Length == 0;
            }

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
                ReflexAction.DrinkCure => new AgentAction { Verb = "drink", Kind = "cure", Reason = "reflex" },
                ReflexAction.CastHeal => new AgentAction { Verb = "cast", Spell = AgentSpells.HEAL.ToString(), Target = uint.MaxValue, Reason = "reflex" },
                ReflexAction.CastGreaterHeal => new AgentAction { Verb = "cast", Spell = AgentSpells.GREATER_HEAL.ToString(), Target = uint.MaxValue, Reason = "reflex" },
                _ => new AgentAction { Verb = "cast", Spell = AgentSpells.CURE.ToString(), Target = uint.MaxValue, Reason = "reflex" }
            };

            if (auth == AgentAuthority.Auto)
            {
                Stats.Reflexes++;

                if (a.Verb == "cast")
                {
                    if (Cast(a, true).Item1 == "done")
                    {
                        Stats.SpellHeals++;
                    }
                }
                else
                {
                    Execute(a);
                }
            }
            else if (auth == AgentAuthority.Suggest && action != _lastHintedReflex)
            {
                _lastHintedReflex = action;
                Suggest(a);
            }
        }

        public static string ThreatKind(Mobile m, World world)
        {
            if (!m.IsHuman || m.IsDead || m == world.Player || world.Party.Contains(m.Serial))
            {
                return null;
            }

            switch (m.NotorietyFlag)
            {
                case NotorietyFlag.Murderer:
                    return "red";

                case NotorietyFlag.Criminal:
                    return "criminal";

                case NotorietyFlag.Innocent:
                case NotorietyFlag.Gray:
                case NotorietyFlag.Enemy:
                    // Townsfolk carry a title in their name ("Lucy the healer"); a person without one
                    // is a player. The name comes from item properties, or from a single click on
                    // shards without them; until it is known, only red and criminal are flagged.
                    string label = world.Agent.LabelOf(m);

                    return !string.IsNullOrEmpty(label) && !label.Contains(" the ", StringComparison.OrdinalIgnoreCase) ? "unknown" : null;

                default:
                    return null;
            }
        }

        private void UpdateThreats(uint now)
        {
            if (Mode == AgentMode.Off || now < _nextThreatLook)
            {
                return;
            }

            _nextThreatLook = now + 500;
            _threats.Clear();

            foreach (Mobile m in _world.Mobiles.Values)
            {
                if (m.Distance > THREAT_RANGE || ThreatKind(m, _world) is not string kind)
                {
                    continue;
                }

                _threats.Add((m.Serial, kind, m.Distance));

                // Above their head and in the panel, once a minute per player (and again if they turn red).
                if (!_threatWarned.TryGetValue((m.Serial, kind), out uint at) || now - at > 60_000)
                {
                    _threatWarned[(m.Serial, kind)] = now;
                    string what = kind == "unknown" ? "an unknown player" : $"a {kind} player";
                    m.AddMessage(MessageType.Regular, $"jev: {what}", 3, 0x0021, true, TextType.CLIENT);
                    Journal.AddAgentEvent($"{what} is {m.Distance} tiles away");
                    Stats.Threats++;
                }
            }
        }

        // What the player is fighting: their attack target (also set by the server when they
        // fight back) or a creature they targeted with a cursor, whichever came last.
        private void UpdatePlayerTarget()
        {
            TargetManager tm = _world.TargetManager;
            uint last = tm.LastAttack;

            if (last != _seenLastAttackForPlayer)
            {
                _seenLastAttackForPlayer = last;

                if (last != 0 && last != _agentAttack)
                {
                    _playerTarget = last;
                }
            }

            uint cursor = tm.LastTargetInfo.IsEntity ? tm.LastTargetInfo.Serial : 0;

            if (cursor != _seenTargetInfo)
            {
                _seenTargetInfo = cursor;

                if (cursor != 0 && cursor != _agentTargeted && cursor != _world.Player.Serial && _world.Mobiles.Get(cursor) is Mobile m && IsMonsterTarget(m))
                {
                    _playerTarget = cursor;
                }
            }

            if (_playerTarget != 0 && (!(_world.Mobiles.Get(_playerTarget) is Mobile t) || t.IsDead))
            {
                _playerTarget = 0;
            }
        }

        private uint _seenLastAttackForPlayer;

        // Combat assist without walking: fight what the player fights, and, as the engage
        // setting allows, what attacks them or anything nearby. A brain may pick targets too
        // (Request checks them against the same setting); this keeps a warrior swinging with
        // no brain running at all.
        private void UpdateAssist(uint now)
        {
            if (Mode != AgentMode.Assist || GetAuthority(AgentBehavior.Fight) != AgentAuthority.Auto || _world.Player.IsDead || now < _nextAssistLook)
            {
                return;
            }

            _nextAssistLook = now + 400;

            if (_playerTarget != 0 && _engaged != _playerTarget && _world.Mobiles.Get(_playerTarget) is Mobile chosen && IsMonsterTarget(chosen))
            {
                _engaged = _playerTarget;
                _agentAttack = _playerTarget;
                Journal.AddAgentEvent($"following your target 0x{_playerTarget:X8}");

                return;
            }

            Mobile current = _engaged == 0 ? null : _world.Mobiles.Get(_engaged);

            if (Engage == AgentEngage.Follow || current != null && !current.IsDead)
            {
                return;
            }

            Mobile pick = null;

            foreach (Mobile m in _world.Mobiles.Values)
            {
                if (m == _world.Player || m.IsDead || !IsMonsterTarget(m))
                {
                    continue;
                }

                bool ok = Engage == AgentEngage.Nearby ? m.Distance <= 8 : IsAttackingMe(m);

                if (ok && (pick == null || m.Distance < pick.Distance))
                {
                    pick = m;
                }
            }

            if (pick != null)
            {
                Attack(pick.Serial, false, _engagedRange);
            }
        }

        private void UpdateEngagement(uint now)
        {
            // The player attacked something else themselves: their choice stands. The
            // server also changes the attack target (0xAA), so only a change right after
            // the player's own input counts.
            uint last = _world.TargetManager.LastAttack;

            if (last != _seenLastAttack)
            {
                _seenLastAttack = last;

                if (_engaged != 0 && last != 0 && last != _engaged && last != _agentAttack && _lastHumanInput != 0 && now - _lastHumanInput < 1500)
                {
                    Mobile chosen = _world.Mobiles.Get(last);
                    _engaged = chosen != null && IsMonsterTarget(chosen) ? last : 0;
                    _agentAttack = _engaged;
                    Journal.AddAgentEvent(_engaged != 0 ? $"player switched target to 0x{last:X8}" : "player took over the fight");
                }
            }

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

            // Casting roots the caster, so do not walk until the cast delay is over.
            if (Paused(AgentBehavior.Fight) || Fleeing || Kiting || now < _nextPursuit || (_castSpell != 0 && now < _castUntil))
            {
                return;
            }

            _nextPursuit = now + PURSUIT_INTERVAL_MS;
            PlayerMobile p = _world.Player;

            if (_world.TargetManager.LastAttack != _engaged && now - _lastAttackSent > 2000)
            {
                _lastAttackSent = now;
                _agentAttack = _engaged;
                GameActions.Attack(_world, _engaged);
            }

            // Melee closes to adjacent; a caster only closes to spell range. Only with moving
            // allowed: combat assist never walks the character.
            if (m.Distance > _engagedRange && GetAuthority(AgentBehavior.Move) == AgentAuthority.Auto && !Paused(AgentBehavior.Move))
            {
                bool moved = m.X != _engagedLastX || m.Y != _engagedLastY;

                if (!p.Pathfinder.AutoWalking || moved)
                {
                    _engagedLastX = m.X;
                    _engagedLastY = m.Y;
                    WalkTo(m.X, m.Y, m.Z, _engagedRange);
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
                if (!p.Pathfinder.AutoWalking && !Paused(AgentBehavior.Loot))
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
                _gump = new AgentGump(_world, this);
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
            Strategy = ProfileText(profile.AgentStrategy);
            StrategyRevision++;
            Goal = ProfileText(profile.AgentGoal);
            GoalPaused = profile.AgentGoalPaused;
            GoalRevision++;

            AgentModes.TryParse(profile.AgentMode, out AgentMode mode);
            Mode = mode;
            Engage = AgentModes.TryParse(profile.AgentEngage, out AgentEngage engage) ? engage : AgentEngage.Defend;
            AddDefaultMacros(profile);

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

        // Once per character: Alt+A switches between combat assist and auto, Alt+D does Jev's
        // next move. Keys the player already uses are left alone; the macros can be rebound or
        // deleted in Options → Macros and are not added again. (Not Alt+N: on a Mac, Option+N
        // is a dead key that starts an accented letter, so the game never sees it.)
        private void AddDefaultMacros(Profile profile)
        {
            if (_world.Macros == null)
            {
                return;
            }

            if (profile.AgentMacrosAdded)
            {
                Macro old = _world.Macros.FindMacro("Agent: next move");

                if (old != null && old.Key == (SDL3.SDL.SDL_Keycode) 'n' && old.Alt && _world.Macros.FindMacro((SDL3.SDL.SDL_Keycode) 'd', true, false, false) == null)
                {
                    old.Key = (SDL3.SDL.SDL_Keycode) 'd';
                    _world.Macros.Save();
                }

                return;
            }

            profile.AgentMacrosAdded = true;

            foreach ((string name, char key, MacroType type) in new[] { ("Agent: switch", 'a', MacroType.AgentSwitch), ("Agent: next move", 'd', MacroType.AgentNext) })
            {
                bool free = _world.Macros.FindMacro((SDL3.SDL.SDL_Keycode) key, true, false, false) == null;
                var macro = new Macro(name, free ? (SDL3.SDL.SDL_Keycode) key : 0, free, false, false);
                macro.PushToBack(new MacroObject(type, MacroSubType.MSC_NONE));
                _world.Macros.PushToBack(macro);
            }

            _world.Macros.Save();
        }

        // ClassicUO's profile loader doubles single backslashes (for Windows paths), so the JSON
        // escapes in saved text come back literally: a backslash and "n" for a line break,
        // "'" spelt out for an apostrophe. Undo that for the agent's texts.
        public static string ProfileText(string saved)
        {
            if (string.IsNullOrEmpty(saved) || saved.IndexOf('\\') < 0)
            {
                return saved ?? string.Empty;
            }

            string text = System.Text.RegularExpressions.Regex.Replace(saved, @"\\u([0-9a-fA-F]{4})",
                m => ((char) Convert.ToInt32(m.Groups[1].Value, 16)).ToString());

            return text.Replace("\\n", "\n").Replace("\\t", "\t").Replace("\\\"", "\"").Replace("\\\\", "\\");
        }

        private void SaveToProfile()
        {
            Profile profile = ProfileManager.CurrentProfile;

            if (profile == null || !_profileLoaded)
            {
                return;
            }

            profile.AgentMode = Mode.Name();
            profile.AgentEngage = Engage.Name();
            profile.AgentBandageBelowPercent = Reflexes.BandageBelowPercent;
            profile.AgentHealPotionBelowPercent = Reflexes.HealPotionBelowPercent;
            profile.AgentStrategy = Strategy;
            profile.AgentGoal = Goal;
            profile.AgentGoalPaused = GoalPaused;

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

        public void SetGoal(string text)
        {
            EnsureProfileLoaded();
            text = (text ?? string.Empty).Trim();
            Goal = text.Length > MAX_STRATEGY_LENGTH ? text.Substring(0, MAX_STRATEGY_LENGTH) : text;
            GoalRevision++;
            GoalPaused = false;
            GoalStep = GoalWhy = string.Empty;
            SaveToProfile();
        }

        public void PauseGoal(bool paused)
        {
            EnsureProfileLoaded();
            GoalPaused = paused;
            GoalRevision++;
            SaveToProfile();
        }

        // From the brain: the step it is on for the goal, and why.
        public void SetGoalStatus(string step, string why)
        {
            GoalStep = step ?? string.Empty;
            GoalWhy = why ?? string.Empty;
            GoalStepTime = _lastBrainContact = Time.Ticks;
            _decisionSeq++;
        }

        private void GoalCommand(string[] args)
        {
            string verb = args.Length > 2 ? args[2].ToLowerInvariant() : "show";
            string rest = args.Length > 3 ? string.Join(" ", args, 3, args.Length - 3) : string.Empty;

            switch (verb)
            {
                case "show":
                    Print(Goal.Length == 0 ? "goal: (none)" : $"goal{(GoalPaused ? " (paused)" : "")}: {Goal}");

                    if (GoalStep.Length != 0)
                    {
                        Print($"now: {GoalStep}{(GoalWhy.Length != 0 ? " (" + GoalWhy + ")" : "")}");
                    }

                    return;
                case "clear":
                    SetGoal(string.Empty);
                    Print("goal cleared");

                    return;
                case "pause":
                case "resume":
                    PauseGoal(verb == "pause");
                    Print(verb == "pause" ? "goal paused" : "goal resumed");

                    return;
                case "templates":
                    foreach (AgentTemplate t in AgentTemplates.Goals())
                    {
                        Print($"{t.Name}: {t.Summary}");
                    }

                    Print("-agent goal template <name> sets one");

                    return;
                case "template":
                    AgentTemplate found = AgentTemplates.FindGoal(rest);

                    if (found == null)
                    {
                        Print($"no goal template '{rest}' (try -agent goal templates)");

                        return;
                    }

                    SetGoal(found.Text);
                    Print($"goal: {found.Title}");

                    return;
                default:
                    SetGoal(string.Join(" ", args, 2, args.Length - 2));
                    Print($"goal: {Goal}");

                    return;
            }
        }

        public void AddStrategy(string line)
        {
            SetStrategy(string.IsNullOrEmpty(Strategy) ? line : Strategy + "\n" + line);
        }

        // ---------------------------------------------------------------- templates

        // A template is "in use" while its text is part of the strategy.
        public bool TemplateInUse(AgentTemplate t) => t.Text.Length != 0 && Strategy.Contains(t.Text, StringComparison.Ordinal);

        // Pulls a template into the strategy (after what is there), or replaces the strategy with it.
        public bool PullTemplate(AgentTemplate t, bool replace)
        {
            if (replace)
            {
                SetStrategy(t.Text);
            }
            else if (TemplateInUse(t))
            {
                return false;
            }
            else
            {
                AddStrategy(t.Text);
            }

            Print($"template {t.Title} {(replace ? "is now the strategy" : "pulled in")}");

            return true;
        }

        public bool DropTemplate(AgentTemplate t)
        {
            if (!TemplateInUse(t))
            {
                return false;
            }

            var lines = new List<string>(Strategy.Replace(t.Text, string.Empty).Split('\n'));
            lines.RemoveAll(string.IsNullOrWhiteSpace);
            SetStrategy(string.Join("\n", lines));
            Print($"template {t.Title} removed");

            return true;
        }

        private void TemplateCommand(string[] args)
        {
            string verb = args.Length > 2 ? args[2].ToLowerInvariant() : "list";
            bool named = verb == "set" || verb == "add" || verb == "remove";
            string name = named ? (args.Length > 3 ? string.Join(" ", args, 3, args.Length - 3) : string.Empty)
                : verb == "list" ? string.Empty : string.Join(" ", args, 2, args.Length - 2);

            if (verb == "list" || name.Length == 0)
            {
                foreach (AgentTemplate t in AgentTemplates.All())
                {
                    Print($"{(TemplateInUse(t) ? "* " : "")}{t.Name} ({t.For}): {t.Summary}");
                }

                Print("-agent template <name> pulls one in; set <name> replaces the strategy; remove <name>");

                return;
            }

            AgentTemplate found = AgentTemplates.Find(name);

            if (found == null)
            {
                Print($"no template '{name}' (try -agent template list)");
            }
            else if (verb == "remove")
            {
                if (!DropTemplate(found))
                {
                    Print($"template {found.Title} is not in use");
                }
            }
            else if (!PullTemplate(found, verb == "set"))
            {
                Print($"template {found.Title} is already in use");
            }
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

        // "-agent [off|combat|auto|switch|next|status|accept|engage <follow|defend|nearby>|set <behaviour> <off|suggest|auto>|bandage <pct>|potion <pct>
        //         |strategy [set <text>|add <text>|clear]|template [list|<name>|set <name>|remove <name>]]"
        public void OnCommand(string[] args)
        {
            string sub = args.Length > 1 ? args[1].ToLowerInvariant() : "status";

            switch (sub)
            {
                case "off":
                case "assist":
                case "combat":
                case "auto":
                    AgentModes.TryParse(sub, out AgentMode mode);
                    SetMode(mode);
                    Print($"mode {Mode.Title()}");

                    break;

                case "accept":
                    if (!AcceptSuggestion())
                    {
                        Print("nothing to accept");
                    }

                    break;

                case "switch":
                case "toggle":
                    SwitchPlayState();

                    break;

                case "next":
                    DoNext();

                    break;

                case "engage" when args.Length > 2 && AgentModes.TryParse(args[2], out AgentEngage engage):
                    SetEngage(engage);
                    Print($"combat assist engages {Engage.Title()}");

                    break;

                case "engage":
                    Print($"combat assist engages {Engage.Title()}; -agent engage follow|defend|nearby");

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

                case "template":
                case "templates":
                    TemplateCommand(args);

                    break;

                case "goal":
                    GoalCommand(args);

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
                    var sb = new StringBuilder($"mode {Mode.Title()} (engages {Engage.Title()}):");

                    foreach (AgentBehavior beh in AgentModes.AllBehaviors)
                    {
                        sb.Append(' ').Append(beh.Name()).Append('=').Append(GetAuthority(beh).Name());
                    }

                    sb.Append($" | bandage<{Reflexes.BandageBelowPercent}% potion<{Reflexes.HealPotionBelowPercent}% | brain {(AgentHost.BrainConnected ? "connected" : "not connected")}");
                    Print(sb.ToString());

                    break;

                default:
                    Print("usage: -agent off|combat|auto|switch|next|status|accept|engage <follow|defend|nearby>|set <behaviour> <off|suggest|auto>|bandage <pct>|potion <pct>|strategy [set|add|clear] <text>|template [list|<name>|set <name>|remove <name>]");

                    break;
            }
        }
    }

    internal sealed class AgentStats
    {
        public int Kills, Deaths, Attacks, Bandages, HealPotions, CurePotions, Reflexes, Flees, Looted, ItemsTaken;
        public int BrainActions, Suggestions, Accepted, Blocked, Deferred, Casts, SpellHeals, Threats, Kites;
        public int ClilocMessages, TextMessages; // server messages acted on, by number and by English text
    }
}
