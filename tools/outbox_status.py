"""Read-only outbox and service diagnostics for a guarded rehearsal copy."""

import argparse
import json
import sys

from tools.rehearsal_inventory import add_guard_options, read_only_connection


def collect(connection):
    from notifications.telegram_notifier import _telegram_delivery_ceiling_open
    from paper.outbox_store import OUTBOX_LOCK_NAME
    from paper.outbox_worker import ALLOWED_PROD_DATABASES, ALLOWED_TEST_DATABASES

    with connection.cursor() as cursor:
        cursor.execute("SELECT DATABASE(), IS_USED_LOCK(%s) IS NOT NULL", (OUTBOX_LOCK_NAME,))
        database, lock_held = cursor.fetchone()
        cursor.execute("SELECT delivery_authorized FROM app_telegram_settings ORDER BY id DESC LIMIT 1")
        setting = cursor.fetchone()
        authorized = None if setting is None or setting[0] is None else bool(setting[0])
        ceiling_open = _telegram_delivery_ceiling_open()
        if authorized is None:
            reason = "explicit_authorization_missing"
        elif not ceiling_open:
            reason = "environment_delivery_disabled"
        elif not authorized:
            reason = "delivery_disabled_in_settings"
        else:
            reason = None
        cursor.execute("SELECT status, COUNT(*) FROM rdt_telegram_outbox GROUP BY status ORDER BY status")
        counts = dict(cursor.fetchall())
        cursor.execute("""SELECT COUNT(*) FROM rdt_telegram_outbox WHERE attempts < 8 AND (
            (status='pending' AND next_attempt_at <= UTC_TIMESTAMP()) OR
            (status='processing' AND locked_until <= UTC_TIMESTAMP()))""")
        due, = cursor.fetchone()
        cursor.execute("SELECT service, enabled, updated_at FROM service_control ORDER BY service")
        services = [{"service": service, "enabled": bool(enabled), "updated_at": updated_at}
                    for service, enabled, updated_at in cursor.fetchall()]
        cursor.execute("""SELECT collector_name, last_beat_time, status,
            TIMESTAMPDIFF(SECOND, last_beat_time, NOW())
            FROM collector_heartbeat ORDER BY collector_name""")
        heartbeats = [{"service": name, "last_beat": time, "status": status, "age_seconds": age}
                      for name, time, status, age in cursor.fetchall()]

    return {
        "database": database,
        "delivery_authorized": authorized,
        "local_environment_delivery_enabled": ceiling_open,
        "production_authorization_preview_reason": reason,
        "worker_cli_accepts_database": database in ALLOWED_TEST_DATABASES + ALLOWED_PROD_DATABASES,
        "advisory_lock_held": bool(lock_held),
        "queue_counts": counts,
        "due": due,
        "services": services,
        "heartbeats": heartbeats,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_guard_options(parser)
    args = parser.parse_args(argv)
    try:
        from config import MYSQL_CONFIG

        with read_only_connection(MYSQL_CONFIG, args.allow_host, args.rehearsal_db_name_prefix) as connection:
            print(json.dumps(collect(connection), default=str, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"Outbox status refused or unavailable ({type(exc).__name__}).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
