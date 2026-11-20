#!/usr/bin/env python3
"""Smart Money Volume Detector - Early Warning System."""

import os
import json
from html import escape
import time
import signal
import logging
import threading
import heapq
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from collections import defaultdict, deque
from typing import Optional, Dict, List, Tuple, Deque
from dataclasses import dataclass, field

import websocket
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv

load_dotenv()
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from Screen import momentum
from db.db import get_pool, update_screener_heartbeat

CONFIG = {
    "enabled": True,
    "timeframe": os.getenv("TIMEFRAME", "1m"),
    "rvol_multiplier": float(os.getenv("RVOL_MULTIPLIER", "7.0")),
    "ema_length": int(os.getenv("EMA_LENGTH", "7200")),
    "velocity_window": int(os.getenv("VELOCITY_WINDOW", "100")),
    "enable_divergence_detection": os.getenv("ENABLE_DIVERGENCE", "true").lower() == "true",
    "divergence_max_price_change": float(os.getenv("DIVERGENCE_MAX_PRICE_CHANGE", "0.3")),
    "enable_velocity_detection": os.getenv("ENABLE_VELOCITY", "true").lower() == "true",
    "velocity_threshold": float(os.getenv("VELOCITY_THRESHOLD", "5")),
    "symbols_filter": os.getenv("SYMBOLS_FILTER", "USDT"),
    "min_quote_volume_24h": float(os.getenv("MIN_QUOTE_VOLUME_24H", "1000000")),
    "min_market_cap_usd": float(os.getenv("MIN_MARKET_CAP_USD", "50000000")),
    "market_cap_pages": int(os.getenv("MARKET_CAP_PAGES", "5")),
    "market_cap_refresh_minutes": int(os.getenv("MARKET_CAP_REFRESH_MINUTES", "30")),
    "exclude_leveraged": os.getenv("EXCLUDE_LEVERAGED", "true").lower() == "true",
    "exclude_stablecoins": os.getenv("EXCLUDE_STABLECOINS", "true").lower() == "true",
    "max_alerts_per_hour": int(os.getenv("MAX_ALERTS_PER_HOUR", "5")),
    "max_alerts_per_symbol_per_hour": int(os.getenv("MAX_ALERTS_PER_SYMBOL_PER_HOUR", "2")),
    "alert_cooldown_sec": int(os.getenv("ALERT_COOLDOWN_SEC", "900")),
    "require_confluence": os.getenv("REQUIRE_CONFLUENCE", "true").lower() == "true",
    "min_signal_rvol": float(os.getenv("MIN_SIGNAL_RVOL", "5.0")),
    "min_signal_velocity": float(os.getenv("MIN_SIGNAL_VELOCITY", "3.0")),
    "min_quality_score": float(os.getenv("MIN_QUALITY_SCORE", "70")),
    "htf_timeframes": tuple(tf.strip() for tf in os.getenv("HTF_TIMEFRAMES", "5m,15m").split(",") if tf.strip()),
    "htf_ema_fast": int(os.getenv("HTF_EMA_FAST", "9")),
    "htf_ema_slow": int(os.getenv("HTF_EMA_SLOW", "21")),
    "htf_min_bars": int(os.getenv("HTF_MIN_BARS", "25")),
    "htf_timeout_sec": float(os.getenv("HTF_TIMEOUT_SEC", "3")),
    "max_quality_without_htf": float(os.getenv("MAX_QUALITY_WITHOUT_HTF", "79")),
    "use_taker_buy_confirmation": os.getenv("USE_TAKER_BUY_CONFIRMATION", "true").lower() == "true",
    "alert_selection_window_sec": float(os.getenv("ALERT_SELECTION_WINDOW_SEC", "3")),
    "max_pending_signals": int(os.getenv("MAX_PENDING_SIGNALS", "200")),
    "min_alert_quote_volume": float(os.getenv("MIN_ALERT_QUOTE_VOLUME", "0")),
    "min_alert_quality_score": float(os.getenv("MIN_ALERT_QUALITY_SCORE", "80")),
    "max_signal_age_sec": int(os.getenv("MAX_SIGNAL_AGE_SEC", "900")),
    "expired_signal_retention_days": max(1, int(os.getenv("EXPIRED_SIGNAL_RETENTION_DAYS", "30"))),
    "expired_signal_cleanup_interval_days": max(1, int(os.getenv("EXPIRED_SIGNAL_CLEANUP_INTERVAL_DAYS", "7"))),
    "max_delivery_attempts": int(os.getenv("MAX_DELIVERY_ATTEMPTS", "5")),
    "delivery_poll_interval_sec": float(os.getenv("DELIVERY_POLL_INTERVAL_SEC", "1.0")),
    "daily_reset_utc_hour": int(os.getenv("DAILY_RESET_UTC_HOUR", "0")),
    "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN"),
    "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID"),
    "ws_ping_interval": 20,
    "ws_ping_timeout": 10,
    "max_streams_per_conn": int(os.getenv("MAX_STREAMS_PER_CONN", "100")),
    "reconnect_delay_base": 5,
    "reconnect_delay_max": 60,
    "log_level": os.getenv("LOG_LEVEL", "INFO"),
    "log_file": os.getenv("LOG_FILE", "smart_money_detector.log"),
}

# Immutable baseline loaded from environment at process startup. Dashboard overrides
# are intentionally reapplied from this baseline on every control-loop tick so that
# removing an override really restores the configured environment default.
DEFAULT_CONFIG = CONFIG.copy()

logging.basicConfig(level=getattr(logging, CONFIG["log_level"].upper()), format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S", handlers=[logging.FileHandler(CONFIG["log_file"], encoding="utf-8"), logging.StreamHandler()])

logger = logging.getLogger("SmartMoneyDetector")

@dataclass(order=True)
class SmartMoneySignal:
    priority_score: float
    symbol: str = field(compare=False)
    signal_type: str = field(compare=False)
    rvol: float = field(compare=False)
    volume: float = field(compare=False)
    quote_volume: float = field(compare=False)
    price: float = field(compare=False)
    price_change_pct: float = field(compare=False)
    volume_velocity: float = field(compare=False)
    timestamp: float = field(compare=False)
    candle_time: str = field(compare=False)
    quality_score: float = field(compare=False)
    direction: str = field(compare=False, default="NEUTRAL")
    taker_buy_ratio: Optional[float] = field(compare=False, default=None)
    htf_alignment: int = field(compare=False, default=0)
    htf_context: str = field(compare=False, default="UNAVAILABLE")

    watch_context: Optional[dict] = field(compare=False, default=None)
    parent_watch_id: Optional[int] = field(compare=False, default=None)

class SmartMoneyState:
    def __init__(self, ema_length: int, rvol_threshold: float, max_alerts_per_hour: int):
        self.lock = threading.RLock()
        self.ema_volume: Dict[str, float] = {}
        self.ema_quote_volume: Dict[str, float] = {}
        self.ema_initialized: Dict[str, int] = defaultdict(int)
        self.last_alert_time: Dict[str, float] = {}
        self.seen_signal_candles: Dict[Tuple[str, str], str] = {}
        self.hourly_alert_count: Dict[str, int] = defaultdict(int)
        self.last_hour_reset = None
        self.volume_history: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=CONFIG["velocity_window"] + 1))
        self.quote_volume_history: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=CONFIG["velocity_window"] + 1))
        self.alpha = 2 / (ema_length + 1)
        self.rvol_threshold = rvol_threshold
        self.warmup_candles = max(10, ema_length // 3)
        self.max_alerts_per_hour = max_alerts_per_hour
        self.shutdown_flag = False
        self.pending_signals: List[SmartMoneySignal] = []
        self.pending_keys: set = set()
        self.htf_cache: Dict[Tuple[str, str, int], Tuple[int, str]] = {}
        self._check_hourly_reset()

    def _check_hourly_reset(self):
        now = datetime.now(timezone.utc)
        hour_key = now.strftime("%Y-%m-%d %H")
        with self.lock:
            if self.last_hour_reset != hour_key:
                self.hourly_alert_count.clear()
                self.last_hour_reset = hour_key
                logger.info("🔄 Hourly reset: cleared alert counters for %s", hour_key)

    def update_ema(self, symbol, volume, quote_volume):
        with self.lock:
            if symbol not in self.ema_volume:
                self.ema_volume[symbol] = volume; self.ema_quote_volume[symbol] = quote_volume; self.ema_initialized[symbol] = 1
                return False, volume, quote_volume
            if self.ema_initialized[symbol] < self.warmup_candles:
                self.ema_volume[symbol] = self.alpha * volume + (1 - self.alpha) * self.ema_volume[symbol]
                self.ema_quote_volume[symbol] = self.alpha * quote_volume + (1 - self.alpha) * self.ema_quote_volume[symbol]
                self.ema_initialized[symbol] += 1
                return False, self.ema_volume[symbol], self.ema_quote_volume[symbol]
            old_ema_vol = self.ema_volume[symbol]; old_ema_quote = self.ema_quote_volume[symbol]
            self.ema_volume[symbol] = self.alpha * volume + (1 - self.alpha) * old_ema_vol
            self.ema_quote_volume[symbol] = self.alpha * quote_volume + (1 - self.alpha) * old_ema_quote
            return True, self.ema_volume[symbol], self.ema_quote_volume[symbol]

    def update_volume_history(self, symbol, volume, quote_volume):
        with self.lock:
            self.volume_history[symbol].append(volume); self.quote_volume_history[symbol].append(quote_volume)

    def calculate_velocity(self, symbol, current_quote_vol):
        with self.lock:
            history = self.quote_volume_history[symbol]
            if len(history) < 2: return 1.0
            prior_avg = sum(list(history)[:-1]) / (len(history) - 1)
            return current_quote_vol / prior_avg if prior_avg > 0 else 1.0

    @staticmethod
    def _ema(values: List[float], span: int) -> float:
        if not values:
            return 0.0
        alpha = 2.0 / (span + 1.0)
        ema = values[0]
        for value in values[1:]:
            ema = alpha * value + (1.0 - alpha) * ema
        return ema

    @staticmethod
    def _direction(price_change_pct: float) -> str:
        if price_change_pct >= 0.10:
            return "BULLISH"
        if price_change_pct <= -0.10:
            return "BEARISH"
        return "NEUTRAL"

    def _fetch_htf_context(self, symbol: str, signal_open_ms: int, direction: str) -> Tuple[int, str]:
        """Use only HTF candles that were already closed at the 1m signal."""
        trends = []
        for timeframe in CONFIG.get("htf_timeframes", ("5m", "15m")):
            if timeframe not in {"5m", "15m"}:
                continue
            interval_ms = 5 * 60_000 if timeframe == "5m" else 15 * 60_000
            cache_key = (symbol, timeframe, signal_open_ms // interval_ms)
            with self.lock:
                cached = self.htf_cache.get(cache_key)
            if cached is not None:
                trends.append(cached[1])
                continue
            try:
                response = requests.get(
                    "https://api.binance.com/api/v3/klines",
                    params={
                        "symbol": symbol,
                        "interval": timeframe,
                        "limit": max(CONFIG["htf_min_bars"], CONFIG["htf_ema_slow"] + 4),
                        "endTime": signal_open_ms - 1,
                    },
                    timeout=CONFIG["htf_timeout_sec"],
                )
                response.raise_for_status()
                rows = response.json()
                closes = [float(row[4]) for row in rows if len(row) >= 5]
                if len(closes) < CONFIG["htf_ema_slow"]:
                    continue
                close = closes[-1]
                fast = self._ema(closes, CONFIG["htf_ema_fast"])
                slow = self._ema(closes, CONFIG["htf_ema_slow"])
                trend = (
                    "BULLISH" if close > fast > slow
                    else "BEARISH" if close < fast < slow
                    else "NEUTRAL"
                )
                context = f"{timeframe}:{trend}"
                with self.lock:
                    self.htf_cache[cache_key] = (1 if trend == direction else -1 if direction != "NEUTRAL" and trend != "NEUTRAL" else 0, context)
                trends.append(context)
            except Exception as exc:
                logger.debug("HTF context unavailable for %s/%s: %s", symbol, timeframe, exc)

        if not trends:
            return 0, "UNAVAILABLE"

        # Neutral price action has no directional bias: neutral HTFs must not
        # count as aligned with a NEUTRAL signal.
        if direction == "NEUTRAL":
            alignment = 0
        else:
            alignment = sum(
                1 if item.split(":", 1)[-1] == direction
                else -1 if item.split(":", 1)[-1] in {"BULLISH", "BEARISH"}
                else 0
                for item in trends
            )
        return alignment, ",".join(trends)

    @staticmethod
    def _score_structure(price_change_pct: float, open_price: float, high: float, low: float, price: float) -> float:
        price_range = max(high - low, 0.0)
        if price_range <= 0 or open_price <= 0:
            return 0.0
        body_ratio = min(abs(price - open_price) / price_range, 1.0)
        close_location = (price - low) / price_range
        direction = SmartMoneyState._direction(price_change_pct)
        directional_close = (
            close_location if direction == "BULLISH"
            else 1.0 - close_location if direction == "BEARISH"
            else 0.5
        )
        return min(15.0, body_ratio * 7.5 + directional_close * 7.5)

    @staticmethod
    def _score_orderflow(direction: str, taker_buy_ratio: Optional[float]) -> float:
        if taker_buy_ratio is None:
            return 0.0
        if direction == "NEUTRAL":
            # Imbalanced taker flow without a directional price bias is not
            # directional confirmation.
            return 0.0
        ratio = min(max(taker_buy_ratio, 0.0), 1.0)
        aligned = ratio if direction == "BULLISH" else 1.0 - ratio
        return min(15.0, max(0.0, (aligned - 0.5) / 0.20) * 15.0)

    @staticmethod
    def _score_htf(alignment: int, context: str, direction: str) -> float:
        if context == "UNAVAILABLE":
            return 0.0
        if direction == "NEUTRAL":
            return 0.0
        if alignment >= 2:
            return 20.0
        if alignment == 1:
            return 10.0
        return 0.0

    def detect_smart_money_signal(self, symbol, volume, quote_volume, price, open_price, high, low, close_time, taker_buy_quote=None):
        self._check_hourly_reset()
        # Compare the closed candle against the PREVIOUS EMA; including the
        # spike itself in the denominator dilutes the anomaly.
        with self.lock:
            old_ema_quote = self.ema_quote_volume.get(symbol, 0.0)
        is_ready, _, _ = self.update_ema(symbol, volume, quote_volume)
        if not is_ready: return None
        self.update_volume_history(symbol, volume, quote_volume)
        rvol = quote_volume / old_ema_quote if old_ema_quote > 0 else 0
        price_change_pct = (price - open_price) / open_price * 100 if open_price > 0 else 0
        price_range_pct = (high - low) / open_price * 100 if open_price > 0 else 0
        velocity = self.calculate_velocity(symbol, quote_volume)
        direction = self._direction(price_change_pct)
        taker_buy_ratio = (
            min(max(float(taker_buy_quote) / quote_volume, 0.0), 1.0)
            if taker_buy_quote is not None and quote_volume > 0
            else None
        )
        signal_type = None; base_priority = 0.0
        if rvol >= CONFIG["rvol_multiplier"]:
            signal_type = "RVOL_SPIKE"
            base_priority = min(rvol, CONFIG["rvol_multiplier"] * 3.0) * 10
        elif CONFIG["enable_divergence_detection"] and rvol >= CONFIG["rvol_multiplier"] * 0.8 and price_range_pct <= CONFIG["divergence_max_price_change"]:
            signal_type = "DIVERGENCE_ACCUMULATION" if price_change_pct >= 0 else "DIVERGENCE_DISTRIBUTION"
            base_priority = min(rvol, CONFIG["rvol_multiplier"] * 3.0) * 15
        elif CONFIG["enable_velocity_detection"] and velocity >= CONFIG["velocity_threshold"] and rvol >= CONFIG["rvol_multiplier"] * 0.7:
            signal_type = "VELOCITY_SURGE"
            base_priority = min(velocity, CONFIG["velocity_threshold"] * 3.0) * 8
        if not signal_type: return None
        # Quality is evidence-weighted, not simply "how extreme is volume".
        # A pure 1m anomaly cannot earn 100/100 without HTF evidence.
        rvol_score = min(rvol / max(CONFIG["min_signal_rvol"], 1.0), 2.0) * 25.0
        velocity_score = min(velocity / max(CONFIG["min_signal_velocity"], 1.0), 2.0) * 15.0
        structure_score = self._score_structure(price_change_pct, open_price, high, low, price)
        orderflow_score = (
            self._score_orderflow(direction, taker_buy_ratio)
            if CONFIG.get("use_taker_buy_confirmation", True) else 0.0
        )
        try:
            signal_open_ms = int(datetime.strptime(
                close_time, "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=timezone.utc).timestamp() * 1000)
            htf_alignment, htf_context = self._fetch_htf_context(
                symbol, signal_open_ms, direction
            )
        except (TypeError, ValueError):
            htf_alignment, htf_context = 0, "UNAVAILABLE"
        htf_score = self._score_htf(htf_alignment, htf_context, direction)
        quality_score = min(
            100.0,
            rvol_score + velocity_score + structure_score + orderflow_score + htf_score,
        )
        # Scores above the unconfirmed ceiling require a directional signal and
        # at least one matching HTF. Merely having an HTF response is not enough.
        has_directional_htf_confirmation = (
            direction != "NEUTRAL"
            and htf_context != "UNAVAILABLE"
            and htf_alignment > 0
        )
        if not has_directional_htf_confirmation:
            quality_score = min(quality_score, CONFIG.get("max_quality_without_htf", 79.0))
        if quality_score < CONFIG.get("min_quality_score", 0): return None
        if CONFIG.get("require_confluence", True):
            orderflow_confirmed = (
                CONFIG.get("use_taker_buy_confirmation", True)
                and taker_buy_ratio is not None
                and (
                    (direction == "BULLISH" and taker_buy_ratio >= 0.55)
                    or (direction == "BEARISH" and taker_buy_ratio <= 0.45)
                )
            )
            conditions = (
                int(rvol >= CONFIG["min_signal_rvol"])
                + int(velocity >= CONFIG["min_signal_velocity"])
                + int(orderflow_confirmed)
                + int(has_directional_htf_confirmation)
            )
            if conditions < 2: return None
            # 80+ is reserved for directionally confirmed signals, not merely
            # signals whose HTF endpoint returned neutral or conflicting trends.
            if quality_score >= 80.0 and not has_directional_htf_confirmation:
                return None
        signal_key = (symbol, signal_type)
        with self.lock:
            if self.seen_signal_candles.get(signal_key) == close_time: return None
            self.seen_signal_candles[signal_key] = close_time
        ranking = quality_score * 1000.0 + base_priority
        return SmartMoneySignal(
            -ranking, symbol, signal_type, rvol, volume, quote_volume, price,
            price_change_pct, velocity, time.time(), close_time, quality_score,
            direction=direction,
            taker_buy_ratio=taker_buy_ratio,
            htf_alignment=htf_alignment,
            htf_context=htf_context,
            watch_context={"high": high, "low": low, "interval": CONFIG["timeframe"]},
        )

    def register_signal(self, signal):
        """Queue a candidate without allowing the in-memory queue to grow indefinitely.

        The database write is handled by the caller even when this returns False.
        When full, retain the highest-ranked candidates (priority_score is negative
        ranking, so smaller values are better).
        """
        key = (signal.symbol, signal.signal_type, signal.candle_time)
        with self.lock:
            if key in self.pending_keys:
                return False

            max_pending = max(1, int(CONFIG.get("max_pending_signals", 200)))
            if len(self.pending_signals) < max_pending:
                self.pending_keys.add(key)
                heapq.heappush(self.pending_signals, signal)
                return True

            worst_index = max(
                range(len(self.pending_signals)),
                key=lambda index: self.pending_signals[index].priority_score,
            )
            worst = self.pending_signals[worst_index]
            if signal.priority_score >= worst.priority_score:
                logger.warning(
                    "Alert queue full (%d); candidate not queued: %s %s quality=%.1f",
                    max_pending, signal.symbol, signal.signal_type, signal.quality_score,
                )
                return False

            evicted_key = (worst.symbol, worst.signal_type, worst.candle_time)
            self.pending_keys.discard(evicted_key)
            self.pending_signals[worst_index] = signal
            heapq.heapify(self.pending_signals)
            self.pending_keys.add(key)
            logger.warning(
                "Alert queue full (%d); replaced lower-ranked %s %s (quality=%.1f) "
                "with %s %s (quality=%.1f)",
                max_pending, worst.symbol, worst.signal_type, worst.quality_score,
                signal.symbol, signal.signal_type, signal.quality_score,
            )
            return True

    def _can_alert(self, symbol, followup=False):
        now = time.time()
        with self.lock:
            self._check_hourly_reset()
            if not followup and now - self.last_alert_time.get(symbol, 0) < CONFIG["alert_cooldown_sec"]: return False
            if self.hourly_alert_count["global"] >= CONFIG["max_alerts_per_hour"]: return False
            if self.hourly_alert_count[symbol] >= CONFIG["max_alerts_per_symbol_per_hour"]: return False
            self.last_alert_time[symbol] = now; self.hourly_alert_count["global"] += 1; self.hourly_alert_count[symbol] += 1
            return True

    def release_alert_slot(self, symbol):
        with self.lock:
            self.hourly_alert_count["global"] = max(0, self.hourly_alert_count["global"] - 1)
            self.hourly_alert_count[symbol] = max(0, self.hourly_alert_count[symbol] - 1)
            self.last_alert_time.pop(symbol, None)

    def pop_best_eligible(self):
        with self.lock:
            self._check_hourly_reset(); deferred=[]; selected=None
            max_age = max(0, int(CONFIG.get("max_signal_age_sec", 180)))
            now = time.time()
            while self.pending_signals:
                candidate=heapq.heappop(self.pending_signals); key=(candidate.symbol,candidate.signal_type,candidate.candle_time); self.pending_keys.discard(key)
                if max_age and now - candidate.timestamp > max_age:
                    logger.info(
                        "Discarding stale queued signal: %s %s age=%.0fs max_age=%ss",
                        candidate.symbol, candidate.signal_type, now - candidate.timestamp, max_age,
                    )
                    continue
                if not CONFIG.get("enabled",True): deferred.append(candidate); continue
                if self._can_alert(candidate.symbol): selected=candidate; break
                deferred.append(candidate)
            for candidate in deferred:
                key=(candidate.symbol,candidate.signal_type,candidate.candle_time)
                if key not in self.pending_keys: self.pending_keys.add(key); heapq.heappush(self.pending_signals,candidate)
            return selected


state=SmartMoneyState(CONFIG["ema_length"],CONFIG["rvol_multiplier"],CONFIG["max_alerts_per_hour"])


def _fetch_closed_kline_history(symbol, required_candles):
    """Fetch recent closed candles backwards, respecting Binance's 1000-row limit."""
    required_candles = max(1, int(required_candles))
    end_time = int(time.time() * 1000)
    history = []

    while len(history) < required_candles:
        limit = min(1000, required_candles - len(history))
        response = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={
                "symbol": symbol,
                "interval": CONFIG.get("timeframe", "1m"),
                "limit": limit,
                "endTime": end_time - 1,
            },
            timeout=10,
        )
        response.raise_for_status()
        page = response.json()
        if not page:
            break

        now_ms = int(time.time() * 1000)
        closed_page = [
            row for row in page
            if len(row) >= 8 and int(row[6]) < now_ms
        ]
        history = closed_page + history

        # Move strictly before the oldest row in this page to avoid duplicates.
        end_time = int(page[0][0])
        if len(page) < limit:
            break
        # Keep the aggregate request rate below Binance's per-IP weight cap.
        time.sleep(0.5)

    return history[-required_candles:]


def _seed_ema_history(symbol, candles):
    """Initialize one symbol's EMA from chronological, already-closed Binance klines."""
    required = state.warmup_candles
    if len(candles) < required:
        return False

    with state.lock:
        # Do not reset a symbol whose EMA is already fully warmed. A partially
        # warmed symbol is replaced by history so it avoids a 40-hour restart wait.
        if state.ema_initialized.get(symbol, 0) >= required:
            return True
        state.ema_volume.pop(symbol, None)
        state.ema_quote_volume.pop(symbol, None)
        state.ema_initialized.pop(symbol, None)
        state.volume_history.pop(symbol, None)
        state.quote_volume_history.pop(symbol, None)
        for candle in candles[-required:]:
            state.update_ema(symbol, float(candle[5]), float(candle[7]))
        return state.ema_initialized.get(symbol, 0) >= required


def bootstrap_ema_history(symbols):
    """Warm up symbols from REST history before live WebSocket candles arrive."""
    with state.lock:
        needed = [
            symbol for symbol in symbols
            if state.ema_initialized.get(symbol, 0) < state.warmup_candles
        ]
    if not needed:
        return

    logger.info(
        "⏳ Bootstrapping EMA history for %d symbols (%d closed candles each)",
        len(needed), state.warmup_candles,
    )

    def fetch_one(symbol):
        try:
            return symbol, _fetch_closed_kline_history(symbol, state.warmup_candles), None
        except Exception as exc:
            return symbol, None, exc

    with ThreadPoolExecutor(max_workers=min(4, len(needed))) as executor:
        for symbol, candles, error in executor.map(fetch_one, needed):
            if error is not None:
                logger.warning("EMA history bootstrap failed for %s: %s", symbol, error)
                continue
            if not candles or len(candles) < state.warmup_candles:
                logger.warning(
                    "EMA history incomplete for %s: received %d/%d closed candles; "
                    "signal detection remains in warmup",
                    symbol, len(candles or []), state.warmup_candles,
                )
                continue
            if _seed_ema_history(symbol, candles):
                logger.info(
                    "✅ EMA history ready for %s (%d closed candles)",
                    symbol, len(candles[-state.warmup_candles:]),
                )


class SmartMoneyTelegram:
    def __init__(self, token, chat_id):
        self.token = token
        self.chat_id = chat_id
        self.url = f"https://api.telegram.org/bot{token}/sendMessage" if token else ""
        self._last_send = 0
        self._lock = threading.Lock()
        self.session = requests.Session()
        self.session.mount(
            "https://",
            HTTPAdapter(max_retries=Retry(total=3, backoff_factor=0.3, status_forcelist=[429, 500, 502, 503, 504])),
        )
        if not self.delivery_authorized:
            logger.info(
                "ℹ️ Screener Telegram delivery is disabled (authorization missing; "
                "set RETAILBOT2_ALLOW_TELEGRAM_DELIVERY=1 to enable delivery)."
            )

    @property
    def delivery_authorized(self) -> bool:
        return os.getenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", "").strip() == "1"

    @staticmethod
    def _safe_http_status(exc: Exception) -> str:
        resp = getattr(exc, "response", None)
        if resp is not None:
            status = getattr(resp, "status_code", None)
            if type(status) is int and 100 <= status <= 599:
                return str(status)
        return "unknown"

    def send_early_alert(self, signal):
        message = momentum.message(signal, ema_length=CONFIG["ema_length"])
        if not self.delivery_authorized:
            logger.info(
                "ℹ️ Screener early alert suppressed (delivery unauthorized): %s | %s | Quality %.0f",
                signal.symbol, signal.signal_type, signal.quality_score,
            )
            return False

        if not self.token or not self.chat_id:
            logger.error("❌ Telegram alert skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing")
            return False

        with self._lock:
            elapsed = time.time() - self._last_send
            if elapsed < 0.8:
                time.sleep(0.8 - elapsed)
            try:
                resp = self.session.post(
                    self.url,
                    json={"chat_id": self.chat_id, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True},
                    timeout=8,
                )
                resp.raise_for_status()
                self._last_send = time.time()
                logger.info("✅ Early alert: %s | %s | Quality %.0f | RVOL %.2fx", signal.symbol, signal.signal_type, signal.quality_score, signal.rvol)
                return True
            except requests.HTTPError as exc:
                status = self._safe_http_status(exc)
                logger.error("❌ Telegram API error: error_type=%s http_status=%s", type(exc).__name__, status)
                return False
            except Exception as exc:
                status = self._safe_http_status(exc)
                logger.error("❌ Telegram request error: error_type=%s http_status=%s", type(exc).__name__, status)
                return False

    def send(self, text):
        if not self.delivery_authorized:
            logger.info("ℹ️ Screener message suppressed (delivery unauthorized): %s", text)
            return False
        if not self.token or not self.chat_id:
            logger.error("❌ Telegram message skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing")
            return False
        with self._lock:
            elapsed = time.time() - self._last_send
            if elapsed < 0.8:
                time.sleep(0.8 - elapsed)
            try:
                resp = self.session.post(
                    self.url,
                    json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
                    timeout=8,
                )
                resp.raise_for_status()
                self._last_send = time.time()
                return True
            except Exception as exc:
                status = self._safe_http_status(exc)
                logger.error("❌ Telegram error: error_type=%s http_status=%s", type(exc).__name__, status)
                return False

telegram = SmartMoneyTelegram(CONFIG["telegram_bot_token"], CONFIG["telegram_chat_id"])
if telegram.delivery_authorized:
    if CONFIG["telegram_bot_token"] and CONFIG["telegram_chat_id"]:
        logger.info("📨 Telegram alert transport configured and authorized")
    else:
        logger.warning("⚠️ Telegram alerts are NOT configured: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing")
else:
    logger.info("ℹ️ Screener Telegram delivery disabled: RETAILBOT2_ALLOW_TELEGRAM_DELIVERY is not set to 1")


def verify_signal_schema():
    """Verify migration-managed screener schema without issuing DDL or DML."""
    conn = get_pool().get_connection()
    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        required_tables = ("smart_money_signals", "screener_control", "screener_heartbeat")
        placeholders = ", ".join(["%s"] * len(required_tables))
        cur.execute(
            f"""SELECT TABLE_NAME AS table_name
                FROM information_schema.tables
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME IN ({placeholders})""",
            required_tables,
        )
        existing = {str(row["table_name"]) for row in cur.fetchall()}
        missing_tables = sorted(set(required_tables) - existing)
        if missing_tables:
            raise RuntimeError(
                "Screener schema is missing tables "
                f"({', '.join(missing_tables)}); run 'python -m db.migrate' before startup."
            )

        required_columns = {
            "smart_money_signals": {
                "quality_score", "direction", "taker_buy_ratio", "htf_alignment",
                "htf_context", "delivery_status", "delivery_attempts",
                "next_attempt_at", "delivery_updated_at", "delivered_at",
                "last_delivery_error", "candle_time", "watch_context", "parent_watch_id",
            },
            "screener_heartbeat": {"timeframe"},
        }
        column_conditions = []
        params = []
        for table, columns in required_columns.items():
            for column in sorted(columns):
                column_conditions.append("(TABLE_NAME = %s AND COLUMN_NAME = %s)")
                params.extend((table, column))
        cur.execute(
            """SELECT TABLE_NAME AS table_name, COLUMN_NAME AS column_name,
                      COLUMN_TYPE AS column_type
               FROM information_schema.columns
               WHERE TABLE_SCHEMA = DATABASE() AND ("""
            + " OR ".join(column_conditions)
            + ")",
            tuple(params),
        )
        rows = cur.fetchall()
        actual = {(str(row["table_name"]), str(row["column_name"])): str(row["column_type"]).lower()
                  for row in rows}
        missing_columns = [
            f"{table}.{column}"
            for table, columns in required_columns.items()
            for column in sorted(columns)
            if (table, column) not in actual
        ]
        if missing_columns:
            raise RuntimeError(
                "Screener schema is outdated "
                f"({', '.join(missing_columns)}); run 'python -m db.migrate' before startup."
            )
        if not actual[("smart_money_signals", "candle_time")].startswith("varchar(32)"):
            raise RuntimeError(
                "Screener schema requires smart_money_signals.candle_time VARCHAR(32); "
                "run 'python -m db.migrate' before startup."
            )
    finally:
        try:
            if cur is not None:
                cur.close()
        finally:
            conn.close()

_TUNABLE_CONFIG_KEYS={"rvol_multiplier","divergence_max_price_change","velocity_threshold","enable_divergence_detection","enable_velocity_detection","max_alerts_per_hour","max_alerts_per_symbol_per_hour","alert_cooldown_sec","require_confluence","min_signal_rvol","min_signal_velocity","min_quote_volume_24h","min_market_cap_usd","min_quality_score","min_alert_quality_score","min_alert_quote_volume","max_signal_age_sec","expired_signal_retention_days","expired_signal_cleanup_interval_days"}

def reload_control():
    conn=get_pool().get_connection()
    try:
        cur=conn.cursor(dictionary=True); cur.execute("SELECT enabled,overrides_json FROM screener_control WHERE id=1"); row=cur.fetchone(); cur.close()
    except Exception as e: logger.error("❌ reload_control: %s",e); return False
    finally: conn.close()
    if not row: return False
    was_enabled=CONFIG.get("enabled",True)
    CONFIG["enabled"]=bool(row["enabled"])
    # Reset all dashboard-tunable values to the process baseline first. The DB
    # stores the complete current override set, so an omitted key means "use
    # the environment default" rather than "keep the previous runtime value".
    for key in _TUNABLE_CONFIG_KEYS:
        if key in DEFAULT_CONFIG:
            CONFIG[key]=DEFAULT_CONFIG[key]
    if was_enabled!=CONFIG["enabled"]:
        state_str="ENABLED" if CONFIG["enabled"] else "PAUSED (alerts suppressed)"; logger.info("🎛️ Screener %s via dashboard control",state_str); telegram.send(f"🎛️ <b>Smart Money Screener {state_str}</b> (via web dashboard)")
    try: overrides=json.loads(row["overrides_json"]) if row["overrides_json"] else {}
    except (TypeError,ValueError): logger.error("❌ Invalid screener overrides JSON; ignoring it"); overrides={}
    applied=[]; universe_changed=False
    for key,value in overrides.items():
        if key not in _TUNABLE_CONFIG_KEYS: continue
        current=CONFIG.get(key)
        try: new_value=bool(value) if isinstance(current,bool) else int(value) if isinstance(current,int) and not isinstance(current,bool) else float(value) if isinstance(current,float) else value
        except (TypeError,ValueError): continue
        if current!=new_value:
            CONFIG[key]=new_value; applied.append(f"{key}={new_value}")
            if key in {"min_quote_volume_24h","min_market_cap_usd"}: universe_changed=True
    if applied: logger.info("🎛️ Applied config overrides: %s",", ".join(applied))
    return universe_changed

def save_signal_to_db(signal, telegram_sent):
    """Persist a candidate with bounded retries; return True only after commit."""
    sql = """
        INSERT INTO smart_money_signals
            (symbol, signal_type, rvol, volume, quote_volume, price,
             price_change_pct, volume_velocity, quality_score, direction,
             taker_buy_ratio, htf_alignment, htf_context, candle_time,
             telegram_sent, watch_context, parent_watch_id, detected_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, UTC_TIMESTAMP())
        ON DUPLICATE KEY UPDATE
            quality_score=VALUES(quality_score),
            direction=VALUES(direction),
            taker_buy_ratio=VALUES(taker_buy_ratio),
            htf_alignment=VALUES(htf_alignment),
            htf_context=VALUES(htf_context),
            watch_context=COALESCE(watch_context, VALUES(watch_context)),
            telegram_sent=telegram_sent OR VALUES(telegram_sent)
    """
    params = (
        signal.symbol, signal.signal_type, signal.rvol, signal.volume,
        signal.quote_volume, signal.price, signal.price_change_pct,
        signal.volume_velocity, signal.quality_score, signal.direction,
        signal.taker_buy_ratio, signal.htf_alignment, signal.htf_context,
        signal.candle_time, telegram_sent, json.dumps(signal.watch_context) if getattr(signal, "watch_context", None) else None,
        getattr(signal, "parent_watch_id", None),
    )

    persisted = False
    for attempt in range(3):
        conn = None
        cur = None
        try:
            conn = get_pool().get_connection()
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()
            persisted = True
            break
        except Exception as exc:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            if attempt < 2:
                logger.warning(
                    "Signal insert attempt %d/3 failed for %s: %s",
                    attempt + 1, signal.symbol, exc,
                )
                time.sleep(0.25 * (attempt + 1))
            else:
                logger.error(
                    "❌ save_signal_to_db failed for %s after 3 attempts: %s",
                    signal.symbol, exc,
                )
        finally:
            if cur is not None:
                try:
                    cur.close()
                except Exception:
                    pass
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    if not persisted:
        return False

    # Heartbeat telemetry is secondary: never let it undo or misreport a
    # successfully committed signal, and don't consume a second connection
    # before the durable insert has succeeded.
    try:
        update_screener_heartbeat(last_signal_at=signal.candle_time)
    except Exception as exc:
        logger.warning("⚠️ screener heartbeat signal update failed: %s", exc)
    return True

_CACHED_MARKET_CAPS = {}
_MARKET_CAPS_FILE = "/tmp/screener_market_caps.json"

def get_market_caps():
    global _CACHED_MARKET_CAPS
    if CONFIG["min_market_cap_usd"] <= 0:
        return {}
    ticker_caps = defaultdict(set)
    try:
        for page in range(1, max(1, CONFIG.get("market_cap_pages", 5)) + 1):
            try:
                resp = requests.get(
                    "https://api.coingecko.com/api/v3/coins/markets",
                    params={"vs_currency": "usd", "order": "market_cap_desc", "per_page": 250, "page": page, "sparkline": "false"},
                    timeout=20
                )
                resp.raise_for_status()
                rows = resp.json()
            except Exception as page_exc:
                logger.warning("CoinGecko market cap fetch failed for page %d: %s", page, page_exc)
                break
            if not rows:
                break
            for row in rows:
                ticker = str(row.get("symbol", "")).upper()
                cap = float(row.get("market_cap") or 0)
                if ticker and cap > 0:
                    ticker_caps[ticker].add(cap)
            if len(rows) < 250:
                break
            time.sleep(1.0)
        caps = {ticker: next(iter(values)) for ticker, values in ticker_caps.items() if len(values) == 1}
        if caps:
            logger.info("Loaded market-cap metadata for %d unambiguous tickers; excluded %d ambiguous tickers", len(caps), len(ticker_caps) - len(caps))
            _CACHED_MARKET_CAPS = caps
            try:
                with open(_MARKET_CAPS_FILE, "w") as f:
                    json.dump(caps, f)
            except Exception:
                pass
            return caps
    except Exception as e:
        logger.warning("Market-cap metadata unavailable; trying cached fallback: %s", e)

    if _CACHED_MARKET_CAPS:
        logger.info("Using memory-cached market-cap metadata (%d tickers)", len(_CACHED_MARKET_CAPS))
        return _CACHED_MARKET_CAPS
    if os.path.exists(_MARKET_CAPS_FILE):
        try:
            with open(_MARKET_CAPS_FILE) as f:
                _CACHED_MARKET_CAPS = json.load(f)
                logger.info("Using disk-cached market-cap metadata (%d tickers)", len(_CACHED_MARKET_CAPS))
                return _CACHED_MARKET_CAPS
        except Exception:
            pass
    logger.warning("Market-cap metadata completely unavailable; unknown assets will be excluded")
    return {}

def get_liquid_symbols():
    market_caps=get_market_caps()
    try:
        resp=requests.get("https://api.binance.com/api/v3/exchangeInfo",timeout=15); resp.raise_for_status(); data=resp.json(); candidates=[s for s in data["symbols"] if s["quoteAsset"]==CONFIG["symbols_filter"] and s["status"]=="TRADING"]; tickers={t["symbol"]:t for t in requests.get("https://api.binance.com/api/v3/ticker/24hr",timeout=15).json()}; symbols=[]
        for s in candidates:
            sym=s["symbol"]
            if CONFIG["exclude_leveraged"] and any(tok in sym for tok in ["UPUSDT","DOWNUSDT","BULL","BEAR"]): continue
            if CONFIG["exclude_stablecoins"] and sym.replace(CONFIG["symbols_filter"],"") in ["USDC","BUSD","TUSD"]: continue
            if float(tickers.get(sym,{}).get("quoteVolume",0))<CONFIG["min_quote_volume_24h"]: continue
            if CONFIG["min_market_cap_usd"]>0:
                base=sym[:-len(CONFIG["symbols_filter"])] if CONFIG["symbols_filter"] else sym
                if market_caps.get(base.upper(),0)<CONFIG["min_market_cap_usd"]: continue
            symbols.append(sym)
        logger.info("✅ Filtered %d liquid pairs (min $%.0f 24h vol)", len(symbols), CONFIG["min_quote_volume_24h"]); return symbols
    except Exception as e: logger.error("❌ Failed to fetch symbols: %s",e); return []

def build_stream_name(symbol,timeframe): return f"{symbol.lower()}@kline_{timeframe}"

def process_kline_message(raw_msg):
    try:
        msg=json.loads(raw_msg); event=msg["data"] if "data" in msg and "stream" in msg else msg
        if event.get("e")!="kline": return
        kline=event.get("k",{}); symbol=event.get("s","").upper()
        if not kline.get("x"): return
        volume=float(kline["v"]); quote_volume=float(kline["q"]); open_price=float(kline["o"]); close_price=float(kline["c"]); high_price=float(kline["h"]); low_price=float(kline["l"]); taker_buy_quote=float(kline["Q"]) if "Q" in kline and kline["Q"] is not None else None; close_time=datetime.fromtimestamp(kline["t"]/1000,tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        candidate=state.detect_smart_money_signal(symbol,volume,quote_volume,close_price,open_price,high_price,low_price,close_time,taker_buy_quote=taker_buy_quote)
        if candidate:
            # MySQL is the durable source of truth for delivery; do not rely on RAM for pending alerts.
            if save_signal_to_db(candidate, telegram_sent=False):
                logger.info("🗃️ Candidate persisted for delivery: %s | %s | Quality %.0f | RVOL %.2fx", candidate.symbol, candidate.signal_type, candidate.quality_score, candidate.rvol)
            else:
                logger.error("❌ Candidate was NOT persisted after retries: %s | %s | %s", candidate.symbol, candidate.signal_type, candidate.candle_time)
    except (json.JSONDecodeError,KeyError,ValueError,TypeError) as e: logger.warning("Invalid kline message: %s",e)
    except Exception as e: logger.error("Error processing message: %s",e,exc_info=True)

def cleanup_expired_signal_history():
    """Delete only old EXPIRED rows; retain SENT/FAILED/PENDING records for audit."""
    conn = None
    cur = None
    try:
        retention_days = max(1, int(CONFIG.get("expired_signal_retention_days", 30)))
        conn = get_pool().get_connection()
        cur = conn.cursor()
        cur.execute(
            """DELETE FROM smart_money_signals
               WHERE delivery_status='EXPIRED'
                 AND delivery_updated_at < DATE_SUB(UTC_TIMESTAMP(), INTERVAL %s DAY)""",
            (retention_days,),
        )
        deleted = max(0, int(getattr(cur, "rowcount", 0) or 0))
        conn.commit()
        logger.info("Weekly expired-signal cleanup removed %d rows older than %d days", deleted, retention_days)
        return deleted
    except Exception as exc:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("Expired signal retention cleanup failed: %s", exc)
        return 0
    finally:
        if cur:
            try:
                cur.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def expired_signal_cleanup_loop():
    """Clean up at startup, then honor the current dashboard-configured interval."""
    last_cleanup = None
    while not state.shutdown_flag:
        interval_seconds = max(1, int(CONFIG.get("expired_signal_cleanup_interval_days", 7))) * 86400
        now = time.monotonic()
        if last_cleanup is None or now - last_cleanup >= interval_seconds:
            cleanup_expired_signal_history()
            last_cleanup = time.monotonic()

        # Recheck the interval periodically so dashboard changes take effect
        # without restarting the process. Retention is read at cleanup time.
        for _ in range(60):
            if state.shutdown_flag:
                return
            time.sleep(1)


def claim_next_pending_signal():
    """Atomically claim the highest-ranked fresh, quality-eligible candidate from MySQL."""
    conn = None
    cur = None
    try:
        conn = get_pool().get_connection()
        cur = conn.cursor(dictionary=True)
        max_age = max(1, int(CONFIG.get("max_signal_age_sec", 180)))
        watch_max_sec = min(momentum.WATCH_FOLLOWUP_MAX_MINUTES * 60, max_age)
        # Recover abandoned claims and classify candidates without deleting history.
        cur.execute("UPDATE smart_money_signals SET delivery_status='PENDING', delivery_updated_at=UTC_TIMESTAMP() WHERE delivery_status='SENDING' AND delivery_updated_at < DATE_SUB(UTC_TIMESTAMP(), INTERVAL 2 MINUTE)")
        min_quality = float(CONFIG.get("min_alert_quality_score", 80))
        cur.execute("""UPDATE smart_money_signals
                       SET delivery_status='FILTERED',
                           last_delivery_error='Quality below Telegram alert threshold',
                           delivery_updated_at=UTC_TIMESTAMP()
                       WHERE delivery_status='PENDING' AND quality_score < %s""", (min_quality,))
        min_alert_volume = float(CONFIG["min_alert_quote_volume"])
        if min_alert_volume > 0:
            cur.execute("""UPDATE smart_money_signals
                           SET delivery_status='FILTERED',
                               last_delivery_error='Candle quote volume below Telegram alert threshold',
                               delivery_updated_at=UTC_TIMESTAMP()
                           WHERE delivery_status='PENDING' AND quote_volume < %s""", (min_alert_volume,))
        # 1. Expire directional 1m parent watches past their 5-candle follow-up deadline (or max_age derived from candle_time)
        cur.execute("""UPDATE smart_money_signals
                       SET delivery_status='EXPIRED',
                           last_delivery_error=%s,
                           delivery_updated_at=UTC_TIMESTAMP()
                       WHERE delivery_status='PENDING' AND parent_watch_id IS NULL AND direction IN ('BULLISH', 'BEARISH')
                         AND candle_time <= CONCAT(REPLACE(UTC_TIMESTAMP() - INTERVAL %s SECOND, ' ', 'T'), 'Z')""",
                    (momentum.EXPIRED_WATCH_REASON, watch_max_sec))
        # 2. General signal expiry based on detected_at for non-directional/other signals
        cur.execute("UPDATE smart_money_signals SET delivery_status='EXPIRED', last_delivery_error='Signal expired before delivery', delivery_updated_at=UTC_TIMESTAMP() WHERE delivery_status='PENDING' AND detected_at < DATE_SUB(UTC_TIMESTAMP(), INTERVAL %s SECOND)", (max_age,))
        # 3. Confirmation child expiry: superseded by next candle (after 1m close)
        cur.execute("""UPDATE smart_money_signals SET delivery_status='EXPIRED', delivery_updated_at=UTC_TIMESTAMP(),
                       last_delivery_error='Confirmation superseded by next candle'
                       WHERE delivery_status='PENDING' AND parent_watch_id IS NOT NULL AND
                         candle_time <= CONCAT(REPLACE(UTC_TIMESTAMP() - INTERVAL 2 MINUTE, ' ', 'T'), 'Z')""")
        conn.commit()

        # Avoid repeatedly claiming/releasing rows while the global rolling-hour cap is full.
        cur.execute("""SELECT COUNT(*) AS sent_count FROM smart_money_signals
                       WHERE delivery_status='SENT'
                         AND delivered_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 HOUR)""")
        sent_row = cur.fetchone()
        sent_last_hour = int((sent_row.get("sent_count", 0) if sent_row else 0) or 0)
        if sent_last_hour >= max(1, int(CONFIG.get("max_alerts_per_hour", 5))):
            conn.commit()
            return None

        # Exclude expired watches when selecting a claim
        cur.execute("""SELECT id,symbol,signal_type,rvol,volume,quote_volume,price,price_change_pct,volume_velocity,quality_score,direction,taker_buy_ratio,htf_alignment,htf_context,candle_time,detected_at,watch_context,parent_watch_id
                       FROM smart_money_signals
                       WHERE delivery_status='PENDING' AND quality_score >= %s
                         AND (%s <= 0 OR quote_volume >= %s)
                         AND (parent_watch_id IS NULL OR candle_time > CONCAT(REPLACE(UTC_TIMESTAMP() - INTERVAL 2 MINUTE, ' ', 'T'), 'Z'))
                         AND (parent_watch_id IS NOT NULL OR direction NOT IN ('BULLISH', 'BEARISH') OR candle_time > CONCAT(REPLACE(UTC_TIMESTAMP() - INTERVAL %s SECOND, ' ', 'T'), 'Z'))
                         AND (next_attempt_at IS NULL OR next_attempt_at <= UTC_TIMESTAMP())
                         AND detected_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL %s SECOND)
                       ORDER BY quality_score DESC, rvol DESC, detected_at ASC, id ASC LIMIT 1 FOR UPDATE""", (min_quality, min_alert_volume, min_alert_volume, watch_max_sec, max_age))
        row = cur.fetchone()
        if not row:
            conn.commit()
            return None
        cur.execute("UPDATE smart_money_signals SET delivery_status='SENDING', delivery_attempts=delivery_attempts+1, delivery_updated_at=UTC_TIMESTAMP() WHERE id=%s AND delivery_status='PENDING'", (row["id"],))
        if getattr(cur, "rowcount", 1) != 1:
            conn.rollback()
            return None
        conn.commit()
        detected = row.get("detected_at")
        if isinstance(detected, datetime):
            detected = detected.replace(tzinfo=timezone.utc).timestamp()
        else:
            detected = time.time()
        quality = float(row.get("quality_score") or 0)
        rvol = float(row.get("rvol") or 0)
        return SmartMoneySignal(
            -(quality * 1000.0 + rvol * 10.0), str(row["symbol"]), str(row["signal_type"]),
            rvol, float(row.get("volume") or 0), float(row.get("quote_volume") or 0),
            float(row.get("price") or 0), float(row.get("price_change_pct") or 0),
            float(row.get("volume_velocity") or 0), detected, str(row["candle_time"]), quality,
            direction=str(row.get("direction") or "NEUTRAL"),
            taker_buy_ratio=float(row["taker_buy_ratio"]) if row.get("taker_buy_ratio") is not None else None,
            htf_alignment=int(row.get("htf_alignment") or 0),
            htf_context=str(row.get("htf_context") or "UNAVAILABLE"),
            watch_context=json.loads(row["watch_context"]) if row.get("watch_context") else None,
            parent_watch_id=row.get("parent_watch_id"),
        )
    except Exception as exc:
        if conn:
            try: conn.rollback()
            except Exception: pass
        logger.error("Durable alert queue claim failed: %s", exc)
        return None
    finally:
        if cur:
            try: cur.close()
            except Exception: pass
        if conn:
            try: conn.close()
            except Exception: pass

def complete_signal_delivery(signal, sent, error=None):
    """Record delivery outcome; retry transient failures with bounded exponential backoff."""
    conn = None
    cur = None
    try:
        conn = get_pool().get_connection()
        cur = conn.cursor()
        key = (signal.symbol, signal.signal_type, signal.candle_time)
        if sent:
            cur.execute("""UPDATE smart_money_signals SET telegram_sent=TRUE, delivery_status='SENT', delivered_at=UTC_TIMESTAMP(), delivery_updated_at=UTC_TIMESTAMP(), last_delivery_error=NULL, next_attempt_at=NULL WHERE symbol=%s AND signal_type=%s AND candle_time=%s""", key)
        else:
            max_attempts = max(1, int(CONFIG.get("max_delivery_attempts", 5)))
            cur.execute("SELECT delivery_attempts FROM smart_money_signals WHERE symbol=%s AND signal_type=%s AND candle_time=%s", key)
            row = cur.fetchone()
            attempts = int(row[0]) if row else max_attempts
            if attempts >= max_attempts:
                status, delay = "FAILED", 0
            else:
                status, delay = "PENDING", min(900, 30 * (2 ** max(0, attempts - 1)))
            # Recheck deadline for retries: if retry crosses deadline, expire immediately
            now_ms = int(time.time() * 1000)
            max_age_sec = max(1, int(CONFIG.get("max_signal_age_sec", 180)))
            if getattr(signal, "parent_watch_id", None) is None and getattr(signal, "direction", None) in ("BULLISH", "BEARISH"):
                if momentum.is_watch_expired(signal.candle_time, now_ms + delay * 1000, max_age_sec):
                    status, delay = "EXPIRED", 0
                    error = momentum.EXPIRED_WATCH_REASON
            elif getattr(signal, "parent_watch_id", None) is not None:
                child_start_ms = momentum.utc_ms(signal.candle_time)
                if now_ms + delay * 1000 >= child_start_ms + 2 * momentum.MINUTE:
                    status, delay = "EXPIRED", 0
                    error = "Confirmation superseded by next candle"
            cur.execute("""UPDATE smart_money_signals SET delivery_status=%s, delivery_updated_at=UTC_TIMESTAMP(), next_attempt_at=IF(%s='PENDING', DATE_ADD(UTC_TIMESTAMP(), INTERVAL %s SECOND), NULL), last_delivery_error=%s WHERE symbol=%s AND signal_type=%s AND candle_time=%s""", (status, status, delay, str(error or "Telegram delivery failed")[:500], *key))
        conn.commit()
    except Exception as exc:
        if conn:
            try: conn.rollback()
            except Exception: pass
        logger.error("Failed to record delivery outcome for %s: %s", signal.symbol, exc)
    finally:
        if cur:
            try: cur.close()
            except Exception: pass
        if conn:
            try: conn.close()
            except Exception: pass

def release_signal_claim(signal):
    """Return a claimed signal to the queue when rate limits currently block delivery."""
    conn = None
    cur = None
    try:
        conn = get_pool().get_connection()
        cur = conn.cursor()
        cur.execute("""UPDATE smart_money_signals SET delivery_status='PENDING', delivery_attempts=GREATEST(delivery_attempts-1,0), next_attempt_at=DATE_ADD(UTC_TIMESTAMP(), INTERVAL 15 SECOND), delivery_updated_at=UTC_TIMESTAMP() WHERE symbol=%s AND signal_type=%s AND candle_time=%s AND delivery_status='SENDING'""", (signal.symbol, signal.signal_type, signal.candle_time))
        conn.commit()
    except Exception as exc:
        if conn:
            try: conn.rollback()
            except Exception: pass
        logger.error("Failed to release alert claim for %s: %s", signal.symbol, exc)
    finally:
        if cur:
            try: cur.close()
            except Exception: pass
        if conn:
            try: conn.close()
            except Exception: pass

def expire_claimed_signal(signal, reason):
    """Mark a claimed signal as expired in MySQL and finalize its status."""
    conn = None
    cur = None
    try:
        conn = get_pool().get_connection()
        cur = conn.cursor()
        cur.execute("""UPDATE smart_money_signals
                       SET delivery_status='EXPIRED',
                           last_delivery_error=%s,
                           delivery_updated_at=UTC_TIMESTAMP()
                       WHERE symbol=%s AND signal_type=%s AND candle_time=%s AND delivery_status='SENDING'""",
                    (reason, signal.symbol, signal.signal_type, signal.candle_time))
        conn.commit()
    except Exception as exc:
        if conn:
            try: conn.rollback()
            except Exception: pass
        logger.error("Failed to expire claimed signal for %s: %s", signal.symbol, exc)
    finally:
        if cur:
            try: cur.close()
            except Exception: pass
        if conn:
            try: conn.close()
            except Exception: pass

def persistent_alert_limit_allows(symbol, parent_id=None):
    """Enforce hourly and per-symbol limits across process restarts using delivery history."""
    conn = None
    cur = None
    try:
        conn = get_pool().get_connection()
        cur = conn.cursor()
        cur.execute("""SELECT COUNT(*), COALESCE(SUM(symbol=%s),0), MAX(CASE WHEN symbol=%s AND id <> %s THEN delivered_at ELSE NULL END)
                       FROM smart_money_signals
                       WHERE delivery_status='SENT' AND delivered_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 HOUR)""", (symbol, symbol, parent_id or 0))
        row = cur.fetchone() or (0, 0, None)
        total_sent, symbol_sent, last_symbol_sent = int(row[0] or 0), int(row[1] or 0), row[2]
        if total_sent >= int(CONFIG.get("max_alerts_per_hour", 5)):
            return False
        if symbol_sent >= int(CONFIG.get("max_alerts_per_symbol_per_hour", 2)):
            return False
        if last_symbol_sent is not None:
            if isinstance(last_symbol_sent, str):
                last_symbol_sent = datetime.fromisoformat(last_symbol_sent)
            if last_symbol_sent.tzinfo is None:
                last_symbol_sent = last_symbol_sent.replace(tzinfo=timezone.utc)
            if time.time() - last_symbol_sent.timestamp() < int(CONFIG.get("alert_cooldown_sec", 900)):
                return False
        return True
    except Exception as exc:
        # Fail closed: do not bypass trading-alert rate limits when the DB is unavailable.
        logger.error("Persistent alert rate-limit check failed; delivery paused: %s", exc)
        return False
    finally:
        if cur:
            try: cur.close()
            except Exception: pass
        if conn:
            try: conn.close()
            except Exception: pass

def queue_watch_confirmations(now_ms):
    # Only delivered watches can have follow-ups. The DB link survives restarts.
    with get_pool().get_connection() as conn, conn.cursor(dictionary=True) as cur:
        cur.execute("""SELECT w.* FROM smart_money_signals w
                       WHERE w.delivery_status='SENT' AND w.parent_watch_id IS NULL
                         AND w.watch_context IS NOT NULL AND w.direction IN ('BULLISH','BEARISH')
                         AND w.delivered_at >= UTC_TIMESTAMP() - INTERVAL 7 MINUTE
                         AND NOT EXISTS (SELECT 1 FROM smart_money_signals c WHERE c.parent_watch_id=w.id)
                       ORDER BY w.delivered_at DESC""")
        watches = cur.fetchall()
    for w in watches:
        ctx = json.loads(w["watch_context"])
        start = momentum.utc_ms(w["candle_time"])
        age = now_ms - start
        if ctx["interval"] != "1m" or not 2 * momentum.MINUTE <= age < (momentum.WINDOW + 2) * momentum.MINUTE:
            continue
        bars = momentum.bars_between(w["symbol"], start - momentum.HISTORY * momentum.MINUTE,
                                     start + (momentum.WINDOW + 1) * momentum.MINUTE - 1, now_ms)
        watch = bars.get(start)
        if not watch or watch.high != ctx["high"] or watch.low != ctx["low"]:
            continue
        result = momentum.first_break(bars, start, momentum.utc_ms(w["delivered_at"]), w["direction"])
        if not result:
            continue
        bar, multiple = result
        closed_at = bar.start + momentum.MINUTE
        if now_ms - closed_at >= momentum.MINUTE:
            continue
        # Keep metrics on this linked row as a snapshot of the originating watch.
        # The new close and normalization belong to the confirmation context.
        signal = SmartMoneySignal(
            0, w["symbol"], "MOMENTUM_CONFIRMED", w["rvol"], w["volume"], w["quote_volume"],
            w["price"], w["price_change_pct"], w["volume_velocity"], time.time(), momentum.iso(bar.start),
            w["quality_score"], direction=w["direction"], parent_watch_id=w["id"],
            watch_context={"watch_time": w["candle_time"], "confirmed_at": momentum.iso(closed_at),
                           "close": bar.close, "range_multiple": multiple,
                           "level": watch.high if w["direction"] == "BULLISH" else watch.low},
        )
        save_signal_to_db(signal, telegram_sent=False)


def confirmation_loop():
    while not state.shutdown_flag:
        try:
            if CONFIG.get("enabled") and telegram.delivery_authorized and CONFIG["timeframe"] == "1m":
                queue_watch_confirmations(int(time.time() * 1000))
        except requests.HTTPError as exc:
            logger.warning("Confirmation market data unavailable: HTTP %s", exc.response.status_code)
            if exc.response.status_code in (418, 429):
                retry = exc.response.headers.get("Retry-After", "")
                time.sleep(max(120, int(retry) if retry.isdigit() else 120))
        except Exception:
            logger.exception("Momentum confirmation check failed")
        time.sleep(60 - time.time() % 60 + 2)


def dispatch_alerts():
    """Deliver from MySQL so pending work survives process restarts."""
    while not state.shutdown_flag:
        if not CONFIG.get("enabled", True):
            time.sleep(max(0.25, CONFIG.get("delivery_poll_interval_sec", 1.0)))
            continue
        if not telegram.delivery_authorized:
            # Preserve pending signals in MySQL rather than claiming and burning retry attempts
            # while delivery is unauthorized.
            time.sleep(max(0.5, CONFIG.get("delivery_poll_interval_sec", 1.0)))
            continue
        candidate = claim_next_pending_signal()
        if candidate is None:
            time.sleep(max(0.25, CONFIG.get("delivery_poll_interval_sec", 1.0)))
            continue
        if candidate.parent_watch_id:
            allowed = (persistent_alert_limit_allows(candidate.symbol, candidate.parent_watch_id)
                       and state._can_alert(candidate.symbol, followup=True))
        else:
            allowed = persistent_alert_limit_allows(candidate.symbol) and state._can_alert(candidate.symbol)
        if not allowed:
            release_signal_claim(candidate)
            time.sleep(1.0)
            continue

        # Recheck deadline immediately before the Telegram send, including retries
        now_ms = int(time.time() * 1000)
        max_age_sec = max(1, int(CONFIG.get("max_signal_age_sec", 180)))
        if candidate.parent_watch_id is None and candidate.direction in ("BULLISH", "BEARISH"):
            if momentum.is_watch_expired(candidate.candle_time, now_ms, max_age_sec):
                state.release_alert_slot(candidate.symbol)
                expire_claimed_signal(candidate, momentum.EXPIRED_WATCH_REASON)
                logger.info("Watch expired immediately before Telegram send: %s %s (candle: %s)", candidate.symbol, candidate.direction, candidate.candle_time)
                continue
        elif candidate.parent_watch_id is not None:
            child_start_ms = momentum.utc_ms(candidate.candle_time)
            if now_ms >= child_start_ms + 2 * momentum.MINUTE:
                state.release_alert_slot(candidate.symbol)
                expire_claimed_signal(candidate, "Confirmation superseded by next candle")
                logger.info("Confirmation expired immediately before Telegram send: %s (candle: %s)", candidate.symbol, candidate.candle_time)
                continue

        try:
            sent = telegram.send_early_alert(candidate)
        except Exception as exc:
            # Treat raised transport/client errors like a failed send so the
            # dispatcher survives and the durable queue can retry this signal.
            state.release_alert_slot(candidate.symbol)
            complete_signal_delivery(
                candidate, False, f"Telegram send raised {type(exc).__name__}: {exc}"
            )
            logger.exception(
                "Telegram delivery raised an exception; retry state saved for %s %s",
                candidate.symbol, candidate.signal_type,
            )
            continue

        if sent:
            complete_signal_delivery(candidate, True)
            logger.info("🎯 Durable alert delivered: %s | %s | Quality %.0f | RVOL %.2fx", candidate.symbol, candidate.signal_type, candidate.quality_score, candidate.rvol)
        else:
            state.release_alert_slot(candidate.symbol)
            complete_signal_delivery(candidate, False, "Telegram send returned false")
            logger.warning("Telegram delivery failed; retry state saved for %s %s", candidate.symbol, candidate.signal_type)

def on_message(ws,message): process_kline_message(message)
def on_error(ws,error): logger.error("WebSocket error: %s",error)
def on_close(ws,close_status_code,close_msg): logger.warning("WebSocket closed: code=%s, msg=%s",close_status_code,close_msg)
def on_open(ws): logger.info("✅ WebSocket connection established")

def start_websocket(streams,thread_id,stop_event):
    base_url="wss://stream.binance.com:9443/stream?streams="; reconnect_delay=CONFIG["reconnect_delay_base"]
    while not state.shutdown_flag and not stop_event.is_set():
        try:
            ws=websocket.WebSocketApp(base_url+"/".join(streams),on_message=on_message,on_error=on_error,on_close=on_close,on_open=on_open); logger.info("[Thread-%s] Connecting to %d streams",thread_id,len(streams)); ws.run_forever(ping_interval=CONFIG["ws_ping_interval"],ping_timeout=CONFIG["ws_ping_timeout"])
        except Exception as e: logger.error("[Thread-%s] WebSocket exception: %s",thread_id,e)
        if state.shutdown_flag or stop_event.is_set(): break
        logger.info("[Thread-%s] Reconnecting in %ss...",thread_id,reconnect_delay)
        if stop_event.wait(reconnect_delay): break
        reconnect_delay=min(reconnect_delay*1.5,CONFIG["reconnect_delay_max"])

def signal_handler(signum, frame):
    if state.shutdown_flag:
        return
    logger.info("Received signal %s, stopping the screener", signum)
    state.shutdown_flag = True
    # Interrupt blocking discovery requests as well as the control-loop sleep.
    raise KeyboardInterrupt

def main():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    logger.info("🚀 Smart Money Volume Detector starting (EARLY WARNING MODE)...")
    logger.info(f"Config: RVOL≥{CONFIG['rvol_multiplier']}x | Divergence: ≤{CONFIG['divergence_max_price_change']}% | Velocity: ≥{CONFIG['velocity_threshold']}x | Quality≥{CONFIG.get('min_quality_score',0):.0f} | Selection window={CONFIG.get('alert_selection_window_sec',3):.1f}s | Max {CONFIG['max_alerts_per_hour']} alerts/hour")
    verify_signal_schema(); reload_control()
    try:
        update_screener_heartbeat(pid=os.getpid(), timeframe=CONFIG.get("timeframe", "1m"), symbols_monitored=0)
    except Exception as e:
        logger.warning("⚠️ initial screener heartbeat failed: %s", e)
    threading.Thread(target=confirmation_loop, daemon=True, name="SmartMoney-Confirmation").start()
    cleanup_thread=threading.Thread(target=expired_signal_cleanup_loop,daemon=True,name="SmartMoney-ExpiredCleanup"); cleanup_thread.start(); dispatch_thread=threading.Thread(target=dispatch_alerts,daemon=True,name="SmartMoney-Dispatcher"); dispatch_thread.start(); threads=[]; ws_stop_event=threading.Event(); last_universe_refresh=0.0
    def start_connections(symbols):
        nonlocal threads,ws_stop_event
        # Restore the in-memory EMA from closed candles before subscribing.
        # Otherwise a restart suppresses signals for the 2400-candle warmup.
        bootstrap_ema_history(symbols)
        ws_stop_event=threading.Event(); threads=[]; chunk_size=CONFIG["max_streams_per_conn"]
        logger.info("📡 Monitoring %d symbols for EARLY smart money signals",len(symbols))
        for i,start_idx in enumerate(range(0,len(symbols),chunk_size)):
            chunk=symbols[start_idx:start_idx+chunk_size]; streams=[build_stream_name(s,CONFIG["timeframe"]) for s in chunk]; t=threading.Thread(target=start_websocket,args=(streams,i,ws_stop_event),daemon=True,name=f"SmartMoney-WS-{i}"); t.start(); threads.append(t); time.sleep(0.2)
    def stop_connections():
        nonlocal threads
        ws_stop_event.set()
        deadline = time.monotonic() + 4
        for t in threads:
            t.join(timeout=max(0, deadline - time.monotonic()))
        threads=[]
    try:
        symbols=get_liquid_symbols()
        if symbols:
            start_connections(symbols); last_universe_refresh=time.time()
            try:
                update_screener_heartbeat(pid=os.getpid(), timeframe=CONFIG.get("timeframe", "1m"), symbols_monitored=len(symbols), last_universe_refresh_at=datetime.utcfromtimestamp(last_universe_refresh))
            except Exception as e:
                logger.warning("⚠️ initial universe heartbeat update failed: %s", e)
        else: logger.error("❌ No liquid symbols found - retrying universe discovery in control loop")
        while not state.shutdown_flag:
            time.sleep(60)
            try:
                update_screener_heartbeat(pid=os.getpid(), timeframe=CONFIG.get("timeframe", "1m"), symbols_monitored=len(symbols), last_universe_refresh_at=datetime.utcfromtimestamp(last_universe_refresh) if last_universe_refresh else None)
            except Exception as e:
                logger.warning("⚠️ screener heartbeat update failed: %s", e)
            universe_changed=reload_control(); refresh_due=time.time()-last_universe_refresh>=max(1,int(CONFIG.get("market_cap_refresh_minutes",30)))*60
            if universe_changed or refresh_due:
                reason="config change" if universe_changed else "scheduled market-cap/liquidity refresh"; logger.info("🔄 Rebuilding screener universe (%s)",reason); stop_connections(); symbols=get_liquid_symbols()
                if symbols:
                    start_connections(symbols); last_universe_refresh=time.time()
                    try:
                        update_screener_heartbeat(pid=os.getpid(), symbols_monitored=len(symbols), last_universe_refresh_at=datetime.utcfromtimestamp(last_universe_refresh))
                    except Exception as e:
                        logger.warning("⚠️ screener universe heartbeat update failed: %s", e)
                else:
                    logger.error("❌ Universe refresh returned no eligible symbols; will retry in 60s")
                    last_universe_refresh = 0
    except KeyboardInterrupt: pass
    finally:
        logger.info("🔄 Shutting down..."); state.shutdown_flag=True; ws_stop_event.set(); stop_connections(); dispatch_thread.join(timeout=5); logger.info("✅ Shutdown complete")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass  # Stop requested during initial schema or heartbeat checks.
