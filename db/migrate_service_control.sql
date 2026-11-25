-- Runtime pause/resume switches for background workers.
-- Default is "enabled" (no row == enabled), so existing deployments behave as before.
CREATE TABLE IF NOT EXISTS service_control (
    service VARCHAR(40) NOT NULL PRIMARY KEY,
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    updated_by VARCHAR(100) NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
