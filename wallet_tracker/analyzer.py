"""Pure aggregation / classification logic (no DB, no network)."""
from collections import defaultdict
from datetime import timedelta

ACCUMULATING = "ACCUMULATING"
DISTRIBUTING = "DISTRIBUTING"
NEUTRAL = "NEUTRAL"


def _safe_div(a, b):
    return (a / b) if b else None


def aggregate_swaps(swaps, period_hours, now, min_net_usd):
    """Group swaps inside [now - period, now] by (chain, wallet, pool) and keep
    only wallets whose |net USD| reaches `min_net_usd` (the candidates).

    avg prices are volume-weighted (USD spent / tokens received, and the same
    for sells), which is the wallet's real average entry / exit price.
    """
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
        # Dollar flow measures capital flow; token delta measures accumulation.
        # Keep them separate so price changes cannot invert the BUY/SELL label.
        if abs(net_usd) < min_net_usd or net_tokens == 0:
            continue
        total_usd = buy_usd + sell_usd
        side = "BUY" if net_tokens > 0 else "SELL"
        out.append({
            "chain": chain,
            "wallet": wallet,
            "pool_address": pool,
            "pair": rows[0]["pair"],
            "period_hours": period_hours,
            "side": side,
            "bias": ACCUMULATING if net_tokens > 0 else DISTRIBUTING,
            "buy_usd": round(buy_usd, 2),
            "sell_usd": round(sell_usd, 2),
            "net_usd": round(net_usd, 2),
            "total_usd": round(total_usd, 2),
            "buy_tokens": buy_tokens,
            "sell_tokens": sell_tokens,
            "net_tokens": net_tokens,
            "avg_buy_price": _safe_div(buy_usd, buy_tokens),
            "avg_sell_price": _safe_div(sell_usd, sell_tokens),
            "avg_price": _safe_div(total_usd, buy_tokens + sell_tokens),
            "trade_count": len(rows),
            "buy_count": len(buys),
            "sell_count": len(sells),
            "first_trade_at": min(r["block_time"] for r in rows),
            "last_trade_at": max(r["block_time"] for r in rows),
        })
    out.sort(key=lambda p: abs(p["net_usd"]), reverse=True)
    return out


def token_flow(positions):
    """Roll wallet positions up to one row per (chain, pool): how many tracked
    wallets accumulate vs distribute and the combined net USD. This is the
    early-detection view: many wallets net-buying the same token."""
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
        if a > d:
            f["signal"] = "ACCUMULATION"
        elif d > a:
            f["signal"] = "DISTRIBUTION"
        else:
            f["signal"] = "MIXED"
        for k in ("buy_usd", "sell_usd", "net_usd"):
            f[k] = round(f[k], 2)
        result.append(f)
    result.sort(key=lambda f: abs(f["net_usd"]), reverse=True)
    return result
