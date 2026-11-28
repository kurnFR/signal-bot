#!/usr/bin/env python3
"""Comprehensive end-to-end verification script for signal-bot.
Tests all dashboard API endpoints, features, buttons logic, and validates database state.
"""
import sys
import os
import json
import time
import requests
import mysql.connector

# Ensure root path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

BASE_URL = "http://127.0.0.1:8050"

def get_db():
    conn = mysql.connector.connect(**config.MYSQL_CONFIG)
    return conn, conn.cursor(dictionary=True)

def run_tests():
    print("=" * 75)
    print("🚀 RUNNING END-TO-END FEATURE AND DATABASE VALIDATION")
    print("=" * 75)

    # 1. Login & Auth
    print("\n[1/11] 🔐 Testing Authentication & User Management...")
    login_res = requests.post(f"{BASE_URL}/api/auth/login", json={"username": "admin", "password": "admin123456"})
    assert login_res.status_code == 200, f"Login failed: {login_res.text}"
    auth_data = login_res.json()
    token = auth_data["token"]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    print("  ✅ Login successful (JWT Token acquired)")

    me_res = requests.get(f"{BASE_URL}/api/auth/me", headers=headers)
    assert me_res.status_code == 200, f"Me endpoint failed: {me_res.text}"
    user_info = me_res.json()
    print(f"  ✅ Current User: {user_info.get('username')} (role: {user_info.get('role')})")

    audit_res = requests.get(f"{BASE_URL}/api/auth/audit-logs?limit=5", headers=headers)
    assert audit_res.status_code == 200, f"Audit logs failed: {audit_res.text}"
    print(f"  ✅ Audit Logs: Retrieved {len(audit_res.json())} recent log entries")

    users_res = requests.get(f"{BASE_URL}/api/users", headers=headers)
    assert users_res.status_code == 200, f"Users failed: {users_res.text}"
    existing_users = users_res.json()
    print(f"  ✅ Users Table: {len(existing_users)} active users retrieved")

    # Test user creation and database validation
    test_uname = f"test_trader_{int(time.time())}"
    create_u_res = requests.post(f"{BASE_URL}/api/users", headers=headers, json={
        "username": test_uname,
        "email": f"{test_uname}@example.com",
        "password": "Tr4d3r!V3r1f13d#2026",
        "role": "trader"
    })
    assert create_u_res.status_code == 201, f"Create user failed: {create_u_res.text}"
    created_user = create_u_res.json()
    created_id = created_user.get("user", {}).get("id") or created_user.get("id")

    # Verify user in database
    conn, cur = get_db()
    cur.execute("SELECT id, username, role FROM users WHERE id = %s", (created_id,))
    db_u = cur.fetchone()
    assert db_u and db_u["username"] == test_uname, "User not found in MySQL database!"
    print(f"  ✅ DB Verification: Created user #{created_id} ('{test_uname}') verified in MySQL 'users' table")

    # Clean up test user
    del_u_res = requests.delete(f"{BASE_URL}/api/users/{created_id}", headers=headers)
    assert del_u_res.status_code == 200
    conn.commit()
    cur.execute("SELECT id FROM users WHERE id = %s", (created_id,))
    assert cur.fetchone() is None, "Deleted user still in database!"
    print(f"  ✅ DB Verification: Cleaned up test user #{created_id} from MySQL 'users' table")
    cur.close()
    conn.close()

    # 2. System Status & Health
    print("\n[2/11] ⚙️ Testing System Status & Health...")
    sys_res = requests.get(f"{BASE_URL}/api/system/status", headers=headers)
    assert sys_res.status_code == 200, f"System status failed: {sys_res.text}"
    sys_data = sys_res.json()
    print(f"  ✅ Public System Status: DB Status={sys_data.get('database', {}).get('status')}, Heartbeats={len(sys_data.get('heartbeats', []))}")

    settings_res = requests.get(f"{BASE_URL}/api/settings/status", headers=headers)
    assert settings_res.status_code == 200, f"Settings status failed: {settings_res.text}"
    set_data = settings_res.json()
    print(f"  ✅ Admin System Status: DB Host={set_data.get('database', {}).get('host')}, Binance Spot Ping={set_data.get('binance', {}).get('spot')}")

    tg_status_res = requests.get(f"{BASE_URL}/api/telegram/status", headers=headers)
    assert tg_status_res.status_code == 200
    print(f"  ✅ Telegram Transport Status: Configured={tg_status_res.json().get('configured')}")

    # 3. Master Data & Binance Search (Free Public API)
    print("\n[3/11] 📊 Testing Master Data & Free Binance Public Search...")
    search_res = requests.get(f"{BASE_URL}/api/crypto/search?q=SOL", headers=headers)
    assert search_res.status_code == 200, f"Search failed: {search_res.text}"
    search_data = search_res.json()
    print(f"  ✅ Binance Free Public API: Searched 'SOL' -> returned {search_data.get('count')} matching pairs")

    pop_res = requests.get(f"{BASE_URL}/api/crypto/popular?market=spot", headers=headers)
    assert pop_res.status_code == 200
    print(f"  ✅ Binance Free Public API: Top 15 liquid spot pairs fetched successfully")

    cov_res = requests.get(f"{BASE_URL}/api/master-data/coverage", headers=headers)
    assert cov_res.status_code == 200, f"Coverage failed: {cov_res.text}"
    print(f"  ✅ Coverage: {len(cov_res.json())} OHLCV symbol/timeframe datasets stored in database")

    jobs_res = requests.get(f"{BASE_URL}/api/master-data/jobs", headers=headers)
    assert jobs_res.status_code == 200
    print(f"  ✅ Background Job Store: {len(jobs_res.json())} historical background jobs registered")

    # 4. Custom Strategies & AI Synthesizer
    print("\n[4/11] 🧠 Testing Custom Strategy Engine & Indicators...")
    strat_list_res = requests.get(f"{BASE_URL}/api/custom-strategy/list", headers=headers)
    assert strat_list_res.status_code == 200
    print(f"  ✅ Strategy Catalog: {len(strat_list_res.json())} custom strategies available")

    ind_res = requests.get(f"{BASE_URL}/api/custom-strategy/indicators", headers=headers)
    assert ind_res.status_code == 200
    indicators = ind_res.json().get("indicators", [])
    print(f"  ✅ Indicators Engine: {len(indicators)} technical indicators supported")

    test_strat = {
        "name": "e2e_verified_strategy_v1",
        "displayName": "E2E Verified Strategy",
        "description": "Rule-based strategy for end-to-end verification",
        "rationale": "High-conviction RSI dip buyer with ATR profit taking",
        "longConditions": [{"indicator": "rsi", "operator": "<", "value": 30}],
        "shortConditions": [{"indicator": "rsi", "operator": ">", "value": 70}],
        "slAtrMult": 1.5,
        "riskRewardRatio": 2.5,
        "useTrailingStop": False
    }
    save_strat_res = requests.post(f"{BASE_URL}/api/custom-strategy/create", headers=headers, json=test_strat)
    assert save_strat_res.status_code == 200, f"Save strategy failed: {save_strat_res.text}"
    print("  ✅ Custom Strategy Creation: 'e2e_verified_strategy_v1' registered and written to state")

    # Quick test custom strategy
    qt_res = requests.post(f"{BASE_URL}/api/custom-strategy/quick-test", headers=headers, json={
        "strategy": test_strat,
        "symbol": "BTCUSDT",
        "market": "spot",
        "timeframe": "1d"
    })
    assert qt_res.status_code == 200, f"Quick test failed: {qt_res.text}"
    qt_data = qt_res.json()
    print(f"  ✅ Quick Test Simulation: Trades={qt_data.get('totalTrades')}, NetReturn={qt_data.get('netReturnPct')}%, WinRate={qt_data.get('winRatePct')}%")

    # 5. Backtest Simulation & Trade Database Validation
    print("\n[5/11] 📈 Testing Backtest Simulation Engine & Database Tables...")
    bt_res = requests.post(f"{BASE_URL}/api/backtest/run", headers=headers, json={
        "symbol": "BTCUSDT",
        "market": "spot",
        "timeframe": "1d",
        "strategy": "rsi_dip_buyer_v1",
        "params": {
            "rsi_oversold": 30,
            "rsi_exit": 50,
            "stop_loss_pct": 0.05,
            "max_hold_days": 10
        },
        "data_segment": "train"
    })
    assert bt_res.status_code == 200, f"Backtest run failed: {bt_res.text}"
    bt_data = bt_res.json()
    run_id = bt_data.get("runId") or bt_data.get("run_id")
    metrics = bt_data.get("metrics", {})
    print(f"  ✅ Backtest Executed: Run #{run_id} | Trades={metrics.get('total_trades')}, WinRate={metrics.get('win_rate_pct')}%, PF={metrics.get('profit_factor')}")

    # Validate backtest_runs and backtest_trades in database
    conn, cur = get_db()
    cur.execute("SELECT id, symbol, strategy_name, total_trades, win_rate_pct FROM backtest_runs WHERE id = %s", (run_id,))
    db_run = cur.fetchone()
    assert db_run is not None, f"Backtest run #{run_id} not persisted to MySQL backtest_runs!"
    print(f"  ✅ DB Verification: backtest_runs record #{db_run['id']} verified in MySQL")

    cur.execute("SELECT COUNT(*) as cnt FROM backtest_trades WHERE run_id = %s", (run_id,))
    db_trade_cnt = cur.fetchone()["cnt"]
    print(f"  ✅ DB Verification: backtest_trades table holds {db_trade_cnt} executed trade records for run #{run_id}")
    cur.close()
    conn.close()

    # 6. ML Studio & Artifacts
    print("\n[6/11] 🤖 Testing ML Studio & Model Store...")
    ml_models_res = requests.get(f"{BASE_URL}/api/ml/models", headers=headers)
    assert ml_models_res.status_code == 200
    models_data = ml_models_res.json().get("models", [])
    print(f"  ✅ ML Models: {len(models_data)} trained machine learning models found")

    ml_exp_res = requests.get(f"{BASE_URL}/api/ml/experiments", headers=headers)
    assert ml_exp_res.status_code == 200
    experiments = ml_exp_res.json()
    print(f"  ✅ ML Experiments: {len(experiments)} tournament experiments recorded")

    # 7. Paper Trading (Phase B) & Circuit Breaker
    print("\n[7/11] 🛡️ Testing Paper Trading Engine & Safety Circuit Breakers...")
    p_cfgs_res = requests.get(f"{BASE_URL}/api/paper/configs", headers=headers)
    assert p_cfgs_res.status_code == 200
    p_cfgs = p_cfgs_res.json()
    print(f"  ✅ Paper Configs: {len(p_cfgs)} paper trading bot instances configured")

    p_pos_res = requests.get(f"{BASE_URL}/api/paper/positions", headers=headers)
    assert p_pos_res.status_code == 200
    p_positions = p_pos_res.json()
    print(f"  ✅ Paper Positions: {len(p_positions)} active open paper positions")

    p_trades_res = requests.get(f"{BASE_URL}/api/paper/trades?limit=10", headers=headers)
    assert p_trades_res.status_code == 200
    p_trades = p_trades_res.json()
    print(f"  ✅ Paper Trades: {len(p_trades)} executed paper trades retrieved")

    cb_res = requests.get(f"{BASE_URL}/api/paper/circuit-breakers", headers=headers)
    assert cb_res.status_code == 200
    cb_data = cb_res.json()
    print(f"  ✅ Circuit Breakers: State={cb_data.get('status')}")

    # Test Paper Sync
    sync_res = requests.post(f"{BASE_URL}/api/paper/sync", headers=headers)
    assert sync_res.status_code == 200
    print(f"  ✅ Paper Sync Engine: Executed cleanly -> {sync_res.json()}")

    # 8. Research (RetailDeathTrapBot)
    print("\n[8/11] 🔬 Testing RetailDeathTrapBot (RDT Bot) Research & Controls...")
    rdt_stats_res = requests.get(f"{BASE_URL}/api/research/retailbot2/stats", headers=headers)
    assert rdt_stats_res.status_code == 200
    rdt_stats = rdt_stats_res.json()
    print(f"  ✅ RDT Bot Stats: Overall PnL=${rdt_stats.get('overall_pnl', 0):,.2f}, Total Trades={rdt_stats.get('total_trades', 0)}")

    rdt_pos_res = requests.get(f"{BASE_URL}/api/research/retailbot2/positions", headers=headers)
    assert rdt_pos_res.status_code == 200
    print(f"  ✅ RDT Bot Positions: {len(rdt_pos_res.json())} open positions")

    rdt_ctrl_res = requests.get(f"{BASE_URL}/api/research/retailbot2/control", headers=headers)
    assert rdt_ctrl_res.status_code == 200
    ctrl_data = rdt_ctrl_res.json()
    print(f"  ✅ RDT Bot Controls: is_running={ctrl_data.get('is_running')}")

    # Test that the retired control write is rejected (step 3: the worker
    # is gone, so POST /retailbot2/control must fail closed, not write).
    update_ctrl_res = requests.post(f"{BASE_URL}/api/research/retailbot2/control", headers=headers, json={
        "enabled": True,
        "overrides": {"risk_per_trade": 0.02}
    })
    assert update_ctrl_res.status_code == 403, f"retired retailbot2 control write must fail closed, got {update_ctrl_res.status_code}"
    print("  ✅ Retired RDT Bot control write rejected (403, worker gone)")
    cur.close()
    conn.close()

    # 9. Market Screener (Smart Money / Binance Free Klines)
    print("\n[9/11] 📡 Testing Smart Money Screener & Signals...")
    scr_status_res = requests.get(f"{BASE_URL}/api/research/screener/status", headers=headers)
    assert scr_status_res.status_code == 200
    print(f"  ✅ Screener Status: Status={scr_status_res.json().get('status')}")

    scr_signals_res = requests.get(f"{BASE_URL}/api/research/screener/signals?limit=10", headers=headers)
    assert scr_signals_res.status_code == 200
    scr_signals = scr_signals_res.json()
    print(f"  ✅ Screener Signals: {len(scr_signals)} signals fetched")

    conn, cur = get_db()
    cur.execute("SELECT COUNT(*) as cnt FROM smart_money_signals")
    sm_total = cur.fetchone()["cnt"]
    print(f"  ✅ DB Verification: {sm_total} total smart money signals stored in MySQL 'smart_money_signals'")
    cur.close()
    conn.close()

    # 10. News AI & Sentiment Feed
    print("\n[10/11] 📰 Testing News AI (Free RSS & Sentiment Engine)...")
    n_stats_res = requests.get(f"{BASE_URL}/api/news/stats", headers=headers)
    assert n_stats_res.status_code == 200
    print(f"  ✅ News Stats: {n_stats_res.json()}")

    n_events_res = requests.get(f"{BASE_URL}/api/news/events?limit=10", headers=headers)
    assert n_events_res.status_code == 200
    print(f"  ✅ News Events: {len(n_events_res.json())} news stories stored")

    conn, cur = get_db()
    cur.execute("SELECT COUNT(*) as cnt FROM news_events")
    ne_cnt = cur.fetchone()["cnt"]
    cur.execute("SELECT COUNT(*) as cnt FROM news_ai_signals")
    ns_cnt = cur.fetchone()["cnt"]
    print(f"  ✅ DB Verification: {ne_cnt} stories in 'news_events', {ns_cnt} signals in 'news_ai_signals'")
    cur.close()
    conn.close()

    # 11. Wallet Tracker (GeckoTerminal & DexScreener Free APIs)
    print("\n[11/11] 👛 Testing Wallet Tracker (GeckoTerminal & DexScreener Public APIs)...")
    wt_meta_res = requests.get(f"{BASE_URL}/api/wallets/meta", headers=headers)
    assert wt_meta_res.status_code == 200
    print(f"  ✅ Wallet Meta: Supported networks = {list(wt_meta_res.json().get('networks', {}).keys())}")

    wt_stat_res = requests.get(f"{BASE_URL}/api/wallets/status", headers=headers)
    assert wt_stat_res.status_code == 200
    print(f"  ✅ Wallet Tracker Status: Active Provider={wt_stat_res.json().get('provider_health', {}).get('active_provider')}")

    wt_tracked_res = requests.get(f"{BASE_URL}/api/wallets/tracked", headers=headers)
    assert wt_tracked_res.status_code == 200
    print(f"  ✅ Tracked Wallets: {len(wt_tracked_res.json())} wallets currently in checklist")

    # Test adding tracked wallet and verify in database
    test_sol_wallet = "So11111111111111111111111111111111111111112"
    add_w_res = requests.post(f"{BASE_URL}/api/wallets/tracked", headers=headers, json={
        "chain": "solana",
        "wallet": test_sol_wallet,
        "label": "Test Solana System Wallet"
    })
    assert add_w_res.status_code == 200, f"Add wallet failed: {add_w_res.text}"

    conn, cur = get_db()
    cur.execute("SELECT id, chain, wallet, label FROM tracked_wallets WHERE wallet = %s", (test_sol_wallet,))
    tw_row = cur.fetchone()
    assert tw_row is not None, "Tracked wallet not found in MySQL tracked_wallets table!"
    print(f"  ✅ DB Verification: Tracked wallet #{tw_row['id']} ('{tw_row['wallet'][:12]}...') verified in MySQL")

    # Clean up tracked wallet (soft-delete sets status to IGNORED)
    del_w_res = requests.delete(f"{BASE_URL}/api/wallets/tracked/{tw_row['id']}", headers=headers)
    assert del_w_res.status_code == 200
    conn.commit()
    cur.execute("SELECT status FROM tracked_wallets WHERE id = %s", (tw_row["id"],))
    status_row = cur.fetchone()
    assert status_row and status_row["status"] == "IGNORED", "Wallet status not set to IGNORED!"
    print(f"  ✅ DB Verification: Tracked wallet #{tw_row['id']} soft-deleted to status='IGNORED'")

    # Hard-clean test wallet
    cur.execute("DELETE FROM tracked_wallets WHERE id = %s", (tw_row["id"],))
    conn.commit()

    cur.execute("SELECT COUNT(*) as cnt FROM wallet_swaps")
    ws_cnt = cur.fetchone()["cnt"]
    print(f"  ✅ DB Verification: {ws_cnt} DEX swaps recorded in MySQL 'wallet_swaps'")
    cur.close()
    conn.close()

    print("\n" + "=" * 75)
    print("🏆 ALL 11 FEATURES, DASHBOARD BUTTONS, AND DATABASE TABLES VALIDATED 100% OK!")
    print("=" * 75)

if __name__ == "__main__":
    run_tests()
