package org.interestslab.t7rekindle.server.integration;

import jakarta.annotation.PreDestroy;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;
import org.interestslab.t7rekindle.server.domain.Problem;
import org.interestslab.t7rekindle.server.domain.QqGroupModels.*;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import static org.interestslab.t7rekindle.server.domain.QqGroupModels.unavailable;

@Component
public class QqGroupClient {
    static final URI QUN = URI.create("https://qun.qq.com/member.html");
    private static final Set<String> HOSTS = Set.of("qun.qq.com", "ptlogin2.qun.qq.com", "ssl.ptlogin2.qq.com", "xui.ptlogin2.qq.com");
    private final HttpClient http;
    private final JsonMapper json;
    private final Clock clock;
    public record Result<T>(T value, QqCookies cookies) { }
    record Reply(int status, byte[] body, QqCookies cookies, String location) {
        String text() { return new String(body, StandardCharsets.UTF_8); }
        @Override public String toString() { return "QqReply[status=" + status + "]"; }
    }

    @Autowired public QqGroupClient(JsonMapper json, Clock clock) {
        this(HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).followRedirects(HttpClient.Redirect.NEVER).build(), json, clock);
    }
    QqGroupClient(HttpClient http, JsonMapper json, Clock clock) { this.http = http; this.json = json; this.clock = clock; }

    public Result<Administrator> identity(QqCookies cookies) {
        var uri = URI.create("https://qun.qq.com/cgi-bin/qunwelcome/myinfo?callback=t7GroupInfo&bkn=" + bkn(cookies));
        Reply reply = exchange(uri, cookies, null, Duration.ofSeconds(5));
        String body = reply.text().trim().replaceFirst(";\\s*$", "");
        if (body.startsWith("t7GroupInfo(")) {
            if (!body.endsWith(")")) throw unavailable();
            body = body.substring(12, body.length() - 1);
        }
        var root = parse(body);
        if (integer(root, "retcode") != 0) throw expired();
        String qq = number(root.path("data").path("uin"));
        String nick = root.path("data").path("nickName").asString("");
        if (nick.isBlank() || nick.length() > 255) throw unavailable();
        return new Result<>(new Administrator(qq, nick), reply.cookies());
    }
    public Result<List<Group>> groups(QqCookies cookies) {
        var reply = form("get_group_list", cookies, Map.of(), Duration.ofSeconds(5));
        var root = parse(reply.text());
        requireSuccess(root);
        var groups = new LinkedHashMap<String, Group>();
        for (String field : List.of("create", "manage")) {
            var values = root.path(field);
            if (values.isMissingNode()) continue;
            if (!values.isArray()) throw unavailable();
            for (var item : values) {
                String id = number(item.path("gc"));
                String name = item.path("gn").asString("");
                groups.put(id, new Group(id, name));
            }
        }
        return new Result<>(List.copyOf(groups.values()), reply.cookies());
    }
    public Result<Boolean> member(QqCookies initial, String group, String qq) {
        long deadline = System.nanoTime() + Duration.ofSeconds(6).toNanos();
        QqCookies cookies = initial;
        int start = 0;
        int total = -1;
        var seen = new java.util.HashSet<String>();
        for (int page = 0; page < 100; page++) {
            long remaining = deadline - System.nanoTime();
            if (remaining <= 0) throw unavailable();
            var reply = form("search_group_members", cookies, Map.of("gc", group, "key", qq, "st", "" + start,
                    "end", "" + (start + 20), "sort", "0"), Duration.ofNanos(remaining));
            cookies = reply.cookies();
            var root = parse(reply.text());
            requireSuccess(root);
            int count = integer(root, "search_count");
            if (count < 0) throw unavailable();
            if (total != -1 && total != count) throw unavailable();
            total = count;
            var members = root.path("mems");
            if (count == 0 && (members.isMissingNode() || members.isArray() && members.isEmpty())) return new Result<>(false, cookies);
            if (!members.isArray() || members.isEmpty() || members.size() > 21 || count == 0) throw unavailable();
            for (var item : members) {
                String member = number(item.path("uin"));
                if (member.equals(qq)) return new Result<>(true, cookies);
                if (!seen.add(member)) throw unavailable();
            }
            start += members.size();
            if (start >= count) return new Result<>(false, cookies);
        }
        throw unavailable();
    }
    private Reply form(String operation, QqCookies cookies, Map<String, String> data, Duration timeout) {
        var fields = new LinkedHashMap<>(data);
        fields.put("bkn", "" + bkn(cookies));
        return exchange(URI.create("https://qun.qq.com/cgi-bin/qun_mgr/" + operation), cookies, encode(fields), timeout);
    }
    int bkn(QqCookies cookies) {
        String key = cookies.value(QUN, "skey", clock.instant());
        if (key.isEmpty()) throw expired();
        return hash(key, 5381);
    }
    static int hash(String value, int seed) {
        int hash = seed;
        for (int i = 0; i < value.length(); i++) hash = hash * 33 + value.charAt(i);
        return hash & 0x7fffffff;
    }
    static String encode(Map<String, String> fields) {
        return fields.entrySet().stream().map(e -> URLEncoder.encode(e.getKey(), StandardCharsets.UTF_8) + "="
                + URLEncoder.encode(e.getValue(), StandardCharsets.UTF_8)).collect(java.util.stream.Collectors.joining("&"));
    }
    Reply exchange(URI uri, QqCookies cookies, String form, Duration timeout) {
        requireUrl(uri);
        java.util.concurrent.CompletableFuture<HttpResponse<byte[]>> pending = null;
        try {
            var builder = HttpRequest.newBuilder(uri).timeout(timeout).header("Referer", QUN.toString())
                    .header("Cookie", cookies.header(uri, clock.instant())).header("User-Agent", "Mozilla/5.0");
            if (form != null) builder.header("Content-Type", "application/x-www-form-urlencoded").POST(HttpRequest.BodyPublishers.ofString(form));
            pending = http.sendAsync(builder.build(), HttpResponse.BodyHandlers.ofByteArray());
            var response = pending.get(Math.max(1, timeout.toMillis()), TimeUnit.MILLISECONDS);
            byte[] body = response.body();
            if (body.length > 2 * 1024 * 1024) throw unavailable();
            int status = response.statusCode();
            if (status != 200 && status != 301 && status != 302 && status != 303 && status != 307 && status != 308) throw unavailable();
            var next = cookies.accept(uri, response.headers().allValues("set-cookie"), clock.instant());
            return new Reply(status, body, next, response.headers().firstValue("location").orElse(null));
        } catch (ExecutionException | TimeoutException | IllegalArgumentException error) {
            throw unavailable();
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            throw unavailable();
        } finally { if (pending != null) pending.cancel(true); }
    }
    static void requireUrl(URI uri) {
        if (!"https".equals(uri.getScheme()) || !HOSTS.contains(uri.getHost()) || uri.getUserInfo() != null
                || uri.getPort() != -1 || uri.getFragment() != null) throw unavailable();
    }
    private JsonNode parse(String body) {
        try { return json.readTree(body); }
        catch (JacksonException error) { throw unavailable(); }
    }
    private static int integer(JsonNode root, String field) {
        var value = root.path(field);
        if (!value.isIntegralNumber() || !value.canConvertToInt()) throw unavailable();
        return value.asInt();
    }
    private static String number(JsonNode node) {
        String value = node.isIntegralNumber() ? node.asString() : node.asString("");
        if (!value.matches("[1-9][0-9]{4,19}")) throw unavailable();
        return value;
    }
    private static void requireSuccess(JsonNode root) {
        int code = integer(root, "ec");
        if (code == 4) throw expired();
        if (code == 7) throw new Problem(503, "QQ_GROUP_PERMISSION_LOST", "群管理权限不足，请重新扫码并选择可管理的群");
        if (code != 0 || integer(root, "errcode") != 0) throw unavailable();
    }
    static Problem expired() { return new Problem(503, "QQ_GROUP_COOKIE_EXPIRED", "群站凭据已失效，请管理员重新扫码"); }
    @PreDestroy public void close() { http.close(); }
}
