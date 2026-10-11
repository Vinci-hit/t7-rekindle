ALTER TABLE account ADD last_heartbeat_at DATETIME(3) NULL;

CREATE TABLE qq_group_config (
    config_id TINYINT PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    group_number VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NULL,
    group_name VARCHAR(255) NULL,
    qq_number VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NULL,
    nickname VARCHAR(255) NULL,
    encrypted_cookie MEDIUMTEXT NULL,
    policy_version BIGINT NOT NULL DEFAULT 0,
    credential_version BIGINT NOT NULL DEFAULT 0,
    health_status VARCHAR(32) NOT NULL DEFAULT 'MISSING',
    last_verified_at DATETIME(3) NULL,
    CONSTRAINT chk_group_singleton CHECK (config_id = 1)
) ENGINE=InnoDB;
INSERT INTO qq_group_config(config_id) VALUES(1);

CREATE TABLE qq_group_check (
    qq_number VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    group_number VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    policy_version BIGINT NOT NULL,
    checked_at DATETIME(3) NOT NULL,
    result VARCHAR(16) NOT NULL,
    CONSTRAINT chk_group_result CHECK (result IN ('MEMBER', 'NOT_MEMBER', 'ERROR'))
) ENGINE=InnoDB;
