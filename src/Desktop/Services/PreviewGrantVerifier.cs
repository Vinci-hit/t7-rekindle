using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace T7R.Desktop.Services
{
    internal sealed class PreviewGrant
    {
        internal PreviewGrant(string code, DateTimeOffset issuedAt, DateTimeOffset expiresAt)
        { Code = code; IssuedAt = issuedAt; ExpiresAt = expiresAt; }
        internal string Code { get; }
        internal DateTimeOffset IssuedAt { get; }
        internal DateTimeOffset ExpiresAt { get; }
        internal bool IsValidAt(DateTimeOffset now) => now >= IssuedAt.AddMinutes(-5) && now < ExpiresAt;
    }

    internal sealed class PreviewGrantVerifier
    {
        internal const int PolicyVersion = 1;
        private const long DurationSeconds = 7 * 24 * 60 * 60;
        private readonly byte[] _publicKey;
        private readonly string _keyId;
        private static readonly string[] Fields = { "policyVersion", "keyId", "subjectHash", "issuedAt", "expiresAt" };

        internal PreviewGrantVerifier(string publicKey)
        {
            _publicKey = Convert.FromBase64String(publicKey);
            if (_publicKey.Length != 72 || BitConverter.ToUInt32(_publicKey, 0) != 0x31534345
                || BitConverter.ToUInt32(_publicKey, 4) != 32) throw new InvalidDataException("预览公钥格式错误。");
            using (var key = CngKey.Import(_publicKey, CngKeyBlobFormat.EccPublicBlob))
            using (var verifier = new ECDsaCng(key))
                if (verifier.KeySize != 256) throw new InvalidDataException("预览公钥曲线错误。");
            _keyId = GetKeyId(_publicKey);
        }

        internal PreviewGrant Verify(string code, string subject)
        {
            try
            {
                code = (code ?? string.Empty).Trim();
                if (code.Length > 4096) throw new FormatException();
                var parts = code.Split('.');
                if (parts.Length != 3 || parts[0] != "T7PA1") throw new FormatException();
                var payload = Decode(parts[1]);
                var signature = Decode(parts[2]);
                if (signature.Length != 64) throw new FormatException();
                using (var key = CngKey.Import(_publicKey, CngKeyBlobFormat.EccPublicBlob))
                using (var verifier = new ECDsaCng(key) { HashAlgorithm = CngAlgorithm.Sha256 })
                    if (!verifier.VerifyData(Encoding.UTF8.GetBytes(parts[0] + "." + parts[1]), signature))
                        throw new InvalidDataException("授权码签名无效，请向管理员重新获取。");
                var value = ReadObject(new UTF8Encoding(false, true).GetString(payload));
                if (value.Properties().Count() != Fields.Length || Fields.Any(field => value[field] == null)
                    || value["policyVersion"].Type != JTokenType.Integer || (long)value["policyVersion"] != PolicyVersion
                    || value["keyId"].Type != JTokenType.String || (string)value["keyId"] != _keyId)
                    throw new InvalidDataException("授权码版本或签发密钥不匹配。");
                if (value["subjectHash"].Type != JTokenType.String || (string)value["subjectHash"] != subject)
                    throw new InvalidDataException("授权码不属于当前电脑和 Windows 用户，请重新申请。");
                if (value["issuedAt"].Type != JTokenType.Integer || value["expiresAt"].Type != JTokenType.Integer)
                    throw new FormatException();
                var issued = (long)value["issuedAt"];
                var expires = (long)value["expiresAt"];
                if (issued < 300 || issued > 253402300799L - DurationSeconds || expires != issued + DurationSeconds)
                    throw new InvalidDataException("授权有效期格式错误，应为签发后 7 天。");
                return new PreviewGrant(code, DateTimeOffset.FromUnixTimeSeconds(issued), DateTimeOffset.FromUnixTimeSeconds(expires));
            }
            catch (Exception error) when (error is FormatException || error is JsonException || error is ArgumentException
                || error is OverflowException || error is CryptographicException)
            {
                throw new InvalidDataException("授权码格式或签名无效，请复制完整授权码。", error);
            }
        }

        internal static JObject ReadObject(string json)
        {
            using (var reader = new JsonTextReader(new StringReader(json)) { DateParseHandling = DateParseHandling.None, MaxDepth = 8 })
            {
                var value = JObject.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
                if (reader.Read()) throw new JsonReaderException("Unexpected trailing content.");
                return value;
            }
        }

        internal static string CreateSubject(string machineId, string userSid)
        {
            if (!Guid.TryParse(machineId, out var machine) || machine == Guid.Empty || string.IsNullOrWhiteSpace(userSid))
                throw new InvalidOperationException("读取本机用户标识失败。");
            using (var hash = SHA256.Create())
                return Encode(hash.ComputeHash(Encoding.UTF8.GetBytes("T7-Rekindle/preview/v1\n" + machine.ToString("D") + "\n" + userSid)));
        }

        internal static string GetKeyId(byte[] key)
        {
            using (var hash = SHA256.Create()) return Encode(hash.ComputeHash(key));
        }

        internal static string Encode(byte[] value) => Convert.ToBase64String(value).TrimEnd('=').Replace('+', '-').Replace('/', '_');
        internal static byte[] Decode(string value)
        {
            if (value == null || !Regex.IsMatch(value, @"\A[A-Za-z0-9_-]+\z")) throw new FormatException();
            var decoded = Convert.FromBase64String(value.Replace('-', '+').Replace('_', '/') + new string('=', (4 - value.Length % 4) % 4));
            if (Encode(decoded) != value) throw new FormatException();
            return decoded;
        }
    }
}
