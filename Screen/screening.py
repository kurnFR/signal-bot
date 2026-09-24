#!/usr/bin/env python3
"""
Smart Money Volume Detector - Early Warning System
Detects institutional accumulation/distribution BEFORE price breakout.
Uses pure RVOL + volume divergence + velocity analysis for earliest signals.

Professional Logic:
✅ Volume precedes price - alert on smart money entry, not confirmation
✅ High volume + low volatility = accumulation (bullish) or distribution (bearish)
✅ Quote volume (USDT) tracks real capital flow, not token count
✅ Velocity detection catches momentum shifts before candles close
"""

import os
import json
import time
import signal
import logging
import threading
import heapq
import sys
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

# Reuse the main app's shared MySQL connection pool (crypto_signals DB)
# rather than standing up a separate database, so signals this screener
# fires can be read by the web dashboard directly. See db/db.py.
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.db import get_pool


# =========================
# PROFESSIONAL CONFIGURATION
# =========================
CONFIG = {
    # === Web dashboard control (screener_control table) ===
    "enabled": True,  # reload_control() overwrites this from the DB every ~60s

    # === Early Detection Settings ===
    "timeframe": os.getenv("TIMEFRAME", "1m"),  # 1m for fastest detection
    "rvol_multiplier": float(os.getenv("RVOL_MULTIPLIER", "7.0")),  # Lower threshold for early signals
    "ema_length": int(os.getenv("EMA_LENGTH", "7200")),  # Shorter EMA = more responsive
    "velocity_window": int(os.getenv("VELOCITY_WINDOW", "100")),  # Candles to measure volume acceleration
    
    # === Smart Money Signal Types ===
    "enable_divergence_detection": os.getenv("ENABLE_DIVERGENCE", "true").lower() == "true",  # High vol + low price move
    "divergence_max_price_change": float(os.getenv("DIVERGENCE_MAX_PRICE_CHANGE", "0.3")),  # <0.3% move = accumulation signal
    "enable_velocity_detection": os.getenv("ENABLE_VELOCITY", "true").lower() == "true",  # Volume acceleration
    "velocity_threshold": float(os.getenv("VELOCITY_THRESHOLD", "5")),  # 1.5x volume increase vs prior candle
    
    # === Liquidity Filters ===
    "symbols_filter": os.getenv("SYMBOLS_FILTER", "USDT"),
    "min_quote_volume_24h": float(os.getenv("MIN_QUOTE_VOLUME_24H", "100000")),  # $100k min for serious pairs
    "exclude_leveraged": os.getenv("EXCLUDE_LEVERAGED", "true").lower() == "true",
    "exclude_stablecoins": os.getenv("EXCLUDE_STABLECOINS", "true").lower() == "true",
    
    # === Alert Control ===
    "max_alerts_per_hour": int(os.getenv("MAX_ALERTS_PER_HOUR", "5")),  # Prevent spam
    "max_alerts_per_symbol_per_hour": int(os.getenv("MAX_ALERTS_PER_SYMBOL_PER_HOUR", "2")),  # Stop one volatile symbol from burning the whole global budget
    "alert_cooldown_sec": int(os.getenv("ALERT_COOLDOWN_SEC", "120")),  # 2 min per pair
    "daily_reset_utc_hour": int(os.getenv("DAILY_RESET_UTC_HOUR", "0")),
    
    # === Telegram ===
    "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN"),
    "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID"),
    
    # === WebSocket ===
    "ws_ping_interval": 20,
    "ws_ping_timeout": 10,
    "max_streams_per_conn": int(os.getenv("MAX_STREAMS_PER_CONN", "100")),
    "reconnect_delay_base": 5,
    "reconnect_delay_max": 60,
    
    # === Logging ===
    "log_level": os.getenv("LOG_LEVEL", "INFO"),
    "log_file": os.getenv("LOG_FILE", "smart_money_detector.log"),
}

# =========================
# LOGGING SETUP
# =========================
logging.basicConfig(
    level=getattr(logging, CONFIG["log_level"].upper()),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(CONFIG["log_file"], encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("SmartMoneyDetector")

if not CONFIG["telegram_bot_token"] or not CONFIG["telegram_chat_id"]:
    logger.warning("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set -- Telegram alerts disabled (signals will be saved to database)")


# =========================
# DATA CLASSES
# =========================
@dataclass(order=True)
class SmartMoneySignal:
    """Heap-ordered signal for prioritization"""
    priority_score: float  # Negative for max-heap
    symbol: str = field(compare=False)
    signal_type: str = field(compare=False)  # "RVOL_SPIKE", "DIVERGENCE", "VELOCITY"
    rvol: float = field(compare=False)
    volume: float = field(compare=False)
    quote_volume: float = field(compare=False)
    price: float = field(compare=False)
    price_change_pct: float = field(compare=False)
    volume_velocity: float = field(compare=False)
    timestamp: float = field(compare=False)
    candle_time: str = field(compare=False)
    
    @property
    def priority(self) -> float:
        return -self.priority_score

# =========================
# THREAD-SAFE STATE WITH EARLY DETECTION
# =========================
class SmartMoneyState:
    """State management with volume divergence & velocity detection"""
    
    def __init__(self, ema_length: int, rvol_threshold: float, max_alerts_per_hour: int):
        self.lock = threading.Lock()
        self.ema_volume: Dict[str, float] = {}
        self.ema_quote_volume: Dict[str, float] = {}
        self.ema_initialized: Dict[str, int] = defaultdict(int)
        self.last_alert_time: Dict[str, float] = {}
        self.hourly_alert_count: Dict[str, int] = defaultdict(int)
        self.last_hour_reset = None
        
        # Volume history for velocity calculation
        self.volume_history: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=CONFIG["velocity_window"]+1))
        self.quote_volume_history: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=CONFIG["velocity_window"]+1))
        
        # Signal tracking
        self.alpha = 2 / (ema_length + 1)
        self.rvol_threshold = rvol_threshold
        self.warmup_candles = max(10, ema_length // 3)
        self.max_alerts_per_hour = max_alerts_per_hour
        
        self.shutdown_flag = False
        self._check_hourly_reset()
    
    def _check_hourly_reset(self):
        """Reset hourly alert counters"""
        now = datetime.now(timezone.utc)
        hour_key = now.strftime("%Y-%m-%d %H")
        
        if self.last_hour_reset != hour_key:
            with self.lock:
                self.hourly_alert_count.clear()
                self.last_hour_reset = hour_key
                logger.info(f"🔄 Hourly reset: cleared alert counters for {hour_key}")
    
    def update_ema(self, symbol: str, volume: float, quote_volume: float) -> Tuple[bool, float, float]:
        """Update EMA for volume tracking"""
        with self.lock:
            if symbol not in self.ema_volume:
                self.ema_volume[symbol] = volume
                self.ema_quote_volume[symbol] = quote_volume
                self.ema_initialized[symbol] = 1
                return False, volume, quote_volume
            
            if self.ema_initialized[symbol] < self.warmup_candles:
                self.ema_volume[symbol] = (self.alpha * volume) + ((1 - self.alpha) * self.ema_volume[symbol])
                self.ema_quote_volume[symbol] = (self.alpha * quote_volume) + ((1 - self.alpha) * self.ema_quote_volume[symbol])
                self.ema_initialized[symbol] += 1
                return False, self.ema_volume[symbol], self.ema_quote_volume[symbol]
            
            old_ema_vol = self.ema_volume[symbol]
            old_ema_quote = self.ema_quote_volume[symbol]
            
            self.ema_volume[symbol] = (self.alpha * volume) + ((1 - self.alpha) * old_ema_vol)
            self.ema_quote_volume[symbol] = (self.alpha * quote_volume) + ((1 - self.alpha) * old_ema_quote)
            
            return True, self.ema_volume[symbol], self.ema_quote_volume[symbol]
    
    def update_volume_history(self, symbol: str, volume: float, quote_volume: float):
        """Track recent volume for velocity calculation"""
        with self.lock:
            self.volume_history[symbol].append(volume)
            self.quote_volume_history[symbol].append(quote_volume)
    
    def calculate_velocity(self, symbol: str, current_quote_vol: float) -> float:
        """Calculate volume velocity: current vs recent average"""
        with self.lock:
            history = self.quote_volume_history[symbol]
            if len(history) < 2:
                return 1.0
            # Compare current to average of prior N candles
            prior_avg = sum(list(history)[:-1]) / (len(history) - 1)
            return current_quote_vol / prior_avg if prior_avg > 0 else 1.0
    
    def detect_smart_money_signal(self, symbol: str, volume: float, quote_volume: float,
                                 price: float, open_price: float, high: float, low: float,
                                 close_time: str) -> Optional[SmartMoneySignal]:
        """
        Detect early smart money signals:
        1. Pure RVOL spike (any direction)
        2. Volume divergence: high volume + low price move = accumulation/distribution
        3. Volume velocity: sudden acceleration
        """
        self._check_hourly_reset()
        
        is_ready, ema_vol, ema_quote = self.update_ema(symbol, volume, quote_volume)
        if not is_ready:
            return None
        
        # Update volume history for velocity
        self.update_volume_history(symbol, volume, quote_volume)
        
        # Calculate metrics
        rvol = quote_volume / ema_quote if ema_quote > 0 else 0
        price_change_pct = (price - open_price) / open_price * 100 if open_price > 0 else 0
        price_range_pct = (high - low) / open_price * 100 if open_price > 0 else 0
        velocity = self.calculate_velocity(symbol, quote_volume)
        
        signal_type = None
        priority_score = 0.0
        
        # === SIGNAL 1: Pure RVOL Spike (earliest detection) ===
        if rvol >= CONFIG["rvol_multiplier"]:
            signal_type = "RVOL_SPIKE"
            # Priority: higher RVOL = higher priority
            priority_score = rvol * 10
        
        # === SIGNAL 2: Volume Divergence (smart money accumulation) ===
        elif (CONFIG["enable_divergence_detection"] and 
              rvol >= CONFIG["rvol_multiplier"] * 0.8 and  # Slightly lower threshold
              price_range_pct <= CONFIG["divergence_max_price_change"]):
            # High volume but price NOT moving = institutions accumulating quietly
            signal_type = "DIVERGENCE_ACCUMULATION" if price_change_pct >= 0 else "DIVERGENCE_DISTRIBUTION"
            # Priority: high volume + tight range = stronger signal
            priority_score = rvol * 15  # Weight divergence higher
            
        # === SIGNAL 3: Volume Velocity Acceleration ===
        elif (CONFIG["enable_velocity_detection"] and 
              velocity >= CONFIG["velocity_threshold"] and
              rvol >= CONFIG["rvol_multiplier"] * 0.7):
            signal_type = "VELOCITY_SURGE"
            # Priority: faster acceleration = higher priority
            priority_score = velocity * 8
        
        # No signal detected
        if not signal_type:
            return None
        
        # Cooldown & rate limit check
        if not self._can_alert(symbol):
            return None
        
        return SmartMoneySignal(
            priority_score=-priority_score,  # Negative for max-heap
            symbol=symbol,
            signal_type=signal_type,
            rvol=rvol,
            volume=volume,
            quote_volume=quote_volume,
            price=price,
            price_change_pct=price_change_pct,
            volume_velocity=velocity,
            timestamp=time.time(),
            candle_time=close_time
        )
    
    def _can_alert(self, symbol: str) -> bool:
        """Check cooldown and rate limits (both global and per-symbol).

        [FIX] hourly_alert_count[symbol] was being incremented on every
        alert but never actually checked -- only the "global" counter was
        enforced, so max_alerts_per_hour was effectively a *global* cap
        across every monitored symbol combined. On a volatile day, a
        handful of alerts on one or two symbols could silently exhaust the
        entire hour's budget and suppress every other symbol's legitimate
        signal. Both caps now apply: the global one still bounds total
        alert volume, and the new per-symbol one stops any single symbol
        from consuming that whole budget by itself.
        """
        now = time.time()

        with self.lock:
            # Per-symbol cooldown
            last = self.last_alert_time.get(symbol, 0)
            if now - last < CONFIG["alert_cooldown_sec"]:
                return False

            # Hourly global limit
            if self.hourly_alert_count["global"] >= CONFIG["max_alerts_per_hour"]:
                return False

            # Hourly per-symbol limit
            if self.hourly_alert_count[symbol] >= CONFIG["max_alerts_per_symbol_per_hour"]:
                return False

            # Update counters
            self.last_alert_time[symbol] = now
            self.hourly_alert_count["global"] += 1
            self.hourly_alert_count[symbol] += 1
            return True
    
    def register_signal(self, signal: SmartMoneySignal) -> bool:
        """Register signal and return True if should alert"""
        # For early detection: alert immediately on valid signal (no TOP-N delay)
        # But still track for potential duplicate suppression
        return True  # Always alert on valid early signal

state = SmartMoneyState(
    ema_length=CONFIG["ema_length"],
    rvol_threshold=CONFIG["rvol_multiplier"],
    max_alerts_per_hour=CONFIG["max_alerts_per_hour"]
)

# =========================
# TELEGRAM WITH SMART MONEY FORMATTING
# =========================
class SmartMoneyTelegram:
    """Telegram sender with early-signal optimized formatting"""
    
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.url = f"https://api.telegram.org/bot{token}/sendMessage"
        self._last_send = 0
        self._lock = threading.Lock()
        
        self.session = requests.Session()
        retry = Retry(total=3, backoff_factor=0.3, status_forcelist=[429, 500, 502, 503, 504])
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
    
    def send_early_alert(self, signal: SmartMoneySignal) -> bool:
        """Send early warning alert with smart money context"""
        if not self.token or not self.chat_id:
            return False
        with self._lock:

            # Rate limit
            elapsed = time.time() - self._last_send
            if elapsed < 0.8:  # Slightly faster for early signals
                time.sleep(0.8 - elapsed)
            
            # Signal-specific emoji and messaging
            signal_emoji = {
                "RVOL_SPIKE": "⚡",
                "DIVERGENCE_ACCUMULATION": "🤫",
                "DIVERGENCE_DISTRIBUTION": "🚨",
                "VELOCITY_SURGE": "🚀"
            }.get(signal.signal_type, "🔍")
            
            # Smart money interpretation
            if "ACCUMULATION" in signal.signal_type:
                interpretation = "🟢 Institutions accumulating (buying quietly)"
                action_hint = "Watch for upside breakout"
            elif "DISTRIBUTION" in signal.signal_type:
                interpretation = "🔴 Institutions distributing (selling quietly)"
                action_hint = "Watch for downside breakdown"
            elif signal.price_change_pct > 0.5:
                interpretation = "🟢 Strong buying pressure"
                action_hint = "Momentum building"
            elif signal.price_change_pct < -0.5:
                interpretation = "🔴 Strong selling pressure"
                action_hint = "Momentum breaking down"
            else:
                interpretation = "🟡 Unusual volume - watch direction"
                action_hint = "Wait for price confirmation"
            
            message = (
                f"{signal_emoji} <b>SMART MONEY EARLY SIGNAL</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Pair: <b>{signal.symbol}</b>\n"
                f"Signal: <code>{signal.signal_type}</code>\n"
                f"RVOL: <b>{signal.rvol:.2f}x</b> (vs {CONFIG['ema_length']}-EMA)\n"
                f"Velocity: <b>{signal.volume_velocity:.2f}x</b> acceleration\n"
                f"Price: <code>${signal.price:.6f}</code> ({signal.price_change_pct:+.3f}%)\n"
                f"Volume: <code>{signal.volume:,.0f}</code> | Quote: <code>${signal.quote_volume:,.0f}</code>\n"
                f"Time: <code>{signal.candle_time}</code> UTC\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>Interpretation:</b> {interpretation}\n"
                f"<i>Action: {action_hint}</i>\n"
                f"<i>⚠️ Early signal - confirm with your strategy before entry</i>"
            )
            
            try:
                payload = {
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True
                }
                resp = self.session.post(self.url, json=payload, timeout=8)
                resp.raise_for_status()
                self._last_send = time.time()
                logger.info(f"✅ Early alert: {signal.symbol} | {signal.signal_type} | RVOL {signal.rvol:.2f}x")
                return True
            except Exception as e:
                logger.error(f"❌ Telegram error: {e}")
                return False

    def send(self, text: str) -> bool:
        """Generic HTML message send, for control-plane notices (enable/
        disable, config overrides) that aren't tied to a specific
        SmartMoneySignal. Shares the same session/retry/rate-limit as
        send_early_alert()."""
        if not self.token or not self.chat_id:
            return False
        with self._lock:
            elapsed = time.time() - self._last_send
            if elapsed < 0.8:
                time.sleep(0.8 - elapsed)
            try:
                payload = {
                    "chat_id": self.chat_id, "text": text,
                    "parse_mode": "HTML", "disable_web_page_preview": True,
                }
                resp = self.session.post(self.url, json=payload, timeout=8)
                resp.raise_for_status()
                self._last_send = time.time()
                return True
            except Exception as e:
                logger.error(f"❌ Telegram error: {e}")
                return False

telegram = SmartMoneyTelegram(CONFIG["telegram_bot_token"], CONFIG["telegram_chat_id"])

# =========================
# PERSISTENCE (shared crypto_signals DB -- read by the web dashboard)
# =========================
def ensure_signal_table():
    """Create smart_money_signals and screener_control if they don't exist.
    Previously this screener had NO database persistence at all -- it only
    fired Telegram alerts, so there was nothing for a dashboard to read.
    Called once at startup; safe to call repeatedly (CREATE TABLE IF NOT
    EXISTS)."""
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS smart_money_signals (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                symbol VARCHAR(20) NOT NULL,
                signal_type VARCHAR(30) NOT NULL,
                rvol DECIMAL(12,4) NOT NULL,
                volume DECIMAL(30,8) NOT NULL,
                quote_volume DECIMAL(20,2) NOT NULL,
                price DECIMAL(20,8) NOT NULL,
                price_change_pct DECIMAL(10,4) NOT NULL,
                volume_velocity DECIMAL(12,4) NOT NULL,
                candle_time VARCHAR(20) NOT NULL,
                telegram_sent BOOLEAN NOT NULL DEFAULT FALSE,
                detected_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_smsig_symbol_time (symbol, detected_at),
                INDEX idx_smsig_type_time (signal_type, detected_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS screener_control (
                id INT PRIMARY KEY DEFAULT 1,
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                overrides_json TEXT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    ON UPDATE CURRENT_TIMESTAMP,
                CONSTRAINT chk_screener_control_singleton CHECK (id = 1)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cur.execute("INSERT IGNORE INTO screener_control (id, enabled, overrides_json) VALUES (1, TRUE, NULL)")
        conn.commit()
        cur.close()
        logger.info("✅ smart_money_signals / screener_control tables ready")
    except Exception as e:
        logger.error(f"❌ ensure_signal_table failed: {e}")
    finally:
        conn.close()


# Keys safe to change on a running process without corrupting state.
# ema_length/velocity_window/timeframe/symbols_filter etc. are NOT here --
# changing those live would invalidate the already-accumulated EMA/volume
# history rather than just changing behavior going forward.
_TUNABLE_CONFIG_KEYS = {
    "rvol_multiplier", "divergence_max_price_change", "velocity_threshold",
    "enable_divergence_detection", "enable_velocity_detection",
    "max_alerts_per_hour", "max_alerts_per_symbol_per_hour", "alert_cooldown_sec",
}


def reload_control():
    """Poll screener_control and apply enabled flag + safe CONFIG overrides.
    CONFIG is mutated in place (not reassigned), so every place in this
    file that reads CONFIG["..."] picks up the new value immediately --
    no extra plumbing needed. Called once per main-loop tick (~60s)."""
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT enabled, overrides_json FROM screener_control WHERE id=1")
        row = cur.fetchone()
        cur.close()
    except Exception as e:
        logger.error(f"❌ reload_control: {e}")
        return
    finally:
        conn.close()

    if not row:
        return

    was_enabled = CONFIG.get("enabled", True)
    CONFIG["enabled"] = bool(row["enabled"])
    if was_enabled != CONFIG["enabled"]:
        state_str = "ENABLED" if CONFIG["enabled"] else "PAUSED (still detecting, alerts suppressed)"
        logger.info(f"🎛️  Screener {state_str} via dashboard control")
        telegram.send(f"🎛️ <b>Smart Money Screener {state_str}</b> (via web dashboard)")

    overrides = json.loads(row["overrides_json"]) if row["overrides_json"] else {}
    applied = []
    for key, value in overrides.items():
        if key not in _TUNABLE_CONFIG_KEYS:
            logger.warning(f"⚠️  Ignoring non-tunable/unknown screener_control override key: {key}")
            continue
        current = CONFIG.get(key)
        try:
            if isinstance(current, bool):
                new_value = bool(value)
            elif isinstance(current, int) and not isinstance(current, bool):
                new_value = int(value)
            elif isinstance(current, float):
                new_value = float(value)
            else:
                new_value = value
        except (TypeError, ValueError):
            logger.warning(f"⚠️  Could not coerce override {key}={value!r}, skipping")
            continue
        if current != new_value:
            CONFIG[key] = new_value
            applied.append(f"{key}={new_value}")
    if applied:
        logger.info(f"🎛️  Applied config overrides: {', '.join(applied)}")


def save_signal_to_db(signal: SmartMoneySignal, telegram_sent: bool):
    """Persist a fired signal so the web dashboard can display it. Never
    raises -- a DB hiccup here must not stop the screener from continuing
    to detect and alert on signals, which is its primary job."""
    try:
        conn = get_pool().get_connection()
    except Exception as e:
        logger.error(f"❌ save_signal_to_db: could not get connection: {e}")
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO smart_money_signals
                (symbol, signal_type, rvol, volume, quote_volume, price,
                 price_change_pct, volume_velocity, candle_time, telegram_sent)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            signal.symbol, signal.signal_type, signal.rvol, signal.volume,
            signal.quote_volume, signal.price, signal.price_change_pct,
            signal.volume_velocity, signal.candle_time, telegram_sent,
        ))
        conn.commit()
        cur.close()
    except Exception as e:
        logger.error(f"❌ save_signal_to_db failed for {signal.symbol}: {e}")
    finally:
        conn.close()

# =========================
# BINANCE API WITH LIQUIDITY FILTERING
# =========================
def get_liquid_symbols() -> List[str]:
    """Fetch symbols with professional liquidity filtering"""
    url = "https://api.binance.com/api/v3/exchangeInfo"
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        
        candidates = [
            s for s in data["symbols"]
            if s["quoteAsset"] == CONFIG["symbols_filter"]
            and s["status"] == "TRADING"
        ]
        
        # Fetch 24h tickers for volume filtering
        ticker_url = "https://api.binance.com/api/v3/ticker/24hr"
        tickers = {t["symbol"]: t for t in requests.get(ticker_url, timeout=15).json()}
        
        symbols = []
        for s in candidates:
            sym = s["symbol"]
            if CONFIG["exclude_leveraged"] and any(tok in sym for tok in ["UPUSDT", "DOWNUSDT", "BULL", "BEAR"]):
                continue
            if CONFIG["exclude_stablecoins"] and sym.replace(CONFIG["symbols_filter"], "") in ["USDC", "BUSD", "TUSD"]:
                continue
            ticker = tickers.get(sym, {})
            quote_vol = float(ticker.get("quoteVolume", 0))
            if quote_vol >= CONFIG["min_quote_volume_24h"]:
                symbols.append(sym)
        
        logger.info(f"✅ Filtered {len(symbols)} liquid pairs (min ${CONFIG['min_quote_volume_24h']:,.0f} 24h vol)")
        return symbols
        
    except Exception as e:
        logger.error(f"❌ Failed to fetch symbols: {e}")
        return []

def build_stream_name(symbol: str, timeframe: str) -> str:
    return f"{symbol.lower()}@kline_{timeframe}"

# =========================
# EARLY DETECTION MESSAGE PROCESSING
# =========================
def process_kline_message(raw_msg: str):
    """Process kline for early smart money signal detection"""
    try:
        msg = json.loads(raw_msg)
        event = msg["data"] if "data" in msg and "stream" in msg else msg
        
        if event.get("e") != "kline":
            return
        
        kline = event.get("k", {})
        symbol = event.get("s", "").upper()
        
        # Only process CLOSED candles for reliable signals
        if not kline.get("x"):
            return
        
        try:
            volume = float(kline["v"])
            quote_volume = float(kline["q"])  # USDT volume - key for smart money detection
            open_price = float(kline["o"])
            close_price = float(kline["c"])
            high_price = float(kline["h"])
            low_price = float(kline["l"])
            close_time = datetime.fromtimestamp(kline["t"] / 1000, tz=timezone.utc).strftime("%H:%M:%S")
        except (KeyError, ValueError) as e:
            logger.warning(f"Invalid kline data for {symbol}: {e}")
            return
        
        # Detect smart money signal
        signal = state.detect_smart_money_signal(
            symbol=symbol,
            volume=volume,
            quote_volume=quote_volume,
            price=close_price,
            open_price=open_price,
            high=high_price,
            low=low_price,
            close_time=close_time
        )
        
        if signal and state.register_signal(signal):
            screener_enabled = CONFIG.get("enabled", True)
            sent = telegram.send_early_alert(signal) if screener_enabled else False
            save_signal_to_db(signal, telegram_sent=sent)
            if screener_enabled:
                logger.info(f"🎯 EARLY SIGNAL: {signal.symbol} | {signal.signal_type} | RVOL: {signal.rvol:.2f}x | ${signal.price:.4f}")
            else:
                logger.info(f"🔇 Signal detected but suppressed (dashboard paused): {signal.symbol} | {signal.signal_type} -- logged to DB, no Telegram")
                
    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse error: {e}")
    except Exception as e:
        logger.error(f"Error processing message: {e}", exc_info=True)

# =========================
# WEBSOCKET HANDLERS
# =========================
def on_message(ws, message: str):
    process_kline_message(message)

def on_error(ws, error):
    logger.error(f"WebSocket error: {error}")

def on_close(ws, close_status_code, close_msg):
    logger.warning(f"WebSocket closed: code={close_status_code}, msg={close_msg}")

def on_open(ws):
    logger.info("✅ WebSocket connection established")

# =========================
# WEBSOCKET MANAGER
# =========================
def start_websocket(streams: List[str], thread_id: int):
    """Start WebSocket with exponential backoff reconnect"""
    base_url = "wss://stream.binance.com:9443/stream?streams="
    reconnect_delay = CONFIG["reconnect_delay_base"]
    
    while not state.shutdown_flag:
        try:
            url = base_url + "/".join(streams)
            logger.info(f"[Thread-{thread_id}] Connecting to {len(streams)} streams")
            
            ws = websocket.WebSocketApp(
                url,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
                on_open=on_open
            )
            
            ws.run_forever(
                ping_interval=CONFIG["ws_ping_interval"],
                ping_timeout=CONFIG["ws_ping_timeout"]
            )
            
        except Exception as e:
            logger.error(f"[Thread-{thread_id}] WebSocket exception: {e}")
        
        if state.shutdown_flag:
            break
            
        logger.info(f"[Thread-{thread_id}] Reconnecting in {reconnect_delay}s...")
        time.sleep(reconnect_delay)
        reconnect_delay = min(reconnect_delay * 1.5, CONFIG["reconnect_delay_max"])

# =========================
# GRACEFUL SHUTDOWN
# =========================
def signal_handler(signum, frame):
    logger.info(f"🛑 Received signal {signum}, initiating graceful shutdown...")
    state.shutdown_flag = True

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# =========================
# MAIN
# =========================
def main():
    logger.info("🚀 Smart Money Volume Detector starting (EARLY WARNING MODE)...")
    logger.info(f"Config: RVOL≥{CONFIG['rvol_multiplier']}x | Divergence: ≤{CONFIG['divergence_max_price_change']}% price move | "
                f"Velocity: ≥{CONFIG['velocity_threshold']}x | Max {CONFIG['max_alerts_per_hour']} alerts/hour")

    ensure_signal_table()
    reload_control()

    symbols = get_liquid_symbols()
    if not symbols:
        logger.error("❌ No liquid symbols found - exiting")
        return
    
    chunk_size = CONFIG["max_streams_per_conn"]
    threads = []
    
    logger.info(f"📡 Monitoring {len(symbols)} symbols for EARLY smart money signals")
    
    for i, start_idx in enumerate(range(0, len(symbols), chunk_size)):
        chunk = symbols[start_idx:start_idx + chunk_size]
        streams = [build_stream_name(s, CONFIG["timeframe"]) for s in chunk]
        
        t = threading.Thread(
            target=start_websocket,
            args=(streams, i),
            daemon=True,
            name=f"SmartMoney-WS-{i}"
        )
        t.start()
        threads.append(t)
        time.sleep(0.2)  # Minimal stagger for fastest startup
    
    try:
        while not state.shutdown_flag:
            time.sleep(60)
            reload_control()
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("🔄 Shutting down...")
        state.shutdown_flag = True
        for t in threads:
            t.join(timeout=5)
        logger.info("✅ Shutdown complete")

if __name__ == "__main__":
    main()