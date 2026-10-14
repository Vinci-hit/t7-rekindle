using System;
using System.IO;
using static T7R.ManagedHarness.LauncherTests;

namespace T7R.ManagedHarness
{
    internal static class HarnessProgressTests
    {
        internal static void Run()
        {
            var previous = Console.Out;
            using (var output = new StringWriter())
            {
                Console.SetOut(output);
                try
                {
                    var calls = 0;
                    Program.RunGroup("success fixture", () =>
                    {
                        calls++;
                        Assert(output.ToString().Contains("[RUN] success fixture")
                            && !output.ToString().Contains("[PASS]"), "group progress was not written before execution");
                    });
                    var success = output.ToString();
                    Assert(calls == 1 && success.Contains("[PASS] success fixture (") && success.Contains(" ms)")
                        && !success.Contains("[FAIL]"), "successful group lost its result or elapsed time");

                    output.GetStringBuilder().Clear();
                    var expected = new InvalidOperationException("group failure fixture");
                    Exception observed = null;
                    try { Program.RunGroup("failure fixture", () => { throw expected; }); }
                    catch (Exception error) { observed = error; }
                    var failure = output.ToString();
                    Assert(ReferenceEquals(observed, expected), "group progress swallowed or replaced the test failure");
                    Assert(failure.Contains("[RUN] failure fixture") && failure.Contains("[FAIL] failure fixture (")
                        && failure.Contains(" ms)") && !failure.Contains("[PASS]"),
                        "failed group lost its result or elapsed time");
                }
                finally { Console.SetOut(previous); }
            }
        }
    }
}
