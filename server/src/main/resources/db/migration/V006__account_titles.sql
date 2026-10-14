CREATE TABLE player_title (
    title_id INT PRIMARY KEY,
    name VARCHAR(32) NOT NULL,
    description VARCHAR(64) NOT NULL,
    sort_order INT NOT NULL DEFAULT 0,
    enabled BOOLEAN NOT NULL DEFAULT TRUE
) ENGINE=InnoDB;

INSERT INTO player_title(title_id,name,description,sort_order) VALUES
    (1007,'首测元老','感谢最早参与测试的玩家',0),
    (1008,'内测精英','感谢参与本次测试的玩家',1);

CREATE TABLE account_title (
    account_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    title_id INT NOT NULL,
    state VARCHAR(16) NOT NULL,
    source VARCHAR(32) NOT NULL,
    granted_at DATETIME(3) NOT NULL,
    revoked_at DATETIME(3),
    PRIMARY KEY(account_id,title_id),
    CONSTRAINT fk_title_account FOREIGN KEY(account_id) REFERENCES account(account_id),
    CONSTRAINT fk_title_definition FOREIGN KEY(title_id) REFERENCES player_title(title_id),
    CONSTRAINT chk_title_state CHECK(state IN ('GRANTED','REVOKED'))
) ENGINE=InnoDB;

CREATE TABLE account_title_selection (
    account_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    title_id INT,
    CONSTRAINT fk_selection_account FOREIGN KEY(account_id) REFERENCES account(account_id),
    CONSTRAINT fk_selection_title FOREIGN KEY(title_id) REFERENCES player_title(title_id)
) ENGINE=InnoDB;
