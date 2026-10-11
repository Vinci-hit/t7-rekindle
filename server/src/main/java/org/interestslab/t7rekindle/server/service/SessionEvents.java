package org.interestslab.t7rekindle.server.service;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.interestslab.t7rekindle.server.domain.Models.*;
import tools.jackson.databind.json.JsonMapper;

@Component
public class SessionEvents {
    public static final String CHANNEL = "rekindle:auth:kicked";
    private static final Logger LOG = LoggerFactory.getLogger(SessionEvents.class);
    private final ApplicationEventPublisher local;
    private final StringRedisTemplate redis;
    private final JsonMapper json;

    public SessionEvents(ApplicationEventPublisher local, StringRedisTemplate redis, JsonMapper json) {
        this.local = local;
        this.redis = redis;
        this.json = json;
    }

    public void kick(Presence owner, String reason) {
        kick(owner, reason, null);
    }
    public void kick(Presence owner, String reason, String groupNumber) {
        if (owner == null) return;
        var event = new KickEvent(owner.accountId(), owner.sessionId(), owner.connectionId(), reason, groupNumber);
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                @Override public void afterCommit() { publish(event); }
            });
        } else {
            publish(event);
        }
    }

    private void publish(KickEvent event) {
        try { local.publishEvent(event); }
        catch (RuntimeException error) { LOG.warn("Local session notification failed"); }
        try { redis.convertAndSend(CHANNEL, json.writeValueAsString(event)); }
        catch (RuntimeException error) { LOG.warn("Session notification relay unavailable; heartbeat remains authoritative"); }
    }
}
