using System;
using System.Collections.Generic;
using System.IO;
using System.Threading;
using T7R.Battle;
using T7R.Core;

namespace T7R.BattleServer
{
    internal static class Program
    {
        private static int Main(string[] args)
        {
            if (args.Length == 1 && args[0] == "--help")
            {
                Console.WriteLine("T7R.BattleServer --package-root PATH --client-directory PATH --state-root PATH");
                Console.WriteLine("Runs the shared battle runtime without a game process. Ctrl+C stops it.");
                Console.WriteLine("Only private loopback game endpoints are enabled; this is not an online matchmaking node.");
                return 0;
            }
            try
            {
                var options = Parse(args);
                using (var stopped = new ManualResetEventSlim())
                using (var host = new NativeBattleHost(options["--package-root"], options["--client-directory"], options["--state-root"]))
                {
                    ConsoleCancelEventHandler cancel = (sender, e) => { e.Cancel = true; stopped.Set(); };
                    Console.CancelKeyPress += cancel;
                    try
                    {
                        host.Start();
                        var snapshot = host.GetSnapshot();
                        Console.WriteLine("Battle runtime ready (loopback): login={0}, logic={1}, instance={2}",
                            snapshot.LoginPort, snapshot.LogicPort, snapshot.InstancePort);
                        while (!stopped.Wait(250))
                        {
                            if (host.GetSnapshot().State != BattleHostState.Running)
                                throw new InvalidOperationException("Battle runtime stopped: " + host.GetError());
                        }
                        host.Stop();
                    }
                    finally { Console.CancelKeyPress -= cancel; }
                }
                return 0;
            }
            catch (Exception error) { Console.Error.WriteLine(error.Message); return 1; }
        }

        private static Dictionary<string, string> Parse(string[] args)
        {
            var result = new Dictionary<string, string>(StringComparer.Ordinal);
            if (args.Length != 6) throw new ArgumentException("Specify --package-root, --client-directory and --state-root. Use --help for usage.");
            for (int i = 0; i < args.Length; i += 2)
            {
                var key = args[i];
                if (key != "--package-root" && key != "--client-directory" && key != "--state-root")
                    throw new ArgumentException("Unknown option: " + key);
                if (result.ContainsKey(key)) throw new ArgumentException("Duplicate option: " + key);
                result.Add(key, Path.GetFullPath(args[i + 1]));
            }
            return result;
        }
    }
}
