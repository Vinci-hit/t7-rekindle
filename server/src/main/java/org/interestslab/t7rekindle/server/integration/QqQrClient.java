package org.interestslab.t7rekindle.server.integration;

import java.net.URI;
import java.time.Clock;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.regex.Pattern;
import org.springframework.stereotype.Component;
import static org.interestslab.t7rekindle.server.domain.QqGroupModels.unavailable;

@Component
public class QqQrClient {
    private static final String LOGIN = "https://xui.ptlogin2.qq.com/cgi-bin/xlogin?pt_disable_pwd=1&appid=715030901"
            + "&daid=73&hide_close_icon=1&pt_no_auth=1&s_url=https%3A%2F%2Fqun.qq.com%2Fmember.html";
    private static final URI POLL = URI.create("https://ssl.ptlogin2.qq.com/ptqrlogin");
    private static final Pattern CALLBACK = Pattern.compile("^ptuiCB\\(((?:'(?:[^'\\\\\\r\\n]|\\\\.)*'\\s*,\\s*){5,6}'(?:[^'\\\\\\r\\n]|\\\\.)*')\\);?$");
    private static final Pattern VERSION = Pattern.compile("ptui_version\\s*:\\s*(?:encodeURIComponent\\()?\"([0-9]+)\"");
    private final QqGroupClient client;
    private final Clock clock;
    public record Ticket(QqCookies cookies, byte[] image, String version) {
        @Override public String toString() { return "QqQrTicket"; }
    }
    public record Poll(String state, QqCookies cookies) { }
    public QqQrClient(QqGroupClient client, Clock clock) { this.client = client; this.clock = clock; }

    public Ticket start() {
        var login = client.exchange(URI.create(LOGIN), QqCookies.empty(), null, Duration.ofSeconds(5));
        var match = VERSION.matcher(login.text());
        if (!match.find()) throw unavailable();
        String version = match.group(1);
        String query = QqGroupClient.encode(Map.of("appid", "715030901", "daid", "73", "e", "2", "l", "M", "s", "3",
                "d", "72", "v", "4", "t", "" + clock.millis(), "u1", QqGroupClient.QUN.toString()));
        var image = client.exchange(URI.create("https://ssl.ptlogin2.qq.com/ptqrshow?" + query), login.cookies(), null, Duration.ofSeconds(5));
        byte[] bytes = image.body();
        if (bytes.length < 8 || bytes[0] != (byte) 137 || bytes[1] != 80 || bytes[2] != 78 || bytes[3] != 71
                || image.cookies().value(POLL, "qrsig", clock.instant()).isBlank()) throw unavailable();
        return new Ticket(image.cookies(), bytes, version);
    }
    public Poll poll(Ticket ticket) {
        var fields = new LinkedHashMap<String, String>();
        fields.put("u1", QqGroupClient.QUN.toString());
        fields.put("ptqrtoken", "" + QqGroupClient.hash(ticket.cookies().value(POLL, "qrsig", clock.instant()), 0));
        fields.put("ptredirect", "0"); fields.put("h", "1"); fields.put("t", "1"); fields.put("g", "1");
        fields.put("from_ui", "1"); fields.put("ptlang", "2052"); fields.put("action", "0-0-" + clock.millis());
        fields.put("js_ver", ticket.version()); fields.put("js_type", "1"); fields.put("pt_uistyle", "40");
        fields.put("login_sig", ticket.cookies().value(URI.create(LOGIN), "pt_login_sig", clock.instant()));
        fields.put("aid", "715030901"); fields.put("daid", "73");
        var reply = client.exchange(URI.create(POLL + "?" + QqGroupClient.encode(fields)), ticket.cookies(), null, Duration.ofSeconds(5));
        String[] parts = callback(reply.text());
        return switch (parts[0]) {
            case "66" -> new Poll("WAITING", reply.cookies());
            case "67" -> new Poll("SCANNED", reply.cookies());
            case "65" -> new Poll("EXPIRED", reply.cookies());
            case "0" -> new Poll("CONFIRMED", complete(parts[2], reply.cookies()));
            default -> new Poll("FAILED", reply.cookies());
        };
    }
    static String[] callback(String body) {
        var match = CALLBACK.matcher(body.trim());
        if (!match.matches()) throw unavailable();
        var strings = Pattern.compile("'((?:[^'\\\\]|\\\\.)*)'").matcher(match.group(1));
        var result = new java.util.ArrayList<String>();
        while (strings.find()) result.add(strings.group(1).replace("\\/", "/"));
        if (result.get(2).contains("\\")) throw unavailable();
        return result.toArray(String[]::new);
    }
    private QqCookies complete(String address, QqCookies cookies) {
        URI uri;
        try { uri = URI.create(address); }
        catch (IllegalArgumentException error) { throw unavailable(); }
        for (int i = 0; i < 6; i++) {
            var reply = client.exchange(uri, cookies, null, Duration.ofSeconds(5));
            cookies = reply.cookies();
            if (reply.status() == 200) {
                if (!uri.getHost().equals("qun.qq.com")) throw unavailable();
                return cookies;
            }
            if (reply.location() == null) throw unavailable();
            try { uri = uri.resolve(reply.location()); }
            catch (IllegalArgumentException error) { throw unavailable(); }
        }
        throw unavailable();
    }
}
