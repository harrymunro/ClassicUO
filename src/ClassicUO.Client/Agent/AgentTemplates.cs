// SPDX-License-Identifier: BSD-2-Clause

using System;
using System.Collections.Generic;
using System.IO;
using ClassicUO.Utility.Logging;

namespace ClassicUO.Agent
{
    // A ready-made piece of strategy the player can pull into their character's own
    // ("relentless", "nuker"). Templates ship inside the client (Agent/Templates/*.md)
    // and players add their own as .md files in an AgentTemplates folder next to the
    // executable; a user file replaces a built-in one of the same name. Format:
    //
    //   # Title
    //   for: warrior | mage | any
    //   summary: one line for lists and tooltips
    //
    //   The strategy text, one instruction per line.
    internal sealed class AgentTemplate
    {
        public string Name = string.Empty;
        public string Title = string.Empty;
        public string For = "any";
        public string Summary = string.Empty;
        public string Text = string.Empty;
        public bool User;

        public bool Suits(string archetype) =>
            For == "any" || string.IsNullOrEmpty(archetype) || string.Equals(For, archetype, StringComparison.OrdinalIgnoreCase);
    }

    // Goal templates (Agent/Goals/*.md, and an AgentGoals folder next to the client) use the
    // same format: a goal for the planner in the player's words.
    internal static class AgentTemplates
    {
        private const string RESOURCE_PREFIX = "AgentTemplates.";

        public static string UserDirectory => Path.Combine(AppContext.BaseDirectory, "AgentTemplates");

        public static List<AgentTemplate> All() => All(RESOURCE_PREFIX, UserDirectory);

        public static List<AgentTemplate> Goals() => All("AgentGoals.", Path.Combine(AppContext.BaseDirectory, "AgentGoals"));

        public static AgentTemplate FindGoal(string name) => Find(name, Goals());

        // Built-in templates, then the user's; read fresh each time so new files show up.
        private static List<AgentTemplate> All(string prefix, string userDirectory)
        {
            var byName = new Dictionary<string, AgentTemplate>(StringComparer.OrdinalIgnoreCase);
            var assembly = typeof(AgentTemplates).Assembly;

            foreach (string resource in assembly.GetManifestResourceNames())
            {
                if (!resource.StartsWith(prefix, StringComparison.Ordinal))
                {
                    continue;
                }

                using Stream stream = assembly.GetManifestResourceStream(resource);
                using var reader = new StreamReader(stream);
                string name = Path.GetFileNameWithoutExtension(resource.Substring(prefix.Length));
                byName[name] = Parse(name, reader.ReadToEnd(), false);
            }

            try
            {
                if (Directory.Exists(userDirectory))
                {
                    foreach (string file in Directory.GetFiles(userDirectory, "*.md"))
                    {
                        string name = Path.GetFileNameWithoutExtension(file);
                        byName[name] = Parse(name, File.ReadAllText(file), true);
                    }
                }
            }
            catch (Exception ex)
            {
                Log.Warn($"[agent] could not read templates in {userDirectory}: {ex.Message}");
            }

            var list = new List<AgentTemplate>(byName.Values);
            list.Sort((a, b) => a.For == b.For ? string.CompareOrdinal(a.Name, b.Name) : a.For == "any" ? -1 : b.For == "any" ? 1 : string.CompareOrdinal(a.For, b.For));

            return list;
        }

        // By file name or title, ignoring case.
        public static AgentTemplate Find(string name) => Find(name, All());

        private static AgentTemplate Find(string name, List<AgentTemplate> all)
        {
            if (string.IsNullOrWhiteSpace(name))
            {
                return null;
            }

            name = name.Trim();

            foreach (AgentTemplate t in all)
            {
                if (string.Equals(t.Name, name, StringComparison.OrdinalIgnoreCase) || string.Equals(t.Title, name, StringComparison.OrdinalIgnoreCase))
                {
                    return t;
                }
            }

            return null;
        }

        public static AgentTemplate Parse(string name, string source, bool user)
        {
            var t = new AgentTemplate { Name = name.ToLowerInvariant(), Title = name, User = user };
            var text = new List<string>();
            bool header = true;

            foreach (string raw in source.Replace("\r", string.Empty).Split('\n'))
            {
                string line = raw.Trim();

                if (header)
                {
                    if (line.StartsWith("# "))
                    {
                        t.Title = line.Substring(2).Trim();

                        continue;
                    }

                    int colon = line.IndexOf(':');
                    string key = colon > 0 ? line.Substring(0, colon).Trim().ToLowerInvariant() : string.Empty;

                    if (key == "for" || key == "summary")
                    {
                        string value = line.Substring(colon + 1).Trim();

                        if (key == "for")
                        {
                            t.For = value.ToLowerInvariant();
                        }
                        else
                        {
                            t.Summary = value;
                        }

                        continue;
                    }

                    if (line.Length == 0)
                    {
                        continue;
                    }

                    header = false;
                }

                if (line.Length != 0)
                {
                    text.Add(line);
                }
            }

            t.Text = string.Join("\n", text);

            return t;
        }
    }
}
