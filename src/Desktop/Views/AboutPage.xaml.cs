using System.Windows;
using System.Windows.Controls;

namespace T7R.Desktop.Views
{
    public partial class AboutPage : UserControl
    {
        private bool? _compact;

        public AboutPage() { InitializeComponent(); }

        private void OnContentSizeChanged(object sender, SizeChangedEventArgs e)
        {
            if (e.NewSize.Width <= 0) return;
            var compact = e.NewSize.Width < 720;
            if (_compact == compact) return;
            _compact = compact;

            ClientSupportGrid.ColumnDefinitions[1].Width = compact ? new GridLength(0) : new GridLength(1, GridUnitType.Star);
            ClientSupportGrid.ColumnDefinitions[2].Width = compact ? new GridLength(0) : new GridLength(1, GridUnitType.Star);
            Grid.SetColumnSpan(ClientDownloadButton, compact ? 1 : 3);
            EnvironmentInfoButton.Margin = compact ? new Thickness(0, 12, 0, 0) : new Thickness(0, 16, 8, 0);
            Grid.SetColumn(LogDirectoryButton, compact ? 0 : 1);
            Grid.SetRow(LogDirectoryButton, compact ? 2 : 1);
            LogDirectoryButton.Margin = compact ? new Thickness(0, 12, 0, 0) : new Thickness(4, 16, 4, 0);
            Grid.SetColumn(IssuesButton, compact ? 0 : 2);
            Grid.SetRow(IssuesButton, compact ? 3 : 1);
            IssuesButton.Margin = compact ? new Thickness(0, 12, 0, 0) : new Thickness(8, 16, 0, 0);

            ProjectLinksGrid.Columns = compact ? 1 : 3;
            RepositoryButton.Margin = compact ? new Thickness(0, 0, 0, 12) : new Thickness(0, 0, 8, 0);
            LicensesButton.Margin = compact ? new Thickness(0, 0, 0, 12) : new Thickness(4, 0, 4, 0);
            ThanksButton.Margin = compact ? new Thickness(0, 0, 0, 12) : new Thickness(8, 0, 0, 0);
        }
    }
}
