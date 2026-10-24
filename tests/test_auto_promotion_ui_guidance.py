from pathlib import Path


def test_backtest_auto_promotion_ui_explains_train_selection_and_holdout_validation():
    html = Path("web/static/index.html").read_text(encoding="utf-8")

    assert "For auto-promotion, select Train to rank candidates." in html
    assert "evaluated separately on Holdout" in html
    assert "Full/Holdout-selected runs are analysis-only for promotion." in html
    assert "Default requires the holdout segment." not in html


def test_retailbot2_lifecycle_ui_does_not_claim_auto_promotion_is_available():
    html = Path("web/static/index.html").read_text(encoding="utf-8")

    assert "RetailBot2 automatic promotion is not available yet" in html
    assert "until the validated promotion workflow is implemented" in html
    assert "Automatic promotion is opt-in and configured in Backtest Studio." not in html


def test_retailbot2_lifecycle_has_one_click_stop_and_hide_without_deleting_history():
    js = Path("web/static/js/app.js").read_text(encoding="utf-8")
    html = Path("web/static/index.html").read_text(encoding="utf-8")

    assert "data-strategy-lifecycle-stop-hide" in js
    assert "stopAndHideStrategy(button.dataset.strategyId, button.dataset.strategyMode)" in js
    assert '["evaluation_enabled", "paper_enabled", "telegram_enabled", "report_enabled"]' in js
    assert "await this.saveStrategyLifecycle(strategyId, mode);" in js
    assert "Stop &amp; Hide" in js
    assert 'app.js?v=20261025-2' in html
