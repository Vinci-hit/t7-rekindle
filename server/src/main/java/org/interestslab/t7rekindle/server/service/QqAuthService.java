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

    public QqAuthService(AuthMapper db, AdminMapper audit, Secrets secrets, LoginLimiter limiter,
                         QqSessionVerifier verifier, SessionEvents events, TransactionTemplate tx) {
        this.db = db;
        this.audit = audit;
        this.secrets = secrets;
        this.limiter = limiter;
        this.verifier = verifier;
        this.events = events;
        this.tx = tx;
    }

    public QqSessionCredentials login(String machineId, String cookie, String address) {
        String machine = Inputs.machineId(machineId);
        limiter.check("qq", QqSessionVerifier.qqNumber(cookie), address);
        QqProfile profile = verifier.verify(cookie);
        try {
            return tx.execute(status -> createSession(machine, profile));
        } catch (DuplicateKeyException error) {
            // A concurrent first login may have created the same QQ account.
            return tx.execute(status -> createSession(machine, profile));
        }
    }

    private QqSessionCredentials createSession(String machine, QqProfile profile) {
        Account account = db.lockByName(profile.qqNumber());
        if (account == null) {
            db.createQqAccount(UUID.randomUUID().toString(), profile);
            account = db.lockByName(profile.qqNumber());
        }
        if (!profile.qqNumber().equals(account.qqNumber())) {
            throw new Problem(409, "QQ_ACCOUNT_CONFLICT", "QQ 号与已有密码账号重名，请联系管理员处理");
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
