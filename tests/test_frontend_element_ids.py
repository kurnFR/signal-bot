"""Static guard: JS must not write to elements that the HTML does not contain.

A single unguarded `getElementById("x").innerText = ...` on a missing id throws and
aborts the rest of that render (this broke the Settings page once).
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "web" / "static"
WRITE = re.compile(r'getElementById\(["\']([^"\']+)["\']\)\.(?:innerText|innerHTML|textContent|value|className|disabled)\s*=')


class TestFrontendElementIds(unittest.TestCase):
    def test_unguarded_writes_target_existing_ids(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "js" / "app.js").read_text(encoding="utf-8")
        html_ids = set(re.findall(r'id="([^"]+)"', html))
        # ids created at runtime by template strings in the JS itself
        js_ids = set(re.findall(r'id=\\?"([^"\\$]+)\\?"', js))
        missing = sorted({m.group(1) for m in WRITE.finditer(js)} - html_ids - js_ids)
        self.assertEqual(missing, [], f"app.js writes to ids absent from the markup: {missing}")

    def test_settings_status_does_not_expose_the_database_user(self):
        src = (Path(__file__).resolve().parents[1] / "web" / "routes" / "settings.py").read_text(encoding="utf-8")
        self.assertNotIn('MYSQL_CONFIG["user"]', src)


if __name__ == "__main__":
    unittest.main()
