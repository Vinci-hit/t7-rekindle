package org.interestslab.t7rekindle.server.integration;

import java.net.HttpCookie;
import java.net.URI;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;

/** An immutable cookie jar; expiry is absolute so restoring it does not extend cookie lifetime. */
public record QqCookies(List<Cookie> values) {
    public record Cookie(String name, String value, String domain, String path, boolean hostOnly,
                         boolean secure, boolean httpOnly, Instant expiresAt) {
        @Override public String toString() { return "QqCookie[" + name + "]"; }
    }
    public QqCookies { values = List.copyOf(values); }
    public static QqCookies empty() { return new QqCookies(List.of()); }
    @Override public String toString() { return "QqCookies[count=" + values.size() + "]"; }

    public QqCookies accept(URI origin, List<String> headers, Instant now) {
        var next = new ArrayList<>(values.stream().filter(c -> active(c, now)).toList());
        for (String header : headers) {
            for (HttpCookie item : HttpCookie.parse(header)) {
                String domain = item.getDomain() == null ? origin.getHost() : item.getDomain().replaceFirst("^\\.", "");
                domain = domain.toLowerCase(Locale.ROOT);
                if (!domainMatches(origin.getHost(), domain)) continue;
                String path = item.getPath();
                if (path == null || !path.startsWith("/")) {
                    path = origin.getPath().substring(0, origin.getPath().lastIndexOf('/') + 1);
                    if (path.isEmpty()) path = "/";
                }
                Instant expiry = item.getMaxAge() < 0 ? null : now.plusSeconds(Math.min(item.getMaxAge(), 315360000L));
                var cookie = new Cookie(item.getName(), item.getValue(), domain, path, item.getDomain() == null,
                        item.getSecure(), item.isHttpOnly(), expiry);
                next.removeIf(c -> c.name().equals(cookie.name()) && c.domain().equals(cookie.domain()) && c.path().equals(cookie.path()));
                if (active(cookie, now)) next.add(cookie);
            }
        }
        return new QqCookies(next);
    }
    public String header(URI uri, Instant now) {
        return matching(uri, now).stream().map(c -> c.name() + "=" + c.value()).collect(java.util.stream.Collectors.joining("; "));
    }
    public String value(URI uri, String name, Instant now) {
        return matching(uri, now).stream().filter(c -> c.name().equals(name)).map(Cookie::value).findFirst().orElse("");
    }
    public QqCookies scopedTo(URI uri, Instant now) {
        return new QqCookies(values.stream().filter(c -> active(c, now)
                && (c.hostOnly() ? uri.getHost().equals(c.domain()) : domainMatches(uri.getHost(), c.domain()))).toList());
    }
    private List<Cookie> matching(URI uri, Instant now) {
        String path = uri.getPath().isEmpty() ? "/" : uri.getPath();
        return values.stream().filter(c -> active(c, now) && (!c.secure() || uri.getScheme().equals("https"))
                && (c.hostOnly() ? uri.getHost().equals(c.domain()) : domainMatches(uri.getHost(), c.domain()))
                && (path.equals(c.path()) || path.startsWith(c.path().endsWith("/") ? c.path() : c.path() + "/")))
                .sorted(Comparator.<Cookie>comparingInt(c -> c.path().length()).reversed()
                        .thenComparing(Comparator.<Cookie>comparingInt(c -> c.domain().length()).reversed())).toList();
    }
    private static boolean active(Cookie cookie, Instant now) { return cookie.expiresAt() == null || cookie.expiresAt().isAfter(now); }
    private static boolean domainMatches(String host, String domain) { return host.equals(domain) || host.endsWith("." + domain); }
}
