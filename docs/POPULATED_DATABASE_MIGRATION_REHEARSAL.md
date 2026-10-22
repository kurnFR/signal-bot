# Populated Database Migration Rehearsal

## Purpose and safety boundary

Use this runbook to validate migrations against a representative, populated **copy** of a deployed schema before any production upgrade. Automated tests and green CI do not replace this rehearsal.

**Never point rehearsal commands at production.** Do not run the migration apply command against a live database as part of this procedure. Use an isolated MySQL/MariaDB instance, a uniquely named rehearsal database, credentials limited to that instance, and a verified backup. Do not include database dumps, credentials, personal data, wallet addresses, or tokens in GitHub issues or pull requests.

This runbook describes the process; it does not claim that a populated-schema rehearsal has already been run.

## Entry criteria

Do not start until all are true:

- [ ] The database owner approved the source snapshot and rehearsal.
- [ ] A fresh backup/snapshot exists, its checksum is recorded, and a restore test succeeded.
- [ ] The source schema/version and database engine/version are recorded.
- [ ] The rehearsal database is on an isolated instance, with network access restricted and no application workers pointed at it.
- [ ] The operator can prove which host and database each command will access.
- [ ] There is enough disk space for the restored copy and logs.
- [ ] A recovery owner and stop/abort decision-maker are identified.

If the source contains sensitive or regulated data, use an approved sanitized snapshot when it preserves the schema and edge cases needed for validation.

## 1. Prepare a source snapshot safely

Follow the database owner's approved backup process. For an InnoDB-heavy database, a logical backup may use `mysqldump --single-transaction`; confirm whether non-transactional tables, routines, triggers, events, grants, or external dependencies require additional steps.

Example only, to be adapted to the approved environment:

```sh
# Run with source-only credentials in a protected shell/session.
# Do not put passwords directly in command arguments or commit the dump.
mysqldump --single-transaction --routines --triggers --events \
  --host="$SOURCE_HOST" --user="$SOURCE_USER" -p \
  "$SOURCE_DATABASE" > "$BACKUP_FILE"

sha256sum "$BACKUP_FILE"
```

Record the dump checksum, timestamp, engine/version, source schema identifier, and approved redaction method in the private change record. Store the dump only in approved restricted storage. Validate that the dump is readable and that the approved restore procedure succeeds before using it.

## 2. Restore to an isolated rehearsal database

Provision a disposable MySQL/MariaDB instance or isolated server. Create a unique target database, for example `signal_bot_rehearsal_YYYYMMDD`, using an operator account on that isolated instance. Do not use the production hostname or production credentials.

Restore the dump into the target database without restoring it under the source database name. Verify the connected host and selected database independently before running any application command.

The migration CLI reads connection configuration from `config.py`: `MYSQL_HOST` or `DB_HOST`, `MYSQL_PORT`, `MYSQL_USER` or `DB_USER`, `MYSQL_PASSWORD` or `DB_PASS`, and `MYSQL_DATABASE`. Set the variables explicitly for the isolated instance; ensure environment variables override any local `.env` values. Do not print secrets while checking configuration.

## 3. Capture the pre-migration baseline

Before applying migrations, record privately:

- Database engine/version, table definitions, indexes, constraints, and generated columns.
- Table names and row counts, especially OHLCV/market data, paper orders, fills/trades, positions, accounting/ledger rows, users, strategy records, and migration metadata when present.
- Representative aggregate checks (for example, min/max timestamps and sums/counts over selected stable numeric fields) appropriate to the schema.
- Existing warnings, inconsistent legacy structures, and known partial migrations.

For large tables, use a repeatable scripted inventory rather than manually copying results. Use stable deterministic checks for key business tables; row counts alone cannot prove that values were preserved. Keep output in the restricted rehearsal record, not in the repository.

## 4. Inspect the plan before changing the copy

From the repository revision proposed for release, with configuration explicitly targeting the isolated rehearsal database:

```sh
python -m db.migrate --status
python -m db.migrate --dry-run
```

Confirm the host/database are the rehearsal target and review every reported pending, changed, or baseline-covered migration. Stop if an unexpected checksum change, unexplained state, connection ambiguity, or production endpoint appears. These inspection modes are intended to be read-only, but they do not replace an isolated environment.

## 5. Apply migrations to the isolated copy

Only after the plan is reviewed and entry criteria are satisfied, apply migrations to the disposable rehearsal database:

```sh
python -m db.migrate
python -m db.migrate --status
```

Save complete output and exit codes. If a migration fails, stop and preserve logs and the failed rehearsal copy for diagnosis. Do not repeatedly retry unexplained failures, manually edit migration metadata, or continue to the next release step.

## 6. Verify data and schema preservation

Compare post-migration state against the recorded baseline:

- [ ] Expected tables exist and have expected columns, types, nullability, indexes, foreign keys, and generated-column definitions.
- [ ] Core market-data tables retain expected row counts and timestamp ranges.
- [ ] Paper-order, fill/trade, position, and accounting/ledger records retain key counts and stable aggregates.
- [ ] Primary/unique keys and referential-integrity checks pass.
- [ ] Known legacy partial-migration cases are resolved without duplicate or missing columns.
- [ ] No unexpected row deletion, rewrite, or truncation occurred.
- [ ] `python -m db.migrate --status` reports the expected final state and no unexplained checksum drift.
- [ ] Relevant migration, accounting, and MariaDB integration tests pass against the release revision.

Investigate every difference. A changed row count is not automatically a failure if a documented migration intentionally transforms data, but every expected transformation must be described and independently verified. Successful command exit alone is not proof of data preservation.

## 7. Recovery and abort procedure

For rehearsal recovery, discard and recreate the **isolated target** from the verified source snapshot, then repeat only after the cause is understood. Keep the original snapshot immutable.

For any later production change, prepare a separate reviewed release plan that names the exact backup, recovery method, maintenance window, owners, monitoring, and stop conditions. MySQL/MariaDB DDL is not generally transactional, so do not assume reverting application code reverses schema changes. Production migration is a separate authorized operation and is not approved by completing this rehearsal.

## 8. Evidence and sign-off

Record in a restricted change ticket:

- Source schema identity and engine version (no secrets).
- Snapshot checksum and successful restore evidence.
- Release commit SHA and migration status/dry-run output.
- Pre/post table inventories and data-integrity comparison results.
- Test results, deviations, issue links, and remediation commits.
- Operator, reviewer, date, and explicit pass/fail decision.

**Pass** only when expected schema and data invariants hold, deviations are resolved or formally accepted, and the database owner/reviewer signs off. Otherwise mark **fail / blocked**, preserve evidence, and do not proceed to production.
