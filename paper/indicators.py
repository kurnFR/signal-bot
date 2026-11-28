"""Technical indicators shared by Backtest Studio and the paper engine.

Extracted from the retired RetailBot2 worker (``paper/retailbot2.py``,
deleted in the retailbot2 migration) so that importing indicators no
longer pulls in the worker's heavy dependencies (ccxt, MySQL pools,
Telegram).
"""
from typing import List, Tuple

import numpy as np
import pandas as pd


class Indicators:
    @staticmethod
    def rsi(series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain = delta.where(delta > 0, 0.0).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(window=period).mean()
        rs = gain / (loss + 1e-10)  # Avoid division by zero
        return 100.0 - (100.0 / (1.0 + rs))

    @staticmethod
    def ema(series: pd.Series, span: int) -> pd.Series:
        return series.ewm(span=span, adjust=False).mean()

    @staticmethod
    def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        hl = df['high'] - df['low']
        hc = (df['high'] - df['close'].shift()).abs()
        lc = (df['low'] - df['close'].shift()).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        return tr.rolling(window=period).mean()

    @staticmethod
    def macd(series: pd.Series, fast: int = 12,
             slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
        ema_f = series.ewm(span=fast, adjust=False).mean()
        ema_s = series.ewm(span=slow, adjust=False).mean()
        line = ema_f - ema_s
        sig = line.ewm(span=signal, adjust=False).mean()
        hist = line - sig
        return line, sig, hist

    @staticmethod
    def bollinger(series: pd.Series,
                  period: int = 20, std: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
        mid = series.rolling(window=period).mean()
        s = series.rolling(window=period).std()
        return mid + std * s, mid, mid - std * s

    @staticmethod
    def find_sr_levels(df: pd.DataFrame, lookback: int,
                       threshold: float, min_touches: int) -> Tuple[List[float], List[float]]:
        """
        Find S/R by clustering local pivot highs and lows.
        [F1] More precise — uses actual pivot points not all candle data
        """
        if len(df) < lookback + 5:
            return [], []

        recent = df.iloc[-lookback:].copy()
        current_price = df['close'].iloc[-2]  # Use completed candle

        # Find local pivot highs and lows (5-bar pivot)
        pivot_highs = []
        pivot_lows = []
        closes = recent['high'].values
        lows_arr = recent['low'].values

        for i in range(2, len(recent) - 2):
            # Pivot high: higher than 2 bars each side
            if (closes[i] > closes[i-1] and closes[i] > closes[i-2] and
                    closes[i] > closes[i+1] and closes[i] > closes[i+2]):
                pivot_highs.append(closes[i])
            # Pivot low
            if (lows_arr[i] < lows_arr[i-1] and lows_arr[i] < lows_arr[i-2] and
                    lows_arr[i] < lows_arr[i+1] and lows_arr[i] < lows_arr[i+2]):
                pivot_lows.append(lows_arr[i])

        def cluster_levels(levels: List[float]) -> List[float]:
            if not levels:
                return []
            levels_sorted = sorted(levels)
            clusters = []
            used = set()
            for i, lv in enumerate(levels_sorted):
                if i in used:
                    continue
                group = [lv]
                for j in range(i + 1, len(levels_sorted)):
                    if j in used:
                        continue
                    if abs(levels_sorted[j] - lv) / (lv + 1e-10) < threshold:
                        group.append(levels_sorted[j])
                        used.add(j)
                used.add(i)
                if len(group) >= min_touches:
                    clusters.append(float(np.mean(group)))
            return clusters

        all_supports = cluster_levels(
            [lv for lv in pivot_lows if lv < current_price]
        )
        all_resistances = cluster_levels(
            [lv for lv in pivot_highs if lv > current_price]
        )

        return sorted(all_supports, reverse=True), sorted(all_resistances)
