// SPDX-License-Identifier: BSD-2-Clause

using System.Collections.Generic;
using ClassicUO.Utility;

namespace ClassicUO.Agent
{
    // The character's long goals (brain/longgoals.py) as the panel shows them. The brain keeps
    // them, with their progress, in the shard's store and sends them over the long_goals RPC;
    // what the player asks here (add one, mark one done, drop one) waits in a queue until the
    // brain's next call picks it up and acknowledges it. With no brain running the requests wait.
    internal sealed partial class AgentController
    {
        public sealed class LongGoal
        {
            public int Id;
            public string Text = string.Empty;
            public string Progress = string.Empty;
        }

        public sealed class LongGoalRequest
        {
            public int Seq;
            public string Action = string.Empty; // add, done, drop
            public int Id;
            public string Text = string.Empty;
        }

        public const int MAX_LONG_GOAL_LENGTH = 200;

        private readonly List<LongGoal> _longGoals = new List<LongGoal>();
        private readonly List<LongGoalRequest> _longGoalRequests = new List<LongGoalRequest>();
        private int _longGoalSeq;

        public IReadOnlyList<LongGoal> LongGoals => _longGoals;
        public IReadOnlyList<LongGoalRequest> LongGoalRequests => _longGoalRequests;
        public uint LongGoalsTime { get; private set; }

        // From the brain: the active goals and their progress. The brain sends them every few
        // seconds; the panel only redraws when something in them changed.
        public void SetLongGoals(List<LongGoal> goals)
        {
            bool same = goals.Count == _longGoals.Count;

            for (int i = 0; same && i < goals.Count; i++)
            {
                same = goals[i].Id == _longGoals[i].Id && goals[i].Text == _longGoals[i].Text && goals[i].Progress == _longGoals[i].Progress;
            }

            if (same)
            {
                return;
            }

            _longGoals.Clear();
            _longGoals.AddRange(goals);
            LongGoalsTime = Time.Ticks;
        }

        // Requests up to and including seq have been applied by the brain.
        public void AckLongGoalRequests(int seq)
        {
            _longGoalRequests.RemoveAll(r => r.Seq <= seq);
        }

        public void RequestLongGoal(string action, int id, string text)
        {
            text = (text ?? string.Empty).Trim();

            if (text.Length > MAX_LONG_GOAL_LENGTH)
            {
                text = text.Substring(0, MAX_LONG_GOAL_LENGTH);
            }

            _longGoalRequests.Add(new LongGoalRequest { Seq = ++_longGoalSeq, Action = action, Id = id, Text = text });
        }

        // -agent longgoal [list] | add <text> | done <id> | drop <id>
        private void LongGoalCommand(string[] args)
        {
            string verb = args.Length > 2 ? args[2].ToLowerInvariant() : "list";
            string rest = args.Length > 3 ? string.Join(" ", args, 3, args.Length - 3).Trim() : string.Empty;

            switch (verb)
            {
                case "add" when rest.Length != 0:
                    RequestLongGoal("add", 0, rest);
                    Print($"long goal: {rest}{(AgentHost.BrainConnected ? string.Empty : " (kept for when the brain runs)")}");

                    return;
                case "done":
                case "drop":
                    if (!int.TryParse(rest, out int goalId))
                    {
                        Print($"usage: -agent longgoal {verb} <id> (the ids are in -agent longgoal list)");

                        return;
                    }

                    RequestLongGoal(verb, goalId, string.Empty);
                    Print($"long goal {goalId}: {verb}");

                    return;
                case "list":
                    if (_longGoals.Count == 0)
                    {
                        Print(AgentHost.BrainConnected ? "long goals: none" : "long goals: the brain sends them when it runs");
                    }

                    foreach (LongGoal g in _longGoals)
                    {
                        Print($"{g.Id}. {g.Text}: {g.Progress}");
                    }

                    foreach (LongGoalRequest r in _longGoalRequests)
                    {
                        Print($"waiting for the brain: {r.Action} {(r.Action == "add" ? r.Text : r.Id.ToString())}");
                    }

                    return;
                default:
                    Print("usage: -agent longgoal [list] | add <text> | done <id> | drop <id>");

                    return;
            }
        }
    }
}
