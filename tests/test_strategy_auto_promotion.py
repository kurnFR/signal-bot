from unittest.mock import patch

from fastapi import HTTPException

from db.db import set_strategy_auto_promotion_policy

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


def _call(candidate, data_segment="holdout", validation_metrics=None, validation_segment=None):
    return _auto_promote_top_candidate(
        candidate,
        symbol="BTCUSDT",
        market="spot",
        timeframe="1d",
        data_segment=data_segment,
        allocated_capital=5000.0,
        risk_per_trade_pct=1.0,
        validation_metrics=validation_metrics,
        validation_segment=validation_segment,
    )


def test_auto_promotion_is_disabled_by_default():
    with patch("web.routes.backtest.get_strategy_auto_promotion_policy", return_value=_policy(enabled=False)):
        result = _call(_candidate())
    assert result["status"] == "disabled"



def test_auto_promotion_fails_closed_when_persisted_policy_thresholds_are_invalid():
    invalid_policies = (
        _policy(min_profit_factor=float("nan")),
        _policy(min_profit_factor=0.5),
        _policy(max_drawdown_r=0.0),
        _policy(min_rank_score=2.0),
        _policy(enabled="false"),
        _policy(require_holdout="false"),
        _policy(send_telegram="false"),
    )
    for invalid_policy in invalid_policies:
        with patch("web.routes.backtest.get_strategy_auto_promotion_policy", return_value=invalid_policy), \
             patch("web.routes.backtest.record_strategy_promotion_decision") as record, \
             patch("paper.engine.set_paper_config") as set_config, \
             patch("notifications.telegram_notifier.send_deployment_alert") as alert:
            result = _call(_candidate(), data_segment="train", validation_metrics={
                "total_trades": 40,
                "expectancy_r": 0.2,
                "profit_factor": 1.5,
                "max_drawdown_r": 2.0,
            }, validation_segment="holdout")

        assert result["status"] == "error"
        assert "policy" in result["reason"].lower()
        record.assert_not_called()
        set_config.assert_not_called()
        alert.assert_not_called()


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

def test_auto_promotion_uses_separate_holdout_metrics_for_train_selected_winner():
    validation_metrics = {
        "total_trades": 35,
        "expectancy_r": 0.12,
        "profit_factor": 1.32,
        "max_drawdown_r": 4.0,
    }
    with patch("web.routes.backtest.get_strategy_auto_promotion_policy", return_value=_policy()), \
         patch("web.routes.backtest.record_strategy_promotion_decision", return_value=True) as record, \
         patch("web.routes.backtest.update_strategy_promotion_decision") as update, \
         patch("paper.engine.set_paper_config", return_value={"status": "success", "id": 42}) as set_config, \
         patch("notifications.telegram_notifier.send_deployment_alert", return_value={"success": True}):
        result = _call(
            _candidate(),
            data_segment="train",
            validation_metrics=validation_metrics,
            validation_segment="holdout",
        )

    assert result["status"] == "promoted"
    assert set_config.call_args.kwargs["is_active"] is True
    decision = record.call_args.args[0]
    assert decision["data_segment"] == "holdout"
    assert '"selection_segment": "train"' in decision["metrics_json"]
    assert '"validation_segment": "holdout"' in decision["metrics_json"]
    assert update.call_args.kwargs["status"] == "promoted"
    assert update.call_args.kwargs["paper_config_id"] == 42


def test_auto_promotion_rejects_candidate_when_holdout_metrics_fail_even_if_train_passes():
    validation_metrics = {
        "total_trades": 35,
        "expectancy_r": 0.12,
        "profit_factor": 0.8,
        "max_drawdown_r": 4.0,
    }
    with patch("web.routes.backtest.get_strategy_auto_promotion_policy", return_value=_policy()), \
         patch("web.routes.backtest.record_strategy_promotion_decision", return_value=True), \
         patch("paper.engine.set_paper_config") as set_config:
        result = _call(
            _candidate(),
            data_segment="train",
            validation_metrics=validation_metrics,
            validation_segment="holdout",
        )

    assert result["status"] == "rejected"
    assert "profit factor" in result["reason"].lower()
    set_config.assert_not_called()


def test_auto_promotion_activates_train_candidate_only_when_holdout_gate_is_explicitly_disabled():
    with patch("web.routes.backtest.get_strategy_auto_promotion_policy", return_value=_policy(require_holdout=False)), \
         patch("web.routes.backtest.record_strategy_promotion_decision", return_value=True), \
         patch("web.routes.backtest.update_strategy_promotion_decision") as update, \
         patch("web.routes.backtest.get_strategy_promotion_decision", return_value=None), \
         patch("paper.engine.set_paper_config", return_value={"status": "success", "id": 42}) as set_config, \
         patch("notifications.telegram_notifier.send_deployment_alert", return_value={"success": True}):
        result = _call(_candidate(), data_segment="train")

    assert result["status"] == "promoted"
    assert result["telegram_result"] == "sent"
    assert set_config.call_args.kwargs["is_active"] is True
    assert set_config.call_args.kwargs["telegram_enabled"] is True
    assert update.call_args.kwargs["status"] == "promoted"



def test_auto_promotion_preserves_activation_outcome_when_final_audit_write_fails():
    with patch("web.routes.backtest.get_strategy_auto_promotion_policy", return_value=_policy(require_holdout=False)), \
         patch("web.routes.backtest.record_strategy_promotion_decision", return_value=True), \
         patch("web.routes.backtest.update_strategy_promotion_decision", side_effect=RuntimeError("audit store unavailable")) as update, \
         patch("paper.engine.set_paper_config", return_value={"status": "success", "id": 42}), \
         patch("notifications.telegram_notifier.send_deployment_alert", return_value={"success": True}) as alert:
        result = _call(_candidate(), data_segment="train")

    assert result["status"] == "promoted_audit_pending"
    assert result["telegram_result"] == "sent"
    assert "paper config is active" in result["reason"].lower()
    assert "reconcile" in result["reason"].lower()
    alert.assert_called_once()
    update.assert_called_once()


def test_auto_promotion_requires_reconciliation_if_config_id_unconfirmed_and_audit_write_fails():
    with patch("web.routes.backtest.get_strategy_auto_promotion_policy", return_value=_policy(require_holdout=False)), \
         patch("web.routes.backtest.record_strategy_promotion_decision", return_value=True), \
         patch("web.routes.backtest.update_strategy_promotion_decision", side_effect=RuntimeError("audit store unavailable")), \
         patch("paper.engine.set_paper_config", return_value={"status": "success", "id": None}), \
         patch("notifications.telegram_notifier.send_deployment_alert") as alert:
        result = _call(_candidate(), data_segment="train")

    assert result["status"] == "reconciliation_required"
    assert "reconcile paper_configs" in result["reason"].lower()
    assert result["telegram_result"] == "not_attempted"
    alert.assert_not_called()

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


def test_auto_promotion_reports_processing_decision_as_in_progress():
    with patch("web.routes.backtest.get_strategy_auto_promotion_policy", return_value=_policy(require_holdout=False)), \
         patch("web.routes.backtest.record_strategy_promotion_decision", return_value=False), \
         patch("web.routes.backtest.get_strategy_promotion_decision", return_value={
             "status": "processing", "reason": "claim persisted before activation"
         }), \
         patch("paper.engine.set_paper_config") as set_config:
        result = _call(_candidate(), data_segment="train")

    assert result["status"] == "in_progress"
    assert "reconcile" in result["reason"].lower()
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



def test_auto_promotion_rejects_strategy_outside_executable_registry():
    candidate = _candidate(strategy="unregistered_strategy_v99")
    validation_metrics = {
        "total_trades": 35,
        "expectancy_r": 0.12,
        "profit_factor": 1.32,
        "max_drawdown_r": 4.0,
    }
    with patch("web.routes.backtest.get_strategy_auto_promotion_policy", return_value=_policy()), \
         patch("web.routes.backtest.record_strategy_promotion_decision", return_value=True), \
         patch("paper.engine.set_paper_config") as set_config:
        result = _call(
            candidate,
            data_segment="train",
            validation_metrics=validation_metrics,
            validation_segment="holdout",
        )

    assert result["status"] == "rejected"
    assert "executable paper-trading registry" in result["reason"].lower()
    set_config.assert_not_called()


def test_auto_promotion_fails_closed_when_policy_store_is_unavailable():
    with patch(
        "web.routes.backtest.get_strategy_auto_promotion_policy",
        side_effect=RuntimeError("database unavailable"),
    ), patch("paper.engine.set_paper_config") as set_config:
        result = _call(_candidate())

    assert result["status"] == "error"
    assert "policy unavailable" in result["reason"].lower()
    set_config.assert_not_called()


def test_auto_promotion_fails_closed_when_audit_claim_cannot_be_persisted():
    validation_metrics = {
        "total_trades": 35,
        "expectancy_r": 0.12,
        "profit_factor": 1.32,
        "max_drawdown_r": 4.0,
    }
    with patch("web.routes.backtest.get_strategy_auto_promotion_policy", return_value=_policy()), \
         patch(
             "web.routes.backtest.record_strategy_promotion_decision",
             side_effect=RuntimeError("audit database unavailable"),
         ), patch("paper.engine.set_paper_config") as set_config:
        result = _call(
            _candidate(),
            data_segment="train",
            validation_metrics=validation_metrics,
            validation_segment="holdout",
        )

    assert result["status"] == "error"
    assert "promotion audit" in result["reason"].lower()
    set_config.assert_not_called()


def test_auto_promotion_does_not_report_success_without_persisted_config_id():
    validation_metrics = {
        "total_trades": 35,
        "expectancy_r": 0.12,
        "profit_factor": 1.32,
        "max_drawdown_r": 4.0,
    }
    with patch("web.routes.backtest.get_strategy_auto_promotion_policy", return_value=_policy()), \
         patch("web.routes.backtest.record_strategy_promotion_decision", return_value=True), \
         patch("web.routes.backtest.update_strategy_promotion_decision") as update, \
         patch("paper.engine.set_paper_config", return_value={"status": "success", "id": None}), \
         patch("notifications.telegram_notifier.send_deployment_alert") as alert:
        result = _call(
            _candidate(),
            data_segment="train",
            validation_metrics=validation_metrics,
            validation_segment="holdout",
        )

    assert result["status"] == "reconciliation_required"
    assert "reconcil" in result["reason"].lower()
    assert update.call_args.kwargs["status"] == "reconciliation_required"
    assert update.call_args.kwargs["paper_config_id"] is None
    assert update.call_args.kwargs["telegram_result"] == "not_attempted"
    alert.assert_not_called()


def test_auto_promotion_fails_closed_when_telegram_result_does_not_confirm_success():
    with patch("web.routes.backtest.get_strategy_auto_promotion_policy", return_value=_policy(require_holdout=False)), \
         patch("web.routes.backtest.record_strategy_promotion_decision", return_value=True), \
         patch("web.routes.backtest.update_strategy_promotion_decision") as update, \
         patch("paper.engine.set_paper_config", return_value={"status": "success", "id": 42}), \
         patch("notifications.telegram_notifier.send_deployment_alert", return_value=None):
        result = _call(_candidate(), data_segment="train")

    assert result["status"] == "promoted_alert_failed"
    assert result["telegram_result"] == "failed"
    assert "delivery reported failure" in result["reason"].lower()
    assert update.call_args.kwargs["status"] == "promoted_alert_failed"
    assert update.call_args.kwargs["telegram_result"] == "failed"


def test_auto_promotion_requires_reconciliation_when_activation_raises():
    with (
        patch("web.routes.backtest.get_strategy_auto_promotion_policy", return_value=_policy(require_holdout=False)),
        patch("web.routes.backtest.record_strategy_promotion_decision", return_value=True),
        patch("web.routes.backtest.update_strategy_promotion_decision") as update,
        patch("paper.engine.set_paper_config", side_effect=RuntimeError("connection lost after write")),
        patch("notifications.telegram_notifier.send_deployment_alert") as alert,
    ):
        result = _call(_candidate(), data_segment="train")

    assert result["status"] == "reconciliation_required"
    assert "reconcile" in result["reason"].lower()
    assert update.call_args.kwargs["status"] == "reconciliation_required"
    assert update.call_args.kwargs["telegram_result"] == "unknown"
    alert.assert_not_called()

def test_auto_promotion_reports_audit_pending_when_final_update_changes_no_row():
    with patch("web.routes.backtest.get_strategy_auto_promotion_policy", return_value=_policy(require_holdout=False)), \
         patch("web.routes.backtest.record_strategy_promotion_decision", return_value=True), \
         patch("web.routes.backtest.update_strategy_promotion_decision", return_value=False) as update, \
         patch("paper.engine.set_paper_config", return_value={"status": "success", "id": 42}), \
         patch("notifications.telegram_notifier.send_deployment_alert", return_value={"success": True}) as alert:
        result = _call(_candidate(), data_segment="train")

    assert result["status"] == "promoted_audit_pending"
    assert result["telegram_result"] == "sent"
    assert "audit" in result["reason"].lower()
    assert "reconcile" in result["reason"].lower()
    alert.assert_called_once()
    update.assert_called_once()


def test_policy_db_writer_rejects_malformed_boolean_flags_before_database_access():
    valid_policy = {
        "enabled": True,
        "min_test_trades": 30,
        "min_profit_factor": 1.2,
        "min_expectancy_r": 0.05,
        "max_drawdown_r": 8.0,
        "min_rank_score": 0.1,
        "require_holdout": True,
        "send_telegram": True,
    }
    for key in ("enabled", "require_holdout", "send_telegram"):
        malformed = dict(valid_policy)
        malformed[key] = "false"
        with patch("db.db.get_pool") as get_pool:
            try:
                set_strategy_auto_promotion_policy(malformed)
            except ValueError as exc:
                assert key in str(exc)
            else:
                raise AssertionError(f"malformed {key} flag was accepted")
        get_pool.assert_not_called()



def test_policy_db_writer_rejects_invalid_numeric_values_before_database_access():
    valid_policy = {
        "enabled": True,
        "min_test_trades": 30,
        "min_profit_factor": 1.2,
        "min_expectancy_r": 0.05,
        "max_drawdown_r": 8.0,
        "min_rank_score": 0.1,
        "require_holdout": True,
        "send_telegram": True,
    }
    invalid_values = {
        "min_test_trades": (29, 10001, 30.5, "30", True),
        "min_profit_factor": (float("nan"), float("inf"), 0.9, 10.1, "1.2"),
        "min_expectancy_r": (float("nan"), -0.1, 10.1, "0.05"),
        "max_drawdown_r": (float("nan"), 0, 100.1, "8.0"),
        "min_rank_score": (float("nan"), 0, 1.1, "0.1"),
    }
    for key, values in invalid_values.items():
        for value in values:
            malformed = dict(valid_policy)
            malformed[key] = value
            with patch("db.db.get_pool") as get_pool:
                try:
                    set_strategy_auto_promotion_policy(malformed)
                except (TypeError, ValueError) as exc:
                    assert key in str(exc) or "invalid" in str(exc).lower()
                else:
                    raise AssertionError(f"invalid {key} value {value!r} was accepted")
            get_pool.assert_not_called()
