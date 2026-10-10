package org.interestslab.t7rekindle.server.config;

import org.slf4j.LoggerFactory;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.redis.connection.RedisConnectionFactory;
import org.springframework.data.redis.listener.ChannelTopic;
import org.springframework.data.redis.listener.RedisMessageListenerContainer;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.interestslab.t7rekindle.server.domain.Models.KickEvent;
import org.interestslab.t7rekindle.server.service.SessionEvents;
import tools.jackson.databind.json.JsonMapper;

@Configuration
@EnableScheduling
public class OnlinePushConfig {
    @Bean
    public RedisMessageListenerContainer sessionNotifications(RedisConnectionFactory connections,
            ApplicationEventPublisher local, JsonMapper json) {
        var container = new RedisMessageListenerContainer();
        container.setConnectionFactory(connections);
        container.addMessageListener((message, pattern) -> {
            try { local.publishEvent(json.readValue(message.getBody(), KickEvent.class)); }
            catch (RuntimeException error) { LoggerFactory.getLogger(OnlinePushConfig.class).warn("Invalid session notification"); }
        }, new ChannelTopic(SessionEvents.CHANNEL));
        return container;
    }
}
