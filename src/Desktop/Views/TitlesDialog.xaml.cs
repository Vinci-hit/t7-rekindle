using System.Windows;
using System.Windows.Controls;
using T7R.Desktop.ViewModels;

namespace T7R.Desktop.Views
{
    public partial class TitlesDialog : Window
    {
        private AccountViewModel Account => (AccountViewModel)DataContext;
        public TitlesDialog(AccountViewModel account) { InitializeComponent(); DataContext = account; }
        private async void OnEquip(object sender, RoutedEventArgs e)
        {
            if (Account.CanChangeTitle) await Account.EquipTitleAsync((int)((Button)sender).Tag);
        }
        private async void OnUnequip(object sender, RoutedEventArgs e)
        {
            if (Account.CanChangeTitle) await Account.EquipTitleAsync(null);
        }
    }
}
