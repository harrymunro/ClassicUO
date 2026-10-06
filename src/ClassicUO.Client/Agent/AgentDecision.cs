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
        public readonly List<string> Results = new List<string>();
        public string Note = string.Empty;

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
