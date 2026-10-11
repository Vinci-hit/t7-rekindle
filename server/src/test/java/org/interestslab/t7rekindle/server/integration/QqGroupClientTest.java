package org.interestslab.t7rekindle.server.integration;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpHeaders;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.interestslab.t7rekindle.server.domain.Problem;
import tools.jackson.databind.json.JsonMapper;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class QqGroupClientTest {
    private final Instant now = Instant.parse("2026-01-01T00:00:00Z");
    private final HttpClient http = mock(HttpClient.class);
    private final QqGroupClient client = new QqGroupClient(http, JsonMapper.builder().build(), Clock.fixed(now, ZoneOffset.UTC));
    private final QqCookies cookies = QqCookies.empty().accept(QqGroupClient.QUN, List.of("skey=TEST; Domain=.qq.com; Path=/"), now);

    @Test void identityAndOnlyManagedGroupsUseScopedCredentialsAndBkn() {
        reply("t7GroupInfo({\"retcode\":0,\"data\":{\"uin\":54321,\"nickName\":\"管理员\"}});");
        assertThat(client.identity(cookies).value().qqNumber()).isEqualTo("54321");
        reply("{\"ec\":0,\"errcode\":0,\"create\":[{\"gc\":123456,\"gn\":\"群一\"}],\"manage\":[{\"gc\":\"123456\",\"gn\":\"群一\"},{\"gc\":234567,\"gn\":\"群二\"}],\"join\":[{\"gc\":345678,\"gn\":\"普通群\"}]}");
        assertThat(client.groups(cookies).value()).extracting(g -> g.number()).containsExactly("123456", "234567");
        var request = ArgumentCaptor.forClass(HttpRequest.class);
        verify(http, times(2)).sendAsync(request.capture(), org.mockito.ArgumentMatchers.<HttpResponse.BodyHandler<byte[]>>any());
        assertThat(request.getAllValues().getFirst().uri().getQuery()).contains("bkn=" + QqGroupClient.hash("TEST", 5381));
        assertThat(request.getValue().headers().firstValue("Cookie")).contains("skey=TEST");
        assertThat(request.getValue().uri().getHost()).isEqualTo("qun.qq.com");
        reply("{\"ec\":0,\"errcode\":0,\"em\":\"\"}");
        assertThat(client.groups(cookies).value()).isEmpty();
        client.close();
        verify(http).close();
    }
    @Test void exactMembershipAndEmptyResponseAreDistinguishedFromIncompleteResults() {
        reply("{\"ec\":0,\"errcode\":0,\"search_count\":0}");
        assertThat(client.member(cookies, "123456", "12345").value()).isFalse();
        reply("{\"ec\":0,\"errcode\":0,\"search_count\":0,\"mems\":[]}");
        assertThat(client.member(cookies, "123456", "12345").value()).isFalse();
        reply("{\"ec\":0,\"errcode\":0,\"search_count\":1,\"mems\":[{\"uin\":1234567}]}");
        assertThat(client.member(cookies, "123456", "12345").value()).isFalse();
        var pageOne = response("{\"ec\":0,\"errcode\":0,\"search_count\":2,\"mems\":[{\"uin\":99999}]}");
        var pageTwo = response("{\"ec\":0,\"errcode\":0,\"search_count\":2,\"mems\":[{\"uin\":12345}]}");
        when(http.sendAsync(any(), org.mockito.ArgumentMatchers.<HttpResponse.BodyHandler<byte[]>>any())).thenReturn(pageOne).thenReturn(pageTwo);
        assertThat(client.member(cookies, "123456", "12345").value()).isTrue();
        for (String body : List.of("{\"ec\":0,\"errcode\":0,\"search_count\":1}",
                "{\"ec\":0,\"errcode\":0,\"search_count\":-1}", "{\"ec\":0,\"errcode\":0,\"search_count\":\"0\"}",
                "{\"ec\":0,\"errcode\":0,\"search_count\":1,\"mems\":[]}",
                "{\"ec\":0,\"errcode\":0,\"search_count\":0,\"mems\":[{\"uin\":12345}]}")) {
            reply(body);
            assertThatThrownBy(() -> client.member(cookies, "123456", "12345")).isInstanceOf(Problem.class);
        }
    }
    @Test void identityAcceptsPlainJsonAndPreservesCredentialErrors() {
        reply("{\"retcode\":0,\"data\":{\"uin\":54321,\"nickName\":\"管理员\"}}");
        assertThat(client.identity(cookies).value().qqNumber()).isEqualTo("54321");
        reply("{\"cgicode\":100000,\"retcode\":100000,\"msg\":\"login error\",\"data\":[]}");
        assertThatThrownBy(() -> client.identity(cookies)).isInstanceOfSatisfying(Problem.class,
                error -> assertThat(error.code()).isEqualTo("QQ_GROUP_COOKIE_EXPIRED"));
        for (String body : List.of("otherCallback({\"retcode\":0});", "t7GroupInfo({\"retcode\":0}",
                "t7GroupInfo({\"retcode\":0});extra()")) {
            reply(body);
            assertThatThrownBy(() -> client.identity(cookies)).isInstanceOf(Problem.class);
        }
    }
    @Test void invalidCredentialsPermissionErrorsAndInvalidFormatsNeverMeanNotMember() {
        for (String body : List.of("{}", "[]", "bad", "{\"ec\":4}", "{\"ec\":7}", "{\"ec\":9}",
                "{\"ec\":0,\"errcode\":1}", "{\"ec\":0,\"errcode\":0,\"create\":{}}",
                "{\"ec\":0,\"errcode\":0,\"create\":[{\"gc\":\"bad\"}]}")) {
            reply(body);
            assertThatThrownBy(() -> client.groups(cookies)).isInstanceOf(Problem.class);
        }
        for (String body : List.of("bad", "t7GroupInfo({\"retcode\":4})", "t7GroupInfo({\"retcode\":0,\"data\":{\"uin\":12345}})")) {
            reply(body);
            assertThatThrownBy(() -> client.identity(cookies)).isInstanceOf(Problem.class);
        }
        assertThatThrownBy(() -> client.groups(QqCookies.empty())).isInstanceOf(Problem.class);
    }
    @Test void restrictsRedirectTargetsAndTimesOutWholeResponses() {
        for (String address : List.of("http://qun.qq.com/", "https://example.invalid/", "https://qun.qq.com:443/",
                "https://user@qun.qq.com/", "https://qun.qq.com/#x"))
            assertThatThrownBy(() -> QqGroupClient.requireUrl(URI.create(address))).isInstanceOf(Problem.class);
        var waiting = new CompletableFuture<HttpResponse<byte[]>>();
        when(http.sendAsync(any(), org.mockito.ArgumentMatchers.<HttpResponse.BodyHandler<byte[]>>any())).thenReturn(waiting);
        assertThatThrownBy(() -> client.exchange(QqGroupClient.QUN, cookies, null, Duration.ofMillis(10))).isInstanceOf(Problem.class);
        assertThat(waiting.isCancelled()).isTrue();
        when(http.sendAsync(any(), org.mockito.ArgumentMatchers.<HttpResponse.BodyHandler<byte[]>>any()))
                .thenReturn(CompletableFuture.failedFuture(new java.io.IOException("TEST_SECRET")));
        assertThatThrownBy(() -> client.groups(cookies)).isInstanceOfSatisfying(Problem.class,
                error -> assertThat(error.getMessage()).doesNotContain("TEST_SECRET"));
        when(http.sendAsync(any(), org.mockito.ArgumentMatchers.<HttpResponse.BodyHandler<byte[]>>any())).thenReturn(new CompletableFuture<>());
        Thread.currentThread().interrupt();
        try {
            assertThatThrownBy(() -> client.groups(cookies)).isInstanceOf(Problem.class);
            assertThat(Thread.currentThread().isInterrupted()).isTrue();
        } finally { Thread.interrupted(); }
    }
    private void reply(String body) {
        var value = response(body);
        when(http.sendAsync(any(), org.mockito.ArgumentMatchers.<HttpResponse.BodyHandler<byte[]>>any())).thenReturn(value);
    }
    private CompletableFuture<HttpResponse<byte[]>> response(String body) {
        @SuppressWarnings("unchecked") var response = (HttpResponse<byte[]>) mock(HttpResponse.class);
        when(response.statusCode()).thenReturn(200);
        when(response.body()).thenReturn(body.getBytes(StandardCharsets.UTF_8));
        when(response.headers()).thenReturn(HttpHeaders.of(Map.of(), (a, b) -> true));
        return CompletableFuture.completedFuture(response);
    }
}
