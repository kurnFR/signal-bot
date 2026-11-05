-- Index active administrator lookups for concurrency serialization.
-- Optimizes the last-admin protection query:
-- SELECT id, username FROM users WHERE role = 'admin' AND is_active = 1 ORDER BY id FOR UPDATE;
-- Without this index, MariaDB performs a full index scan on PRIMARY key,
-- potentially locking non-administrator rows across the users table.
-- With this index, MariaDB uses a ref lookup (const, const) matching only active admins.
CREATE INDEX IF NOT EXISTS idx_users_role_active_id
    ON users (role, is_active, id);
