"""
News AI Overlay strategy -- Phase 3: execution.

Reads high-confidence, not-yet-acted-on signals from `news_ai_signals`
(written by ai/news_strategy_engine.py, Phase 2), applies the guardrails from
NEWS_AI_STRATEGY_PLAN.md §4, and for signals that pass:
  1. computes entry/stop/target using the same ATR + R-multiple conventions
     as the technical strategies
  2. opens a real paper position through paper.engine.insert_paper_position()
     -- the same accounting path (position sizing, fees) every other
     strategy uses
  3. sends a Telegram alert via notifications.telegram_notifier.send_news_signal_alert()

Every signal this module looks at gets a final disposition recorded via
mark_news_signal_acted() -- either a paper_position_id (trade opened) or a
skip_reason (confluence block, daily cap, still waiting on a scheduled
release, market data unavailable, etc.) -- so nothing is silently dropped
and nothing gets retried forever.

Guardrails implemented here (see NEWS_AI_STRATEGY_PLAN.md §4):
  - Confluence: blocked if the symbol's active technical strategy already
    has an open position (no stacking independent, possibly conflicting
    positions on the same underlying asset).
  - Daily cap: NEWS_AI_MAX_TRADES_PER_DAY, regardless of how many
    high-confidence signals fired.
  - Don't trade the pre-release gap: if trade_timing == "wait_for_reaction"
    and the underlying macro event hasn't actually released yet
    (actual_value still empty), the signal is left un-acted and retried
    next cycle rather than skipped outright -- it may become tradeable once
    the print lands.
  - Circuit breakers: reuses paper.circuit_breaker.check_circuit_breakers(),
    the same account-wide max-daily-loss / max-concurrent-positions /
    emergency-stop guard the technical strategies already go through.

Usage:
    python3 -m ai.news_execution          # continuous poll loop
    python3 -m ai.news_execution --once    # single pass, for cron/testing
"""
import logging
import sys
import os
import time
import argparse

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    NEWS_AI_EXECUTION_POLL_INTERVAL_SECONDS, NEWS_AI_MAX_TRADES_PER_DAY,
    NEWS_AI_STOP_ATR_MULT, NEWS_AI_TP_R_MULTIPLE, NEWS_AI_DEFAULT_TP_R_MULTIPLE,
)
from db.db import (
    get_unacted_news_signals, get_news_event, mark_news_signal_acted,
    count_news_trades_opened_today, get_technical_paper_config, ensure_overlay_paper_config,
    fetch_ohlcv_with_features_df, heartbeat, claim_news_signal, release_news_signal_claim,
)
from paper.engine import get_active_positions, insert_paper_position, OVERLAY_STRATEGIES
from paper.circuit_breaker import check_circuit_breakers
from notifications.telegram_notifier import send_news_signal_alert

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("news_execution")

STRATEGY_NAME = "news_ai_overlay"
assert STRATEGY_NAME in OVERLAY_STRATEGIES  # keep paper/engine.py and this module in sync


def _pending_on_release(signal, event):
    """True if this signal should wait -- it's tied to a scheduled macro
    event that hasn't actually released yet."""
    if signal.get("trade_timing") != "wait_for_reaction":
        return False
    if not event:
        return False
    if event.get("category") != "macro_calendar":
        return False
    # Some free calendar feeds never populate actual_value. Once the scheduled
    # time has passed, the event is no longer a pre-release signal even if the
    # provider has not supplied the actual figure yet.
    if event.get("actual_value"):
        return False
    scheduled_at = event.get("scheduled_at")
    if not scheduled_at:
        return False
    try:
        from datetime import datetime, timezone
        if isinstance(scheduled_at, str):
            scheduled_at = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
        return scheduled_at > datetime.now(timezone.utc)
    except (TypeError, ValueError):
        # Fail closed when a provider gives an unusable timestamp.
        return True


def _has_open_technical_position(symbol, market, timeframe):
    """Confluence guardrail: is there already an open position from the
    active *technical* strategy on this exact symbol/market/timeframe?"""
    for pos in get_active_positions():
        if (
            pos["symbol"] == symbol and pos["market"] == market
            and pos["timeframe"] == timeframe and pos["strategy_name"] != STRATEGY_NAME
        ):
            return True
    return False


def _compute_trade(symbol, market, timeframe, bias, time_horizon):
    """Entry = latest close, stop = ATR-based in the signal's direction,
    target = R-multiple by time_horizon. Returns None if market data isn't
    available/fresh enough to trade off of."""
    df = fetch_ohlcv_with_features_df(symbol, market, timeframe, closed_only=True, limit=50)
    if df is None or len(df) < 5:
        return None
    latest = df.iloc[-1]
    atr = latest.get("atr")
    if atr is None or atr != atr or float(atr) <= 0:  # NaN/zero check
        return None
    atr = float(atr)
    entry_price = float(latest["close"])
    candle_open_time = int(latest["open_time"])
    direction = "LONG" if bias == "long" else "SHORT"
    r_mult = NEWS_AI_TP_R_MULTIPLE.get(time_horizon, NEWS_AI_DEFAULT_TP_R_MULTIPLE)

    stop_distance = NEWS_AI_STOP_ATR_MULT * atr
    if direction == "LONG":
        stop_loss = entry_price - stop_distance
        take_profit = entry_price + stop_distance * r_mult
    else:
        stop_loss = entry_price + stop_distance
        take_profit = entry_price - stop_distance * r_mult

    return {
        "direction": direction,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "atr_at_signal": atr,
        "current_close": entry_price,
        "candle_open_time": candle_open_time,
    }


def process_signal(signal):
    """Process one signal with an atomic DB claim.

    Multiple execution workers may poll at the same time; only the worker
    that successfully claims the signal is allowed to make the final
    disposition. A claim is released only for the deliberate pending-macro
    retry path.
    """
    if not claim_news_signal(signal["id"]):
        return False

    symbol, market, timeframe = signal["symbol"], signal.get("market"), signal.get("timeframe")
    if not market or not timeframe:
        # Signals written before the Phase 3 migration added these columns.
        mark_news_signal_acted(signal["id"], skip_reason="missing_market_timeframe")
        return False

    event = get_news_event(signal["news_event_id"]) if signal.get("news_event_id") else None
    if _pending_on_release(signal, event):
        logger.info(f"{symbol}: signal #{signal['id']} waiting on scheduled release, retrying later")
        release_news_signal_claim(signal["id"])
        return False  # deliberately not marked acted -- retry next cycle

    if _has_open_technical_position(symbol, market, timeframe):
        logger.info(f"{symbol}: signal #{signal['id']} blocked -- technical strategy already has an open position")
        mark_news_signal_acted(signal["id"], skip_reason="confluence_blocked")
        return False

    opened_today = count_news_trades_opened_today(STRATEGY_NAME)
    if opened_today >= NEWS_AI_MAX_TRADES_PER_DAY:
        logger.info(f"{symbol}: signal #{signal['id']} skipped -- daily cap reached ({opened_today}/{NEWS_AI_MAX_TRADES_PER_DAY})")
        mark_news_signal_acted(signal["id"], skip_reason="daily_cap_reached")
        return False

    allowed, breaker_msg = check_circuit_breakers()
    if not allowed:
        logger.info(f"{symbol}: signal #{signal['id']} skipped -- circuit breaker: {breaker_msg}")
        mark_news_signal_acted(signal["id"], skip_reason=f"circuit_breaker:{breaker_msg}")
        return False

    trade = _compute_trade(symbol, market, timeframe, signal["bias"], signal.get("time_horizon"))
    if trade is None:
        logger.warning(f"{symbol}: signal #{signal['id']} skipped -- market data/ATR unavailable")
        mark_news_signal_acted(signal["id"], skip_reason="market_data_unavailable")
        return False

    technical_cfg = get_technical_paper_config(symbol, market, timeframe)
    if technical_cfg:
        allocated_capital = technical_cfg["allocated_capital"]
        risk_per_trade_pct = technical_cfg["risk_per_trade_pct"]
    else:
        # Shouldn't normally happen -- Phase 2 only evaluates symbols with
        # an active technical config -- but fail safe with a conservative
        # default rather than crashing if one was deactivated in between.
        logger.warning(f"{symbol}: no active technical paper_config found to mirror capital from; using conservative default")
        allocated_capital, risk_per_trade_pct = 1000.0, 0.5

    ensure_overlay_paper_config(symbol, market, timeframe, STRATEGY_NAME, allocated_capital, risk_per_trade_pct)
    cfg = {
        "symbol": symbol, "market": market, "timeframe": timeframe,
        "strategy_name": STRATEGY_NAME,
        "allocated_capital": allocated_capital, "risk_per_trade_pct": risk_per_trade_pct,
    }

    new_id = insert_paper_position(cfg, trade, trade["current_close"], trade["candle_open_time"])
    if not new_id:
        logger.info(f"{symbol}: signal #{signal['id']} -- insert_paper_position reported a duplicate/no-op")
        mark_news_signal_acted(signal["id"], skip_reason="duplicate_position")
        return False

    mark_news_signal_acted(signal["id"], paper_position_id=new_id)
    logger.info(
        f"{symbol}: OPENED news_ai_overlay position #{new_id} ({trade['direction']}) "
        f"from signal #{signal['id']} (confidence={signal['confidence']})"
    )
    try:
        send_news_signal_alert(
            symbol=symbol, market=market, timeframe=timeframe, direction=trade["direction"],
            entry_price=trade["entry_price"], stop_loss=trade["stop_loss"], take_profit=trade["take_profit"],
            confidence=signal["confidence"], reasoning=signal.get("reasoning"),
            invalidation_condition=signal.get("invalidation_condition"),
            trigger_headline=event.get("headline") if event else None,
            time_horizon=signal.get("time_horizon"),
        )
    except Exception as tel_err:
        logger.warning(f"Telegram news signal alert failed: {tel_err}")
    return True


def poll_once():
    signals = get_unacted_news_signals(limit=20)
    if not signals:
        heartbeat("news_execution", detail="0 pending signals")
        return
    opened = 0
    for signal in signals:
        try:
            if process_signal(signal):
                opened += 1
        except Exception as e:
            # Transient DB/API failures must not permanently discard a signal.
            # The atomic claim will expire after 10 minutes, allowing retry.
            logger.error(f"process_signal failed for signal #{signal['id']}: {e}", exc_info=True)
    heartbeat("news_execution", detail=f"{len(signals)} signals processed, {opened} positions opened")
    logger.info(f"Cycle complete: {len(signals)} signals processed, {opened} positions opened")


def run(once=False):
    while True:
        start = time.time()
        try:
            poll_once()
        except Exception as e:
            logger.error(f"poll_once failed: {e}")
            heartbeat("news_execution", status="error", detail=str(e))
        if once:
            return
        elapsed = time.time() - start
        time.sleep(max(0, NEWS_AI_EXECUTION_POLL_INTERVAL_SECONDS - elapsed))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run a single pass instead of polling forever")
    args = parser.parse_args()
    run(once=args.once)
