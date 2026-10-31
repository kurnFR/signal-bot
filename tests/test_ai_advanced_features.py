"""
Comprehensive automated test suite for advanced AI/ML features:
1. Continuous Online Retraining & Walk-Forward Inference (FreqAI/Qlib style)
2. FinRL-style Reinforcement Learning Trading Environment & Agents
3. Enhanced Backtesting Engine with WFA, Sharpe, Sortino, Calmar, and SQN
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.metrics import compute_metrics
from backtest.walk_forward import split_into_folds
from ml.rl.environment import CryptoTradingEnv
from ml.rl.agents import DQNAgent, PolicyGradientAgent
from ml.walk_forward_engine import compute_drift_psi, run_ml_walk_forward
from ml.online_retrainer import (
    register_model_for_retraining,
    get_retraining_status,
)


def _make_dummy_candles(n: int = 150) -> pd.DataFrame:
    np.random.seed(42)
    base_price = 100.0 + np.cumsum(np.random.randn(n) * 0.5)
    high = base_price + np.random.uniform(0.5, 2.0, n)
    low = base_price - np.random.uniform(0.5, 2.0, n)
    close = base_price + np.random.randn(n) * 0.2
    open_p = np.roll(close, 1)
    open_p[0] = 100.0

    return pd.DataFrame({
        "open_time": np.arange(n) * 3600000,
        "open": open_p,
        "high": high,
        "low": low,
        "close": close,
        "volume": np.random.randint(1000, 50000, n),
        "rsi": np.random.uniform(25, 75, n),
        "macd": np.random.randn(n),
        "macd_signal": np.random.randn(n),
        "macd_hist": np.random.randn(n),
        "atr": np.random.uniform(1.0, 3.0, n),
        "atr_pct": np.random.uniform(0.01, 0.03, n),
        "volume_sma": np.random.randint(2000, 40000, n),
        "volume_ratio": np.random.uniform(0.8, 2.5, n),
        "support": low - 1.0,
        "resistance": high + 1.0,
        "swing_high": high + 0.5,
        "swing_low": low - 0.5,
        "fib_0": low,
        "fib_236": low + 0.236 * (high - low),
        "fib_382": low + 0.382 * (high - low),
        "fib_5": (high + low) / 2.0,
        "fib_618": low + 0.618 * (high - low),
        "fib_786": low + 0.786 * (high - low),
        "fib_1": high,
    })


# =====================================================================
# 1. Backtesting Engine Improvements: Advanced Metrics
# =====================================================================

def test_enhanced_backtesting_metrics():
    """Verify calculation of Sharpe, Sortino, Calmar, SQN, and drawdown duration."""
    trades = [
        {"r_multiple": 1.5},
        {"r_multiple": -1.0},
        {"r_multiple": 2.0},
        {"r_multiple": 0.5},
        {"r_multiple": -0.8},
        {"r_multiple": 3.0},
    ]
    m = compute_metrics(trades)

    # Core existing keys must be intact
    assert m["total_trades"] == 6
    assert m["scored_trades"] == 6
    assert m["win_rate_pct"] == pytest.approx(66.667, abs=0.01)
    assert m["expectancy_r"] > 0
    assert m["profit_factor"] > 1.0
    assert m["max_drawdown_r"] >= 0.0

    # Enhanced metrics must be populated
    assert "sharpe_ratio" in m and m["sharpe_ratio"] is not None
    assert "sortino_ratio" in m and m["sortino_ratio"] is not None
    assert "calmar_ratio" in m and m["calmar_ratio"] is not None
    assert "sqn" in m and m["sqn"] is not None
    assert "avg_win_r" in m and m["avg_win_r"] is not None
    assert "avg_loss_r" in m and m["avg_loss_r"] is not None
    assert "payoff_ratio" in m and m["payoff_ratio"] is not None
    assert "max_consecutive_wins" in m and m["max_consecutive_wins"] >= 1
    assert "max_drawdown_duration_trades" in m


def test_empty_metrics_handling():
    """Ensure empty trade lists return None/0 safely without crashing."""
    m = compute_metrics([])
    assert m["total_trades"] == 0
    assert m["scored_trades"] == 0
    assert m["sharpe_ratio"] is None
    assert m["sortino_ratio"] is None
    assert m["calmar_ratio"] is None


# =====================================================================
# 2. Reinforcement Learning Environment & Agents (FinRL-style)
# =====================================================================

def test_crypto_trading_env_lifecycle():
    """Test standard Gymnasium API: reset, step, observation space, action space."""
    df = _make_dummy_candles(50)
    env = CryptoTradingEnv(df, reward_type="sharpe")

    obs, info = env.reset()
    assert obs.shape == env.observation_space.shape
    assert info["equity"] == 10000.0

    # Step through all actions: 0=FLAT, 1=LONG, 2=SHORT
    for action in [1, 1, 0, 2, 2, 0]:
        next_obs, reward, terminated, truncated, step_info = env.step(action)
        assert next_obs.shape == env.observation_space.shape
        assert isinstance(reward, float)
        assert isinstance(terminated, bool)
        assert "equity" in step_info

    assert len(env.trades) >= 1


def test_dqn_agent_learning_step():
    """Test DQNAgent action selection, experience replay, and weight updates."""
    df = _make_dummy_candles(40)
    env = CryptoTradingEnv(df)
    obs, _ = env.reset()

    agent = DQNAgent(state_dim=obs.shape[0], action_dim=3, hidden_dim=16, batch_size=8)
    action = agent.select_action(obs, evaluate=False)
    assert action in (0, 1, 2)

    # Push transitions
    for _ in range(12):
        act = agent.select_action(obs)
        next_obs, r, done, _, _ = env.step(act)
        agent.buffer.push(obs, act, r, next_obs, done)
        obs = next_obs
        if done:
            break

    loss = agent.train_step()
    assert loss is not None
    assert loss >= 0.0


def test_policy_gradient_agent_episode():
    """Test PolicyGradientAgent (REINFORCE) action probabilities and gradient step."""
    df = _make_dummy_candles(30)
    env = CryptoTradingEnv(df)
    obs, _ = env.reset()

    agent = PolicyGradientAgent(state_dim=obs.shape[0], action_dim=3, lr=0.01)
    probs = agent.get_action_probabilities(obs)
    assert probs.shape == (1, 3)
    assert np.isclose(np.sum(probs), 1.0)

    for _ in range(10):
        act = agent.select_action(obs)
        next_obs, r, done, _, _ = env.step(act)
        agent.record_step(obs, act, r)
        obs = next_obs
        if done:
            break

    reward_mean = agent.train_episode()
    assert isinstance(reward_mean, float)


# =====================================================================
# 3. Continuous Online Retraining & Walk-Forward (FreqAI/Qlib style)
# =====================================================================

def test_drift_psi_computation():
    """Test Population Stability Index (PSI) drift calculation."""
    np.random.seed(42)
    train_dist = np.random.normal(0, 1, (100, 4))
    similar_dist = np.random.normal(0, 1, (100, 4))
    drifted_dist = np.random.normal(3, 2, (100, 4))

    psi_stable = compute_drift_psi(train_dist, similar_dist)
    psi_drifted = compute_drift_psi(train_dist, drifted_dist)

    assert psi_stable < 0.20
    assert psi_drifted > psi_stable


def test_online_retraining_registry():
    """Test registering and querying continuous retraining state."""
    entry = register_model_for_retraining(
        symbol="ETHUSDT",
        market="spot",
        timeframe="1h",
        strategy_name="trend_ema_v1",
        model_type="hist_gradient_boosting",
        auto_promote=True,
    )
    assert entry["symbol"] == "ETHUSDT"
    assert entry["status"] == "active"

    status = get_retraining_status()
    assert status["registered_models_count"] >= 1
    assert any(m["symbol"] == "ETHUSDT" for m in status["models"])


def test_ml_walk_forward_engine():
    """Test rolling walk-forward temporal ML validation."""
    n = 100
    df = _make_dummy_candles(n)
    df["target"] = np.random.choice([0, 1], size=n)
    df["outcome_r"] = np.where(df["target"] == 1, np.random.uniform(0.5, 2.5, n), -1.0)

    res = run_ml_walk_forward(
        dataset=df,
        feature_columns=["rsi", "atr", "volume_ratio", "macd_hist"],
        model_type="hist_gradient_boosting",
        n_folds=3,
        min_train_samples=25,
        expanding=False,
    )

    assert "folds" in res
    assert len(res["folds"]) >= 1
    assert "mean_drift_psi" in res
    assert "drift_status" in res
    assert "cumulative_oos_metrics" in res

