"""
Central configuration for the crypto signal bot.
Loads secrets from .env, defines symbols/timeframes/endpoints used across
all collectors, backtester, and the future signal engine.
"""
import os

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    # Keep configuration usable when the optional python-dotenv dependency is
    # not installed; environment variables can still be supplied by the shell.
    def load_dotenv(*_args, **_kwargs):
        return False

load_dotenv()

# ---------------------------------------------------------------------------
# Symbols — top 10 by liquidity on Binance USDT-M Futures.
# Edit this list any time; nothing else needs to change.
# ---------------------------------------------------------------------------
SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",

]

# Timeframes to maintain as *exact* Binance klines (not resampled).
# 1m/5m intentionally excluded for now -- current focus (trend_ema_v1 on 1d)
# doesn't need them, and adding them on top of 10 symbols x 5 years would be
# a very large, currently-unneeded backfill (1m alone is ~2.6M candles/symbol
# over 5 years). Add them back later if a faster-timeframe strategy needs them.
TIMEFRAMES = ["15m", "1h", "4h", "1d"]

# Futures data (funding rate, open interest, liquidations, futures klines)
# requires connecting to fstream.binance.com. Binance restricts derivatives
# access from some jurisdictions (e.g. Indonesia, due to OJK/Bappebti rules)
# -- if your futures WS connections keep failing while spot works fine,
# that's usually why. Set to True once you've confirmed futures WS is
# reachable from your network/location.
ENABLE_FUTURES = os.getenv("ENABLE_FUTURES", "false").lower() == "true"

# Which market(s) to collect.
MARKETS = ["spot", "futures"] if ENABLE_FUTURES else ["spot"]

# ---------------------------------------------------------------------------
# Binance REST endpoints
# ---------------------------------------------------------------------------
SPOT_KLINE_URL = "https://api.binance.com/api/v3/klines"
FUTURES_KLINE_URL = "https://fapi.binance.com/fapi/v1/klines"
FUTURES_AGGTRADES_URL = "https://fapi.binance.com/fapi/v1/aggTrades"
FUTURES_OI_URL = "https://fapi.binance.com/fapi/v1/openInterest"
FUTURES_FUNDING_HISTORY_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
# NOTE: Binance only retains ~30 days of history via this endpoint --
# unlike klines/funding rate, there is no way to get years of OI history.
FUTURES_OI_HIST_URL = "https://fapi.binance.com/futures/data/openInterestHist"

# ---------------------------------------------------------------------------
# Binance WebSocket endpoints
# ---------------------------------------------------------------------------
SPOT_WS_BASE = "wss://stream.binance.com:9443"
FUTURES_WS_BASE = "wss://fstream.binance.com"

# Combined-stream base (lets us subscribe to many symbols/timeframes on ONE
# connection instead of opening one socket per symbol — Binance limits
# connections and this is far more efficient).
FUTURES_WS_COMBINED = f"{FUTURES_WS_BASE}/stream?streams="
SPOT_WS_COMBINED = f"{SPOT_WS_BASE}/stream?streams="

FUTURES_MARKPRICE_ARR_STREAM = "!markPrice@arr"   # funding + mark price, all symbols
FUTURES_LIQUIDATION_ARR_STREAM = "!forceOrder@arr"  # liquidations, all symbols

# Fixed UTC offset for WIB (Asia/Jakarta). WIB has no DST, so this never
# needs updating -- used both in generated MySQL columns and in Python-side
# display where relevant.
WIB_UTC_OFFSET_HOURS = 7

# ---------------------------------------------------------------------------
# Backfill settings
# ---------------------------------------------------------------------------
BACKFILL_YEARS = 5
KLINE_FETCH_LIMIT = 1000  # Binance max per request

# ---------------------------------------------------------------------------
# Collector tuning (overridable via .env)
# ---------------------------------------------------------------------------
OI_POLL_INTERVAL_SECONDS = int(os.getenv("OI_POLL_INTERVAL_SECONDS", 60))
FUNDING_WRITE_INTERVAL_SECONDS = int(os.getenv("FUNDING_WRITE_INTERVAL_SECONDS", 60))

# ---------------------------------------------------------------------------
# MySQL
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Strategy / indicator parameters.
# Single source of truth -- the feature engine, backtester (Step 3), and
# live signal engine (Step 6+) all read these from here, never hardcode
# their own copies. Change a parameter once, it applies everywhere.
# ---------------------------------------------------------------------------
VOLUME_MULTIPLIER = 1.2          # volume vs its rolling average to count as "elevated"
VOLUME_SMA_PERIOD = 20           # rolling window for the volume average above
ATR_PERIOD = 14
VOLATILITY_THRESHOLD_PCT = 0.3   # ATR as % of price, minimum to consider "volatile enough"
RSI_WINDOW = 14
RSI_LOOKBACK = 100               # bars of history kept/considered for RSI-based context
HTF_S_R_LOOKBACK_BARS = 50       # bars used to derive rolling support/resistance + fib swing range
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
LEVERAGE_ASSUMPTION = 20
LIQ_BIN_SIZE = 100
RISK_REWARD_RATIO = 2

# ---------------------------------------------------------------------------
# Signal thresholds -- used by the strategy logic (backtest AND live, once
# built) to decide entries. Kept separate from the indicator params above
# since these are trading-decision thresholds, not calculation windows.
# ---------------------------------------------------------------------------
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
SR_PROXIMITY_PCT = 0.5   # how close price must be to support/resistance to count as "at the level"

# ---------------------------------------------------------------------------
# Backtest execution assumptions -- these model real trading costs/limits
# so backtested performance isn't unrealistically optimistic.
# ---------------------------------------------------------------------------
BACKTEST_FEE_PCT = 0.001        # 0.1% per side (Binance spot taker default)
BACKTEST_SLIPPAGE_PCT = 0.0005  # 0.05% assumed slippage per side
BACKTEST_MAX_HOLD_BARS = 200    # force-close a trade if neither SL nor TP hit within this many bars

# ---------------------------------------------------------------------------
# Alternative strategy parameters (Step 3 comparison: mean-reversion vs
# breakout vs trend-following). Trend/breakout strategies conventionally
# use a wider risk:reward than mean-reversion since they're expected to win
# less often but for a bigger move when right.
# ---------------------------------------------------------------------------
BREAKOUT_RISK_REWARD_RATIO = 3
TREND_EMA_FAST = 20
TREND_EMA_SLOW = 50
TREND_RISK_REWARD_RATIO = 3

# Breakout-specific: the shared 50-bar support/resistance (used by
# mean_reversion) turned out too reactive for breakout detection -- price
# pokes marginally above it constantly in normal chop, producing 900+
# "breakout" trades on 15m over 2 years (clearly mostly noise, not genuine
# breaks). Breakout uses its own longer lookback + minimum margin instead.
BREAKOUT_LOOKBACK_BARS = 100
BREAKOUT_MIN_MARGIN_PCT = 0.15   # price must clear the level by this % to count as a genuine break

# ---------------------------------------------------------------------------
# Train/holdout split. Everything under Step 3/4 development (run_backtest,
# walk_forward) operates ONLY on the train portion by default -- the
# holdout portion (most recent HOLDOUT_FRACTION of history) is reserved and
# checked only once, deliberately, via backtest/run_holdout_check.py, after
# a strategy has been fully decided on. This guards against the multiple-
# comparisons trap: the more strategy variants tested against the same
# data, the more likely one looks good purely by chance.
# ---------------------------------------------------------------------------
HOLDOUT_FRACTION = 0.2

# ---------------------------------------------------------------------------
# Fibonacci retracement strategy (reuses the shared swing/fib columns
# already computed in `features` -- no separate lookback needed).
# ---------------------------------------------------------------------------
FIB_RISK_REWARD_RATIO = 2.5

# ---------------------------------------------------------------------------
# Supply & demand zone strategy: a small-bodied "base" candle immediately
# followed by a strong "displacement" candle marks a zone; price returning
# to that zone is expected to react.
# ---------------------------------------------------------------------------
SD_BASE_MAX_ATR_MULT = 0.3        # base candle body must be <= this x ATR to count as consolidation
SD_DISPLACEMENT_ATR_MULT = 1.0    # the following candle's body must be >= this x ATR to count as a real move
SD_MAX_ZONE_AGE_BARS = 100        # a zone expires (stops being tradable) after this many bars unmitigated
SD_RISK_REWARD_RATIO = 2.5

# ---------------------------------------------------------------------------
# Smart Money Concepts (SMC) liquidity-sweep strategy: a wick beyond recent
# support/resistance that gets rejected (closes back inside) -- interpreted
# as a stop-hunt/liquidity grab before a reversal.
# ---------------------------------------------------------------------------
SMC_WICK_MIN_ATR_MULT = 0.3       # the sweeping wick must be at least this x ATR to count as meaningful
SMC_RISK_REWARD_RATIO = 2.5

# ---------------------------------------------------------------------------
# PnL / equity curve simulation. Converts a backtest's R-multiples into
# actual dollar terms using fixed-fractional position sizing: every trade
# risks RISK_PER_TRADE_PCT of CURRENT equity (compounding), regardless of
# that trade's own stop distance -- standard practice, and mathematically
# consistent with the R-multiples already computed (r_multiple = return% /
# risk%, so dollar_pnl = equity * risk_pct * r_multiple exactly reproduces
# what sizing off that trade's real entry/stop distance would have given).
# ---------------------------------------------------------------------------
INITIAL_CAPITAL_USD = 5000
RISK_PER_TRADE_PCT = 1.0

# ---------------------------------------------------------------------------
# Double top / double bottom pattern strategy.
# ---------------------------------------------------------------------------
PIVOT_LOOKBACK_BARS = 5             # bars on each side required to confirm a swing pivot
DOUBLE_PATTERN_TOLERANCE_PCT = 1.0  # how close the 2nd pivot must be to the 1st, in %
DOUBLE_PATTERN_MAX_BARS_APART = 60  # max bars between the two pivots to count as the same pattern
DOUBLE_PATTERN_RISK_REWARD_RATIO = 2.5

# ---------------------------------------------------------------------------
# Trailing stop (opt-in via params["USE_TRAILING_STOP"]=True on a given
# backtest.optimize.py run or strategy call -- default OFF, existing fixed
# SL/TP behavior is unchanged unless explicitly enabled). Once a trade's
# unrealized profit reaches TRAIL_ACTIVATION_R x its initial risk, the stop
# trails behind the best price by TRAIL_DISTANCE_ATR_MULT x ATR and the
# fixed take-profit is removed (upside uncapped).
# ---------------------------------------------------------------------------
USE_TRAILING_STOP = False
TRAIL_ACTIVATION_R = 1.0
TRAIL_DISTANCE_ATR_MULT = 1.5

# ---------------------------------------------------------------------------
# Funding rate extreme-reversal strategy. Funding rate reflects aggregate
# futures positioning even when trading spot -- extreme positive funding
# means longs are paying a lot to stay long (crowded/over-leveraged long
# positioning, often preceding a downward correction); extreme negative
# funding means the opposite (crowded shorts, often preceding a squeeze
# upward). Thresholds are per 8-hour settlement period, as Binance reports it.
# ---------------------------------------------------------------------------
FUNDING_EXTREME_POSITIVE_PCT = 0.05   # % per 8h -- fade longs above this
FUNDING_EXTREME_NEGATIVE_PCT = -0.05  # % per 8h -- fade shorts below this
FUNDING_RISK_REWARD_RATIO = 2.5

# ---------------------------------------------------------------------------
# Confluence/ensemble strategy: combines several independently weak signals
# into one weighted score, trading only when enough of them agree. Each
# component contributes -1 (bearish) / 0 (neutral) / +1 (bullish), weighted
# and summed; MIN_CONFLUENCE_SCORE is the threshold (in either direction)
# required to trade. Weights default to 1.0 each (6 components -> max |score|=6).
# ---------------------------------------------------------------------------
MIN_CONFLUENCE_SCORE = 3.0
CONFLUENCE_WEIGHT_RSI = 1.0
CONFLUENCE_WEIGHT_TREND = 1.0
CONFLUENCE_WEIGHT_MACD = 1.0
CONFLUENCE_WEIGHT_VOLUME = 1.0
CONFLUENCE_WEIGHT_SR = 1.0
CONFLUENCE_WEIGHT_FUNDING = 1.0
CONFLUENCE_RISK_REWARD_RATIO = 2.5

# ---------------------------------------------------------------------------
# Pairs / relative-value strategy: trades the RATIO between a symbol and a
# base/quote leg (default BTCUSDT) reverting to its recent mean, rather
# than either asset's absolute price direction. Genuinely different signal
# source than every price-pattern or positioning strategy tried so far.
# ---------------------------------------------------------------------------
PAIRS_BASE_SYMBOL = "BTCUSDT"
PAIRS_ZSCORE_LOOKBACK_BARS = 50
PAIRS_ZSCORE_ENTRY_THRESHOLD = 2.0
PAIRS_RISK_REWARD_RATIO = 2.5

VOL_BREAKOUT_LOOKBACK_BARS = 100
VOL_BREAKOUT_ATR_BASELINE_BARS = 50
VOL_BREAKOUT_ATR_EXPANSION_MULT = 1.1
VOL_BREAKOUT_MIN_MARGIN_PCT = 0.15
VOL_BREAKOUT_EMA_PERIOD = 100
VOL_BREAKOUT_RISK_REWARD_RATIO = 3

MYSQL_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "port": int(os.getenv("MYSQL_PORT", 3306)),
    "user": os.getenv("MYSQL_USER", "crypto_bot"),
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "database": os.getenv("MYSQL_DATABASE", "crypto_signals"),
}
