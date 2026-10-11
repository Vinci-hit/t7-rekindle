package org.interestslab.t7rekindle.server.service;

import java.time.Clock;
import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.dao.DataAccessResourceFailureException;
import org.interestslab.t7rekindle.server.domain.Models.AdminIdentity;
import org.interestslab.t7rekindle.server.domain.Problem;
import org.interestslab.t7rekindle.server.domain.QqGroupModels.*;
import org.interestslab.t7rekindle.server.integration.*;
import org.interestslab.t7rekindle.server.persistence.*;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class QqGroupAdminServiceTest {
    private final QqGroupMapper db = mock(QqGroupMapper.class);
    private final AdminMapper audit = mock(AdminMapper.class);
    private final AdminService admins = mock(AdminService.class);
    private final QqGroupClient client = mock(QqGroupClient.class);
    private final QqQrClient qr = mock(QqQrClient.class);
    private final QqCookieVault vault = mock(QqCookieVault.class);
    private final Clock clock = mock(Clock.class);
    private final QqCookies cookies = QqCookies.empty();
    private final Group group = new Group("123456", "测试群");
    private final Administrator administrator = new Administrator("54321", "管理员");
    private final AdminIdentity admin = new AdminIdentity(0, false);
    private final Instant now = QqGroupServiceTest.NOW;
    private final QqGroupAdminService service = new QqGroupAdminService(db, audit, admins, client, qr, vault,
            TestTransactions.immediate(), clock);
    @BeforeEach void setup() {
        when(clock.instant()).thenReturn(now);
        when(db.policy()).thenReturn(QqGroupServiceTest.POLICY);
        when(db.lockPolicy()).thenReturn(QqGroupServiceTest.POLICY);
        when(vault.ready()).thenReturn(true);
        when(vault.decrypt("encrypted")).thenReturn(cookies);
        when(vault.encrypt(cookies)).thenReturn("updated");
        when(qr.start()).thenReturn(new QqQrClient.Ticket(cookies, new byte[] {1, 2}, "123"));
        when(qr.poll(any())).thenReturn(new QqQrClient.Poll("CONFIRMED", cookies));
        when(client.identity(cookies)).thenReturn(new QqGroupClient.Result<>(administrator, cookies));
        when(client.groups(cookies)).thenReturn(new QqGroupClient.Result<>(List.of(group), cookies));
    }
    @Test void scansBeforeSelectingGroupAndPersistsOnlyAfterSave() {
        var start = service.start("owner");
        assertThat(start.state()).isEqualTo("WAITING");
        assertThat(service.image("owner", start.id())).containsExactly(1, 2);
        assertThatThrownBy(() -> service.image("other", start.id())).isInstanceOf(Problem.class);
        var result = service.poll("owner", start.id());
        assertThat(result.state()).isEqualTo("READY");
        assertThat(result.groups()).containsExactly(group);
        assertThat(result.expiresAt()).isEqualTo(now.plusSeconds(600));
        assertThat(service.poll("owner", start.id())).isSameAs(result);
        verify(db, never()).configure(anyBoolean(), any(), any(), any());
        service.save(admin, "owner", start.id(), group.number(), true, 1);
        verify(db).configure(true, group, administrator, "updated");
        verify(admins).requireManagement(admin);
        assertThatThrownBy(() -> service.poll("owner", start.id())).isInstanceOf(Problem.class);
    }
    @Test void refreshAndCancelAreIsolatedAndExpiryPurgesCredentials() {
        var first = service.start("owner");
        var second = service.start("owner");
        assertThatThrownBy(() -> service.poll("owner", first.id())).isInstanceOf(Problem.class);
        service.cancel("owner", second.id());
        assertThatThrownBy(() -> service.cancel("owner", second.id())).isInstanceOf(Problem.class);
        var third = service.start("owner");
        when(clock.instant()).thenReturn(now.plusSeconds(300));
        assertThatThrownBy(() -> service.poll("owner", third.id())).isInstanceOf(Problem.class);
        verify(db, never()).configure(anyBoolean(), any(), any(), any());
    }
    @Test void respectsPollingIntervalAndAllTerminalStates() {
        for (String state : List.of("WAITING", "SCANNED", "EXPIRED", "FAILED")) {
            var scan = service.start("owner");
            when(qr.poll(any())).thenReturn(new QqQrClient.Poll(state, cookies));
            assertThat(service.poll("owner", scan.id()).state()).isEqualTo(state);
            assertThat(service.poll("owner", scan.id()).state()).isEqualTo(state);
            service.cancel("owner", scan.id());
        }
        verify(qr, times(4)).poll(any());
    }
    @Test void wrongAccountAndNetworkFailureDoNotReplaceWorkingCredential() {
        var scan = service.start("owner");
        when(client.groups(cookies)).thenReturn(new QqGroupClient.Result<>(List.of(), cookies));
        assertThat(service.poll("owner", scan.id()).message()).contains("没有可管理");
        scan = service.start("owner");
        when(qr.poll(any())).thenThrow(Problem.unavailable());
        assertThat(service.poll("owner", scan.id()).state()).isEqualTo("FAILED");
        verifyNoInteractions(audit);
        verify(db, never()).configure(anyBoolean(), any(), any(), any());
    }
    @Test void missingKeyAndSessionCapacityHaveExplicitErrors() {
        when(vault.ready()).thenReturn(false);
        assertThat(service.view().healthStatus()).isEqualTo("KEY_MISSING");
        assertThatThrownBy(() -> service.start("owner")).isInstanceOfSatisfying(Problem.class, error -> {
            assertThat(error.code()).isEqualTo("QQ_GROUP_KEY_REQUIRED");
            assertThat(error.getMessage()).contains("4–10 个字符").doesNotContain("Base64");
        });
        when(vault.ready()).thenReturn(true);
        assertThat(service.view().healthStatus()).isEqualTo("READY");
        for (int i = 0; i < 16; i++) service.start("owner" + i);
        assertThatThrownBy(() -> service.start("extra")).isInstanceOfSatisfying(Problem.class, error -> assertThat(error.status()).isEqualTo(429));
    }
    @Test void saveRevalidatesManagementAndConfigurationVersion() {
        var scan = service.start("owner");
        assertThatThrownBy(() -> service.save(admin, "owner", scan.id(), "123456", true, 1)).isInstanceOf(Problem.class);
        service.poll("owner", scan.id());
        assertThatThrownBy(() -> service.save(admin, "owner", scan.id(), "999999", true, 1)).isInstanceOf(Problem.class);
        assertThatThrownBy(() -> service.save(admin, "owner", scan.id(), "123456", true, 9)).isInstanceOf(Problem.class);
        verify(db, never()).configure(anyBoolean(), any(), any(), any());
        service.save(admin, "owner", "", "123456", false, 1);
        service.save(admin, "owner", null, "123456", true, 1);
        verify(db).configure(false, group, administrator, "updated");
        service.disable(admin);
        verify(db).disable();
        verify(audit).audit("admin", "QQ_GROUP_DISABLED", "qq-group");
    }
    @Test void currentGroupsAndHealthDetectPermissionAndIdentityChanges() {
        assertThat(service.groups()).containsExactly(group);
        service.check();
        verify(db, times(2)).refreshed(2, "updated");
        when(client.groups(cookies)).thenReturn(new QqGroupClient.Result<>(List.of(), cookies));
        assertThat(service.groups()).isEmpty();
        assertThatThrownBy(service::check).isInstanceOfSatisfying(Problem.class,
                error -> assertThat(error.code()).isEqualTo("QQ_GROUP_PERMISSION_LOST"));
        when(client.identity(cookies)).thenReturn(new QqGroupClient.Result<>(new Administrator("99999", "other"), cookies));
        assertThatThrownBy(service::groups).isInstanceOf(Problem.class);
        when(vault.decrypt("encrypted")).thenThrow(Problem.unavailable());
        when(vault.ready()).thenReturn(false);
        assertThatThrownBy(service::check).isInstanceOf(Problem.class);
        verify(db).failed(2, "KEY_MISSING");
    }
    @Test void healthScheduleSkipsFreshOrMissingCredentialsAndReportsFailures() {
        service.maintain();
        verifyNoInteractions(client);
        when(db.policy()).thenReturn(new Policy(false, null, null, null, null, null, 0, 0, "MISSING", null));
        service.maintain();
        when(db.policy()).thenReturn(new Policy(true, "123456", "group", "54321", "nick", "encrypted", 1, 2, "READY", null));
        service.maintain();
        verify(client).groups(cookies);
        when(db.policy()).thenReturn(QqGroupServiceTest.POLICY);
        when(clock.instant()).thenReturn(now.plusSeconds(1801));
        service.maintain();
        when(client.identity(cookies)).thenThrow(Problem.unavailable());
        service.maintain();
        when(db.policy()).thenThrow(new DataAccessResourceFailureException("TEST"));
        service.maintain();
    }
}
