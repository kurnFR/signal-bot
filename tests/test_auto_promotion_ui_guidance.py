from pathlib import Path


def test_backtest_auto_promotion_ui_explains_train_selection_and_holdout_validation():
    html = Path("web/static/index.html").read_text(encoding="utf-8")

    assert "For auto-promotion, select Train to rank candidates." in html
    assert "evaluated separately on Holdout" in html
    assert "Full/Holdout-selected runs are analysis-only for promotion." in html
    assert "Default requires the holdout segment." not in html
