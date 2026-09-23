"""
████████████████████████████████████████████████████████████████████
██  RETAIL DEATH TRAP BOT v2.1 - PRODUCTION FIXES               ██
██  Fix: Mixed signals | Fix: All 7 strategies | Fix: Inverse   ██
██  Fix: Confluence count | Fix: Separate equity per mode       ██
████████████████████████████████████████████████████████████████████

FIXES IN v2.1:
  [F1] SR_TRAP threshold tightened — was triggering on everything
  [F2] DBL_PAT tolerance tightened — same issue
  [F3] RSI strategy fixed — uses range not exact cross
  [F4] BB strategy fixed — percentage proximity not exact touch
  [F5] MACD divergence fixed — robust swing detection
  [F6] Spike fade fixed — correct candle indexing
  [F7] MA crossover fixed — handles flat EMA edge case
  [F8] Confluence score correctly reflects triggering strategies
  [F9] Inverse trades use SEPARATE slot counter from shadow
  [F10] max_open_trades applies per-mode not globally
  [F11] Signals can be SHORT — trend context added to SR/DBL
  [F12] Strategy signal logging shows actual reasons
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from logging.handlers import RotatingFileHandler
from enum import Enum
import warnings
import ccxt
import mysql.connector
from mysql.connector import pooling
import logging
import time
import requests
import traceback
import random
import json
import os
from dotenv import load_dotenv

load_dotenv()

warnings.filterwarnings('ignore')


# ═══════════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════════

class SignalMode(Enum):
    SHADOW = "shadow"
    INVERSE = "inverse"
    BOTH = "both"


class StrategyID(Enum):
    RSI_COUNTER_TREND = "RSI_CTR"
    MA_CROSSOVER_WHIPSAW = "MA_WHIP"
    SR_BOUNCE_TRAP = "SR_TRAP"
    MACD_DIVERGENCE = "MACD_DIV"
    BBAND_REVERT = "BB_REV"
    DOUBLE_PATTERN = "DBL_PAT"
    SPIKE_FADE = "SPIKE_FADE"


# ═══════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════

@dataclass
class BotConfig:
    symbols: List[str] = field(default_factory=lambda: [
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT",
        "XRP/USDT", "DOGE/USDT", "ADA/USDT", "AVAX/USDT"
    ])

    signal_mode: str = "both"

    # Timeframe
    primary_timeframe: str = "15m"

    # Strategy 1: RSI — classic retail levels
    rsi_period: int = 14
    rsi_oversold: int = 35      # [F3] Wider than 30 = more triggers
    rsi_overbought: int = 65    # [F3] Wider than 70 = more triggers
    rsi_zone_width: int = 5     # RSI must be within this of threshold

    # Strategy 2: MA Crossover
    ma_fast: int = 9
    ma_slow: int = 21
    ma_trend: int = 200         # For context only (retail ignores it)

    # Strategy 3: S/R Bounce
    sr_lookback: int = 50
    # [F1] Tightened from 0.002 to 0.0015 — still triggers but less spam
    sr_touch_threshold: float = 0.0015
    sr_min_touches: int = 2

    # Strategy 4: MACD
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal_period: int = 9
    divergence_lookback: int = 30

    # Strategy 5: Bollinger Bands
    bb_period: int = 20
    bb_std: float = 2.0
    # [F4] Band proximity — within 0.3% of band counts
    bb_proximity_pct: float = 0.003

    # Strategy 6: Double Pattern
    pattern_lookback: int = 60
    # [F2] Tightened from 0.003 to 0.002
    pattern_tolerance: float = 0.002
    pattern_min_bars_apart: int = 10

    # Strategy 7: Spike Fade
    spike_atr_multiplier: float = 2.5  # Raised — less noise
    # [F6] Fixed candle index — confirmed candles only

    # Risk
    atr_period: int = 14
    atr_sl_multiplier: float = 1.5
    rr_ratio: float = 2.0
    risk_per_trade: float = 0.01
    initial_capital: float = 10000.0

    # [F10] Separate limits per mode
    max_open_trades_per_mode: int = 8   # 4 shadow + 4 inverse = 8 total
    max_trades_per_symbol: int = 1
    min_trade_interval_hours: int = 2

    # Infrastructure
    db_host: str = field(default_factory=lambda: os.getenv('RETAILBOT2_DB_HOST', os.getenv('DB_HOST', os.getenv('MYSQL_HOST', '192.168.1.30'))))
    db_port: int = int(os.getenv('RETAILBOT2_DB_PORT', os.getenv('DB_PORT', os.getenv('MYSQL_PORT', '3306'))))
    db_name: str = os.getenv('RETAILBOT2_DB_NAME', os.getenv('DB_NAME', 'Binance'))
    db_user: str = field(default_factory=lambda: os.getenv('RETAILBOT2_DB_USER', os.getenv('DB_USER', os.getenv('MYSQL_USER', 'cms'))))
    db_password: str = field(default_factory=lambda: os.getenv('RETAILBOT2_DB_PASSWORD', os.getenv('DB_PASS', os.getenv('MYSQL_PASSWORD', ''))))
    telegram_token: str = field(default_factory=lambda: os.getenv('TELEGRAM_TOKEN', os.getenv('TELEGRAM_BOT_TOKEN', '')))
    telegram_chat_id: str = field(default_factory=lambda: os.getenv('TELEGRAM_CHAT_ID', ''))


    exit_check_interval: int = 5
    api_retries: int = 3
    api_retry_delay: float = 1.0
    heartbeat_interval: int = 60


# ═══════════════════════════════════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════════════════════════════════

class TelegramAlert:
    """
    Fixed version with:
    - Proper token validation
    - Detailed error logging
    - Automatic retry on network error
    - Response body logging on failure
    - Rate limit handling (30 msg/sec Telegram limit)
    """

    def __init__(self, token: str, chat_id: str, logger: logging.Logger = None):
        self.token = str(token).strip()
        self.chat_id = str(chat_id).strip()
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self.logger = logger or logging.getLogger("Telegram")
        self._last_send_time = 0.0
        self._min_interval = 0.05  # Max 20 msg/sec (safe under 30 limit)

        # Validate on init — tell user exactly what's wrong
        self.enabled = self._validate()

    def _validate(self) -> bool:
        """Strict validation with clear error messages."""
        
        if not self.token or self.token in ("", "YOUR_TOKEN", "YOUR_BOT_TOKEN_HERE"):
            self.logger.warning("⚠️  Telegram DISABLED: token is empty/placeholder")
            return False

        if ':' not in self.token:
            self.logger.error(
                f"❌ Telegram DISABLED: token missing colon separator\n"
                f"   Got:    '{self.token}'\n"
                f"   Expect: '1234567890:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'"
            )
            return False

        parts = self.token.split(':', 1)
        if not parts[0].isdigit():
            self.logger.error(
                f"❌ Telegram DISABLED: token prefix must be numeric bot ID\n"
                f"   Got prefix: '{parts[0]}'"
            )
            return False

        if len(parts[1]) < 30:
            self.logger.error(
                f"❌ Telegram DISABLED: token hash too short "
                f"({len(parts[1])} chars, expected 35+)"
            )
            return False

        if not self.chat_id or self.chat_id in ("", "YOUR_CHAT_ID", "YOUR_CHAT_ID_HERE"):
            self.logger.warning("⚠️  Telegram DISABLED: chat_id is empty/placeholder")
            return False

        # chat_id must be numeric (positive for users, negative for groups)
        try:
            int(self.chat_id)
        except ValueError:
            self.logger.error(
                f"❌ Telegram DISABLED: chat_id must be numeric\n"
                f"   Got: '{self.chat_id}'\n"
                f"   User chat_id looks like: 123456789\n"
                f"   Group chat_id looks like: -1001234567890"
            )
            return False

        # Live connectivity test
        try:
            resp = requests.get(
                f"{self.base_url}/getMe",
                timeout=8
            )
            data = resp.json()
            if resp.status_code == 200 and data.get('ok'):
                bot_name = data['result'].get('first_name', 'Unknown')
                bot_user = data['result'].get('username', 'N/A')
                self.logger.info(
                    f"✅ Telegram connected: {bot_name} (@{bot_user})"
                )
                return True
            else:
                desc = data.get('description', 'No description')
                self.logger.error(
                    f"❌ Telegram DISABLED: getMe failed — {desc}\n"
                    f"   HTTP {resp.status_code}"
                )
                return False
        except requests.exceptions.ConnectionError:
            self.logger.error(
                "❌ Telegram DISABLED: Cannot reach api.telegram.org\n"
                "   Check internet connection"
            )
            return False
        except requests.exceptions.Timeout:
            self.logger.error(
                "❌ Telegram DISABLED: Connection timed out\n"
                "   Firewall may be blocking api.telegram.org"
            )
            return False
        except Exception as e:
            self.logger.error(f"❌ Telegram validation error: {e}")
            return False

    def send(self, message: str, parse_mode: str = "HTML",
             retries: int = 2) -> bool:
        """Send with retry, rate limiting, and full error reporting."""
        if not self.enabled:
            # Console fallback — clean up HTML tags
            clean = (message
                     .replace('<b>', '').replace('</b>', '')
                     .replace('<i>', '').replace('</i>', '')
                     .replace('\n', ' | '))
            print(f"📝 [NO-TG] {clean[:160]}")
            return False

        # Rate limit protection
        now = time.time()
        gap = now - self._last_send_time
        if gap < self._min_interval:
            time.sleep(self._min_interval - gap)

        for attempt in range(retries + 1):
            try:
                resp = requests.post(
                    f"{self.base_url}/sendMessage",
                    json={
                        'chat_id': self.chat_id,
                        'text': message,
                        'parse_mode': parse_mode,
                        'disable_web_page_preview': True,
                    },
                    timeout=10,
                )
                self._last_send_time = time.time()

                if resp.status_code == 200:
                    return True

                # Parse error
                try:
                    err_data = resp.json()
                    err_desc = err_data.get('description', 'No description')
                    err_code = err_data.get('error_code', resp.status_code)
                except Exception:
                    err_desc = resp.text[:200]
                    err_code = resp.status_code

                # Handle specific errors
                if resp.status_code == 429:
                    # Too many requests
                    retry_after = err_data.get('parameters', {}).get('retry_after', 5)
                    self.logger.warning(
                        f"⏳ Telegram rate limited — waiting {retry_after}s"
                    )
                    time.sleep(retry_after)
                    continue

                elif resp.status_code == 400:
                    # Bad request — don't retry, log full details
                    self.logger.error(
                        f"❌ Telegram 400 Bad Request: {err_desc}\n"
                        f"   Message preview: {message[:100]}"
                    )
                    
                    # Bad parse mode — try plain text
                    if 'parse' in err_desc.lower() and parse_mode != 'plain':
                        self.logger.info("   Retrying without parse_mode...")
                        plain_resp = requests.post(
                            f"{self.base_url}/sendMessage",
                            json={
                                'chat_id': self.chat_id,
                                'text': message.replace('<b>', '').replace('</b>', ''),
                            },
                            timeout=10,
                        )
                        return plain_resp.status_code == 200
                    return False

                elif resp.status_code == 401:
                    self.logger.error(
                        f"❌ Telegram 401 Unauthorized: Invalid token\n"
                        f"   Disabling Telegram alerts"
                    )
                    self.enabled = False
                    return False

                elif resp.status_code == 403:
                    self.logger.error(
                        f"❌ Telegram 403 Forbidden: {err_desc}\n"
                        f"   Bot may be blocked by user or not started\n"
                        f"   → Open Telegram → find your bot → send /start"
                    )
                    return False

                else:
                    self.logger.warning(
                        f"⚠️  Telegram HTTP {err_code}: {err_desc} "
                        f"(attempt {attempt + 1}/{retries + 1})"
                    )
                    if attempt < retries:
                        time.sleep(2 ** attempt)

            except requests.exceptions.Timeout:
                self.logger.warning(
                    f"⏳ Telegram timeout (attempt {attempt + 1}/{retries + 1})"
                )
                if attempt < retries:
                    time.sleep(2)

            except requests.exceptions.ConnectionError as e:
                self.logger.warning(f"🌐 Telegram connection error: {e}")
                if attempt < retries:
                    time.sleep(3)

            except Exception as e:
                self.logger.error(f"❌ Telegram unexpected error: {e}")
                break

        self.logger.error(f"❌ Telegram send failed after {retries + 1} attempts")
        return False

    # Keep your existing send_entry / send_exit / send_stats methods
    # Just change __init__ calls to pass logger:

    def send_entry(self, signal: Dict, trade_id: int, units: float,
                   risk_amt: float, risk_pct: float, mode: str) -> bool:
        side_emoji = "🟢 LONG" if signal['side'] == 'LONG' else "🔴 SHORT"
        mode_tag = "🔄 INVERSE" if mode == "inverse" else "👁️ SHADOW"
        strategies = signal.get('strategies_triggered', [signal.get('strategy', '?')])
        confluence = signal.get('confluence_score', len(strategies))

        # Build strategy list safely
        strat_lines = []
        for s in strategies:
            strat_lines.append(f"  ├ {s}")
        strat_text = "\n".join(strat_lines) if strat_lines else "  ├ N/A"

        # Truncate reason to avoid Telegram 4096 char limit
        reason = str(signal.get('reason', 'N/A'))[:200]

        msg = (
            f"{side_emoji} <b>{mode_tag} #{trade_id}</b>\n"
            f"\n"
            f"<b>Symbol:</b> {signal['symbol']}\n"
            f"<b>Time:</b> {signal.get('candle_close_time', 'N/A')}\n"
            f"\n"
            f"<b>📊 PLAN:</b>\n"
            f"  ├ Entry:  <code>${signal['entry']:.6f}</code>\n"
            f"  ├ Stop:   <code>${signal['stop']:.6f}</code>\n"
            f"  └ Target: <code>${signal['target']:.6f}</code>\n"
            f"\n"
            f"<b>💰 RISK:</b>\n"
            f"  ├ Units: {units:.6f}\n"
            f"  ├ Risk:  ${risk_amt:.2f} ({risk_pct:.1f}%)\n"
            f"  └ R:R:   1:{signal.get('rr', 1.0):.1f}\n"
            f"\n"
            f"<b>🎯 Strategies ({confluence}/7):</b>\n"
            f"{strat_text}\n"
            f"\n"
            f"<b>Reason:</b> <i>{reason}</i>"
        )
        return self.send(msg)

    def send_exit(self, trade: Dict, exit_price: float,
                  reason: str, pnl: float, pnl_pct: float) -> bool:
        emoji_map = {
            'TAKE_PROFIT': "🎯✅",
            'STOP_LOSS': "🛑❌",
        }
        emoji = emoji_map.get(reason, "⏹️")
        color = "🟢" if pnl >= 0 else "🔴"
        mode = trade.get('mode', 'shadow').upper()

        msg = (
            f"{emoji} <b>EXIT [{mode}] #{trade.get('id', '?')}</b>\n"
            f"\n"
            f"<b>Symbol:</b> {trade.get('symbol')} "
            f"<b>Side:</b> {trade.get('side')}\n"
            f"<b>Strategy:</b> {trade.get('strategy', '?')}\n"
            f"<b>Reason:</b> {reason.replace('_', ' ')}\n"
            f"\n"
            f"{color} <b>P&L:</b> ${pnl:+.2f} ({pnl_pct:+.2f}%)\n"
            f"{color} <b>Exit:</b> <code>${exit_price:.6f}</code>\n"
            f"\n"
            f"⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
        )
        return self.send(msg)

    def send_stats(self, stats: Dict,
                   equity_shadow: float, equity_inverse: float) -> bool:
        lines = [
            "📊 <b>PERFORMANCE REPORT</b>",
            "─" * 30,
            f"💰 Shadow Equity:  <code>${equity_shadow:,.2f}</code>",
            f"🔄 Inverse Equity: <code>${equity_inverse:,.2f}</code>",
            "",
        ]
        for key, data in sorted(stats.items()):
            total = data.get('total', 0)
            if total == 0:
                continue
            wins = data.get('wins', 0)
            losses = data.get('losses', 0)
            wr = wins / total * 100 if total > 0 else 0
            net = float(data.get('net_pnl', 0))
            color = "🟢" if net >= 0 else "🔴"
            lines.append(
                f"{color} <b>{key}</b>: "
                f"T={total} W={wins} L={losses} "
                f"WR={wr:.0f}% Net=${net:+.0f}"
            )

        return self.send("\n".join(lines))


# ═══════════════════════════════════════════════════════════════════
# INDICATORS
# ═══════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════
# STRATEGY ENGINE — ALL 7 FIXED
# ═══════════════════════════════════════════════════════════════════

class RetailStrategyEngine:
    def __init__(self, config: BotConfig):
        self.config = config

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        c = self.config

        df['rsi'] = Indicators.rsi(df['close'], c.rsi_period)
        df['ema_fast'] = Indicators.ema(df['close'], c.ma_fast)
        df['ema_slow'] = Indicators.ema(df['close'], c.ma_slow)
        df['ema_trend'] = Indicators.ema(df['close'], c.ma_trend)
        df['atr'] = Indicators.atr(df, c.atr_period)
        df['macd_line'], df['macd_sig'], df['macd_hist'] = Indicators.macd(
            df['close'], c.macd_fast, c.macd_slow, c.macd_signal_period
        )
        df['bb_upper'], df['bb_mid'], df['bb_lower'] = Indicators.bollinger(
            df['close'], c.bb_period, c.bb_std
        )
        df['body'] = (df['close'] - df['open']).abs()
        df['range'] = df['high'] - df['low']
        return df

    def _base_signal(self, df: pd.DataFrame, symbol: str,
                     side: str, strategy: StrategyID, reason: str) -> Dict:
        """Build a standard signal dict from the completed candle."""
        curr = df.iloc[-2]
        entry = float(curr['close'])
        atr_val = float(curr['atr'])
        c = self.config
        risk = atr_val * c.atr_sl_multiplier

        if side == 'LONG':
            stop = entry - risk
            target = entry + risk * c.rr_ratio
        else:
            stop = entry + risk
            target = entry - risk * c.rr_ratio

        return {
            'symbol': symbol,
            'side': side,
            'entry': entry,
            'stop': stop,
            'target': target,
            'atr': atr_val,
            'rr': c.rr_ratio,
            'strategy': strategy.value,
            'reason': reason,
            'candle_close_time': df.index[-2],
            'strategies_triggered': [strategy.value],
            'confluence_score': 1,
        }

    # ──────────────────────────────────────────────────────────────
    # [F3] STRATEGY 1: RSI Counter-Trend — FIXED
    # Zone-based: RSI within 5 points of threshold, not exact cross
    # Also checks whether RSI was BELOW threshold recently (more signals)
    # ──────────────────────────────────────────────────────────────
    def strat_rsi_counter_trend(self, df: pd.DataFrame, symbol: str) -> Optional[Dict]:
        if len(df) < 50:
            return None

        curr = df.iloc[-2]
        prev = df.iloc[-3]

        if pd.isna(curr['rsi']) or pd.isna(curr['atr']) or curr['atr'] <= 0:
            return None

        c = self.config

        # LONG: RSI was below oversold and now recovering
        # [F3] Was: exact cross. Now: curr in zone AND prev was below threshold
        rsi_was_oversold = prev['rsi'] < c.rsi_oversold
        rsi_recovering = curr['rsi'] > prev['rsi']
        rsi_in_bounce_zone = c.rsi_oversold <= curr['rsi'] <= (c.rsi_oversold + c.rsi_zone_width)

        if rsi_was_oversold and rsi_recovering and rsi_in_bounce_zone:
            return self._base_signal(
                df, symbol, 'LONG',
                StrategyID.RSI_COUNTER_TREND,
                f"RSI bounce {prev['rsi']:.1f}→{curr['rsi']:.1f} "
                f"(oversold={c.rsi_oversold}, no trend filter)"
            )

        # SHORT: RSI was above overbought and now rejecting
        rsi_was_overbought = prev['rsi'] > c.rsi_overbought
        rsi_rejecting = curr['rsi'] < prev['rsi']
        rsi_in_reject_zone = (c.rsi_overbought - c.rsi_zone_width) <= curr['rsi'] <= c.rsi_overbought

        if rsi_was_overbought and rsi_rejecting and rsi_in_reject_zone:
            return self._base_signal(
                df, symbol, 'SHORT',
                StrategyID.RSI_COUNTER_TREND,
                f"RSI rejection {prev['rsi']:.1f}→{curr['rsi']:.1f} "
                f"(overbought={c.rsi_overbought}, no trend filter)"
            )

        return None

    # ──────────────────────────────────────────────────────────────
    # [F7] STRATEGY 2: MA Crossover — FIXED
    # Handles flat EMA, adds minimum separation check
    # ──────────────────────────────────────────────────────────────
    def strat_ma_crossover(self, df: pd.DataFrame, symbol: str) -> Optional[Dict]:
        if len(df) < 50:
            return None

        curr = df.iloc[-2]
        prev = df.iloc[-3]

        if pd.isna(curr['ema_fast']) or pd.isna(curr['ema_slow']) or curr['atr'] <= 0:
            return None

        # [F7] Minimum separation to avoid flat EMA false cross
        min_sep = curr['atr'] * 0.05  # Must differ by at least 5% of ATR

        curr_fast = float(curr['ema_fast'])
        curr_slow = float(curr['ema_slow'])
        prev_fast = float(prev['ema_fast'])
        prev_slow = float(prev['ema_slow'])

        # Golden cross
        crossed_up = prev_fast <= prev_slow and curr_fast > curr_slow
        separation_ok = abs(curr_fast - curr_slow) > min_sep

        if crossed_up and separation_ok:
            return self._base_signal(
                df, symbol, 'LONG',
                StrategyID.MA_CROSSOVER_WHIPSAW,
                f"EMA{self.config.ma_fast}/{self.config.ma_slow} golden cross "
                f"({prev_fast:.2f}→{curr_fast:.2f})"
            )

        # Death cross
        crossed_down = prev_fast >= prev_slow and curr_fast < curr_slow

        if crossed_down and separation_ok:
            return self._base_signal(
                df, symbol, 'SHORT',
                StrategyID.MA_CROSSOVER_WHIPSAW,
                f"EMA{self.config.ma_fast}/{self.config.ma_slow} death cross "
                f"({prev_fast:.2f}→{curr_fast:.2f})"
            )

        return None

    # ──────────────────────────────────────────────────────────────
    # [F1] [F11] STRATEGY 3: S/R Bounce — FIXED
    # Uses actual pivot-based S/R, adds trend context for direction
    # ──────────────────────────────────────────────────────────────
    def strat_sr_bounce(self, df: pd.DataFrame, symbol: str) -> Optional[Dict]:
        if len(df) < self.config.sr_lookback + 10:
            return None

        curr = df.iloc[-2]
        if pd.isna(curr['atr']) or curr['atr'] <= 0:
            return None

        supports, resistances = Indicators.find_sr_levels(
            df.iloc[:-1],
            self.config.sr_lookback,
            self.config.sr_touch_threshold,
            self.config.sr_min_touches
        )

        price = float(curr['close'])
        threshold = self.config.sr_touch_threshold

        # [F11] Use EMA trend to decide direction
        is_uptrend = curr['close'] > curr['ema_trend']

        # Buy at support — worse if in downtrend (retail buys dip in downtrend)
        for support in supports[:2]:
            dist = abs(price - support) / (support + 1e-10)
            if dist < threshold:
                direction = 'LONG'
                trend_note = "IN DOWNTREND (catching knife)" if not is_uptrend else "at support"
                return self._base_signal(
                    df, symbol, direction,
                    StrategyID.SR_BOUNCE_TRAP,
                    f"Bounce at support ${support:.4f} {trend_note}"
                )

        # Sell at resistance — worse if in uptrend
        for resistance in resistances[:2]:
            dist = abs(price - resistance) / (resistance + 1e-10)
            if dist < threshold:
                direction = 'SHORT'
                trend_note = "IN UPTREND (selling rip)" if is_uptrend else "at resistance"
                return self._base_signal(
                    df, symbol, direction,
                    StrategyID.SR_BOUNCE_TRAP,
                    f"Rejection at resistance ${resistance:.4f} {trend_note}"
                )

        return None

    # ──────────────────────────────────────────────────────────────
    # [F5] STRATEGY 4: MACD Divergence — FIXED
    # Robust swing detection using argrelextrema
    # ──────────────────────────────────────────────────────────────
    def strat_macd_divergence(self, df: pd.DataFrame, symbol: str) -> Optional[Dict]:
        from scipy.signal import argrelextrema

        if len(df) < self.config.divergence_lookback + 40:
            return None

        curr = df.iloc[-2]
        if pd.isna(curr['macd_hist']) or pd.isna(curr['atr']) or curr['atr'] <= 0:
            return None

        lb = self.config.divergence_lookback
        recent = df.iloc[-(lb + 2):-1].copy()

        if len(recent) < 20:
            return None

        prices_low = recent['low'].values
        prices_high = recent['high'].values
        hist = recent['macd_hist'].values

        # Find local minima (bullish divergence)
        try:
            min_idx = argrelextrema(prices_low, np.less, order=3)[0]
            if len(min_idx) >= 2:
                i1, i2 = min_idx[-2], min_idx[-1]
                if i2 - i1 >= 5:
                    # Price: lower low | MACD: higher low = bullish divergence
                    if prices_low[i2] < prices_low[i1] and hist[i2] > hist[i1]:
                        return self._base_signal(
                            df, symbol, 'LONG',
                            StrategyID.MACD_DIVERGENCE,
                            f"Bullish MACD div: price {prices_low[i1]:.4f}→{prices_low[i2]:.4f} "
                            f"hist {hist[i1]:.4f}→{hist[i2]:.4f}"
                        )

            # Find local maxima (bearish divergence)
            max_idx = argrelextrema(prices_high, np.greater, order=3)[0]
            if len(max_idx) >= 2:
                i1, i2 = max_idx[-2], max_idx[-1]
                if i2 - i1 >= 5:
                    # Price: higher high | MACD: lower high = bearish divergence
                    if prices_high[i2] > prices_high[i1] and hist[i2] < hist[i1]:
                        return self._base_signal(
                            df, symbol, 'SHORT',
                            StrategyID.MACD_DIVERGENCE,
                            f"Bearish MACD div: price {prices_high[i1]:.4f}→{prices_high[i2]:.4f} "
                            f"hist {hist[i1]:.4f}→{hist[i2]:.4f}"
                        )
        except Exception:
            return None

        return None

    # ──────────────────────────────────────────────────────────────
    # [F4] STRATEGY 5: Bollinger Band — FIXED
    # Uses proximity percentage not exact touch (more realistic)
    # ──────────────────────────────────────────────────────────────
    def strat_bband_reversion(self, df: pd.DataFrame, symbol: str) -> Optional[Dict]:
        if len(df) < self.config.bb_period + 10:
            return None

        curr = df.iloc[-2]
        prev = df.iloc[-3]

        if pd.isna(curr['bb_lower']) or pd.isna(curr['atr']) or curr['atr'] <= 0:
            return None

        price = float(curr['close'])
        lower = float(curr['bb_lower'])
        upper = float(curr['bb_upper'])
        prox = self.config.bb_proximity_pct

        # [F4] Within proximity % of lower band (not exact cross)
        near_lower = abs(price - lower) / (lower + 1e-10) < prox
        prev_price = float(prev['close'])
        was_near_or_below = prev_price <= lower * (1 + prox)

        if near_lower or (prev_price < lower and price > lower):
            return self._base_signal(
                df, symbol, 'LONG',
                StrategyID.BBAND_REVERT,
                f"Near lower BB ${lower:.4f} (price ${price:.4f}, "
                f"gap {abs(price - lower) / lower * 100:.2f}%)"
            )

        near_upper = abs(price - upper) / (upper + 1e-10) < prox

        if near_upper or (prev_price > upper and price < upper):
            return self._base_signal(
                df, symbol, 'SHORT',
                StrategyID.BBAND_REVERT,
                f"Near upper BB ${upper:.4f} (price ${price:.4f}, "
                f"gap {abs(price - upper) / upper * 100:.2f}%)"
            )

        return None

    # ──────────────────────────────────────────────────────────────
    # [F2] STRATEGY 6: Double Pattern — FIXED
    # Tighter tolerance, proper min separation, clearer direction
    # ──────────────────────────────────────────────────────────────
    def strat_double_pattern(self, df: pd.DataFrame, symbol: str) -> Optional[Dict]:
        from scipy.signal import argrelextrema

        if len(df) < self.config.pattern_lookback + 10:
            return None

        curr = df.iloc[-2]
        if pd.isna(curr['atr']) or curr['atr'] <= 0:
            return None

        lb = self.config.pattern_lookback
        recent = df.iloc[-(lb + 2):-1].copy()
        tol = self.config.pattern_tolerance
        min_sep = self.config.pattern_min_bars_apart

        highs = recent['high'].values
        lows = recent['low'].values

        try:
            # Double top: two highs at similar level
            peak_idx = argrelextrema(highs, np.greater, order=4)[0]
            if len(peak_idx) >= 2:
                p1, p2 = peak_idx[-2], peak_idx[-1]
                if p2 - p1 >= min_sep:
                    similarity = abs(highs[p1] - highs[p2]) / (highs[p1] + 1e-10)
                    if similarity < tol:
                        # Check price is below both peaks (early short entry)
                        pattern_high = max(highs[p1], highs[p2])
                        if float(curr['close']) < pattern_high * (1 - tol / 2):
                            return self._base_signal(
                                df, symbol, 'SHORT',
                                StrategyID.DOUBLE_PATTERN,
                                f"Double top at ${pattern_high:.4f} "
                                f"(sep={p2-p1} bars, sim={similarity*100:.2f}%)"
                            )

            # Double bottom: two lows at similar level
            trough_idx = argrelextrema(lows, np.less, order=4)[0]
            if len(trough_idx) >= 2:
                t1, t2 = trough_idx[-2], trough_idx[-1]
                if t2 - t1 >= min_sep:
                    similarity = abs(lows[t1] - lows[t2]) / (lows[t1] + 1e-10)
                    if similarity < tol:
                        pattern_low = min(lows[t1], lows[t2])
                        if float(curr['close']) > pattern_low * (1 + tol / 2):
                            return self._base_signal(
                                df, symbol, 'LONG',
                                StrategyID.DOUBLE_PATTERN,
                                f"Double bottom at ${pattern_low:.4f} "
                                f"(sep={t2-t1} bars, sim={similarity*100:.2f}%)"
                            )
        except Exception:
            return None

        return None

    # ──────────────────────────────────────────────────────────────
    # [F6] STRATEGY 7: Spike Fade — FIXED
    # Correct candle indexing: spike at [-3], confirm at [-2]
    # ──────────────────────────────────────────────────────────────
    def strat_spike_fade(self, df: pd.DataFrame, symbol: str) -> Optional[Dict]:
        if len(df) < 30:
            return None

        # [F6] Both must be COMPLETED candles
        confirm = df.iloc[-2]   # Most recent completed candle
        spike = df.iloc[-3]     # Candle before that

        if pd.isna(spike['atr']) or spike['atr'] <= 0:
            return None
        if pd.isna(confirm['atr']) or confirm['atr'] <= 0:
            return None

        threshold = spike['atr'] * self.config.spike_atr_multiplier

        # Bull spike: big green candle at [-3], fade with short
        bull_spike = (
            spike['body'] > threshold
            and spike['close'] > spike['open']  # Green candle
        )
        if bull_spike:
            # Confirm: next candle shows reversal attempt
            if confirm['close'] < confirm['open']:  # Red confirmation
                return self._base_signal(
                    df, symbol, 'SHORT',
                    StrategyID.SPIKE_FADE,
                    f"Fading bull spike: body={spike['body']:.4f} "
                    f"vs ATR={spike['atr']:.4f} ({spike['body']/spike['atr']:.1f}x)"
                )

        # Bear spike: big red candle at [-3], fade with long
        bear_spike = (
            spike['body'] > threshold
            and spike['close'] < spike['open']  # Red candle
        )
        if bear_spike:
            if confirm['close'] > confirm['open']:  # Green confirmation
                return self._base_signal(
                    df, symbol, 'LONG',
                    StrategyID.SPIKE_FADE,
                    f"Fading bear spike: body={spike['body']:.4f} "
                    f"vs ATR={spike['atr']:.4f} ({spike['body']/spike['atr']:.1f}x)"
                )

        return None

    # ──────────────────────────────────────────────────────────────
    # COMPOSITE SIGNAL BUILDER
    # ──────────────────────────────────────────────────────────────
    def run_all(self, df: pd.DataFrame, symbol: str) -> List[Dict]:
        """Run all 7 strategies, return all that fire."""
        runners = [
            self.strat_rsi_counter_trend,
            self.strat_ma_crossover,
            self.strat_sr_bounce,
            self.strat_macd_divergence,
            self.strat_bband_reversion,
            self.strat_double_pattern,
            self.strat_spike_fade,
        ]
        results = []
        for fn in runners:
            try:
                sig = fn(df, symbol)
                if sig is not None:
                    results.append(sig)
            except Exception as e:
                pass  # Silent per-strategy failures
        return results

    def build_composite(self, signals: List[Dict]) -> Optional[Dict]:
        """
        [F8] Fixed confluence count.
        Group by direction, majority wins.
        """
        if not signals:
            return None

        longs = [s for s in signals if s['side'] == 'LONG']
        shorts = [s for s in signals if s['side'] == 'SHORT']

        chosen = longs if len(longs) >= len(shorts) else shorts
        if not chosen:
            return None

        direction = chosen[0]['side']
        base = chosen[0].copy()

        # [F8] Correct confluence count = number of strategies agreeing
        base['confluence_score'] = len(chosen)      # FIXED
        base['strategies_triggered'] = [s['strategy'] for s in chosen]  # FIXED
        base['reason'] = " ⊕ ".join(
            set(s.get('reason', '') for s in chosen)
        )

        # Use average entry/stop across agreeing strategies
        avg_entry = np.mean([s['entry'] for s in chosen])
        if direction == 'LONG':
            # Tightest stop = closest to entry = most likely to be hit
            tightest_stop = max(s['stop'] for s in chosen)
            risk = avg_entry - tightest_stop
            base['entry'] = avg_entry
            base['stop'] = tightest_stop
            base['target'] = avg_entry + risk * self.config.rr_ratio
        else:
            tightest_stop = min(s['stop'] for s in chosen)
            risk = tightest_stop - avg_entry
            base['entry'] = avg_entry
            base['stop'] = tightest_stop
            base['target'] = avg_entry - risk * self.config.rr_ratio

        base['atr'] = np.mean([s['atr'] for s in chosen])
        return base


# ═══════════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════════

class DatabaseManager:
    def __init__(self, config: BotConfig, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.pool: Optional[pooling.MySQLConnectionPool] = None
        self._init_pool()

    def _init_pool(self):
        try:
            self.pool = pooling.MySQLConnectionPool(
                pool_name="rdt_bot_pool",
                pool_size=3,
                pool_reset_session=True,
                host=self.config.db_host,
                port=self.config.db_port,
                database=self.config.db_name,
                user=self.config.db_user,
                password=self.config.db_password,
            )
            self._create_tables()
            self.logger.info("✅ Database pool ready")
        except Exception as e:
            self.logger.error(f"❌ DB init error: {e}")
            self.pool = None

    def _create_tables(self):
        conn = self.get_conn()
        if not conn:
            return
        try:
            c = conn.cursor()
            c.execute("""
                CREATE TABLE IF NOT EXISTS rdt_trades (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    symbol VARCHAR(20) NOT NULL,
                    side VARCHAR(10) NOT NULL,
                    mode VARCHAR(10) NOT NULL DEFAULT 'shadow',
                    entry_price DECIMAL(20,8) NOT NULL,
                    stop_price DECIMAL(20,8) NOT NULL,
                    target_price DECIMAL(20,8) NOT NULL,
                    atr DECIMAL(20,8) NOT NULL,
                    rr DECIMAL(10,2) NOT NULL,
                    units DECIMAL(20,8) NOT NULL,
                    strategy VARCHAR(30) NOT NULL,
                    strategies_json TEXT NULL,
                    confluence_score INT DEFAULT 1,
                    reason TEXT NULL,
                    status VARCHAR(10) DEFAULT 'OPEN',
                    exit_price DECIMAL(20,8) NULL,
                    exit_reason VARCHAR(20) NULL,
                    net_pnl DECIMAL(20,8) NULL,
                    pnl_percent DECIMAL(10,4) NULL,
                    entry_time DATETIME,
                    exit_time DATETIME NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_sym_status (symbol, status),
                    INDEX idx_mode_status (mode, status),
                    INDEX idx_strategy (strategy)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS rdt_strategy_stats (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    strategy VARCHAR(30) NOT NULL,
                    mode VARCHAR(10) NOT NULL,
                    total_trades INT DEFAULT 0,
                    wins INT DEFAULT 0,
                    losses INT DEFAULT 0,
                    total_pnl DECIMAL(20,8) DEFAULT 0,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY uk_strat_mode (strategy, mode)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            conn.commit()
            c.close()
        except Exception as e:
            self.logger.error(f"❌ Table create error: {e}")
        finally:
            conn.close()

    def get_conn(self):
        if not self.pool:
            return None
        try:
            conn = self.pool.get_connection()
            conn.ping(reconnect=True)
            return conn
        except Exception as e:
            self.logger.error(f"❌ DB conn error: {e}")
            return None

    def save_trade(self, signal: Dict, units: float, mode: str) -> Optional[int]:
        conn = self.get_conn()
        if not conn:
            return None
        try:
            c = conn.cursor()
            c.execute("""
                INSERT INTO rdt_trades
                (symbol, side, mode, entry_price, stop_price, target_price,
                 atr, rr, units, strategy, strategies_json, confluence_score,
                 reason, entry_time)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                signal['symbol'], signal['side'], mode,
                signal['entry'], signal['stop'], signal['target'],
                signal['atr'], signal['rr'], units,
                signal.get('strategy', 'combined'),
                json.dumps(signal.get('strategies_triggered', [])),
                signal.get('confluence_score', 1),
                str(signal.get('reason', ''))[:500],
                signal.get('candle_close_time', datetime.utcnow()),
            ))
            tid = c.lastrowid
            conn.commit()
            c.close()
            return tid
        except Exception as e:
            self.logger.error(f"❌ save_trade: {e}")
            return None
        finally:
            conn.close()

    def close_trade(self, trade_id: int, exit_price: float,
                    reason: str, pnl: float, pnl_pct: float):
        conn = self.get_conn()
        if not conn:
            return
        try:
            c = conn.cursor()
            c.execute("""
                UPDATE rdt_trades SET status='CLOSED',
                exit_price=%s, exit_reason=%s, net_pnl=%s,
                pnl_percent=%s, exit_time=%s WHERE id=%s
            """, (exit_price, reason, pnl, pnl_pct, datetime.utcnow(), trade_id))
            conn.commit()
            c.close()
        except Exception as e:
            self.logger.error(f"❌ close_trade: {e}")
        finally:
            conn.close()

    def update_stats(self, strategy: str, mode: str,
                     won: bool, pnl: float):
        conn = self.get_conn()
        if not conn:
            return
        try:
            c = conn.cursor()
            c.execute("""
                INSERT INTO rdt_strategy_stats
                    (strategy, mode, total_trades, wins, losses, total_pnl)
                VALUES (%s, %s, 1, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    total_trades = total_trades + 1,
                    wins = wins + %s,
                    losses = losses + %s,
                    total_pnl = total_pnl + %s
            """, (
                strategy, mode,
                1 if won else 0, 0 if won else 1, pnl,
                1 if won else 0, 0 if won else 1, pnl,
            ))
            conn.commit()
            c.close()
        except Exception as e:
            self.logger.error(f"❌ update_stats: {e}")
        finally:
            conn.close()

    def load_open_trades(self) -> List[Dict]:
        conn = self.get_conn()
        if not conn:
            return []
        try:
            c = conn.cursor(dictionary=True)
            c.execute("SELECT * FROM rdt_trades WHERE status='OPEN'")
            rows = c.fetchall()
            c.close()
            for r in rows:
                for k, v in r.items():
                    if isinstance(v, Decimal):
                        r[k] = float(v)
            return rows
        except Exception as e:
            self.logger.error(f"❌ load_open_trades: {e}")
            return []
        finally:
            conn.close()

    def count_open(self, mode: str = None, symbol: str = None) -> int:
        conn = self.get_conn()
        if not conn:
            return 0
        try:
            c = conn.cursor()
            sql = "SELECT COUNT(*) FROM rdt_trades WHERE status='OPEN'"
            params = []
            if mode:
                sql += " AND mode=%s"
                params.append(mode)
            if symbol:
                sql += " AND symbol=%s"
                params.append(symbol)
            c.execute(sql, params)
            count = c.fetchone()[0]
            c.close()
            return count
        except Exception as e:
            self.logger.error(f"❌ count_open: {e}")
            return 0
        finally:
            conn.close()

    def get_stats(self) -> Dict:
        conn = self.get_conn()
        if not conn:
            return {}
        try:
            c = conn.cursor(dictionary=True)
            c.execute("SELECT * FROM rdt_strategy_stats ORDER BY strategy, mode")
            rows = c.fetchall()
            c.close()
            result = {}
            for r in rows:
                key = f"{r['strategy']}_{r['mode']}"
                result[key] = {
                    'total': r['total_trades'],
                    'wins': r['wins'],
                    'losses': r['losses'],
                    'net_pnl': float(r['total_pnl'])
                    if isinstance(r['total_pnl'], Decimal)
                    else float(r.get('total_pnl', 0)),
                }
            return result
        except Exception as e:
            self.logger.error(f"❌ get_stats: {e}")
            return {}
        finally:
            conn.close()

    def get_realized_pnl_by_mode(self) -> Dict[str, float]:
        """Sum of net_pnl for CLOSED trades, grouped by mode. Used to
        rebuild equity_shadow/equity_inverse on restart -- without this,
        _load_state() resets both back to initial_capital on every
        restart, silently discarding all realized gains/losses."""
        conn = self.get_conn()
        if not conn:
            return {}
        try:
            c = conn.cursor(dictionary=True)
            c.execute("""
                SELECT mode, COALESCE(SUM(net_pnl), 0) AS total_pnl
                FROM rdt_trades WHERE status='CLOSED' GROUP BY mode
            """)
            rows = c.fetchall()
            c.close()
            return {r['mode']: float(r['total_pnl']) for r in rows}
        except Exception as e:
            self.logger.error(f"❌ get_realized_pnl_by_mode: {e}")
            return {}
        finally:
            conn.close()

    def get_last_trade_times(self) -> Dict[str, Dict[str, datetime]]:
        """Most recent entry_time per (symbol, mode) across ALL trades
        (open + closed). Used to rebuild the per-symbol cooldown on
        restart -- without this, _load_state() resets every symbol's
        cooldown to datetime.min, so a restart can immediately bypass
        min_trade_interval_hours and re-enter a symbol with no cooldown."""
        conn = self.get_conn()
        if not conn:
            return {}
        try:
            c = conn.cursor(dictionary=True)
            c.execute("""
                SELECT symbol, mode, MAX(entry_time) AS last_entry
                FROM rdt_trades GROUP BY symbol, mode
            """)
            rows = c.fetchall()
            c.close()
            result: Dict[str, Dict[str, datetime]] = {}
            for r in rows:
                result.setdefault(r['symbol'], {})[r['mode']] = r['last_entry']
            return result
        except Exception as e:
            self.logger.error(f"❌ get_last_trade_times: {e}")
            return {}
        finally:
            conn.close()


# ═══════════════════════════════════════════════════════════════════
# DATA FETCHER
# ═══════════════════════════════════════════════════════════════════

class DataFetcher:
    def __init__(self, config: BotConfig):
        self.config = config
        self.exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'},
            'timeout': 30000,
        })

    def validate_symbols(self) -> List[str]:
        try:
            markets = self.exchange.load_markets()
            valid = [s for s in self.config.symbols if s in markets]
            bad = set(self.config.symbols) - set(valid)
            if bad:
                print(f"⚠️  Removed invalid: {bad}")
            return valid
        except Exception as e:
            print(f"❌ Market load error: {e}")
            return self.config.symbols

    def fetch(self, symbol: str, limit: int = 500) -> Optional[pd.DataFrame]:
        c = self.config
        for attempt in range(c.api_retries):
            try:
                ohlcv = self.exchange.fetch_ohlcv(
                    symbol, c.primary_timeframe, limit=limit
                )
                if not ohlcv or len(ohlcv) < 10:
                    return None
                df = pd.DataFrame(
                    ohlcv,
                    columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
                )
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                df.set_index('timestamp', inplace=True)
                return df
            except ccxt.RateLimitExceeded:
                wait = c.api_retry_delay * (2 ** attempt) + random.uniform(0, 1)
                print(f"⏳ Rate limit {symbol}, wait {wait:.1f}s")
                time.sleep(wait)
            except ccxt.ExchangeError as e:
                print(f"❌ Exchange error {symbol}: {e}")
                return None
            except ccxt.NetworkError as e:
                print(f"🌐 Network error {symbol}: {e}")
                if attempt < c.api_retries - 1:
                    time.sleep(c.api_retry_delay * (attempt + 1))
            except Exception as e:
                print(f"❌ Fetch error {symbol}: {e}")
                if attempt < c.api_retries - 1:
                    time.sleep(c.api_retry_delay)
        return None

    def get_price(self, symbol: str) -> Optional[float]:
        for _ in range(2):
            try:
                t = self.exchange.fetch_ticker(symbol)
                p = float(t.get('last', 0))
                return p if p > 0 else None
            except ccxt.RateLimitExceeded:
                time.sleep(1.5)
            except Exception:
                return None
        return None


# ═══════════════════════════════════════════════════════════════════
# MAIN BOT
# ═══════════════════════════════════════════════════════════════════

class RetailDeathTrapBot:
    def __init__(self, config: BotConfig):
        # Logger
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s │ %(levelname)-7s │ %(message)s',
            handlers=[
                logging.StreamHandler(),
                RotatingFileHandler(
                    'rdt_bot.log',
                    maxBytes=10 * 1024 * 1024,
                    backupCount=5
                )
            ]
        )
        self.logger = logging.getLogger("RDT_Bot")
        self.config = config
        self.mode = SignalMode(config.signal_mode)

        # Components
        self.fetcher = DataFetcher(config)
        self.engine = RetailStrategyEngine(config)
        self.db = DatabaseManager(config, self.logger)
        self.tg = TelegramAlert(config.telegram_token, config.telegram_chat_id)

        # Validate symbols
        self.symbols = self.fetcher.validate_symbols()

        # State — separate per mode
        # [F10] Track open trades PER MODE separately
        self.active_shadow: Dict[int, Dict] = {}
        self.active_inverse: Dict[int, Dict] = {}

        # Equities
        self.equity_shadow = config.initial_capital
        self.equity_inverse = config.initial_capital

        # Timing
        self.last_scan: Dict[str, float] = {s: 0.0 for s in self.symbols}
        self.last_trade_time: Dict[str, Dict[str, datetime]] = {
            s: {'shadow': datetime.min, 'inverse': datetime.min}
            for s in self.symbols
        }

        self._load_state()
        self._startup_msg()

    def _load_state(self):
        rows = self.db.load_open_trades()
        for t in rows:
            record = {
                'id': t['id'],
                'symbol': t['symbol'],
                'side': t['side'],
                'mode': t.get('mode', 'shadow'),
                'entry': t['entry_price'],
                'stop': t['stop_price'],
                'target': t['target_price'],
                'units': t['units'],
                'strategy': t.get('strategy', 'unknown'),
            }
            if record['mode'] == 'inverse':
                self.active_inverse[t['id']] = record
            else:
                self.active_shadow[t['id']] = record
            self.logger.info(
                f"📂 Loaded #{t['id']} {t['symbol']} "
                f"{t['side']} [{record['mode']}]"
            )

        # Rebuild equity from realized P&L -- without this, a restart
        # silently resets both equities back to initial_capital, discarding
        # every closed trade's gain/loss (see get_realized_pnl_by_mode()).
        realized = self.db.get_realized_pnl_by_mode()
        if realized:
            self.equity_shadow = self.config.initial_capital + realized.get('shadow', 0.0)
            self.equity_inverse = self.config.initial_capital + realized.get('inverse', 0.0)
            self.logger.info(
                f"💰 Restored equity from DB: shadow=${self.equity_shadow:,.2f} "
                f"inverse=${self.equity_inverse:,.2f}"
            )

        # Rebuild per-symbol cooldown -- without this, a restart resets
        # last_trade_time to datetime.min for every symbol, bypassing
        # min_trade_interval_hours immediately after restart (see
        # get_last_trade_times()).
        last_times = self.db.get_last_trade_times()
        restored = 0
        for symbol, by_mode in last_times.items():
            if symbol not in self.last_trade_time:
                continue
            for mode, last_entry in by_mode.items():
                if last_entry is not None:
                    self.last_trade_time[symbol][mode] = last_entry
                    restored += 1
        if restored:
            self.logger.info(f"⏱️  Restored cooldown state for {restored} symbol/mode pairs")

    def _startup_msg(self):
        msg = (
            f"🤖 <b>RETAIL DEATH TRAP BOT v2.1</b>\n"
            f"✅ All 7 Strategies Fixed\n"
            f"✅ Mixed Long/Short Signals\n"
            f"✅ Separate Shadow/Inverse Slots\n\n"
            f"📊 Mode: <b>{self.mode.value.upper()}</b>\n"
            f"📈 Symbols: {len(self.symbols)}\n"
            f"💰 Shadow Capital:  ${self.config.initial_capital:,.2f}\n"
            f"💰 Inverse Capital: ${self.config.initial_capital:,.2f}\n"
            f"🎯 Max trades/mode: {self.config.max_open_trades_per_mode}"
        )
        self.tg.send(msg)
        self.logger.info("=" * 60)
        self.logger.info("RETAIL DEATH TRAP BOT v2.1 — STARTED")
        self.logger.info(f"Symbols: {self.symbols}")
        self.logger.info("=" * 60)

    # ──────────────────────────────────────────────────────────────
    # TRADE CONTROL
    # ──────────────────────────────────────────────────────────────

    def _can_open(self, symbol: str, mode: str) -> Tuple[bool, str]:
        """[F10] Check per-mode limits separately."""
        active = self.active_inverse if mode == 'inverse' else self.active_shadow

        # Mode-level cap
        if len(active) >= self.config.max_open_trades_per_mode:
            return False, f"Max {mode} trades ({self.config.max_open_trades_per_mode}) reached"

        # Per-symbol cap (across both modes for same symbol)
        sym_count = sum(
            1 for t in active.values() if t['symbol'] == symbol
        )
        if sym_count >= self.config.max_trades_per_symbol:
            return False, f"{symbol} already has {mode} trade open"

        # Cooldown
        last = self.last_trade_time.get(symbol, {}).get(mode, datetime.min)
        elapsed = (datetime.utcnow() - last).total_seconds()
        cooldown = self.config.min_trade_interval_hours * 3600
        if elapsed < cooldown:
            remaining = (cooldown - elapsed) / 60
            return False, f"{symbol} {mode} cooldown: {remaining:.0f}m left"

        # DB check
        db_count = self.db.count_open(mode=mode, symbol=symbol)
        if db_count >= self.config.max_trades_per_symbol:
            return False, f"{symbol} {mode} already open in DB"

        return True, "OK"

    def _open_trade(self, signal: Dict, mode: str) -> Optional[int]:
        can, reason = self._can_open(signal['symbol'], mode)
        if not can:
            self.logger.debug(f"⏸️  [{mode}] Skip {signal['symbol']}: {reason}")
            return None

        equity = self.equity_inverse if mode == 'inverse' else self.equity_shadow
        risk_amt = equity * self.config.risk_per_trade
        risk_pu = abs(signal['entry'] - signal['stop'])
        if risk_pu <= 0:
            return None
        units = risk_amt / risk_pu
        if units <= 0:
            return None

        tid = self.db.save_trade(signal, units, mode)
        if tid is None:
            return None

        record = {
            'id': tid,
            'symbol': signal['symbol'],
            'side': signal['side'],
            'mode': mode,
            'entry': signal['entry'],
            'stop': signal['stop'],
            'target': signal['target'],
            'units': units,
            'strategy': signal.get('strategy', 'combined'),
            'strategies_triggered': signal.get('strategies_triggered', []),
        }

        if mode == 'inverse':
            self.active_inverse[tid] = record
        else:
            self.active_shadow[tid] = record

        # Update cooldown
        if signal['symbol'] not in self.last_trade_time:
            self.last_trade_time[signal['symbol']] = {'shadow': datetime.min, 'inverse': datetime.min}
        self.last_trade_time[signal['symbol']][mode] = datetime.utcnow()

        risk_pct = self.config.risk_per_trade * 100
        self.tg.send_entry(signal, tid, units, risk_amt, risk_pct, mode)
        self.logger.info(
            f"✅ [{mode.upper()}] #{tid} {signal['symbol']} {signal['side']} "
            f"| Entry: {signal['entry']:.4f} "
            f"| Stop: {signal['stop']:.4f} "
            f"| Confluence: {signal.get('confluence_score', 1)}/7 "
            f"| Strategies: {signal.get('strategies_triggered', [])}"
        )
        return tid

    def _close_trade(self, tid: int, exit_price: float,
                     reason: str, mode: str):
        active = self.active_inverse if mode == 'inverse' else self.active_shadow
        trade = active.get(tid)
        if not trade:
            return

        units = trade['units']
        if trade['side'] == 'LONG':
            pnl = (exit_price - trade['entry']) * units
        else:
            pnl = (trade['entry'] - exit_price) * units

        base_value = trade['entry'] * units
        pnl_pct = (pnl / base_value * 100) if base_value > 0 else 0.0

        # Update equity
        if mode == 'inverse':
            self.equity_inverse += pnl
        else:
            self.equity_shadow += pnl

        self.db.close_trade(tid, exit_price, reason, pnl, pnl_pct)
        self.db.update_stats(trade['strategy'], mode, pnl > 0, pnl)

        del active[tid]

        self.tg.send_exit(trade, exit_price, reason, pnl, pnl_pct)
        result_icon = "✅" if pnl > 0 else "❌"
        self.logger.info(
            f"🛑 [{mode.upper()}] #{tid} {trade['symbol']} closed: "
            f"{reason} {result_icon} | "
            f"P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%)"
        )

    # ──────────────────────────────────────────────────────────────
    # EXIT MONITOR
    # ──────────────────────────────────────────────────────────────

    def check_exits(self):
        all_active = {**self.active_shadow, **self.active_inverse}
        if not all_active:
            return

        # Batch fetch prices
        symbols_needed = list(set(t['symbol'] for t in all_active.values()))
        prices: Dict[str, float] = {}
        for sym in symbols_needed:
            p = self.fetcher.get_price(sym)
            if p:
                prices[sym] = p
            time.sleep(0.08)

        # Check shadow trades
        for tid, trade in list(self.active_shadow.items()):
            price = prices.get(trade['symbol'])
            if not price:
                continue
            exit_p, reason = self._check_exit_condition(trade, price)
            if reason:
                self._close_trade(tid, exit_p, reason, 'shadow')

        # Check inverse trades
        for tid, trade in list(self.active_inverse.items()):
            price = prices.get(trade['symbol'])
            if not price:
                continue
            exit_p, reason = self._check_exit_condition(trade, price)
            if reason:
                self._close_trade(tid, exit_p, reason, 'inverse')

    def _check_exit_condition(self, trade: Dict,
                               price: float) -> Tuple[float, str]:
        if trade['side'] == 'LONG':
            if price <= trade['stop']:
                return trade['stop'], 'STOP_LOSS'
            if price >= trade['target']:
                return trade['target'], 'TAKE_PROFIT'
        else:
            if price >= trade['stop']:
                return trade['stop'], 'STOP_LOSS'
            if price <= trade['target']:
                return trade['target'], 'TAKE_PROFIT'
        return price, ''

    # ──────────────────────────────────────────────────────────────
    # SIGNAL SCANNING
    # ──────────────────────────────────────────────────────────────

    def scan(self):
        for symbol in self.symbols:
            try:
                # Quick candle check
                df_check = self.fetcher.fetch(symbol, limit=15)
                if df_check is None or len(df_check) < 3:
                    continue

                last_candle_ts = df_check.index[-2].timestamp()
                if last_candle_ts <= self.last_scan.get(symbol, 0):
                    continue

                self.last_scan[symbol] = last_candle_ts
                self.logger.info(
                    f"🕯️  New candle {symbol} @ "
                    f"{df_check.index[-2].strftime('%H:%M')}"
                )

                # Full fetch + indicators
                df = self.fetcher.fetch(symbol, limit=500)
                if df is None or len(df) < 100:
                    continue

                df = self.engine.calculate_indicators(df)

                # Run all 7 strategies
                raw = self.engine.run_all(df, symbol)

                if not raw:
                    continue

                fired = [s['strategy'] for s in raw]
                directions = [s['side'] for s in raw]
                self.logger.info(
                    f"📡 {symbol}: {len(raw)} signals → "
                    f"{list(zip(fired, directions))}"
                )

                # Build composite
                composite = self.engine.build_composite(raw)
                if not composite:
                    continue

                # Open based on mode
                if self.mode in (SignalMode.SHADOW, SignalMode.BOTH):
                    self._open_trade(composite, 'shadow')

                if self.mode in (SignalMode.INVERSE, SignalMode.BOTH):
                    inv = self._invert(composite)
                    self._open_trade(inv, 'inverse')

                time.sleep(0.3)

            except Exception as e:
                self.logger.error(
                    f"❌ Scan error {symbol}: {e}\n"
                    f"{traceback.format_exc()}"
                )

    @staticmethod
    def _invert(signal: Dict) -> Dict:
        """Flip direction, recompute stop/target."""
        inv = signal.copy()
        risk = abs(signal['entry'] - signal['stop'])
        if signal['side'] == 'LONG':
            inv['side'] = 'SHORT'
            inv['stop'] = signal['entry'] + risk
            inv['target'] = signal['entry'] - risk * signal['rr']
        else:
            inv['side'] = 'LONG'
            inv['stop'] = signal['entry'] - risk
            inv['target'] = signal['entry'] + risk * signal['rr']
        inv['strategy'] = signal.get('strategy', '') + '_INV'
        inv['strategies_triggered'] = [
            s + '_INV' for s in signal.get('strategies_triggered', [])
        ]
        inv['reason'] = f"INVERSE: {signal.get('reason', '')}"
        return inv

    # ──────────────────────────────────────────────────────────────
    # MAIN LOOP
    # ──────────────────────────────────────────────────────────────

    def run(self):
        self.logger.info("🚀 Bot running — CTRL+C to stop")
        loop_count = 0
        stats_every = 720  # Every hour at 5s interval

        while True:
            try:
                loop_count += 1

                self.check_exits()
                self.scan()

                # Heartbeat every 60s
                hb_loops = self.config.heartbeat_interval // self.config.exit_check_interval
                if loop_count % max(hb_loops, 1) == 0:
                    s_open = len(self.active_shadow)
                    i_open = len(self.active_inverse)
                    self.logger.info(
                        f"💓 Shadow: {s_open} open ${self.equity_shadow:,.2f} | "
                        f"Inverse: {i_open} open ${self.equity_inverse:,.2f}"
                    )

                # Stats report every hour
                if loop_count % stats_every == 0:
                    stats = self.db.get_stats()
                    self.tg.send_stats(
                        stats, self.equity_shadow, self.equity_inverse
                    )

                time.sleep(self.config.exit_check_interval)

            except KeyboardInterrupt:
                self.logger.info("🛑 Stopped by user")
                self.tg.send("🛑 <b>Bot stopped by user</b>")
                break
            except Exception as e:
                self.logger.error(f"🚨 Loop error: {e}\n{traceback.format_exc()}")
                self.tg.send(f"🚨 Error: {str(e)[:150]}")
                time.sleep(30)


# ═══════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("RETAIL DEATH TRAP BOT v2.1")
    print("Fixes: Mixed signals | All 7 strategies | Inverse slots")
    print("=" * 60)

    config = BotConfig(
        symbols=[
            "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT",
            "XRP/USDT", "DOGE/USDT", "ADA/USDT", "AVAX/USDT",
        ],
        signal_mode="both",
        primary_timeframe="15m",
        # RSI wider zones = more triggers
        rsi_oversold=35,
        rsi_overbought=65,
        rsi_zone_width=5,
        # Risk
        atr_sl_multiplier=1.5,
        rr_ratio=2.0,
        risk_per_trade=0.01,
        initial_capital=10000.0,
        # [F10] Separate limits
        max_open_trades_per_mode=8,
        max_trades_per_symbol=1,
        min_trade_interval_hours=2,
        # ── Infrastructure ──
        db_host=os.getenv('RETAILBOT2_DB_HOST', os.getenv('DB_HOST', os.getenv('MYSQL_HOST', '192.168.1.30'))),
        db_user=os.getenv('RETAILBOT2_DB_USER', os.getenv('DB_USER', os.getenv('MYSQL_USER', 'cms'))),
        db_password=os.getenv('RETAILBOT2_DB_PASSWORD', os.getenv('DB_PASS', os.getenv('MYSQL_PASSWORD', ''))),
        db_name=os.getenv('RETAILBOT2_DB_NAME', os.getenv('DB_NAME', 'Binance')),

        # ── Telegram ──
        telegram_token=os.getenv('TELEGRAM_TOKEN', os.getenv('TELEGRAM_BOT_TOKEN', '')),
        telegram_chat_id=os.getenv('TELEGRAM_CHAT_ID', ''),
    )


    bot = RetailDeathTrapBot(config)
    bot.run()


if __name__ == "__main__":
    main()