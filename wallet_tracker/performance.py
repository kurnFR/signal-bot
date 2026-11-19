"""Realized return on observed, pool-matched DEX swaps."""
from decimal import Decimal
from itertools import groupby
from operator import itemgetter


def rank_wallets(swaps, metric, limit):
    """Input is ordered by chain, wallet, pool, time. Inventory starts empty."""
    wallets = []
    for (chain, wallet), trades in groupby(swaps, itemgetter("chain", "wallet")):
        cost = proceeds = Decimal(0)
        matched = 0
        pairs = set()
        for _, pool in groupby(trades, itemgetter("pool_address")):
            units = basis = Decimal(0)
            for _, simultaneous in groupby(pool, itemgetter("block_time")):
                batch = list(simultaneous)
                pairs.update(r["pair"] for r in batch)
                if len({r["side"] for r in batch}) > 1 or any(
                    r["token_amount"] <= 0 or r["usd_value"] <= 0 for r in batch
                ):
                    # Without execution order or a valid valuation the basis is uncertain.
                    units = basis = Decimal(0)
                    continue
                quantity = sum(r["token_amount"] for r in batch)
                value = sum(r["usd_value"] for r in batch)
                if batch[0]["side"] == "buy":
                    units += quantity
                    basis += value
                elif units:
                    # For simultaneous sales partial coverage cannot be assigned reliably.
                    if len(batch) == 1 or quantity <= units:
                        covered = min(quantity, units)
                        allocated = basis if covered == units else basis * covered / units
                        cost += allocated
                        proceeds += value if covered == quantity else value * covered / quantity
                        matched += len(batch)
                        units -= covered
                        basis -= allocated
                    else:
                        units = basis = Decimal(0)
        if cost:
            profit = proceeds - cost
            wallets.append(dict(chain=chain, wallet=wallet, pairs=sorted(pairs), matched_sells=matched,
                                cost_usd=cost, pnl_usd=profit, roi_pct=100 * profit / cost))
    wallets.sort(key=lambda w: (-w[metric], -w["cost_usd"], w["chain"], w["wallet"]))
    return wallets[:limit]
