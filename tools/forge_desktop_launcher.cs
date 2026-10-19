// Forge AI desktop launcher.
//
// A native wrapper, not a second application. It locates the repository, verifies what the real
// launcher needs, and then runs tools/forge_launch.py with the virtual environment's interpreter --
// the same thing the desktop shortcut does. Process lifecycle, readiness checks, window restore,
// duplicate handling and clean shutdown all stay in that launcher.
//
// --check writes a prerequisite report next to the executable and exits 0 (ready) or 2 (not ready),
// so the built artifact is verifiable without opening a window.

using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;

internal static class ForgeDesktop
{
    private const string Marker = @"tools\forge_launch.py";

    private static string FindRepo()
    {
        string overridePath = Environment.GetEnvironmentVariable("FORGE_REPO");
        if (!string.IsNullOrEmpty(overridePath) && IsRepo(overridePath)) return overridePath;

        string[] args = Environment.GetCommandLineArgs();
        for (int index = 1; index < args.Length - 1; index++)
        {
            if (args[index] == "--repo" && IsRepo(args[index + 1])) return args[index + 1];
        }

        string here = Path.GetDirectoryName(Process.GetCurrentProcess().MainModule.FileName);
        DirectoryInfo directory = new DirectoryInfo(here);
        for (int depth = 0; depth < 6 && directory != null; depth++)
        {
            if (IsRepo(directory.FullName)) return directory.FullName;
            directory = directory.Parent;
        }
        return null;
    }

    private static bool IsRepo(string path)
    {
        try
        {
            return path != null
                && File.Exists(Path.Combine(path, Marker))
                && File.Exists(Path.Combine(path, @"app\api\main.py"));
        }
        catch (Exception) { return false; }
    }

    private static string EdgePath()
    {
        string[] roots = new string[]
        {
            Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
            Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86)
        };
        foreach (string root in roots)
        {
            string candidate = Path.Combine(root, @"Microsoft\Edge\Application\msedge.exe");
            if (File.Exists(candidate)) return candidate;
        }
        return null;
    }

    private static string Check(string repo, out bool ready)
    {
        string python = Path.Combine(repo, @".venv\Scripts\python.exe");
        string pythonw = Path.Combine(repo, @".venv\Scripts\pythonw.exe");
        string index = Path.Combine(repo, @"ui\dist\index.html");
        string edge = EdgePath();

        StringBuilder report = new StringBuilder();
        report.AppendLine("Forge AI - prerequisite check");
        report.AppendLine("  [ok] repository: " + repo);
        report.AppendLine("  [" + (File.Exists(python) ? "ok" : "MISSING") + "] python (venv): " + python);
        report.AppendLine("  [" + (File.Exists(pythonw) ? "ok" : "MISSING") + "] pythonw (venv): " + pythonw);
        report.AppendLine("  [" + (File.Exists(index) ? "ok" : "MISSING") + "] UI build: " + index);
        report.AppendLine("  [" + (edge != null ? "ok" : "MISSING") + "] Microsoft Edge: " + (edge ?? "not found in Program Files"));

        ready = File.Exists(python) && File.Exists(index) && edge != null;
        report.AppendLine(ready ? "All prerequisites present." : "Cannot start Forge AI: a prerequisite is missing.");
        return report.ToString();
    }

    [STAThread]
    private static int Main(string[] args)
    {
        string repo = FindRepo();
        if (repo == null)
        {
            MessageBox.Show(
                "Forge AI: repository not found.\n\nKeep this launcher inside the Forge AI folder " +
                "(for example its dist folder), or set FORGE_REPO to the repository path.",
                "Forge AI", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 2;
        }

        bool ready;
        string report = Check(repo, out ready);

        bool checkOnly = Array.IndexOf(args, "--check") >= 0;
        if (checkOnly)
        {
            string reportPath = Path.Combine(
                Path.GetDirectoryName(Process.GetCurrentProcess().MainModule.FileName),
                "forge-exe-check.txt");
            File.WriteAllText(reportPath, report, new UTF8Encoding(false));
            Console.Out.Write(report);
            return ready ? 0 : 2;
        }

        if (!ready)
        {
            MessageBox.Show("Forge AI cannot start.\n\n" + report, "Forge AI",
                MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 2;
        }

        string pythonwPath = Path.Combine(repo, @".venv\Scripts\pythonw.exe");
        string interpreter = File.Exists(pythonwPath)
            ? pythonwPath
            : Path.Combine(repo, @".venv\Scripts\python.exe");
        ProcessStartInfo start = new ProcessStartInfo(interpreter);
        start.Arguments = "-B -X utf8 \"" + Path.Combine(repo, @"tools\forge_launch.py") + "\"";
        start.WorkingDirectory = repo;
        // The child must not inherit this process's standard handles. With UseShellExecute = false
        // the launched pythonw kept the caller's stdout/stderr open for the whole session, which blocked
        // every terminal, script and automation tool that ran this exe until Forge was closed. ShellExecute
        // starts the launcher detached, so the caller regains control at once while Forge keeps running.
        // The arguments, working directory and error handling are unchanged.
        start.UseShellExecute = true;
        start.CreateNoWindow = false;
        Process.Start(start);
        return 0;
    }
}
