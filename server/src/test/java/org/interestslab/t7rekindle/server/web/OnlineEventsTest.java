package org.interestslab.t7rekindle.server.web;

import java.time.Instant;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.core.MethodParameter;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.bind.support.WebDataBinderFactory;
import org.springframework.web.context.request.NativeWebRequest;
import org.springframework.web.method.support.HandlerMethodArgumentResolver;
import org.springframework.web.method.support.ModelAndViewContainer;
import org.interestslab.t7rekindle.server.domain.Models.*;
import org.interestslab.t7rekindle.server.domain.Problem;
import org.interestslab.t7rekindle.server.service.PresenceService;
import tools.jackson.databind.json.JsonMapper;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

class OnlineEventsTest {
    private final PresenceService presence = mock(PresenceService.class);
    private final OnlineEvents events = new OnlineEvents(presence);
    private final Identity identity = new Identity("account", "session", "sample", Instant.MAX);
    private MockMvc mvc;

    @BeforeEach void setup() {
        mvc = MockMvcBuilders.standaloneSetup(events).setControllerAdvice(new WebErrors(JsonMapper.builder().build()))
                .setCustomArgumentResolvers(new HandlerMethodArgumentResolver() {
                    @Override public boolean supportsParameter(MethodParameter parameter) { return parameter.getParameterType() == Identity.class; }
                    @Override public Object resolveArgument(MethodParameter p, ModelAndViewContainer m, NativeWebRequest r,
                            WebDataBinderFactory f) { return identity; }
                }).build();
    }
    @Test void revalidatesThenKeepsAliveAndOnlyKicksMatchingConnection() throws Exception {
        var stream = mvc.perform(get("/api/v1/auth/online/connection/events")).andExpect(request().asyncStarted()).andReturn();
        verify(presence, times(2)).checkConnection(identity, "connection");
        assertThat(stream.getResponse().getHeader("X-Accel-Buffering")).isEqualTo("no");
        events.keepAlive();
        assertThat(stream.getResponse().getContentAsString()).contains("event:ready", ":keepalive");
        events.kick(new KickEvent("account", "session", "other-connection", "ADMIN_REVOKED"));
        events.kick(new KickEvent("account", "other-session", "connection", "ADMIN_REVOKED"));
        events.kick(new KickEvent("other-account", "session", "connection", "ADMIN_REVOKED"));
        assertThat(stream.getResponse().getContentAsString()).doesNotContain("kicked");
        events.kick(new KickEvent("account", "session", "connection", "ADMIN_REVOKED"));
        mvc.perform(asyncDispatch(stream)).andExpect(status().isOk());
        assertThat(stream.getResponse().getContentAsString()).contains("event:kicked", "ADMIN_REVOKED");
        events.kick(new KickEvent("account", "session", "connection", "ADMIN_REVOKED"));
    }
    @Test void reconnectCompletionDoesNotRemoveReplacementStream() throws Exception {
        var first = mvc.perform(get("/api/v1/auth/online/connection/events")).andReturn();
        var second = mvc.perform(get("/api/v1/auth/online/connection/events")).andReturn();
        mvc.perform(asyncDispatch(first)).andExpect(status().isOk());
        events.keepAlive();
        events.kick(new KickEvent("account", "session", "connection", "ONLINE_TAKEN_OVER"));
        mvc.perform(asyncDispatch(second)).andExpect(status().isOk());
        assertThat(first.getResponse().getContentAsString()).doesNotContain("kicked");
        assertThat(second.getResponse().getContentAsString()).contains("kicked", "ONLINE_TAKEN_OVER");
    }
    @Test void revocationBetweenValidationAndRegistrationNeverLeavesLiveStream() throws Exception {
        doNothing().doThrow(Problem.unauthorized()).when(presence).checkConnection(identity, "connection");
        mvc.perform(get("/api/v1/auth/online/connection/events")).andExpect(status().isUnauthorized());
        events.keepAlive();
    }
}
