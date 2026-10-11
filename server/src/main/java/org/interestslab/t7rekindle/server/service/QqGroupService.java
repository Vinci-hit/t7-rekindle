package org.interestslab.t7rekindle.server.service;

import java.time.Clock;
import org.springframework.dao.DataAccessException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import org.interestslab.t7rekindle.server.domain.Problem;
import org.interestslab.t7rekindle.server.domain.QqGroupModels.*;
import org.interestslab.t7rekindle.server.integration.*;
import org.interestslab.t7rekindle.server.persistence.AuthMapper;
import org.interestslab.t7rekindle.server.persistence.QqGroupMapper;
import static org.interestslab.t7rekindle.server.domain.QqGroupModels.unavailable;

@Service
public class QqGroupService {
    private final QqGroupMapper db;
    private final AuthMapper accounts;
    private final QqGroupClient client;
    private final QqCookieVault vault;
    private final QqGroupCache cache;
    private final SessionEvents events;
    private final TransactionTemplate tx;
    private final Clock clock;
    public QqGroupService(QqGroupMapper db, AuthMapper accounts, QqGroupClient client, QqCookieVault vault,
            QqGroupCache cache, SessionEvents events, TransactionTemplate tx, Clock clock) {
        this.db = db; this.accounts = accounts; this.client = client; this.vault = vault;
        this.cache = cache; this.events = events; this.tx = tx; this.clock = clock;
    }
    public void require(String qq, boolean existingConnection) {
        if (qq == null) return;
        Decision decision;
        try {
            decision = evaluate(qq);
        } catch (DataAccessException error) {
            if (!existingConnection) throw unavailable();
            return;
        } catch (Problem error) {
            if (!existingConnection || error.status() != 503) throw error;
            return;
        }
        if (decision == null) return;
        if (decision.result().equals("NOT_MEMBER")) removeOnline(qq, decision.groupNumber());
        if (existingConnection && decision.result().equals("ERROR")) return;
        decision.requireMember();
    }
    private Decision evaluate(String qq) {
        Policy policy = db.policy();
        if (!policy.enabled()) return null;
        if (!vault.ready()) {
            db.failed(policy.credentialVersion(), "KEY_MISSING");
            throw unavailable();
        }
        if (cache.coolingDown(policy.policyVersion())) throw unavailable();
        boolean healthy = "READY".equals(policy.healthStatus());
        Decision previous = healthy ? cache.get(policy.policyVersion(), qq) : null;
        if (previous != null) return previous;
        String token = cache.lock(policy.policyVersion(), qq);
        if (token == null) {
            if (!healthy) throw unavailable();
            return await(policy.policyVersion(), qq);
        }
        try {
            previous = healthy ? cache.get(policy.policyVersion(), qq) : null;
            if (previous != null) return previous;
            Decision decision = query(policy, qq);
            Policy current = db.policy();
            if (!current.enabled()) return null;
            if (current.policyVersion() != policy.policyVersion()) throw unavailable();
            try { cache.put(qq, decision); }
            catch (DataAccessException error) {
                org.slf4j.LoggerFactory.getLogger(QqGroupService.class).warn("QQ group result cache write failed; using verified result");
            }
            return decision;
        } finally {
            try { cache.unlock(policy.policyVersion(), qq, token); }
            catch (DataAccessException error) {
                org.slf4j.LoggerFactory.getLogger(QqGroupService.class).warn("QQ group query lock release failed; lock will expire");
            }
        }
    }
    private Decision await(long version, String qq) {
        for (int i = 0; i < 24; i++) {
            try { Thread.sleep(250); }
            catch (InterruptedException error) { Thread.currentThread().interrupt(); throw unavailable(); }
            Decision result = cache.get(version, qq);
            if (result != null) return result;
        }
        throw unavailable();
    }
    private Decision query(Policy policy, String qq) {
        QqCookies cookies;
        try { cookies = vault.decrypt(policy.encryptedCookie()); }
        catch (Problem error) {
            db.failed(policy.credentialVersion(), "QQ_GROUP_COOKIE_UNREADABLE");
            throw unavailable();
        }
        Decision decision;
        try {
            var result = client.member(cookies, policy.groupNumber(), qq);
            db.refreshed(policy.credentialVersion(), vault.encrypt(result.cookies()));
            decision = new Decision(policy.groupNumber(), policy.policyVersion(), result.value() ? "MEMBER" : "NOT_MEMBER", clock.instant());
        } catch (Problem error) {
            db.failed(policy.credentialVersion(), error.code());
            decision = new Decision(policy.groupNumber(), policy.policyVersion(), "ERROR", clock.instant());
        }
        db.record(qq, decision);
        return decision;
    }
    private void removeOnline(String qq, String group) {
        tx.executeWithoutResult(status -> {
            var account = accounts.lockByName(qq);
            if (account == null || !qq.equals(account.qqNumber())) return;
            var owner = accounts.lockPresence(account.accountId());
            accounts.clearPresence(account.accountId());
            events.kick(owner, "QQ_GROUP_NOT_MEMBER", group);
        });
    }
}
