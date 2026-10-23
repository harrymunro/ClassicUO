// SPDX-License-Identifier: BSD-2-Clause

using System;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using Microsoft.Xna.Framework;

namespace ClassicUO.Agent
{
    // Films the client from its own frames (cuo-cym.32, the "record" RPC). ScreenCaptureKit gets no frames while
    // the Mac's screen is locked, which is how unattended runs go; the back buffer is still drawn then. After each
    // frame is drawn, a frame is read once its slot (1/fps of a second) comes round and handed to a writer thread,
    // which writes raw RGBA to a file (a FIFO that ffmpeg reads, in tools/video/film.py). Slots the game thread
    // missed (a long frame, the window hidden, no free buffer) show the last frame again, so a clip's time is the
    // wall clock's and log times line up with it.
    internal sealed class AgentRecorder
    {
        // Frames read and not yet written: the game thread never waits on the writer, it skips the frame instead.
        public const int BUFFERS = 4;

        private readonly Stopwatch _clock = Stopwatch.StartNew();
        private readonly BlockingCollection<(Color[] Frame, long Slot)> _queue = new();
        private readonly ConcurrentBag<Color[]> _free = new();
        private readonly Func<Stream> _open;
        private readonly Thread _writer;
        private long _lastSlot = -1;
        private long _captureTicks;
        private long _written;
        private volatile bool _stopped, _done;
        private volatile string _error;

        public AgentRecorder(string path, int fps, int width, int height) : this(
            () => new FileStream(path, FileMode.OpenOrCreate, FileAccess.Write, FileShare.ReadWrite, 1 << 20), fps, width, height)
        {
            Path = path;
        }

        // A stream of our own (tests); the path form opens the file on the writer thread, as opening a FIFO waits for its reader.
        public AgentRecorder(Func<Stream> open, int fps, int width, int height)
        {
            _open = open;
            Fps = fps;
            Width = width;
            Height = height;
            StartedAt = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0;

            for (int i = 0; i < BUFFERS; i++)
            {
                _free.Add(new Color[width * height]);
            }

            _writer = new Thread(Write) { IsBackground = true, Name = "agent-recorder" };
            _writer.Start();
        }

        public string Path { get; }
        public int Fps { get; }
        public int Width { get; }
        public int Height { get; }
        public double StartedAt { get; }
        public int Captured { get; private set; }
        // Draws that found every buffer waiting on the writer: the slot is tried again at the next draw, and only
        // shows as a repeated frame if no draw of it gets one.
        public int Busy { get; private set; }
        public int Skipped { get; private set; }
        public long Written => Interlocked.Read(ref _written);
        public bool Stopped => _stopped;
        public bool Done => _done;
        public string Error => _error;
        public double CaptureMs => Captured == 0 ? 0 : _captureTicks * 1000.0 / Stopwatch.Frequency / Captured;

        public long SlotNow => (long)(_clock.Elapsed.TotalSeconds * Fps);

        // Called after a frame is drawn: reads the frame if its slot has come and a buffer is free.
        public void OnDraw(int width, int height, Action<Color[]> read) => Offer(SlotNow, width, height, read);

        public void Offer(long slot, int width, int height, Action<Color[]> read)
        {
            if (_stopped || slot <= _lastSlot)
            {
                return;
            }

            if (width != Width || height != Height)
            {
                // The window was resized: its frames no longer fit the clip, so the last one stands in.
                Skipped++;
                _lastSlot = slot;

                return;
            }

            if (!_free.TryTake(out Color[] frame))
            {
                Busy++;

                return;
            }

            long began = Stopwatch.GetTimestamp();
            read(frame);
            _captureTicks += Stopwatch.GetTimestamp() - began;
            Captured++;
            _lastSlot = slot;
            _queue.Add((frame, slot));
        }

        // Ends the clip at this slot: the last frame fills the time up to it.
        public void Stop() => Stop(SlotNow);

        public void Stop(long slot)
        {
            if (_stopped)
            {
                return;
            }

            _stopped = true;
            _queue.Add((null, slot));
            _queue.CompleteAdding();
        }

        public bool Wait(int ms) => _writer.Join(ms);

        private void Write()
        {
            Color[] last = null;
            long lastSlot = -1;

            try
            {
                using Stream stream = _open();

                foreach ((Color[] frame, long slot) in _queue.GetConsumingEnumerable())
                {
                    for (long s = lastSlot + 1; last != null && s < slot; s++)
                    {
                        Put(stream, last);
                    }

                    // The slots before the first frame read (none drawn yet) show it, so the clip starts at the start.
                    for (long s = 0; last == null && frame != null && s < slot; s++)
                    {
                        Put(stream, frame);
                    }

                    if (frame == null)
                    {
                        break;
                    }

                    Put(stream, frame);

                    if (last != null)
                    {
                        _free.Add(last);
                    }

                    last = frame;
                    lastSlot = slot;
                }

                stream.Flush();
            }
            catch (Exception ex)
            {
                // ffmpeg gone (a broken pipe) or the file can't be written: the recording ends here.
                _error = ex.Message;
                _stopped = true;
            }
            finally
            {
                _done = true;
            }
        }

        private void Put(Stream stream, Color[] frame)
        {
            stream.Write(MemoryMarshal.AsBytes(frame.AsSpan()));
            Interlocked.Increment(ref _written);
        }
    }
}
