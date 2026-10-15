using System;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace T7.Rekindle.Desktop.Services
{
    internal sealed class AnnouncementApi : IDisposable
    {
        private readonly HttpClient _client;
        private readonly Uri _address;
        private readonly TimeSpan _timeout;

        internal AnnouncementApi(string address, HttpClient client = null, TimeSpan? timeout = null)
        {
            if (!Uri.TryCreate((address ?? string.Empty).TrimEnd('/') + "/", UriKind.Absolute, out var baseAddress)
                || !IsAllowedScheme(baseAddress) || baseAddress.UserInfo.Length != 0
                || baseAddress.Query.Length != 0 || baseAddress.Fragment.Length != 0)
                throw new ArgumentException("公告服务地址必须为 HTTPS；仅 Debug 构建允许本机回环 HTTP。", nameof(address));
            _address = new Uri(baseAddress, "api/v1/announcements/latest");
            _client = client ?? new HttpClient(new HttpClientHandler { AllowAutoRedirect = false, UseCookies = false });
            _client.Timeout = Timeout.InfiniteTimeSpan;
            _client.MaxResponseContentBufferSize = 1024 * 1024;
            _timeout = timeout ?? TimeSpan.FromSeconds(8);
        }

        internal static AnnouncementApi CreateConfigured(string address)
        {
            if (string.IsNullOrWhiteSpace(address)) return null;
            try { return new AnnouncementApi(address); }
            catch (ArgumentException error)
            {
                new LogService().Error("公告服务配置错误，使用内置公告", error);
                return null;
            }
        }

        internal async Task<LauncherAnnouncement> GetLatestAsync(CancellationToken cancellation)
        {
            using (var limit = CancellationTokenSource.CreateLinkedTokenSource(cancellation))
            using (var request = new HttpRequestMessage(HttpMethod.Get, _address))
            {
                limit.CancelAfter(_timeout);
                request.Headers.Accept.ParseAdd("application/json");
                using (var response = await _client.SendAsync(request, limit.Token).ConfigureAwait(false))
                {
                    response.EnsureSuccessStatusCode();
                    if (response.StatusCode == HttpStatusCode.NoContent) return null;
                    var json = JsonConvert.DeserializeObject<JObject>(await response.Content.ReadAsStringAsync().ConfigureAwait(false),
                        new JsonSerializerSettings { DateParseHandling = DateParseHandling.None, MaxDepth = 16 });
                    if (json == null) throw new InvalidDataException("公告响应为空。");
                    var id = ReadText(json, "announcementId", 36);
                    var publishedAt = ReadText(json, "publishedAt", 64);
                    if (!Guid.TryParseExact(id, "D", out _) || !DateTimeOffset.TryParse(publishedAt,
                        CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out var time))
                        throw new InvalidDataException("公告标识或发布时间无效。");
                    return new LauncherAnnouncement(id, ReadText(json, "title", LauncherAnnouncement.TitleLimit),
                        ReadText(json, "summary", LauncherAnnouncement.SummaryLimit),
                        ReadText(json, "bodyMarkdown", LauncherAnnouncement.BodyLimit), time.ToUniversalTime());
                }
            }
        }

        private static string ReadText(JObject json, string name, int limit)
        {
            var value = json[name];
            if (value?.Type != JTokenType.String) throw new InvalidDataException("公告字段格式错误：" + name);
            var text = (string)value;
            if (string.IsNullOrWhiteSpace(text) || text.Length > limit) throw new InvalidDataException("公告字段长度错误：" + name);
            return text;
        }

        private static bool IsAllowedScheme(Uri address)
        {
            if (address.Scheme == Uri.UriSchemeHttps) return true;
#if DEBUG
            return address.Scheme == Uri.UriSchemeHttp && address.IsLoopback;
#else
            return false;
#endif
        }

        public void Dispose() => _client.Dispose();
    }
}
