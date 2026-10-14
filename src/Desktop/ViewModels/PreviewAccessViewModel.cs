using System;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using T7R.Desktop.Services;

namespace T7R.Desktop.ViewModels
{
    public sealed class PreviewAccessViewModel : ObservableObject
    {
        internal const string RiskDescription = "预览版包含未经充分验证的更改，可能出现崩溃、功能异常或兼容性问题。建议先备份重要配置与游戏数据。";
        private readonly PreviewAccessService _access;
        private string _grantCode = string.Empty;
        private string _error = string.Empty;
        private string _feedback = string.Empty;

        internal PreviewAccessViewModel(PreviewAccessService access, Action<string> copy)
        {
            _access = access;
            CopyRequestCommand = new RelayCommand(() =>
            {
                try
                {
                    copy(RequestCode);
                    Feedback = "申请码已复制，请发给管理员获取 7 天授权码。";
                    Notices.Publish("preview-access.request-copied", Feedback, NoticeSeverity.Success);
                }
                catch (Exception) { Error = "复制失败，请选中申请码后手动复制。"; }
            }, () => RequestCode.Length != 0);
            AuthorizeCommand = new RelayCommand(() =>
            {
                if (!_access.TryImport(GrantCode, out var error)) { Error = error; return; }
                Error = string.Empty;
                IsAccepted = true;
                Accepted?.Invoke();
            }, () => _access.CanAuthorize && !string.IsNullOrWhiteSpace(GrantCode));
        }

        internal event Action Accepted;
        internal bool IsAccepted { get; private set; }
        public string RiskText => RiskDescription;
        public string RequestCode => _access.RequestCode;
        public string StatusText => _access.StatusText;
        public string GrantCode
        {
            get => _grantCode;
            set { if (SetProperty(ref _grantCode, value ?? string.Empty)) AuthorizeCommand.NotifyCanExecuteChanged(); }
        }
        public string Error { get => _error; private set => SetProperty(ref _error, value); }
        public string Feedback { get => _feedback; private set => SetProperty(ref _feedback, value); }
        public NoticeCenter Notices { get; } = new NoticeCenter();
        public RelayCommand CopyRequestCommand { get; }
        public RelayCommand AuthorizeCommand { get; }
    }
}
