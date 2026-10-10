using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;

namespace T7.Rekindle.Desktop.Views
{
    public partial class AccountPanel : UserControl
    {
        public AccountPanel() { InitializeComponent(); }
        private void OnAvatarFailed(object sender, ExceptionRoutedEventArgs e) => ((Image)sender).Visibility = Visibility.Hidden;
        private void OnAvatarChanged(object sender, DataTransferEventArgs e) => ((Image)sender).Visibility = Visibility.Visible;
    }
}
