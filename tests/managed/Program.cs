using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.Input;
using T7.Rekindle.Core;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;

namespace T7.ManagedHarness
{
    internal static class Program
    {
        [STAThread]
        private static int Main(string[] args)
        {
            if (args.Length >= 1 && args[0] == "--announcement-tests")
            {
                var directory = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "T7-announcement-tests-" + Guid.NewGuid().ToString("N"));
                var app = new T7.Rekindle.Desktop.App(false);
                app.InitializeComponent();
                app.ShutdownMode = System.Windows.ShutdownMode.OnExplicitShutdown;
                var previousContext = SynchronizationContext.Current;
                SynchronizationContext.SetSynchronizationContext(new System.Windows.Threading.DispatcherSynchronizationContext());
                try
                {
                    RemoteAnnouncementTests.Run(directory);
                    var output = args.Length == 2 ? args[1] : null;
                    AnnouncementTests.Run(directory, output);
                    foreach (var theme in new[] { "Dark", "HighContrast" })
                    {
                        var resources = new System.Windows.ResourceDictionary
                        {
                            Source = new Uri("/T7-Rekindle;component/Resources/Theme." + theme + ".xaml", UriKind.Relative)
                        };
                        app.Resources.MergedDictionaries.Add(resources);
                        try { AnnouncementTests.Run(System.IO.Path.Combine(directory, theme), output == null ? null : System.IO.Path.Combine(output, theme)); }
                        finally { app.Resources.MergedDictionaries.Remove(resources); }
                    }
                    Console.WriteLine("announcement API, refresh, fallback and UI checks passed");
                    return 0;
                }
                catch (Exception error) { Console.Error.WriteLine(error); return 1; }
                finally
                {
                    NLog.LogManager.Shutdown();
                    app.Shutdown();
                    SynchronizationContext.SetSynchronizationContext(previousContext);
                    if (System.IO.Directory.Exists(directory)) System.IO.Directory.Delete(directory, true);
                }
            }
            if (args.Length >= 1 && args[0] == "--log-tests")
            {
                var directory = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "T7-log-tests-" + Guid.NewGuid().ToString("N"));
                var app = new T7.Rekindle.Desktop.App(false);
                app.InitializeComponent();
                app.ShutdownMode = System.Windows.ShutdownMode.OnExplicitShutdown;
                var previousContext = SynchronizationContext.Current;
                SynchronizationContext.SetSynchronizationContext(new System.Windows.Threading.DispatcherSynchronizationContext());
                try
                {
                    var output = args.Length == 2 ? args[1] : null;
                    DiagnosticLogTests.Run(directory, output);
                    LogLevelTests.Run(directory, output);
                    SettingsMergeTests.Run(directory);
                    foreach (var theme in new[] { "Dark", "HighContrast" })
                    {
                        var resources = new System.Windows.ResourceDictionary
                        {
                            Source = new Uri("/T7-Rekindle;component/Resources/Theme." + theme + ".xaml", UriKind.Relative)
                        };
                        app.Resources.MergedDictionaries.Add(resources);
                        try { LogLevelTests.Run(System.IO.Path.Combine(directory, theme), output == null ? null : System.IO.Path.Combine(output, theme)); }
                        finally { app.Resources.MergedDictionaries.Remove(resources); }
                    }
                    Console.WriteLine("log level, persistence, forwarding and UI checks passed");
                    return 0;
                }
                catch (Exception error) { Console.Error.WriteLine(error); return 1; }
                finally
                {
                    NLog.LogManager.Shutdown();
                    app.Shutdown();
                    SynchronizationContext.SetSynchronizationContext(previousContext);
                    if (System.IO.Directory.Exists(directory)) System.IO.Directory.Delete(directory, true);
                }
            }
            if (args.Length == 2 && args[0] == "--login-ui-native")
            {
                try { LoginNativeUiTests.Run(args[1]); return 0; }
                catch (Exception error) { Console.Error.WriteLine(error); return 1; }
            }
            if (args.Length >= 1 && args[0] == "--account-tests")
            {
                try
                {
                    AccountTests.Run();
                    if (args.Length == 2)
                    {
                        var app = new T7.Rekindle.Desktop.App(false);
                        app.InitializeComponent();
                        app.ShutdownMode = System.Windows.ShutdownMode.OnExplicitShutdown;
                        AccountTests.Render(args[1]);
                        foreach (var theme in new[] { "Dark", "HighContrast" })
                        {
                            var resources = new System.Windows.ResourceDictionary
                            {
                                Source = new Uri("/T7-Rekindle;component/Resources/Theme." + theme + ".xaml", UriKind.Relative)
                            };
                            app.Resources.MergedDictionaries.Add(resources);
                            try { AccountTests.Render(System.IO.Path.Combine(args[1], theme)); }
                            finally { app.Resources.MergedDictionaries.Remove(resources); }
                        }
                        app.Shutdown();
                    }
                    Console.WriteLine("account checks passed");
                    return 0;
                }
                catch (Exception error) { Console.Error.WriteLine(error); return 1; }
            }
            if (args.Length > 0 && args[0] == "--rollback-tests")
            {
                try
                {
                    Console.WriteLine("Checking rollback services and state...");
                    ReleaseHistoryTests.Run();
                    Console.WriteLine("Checking rollback layout...");
                    var app = new T7.Rekindle.Desktop.App(false);
                    app.InitializeComponent();
                    app.ShutdownMode = System.Windows.ShutdownMode.OnExplicitShutdown;
                    ReleaseHistoryTests.Render(args.Length == 2 ? args[1] : null);
                    app.Shutdown();
                    Console.WriteLine("rollback checks passed");
                    return 0;
                }
                catch (Exception error)
                {
                    Console.Error.WriteLine(error.GetType().FullName + ": " + error.Message);
                    Console.Error.WriteLine(error.StackTrace);
                    return 1;
                }
            }
            if (args.Length == 2 && args[0] == "--native-ui")
            {
                NativeWindowUiTests.Run(args[1]);
                return 0;
            }
            if (!SettingsSchema.IsValid(new UserSettings())) return 1;
            if (SettingsSchema.IsValid(new UserSettings { ClientDirectory = "C:\\client\r\n" })) return 2;
            if (SettingsSchema.IsValid(new UserSettings { WindowWidth = 479 })) return 3;
            if (SettingsSchema.IsValid(new UserSettings { WindowHeight = 4097 })) return 4;
            if (SettingsSchema.IsValid(new UserSettings { SchemaVersion = 2 })) return 5;
            if (NativeBridgeContract.IsUtf8PathAcceptable("C:\\client\r")) return 6;
            if (NativeBridgeContract.IsUtf8PathAcceptable(new string('界', 20000))) return 7;
            if (NativeBridgeContract.IsUtf8PathAcceptable("relative\\client")) return 8;
            if (NativeBridgeContract.IsUtf8PathAcceptable("C:\\client\uD800")) return 9;
            if (SettingsSchema.IsValid(new UserSettings { WindowWidth = double.NaN })) return 10;
            if (SettingsSchema.IsValid(new UserSettings { ClientDirectory = "relative\\client" })) return 11;
            if (NativeBridgeContract.IsUtf8PathAcceptable("C:drive-relative")) return 12;
            if (SettingsSchema.IsValid(new UserSettings { ClientDirectory = "C:drive-relative" })) return 13;
            if (NativeBridgeContract.IsUtf8PathAcceptable("\\root-relative")) return 14;
            if (SettingsSchema.IsValid(new UserSettings { ClientDirectory = "\\root-relative" })) return 15;
            if (NativeBridgeContract.IsUtf8PathAcceptable("\\\\")) return 16;
            if (SettingsSchema.IsValid(new UserSettings { ClientDirectory = "\\\\" })) return 17;
            if (args.Length == 2 && args[0] == "--render-v11")
            {
                try { ThemeTests.Run(args[1]); return 0; }
                catch (Exception error) { Console.Error.WriteLine(error); return 1; }
            }
            NativeAbiLayout.Validate();
            AccountTests.Run();
            NativeBridgeServiceTests.Run();
            GitHubReleaseUpdateTests.Run();
            UpdateFeedTests.Run();
            UpdateChannelTests.Run();
            ReleaseNotesTests.Run();
            ReleaseHistoryTests.Run();
            UpdateDownloadTests.Run();
            UpdateDownloadViewModelTests.Run();
            LauncherTests.Run();
            ThemeTests.Run(args.Length == 2 && args[0] == "--render-ui" ? args[1] : null);
            Console.WriteLine("managed contract checks passed");
            return 0;
        }

    }
}
