-- Durable state for long-running dashboard jobs (backfill, feature build).
-- A job still marked 'running' after a process restart is reported as 'interrupted'.
CREATE TABLE IF NOT EXISTS background_jobs (
    job_id       VARCHAR(64)  NOT NULL PRIMARY KEY,
    job_type     VARCHAR(40)  NOT NULL,
    status       VARCHAR(20)  NOT NULL DEFAULT 'running',
    progress     INT          NOT NULL DEFAULT 0,
    message      VARCHAR(500) NULL,
    payload      JSON         NULL,
    owner_pid    INT          NULL,
    created_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_background_jobs_type_time (job_type, created_at)
) ENGINE=InnoDB;
