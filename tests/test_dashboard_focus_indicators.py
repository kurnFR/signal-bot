"""Regression tests for theme-aware dashboard keyboard focus indicators."""

from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = REPO_ROOT / "web" / "static" / "index.html"


class TestDashboardFocusIndicators(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index_html = INDEX_HTML.read_text(encoding="utf-8")

    def test_global_focus_indicator_uses_theme_focus_color(self):
        style = re.search(r"<style>(.*?)</style>", self.index_html, re.DOTALL)
        self.assertIsNotNone(style)
        focus_rule = re.search(r":focus-visible:not\(#main-content\)\s*\{([^}]*)\}", style.group(1))
        self.assertIsNotNone(focus_rule)
        self.assertRegex(
            focus_rule.group(1),
            r"outline:\s*2px solid var\(--dashboard-focus,\s*#38bdf8\)\s*!important;",
        )

    def test_forced_colors_mode_keeps_system_focus_color(self):
        self.assertRegex(
            self.index_html,
            r"@media\s*\(forced-colors:\s*active\)\s*\{\s*:focus-visible:not\(#main-content\)\s*\{\s*outline-color:\s*Highlight\s*!important;",
        )


if ( __name__ == "__main__" ):
    unittest.main()
