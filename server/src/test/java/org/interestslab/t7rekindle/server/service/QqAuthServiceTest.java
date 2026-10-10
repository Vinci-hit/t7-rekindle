package org.interestslab.t7rekindle.server.service;

import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.dao.DuplicateKeyException;
import org.interestslab.t7rekindle.server.domain.Models.*;
import org.interestslab.t7rekindle.server.domain.Problem;
import org.interestslab.t7rekindle.server.persistence.AdminMapper;
import org.interestslab.t7rekindle.server.persistence.AuthMapper;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class QqAuthServiceTest {
    private static final String MACHINE = "12345678-1234-1234-1234-123456789abc";
    private static final String COOKIE = "uin=o12345; skey=TEST_SESSION";
    private final Instant now = Instant.parse("2026-01-01T00:00:00Z");
    private final QqProfile profile = new QqProfile("12345", "测试昵称", "https://q.qlogo.cn/g?b=qq&nk=12345&s=100");
    private final Account account = new Account("id", "12345", null, "ACTIVE", 0, now, "12345", "旧昵称", profile.avatarUrl());
    private final AuthMapper db = mock(AuthMapper.class);
    private final AdminMapper audit = mock(AdminMapper.class);
    private final Secrets secrets = mock(Secrets.class);
    private final LoginLimiter limiter = mock(LoginLimiter.class);
    private final QqSessionVerifier verifier = mock(QqSessionVerifier.class);
    private final SessionEvents events = mock(SessionEvents.class);
    private final QqAuthService service = new QqAuthService(db, audit, secrets, limiter, verifier, events, TestTransactions.immediate());

    @BeforeEach void setup() {
        when(verifier.verify(COOKIE)).thenReturn(profile);
        when(db.lockByName("12345")).thenReturn(account);
        when(db.now()).thenReturn(now);
        when(secrets.randomToken()).thenReturn("A".repeat(43));
    }

    @Test void createsQqAccountWithNumericLoginAndFixedMachineBoundSession() {
        when(db.lockByName("12345")).thenReturn(null, account);
        var result = service.login(MACHINE.toUpperCase(), COOKIE, "IP");
        assertThat(result.loginName()).isEqualTo("12345");
        assertThat(result.machineId()).isEqualTo(MACHINE);
        assertThat(result.nickname()).isEqualTo(profile.nickname());
        assertThat(result.expiresAt()).isEqualTo(now.plusSeconds(30L * 86400));
        verify(db).createQqAccount(any(), eq(profile));
        verify(db).createSession(argThat(s -> s.machineId().equals(MACHINE) && s.tokenHash().equals(Secrets.hash(result.sessionToken()))));
        verify(limiter).check("qq", "12345", "IP");
        verify(db, never()).savePresence(any());
    }

    @Test void retriesConcurrentCreationInNewTransaction() {
        when(db.lockByName("12345")).thenReturn(null, account);
        doThrow(new DuplicateKeyException("duplicate")).when(db).createQqAccount(any(), eq(profile));
        assertThat(service.login(MACHINE, COOKIE, "IP").accountId()).isEqualTo("id");
        verify(verifier, times(1)).verify(COOKIE);
    }

    @Test void conflictNeverBindsPasswordAccountAndDisabledAccountCannotLogin() {
        when(db.lockByName("12345")).thenReturn(new Account("old", "12345", "hash", "ACTIVE", 0, now));
        assertThatThrownBy(() -> service.login(MACHINE, COOKIE, "IP")).isInstanceOfSatisfying(Problem.class,
                e -> assertThat(e.code()).isEqualTo("QQ_ACCOUNT_CONFLICT"));
        when(db.lockByName("12345")).thenReturn(new Account("id", "12345", null, "DISABLED", 0, now, "12345", "nick", "url"));
        assertThatThrownBy(() -> service.login(MACHINE, COOKIE, "IP")).isInstanceOf(Problem.class);
        verify(db, never()).updateQqProfile(any(), any());
        verify(db, never()).createSession(any());
    }

    @Test void revokesOtherMachinesAndTargetsOnlyTheirPresence() {
        var same = session("same", MACHINE);
        var other = session("other", "other-machine");
        when(db.lockSessions("id")).thenReturn(List.of(same, other));
        var owner = new Presence("id", "other", "connection", now.plusSeconds(45));
        when(db.lockPresence("id")).thenReturn(owner);
        service.login(MACHINE, COOKIE, "IP");
        verify(db).revokeSession("other", "MACHINE_CHANGED");
        verify(db, never()).revokeSession(eq("same"), any());
        verify(events).kick(owner, "MACHINE_CHANGED");
        verify(db).clearSessionPresence("id", "other");
        reset(events);
        when(db.lockPresence("id")).thenReturn(new Presence("id", "same", "connection", now.plusSeconds(45)));
        service.login(MACHINE, COOKIE, "IP");
        verifyNoInteractions(events);
    }

    @Test void rejectsInvalidMachineAndFailedTencentValidationBeforeDatabaseWork() {
        for (String machine : new String[] {null, "", "not-a-uuid"})
            assertThatThrownBy(() -> service.login(machine, COOKIE, "IP")).isInstanceOf(Problem.class);
        when(verifier.verify(COOKIE)).thenThrow(Problem.unauthorized());
        assertThatThrownBy(() -> service.login(MACHINE, COOKIE, "IP")).isInstanceOf(Problem.class);
        verifyNoInteractions(db);
    }

    private Session session(String id, String machine) {
        return new Session(id, "id", "hash", 0, now, now.plusSeconds(100), null, null, machine);
    }
}
