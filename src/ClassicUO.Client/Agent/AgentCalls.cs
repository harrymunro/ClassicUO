// SPDX-License-Identifier: BSD-2-Clause

using System.Collections.Generic;
using System.Text.Json;

namespace ClassicUO.Agent
{
    // One option of a question the model answered, with its probability.
    internal sealed class AgentCallOption
    {
        public string Id = string.Empty;
        public string Label = string.Empty;
        public float P;
        public bool Kept; // for a pick of several (world facts): one of those kept
    }

    // One question of a model call, as the brain reports it for the live view: a choice with its
    // options, Jev's pick and what code went with; a yes/no with its probability, the cut that
    // decides it and the verdict; a score; or a line of text (the planner's goal).
    internal sealed class AgentCallQuestion
    {
        public string Q = string.Empty;
        public string Title = string.Empty;
        public string Kind = string.Empty; // choice, many, yesno, score, text
        public readonly List<AgentCallOption> Options = new List<AgentCallOption>();
        public string Picked = string.Empty;
        public string Used = string.Empty;
        public float Confidence = -1, P = -1, Cut = -1;
        public string Verdict = string.Empty;

        public string LabelOf(string id)
        {
            foreach (AgentCallOption o in Options)
            {
                if (o.Id == id)
                {
                    return o.Label;
                }
            }

            return id;
        }

        public static AgentCallQuestion FromJson(JsonElement q)
        {
            var question = new AgentCallQuestion();

            if (q.ValueKind != JsonValueKind.Object)
            {
                return question;
            }

            foreach (JsonProperty prop in q.EnumerateObject())
            {
                JsonElement v = prop.Value;

                switch (prop.Name)
                {
                    case "q" when v.ValueKind == JsonValueKind.String: question.Q = v.GetString(); break;
                    case "title" when v.ValueKind == JsonValueKind.String: question.Title = v.GetString(); break;
                    case "kind" when v.ValueKind == JsonValueKind.String: question.Kind = v.GetString(); break;
                    case "picked" when v.ValueKind == JsonValueKind.String: question.Picked = v.GetString(); break;
                    case "used" when v.ValueKind == JsonValueKind.String: question.Used = v.GetString(); break;
                    case "verdict" when v.ValueKind == JsonValueKind.String: question.Verdict = v.GetString(); break;
                    case "confidence" when v.ValueKind == JsonValueKind.Number: question.Confidence = v.GetSingle(); break;
                    case "p" when v.ValueKind == JsonValueKind.Number: question.P = v.GetSingle(); break;
                    case "cut" when v.ValueKind == JsonValueKind.Number: question.Cut = v.GetSingle(); break;

                    case "options" when v.ValueKind == JsonValueKind.Array:
                        foreach (JsonElement o in v.EnumerateArray())
                        {
                            var opt = new AgentCallOption();

                            foreach (JsonProperty op in o.EnumerateObject())
                            {
                                switch (op.Name)
                                {
                                    case "id" when op.Value.ValueKind == JsonValueKind.String: opt.Id = op.Value.GetString(); break;
                                    case "label" when op.Value.ValueKind == JsonValueKind.String: opt.Label = op.Value.GetString(); break;
                                    case "p" when op.Value.ValueKind == JsonValueKind.Number: opt.P = op.Value.GetSingle(); break;
                                    case "kept" when op.Value.ValueKind is JsonValueKind.True or JsonValueKind.False:
                                        opt.Kept = op.Value.GetBoolean();

                                        break;
                                }
                            }

                            question.Options.Add(opt);
                        }

                        break;
                }
            }

            return question;
        }
    }

    // One model call other than a fight decision ("ai_call" RPC): a routine hunt question, a pick of
    // world facts, a strategy read, a planner goal. The latest of each kind is kept for the live view.
    internal sealed class AgentCall
    {
        public string Kind = string.Empty;
        public string Title = string.Empty;
        public string Model = string.Empty;
        public string Note = string.Empty;
        public float LatencyMs = -1, Cost = -1;
        public uint Time;
        public readonly List<AgentCallQuestion> Questions = new List<AgentCallQuestion>();

        public static List<AgentCallQuestion> ParseQuestions(JsonElement arr)
        {
            var list = new List<AgentCallQuestion>();

            if (arr.ValueKind == JsonValueKind.Array)
            {
                foreach (JsonElement q in arr.EnumerateArray())
                {
                    list.Add(AgentCallQuestion.FromJson(q));
                }
            }

            return list;
        }

        public static AgentCall FromJson(JsonElement p)
        {
            var c = new AgentCall();

            if (p.ValueKind != JsonValueKind.Object)
            {
                return c;
            }

            foreach (JsonProperty prop in p.EnumerateObject())
            {
                JsonElement v = prop.Value;

                switch (prop.Name)
                {
                    case "kind" when v.ValueKind == JsonValueKind.String: c.Kind = v.GetString(); break;
                    case "title" when v.ValueKind == JsonValueKind.String: c.Title = v.GetString(); break;
                    case "model" when v.ValueKind == JsonValueKind.String: c.Model = v.GetString(); break;
                    case "note" when v.ValueKind == JsonValueKind.String: c.Note = v.GetString(); break;
                    case "latency_ms" when v.ValueKind == JsonValueKind.Number: c.LatencyMs = v.GetSingle(); break;
                    case "cost" when v.ValueKind == JsonValueKind.Number: c.Cost = v.GetSingle(); break;
                    case "questions": c.Questions.AddRange(ParseQuestions(v)); break;
                }
            }

            return c;
        }
    }
}
