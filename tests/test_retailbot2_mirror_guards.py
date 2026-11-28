"""Mirror-only guards for the RetailBot2 strategy library (migration step 3).

The live worker (``paper/retailbot2.py``) is deleted, so the old
``test_retailbot2_mirror_fidelity.py`` live-vs-mirror comparisons are gone
with it. The mirror (``backtest/strategies/retail_death_trap.py``) is now
the implementation of record for these 16 strategies. This file keeps the
live-free properties step 2 proved, rewritten against the mirror alone:

- DBL_PAT / MACD_DIV have no lookahead (4-bar pivot-confirmation lag).
- RSI effective thresholds are 35/65 through the paper path
  (``paper/engine.py`` run_params) and the backtest run path, and the
  ``RSI_OVERSOLD=30`` / ``RSI_OVERBOUGHT=70`` keys in ``BASE_PARAMS`` are
  generic-strategy keys (confluence_reversal_v1 in mean_reversion.py /
  confluence_ensemble_v1 use them), NOT retail keys -- see
  ``test_rsi_shadow_keys_are_generic_not_retail``.
- COMPOSITE paper guard: ``set_paper_config`` rejects COMPOSITE /
  COMPOSITE_INV deployments and the paper evaluation loop skips them even
  if a row somehow exists. Backtest COMPOSITE stays research-only.

The capture helper patches ``library.simulate`` so the test sees the
mirror's per-bar buy/sell decision (direction + signal-close stop/target)
without the next-bar-open execution layer of ``simulate()``.
"""
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

import paper.engine as pe
import web.routes.backtest as backtest_route
from backtest.params import BASE_PARAMS, retailbot2_param_overrides
from backtest.strategies import retail_death_trap as library
from backtest.strategies.retail_death_trap import RETAILBOT2_STRATEGIES


def candles(seed, count=768):
    """Deterministic synthetic OHLC frame (inlined from the retired parity harness)."""
    rng = np.random.default_rng(seed)
    candles_no = np.arange(count)
    close = 100 + 0.008 * candles_no + 1.3 * np.sin(candles_no / 8)
    close += rng.normal(0, 0.25, count)
    opening = np.r_[close[0], close[:-1]] + rng.normal(0, 0.12, count)
    opening[::19] += rng.choice([-2.5, 2.5], len(opening[::19]))
    return pd.DataFrame({
        "open_time": candles_no * 3_600_000,
        "open": opening,
        "close": close,
        "high": np.maximum(opening, close) + rng.uniform(0.05, 0.2, count),
        "low": np.minimum(opening, close) - rng.uniform(0.05, 0.2, count),
    })


def _mirror_signals(runner_name, frame, params=None):
    """{signal_bar_index: side} from the mirror runner, via simulate capture."""
    params = dict(BASE_PARAMS) if params is None else params
    out = {}

    def capture(prepared, effective_params, buy, sell, stop_target):
        for i in range(1, len(prepared) - 1):
            row, previous = prepared.iloc[i], prepared.iloc[i - 1]
            side = "LONG" if buy(row, previous, effective_params) else (
                "SHORT" if sell(row, previous, effective_params) else None
            )
            if side:
                out[int(row.name)] = side
        return []

    with patch.object(library, "simulate", capture):
        RETAILBOT2_STRATEGIES[runner_name](frame, params)
    return out


def _double_bottom_fixture():
    """Two equal troughs (low 94.0) at bars 70 and 90.

    NOTE on the confirmation lag: ``argrelextrema(order=4)`` truncates its
    neighbourhood at the window edge, so the bar-90 trough is already
    detectable once ONE confirming bar (bar 91) exists -- the earliest
    signal bar is 91, not 94. (A 4-bar lag would only apply to an interior
    pivot.) The mirror shares this scipy edge behaviour, which is exactly
    what the test below pins: earliest bar 91, and no signal while the
    trough is still the window's last bar.
    """
    n = 130
    close = [100.0] * n
    for i in range(60, 71):  # dip 1: 99.5 -> 94.5
        close[i] = 100.0 - (i - 59) * 0.5
    for i in range(71, 81):  # recover to 100
        close[i] = 95.0 + (i - 71) * 0.5
    for i, v in enumerate([99.1, 98.2, 97.3, 96.4, 95.5, 94.5], start=85):  # dip 2: -> 94.5
        close[i] = v
    for i in range(91, n):  # recover and hold above the pattern level
        close[i] = min(102.0, 95.5 + (i - 91) * 1.0)
    open_ = [close[0]] + close[:-1]
    high = [max(o, c) + 0.4 for o, c in zip(open_, close)]
    low = [min(o, c) - 0.4 for o, c in zip(open_, close)]
    low[70] = 94.0
    low[90] = 94.0
    return pd.DataFrame({
        "open_time": [i * 3_600_000 for i in range(n)],
        "open": open_, "close": close, "high": high, "low": low,
    })


# ============================================================================
# 1. No-lookahead properties of the mirror (formerly live-vs-mirror)
# ============================================================================
def test_dbl_pat_pivot_needs_a_confirming_bar():
    frame = _double_bottom_fixture()
    mirror = _mirror_signals("DBL_PAT", frame)
    assert mirror, "fixture produced no mirror DBL_PAT signal"
    assert all(side == "LONG" for side in mirror.values())
    # The bar-90 trough is only detectable once bar 91 exists to confirm
    # it (scipy truncates the order=4 neighbourhood at the window edge).
    assert min(mirror) == 91, mirror

    # With the confirming bar absent (frame ends at bar 91, so the trough
    # is the window's last bar) neither side may fire at all.
    truncated = frame.iloc[:92]  # bars 0..91
    assert _mirror_signals("DBL_PAT", truncated) == {}
    assert RETAILBOT2_STRATEGIES["DBL_PAT"](truncated, dict(BASE_PARAMS)) == []


@pytest.mark.parametrize("name", ["MACD_DIV", "DBL_PAT"])
def test_no_lookahead_mutating_future_bars(name):
    """Signals at bar i must not depend on any bar after i.

    Find the first mirror signal bar, replace every later bar with extreme
    values, and assert the mirror's trades at/before that bar are identical.
    """
    frame = candles(1, 320)
    mirror = _mirror_signals(name, frame)
    assert mirror, name
    first_bar = min(mirror)
    mutated = frame.copy()
    mutated.loc[first_bar + 2:, ["open", "high", "low", "close"]] = 1000.0
    before = [
        (t["signal_open_time"], t["direction"])
        for t in RETAILBOT2_STRATEGIES[name](frame, dict(BASE_PARAMS))
        if t["signal_open_time"] <= int(frame["open_time"].iloc[first_bar])
    ]
    after = [
        (t["signal_open_time"], t["direction"])
        for t in RETAILBOT2_STRATEGIES[name](mutated, dict(BASE_PARAMS))
        if t["signal_open_time"] <= int(frame["open_time"].iloc[first_bar])
    ]
    assert before and before == after, name
    assert before[0][0] == int(frame["open_time"].iloc[first_bar]), name


# ============================================================================
# 2. RSI shadow keys: 30/70 are generic-strategy keys, retail uses 35/65
# ============================================================================
def test_rsi_shadow_keys_are_generic_not_retail():
    # confluence_reversal_v1 and confluence_ensemble_v1 read the generic
    # 30/70 keys -- deleting them from BASE_PARAMS would break those
    # strategies, so they stay. The retail mirror never reads them: it
    # reads RB2_RSI_OVERSOLD / RB2_RSI_OVERBOUGHT (35/65) only.
    from backtest.strategies import confluence_ensemble, mean_reversion

    assert BASE_PARAMS["RSI_OVERSOLD"] == 30
    assert BASE_PARAMS["RSI_OVERBOUGHT"] == 70
    assert BASE_PARAMS["RB2_RSI_OVERSOLD"] == 35
    assert BASE_PARAMS["RB2_RSI_OVERBOUGHT"] == 65
    import inspect

    assert "RSI_OVERSOLD" in inspect.getsource(confluence_ensemble)
    assert "RSI_OVERSOLD" in inspect.getsource(mean_reversion)
    # Direct proof: RSI_CTR fires identically when the generic keys are
    # moved to absurd values, but stops when the RB2 keys move.
    frame = candles(1, 240)
    baseline = _mirror_signals("RSI_CTR", frame)
    assert baseline
    moved_generic = _mirror_signals(
        "RSI_CTR", frame, dict(BASE_PARAMS, RSI_OVERSOLD=5, RSI_OVERBOUGHT=95)
    )
    assert moved_generic == baseline
    moved_rb2 = _mirror_signals(
        "RSI_CTR", frame, dict(BASE_PARAMS, RB2_RSI_OVERSOLD=25, RB2_RSI_OVERBOUGHT=75)
    )
    assert moved_rb2 != baseline


def test_translator_maps_all_four_shared_keys():
    assert retailbot2_param_overrides({"ATR_PERIOD": 10}) == {"RB2_ATR_PERIOD": 10}
    assert retailbot2_param_overrides({"RISK_REWARD_RATIO": 3.0}) == {
        "RB2_RISK_REWARD_RATIO": 3.0
    }
    assert retailbot2_param_overrides({"RSI_OVERBOUGHT": 60}) == {
        "RB2_RSI_OVERBOUGHT": 60
    }
    assert retailbot2_param_overrides({"RSI_OVERSOLD": 32}) == {
        "RB2_RSI_OVERSOLD": 32
    }
    # An explicit RB2_* key wins over the translated shared key.
    assert retailbot2_param_overrides(
        {"RSI_OVERSOLD": 32, "RB2_RSI_OVERSOLD": 38}
    ) == {"RB2_RSI_OVERSOLD": 38}
    # Non-retail keys pass through untouched.
    assert retailbot2_param_overrides({"PURE_TP_SL": True}) == {"PURE_TP_SL": True}


def _paper_eval_capture(cfg, df, open_candle):
    """Run one paper eval cycle with a capturing strategy; return (result, captured params)."""
    captured = {}

    def capture_strategy(frame, params):
        captured.update(params)
        return []

    entry_time = int(df["open_time"].iloc[-1]) + 3_600_000
    with patch.object(pe, "get_paper_configs", return_value=[cfg]), \
         patch.object(pe, "_process_pending_paper_orders", return_value={"filled": 0, "expired": 0, "rejected": 0}), \
         patch.object(pe, "get_active_positions", return_value=[]), \
         patch.object(pe, "fetch_ohlcv_with_features_df", return_value=df), \
         patch.object(pe, "fetch_latest_open_candle", return_value=open_candle), \
         patch.object(pe, "_signal_already_traded", return_value=False), \
         patch("paper.circuit_breaker.check_circuit_breakers", return_value=(True, "ok")), \
         patch.object(pe, "paper_strategy_registry", return_value={cfg["strategy_name"]: capture_strategy}), \
         patch.object(pe.time, "time", return_value=(entry_time + 10_000) / 1000.0):
        result = pe._sync_and_evaluate_paper_trading_impl()
    return result, captured


def _paper_cfg(strategy_name, strategy_params=None):
    return {
        "id": 11, "symbol": "BTCUSDT", "market": "spot", "timeframe": "1h",
        "strategy_name": strategy_name, "is_active": True,
        "allocated_capital": 5000.0, "risk_per_trade_pct": 1.0,
        "telegram_enabled": False, "report_enabled": True,
        "strategy_params": strategy_params, "ml_model_id": None,
        "ml_generator_model_id": None,
    }


def _paper_df(rows=60):
    base = 1_795_737_600_000 - rows * 3_600_000
    return pd.DataFrame({
        "open_time": [base + i * 3_600_000 for i in range(rows)],
        "open": [100.0 + i * 0.1 for i in range(rows)],
        "high": [101.0 + i * 0.1 for i in range(rows)],
        "low": [99.0 + i * 0.1 for i in range(rows)],
        "close": [100.5 + i * 0.1 for i in range(rows)],
        "volume": [500.0] * rows,
    })


def test_rsi_effective_thresholds_through_paper_path():
    """paper/engine.py run_params: RB2 35/65 by default, translator on override."""
    df = _paper_df()
    open_candle = {
        "open_time": int(df["open_time"].iloc[-1]) + 3_600_000,
        "open": 106.0, "high": 107.0, "low": 105.0, "close": 106.2, "volume": 500.0,
    }
    _, captured = _paper_eval_capture(_paper_cfg("RSI_CTR"), df, open_candle)
    assert captured["RB2_RSI_OVERSOLD"] == 35
    assert captured["RB2_RSI_OVERBOUGHT"] == 65
    # The generic 30/70 keys are still present in run_params (BASE_PARAMS)
    # but the mirror never reads them -- that is the documented shadow.
    assert captured["RSI_OVERSOLD"] == 30
    assert captured["RSI_OVERBOUGHT"] == 70

    _, captured = _paper_eval_capture(
        _paper_cfg("RSI_CTR", {"RSI_OVERSOLD": 32}), df, open_candle
    )
    assert captured["RB2_RSI_OVERSOLD"] == 32
    assert captured["RSI_OVERBOUGHT"] == 70  # untranslated generic key untouched


def test_rsi_effective_thresholds_through_backtest_path():
    """web/routes/backtest.py run path applies the same translator."""
    captured = {}

    def stub(frame, params):
        captured.update(params)
        return []

    request = backtest_route.BacktestRunRequest(
        symbol="BTCUSDT", strategy="RSI_CTR",
        params={"RSI_OVERSOLD": 32}, save_to_db=False,
    )
    metrics = {
        "total_trades": 0, "win_rate_pct": 0.0, "expectancy_r": 0.0,
        "profit_factor": 0.0, "max_drawdown_r": 0.0,
    }
    candles_df = pd.DataFrame({
        "open_time": [1_700_000_000_000 + i * 3_600_000 for i in range(60)],
        "open": [100.0 + i for i in range(60)],
        "high": [102.0 + i for i in range(60)],
        "low": [99.0 + i for i in range(60)],
        "close": [101.0 + i for i in range(60)],
        "volume": [10.0] * 60,
    })
    with patch.dict(backtest_route.STRATEGIES, {"RSI_CTR": stub}), \
         patch.object(backtest_route, "fetch_ohlcv_with_features_df", return_value=candles_df), \
         patch.object(backtest_route, "compute_metrics", return_value=metrics), \
         patch.object(backtest_route, "simulate_equity_curve", return_value={"equity": []}), \
         patch.object(backtest_route, "insert_backtest_run", return_value=1), \
         patch.object(backtest_route, "insert_backtest_trades"):
        backtest_route.execute_backtest(request)
    assert captured["RB2_RSI_OVERSOLD"] == 32
    assert captured["RB2_RSI_OVERBOUGHT"] == 65


# ============================================================================
# 3. COMPOSITE paper guard (set_paper_config + evaluation loop)
# ============================================================================
class _NoDbPool:
    """get_pool() stand-in that fails loudly if the guard lets a call through."""

    def get_connection(self):
        raise RuntimeError("DB must not be reached for a rejected COMPOSITE deployment")


@pytest.mark.parametrize("strategy_name", ["COMPOSITE", "COMPOSITE_INV"])
def test_set_paper_config_rejects_composite(strategy_name):
    with patch.object(pe, "get_pool", return_value=_NoDbPool()):
        with pytest.raises(ValueError, match="COMPOSITE"):
            pe.set_paper_config(
                "BTCUSDT", "spot", "1h", strategy_name, True,
                allocated_capital=5000.0, risk_per_trade_pct=1.0,
            )


def test_set_paper_config_still_accepts_other_retail_strategies():
    # Guard must be COMPOSITE-specific: a normal retail deployment reaches
    # the DB layer (here the stand-in pool, proving no ValueError was raised).
    with patch.object(pe, "get_pool", return_value=_NoDbPool()):
        with pytest.raises(RuntimeError, match="DB must not be reached"):
            pe.set_paper_config(
                "BTCUSDT", "spot", "1h", "RSI_CTR", True,
                allocated_capital=5000.0, risk_per_trade_pct=1.0,
            )


@pytest.mark.parametrize("strategy_name", ["COMPOSITE", "COMPOSITE_INV"])
def test_eval_loop_skips_composite_even_if_config_exists(strategy_name):
    """A COMPOSITE row that somehow exists (legacy / direct-SQL insert) must
    never open a paper position; its strategy runner must not even be called."""
    from unittest.mock import MagicMock

    df = _paper_df()
    open_candle = {
        "open_time": int(df["open_time"].iloc[-1]) + 3_600_000,
        "open": 106.0, "high": 107.0, "low": 105.0, "close": 106.2, "volume": 500.0,
    }
    sim_trade = {
        "signal_open_time": int(df["open_time"].iloc[-1]),
        "entry_time": int(open_candle["open_time"]),
        "direction": "LONG", "entry_price": 106.0, "stop_loss": 104.0,
        "take_profit": 110.0, "atr_at_signal": 1.2,
    }
    mock_strat = MagicMock(return_value=[sim_trade])
    cfg = _paper_cfg(strategy_name)
    entry_time = int(open_candle["open_time"])
    with patch.object(pe, "get_paper_configs", return_value=[cfg]), \
         patch.object(pe, "_process_pending_paper_orders", return_value={"filled": 0, "expired": 0, "rejected": 0}), \
         patch.object(pe, "get_active_positions", return_value=[]), \
         patch.object(pe, "fetch_ohlcv_with_features_df", return_value=df), \
         patch.object(pe, "fetch_latest_open_candle", return_value=open_candle), \
         patch.object(pe, "_signal_already_traded", return_value=False), \
         patch("paper.circuit_breaker.check_circuit_breakers", return_value=(True, "ok")), \
         patch.object(pe, "paper_strategy_registry", return_value={strategy_name: mock_strat}), \
         patch.object(pe, "create_pending_order") as mock_order, \
         patch.object(pe, "_insert_position") as mock_insert, \
         patch.object(pe.time, "time", return_value=(entry_time + 10_000) / 1000.0):
        result = pe._sync_and_evaluate_paper_trading_impl()
    assert result["new_positions_opened"] == 0
    assert result["orders_filled"] == 0
    mock_strat.assert_not_called()
    mock_order.assert_not_called()
    mock_insert.assert_not_called()


def test_eval_loop_still_evaluates_non_composite_control():
    """Same harness with RSI_CTR: the strategy IS called (guard is specific)."""
    from unittest.mock import MagicMock

    df = _paper_df()
    open_candle = {
        "open_time": int(df["open_time"].iloc[-1]) + 3_600_000,
        "open": 106.0, "high": 107.0, "low": 105.0, "close": 106.2, "volume": 500.0,
    }
    mock_strat = MagicMock(return_value=[])
    cfg = _paper_cfg("RSI_CTR")
    entry_time = int(open_candle["open_time"])
    with patch.object(pe, "get_paper_configs", return_value=[cfg]), \
         patch.object(pe, "_process_pending_paper_orders", return_value={"filled": 0, "expired": 0, "rejected": 0}), \
         patch.object(pe, "get_active_positions", return_value=[]), \
         patch.object(pe, "fetch_ohlcv_with_features_df", return_value=df), \
         patch.object(pe, "fetch_latest_open_candle", return_value=open_candle), \
         patch.object(pe, "_signal_already_traded", return_value=False), \
         patch("paper.circuit_breaker.check_circuit_breakers", return_value=(True, "ok")), \
         patch.object(pe, "paper_strategy_registry", return_value={"RSI_CTR": mock_strat}), \
         patch.object(pe.time, "time", return_value=(entry_time + 10_000) / 1000.0):
        pe._sync_and_evaluate_paper_trading_impl()
    mock_strat.assert_called_once()


def test_composite_stays_available_for_backtest_research():
    """The guard is paper-only: COMPOSITE remains in the backtest catalog
    and its mirror runner still works for research."""
    assert "COMPOSITE" in RETAILBOT2_STRATEGIES
    assert "COMPOSITE_INV" in RETAILBOT2_STRATEGIES
    assert "COMPOSITE" in backtest_route.STRATEGY_METADATA
    assert "COMPOSITE_INV" in backtest_route.STRATEGY_METADATA
    trades = RETAILBOT2_STRATEGIES["COMPOSITE"](candles(1, 240), dict(BASE_PARAMS))
    assert trades, "COMPOSITE backtest runner must still produce research trades"
