using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using T7R.Core;
using T7R.Desktop.Services;

namespace T7R.Desktop.ViewModels
{
    public sealed partial class AccountViewModel
    {
        private readonly SemaphoreSlim _titleRequests = new SemaphoreSlim(1, 1);
        private AccountTitles _titles = AccountTitles.Empty;
        private string _titleStatus = "上线后可查看称号";
        private bool _titleBusy;
        public IReadOnlyList<AccountTitle> Titles => IsOnline ? _titles.Titles : AccountTitles.Empty.Titles;
        public string EquippedTitle => IsOnline ? _titles.EquippedName : "离线时隐藏称号";
        public string TitleStatus => IsOnline ? _titleStatus : "上线后可查看称号";
        public bool CanChangeTitle => IsOnline && !_titleBusy && _api is IAccountTitlesApi;
        internal int TitleGeneration => _generation;
        internal AccountTitles TitleSnapshot => IsOnline ? _titles : AccountTitles.Empty;

        internal Task RefreshTitlesAsync() => ChangeTitleAsync(null, false, _generation);
        public Task<bool> EquipTitleAsync(int? titleId) => ChangeTitleAsync(titleId, true, _generation);
        internal Task<bool> EquipTitleAsync(int? titleId, int generation) => ChangeTitleAsync(titleId, true, generation);

        private async Task<bool> ChangeTitleAsync(int? titleId, bool change, int generation)
        {
            if (!IsCurrent(generation) || !IsOnline || !(_api is IAccountTitlesApi api)) return false;
            var cancellation = _lifetime.Token;
            var session = _session;
            var connection = _connection.ConnectionId;
            bool entered = false;
            try
            {
                if (change) { await _titleRequests.WaitAsync(cancellation).ConfigureAwait(true); entered = true; }
                else entered = await _titleRequests.WaitAsync(0, cancellation).ConfigureAwait(true);
                if (!entered || !IsCurrent(generation) || !IsOnline || _connection.ConnectionId != connection) return false;
                _titleBusy = true; OnPropertyChanged(nameof(CanChangeTitle));
                var snapshot = change
                    ? await api.EquipTitleAsync(session, connection, titleId, cancellation).ConfigureAwait(true)
                    : await api.TitlesAsync(session, connection, cancellation).ConfigureAwait(true);
                if (!IsCurrent(generation) || !IsOnline || _connection.ConnectionId != connection) return false;
                _titles = snapshot ?? throw new System.IO.InvalidDataException("称号响应为空。");
                _titleStatus = change ? (titleId.HasValue ? "称号已佩戴" : "称号已卸下") : "称号已同步";
                OnPropertyChanged(string.Empty);
                return true;
            }
            catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { return false; }
            catch (Exception error)
            {
                if (IsCurrent(generation))
                {
                    HandleTerminal(error);
                    _titleStatus = change ? "称号变更未完成，请重试" : "称号同步暂未完成";
                    Record(DiagnosticLogLevel.Warn, _titleStatus, error);
                    OnPropertyChanged(string.Empty);
                }
                return false;
            }
            finally
            {
                if (entered) { _titleBusy = false; _titleRequests.Release(); OnPropertyChanged(nameof(CanChangeTitle)); }
            }
        }
    }
}
