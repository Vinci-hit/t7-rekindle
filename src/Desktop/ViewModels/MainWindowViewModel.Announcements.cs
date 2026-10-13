using System;
using System.Threading;
using System.Threading.Tasks;
using T7.Rekindle.Core;
using T7.Rekindle.Desktop.Services;

namespace T7.Rekindle.Desktop.ViewModels
{
    public sealed partial class MainWindowViewModel
    {
        private static readonly TimeSpan AnnouncementRefreshInterval = TimeSpan.FromSeconds(60);
        private readonly CancellationTokenSource _announcementCancellation = new CancellationTokenSource();
        private LauncherAnnouncement _announcement = LauncherAnnouncement.BuiltIn;
        private AnnouncementApi _announcementApi;
        private Func<CancellationToken, Task<LauncherAnnouncement>> _fetchAnnouncement;
        private Func<DateTime> _announcementUtcNow;
        private DateTime? _lastAnnouncementAttemptUtc;
        private bool _announcementChecksStarted;

        public string AnnouncementTitle => _announcement.Title;
        public string AnnouncementSummary => _announcement.Summary;
        internal Task AnnouncementTask { get; private set; } = Task.CompletedTask;

        private void InitializeAnnouncements(Func<CancellationToken, Task<LauncherAnnouncement>> fetch, Func<DateTime> utcNow)
        {
            _announcementUtcNow = utcNow ?? (() => DateTime.UtcNow);
            if (fetch != null) _fetchAnnouncement = fetch;
            else
            {
                _announcementApi = AnnouncementApi.CreateConfigured(LauncherInformation.AuthBaseAddress);
                _fetchAnnouncement = token => _announcementApi == null
                    ? Task.FromResult<LauncherAnnouncement>(null) : _announcementApi.GetLatestAsync(token);
            }
        }

        internal void StartAnnouncementChecks()
        {
            if (_disposed || _announcementChecksStarted) return;
            _announcementChecksStarted = true;
            _ = RefreshAnnouncementAsync();
        }

        internal Task RefreshAnnouncementAsync()
        {
            if (_disposed || !AnnouncementTask.IsCompleted) return AnnouncementTask;
            var now = _announcementUtcNow();
            if (_lastAnnouncementAttemptUtc.HasValue && now - _lastAnnouncementAttemptUtc.Value < AnnouncementRefreshInterval)
                return AnnouncementTask;
            _lastAnnouncementAttemptUtc = now;
            AnnouncementTask = LoadAnnouncementAsync(_announcementCancellation.Token);
            return AnnouncementTask;
        }

        private async Task LoadAnnouncementAsync(CancellationToken cancellation)
        {
            try
            {
                var announcement = await _fetchAnnouncement(cancellation);
                if (!_disposed) SetAnnouncement(announcement ?? LauncherAnnouncement.BuiltIn);
            }
            catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { }
            catch (Exception error)
            {
                if (_disposed) return;
                SetAnnouncement(LauncherAnnouncement.BuiltIn);
                _log.Write(DiagnosticLogLevel.Warn, "获取首页公告失败，使用内置公告", error);
            }
        }

        private void SetAnnouncement(LauncherAnnouncement value)
        {
            _announcement = value;
            OnPropertyChanged(nameof(AnnouncementTitle));
            OnPropertyChanged(nameof(AnnouncementSummary));
        }

        private void ShowAnnouncement()
        {
            var announcement = _announcement;
            try { _interaction.ShowMarkdown(announcement.Title, announcement.BodyMarkdown); }
            catch (Exception error) { ReportUiError("打开公告失败", error); }
        }
    }
}
