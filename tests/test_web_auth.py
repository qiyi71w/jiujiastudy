"""聚焦测试：WebAuth 单账号凭据生命周期与安全边界。"""
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import hashlib
import io
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

try:
    import argon2
except ImportError:
    argon2 = None

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))
from cc_web_auth import WebAuth, main  # noqa: E402


@unittest.skipIf(argon2 is None, "argon2-cffi 未安装，跳过真实密码哈希测试")
class TestWebAuthLifecycle(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="webauth-test-")
        self.account_id = "test-acc-01"
        self.auth = WebAuth(self.test_dir, self.account_id)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_provision_and_login_lifecycle(self):
        self.auth.provision("testuser", "secure_password_123")
        res = self.auth.login("testuser", "secure_password_123")
        self.assertIn("token", res)
        self.assertIn("csrf", res)
        self.assertEqual(res["username"], "testuser")
        self.assertIn("expires_at", res)
        self.assertNotIn("password_hash", res)
        self.assertNotIn("hash", res)

        session = self.auth.authenticate(res["token"])
        self.assertEqual(session["username"], "testuser")
        self.assertEqual(session["csrf"], res["csrf"])
        self.assertEqual(session["expires_at"], res["expires_at"])
        self.assertNotIn("password_hash", session)
        self.assertNotIn("hash", session)

    def test_invalid_login_credentials(self):
        self.auth.provision("testuser", "secure_password_123")
        with self.assertRaises(PermissionError):
            self.auth.login("testuser", "wrong_password_xyz")
        with self.assertRaises(PermissionError):
            self.auth.login("nonexistent_user", "secure_password_123")

    def test_failures_throttle_verification_without_locking_out_correct_password(self):
        self.auth.provision("testuser", "secure_password_123")
        clock = [self.auth._now_utc()]
        def advance(seconds):
            clock[0] += dt.timedelta(seconds=seconds)
        with patch.object(WebAuth, "_now_utc", side_effect=lambda: clock[0]), patch("cc_web_auth.time.sleep", side_effect=advance) as sleep:
            for index in range(12):
                username = "unknown_user" if index < 6 else "testuser"
                with self.assertRaises(PermissionError):
                    self.auth.login(username, "bad_pass_12345")
            data = self.auth._load_data()
            self.assertLessEqual(len(data["rate_limit"]["failures"]), 5)
            self.assertIn("next_attempt_at", data["rate_limit"])
            self.assertGreaterEqual(sleep.call_count, 7)
            self.assertTrue(all(0 < call.args[0] <= 5 for call in sleep.call_args_list))
            self.assertEqual("testuser", self.auth.login("testuser", "secure_password_123")["username"])
        self.assertEqual({"failures": []}, self.auth._load_data()["rate_limit"])

    def test_anonymous_failures_do_not_block_authenticated_password_change(self):
        self.auth.provision("testuser", "secure_password_123")
        session = self.auth.login("testuser", "secure_password_123")
        for _ in range(5):
            with self.assertRaises(PermissionError):
                self.auth.login("unknown_user", "bad_pass_12345")
        with patch("cc_web_auth.time.sleep") as sleep:
            self.auth.change_password(session["token"], "secure_password_123", "new_secure_password_456")
        sleep.assert_not_called()
        with patch("cc_web_auth.time.sleep"):
            self.assertEqual("testuser", self.auth.login("testuser", "new_secure_password_456")["username"])

    def test_session_password_failures_are_throttled_independently(self):
        self.auth.provision("testuser", "secure_password_123")
        session = self.auth.login("testuser", "secure_password_123")
        clock = [self.auth._now_utc()]
        with patch.object(WebAuth, "_now_utc", side_effect=lambda: clock[0]):
            for _ in range(5):
                with self.assertRaises(PermissionError):
                    self.auth.change_password(session["token"], "bad_pass_12345", "new_secure_password_456")
            limit = self.auth._load_data()["sessions"][hashlib.sha256(session["token"].encode()).hexdigest()]["password_limit"]
            self.assertEqual(5, len(limit["failures"]))
            with self.assertRaisesRegex(PermissionError, "频繁"):
                self.auth.change_password(session["token"], "secure_password_123", "new_secure_password_456")
            clock[0] += dt.timedelta(seconds=5)
            self.auth.change_password(session["token"], "secure_password_123", "new_secure_password_456")

    def test_login_pacing_does_not_block_existing_session(self):
        self.auth.provision("testuser", "secure_password_123")
        session = self.auth.login("testuser", "secure_password_123")
        for _ in range(5):
            with self.assertRaises(PermissionError):
                self.auth.login("unknown_user", "bad_pass_12345")
        entered = threading.Event()
        release = threading.Event()
        check = WebAuth._check_rate_limit

        def pause(auth, limit, now):
            if limit.get("next_attempt_at"):
                entered.set()
                if not release.wait(3):
                    raise AssertionError("login remained paused")
                limit.pop("next_attempt_at")
            return check(auth, limit, now)

        with patch.object(WebAuth, "_check_rate_limit", pause), ThreadPoolExecutor(max_workers=3) as pool:
            pending = pool.submit(self.auth.login, "unknown_user", "bad_pass_12345")
            try:
                self.assertTrue(entered.wait(2))
                with self.assertRaisesRegex(PermissionError, "频繁"):
                    self.auth.login("testuser", "secure_password_123")
                self.assertEqual("testuser", pool.submit(self.auth.authenticate, session["token"]).result(timeout=1)["username"])
                pool.submit(self.auth.logout, session["token"]).result(timeout=1)
                with self.assertRaises(PermissionError):
                    self.auth.authenticate(session["token"])
            finally:
                release.set()
            with self.assertRaises(PermissionError):
                pending.result(timeout=3)

    def test_login_rechecks_credentials_after_pacing(self):
        self.auth.provision("testuser", "secure_password_123")
        for _ in range(5):
            with self.assertRaises(PermissionError):
                self.auth.login("unknown_user", "bad_pass_12345")
        entered = threading.Event()
        release = threading.Event()
        check = WebAuth._check_rate_limit

        def pause(auth, limit, now):
            entered.set()
            if not release.wait(3):
                raise AssertionError("login remained paused")
            limit.pop("next_attempt_at", None)
            return check(auth, limit, now)

        with patch.object(WebAuth, "_check_rate_limit", pause), ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.auth.login, "testuser", "secure_password_123")
            try:
                self.assertTrue(entered.wait(2))
                self.auth.provision("testuser", "new_secure_password_456")
            finally:
                release.set()
            with self.assertRaises(PermissionError):
                pending.result(timeout=3)
        self.assertEqual("testuser", self.auth.login("testuser", "new_secure_password_456")["username"])

    def test_legacy_global_lock_is_discarded(self):
        self.auth.provision("testuser", "secure_password_123")
        data = self.auth._load_data()
        data["rate_limit"] = {"failures": [], "locked_until": "2999-01-01T00:00:00Z"}
        self.auth._save_data(data)
        self.assertEqual("testuser", self.auth.login("testuser", "secure_password_123")["username"])
        self.assertNotIn("locked_until", self.auth._load_data()["rate_limit"])

    def test_cross_account_rejection(self):
        self.auth.provision("admin", "secure_password_123")
        res = self.auth.login("admin", "secure_password_123")

        other_auth = WebAuth(self.test_dir, "other-acc-99")
        with self.assertRaises(PermissionError):
            other_auth.login("admin", "secure_password_123")
        with self.assertRaises(PermissionError):
            other_auth.authenticate(res["token"])
        with self.assertRaises(PermissionError):
            other_auth.provision("admin", "secure_password_123")

    def test_cross_instance_restart_consistency(self):
        self.auth.provision("admin", "secure_password_123")
        res = self.auth.login("admin", "secure_password_123")

        restarted = WebAuth(self.test_dir, self.account_id)
        session = restarted.authenticate(res["token"])
        self.assertEqual(session["username"], "admin")

    def test_logout(self):
        self.auth.provision("admin", "secure_password_123")
        res = self.auth.login("admin", "secure_password_123")
        self.auth.logout(res["token"])
        with self.assertRaises(PermissionError):
            self.auth.authenticate(res["token"])

    def test_session_expiration(self):
        self.auth.provision("admin", "secure_password_123")
        res = self.auth.login("admin", "secure_password_123")

        future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=13)
        with patch.object(WebAuth, "_now_utc", return_value=future):
            with self.assertRaises(PermissionError):
                self.auth.authenticate(res["token"])

    def test_change_password_revokes_all_sessions(self):
        self.auth.provision("admin", "secure_password_123")
        s1 = self.auth.login("admin", "secure_password_123")
        s2 = self.auth.login("admin", "secure_password_123")

        self.auth.change_password(s1["token"], "secure_password_123", "new_secure_password_456")
        with self.assertRaises(PermissionError):
            self.auth.authenticate(s1["token"])
        with self.assertRaises(PermissionError):
            self.auth.authenticate(s2["token"])

        # 新密码登录成功，旧密码失败
        self.auth.login("admin", "new_secure_password_456")
        with self.assertRaises(PermissionError):
            self.auth.login("admin", "secure_password_123")

    def test_corrupted_archive_rejected(self):
        self.auth.provision("admin", "secure_password_123")
        with open(self.auth.auth_path, "w", encoding="utf-8") as f:
            f.write("{invalid_json: true")
        with self.assertRaises(ValueError):
            self.auth.login("admin", "secure_password_123")

    def test_symlink_storage_rejected(self):
        self.auth.provision("admin", "secure_password_123")
        target_path = os.path.join(self.test_dir, "real-auth.json")
        os.rename(self.auth.auth_path, target_path)
        os.symlink(target_path, self.auth.auth_path)
        with self.assertRaises(PermissionError):
            self.auth.login("admin", "secure_password_123")

    def test_overly_permissive_file_rejected(self):
        if os.name == "nt":
            return
        self.auth.provision("admin", "secure_password_123")
        os.chmod(self.auth.auth_path, 0o666)
        with self.assertRaises(PermissionError):
            self.auth.login("admin", "secure_password_123")

    def test_session_cap_and_eviction(self):
        self.auth.provision("admin", "secure_password_123")
        tokens = []
        for _ in range(25):
            res = self.auth.login("admin", "secure_password_123")
            tokens.append(res["token"])

        data = self.auth._load_data(must_exist=True)
        self.assertIsNotNone(data)
        self.assertLessEqual(len(data["sessions"]), 20)
        # 最早登录的 session 应当已被逐出
        with self.assertRaises(PermissionError):
            self.auth.authenticate(tokens[0])
        # 最新登录的 session 仍然有效
        self.assertEqual(self.auth.authenticate(tokens[-1])["username"], "admin")

    def test_cli_main_password_stdin(self):
        argv = [
            "--home", self.test_dir,
            "--account-id", self.account_id,
            "--username", "cli_admin",
            "--password-stdin",
        ]
        with io.TextIOWrapper(io.BytesIO(), encoding="cp1252") as stdout, \
             io.TextIOWrapper(io.BytesIO(), encoding="cp1252") as stderr, \
             patch("sys.stdout", stdout), patch("sys.stderr", stderr), \
             patch("sys.stdin", io.StringIO("cli_password_123456\n")):
            code = main(argv)
        self.assertEqual(code, 0)
        auth = WebAuth(self.test_dir, self.account_id)
        res = auth.login("cli_admin", "cli_password_123456")
        self.assertEqual(res["username"], "cli_admin")


if __name__ == "__main__":
    unittest.main()
