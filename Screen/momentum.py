"""Closed one-minute spot candle rules shared by the screener and its replay."""
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import requests

MINUTE = 60_000
WINDOW = 5
HISTORY = 20
WATCH_FOLLOWUP_MAX_MINUTES = WINDOW + 1  # 6 minutes from watch open (5 minutes from watch close)
EXPIRED_WATCH_REASON = "Watch expired: past 5-candle follow-up window"


@dataclass(frozen=True)
class Bar:
    start: int
    open: float
    high: float
    low: float
    close: float


def utc_ms(value):
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp() * 1000)
    raise TypeError(f"Unsupported timestamp value: {type(value)}")


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def watch_deadline_ms(candle_time, max_signal_age_sec=None):
    """Last eligible delivery instant for a directional 1m parent watch.

    Strictly before the fifth follow-up candle closes: watch open + 6 minutes
    (or watch close + 5 minutes). Uses the earlier of that deadline and the
    existing signal-age limit. Derived from candle time so delayed detection
    cannot extend it.
    """
    start_ms = utc_ms(candle_time)
    window_limit_sec = WATCH_FOLLOWUP_MAX_MINUTES * 60
    if max_signal_age_sec is not None:
        max_sec = min(window_limit_sec, int(max_signal_age_sec))
    else:
        max_sec = window_limit_sec
    return start_ms + max_sec * 1000


def is_watch_expired(candle_time, now_ms=None, max_signal_age_sec=None):
    """True if current instant is at or after the watch deadline."""
    if now_ms is None:
        import time
        now_ms = int(time.time() * 1000)
    elif isinstance(now_ms, (datetime, str)):
        now_ms = utc_ms(now_ms)
    else:
        now_ms = int(now_ms)
    return now_ms >= watch_deadline_ms(candle_time, max_signal_age_sec)


def bars_between(symbol, start, end, now):
    response = requests.get("https://api.binance.com/api/v3/klines", params={
        "symbol": symbol, "interval": "1m", "startTime": start, "endTime": min(now - 1, end),
        "limit": 1000}, timeout=5)
    response.raise_for_status()
    bars = {}
    for raw in response.json():
        t = int(raw[0])
        if t + MINUTE > now or int(raw[6]) != t + MINUTE - 1:
            continue
        prices = tuple(map(float, raw[1:5]))
        o, h, l, c = prices
        if all(math.isfinite(p) and p > 0 for p in prices) and l <= min(o, c) <= max(o, c) <= h:
            bars[t] = Bar(t, *prices)
    return bars


def first_break(bars, start, delivered, direction):
    """Require continuous bars and invalidate on an opposite close or pre-delivery break."""
    if direction not in ("BULLISH", "BEARISH") or start not in bars:
        return None
    watch = bars[start]
    for offset in range(1, WINDOW + 1):
        bar = bars.get(start + offset * MINUTE)
        if bar is None:
            return None
        if watch.low <= bar.close <= watch.high:
            continue
        if bar.start + MINUTE <= delivered or (bar.close > watch.high) != (direction == "BULLISH"):
            return None
        prior = [bars.get(start - n * MINUTE) for n in range(1, HISTORY + 1)]
        average = sum(b.high - b.low for b in prior) / HISTORY if all(prior) else 0
        distance = abs(bar.close - (watch.high if direction == "BULLISH" else watch.low))
        return bar, distance / average if average > 0 else None
    return None


def message(signal, *, ema_length=None):
    from html import escape

    ctx = getattr(signal, "watch_context", None) or {}
    pair = escape(str(signal.symbol))
    when = escape(str(signal.candle_time))
    direction = escape(str(signal.direction))
    if getattr(signal, "parent_watch_id", None):
        multiple = ctx.get("range_multiple")
        strength = f"{multiple:.2f}× prior mean 1m range" if multiple is not None else "unavailable"
        return (
            f"📍 <b>MOMENTUM CONFIRMED · {direction}</b>\n{pair} · Watch #{signal.parent_watch_id} "
            f"({escape(ctx['watch_time'])} UTC)\n"
            f"1m close {ctx['close']:.8g} crossed the watch {'high' if direction == 'BULLISH' else 'low'} "
            f"{ctx['level']:.8g}.\nDistance beyond level: {strength}\n"
            f"Confirmation candle closed: {escape(ctx['confirmed_at'])} UTC"
        )
    ratio = signal.taker_buy_ratio
    if ratio is None:
        taker_buy = "Unavailable"
    else:
        flow = "buyer-led" if ratio >= .55 else "seller-led" if ratio <= .45 else "balanced"
        taker_buy = f"<b>{ratio:.1%}</b> ({flow})"
    baseline = f"{ema_length:d}-EMA" if ema_length is not None else "long EMA"
    levels = (
        f"Watch range: <code>${ctx['low']:.8g}</code> to <code>${ctx['high']:.8g}</code>\n"
        if ctx.get("low") is not None and ctx.get("high") is not None else ""
    )
    return (
        "⚡ <b>SMART MONEY EARLY SIGNAL</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Pair: <b>{pair}</b>\n"
        f"Signal: <code>{escape(signal.signal_type)}</code> · Volume Watch\n\n"
        f"Quality: <b>{signal.quality_score:.0f}/100</b> (watch score)\n"
        f"Bias: <b>{direction}</b>\n"
        f"RVOL: <b>{signal.rvol:.2f}x</b> (vs {baseline})\n"
        f"Velocity: <b>{signal.volume_velocity:.2f}x</b> (vs recent average)\n"
        f"Taker Buy: {taker_buy}\n"
        f"HTF: <b>{escape(str(signal.htf_context))}</b>\n"
        f"Price: <code>${signal.price:.8g}</code> ({signal.price_change_pct:+.3f}%)\n"
        f"Volume: <code>{signal.volume:,.0f}</code> | Quote: <code>{signal.quote_volume:,.0f} USDT</code>\n"
        f"{levels}\n"
        f"Candle opened: <code>{when}</code> UTC\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>Interpretation:</b> 🟡 Unusual volume · momentum unconfirmed\n"
        "<i>Action: Wait for price confirmation</i>"
    )
