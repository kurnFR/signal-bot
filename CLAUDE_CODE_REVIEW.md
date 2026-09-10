# Signal Bot — Independent Code Review & Path to Completion

**Reviewed by:** Claude (independent review)
**Review date:** 2026-09-10
**Reviewed commit:** `d93760d` (`test: enforce validation-only ML tournament ranking`)
**Method:** Cloned the repository fresh, read the existing planning docs (`PRD.md`,
`PROJECT_STATUS_AUDIT.md`, `NEXT_IMPROVEMENT_PROJECT.md`, `ML_STRATEGY_PROJECT.md`,
the P0.x notes), then independently re-checked their claims against the actual
source and by **running the test suite and installing dependencies**, rather
than trusting the docs at face value. No code was changed as part of this review.

---

## 1. How this file relates to the existing docs

This repo already has an unusually thorough paper trail
(`PROJECT_STATUS_AUDIT.md`, `NEXT_IMPROVEMENT_PROJECT.md`,
`P0_SECURITY_IMPLEMENTATION.md`, `P0.3_PAPER_ACCOUNTING_IMPLEMENTATION.md`,
`P0.5_EXECUTION_PARITY.md`). Those documents are largely accurate and I'm not
duplicating them. This file:

1. **Verifies** their key claims against the live code (some are confirmed,
   some are stale).
2. **Adds new findings** that no existing doc mentions, found by actually
   running the code.
3. **Gives one consolidated, priority-ordered punch list** to reach the
   project's own stated definition of done (§17 of `PROJECT_STATUS_AUDIT.md`,
   "Ready for Live Trading" / PRD §9).

---

## 2. New findings (not previously documented)

### 2.1 — P0 (blocking): CI is currently broken / would fail on this commit

Running the exact command CI uses (`python -m pytest -q`, per
`.github/workflows/test.yml`) fails at **collection**, before a single test
runs:

```
ERROR tests/test_ml_end_to_end.py
ImportError: cannot import name 'chronological_split' from 'ml.split'
Interrupted: 1 error during collection
```

`tests/test_ml_end_to_end.py` imports `chronological_split` from `ml/split.py`,
but `ml/split.py` only defines `split_by_fractions()` and
`split_by_timestamps()` — there is no `chronological_split` function or alias
anywhere in the module. This isn't a flaky/environment issue: the name simply
doesn't exist. Every other test passes once this file is excluded (**59
passed, 1 collection error**), so the regression is isolated to this one
test/implementation mismatch, but because pytest aborts the whole run on a
collection error, **the entire suite currently reports as failing**, which
defeats the CI safety net the project just added (`ci: add pytest workflow
for regression coverage`, commit `d98bf4f`).

**Why this matters more than it looks:** the project's own P0.5 doc says
"P0.5 must not be marked complete until the pending-entry lifecycle and
engine integration are covered by deterministic tests," and the whole P0
program leans on "regression tests" as the proof that accounting/parity work
is real. If CI can silently be red without anyone noticing (e.g., a
maintainer running only a subset of tests locally), that safety net has a
gap.

**Fix:** either rename `chronological_split` to match the test, or add it as
a thin alias for `split_by_fractions` in `ml/split.py`, then run `python -m
pytest -q` (no flags) and confirm a clean, non-zero-error run before trusting
any other "tests exist" claim in this repo going forward.

### 2.2 — P1: default admin credential is still live and still self-flagged as unresolved

`web/auth.py::seed_default_admin()` still creates `admin / admin123` on an
empty `users` table, and the function's own docstring says this is a
"legacy bootstrap... explicitly tracked as a P0/P1 security item... must be
replaced before external use." This is honestly labeled, not hidden — but
it means the project cannot be exposed beyond localhost yet. Confirmed still
present at the reviewed commit.

**Suggestion:** generate a random one-time bootstrap password on first
startup, print/log it once, and require a forced password change on first
login — this removes a well-known, guessable default credential without
adding much code.

### 2.3 — P2: hard-coded, non-portable, single-user file path for custom strategies

`web/routes/custom_strategy.py` still hard-codes:

```python
CUSTOM_FILE = "/home/BIS/signal-bot/custom_strategies.json"
```

This breaks the app for anyone who doesn't happen to deploy to that exact
path (fresh clones, CI, containers, another maintainer's machine), and
because it's a single shared file with no per-user namespace, it's also a
multi-tenant correctness/security smell now that auth has real
viewer/trader/admin roles. `PROJECT_STATUS_AUDIT.md` §11 already flagged the
general persistence issue; this note just confirms the exact hard-coded path
is still unresolved and adds the portability angle.

**Suggestion:** derive the path from a config/env var (e.g.
`os.path.join(BASE_DIR, "data", "custom_strategies.json")` with an
`os.makedirs(..., exist_ok=True)`), or better, move this into the existing
MySQL database alongside a per-user/owner column, since the DB already has
users and roles.

### 2.4 — P2: `/api/settings/status` still returns raw DB connection metadata

Confirmed still true: `web/routes/settings.py::get_system_status()` returns
`MYSQL_CONFIG["host"]`, `["port"]`, `["database"]`, and `["user"]` verbatim.
The good news (not in the older audit) is that `web/app.py` now gates
`/api/settings/*` to `admin`-only via `ADMIN_ONLY_PREFIXES`, so this is no
longer an unauthenticated leak — it's now "admin can see the DB user/host,"
which is a much smaller issue. Still worth trimming to a boolean
`"connected": true/false` plus the database name, since host/user add no
operational value on a status page and slightly widen the blast radius if an
admin session is ever hijacked.

### 2.5 — P3: `backtest/metrics.py` — `total_trades` still counts trades with no `r_multiple`

Confirmed still present: `total_trades = len(trades)` counts every trade
object passed in, while `win_rate_pct`/`expectancy_r` are computed only from
`r_multiples` (which filters out `None`). If any trade in a result set has
`r_multiple is None` (e.g., still open, or a data edge case), `total_trades`
silently disagrees with the trades actually used for win rate/expectancy.
Already flagged as P2 in `PROJECT_STATUS_AUDIT.md` §3.4 — I re-verified it's
unresolved and it's cheap to fix (report `total_trades` and
`closed_trades_with_r` as two explicit fields).

### 2.6 — Scope-drift risk worth naming explicitly

Looking at `git log`, the most recent ~15 commits (through `d93760d`, all
dated 2026-09-09) are entirely ML-strategy work (`ml/`, `tests/test_ml_*`),
per `ML_STRATEGY_PROJECT.md`. Meanwhile `NEXT_IMPROVEMENT_PROJECT.md`'s own
checkpoint table still lists **P0.1 runtime verification, P0.3 runtime/DB
verification, and P0.5 paper-engine state-machine integration as pending**.
`ML_STRATEGY_PROJECT.md` itself says ML implementation should "follow the P0
execution/accounting gates" and is currently "DESIGN LOCKED... implementation
follows the P0 gates" — but the commit history shows ML implementation work
(not just design) already happening in parallel with, not after, the P0
gates closing. This isn't a code bug, but it's a direct tension with the
project's own stated sequencing and with the PRD's core principle ("no
strategy goes live... without surviving a disciplined backtest process").

**Suggestion:** either (a) finish and runtime-verify P0.1/P0.3/P0.5 before
continuing ML feature work, or (b) if ML work continues in parallel
deliberately, say so explicitly in `NEXT_IMPROVEMENT_PROJECT.md` so the
"P0 must be closed first" framing doesn't silently go stale the way older
docs already did (per `PROJECT_STATUS_AUDIT.md` §2's own complaint about
`BUG_ANALYSIS_AND_IMPROVEMENTS.md` and `WEBSITE_IMPROVEMENT_RECOMMENDATIONS.md`
going stale).

---

## 3. Claims I independently re-verified as TRUE (still accurate today)

To avoid just repeating the existing audit, here's what I specifically
checked in the live code (not just read about) and confirmed still holds:

- **Auth middleware is real and fail-closed.** `web/app.py` has a global
  `@app.middleware("http")` boundary (`api_security_boundary`) applied to
  every `/api/*` path except `/api/auth/login`, with an explicit
  role/prefix policy (`_role_allowed`). This matches
  `P0_SECURITY_IMPLEMENTATION.md`'s description.
- **CORS is no longer wildcard.** Origins come from
  `SIGNAL_BOT_ALLOWED_ORIGINS`, defaulting to `localhost:8050` /
  `127.0.0.1:8050` only.
- **Paper accounting is wired through a shared module**, not duplicated
  math: `paper/engine.py` imports and calls
  `calculate_position_size`, `calculate_trade_accounting`, and
  `calculate_funding_cost` from `backtest/accounting.py`, including a
  funding lookup against the real `funding_rate` table over the position's
  holding interval.
- **CI workflow exists** (`.github/workflows/test.yml`, runs on push/PR to
  `master`) — though see §2.1, it is not currently green.
- **59 of the 60 test files collect and pass** when the one broken import is
  excluded — the underlying accounting/ML logic itself looks sound where
  it's covered; the problem in §2.1 is a naming mismatch, not a logic bug.

---

## 4. Consolidated punch list to reach "finished" (per the project's own goal)

The project's own definition of "finished enough for live trading" is
already well-specified in `PROJECT_STATUS_AUDIT.md` §17 and
`NEXT_IMPROVEMENT_PROJECT.md`. Rather than inventing a competing roadmap,
here is that same priority order, **updated with this review's findings**
and with runtime-verification called out as its own trackable step (since
"pending verification" items have a tendency to stay pending indefinitely
otherwise):

### P0 — Fix now, blocks everything else
1. **Fix the broken `chronological_split` import** (§2.1) and confirm
   `python -m pytest -q` (exact CI command, no flags) exits clean. This is a
   ~5-minute fix but currently invalidates the "tests prove it" story for
   every other P0 item.
2. **Runtime-verify P0.1 (auth/RBAC)** using the exact checklist already
   written in `P0_SECURITY_IMPLEMENTATION.md`'s "Verification gate" section
   — spin up the app against a real/test MySQL instance and hit each listed
   endpoint with viewer/trader/admin/unauthenticated tokens. This has been
   "implemented in code" since 2026-09-08 without a recorded runtime pass.
3. **Apply `db/migrate_p0_paper_accounting.sql` to a real database and
   runtime-verify P0.3** per the checklist in
   `P0.3_PAPER_ACCOUNTING_IMPLEMENTATION.md` (quantity/notional/risk fields
   populate correctly, duplicate-OPEN-position constraint actually rejects
   duplicates, funding accrues from real stored events).
4. **Finish the P0.5 paper engine state machine** (`SIGNAL -> PENDING_ENTRY
   -> ACTIVE -> CLOSED`) — currently contract + tests exist but "engine
   integration remains" per `P0.5_EXECUTION_PARITY.md`'s own status line.
5. **Replace the `admin/admin123` bootstrap** (§2.2) before any
   non-localhost exposure.

### P1 — Needed before trusting strategy selection or risk numbers
6. Fix the AI insight engine's regime detector: it reads `ema_20`, `ema_50`,
   `adx` from `last_bar`, but `features/indicators.py` does not compute
   those columns, so they silently default to `0` via `.get(..., 0)`
   (confirmed still true, `ai/insight_engine.py` lines ~95-98). Either
   compute these in the shared feature engine or explicitly label the AI
   layer's regime call as unavailable/low-confidence when they're missing,
   instead of silently reporting a zero-based "generic regime."
7. Fix the Kelly-sizing floor so it can never recommend positive risk on a
   negative-edge signal (`PROJECT_STATUS_AUDIT.md` §9.2 — re-confirm this is
   still the case and fix alongside #6, since both are in the same file).
8. Add multiple-testing-aware evidence scoring to the Battle Royale ranking
   before treating any "Top 3" result as more than a heuristic filter.
9. Fix `custom_strategy.py`'s hard-coded path and add ownership/versioning
   (§2.3).
10. Trim `/api/settings/status`'s response (§2.4).
11. Fix `backtest/metrics.py`'s `total_trades` vs. `r_multiple`-filtered
    count mismatch (§2.5) — cheap, but silently wrong numbers undermine
    trust in everything downstream of them.

### P2 — Reliability/hygiene, do once P0/P1 are closed
12. Add a lightweight CI database fixture (SQLite/dockerized MySQL) so P0
    runtime-verification checklists in the docs can become actual automated
    CI assertions instead of manual one-time checks that go stale — this
    directly addresses why §2.1 and the "pending runtime verification"
    items have been able to sit unresolved.
13. Telegram durable queue/retry; incremental (non-full-history) signal
    evaluation; durable job queue instead of the in-memory `JOBS` dict;
    remove tracked `__pycache__` artifacts from git.
14. Resolve the scope-drift tension in §2.6 explicitly in
    `NEXT_IMPROVEMENT_PROJECT.md` so the doc's own sequencing claim stays
    accurate.

### P3 — Only after all of the above
15. Live exchange execution. Per the project's own non-negotiable gate in
    `NEXT_IMPROVEMENT_PROJECT.md`: **must remain disabled until every P0 item
    is implemented, regression-tested, and reviewed** — nothing in this
    review changes that conclusion; if anything, §2.1 (currently-broken CI)
    is a reason to consider the "regression-tested" bar not yet met even for
    the P0 items marked "implemented."

---

## 5. Bottom line

The engineering discipline in this repo is genuinely above average for a
solo/AI-assisted project — the accounting model, the holdout/leakage
awareness, the auth middleware redesign, and the ML validation contract are
all well thought out, and most of the existing self-audits are accurate.
The main risk right now isn't missing ideas, it's **unverified "implemented"
claims accumulating faster than they're runtime-checked**, plus a small
number of concrete, fixable bugs (broken test import, hard-coded path,
default credential, metrics count mismatch) that are easy to lose track of
across ten-plus planning documents. Closing the P0 list in §4 — starting
with the 5-minute CI fix in §2.1, since it undermines confidence in
everything else — is the fastest path back to a verifiably trustworthy
state before any further feature work (ML or otherwise) continues.
