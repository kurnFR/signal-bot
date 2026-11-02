"""Paper configuration request and persistence field-size contracts."""

import unittest
from unittest.mock import patch

from pydantic import ValidationError

from paper.engine import set_paper_config
from web.routes.backtest import BacktestRunRequest, RunAllStrategiesRequest, WalkForwardBacktestRequest
from web.routes.ml_routes import DeployMLToPaperRequest
from web.routes.paper_routes import DeployStrategyRequest, PaperConfigRequest


class TestPaperConfigFieldLimits(unittest.TestCase):
    def test_paper_config_request_accepts_database_column_boundaries(self):
        PaperConfigRequest(
            symbol="S" * 20,
            market="futures",
            timeframe="1" * 10,
            strategy_name="T" * 50,
            ml_model_id="m" * 100,
        )

    def test_paper_config_request_rejects_values_over_database_column_widths(self):
        invalid_payloads = (
            {"symbol": "S" * 21},
            {"market": "m" * 8},
            {"timeframe": "t" * 11},
            {"strategy_name": "T" * 51},
            {"ml_model_id": "m" * 101},
        )
        for change in invalid_payloads:
            with self.subTest(change=next(iter(change))):
                payload = {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "timeframe": "1d",
                    "strategy_name": "trend_ema_v1",
                }
                payload.update(change)
                with self.assertRaises(ValidationError):
                    PaperConfigRequest(**payload)

    def test_deploy_request_bounds_symbol_and_model_id(self):
        DeployStrategyRequest(
            symbol="S" * 20,
            strategy_name="T" * 50,
            ml_model_id="m" * 100,
        )
        with self.assertRaises(ValidationError):
            DeployStrategyRequest(symbol="S" * 21, strategy_name="trend_ema_v1")
        with self.assertRaises(ValidationError):
            DeployStrategyRequest(symbol="BTCUSDT", strategy_name="trend_ema_v1", ml_model_id="m" * 101)

    def test_backtest_run_request_bounds_paper_promotion_identifiers(self):
        BacktestRunRequest(
            symbol="S" * 20,
            market="futures",
            timeframe="1" * 10,
            strategy="T" * 50,
        )
        for change in (
            {"symbol": "S" * 21},
            {"market": "m" * 8},
            {"timeframe": "t" * 11},
            {"strategy": "T" * 51},
        ):
            with self.subTest(change=next(iter(change))):
                with self.assertRaises(ValidationError):
                    BacktestRunRequest(**change)

    def test_other_backtest_entrypoints_bound_identifier_fields(self):
        with self.assertRaises(ValidationError):
            WalkForwardBacktestRequest(symbol="S" * 21)
        with self.assertRaises(ValidationError):
            WalkForwardBacktestRequest(strategy="T" * 51)
        with self.assertRaises(ValidationError):
            RunAllStrategiesRequest(symbol="S" * 21)
        with self.assertRaises(ValidationError):
            RunAllStrategiesRequest(market="m" * 8)
        with self.assertRaises(ValidationError):
            RunAllStrategiesRequest(timeframe="t" * 11)
        with self.assertRaises(ValidationError):
            RunAllStrategiesRequest(data_segment="d" * 11)

    def test_ml_deploy_model_id_matches_paper_config_column_width(self):
        DeployMLToPaperRequest(model_id="m" + "x" * 99)
        with self.assertRaises(ValidationError):
            DeployMLToPaperRequest(model_id="m" + "x" * 100)

    def test_persistence_helper_rejects_overlong_values_before_database_access(self):
        valid = {
            "symbol": "BTCUSDT",
            "market": "spot",
            "timeframe": "1h",
            "strategy_name": "trend_ema_v1",
            "is_active": True,
        }
        invalid_values = (
            ("symbol", "S" * 21),
            ("market", "m" * 8),
            ("timeframe", "t" * 11),
            ("strategy_name", "T" * 51),
            ("ml_model_id", "m" * 101),
        )
        for field_name, value in invalid_values:
            with self.subTest(field=field_name):
                with patch("paper.engine.get_pool") as get_pool:
                    with self.assertRaises(ValueError):
                        set_paper_config(**{**valid, field_name: value})
                get_pool.assert_not_called()


if __name__ == "__main__":
    unittest.main()
