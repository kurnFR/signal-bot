-- Migration 028: RetailBot2 safe control defaults.
-- Ensure bot_control table defaults to trading disabled and transition legacy
-- rows previously seeded as enabled=TRUE by migration 022 to disabled.

ALTER TABLE bot_control
    MODIFY COLUMN enabled BOOLEAN NOT NULL DEFAULT FALSE;

UPDATE bot_control
   SET enabled = FALSE
 WHERE id = 1;
