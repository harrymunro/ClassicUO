// SPDX-License-Identifier: BSD-2-Clause

using ClassicUO.Game;
using ClassicUO.Game.Data;
using ClassicUO.Game.GameObjects;
using ClassicUO.Game.Managers;

namespace ClassicUO.Agent
{
    // Messages the agent has seen, numbered so the brain can ask for "everything
    // since N". ClassicUO's own JournalManager keeps no sender serial and no
    // sequence, so the agent keeps its own ring.
    internal sealed class AgentJournal
    {
        public const string AGENT_PREFIX = "[agent] ";
        private const int CAPACITY = 256;

        private readonly World _world;
        private readonly Entry[] _ring = new Entry[CAPACITY];

        public AgentJournal(World world)
        {
            _world = world;
            world.MessageManager.MessageReceived += OnMessageReceived;
        }

        public long LastSeq { get; private set; }

        public struct Entry
        {
            public long Seq;
            public uint Time;
            public string Name;
            public uint Serial;
            public string Text;
            public MessageType Type;
            public TextType TextType;
            public bool FromSelf;
            public bool Agent;
            public bool Relayed;
        }

        public void AddAgentEvent(string text)
        {
            Add(new Entry { Name = "agent", Text = text, Type = MessageType.System, TextType = TextType.CLIENT, Agent = true });
        }

        // A line of speech the server didn't send this character, passed on by its brain (cuo-cym.28): the party's
        // leader said it within earshot, and a wall between them kept it from this client (ModernUO sends speech
        // only to those in line of sight). It is read as the speech it is; the game's own journal doesn't show it.
        public long AddHeard(string name, uint serial, string text, MessageType type)
        {
            Add(new Entry { Name = name, Serial = serial, Text = text, Type = type, TextType = TextType.OBJECT, Relayed = true });

            return LastSeq;
        }

        // Entries newer than since, oldest first, at most max of the newest.
        public int CopySince(long since, Entry[] dest)
        {
            long first = System.Math.Max(since + 1, LastSeq - dest.Length + 1);
            first = System.Math.Max(first, LastSeq - CAPACITY + 1);
            int n = 0;

            for (long s = System.Math.Max(first, 1); s <= LastSeq; s++)
            {
                dest[n++] = _ring[s % CAPACITY];
            }

            return n;
        }

        private void OnMessageReceived(object sender, MessageEventArgs e)
        {
            if (string.IsNullOrEmpty(e.Text))
            {
                return;
            }

            bool agent = e.Text.StartsWith(AGENT_PREFIX);

            if (e.Type == MessageType.Label && e.Parent is Entity labelled)
            {
                _world.Agent?.OnLabel(labelled.Serial, e.Text);
            }

            Add
            (
                new Entry
                {
                    Name = e.Name ?? string.Empty,
                    Serial = e.Parent?.Serial ?? 0,
                    Text = agent ? e.Text.Substring(AGENT_PREFIX.Length) : e.Text,
                    Type = e.Type,
                    TextType = e.TextType,
                    FromSelf = e.Parent != null && e.Parent == _world.Player,
                    Agent = agent
                }
            );

            // Only server text and our own lines may move agent state; anyone nearby can say anything.
            // Party, guild and alliance chat comes with no speaker object, as server text does, and is
            // what another player typed.
            bool chat = e.Type == MessageType.Party || e.Type == MessageType.Guild || e.Type == MessageType.Alliance;

            if (!agent && !chat && (e.TextType == TextType.SYSTEM || e.Parent == null || e.Parent == _world.Player))
            {
                _world.Agent?.OnMessage(e.Text);
            }
        }

        private void Add(Entry entry)
        {
            entry.Seq = ++LastSeq;
            entry.Time = Time.Ticks;
            _ring[entry.Seq % CAPACITY] = entry;
        }
    }
}
