package org.interestslab.t7rekindle.server.integration;

import java.time.Duration;
import java.util.List;
import java.util.UUID;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Component;
import org.interestslab.t7rekindle.server.domain.QqGroupModels.Decision;
import tools.jackson.databind.json.JsonMapper;

@Component
public class QqGroupCache {
    private static final DefaultRedisScript<Long> RELEASE = new DefaultRedisScript<>(
            "if redis.call('GET',KEYS[1]) == ARGV[1] then return redis.call('DEL',KEYS[1]) end return 0", Long.class);
    private final StringRedisTemplate redis;
    private final JsonMapper json;
    public QqGroupCache(StringRedisTemplate redis, JsonMapper json) { this.redis = redis; this.json = json; }
    private String key(long version, String qq) { return "rekindle:qq-group:" + version + ":" + qq; }
    public Decision get(long version, String qq) {
        String value = redis.opsForValue().get(key(version, qq));
        return value == null ? null : json.readValue(value, Decision.class);
    }
    public boolean coolingDown(long version) { return Boolean.TRUE.equals(redis.hasKey(key(version, "error"))); }
    public void put(String qq, Decision decision) {
        Duration ttl = switch (decision.result()) {
            case "MEMBER" -> Duration.ofMinutes(15);
            case "NOT_MEMBER" -> Duration.ofSeconds(1);
            default -> Duration.ofMinutes(1);
        };
        redis.opsForValue().set(key(decision.policyVersion(), qq), json.writeValueAsString(decision), ttl);
        if (decision.result().equals("ERROR")) redis.opsForValue().set(key(decision.policyVersion(), "error"), "1", ttl);
    }
    public String lock(long version, String qq) {
        String token = UUID.randomUUID().toString();
        return Boolean.TRUE.equals(redis.opsForValue().setIfAbsent(key(version, qq) + ":lock", token, Duration.ofSeconds(10))) ? token : null;
    }
    public void unlock(long version, String qq, String token) { redis.execute(RELEASE, List.of(key(version, qq) + ":lock"), token); }
}
