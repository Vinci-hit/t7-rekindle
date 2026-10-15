using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using Newtonsoft.Json.Linq;
using T7.Rekindle.Desktop.Services;
using static T7.ManagedHarness.LauncherTests;

namespace T7.ManagedHarness
{
    internal static class PreviewSigningScriptTests
    {
        internal static void Run(string directory)
        {
            Directory.CreateDirectory(directory);
            var root = new DirectoryInfo(AppContext.BaseDirectory);
            while (root != null && !File.Exists(Path.Combine(root.FullName, "scripts", "preview-access.ps1"))) root = root.Parent;
            Assert(root != null, "preview signing script is missing");
            var script = Path.Combine(root.FullName, "scripts", "preview-access.ps1");
            var key = Path.Combine(directory, "authority.private.json");
            var publicKey = Path.Combine(directory, "authority.public.txt");
            var init = RunScript(script, "-Action Init -PrivateKeyPath " + Quote(key) + " -PublicKeyPath " + Quote(publicKey), 0);
            Assert((string)JObject.Parse(init)["publicKey"] == File.ReadAllText(publicKey, Encoding.UTF8).Trim(), "script public output differs from its file");
            var protectedKey = File.ReadAllText(key, Encoding.UTF8);
            RunScript(script, "-Action Init -PrivateKeyPath " + Quote(key) + " -PublicKeyPath " + Quote(publicKey), 1);
            Assert(File.ReadAllText(key, Encoding.UTF8) == protectedKey, "key initialization overwrote an existing key");
            using (var fixture = new PreviewAccessFixture())
            {
                var access = new PreviewAccessService(Path.Combine(directory, "client"), File.ReadAllText(publicKey, Encoding.UTF8),
                    () => fixture.Subject);
                var issued = RunScript(script, "-Action Issue -PrivateKeyPath " + Quote(key) + " -RequestCode " + access.RequestCode, 0).Trim();
                Assert(access.TryImport(issued, out var error), "PowerShell 5.1 grant rejected by net48: " + error);
                Assert(access.ExpiresAt > DateTimeOffset.UtcNow.AddDays(7).AddMinutes(-1), "script did not issue a seven-day grant");
                var failed = RunScript(script, "-Action Issue -PrivateKeyPath " + Quote(key) + " -RequestCode 123456", 1);
                Assert(!failed.Contains("T7PA1"), "failed signing emitted a grant");
                File.WriteAllText(key, "{}", Encoding.UTF8);
                RunScript(script, "-Action Issue -PrivateKeyPath " + Quote(key) + " -RequestCode " + access.RequestCode, 1);
            }
        }

        private static string Quote(string value) => "\"" + value + "\"";

        private static string RunScript(string script, string arguments, int expectedCode)
        {
            var shell = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
            var start = new ProcessStartInfo(shell, "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File " + Quote(script) + " " + arguments)
            { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
            using (var process = Process.Start(start))
            {
                var output = process.StandardOutput.ReadToEndAsync();
                var error = process.StandardError.ReadToEndAsync();
                if (!process.WaitForExit(30000)) { process.Kill(); throw new Exception("preview signing script timed out"); }
                Assert(process.ExitCode == expectedCode, "preview signing script failed: " + error.GetAwaiter().GetResult());
                return output.GetAwaiter().GetResult();
            }
        }
    }
}
