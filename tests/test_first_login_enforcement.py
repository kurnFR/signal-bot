import unittest
from pathlib import Path

from fastapi import HTTPException

from web.routes.auth_routes import _validate_new_password

ROOT = Path(__file__).resolve().parents[1]


class TestPasswordPolicy(unittest.TestCase):
    def test_rejects_short_common_and_username_passwords(self):
        for bad in ("short1", "password123", "password1234", "Password1234!", "P@ssw0rd2024", "admin1234", "aaaaaaaaaaaa",
                    "1234567890", "abcdefghijk", "qwerty12345", "Bitcoin2024!!", "Welcome123456"):
            with self.assertRaises(HTTPException, msg=bad) as ctx:
                _validate_new_password(bad, "someone")
            self.assertEqual(ctx.exception.status_code, 400)
        with self.assertRaises(HTTPException):
            _validate_new_password("administrator", "administrator")

    def test_accepts_reasonable_password(self):
        for good in ("Correct-horse-battery-9", "Strong-New-Pass-77", "tr1cky-Mango-river-42"):
            _validate_new_password(good, "admin")


class TestForcedPasswordDashboard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = (ROOT / "web/static/js/app.js").read_text(encoding="utf-8")
        cls.html = (ROOT / "web/static/index.html").read_text(encoding="utf-8")

    def test_modal_is_an_accessible_blocking_dialog(self):
        start = self.html.index('id="force-password-modal"')
        block = self.html[start:start + 4500]
        self.assertIn('role="dialog"', block)
        self.assertIn('aria-modal="true"', block)
        self.assertIn('aria-labelledby="force-password-title"', block)
        for field in ("force-old-pwd", "force-new-pwd", "force-conf-pwd"):
            self.assertIn(f'<label for="{field}"', block)
        self.assertNotIn("<button>", block)  # every button declares a type

    def test_login_and_session_restore_both_enforce_the_flag(self):
        self.assertIn("enforcePasswordChangeIfRequired()", self.js)
        login = self.js[self.js.index("async handleLogin()"):self.js.index("async handleLogout()")]
        self.assertIn("this.enforcePasswordChangeIfRequired()", login)
        check = self.js[self.js.index("async checkAuth()"):self.js.index("    showLoginModal() {")]
        self.assertIn("this.enforcePasswordChangeIfRequired()", check)

    def test_data_loads_only_for_usable_sessions_and_after_every_sign_in_path(self):
        init = self.js[self.js.index("    async init() {"):self.js.index("setInterval(async () => {")]
        self.assertIn("const sessionUsable = await this.checkAuth()", init)
        self.assertIn("if (sessionUsable)", init)
        self.assertNotIn('this.switchTab("home")', init)  # anonymous visitors must not trigger data loads
        for fn_start, fn_end in (("async handleLogin()", "async handleLogout()"),
                                 ("async handleForcedPasswordChange()", "async handleChangeMyPassword()")):
            body = self.js[self.js.index(fn_start):self.js.index(fn_end)]
            self.assertIn("await this.loadSessionData()", body, fn_start)
        loader = self.js[self.js.index("async loadSessionData()"):self.js.index("async checkAuth()")]
        self.assertIn('this.switchTab(this.currentTab || "home")', loader)

    def test_logout_closes_the_forced_dialog(self):
        logout = self.js[self.js.index("async handleLogout()"):self.js.index("    updateUserUI() {")]
        self.assertIn("this.hideForcedPasswordModal()", logout)


if __name__ == "__main__":
    unittest.main()
