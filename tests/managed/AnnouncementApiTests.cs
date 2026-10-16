using System;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using T7R.Desktop.Services;
using static T7R.ManagedHarness.LauncherTests;

namespace T7R.ManagedHarness
{
    internal static class AnnouncementApiTests
    {
        internal static void Run() => RunTask(CheckAsync());

        private static async Task CheckAsync()
        {
            CheckAddresses();
            var data = new JObject
            {
                ["announcementId"] = "12345678-1234-1234-1234-123456789abc",
                ["title"] = "线上公告", ["summary"] = "线上摘要", ["bodyMarkdown"] = "## 线上正文\n\n内容",
                ["publishedAt"] = "2026-10-12T00:00:00Z"
            };
            var status = HttpStatusCode.OK;
            string body = data.ToString(Formatting.None);
            using (var api = new AnnouncementApi("https://server.example.invalid/", new HttpClient(new Handler((request, token) =>
            {
                Assert(request.Method == HttpMethod.Get && request.RequestUri.AbsoluteUri == "https://server.example.invalid/api/v1/announcements/latest",
                    "announcement did not use the public latest endpoint");
                Assert(request.Headers.Authorization == null && !request.Headers.Contains("X-Machine-Id")
                    && !request.Headers.Contains("Cookie"), "announcement request included account credentials");
                return Task.FromResult(new HttpResponseMessage(status) { Content = new StringContent(body, Encoding.UTF8, "application/json") });
            }))))
            {
                var result = await api.GetLatestAsync(CancellationToken.None);
                Assert(result.Title == "线上公告" && result.Summary == "线上摘要" && result.BodyMarkdown.Contains("## 线上正文")
                    && result.PublishedAt == DateTimeOffset.Parse("2026-10-12T00:00:00Z"), "announcement response changed content or timestamp");
                status = HttpStatusCode.NoContent;
                Assert(await api.GetLatestAsync(CancellationToken.None) == null, "204 was not treated as no published announcement");
                foreach (var failure in new[] { HttpStatusCode.NotFound, HttpStatusCode.ServiceUnavailable, HttpStatusCode.Redirect })
                {
                    status = failure;
                    await FailsAsync<HttpRequestException>(() => api.GetLatestAsync(CancellationToken.None));
                }
                status = HttpStatusCode.OK;
                foreach (var invalid in new[] { "", "null", "{}", "[]", "<html>error</html>" })
                {
                    body = invalid;
                    await FailsAsync<Exception>(() => api.GetLatestAsync(CancellationToken.None));
                }
                foreach (string field in new[] { "announcementId", "title", "summary", "bodyMarkdown", "publishedAt" })
                {
                    var invalid = (JObject)data.DeepClone();
                    invalid.Remove(field);
                    body = invalid.ToString();
                    await FailsAsync<InvalidDataException>(() => api.GetLatestAsync(CancellationToken.None));
                    invalid[field] = 42;
                    body = invalid.ToString();
                    await FailsAsync<InvalidDataException>(() => api.GetLatestAsync(CancellationToken.None));
                    invalid[field] = " ";
                    body = invalid.ToString();
                    await FailsAsync<InvalidDataException>(() => api.GetLatestAsync(CancellationToken.None));
                }
                foreach (var field in new[] { "title", "summary", "bodyMarkdown" })
                {
                    var invalid = (JObject)data.DeepClone();
                    invalid[field] = new string('界', field == "title" ? 121 : field == "summary" ? 501 : 65537);
                    body = invalid.ToString();
                    await FailsAsync<InvalidDataException>(() => api.GetLatestAsync(CancellationToken.None));
                }
                foreach (var field in new[] { "announcementId", "publishedAt" })
                {
                    var invalid = (JObject)data.DeepClone();
                    invalid[field] = "invalid";
                    body = invalid.ToString();
                    await FailsAsync<InvalidDataException>(() => api.GetLatestAsync(CancellationToken.None));
                }
                body = new string('x', 2 * 1024 * 1024);
                await FailsAsync<HttpRequestException>(() => api.GetLatestAsync(CancellationToken.None));
            }
            using (var api = new AnnouncementApi("https://server.example.invalid", new HttpClient(new Handler(async (request, token) =>
            {
                await Task.Delay(Timeout.Infinite, token);
                return new HttpResponseMessage(HttpStatusCode.OK);
            })), TimeSpan.FromMilliseconds(30)))
            {
                await FailsAsync<OperationCanceledException>(() => api.GetLatestAsync(CancellationToken.None));
                using (var cancel = new CancellationTokenSource())
                {
                    cancel.Cancel();
                    await FailsAsync<OperationCanceledException>(() => api.GetLatestAsync(cancel.Token));
                }
            }
        }

        private static void CheckAddresses()
        {
            Assert(AnnouncementApi.CreateConfigured("") == null, "missing server configuration should keep the built-in announcement");
            Assert(AnnouncementApi.CreateConfigured("invalid") == null, "invalid configuration should not prevent launcher startup");
            using (var api = AnnouncementApi.CreateConfigured("https://server.example.invalid"))
                Assert(api != null, "configured HTTPS announcement service was not created");
            foreach (var address in new[] { "http://server.example.invalid", "relative", "https://user@server.example.invalid",
                "https://server.example.invalid/?query=1", "https://server.example.invalid/#fragment" })
            {
                bool rejected = false;
                try { using (var api = new AnnouncementApi(address)) { } }
                catch (ArgumentException) { rejected = true; }
                Assert(rejected, "announcement accepted invalid address: " + address);
            }
#if DEBUG
            using (var api = new AnnouncementApi("http://localhost:8080")) { }
#else
            bool loopbackRejected = false;
            try { using (var api = new AnnouncementApi("http://localhost:8080")) { } }
            catch (ArgumentException) { loopbackRejected = true; }
            Assert(loopbackRejected, "release announcement service accepted HTTP");
#endif
        }

        private static async Task FailsAsync<T>(Func<Task> action) where T : Exception
        {
            try { await action(); }
            catch (T) { return; }
            throw new InvalidOperationException("expected announcement failure: " + typeof(T).Name);
        }

        private sealed class Handler : HttpMessageHandler
        {
            private readonly Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> _send;
            internal Handler(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> send) { _send = send; }
            protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) => _send(request, cancellationToken);
        }
    }
}
