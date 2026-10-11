package org.interestslab.t7rekindle.server.integration;

import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.interestslab.t7rekindle.server.domain.Problem;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class QqQrClientTest {
    private final Instant now = Instant.parse("2026-01-01T00:00:00Z");
    private final QqGroupClient client = mock(QqGroupClient.class);
    private final QqQrClient qr = new QqQrClient(client, Clock.fixed(now, ZoneOffset.UTC));
    private final QqCookies cookies = QqCookies.empty().accept(URI.create("https://ssl.ptlogin2.qq.com/"),
            List.of("qrsig=TEST_QR; Domain=.ptlogin2.qq.com; Path=/", "pt_login_sig=TEST_LOGIN; Domain=.ptlogin2.qq.com; Path=/"), now);
    @Test void startsWithOfficialImageAndParsesLoginVersion() {
        when(client.exchange(any(), any(), isNull(), any())).thenReturn(reply("ptui_version:encodeURIComponent(\"123456\")"),
                new QqGroupClient.Reply(200, new byte[] {(byte) 137, 80, 78, 71, 13, 10, 26, 10}, cookies, null));
        var result = qr.start();
        assertThat(result.version()).isEqualTo("123456");
        assertThat(result.cookies()).isEqualTo(cookies);
        assertThat(result.toString()).doesNotContain("TEST");
        when(client.exchange(any(), any(), isNull(), any())).thenReturn(reply("bad"));
        assertThatThrownBy(qr::start).isInstanceOf(Problem.class);
        when(client.exchange(any(), any(), isNull(), any())).thenReturn(reply("ptui_version:\"123\""), reply("not an image"));
        assertThatThrownBy(qr::start).isInstanceOf(Problem.class);
    }
    @Test void pollsWaitingScannedExpiredAndFailedWithoutEvaluatingJavascript() {
        var ticket = new QqQrClient.Ticket(cookies, new byte[0], "123");
        String[] codes = {"66", "67", "65", "12"};
        String[] states = {"WAITING", "SCANNED", "EXPIRED", "FAILED"};
        for (int i = 0; i < codes.length; i++) {
            when(client.exchange(any(), any(), isNull(), any())).thenReturn(reply("ptuiCB('" + codes[i] + "','0','','0','message','nick');"));
            assertThat(qr.poll(ticket).state()).isEqualTo(states[i]);
        }
        assertThat(QqQrClient.callback("ptuiCB('66','0','','0','message','O\\'name');")[0]).isEqualTo("66");
        for (String body : List.of("alert('test')", "ptuiCB('66');", "ptuiCB('66','0','','0','message','nick');extra()"))
            assertThatThrownBy(() -> QqQrClient.callback(body)).isInstanceOf(Problem.class);
    }
    @Test void successFollowsOnlyTheValidatedRedirectChainAndCompletesOnGroupSite() {
        var ticket = new QqQrClient.Ticket(cookies, new byte[0], "123");
        when(client.exchange(any(), any(), isNull(), any())).thenReturn(
                reply("ptuiCB('0','0','https://ptlogin2.qun.qq.com/check_sig?ticket=TEST','0','ok','nick');"),
                new QqGroupClient.Reply(302, new byte[0], cookies, "https://qun.qq.com/member.html"), reply("group page"));
        assertThat(qr.poll(ticket).state()).isEqualTo("CONFIRMED");
        when(client.exchange(any(), any(), isNull(), any())).thenReturn(
                reply("ptuiCB('0','0','https://ptlogin2.qun.qq.com/check_sig','0','ok','nick');"), reply("wrong final site"));
        assertThatThrownBy(() -> qr.poll(ticket)).isInstanceOf(Problem.class);
    }
    @Test void acceptsTheAdditionalSuccessCallbackFieldWithoutExecutingJavascript() {
        var ticket = new QqQrClient.Ticket(cookies, new byte[0], "123");
        String callback = "ptuiCB('0','0','https://ptlogin2.qun.qq.com/check_sig?ticket=TEST','0','ok','nick', 'TOKEN');";
        assertThat(QqQrClient.callback(callback)).hasSize(7);
        when(client.exchange(any(), any(), isNull(), any())).thenReturn(reply(callback),
                new QqGroupClient.Reply(302, new byte[0], cookies, "https://qun.qq.com/member.html"), reply("group page"));
        assertThat(qr.poll(ticket).state()).isEqualTo("CONFIRMED");
        for (String body : List.of(callback + "extra()", callback.replace("'TOKEN'", "extra()"),
                callback.replace("'TOKEN'", "'TOKEN','extra'")))
            assertThatThrownBy(() -> QqQrClient.callback(body)).isInstanceOf(Problem.class);
    }
    private QqGroupClient.Reply reply(String body) {
        return new QqGroupClient.Reply(200, body.getBytes(StandardCharsets.UTF_8), cookies, null);
    }
}
