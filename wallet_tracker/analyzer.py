"""Pure aggregation / classification logic (no DB, no network)."""
from collections import defaultdict
from datetime import timedelta

ACCUMULATING = "ACCUMULATING"
DISTRIBUTING = "DISTRIBUTING"
NEUTRAL = "NEUTRAL"


def _safe_div(a, b):
    return (a / b) if b else None


def aggregate_swaps(swaps, period_hours, now, min_net_usd):
    """Aggregate candidate positions by chain, wallet, and pool."""
    cutoff = now - timedelta(hours=period_hours)
    groups = defaultdict(list)
    for s in swaps:
        if cutoff <= s["block_time"] <= now:
            groups[(s["chain"], s["wallet"], s["pool_address"])].append(s)

    out = []
    for (chain, wallet, pool), rows in groups.items():
        buys = [r for r in rows if r["side"] == "buy"]
        sells = [r for r in rows if r["side"] == "sell"]
        buy_usd = sum(r["usd_value"] for r in buys)
        sell_usd = sum(r["usd_value"] for r in sells)
        buy_tokens = sum(r["token_amount"] for r in buys)
        sell_tokens = sum(r["token_amount"] for r in sells)
        net_usd = buy_usd - sell_usd
        net_tokens = buy_tokens - sell_tokens
        if abs(net_usd) < min_net_usd or net_tokens == 0:
            continue
        total_usd = buy_usd + sell_usd
        side = "BUY" if net_tokens > 0 else "SELL"
        out.append({
            "chain": chain, "wallet": wallet, "pool_address": pool,
            "pair": rows[0]["pair"], "period_hours": period_hours,
            "side": side, "bias": ACCUMULATING if net_tokens > 0 else DISTRIBUTING,
            "buy_usd": round(buy_usd, 2), "sell_usd": round(sell_usd, 2),
            "net_usd": round(net_usd, 2), "total_usd": round(total_usd, 2),
            "buy_tokens": buy_tokens, "sell_tokens": sell_tokens, "net_tokens": net_tokens,
            "avg_buy_price": _safe_div(buy_usd, buy_tokens),
            "avg_sell_price": _safe_div(sell_usd, sell_tokens),
            "avg_price": _safe_div(total_usd, buy_tokens + sell_tokens),
            "trade_count": len(rows), "buy_count": len(buys), "sell_count": len(sells),
            "first_trade_at": min(r["block_time"] for r in rows),
            "last_trade_at": max(r["block_time"] for r in rows),
        })
    out.sort(key=lambda p: abs(p["net_usd"]), reverse=True)
    return out


def aggregate_wallet_flows(swaps, period_hours, now):
    """Aggregate raw swaps across ALL pools for each chain+wallet.

    Used for follow decisions only. Token quantities are deliberately not added
    across pools because different pools can represent different base tokens.
    """
    cutoff = now - timedelta(hours=period_hours)
    groups = defaultdict(list)
    for swap in swaps:
        if cutoff <= swap["block_time"] <= now:
            groups[(swap["chain"], swap["wallet"])].append(swap)

    flows = []
    for (chain, wallet), rows in groups.items():
        buy_usd = sum(s["usd_value"] for s in rows if s["side"] == "buy")
        sell_usd = sum(s["usd_value"] for s in rows if s["side"] == "sell")
        total_usd = buy_usd + sell_usd
        net_usd = buy_usd - sell_usd
        flows.append({
            "chain": chain, "wallet": wallet, "period_hours": period_hours,
            "buy_usd": round(buy_usd, 2), "sell_usd": round(sell_usd, 2),
            "net_usd": round(net_usd, 2), "total_usd": round(total_usd, 2),
            "trade_count": len(rows),
            "net_flow_ratio": abs(net_usd) / total_usd if total_usd else 0.0,
            "first_trade_at": min(s["block_time"] for s in rows),
            "last_trade_at": max(s["block_time"] for s in rows),
        })
    return flows


def quality_follow_candidates(swaps, periods, now, min_net_usd, min_total_usd,
                              min_trade_count=3, min_net_flow_ratio=0.25,
                              max_trades_per_hour=20, high_frequency_min_ratio=0.50):
    """Return one candidate per chain+wallet based on a single pool/token flow.

    USD buys and sells are never netted across unrelated pools. A wallet must
    show material directional flow, enough turnover and repeated trades in the
    same pool. Extremely high-frequency, round-trip-heavy activity is rejected,
    while high-frequency directional activity is retained.
    """
    selected = {}
    for hours in periods:
        positions = aggregate_swaps(swaps, hours, now, min_net_usd)
        for flow in positions:
            total_usd = flow["total_usd"]
            trade_count = flow["trade_count"]
            ratio = abs(flow["net_usd"]) / total_usd if total_usd else 0.0
            trades_per_hour = trade_count / max(float(hours), 1.0)
            if total_usd < min_total_usd or trade_count < min_trade_count:
                continue
            if ratio < min_net_flow_ratio:
                continue
            # Penalize suspicious churn only when it is both unusually frequent
            # and weakly directional. Do not blanket-exclude market makers.
            if trades_per_hour > max_trades_per_hour and ratio < high_frequency_min_ratio:
                continue
            flow["period_hours"] = hours
            flow["net_flow_ratio"] = ratio
            flow["trades_per_hour"] = round(trades_per_hour, 2)
            net_strength = min(abs(flow["net_usd"]) / max(float(min_net_usd), 1.0), 2.0) / 2.0
            volume_strength = min(total_usd / max(float(min_total_usd), 1.0), 2.0) / 2.0
            activity_strength = min(trade_count / 10.0, 1.0)
            flow["quality_score"] = round(
                30 * net_strength + 25 * volume_strength
                + 25 * ratio + 20 * activity_strength, 1
            )
            if flow["quality_score"] < 50:
                continue
            key = (flow["chain"], flow["wallet"])
            previous = selected.get(key)
            if previous is None or flow["quality_score"] > previous["quality_score"]:
                selected[key] = flow
    return sorted(selected.values(), key=lambda f: (f["quality_score"], abs(f["net_usd"])), reverse=True)


def token_flow(positions):
    """Roll wallet positions up to one row per (chain, pool)."""
    flows = {}
    for p in positions:
        key = (p["chain"], p["pool_address"])
        f = flows.setdefault(key, {
            "chain": p["chain"], "pool_address": p["pool_address"], "pair": p["pair"],
            "accumulating_wallets": 0, "distributing_wallets": 0,
            "buy_usd": 0.0, "sell_usd": 0.0, "net_usd": 0.0,
        })
        if p.get("bias") == ACCUMULATING or (not p.get("bias") and p.get("net_tokens", 0) > 0):
            f["accumulating_wallets"] += 1
        else:
            f["distributing_wallets"] += 1
        f["buy_usd"] += p["buy_usd"]
        f["sell_usd"] += p["sell_usd"]
        f["net_usd"] += p["net_usd"]

    result = []
    for f in flows.values():
        a, d = f["accumulating_wallets"], f["distributing_wallets"]
        # Require a multi-wallet majority AND agreement with aggregate USD flow.
        # A lone wallet or conflicting count/net-flow evidence is not a signal.
        total_wallets = a + d
        if total_wallets >= 2 and a > d and f["net_usd"] > 0:
            f["signal"] = "ACCUMULATION"
        elif total_wallets >= 2 and d > a and f["net_usd"] < 0:
            f["signal"] = "DISTRIBUTION"
        else:
            f["signal"] = "MIXED"
        for k in ("buy_usd", "sell_usd", "net_usd"):
            f[k] = round(f[k], 2)
        result.append(f)
    result.sort(key=lambda f: abs(f["net_usd"]), reverse=True)
    return result
