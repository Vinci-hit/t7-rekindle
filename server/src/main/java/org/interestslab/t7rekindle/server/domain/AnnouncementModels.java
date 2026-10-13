package org.interestslab.t7rekindle.server.domain;

import java.time.Instant;

public final class AnnouncementModels {
    private AnnouncementModels() { }

    public static final int TITLE_LIMIT = 120;
    public static final int SUMMARY_LIMIT = 500;
    public static final int BODY_LIMIT = 65536;

    public record Draft(String title, String summary, String bodyMarkdown) { }
    public record Entry(String announcementId, String title, String summary, String bodyMarkdown,
                        boolean enabled, Instant createdAt, Instant updatedAt, Instant publishedAt) { }
    public record Summary(String announcementId, String title, boolean enabled, Instant updatedAt, Instant publishedAt) { }
    public record Published(String announcementId, String title, String summary, String bodyMarkdown, Instant publishedAt) { }
}
