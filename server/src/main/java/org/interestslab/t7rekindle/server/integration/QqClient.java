package org.interestslab.t7rekindle.server.integration;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import jakarta.annotation.PreDestroy;
import org.springframework.stereotype.Component;
import org.interestslab.t7rekindle.server.domain.Problem;

@Component
public class QqClient {
    private static final URI GET_NICK = URI.create(
            "https://cgi.connect.qq.com/qqconnectopen/openapi/get_nick?retype=2&callback=t7QqNick");
    private final HttpClient client;

    public QqClient() {
        this(HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5))
                .followRedirects(HttpClient.Redirect.NEVER).build());
    }
    QqClient(HttpClient client) { this.client = client; }

    public String getNick(String cookie) {
        try {
            var request = HttpRequest.newBuilder(GET_NICK).timeout(Duration.ofSeconds(8))
                    .header("Cookie", cookie).header("Referer", "https://connect.qq.com/").GET().build();
            var response = client.send(request, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() != 200) throw Problem.unavailable();
            return response.body();
        } catch (IllegalArgumentException error) {
            throw Problem.invalid();
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            throw Problem.unavailable();
        } catch (IOException error) {
            throw Problem.unavailable();
        }
    }

    @PreDestroy void close() { client.close(); }
}
