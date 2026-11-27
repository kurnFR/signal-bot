"""MariaDB storage for saved rule definitions."""

import json

from db.db import _owned_cursor, get_pool


def _serialize(definition):
    return json.dumps(definition, allow_nan=False)


def import_legacy_definitions(cursor, definitions):
    """Import each saved ID once while preserving a previously imported row."""
    for definition in definitions:
        name = definition.get("name")
        if not isinstance(name, str) or not name or len(name) > 80:
            raise ValueError("Invalid saved custom strategy identifier")
        cursor.execute(
            """INSERT INTO custom_strategies (name, definition, enabled)
               VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE name = name""",
            (name, _serialize(definition), definition.get("enabled", True)),
        )


def list_definitions(*, include_deployments=False):
    with _owned_cursor(get_pool().get_connection(), dictionary=True) as cur:
        cur.execute("SELECT name, definition, enabled, updated_at FROM custom_strategies ORDER BY name")
        definitions = []
        for row in cur.fetchall():
            definition = json.loads(row["definition"])
            definition.update(name=row["name"], enabled=bool(row["enabled"]), updated_at=row["updated_at"].isoformat())
            definitions.append(definition)
        if include_deployments and definitions:
            cur.execute(
                """SELECT strategy_name, COUNT(*) AS total
                   FROM paper_configs WHERE is_active = 1 GROUP BY strategy_name"""
            )
            counts = {row["strategy_name"]: row["total"] for row in cur.fetchall()}
            for definition in definitions:
                name = definition["name"]
                definition["active_deployments"] = counts.get(name, 0) + counts.get(f"inverse_{name}", 0)
        return definitions


class StrategyInUseError(ValueError):
    def __init__(self, message, deployments):
        super().__init__(message)
        self.deployments = deployments


def mutate_definition(name, action, *, definition=None, enabled=None, force=False):
    # Disable/delete and the entry evaluation loop use the same advisory lock.
    from paper.engine import PaperEngineAdvisoryLock

    lock = PaperEngineAdvisoryLock(timeout=2)
    if not lock.acquire():
        raise StrategyInUseError("The paper engine is processing deployments. Try again.", [])
    try:
        conn = get_pool().get_connection()
        with _owned_cursor(conn, dictionary=True) as cur:
            cur.execute("SELECT name FROM custom_strategies WHERE name=%s FOR UPDATE", (name,))
            exists = cur.fetchone() is not None
            if action == "create":
                cur.execute(
                    """INSERT INTO custom_strategies (name, definition) VALUES (%s, %s)
                       ON DUPLICATE KEY UPDATE definition = VALUES(definition)""",
                    (name, _serialize(definition)),
                )
            elif not exists:
                return False
            elif action == "update":
                cur.execute("UPDATE custom_strategies SET definition=%s WHERE name=%s", (_serialize(definition), name))
            else:
                names = (name, f"inverse_{name}")
                if action == "delete" or (enabled is False and not force):
                    active_only = " AND is_active = 1" if action == "toggle" else ""
                    cur.execute(
                        f"""SELECT id, symbol, timeframe FROM paper_configs
                            WHERE strategy_name IN (%s,%s){active_only} ORDER BY id FOR UPDATE""",
                        names,
                    )
                    deployments = cur.fetchall()
                    if deployments:
                        message = (
                            "Remove the referenced deployments before deleting this strategy."
                            if action == "delete" else "Pause the active deployments before disabling this strategy."
                        )
                        raise StrategyInUseError(message, deployments)
                if action == "delete":
                    cur.execute(
                        """SELECT id FROM paper_positions
                           WHERE strategy_name IN (%s,%s) AND status='OPEN' LIMIT 1 FOR UPDATE""",
                        names,
                    )
                    if cur.fetchone():
                        raise StrategyInUseError("Close the open positions before deleting this strategy.", [])
                    cur.execute("DELETE FROM custom_strategies WHERE name=%s", (name,))
                else:
                    cur.execute("UPDATE custom_strategies SET enabled=%s WHERE name=%s", (enabled, name))
                    if not enabled:
                        cur.execute(
                            """UPDATE paper_orders AS o JOIN paper_configs AS c ON c.id = o.config_id
                               SET o.status = 'CANCELLED', o.state_reason = 'Custom strategy disabled',
                                   o.terminal_at = CURRENT_TIMESTAMP, o.updated_at = CURRENT_TIMESTAMP
                               WHERE c.strategy_name IN (%s,%s) AND o.status = 'PENDING'""",
                            names,
                        )
            conn.commit()
            return True
    finally:
        lock.release()
