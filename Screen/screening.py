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
    "alert_selection_window_sec": float(os.getenv("ALERT_SELECTION_WINDOW_SEC", "3")),
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

# Immutable baseline loaded from environment at process startup. Dashboard overrides
# are intentionally reapplied from this baseline on every control-loop tick so that
# removing an override really restores the configured environment default.
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

    def detect_smart_money_signal(self, symbol, volume, quote_volume, price, open_price, high, low, close_time):
        self._check_hourly_reset()
        is_ready, ema_vol, ema_quote = self.update_ema(symbol, volume, quote_volume)
        if not is_ready: return None
        self.update_volume_history(symbol, volume, quote_volume)
        rvol = quote_volume / ema_quote if ema_quote > 0 else 0
        price_change_pct = (price - open_price) / open_price * 100 if open_price > 0 else 0
        price_range_pct = (high - low) / open_price * 100 if open_price > 0 else 0
        velocity = self.calculate_velocity(symbol, quote_volume)
        signal_type = None; base_priority = 0.0
        if rvol >= CONFIG["rvol_multiplier"]:
            signal_type = "RVOL_SPIKE"; base_priority = rvol * 10
        elif CONFIG["enable_divergence_detection"] and rvol >= CONFIG["rvol_multiplier"] * 0.8 and price_range_pct <= CONFIG["divergence_max_price_change"]:
            signal_type = "DIVERGENCE_ACCUMULATION" if price_change_pct >= 0 else "DIVERGENCE_DISTRIBUTION"; base_priority = rvol * 15
        elif CONFIG["enable_velocity_detection"] and velocity >= CONFIG["velocity_threshold"] and rvol >= CONFIG["rvol_multiplier"] * 0.7:
            signal_type = "VELOCITY_SURGE"; base_priority = velocity * 8
        if not signal_type: return None
        rvol_score = min(rvol / max(CONFIG["min_signal_rvol"], 1.0), 2.0) * 25.0
        velocity_score = min(velocity / max(CONFIG["min_signal_velocity"], 1.0), 2.0) * 20.0
        tightness_score = max(0.0, 1.0 - price_range_pct / max(CONFIG["divergence_max_price_change"], 0.0001)) * 20.0 if CONFIG["enable_divergence_detection"] else 0.0
        movement_score = min(abs(price_change_pct), 1.0) * 10.0
        quality_score = min(100.0, rvol_score + velocity_score + tightness_score + movement_score)
        if quality_score < CONFIG.get("min_quality_score", 0): return None
        if CONFIG.get("require_confluence", True):
            conditions = int(rvol >= CONFIG["min_signal_rvol"]) + int(velocity >= CONFIG["min_signal_velocity"]) + int(price_range_pct <= CONFIG["divergence_max_price_change"] and rvol >= CONFIG["rvol_multiplier"] * 0.8)
            if conditions < 2: return None
        signal_key = (symbol, signal_type)
        with self.lock:
            if self.seen_signal_candles.get(signal_key) == close_time: return None
            self.seen_signal_candles[signal_key] = close_time
        ranking = quality_score * 1000.0 + base_priority
        return SmartMoneySignal(-ranking, symbol, signal_type, rvol, volume, quote_volume, price, price_change_pct, velocity, time.time(), close_time, quality_score)

    def register_signal(self, signal):
        key = (signal.symbol, signal.signal_type, signal.candle_time)
        with self.lock:
            if key in self.pending_keys: return False
            self.pending_keys.add(key); heapq.heappush(self.pending_signals, signal); return True

    def _can_alert(self, symbol):
        now = time.time()
        with self.lock:
            self._check_hourly_reset()
            if now - self.last_alert_time.get(symbol, 0) < CONFIG["alert_cooldown_sec"]: return False
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
            while self.pending_signals:
                candidate=heapq.heappop(self.pending_signals); key=(candidate.symbol,candidate.signal_type,candidate.candle_time); self.pending_keys.discard(key)
                if not CONFIG.get("enabled",True): deferred.append(candidate); continue
                if self._can_alert(candidate.symbol): selected=candidate; break
                deferred.append(candidate)
            for candidate in deferred:
                key=(candidate.symbol,candidate.signal_type,candidate.candle_time)
                if key not in self.pending_keys: self.pending_keys.add(key); heapq.heappush(self.pending_signals,candidate)
            return selected

state=SmartMoneyState(CONFIG["ema_length"],CONFIG["rvol_multiplier"],CONFIG["max_alerts_per_hour"])

class SmartMoneyTelegram:
    def __init__(self, token, chat_id):
        self.token=token; self.chat_id=chat_id; self.url=f"https://api.telegram.org/bot{token}/sendMessage" if token else ""; self._last_send=0; self._lock=threading.Lock(); self.session=requests.Session()
        self.session.mount("https://",HTTPAdapter(max_retries=Retry(total=3,backoff_factor=0.3,status_forcelist=[429,500,502,503,504])))
    def send_early_alert(self, signal):
        if not self.token or not self.chat_id: return False
        with self._lock:
            elapsed=time.time()-self._last_send
            if elapsed<0.8: time.sleep(0.8-elapsed)
            emoji={"RVOL_SPIKE":"⚡","DIVERGENCE_ACCUMULATION":"🤫","DIVERGENCE_DISTRIBUTION":"🚨","VELOCITY_SURGE":"🚀"}.get(signal.signal_type,"🔍")
            if "ACCUMULATION" in signal.signal_type: interpretation,action_hint="🟢 Possible accumulation (high volume, limited price movement)","Watch for upside breakout"
            elif "DISTRIBUTION" in signal.signal_type: interpretation,action_hint="🔴 Possible distribution (high volume, limited price movement)","Watch for downside breakdown"
            elif signal.price_change_pct>0.5: interpretation,action_hint="🟢 Strong buying pressure","Momentum building"
            elif signal.price_change_pct<-0.5: interpretation,action_hint="🔴 Strong selling pressure","Momentum breaking down"
            else: interpretation,action_hint="🟡 Unusual volume - watch direction","Wait for price confirmation"
            message=(f"{emoji} <b>SMART MONEY EARLY SIGNAL</b>\n━━━━━━━━━━━━━━━━━━━━━━\nPair: <b>{escape(str(signal.symbol))}</b>\nSignal: <code>{escape(str(signal.signal_type))}</code>\nQuality: <b>{signal.quality_score:.0f}/100</b>\nRVOL: <b>{signal.rvol:.2f}x</b> (vs {CONFIG['ema_length']}-EMA)\nVelocity: <b>{signal.volume_velocity:.2f}x</b> acceleration\nPrice: <code>${signal.price:.6f}</code> ({signal.price_change_pct:+.3f}%)\nVolume: <code>{signal.volume:,.0f}</code> | Quote: <code>${signal.quote_volume:,.0f}</code>\nTime: <code>{escape(str(signal.candle_time))}</code> UTC\n━━━━━━━━━━━━━━━━━━━━━━\n<b>Interpretation:</b> {interpretation}\n<i>Action: {action_hint}</i>\n<i>⚠️ Early signal - confirm with your strategy before entry</i>")
            try:
                resp=self.session.post(self.url,json={"chat_id":self.chat_id,"text":message,"parse_mode":"HTML","disable_web_page_preview":True},timeout=8); resp.raise_for_status(); self._last_send=time.time(); logger.info("✅ Early alert: %s | %s | Quality %.0f | RVOL %.2fx",signal.symbol,signal.signal_type,signal.quality_score,signal.rvol); return True
            except Exception as e: logger.error("❌ Telegram error: %s",e); return False
    def send(self,text):
        if not self.token or not self.chat_id: return False
        with self._lock:
            elapsed=time.time()-self._last_send
            if elapsed<0.8: time.sleep(0.8-elapsed)
            try:
                resp=self.session.post(self.url,json={"chat_id":self.chat_id,"text":text,"parse_mode":"HTML","disable_web_page_preview":True},timeout=8); resp.raise_for_status(); self._last_send=time.time(); return True
            except Exception as e: logger.error("❌ Telegram error: %s",e); return False

telegram=SmartMoneyTelegram(CONFIG["telegram_bot_token"],CONFIG["telegram_chat_id"])


def ensure_signal_table():
    conn=get_pool().get_connection()
    try:
        cur=conn.cursor(); cur.execute("""CREATE TABLE IF NOT EXISTS smart_money_signals (id BIGINT AUTO_INCREMENT PRIMARY KEY, symbol VARCHAR(20) NOT NULL, signal_type VARCHAR(30) NOT NULL, rvol DECIMAL(12,4) NOT NULL, volume DECIMAL(30,8) NOT NULL, quote_volume DECIMAL(20,2) NOT NULL, price DECIMAL(20,8) NOT NULL, price_change_pct DECIMAL(10,4) NOT NULL, volume_velocity DECIMAL(12,4) NOT NULL, quality_score DECIMAL(6,2) NOT NULL DEFAULT 0, candle_time VARCHAR(32) NOT NULL, telegram_sent BOOLEAN NOT NULL DEFAULT FALSE, detected_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, INDEX idx_smsig_symbol_time (symbol, detected_at), INDEX idx_smsig_type_time (signal_type, detected_at), UNIQUE KEY uq_smsig_candle (symbol, signal_type, candle_time)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        cur.execute("ALTER TABLE smart_money_signals ADD COLUMN IF NOT EXISTS quality_score DECIMAL(6,2) NOT NULL DEFAULT 0")
        cur.execute("""CREATE TABLE IF NOT EXISTS screener_control (id INT PRIMARY KEY DEFAULT 1, enabled BOOLEAN NOT NULL DEFAULT TRUE, overrides_json TEXT NULL, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, CONSTRAINT chk_screener_control_singleton CHECK (id = 1)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        cur.execute("""CREATE TABLE IF NOT EXISTS screener_heartbeat (id INT PRIMARY KEY DEFAULT 1, pid INT NULL, last_beat_at DATETIME NULL, timeframe VARCHAR(20) NULL, symbols_monitored INT NOT NULL DEFAULT 0, last_signal_at DATETIME NULL, last_universe_refresh_at DATETIME NULL, CONSTRAINT chk_screener_heartbeat_singleton CHECK (id = 1)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        cur.execute("ALTER TABLE screener_heartbeat ADD COLUMN IF NOT EXISTS timeframe VARCHAR(20) NULL")
        cur.execute("INSERT IGNORE INTO screener_heartbeat (id) VALUES (1)")
        cur.execute("INSERT IGNORE INTO screener_control (id,enabled,overrides_json) VALUES (1,TRUE,NULL)"); conn.commit(); cur.close(); logger.info("✅ screener persistence tables ready")
    except Exception as e: logger.error("❌ ensure_signal_table failed: %s",e)
    finally: conn.close()

_TUNABLE_CONFIG_KEYS={"rvol_multiplier","divergence_max_price_change","velocity_threshold","enable_divergence_detection","enable_velocity_detection","max_alerts_per_hour","max_alerts_per_symbol_per_hour","alert_cooldown_sec","require_confluence","min_signal_rvol","min_signal_velocity","min_quote_volume_24h","min_market_cap_usd","min_quality_score"}

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

def save_signal_to_db(signal,telegram_sent):
    try:
        update_screener_heartbeat(last_signal_at=signal.candle_time)
    except Exception as e:
        logger.warning("⚠️ screener heartbeat signal update failed: %s", e)
    try: conn=get_pool().get_connection()
    except Exception as e: logger.error("❌ save_signal_to_db connection: %s",e); return
    try:
        cur=conn.cursor(); cur.execute("""INSERT INTO smart_money_signals (symbol,signal_type,rvol,volume,quote_volume,price,price_change_pct,volume_velocity,quality_score,candle_time,telegram_sent) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE quality_score=GREATEST(quality_score,VALUES(quality_score)),telegram_sent=telegram_sent OR VALUES(telegram_sent)""",(signal.symbol,signal.signal_type,signal.rvol,signal.volume,signal.quote_volume,signal.price,signal.price_change_pct,signal.volume_velocity,signal.quality_score,signal.candle_time,telegram_sent)); conn.commit(); cur.close()
    except Exception as e: logger.error("❌ save_signal_to_db failed for %s: %s",signal.symbol,e)
    finally: conn.close()

def get_market_caps():
    if CONFIG["min_market_cap_usd"]<=0: return {}
    ticker_caps=defaultdict(set)
    try:
        for page in range(1,max(1,CONFIG.get("market_cap_pages",5))+1):
            resp=requests.get("https://api.coingecko.com/api/v3/coins/markets",params={"vs_currency":"usd","order":"market_cap_desc","per_page":250,"page":page,"sparkline":"false"},timeout=20); resp.raise_for_status(); rows=resp.json()
            if not rows: break
            for row in rows:
                ticker=str(row.get("symbol","")).upper(); cap=float(row.get("market_cap") or 0)
                if ticker and cap>0: ticker_caps[ticker].add(cap)
            if len(rows)<250: break
            time.sleep(0.2)
        caps={ticker:next(iter(values)) for ticker,values in ticker_caps.items() if len(values)==1}; logger.info("Loaded market-cap metadata for %d unambiguous tickers; excluded %d ambiguous tickers",len(caps),len(ticker_caps)-len(caps)); return caps
    except Exception as e: logger.warning("Market-cap metadata unavailable; unknown assets will be excluded: %s",e); return {}

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
        volume=float(kline["v"]); quote_volume=float(kline["q"]); open_price=float(kline["o"]); close_price=float(kline["c"]); high_price=float(kline["h"]); low_price=float(kline["l"]); close_time=datetime.fromtimestamp(kline["t"]/1000,tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        candidate=state.detect_smart_money_signal(symbol,volume,quote_volume,close_price,open_price,high_price,low_price,close_time)
        if candidate and state.register_signal(candidate): save_signal_to_db(candidate,telegram_sent=False); logger.info("📥 Candidate queued: %s | %s | Quality %.0f | RVOL %.2fx",candidate.symbol,candidate.signal_type,candidate.quality_score,candidate.rvol)
    except (json.JSONDecodeError,KeyError,ValueError,TypeError) as e: logger.warning("Invalid kline message: %s",e)
    except Exception as e: logger.error("Error processing message: %s",e,exc_info=True)

def dispatch_alerts():
    while not state.shutdown_flag:
        if not state.pending_signals:
            time.sleep(0.25); continue
        window=max(0.0,CONFIG.get("alert_selection_window_sec",3.0)); deadline=time.monotonic()+window
        while not state.shutdown_flag and time.monotonic()<deadline: time.sleep(min(0.1,max(0.0,deadline-time.monotonic())))
        candidate=state.pop_best_eligible()
        if candidate is None: continue
        sent=telegram.send_early_alert(candidate) if CONFIG.get("enabled",True) else False
        if not sent: state.release_alert_slot(candidate.symbol)
        save_signal_to_db(candidate,telegram_sent=sent)
        logger.info("🎯 Selected alert: %s | %s | Quality %.0f | RVOL %.2fx | pending=%d | sent=%s",candidate.symbol,candidate.signal_type,candidate.quality_score,candidate.rvol,len(state.pending_signals),sent)

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

def signal_handler(signum,frame): logger.info("🛑 Received signal %s, initiating graceful shutdown...",signum); state.shutdown_flag=True
signal.signal(signal.SIGINT,signal_handler); signal.signal(signal.SIGTERM,signal_handler)

def main():
    logger.info("🚀 Smart Money Volume Detector starting (EARLY WARNING MODE)...")
    logger.info(f"Config: RVOL≥{CONFIG['rvol_multiplier']}x | Divergence: ≤{CONFIG['divergence_max_price_change']}% | Velocity: ≥{CONFIG['velocity_threshold']}x | Quality≥{CONFIG.get('min_quality_score',0):.0f} | Selection window={CONFIG.get('alert_selection_window_sec',3):.1f}s | Max {CONFIG['max_alerts_per_hour']} alerts/hour")
    ensure_signal_table(); reload_control()
    try:
        update_screener_heartbeat(pid=os.getpid(), timeframe=CONFIG.get("timeframe", "1m"), symbols_monitored=0)
    except Exception as e:
        logger.warning("⚠️ initial screener heartbeat failed: %s", e)
    dispatch_thread=threading.Thread(target=dispatch_alerts,daemon=True,name="SmartMoney-Dispatcher"); dispatch_thread.start(); threads=[]; ws_stop_event=threading.Event(); last_universe_refresh=0.0
    def start_connections(symbols):
        nonlocal threads,ws_stop_event
        ws_stop_event=threading.Event(); threads=[]; chunk_size=CONFIG["max_streams_per_conn"]
        logger.info("📡 Monitoring %d symbols for EARLY smart money signals",len(symbols))
        for i,start_idx in enumerate(range(0,len(symbols),chunk_size)):
            chunk=symbols[start_idx:start_idx+chunk_size]; streams=[build_stream_name(s,CONFIG["timeframe"]) for s in chunk]; t=threading.Thread(target=start_websocket,args=(streams,i,ws_stop_event),daemon=True,name=f"SmartMoney-WS-{i}"); t.start(); threads.append(t); time.sleep(0.2)
    def stop_connections():
        nonlocal threads
        ws_stop_event.set()
        for t in threads: t.join(timeout=5)
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
                else: logger.error("❌ Universe refresh returned no eligible symbols; keeping screener disconnected until next refresh"); last_universe_refresh=time.time()
    except KeyboardInterrupt: pass
    finally:
        logger.info("🔄 Shutting down..."); state.shutdown_flag=True; ws_stop_event.set(); stop_connections(); dispatch_thread.join(timeout=5); logger.info("✅ Shutdown complete")

if __name__ == "__main__": main()
