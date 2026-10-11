package org.interestslab.t7rekindle.server.service;

import java.time.Duration;
import java.util.HashSet;
import java.util.UUID;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import org.interestslab.t7rekindle.server.domain.Models.*;
import org.interestslab.t7rekindle.server.domain.Problem;
import org.interestslab.t7rekindle.server.persistence.AdminMapper;
import org.interestslab.t7rekindle.server.persistence.AuthMapper;

@Service
public class QqAuthService {
    private final AuthMapper db;
    private final AdminMapper audit;
    private final Secrets secrets;
    private final LoginLimiter limiter;
    private final QqSessionVerifier verifier;
    private final SessionEvents events;
    private final TransactionTemplate tx;
    private final QqGroupService groups;

    public QqAuthService(AuthMapper db, AdminMapper audit, Secrets secrets, LoginLimiter limiter,
                         QqSessionVerifier verifier, SessionEvents events, TransactionTemplate tx, QqGroupService groups) {
        this.db = db;
        this.audit = audit;
        this.secrets = secrets;
        this.limiter = limiter;
        this.verifier = verifier;
        this.events = events;
        this.tx = tx;
        this.groups = groups;
    }

    public QqSessionCredentials login(String machineId, String cookie, String address) {
        String machine = Inputs.machineId(machineId);
        limiter.check("qq", QqSessionVerifier.qqNumber(cookie), address);
        QqProfile profile = verifier.verify(cookie);
        Problem rejection = groupRejection(profile.qqNumber());
        QqSessionCredentials result;
        try {
            result = tx.execute(status -> completeLogin(machine, profile, rejection));
        } catch (DuplicateKeyException error) {
            // A concurrent first login may have created the same QQ account.
            result = tx.execute(status -> completeLogin(machine, profile, rejection));
        }
        // 拒绝结果在事务提交后抛出，保留账户与操作记录。
        if (rejection != null) throw rejection;
        return result;
    }

    private Problem groupRejection(String qqNumber) {
        try {
            groups.require(qqNumber, false);
            return null;
        } catch (Problem error) {
            if (!error.code().equals("QQ_GROUP_NOT_MEMBER")) throw error;
            return error;
        }
    }

    private QqSessionCredentials completeLogin(String machine, QqProfile profile, Problem rejection) {
        Account account = db.lockByName(profile.qqNumber());
        if (account == null) {
            db.createQqAccount(UUID.randomUUID().toString(), profile);
            account = db.lockByName(profile.qqNumber());
        }
        if (!profile.qqNumber().equals(account.qqNumber())) {
            throw new Problem(409, "QQ_ACCOUNT_CONFLICT", "QQ 号与已有密码账号重名，请联系管理员处理");
        }
        if (rejection != null) {
            db.updateQqProfile(account.accountId(), profile);
            audit.audit(account.accountId(), "QQ_GROUP_NOT_MEMBER", rejection.getMessage());
            return null;
        }
        if (!account.status().equals("ACTIVE")) throw Problem.unauthorized();
        var revoked = new HashSet<String>();
        for (Session session : db.lockSessions(account.accountId())) {
            if (!machine.equals(session.machineId())) {
                db.revokeSession(session.sessionId(), "MACHINE_CHANGED");
                revoked.add(session.sessionId());
            }
        }
        Presence owner = db.lockPresence(account.accountId());
        if (owner != null && revoked.contains(owner.sessionId())) {
            db.clearSessionPresence(account.accountId(), owner.sessionId());
            events.kick(owner, "MACHINE_CHANGED");
        }
        db.updateQqProfile(account.accountId(), profile);
        var now = db.now();
        String token = secrets.randomToken();
        var session = new Session(UUID.randomUUID().toString(), account.accountId(), Secrets.hash(token),
                account.authEpoch(), now, now.plus(Duration.ofDays(30)), null, null, machine);
        db.createSession(session);
        audit.audit(account.accountId(), "QQ_LOGIN", session.sessionId());
        return new QqSessionCredentials(session.sessionId(), token, session.expiresAt(), account.accountId(),
                account.loginName(), machine, profile.qqNumber(), profile.nickname(), profile.avatarUrl());
    }
}
