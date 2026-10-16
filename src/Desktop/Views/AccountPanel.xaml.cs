using System;
using System.ComponentModel;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Data;
using System.Windows.Input;
using T7R.Desktop.ViewModels;

namespace T7R.Desktop.Views
{
    public partial class AccountPanel : UserControl
    {
        private bool _synchronizing;
        private AccountViewModel Account => DataContext as AccountViewModel;

        public AccountPanel()
        {
            InitializeComponent();
            AccountPopup.CustomPopupPlacementCallback = PlaceMenu;
        }

        internal static CustomPopupPlacement[] PlaceMenu(Size popup, Size target, Point offset) =>
            new[] { new CustomPopupPlacement(new Point(target.Width - popup.Width + 30, target.Height), PopupPrimaryAxis.Horizontal) };

        private void OnAccountChanged(object sender, DependencyPropertyChangedEventArgs e)
        {
            if (e.OldValue is AccountViewModel previous)
                PropertyChangedEventManager.RemoveHandler(previous, OnAccountPropertyChanged, string.Empty);
            if (e.NewValue is AccountViewModel current)
                PropertyChangedEventManager.AddHandler(current, OnAccountPropertyChanged, string.Empty);
            UpdateAccount();
        }

        private void OnAccountPropertyChanged(object sender, PropertyChangedEventArgs e) => UpdateAccount();
        private void UpdateAccount()
        {
            if (AccountToggle == null) return;
            if (Account?.IsSignedIn != true) AccountPopup.IsOpen = false;
            _synchronizing = true;
            try { AccountToggle.IsChecked = AccountPopup.IsOpen || Account?.IsLoginOpen == true; }
            finally { _synchronizing = false; }
            string action = Account?.IsSignedIn == true
                ? (AccountPopup.IsOpen ? "，收起账号菜单" : "，打开账号菜单") : string.Empty;
            AutomationProperties.SetName(AccountToggle, (Account?.AccountHint ?? "未登录，打开 QQ 登录页") + action);
        }

        private void OnAccountUnchecked(object sender, RoutedEventArgs e)
        {
            if (_synchronizing) return;
            AccountPopup.IsOpen = false;
            UpdateAccount();
        }
        private async void OnAccountChecked(object sender, RoutedEventArgs e)
        {
            if (_synchronizing) return;
            if (Account == null) { UpdateAccount(); return; }
            if (Account.IsSignedIn)
            {
                AccountPopup.IsOpen = true;
                return;
            }
            UpdateAccount();
            if (!Account.LoginCommand.CanExecute(null)) return;
            await Account.LoginCommand.ExecuteAsync(null);
            UpdateAccount();
            AccountToggle.Focus();
            if (!Account.IsSignedIn && Account.Status != "未登录"
                && Window.GetWindow(this)?.DataContext is MainWindowViewModel main)
                main.Notices.Publish("account-login", Account.Status, NoticeSeverity.Error);
        }

        private void OnMenuOpened(object sender, EventArgs e)
        {
            UpdateAccount();
            LogoutButton.Focus();
        }
        private void OnMenuClosed(object sender, EventArgs e) => UpdateAccount();
        private void OnMenuKeyDown(object sender, KeyEventArgs e)
        {
            if (e.Key != Key.Escape) return;
            e.Handled = true;
            AccountPopup.IsOpen = false;
            AccountToggle.Focus();
        }
        private async void OnLogout(object sender, RoutedEventArgs e)
        {
            var account = Account;
            AccountPopup.IsOpen = false;
            if (account?.LogoutCommand.CanExecute(null) == true) await account.LogoutCommand.ExecuteAsync(null);
            AccountToggle.Focus();
        }
        private void OnTitles(object sender, RoutedEventArgs e)
        {
            AccountPopup.IsOpen = false;
            if (Account == null) return;
            new TitlesDialog(Account) { Owner = Window.GetWindow(this) }.ShowDialog();
        }
        private void OnAvatarFailed(object sender, ExceptionRoutedEventArgs e) => ((Image)sender).Visibility = Visibility.Hidden;
        private void OnAvatarChanged(object sender, DataTransferEventArgs e) => ((Image)sender).Visibility = Visibility.Visible;
    }
}
