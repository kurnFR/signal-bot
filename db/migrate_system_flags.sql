-- Cross-process flags. The emergency stop must be visible to the paper-engine process,
-- not just the web server process that flipped it.
CREATE TABLE IF NOT EXISTS system_flags (
    flag_name  VARCHAR(64)  NOT NULL PRIMARY KEY,
    is_active  TINYINT(1)   NOT NULL DEFAULT 0,
    reason     VARCHAR(500) NULL,
    updated_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;
