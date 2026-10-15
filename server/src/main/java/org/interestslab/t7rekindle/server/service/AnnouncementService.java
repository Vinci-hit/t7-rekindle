package org.interestslab.t7rekindle.server.service;

import java.util.List;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import org.interestslab.t7rekindle.server.domain.AnnouncementModels.*;
import org.interestslab.t7rekindle.server.domain.Models.AdminIdentity;
import org.interestslab.t7rekindle.server.domain.Problem;
import org.interestslab.t7rekindle.server.persistence.AdminMapper;
import org.interestslab.t7rekindle.server.persistence.AnnouncementMapper;
import static org.interestslab.t7rekindle.server.domain.AnnouncementModels.*;

@Service
public class AnnouncementService {
    private final AnnouncementMapper db;
    private final AdminMapper audit;
    private final AdminService admins;
    private final TransactionTemplate tx;

    public AnnouncementService(AnnouncementMapper db, AdminMapper audit, AdminService admins, TransactionTemplate tx) {
        this.db = db;
        this.audit = audit;
        this.admins = admins;
        this.tx = tx;
    }

    public List<Summary> list(int page) { return db.list(Inputs.offset(page)); }
    public Published latest() { return db.latest(); }
    public Entry entry(String id) {
        Entry result = db.entry(id);
        if (result == null) throw Problem.missing();
        return result;
    }

    public String create(AdminIdentity admin, Draft input) {
        Draft draft = validate(input);
        return tx.execute(status -> {
            admins.requireManagement(admin);
            String id = UUID.randomUUID().toString();
            db.create(id, draft);
            audit.audit("admin", "ANNOUNCEMENT_CREATED", id);
            return id;
        });
    }

    public void save(AdminIdentity admin, String id, Draft input, boolean publish) {
        Draft draft = validate(input);
        tx.executeWithoutResult(status -> {
            requireEditable(admin, id);
            if (publish) db.publish(id, draft);
            else db.saveDraft(id, draft);
            audit.audit("admin", publish ? "ANNOUNCEMENT_PUBLISHED" : "ANNOUNCEMENT_DRAFT_SAVED", id);
        });
    }

    public void unpublish(AdminIdentity admin, String id) {
        tx.executeWithoutResult(status -> {
            requireEditable(admin, id);
            db.unpublish(id);
            audit.audit("admin", "ANNOUNCEMENT_UNPUBLISHED", id);
        });
    }

    private void requireEditable(AdminIdentity admin, String id) {
        admins.requireManagement(admin);
        if (db.lock(id) == null) throw Problem.missing();
    }

    private static Draft validate(Draft input) {
        requireText(input.title(), TITLE_LIMIT, "标题");
        requireText(input.summary(), SUMMARY_LIMIT, "摘要");
        requireText(input.bodyMarkdown(), BODY_LIMIT, "正文");
        return new Draft(input.title().strip(), input.summary().strip(), input.bodyMarkdown());
    }

    private static void requireText(String text, int limit, String label) {
        if (text == null || text.isBlank() || text.length() > limit) {
            throw new Problem(400, "INVALID_ANNOUNCEMENT", label + "为必填项，长度最多 " + limit + " 个字符");
        }
    }
}
