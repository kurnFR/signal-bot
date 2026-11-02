"""
Reinforcement Learning Execution and Evaluation Runner.
Provides end-to-end training, out-of-sample testing, and metric computation.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from backtest.metrics import compute_metrics
from db.db import fetch_ohlcv_with_features_df
from ml.rl.agents import DQNAgent, PolicyGradientAgent
from ml.rl.environment import CryptoTradingEnv

RL_MODELS_DIR = Path("ml_models") / "rl"

_RL_SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,20}$")
_RL_TIMEFRAME_RE = re.compile(r"^[A-Za-z0-9]{1,10}$")
_RL_MARKETS = {"spot", "futures"}
_RL_AGENT_TYPES = {"dqn", "policy_gradient", "pg"}
_RL_REWARD_TYPES = {"pnl", "sharpe", "sortino", "r_multiple"}


def _validate_rl_training_inputs(
    symbol: str,
    market: str,
    timeframe: str,
    agent_type: str,
    reward_type: str,
    episodes: int,
) -> tuple[str, str, str, str, str]:
    if not isinstance(symbol, str):
        raise ValueError("symbol must be a string")
    symbol = symbol.strip().upper()
    if not _RL_SYMBOL_RE.fullmatch(symbol):
        raise ValueError("symbol must contain 2-20 ASCII letters or digits")

    if not isinstance(market, str):
        raise ValueError("market must be a string")
    market = market.strip().lower()
    if market not in _RL_MARKETS:
        raise ValueError("market must be 'spot' or 'futures'")

    if not isinstance(timeframe, str):
        raise ValueError("timeframe must be a string")
    timeframe = timeframe.strip()
    if not _RL_TIMEFRAME_RE.fullmatch(timeframe):
        raise ValueError("timeframe must contain 1-10 ASCII letters or digits")

    if not isinstance(agent_type, str):
        raise ValueError("agent_type must be a string")
    agent_type = agent_type.strip().lower()
    if agent_type not in _RL_AGENT_TYPES:
        raise ValueError("agent_type must be 'dqn' or 'policy_gradient'")

    if not isinstance(reward_type, str):
        raise ValueError("reward_type must be a string")
    reward_type = reward_type.strip().lower()
    if reward_type not in _RL_REWARD_TYPES:
        raise ValueError("unsupported RL reward_type")

    if isinstance(episodes, bool) or not isinstance(episodes, int) or not 1 <= episodes <= 50:
        raise ValueError("episodes must be an integer between 1 and 50")

    return symbol, market, timeframe, agent_type, reward_type


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
    symbol, market, timeframe, agent_type_clean, reward_type = _validate_rl_training_inputs(
        symbol, market, timeframe, agent_type, reward_type, episodes
    )
    np.random.seed(seed)

    root = RL_MODELS_DIR.resolve()
    root.mkdir(parents=True, exist_ok=True)
    model_filename = f"rl_{agent_type_clean}_{symbol}_{market}_{timeframe}.npz"
    model_path = (root / model_filename).resolve()
    audit_file = (root / f"{model_filename}.json").resolve()
    for artifact_path in (model_path, audit_file):
        try:
            artifact_path.relative_to(root)
        except ValueError as exc:
            raise ValueError("RL artifact path must remain inside the RL model directory") from exc

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

    # Save trained model only after the resolved destination has passed containment checks.
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
        "model_saved_path": str(RL_MODELS_DIR / model_filename),
    }

    # Save audit record to the separately containment-checked destination.
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

