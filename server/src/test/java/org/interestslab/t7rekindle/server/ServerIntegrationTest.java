package org.interestslab.t7rekindle.server;

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.Executors;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.mock.web.MockHttpSession;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.mysql.MySQLContainer;
import org.testcontainers.utility.DockerImageName;
import org.interestslab.t7rekindle.server.config.SecurityConfig;
import org.interestslab.t7rekindle.server.config.OnlinePushConfig;
import org.interestslab.t7rekindle.server.integration.QqClient;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.interestslab.t7rekindle.server.domain.Models.*;
import org.interestslab.t7rekindle.server.domain.Problem;
import org.interestslab.t7rekindle.server.persistence.AdminMapper;
import org.interestslab.t7rekindle.server.persistence.AuthMapper;
import org.interestslab.t7rekindle.server.service.*;
import tools.jackson.databind.json.JsonMapper;
import static org.assertj.core.api.Assertions.*;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.csrf;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;
import static org.mockito.Mockito.when;

@SpringBootTest(properties = {"server.servlet.session.cookie.secure=false", "spring.flyway.enabled=true"})
@AutoConfigureMockMvc
class ServerIntegrationTest {
    static final MySQLContainer MYSQL = new MySQLContainer("mysql:8.4.11").withTmpFs(java.util.Map.of("/var/lib/mysql", "rw"));
    static final GenericContainer<?> REDIS = new GenericContainer<>(DockerImageName.parse("redis:8.6.6")).withExposedPorts(6379);
    static {
        MYSQL.start();
        REDIS.start();
    }
    @DynamicPropertySource static void properties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", MYSQL::getJdbcUrl);
        registry.add("spring.datasource.username", MYSQL::getUsername);
        registry.add("spring.datasource.password", MYSQL::getPassword);
        registry.add("spring.data.redis.host", REDIS::getHost);
        registry.add("spring.data.redis.port", () -> REDIS.getMappedPort(6379));
    }
    @Autowired MockMvc mvc;
    @Autowired JsonMapper json;
    @Autowired JdbcTemplate jdbc;
    @Autowired Flyway flyway;
    @Autowired StringRedisTemplate redis;
    @Autowired Secrets secrets;
    @Autowired AdminService admins;
    @Autowired AccountService accounts;
    @Autowired AuthService auth;
    @Autowired QqAuthService qq;
    @Autowired SessionEvents events;
    @MockitoBean QqClient qqClient;
    @Autowired PresenceService presence;
    @Autowired AuthMapper db;
    @Autowired AdminMapper adminDb;
    private static final String PASSWORD = "TEST_PASSWORD";
    private static final String NEXT_PASSWORD = "CHANGED_TEST_PASSWORD";
    private static final AdminIdentity ADMIN = new AdminIdentity(0, false);

    @Test void qqExchangeBindsMachineAndPushesOldConnectionAfterNewMachineLogin() throws Exception {
        String machine = "12345678-1234-1234-1234-123456789abc";
        String other = "12345678-1234-1234-1234-123456789def";
        String cookie = "uin=o12345; skey=TEST_SESSION";
        when(qqClient.getNick(cookie)).thenReturn("t7QqNick({\"retcode\":0,\"result\":{\"nick\":\"测试昵称\"}})");
        var response = mvc.perform(post("/api/v1/auth/qq/sessions").contentType("application/json")
                .content(json.writeValueAsString(java.util.Map.of("machineId", machine, "qqSessionCookie", cookie))))
                .andExpect(status().isCreated()).andExpect(jsonPath("$.loginName").value("12345")).andReturn();
        var first = json.readValue(response.getResponse().getContentAsString(), QqSessionCredentials.class);
        mvc.perform(get("/api/v1/auth/session").header("Authorization", "Bearer " + first.sessionToken())).andExpect(status().isUnauthorized());
        mvc.perform(get("/api/v1/auth/session").header("Authorization", "Bearer " + first.sessionToken()).header("X-Machine-Id", other))
                .andExpect(status().isUnauthorized());
        var identity = auth.authenticate(first.sessionToken(), machine);
        var online = presence.online(identity);
        var stream = mvc.perform(get("/api/v1/auth/online/" + online.connectionId() + "/events")
                .header("Authorization", "Bearer " + first.sessionToken()).header("X-Machine-Id", machine))
                .andExpect(request().asyncStarted()).andReturn();
        assertThat(stream.getResponse().getContentAsString()).contains("event:ready");
        var second = qq.login(other, cookie, "127.0.0.1");
        assertThat(second.accountId()).isEqualTo(first.accountId());
        assertThat(stream.getResponse().getContentAsString()).contains("event:kicked", "MACHINE_CHANGED", online.connectionId());
        mvc.perform(asyncDispatch(stream)).andExpect(status().isOk());
        assertThatThrownBy(() -> auth.authenticate(first.sessionToken(), machine)).isInstanceOf(Problem.class);
        assertThat(auth.authenticate(second.sessionToken(), other).nickname()).isEqualTo("测试昵称");
        assertThat(db.account(first.accountId()).passwordHash()).isNull();
        assertThatThrownBy(() -> accounts.resetPassword(ADMIN, first.accountId())).isInstanceOf(Problem.class);
    }

    @Test void legacyNumericPasswordAccountIsPreservedAndConcurrentQqRegistrationIsUnique() throws Exception {
        db.createAccount("00000000-0000-0000-0000-000000000001", "23456", secrets.encode(PASSWORD));
        assertThat(auth.login("23456", PASSWORD, "IP")).isNotNull();
        String cookie = "uin=23456; skey=TEST_SESSION";
        when(qqClient.getNick(cookie)).thenReturn("t7QqNick({\"retcode\":0,\"result\":{\"nick\":\"name\"}})");
        assertThatThrownBy(() -> qq.login("12345678-1234-1234-1234-123456789abc", cookie, "IP"))
                .isInstanceOfSatisfying(Problem.class, e -> assertThat(e.code()).isEqualTo("QQ_ACCOUNT_CONFLICT"));
        String concurrentCookie = "uin=34567; skey=TEST_SESSION";
        when(qqClient.getNick(concurrentCookie)).thenReturn("t7QqNick({\"retcode\":0,\"result\":{\"nick\":\"name\"}})");
        try (var pool = Executors.newFixedThreadPool(4)) {
            var work = java.util.stream.IntStream.range(0, 4).<Callable<QqSessionCredentials>>mapToObj(i ->
                    () -> qq.login("12345678-1234-1234-1234-123456789abc", concurrentCookie, "IP")).toList();
            var ids = new java.util.HashSet<String>();
            for (var result : pool.invokeAll(work)) ids.add(result.get().accountId());
            assertThat(ids).hasSize(1);
        }
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM account WHERE qq_number='34567'", Integer.class)).isEqualTo(1);
    }

    @Test void committedKickIsRelayedToAnotherInstanceSubscriber() throws Exception {
        var received = new java.util.concurrent.CompletableFuture<KickEvent>();
        var subscriber = new OnlinePushConfig().sessionNotifications(redis.getConnectionFactory(),
                event -> received.complete((KickEvent) event), json);
        try {
            subscriber.afterPropertiesSet();
            subscriber.start();
            events.kick(new Presence("account", "session", "connection", java.time.Instant.MAX), "ADMIN_REVOKED");
            assertThat(received.get(5, java.util.concurrent.TimeUnit.SECONDS).connectionId()).isEqualTo("connection");
        } finally { subscriber.destroy(); }
    }

    @BeforeEach void reset() {
        jdbc.update("DELETE FROM account_presence");
        jdbc.update("DELETE FROM auth_session");
        jdbc.update("DELETE FROM account");
        jdbc.update("DELETE FROM audit_log");
        jdbc.update("UPDATE admin_account SET password_hash=?,must_change_password=FALSE,auth_epoch=0", secrets.encode(PASSWORD));
        try (var connection = redis.getConnectionFactory().getConnection()) { connection.serverCommands().flushDb(); }
    }

    @Test void startupMigratesBeforeInitializingAdmin() {
        assertThat(flyway.info().applied()).extracting(migration -> migration.getScript())
                .containsExactly("V001__authentication.sql", "V002__administration.sql", "V003__qq_authentication.sql");
        assertThat(flyway.info().pending()).isEmpty();
        assertThat(adminDb.admin()).isNotNull();
        assertThat(admins.initialize()).isNull();
    }

    @Test void fixedExpiryAndExplicitTakeoverKeepBothCredentialsValid() {
        var created = accounts.create(ADMIN, "sample-user");
        var first = auth.login(created.loginName(), created.password(), "127.0.0.1");
        var a = auth.authenticate(first.sessionToken());
        var onlineA = presence.online(a);
        var second = auth.login(created.loginName(), created.password(), "127.0.0.1");
        var b = auth.authenticate(second.sessionToken());
        assertThat(presence.heartbeat(a, onlineA.connectionId())).isNotNull();
        var onlineB = presence.online(b);
        assertThatThrownBy(() -> presence.heartbeat(a, onlineA.connectionId())).isInstanceOfSatisfying(Problem.class,
                error -> assertThat(error.code()).isEqualTo("ONLINE_TAKEN_OVER"));
        presence.offline(a, onlineA.connectionId());
        assertThat(presence.heartbeat(b, onlineB.connectionId())).isNotNull();
        assertThat(auth.authenticate(first.sessionToken())).isEqualTo(a);
        var replacement = presence.online(b);
        assertThatThrownBy(() -> presence.heartbeat(b, onlineB.connectionId())).isInstanceOf(Problem.class);
        presence.offline(b, replacement.connectionId());
        assertThat(auth.authenticate(second.sessionToken())).isEqualTo(b);
        var stored = db.sessionByHash(Secrets.hash(first.sessionToken()));
        assertThat(Duration.between(stored.createdAt(), stored.expiresAt())).isEqualTo(Duration.ofDays(30));
        assertThat(stored.tokenHash()).isNotEqualTo(first.sessionToken());
        assertThat(accounts.sessions(a.accountId(), 0)).allMatch(item -> item.credentialState().equals("VALID"));
    }

    @Test void expiryCannotBeRenewedByHeartbeatAndSessionDeadlineCapsLease() {
        var identity = accountIdentity("expiring-user");
        jdbc.update("UPDATE auth_session SET expires_at=UTC_TIMESTAMP(3)+INTERVAL 5 SECOND WHERE session_id=?", identity.sessionId());
        var connection = presence.online(identity);
        assertThat(connection.leaseExpiresAt()).isEqualTo(db.lockSession(identity.sessionId()).expiresAt());
        jdbc.update("UPDATE account_presence SET lease_expires_at=UTC_TIMESTAMP(3) WHERE account_id=?", identity.accountId());
        assertThatThrownBy(() -> presence.heartbeat(identity, connection.connectionId())).isInstanceOfSatisfying(Problem.class,
                error -> assertThat(error.code()).isEqualTo("NOT_ONLINE"));
        jdbc.update("UPDATE auth_session SET created_at=UTC_TIMESTAMP(3)-INTERVAL 31 DAY,expires_at=UTC_TIMESTAMP(3)-INTERVAL 1 DAY WHERE session_id=?", identity.sessionId());
        assertThatThrownBy(() -> presence.online(identity)).isInstanceOf(Problem.class);
        assertThat(accounts.sessions(identity.accountId(), 0).getFirst().credentialState()).isEqualTo("EXPIRED");
    }

    @Test void concurrentOnlineRequestsHaveExactlyOneOwner() throws Exception {
        var identity = accountIdentity("concurrent-user");
        List<Callable<OnlineConnection>> work = new ArrayList<>();
        for (int i = 0; i < 100; i++) work.add(() -> presence.online(identity));
        try (var pool = Executors.newFixedThreadPool(12)) {
            var outcomes = pool.invokeAll(work);
            for (var outcome : outcomes) assertThat(outcome.get()).isNotNull();
        }
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM account_presence", Integer.class)).isEqualTo(1);
        assertThat(accounts.sessions(identity.accountId(), 0)).filteredOn(SessionView::online).hasSize(1);
    }

    @Test void revocationRacingOnlineAcquisitionNeverResurrectsPresence() throws Exception {
        var identity = accountIdentity("racing-user");
        List<Callable<Void>> work = new ArrayList<>();
        for (int i = 0; i < 40; i++) {
            work.add(() -> {
                try { presence.online(identity); }
                catch (Problem error) { assertThat(error.status()).isEqualTo(401); }
                return null;
            });
        }
        work.add(10, () -> { accounts.resetPassword(ADMIN, identity.accountId()); return null; });
        try (var pool = Executors.newFixedThreadPool(8)) {
            for (var result : pool.invokeAll(work)) result.get();
        }
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM account_presence", Integer.class)).isZero();
        assertThatThrownBy(() -> presence.online(identity)).isInstanceOf(Problem.class);
    }

    @Test void revocationResetAndDisableReleasePresenceWithoutRevivingOldTokens() {
        var identity = accountIdentity("managed-user");
        presence.online(identity);
        accounts.setEnabled(ADMIN, identity.accountId(), false);
        accounts.setEnabled(ADMIN, identity.accountId(), true);
        assertThatThrownBy(() -> presence.online(identity)).isInstanceOf(Problem.class);
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM account_presence", Integer.class)).isZero();
        var password = accounts.resetPassword(ADMIN, identity.accountId());
        var credentials = auth.login(password.loginName(), password.password(), "127.0.0.1");
        var fresh = auth.authenticate(credentials.sessionToken());
        presence.online(fresh);
        accounts.revoke(ADMIN, fresh.accountId(), fresh.sessionId());
        assertThatThrownBy(() -> auth.authenticate(credentials.sessionToken())).isInstanceOf(Problem.class);
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM account_presence", Integer.class)).isZero();
        fresh = auth.authenticate(auth.login(password.loginName(), password.password(), "127.0.0.1").sessionToken());
        presence.online(fresh);
        accounts.revokeAll(ADMIN, fresh.accountId());
        assertThatThrownBy(() -> presence.online(identity)).isInstanceOf(Problem.class);
        assertThat(accounts.sessions(identity.accountId(), 0)).allMatch(item -> item.credentialState().equals("REVOKED"));
    }

    @Test void apiUsesBearerOnlyAndNeverCreatesBrowserSession() throws Exception {
        var created = accounts.create(ADMIN, "api-user");
        String body = json.writeValueAsString(new org.interestslab.t7rekindle.server.web.ApiController.Login(created.loginName(), created.password()));
        var login = mvc.perform(post("/api/v1/auth/sessions").contentType("application/json").content(body))
                .andExpect(status().isCreated()).andExpect(header().string("Cache-Control", "no-store"))
                .andExpect(cookie().doesNotExist("REKINDLE_ADMIN")).andReturn();
        var credentials = json.readValue(login.getResponse().getContentAsString(), SessionCredentials.class);
        String bearer = "Bearer " + credentials.sessionToken();
        mvc.perform(get("/api/v1/auth/session").header("Authorization", bearer)).andExpect(status().isOk()).andExpect(jsonPath("$.sessionToken").doesNotExist());
        var online = mvc.perform(post("/api/v1/auth/online").header("Authorization", bearer)).andExpect(status().isOk()).andReturn();
        String connectionId = json.readTree(online.getResponse().getContentAsString()).get("connectionId").asString();
        mvc.perform(post("/api/v1/auth/online/{id}/heartbeat", connectionId).header("Authorization", bearer)).andExpect(status().isOk());
        mvc.perform(delete("/api/v1/auth/online/{id}", connectionId).header("Authorization", bearer)).andExpect(status().isNoContent());
        mvc.perform(get("/admin/accounts").header("Authorization", bearer)).andExpect(redirectedUrl("/admin/login"));
        mvc.perform(get("/api/v1/auth/session").session(adminSession())).andExpect(status().isUnauthorized());
        mvc.perform(delete("/api/v1/auth/session").header("Authorization", bearer)).andExpect(status().isNoContent());
        mvc.perform(get("/api/v1/auth/session").header("Authorization", bearer)).andExpect(status().isUnauthorized());
        mvc.perform(post("/api/v1/auth/sessions").contentType("application/json").content("{"))
                .andExpect(status().isBadRequest()).andExpect(jsonPath("$.code").value("INVALID_INPUT"));
    }

    @Test void embeddedPagesCsrfAndOneTimePasswordsWork() throws Exception {
        mvc.perform(get("/admin/login")).andExpect(status().isOk()).andExpect(content().string(org.hamcrest.Matchers.containsString("_csrf")));
        mvc.perform(get("/admin/assets/admin.css")).andExpect(status().isOk());
        mvc.perform(get("/admin/assets/mark.svg")).andExpect(status().isOk());
        mvc.perform(post("/admin/login").param("loginName", "admin").param("password", PASSWORD)).andExpect(status().isForbidden());
        MockHttpSession session = adminSession();
        mvc.perform(get("/admin/accounts").session(session)).andExpect(status().isOk());
        var created = mvc.perform(post("/admin/accounts").session(session).with(csrf()).param("loginName", "ui-user"))
                .andExpect(status().is3xxRedirection()).andReturn();
        String resultUrl = created.getResponse().getRedirectedUrl();
        mvc.perform(get(resultUrl).session(session)).andExpect(status().isOk())
                .andExpect(content().string(org.hamcrest.Matchers.containsString("data-secret")));
        mvc.perform(get(resultUrl).session(session)).andExpect(status().isOk())
                .andExpect(content().string(org.hamcrest.Matchers.not(org.hamcrest.Matchers.containsString("data-secret"))));
        String id = accounts.list("ui-user", 0).getFirst().accountId();
        mvc.perform(get("/admin/accounts/{id}", id).session(session)).andExpect(status().isOk());
        mvc.perform(get("/admin/audit").session(session)).andExpect(status().isOk());
        mvc.perform(get("/admin/password").session(session)).andExpect(status().isOk());
        mvc.perform(post("/admin/accounts/{id}/status", id).session(session).with(csrf()).param("enabled", "false")).andExpect(status().is3xxRedirection());
        mvc.perform(post("/admin/accounts/{id}/password", id).session(session).with(csrf())).andExpect(status().is3xxRedirection());
        mvc.perform(post("/admin/accounts/{id}/sessions/revoke-all", id).session(session).with(csrf())).andExpect(status().is3xxRedirection());
        mvc.perform(get("/admin/accounts").session(session).param("page", "-1")).andExpect(status().isBadRequest());
    }

    @Test void firstLoginForcesPasswordChangeAndRotatesSession() throws Exception {
        jdbc.update("UPDATE admin_account SET must_change_password=TRUE");
        MockHttpSession anonymous = new MockHttpSession();
        var login = mvc.perform(post("/admin/login").session(anonymous).with(csrf()).param("loginName", "admin").param("password", PASSWORD))
                .andExpect(redirectedUrl("/admin/password")).andReturn();
        MockHttpSession authenticated = (MockHttpSession) login.getRequest().getSession(false);
        assertThat(anonymous.isInvalid()).isTrue();
        mvc.perform(get("/admin/accounts").session(authenticated)).andExpect(redirectedUrl("/admin/password"));
        mvc.perform(post("/admin/password").session(authenticated).with(csrf()).param("oldPassword", PASSWORD)
                .param("newPassword", NEXT_PASSWORD).param("confirmation", NEXT_PASSWORD))
                .andExpect(redirectedUrl("/admin/login?changed"));
        assertThat(authenticated.isInvalid()).isTrue();
        assertThatThrownBy(() -> admins.current(new AdminIdentity(0, true))).isInstanceOf(Problem.class);
        assertThat(admins.login("admin", NEXT_PASSWORD, "127.0.0.1").mustChangePassword()).isFalse();
    }

    @Test void bootstrapIsPersistentAndConcurrentInitializationHasOneWinner() throws Exception {
        assertThat(admins.initialize()).isNull();
        String before = adminDb.admin().passwordHash();
        assertThat(admins.initialize()).isNull();
        assertThat(adminDb.admin().passwordHash()).isEqualTo(before);
        jdbc.update("DELETE FROM admin_account");
        List<Callable<String>> initializers = new ArrayList<>();
        for (int i = 0; i < 8; i++) initializers.add(admins::initialize);
        int winners = 0;
        try (var pool = Executors.newFixedThreadPool(8)) {
            for (var result : pool.invokeAll(initializers)) if (result.get() != null) winners++;
        }
        assertThat(winners).isEqualTo(1);
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM audit_log WHERE action='ADMIN_INITIALIZED'", Integer.class)).isEqualTo(1);
        String recovered = admins.reset();
        assertThat(admins.login("admin", recovered, "127.0.0.1").mustChangePassword()).isTrue();
    }

    @Test void realRedisRateLimitAndAuditNeverContainPasswords() {
        var created = accounts.create(ADMIN, "limited-user");
        for (int i = 0; i < 10; i++) {
            assertThatThrownBy(() -> auth.login(created.loginName(), "WRONG_PASSWORD", "127.0.0.1")).isInstanceOf(Problem.class);
        }
        assertThatThrownBy(() -> auth.login(created.loginName(), created.password(), "127.0.0.1"))
                .isInstanceOfSatisfying(Problem.class, error -> assertThat(error.status()).isEqualTo(429));
        assertThat(accounts.audits(0).toString()).doesNotContain(created.password(), "WRONG_PASSWORD", PASSWORD);
    }

    private Identity accountIdentity(String name) {
        var created = accounts.create(ADMIN, name);
        return auth.authenticate(auth.login(name, created.password(), "127.0.0.1").sessionToken());
    }
    private MockHttpSession adminSession() {
        var session = new MockHttpSession();
        session.setAttribute(SecurityConfig.ADMIN_IDENTITY, ADMIN);
        return session;
    }
}
