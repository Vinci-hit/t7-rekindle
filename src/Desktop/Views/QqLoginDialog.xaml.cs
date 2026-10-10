using System;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.Wpf;
using T7.Rekindle.Desktop.Services;

namespace T7.Rekindle.Desktop.Views
{
    public partial class QqLoginDialog : Window
    {
        internal const string LoginAddress = "https://xui.ptlogin2.qq.com/cgi-bin/xlogin?daid=377&style=11&appid=716027613&target=self&s_url=https%3A%2F%2Fconnect.qq.com%2Flogin_success.html&hide_close_icon=1";
        internal const string VerifyAddress = "https://cgi.connect.qq.com/qqconnectopen/openapi/get_nick";
        private readonly WindowLayoutController _layout;
        private bool _closed, _reading;
        private string _cookie;

        public QqLoginDialog()
        {
            InitializeComponent();
            _layout = new WindowLayoutController(this, error => Report(error), Width, Height);
        }

        internal static Task<string> ScanAsync(CancellationToken cancellation)
        {
            cancellation.ThrowIfCancellationRequested();
            var dialog = new QqLoginDialog();
            using (cancellation.Register(() => dialog.Dispatcher.BeginInvoke(new Action(() => { if (!dialog._closed) dialog.Close(); }))))
                DesktopInteraction.ShowModal(dialog);
            string cookie = dialog._cookie;
            dialog._cookie = null;
            cancellation.ThrowIfCancellationRequested();
            return Task.FromResult(cookie);
        }

        private async void OnLoaded(object sender, RoutedEventArgs e)
        {
            try
            {
                CoreWebView2Environment.GetAvailableBrowserVersionString();
                Browser.CreationProperties = new CoreWebView2CreationProperties
                {
                    UserDataFolder = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                        "T7-Rekindle", "qq-webview"),
                    ProfileName = "QqLogin",
                    IsInPrivateModeEnabled = true
                };
                await Browser.EnsureCoreWebView2Async();
                if (_closed) return;
                Browser.CoreWebView2.Settings.IsStatusBarEnabled = false;
                Browser.CoreWebView2.NewWindowRequested += (_, args) => args.Handled = true;
                Browser.CoreWebView2.DownloadStarting += (_, args) => args.Cancel = true;
                Browser.CoreWebView2.NavigationCompleted += OnNavigationCompleted;
                Browser.CoreWebView2.Navigate(LoginAddress);
            }
            catch (WebView2RuntimeNotFoundException)
            {
                if (_closed) return;
                Browser.Visibility = Visibility.Collapsed;
                Feedback.Text = "扫码登录需要 Microsoft Edge WebView2 Runtime。安装后重新打开登录窗口。";
                InstallRuntime.Visibility = Visibility.Visible;
            }
            catch (Exception error) { if (!_closed) Report(error); }
        }

        private async void OnNavigationCompleted(object sender, CoreWebView2NavigationCompletedEventArgs e)
        {
            if (_closed || _reading) return;
            if (!e.IsSuccess) { Feedback.Text = "登录页面加载失败，请刷新重试。"; Feedback.Visibility = Visibility.Visible; return; }
            Feedback.Visibility = Visibility.Collapsed;
            if (!Uri.TryCreate(Browser.Source?.AbsoluteUri, UriKind.Absolute, out var address)
                || address.Scheme != "https" || address.Host != "connect.qq.com" || address.AbsolutePath != "/login_success.html") return;
            _reading = true;
            try
            {
                var cookies = await Browser.CoreWebView2.CookieManager.GetCookiesAsync(VerifyAddress);
                if (_closed) return;
                var scoped = cookies.GroupBy(cookie => cookie.Name)
                    .Select(group => group.OrderByDescending(cookie => cookie.Domain.TrimStart('.').Length).First()).ToArray();
                if (!scoped.Any(cookie => cookie.Name == "uin")) throw new InvalidOperationException("QQ 登录状态尚未就绪。");
                _cookie = string.Join("; ", scoped.Select(cookie => cookie.Name + "=" + cookie.Value));
                DialogResult = true;
            }
            catch (Exception error) { if (!_closed) { _reading = false; Report(error); } }
        }

        private void OnRefresh(object sender, RoutedEventArgs e)
        {
            if (Browser.CoreWebView2 == null) return;
            try
            {
                _reading = false;
                Feedback.Visibility = Visibility.Collapsed;
                Browser.CoreWebView2.Navigate(LoginAddress);
            }
            catch (Exception error) { Report(error); }
        }
        private void OnInstallRuntime(object sender, RoutedEventArgs e)
        {
            try { new DesktopInteraction().OpenAddress("https://developer.microsoft.com/microsoft-edge/webview2/"); }
            catch (Exception error)
            {
                new LogService().Info("打开 WebView2 下载页面失败（" + error.GetType().Name + "）");
                Feedback.Text = "请在浏览器打开 https://developer.microsoft.com/microsoft-edge/webview2/ 安装 Runtime。";
                Feedback.Visibility = Visibility.Visible;
            }
        }
        private void Report(Exception error)
        {
            new LogService().Info("QQ 登录窗口加载失败（" + error.GetType().Name + "）");
            Feedback.Text = "QQ 登录页面暂未就绪，请刷新或重新打开登录窗口。";
            Feedback.Visibility = Visibility.Visible;
        }
        private void OnClosed(object sender, EventArgs e)
        {
            _closed = true;
            _layout.Dispose();
            Browser.Dispose();
        }
    }
}
