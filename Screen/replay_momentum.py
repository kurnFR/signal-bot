"""Replay delivered spot 1m watches using read-only DB access and public Binance klines."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from statistics import mean
import time

from Screen import momentum as m
from db.db import get_pool


def outcomes(bars, event, sign):
    # Delivery is recorded to seconds. Use the first full minute strictly afterwards.
    start = (event // m.MINUTE + 1) * m.MINUTE
    for horizon in (1, 5, 15):
        path = [bars.get(start + n * m.MINUTE) for n in range(horizon)]
        if not all(path):
            continue
        entry = path[0].open
        ret = lambda price: sign * 100 * (price / entry - 1)
        extremes = [ret(price) for b in path for price in (b.high, b.low)]
        yield horizon, (ret(path[-1].close), max(0, *extremes), min(0, *extremes))


def run(days, limit, legacy_1m):
    now = int(time.time() * 1000)
    since = now - days * 86_400_000
    split = since + int((now - since) * .7)
    with get_pool().get_connection() as conn, conn.cursor(dictionary=True) as cur:
        cur.execute("""SELECT w.id,w.symbol,w.direction,w.taker_buy_ratio,w.candle_time,w.watch_context,
                              w.delivered_at,c.delivered_at AS confirmation_delivery
                       FROM smart_money_signals w LEFT JOIN smart_money_signals c
                         ON c.parent_watch_id=w.id AND c.delivery_status='SENT'
                       WHERE w.delivery_status='SENT' AND w.parent_watch_id IS NULL AND w.delivered_at >= %s
                       ORDER BY w.delivered_at DESC,w.id DESC LIMIT %s""",
                    (datetime.fromtimestamp(since / 1000, timezone.utc).replace(tzinfo=None), limit))
        rows = cur.fetchall()
    counts = Counter()
    groups = defaultdict(list)
    for row in rows:
        ctx = json.loads(row["watch_context"]) if row["watch_context"] else {}
        if ctx.get("interval", "1m" if legacy_1m else None) != "1m" or row["direction"] not in ("BULLISH", "BEARISH"):
            counts["unknown_interval_or_neutral"] += 1
            continue
        start = m.utc_ms(row["candle_time"])
        delivery = m.utc_ms(row["delivered_at"])
        if not start + m.MINUTE <= delivery < start + 60 * m.MINUTE:
            counts["invalid_or_late_watch"] += 1
            continue
        bars = m.bars_between(row["symbol"], start - m.HISTORY * m.MINUTE,
                              max(delivery, start + 8 * m.MINUTE) + 17 * m.MINUTE, now)
        time.sleep(.2)
        if start not in bars or (ctx and (bars[start].high != ctx["high"] or bars[start].low != ctx["low"])):
            counts["missing_or_different_watch"] += 1
            continue
        sign = 1 if row["direction"] == "BULLISH" else -1
        samples = [("watch_delivered", delivery)]
        found = m.first_break(bars, start, delivery, row["direction"])
        split_name = "held_out" if delivery >= split else "earlier"
        counts[split_name + "_watches"] += 1
        if found:
            close = found[0].start + m.MINUTE
            samples.append(("break_at_close_simulated", close))
            counts[split_name + "_breaks"] += 1
        if row["confirmation_delivery"]:
            samples.append(("confirmation_delivered", m.utc_ms(row["confirmation_delivery"])))
        ratio = row["taker_buy_ratio"]
        conflict = ratio is not None and ((sign < 0 and float(ratio) >= .55) or (sign > 0 and float(ratio) <= .45))
        for stage, event in samples:
            for horizon, result in outcomes(bars, event, sign):
                if delivery < split <= (event // m.MINUTE + 1 + horizon) * m.MINUTE:
                    continue
                move = sign * 100 * (bars[(event // m.MINUTE + 1) * m.MINUTE].open / bars[start].close - 1)
                for cohort in (["all", "opposing_flow"] if conflict else ["all"]):
                    groups[split_name, stage, cohort, horizon].append((*result, move))
    summary = []
    for (period, stage, cohort, minutes), values in sorted(groups.items()):
        returns, favorable, adverse, prior_move = zip(*values)
        summary.append(dict(period=period, stage=stage, cohort=cohort, minutes=minutes,
                            n=len(values), nonpositive_pct=100 * mean(r <= 0 for r in returns),
                            mean_return_pct=mean(returns), mean_favorable_pct=mean(favorable),
                            mean_adverse_pct=mean(adverse), mean_move_before_entry_pct=mean(prior_move)))
    return dict(selected=len(rows), limit=limit, split_utc=m.iso(split), coverage=counts, results=summary)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--legacy-1m", action="store_true", help="Include older watches after verifying CMS used 1m")
    args = parser.parse_args()
    if not 1 <= args.days <= 365 or not 1 <= args.limit <= 5000:
        parser.error("days must be 1–365 and limit 1–5000")
    print(json.dumps(run(args.days, args.limit, args.legacy_1m), indent=2))
