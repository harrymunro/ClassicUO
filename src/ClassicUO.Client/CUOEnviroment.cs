// SPDX-License-Identifier: BSD-2-Clause

using System;
using System.IO;
using System.Reflection;
using System.Threading;

namespace ClassicUO
{
    internal static class CUOEnviroment
    {
        public static Thread GameThread;
        public static float DPIScaleFactor = 1.0f;
        public static bool NoSound;
        public static string[] Args;
        public static string[] Plugins;
        public static bool Debug;
        public static bool IsHighDPI;
        public static uint CurrentRefreshRate;
        public static bool SkipLoginScreen;
        public static bool NoServerPing;
        // -headless [true|false] on the command line; null when not given (settings.json's "headless" decides).
        // Kept out of Settings so that one headless launch doesn't make the next one headless too.
        public static bool? HeadlessArg;
        // -agent_sight_rule on the command line; null when not given (settings.json's "agent_sight_rule"
        // decides). Kept out of Settings for the same reason: a launch against a RunUO shard from a folder
        // shared with the local server's clients must not leave them on its rule.
        public static string AgentSightRuleArg;

        public static readonly bool IsWindows = Environment.OSVersion.Platform == PlatformID.Win32NT || Environment.OSVersion.Platform == PlatformID.Win32Windows || Environment.OSVersion.Platform == PlatformID.Win32S || Environment.OSVersion.Platform == PlatformID.WinCE;
        public static readonly bool IsUnix = !IsWindows;

        public static readonly string Version = Assembly.GetExecutingAssembly()?.GetName()?.Version?.ToString() ?? "0.0.0.0";
        public static readonly string ExecutablePath =
#if NETFRAMEWORK
           AppContext.BaseDirectory; // Path.GetDirectoryName(Assembly.GetEntryAssembly()?.Location);
#else
            Environment.CurrentDirectory;
#endif
    }
}