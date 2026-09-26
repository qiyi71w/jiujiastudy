"""Web account contracts against isolated HTTPS Canvas and real password hashing."""
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import test_account_service as fixtures
from cc_account import AccountService, StateConflict
from cc_store import jload, jsave
from cc_web import create_app
from cc_web_auth import WebAuth


@unittest.skipUnless(shutil.which("openssl") and os.name == "posix", "requires OpenSSL and POSIX service fixtures")
class Portal(unittest.TestCase):
    setUpClass = classmethod(fixtures.SecureRefresh.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.SecureRefresh.tearDownClass.__func__)
    command = fixtures.SecureRefresh.command
    ai_endpoint = fixtures.SecureRefresh.ai_endpoint
    toggle_ai = fixtures.SecureRefresh.toggle_ai

    def setUp(self):
        fixtures.SecureRefresh.setUp(self)
        self.web_origin = "https://study.example"
        self.auth = WebAuth(self.home.archive, self.service.account_id)
        self.auth.provision("student", "synthetic-long-password")
        self.app = create_app(self.service, self.web_origin)
        self.client = self.app.test_client()
        self.csrf = None

    def login(self, client=None):
        response = (client or self.client).post("/api/login", base_url=self.web_origin,
            headers={"Origin": self.web_origin}, json={"username": "student", "password": "synthetic-long-password"})
        self.assertEqual(200, response.status_code)
        self.assertIn("Secure", response.headers["Set-Cookie"])
        self.assertIn("HttpOnly", response.headers["Set-Cookie"])
        return response.json["csrf"]

    def post(self, path, data, csrf=None):
        return self.client.post(path, base_url=self.web_origin,
            headers={"Origin": self.web_origin, "X-CSRF-Token": csrf or self.csrf or ""}, json=data)

    def setting(self, key, value):
        state = self.service.portal_state()
        return self.service.portal_action({"action": "setting", "id": key, "value": value, "version": state["settings_version"]})

    def test_auth_csrf_and_password_revocation(self):
        self.assertEqual(401, self.client.get("/api/state", base_url=self.web_origin).status_code)
        self.csrf = self.login()
        second = self.app.test_client()
        second_csrf = self.login(second)
        forbidden = self.client.post("/api/action", base_url=self.web_origin, json={"action": "service", "value": False})
        self.assertEqual(403, forbidden.status_code)
        self.assertEqual(403, self.post("/api/action", {"action": "service", "value": False}, "wrong").status_code)
        self.assertEqual(200, self.post("/api/password", {"current_password": "synthetic-long-password", "new_password": "replacement-long-password"}).status_code)
        self.assertEqual(401, second.get("/api/state", base_url=self.web_origin).status_code)
        self.assertEqual([], self.server.requests())

    def test_foreign_account_session_and_identifiers_rejected(self):
        self.csrf = self.login()
        with tempfile.TemporaryDirectory() as other:
            cfg = jload(os.path.join(self.home.archive, "config.json"))
            cfg["service"]["account_id"] = "another-account-id"
            jsave(os.path.join(other, "config.json"), cfg)
            peer = AccountService(other, self.secrets_path)
            WebAuth(other, peer.account_id).provision("student", "another-long-password")
            app = create_app(peer, self.web_origin)
            client = app.test_client()
            cookie = self.client.get_cookie("__Host-coach_session", domain="study.example")
            client.set_cookie("__Host-coach_session", cookie.value, domain="study.example")
            self.assertEqual(401, client.get("/api/state", base_url=self.web_origin).status_code)
        rejected = self.post("/api/action", {"action": "setting", "id": "ai_enabled", "value": True, "account_id": "other"})
        self.assertEqual(400, rejected.status_code)
        self.assertFalse(self.service.portal_state()["settings"]["ai_enabled"])

    def test_shared_settings_completion_and_stale_writes(self):
        self.command("refresh")
        state = self.service.portal_state()
        self.setting("telegram_notifications", False)
        with self.assertRaises(StateConflict):
            self.service.portal_action({"action": "setting", "id": "ai_enabled", "value": True, "version": state["settings_version"]})
        self.assertIn("Telegram 自动通知：关", self.service.ai_settings(1234)["text"])
        task = next(t for t in state["tasks"] if t["sub_state"] not in ("submitted", "excused"))
        action = {"action": "task", "id": task["id"], "value": "complete", "version": task["version"]}
        self.service.portal_action(action)
        with self.assertRaises(StateConflict):
            self.service.portal_action(action)
        restarted = AccountService(self.home.archive, self.secrets_path)
        result = next(t for t in restarted.portal_state()["tasks"] if t["id"] == task["id"])
        self.assertTrue(result["completed"])
        self.assertTrue(result["stopped"])
        self.assertEqual(task["sub_state"], result["sub_state"])
        self.assertNotIn(task["name"], self.command("report")["text"])

    def test_canvas_read_and_local_read_filter_model_input(self):
        received = self.ai_endpoint(limit=3)
        self.setting("ai_enabled", True)
        self.setting("ai_announcements", True)
        self.command("refresh")
        anns = self.service.portal_state()["announcements"]
        self.assertTrue(anns)
        first = anns[0]
        self.service.portal_action({"action": "announcement", "id": first["id"], "value": True, "version": first["version"]})
        # A new Canvas read state is a server fact, not a browser decision.
        saved = self.service._saved()
        remaining = [a for a in saved["snapshot"]["announcements"] if a["id"] != first["id"]]
        for ann in remaining:
            ann["canvas_read_state"] = "read"
        jsave(self.service.report_path, saved)
        from cc_account import _CollectionContext
        _, selected = self.service._ai_input(_CollectionContext(self.home.archive, quiet=True), saved, saved["snapshot"])
        self.assertEqual([], selected)
        # Reading the website and locally marking an announcement never writes Canvas.
        self.csrf = self.login()
        self.server.requests(clear=True)
        self.assertEqual(200, self.client.get("/api/state", base_url=self.web_origin).status_code)
        self.assertEqual([], self.server.requests())
        self.assertEqual([], received)

    def test_daily_report_forms_without_telegram_and_keeps_restart_state(self):
        self.setting("telegram_notifications", False)
        with patch("cc_schedule.secrets.randbelow", return_value=0):
            self.service.execute(1234, "schedule", ["22:45", "UTC"], "telegram", "schedule")
        self.assertIsNone(self.service.scheduled_tick())
        history = self.service.portal_state()["history"]
        self.assertEqual(1, len(history))
        self.assertIn("ACCT1101", history[0]["text"])
        calls = len(self.server.requests())
        self.setting("telegram_notifications", True)
        restarted = AccountService(self.home.archive, self.secrets_path)
        self.assertIsNone(restarted.scheduled_tick())
        self.assertEqual(calls, len(self.server.requests()))
        self.assertEqual(history, restarted.portal_state()["history"])

    def test_website_only_account_generates_authorized_ai_without_delivery(self):
        received = self.ai_endpoint(limit=3)
        cfg = jload(os.path.join(self.home.archive, "config.json"))
        cfg["service"].pop("telegram_user_id")
        jsave(os.path.join(self.home.archive, "config.json"), cfg)
        secret = jload(self.secrets_path)
        secret.pop("telegram_bot_token")
        jsave(self.secrets_path, secret)
        self.service = AccountService(self.home.archive, self.secrets_path)
        self.setting("ai_enabled", True)
        self.setting("ai_summary", True)
        state = self.service.portal_state()
        with patch("cc_schedule.secrets.randbelow", return_value=0):
            self.service.portal_action({"action": "schedule", "params": ["22:45", "UTC"], "version": state["settings_version"]})
        self.assertIsNone(self.service.scheduled_tick())
        self.assertEqual(1, len(received))
        self.assertTrue(self.service.portal_state()["history"][0]["ai_text"])
        self.assertFalse(self.service.portal_state()["telegram_available"])
        with self.assertRaises(ValueError):
            self.setting("telegram_notifications", True)

    def test_schedule_rejects_stale_settings_across_channels(self):
        state = self.service.portal_state()
        self.service.execute(1234, "schedule", ["20:00", "UTC"], "telegram", "setting")
        with self.assertRaises(StateConflict):
            self.service.portal_action({"action": "schedule", "params": ["21:00", "UTC"], "version": state["settings_version"]})
        self.assertEqual("20:00", self.service.portal_state()["settings"]["daily_time"])

    def test_read_mark_preserves_saved_analysis_and_filters_next_request(self):
        received = self.ai_endpoint(limit=3)
        self.setting("ai_enabled", True)
        self.setting("ai_announcements", True)
        self.command("ai")
        state = self.service.portal_state()
        analysis = state["analysis"]
        self.assertIsNotNone(analysis)
        ann = state["announcements"][0]
        self.service.portal_action({"action": "announcement", "id": ann["id"], "value": True, "version": ann["version"]})
        restarted = AccountService(self.home.archive, self.secrets_path)
        updated = restarted.portal_state()["analysis"]
        self.assertEqual(analysis["announcements"][0]["generated_at"], updated["announcements"][0]["generated_at"])
        self.assertTrue(next(a for a in updated["announcements"] if a["id"] == ann["id"])["effective_read"])
        self.assertFalse(next(a for a in updated["announcements"] if a["id"] == ann["id"])["analysis_stale"])
        self.assertEqual(analysis["announcements"][0]["id"], updated["announcements"][0]["id"])
        self.command("ai", restarted)
        prompt = received[-1][2]["messages"][1]["content"]
        self.assertNotIn("- ID: " + ann["id"] + "\n", prompt)

    def test_quota_period_uses_frozen_zone_across_dst_and_account_day(self):
        from cc_time import pin_now
        saved = self.service._saved()
        saved["ai_quota_zone"] = "America/New_York"
        saved["ai_usage"] = {"2026-03-08": 2}
        jsave(self.service.report_path, saved)
        cfg_path = os.path.join(self.home.archive, "config.json")
        cfg = jload(cfg_path)
        cfg["user_tz"] = "Asia/Tokyo"
        jsave(cfg_path, cfg)
        pin_now("2026-03-08T06:30:00Z")
        before = self.service.portal_state()["quota"]
        self.assertEqual(("day", "America/New_York", 2),
                         (before["period"], before["timezone"], before["used"]))
        self.assertEqual("2026-03-08T00:00:00-05:00", before["period_start"])
        self.assertEqual("2026-03-09T00:00:00-04:00", before["reset_at"])
        pin_now("2026-03-08T07:30:00Z")
        self.assertEqual(before, self.service.portal_state()["quota"])
        pin_now("2026-03-09T03:59:00Z")
        self.assertEqual(2, self.service.portal_state()["quota"]["used"])
        pin_now("2026-03-09T04:01:00Z")
        after = self.service.portal_state()["quota"]
        self.assertEqual(0, after["used"])
        self.assertEqual(before["limit"], after["limit"])
        self.assertEqual(before["reset_at"], after["period_start"])
        self.assertEqual("2026-03-10T00:00:00-04:00", after["reset_at"])

    def test_ai_time_context_uses_user_zone_with_dst(self):
        received = self.ai_endpoint(limit=1)
        self.setting("ai_enabled", True)
        cfg = jload(os.path.join(self.home.archive, "config.json"))
        cfg["user_tz"] = "America/New_York"
        jsave(os.path.join(self.home.archive, "config.json"), cfg)
        tasks = self.sc.course[1101]["assignments"]
        tasks[0]["due_at"] = "2026-03-25T01:30:00Z"
        tasks[1]["due_at"] = "2026-01-25T01:30:00Z"
        self.command("ai")
        prompt = received[0][2]["messages"][1]["content"]
        self.assertIn("2026-03-24T21:30:00-04:00", prompt)
        self.assertIn("2026-01-24T20:30:00-05:00", prompt)
        self.assertIn("as_of=2026-03-24T19:00:00-04:00", prompt)
        self.assertEqual("America/New_York", self.service.portal_state()["analysis"]["timezone"])

    def test_ai_progress_reports_real_stages_and_is_session_bound(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        entered, release = threading.Event(), threading.Event()
        def hold():
            entered.set()
            release.wait(5)
        self.ai_endpoint(handler_hook=hold, usage={"completion_tokens": 120})
        self.setting("ai_enabled", True)
        self.app = create_app(self.service, self.web_origin)
        self.client = self.app.test_client()
        self.csrf = self.login()
        peer = self.app.test_client()
        self.login(peer)
        request_id = "isolated-progress-request"
        path = "/api/ai-progress/" + request_id
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.post, "/api/action", {"action": "ai", "request_id": request_id})
            try:
                self.assertTrue(entered.wait(5))
                polling = self.app.test_client()
                cookie = self.client.get_cookie("__Host-coach_session", domain="study.example")
                polling.set_cookie("__Host-coach_session", cookie.value, domain="study.example")
                current = polling.get(path, base_url=self.web_origin).json
                self.assertEqual("requesting", current["status"])
                self.assertEqual("test-model", current["model"])
                self.assertIsNone(current["tokens_per_second"])
                self.assertEqual(404, peer.get(path, base_url=self.web_origin).status_code)
                self.assertEqual(401, self.app.test_client().get(path, base_url=self.web_origin).status_code)
            finally:
                release.set()
            response = future.result(5)
        self.assertEqual(200, response.status_code)
        self.assertEqual("done", response.json["progress"]["status"])
        self.assertEqual(120, response.json["progress"]["output_tokens"])
        self.assertGreater(response.json["progress"]["tokens_per_second"], 0)
        self.assertEqual("request_average", response.json["progress"]["rate_kind"])

    def test_stream_preview_precedes_commit_and_disconnect_does_not_repeat_request(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        entered, release = threading.Event(), threading.Event()
        def hold_first(index):
            if index == 0:
                entered.set()
                release.wait(10)
        received = self.ai_endpoint(stream=True, chunk_hook=hold_first,
                                    answer="先看最近的作业，再核对公告。", usage={"completion_tokens": 80})
        self.setting("ai_enabled", True)
        self.app = create_app(self.service, self.web_origin)
        self.client = self.app.test_client()
        self.csrf = self.login()
        peer = self.app.test_client()
        self.login(peer)
        request_id = "stream-preview-before-commit"
        path = "/api/ai-progress/" + request_id
        headers = {"Accept": "text/event-stream"}
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.post, "/api/action", {"action": "ai", "request_id": request_id})
            connection = None
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(404, peer.get(path, base_url=self.web_origin, headers=headers).status_code)
                self.assertEqual(401, self.app.test_client().get(path, base_url=self.web_origin, headers=headers).status_code)
                preview_client = self.app.test_client()
                cookie = self.client.get_cookie("__Host-coach_session", domain="study.example")
                preview_client.set_cookie("__Host-coach_session", cookie.value, domain="study.example")
                connection = preview_client.get(path, base_url=self.web_origin, headers=headers, buffered=False)
                self.assertEqual("text/event-stream", connection.mimetype)
                self.assertEqual("no", connection.headers["X-Accel-Buffering"])
                for packet in connection.response:
                    event = json.loads(packet.decode().split("data: ", 1)[1])
                    if event.get("preview"):
                        break
                self.assertIn("先看最近的作业", event["preview"])
                self.assertFalse(future.done())
                self.assertIsNone(self.service.portal_state()["analysis"])
                self.assertEqual(409, preview_client.get(path, base_url=self.web_origin, headers=headers).status_code)
            finally:
                if connection:
                    connection.close()
                release.set()
            result = future.result(5)
        self.assertEqual("done", result.json["progress"]["status"])
        self.assertEqual("先看最近的作业，再核对公告。", result.json["state"]["analysis"]["summary"])
        self.assertEqual(1, len(received))
        self.assertTrue(received[0][2]["stream"])
        self.assertEqual(1, result.json["state"]["quota"]["used"])

    def test_interrupted_model_stream_discards_draft_without_retry(self):
        received = self.ai_endpoint(stream=True, chunk_hook=lambda index: False)
        self.setting("ai_enabled", True)
        self.app = create_app(self.service, self.web_origin)
        self.client = self.app.test_client()
        self.csrf = self.login()
        request_id = "interrupted-stream-request"
        response = self.post("/api/action", {"action": "ai", "request_id": request_id})
        self.assertEqual(200, response.status_code)
        self.assertEqual("failed", response.json["progress"]["status"])
        self.assertEqual("", response.json["progress"]["preview"])
        self.assertIsNone(response.json["state"]["analysis"])
        self.assertEqual([], response.json["state"]["weekly_plan"]["announcement_actions"])
        self.assertEqual(1, len(received))
        self.assertEqual(1, response.json["state"]["quota"]["used"])

    def test_complete_stream_with_invalid_actions_never_commits(self):
        received = self.ai_endpoint(stream=True, action_factory=lambda aid, body: [{
            "title": "Unverified task", "first_step": "Do it", "due_at": None,
            "uncertainty": "Unknown date", "evidence": "Invented text absent from this announcement"}])
        self.setting("ai_enabled", True)
        self.setting("ai_announcements", True)
        self.app = create_app(self.service, self.web_origin)
        self.client = self.app.test_client()
        self.csrf = self.login()
        response = self.post("/api/action", {"action": "ai", "request_id": "invalid-actions-stream"})
        self.assertEqual("failed", response.json["progress"]["status"])
        self.assertEqual("", response.json["progress"]["preview"])
        self.assertIsNone(response.json["state"]["analysis"])
        self.assertEqual([], response.json["state"]["weekly_plan"]["announcement_actions"])
        self.assertEqual(1, len(received))

    def test_open_preview_closes_when_session_is_revoked(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        entered, release = threading.Event(), threading.Event()
        def hold_first(index):
            if index == 0:
                entered.set()
                release.wait(10)
        self.ai_endpoint(stream=True, chunk_hook=hold_first)
        self.setting("ai_enabled", True)
        self.app = create_app(self.service, self.web_origin)
        self.client = self.app.test_client()
        self.csrf = self.login()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.post, "/api/action", {"action": "ai", "request_id": "revoked-open-stream"})
            connection = None
            try:
                self.assertTrue(entered.wait(5))
                preview_client = self.app.test_client()
                cookie = self.client.get_cookie("__Host-coach_session", domain="study.example")
                preview_client.set_cookie("__Host-coach_session", cookie.value, domain="study.example")
                connection = preview_client.get("/api/ai-progress/revoked-open-stream", base_url=self.web_origin,
                    headers={"Accept": "text/event-stream"}, buffered=False)
                packets = iter(connection.response)
                next(packets)
                self.auth.logout(cookie.value)
                self.assertEqual([], list(packets))
            finally:
                if connection:
                    connection.close()
                release.set()
            self.assertEqual(401, future.result(5).status_code)

    def test_legacy_identity_migration_preserves_account_state(self):
        self.command("refresh")
        saved = self.service._saved()
        expected_snapshot = saved["snapshot"]
        saved["binding"] = {"origin": self.origin, "telegram_user_id": 1234, "bot_id": "1000"}
        saved["ai_usage"] = {"2026-03-24": 2}
        jsave(self.service.report_path, saved)
        restarted = AccountService(self.home.archive, self.secrets_path)
        self.assertEqual(expected_snapshot, restarted._saved()["snapshot"])
        self.assertEqual({"2026-03-24": 2}, restarted._saved()["ai_usage"])
        self.assertEqual(self.service.account_id, restarted.account_id)


if __name__ == "__main__":
    unittest.main()
