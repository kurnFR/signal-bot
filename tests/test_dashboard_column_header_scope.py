"""Regression checks for explicit dashboard table column-header semantics."""

from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ENHANCEMENTS_JS = REPO_ROOT / "web" / "static" / "js" / "dashboard_enhancements.js"


class TestDashboardColumnHeaderScope(unittest.TestCase):
    def test_all_first_row_header_cells_receive_column_scope_before_sort_filtering(self):
        source = DASHBOARD_ENHANCEMENTS_JS.read_text(encoding="utf-8")
        start = source.index("Array.from(thead.rows[0].cells).forEach((th, index) => {")
        end = source.index("th.addEventListener(\"click\"", start)
        header_setup = source[start:end]

        scope_assignment = 'if (!th.hasAttribute("scope")) th.setAttribute("scope", "col");'
        sortable_filter = 'if (!label || SORTABLE_EXCLUDES.has(label) || th.querySelector("button")) return;'
        self.assertIn(scope_assignment, header_setup)
        self.assertIn(sortable_filter, header_setup)
        self.assertLess(header_setup.index(scope_assignment), header_setup.index(sortable_filter))

    def test_existing_header_scope_is_preserved(self):
        source = DASHBOARD_ENHANCEMENTS_JS.read_text(encoding="utf-8")
        self.assertIn('if (!th.hasAttribute("scope")) th.setAttribute("scope", "col");', source)


if __name__ == "__main__":
    unittest.main()
