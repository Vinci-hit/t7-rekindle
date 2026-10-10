package org.interestslab.t7rekindle.server.integration;

import java.io.IOException;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.interestslab.t7rekindle.server.domain.Problem;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class QqClientTest {
    private final HttpClient http = mock(HttpClient.class);
    private final QqClient client = new QqClient(http);

    @AfterEach void clearInterrupt() { Thread.interrupted(); }
    @Test void usesFixedEndpointAndCookieHeaderWithoutRedirecting() throws Exception {
        @SuppressWarnings("unchecked") var response = (HttpResponse<String>) mock(HttpResponse.class);
        when(response.statusCode()).thenReturn(200);
        when(response.body()).thenReturn("TEST_BODY");
        when(http.send(any(), org.mockito.ArgumentMatchers.<HttpResponse.BodyHandler<String>>any())).thenReturn(response);
        assertThat(client.getNick("TEST_COOKIE")).isEqualTo("TEST_BODY");
        var request = ArgumentCaptor.forClass(HttpRequest.class);
        verify(http).send(request.capture(), any());
        assertThat(request.getValue().uri().toString()).isEqualTo(
                "https://cgi.connect.qq.com/qqconnectopen/openapi/get_nick?retype=2&callback=t7QqNick");
        assertThat(request.getValue().headers().firstValue("Cookie")).contains("TEST_COOKIE");
        client.close();
        verify(http).close();
    }
    @SuppressWarnings("unchecked")
    @Test void upstreamErrorsAreUnavailableAndInterruptIsPreserved() throws Exception {
        var response = (HttpResponse<String>) mock(HttpResponse.class);
        when(http.send(any(), org.mockito.ArgumentMatchers.<HttpResponse.BodyHandler<String>>any())).thenReturn(response);
        when(response.statusCode()).thenReturn(302);
        fails();
        doThrow(new IOException("TEST_NETWORK_FAILURE")).when(http).send(any(), org.mockito.ArgumentMatchers.<HttpResponse.BodyHandler<String>>any());
        fails();
        doThrow(new InterruptedException()).when(http).send(any(), org.mockito.ArgumentMatchers.<HttpResponse.BodyHandler<String>>any());
        fails();
        assertThat(Thread.currentThread().isInterrupted()).isTrue();
    }
    private void fails() {
        assertThatThrownBy(() -> client.getNick("TEST_COOKIE")).isInstanceOfSatisfying(Problem.class,
                e -> assertThat(e.status()).isEqualTo(503));
    }
    @Test void malformedHeaderDoesNotExposeCookieInError() {
        assertThatThrownBy(() -> client.getNick("TEST_COOKIE\u0000"))
                .isInstanceOfSatisfying(Problem.class, error -> {
                    assertThat(error.status()).isEqualTo(400);
                    assertThat(error.getMessage()).doesNotContain("TEST_COOKIE");
                });
        verifyNoInteractions(http);
    }
}
