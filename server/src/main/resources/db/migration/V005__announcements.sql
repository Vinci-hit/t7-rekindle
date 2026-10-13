CREATE TABLE announcement (
    announcement_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    title VARCHAR(120) NOT NULL,
    summary VARCHAR(500) NOT NULL,
    body_markdown MEDIUMTEXT NOT NULL,
    published_title VARCHAR(120),
    published_summary VARCHAR(500),
    published_body_markdown MEDIUMTEXT,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    created_at DATETIME(3) NOT NULL DEFAULT (UTC_TIMESTAMP(3)),
    updated_at DATETIME(3) NOT NULL DEFAULT (UTC_TIMESTAMP(3)),
    published_at DATETIME(3),
    KEY ix_announcement_latest (enabled, published_at, announcement_id),
    CONSTRAINT chk_announcement_snapshot CHECK (
        (published_at IS NULL AND published_title IS NULL AND published_summary IS NULL AND published_body_markdown IS NULL)
        OR (published_at IS NOT NULL AND published_title IS NOT NULL AND published_summary IS NOT NULL AND published_body_markdown IS NOT NULL)
    ),
    CONSTRAINT chk_announcement_enabled CHECK (NOT enabled OR published_at IS NOT NULL)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
