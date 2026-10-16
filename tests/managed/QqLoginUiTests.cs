using System;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using Microsoft.Web.WebView2.Core;
using T7R.Desktop.Views;
using static T7R.ManagedHarness.LauncherTests;

namespace T7R.ManagedHarness
{
    internal static class QqLoginUiTests
    {
        internal static void Run(string output)
        {
            Assert(T7R.Desktop.Services.WindowLayoutController.CalculateSize(1920, 1032, 1, 636, 378, 1)
                == new Size(636, 378), "login viewport was rounded away from the approved logical size");
            int detections = 0, creations = 0;
            bool available = false;
            string opened = null;
            var dialog = new QqLoginDialog(() =>
            {
                detections++;
                if (!available) throw new WebView2RuntimeNotFoundException();
                return "TEST_RUNTIME";
            }, () => { creations++; return Task.CompletedTask; }, address => opened = address);
            try
            {
                RunTask(dialog.InitializeBrowserAsync());
                Assert(creations == 0, "WebView was created before checking runtime availability");
                var panel = (ScrollViewer)dialog.FindName("RuntimePanel");
                var install = (Button)dialog.FindName("InstallRuntime");
                var detect = (Button)dialog.FindName("DetectRuntime");
                var reload = (Button)dialog.FindName("ReloadButton");
                Assert(panel.Visibility == Visibility.Visible && reload.Visibility == Visibility.Collapsed,
                    "missing runtime did not show the native prompt without reload overlay");
                foreach (var scale in new[] { 1.0, 1.25, 1.5, 1.75, 2.0 })
                {
                    LauncherLayoutTests.Render((FrameworkElement)dialog.Content, null, output, "login-runtime-missing-" + (int)(scale * 100), 636, 378, scale);
                    LauncherLayoutTests.AssertWithin(install, (FrameworkElement)dialog.Content, 636, 378);
                    LauncherLayoutTests.AssertWithin(detect, (FrameworkElement)dialog.Content, 636, 378);
                    LauncherLayoutTests.Render((FrameworkElement)dialog.Content, null, output, "login-runtime-compact-" + (int)(scale * 100), 400, 300, scale);
                    detect.BringIntoView(); Pump();
                    Assert(panel.ScrollableHeight > 0, "short work area clips the runtime actions instead of scrolling");
                }
                install.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                Assert(opened == QqLoginDialog.RuntimeDownloadAddress, "download action did not open the runtime page");
                available = true;
                detect.RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); Pump();
                Assert(detections == 2 && creations == 1 && panel.Visibility == Visibility.Collapsed,
                    "successful re-detection did not enter the provider surface");
                Assert(reload.Visibility == Visibility.Visible, "provider surface lost its reload overlay");
            }
            finally { dialog.Close(); }
            var failure = new QqLoginDialog(() => "TEST_RUNTIME",
                () => Task.FromException(new System.IO.IOException("TEST_NETWORK")), address => { throw new Exception("unexpected download"); });
            try
            {
                RunTask(failure.InitializeBrowserAsync());
                Assert(((Button)failure.FindName("InstallRuntime")).Visibility == Visibility.Collapsed,
                    "generic initialization failure was misreported as a missing component");
                LauncherLayoutTests.Render((FrameworkElement)failure.Content, null, output, "login-load-error", 636, 378);
            }
            finally { failure.Close(); }
            var pending = new TaskCompletionSource<bool>();
            int attempts = 0;
            var delayed = new QqLoginDialog(() => "TEST_RUNTIME", () => { attempts++; return pending.Task; }, null);
            var first = delayed.InitializeBrowserAsync();
            RunTask(delayed.InitializeBrowserAsync());
            Assert(attempts == 1, "repeated detection created duplicate WebViews");
            delayed.Close();
            pending.SetResult(true);
            RunTask(first);
            Assert(((Grid)delayed.FindName("BrowserHost")).Children.Count == 0, "late callback recreated a closed WebView");
        }
    }
}
