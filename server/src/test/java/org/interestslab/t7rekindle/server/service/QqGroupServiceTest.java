package org.interestslab.t7rekindle.server.service;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.dao.DataAccessResourceFailureException;
import org.interestslab.t7rekindle.server.domain.Models.*;
import org.interestslab.t7rekindle.server.domain.Problem;
import org.interestslab.t7rekindle.server.domain.QqGroupModels.*;
import org.interestslab.t7rekindle.server.integration.*;
import org.interestslab.t7rekindle.server.persistence.*;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class QqGroupServiceTest {
    static final Instant NOW = Instant.parse("2026-01-01T00:00:00Z");
    static final Policy POLICY = policy(true, 1);
    private final QqGroupMapper db = mock(QqGroupMapper.class);
    private final AuthMapper accounts = mock(AuthMapper.class);
    private final QqGroupClient client = mock(QqGroupClient.class);
    private final QqCookieVault vault = mock(QqCookieVault.class);
    private final QqGroupCache cache = mock(QqGroupCache.class);
    private final SessionEvents events = mock(SessionEvents.class);
    private final QqCookies cookies = QqCookies.empty();
    private final QqGroupService service = new QqGroupService(db, accounts, client, vault, cache, events,
            TestTransactions.immediate(), Clock.fixed(NOW, ZoneOffset.UTC));
    @BeforeEach void setup() {
        when(db.policy()).thenReturn(POLICY);
        when(cache.lock(1, "12345")).thenReturn("lock");
        when(vault.decrypt("encrypted")).thenReturn(cookies);
        when(vault.encrypt(cookies)).thenReturn("updated");
        when(vault.ready()).thenReturn(true);
        when(client.member(cookies, "123456", "12345")).thenReturn(new QqGroupClient.Result<>(true, cookies));
    }
    static Policy policy(boolean enabled, long version) {
        return new Policy(enabled, "123456", "测试群", "54321", "管理员", "encrypted", version, 2, "READY", NOW);
    }
    @Test void disabledAndPasswordAccountsNeverQueryTencent() {
        service.require(null, false);
        when(db.policy()).thenReturn(policy(false, 1));
        service.require("12345", false);
        verifyNoInteractions(client, cache, vault);
    }
    @Test void successfulQueryRecordsActualCheckAndRefreshesCredentials() {
        service.require("12345", false);
        var decision = new Decision("123456", 1, "MEMBER", NOW);
        verify(db).record("12345", decision);
        verify(cache).put("12345", decision);
        verify(db).refreshed(2, "updated");
        verify(cache).unlock(1, "12345", "lock");
    }
    @Test void cacheHitsDoNotMoveDetectionTimestamp() {
        when(cache.get(1, "12345")).thenReturn(new Decision("123456", 1, "MEMBER", NOW.minusSeconds(30)));
        service.require("12345", false);
        verifyNoInteractions(client);
        verify(vault, never()).decrypt(any());
        verify(db, never()).record(anyString(), any());
        when(cache.get(1, "12345")).thenReturn(null, new Decision("123456", 1, "MEMBER", NOW));
        service.require("12345", true);
        verify(cache).unlock(1, "12345", "lock");
    }
    @Test void notMemberIncludesGroupAndClearsOnlineWithoutRevokingSession() {
        when(client.member(cookies, "123456", "12345")).thenReturn(new QqGroupClient.Result<>(false, cookies));
        var account = new Account("account", "12345", null, "ACTIVE", 0, NOW, "12345", "name", null);
        var owner = new Presence("account", "session", "connection", NOW.plusSeconds(45));
        when(accounts.lockByName("12345")).thenReturn(account);
        when(accounts.lockPresence("account")).thenReturn(owner);
        for (boolean online : new boolean[] {true, false}) {
            assertThatThrownBy(() -> service.require("12345", online)).isInstanceOfSatisfying(Problem.class, error -> {
                assertThat(error.code()).isEqualTo("QQ_GROUP_NOT_MEMBER");
                assertThat(error.getMessage()).contains("123456");
                assertThat(error.status()).isEqualTo(403);
            });
        }
        verify(events, times(2)).kick(owner, "QQ_GROUP_NOT_MEMBER", "123456");
        verify(accounts, never()).revokeSession(any(), any());
        when(accounts.lockByName("12345")).thenReturn(null,
                new Account("other", "12345", "hash", "ACTIVE", 0, NOW));
        assertThatThrownBy(() -> service.require("12345", false)).isInstanceOf(Problem.class);
        assertThatThrownBy(() -> service.require("12345", false)).isInstanceOf(Problem.class);
    }
    @Test void queryFailuresKeepExistingConnectionButBlockNewAdmission() {
        when(client.member(cookies, "123456", "12345")).thenThrow(Problem.unavailable());
        assertUnavailable(() -> service.require("12345", false));
        service.require("12345", true);
        verify(db, times(2)).record("12345", new Decision("123456", 1, "ERROR", NOW));
        verifyNoInteractions(accounts, events);
        when(cache.coolingDown(1)).thenReturn(true);
        assertUnavailable(() -> service.require("12345", false));
        service.require("12345", true);
    }
    @Test void missingKeyOrCorruptCookieDoesNotFabricateDetectionTime() {
        when(vault.decrypt("encrypted")).thenThrow(Problem.unavailable());
        when(vault.ready()).thenReturn(false);
        service.require("12345", true);
        verify(db).failed(2, "KEY_MISSING");
        when(vault.ready()).thenReturn(true);
        assertUnavailable(() -> service.require("12345", false));
        verify(db).failed(2, "QQ_GROUP_COOKIE_UNREADABLE");
        verify(db, never()).record(any(), any());
    }
    @Test void unhealthyCredentialsBypassCachedPositiveResultsUntilVerifiedAgain() {
        var bad = new Policy(true, "123456", "测试群", "54321", "管理员", "encrypted", 1, 2, "QQ_GROUP_COOKIE_EXPIRED", NOW);
        when(db.policy()).thenReturn(bad);
        when(cache.get(1, "12345")).thenReturn(new Decision("123456", 1, "MEMBER", NOW.minusSeconds(20)));
        service.require("12345", false);
        verify(client).member(cookies, "123456", "12345");
        verify(cache, never()).get(anyLong(), any());
        when(cache.lock(1, "12345")).thenReturn(null);
        assertUnavailable(() -> service.require("12345", false));
        service.require("12345", true);
    }
    @Test void changedConfigurationDoesNotPopulateOldCache() {
        when(db.policy()).thenReturn(POLICY, policy(true, 3));
        assertUnavailable(() -> service.require("12345", false));
        verify(cache, never()).put(any(), any());
        when(db.policy()).thenReturn(POLICY, policy(false, 3));
        service.require("12345", false);
    }
    @Test void cacheWriteAndUnlockFailuresNeverOverrideConfirmedNonMembership() {
        when(client.member(cookies, "123456", "12345")).thenReturn(new QqGroupClient.Result<>(false, cookies));
        doThrow(new DataAccessResourceFailureException("TEST")).when(cache).put(any(), any());
        doThrow(new DataAccessResourceFailureException("TEST")).when(cache).unlock(anyLong(), any(), any());
        assertThatThrownBy(() -> service.require("12345", true)).isInstanceOfSatisfying(Problem.class,
                error -> assertThat(error.code()).isEqualTo("QQ_GROUP_NOT_MEMBER"));
    }
    @Test void cacheOutageFollowsTheAdmissionPolicy() {
        when(cache.get(1, "12345")).thenThrow(new DataAccessResourceFailureException("TEST"));
        assertUnavailable(() -> service.require("12345", false));
        service.require("12345", true);
        when(db.policy()).thenThrow(new Problem(400, "INVALID_INPUT", "test"));
        assertThatThrownBy(() -> service.require("12345", true)).isInstanceOfSatisfying(Problem.class,
                error -> assertThat(error.status()).isEqualTo(400));
    }
    @Test void coalescedRequestUsesPublishedResultAndPreservesInterrupt() {
        when(cache.lock(1, "12345")).thenReturn(null);
        when(cache.get(1, "12345")).thenReturn(null, new Decision("123456", 1, "MEMBER", NOW));
        service.require("12345", false);
        verifyNoInteractions(client);
        when(cache.get(1, "12345")).thenReturn(null);
        Thread.currentThread().interrupt();
        try {
            assertUnavailable(() -> service.require("12345", false));
            assertThat(Thread.currentThread().isInterrupted()).isTrue();
        } finally { Thread.interrupted(); }
        assertUnavailable(() -> service.require("12345", false));
    }
    static void assertUnavailable(org.assertj.core.api.ThrowableAssert.ThrowingCallable call) {
        assertThatThrownBy(call).isInstanceOfSatisfying(Problem.class, error -> {
            assertThat(error.code()).isEqualTo("QQ_GROUP_UNAVAILABLE");
            assertThat(error.status()).isEqualTo(503);
        });
    }
}
