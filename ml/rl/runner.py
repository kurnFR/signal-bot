"""
Reinforcement Learning Execution and Evaluation Runner.
Provides end-to-end training, out-of-sample testing, and metric computation.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from backtest.metrics import compute_metrics
from db.db import fetch_ohlcv_with_features_df
from ml.rl.agents import DQNAgent, PolicyGradientAgent
from ml.rl.environment import CryptoTradingEnv

RL_MODELS_DIR = Path("ml_models") / "rl"


def train_rl_model(
    symbol: str,
    market: str = "spot",
    timeframe: str = "1h",
    agent_type: str = "dqn",
    episodes: int = 15,
    reward_type: str = "sharpe",
    train_fraction: float = 0.70,
    seed: int = 42,
) -> dict[str, Any]:
    """Train a reinforcement learning agent on historical data with an OOS test split."""
    np.random.seed(seed)
    RL_MODELS_DIR.mkdir(parents=True, exist_ok=True)

    df = fetch_ohlcv_with_features_df(symbol, market, timeframe, closed_only=True)
    if len(df) < 150:
        raise ValueError(f"Insufficient candles ({len(df)}) for RL training; need at least 150")

    split_idx = int(len(df) * train_fraction)
    train_df = df.iloc[:split_idx].reset_index(drop=True)
    test_df = df.iloc[split_idx:].reset_index(drop=True)

    train_env = CryptoTradingEnv(train_df, reward_type=reward_type)
    test_env = CryptoTradingEnv(test_df, reward_type=reward_type)

    state_dim = train_env.observation_space.shape[0]
    action_dim = train_env.action_space.n

    agent_type_clean = agent_type.strip().lower()
    if agent_type_clean == "dqn":
        agent = DQNAgent(state_dim=state_dim, action_dim=action_dim, hidden_dim=64, lr=0.001)
    elif agent_type_clean in {"policy_gradient", "pg"}:
        agent = PolicyGradientAgent(state_dim=state_dim, action_dim=action_dim, lr=0.005)
    else:
        raise ValueError(f"Unsupported RL agent_type: '{agent_type}'. Use 'dqn' or 'policy_gradient'.")

    training_history = []

    # Training Loop
    for ep in range(1, episodes + 1):
        obs, info = train_env.reset()
        ep_reward = 0.0
        done = False

        while not done:
            action = agent.select_action(obs, evaluate=False)
            next_obs, reward, terminated, truncated, step_info = train_env.step(action)
            done = terminated or truncated

            if isinstance(agent, DQNAgent):
                agent.buffer.push(obs, action, reward, next_obs, done)
                agent.train_step()
            elif isinstance(agent, PolicyGradientAgent):
                agent.record_step(obs, action, reward)

            obs = next_obs
            ep_reward += reward

        if isinstance(agent, PolicyGradientAgent):
            agent.train_episode()

        final_equity = float(train_env.equity)
        ret_pct = (final_equity - train_env.initial_capital) / train_env.initial_capital * 100.0
        training_history.append({
            "episode": ep,
            "total_reward": round(ep_reward, 2),
            "final_equity": round(final_equity, 2),
            "return_pct": round(ret_pct, 2),
            "trades_count": len(train_env.trades),
        })

    # Save trained model
    model_filename = f"rl_{agent_type_clean}_{symbol}_{market}_{timeframe}.npz"
    model_path = RL_MODELS_DIR / model_filename
    agent.save(model_path)

    # Out-of-sample Evaluation
    oos_result = evaluate_rl_agent(agent, test_env)

    result = {
        "model_id": model_filename,
        "symbol": symbol,
        "market": market,
        "timeframe": timeframe,
        "agent_type": agent_type_clean,
        "episodes": episodes,
        "reward_type": reward_type,
        "train_candles": len(train_df),
        "test_candles": len(test_df),
        "training_history": training_history,
        "out_of_sample_metrics": oos_result["metrics"],
        "out_of_sample_trades_count": len(oos_result["trades"]),
        "model_saved_path": str(model_path),
    }

    # Save audit record
    audit_file = RL_MODELS_DIR / f"{model_filename}.json"
    audit_file.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def evaluate_rl_agent(agent: Any, env: CryptoTradingEnv) -> dict[str, Any]:
    """Run an evaluation pass with deterministic greedy policy."""
    obs, info = env.reset()
    done = False
    action_counts = {0: 0, 1: 0, 2: 0}

    while not done:
        action = agent.select_action(obs, evaluate=True)
        action_counts[action] = action_counts.get(action, 0) + 1
        obs, reward, terminated, truncated, step_info = env.step(action)
        done = terminated or truncated

    metrics = compute_metrics(env.trades)
    return {
        "trades": env.trades,
        "metrics": metrics,
        "final_equity": float(env.equity),
        "action_distribution": action_counts,
        "equity_curve": env.equity_history,
    }
