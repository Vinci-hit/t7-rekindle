using System.Windows;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;

namespace T7.Rekindle.Desktop.Views
{
    public partial class PreviewAccessDialog : Window
    {
        private readonly WindowLayoutController _layout;
        internal PreviewAccessDialog(PreviewAccessViewModel model)
        {
            InitializeComponent();
            DataContext = model;
            model.Accepted += OnAccepted;
            _layout = new WindowLayoutController(this, error => new LogService().Error("调整预览授权窗口失败", error), Width, Height);
            Closed += (_, __) => { model.Accepted -= OnAccepted; _layout.Dispose(); };
        }
        private void OnAccepted() { DialogResult = true; }
    }
}
