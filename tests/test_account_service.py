"""HTTPS boundary regressions for the Telegram account service (synthetic data only)."""
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import harness
import mockcanvas

sys.path.insert(0, os.path.join(harness.code_dir(), "tools"))
from cc_account import AccountService
from cc_service_security import SecureCanvas, ServiceSecrets
from cc_store import jload, jsave
from cc_time import pin_now
class TLSMockCanvas(mockcanvas.MockCanvas):
    @property
    def base_url(self):
        return f"https://{self.host}:{self.port}"




@unittest.skipUnless(shutil.which("openssl") and os.name == "posix", "requires openssl and POSIX secret permissions")
class SecureRefresh(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tls = tempfile.TemporaryDirectory(prefix="coach-tls-")
        cls.cert = os.path.join(cls.tls.name, "cert.pem")
        cls.key = os.path.join(cls.tls.name, "key.pem")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-keyout", cls.key, "-out", cls.cert, "-subj", "/CN=127.0.0.1",
                        "-addext", "subjectAltName=IP:127.0.0.1"], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.tls.cleanup()

    def setUp(self):
        self.home = harness.FakeHome("secure-refresh")
        self.addCleanup(self.home.cleanup)
        self.sc = mockcanvas.Scenario("au_semester")
        self.server = TLSMockCanvas(self.sc).start()
        self.addCleanup(self.server.stop)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.cert, self.key)
        self.server.httpd.socket = context.wrap_socket(self.server.httpd.socket, server_side=True)
        self.origin = f"https://127.0.0.1:{self.server.port}"
        self.home.seed(self.sc, self.origin)
        cfg = jload(os.path.join(self.home.archive, "config.json"))
        cfg["service"] = {"telegram_user_id": 1234}
        jsave(os.path.join(self.home.archive, "config.json"), cfg)
        self.secrets_path = os.path.join(self.home.tmp, "service.json")
        jsave(self.secrets_path, {"canvas_origin": self.origin, "canvas_token": self.sc.token,
                                 "telegram_bot_token": "1000:" + "x" * 35})
        os.chmod(self.secrets_path, 0o600)
        self.env = patch.dict(os.environ, {"SSL_CERT_FILE": self.cert, "NO_PROXY": "*", "no_proxy": "*"})
        self.env.start()
        self.addCleanup(self.env.stop)
        connect = socket.socket.connect
        def local_only(sock, address):
            if address[0] not in ("127.0.0.1", "::1"):
                raise AssertionError("test attempted external connection")
            return connect(sock, address)
        guard = patch.object(socket.socket, "connect", local_only)
        guard.start()
        self.addCleanup(guard.stop)
        pin_now("2026-03-24T23:00:00Z")
        self.addCleanup(pin_now, None)
        self.service = AccountService(self.home.archive, self.secrets_path)

    def command(self, command, service=None):
        return (service or self.service).execute(1234, command, [], "telegram", "test-update")

    def test_reads_do_not_collect_and_failed_refresh_preserves_restart_report(self):
        for command in ("help", "status", "report"):
            self.assertFalse(self.command(command)["complete"])
        self.assertEqual([], self.server.requests())
        first = self.command("refresh")
        self.assertTrue(first["complete"], first)
        self.assertIn("ACCT1101", first["text"])
        self.assertIn("2026-03-24", first["collected_at"])
        self.assertFalse(any(r["path"].endswith(("conversations", "modules", "files", "announcements"))
                             for r in self.server.requests()))
        self.server.requests(clear=True)
        restarted = AccountService(self.home.archive, self.secrets_path)
        self.assertEqual(first, self.command("report", restarted))
        self.assertEqual([], self.server.requests())
        self.server.force_status(r"/courses/1101/assignments", 403)
        failed = self.command("refresh")
        self.assertIn("失败", failed["text"])
        self.assertEqual(first, self.command("report", restarted))

    def test_binding_changes_and_unauthorized_identity_cannot_read(self):
        with self.assertRaises(ValueError):
            self.service.execute(4321, "refresh", [], "telegram", "foreign")
        cfg = jload(os.path.join(self.home.archive, "config.json"))
        cfg["canvas_host"] = "https://other.invalid"
        jsave(os.path.join(self.home.archive, "config.json"), cfg)
        with self.assertRaises(ValueError):
            self.command("report")
        self.assertEqual([], self.server.requests())

    def test_secret_permissions_and_origin_rejected_before_requests(self):
        cfg = jload(os.path.join(self.home.archive, "config.json"))
        os.chmod(self.secrets_path, 0o644)
        with self.assertRaises(ValueError):
            ServiceSecrets(cfg, self.secrets_path)
        os.chmod(self.secrets_path, 0o600)
        for host in ("", "http://127.0.0.1", "https://user:secret@example.com", "https://example.com/path"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                ServiceSecrets(dict(cfg, canvas_host=host), self.secrets_path)
        self.assertEqual([], self.server.requests())

    def test_fetch_refuses_cross_origin_downgrade_and_non_api(self):
        api = SecureCanvas(self.origin, self.sc.token)
        for url in ("https://other.invalid/api/v1/users/self", self.origin.replace("https:", "http:") + "/api/v1/users/self",
                    self.origin + "/not-api"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                api.fetch(url)
        self.assertEqual([], self.server.requests())

    def test_new_course_is_collected_and_initial_snapshot_is_baseline(self):
        self.sc.courses.append({"id": 1104, "name": "STAT2011 Probability", "course_code": "STAT2011", "_active": True})
        self.sc.course[1104] = {k: [] for k in ("assignments", "assignment_groups", "modules", "files", "discussion_topics")}
        self.sc.course[1104]["assignments"] = [{
            "id": 991, "name": "Probability homework", "due_at": "2026-03-27T10:00:00Z",
            "points_possible": 10, "submission_types": ["online_upload"],
            "html_url": "{{BASE}}/courses/1104/assignments/991", "submission": {"workflow_state": "unsubmitted"}}]
        first = self.command("refresh")
        self.assertIn("Probability homework", first["text"])
        self.assertIn("新课程：STAT2011", first["text"])
        self.assertNotIn("新增：", first["text"])
        self.sc.course[1104]["assignments"][0]["due_at"] = "2026-03-28T10:00:00Z"
        second = self.command("refresh")
        self.assertIn("改期：STAT2011", second["text"])
        self.assertNotIn("新课程：STAT2011", second["text"])
        self.assertEqual(2, sum(r["path"] == "/api/v1/courses/1104/assignments" for r in self.server.requests()))

    def test_redirect_and_pagination_never_contact_target(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        received = []
        mode = ["redirect"]
        target = "http://127.0.0.1:1/api/v1/stolen"
        class Endpoint(BaseHTTPRequestHandler):
            def do_GET(handler):
                received.append((handler.path, handler.headers.get("Authorization")))
                handler.send_response(302 if mode[0] == "redirect" else 200)
                handler.send_header("Location" if mode[0] == "redirect" else "Link",
                                    target if mode[0] == "redirect" else f'<{target}>; rel="next"')
                handler.end_headers()
                handler.wfile.write(b"[]")
            def log_message(handler, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(self.cert, self.key)
        server.socket = tls.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connected = []
        connect = socket.socket.connect
        def observe_connect(sock, address):
            connected.append(address)
            return connect(sock, address)
        observer = patch.object(socket.socket, "connect", observe_connect)
        observer.start()
        try:
            api = SecureCanvas(f"https://127.0.0.1:{server.server_port}", "private-bearer", retries=0)
            for method in ("redirect", "pagination"):
                mode[0] = method
                with self.assertRaises(ValueError) as caught:
                    api.get("/api/v1/courses")
                self.assertNotIn("private-bearer", str(caught.exception))
                self.assertNotIn(target, str(caught.exception))
            self.assertEqual([("/api/v1/courses", "Bearer private-bearer")] * 2, received)
            self.assertEqual([("127.0.0.1", server.server_port)] * 2, connected)
        finally:
            observer.stop()
            server.shutdown()
            server.server_close()
            thread.join()

    def test_telegram_polling_auth_ack_and_safe_segmented_output(self):
        import html
        import io
        import urllib.request
        from cc_telegram import TelegramBot
        self.sc.course[1101]["assignments"][0].update(
            name="<button>＠fake @everyone</button>" + "\U0001f600" * 4000,
            due_at="2026-03-27T10:00:00Z", submission={"workflow_state": "unsubmitted"})
        bot = TelegramBot(self.service, self.service.secrets.telegram_bot_token)
        sent, polls = [], []
        def message(identity, kind, text, update_id):
            return {"update_id": update_id, "message": {"from": {"id": identity, "is_bot": False},
                    "chat": {"id": identity, "type": kind}, "text": text}}
        updates = [message(4321, "private", "/report", 1), message(1234, "group", "/refresh", 2),
                   message(1234, "private", "/canvas refresh", 3), message(1234, "private", "/status", 4)]
        original = urllib.request.OpenerDirector.open
        def boundary(opener, request, *args, **kwargs):
            if not request.full_url.startswith("https://api.telegram.org/"):
                return original(opener, request, *args, **kwargs)
            method = request.full_url.rsplit("/", 1)[1]
            payload = json.loads(request.data)
            if method == "getMe":
                result = {"is_bot": True, "id": 1000, "username": "test_bot"}
            elif method == "getWebhookInfo":
                result = {"url": ""}
            elif method == "getUpdates":
                polls.append(payload)
                if len(polls) > 1:
                    raise KeyboardInterrupt
                result = updates
            elif method == "sendMessage":
                if not sent:
                    self.assertEqual([], self.server.requests(), "acknowledge before Canvas work")
                sent.append(payload)
                result = {"message_id": len(sent)}
            else:
                raise AssertionError(method)
            return io.BytesIO(json.dumps({"ok": True, "result": result}).encode())
        with patch.object(urllib.request.OpenerDirector, "open", boundary):
            bot.run()
        self.assertEqual(5, polls[1]["offset"])
        self.assertEqual(30, polls[0]["timeout"])
        self.assertTrue(all(item["chat_id"] == 1234 for item in sent))
        reconstructed = "".join(html.unescape(item["text"][5:-6]) for item in sent[1:])
        self.assertIn("\U0001f600" * 4000, reconstructed)
        self.assertIn("＠everyone", reconstructed)
        for item in sent:
            self.assertNotIn("reply_markup", item)
            self.assertNotIn("@", item["text"])
            self.assertNotIn("<button>", item["text"])
            self.assertLessEqual(len(html.unescape(item["text"][5:-6]).encode("utf-16-le")) // 2, 3000)

    def test_telegram_errors_do_not_expose_token_urls(self):
        import io
        import urllib.error
        import urllib.request
        from contextlib import redirect_stderr
        from cc_telegram import TelegramBot
        bot = TelegramBot(self.service, self.service.secrets.telegram_bot_token)
        secret_url = "https://api.telegram.org/bot" + self.service.secrets.telegram_bot_token + "/getMe"
        diagnostics = io.StringIO()
        with patch.object(urllib.request.OpenerDirector, "open", side_effect=urllib.error.URLError(secret_url)):
            with redirect_stderr(diagnostics), self.assertRaises(ValueError) as caught:
                bot.request("getMe")
        self.assertNotIn(self.service.secrets.telegram_bot_token, str(caught.exception) + diagnostics.getvalue())

    def test_fresh_config_initializes_without_collecting_and_legacy_is_rejected(self):
        state_path = Path(self.home.archive, "state.json")
        state_path.unlink()
        fresh = AccountService(self.home.archive, self.secrets_path)
        self.assertFalse(self.command("status", fresh)["complete"])
        self.assertEqual([], self.server.requests())
        self.assertTrue(self.command("refresh", fresh)["complete"])
        jsave(str(state_path), {"schema_version": 1})
        with self.assertRaises(ValueError):
            AccountService(self.home.archive, self.secrets_path)

    def test_invalid_course_list_does_not_replace_success(self):
        first = self.command("refresh")
        self.assertTrue(first["complete"])
        config_before = Path(self.home.archive, "config.json").read_bytes()
        pin_now("2026-03-25T01:00:00Z")
        for payload in ({}, None):
            with self.subTest(payload=payload):
                def invalid_courses(handler):
                    return handler._send(200, payload)
                with patch.object(mockcanvas._Handler, "r_courses", invalid_courses):
                    failed = self.command("refresh")
                self.assertIn("失败", failed["text"])
                self.assertEqual(first, self.command("report"))
                self.assertEqual(config_before, Path(self.home.archive, "config.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
