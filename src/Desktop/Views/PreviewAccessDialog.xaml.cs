using System;
using System.Windows;
using System.Windows.Threading;
using T7R.Desktop.Services;
using T7R.Desktop.ViewModels;

namespace T7R.Desktop.Views
{
    public partial class PreviewAccessDialog : Window
    {
        private readonly WindowLayoutController _layout;
        private readonly DispatcherTimer _noticeTimer;
        internal PreviewAccessDialog(PreviewAccessViewModel model)
        {
            InitializeComponent();
            DataContext = model;
            model.Accepted += OnAccepted;
            _layout = new WindowLayoutController(this, error => new LogService().Error("调整预览授权窗口失败", error), Width, Height);
            _noticeTimer = new DispatcherTimer(TimeSpan.FromMilliseconds(250), DispatcherPriority.Background,
                (_, __) => model.Notices.Tick(DateTime.UtcNow), Dispatcher);
            Activated += (_, __) => { model.Notices.Tick(DateTime.UtcNow); model.Notices.IsActive = true; };
            Deactivated += (_, __) => { model.Notices.Tick(DateTime.UtcNow); model.Notices.IsActive = false; };
            Closed += (_, __) => { _noticeTimer.Stop(); model.Accepted -= OnAccepted; _layout.Dispose(); };
        }
        private void OnAccepted() { DialogResult = true; }
    }
}
