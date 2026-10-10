package org.interestslab.t7rekindle.server.web;

import java.io.IOException;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import org.springframework.context.event.EventListener;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;
import org.interestslab.t7rekindle.server.domain.Models.*;
import org.interestslab.t7rekindle.server.service.PresenceService;

@RestController
@RequestMapping("/api/v1/auth/online")
public class OnlineEvents {
    private record Stream(Identity identity, SseEmitter emitter) { }
    private final Map<String, Stream> streams = new ConcurrentHashMap<>();
    private final PresenceService presence;

    public OnlineEvents(PresenceService presence) { this.presence = presence; }

    @GetMapping(value = "/{connectionId}/events", produces = "text/event-stream")
    public SseEmitter connect(@AuthenticationPrincipal Identity identity, @PathVariable String connectionId,
            jakarta.servlet.http.HttpServletResponse response) throws IOException {
        presence.checkConnection(identity, connectionId);
        var emitter = new SseEmitter(0L);
        var stream = new Stream(identity, emitter);
        emitter.onCompletion(() -> streams.remove(connectionId, stream));
        emitter.onTimeout(() -> streams.remove(connectionId, stream));
        emitter.onError(error -> streams.remove(connectionId, stream));
        Stream previous = streams.put(connectionId, stream);
        if (previous != null) previous.emitter().complete();
        try {
            presence.checkConnection(identity, connectionId);
            response.setHeader("X-Accel-Buffering", "no");
            emitter.send(SseEmitter.event().name("ready").data(Map.of("connectionId", connectionId)));
            return emitter;
        } catch (RuntimeException | IOException error) {
            streams.remove(connectionId, stream);
            emitter.complete();
            throw error;
        }
    }

    @EventListener
    public void kick(KickEvent event) {
        Stream stream = streams.get(event.connectionId());
        if (stream == null || !stream.identity().sessionId().equals(event.sessionId())
                || !stream.identity().accountId().equals(event.accountId())) return;
        if (!streams.remove(event.connectionId(), stream)) return;
        try {
            stream.emitter().send(SseEmitter.event().name("kicked").data(event));
            stream.emitter().complete();
        } catch (IOException | IllegalStateException error) {
            // Disconnected clients discover revocation on heartbeat or reconnect.
        }
    }

    @Scheduled(fixedDelay = 15000)
    public void keepAlive() {
        streams.forEach((id, stream) -> {
            try { stream.emitter().send(SseEmitter.event().comment("keepalive")); }
            catch (IOException | IllegalStateException error) { streams.remove(id, stream); }
        });
    }
}
