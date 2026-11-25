// SmoovPlay launcher and installer: everything except the window (see Ui.cs, Ui.xaml) and the PC checks (Checks.cs).
//
// SmoovPlay-Setup.exe is this program with the app files embedded as "payload.zip". It checks the PC, unpacks the
// app into a per-user folder (no admin rights), downloads Python, PyTorch and TensorRT from their official sources,
// prepares the GPU once and adds shortcuts. SmoovPlay.exe (the same code without a payload) is the launcher: it
// starts the pipeline before chiaki-ng so the stream's codec header is never missed, shows status, guides the PS5
// setup, and copies diagnostics for bug reports. WPF on .NET Framework 4.8, which Windows 10 and 11 include.
//
// Command line:  --unattended --dir <folder> [--no-shortcuts] [--skip-prepare]   install without UI (exit code 0 = ok)
//                --update --dir <folder> [--wait <pid>]  (setup) update that install, after process <pid> (the
//                                                        launcher that started it, Updater.cs) has closed; then open it
//                --uninstall [--yes]                                               remove an installed copy
//                --diagnostics <file>                                              write what "Copy diagnostics" copies
//                --page <check|play|guide|settings|looks|help>                           open the launcher on that page
// Dev/testing:   SMOOVPLAY_PYTHON, SMOOVPLAY_APP, SMOOVPLAY_CHIAKI, SMOOVPLAY_CHIAKI_ARGS environment variables run
//                the launcher against a dev checkout (e.g. the repo's venv with scripts\frametap_fake.py as "chiaki");
//                SMOOVPLAY_RUN_ARGS adds arguments to run.py; SMOOVPLAY_TRACE=<file> traces; SMOOVPLAY_PREFS=<file>
//                keeps the launcher's own memory (the PS5 guide's ticks) somewhere else; SMOOVPLAY_SHOTS=<folder>
//                saves a PNG of the window on every page or state change; SMOOVPLAY_RECORD=<folder> records it as
//                video (Ui.StartRecorder); SMOOVPLAY_SETTINGS=<file> reads another settings.json.
//                SMOOVPLAY_SOURCES=all offers the sources that aren't ready yet (video files) on the Play page;
//                SMOOVPLAY_WINDOW=<app> overrides the app window picked there (framegen/window.py's --window),
//                SMOOVPLAY_CAPTURE=<name|index|video file> overrides the capture card picked there, and
//                SMOOVPLAY_SOURCE_FILE=<video file> feeds the video file source.
// Built by installer\build.py, which also generates BuildInfo.cs (version, Python download and its SHA-256).

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;
using Microsoft.Win32;

namespace SmoovPlay
{
    static class Program
    {
        [STAThread]
        static int Main(string[] args)
        {
            // Tooltips as before .NET Framework 4.8's WCAG 2.1 ones, which stay open while the mouse is between a
            // tooltip's owner and the tooltip (so it can get onto it). Ours open below the mouse and run to the right,
            // over the next buttons in a row (the Settings page's choices: AMD FSR 1, NVIDIA RTX VSR, Bilinear), so
            // moving along a row kept showing the first button's tooltip. First thing: WPF keeps the value it reads first.
            AppContext.SetSwitch("Switch.System.Windows.Controls.ToolTip.OptOutOfWCAG21ToolTipBehavior", true);
            ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12;
            var opts = Options.Parse(args);
            if (args.Length == 2 && args[0] == "--check-signature")  // testing the Visual C++ download check
                return SystemCheck.SignedByMicrosoft(Path.GetFullPath(args[1])) ? 0 : 1;
            if (opts.Uninstall)
                return Uninstaller.Run(opts.Yes);
            if (opts.Unattended)
                return UnattendedInstall(opts);
            if (opts.Diagnostics != null)
            {
                File.WriteAllText(opts.Diagnostics, Diagnostics.Collect(Paths.Detect(), null), Encoding.UTF8);
                return 0;
            }
            var app = new System.Windows.Application { ShutdownMode = System.Windows.ShutdownMode.OnMainWindowClose };
            app.DispatcherUnhandledException += (s, e) => { Crash(e.Exception); e.Handled = true; };
            AppDomain.CurrentDomain.UnhandledException += (s, e) => Crash(e.ExceptionObject as Exception);
            var ui = new Ui(opts);
            return app.Run(ui.Window);
        }

        public static string CrashLog { get { return Path.Combine(Path.GetTempPath(), "SmoovPlay-launcher-errors.log"); } }

        /// <summary>A launcher bug: keep the details in a log (diagnostics include it) and say so.</summary>
        static void Crash(Exception e)
        {
            try { File.AppendAllText(CrashLog, $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} SmoovPlay {BuildInfo.Version}\r\n{e}\r\n\r\n"); }
            catch (Exception) { }
            System.Windows.MessageBox.Show("Something went wrong in the SmoovPlay window:\n\n" + (e != null ? e.Message : "unknown error") +
                                           "\n\nThe details are in " + CrashLog + ".", "SmoovPlay",
                                           System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Error);
        }

        static readonly string TraceFile = Environment.GetEnvironmentVariable("SMOOVPLAY_TRACE");
        /// <summary>Developer tracing (SMOOVPLAY_TRACE=file): timestamped, with the thread id.</summary>
        public static void Trace(string msg)
        {
            if (string.IsNullOrEmpty(TraceFile)) return;
            lock (TraceFile)
                File.AppendAllText(TraceFile, $"{DateTime.Now:HH:mm:ss.fff} t{System.Threading.Thread.CurrentThread.ManagedThreadId} {msg}" + Environment.NewLine);
        }

        /// <summary>Lets process `pid` (the launcher an update came from) close first, for up to `seconds`.</summary>
        public static void WaitFor(int pid, int seconds)
        {
            if (pid <= 0) return;
            try { using (var pr = Process.GetProcessById(pid)) pr.WaitForExit(seconds * 1000); }
            catch (Exception) { }  // already gone
        }

        static int UnattendedInstall(Options opts)
        {
            WaitFor(opts.Wait, 30);
            Paths paths = opts.Dir != null ? Paths.ForInstall(opts.Dir) : Paths.Detect();
            Directory.CreateDirectory(paths.Install);
            using (var log = new StreamWriter(Path.Combine(paths.Install, "install.log"), false, Encoding.UTF8) { AutoFlush = true })
            {
                bool payload = Payload.Present;
                var inst = new Installer(paths, payload, payload && !opts.NoShortcuts, !opts.SkipPrepare, false, false);
                var gate = new object();
                inst.Log = line => { lock (gate) log.WriteLine(line); };
                double last = -1;
                inst.Progress = (f, s) =>
                {
                    lock (gate)
                        if (f - last >= 0.01 || f >= 1) { last = f; log.WriteLine($"[progress] {f * 100:0}% {s}"); }
                };
                foreach (var c in SystemCheck.RunAll(paths.Install, payload))
                    log.WriteLine($"[check] {c.Level}: {c.Title}: {c.Value}. {c.Detail}");
                try { inst.Run(); log.WriteLine("[done] ok"); return 0; }
                catch (Exception e) { log.WriteLine("[done] FAILED: " + Installer.Message(e)); return 1; }
            }
        }
    }

    class Options
    {
        public bool Uninstall, Unattended, NoShortcuts, SkipPrepare, Yes, Update;
        public string Dir, Diagnostics, Page;
        public int Wait;  // --wait: a process to let close first (the launcher an update came from)

        public static Options Parse(string[] args)
        {
            var o = new Options();
            for (int i = 0; i < args.Length; i++)
            {
                switch (args[i].ToLowerInvariant())
                {
                    case "--uninstall": o.Uninstall = true; break;
                    case "--unattended": o.Unattended = true; break;
                    case "--no-shortcuts": o.NoShortcuts = true; break;
                    case "--skip-prepare": o.SkipPrepare = true; break;
                    case "--yes": o.Yes = true; break;
                    case "--update": o.Update = true; break;
                    case "--wait": if (i + 1 < args.Length) int.TryParse(args[++i], out o.Wait); break;
                    case "--dir": if (i + 1 < args.Length) o.Dir = Path.GetFullPath(args[++i]); break;
                    case "--diagnostics": if (i + 1 < args.Length) o.Diagnostics = Path.GetFullPath(args[++i]); break;
                    case "--page": if (i + 1 < args.Length) o.Page = args[++i].ToLowerInvariant(); break;
                }
            }
            return o;
        }
    }

    /// <summary>Where everything lives. An install holds app\ (run.py, framegen), runtime\python\, chiaki\ and logs\.</summary>
    class Paths
    {
        public string Install, App, Runtime, Python, Chiaki, ChiakiArgs = "", RunArgs = "", Logs, Ready;
        public string Vfx;  // NVIDIA VFG, if the user sets it up (Vfg.cs); every Python process gets it as FRAMEGEN_VFX_PATH
        public bool Dev;

        public static Paths ForInstall(string dir)
        {
            var p = new Paths { Install = dir };
            p.App = Path.Combine(dir, "app");
            p.Runtime = Path.Combine(dir, "runtime");
            p.Python = Path.Combine(p.Runtime, "python", "python.exe");
            p.Chiaki = Path.Combine(dir, "chiaki", "chiaki.exe");
            p.Logs = Path.Combine(dir, "logs");
            p.Ready = Path.Combine(p.Runtime, "ready.txt");
            p.Vfx = Path.Combine(p.Runtime, "vfx");
            return p;
        }

        public static Paths Detect()
        {
            string py = Environment.GetEnvironmentVariable("SMOOVPLAY_PYTHON");
            if (string.IsNullOrEmpty(py))
                return ForInstall(ExeDir);
            var p = new Paths { Dev = true, Python = py };
            p.App = Environment.GetEnvironmentVariable("SMOOVPLAY_APP") ?? ExeDir;
            p.Install = p.App;
            p.Runtime = Path.Combine(Path.GetTempPath(), "SmoovPlay-dev");
            p.Chiaki = Environment.GetEnvironmentVariable("SMOOVPLAY_CHIAKI") ?? "";
            p.ChiakiArgs = Environment.GetEnvironmentVariable("SMOOVPLAY_CHIAKI_ARGS") ?? "";
            p.RunArgs = Environment.GetEnvironmentVariable("SMOOVPLAY_RUN_ARGS") ?? "";
            p.Logs = Path.Combine(p.Runtime, "logs");
            p.Ready = Path.Combine(p.Runtime, "ready.txt");
            p.Vfx = Environment.GetEnvironmentVariable("FRAMEGEN_VFX_PATH") ?? Path.Combine(p.Runtime, "vfx");
            return p;
        }

        public static string ExeDir { get { return Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location); } }
        public static string DefaultInstall
        {
            get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "SmoovPlay"); }
        }
        /// <summary>Where an earlier install is, from its Apps &amp; features entry (null if none).</summary>
        public static string ExistingInstall
        {
            get
            {
                try
                {
                    using (var k = Registry.CurrentUser.OpenSubKey(Uninstaller.Key))
                    {
                        var dir = k != null ? k.GetValue("InstallLocation") as string : null;
                        return dir != null && ForInstall(dir).IsInstall ? dir : null;
                    }
                }
                catch (Exception) { return null; }
            }
        }
        public static string SettingsDir
        {
            get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "framegen"); }
        }
        /// <summary>The pipeline's settings (SMOOVPLAY_SETTINGS overrides it, for tests and recordings).</summary>
        public static string SettingsFile
        {
            get { return Environment.GetEnvironmentVariable("SMOOVPLAY_SETTINGS") ?? Path.Combine(SettingsDir, "settings.json"); }
        }
        public bool RuntimeReady { get { return Dev ? File.Exists(Python) : File.Exists(Ready) && File.Exists(Python); } }
        public bool IsInstall { get { return File.Exists(Path.Combine(App, "run.py")); } }
    }

    static class Payload
    {
        public static bool Present
        {
            get { return Assembly.GetExecutingAssembly().GetManifestResourceNames().Contains("payload.zip"); }
        }
        public static Stream Open() { return Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip"); }
    }

    // ------------------------------------------------------------------------------------------------ install
    /// <summary>The install steps. Each can run again: Retry or Repair picks up where a run stopped.</summary>
    class Installer
    {
        public static readonly string[] StepNames =
            { "Unpack SmoovPlay", "Download Python", "Download PyTorch and TensorRT", "Optimise for your graphics card", "Finish up" };

        readonly Paths p;
        readonly bool extract, register, prepare, desktopShortcut, fresh;
        public Action<string> Log = s => { };
        public Action<double, string> Progress = (f, s) => { };
        public Action<int> StepStarted = i => { };
        public Action<double> Downloaded = bytes => { };  // bytes fetched so far by the package step, for speed and ETA
        volatile bool cancelled;
        volatile Process current;

        // Share of the progress bar per step, and what the package downloads add up to (torch, TensorRT, ...).
        const double WExtract = 0.04, WPython = 0.02, WPip = 0.02, WPackages = 0.77, WPrepare = 0.15;
        public const double ExpectedDownloadBytes = 5.0e9;

        /// <param name="fresh">Remove the downloaded runtime first, so everything is downloaded again.</param>
        public Installer(Paths p, bool extract, bool register, bool prepare, bool desktopShortcut, bool fresh)
        {
            this.p = p; this.extract = extract; this.register = register; this.prepare = prepare;
            this.desktopShortcut = desktopShortcut; this.fresh = fresh;
        }

        public static string Message(Exception e)
        {
            while (e is AggregateException && e.InnerException != null) e = e.InnerException;
            return e.Message;
        }

        public void Cancel()
        {
            cancelled = true;
            try { var c = current; if (c != null && !c.HasExited) c.Kill(); } catch (Exception) { }
        }

        public void Run()
        {
            Log($"SmoovPlay {BuildInfo.Version} setup, {DateTime.Now:yyyy-MM-dd HH:mm:ss}, into {p.Install}");
            if (p.Install.Any(c => c > 127))  // (diagnostics hide the user's name, which can be where they are)
                Log("Note: the install folder's path has letters outside English A-Z. If a step fails, a folder like C:\\SmoovPlay is worth a try.");
            Directory.CreateDirectory(p.Install);
            Directory.CreateDirectory(p.Runtime);
            if (File.Exists(p.Ready)) File.Delete(p.Ready);
            if (fresh)
            {
                Step("Removing the old runtime");
                string pyDir = Path.GetDirectoryName(p.Python);
                if (Directory.Exists(pyDir)) Directory.Delete(pyDir, true);
            }
            double at = 0;
            StepStarted(0);
            if (extract) ExtractPayload(at, WExtract);
            at += WExtract;
            StepStarted(1);
            InstallPython(at, WPython); at += WPython;
            InstallPip(at, WPip); at += WPip;
            StepStarted(2);
            InstallPackages(at, WPackages); at += WPackages;
            StepStarted(3);
            if (prepare) Prepare(at, WPrepare);
            StepStarted(4);
            if (register) Register();
            TryDelete(Path.Combine(p.Runtime, "downloads"));
            File.WriteAllText(p.Ready, $"SmoovPlay {BuildInfo.Version} runtime ready {DateTime.Now:yyyy-MM-dd HH:mm}\r\n");
            Log("== Done");
            StepStarted(5);
            Progress(1, "Done");
        }

        void Step(string s) { Log("== " + s); }
        void ThrowIfCancelled() { if (cancelled) throw new OperationCanceledException("Cancelled."); }

        void ExtractPayload(double at, double w)
        {
            Step("Unpacking SmoovPlay");
            string root = Path.GetFullPath(p.Install).TrimEnd('\\') + "\\";
            int files = 0;
            using (var z = new ZipArchive(Payload.Open(), ZipArchiveMode.Read))
            {
                long total = Math.Max(1, z.Entries.Sum(e => e.Length)), done = 0;
                foreach (var e in z.Entries)
                {
                    ThrowIfCancelled();
                    string dest = Path.GetFullPath(Path.Combine(p.Install, e.FullName));
                    if (!dest.StartsWith(root, StringComparison.OrdinalIgnoreCase))
                        continue;  // never write outside the install folder
                    if (e.FullName.EndsWith("/")) { Directory.CreateDirectory(dest); continue; }
                    Directory.CreateDirectory(Path.GetDirectoryName(dest));
                    try { e.ExtractToFile(dest, true); }
                    catch (IOException ex)
                    {
                        throw new Exception($"Couldn't write {e.FullName} ({ex.Message}). Close SmoovPlay and chiaki-ng if they're running, then press Retry.");
                    }
                    files++;
                    done += e.Length;
                    Progress(at + w * done / total, "Unpacking SmoovPlay");
                }
            }
            Log($"Unpacked {files} files");
        }

        void InstallPython(double at, double w)
        {
            if (File.Exists(p.Python)) { Log("Python already installed"); return; }
            Step("Downloading Python " + BuildInfo.PythonVersion);
            string dl = Path.Combine(p.Runtime, "downloads");
            Directory.CreateDirectory(dl);
            string zip = Path.Combine(dl, Path.GetFileName(BuildInfo.PythonUrl));
            Download(BuildInfo.PythonUrl, zip, f => Progress(at + w * 0.8 * f, "Downloading Python"));
            string sha = Sha256(zip);
            if (!sha.Equals(BuildInfo.PythonSha256, StringComparison.OrdinalIgnoreCase))
            {
                File.Delete(zip);
                throw new Exception($"The Python download isn't the expected file (SHA-256 {sha}). Press Retry.");
            }
            string dir = Path.GetDirectoryName(p.Python);
            if (Directory.Exists(dir)) Directory.Delete(dir, true);
            ZipFile.ExtractToDirectory(zip, dir);
            // The embeddable Python only sees what its ._pth file lists: add site-packages and the app folder.
            var pth = Directory.GetFiles(dir, "python3*._pth").FirstOrDefault();
            if (pth == null) throw new Exception("Unexpected Python package: it has no ._pth file.");
            string zipName = Path.GetFileNameWithoutExtension(pth) + ".zip";
            File.WriteAllText(pth, zipName + "\r\n.\r\nLib\\site-packages\r\n..\\..\\app\r\nimport site\r\n");
            File.Delete(zip);
            Progress(at + w, "Python ready");
        }

        void InstallPip(double at, double w)
        {
            string pyDir = Path.GetDirectoryName(p.Python);
            bool havePip = File.Exists(Path.Combine(pyDir, "Scripts", "pip.exe"));
            bool haveTools = Directory.Exists(Path.Combine(pyDir, "Lib", "site-packages", "setuptools")) &&
                             Directory.Exists(Path.Combine(pyDir, "Lib", "site-packages", "wheel"));
            if (havePip && haveTools) { Log("pip already installed"); return; }
            Step("Installing pip");
            // setuptools and wheel too: tensorrt-cu12 is a source package, and pip's isolated build environment
            // doesn't work in the embeddable Python (its ._pth file hides the environment), so packages are built
            // against these instead (--no-build-isolation below).
            string tools = "\"setuptools>=70,<82\" \"wheel>=0.40\" --no-warn-script-location" + ProxyArg;  // torch wants setuptools<82
            int rc;
            if (havePip)
                rc = RunPython("-m pip install " + tools, p.Runtime, Log);
            else
            {
                string getpip = Path.Combine(p.Runtime, "downloads", "get-pip.py");
                Directory.CreateDirectory(Path.GetDirectoryName(getpip));
                Download("https://bootstrap.pypa.io/get-pip.py", getpip, f => Progress(at + w * 0.5 * f, "Installing pip"));
                rc = RunPython("\"" + getpip + "\" " + tools, p.Runtime, Log);
            }
            if (rc != 0) throw new Exception("Installing pip failed (exit code " + rc + "). See the log, then press Retry.");
            Progress(at + w, "pip ready");
        }

        static string ProxyArg { get { return SystemCheck.Proxy != null ? " --proxy \"" + SystemCheck.Proxy + "\"" : ""; } }

        void InstallPackages(double at, double w)
        {
            Step("Downloading PyTorch, TensorRT and the other packages (about 5 GB)");
            string req = Path.Combine(p.App, "requirements-runtime.txt");
            string cache = Path.Combine(p.Runtime, "pip-cache");  // kept until success, so Retry doesn't download it all again
            double doneBytes = 0, curTotal = 0, cur = 0;
            string pkg = "packages";
            var progress = new Regex(@"^Progress (\d+) of (\d+)");
            var collecting = new Regex(@"^Collecting (\S+)");
            Action<string> onLine = line =>
            {
                var m = progress.Match(line);
                if (m.Success)
                {
                    double c = double.Parse(m.Groups[1].Value), t = double.Parse(m.Groups[2].Value);
                    if (t != curTotal || c < cur) { doneBytes += cur; curTotal = t; }
                    cur = c;
                    Downloaded(doneBytes + c);
                    double f = Math.Min(0.92, (doneBytes + c) / ExpectedDownloadBytes);
                    Progress(at + w * f, $"Downloading {pkg}  ({c / 1e9:0.00} of {t / 1e9:0.00} GB)");
                    return;
                }
                Log(line);
                var mc = collecting.Match(line);
                if (mc.Success) pkg = Regex.Replace(mc.Groups[1].Value, @"[=<>!~;\[].*", "");
                if (line.StartsWith("Installing collected packages"))
                    Progress(at + w * 0.94, "Installing the packages (a few minutes)");
            };
            string args = "-m pip install --no-warn-script-location --disable-pip-version-check --progress-bar raw --no-build-isolation " +
                          "--cache-dir \"" + cache + "\" -r \"" + req + "\" --extra-index-url " + BuildInfo.TorchIndex +
                          " --extra-index-url " + BuildInfo.NvidiaIndex + ProxyArg;  // TensorRT's wheels are on NVIDIA's index
            int rc = RunPython(args, p.App, onLine);
            if (rc != 0) throw new Exception("Installing the packages failed (exit code " + rc + "). Check the internet connection, then press Retry.");
            TryDelete(cache);
            Progress(at + w, "Packages installed");
        }

        void Prepare(double at, double w)
        {
            Step("Optimising for your graphics card (once, a few minutes)");
            Progress(at, "Optimising for your graphics card");
            var stepRe = new Regex(@"step (\d+) of (\d+)");
            string lastError = null;
            int rc = RunPython("-m framegen.prepare", p.App, line =>
            {
                Log(line);
                if (line.Contains("DLL load failed") || line.Contains("ImportError")) lastError = line;
                var m = stepRe.Match(line);
                if (m.Success && line.Contains("optimising"))
                {
                    double i = double.Parse(m.Groups[1].Value), n = double.Parse(m.Groups[2].Value);
                    Progress(at + w * (i - 1) / n, $"Optimising for your graphics card, step {i} of {n} (a minute or two each)");
                }
            });
            if (rc == 2) throw new Exception("PyTorch can't use the NVIDIA card. Update the NVIDIA driver, then press Retry.");
            if (rc != 0 && lastError != null)
                throw new Exception("PyTorch couldn't load (" + lastError.Trim() + "). This usually means the Microsoft Visual C++ runtime " +
                                    "is missing or old: install it from the PC check, then press Retry.");
            if (rc != 0) throw new Exception("Preparing the graphics card failed (exit code " + rc + "). See the log, then press Retry.");
            Progress(at + w, "Graphics card ready");
        }

        void Register()
        {
            Step("Adding shortcuts");
            string exe = Path.Combine(p.Install, "SmoovPlay.exe");
            Shortcuts.Create(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "SmoovPlay.lnk"), exe, p.Install);
            if (desktopShortcut)
                Shortcuts.Create(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "SmoovPlay.lnk"), exe, p.Install);
            using (var k = Registry.CurrentUser.CreateSubKey(Uninstaller.Key))
            {
                k.SetValue("DisplayName", "SmoovPlay");
                k.SetValue("DisplayVersion", BuildInfo.Version);
                k.SetValue("Publisher", "the SmoovPlay authors");
                k.SetValue("InstallLocation", p.Install);
                k.SetValue("DisplayIcon", exe);
                k.SetValue("UninstallString", "\"" + exe + "\" --uninstall");
                k.SetValue("NoModify", 1, RegistryValueKind.DWord);
                k.SetValue("NoRepair", 1, RegistryValueKind.DWord);
                k.SetValue("EstimatedSize", (int)(DirSize(p.Install) / 1024), RegistryValueKind.DWord);
            }
        }

        int RunPython(string args, string cwd, Action<string> onLine)
        {
            ThrowIfCancelled();
            // Temporary files (pip unpacks multi-GB wheels) go on the install drive, not C:\...\Temp.
            string tmp = Path.Combine(p.Runtime, "tmp");
            Directory.CreateDirectory(tmp);
            var psi = Proc.Python(p.Python, args, cwd);
            psi.EnvironmentVariables["TMP"] = tmp;
            psi.EnvironmentVariables["TEMP"] = tmp;
            try { return Proc.Run(psi, onLine, pr => current = pr); }
            finally
            {
                current = null;
                TryDelete(tmp);
                ThrowIfCancelled();
            }
        }

        void Download(string url, string dest, Action<double> onProgress)
        {
            Log("Downloading " + url);
            using (var http = new HttpClient { Timeout = TimeSpan.FromMinutes(30) })
            {
                http.DefaultRequestHeaders.UserAgent.ParseAdd("SmoovPlay-installer/" + BuildInfo.Version);
                using (var resp = http.GetAsync(url, HttpCompletionOption.ResponseHeadersRead).Result)
                {
                    resp.EnsureSuccessStatusCode();
                    long total = resp.Content.Headers.ContentLength ?? -1, got = 0;
                    using (var src = resp.Content.ReadAsStreamAsync().Result)
                    using (var dst = File.Create(dest + ".part"))
                    {
                        var buf = new byte[1 << 16];
                        int n;
                        while ((n = src.Read(buf, 0, buf.Length)) > 0)
                        {
                            ThrowIfCancelled();
                            dst.Write(buf, 0, n);
                            got += n;
                            if (total > 0) onProgress((double)got / total);
                        }
                    }
                }
            }
            if (File.Exists(dest)) File.Delete(dest);
            File.Move(dest + ".part", dest);
        }

        static string Sha256(string file)
        {
            using (var h = System.Security.Cryptography.SHA256.Create())
            using (var f = File.OpenRead(file))
                return BitConverter.ToString(h.ComputeHash(f)).Replace("-", "");
        }

        static void TryDelete(string dir) { try { if (Directory.Exists(dir)) Directory.Delete(dir, true); } catch (Exception) { } }

        public static long DirSize(string dir)
        {
            try { return new DirectoryInfo(dir).EnumerateFiles("*", SearchOption.AllDirectories).Sum(f => f.Length); }
            catch (Exception) { return 0; }
        }
    }

    static class Proc
    {
        /// <summary>Where NVIDIA VFG is (or would go): Paths.Vfx, set once the launcher knows its paths.</summary>
        public static string VfxPath;

        /// <summary>A Python process with UTF-8, unbuffered output. The embeddable Python ignores most PYTHON*
        /// variables (its ._pth file puts it in isolated mode), hence -u and -X utf8 on the command line.
        /// -s: never the user's own site-packages (%APPDATA%\Python\Python312\site-packages). Isolated mode doesn't
        /// leave it out, so with a regular Python 3.12 on the PC, pip counted that folder's packages as installed,
        /// uninstalled the user's copies when it wanted other versions, and the runtime came to need them.
        /// PYTHONNOUSERSITE, which the embeddable Python does read, keeps it out of the Pythons this one starts
        /// (pip's, and framegen's subprocesses) too.</summary>
        public static ProcessStartInfo Python(string python, string args, string cwd)
        {
            var psi = new ProcessStartInfo(python, "-u -X utf8 -s " + args)
            {
                WorkingDirectory = cwd, UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8,
            };
            psi.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";  // for a dev venv Python, which does read them
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            psi.EnvironmentVariables["PIP_DISABLE_PIP_VERSION_CHECK"] = "1";
            psi.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";
            if (!string.IsNullOrEmpty(VfxPath)) psi.EnvironmentVariables["FRAMEGEN_VFX_PATH"] = VfxPath;  // a plain variable: the embeddable Python passes it on
            return psi;
        }

        /// <summary>Runs to completion, feeding every output line (stdout and stderr) to onLine.</summary>
        /// <summary>Runs a process to the end, each output line to onLine. timeoutMs: past it the process is stopped and
        /// TimedOut (-2) comes back (a capture driver that never answers mustn't leave the Play page looking forever).</summary>
        public static int Run(ProcessStartInfo psi, Action<string> onLine, Action<Process> started, int timeoutMs = -1)
        {
            using (var pr = new Process { StartInfo = psi })
            {
                pr.OutputDataReceived += (s, e) => { if (e.Data != null) onLine(e.Data); };
                pr.ErrorDataReceived += (s, e) => { if (e.Data != null) onLine(e.Data); };
                pr.Start();
                if (started != null) started(pr);
                pr.BeginOutputReadLine();
                pr.BeginErrorReadLine();
                if (!pr.WaitForExit(timeoutMs))
                {
                    try { pr.Kill(); } catch (Exception) { }
                    return TimedOut;
                }
                pr.WaitForExit();  // the rest of the output
                return pr.ExitCode;
            }
        }

        public const int TimedOut = -2;

        /// <summary>A process's exit code in words: Windows' crash codes are named (a camera driver that crashes while
        /// devices are listed takes Python down with 0xC0000005).</summary>
        public static string Describe(int rc)
        {
            switch ((uint)rc)
            {
                case 0xC0000005: return "Python crashed (0xC0000005, access violation): usually a camera, capture or virtual-camera driver";
                case 0xC0000409: return "Python crashed (0xC0000409)";
                case 0xC0000135: return "a file Python needs is missing (0xC0000135): Repair SmoovPlay on the Help page";
                case 0xC000013A: return "it was stopped (0xC000013A)";
            }
            return rc == TimedOut ? "it didn't finish in time" : rc < 0 ? $"exit code 0x{(uint)rc:X8}" : "exit code " + rc;
        }

        public static void Open(string target)
        {
            try { Process.Start(new ProcessStartInfo(target) { UseShellExecute = true }); } catch (Exception) { }
        }
    }

    static class Shortcuts
    {
        public static void Create(string lnk, string target, string workDir)
        {
            var t = Type.GetTypeFromProgID("WScript.Shell");
            object shell = Activator.CreateInstance(t);
            object sc = t.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { lnk });
            var st = sc.GetType();
            st.InvokeMember("TargetPath", BindingFlags.SetProperty, null, sc, new object[] { target });
            st.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, sc, new object[] { workDir });
            st.InvokeMember("Description", BindingFlags.SetProperty, null, sc, new object[] { "SmoovPlay: frame generation for 30 fps console games" });
            st.InvokeMember("IconLocation", BindingFlags.SetProperty, null, sc, new object[] { target + ",0" });
            st.InvokeMember("Save", BindingFlags.InvokeMethod, null, sc, null);
        }
    }

    static class Uninstaller
    {
        public const string Key = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\SmoovPlay";

        /// <param name="confirmed">The launcher already asked (its Help page).</param>
        public static int Run(bool confirmed)
        {
            string dir = Paths.ExeDir;
            if (!Paths.ForInstall(dir).IsInstall)
            {
                System.Windows.MessageBox.Show("This copy of SmoovPlay isn't an installed one, so there's nothing to remove.", "SmoovPlay");
                return 1;
            }
            if (!confirmed && System.Windows.MessageBox.Show("Remove SmoovPlay from\n" + dir + "?\n\nYour settings are kept.", "Uninstall SmoovPlay",
                                               System.Windows.MessageBoxButton.OKCancel, System.Windows.MessageBoxImage.Question)
                != System.Windows.MessageBoxResult.OK)
                return 1;
            foreach (var lnk in new[] {
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "SmoovPlay.lnk"),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "SmoovPlay.lnk") })
                try { if (File.Exists(lnk)) File.Delete(lnk); } catch (Exception) { }
            try { Registry.CurrentUser.DeleteSubKeyTree(Key, false); } catch (Exception) { }
            // Only what setup created is removed, then the folder itself if that leaves it empty. This exe is inside
            // it, so a short-lived cmd does the deleting once we've exited.
            var cmd = new StringBuilder("/c ping 127.0.0.1 -n 3 > nul");
            foreach (var sub in new[] { "app", "runtime", "chiaki", "logs" })
                cmd.Append($" & rmdir /s /q \"{Path.Combine(dir, sub)}\"");
            foreach (var f in new[] { "SmoovPlay.exe", "install.log", "LICENSE.txt", "THIRD_PARTY_NOTICES.txt" })
                cmd.Append($" & del /q \"{Path.Combine(dir, f)}\"");
            cmd.Append($" & rmdir \"{dir}\"");
            Process.Start(new ProcessStartInfo("cmd.exe", cmd.ToString())
            { CreateNoWindow = true, UseShellExecute = false, WorkingDirectory = Path.GetTempPath() });
            return 0;
        }
    }

    // ------------------------------------------------------------------------------------------------ play
    /// <summary>The chiaki-ng the launcher started. Its pid and start time are saved so a later launcher run can tell
    /// it from a chiaki-ng started some other way, which wouldn't have the frame tap switched on.</summary>
    static class Chiaki
    {
        static string Marker(Paths p) { return Path.Combine(p.Runtime, "chiaki.pid"); }
        static bool IsChiakiExe(Paths p) { return Path.GetFileNameWithoutExtension(p.Chiaki).Equals("chiaki", StringComparison.OrdinalIgnoreCase); }

        public static Process Ours(Paths p)
        {
            try
            {
                var parts = File.ReadAllText(Marker(p)).Split(' ');
                var pr = Process.GetProcessById(int.Parse(parts[0]));
                if (!pr.HasExited && pr.StartTime.Ticks.ToString() == parts[1].Trim()) return pr;
            }
            catch (Exception) { }
            return null;
        }

        /// <summary>chiaki-ng windows the launcher didn't start.</summary>
        public static Process[] Others(Paths p)
        {
            if (!IsChiakiExe(p)) return new Process[0];
            var ours = Ours(p);
            return Process.GetProcessesByName("chiaki").Where(x => ours == null || x.Id != ours.Id).ToArray();
        }

        public static bool Start(Paths p, Action<string> log)
        {
            if (string.IsNullOrEmpty(p.Chiaki) || !File.Exists(p.Chiaki))
            {
                log("[launcher] chiaki-ng isn't at " + p.Chiaki + "; start it yourself with CHIAKI_FRAMETAP=1");
                return false;
            }
            var psi = new ProcessStartInfo(p.Chiaki, p.ChiakiArgs)
            {
                UseShellExecute = false, CreateNoWindow = true,  // no console window; chiaki-ng's own windows still open
                WorkingDirectory = p.Dev ? p.App : Path.GetDirectoryName(p.Chiaki),
            };
            psi.EnvironmentVariables["CHIAKI_FRAMETAP"] = "1";
            string renderer = Renderer(psi.EnvironmentVariables["CHIAKI_FRAMETAP_RENDERER"]);
            if (renderer != null) psi.EnvironmentVariables["CHIAKI_FRAMETAP_RENDERER"] = renderer;
            var pr = Process.Start(psi);
            try
            {
                Directory.CreateDirectory(p.Runtime);
                File.WriteAllText(Marker(p), pr.Id + " " + pr.StartTime.Ticks);
            }
            catch (Exception) { }
            log("[launcher] started chiaki-ng" + (renderer == "opengl"
                ? " (drawing with OpenGL this time: chiaki-ng's Vulkan has crashed on AMD and Intel graphics; SmoovPlay shows the picture)"
                : ""));
            return true;
        }

        /// <summary>What chiaki-ng draws its own window with (CHIAKI_FRAMETAP_RENDERER, our chiaki-ng's switch, for that
        /// session only): OpenGL without an NVIDIA card, where chiaki-ng's default, Vulkan (with Vulkan decoding), has
        /// crashed (issue 19, a Radeon RX 9070 XT). SmoovPlay draws the stream itself, so chiaki-ng's window, hidden
        /// behind it, loses nothing. On NVIDIA it stays as set: Vulkan works there, and an HDR stream needs it. One set
        /// already (e.g. "vulkan" to try it anyway) is kept.</summary>
        public static string Renderer(string already)
        {
            if (!string.IsNullOrEmpty(already)) return already;
            if (SystemCheck.GpuName == "unknown" && SystemCheck.OtherGpu == null) SystemCheck.QueryGpu();
            return SystemCheck.GpuName == "unknown" && SystemCheck.OtherGpu != null ? "opengl" : null;
        }

        public static void Close(Process[] procs)
        {
            foreach (var pr in procs)
                try
                {
                    pr.CloseMainWindow();
                    if (!pr.WaitForExit(5000)) pr.Kill();
                }
                catch (Exception) { }
        }
    }

    enum PlayState { Idle, Starting, Waiting, Found, Building, Playing, Ended, Stopped, Error }

    /// <summary>One video source, from the pipeline's list (python -m framegen.sources --json, framegen/sources.py).</summary>
    class SourceDef
    {
        public string Kind, Name, Guide;
        public bool Hidden, TestMode = true;
        public bool Experimental;  // offered, but built without the hardware to test it: a badge and Send feedback
        public bool RemotePlay { get { return Kind == "chiaki"; } }
    }

    /// <summary>The video sources. PS5 Remote Play is the one offered; capture cards and video files work in the
    /// pipeline but are hidden until they're ready (SMOOVPLAY_SOURCES=all shows them, for testing).</summary>
    static class Sources
    {
        public static readonly SourceDef RemotePlay = new SourceDef { Kind = "chiaki", Name = "PS5 Remote Play" };
        static List<SourceDef> all;
        static readonly object gate = new object();

        /// <summary>Asks the pipeline once (it answers in well under a second); just Remote Play if it can't.</summary>
        public static List<SourceDef> All(Paths p)
        {
            lock (gate)
            {
                if (all != null) return all;
                var list = new List<SourceDef>();
                if (p.RuntimeReady)
                {
                    try
                    {
                        var lines = new List<string>();
                        int rc = Proc.Run(Proc.Python(p.Python, "-m framegen.sources --json", p.App), l => { lock (lines) lines.Add(l); }, null);
                        string json = lines.LastOrDefault(l => l.StartsWith("{"));
                        if (rc == 0 && json != null) list = Parse(json);
                        else Program.Trace("source list: " + (lines.LastOrDefault(l => l.Trim().Length > 0) ?? "exit code " + rc));
                    }
                    catch (Exception e) { Program.Trace("source list: " + e.Message); }
                }
                if (!list.Any(s => s.RemotePlay)) list.Insert(0, RemotePlay);
                return all = list;
            }
        }

        public static List<SourceDef> Parse(string json)
        {
            var root = (Dictionary<string, object>)new JavaScriptSerializer().DeserializeObject(json);
            var list = new List<SourceDef>();
            foreach (Dictionary<string, object> d in (object[])root["sources"])
                list.Add(new SourceDef
                {
                    Kind = (string)d["kind"], Name = (string)d["name"], Guide = d.ContainsKey("guide") ? d["guide"] as string : null,
                    Hidden = d.ContainsKey("hidden") && (bool)d["hidden"], TestMode = !d.ContainsKey("test_mode") || (bool)d["test_mode"],
                    Experimental = d.ContainsKey("experimental") && (bool)d["experimental"],
                });
            return list;
        }

        public static List<SourceDef> Offered(Paths p) { return All(p).Where(s => !s.Hidden).ToList(); }

        /// <summary>The source to play from: the one picked on the Play page if it's still offered, else Remote Play.</summary>
        public static SourceDef Chosen(Paths p, HashSet<string> prefs)
        {
            var offered = Offered(p);
            return offered.FirstOrDefault(s => prefs.Contains("source:" + s.Kind)) ?? offered.FirstOrDefault(s => s.RemotePlay) ?? RemotePlay;
        }

        public static void Choose(HashSet<string> prefs, string kind)
        {
            prefs.RemoveWhere(f => f.StartsWith("source:"));
            if (kind != "chiaki") prefs.Add("source:" + kind);  // Remote Play is the default, so it needs no flag
        }

        /// <summary>What run.py needs to play from a source. video, audio: the capture card and its sound as picked on
        /// the Play page (CaptureDevices; null: the first card, and the sound device named like it; audio "off": none).</summary>
        public static string RunArgs(string kind, string video = null, string audio = null)
        {
            if (kind == "capture")
                return "--source capture --capture " + Quote(Environment.GetEnvironmentVariable("SMOOVPLAY_CAPTURE") ?? video ?? "0")
                       + (audio != null ? " --capture-audio " + Quote(audio) : "");
            if (kind == "window")  // video: the app's window, as AppWindows.Query gives it
                return "--source window --window " + Quote(Environment.GetEnvironmentVariable("SMOOVPLAY_WINDOW") ?? video ?? "ps-remote-play");
            if (kind == "tap")  // SMOOVPLAY_TAP: another shared-memory name (docs/frame-tap.md)
                return "--source tap" + (Environment.GetEnvironmentVariable("SMOOVPLAY_TAP") != null
                                         ? " --tap " + Quote(Environment.GetEnvironmentVariable("SMOOVPLAY_TAP")) : "");
            if (kind == "file")
                return "--source file " + Quote(Environment.GetEnvironmentVariable("SMOOVPLAY_SOURCE_FILE") ?? "") + " --raw --realtime";
            return "--source chiaki";
        }

        static string Quote(string s) { return "\"" + s.Replace("\"", "") + "\""; }
    }

    /// <summary>The app windows that window capture could read (python -m framegen.window --list --json): the Play
    /// page's App choice when playing from another app's window. Read in the background when that source is picked, and
    /// again on Look again (windows come and go, so it isn't kept for long).</summary>
    static class AppWindows
    {
        public class Win { public long Hwnd; public string Title, Exe, App; public int W, H; public bool Known; }
        public static List<Win> List = new List<Win>();
        /// <summary>Streaming apps that are running but weren't listed, and why ("Xbox app" -> "minimised"), from the
        /// pipeline's left_out: only apps it knows are named, never other windows.</summary>
        public static Dictionary<string, string> Missed = new Dictionary<string, string>();
        public static string Error;  // why the last look failed, for diagnostics (null: it worked)
        public static bool Loaded;
        static readonly object gate = new object();

        public static void Load(Paths p)
        {
            lock (gate)
            {
                var lines = new List<string>();
                try
                {
                    int rc = Proc.Run(Proc.Python(p.Python, "-m framegen.window --list --json", p.App), l => { lock (lines) lines.Add(l); }, null, 30000);
                    string json = lines.LastOrDefault(l => l.StartsWith("{"));
                    var list = new List<Win>();
                    var missed = new Dictionary<string, string>();
                    if (rc == 0 && json != null)
                    {
                        var root = (Dictionary<string, object>)new JavaScriptSerializer().DeserializeObject(json);
                        foreach (Dictionary<string, object> d in (object[])root["windows"])
                            list.Add(new Win
                            {
                                Hwnd = Convert.ToInt64(d["hwnd"]), Title = (string)d["title"], Exe = (string)d["exe"], App = (string)d["app"],
                                W = Convert.ToInt32(d["w"]), H = Convert.ToInt32(d["h"]), Known = d.ContainsKey("known") && (bool)d["known"],
                            });
                        if (root.ContainsKey("left_out") && root["left_out"] is object[] left)
                            foreach (Dictionary<string, object> d in left)
                                missed[(string)d["app"]] = string.Join(", ", ((object[])d["why"]).Cast<string>());
                        Error = null;
                    }
                    else
                    {
                        Error = lines.LastOrDefault(l => l.Trim().Length > 0) ?? "exit code " + rc;
                        Program.Trace("app windows: " + Error);
                    }
                    List = list;
                    Missed = missed;
                }
                catch (Exception e) { Error = e.Message; Program.Trace("app windows: " + e.Message); }
                Loaded = true;
            }
        }

        /// <summary>The window to capture: the one picked last time (by its program) if it's open, else the first app it
        /// knows (PS Remote Play, the Xbox app, Moonlight, then browsers), else the first.</summary>
        public static Win Chosen(HashSet<string> prefs)
        {
            string hwnd = CaptureDevices.Pref(prefs, "window-hwnd:"), exe = CaptureDevices.Pref(prefs, "window-app:");
            return List.FirstOrDefault(w => w.Hwnd.ToString() == hwnd) ?? List.FirstOrDefault(w => w.Exe == exe)
                   ?? List.FirstOrDefault(w => w.Known) ?? List.FirstOrDefault();
        }

        /// <summary>For diagnostics: the streaming apps and browsers found, with sizes, and only a count of other windows
        /// (their titles and programs stay private).</summary>
        public static string Summary()
        {
            var known = List.Where(w => w.Known).Select(w => w.App + (w.W > 0 ? $" {w.W}×{w.H}" : " (minimised)")).ToList();
            int others = List.Count(w => !w.Known);
            var sb = new StringBuilder();
            sb.AppendLine("App windows: " + (known.Count == 0 ? "no streaming app or browser" : string.Join("; ", known))
                          + (others > 0 ? $" (and {others} other windows)" : ""));
            foreach (var m in Missed) sb.AppendLine($"App window left out: {m.Key} ({m.Value})");
            if (Error != null) sb.AppendLine("App window list failed: " + Error);
            return sb.ToString();
        }

        /// <summary>A line on a streaming app that's running but has no window to pick, or null.</summary>
        public static string MissedHint()
        {
            if (Missed.Count == 0) return null;
            var m = Missed.First();
            string fix = m.Value.Contains("full screen") ? "leave full screen (its full screen button, or Shift+Win+Enter)"
                       : m.Value.Contains("minimised") ? "restore its window"
                       : m.Value.Contains("another desktop") ? "bring its window to this desktop"
                       : m.Value.Contains("too small") ? "make its window bigger"
                       : "open its window (not just the tray icon) and start streaming";
            return $"The {m.Key} is running, but its window can't be used ({m.Value}). {Char.ToUpper(fix[0]) + fix.Substring(1)}, then press Look again.";
        }

        /// <summary>What run.py's --window gets: the window itself, or failing that (reopened since) its program.</summary>
        public static string Query(Win w) { return w == null ? null : "hwnd:" + w.Hwnd + "|exe:" + w.Exe; }

        public static string Label(Win w)
        {
            string title = w.Title.Length > 48 ? w.Title.Substring(0, 47) + "…" : w.Title;
            return w.App + (title.Length > 0 && title != w.App ? "  ·  " + title : "") + (w.W > 0 ? $"  ({w.W}×{w.H})" : "  (minimised)");
        }
    }

    /// <summary>The capture devices DirectShow lists (python -m framegen.capture --list --json): the Play page's Card and
    /// Sound choices when playing from a capture card. Read once, in the background (a second or so), and again on Refresh.</summary>
    static class CaptureDevices
    {
        public static List<string> Video = new List<string>(), Audio = new List<string>();
        public static string Error;  // why the last look failed, or what went wrong after the list came (null: all well)
        public static bool Failed;  // no list came at all (Error says why)
        public static bool Loaded;
        static readonly object gate = new object();
        // Names that are likely a capture card rather than a webcam (also the PC check's, in Checks.cs)
        public const string CardPattern = "capture|cam link|elgato|avermedia|hdmi|usb video|ms2130|live gamer|magewell|razer ripsaw";

        public static void Load(Paths p)
        {
            lock (gate)
            {
                var lines = new List<string>();
                try
                {
                    int rc = Proc.Run(Proc.Python(p.Python, "-m framegen.capture --list --json", p.App), l => { lock (lines) lines.Add(l); }, null, 30000);
                    string json = lines.LastOrDefault(l => l.StartsWith("{"));
                    if (json != null)  // the list came out, even if something went wrong after (the exit code says)
                    {
                        var root = (Dictionary<string, object>)new JavaScriptSerializer().DeserializeObject(json);
                        Video = Names(root, "video"); Audio = Names(root, "audio");
                        Error = root.ContainsKey("error") ? root["error"] as string : rc != 0 ? "after listing, " + Proc.Describe(rc) : null;
                        Failed = Video.Count == 0 && Audio.Count == 0 && root.ContainsKey("error");
                    }
                    else
                    {
                        string last = lines.LastOrDefault(l => l.Trim().Length > 0 && !l.StartsWith("Traceback"));
                        Error = rc == Proc.TimedOut ? "Windows' capture devices didn't answer in 30 s (a capture driver may be stuck: unplug the card and plug it back in)"
                              : Proc.Describe(rc) + (last != null ? ": " + last.Trim() : "");
                        Video = new List<string>(); Audio = new List<string>();
                        Failed = true;
                    }
                    if (Error != null) Program.Trace("capture devices: " + Error);
                }
                catch (Exception e) { Error = e.Message; Failed = true; Program.Trace("capture devices: " + e.Message); }
                Loaded = true;
            }
        }

        static List<string> Names(Dictionary<string, object> root, string key)
        {
            var list = new List<string>();
            if (root.ContainsKey(key) && root[key] is object[] items)
                foreach (Dictionary<string, object> d in items) list.Add((string)d["name"]);
            return list;
        }

        /// <summary>For diagnostics: every video device, the sound devices named like a card (other sound devices are only
        /// counted: a headset can carry its owner's name), and why the last look failed.</summary>
        public static string Summary()
        {
            var sb = new StringBuilder();
            sb.AppendLine("Capture devices (DirectShow): " + (Video.Count == 0 ? "no video device" : string.Join("; ", Video.Select(Diagnostics.NoOwner))));
            var cardAudio = Audio.Where(n => Regex.IsMatch(n, CardPattern, RegexOptions.IgnoreCase)).ToList();
            sb.AppendLine("Capture sound: " + (cardAudio.Count == 0 ? "none named like a card" : string.Join("; ", cardAudio.Select(Diagnostics.NoOwner)))
                          + (Audio.Count > cardAudio.Count ? $" (and {Audio.Count - cardAudio.Count} other sound devices)" : ""));
            if (Error != null) sb.AppendLine((Failed ? "Capture device list failed: " : "Capture device list: ") + Error);
            return sb.ToString();
        }

        /// <summary>The card to use: the one picked (if it's still there), else the first that looks like a card, else the first.</summary>
        public static string ChosenVideo(HashSet<string> prefs)
        {
            string picked = Pref(prefs, "capture-video:");
            if (picked != null && Video.Contains(picked)) return picked;
            return Video.FirstOrDefault(n => System.Text.RegularExpressions.Regex.IsMatch(n, CardPattern, System.Text.RegularExpressions.RegexOptions.IgnoreCase))
                   ?? Video.FirstOrDefault();
        }

        /// <summary>The sound: null for "auto" (the device named like the card), "off", or a device's name.</summary>
        public static string ChosenAudio(HashSet<string> prefs)
        {
            string picked = Pref(prefs, "capture-audio:");
            return picked == "off" || (picked != null && Audio.Contains(picked)) ? picked : null;
        }

        public static string Pref(HashSet<string> prefs, string prefix)
        {
            var f = prefs.FirstOrDefault(x => x.StartsWith(prefix));
            return f == null ? null : f.Substring(prefix.Length);
        }

        public static void Choose(HashSet<string> prefs, string prefix, string value)
        {
            prefs.RemoveWhere(f => f.StartsWith(prefix));
            if (value != null) prefs.Add(prefix + value);
        }
    }

    /// <summary>One play session: the pipeline (run.py --source chiaki --display), then chiaki-ng once the pipeline waits.
    /// With --control the pipeline also takes settings commands on its stdin (Send) and reports its settings as
    /// "[settings-state] {json}" lines (OnSettings) once the video is up: the launcher's Settings page while playing.
    /// The commands and the state are described in framegen/remote.py.</summary>
    class Session
    {
        public const string StatePrefix = "[settings-state] ";
        readonly Paths p;
        Process py;
        StreamWriter log, commands;
        readonly object gate = new object();
        volatile bool stopping;
        volatile bool live;  // the video window reads commands (it sent its settings): Stop asks it to quit, as Esc does
        bool streamEnded;
        // TestMode: "Test with a 60 fps game" (run.py --test-mode). Every session passes --results, since the
        // video window's menu can also turn the test on; run.py only writes the file once something was tested.
        public bool TestMode;
        public string Source = "chiaki";  // a SourceDef.Kind
        public string CaptureVideo, CaptureAudio;  // a capture card's devices (CaptureDevices), for Source "capture"
        public string WindowApp;  // the app window (AppWindows.Query), for Source "window"
        public string WindowKeysArg;  // its shortcuts, if changed (WindowKeys.Arg), for Source "window"
        public string LogPath, ResultsPath, LastLine = "";
        public Action<string> OnLine = s => { };
        public Action<PlayState, string> OnState = (s, d) => { };
        public Action<string> OnNote = s => { };
        public Action<string> OnSettings = json => { };  // a state line's JSON, on the output thread
        public Action<string> OnWindowKeys = json => { };  // the shortcuts the video window got (App window mode)

        // Windows lets a program bring its window to the front only if the program in front allows it. When the
        // player has just pressed Play here, that's this launcher: let the video window come to the front, so the
        // keys (M, F, Esc) go to it without a click first. (chiaki-ng does the same when its stream starts.)
        [System.Runtime.InteropServices.DllImport("user32.dll")] static extern bool AllowSetForegroundWindow(int pid);

        void LetVideoComeToFront()
        {
            try { if (Running) AllowSetForegroundWindow(py.Id); } catch (Exception) { }
        }
        // py exists a moment before it starts (OnState(Starting) comes in between), and asking it anything then
        // throws "No process is associated with this object": running means started and not exited.
        volatile bool started;
        public bool Running
        {
            get
            {
                var p = py;
                if (p == null || !started) return false;
                try { return !p.HasExited; }
                catch (InvalidOperationException) { return false; }
            }
        }

        public Session(Paths p) { this.p = p; }

        public void Start()
        {
            Directory.CreateDirectory(p.Logs);
            string stamp = $"{DateTime.Now:yyyyMMdd-HHmmss}";
            LogPath = Path.Combine(p.Logs, $"session-{stamp}.log");
            ResultsPath = Path.Combine(p.Logs, $"results-{stamp}.txt");
            log = new StreamWriter(LogPath, false, Encoding.UTF8) { AutoFlush = true };
            if (SystemCheck.GpuName == "unknown") SystemCheck.QueryGpu();  // fills in the GPU and driver
            log.WriteLine($"[launcher] SmoovPlay {BuildInfo.Version}, {DateTime.Now:yyyy-MM-dd HH:mm:ss}, {(SystemCheck.GpuName == "unknown" && SystemCheck.OtherGpu != null ? SystemCheck.OtherGpu + " (DirectML)" : SystemCheck.GpuName + ", driver " + SystemCheck.Driver)}"
                          + (Source != "chiaki" ? ", source " + Source : "") + (TestMode ? ", test mode (60 fps game played as 30 fps)" : ""));
            string args = "run.py " + Sources.RunArgs(Source, Source == "window" ? WindowApp : CaptureVideo, CaptureAudio)
                          + (Source == "window" && !string.IsNullOrEmpty(WindowKeysArg) ? " --window-keys \"" + WindowKeysArg.Replace("\"", "") + "\"" : "")
                          + " --display --control" + (TestMode ? " --test-mode" : "") + $" --results \"{ResultsPath}\" " + p.RunArgs;
            var psi = Proc.Python(p.Python, args.Trim(), p.App);
            psi.RedirectStandardInput = true;
            py = new Process { StartInfo = psi, EnableRaisingEvents = true };
            py.OutputDataReceived += (s, e) => { if (e.Data != null) Line(e.Data); };
            py.ErrorDataReceived += (s, e) => { if (e.Data != null) Line(e.Data); };
            py.Exited += (s, e) =>
            {
                py.WaitForExit();  // the rest of the output
                int rc = py.ExitCode;
                lock (gate) { if (log != null) { log.WriteLine($"[launcher] pipeline exited with code {rc}"); log.Dispose(); log = null; } }
                Program.Trace("pipeline exited, code " + rc);
                if (stopping) OnState(PlayState.Stopped, null);
                else if (rc == 0 && streamEnded) OnState(PlayState.Ended, null);
                else if (rc == 0) OnState(PlayState.Stopped, null);
                else OnState(PlayState.Error, LastLine);
            };
            OnState(PlayState.Starting, null);
            py.Start();
            started = true;
            LetVideoComeToFront();
            // Our own writer: UTF-8 without a BOM (Process's own uses the console's code page).
            commands = new StreamWriter(py.StandardInput.BaseStream, new UTF8Encoding(false)) { AutoFlush = true, NewLine = "\n" };
            py.BeginOutputReadLine();
            py.BeginErrorReadLine();
        }

        /// <summary>One command for the running pipeline, e.g. "set hz 90" or "restart". False if it has gone.</summary>
        public bool Send(string command)
        {
            lock (gate)
            {
                // (the Looks page sends a look up to 25 times a second while a slider moves: just its size)
                if (log != null) log.WriteLine(command == "quit" ? "[launcher] Stop: asked the video window to close"
                    : "[launcher] settings: " + (command.StartsWith("look-show ") ? "look-show (" + command.Length + " characters)" : command));
                try
                {
                    if (commands == null || !Running) return false;
                    commands.WriteLine(command);
                    return true;
                }
                catch (Exception) { return false; }  // it exited meanwhile
            }
        }

        /// <summary>Stops the session. Once the video is up it's asked to close, as Esc does, so a clip being saved is
        /// finished and chiaki-ng comes back; it's ended outright if it hasn't gone 20 s later, if it isn't up yet,
        /// or on a second Stop.</summary>
        public void Stop()
        {
            bool again = stopping;
            stopping = true;
            var pr = py;
            try
            {
                if (pr == null || pr.HasExited) return;
                if (!again && live && Send("quit"))
                {
                    System.Threading.ThreadPool.QueueUserWorkItem(_ => { try { if (!pr.WaitForExit(20000)) pr.Kill(); } catch (Exception) { } });
                    return;
                }
                pr.Kill();
            }
            catch (Exception) { }
        }

        void Line(string line)
        {
            if (line.StartsWith(StatePrefix))  // not for the log; it also means the video window reads commands
            {
                live = true;
                OnSettings(line.Substring(StatePrefix.Length));
                return;
            }
            lock (gate) { if (log != null) log.WriteLine(line); }
            if (line.Trim().Length > 0) LastLine = line.Trim();
            OnLine(line);
            if (line.StartsWith("[chiaki] waiting for chiaki-ng"))
            {
                if (Source == "chiaki" && Chiaki.Ours(p) == null) Chiaki.Start(p, OnLine);
                OnState(PlayState.Waiting, null);
            }
            else if (line.StartsWith("[tap] waiting for an app"))  // the frame tap: the app starts when the player likes
                OnState(PlayState.Waiting, null);
            else if (line.StartsWith("[chiaki] stream:"))
            {
                LetVideoComeToFront();  // (the video window opens next)
                OnState(PlayState.Found, line.Substring("[chiaki] stream:".Length).Trim());
            }
            else if (line.StartsWith("[tap] frames:"))
                OnState(PlayState.Found, line.Substring("[tap] frames:".Length).Trim());
            else if (line.StartsWith("[trt] building"))
                OnState(PlayState.Building, null);
            else if (line.StartsWith("[run] restarting for "))  // "[run] restarting for AI engine: RIFE ..."
            {
                string why = line.Substring("[run] restarting for ".Length);
                int colon = why.IndexOf(": ");
                OnState(PlayState.Building, colon > 0 ? why.Substring(0, colon) : why);
            }
            else if (line.StartsWith("[run] playing"))  // the video is up: first, and after every in-app restart
                OnState(PlayState.Playing, null);
            else if (line.StartsWith("[chiaki] this stream was already running"))
                OnNote("This stream started before SmoovPlay. In chiaki-ng, end the stream and connect again.");
            else if (line.StartsWith("[tap] this stream was already running"))  // another app's compressed frames
                OnNote("This stream started before SmoovPlay. Restart the stream in the app.");
            else if (line.StartsWith("[chiaki] WARNING:") || line.StartsWith("[tap] WARNING:"))
                OnNote(line.Substring(line.IndexOf("WARNING:") + "WARNING:".Length).Trim());
            else if (line.StartsWith("[chiaki] stream ended") || line.StartsWith("[tap] the app stopped sending frames"))
                streamEnded = true;
            else if (line.StartsWith("[window-keys] "))
                OnWindowKeys(line.Substring("[window-keys] ".Length));
        }
    }

    /// <summary>Before &amp; after videos being made (framegen/replay.py, SAVING_DIR). Each is a process of its own that
    /// carries on after the video window and this launcher close, and keeps %APPDATA%\framegen\saving\&lt;process id&gt;.txt
    /// while it works: the video's path, then "failed: why" if it failed. Updating, repairing or uninstalling replaces
    /// the Python it runs on, so those stop it.</summary>
    static class Saving
    {
        public class Video { public int Pid; public string Out, Failed; public bool Running; }

        static string Dir { get { return Path.Combine(Paths.SettingsDir, "saving"); } }

        /// <summary>Every one there: still being made, failed, or left by one that was stopped.</summary>
        public static List<Video> Read()
        {
            var all = new List<Video>();
            try
            {
                if (!Directory.Exists(Dir)) return all;
                foreach (var f in Directory.GetFiles(Dir, "*.txt"))
                {
                    int pid;
                    string[] l;
                    if (!int.TryParse(Path.GetFileNameWithoutExtension(f), out pid)) continue;
                    try { l = File.ReadAllLines(f); } catch (Exception) { continue; }  // being written
                    if (l.Length == 0 || l[0].Trim().Length == 0) continue;
                    var v = new Video { Pid = pid, Out = l[0].Trim() };
                    v.Failed = l.Length > 1 && l[1].StartsWith("failed: ") ? l[1].Substring(8).Trim() : null;
                    v.Running = v.Failed == null && Alive(pid, File.GetLastWriteTime(f));
                    all.Add(v);
                }
            }
            catch (Exception) { }
            return all;
        }

        /// <summary>That process is still the one that wrote the file at `since` (not a later one given its id).</summary>
        static bool Alive(int pid, DateTime since)
        {
            try
            {
                using (var pr = Process.GetProcessById(pid))
                    return !pr.HasExited && pr.ProcessName.StartsWith("python", StringComparison.OrdinalIgnoreCase)
                           && pr.StartTime <= since.AddSeconds(2);
            }
            catch (Exception) { return false; }
        }

        /// <summary>Done with it: its file goes, and an unfinished video's ".part" with it.</summary>
        public static void Forget(Video v)
        {
            try { File.Delete(Path.Combine(Dir, v.Pid + ".txt")); } catch (Exception) { }
            if (!v.Running) { try { File.Delete(v.Out + ".part"); } catch (Exception) { } }
        }

        public static void Stop(IEnumerable<Video> videos)
        {
            foreach (var v in videos.Where(x => x.Running).ToList())
            {
                try { using (var pr = Process.GetProcessById(v.Pid)) { pr.Kill(); pr.WaitForExit(5000); } } catch (Exception) { }
                v.Running = false;
                Forget(v);
            }
        }
    }

    static class Diagnostics
    {
        static void AppendLog(StringBuilder sb, string logFile, int max)
        {
            string[] lines;
            using (var fs = new FileStream(logFile, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
            using (var sr = new StreamReader(fs))
                lines = sr.ReadToEnd().Split('\n');
            int n = Math.Min(max, lines.Length);
            sb.AppendLine($"Log {Path.GetFileName(logFile)}, last {n} lines:");
            foreach (var l in lines.Skip(lines.Length - n))
                sb.AppendLine(l.TrimEnd('\r'));
        }

        public static string Collect(Paths p, string lastLog)
        {
            var sb = new StringBuilder();
            sb.AppendLine($"SmoovPlay {BuildInfo.Version} diagnostics, {DateTime.Now:yyyy-MM-dd HH:mm}");
            const string nt = @"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows NT\CurrentVersion";
            string product = Registry.GetValue(nt, "ProductName", "") as string, build = Registry.GetValue(nt, "CurrentBuild", "") as string;
            string release = Registry.GetValue(nt, "DisplayVersion", "") as string;
            sb.AppendLine($"Windows: {WindowsName(product, build)}{(string.IsNullOrEmpty(release) ? "" : " " + release)} build {build}");
            var prefs = Prefs.Load();
            string kind = prefs.Where(f => f.StartsWith("source:")).Select(f => f.Substring(7)).FirstOrDefault() ?? "chiaki";
            sb.AppendLine("Source: " + kind);
            foreach (var c in SystemCheck.RunAll(p.Install, false, kind))
                sb.AppendLine($"Check [{c.Level}] {c.Title}: {c.Value}. {c.Detail}");
            if (kind == "capture" || CaptureDevices.Loaded)
            {
                if (!CaptureDevices.Loaded && p.RuntimeReady) CaptureDevices.Load(p);
                sb.Append(CaptureDevices.Summary());
                sb.Append(SystemCheck.CaptureHardware());
            }
            if (kind == "window" || AppWindows.Loaded)
            {
                if (!AppWindows.Loaded && p.RuntimeReady) AppWindows.Load(p);
                sb.Append(AppWindows.Summary());
            }
            // (The user's name is taken out below: a path like C:\Users\<name with Chinese letters> would look fine.)
            sb.AppendLine("Install: " + p.Install + (p.Dev ? " (dev)" : "")
                          + (p.Install.Any(c => c > 127) ? " (the path has letters outside English A-Z)" : ""));
            sb.AppendLine("Runtime: " + (File.Exists(p.Ready) ? File.ReadAllText(p.Ready).Trim() : "not ready"));
            sb.AppendLine("chiaki-ng: " + (Chiaki.Ours(p) != null ? "running (started by SmoovPlay)" : "not started by SmoovPlay")
                          + $", {Chiaki.Others(p).Length} other chiaki-ng running");
            string settings = Paths.SettingsFile;
            sb.AppendLine("Settings: " + (File.Exists(settings) ? File.ReadAllText(settings).Trim() : "none saved"));
            string logFile = lastLog;
            if (logFile == null && Directory.Exists(p.Logs))
                logFile = new DirectoryInfo(p.Logs).GetFiles("*.log").OrderByDescending(f => f.LastWriteTime).Select(f => f.FullName).FirstOrDefault();
            if (logFile != null && File.Exists(logFile))
                AppendLog(sb, logFile, 200);
            // Copied after setup (its log is the launcher's last): the newest game session's log too, which is what a
            // report about playing needs, or a note that there's none yet
            if (logFile == null || Path.GetFileName(logFile).StartsWith("setup-", StringComparison.OrdinalIgnoreCase))
            {
                string session = Directory.Exists(p.Logs)
                    ? new DirectoryInfo(p.Logs).GetFiles("session-*.log").OrderByDescending(f => f.LastWriteTime).Select(f => f.FullName).FirstOrDefault()
                    : null;
                if (session != null) AppendLog(sb, session, 200);
                else sb.AppendLine("No game session log yet: after playing, press Copy diagnostics again.");
            }
            string results = LatestResults(p);
            if (results != null)
            {
                string text = ReadShared(results).TrimEnd();
                sb.AppendLine($"Test results {Path.GetFileName(results)}:");
                // NVIDIA's licence forbids publishing VFG's results, and bug reports get pasted in public places.
                sb.AppendLine(text.Contains(VfgPrivate) ? "(left out: they include NVIDIA VFG, whose licence doesn't allow sharing its results)" : text);
            }
            if (File.Exists(Program.CrashLog))
            {
                var crash = File.ReadAllLines(Program.CrashLog);
                sb.AppendLine("Launcher errors (last 40 lines):");
                foreach (var l in crash.Skip(Math.Max(0, crash.Length - 40))) sb.AppendLine(l);
            }
            return Anonymize(sb.ToString(), Environment.GetFolderPath(Environment.SpecialFolder.UserProfile));
        }

        /// <summary>A device name without its owner's: "Alex's iPhone Camera" becomes "(name)'s iPhone Camera".</summary>
        public static string NoOwner(string name)
        {
            return Regex.Replace(name ?? "", @"[^\s()""]+['’]s\b", "(name)'s");
        }

        /// <summary>Windows 11 still calls itself "Windows 10 Pro" (and so on) in the registry's ProductName, so the build
        /// number decides: 22000 and up is Windows 11.</summary>
        public static string WindowsName(string product, string build)
        {
            product = string.IsNullOrEmpty(product) ? "Windows" : product;
            return int.TryParse(build, out int b) && b >= 22000 ? Regex.Replace(product, @"\bWindows 10\b", "Windows 11") : product;
        }

        /// <summary>Bug reports get pasted in public places, so the Windows user name is left out: the user's folder
        /// becomes %USERPROFILE% however a log spells it (pip writes it in lower case, JSON doubles the backslashes,
        /// some tools use forward slashes or the short 8.3 name), and so does any other C:\Users\NAME folder.</summary>
        public static string Anonymize(string text, string home)
        {
            const RegexOptions ic = RegexOptions.IgnoreCase | RegexOptions.CultureInvariant;
            const string sep = @"(?:\\\\|\\|/)";  // \\ (JSON), \ or /
            if (!string.IsNullOrEmpty(home))
            {
                var parts = home.TrimEnd('\\', '/').Split(new[] { '\\', '/' }, StringSplitOptions.RemoveEmptyEntries);
                if (parts.Length > 0)  // the whole folder name, so C:\Users\Al leaves C:\Users\Alice alone
                    text = Regex.Replace(text, string.Join(sep, parts.Select(Regex.Escape)) + @"(?=$|[\\/""'\s:;,)\]])", "%USERPROFILE%", ic | RegexOptions.Multiline);
            }
            // Any other user folder: X:\Users\NAME (not Public or Default), whatever the case and separators
            return Regex.Replace(text, @"\b[A-Za-z]:" + sep + "Users" + sep + @"(?!(?:Public|Default|All Users)(?:\\|/|""|$))[^\\/""\r\n]+", "%USERPROFILE%", ic | RegexOptions.Multiline);
        }

        /// <summary>run.py's PRIVATE_RESULTS line, in a test report that includes NVIDIA VFG.</summary>
        const string VfgPrivate = "These results include NVIDIA VFG.";

        /// <summary>The newest test-mode report (logs\results-*.txt, written by run.py --results), or null.</summary>
        public static string LatestResults(Paths p)
        {
            if (!Directory.Exists(p.Logs)) return null;
            return new DirectoryInfo(p.Logs).GetFiles("results-*.txt").OrderByDescending(f => f.LastWriteTime)
                .Select(f => f.FullName).FirstOrDefault();
        }

        /// <summary>A text file's contents, even while run.py may be replacing it.</summary>
        public static string ReadShared(string path)
        {
            try
            {
                using (var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                using (var sr = new StreamReader(fs, Encoding.UTF8))
                    return sr.ReadToEnd();
            }
            catch (IOException) { return ""; }
            catch (UnauthorizedAccessException) { return ""; }
        }
    }

    /// <summary>What the launcher remembers between runs (the setup guides' ticks, the source picked on the Play page),
    /// next to the pipeline's settings.</summary>
    static class Prefs
    {
        static string File_
        {
            get { return Environment.GetEnvironmentVariable("SMOOVPLAY_PREFS") ?? Path.Combine(Paths.SettingsDir, "smoovplay-launcher.txt"); }
        }

        public static HashSet<string> Load()
        {
            try { return new HashSet<string>(File.ReadAllLines(File_).Select(l => l.Trim()).Where(l => l.Length > 0)); }
            catch (Exception) { return new HashSet<string>(); }
        }

        public static void Save(HashSet<string> flags)
        {
            try { Directory.CreateDirectory(Path.GetDirectoryName(File_)); File.WriteAllLines(File_, flags.OrderBy(f => f)); }
            catch (Exception) { }
        }

        /// <summary>A value from the pipeline's settings.json (a flat JSON object), or null.</summary>
        public static string Setting(string key)
        {
            try
            {
                var m = Regex.Match(File.ReadAllText(Paths.SettingsFile), "\"" + Regex.Escape(key) + "\"\\s*:\\s*\"?([^\",}\\s]+)");
                return m.Success ? m.Groups[1].Value : null;
            }
            catch (Exception) { return null; }
        }
    }
}
