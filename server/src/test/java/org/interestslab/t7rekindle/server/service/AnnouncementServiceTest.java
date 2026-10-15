package org.interestslab.t7rekindle.server.service;

import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.interestslab.t7rekindle.server.domain.AnnouncementModels.*;
import org.interestslab.t7rekindle.server.domain.Models.AdminIdentity;
import org.interestslab.t7rekindle.server.domain.Problem;
import org.interestslab.t7rekindle.server.persistence.AdminMapper;
import org.interestslab.t7rekindle.server.persistence.AnnouncementMapper;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class AnnouncementServiceTest {
    private final AnnouncementMapper db = mock(AnnouncementMapper.class);
    private final AdminMapper audit = mock(AdminMapper.class);
    private final AdminService admins = mock(AdminService.class);
    private final AnnouncementService service = new AnnouncementService(db, audit, admins, TestTransactions.immediate());
    private final AdminIdentity admin = new AdminIdentity(0, false);
    private final Draft draft = new Draft("公告标题", "公告摘要", "## 正文\n\n内容");

    @Test void readsUseDedicatedPublishedProjectionAndValidatePages() {
        var entry = new Entry("id", draft.title(), draft.summary(), draft.bodyMarkdown(), false,
                Instant.EPOCH, Instant.EPOCH, null);
        when(db.entry("id")).thenReturn(entry);
        when(db.list(50)).thenReturn(List.of());
        assertThat(service.entry("id")).isSameAs(entry);
        assertThat(service.list(1)).isEmpty();
        assertThat(service.latest()).isNull();
        var published = new Published("id", "线上标题", "线上摘要", "线上正文", Instant.EPOCH);
        when(db.latest()).thenReturn(published);
        assertThat(service.latest()).isSameAs(published);
        assertThatThrownBy(() -> service.entry("missing")).isInstanceOfSatisfying(Problem.class,
                error -> assertThat(error.status()).isEqualTo(404));
        assertThatThrownBy(() -> service.list(-1)).isInstanceOf(Problem.class);
        verifyNoInteractions(admins, audit);
    }

    @Test void creationSavesOnlyDraftAndNormalizesPlainTextNotMarkdown() {
        String id = service.create(admin, new Draft("  公告标题  ", " 公告摘要 ", draft.bodyMarkdown()));
        assertThat(id).matches("[a-f0-9-]{36}");
        verify(db).create(id, draft);
        verify(db, never()).publish(any(), any());
        verify(admins).requireManagement(admin);
        verify(audit).audit("admin", "ANNOUNCEMENT_CREATED", id);
    }

    @Test void draftSavePublishAndUnpublishLockAdminBeforeAnnouncementAndAudit() {
        when(db.lock("id")).thenReturn("id");
        service.save(admin, "id", draft, false);
        service.save(admin, "id", draft, true);
        service.unpublish(admin, "id");
        var order = inOrder(admins, db, audit);
        order.verify(admins).requireManagement(admin);
        order.verify(db).lock("id");
        order.verify(db).saveDraft("id", draft);
        order.verify(audit).audit("admin", "ANNOUNCEMENT_DRAFT_SAVED", "id");
        order.verify(admins).requireManagement(admin);
        order.verify(db).lock("id");
        order.verify(db).publish("id", draft);
        order.verify(audit).audit("admin", "ANNOUNCEMENT_PUBLISHED", "id");
        order.verify(admins).requireManagement(admin);
        order.verify(db).lock("id");
        order.verify(db).unpublish("id");
        order.verify(audit).audit("admin", "ANNOUNCEMENT_UNPUBLISHED", "id");
    }

    @Test void missingAnnouncementAndRevokedAdminNeverWrite() {
        assertThatThrownBy(() -> service.save(admin, "missing", draft, true)).isInstanceOf(Problem.class);
        assertThatThrownBy(() -> service.unpublish(admin, "missing")).isInstanceOf(Problem.class);
        doThrow(Problem.unauthorized()).when(admins).requireManagement(admin);
        assertThatThrownBy(() -> service.create(admin, draft)).isInstanceOf(Problem.class);
        assertThatThrownBy(() -> service.save(admin, "id", draft, false)).isInstanceOf(Problem.class);
        assertThatThrownBy(() -> service.unpublish(admin, "id")).isInstanceOf(Problem.class);
        verify(db, never()).create(any(), any());
        verify(db, never()).saveDraft(any(), any());
        verify(db, never()).publish(any(), any());
        verify(db, never()).unpublish(any());
        verifyNoInteractions(audit);
    }

    @Test void textLimitsRejectEmptyAndOversizedFieldsWithoutWriting() {
        for (String value : new String[] {null, "", " \n\t", "界".repeat(121)}) {
            assertInvalid(new Draft(value, draft.summary(), draft.bodyMarkdown()));
        }
        assertInvalid(new Draft(draft.title(), "界".repeat(501), draft.bodyMarkdown()));
        assertInvalid(new Draft(draft.title(), null, draft.bodyMarkdown()));
        assertInvalid(new Draft(draft.title(), draft.summary(), " "));
        assertInvalid(new Draft(draft.title(), draft.summary(), "界".repeat(65537)));
        verifyNoInteractions(db, audit, admins);
        var boundary = new Draft("界".repeat(120), "界".repeat(500), "界".repeat(65536));
        service.create(admin, boundary);
        verify(db).create(anyString(), eq(boundary));
    }

    private void assertInvalid(Draft value) {
        assertThatThrownBy(() -> service.create(admin, value)).isInstanceOfSatisfying(Problem.class,
                error -> assertThat(error.code()).isEqualTo("INVALID_ANNOUNCEMENT"));
    }
}
