ALTER TABLE account
    MODIFY password_hash VARCHAR(255) NULL,
    ADD qq_number VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NULL,
    ADD nickname VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL,
    ADD avatar_url VARCHAR(255) NULL,
    ADD UNIQUE KEY uq_account_qq (qq_number);

ALTER TABLE auth_session
    ADD machine_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL;
