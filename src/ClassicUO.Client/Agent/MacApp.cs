// SPDX-License-Identifier: BSD-2-Clause

using System;
using System.Runtime.InteropServices;
using ClassicUO.Utility.Logging;

namespace ClassicUO.Agent
{
    // The few AppKit calls a headless client needs on macOS (cuo-1k7), through the Objective-C
    // runtime with blittable arguments only (NativeAOT). A headless client starts as a background
    // app (SDL_HINT_MAC_BACKGROUND_APP): no Dock icon and it never takes the focus. Showing its
    // window makes it a regular app again, so the window can come to the front; hiding it makes it
    // an accessory app (no Dock icon). Main thread only, like the rest of the game loop.
    internal static unsafe class MacApp
    {
        private const string OBJC = "/usr/lib/libobjc.dylib";

        private const long POLICY_REGULAR = 0;
        private const long POLICY_ACCESSORY = 1;

        [DllImport(OBJC)]
        private static extern IntPtr objc_getClass(byte* name);

        [DllImport(OBJC)]
        private static extern IntPtr sel_registerName(byte* name);

        [DllImport(OBJC, EntryPoint = "objc_msgSend")]
        private static extern IntPtr Send(IntPtr receiver, IntPtr selector);

        [DllImport(OBJC, EntryPoint = "objc_msgSend")]
        private static extern byte SendLong(IntPtr receiver, IntPtr selector, long arg);

        [DllImport(OBJC, EntryPoint = "objc_msgSend")]
        private static extern void SendByte(IntPtr receiver, IntPtr selector, byte arg);

        // Shown: a regular app (Dock icon), brought to the front. Hidden: an accessory app.
        public static void SetWindowShown(bool shown)
        {
            if (!OperatingSystem.IsMacOS())
            {
                return;
            }

            try
            {
                fixed (byte* cls = "NSApplication\0"u8)
                fixed (byte* shared = "sharedApplication\0"u8)
                fixed (byte* policy = "setActivationPolicy:\0"u8)
                fixed (byte* activate = "activateIgnoringOtherApps:\0"u8)
                {
                    IntPtr app = Send(objc_getClass(cls), sel_registerName(shared));

                    if (app == IntPtr.Zero)
                    {
                        return;
                    }

                    SendLong(app, sel_registerName(policy), shown ? POLICY_REGULAR : POLICY_ACCESSORY);

                    if (shown)
                    {
                        SendByte(app, sel_registerName(activate), 1);
                    }
                }
            }
            catch (Exception ex)
            {
                Log.Warn($"[agent] could not change the app's activation policy: {ex.Message}");
            }
        }
    }
}
