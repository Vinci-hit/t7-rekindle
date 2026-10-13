using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security;
using System.Security.Cryptography;
using System.Security.Principal;
using Newtonsoft.Json;

namespace T7.Rekindle.Desktop.Services
{
    internal sealed class PreviewAccessService
    {
        private readonly PreviewAccessStore _store;
        private readonly PreviewGrantVerifier _verifier;
        private readonly Func<DateTimeOffset> _utcNow;
        private readonly string _subject;
        private readonly string _configurationError = string.Empty;
        private PreviewAccessState _state = new PreviewAccessState(0, string.Empty);
        private PreviewGrant _grant;
        internal string Warning { get; private set; } = string.Empty;

        internal PreviewAccessService(string directory, string publicKey = null, Func<string> readSubject = null,
            Func<DateTimeOffset> utcNow = null)
        {
            _store = new PreviewAccessStore(directory);
            _utcNow = utcNow ?? (() => DateTimeOffset.UtcNow);
            try
            {
                _subject = (readSubject ?? ReadSubject)();
                if (PreviewGrantVerifier.Decode(_subject).Length != 32) throw new FormatException();
                var key = publicKey ?? BuiltInPublicKey;
                if (string.IsNullOrWhiteSpace(key)) _configurationError = "此构建尚未配置预览授权公钥，请使用正式渠道。";
                else _verifier = new PreviewGrantVerifier(key);
            }
            catch (Exception error) when (IsAccessError(error) || error is InvalidOperationException)
            { _configurationError = "读取预览公钥或本机用户标识失败，请使用正式渠道。"; }
            try
            {
                _state = _store.Load();
                if (_state.GrantCode.Length != 0 && CanAuthorize) _grant = _verifier.Verify(_state.GrantCode, _subject);
            }
            catch (Exception error) when (IsAccessError(error))
            { Warning = "预览授权数据无效，需重新申请或导入授权。"; }
        }

        private static string ReadSubject()
        {
            using (var identity = WindowsIdentity.GetCurrent())
                return PreviewGrantVerifier.CreateSubject(AccountSessionStore.ReadMachineId(), identity.User?.Value);
        }

        internal static string BuiltInPublicKey => typeof(PreviewAccessService).Assembly.GetCustomAttributes<AssemblyMetadataAttribute>()
            .FirstOrDefault(attribute => attribute.Key == "PreviewAccessPublicKey")?.Value ?? string.Empty;

        internal bool CanAuthorize => _configurationError.Length == 0 && _verifier != null && _subject != null;
        internal bool IsAuthorized => CanAuthorize && _grant?.IsValidAt(_utcNow()) == true;
        internal bool MigrationRequired => _state.PolicyVersion != PreviewGrantVerifier.PolicyVersion;
        internal DateTimeOffset? ExpiresAt => _grant?.ExpiresAt;
        internal string RequestCode => _subject == null ? string.Empty : "T7PR1." + _subject;
        internal string StatusText => !CanAuthorize ? _configurationError : _grant == null ? "预览渠道需管理员离线授权，有效期为签发后 7 天。"
            : _utcNow() >= _grant.ExpiresAt ? "预览授权已到期，请向管理员重新申请。"
            : !IsAuthorized ? "授权尚未生效，请检查本机日期和时间。"
            : "预览授权有效至 " + _grant.ExpiresAt.ToLocalTime().ToString("yyyy-MM-dd HH:mm:ss") + "（本地时间）";

        internal bool TryImport(string code, out string error)
        {
            error = string.Empty;
            if (!CanAuthorize) { error = StatusText; return false; }
            PreviewGrant grant;
            try
            {
                grant = _verifier.Verify(code, _subject);
                if (!grant.IsValidAt(_utcNow()))
                { error = _utcNow() >= grant.ExpiresAt ? "预览授权已到期，请重新申请。" : "授权尚未生效，请检查本机日期和时间。"; return false; }
            }
            catch (InvalidDataException failure) { error = failure.Message; return false; }
            try
            {
                var state = new PreviewAccessState(PreviewGrantVerifier.PolicyVersion, grant.Code);
                _store.Save(state);
                _state = state;
                _grant = grant;
                Warning = string.Empty;
                return true;
            }
            catch (Exception failure) when (IsAccessError(failure))
            { error = "授权未保存，请检查个人配置目录权限后重试。"; return false; }
        }

        internal void CompleteMigration()
        {
            var state = new PreviewAccessState(PreviewGrantVerifier.PolicyVersion, _grant?.Code ?? string.Empty);
            _store.Save(state);
            _state = state;
        }

        internal UpdateChannel InitializeChannel(SettingsService settings, out string warning)
        {
            var channel = settings.LoadUpdateChannel(out warning);
            warning += Warning;
            if (!MigrationRequired && (channel != UpdateChannel.Preview || IsAuthorized)) return channel;
            try
            {
                settings.ResetUpdateChannelToStable();
                if (MigrationRequired) CompleteMigration();
                if (channel == UpdateChannel.Preview) warning += "预览渠道现需有效离线授权，已切换为正式渠道。";
            }
            catch (Exception error) when (IsAccessError(error))
            { warning += "预览门禁初始化未保存，当前使用正式渠道，下次启动重试。"; }
            return UpdateChannel.Stable;
        }

        private static bool IsAccessError(Exception error) => error is IOException || error is InvalidDataException || error is UnauthorizedAccessException
            || error is CryptographicException || error is FormatException || error is JsonException
            || error is ArgumentException || error is SecurityException || error is OverflowException;
    }
}
