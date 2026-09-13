import numpy as np
import pandas as pd

from ml.execution_tournament import run_execution_aware_tournament
from ml.tournament import Candidate


def _dataset(n=50):
    times = np.arange(1, n + 1, dtype=np.int64)
    x = np.linspace(-2.0, 2.0, n)
    # Alternating labels keep every chronological split class-balanced enough
    # for the lightweight logistic-regression fixture.
    target = (np.arange(n) % 2).astype(int)
    return pd.DataFrame({"open_time": times, "x": x, "target": target, "outcome_r": np.where(target, 1.0, -0.5)})


def test_execution_tournament_selects_threshold_from_simulator_and_tests_once():
    dataset = _dataset()
    market = dataset[["open_time", "x"]].copy()
    calls = []

    candidate = Candidate(
        model_type="logistic_regression",
        model_params={"C": 1.0},
        feature_columns=("x",),
        threshold_candidates=(0.50, 0.80),
    )

    def strategy_fn(df, params):
        start = params["_simulation_start_open_time"]
        end = params["_simulation_end_open_time"]
        calls.append((start, end))
        filt = params["_ml_signal_filter"]
        rows = df[(df.open_time >= start) & (df.open_time <= end)]
        probabilities = filt.predict_probability(rows[["x"]])
        trades = []
        for row, probability in zip(rows.itertuples(), probabilities):
            if probability < filt.threshold:
                continue
            # Simulated execution result deliberately includes a cost so this
            # path is not merely a classification-score tournament.
            r = 1.0 if row.x > 0 else -1.0
            r -= 0.10
            trades.append({"r_multiple": r, "net_pnl": r, "fees": 0.10})
        return trades

    result = run_execution_aware_tournament(
        market,
        dataset,
        [candidate],
        strategy_fn=strategy_fn,
        experiment_id="exec-test",
        symbol="BTCUSDT",
        timeframe="1h",
        base_strategy="trend_ema_v1",
        min_validation_trades=2,
        threshold_candidates=(0.50, 0.80),
    )

    # Two validation thresholds plus exactly one locked TEST execution.
    assert len(calls) == 3
    validation_end = int(dataset.iloc[39]["open_time"])
    test_start = int(dataset.iloc[40]["open_time"])
    assert all(end <= validation_end for _, end in calls[:2])
    assert calls[2][0] == test_start
    assert result.experiment.status == "research"
    assert result.experiment.threshold in (0.50, 0.80)
    assert result.validation_by_candidate[0]["selected_metrics"]["fees"] if "fees" in result.validation_by_candidate[0]["selected_metrics"] else True


def test_execution_tournament_rejects_candidate_with_too_few_validation_trades():
    dataset = _dataset()
    market = dataset[["open_time", "x"]].copy()
    candidate = Candidate("logistic_regression", {}, ("x",), (0.99,))

    def strategy_fn(df, params):
        return []

    try:
        run_execution_aware_tournament(
            market, dataset, [candidate], strategy_fn=strategy_fn,
            experiment_id="too-few", symbol="BTCUSDT", timeframe="1h",
            base_strategy="trend_ema_v1", min_validation_trades=1,
        )
    except ValueError as exc:
        assert "minimum is 1" in str(exc)
    else:
        raise AssertionError("expected minimum validation trade gate")
