using System;
using System.IO;
using System.Linq;
using ClassicUO.Agent;
using FluentAssertions;
using Microsoft.Xna.Framework;
using Xunit;

namespace ClassicUO.UnitTests.Agent
{
    // cuo-cym.32: the client films itself, a frame per slot of 1/fps of a second, so a clip keeps the wall clock's time.
    public class AgentRecorderTests
    {
        private sealed class Kept : MemoryStream
        {
            public byte[] Bytes;

            protected override void Dispose(bool disposing)
            {
                Bytes ??= ToArray();
                base.Dispose(disposing);
            }
        }

        private static Action<Color[]> Fill(byte shade) => frame => Array.Fill(frame, new Color(shade, shade, shade, (byte)255));

        // The shade of each frame written: every frame is 2x2 pixels of one grey, 16 bytes.
        private static byte[] Shades(byte[] bytes) => bytes.Where((_, i) => i % 16 == 0).ToArray();

        [Fact]
        public void Slots_the_game_missed_show_the_last_frame_and_the_stop_fills_the_time_up_to_it()
        {
            var kept = new Kept();
            var rec = new AgentRecorder(() => kept, fps: 10, width: 2, height: 2);
            rec.Offer(0, 2, 2, Fill(10));
            rec.Offer(0, 2, 2, Fill(99)); // the same slot: not read again
            rec.Offer(1, 2, 2, Fill(20));
            rec.Offer(4, 2, 2, Fill(40)); // slots 2 and 3 were missed
            rec.Stop(6);
            rec.Wait(5000).Should().BeTrue();

            Shades(kept.Bytes).Should().Equal(10, 20, 20, 20, 40, 40);
            rec.Captured.Should().Be(3);
            rec.Written.Should().Be(6);
            rec.Error.Should().BeNull();
        }

        [Fact]
        public void A_frame_of_another_size_is_not_read_and_the_last_one_stands_in()
        {
            var kept = new Kept();
            var rec = new AgentRecorder(() => kept, fps: 10, width: 2, height: 2);
            rec.Offer(0, 2, 2, Fill(10));
            rec.Offer(1, 3, 2, Fill(30)); // the window was resized
            rec.Offer(2, 2, 2, Fill(50));
            rec.Stop(3);
            rec.Wait(5000).Should().BeTrue();

            Shades(kept.Bytes).Should().Equal(10, 10, 50);
            rec.Skipped.Should().Be(1);
        }

        [Fact]
        public void With_every_buffer_waiting_on_the_writer_the_game_skips_the_frame_rather_than_wait()
        {
            var gate = new System.Threading.ManualResetEventSlim();
            var kept = new Kept();
            var rec = new AgentRecorder(() =>
            {
                gate.Wait();

                return kept;
            }, fps: 10, width: 2, height: 2);

            for (int slot = 0; slot < AgentRecorder.BUFFERS + 2; slot++)
            {
                rec.Offer(slot, 2, 2, Fill((byte)(slot + 1)));
            }

            rec.Captured.Should().Be(AgentRecorder.BUFFERS);
            rec.Dropped.Should().Be(2);
            gate.Set();
            rec.Stop(AgentRecorder.BUFFERS + 2);
            rec.Wait(5000).Should().BeTrue();

            Shades(kept.Bytes).Should().Equal(1, 2, 3, 4, 4, 4);
        }

        [Fact]
        public void A_writer_that_fails_ends_the_recording_and_says_why()
        {
            var rec = new AgentRecorder(() => throw new IOException("Broken pipe"), fps: 10, width: 2, height: 2);
            rec.Wait(5000).Should().BeTrue();

            rec.Error.Should().Be("Broken pipe");
            rec.Stopped.Should().BeTrue();
            rec.Offer(0, 2, 2, Fill(10));
            rec.Captured.Should().Be(0);
        }
    }
}
