package org.interestslab.t7rekindle.server.service;

import java.time.Instant;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.interestslab.t7rekindle.server.domain.Models.*;
import tools.jackson.databind.json.JsonMapper;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class SessionEventsTest {
    private final ApplicationEventPublisher local = mock(ApplicationEventPublisher.class);
    private final StringRedisTemplate redis = mock(StringRedisTemplate.class);
    private final SessionEvents events = new SessionEvents(local, redis, JsonMapper.builder().build());
    private final Presence owner = new Presence("account", "session", "connection", Instant.MAX);

    @AfterEach void clear() {
        if (TransactionSynchronizationManager.isSynchronizationActive()) TransactionSynchronizationManager.clearSynchronization();
    }
    @Test void onlyPublishesAfterCommitAndNeverOnRollback() {
        events.kick(null, "reason");
        TransactionSynchronizationManager.initSynchronization();
        events.kick(owner, "ADMIN_REVOKED");
        verifyNoInteractions(local, redis);
        var callback = TransactionSynchronizationManager.getSynchronizations().getFirst();
        callback.afterCompletion(1);
        verifyNoInteractions(local, redis);
        callback.afterCommit();
        verify(local).publishEvent(new KickEvent("account", "session", "connection", "ADMIN_REVOKED"));
        verify(redis).convertAndSend(eq(SessionEvents.CHANNEL), contains("ADMIN_REVOKED"));
    }
    @Test void notificationFailuresDoNotChangeSuccessfulDatabaseResult() {
        doThrow(new IllegalStateException("local error")).when(local).publishEvent(any(Object.class));
        doThrow(new IllegalStateException("redis error")).when(redis).convertAndSend(anyString(), any());
        assertThatCode(() -> events.kick(owner, "MACHINE_CHANGED")).doesNotThrowAnyException();
        verify(redis).convertAndSend(eq(SessionEvents.CHANNEL), contains("connection"));
    }
}
