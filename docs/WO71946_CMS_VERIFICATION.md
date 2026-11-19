# WO71946 — CMS release verification for Antigravity

Verify the GitHub release on CMS and return a pass, fail, or pending verdict for each section. Record the hostname, WIB timestamps, full revision, commands, counts, and redacted errors. Use the release SHA supplied with this work order. Production access in this verification is read-only. Use the existing accounts and keep credentials out of the report.

## 1. Verify the checkout

From /home/BIS/signal-bot, record the current branch, HEAD, upstream, and worktree status. Fetch origin/master. Check the supplied release is reachable from origin/master. The active checkout may still use an older collector branch or contain local work. In that case use a separate verification worktree. Preserve local commits, generated artifacts, configuration and untracked files.

```bash
cd /home/BIS/signal-bot
git status --short --branch
git rev-parse HEAD
git fetch origin master
git merge-base --is-ancestor "$RELEASE_SHA" origin/master
git worktree add --detach "../signal-bot-verify-${RELEASE_SHA:0:8}" "$RELEASE_SHA"
```

Set RELEASE_SHA to the full release SHA from the handoff before running these commands. Reuse an existing verification checkout after confirming its HEAD. An operator updating a clean master checkout can use git pull --ff-only origin master. A pull only updates files. Record the actual executable and working directory for each running service before attributing runtime results to the new revision.

## 2. Reproduce the repository checks

Run in the verification worktree with a separate virtual environment and staging-only configuration. Use the dependency and MariaDB setup in .github/workflows/test.yml for a disposable test instance. The browser harness expects signal_bot_test at 127.0.0.1:3306. Use an isolated container or VM with that loopback address so fixture creation and restoration cannot affect the CMS production database. Keep outbound trading and messaging disabled in staging.

```bash
python -m pytest -q tests/test_work_order_71946.py
PAPER_ORDERS_MARIADB_TESTS=1 WALLET_TRACKER_MARIADB_TESTS=1 python -m pytest -q -rs
(cd frontend && npm ci && npm run build && npm test)
```

Record skips separately. Verify the paper lifecycle cases against MariaDB, including gap fills, duplicate protection, retry after funding failure and persisted execution prices. Verify the funding poller retains the previous watermark on malformed pages and defers remaining symbols after an exhausted HTTP 418.

## 3. Verify the modern dashboard in populated staging

Supply closed BTCUSDT spot 1h candles in the isolated staging database, with sufficient price history and completed signals for chronological training, validation and test. Use a clean, built verification checkout. Install Playwright Chromium if needed. If using a compatible preinstalled headless browser, set STAGING_CHROMIUM_PATH to its executable. Set PYTHON to the staging interpreter.

```bash
export PYTHON="$(command -v python)"
export STAGING_ML_SYMBOL=BTCUSDT
export STAGING_SCREENSHOT_DIR="$(mktemp -d)"
node tests/browser/staging_workflow_acceptance.cjs
```

Require a browser-submitted ML-03 tournament with a successful response, experiment ID, market row count, signal count and winner. For ML-04 require browser deployment of the eligible test artifact, persisted paper config, verified probability on a closed candle, Telegram disabled, viewer denial and ineligible-model rejection. Confirm fixture cleanup. Inspect each classification, including PT-10, which needs an inactive emergency_stop baseline in staging. Record skipped or partial checks individually.

Sign in at /modern/paper-trading. Refresh, navigate to Overview and use browser Back. Confirm the correct view, working assets, and a 404 for a missing asset. Check the funding collector heartbeat and timestamp in Overview, booked funding on open positions, signed funding on closed trades, and aggregate RetailBot2 outbox counts in admin Settings. Verify a viewer cannot see the outbox counts. Keep the production UI flag at its existing value throughout verification.

## 4. Observe the CMS services and reconcile production data

```bash
for unit in signal-collectors signal-screener signal-funding-collector signal-futures-poller signal-paper-trading; do
  systemctl show "$unit.service" -p ActiveState -p SubState -p MainPID -p NRestarts -p WorkingDirectory
done
```

Using the existing read-only access path, confirm the database identity before selecting from crypto_signals. Capture the funding heartbeat twice across one configured polling interval. Confirm independent OHLCV and paper-engine progress across a candle close. For Telegram, reconcile recent persisted signal delivery states with service journal acknowledgments. Idle periods and rate-limit cooldowns are valid observations. Keep Telegram chat IDs, tokens and database credentials out of evidence.

For BTCUSDT, ETHUSDT and BNBUSDT, compare one recent finalized futures 1h row per symbol to Binance USD-M REST /fapi/v1/klines for the same UTC open time. Compare OHLC, base volume, quote volume and trade count using the database column precision. Compare settled funding event timestamps, rates and mark prices to /fapi/v1/fundingRate. Record source values, stored values and deltas. Distinguish a source access failure or an open candle from a data mismatch.

For each newly closed, engine-generated paper trade after the new process actually starts, verify gross PnL equals signed (executed exit minus executed entry) times quantity, reversing the sign for SHORT. Verify net PnL equals gross minus both fees plus signed funding PnL. Compare stored amounts at the schema precision, allowing for rounding of individual persisted prices and quantities. Match by position ID and check that it closes once. Keep administrative legacy closures and pre-release fills separate. If there is no new trade, return PENDING_NEW_CLOSE and retain the last observed ledger ID and timestamp. Do not force a signal.

Return a paper strategy comparison for a stated time window. Include strategy, market, closed count, wins/losses/breakevens, gross PnL, fees, signed funding, net PnL and expectancy in R. Include open exposure separately. Compare RetailBot2 shadow and inverse results on their documented gross basis. Do not rank strategies with zero closed trades.

## Acceptance and recovery

Report separate verdicts for repository tests, populated browser workflows, running-service revision, market data, Telegram and paper ledger. Attach redacted failures with their timestamps and the last known good revision. A successful checkout or staging pass cannot establish a production runtime verdict. Keep the current service configuration in place during verification. Escalate any deployment restart, UI cutover, historical data repair or rollback to the operator with the affected service and evidence.
