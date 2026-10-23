from types import SimpleNamespace

from paper.retailbot2 import RetailStrategyEngine


def test_composite_pnl_is_not_misattributed_to_first_contributor():
    engine = object.__new__(RetailStrategyEngine)
    engine.config = SimpleNamespace(rr_ratio=2.0)
    signals = [
        {
            "strategy": "BB_REV",
            "side": "LONG",
            "entry": 100.0,
            "stop": 95.0,
            "target": 110.0,
            "atr": 2.0,
            "reason": "lower band",
        },
        {
            "strategy": "DBL_PAT",
            "side": "LONG",
            "entry": 102.0,
            "stop": 97.0,
            "target": 112.0,
            "atr": 3.0,
            "reason": "double bottom",
        },
        {
            "strategy": "RSI_CTR",
            "side": "SHORT",
            "entry": 100.0,
            "stop": 105.0,
            "target": 90.0,
            "atr": 2.5,
            "reason": "overbought",
        },
    ]

    composite = engine.build_composite(signals)

    assert composite is not None
    assert composite["strategy"] == "COMPOSITE"
    assert composite["strategies_triggered"] == ["BB_REV", "DBL_PAT"]
    assert composite["confluence_score"] == 2
    assert composite["entry"] == 101.0
