"""ML request schemas reject invalid identifiers and unsupported training options."""
import unittest

from pydantic import ValidationError

from web.routes.ml_routes import (
    MLWalkForwardRequest,
    RLTrainRequest,
    RegisterRetrainRequest,
    RunExperimentRequest,
    RunGeneratorExperimentRequest,
)


class TestMLRequestValidation(unittest.TestCase):
    def test_valid_defaults_remain_accepted(self):
        self.assertEqual(RunExperimentRequest().symbol, "BTCUSDT")
        self.assertEqual(MLWalkForwardRequest().market, "spot")
        self.assertEqual(RegisterRetrainRequest().timeframe, "1h")
        self.assertEqual(RLTrainRequest().agent_type, "dqn")


    def test_common_market_fields_are_normalized(self):
        request = RunExperimentRequest(symbol=" btcusdt ", market="SPOT", timeframe=" 1h ")
        self.assertEqual(request.symbol, "BTCUSDT")
        self.assertEqual(request.market, "spot")
        self.assertEqual(request.timeframe, "1h")

    def test_experiment_request_rejects_invalid_symbol_market_timeframe(self):
        for payload in (
            {"symbol": "BTC-USDT"},
            {"symbol": "X" * 21},
            {"market": "margin"},
            {"timeframe": "2d"},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                RunExperimentRequest(**payload)

    def test_experiment_request_rejects_unknown_strategy_model_and_target(self):
        for payload in (
            {"strategy_name": "not_registered"},
            {"model_type": "unsupported_model"},
            {"target_type": "arbitrary_target"},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                RunExperimentRequest(**payload)

    def test_walk_forward_request_rejects_unsupported_options(self):
        for payload in (
            {"market": "margin"},
            {"timeframe": "2d"},
            {"strategy_name": "not_registered"},
            {"model_type": "unsupported_model"},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                MLWalkForwardRequest(**payload)

    def test_retraining_registration_rejects_invalid_configuration(self):
        for payload in (
            {"symbol": "BTC/USDT"},
            {"market": "margin"},
            {"timeframe": "2d"},
            {"strategy_name": "not_registered"},
            {"model_type": "unsupported_model"},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                RegisterRetrainRequest(**payload)

    def test_rl_request_rejects_unsupported_agent_and_reward(self):
        for payload in (
            {"symbol": "BTC-USDT"},
            {"market": "margin"},
            {"timeframe": "2d"},
            {"agent_type": "unsupported_agent"},
            {"reward_type": "r_multiple"},
            {"episodes": 51},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                RLTrainRequest(**payload)


    def test_generator_experiment_request_defaults_and_normalizes_market_fields(self):
        request = RunGeneratorExperimentRequest(
            symbol=" btcusdt ",
            market="SPOT",
            timeframe=" 1h ",
        )
        self.assertEqual(request.symbol, "BTCUSDT")
        self.assertEqual(request.market, "spot")
        self.assertEqual(request.timeframe, "1h")
        self.assertEqual(request.model_type, "hist_gradient_boosting")
        self.assertEqual(request.horizon_bars, 12)

    def test_generator_experiment_request_rejects_invalid_parameters(self):
        for payload in (
            {"model_type": "unsupported"},
            {"horizon_bars": 1},
            {"barrier_atr_multiple": 0},
            {"fee_rate": -0.01},
            {"slippage_rate": 0.2},
            {"train_fraction": 0.9, "validation_fraction": 0.2},
            {"max_hold_bars": 0},
            {"risk_reward_ratio": 0},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                RunGeneratorExperimentRequest(**payload)


if __name__ == "__main__":
    unittest.main()
