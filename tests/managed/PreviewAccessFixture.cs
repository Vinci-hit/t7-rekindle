using System;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json.Linq;
using T7.Rekindle.Desktop.Services;

namespace T7.ManagedHarness
{
    internal sealed class PreviewAccessFixture : IDisposable
    {
        private readonly ECDsaCng _key = new ECDsaCng(256) { HashAlgorithm = CngAlgorithm.Sha256 };
        internal DateTimeOffset Now { get; set; } = new DateTimeOffset(2026, 1, 1, 0, 0, 0, TimeSpan.Zero);
        internal string Subject { get; set; } = PreviewGrantVerifier.CreateSubject(
            "11111111-1111-1111-1111-111111111111", "S-1-5-21-100-200-300-1001");
        internal string PublicKey => Convert.ToBase64String(_key.Key.Export(CngKeyBlobFormat.EccPublicBlob));
        internal PreviewAccessService Create(string directory) => new PreviewAccessService(directory, PublicKey, () => Subject, () => Now);

        internal static PreviewAccessService Authorized(string directory)
        {
            using (var fixture = new PreviewAccessFixture())
            {
                var service = fixture.Create(directory);
                if (!service.TryImport(fixture.Issue(), out var error)) throw new InvalidOperationException(error);
                return service;
            }
        }

        internal string Issue(Action<JObject> change = null)
        {
            var payload = new JObject
            {
                ["policyVersion"] = 1,
                ["keyId"] = PreviewGrantVerifier.GetKeyId(Convert.FromBase64String(PublicKey)),
                ["subjectHash"] = Subject,
                ["issuedAt"] = Now.ToUnixTimeSeconds(),
                ["expiresAt"] = Now.AddDays(7).ToUnixTimeSeconds()
            };
            change?.Invoke(payload);
            return Sign(payload.ToString(Newtonsoft.Json.Formatting.None));
        }

        internal string Sign(string payload)
        {
            var message = "T7PA1." + Encode(Encoding.UTF8.GetBytes(payload));
            return message + "." + Encode(_key.SignData(Encoding.UTF8.GetBytes(message)));
        }

        internal static string Encode(byte[] value) => Convert.ToBase64String(value).TrimEnd('=').Replace('+', '-').Replace('/', '_');
        public void Dispose() => _key.Dispose();
    }
}
