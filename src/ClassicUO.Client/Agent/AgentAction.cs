// SPDX-License-Identifier: BSD-2-Clause

using System.Text.Json;
using ClassicUO.Game;
using ClassicUO.Game.Data;
using ClassicUO.Game.GameObjects;
using ClassicUO.Game.Managers;

namespace ClassicUO.Agent
{
    // One requested action, parsed out of the RPC params so it can outlive the
    // request (a suggestion waits for the player to accept it).
    internal sealed class AgentAction
    {
        public string Verb = string.Empty;
        public uint Target;
        public int X, Y;
        public int Distance = 1;
        public bool HasDistance;              // given, not the default: a follow keeps within 2 unless told
        public int Range = 1;
        public int Tiles = 8;
        public bool On = true;
        public bool Queue;
        public bool Run = true;               // walk_to: false walks
        public string Pace = string.Empty;    // travel, follow: "walk" or "run"
        public string Kind = string.Empty;
        public string Text = string.Empty;
        public string Direction = string.Empty;
        public string Spell = string.Empty;
        public string Name = string.Empty;
        public string Deposit = string.Empty, Withdraw = string.Empty, Items = string.Empty; // bank, buy, sell
        public float Confidence = -1;
        public string Reason = string.Empty;

        // "brain" actions are subject to the behaviour's authority; "manual" ones
        // (the player, the CLI, an accepted suggestion) run as asked.
        public bool Manual;

        // A skill's targets, answered to its target cursors in order (provocation asks for two).
        public uint[] Targets = System.Array.Empty<uint>();

        public AgentBehavior Behavior
        {
            get
            {
                switch (Verb)
                {
                    case "bandage_self":
                    case "bandage":
                        return AgentBehavior.Heal;

                    case "drink":
                        return Kind == "heal" ? AgentBehavior.Potion : Kind == "cure" ? AgentBehavior.Cure : AgentBehavior.Misc;

                    case "attack":
                    case "war_mode":
                        return AgentBehavior.Fight;

                    // A bard's songs are aimed at monsters, so they answer to fight.
                    case "skill":
                        return AgentBard.IsSong(Name) ? AgentBehavior.Fight : AgentBehavior.Misc;

                    // Setting the pet on a creature is fighting; calling it back is moving.
                    case "pet":
                        return Kind == "kill" ? AgentBehavior.Fight : AgentBehavior.Move;

                    // Healing spells answer to the same authority as bandages and potions;
                    // attack spells, and a paladin's fighting blessings, to fight.
                    case "cast":
                        SpellDefinition spell = AgentSpells.Find(Spell);

                        return spell == null ? AgentBehavior.Misc
                            : spell.ID == AgentSpells.HEAL || spell.ID == AgentSpells.GREATER_HEAL || spell.ID == AgentSpells.CLOSE_WOUNDS
                                ? AgentBehavior.Heal
                            : spell.ID == AgentSpells.CURE || spell.ID == AgentSpells.CLEANSE_BY_FIRE ? AgentBehavior.Cure
                            : spell.TargetType == TargetType.Harmful || AgentSpells.FightSpells.Contains(spell.ID) ? AgentBehavior.Fight
                            : AgentBehavior.Misc;

                    case "loot":
                    case "take":
                        return AgentBehavior.Loot;

                    case "walk_to":
                    case "travel":
                    case "follow":
                    case "move":
                    case "flee":
                    case "kite":
                    case "stop":
                    case "disengage":
                        return AgentBehavior.Move;

                    default:
                        return AgentBehavior.Misc;
                }
            }
        }

        public static AgentAction FromJson(JsonElement p)
        {
            var a = new AgentAction();

            foreach (JsonProperty prop in p.EnumerateObject())
            {
                JsonElement v = prop.Value;

                switch (prop.Name)
                {
                    case "verb": a.Verb = v.GetString() ?? string.Empty; break;
                    case "target": a.Target = ReadSerial(v); break;
                    case "x": a.X = v.GetInt32(); break;
                    case "y": a.Y = v.GetInt32(); break;
                    case "distance": a.Distance = v.GetInt32(); a.HasDistance = true; break;
                    case "range": a.Range = v.GetInt32(); break;
                    case "spell": a.Spell = v.ValueKind == JsonValueKind.Number ? v.GetInt32().ToString() : v.GetString() ?? string.Empty; break;
                    case "name": a.Name = v.GetString() ?? string.Empty; break;
                    case "tiles": a.Tiles = v.GetInt32(); break;
                    case "on": a.On = v.GetBoolean(); break;
                    case "queue": a.Queue = v.GetBoolean(); break;
                    case "run": a.Run = ReadBool(v, true); break;
                    case "pace": a.Pace = v.GetString() ?? string.Empty; break;
                    case "kind": a.Kind = v.GetString() ?? string.Empty; break;
                    case "text": a.Text = v.GetString() ?? string.Empty; break;
                    case "dir": a.Direction = v.GetString() ?? string.Empty; break;
                    case "confidence": a.Confidence = v.GetSingle(); break;
                    case "reason": a.Reason = v.GetString() ?? string.Empty; break;
                    case "deposit": a.Deposit = v.GetString() ?? string.Empty; break;
                    case "withdraw": a.Withdraw = v.GetString() ?? string.Empty; break;
                    case "items": a.Items = v.GetString() ?? string.Empty; break;
                    case "targets" when v.ValueKind == JsonValueKind.Array:
                        var targets = new System.Collections.Generic.List<uint>();

                        foreach (JsonElement t in v.EnumerateArray())
                        {
                            targets.Add(ReadSerial(t));
                        }

                        a.Targets = targets.ToArray();

                        break;
                    case "source": a.Manual = v.GetString() == "manual"; break;
                }
            }

            return a;
        }

        // Serials travel as numbers; "self" names the player.
        public static uint ReadSerial(JsonElement v)
        {
            if (v.ValueKind == JsonValueKind.String)
            {
                string s = v.GetString();

                if (s == "self")
                {
                    return uint.MaxValue;
                }

                return s != null && s.StartsWith("0x") ? uint.Parse(s.Substring(2), System.Globalization.NumberStyles.HexNumber) : uint.Parse(s);
            }

            return v.GetUInt32();
        }

        // A flag as a boolean, or as a word from a command line ("false", "no", "off", "0").
        public static bool ReadBool(JsonElement v, bool otherwise)
        {
            switch (v.ValueKind)
            {
                case JsonValueKind.True: return true;
                case JsonValueKind.False: return false;
                case JsonValueKind.Number: return v.GetDouble() != 0;
                case JsonValueKind.String:
                    string s = (v.GetString() ?? string.Empty).Trim().ToLowerInvariant();

                    return s == "true" || s == "yes" || s == "on" || s == "1" || (s != "false" && s != "no" && s != "off" && s != "0" && otherwise);

                default: return otherwise;
            }
        }

        // The pace asked: true for a walk. A trip runs unless a walk is asked, a follow walks
        // unless a run is.
        public bool WalkPace(bool otherwise) => Pace == "walk" || (otherwise && Pace != "run");

        public bool BadPace => Pace.Length != 0 && Pace != "walk" && Pace != "run";

        // An emote as it is said: between asterisks, as the client's own emote key writes it.
        public static string EmoteText(string text)
        {
            string t = (text ?? string.Empty).Trim();

            return t.Length >= 2 && t[0] == '*' && t[t.Length - 1] == '*' ? t : "*" + t.Trim('*').Trim() + "*";
        }

        // Short text for the overlay and the journal, e.g. "attack an orc".
        public string Describe(World world)
        {
            string target = Target == uint.MaxValue ? "self" : NameOf(world, Target);

            switch (Verb)
            {
                case "attack": return $"attack {target}";
                case "bandage_self": return "bandage self";
                case "bandage": return $"bandage {target}";
                case "drink": return $"drink {Kind} potion";
                case "loot": return $"loot {target}";
                case "take": return $"take {target}";
                case "flee": return Target != 0 ? $"flee from {target}" : "flee";
                case "walk_to": return $"walk to {X},{Y}";
                case "travel": return $"travel to {X},{Y}";
                case "follow": return $"follow {target}";
                case "kite": return "step back";
                case "disengage": return "let the fight go";
                case "pet": return Kind == "kill" ? $"set the pet on {target}" : Kind == "follow" ? "call the pet back" : $"pet: {Kind}";
                case "move": return $"move {Direction}";
                case "war_mode": return On ? "war mode on" : "peace mode";
                case "say": return Kind == "emote" ? $"emote {EmoteText(Text)}" : $"say \"{Text}\"";
                case "use": return $"use {target}";
                case "cast":
                    string spell = AgentSpells.Find(Spell)?.Name ?? Spell;

                    return Target == 0 ? $"cast {spell}" : Target == uint.MaxValue ? $"cast {spell} on self" : $"cast {spell} at {target}";
                case "skill": return Targets.Length == 0 ? $"use {Name}" : Targets.Length == 1 ? $"{Name} on {NameOf(world, Targets[0])}"
                    : $"{Name}: {NameOf(world, Targets[0])} at {NameOf(world, Targets[1])}";
                case "bank": return "bank" + (Deposit.Length != 0 ? $": deposit {Deposit}" : "") + (Withdraw.Length != 0 ? $", withdraw {Withdraw}" : "");
                case "buy": return $"buy {Items}";
                case "sell": return $"sell {Items}";
                case "hint": return Text;
                default: return Verb;
            }
        }

        private static string NameOf(World world, uint serial)
        {
            Entity e = world.Get(serial);

            if (e == null)
            {
                return $"0x{serial:X8}";
            }

            if (world.OPL.TryGetNameAndData(serial, out string name, out _) && !string.IsNullOrEmpty(name))
            {
                return name.Trim();
            }

            return string.IsNullOrEmpty(e.Name) ? (e is Item it ? it.ItemData.Name : $"0x{serial:X8}") : e.Name.Trim();
        }
    }
}
