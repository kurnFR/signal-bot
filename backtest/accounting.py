"""Canonical financial accounting primitives shared by backtest and paper trading.

All rates are decimal fractions (0.001 = 0.1%). Prices are quote-currency
units. The module intentionally contains no database or strategy logic so it
can be reused by the paper engine without importing the backtest simulator.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PositionSize:
    risk_amount: float
    stop_distance: float
    quantity: float
    notional: float


def calculate_position_size(
    equity: float,
    risk_pct: float,
    entry_price: float,
    stop_price: float,
) -> PositionSize:
    """Size a position so the modeled stop loss risks exactly risk_pct of equity."""
    if equity <= 0:
        raise ValueError("equity must be positive")
    if risk_pct <= 0:
        raise ValueError("risk_pct must be positive")
    if entry_price <= 0 or stop_price <= 0:
        raise ValueError("entry_price and stop_price must be positive")

    stop_distance = abs(entry_price - stop_price)
    if stop_distance <= 0:
        raise ValueError("entry_price and stop_price must differ")

    risk_amount = equity * (risk_pct / 100.0)
    quantity = risk_amount / stop_distance
    return PositionSize(
        risk_amount=risk_amount,
        stop_distance=stop_distance,
        quantity=quantity,
        notional=quantity * entry_price,
    )


def apply_entry_slippage(price: float, direction: str, slippage_pct: float) -> float:
    if price <= 0 or slippage_pct < 0:
        raise ValueError("invalid price/slippage")
    if direction == "LONG":
        return price * (1 + slippage_pct)
    if direction == "SHORT":
        return price * (1 - slippage_pct)
    raise ValueError("direction must be LONG or SHORT")


def apply_exit_slippage(price: float, direction: str, slippage_pct: float) -> float:
    if price <= 0 or slippage_pct < 0:
        raise ValueError("invalid price/slippage")
    if direction == "LONG":
        return price * (1 - slippage_pct)
    if direction == "SHORT":
        return price * (1 + slippage_pct)
    raise ValueError("direction must be LONG or SHORT")


def calculate_trade_accounting(
    direction: str,
    quantity: float,
    entry_price: float,
    exit_price: float,
    fee_pct: float,
    entry_slippage_pct: float = 0.0,
    exit_slippage_pct: Optional[float] = None,
    stop_price: Optional[float] = None,
    funding_cost: float = 0.0,
) -> dict:
    """Return gross/net P&L and R using one cost-aware formula.

    `funding_cost` is an absolute quote-currency amount. Positive means a cost
    paid by the position; negative means funding received.
    """
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if fee_pct < 0 or entry_slippage_pct < 0:
        raise ValueError("cost rates cannot be negative")
    if exit_slippage_pct is None:
        exit_slippage_pct = entry_slippage_pct
    if exit_slippage_pct < 0:
        raise ValueError("exit slippage cannot be negative")
    if funding_cost < 0:
        # Negative funding is valid: the trader receives funding.
        pass

    entry_exec = apply_entry_slippage(entry_price, direction, entry_slippage_pct)
    exit_exec = apply_exit_slippage(exit_price, direction, exit_slippage_pct)

    if direction == "LONG":
        gross_pnl = (exit_exec - entry_exec) * quantity
    elif direction == "SHORT":
        gross_pnl = (entry_exec - exit_exec) * quantity
    else:
        raise ValueError("direction must be LONG or SHORT")

    entry_notional = entry_exec * quantity
    exit_notional = exit_exec * quantity
    fees = (entry_notional + exit_notional) * fee_pct
    net_pnl = gross_pnl - fees - funding_cost

    risk_amount = None
    r_multiple = None
    if stop_price is not None:
        risk_amount = abs(entry_price - stop_price) * quantity
        if risk_amount > 0:
            r_multiple = net_pnl / risk_amount

    return {
        "entry_exec_price": entry_exec,
        "exit_exec_price": exit_exec,
        "gross_pnl": gross_pnl,
        "fees": fees,
        "funding_cost": funding_cost,
        "net_pnl": net_pnl,
        "risk_amount": risk_amount,
        "r_multiple": r_multiple,
        "return_pct_on_entry_notional": (net_pnl / entry_notional * 100.0) if entry_notional else None,
    }
