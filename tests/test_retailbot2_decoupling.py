"""Guards the RetailBot2 decoupling: shared code must not import the retired worker."""
import subprocess
import sys


def _imports_retailbot2(code: str) -> bool:
    out = subprocess.run(
        [sys.executable, "-c", code + "\nimport sys\nprint('paper.retailbot2' in sys.modules)"],
        capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()[-1]
    return out == "True"


def test_indicators_module_is_standalone():
    assert not _imports_retailbot2("import paper.indicators")


def test_backtest_strategies_do_not_import_retailbot2_worker():
    assert not _imports_retailbot2("import backtest.strategies")


def test_paper_report_module_is_standalone():
    assert not _imports_retailbot2("import paper.paper_report")


def test_retailbot2_reexports_indicators_for_compatibility():
    from paper.indicators import Indicators as shared
    from paper.retailbot2 import Indicators as legacy
    assert shared is legacy


def test_telegram_alert_delegates_to_shared_report(monkeypatch):
    import paper.paper_report as report
    from paper.retailbot2 import TelegramAlert
    monkeypatch.setattr(report, "paper_performance_lines", lambda: ["LINE"])
    assert "LINE" in TelegramAlert.format_paper_report()
    assert "LINE" in report.format_paper_report()
