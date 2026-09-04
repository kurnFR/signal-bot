# Bug Analysis & Improvements Review
## Crypto Signal Bot — Signal-Bot Project

**Status**: Local project pulled from GitHub (`3e6b0ef` — Web Dashboard, Auth & Paper Trading added)
**Purpose**: Documentation review only — no code changes applied. The project is past the testing/validation phase; `validate_step1.py` and `validate_step2.py` are no longer in active use.

---

## Project Phase Notice
This project has progressed beyond the Step 1/Step 2 validation phase. The following scripts are **not used** in the current final project:
- `validate_step1.py` — formerly confirmed OHLCV coverage, collector heartbeats, and supplementary table recency
- `validate_step2.py` — formerly checked indicator row counts, NaN percentages, and value ranges
These scripts have been superseded by the web dashboard's built-in coverage matrices and the Phase B paper trading engine. They are retained in the repository for historical reference only.

---

## 1. SYMBOLS Configuration Mismatch

### Issue
`config.py:22-27` defines `SYMBOLS` with only 3 entries:
```python
SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
]
```
The README acknowledges 7 more symbols (`SOLUSDT, XRPUSDT, DOGEUSDT, ADAUSDT, AVAXUSDT, LINKUSDT, TONUSDT`) are configured but have zero data — a known gap from the earlier scope.

### Current Status
Since the project is no longer running validation checks, this mismatch is **documented but not actioned**. The web dashboard handles symbol coverage independently via its own discovery matrix.

### Recommendation
Leave as-is for reference, or update `SYMBOLS` to match the 10-symbol scope if the project ever returns to active validation mode.

---

## 2. Web Dashboard Authentication (Phase B)

### Current Status
The web dashboard authentication (PBKDF2-HMAC-SHA256, 200,000 rounds) is active and in use. Default credentials `admin`/`admin123` are documented for initial setup only.

### Recommendation
- Change default credentials on first launch
- Review `web/auth.py` for production hardening (salt management, session security, SQL injection prevention)
- The `admin`/`trader` role-based access control is functional and used by the web interface

---

## 3. Migration Documentation

### Issue
The README and `db/` directory contain several migration scripts that are no longer relevant to the current project state:
- `db/migrate_add_wib_columns.sql` — WIB timezone column additions (Step 1 procedure)
- `db/migrate_add_trail_exit_reason.sql` — trailing stop exit reason migration
- `db/migrate_add_users_and_paper.sql` — users + paper trading tables (Phase B)

Since the project has moved to Phase B (web dashboard + paper trading), these migrations have either been applied or are superseded by the new schema.

### Current Status
- The web dashboard `run_web.py` includes its own migration: `db/migrate_add_users_and_paper.sql`
- Existing MySQL databases should have these applied before launching the web server
- WIB columns and trail exit reason are now managed by the web dashboard, not CLI scripts

### Recommendation
- Remove references to `validate_step1.py`/`validate_step2.py` from all documentation as "no longer in use"
- Keep migration scripts in `db/` for reference, but mark them as "Phase A — applies only before Phase B launch"
- Update README Setup section to reflect: "Phase B assumes Phase A migrations have been applied; see `db/migrate_add_users_and_paper.sql`"

---

## 4. Backtest Parameter Override

### Current Status
No `--override` CLI flag exists. Parameters are sourced from `config.py` or strategy-specific defaults. The web dashboard handles parameter configuration via its UI.

### Recommendation
- No action needed — the web dashboard's parameter configuration replaces the need for CLI overrides
- The `backtest/optimize.py` and holdout check mechanisms can remain for historical reference

---

## 5. Indicator & Feature Validation

### Current Status
`validate_step2.py` is **not used** in the current project flow. Indicator correctness is verified by the web dashboard's on-demand backfill and feature engineering endpoints. The `features/` module (`indicators.py`, `build_features.py`) is the single source of truth for all indicator calculations shared between backtester and live signal engine.

### Recommendation
- Retain `indicators.py` and `build_features.py` as the canonical indicator source
- Remove any documentation referencing `validate_step2.py` as "active validation step"
- The web dashboard logs and coverage matrices serve the validation function previously held by `validate_step1.py`/`validate_step2.py`

---

## 6. Backfill & Collection Scripts

### Current Status
- `backfill_klines.py` — still functional for re-running historical data
- `run_collectors.py` — still active for real-time collection (if `ENABLE_FUTURES=true`)
- `check_futures_access.py` — still usable but no longer a mandatory prerequisite since funding strategies may not be active

### Recommendation
- Keep `backfill_klines.py` and `run_collectors.py` in the repository
- `check_futures_access.py` can remain but is optional; the web dashboard handles futures access configuration

---

## Summary of Changes from Previous Analysis

| Item | Previous Status | Current Status |
|------|-----------------|----------------|
| `validate_step1.py` | Active validation step | **Not used** — replaced by web dashboard |
| `validate_step2.py` | Active validation step | **Not used** — replaced by web dashboard |
| Migration scripts | Phase A procedures | **Phase A only** — apply before Phase B launch |
| `SYMBOLS` mismatch | Action required | **Documented; not actioned** (project past validation) |
| Web auth defaults | Change immediately | **Active; change on first launch** |
| Parameter overrides | CLI `--override` needed | **Handled by web dashboard UI** |

---

## Priority Actions (Updated)

| Priority | Issue | Action |
|----------|-------|--------|
| **P0** | Web auth default credentials `admin`/`admin123` | Change immediately on first launch; force password reset |
| **P1** | Migration scripts referenced but Phase A→B transition unclear | Document: apply `db/migrate_add_users_and_paper.sql` before launching web server; mark other migrations as "Phase A only" |
| **P2** | `validate_step1.py`/`validate_step2.py` referenced as active | **Remove from active workflow docs** — these scripts exist for historical reference only; project uses web dashboard for coverage verification |
| **P3** | `SYMBOLS` only 3 entries vs top 10 | **No action** — project past the symbol-validation phase; web dashboard manages its own symbol matrix |

---
*This document reflects the project's current state: past the Step 1/Step 2 validation phase, in active use with web dashboard and paper trading. No code changes have been applied. This file is for documentation and reference only.*