from decimal import Decimal as D

from wallet_tracker.performance import rank_wallets


def swap(wallet, pool, t, side, units, usd):
    return dict(chain="eth", wallet=wallet, pool_address=pool, pair=pool, block_time=t,
                side=side, token_amount=D(units), usd_value=D(usd))


def test_wallet_realized_return_uses_matched_cost_and_partial_proceeds():
    rows = [
        swap("a", "BTC", 1, "sell", 1, 500),
        swap("a", "BTC", 2, "buy", 2, 40),
        swap("a", "BTC", 3, "buy", 2, 60),
        swap("a", "BTC", 4, "sell", 2, 70),
        swap("a", "BTC", 5, "sell", 4, 160),
        swap("a", "ETH", 1, "buy", 3, 999),
        swap("b", "ETH", 1, "buy", 1, 10),
        swap("b", "ETH", 2, "sell", 1, 30),
        swap("c", "ETH", 1, "sell", 1, 40),
    ]
    top, other = rank_wallets(rows, "pnl_usd", 5)
    assert (top["wallet"], top["cost_usd"], top["pnl_usd"], top["roi_pct"]) == ("a", 100, 50, 50)
    assert top["pairs"] == ["BTC", "ETH"] and top["matched_sells"] == 2
    assert rank_wallets(rows, "roi_pct", 1) == [other]


def test_ambiguous_execution_order_and_other_pools_supply_no_basis():
    rows = [swap("w", "a", 1, "buy", 1, 1), swap("w", "a", 1, "sell", 1, 2),
            swap("w", "a", 2, "sell", 1, 3), swap("w", "b", 1, "buy", 1, 1),
            swap("w", "c", 2, "sell", 1, 10)]
    assert rank_wallets(rows, "roi_pct", 10) == []
