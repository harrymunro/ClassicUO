// SPDX-License-Identifier: BSD-2-Clause

using System.Collections.Generic;
using System.Text.RegularExpressions;

namespace ClassicUO.Agent
{
    // Reading a runebook's gump (RunUO/ModernUO's RunebookGump). Its index page lists the 16
    // entries as cropped labels in two columns of eight, at x 145 and 305, from y 60 in steps of
    // 15; unused ones say "Empty". The gump is told apart by its first "use a charge" button.
    internal static class AgentRunebook
    {
        private static readonly Regex Command = new Regex(@"\{\s*([^}]*?)\s*\}", RegexOptions.Compiled);

        public static bool IsRunebook(string layout) =>
            layout != null && Regex.IsMatch(layout, @"\{\s*button\s+130\s+65\s+2103\s+2104\s+1\s+0\s+2\s*\}");

        public static List<string> Read(string layout, string[] lines)
        {
            var named = new SortedDictionary<int, string>();
            int page = 0;

            foreach (Match m in Command.Matches(layout))
            {
                string[] t = m.Groups[1].Value.Split(' ', System.StringSplitOptions.RemoveEmptyEntries);

                if (t.Length == 2 && t[0] == "page" && int.TryParse(t[1], out int pg))
                {
                    page = pg;

                    continue;
                }

                if (page != 1 || t.Length < 7 || t[0] != "croppedtext" || !int.TryParse(t[1], out int x) || !int.TryParse(t[2], out int y)
                    || !int.TryParse(t[6], out int text) || text < 0 || text >= lines.Length || (x != 145 && x != 305) || (y - 60) % 15 != 0)
                {
                    continue;
                }

                int index = (x == 305 ? 8 : 0) + (y - 60) / 15;

                if (index >= 0 && index < 16 && lines[text] != "Empty")
                {
                    named[index] = lines[text];
                }
            }

            // Entries fill from the start, so the list ends at the first gap.
            var entries = new List<string>();

            for (int i = 0; named.TryGetValue(i, out string name); i++)
            {
                entries.Add(name);
            }

            return entries;
        }
    }
}
