using System;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Input;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.Wpf;
using T7R.Desktop.Services;

namespace T7R.Desktop.Views
{
    public partial class QqLoginDialog : Window
    {
        internal const string LoginAddress = "https://xui.ptlogin2.qq.com/cgi-bin/xlogin?daid=377&style=11&appid=716027613&target=self&s_url=https%3A%2F%2Fconnect.qq.com%2Flogin_success.html&hide_close_icon=1";
        internal const string VerifyAddress = "https://cgi.connect.qq.com/qqconnectopen/openapi/get_nick";
        internal const string RuntimeDownloadAddress = "https://developer.microsoft.com/microsoft-edge/webview2/";
        private readonly WindowLayoutController _layout;
        private readonly Func<string> _detectRuntime;
        private readonly Func<Task> _initializeBrowser;
        private readonly Action<string> _openAddress;
        private readonly string _loginAddress;
        private WebView2CompositionControl _browser;
        private bool _closed, _reading, _initializing;
        private ulong _navigation;
        private string _cookie;

        public QqLoginDialog() : this(() => CoreWebView2Environment.GetAvailableBrowserVersionString(), null, null) { }

        internal QqLoginDialog(Func<string> detectRuntime, Func<Task> initializeBrowser, Action<string> openAddress,
            string loginAddress = LoginAddress)
        {
            InitializeComponent();
            _detectRuntime = detectRuntime;
            _initializeBrowser = initializeBrowser ?? CreateBrowserAsync;
            _openAddress = openAddress ?? new DesktopInteraction().OpenAddress;
            _loginAddress = loginAddress;
            _layout = new WindowLayoutController(this, Report, Width, Height, sizeIncrement: 1);
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

        private async void OnLoaded(object sender, RoutedEventArgs e) => await InitializeBrowserAsync();
        private async void OnDetectRuntime(object sender, RoutedEventArgs e) => await InitializeBrowserAsync();

        internal async Task InitializeBrowserAsync()
        {
            if (_closed || _initializing) return;
            _initializing = true;
            DetectRuntime.IsEnabled = ReloadButton.IsEnabled = false;
            try
            {
                if (string.IsNullOrWhiteSpace(_detectRuntime())) { ShowRuntimeUnavailable(); return; }
                await _initializeBrowser();
                if (_closed) return;
                RuntimePanel.Visibility = LoadingLabel.Visibility = Visibility.Collapsed;
                BrowserHost.Visibility = ReloadButton.Visibility = Visibility.Visible;
                ReloadButton.IsEnabled = true;
            }
            catch (WebView2RuntimeNotFoundException) { if (!_closed) ShowRuntimeUnavailable(); }
            catch (Exception error) { if (!_closed) Report(error); }
            finally
            {
                _initializing = false;
                if (!_closed) DetectRuntime.IsEnabled = true;
            }
        }

        private async Task CreateBrowserAsync()
        {
            ReleaseBrowser();
            var browser = new WebView2CompositionControl
            {
                CreationProperties = new CoreWebView2CreationProperties
                {
                    UserDataFolder = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                        "T7-Rekindle", "qq-webview"),
                    ProfileName = "QqLogin",
                    IsInPrivateModeEnabled = true
                }
            };
            _browser = browser;
            BrowserHost.Children.Add(browser);
            await browser.EnsureCoreWebView2Async();
            if (_closed) return;
            browser.CoreWebView2.Settings.IsStatusBarEnabled = false;
            browser.CoreWebView2.NewWindowRequested += (_, args) => args.Handled = true;
            browser.CoreWebView2.DownloadStarting += (_, args) => args.Cancel = true;
            browser.CoreWebView2.NavigationStarting += (_, args) =>
            {
                _navigation = args.NavigationId;
                _reading = false;
                ReloadButton.IsEnabled = false;
            };
            browser.CoreWebView2.NavigationCompleted += OnNavigationCompleted;
            browser.CoreWebView2.Navigate(_loginAddress);
        }

        private async void OnNavigationCompleted(object sender, CoreWebView2NavigationCompletedEventArgs e)
        {
            if (_closed || _reading || e.NavigationId != _navigation || !ReferenceEquals(sender, _browser?.CoreWebView2)) return;
            ReloadButton.IsEnabled = true;
            if (!e.IsSuccess)
            {
                ShowLoadError("登录页面加载失败", "请检查网络连接，然后重新加载 QQ 登录页。");
                return;
            }
            if (!Uri.TryCreate(_browser.Source?.AbsoluteUri, UriKind.Absolute, out var address)
                || address.Scheme != "https" || address.Host != "connect.qq.com" || address.AbsolutePath != "/login_success.html") return;
            _reading = true;
            ReloadButton.IsEnabled = false;
            try
            {
                var cookies = await _browser.CoreWebView2.CookieManager.GetCookiesAsync(VerifyAddress);
                if (_closed || e.NavigationId != _navigation) return;
                var scoped = cookies.GroupBy(cookie => cookie.Name)
                    .Select(group => group.OrderByDescending(cookie => cookie.Domain.TrimStart('.').Length).First()).ToArray();
                if (!scoped.Any(cookie => cookie.Name == "uin")) throw new InvalidOperationException("QQ 登录状态尚未就绪。");
                _cookie = string.Join("; ", scoped.Select(cookie => cookie.Name + "=" + cookie.Value));
                DialogResult = true;
            }
            catch (Exception error) { if (!_closed && e.NavigationId == _navigation) { _reading = false; Report(error); } }
        }

        private void OnRefresh(object sender, RoutedEventArgs e)
        {
            if (_closed || _initializing || _reading || _browser?.CoreWebView2 == null) return;
            try { _browser.Reload(); }
            catch (Exception error) { Report(error); }
        }

        private void ShowRuntimeUnavailable()
        {
            ReleaseBrowser();
            ShowNativePanel();
            RuntimeTitle.Text = "暂时无法打开 QQ 登录页";
            RuntimeDescription.Text = "未检测到可用的网页组件。请安装适用组件后，点击「重新检测」。";
            RuntimeHelp.Text = "组件下载页将在系统浏览器中打开。";
            InstallRuntime.Visibility = Visibility.Visible;
            DetectLabel.Text = "重新检测";
            System.Windows.Automation.AutomationProperties.SetName(DetectRuntime, "重新检测");
        }

        private void ShowLoadError(string title, string description)
        {
            ShowNativePanel();
            RuntimeTitle.Text = title;
            RuntimeDescription.Text = description;
            RuntimeHelp.Text = "也可以取消本次登录，稍后再试。";
            InstallRuntime.Visibility = Visibility.Collapsed;
            DetectLabel.Text = "重新加载";
            System.Windows.Automation.AutomationProperties.SetName(DetectRuntime, "重新加载登录页");
        }

        private void ShowNativePanel()
        {
            RuntimePanel.Visibility = Visibility.Visible;
            BrowserHost.Visibility = LoadingLabel.Visibility = ReloadButton.Visibility = Visibility.Collapsed;
        }

        private void OnInstallRuntime(object sender, RoutedEventArgs e)
        {
            try { _openAddress(RuntimeDownloadAddress); }
            catch (Exception error)
            {
                new LogService().Error("打开 WebView2 下载页面失败（" + error.GetType().Name + "）", null);
                RuntimeHelp.Text = "下载页未打开。请在系统浏览器中访问：" + RuntimeDownloadAddress;
            }
        }
        private void Report(Exception error)
        {
            new LogService().Error("QQ 登录窗口加载失败（" + error.GetType().Name + "）", null);
            ShowLoadError("QQ 登录页面暂未就绪", "请重新加载登录页；若仍有问题，请关闭窗口后重试。");
        }
        private void OnKeyDown(object sender, KeyEventArgs e)
        {
            if (e.Key != Key.Escape) return;
            e.Handled = true;
            Close();
        }
        private void OnCancel(object sender, RoutedEventArgs e) => Close();
        private void ReleaseBrowser()
        {
            if (_browser == null) return;
            _browser.Dispose();
            BrowserHost.Children.Clear();
            _browser = null;
        }
        private void OnClosed(object sender, EventArgs e)
        {
            _closed = true;
            _layout.Dispose();
            ReleaseBrowser();
        }
    }
}
