// SPDX-License-Identifier: BSD-2-Clause

using System;
using System.Diagnostics;
using System.IO;
using ClassicUO.Configuration;
using ClassicUO.Utility.Logging;

namespace ClassicUO.Agent
{
    // The brain as a child process, so playing takes one window instead of two terminals.
    // When the agent is turned on and nothing is connected to the agent port, the client
    // starts `uv run uo-brain run` in the brain folder, restarts it if it exits (waiting a
    // little longer each time), and stops it when the agent is turned off or the client
    // closes. The brain stays Python; this only launches it. Output goes to
    // brain/logs/client-brain.log.
    //
    // The OpenRouter key lives in brain/.env, as set-openrouter-key.sh writes it. The panel's
    // "paste key" saves whatever is on the clipboard there, so the key is never typed into or
    // shown by the game.
    internal static class AgentBrain
    {
        private static Process _process;
        private static uint _nextStart;
        private static int _starts;
        private static string _brainDir;
        private static bool _looked;

        public static string State { get; private set; } = string.Empty;

        // The brain folder: settings.json's agent_brain_dir, else found by walking up from the
        // client (bin/osx-arm64 -> the repository's brain/).
        public static string BrainDir
        {
            get
            {
                if (_looked)
                {
                    return _brainDir;
                }

                _looked = true;
                string configured = Settings.GlobalSettings.AgentBrainDir;

                if (!string.IsNullOrWhiteSpace(configured))
                {
                    _brainDir = Directory.Exists(configured) ? configured : null;

                    return _brainDir;
                }

                for (DirectoryInfo d = new DirectoryInfo(AppContext.BaseDirectory); d != null; d = d.Parent)
                {
                    string candidate = Path.Combine(d.FullName, "brain");

                    if (File.Exists(Path.Combine(candidate, "pyproject.toml")))
                    {
                        _brainDir = candidate;

                        break;
                    }
                }

                return _brainDir;
            }
        }

        // uv, which apps started from the Finder don't find on PATH.
        private static string Uv()
        {
            string home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);

            foreach (string dir in new[] { Path.Combine(home, ".local", "bin"), Path.Combine(home, ".cargo", "bin"), "/opt/homebrew/bin", "/usr/local/bin" })
            {
                string uv = Path.Combine(dir, "uv");

                if (File.Exists(uv))
                {
                    return uv;
                }
            }

            foreach (string dir in (Environment.GetEnvironmentVariable("PATH") ?? string.Empty).Split(Path.PathSeparator))
            {
                string uv = Path.Combine(dir, "uv");

                if (dir.Length != 0 && File.Exists(uv))
                {
                    return uv;
                }
            }

            return null;
        }

        public static bool Running => _process != null && !_process.HasExited;

        // Called every frame from the agent's update.
        public static void Update(AgentController agent)
        {
            if (Settings.GlobalSettings.AgentPort == 0 || !Settings.GlobalSettings.AgentStartBrain)
            {
                return;
            }

            if (agent.Mode == AgentMode.Off)
            {
                Stop("off");

                return;
            }

            if (Running)
            {
                State = AgentHost.BrainConnected ? "running" : "starting";

                return;
            }

            if (_process != null)
            {
                State = $"exited ({_process.ExitCode}), restarting";
                _process.Dispose();
                _process = null;
                _nextStart = Time.Ticks + (uint) Math.Min(60_000, 5_000 * _starts);
            }

            // Someone else's brain (a terminal) is already connected: leave it be.
            if (AgentHost.BrainConnected || Time.Ticks < _nextStart)
            {
                return;
            }

            Start();
        }

        private static void Start()
        {
            string dir = BrainDir;
            string uv = Uv();

            if (dir == null)
            {
                State = "no brain folder (set agent_brain_dir)";
                _nextStart = Time.Ticks + 60_000;

                return;
            }

            if (uv == null)
            {
                State = "uv not found";
                _nextStart = Time.Ticks + 60_000;

                return;
            }

            _starts++;

            try
            {
                Directory.CreateDirectory(Path.Combine(dir, "logs"));
                string log = Path.Combine(dir, "logs", "client-brain.log");
                string decisions = Path.Combine("logs", $"auto-{DateTime.Now:yyyyMMdd}.jsonl");

                var psi = new ProcessStartInfo(uv)
                {
                    WorkingDirectory = dir,
                    UseShellExecute = false,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    CreateNoWindow = true
                };

                foreach (string arg in new[] { "run", "uo-brain", "--port", Settings.GlobalSettings.AgentPort.ToString(), "run", "--log", decisions })
                {
                    psi.ArgumentList.Add(arg);
                }

                var writer = new StreamWriter(log, append: true) { AutoFlush = true };
                writer.WriteLine($"--- {DateTime.Now:u} started by the client");
                _process = new Process { StartInfo = psi, EnableRaisingEvents = true };
                _process.OutputDataReceived += (_, e) => Write(writer, e.Data);
                _process.ErrorDataReceived += (_, e) => Write(writer, e.Data);
                _process.Exited += (_, _) => writer.Dispose();
                _process.Start();
                _process.BeginOutputReadLine();
                _process.BeginErrorReadLine();
                State = "starting";
                Log.Info($"[agent] started the brain in {dir}");
            }
            catch (Exception ex)
            {
                State = $"could not start: {ex.Message}";
                _process = null;
                _nextStart = Time.Ticks + 30_000;
            }
        }

        private static void Write(StreamWriter w, string line)
        {
            if (line == null)
            {
                return;
            }

            try
            {
                lock (w)
                {
                    w.WriteLine(line);
                }
            }
            catch (ObjectDisposedException)
            {
            }
        }

        public static void Stop(string why)
        {
            if (_process == null)
            {
                return;
            }

            try
            {
                if (!_process.HasExited)
                {
                    _process.Kill(true);
                }
            }
            catch (Exception ex)
            {
                Log.Warn($"[agent] could not stop the brain: {ex.Message}");
            }

            _process.Dispose();
            _process = null;
            _starts = 0;
            State = why == "off" ? string.Empty : $"stopped ({why})";
        }

        // ------------------------------------------------------------ the model key

        private static string EnvFile => BrainDir == null ? null : Path.Combine(BrainDir, ".env");

        public static bool HasKey
        {
            get
            {
                if (!string.IsNullOrEmpty(Environment.GetEnvironmentVariable("OPENROUTER_API_KEY")))
                {
                    return true;
                }

                try
                {
                    string env = EnvFile;

                    if (env == null || !File.Exists(env))
                    {
                        return false;
                    }

                    foreach (string line in File.ReadAllLines(env))
                    {
                        if (line.StartsWith("OPENROUTER_API_KEY=", StringComparison.Ordinal) && line.Length > "OPENROUTER_API_KEY=".Length + 8)
                        {
                            return true;
                        }
                    }
                }
                catch (IOException)
                {
                }

                return false;
            }
        }

        // brain/.env with the key replaced and every other line kept, readable only by the user.
        public static void WriteKey(string env, string key)
        {
            var lines = new System.Collections.Generic.List<string>();

            if (File.Exists(env))
            {
                foreach (string line in File.ReadAllLines(env))
                {
                    if (!line.StartsWith("OPENROUTER_API_KEY=", StringComparison.Ordinal))
                    {
                        lines.Add(line);
                    }
                }
            }

            lines.Add("OPENROUTER_API_KEY=" + key);
            File.WriteAllLines(env, lines);

            if (!OperatingSystem.IsWindows())
            {
                File.SetUnixFileMode(env, UnixFileMode.UserRead | UnixFileMode.UserWrite);
            }
        }

        // Saves the key to brain/.env (mode 600), replacing any earlier one, and restarts the
        // brain so it reads it. Returns what to tell the player; the key itself is never shown.
        public static string SaveKey(string key)
        {
            key = (key ?? string.Empty).Trim();

            if (key.Length < 20 || key.IndexOfAny(new[] { ' ', '\n', '\r', '\t' }) >= 0)
            {
                return "the clipboard doesn't hold a key: copy your OpenRouter key first";
            }

            string env = EnvFile;

            if (env == null)
            {
                return "no brain folder to save the key in";
            }

            try
            {
                WriteKey(env, key);
            }
            catch (Exception ex)
            {
                return $"could not save the key: {ex.Message}";
            }

            Stop("new key");
            _nextStart = 0;

            return key.StartsWith("sk-or-", StringComparison.Ordinal)
                ? "key saved to brain/.env"
                : "key saved to brain/.env (OpenRouter keys usually start with sk-or-)";
        }
    }
}
