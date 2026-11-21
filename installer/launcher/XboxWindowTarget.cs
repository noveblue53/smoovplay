// Explicit App window selection for a hosted Xbox stream that the usual list could not identify.
// Capture the owner of the top-level HWND, even when ApplicationFrameHost hosts a child process.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Text;

namespace SmoovPlay
{
    sealed class XboxWindowTarget
    {
        readonly IntPtr hwnd;
        readonly uint pid;
        public string Label { get; private set; }
        public string Query
        {
            get
            {
                ulong id = IntPtr.Size == 4 ? unchecked((uint)hwnd.ToInt32()) : unchecked((ulong)hwnd.ToInt64());
                return string.Format(CultureInfo.InvariantCulture, "xbox-hwnd:{0}:{1}", id, pid);
            }
        }

        XboxWindowTarget(IntPtr hwnd, uint pid) { this.hwnd = hwnd; this.pid = pid; }

        public static XboxWindowTarget Foreground(out string why)
        {
            var root = GetAncestor(GetForegroundWindow(), 2); // GA_ROOT, without following the owner of a popup
            GetWindowThreadProcessId(root, out uint owner);
            var target = new XboxWindowTarget(root, owner);
            return target.IsCurrent(out why) ? target : null;
        }

        public bool IsCurrent(out string why)
        {
            why = "The streaming window closed or changed.";
            if (hwnd == IntPtr.Zero || pid == 0 || GetAncestor(hwnd, 2) != hwnd ||
                GetWindowThreadProcessId(hwnd, out uint owner) == 0 || owner != pid) return false;

            why = "Bring the Xbox Remote Play window to the front.";
            if (pid == GetCurrentProcessId() || hwnd == GetShellWindow() || hwnd == GetDesktopWindow()) return false;
            var name = new StringBuilder(256);
            GetClassName(hwnd, name, name.Capacity);
            if (ShellClasses.Contains(name.ToString())) return false;
            string exe = ProcessName(pid);
            if (Blocked(exe)) return false;
            if (string.Equals(exe, "ApplicationFrameHost", StringComparison.OrdinalIgnoreCase))
            {
                // Show the app inside a Store frame when Windows exposes it. The query still names the owner's PID.
                EnumChildWindows(hwnd, (child, arg) =>
                {
                    GetWindowThreadProcessId(child, out uint childPid);
                    if (childPid == 0 || childPid == pid) return true;
                    var cls = new StringBuilder(256);
                    GetClassName(child, cls, cls.Capacity);
                    if (cls.ToString() != "Windows.UI.Core.CoreWindow") return true;
                    string hosted = ProcessName(childPid);
                    if (string.IsNullOrEmpty(hosted)) return true;
                    exe = hosted;
                    return false;
                }, IntPtr.Zero);
                if (Blocked(exe)) return false;
            }
            why = "Restore the streaming window and bring it onto this desktop.";
            if (!IsWindowVisible(hwnd) || IsIconic(hwnd) ||
                (DwmGetWindowAttribute(hwnd, 14, out int cloaked, 4) == 0 && cloaked != 0)) return false;
            why = "Make the streaming window at least 320 × 180 pixels.";
            if (!GetClientRect(hwnd, out Rect rect) || rect.Right - rect.Left < 320 || rect.Bottom - rect.Top < 180)
                return false;

            var title = new StringBuilder(256);
            GetWindowText(hwnd, title, title.Capacity);
            string caption = title.ToString().Trim();
            if (caption.Length > 80) caption = caption.Substring(0, 79) + "…";
            string app = string.IsNullOrEmpty(exe) ? "PID " + pid : exe + ".exe";
            Label = string.IsNullOrEmpty(caption) ? app : caption + "  ·  " + app;
            why = null;
            return true;
        }

        static readonly HashSet<string> ShellClasses = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
            { "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd" };
        static readonly HashSet<string> ShellApps = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
            { "explorer", "TextInputHost", "ShellExperienceHost", "SearchHost", "StartMenuExperienceHost", "SystemSettings" };
        static bool Blocked(string exe)
        {
            return ShellApps.Contains(exe) || (exe != null && exe.StartsWith("SmoovPlay", StringComparison.OrdinalIgnoreCase));
        }

        static string ProcessName(uint id)
        {
            try { using (var p = Process.GetProcessById(unchecked((int)id))) return p.ProcessName; }
            catch (Exception) { return null; } // Windows can withhold a process name; the HWND/PID pair still identifies it
        }

        [StructLayout(LayoutKind.Sequential)] struct Rect { public int Left, Top, Right, Bottom; }
        delegate bool EnumWindowProc(IntPtr window, IntPtr arg);
        [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
        [DllImport("user32.dll")] static extern IntPtr GetAncestor(IntPtr hwnd, uint what);
        [DllImport("user32.dll")] static extern IntPtr GetDesktopWindow();
        [DllImport("user32.dll")] static extern IntPtr GetShellWindow();
        [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
        [DllImport("kernel32.dll")] static extern uint GetCurrentProcessId();
        [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr hwnd);
        [DllImport("user32.dll")] static extern bool IsIconic(IntPtr hwnd);
        [DllImport("user32.dll")] static extern bool GetClientRect(IntPtr hwnd, out Rect rect);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr hwnd, StringBuilder text, int n);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetClassName(IntPtr hwnd, StringBuilder text, int n);
        [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr hwnd, EnumWindowProc callback, IntPtr arg);
        [DllImport("dwmapi.dll")] static extern int DwmGetWindowAttribute(IntPtr hwnd, uint attribute, out int value, uint size);
    }
}
