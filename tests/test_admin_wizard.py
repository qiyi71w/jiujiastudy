"""Host account administration boundaries; no live Canvas or paid model calls."""
import os
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cc_admin as admin


@unittest.skipUnless(os.name == "posix", "requires POSIX ownership and permissions")
class AccountAdministration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.home = root / "home"
        self.home.mkdir()
        self.secret = root / "service.json"
        self.config = {"canvas_host": "https://canvas.example.edu", "service": {
            "account_id": "isolated-account", "web_origin": "https://new.example.edu", "ai_daily_limit": 20}}
        self.record = {"canvas_origin": "https://canvas.example.edu", "canvas_token": "old-token"}
        admin.atomic(self.secret, (json.dumps(self.record) + "\n").encode(), os.getuid(), os.getgid())
        admin.atomic(self.home / "service-report.json", json.dumps({"canvas_user_id": 501,
            "ai_usage": {"2026-09-24": 7}}).encode(), os.getuid(), os.getgid())
        self.spec = {"State": {"Running": True}, "HostConfig": {"PortBindings": {
            "8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18888"}]}}}
        self.row = ("jiujiastudy-account-new", self.spec, self.home, self.secret, self.config)

    def test_different_canvas_identity_cannot_replace_token_or_stop_service(self):
        operations = []
        with patch.object(admin, "selected", return_value=self.row), \
             patch.object(admin, "identity", return_value="502"), \
             patch.object(admin, "run", side_effect=lambda *a, **kw: operations.append(a) or ""):
            with self.assertRaisesRegex(admin.AdminError, "另一个 Canvas 身份"):
                admin.rotate(self.row[0], "other-token")
        self.assertEqual([], operations)
        self.assertEqual(self.record, admin.private_json(self.secret))

    def test_rotation_preserves_state_and_rolls_back_failed_health_check(self):
        operations = []
        with patch.object(admin, "selected", return_value=self.row), \
             patch.object(admin, "identity", return_value="501"), \
             patch.object(admin, "run", side_effect=lambda *a, **kw: operations.append(a) or ""), \
             patch.object(admin, "ready") as ready:
            admin.rotate(self.row[0], "new-token")
        self.assertEqual("new-token", admin.private_json(self.secret)["canvas_token"])
        self.assertEqual(7, admin.private_json(self.home / "service-report.json")["ai_usage"]["2026-09-24"])
        self.assertEqual(["stop", "start"], [a[1] for a in operations])
        operations.clear()
        ready.reset_mock()
        ready.side_effect = [admin.AdminError("健康检查失败"), None]
        with patch.object(admin, "selected", return_value=self.row), \
             patch.object(admin, "identity", return_value="501"), \
             patch.object(admin, "run", side_effect=lambda *a, **kw: operations.append(a) or ""), \
             patch.object(admin, "ready", ready):
            with self.assertRaises(admin.AdminError):
                admin.rotate(self.row[0], "another-token")
        self.assertEqual("new-token", admin.private_json(self.secret)["canvas_token"])
        self.assertEqual(["stop", "start", "stop", "start"], [a[1] for a in operations])

    def test_registry_rejects_duplicate_identity_and_port(self):
        registry_file = self.home / "registry.json"
        entry = {"username": "learner", "account_id": "stable-id", "port": 18081}
        original_stat = Path.stat
        with patch.object(admin, "REGISTRY", registry_file), patch.object(admin.Path, "stat", autospec=True) as stat:
            stat.side_effect = lambda path, *a, **kw: (type("Owner", (), {"st_uid": 0, "st_mode": 0o100644})()
                if path == registry_file else original_stat(path, *a, **kw))
            for duplicate in ({"username": "learner", "account_id": "other", "port": 18082},
                              {"username": "other", "account_id": "stable-id", "port": 18082},
                              {"username": "other", "account_id": "other", "port": 18081}):
                registry_file.write_text(json.dumps({"origin": "https://study.qiyi71w.com",
                    "accounts": [entry, duplicate]}))
                with self.assertRaises(admin.AdminError):
                    admin.registry()

    def test_unregistered_or_mismatched_container_is_not_selectable(self):
        registered = {"origin": "https://study.qiyi71w.com", "accounts": [
            {"username": "learner", "account_id": "isolated-account", "port": 18888}]}
        self.config["service"]["web_origin"] = registered["origin"]
        admin.atomic(self.home / "config.json", json.dumps(self.config).encode(), os.getuid(), os.getgid())
        admin.atomic(self.home / "web-auth.json", json.dumps({"username": "learner"}).encode(), os.getuid(), os.getgid())
        self.spec.update(Config={"User": "10001:10001"}, Mounts=[
            {"Destination": "/data", "Type": "bind", "Source": str(self.home)},
            {"Destination": "/run/secrets/service.json", "Type": "bind", "Source": str(self.secret)}])
        with patch.object(admin, "registry", return_value=registered), \
             patch.object(admin, "run", return_value=self.row[0] + "\n"), \
             patch.object(admin, "inspect", return_value=self.spec):
            self.assertEqual(1, len(admin.accounts()))
            self.spec["HostConfig"]["PortBindings"]["8080/tcp"][0]["HostPort"] = "18887"
            with self.assertRaises(admin.AdminError):
                admin.selected(self.row[0])
            self.spec["HostConfig"]["PortBindings"]["8080/tcp"][0]["HostPort"] = "18888"
            registered["accounts"].clear()
            with self.assertRaises(admin.AdminError):
                admin.selected(self.row[0])

    def test_duplicate_username_rejected_before_provisioning(self):
        registered = {"origin": "https://study.qiyi71w.com", "accounts": [
            {"username": "learner", "account_id": "stable-id", "port": 18081}]}
        with patch.object(admin, "registry", return_value=registered), \
             patch.object(admin, "run") as run:
            with self.assertRaisesRegex(admin.AdminError, "用户名已经注册"):
                admin.create(["second", "https://canvas.example.edu", "token", "learner",
                    "long-enough-password", "UTC", ""])
        run.assert_not_called()

    def test_failed_public_login_removes_only_new_account(self):
        existing = {"username": "existing", "account_id": "original", "port": 18081}
        state = {"origin": "https://study.qiyi71w.com", "accounts": [existing.copy()]}
        root = Path(self.temp.name)
        public_seen = []
        commands = []

        def own_save(path, data):
            if path.name == "config.json":
                self.assertEqual(state["origin"], data["service"]["web_origin"])
                self.assertEqual(20, data["service"]["ai_daily_limit"])
            if path.name == "service-report.json":
                self.assertFalse(data["service_enabled"])
            admin.atomic(path, (json.dumps(data) + "\n").encode(), os.getuid(), os.getgid())

        def registry_snapshot():
            return json.loads(json.dumps(state))

        def persist(data):
            state.clear()
            state.update(json.loads(json.dumps(data)))
            public_seen.append(registry_snapshot())

        def docker(*args, **kwargs):
            commands.append(args)
            return ""

        class FailedLogin:
            def open(self, request, timeout):
                self.request = request
                raise admin.AdminError("公网登录检查失败。")

        with patch.object(admin, "HOME_ROOT", root), patch.object(admin, "PRIVATE_ROOT", root / "private"), \
             patch.object(admin, "registry", side_effect=registry_snapshot), \
             patch.object(admin, "save_registry", side_effect=persist), \
             patch.object(admin, "save", side_effect=own_save), \
             patch.object(admin.os, "chown"), patch.object(admin.shutil, "which", return_value="docker"), \
             patch.object(admin, "accounts", return_value=[]), \
             patch.object(admin, "identity", return_value="501"), \
             patch.object(admin, "run", side_effect=docker), \
             patch.object(admin, "ready"), \
             patch.object(admin.urllib.request, "build_opener", return_value=FailedLogin()):
            (root / "private").mkdir()
            with self.assertRaisesRegex(admin.AdminError, "公网登录检查失败"):
                admin.create(["new", "https://canvas.example.edu", "token", "newlearner",
                    "long-enough-password", "UTC", ""], "app:image")
        self.assertEqual([existing], state["accounts"])
        self.assertEqual(2, len(public_seen))
        self.assertEqual("newlearner", public_seen[0]["accounts"][-1]["username"])
        self.assertFalse((root / "new").exists())
        self.assertFalse((root / "private" / "new").exists())
        self.assertIn(("docker", "rm", "-f", "jiujiastudy-account-new"), commands)
        self.assertNotIn(("docker", "rm", "-f", "jiujiastudy-account-existing"), commands)
        self.assertEqual("https://study.qiyi71w.com", public_seen[0]["origin"])


if __name__ == "__main__":
    unittest.main()
