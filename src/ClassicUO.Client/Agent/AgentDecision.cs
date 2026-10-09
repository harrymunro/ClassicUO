// SPDX-License-Identifier: BSD-2-Clause

using System.Collections.Generic;
using System.Text.Json;

namespace ClassicUO.Agent
{
    // One decision as the brain reports it after acting ("decision" RPC): what Jev
    // was asked and answered, what the policy made of it and what happened. Kept
    // only for display in the agent gump.
    internal sealed class AgentDecision
    {
        public int Seq;
        public uint Time;

        public string Judge = string.Empty;
        public string Archetype = string.Empty;
        public float LatencyMs = -1;

        public string Intent = string.Empty;
        public float Confidence = -1;
        public bool Gated;
        public readonly List<string> Masked = new List<string>();

        // Jev's own probabilities for each intent, in the order it was asked.
        public readonly List<(string Name, float P)> Intents = new List<(string, float)>();
        public float Danger = -1;

        public uint TargetSerial;
        public string TargetName = string.Empty;
        public float TargetConfidence = -1;

        public string SpellName = string.Empty;
        public float SpellConfidence = -1;
        public string SpellWhy = string.Empty; // "strategy", "jev" or "fallback"

        public readonly List<AgentAction> Actions = new List<AgentAction>();

        // Jev's best next combat move, done or not, for the next-move key.
        public AgentAction Next;
        public readonly List<string> Results = new List<string>();
        public string Note = string.Empty;

        // The plan being followed, when the brain runs one (a state machine): its name, the step
        // it is at and what that step means, every step in order, and the last move between them.
        public string PlanName = string.Empty;
        public string PlanStep = string.Empty;
        public string PlanSays = string.Empty;
        public string PlanLast = string.Empty;
        public readonly List<string> PlanSteps = new List<string>();

        // Every question of the call with Jev's options and pick, for the live view of calls, and
        // what was done about it in words, with where each move went ("run 15 tiles northwest").
        public readonly List<AgentCallQuestion> Questions = new List<AgentCallQuestion>();
        public readonly List<(string What, string Result)> Did = new List<(string, string)>();

        // What the call cost (dollars; -1 when not said), and the session's running total with it.
        public float Cost = -1;
        public AgentSpent Spent;

        public static AgentDecision FromJson(JsonElement p)
        {
            var d = new AgentDecision();

            if (p.ValueKind != JsonValueKind.Object)
            {
                return d;
            }

            foreach (JsonProperty prop in p.EnumerateObject())
            {
                JsonElement v = prop.Value;

                if (v.ValueKind == JsonValueKind.Null)
                {
                    continue;
                }

                switch (prop.Name)
                {
                    case "judge": d.Judge = v.GetString() ?? string.Empty; break;
                    case "archetype": d.Archetype = v.GetString() ?? string.Empty; break;
                    case "latency_ms": d.LatencyMs = v.GetSingle(); break;
                    case "cost" when v.ValueKind == JsonValueKind.Number: d.Cost = v.GetSingle(); break;
                    case "spent": d.Spent = AgentSpent.FromJson(v); break;
                    case "intent": d.Intent = v.GetString() ?? string.Empty; break;
                    case "confidence": d.Confidence = v.GetSingle(); break;
                    case "gated": d.Gated = v.GetBoolean(); break;
                    case "danger": d.Danger = v.GetSingle(); break;
                    case "note": d.Note = v.GetString() ?? string.Empty; break;

                    case "masked":
                        foreach (JsonElement m in v.EnumerateArray())
                        {
                            d.Masked.Add(m.GetString() ?? string.Empty);
                        }

                        break;

                    case "intents":
                        foreach (JsonProperty i in v.EnumerateObject())
                        {
                            d.Intents.Add((i.Name, i.Value.GetSingle()));
                        }

                        break;

                    case "target":
                        foreach (JsonProperty t in v.EnumerateObject())
                        {
                            switch (t.Name)
                            {
                                case "serial": d.TargetSerial = AgentAction.ReadSerial(t.Value); break;
                                case "name": d.TargetName = t.Value.GetString() ?? string.Empty; break;
                                case "confidence": d.TargetConfidence = t.Value.GetSingle(); break;
                            }
                        }

                        break;

                    case "spell":
                        foreach (JsonProperty t in v.EnumerateObject())
                        {
                            switch (t.Name)
                            {
                                case "name": d.SpellName = t.Value.GetString() ?? string.Empty; break;
                                case "confidence": d.SpellConfidence = t.Value.GetSingle(); break;
                                case "why": d.SpellWhy = t.Value.GetString() ?? string.Empty; break;
                            }
                        }

                        break;

                    case "actions":
                        foreach (JsonElement a in v.EnumerateArray())
                        {
                            d.Actions.Add(AgentAction.FromJson(a));
                        }

                        break;

                    case "machine" when v.ValueKind == JsonValueKind.Object:
                        foreach (JsonProperty t in v.EnumerateObject())
                        {
                            switch (t.Name)
                            {
                                case "name": d.PlanName = t.Value.GetString() ?? string.Empty; break;
                                case "state": d.PlanStep = t.Value.GetString() ?? string.Empty; break;
                                case "says": d.PlanSays = t.Value.GetString() ?? string.Empty; break;
                                case "last": d.PlanLast = t.Value.GetString() ?? string.Empty; break;

                                case "states" when t.Value.ValueKind == JsonValueKind.Array:
                                    foreach (JsonElement st in t.Value.EnumerateArray())
                                    {
                                        d.PlanSteps.Add(st.GetString() ?? string.Empty);
                                    }

                                    break;
                            }
                        }

                        break;

                    case "questions":
                        d.Questions.AddRange(AgentCall.ParseQuestions(v));

                        break;

                    case "did" when v.ValueKind == JsonValueKind.Array:
                        foreach (JsonElement a in v.EnumerateArray())
                        {
                            string what = a.TryGetProperty("what", out JsonElement w) ? w.GetString() ?? "" : "";
                            string result = a.TryGetProperty("result", out JsonElement r) ? r.GetString() ?? "" : "";
                            d.Did.Add((what, result));
                        }

                        break;

                    case "next" when v.ValueKind == JsonValueKind.Object:
                        d.Next = AgentAction.FromJson(v);

                        break;

                    case "results":
                        foreach (JsonElement r in v.EnumerateArray())
                        {
                            d.Results.Add(r.ValueKind == JsonValueKind.String ? r.GetString() : r.GetRawText());
                        }

                        break;
                }
            }

            return d;
        }
    }
}
