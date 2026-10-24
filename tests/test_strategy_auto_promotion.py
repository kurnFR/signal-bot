from unittest.mock import patch

from fastapi import HTTPException

from web.routes.backtest import (
    _auto_promote_top_candidate,
    StrategyAutoPromotionPolicyRequest,
    get_promotion_policy,
    update_promotion_policy,
)


def _candidate(**overrides):
    candidate = {
        "strategy": "trend_ema_v1",
        "rankScore": 0.5,
        "metrics": {
            "total_trades": 40,
            "expectancy_r": 0.2,
            "profit_factor": 1.5,
            "max_drawdown_r": 2.0,
        },
    }
    candidate.update(overrides)
    return candidate


def _policy(**overrides):
    policy = {
        "enabled": True,
        "min_test_trades": 30,
        "min_profit_factor": 1.2,
        "min_expectancy_r": 0.05,
        "max_drawdown_r": 8.0,
        "min_rank_score": 0.1,
        "require_holdout": True,
        "send_telegram": True,
    }
    policy.update(overrides)
    return policy


def _call(candidate, data_segment="holdout"):
    return _auto_promote_top_candidate(
        candidate,
        symbol="BTCUSDT",
        market="spot",
        timeframe="1d",
        data_segment=data_segment,
        allocated_capital=5000.0,
        risk_per_trade_pct=1.0,
    )


def test_auto_promotion_is_disabled_by_default():
    with patch("web.routes.backtest.get_strategy_auto_promotion_policy", return_value=_policy(enabled=False)):
        result = _call(_candidate())
    assert result["status"] == "disabled"


def test_auto_promotion_rejects_non_holdout_when_required():
    with patch("web.routes.backtest.get_strategy_auto_promotion_policy", return_value=_policy()), \
         patch("web.routes.backtest.record_strategy_promotion_decision", return_value=True) as record, \
         patch("web.routes.backtest.set_strategy_auto_promotion_policy"), \
         patch("paper.engine.set_paper_config") as set_config:
        result = _call(_candidate(), data_segment="full")

    assert result["status"] == "rejected"
    assert "holdout" in result["reason"].lower()
    record.assert_called_once()
    set_config.assert_not_called()



def test_auto_promotion_rejects_candidate_ranked_on_the_same_holdout():
    with patch("web.routes.backtest.get_strategy_auto_promotion_policy", return_value=_policy()), patch(
        "web.routes.backtest.record_strategy_promotion_decision", return_value=True
    ), patch("paper.engine.set_paper_config") as set_config:
        result = _call(_candidate(), data_segment="holdout")

    assert result["status"] == "rejected"
    assert "same holdout" in result["reason"].lower()
    set_config.assert_not_called()

def test_auto_promotion_activates_train_candidate_only_when_holdout_gate_is_explicitly_disabled():
    with patch("web.routes.backtest.get_strategy_auto_promotion_policy", return_value=_policy(require_holdout=False)), \
         patch("web.routes.backtest.record_strategy_promotion_decision", return_value=True), \
         patch("web.routes.backtest.update_strategy_promotion_decision") as update, \
         patch("web.routes.backtest.get_strategy_promotion_decision", return_value=None), \
         patch("paper.engine.set_paper_config", return_value={"status": "success"}) as set_config, \
         patch("notifications.telegram_notifier.send_deployment_alert", return_value={"success": True}):
        result = _call(_candidate(), data_segment="train")

    assert result["status"] == "promoted"
    assert result["telegram_result"] == "sent"
    assert set_config.call_args.kwargs["is_active"] is True
    assert set_config.call_args.kwargs["telegram_enabled"] is True
    assert update.call_args.kwargs["status"] == "promoted"


def test_auto_promotion_does_not_repeat_identical_decision():
    with patch("web.routes.backtest.get_strategy_auto_promotion_policy", return_value=_policy(require_holdout=False)), \
         patch("web.routes.backtest.record_strategy_promotion_decision", return_value=False), \
         patch("web.routes.backtest.get_strategy_promotion_decision", return_value={
             "status": "promoted", "reason": "already done"
         }), \
         patch("paper.engine.set_paper_config") as set_config:
        result = _call(_candidate(), data_segment="train")

    assert result["status"] == "already_processed"
    set_config.assert_not_called()


def test_auto_promotion_rejects_unprofitable_metrics_even_if_candidate_is_top_ranked():
    candidate = _candidate(metrics={
        "total_trades": 40,
        "expectancy_r": 0.2,
        "profit_factor": 0.8,
        "max_drawdown_r": 2.0,
    })
    with patch("web.routes.backtest.get_strategy_auto_promotion_policy", return_value=_policy()), \
         patch("web.routes.backtest.record_strategy_promotion_decision", return_value=True), \
         patch("paper.engine.set_paper_config") as set_config:
        result = _call(candidate)

    assert result["status"] == "rejected"
    assert "profit factor" in result["reason"].lower()
    set_config.assert_not_called()


def test_promotion_policy_get_returns_saved_ui_policy():
    policy = _policy(enabled=True, min_profit_factor=1.35)
    with patch("web.routes.backtest.get_strategy_auto_promotion_policy", return_value=policy):
        response = get_promotion_policy()
    assert response["policy"]["enabled"] is True
    assert response["policy"]["min_profit_factor"] == 1.35


def test_promotion_policy_put_persists_user_configured_criteria():
    request = StrategyAutoPromotionPolicyRequest(
        enabled=True,
        min_test_trades=45,
        min_profit_factor=1.4,
        min_expectancy_r=0.1,
        max_drawdown_r=5.0,
        min_rank_score=0.25,
        require_holdout=True,
        send_telegram=False,
    )
    with patch("web.routes.backtest.set_strategy_auto_promotion_policy") as save, \
         patch("web.routes.backtest.get_strategy_auto_promotion_policy", return_value=_policy(
             enabled=True, min_test_trades=45, min_profit_factor=1.4,
             min_expectancy_r=0.1, max_drawdown_r=5.0,
             min_rank_score=0.25, send_telegram=False,
         )):
        response = update_promotion_policy(request)
    save.assert_called_once()
    assert save.call_args.args[0]["min_test_trades"] == 45
    assert save.call_args.args[0]["send_telegram"] is False
    assert response["policy"]["enabled"] is True


def test_promotion_policy_put_rejects_non_finite_thresholds():
    request = StrategyAutoPromotionPolicyRequest.construct(
        enabled=True,
        min_test_trades=30,
        min_profit_factor=float("nan"),
        min_expectancy_r=0.05,
        max_drawdown_r=8.0,
        min_rank_score=0.1,
        require_holdout=True,
        send_telegram=True,
    )
    with patch("web.routes.backtest.set_strategy_auto_promotion_policy") as save:
        try:
            update_promotion_policy(request)
        except HTTPException as exc:
            assert exc.status_code == 400
        else:
            raise AssertionError("non-finite policy threshold was accepted")
    save.assert_not_called()
