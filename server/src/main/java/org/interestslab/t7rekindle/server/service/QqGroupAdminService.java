package org.interestslab.t7rekindle.server.service;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataAccessException;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import org.interestslab.t7rekindle.server.domain.Models.AdminIdentity;
import org.interestslab.t7rekindle.server.domain.Problem;
import org.interestslab.t7rekindle.server.domain.QqGroupModels.*;
import org.interestslab.t7rekindle.server.integration.*;
import org.interestslab.t7rekindle.server.persistence.AdminMapper;
import org.interestslab.t7rekindle.server.persistence.QqGroupMapper;

@Service
public class QqGroupAdminService {
    public record View(boolean enabled, String groupNumber, String groupName, String qqNumber, String nickname,
                       long version, String healthStatus, Instant lastVerifiedAt, boolean keyReady) { }
    public record Scan(String id, String state, Instant expiresAt, String qqNumber, String nickname,
                       List<Group> groups, String message) { }
    private record Pending(String owner, QqQrClient.Ticket ticket, Scan scan, Instant polledAt) {
        @Override public String toString() { return "PendingQqScan"; }
    }
    private record Inspected(Administrator admin, List<Group> groups, QqCookies cookies) { }
    private final Map<String, Pending> pending = new HashMap<>();
    private final QqGroupMapper db;
    private final AdminMapper audit;
    private final AdminService admins;
    private final QqGroupClient client;
    private final QqQrClient qr;
    private final QqCookieVault vault;
    private final TransactionTemplate tx;
    private final Clock clock;

    public QqGroupAdminService(QqGroupMapper db, AdminMapper audit, AdminService admins, QqGroupClient client,
            QqQrClient qr, QqCookieVault vault, TransactionTemplate tx, Clock clock) {
        this.db = db; this.audit = audit; this.admins = admins; this.client = client;
        this.qr = qr; this.vault = vault; this.tx = tx; this.clock = clock;
    }
    public View view() {
        Policy p = db.policy();
        return new View(p.enabled(), p.groupNumber(), p.groupName(), p.qqNumber(), p.nickname(), p.policyVersion(),
                vault.ready() ? p.healthStatus() : "KEY_MISSING", p.lastVerifiedAt(), vault.ready());
    }
    public synchronized Scan start(String owner) {
        requireKey();
        purge();
        pending.entrySet().removeIf(e -> e.getValue().owner().equals(owner));
        if (pending.size() >= 16) throw new Problem(429, "QQ_SCAN_LIMITED", "扫码会话较多，请稍后重试");
        var ticket = qr.start();
        var scan = new Scan(UUID.randomUUID().toString(), "WAITING", clock.instant().plusSeconds(300), null, null, List.of(), "请使用 QQ 扫码并确认");
        pending.put(scan.id(), new Pending(owner, ticket, scan, Instant.EPOCH));
        return scan;
    }
    public synchronized byte[] image(String owner, String id) { return requirePending(owner, id).ticket().image().clone(); }
    public synchronized void cancel(String owner, String id) { requirePending(owner, id); pending.remove(id); }
    public synchronized Scan poll(String owner, String id) {
        Pending current = requirePending(owner, id);
        if (!List.of("WAITING", "SCANNED").contains(current.scan().state())
                || current.polledAt().plusSeconds(3).isAfter(clock.instant())) return current.scan();
        try {
            var response = qr.poll(current.ticket());
            var ticket = new QqQrClient.Ticket(response.cookies(), current.ticket().image(), current.ticket().version());
            Scan scan;
            if (response.state().equals("CONFIRMED")) {
                Inspected result = inspect(response.cookies());
                if (result.groups().isEmpty()) {
                    scan = new Scan(id, "FAILED", current.scan().expiresAt(), null, null, List.of(), "此 QQ 没有可管理的群，请更换账号扫码");
                    ticket = new QqQrClient.Ticket(QqCookies.empty(), new byte[0], "");
                } else {
                    scan = new Scan(id, "READY", clock.instant().plusSeconds(600), result.admin().qqNumber(), result.admin().nickname(),
                            result.groups(), "请选择群组并保存；保存前原配置继续生效");
                    ticket = new QqQrClient.Ticket(result.cookies(), new byte[0], "");
                }
            } else {
                scan = new Scan(id, response.state(), current.scan().expiresAt(), null, null, List.of(), switch (response.state()) {
                    case "WAITING" -> "等待扫码";
                    case "SCANNED" -> "已扫码，请在手机确认";
                    case "EXPIRED" -> "二维码已过期，请刷新";
                    default -> "扫码登录未完成，请刷新二维码重试";
                });
            }
            pending.put(id, new Pending(owner, ticket, scan, clock.instant()));
            return scan;
        } catch (Problem error) {
            var scan = new Scan(id, "FAILED", current.scan().expiresAt(), null, null, List.of(), error.getMessage());
            pending.put(id, new Pending(owner, new QqQrClient.Ticket(QqCookies.empty(), new byte[0], ""), scan, clock.instant()));
            return scan;
        }
    }
    public List<Group> groups() {
        Policy p = db.policy();
        Inspected result = inspect(vault.decrypt(p.encryptedCookie()));
        requireSameAdministrator(p, result.admin());
        updateHealth(p, result);
        return result.groups();
    }
    public synchronized void save(AdminIdentity admin, String owner, String scanId, String groupNumber, boolean enabled, long version) {
        requireKey();
        Policy p = db.policy();
        boolean scanned = scanId != null && !scanId.isBlank();
        QqCookies cookies;
        if (scanned) {
            Pending candidate = requirePending(owner, scanId);
            if (!candidate.scan().state().equals("READY")) throw Problem.invalid();
            cookies = candidate.ticket().cookies();
        } else cookies = vault.decrypt(p.encryptedCookie());
        Inspected result = inspect(cookies);
        if (!scanned) requireSameAdministrator(p, result.admin());
        Group selected = result.groups().stream().filter(g -> g.number().equals(groupNumber)).findFirst()
                .orElseThrow(() -> new Problem(400, "QQ_GROUP_NOT_MANAGED", "请选择当前 QQ 有管理权限的群"));
        String encrypted = vault.encrypt(result.cookies());
        tx.executeWithoutResult(status -> {
            admins.requireManagement(admin);
            if (db.lockPolicy().policyVersion() != version) throw new Problem(409, "QQ_GROUP_CONFIG_CHANGED", "配置已更新，请刷新页面后重试");
            db.configure(enabled, selected, result.admin(), encrypted);
            audit.audit("admin", "QQ_GROUP_CONFIGURED", selected.number());
        });
        if (scanned) pending.remove(scanId);
    }
    public void disable(AdminIdentity admin) {
        tx.executeWithoutResult(status -> {
            admins.requireManagement(admin);
            db.lockPolicy();
            db.disable();
            audit.audit("admin", "QQ_GROUP_DISABLED", "qq-group");
        });
    }
    public void check() {
        Policy p = db.policy();
        try {
            Inspected result = inspect(vault.decrypt(p.encryptedCookie()));
            requireSameAdministrator(p, result.admin());
            if (!updateHealth(p, result)) throw new Problem(503, "QQ_GROUP_PERMISSION_LOST", "当前 QQ 已失去目标群的管理权限，请重新选群");
        } catch (Problem error) {
            db.failed(p.credentialVersion(), vault.ready() ? error.code() : "KEY_MISSING");
            throw error;
        }
    }
    @Scheduled(fixedDelay = 1800000, initialDelay = 1800000)
    public void maintain() {
        try {
            Policy p = db.policy();
            if (p.encryptedCookie() == null || p.lastVerifiedAt() != null
                    && p.lastVerifiedAt().plus(Duration.ofMinutes(30)).isAfter(clock.instant())) return;
            check();
        } catch (Problem | DataAccessException error) {
            LoggerFactory.getLogger(QqGroupAdminService.class).warn("QQ group credential health check failed; administrator action may be required");
        }
    }
    @Scheduled(fixedDelay = 60000, initialDelay = 60000)
    public synchronized void purge() { pending.values().removeIf(p -> !p.scan().expiresAt().isAfter(clock.instant())); }
    private Pending requirePending(String owner, String id) {
        purge();
        Pending value = pending.get(id);
        if (value == null || !value.owner().equals(owner)) throw new Problem(404, "QQ_SCAN_EXPIRED", "扫码会话已结束，请重新扫码");
        return value;
    }
    private Inspected inspect(QqCookies cookies) {
        var identity = client.identity(cookies);
        var groups = client.groups(identity.cookies());
        return new Inspected(identity.value(), groups.value(),
                groups.cookies().scopedTo(java.net.URI.create("https://qun.qq.com/"), clock.instant()));
    }
    private boolean updateHealth(Policy policy, Inspected result) {
        boolean manages = result.groups().stream().anyMatch(g -> g.number().equals(policy.groupNumber()));
        if (manages) db.refreshed(policy.credentialVersion(), vault.encrypt(result.cookies()));
        else db.failed(policy.credentialVersion(), "QQ_GROUP_PERMISSION_LOST");
        return manages;
    }
    private void requireSameAdministrator(Policy policy, Administrator admin) {
        if (!admin.qqNumber().equals(policy.qqNumber())) throw new Problem(503, "QQ_GROUP_IDENTITY_CHANGED", "群站账号发生变化，请重新扫码");
    }
    private void requireKey() {
        if (!vault.ready()) throw new Problem(400, "QQ_GROUP_KEY_REQUIRED", "请先在服务端配置 QQ_GROUP_COOKIE_KEY（4–10 个字符的字符串）");
    }
}
