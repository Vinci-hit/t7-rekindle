using System;
using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Automation;
using T7R.Desktop.ViewModels;
using T7R.Desktop.Views;
using static T7R.ManagedHarness.LauncherTests;

namespace T7R.ManagedHarness
{
    internal static class PreviewAccessUiTests
    {
        internal static void RunStandalone(string directory, string output)
        {
            var app = new T7R.Desktop.App(false);
            app.InitializeComponent();
            app.ShutdownMode = ShutdownMode.OnExplicitShutdown;
            try
            {
                Run(directory, output);
                foreach (var name in new[] { "Dark", "HighContrast" })
                {
                    var theme = new ResourceDictionary
                    {
                        Source = new Uri("/T7-Rekindle;component/Resources/Theme." + name + ".xaml", UriKind.Relative)
                    };
                    app.Resources.MergedDictionaries.Add(theme);
                    try { Run(Path.Combine(directory, name), output == null ? null : Path.Combine(output, name)); }
                    finally { app.Resources.MergedDictionaries.Remove(theme); }
                }
            }
            finally { app.Shutdown(); }
        }

        internal static void Run(string directory, string output)
        {
            using (var fixture = new PreviewAccessFixture())
            {
                var access = fixture.Create(Path.Combine(directory, "preview-dialog"));
                var copied = string.Empty;
                var model = new PreviewAccessViewModel(access, value => copied = value);
                var dialog = new PreviewAccessDialog(model);
                try
                {
                    var root = (FrameworkElement)dialog.Content;
                    LauncherLayoutTests.Render(root, null, output, "preview-access", 624, 641);
                    var cancel = (Button)dialog.FindName("CancelButton");
                    var authorize = (Button)dialog.FindName("AuthorizeButton");
                    var request = (TextBox)dialog.FindName("RequestCodeBox");
                    var input = (TextBox)dialog.FindName("GrantCodeBox");
                    Assert(cancel.IsCancel && cancel.IsDefault && !authorize.IsDefault && !authorize.IsEnabled,
                        "preview authorization did not default to cancellation");
                    Assert(request.IsReadOnly && request.Text == access.RequestCode && input.MaxLength == 4096
                        && AutomationProperties.GetName(input) == "预览授权码", "authorization controls lost request/binding/accessibility");
                    model.CopyRequestCommand.Execute(null);
                    Assert(copied == access.RequestCode && model.Feedback.Length != 0, "request copy did not provide feedback");
                    Assert(model.Notices.Visible.Count == 1 && model.Notices.Visible[0].Severity == NoticeSeverity.Success
                        && model.Notices.Visible[0].Message == model.Feedback, "request copy did not publish a success toast");
                    model.CopyRequestCommand.Execute(null);
                    Assert(model.Notices.Visible.Count == 1, "repeated request copies duplicated the toast");
                    var host = (ToastHost)dialog.FindName("CopyToastHost");
                    var notices = (ItemsControl)host.Content;
                    LauncherLayoutTests.Render(root, null, output, "preview-access-copied", 624, 641);
                    Assert(ReferenceEquals(notices.ItemsSource, model.Notices.Visible) && notices.Items.Count == 1,
                        "copy toast is not displayed inside the authorization dialog");
                    LauncherLayoutTests.Render(root, null, output, "preview-access-copied-small-200", 404, 361, 2);
                    LauncherLayoutTests.AssertWithin(host, root, root.ActualWidth, root.ActualHeight);
                    WaitUntil(() => model.Notices.Visible.Count == 0);
                    Pump();
                    Assert(notices.Items.Count == 0, "copy toast did not automatically disappear");
                    input.Text = "invalid";
                    input.GetBindingExpression(TextBox.TextProperty).UpdateSource();
                    model.AuthorizeCommand.Execute(null);
                    Pump();
                    Assert(model.Error.Length != 0 && !access.IsAuthorized && !model.IsAccepted,
                        "invalid UI grant was accepted or error was hidden");
                    LauncherLayoutTests.Render(root, null, output, "preview-access-error", 624, 641);
                    LauncherLayoutTests.Render(root, null, output, "preview-access-small-200", 404, 361, 2);
                    Assert(cancel.ActualHeight >= 36 && authorize.ActualHeight >= 36, "small authorization dialog lost its actions");
                }
                finally { dialog.Close(); }
                Assert(!access.IsAuthorized, "closing the authorization dialog granted access");
                var copyFailure = new PreviewAccessViewModel(access, _ => throw new InvalidOperationException("fixture"));
                copyFailure.CopyRequestCommand.Execute(null);
                Assert(copyFailure.Error.Contains("复制") && copyFailure.Notices.Visible.Count == 0,
                    "clipboard error was swallowed or reported as a successful copy");
            }
        }
    }
}
