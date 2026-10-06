// SPDX-License-Identifier: BSD-2-Clause

using System;
using System.Collections.Concurrent;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using ClassicUO.Utility.Logging;

namespace ClassicUO.Agent
{
    // Newline-delimited JSON over loopback TCP. Several clients may be connected
    // (the brain plus one-off CLI calls); each reply goes back to the connection
    // that asked. Socket I/O stays on background threads: requests are queued on
    // Inbox and only handled on the game thread, and each connection has its own
    // writer so a slow reader can never stall a frame.
    internal sealed class AgentServer
    {
        private readonly TcpListener _listener;
        private int _clients;

        public AgentServer(ushort port)
        {
            _listener = new TcpListener(IPAddress.Loopback, port);
        }

        public ConcurrentQueue<(AgentConnection Connection, string Line)> Inbox { get; } = new ConcurrentQueue<(AgentConnection, string)>();

        public int Port { get; private set; }

        public bool HasClient => Volatile.Read(ref _clients) > 0;

        public void Start()
        {
            _listener.Start();
            Port = ((IPEndPoint) _listener.LocalEndpoint).Port;
            Log.Info($"[agent] listening on 127.0.0.1:{Port}");

            new Thread(AcceptLoop) { IsBackground = true, Name = "agent-accept" }.Start();
        }

        private void AcceptLoop()
        {
            while (true)
            {
                TcpClient client;

                try
                {
                    client = _listener.AcceptTcpClient();
                }
                catch (Exception ex)
                {
                    Log.Error($"[agent] accept failed: {ex.Message}");

                    return;
                }

                client.NoDelay = true;
                var conn = new AgentConnection(client);
                Interlocked.Increment(ref _clients);

                new Thread(() => ReadLoop(conn)) { IsBackground = true, Name = "agent-read" }.Start();
                new Thread(conn.WriteLoop) { IsBackground = true, Name = "agent-write" }.Start();
            }
        }

        private void ReadLoop(AgentConnection conn)
        {
            try
            {
                using var reader = new StreamReader(conn.Client.GetStream(), Encoding.UTF8);

                string line;

                while ((line = reader.ReadLine()) != null)
                {
                    if (line.Length != 0)
                    {
                        Inbox.Enqueue((conn, line));
                    }
                }
            }
            catch (Exception ex) when (ex is IOException || ex is ObjectDisposedException || ex is InvalidOperationException)
            {
            }

            conn.Close();
            Interlocked.Decrement(ref _clients);
        }
    }

    internal sealed class AgentConnection
    {
        private readonly BlockingCollection<byte[]> _outbox = new BlockingCollection<byte[]>();

        public AgentConnection(TcpClient client)
        {
            Client = client;
        }

        public TcpClient Client { get; }

        public void Send(byte[] line)
        {
            try
            {
                _outbox.Add(line);
            }
            catch (InvalidOperationException)
            {
                // closed while the reply was being built
            }
        }

        public void Close()
        {
            _outbox.CompleteAdding();
            Client.Dispose();
        }

        public void WriteLoop()
        {
            try
            {
                foreach (byte[] line in _outbox.GetConsumingEnumerable())
                {
                    Client.GetStream().Write(line, 0, line.Length);
                }
            }
            catch (Exception ex) when (ex is IOException || ex is ObjectDisposedException || ex is InvalidOperationException)
            {
            }
        }
    }
}
