using System;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json.Linq;

namespace T7.Rekindle.Desktop.Services
{
    internal sealed class PreviewAccessState
    {
        internal PreviewAccessState(int policyVersion, string grantCode)
        { PolicyVersion = policyVersion; GrantCode = grantCode; }
        internal int PolicyVersion { get; }
        internal string GrantCode { get; }
    }

    internal sealed class PreviewAccessStore
    {
        private readonly string _path;
        private static readonly byte[] Entropy = Encoding.UTF8.GetBytes("T7-Rekindle/preview-access/v1");
        internal PreviewAccessStore(string directory) { _path = Path.Combine(directory, "preview-access.json"); }

        internal PreviewAccessState Load()
        {
            if (!File.Exists(_path)) return new PreviewAccessState(0, string.Empty);
            if (new FileInfo(_path).Length > 32768) throw new InvalidDataException("预览授权文件过大。");
            var envelope = PreviewGrantVerifier.ReadObject(File.ReadAllText(_path, Encoding.UTF8));
            if (envelope.Count != 2 || envelope["schemaVersion"]?.Type != JTokenType.Integer
                || (long)envelope["schemaVersion"] != 1 || envelope["protectedState"]?.Type != JTokenType.String)
                throw new InvalidDataException("预览授权文件格式错误。");
            var clear = ProtectedData.Unprotect(Convert.FromBase64String((string)envelope["protectedState"]), Entropy, DataProtectionScope.CurrentUser);
            var state = PreviewGrantVerifier.ReadObject(new UTF8Encoding(false, true).GetString(clear));
            if (state.Count != 2 || state["policyVersion"]?.Type != JTokenType.Integer
                || (long)state["policyVersion"] != PreviewGrantVerifier.PolicyVersion || state["grantCode"]?.Type != JTokenType.String)
                throw new InvalidDataException("预览授权状态格式错误。");
            return new PreviewAccessState(PreviewGrantVerifier.PolicyVersion, (string)state["grantCode"]);
        }

        internal void Save(PreviewAccessState state)
        {
            var json = new JObject { ["policyVersion"] = state.PolicyVersion, ["grantCode"] = state.GrantCode }.ToString();
            var encrypted = ProtectedData.Protect(Encoding.UTF8.GetBytes(json), Entropy, DataProtectionScope.CurrentUser);
            var envelope = new JObject { ["schemaVersion"] = 1, ["protectedState"] = Convert.ToBase64String(encrypted) };
            Directory.CreateDirectory(Path.GetDirectoryName(_path));
            var temporary = _path + "." + Guid.NewGuid().ToString("N") + ".tmp";
            try
            {
                var bytes = Encoding.UTF8.GetBytes(envelope + Environment.NewLine);
                using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                { stream.Write(bytes, 0, bytes.Length); stream.Flush(true); }
                if (File.Exists(_path)) File.Replace(temporary, _path, null);
                else File.Move(temporary, _path);
            }
            finally
            {
                try { if (File.Exists(temporary)) File.Delete(temporary); }
                catch (Exception error) when (error is IOException || error is UnauthorizedAccessException)
                { new LogService().Warn("预览授权临时文件清理失败。"); }
            }
        }
    }
}
