package org.interestslab.t7rekindle.server.integration;

import java.net.URI;
import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.Test;
import static org.assertj.core.api.Assertions.*;

class QqCookiesTest {
    private final Instant now = Instant.parse("2026-01-01T00:00:00Z");
    private final URI qun = URI.create("https://qun.qq.com/member.html");

    @Test void scopesCookiesAndPreservesAbsoluteExpiryAcrossPersistence() {
        var cookies = QqCookies.empty().accept(qun, List.of("skey=TEST; Domain=.qq.com; Path=/; Secure; HttpOnly",
                "p_skey=GROUP; Path=/; Max-Age=60", "bad=NO; Domain=example.invalid"), now);
        assertThat(cookies.header(qun, now)).contains("skey=TEST", "p_skey=GROUP").doesNotContain("bad=");
        assertThat(cookies.header(URI.create("https://connect.qq.com/"), now)).isEqualTo("skey=TEST");
        assertThat(cookies.header(qun, now.plusSeconds(61))).isEqualTo("skey=TEST");
        assertThat(cookies.header(URI.create("http://connect.qq.com/"), now)).isEmpty();
        assertThat(cookies.toString()).doesNotContain("TEST", "GROUP");
        assertThat(cookies.accept(qun, List.of("p_skey=; Max-Age=0; Path=/"), now).header(qun, now))
                .isEqualTo("skey=TEST");
    }

    @Test void respectsPathBoundaryAndReplacesOnlySameScope() {
        var cookies = QqCookies.empty().accept(qun, List.of("a=ROOT; Path=/", "a=PATH; Path=/member"), now);
        assertThat(cookies.header(qun, now)).isEqualTo("a=ROOT");
        assertThat(cookies.header(URI.create("https://qun.qq.com/member/child"), now)).isEqualTo("a=PATH; a=ROOT");
        assertThat(cookies.accept(qun, List.of("a=NEW; Path=/"), now).header(qun, now)).isEqualTo("a=NEW");
    }
    @Test void groupPersistenceDropsTemporaryLoginAndSsoCookies() {
        var cookies = QqCookies.empty().accept(qun, List.of("skey=TEST; Domain=.qq.com; Path=/", "p_skey=GROUP; Path=/"), now)
                .accept(URI.create("https://ssl.ptlogin2.qq.com/"),
                        List.of("qrsig=QR; Domain=.ptlogin2.qq.com; Path=/", "superkey=SSO; Domain=.ptlogin2.qq.com; Path=/"), now);
        var scoped = cookies.scopedTo(qun, now);
        assertThat(scoped.values()).extracting(QqCookies.Cookie::name).containsExactly("skey", "p_skey");
        assertThat(scoped.header(qun, now)).isEqualTo(cookies.header(qun, now));
    }
}
