using System;
using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Automation;
using T7.Rekindle.Desktop.ViewModels;
using T7.Rekindle.Desktop.Views;
using static T7.ManagedHarness.LauncherTests;

namespace T7.ManagedHarness
{
    internal static class PreviewAccessUiTests
    {
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
                Assert(copyFailure.Error.Contains("复制"), "clipboard error was swallowed");
            }
        }
    }
}
