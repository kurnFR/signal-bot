"""Keep operator guidance aligned with migration-managed service schemas."""
from pathlib import Path
import unittest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _doc(rel_path: str) -> Path:
    return REPO_ROOT / rel_path


class TestMigrationManagedStartupDocs(unittest.TestCase):
    def test_security_guide_separates_migration_privileges_from_runtime(self):
        text = _doc("docs/SECURITY.md").read_text(encoding="utf-8")
        self.assertIn("run_all.py", text)
        self.assertIn("read-only", text)
        self.assertNotIn("wallet tracker startup runs an ALTER TABLE", text)

    def test_wallet_tracker_guide_requires_migrations_before_standalone_worker(self):
        text = _doc("docs/WALLET_TRACKER.md").read_text(encoding="utf-8")
        self.assertIn("versioned migrations", text)
        self.assertIn("standalone worker requires migrations", text)
        self.assertNotIn("Tables are created automatically at startup.", text)

    def test_wallet_runtime_schema_migration_documents_read_only_verification(self):
        text = _doc("db/migrate_wallet_tracker_runtime_schema.sql").read_text(encoding="utf-8")
        self.assertIn("read-only", text)

    def test_retailbot2_outbox_runbook_documents_authoritative_advisory_lock(self):
        from paper.outbox_store import OUTBOX_LOCK_NAME
        self.assertEqual(OUTBOX_LOCK_NAME, "telegram_outbox_worker_lock")

        release_gates = _doc("docs/RETAILBOT2_AND_MODERN_UI_RELEASE_GATES.md").read_text(encoding="utf-8")
        self.assertIn("OUTBOX_LOCK_NAME = \"telegram_outbox_worker_lock\"", release_gates)
        self.assertIn("SELECT IS_USED_LOCK('telegram_outbox_worker_lock');", release_gates)
        self.assertIn("WHERE ID = IS_USED_LOCK('telegram_outbox_worker_lock');", release_gates)

        arch_doc = _doc("docs/ARCHITECTURE.md").read_text(encoding="utf-8")
        self.assertIn("`telegram_outbox_worker_lock`", arch_doc)

        # Confirm no lock query in docs mistakenly refers to retailbot2_telegram_outbox
        for doc_path in (REPO_ROOT / "docs").glob("*.md"):
            doc_text = doc_path.read_text(encoding="utf-8")
            self.assertNotIn("IS_USED_LOCK('retailbot2_telegram_outbox')", doc_text)
            self.assertNotIn("GET_LOCK('retailbot2_telegram_outbox'", doc_text)

    def test_retailbot2_release_gates_reconciles_production_schema_evidence(self):
        release_gates = _doc("docs/RETAILBOT2_AND_MODERN_UI_RELEASE_GATES.md").read_text(encoding="utf-8")

        # 1. Records the WO71688 observation and its date
        self.assertIn("WO71688", release_gates)
        self.assertIn("2026-11-10", release_gates)

        # 2. Distinguishes schema presence from worker activation
        self.assertIn("Schema observation does not authorize worker startup", release_gates)
        self.assertIn("Schema presence is not activation authorization", release_gates)
        self.assertIn("RETAILBOT2_OUTBOX_WORKER_ENABLED", release_gates)
        self.assertIn("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", release_gates)

        # 3. Does not state that production currently lacks the outbox table
        self.assertNotIn("Production currently lacks `telegram_outbox`", release_gates)
        self.assertNotIn("Production table `telegram_outbox` is currently reported missing", release_gates)
        self.assertNotIn("migration 026 has only been applied to `signal_bot_test`", release_gates)

        # 4. Production access boundary: requires separate authorization for all access, including read-only
        self.assertIn(
            "Any access to or inspection of the production database `crypto_signals` requires separate, explicit authorization",
            release_gates,
        )
        self.assertIn("read-only SQL queries", release_gates)
        self.assertIn("schema inspection", release_gates)
        self.assertIn("migration-status checks", release_gates)
        self.assertIn("dry-run commands", release_gates)
        self.assertIn("process or session inspection through database connections", release_gates)
        self.assertIn("standing authorization", release_gates)
        # Boundary must not be weakened to only unreviewed migrations or ad-hoc SQL mutations
        self.assertNotIn("Do not execute unreviewed migrations or ad-hoc SQL mutations against `crypto_signals`.", release_gates)

        # 5. Delivery evidence boundary: evidence-bounded wording and absence of unsupported absolute claim
        self.assertNotIn("No real Telegram delivery has occurred", release_gates)
        self.assertIn("WO71688 provides no evidence of production Telegram delivery", release_gates)
        self.assertIn("External Telegram delivery remains unauthorized and unvalidated", release_gates)
        self.assertIn("Telegram external delivery | **Not authorized / not tested**", release_gates)
        self.assertIn("not a comprehensive historical audit", release_gates)

    def test_retailbot2_outbox_recovery_policy_unknown_delivery_and_preconditions(self):
        """Verify Section 4.5 recovery policy distinguishes 3 delivery outcomes and guards DELIVERY_UNKNOWN."""
        release_gates = _doc("docs/RETAILBOT2_AND_MODERN_UI_RELEASE_GATES.md").read_text(encoding="utf-8")

        # 1. Three distinct delivery outcomes documented
        self.assertIn("`CONFIRMED_NOT_DELIVERED`", release_gates)
        self.assertIn("`CONFIRMED_DELIVERED`", release_gates)
        self.assertIn("`DELIVERY_UNKNOWN`", release_gates)

        # 2. DELIVERY_UNKNOWN explicitly carries duplicate-delivery risk
        self.assertIn(
            "Requeueing an event with `DELIVERY_UNKNOWN` can produce duplicate Telegram messages",
            release_gates,
        )
        self.assertIn("**Do NOT describe the event as safe to requeue.**", release_gates)
        self.assertIn("**Do NOT claim that duplicate delivery can be prevented or ruled out.**", release_gates)

        # 3. Process termination, lock release, and lease expiry do not prove non-delivery
        self.assertIn(
            "**Process termination** does NOT prove that a previous HTTP request was unsuccessful",
            release_gates,
        )
        self.assertIn(
            "**Advisory-lock release** does NOT prove that a previous HTTP request was unsuccessful",
            release_gates,
        )
        self.assertIn(
            "**Lease expiry** does NOT prove that a previous HTTP request was unsuccessful",
            release_gates,
        )
        self.assertIn(
            "**Database acknowledgement failure** after successful Telegram delivery",
            release_gates,
        )
        self.assertIn(
            "Satisfying process and advisory-lock checks does NOT resolve external delivery status",
            release_gates,
        )

        # 4. Unknown events are not automatically requeued
        self.assertIn(
            "**Leave the event untouched** unless an authorized operator and database owner explicitly accept the duplicate-delivery risk",
            release_gates,
        )
        self.assertIn("Automated recovery jobs and ad-hoc UPDATE queries are strictly prohibited", release_gates)

        # 5. Manual recovery requires 6 strict preconditions, explicit authorization, and audit record
        self.assertIn("Obtain explicit operator and database owner sign-off", release_gates)
        self.assertIn("Preserve audit record", release_gates)
        self.assertIn("Mandatory Audit Logging", release_gates)
        self.assertIn("Stop the worker gracefully", release_gates)
        self.assertIn("Verify process elimination", release_gates)
        self.assertIn("Verify advisory lock release", release_gates)
        self.assertIn("Read-only event inspection", release_gates)

        # 6. No unconditional recovery UPDATE query is present in documentation
        self.assertNotIn("UPDATE telegram_outbox", release_gates)
        self.assertNotIn("SET status = 'pending'", release_gates)

    def test_retailbot2_outbox_provisioning_runbook_and_failure_states(self):
        """Verify Stage B provisioning runbook, four failure states, audit, and rollback provenance."""
        release_gates = _doc("docs/RETAILBOT2_AND_MODERN_UI_RELEASE_GATES.md").read_text(encoding="utf-8")

        # 1. Four explicit provisioning states
        self.assertIn("`ACCOUNT_NOT_CREATED`", release_gates)
        self.assertIn("`ACCOUNT_CREATED_GRANT_PENDING`", release_gates)
        self.assertIn("`ACCOUNT_CREATED_GRANT_VERIFIED`", release_gates)
        self.assertIn("`PROVISIONING_STATE_UNKNOWN`", release_gates)

        # 2. Invariant: only ACCOUNT_CREATED_GRANT_VERIFIED may proceed
        self.assertIn(
            "No state other than `ACCOUNT_CREATED_GRANT_VERIFIED` may proceed to worker credential configuration or observation worker startup",
            release_gates,
        )

        # 3. Ten-step sequence elements
        self.assertIn("DBA performs account-existence preflight", release_gates)
        self.assertIn("Preflight Stop Condition", release_gates)
        self.assertIn("DBA executes `CREATE USER` without `IF NOT EXISTS`", release_gates)
        self.assertIn("Record creation result", release_gates)
        self.assertIn("Handle `CREATE USER` failure", release_gates)
        self.assertIn("Execute exact table-level grant", release_gates)
        self.assertIn("Handle `GRANT` failure", release_gates)
        self.assertIn("Prohibit worker startup", release_gates)
        self.assertIn("No automatic recovery", release_gates)
        self.assertIn("Record partial state & escalate", release_gates)

        # 4. Audit record requirements (ticket ID, DBA identity, timestamps, status, password secrecy)
        self.assertIn("Change / Incident Ticket ID", release_gates)
        self.assertIn("DBA Identity", release_gates)
        self.assertIn("Owner Authorization Reference", release_gates)
        self.assertIn("Preflight Timestamp & Result", release_gates)
        self.assertIn("Account Creation Timestamp & Result", release_gates)
        self.assertIn("Grant Timestamp & Result", release_gates)
        self.assertIn("Effective Grants Verification Timestamp & Result", release_gates)
        self.assertIn("Exact Account Identity", release_gates)
        self.assertIn("Provisioning Completion Status", release_gates)
        self.assertIn("**Never** record the plaintext password", release_gates)

        # 5. Rollback provenance conditions and exact drop statement
        self.assertIn("DROP USER 'retailbot2_obs'@'127.0.0.1';", release_gates)
        self.assertIn("Rollback is strictly prohibited unless **all six** provenance conditions are satisfied", release_gates)
        self.assertIn("If creation provenance is uncertain or any condition cannot be proven: **STOP IMMEDIATELY.**", release_gates)

    def test_retailbot2_stage_e_operational_runbook_and_supervision_docs(self):
        """Verify Stage E operational runbook, supervision unit, observability, and retention docs."""
        release_gates = _doc("docs/RETAILBOT2_AND_MODERN_UI_RELEASE_GATES.md").read_text(encoding="utf-8")
        runbook = _doc("docs/RETAILBOT2_OUTBOX_OPERATIONAL_RUNBOOK.md").read_text(encoding="utf-8")

        # 1. Release gates section 4.6
        self.assertIn("### 4.6 Stage E Operational Hardening and Worker Supervision", release_gates)
        self.assertIn("deploy/systemd/retailbot2-outbox-worker.service", release_gates)
        self.assertIn("paper/outbox_health.py", release_gates)
        self.assertIn("paper/outbox_retention.py", release_gates)
        self.assertIn("RETAILBOT2_OUTBOX_OPERATIONAL_RUNBOOK.md", release_gates)

        # 2. Operational runbook key sections
        self.assertIn("## 1. Operating Modes & Authorization Boundaries", runbook)
        self.assertIn("## 2. Worker Prerequisites", runbook)
        self.assertIn("## 3. Environment Variables Reference", runbook)
        self.assertIn("## 4. Service Installation Procedure", runbook)
        self.assertIn("## 5. Startup, Shutdown, and Process Management Commands", runbook)
        self.assertIn("## 6. Health & Queue Inspection", runbook)
        self.assertIn("## 7. Backlog & Latency Diagnosis", runbook)
        self.assertIn("## 8. Dead-Letter Diagnosis & Forensic Triage", runbook)
        self.assertIn("## 9. Advisory Lock Contention & Worker Conflict", runbook)
        self.assertIn("## 10. Worker Crash Recovery & Stale Lease Fencing", runbook)
        self.assertIn("## 11. Uncertain Delivery Recovery (Safe Procedure)", runbook)
        self.assertIn("## 12. Retention Policy Proposals & Dry-Run Planner", runbook)
        self.assertIn("## 13. Explicit Prohibitions for Operators", runbook)

        # 3. Explicit safety notice
        self.assertIn("Installing, enabling, or starting the systemd service does NOT authorize outbox event enqueueing, Telegram delivery, or live trading", runbook)





if __name__ == "__main__":
    unittest.main()
