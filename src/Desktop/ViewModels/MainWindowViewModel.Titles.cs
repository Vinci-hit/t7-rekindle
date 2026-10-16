using System;
using System.Linq;
using System.Threading.Tasks;
using Newtonsoft.Json;
using T7R.Core;
using T7R.Desktop.Services;

namespace T7R.Desktop.ViewModels
{
    public sealed partial class MainWindowViewModel
    {
        private ulong _titleEpoch = 1, _handledTitleRequest;
        private int _titleAccountGeneration = -1;
        private bool _titleOnline, _titleGameRunning, _applyingGameTitle;
        private string _publishedTitles;

        private string TitlesJson() => JsonConvert.SerializeObject(new {
            titles = Account.TitleSnapshot.Titles.Select(t => new { titleId = t.TitleId, name = t.Name,
                description = t.Description, rule = t.Rule, enabled = t.Enabled, owned = t.Owned }).ToArray(),
            equippedTitleId = Account.TitleSnapshot.EquippedTitleId
        });

        private void PollTitles()
        {
            if (!(_bridge is INativeTitles native)) return;
            bool running = _snapshot.State == SessionState.Running && _snapshot.Operation == OperationKind.None;
            if (_titleGameRunning != running || _titleAccountGeneration != Account.TitleGeneration || _titleOnline != Account.IsOnline)
            {
                ++_titleEpoch; _publishedTitles = null; _handledTitleRequest = 0;
                _titleGameRunning = running; _titleOnline = Account.IsOnline; _titleAccountGeneration = Account.TitleGeneration;
            }
            if (!running) return;
            try
            {
                string json = TitlesJson();
                if (_publishedTitles != json) { native.PublishTitles(_titleEpoch, json); _publishedTitles = json; }
                var request = native.ReadTitleRequest();
                if (!_applyingGameTitle && request.RequestId != 0 && request.RequestId != _handledTitleRequest && request.Epoch == _titleEpoch)
                {
                    _handledTitleRequest = request.RequestId;
                    _ = ApplyGameTitleAsync(native, request, Account.TitleGeneration);
                }
            }
            catch (Exception error) { AppendLog(DiagnosticLogLevel.Warn, "游戏称号同步未完成：" + error.Message, error); }
        }
        private async Task ApplyGameTitleAsync(INativeTitles native, NativeTitleRequest request, int generation)
        {
            _applyingGameTitle = true;
            try
            {
                bool success = request.TitleId <= int.MaxValue && await Account.EquipTitleAsync(
                    request.TitleId == 0 ? (int?)null : (int)request.TitleId, generation);
                if (_disposed || request.Epoch != _titleEpoch || !_titleGameRunning) return;
                string json = TitlesJson();
                native.PublishTitles(_titleEpoch, json, request.RequestId, success);
                _publishedTitles = json;
            }
            catch (Exception error) { AppendLog(DiagnosticLogLevel.Warn, "游戏称号变更未完成：" + error.Message, error); }
            finally { _applyingGameTitle = false; }
        }
    }
}
