"""
Gymnasium-compatible Reinforcement Learning Trading Environment.
FinRL-style action-space environment with realistic execution, fee subtraction,
slippage modeling, and customizable reward formulations.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import gymnasium as gym
from gymnasium import spaces
import numpy as np
import pandas as pd

from backtest.accounting import apply_entry_slippage, apply_exit_slippage
from ml.dataset import SHARED_FEATURE_COLUMNS


class CryptoTradingEnv(gym.Env):
    """
    Reinforcement Learning environment for single-asset trading.

    Action Space:
        0 = CASH / FLAT (no open position, close existing)
        1 = LONG
        2 = SHORT

    Observation Space:
        Vector of normalized market features + agent state:
        [features..., position (-1, 0, 1), unrealized_pnl_pct, holding_bars_norm, cash_ratio]
    """

    metadata = {"render_modes": ["human"]}

    def __init__(
        self,
        df: pd.DataFrame,
        feature_columns: Sequence[str] | None = None,
        initial_capital: float = 10000.0,
        fee_pct: float = 0.00075,      # 0.075% maker/taker
        slippage_pct: float = 0.0005,  # 0.05% slippage
        reward_type: str = "sharpe",   # "pnl", "sharpe", "sortino", "r_multiple"
        max_holding_bars: int = 48,
        risk_per_trade_pct: float = 2.0,
    ):
        super().__init__()

        if len(df) < 20:
            raise ValueError("DataFrame must contain at least 20 bars")

        self.df = df.reset_index(drop=True)
        self.feature_columns = list(feature_columns) if feature_columns else [
            c for c in SHARED_FEATURE_COLUMNS if c in self.df.columns
        ]
        if not self.feature_columns:
            # Fallback to price ratios if feature columns not present
            for col in ["atr", "rsi", "volume_ratio", "macd_hist"]:
                if col in self.df.columns:
                    self.feature_columns.append(col)

        self.initial_capital = float(initial_capital)
        self.fee_pct = float(fee_pct)
        self.slippage_pct = float(slippage_pct)
        self.reward_type = reward_type
        self.max_holding_bars = int(max_holding_bars)
        self.risk_per_trade_pct = float(risk_per_trade_pct)

        # Precompute normalized features matrix for speed
        raw_features = self.df[self.feature_columns].copy().fillna(0.0).values
        # Robust z-score normalization
        mean = np.nanmean(raw_features, axis=0)
        std = np.nanstd(raw_features, axis=0) + 1e-8
        self.normalized_features = np.clip((raw_features - mean) / std, -5.0, 5.0)

        # 3 internal state features: [position (-1, 0, 1), unrealized_pnl_pct, holding_bars_norm]
        obs_dim = self.normalized_features.shape[1] + 3
        self.observation_space = spaces.Box(
            low=-10.0, high=10.0, shape=(obs_dim,), dtype=np.float32
        )
        self.action_space = spaces.Discrete(3)

        # Runtime state
        self.current_step = 0
        self.position = 0.0            # -1.0, 0.0, +1.0
        self.entry_price = 0.0
        self.entry_step = 0
        self.capital = self.initial_capital
        self.equity = self.initial_capital
        self.trades = []
        self.returns_history = []
        self.equity_history = []

    def reset(
        self,
        *,
        seed: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        super().reset(seed=seed)

        self.current_step = 0
        self.position = 0.0
        self.entry_price = 0.0
        self.entry_step = 0
        self.capital = self.initial_capital
        self.equity = self.initial_capital
        self.trades = []
        self.returns_history = []
        self.equity_history = [self.initial_capital]

        obs = self._get_observation()
        info = {"equity": self.equity, "capital": self.capital, "step": self.current_step}
        return obs, info

    def _get_observation(self) -> np.ndarray:
        current_bar = self.df.iloc[self.current_step]
        close_price = float(current_bar["close"])

        unrealized_pnl_pct = 0.0
        if self.position != 0.0 and self.entry_price > 0:
            if self.position > 0:
                unrealized_pnl_pct = (close_price - self.entry_price) / self.entry_price
            else:
                unrealized_pnl_pct = (self.entry_price - close_price) / self.entry_price

        holding_bars = (self.current_step - self.entry_step) if self.position != 0.0 else 0
        holding_bars_norm = min(1.0, holding_bars / max(1, self.max_holding_bars))

        state_meta = np.array([
            self.position,
            np.clip(unrealized_pnl_pct * 10.0, -5.0, 5.0),
            holding_bars_norm,
        ], dtype=np.float32)

        market_features = self.normalized_features[self.current_step].astype(np.float32)
        return np.concatenate([market_features, state_meta])

    def step(self, action: int) -> tuple[np.ndarray, float, bool, bool, dict[str, Any]]:
        if self.current_step >= len(self.df) - 1:
            terminated = True
            return self._get_observation(), 0.0, terminated, False, {"equity": self.equity}

        current_bar = self.df.iloc[self.current_step]
        next_bar = self.df.iloc[self.current_step + 1]

        # Execution happens on next bar open
        exec_price = float(next_bar["open"])
        target_pos = 0.0 if action == 0 else (1.0 if action == 1 else -1.0)

        # Force timeout exit if held beyond max holding bars
        if self.position != 0.0 and (self.current_step - self.entry_step) >= self.max_holding_bars:
            target_pos = 0.0

        pnl_step = 0.0
        cost_step = 0.0

        # Position change logic
        if target_pos != self.position:
            # Close existing position if any
            if self.position != 0.0:
                direction = "LONG" if self.position > 0 else "SHORT"
                exit_exec = apply_exit_slippage(exec_price, direction, self.slippage_pct)
                raw_ret = (exit_exec - self.entry_price) / self.entry_price if direction == "LONG" else (self.entry_price - exit_exec) / self.entry_price
                fee = exit_exec * self.fee_pct / self.entry_price
                net_ret = raw_ret - fee

                allocated = self.capital * (self.risk_per_trade_pct / 100.0) * 10.0  # nominal notional
                trade_pnl = allocated * net_ret
                self.capital += trade_pnl
                cost_step += allocated * fee
                pnl_step += trade_pnl

                # Record trade
                self.trades.append({
                    "entry_step": self.entry_step,
                    "exit_step": self.current_step + 1,
                    "direction": direction,
                    "entry_price": self.entry_price,
                    "exit_price": exit_exec,
                    "net_pnl": trade_pnl,
                    "return_pct": net_ret * 100.0,
                    "r_multiple": net_ret / (self.risk_per_trade_pct / 100.0) if self.risk_per_trade_pct > 0 else net_ret,
                    "holding_bars": (self.current_step + 1) - self.entry_step,
                })
                self.position = 0.0
                self.entry_price = 0.0

            # Open new position if target_pos is non-zero
            if target_pos != 0.0:
                direction = "LONG" if target_pos > 0 else "SHORT"
                entry_exec = apply_entry_slippage(exec_price, direction, self.slippage_pct)
                fee = entry_exec * self.fee_pct
                self.position = target_pos
                self.entry_price = entry_exec
                self.entry_step = self.current_step + 1
                cost_step += fee
                self.capital -= fee
        else:
            # Maintain position: mark to market change on next bar close
            if self.position != 0.0:
                next_close = float(next_bar["close"])
                curr_close = float(current_bar["close"])
                step_ret = (next_close - curr_close) / curr_close if self.position > 0 else (curr_close - next_close) / curr_close
                allocated = self.capital * (self.risk_per_trade_pct / 100.0) * 10.0
                pnl_step = allocated * step_ret

        # Advance step
        self.current_step += 1
        self.equity = max(1.0, self.capital + pnl_step)
        self.equity_history.append(self.equity)

        step_return = (self.equity - self.equity_history[-2]) / max(1.0, self.equity_history[-2])
        self.returns_history.append(step_return)

        # Reward formulation
        reward = self._calculate_reward(step_return)

        terminated = self.current_step >= len(self.df) - 1 or self.equity <= (0.2 * self.initial_capital)
        obs = self._get_observation()
        info = {
            "equity": self.equity,
            "capital": self.capital,
            "step": self.current_step,
            "position": self.position,
            "total_trades": len(self.trades),
        }
        return obs, reward, terminated, False, info

    def _calculate_reward(self, step_return: float) -> float:
        if self.reward_type == "pnl":
            return float(step_return * 100.0)

        elif self.reward_type == "sharpe":
            # Differential Sharpe reward
            if len(self.returns_history) < 5:
                return float(step_return * 100.0)
            window = self.returns_history[-30:]
            std = float(np.std(window)) + 1e-6
            mean = float(np.mean(window))
            sharpe = (mean / std) * math.sqrt(252)
            return float(step_return * 50.0 + sharpe * 0.1)

        elif self.reward_type == "sortino":
            # Penalize downside volatility
            if len(self.returns_history) < 5:
                return float(step_return * 100.0)
            window = self.returns_history[-30:]
            downside = [r for r in window if r < 0]
            d_std = float(np.std(downside)) if downside else 1e-6
            sortino = (float(np.mean(window)) / (d_std + 1e-6)) * math.sqrt(252)
            return float(step_return * 50.0 + sortino * 0.1)

        # Default: normalized step return
        return float(step_return * 100.0)
