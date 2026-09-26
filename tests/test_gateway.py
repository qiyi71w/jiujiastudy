"""Gateway boundary tests against two independent loopback HTTP backends."""

import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time
import unittest
from werkzeug.serving import make_server
from flask import Flask, jsonify, request, Response

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from cc_gateway import create_app, COOKIE  # noqa: E402

ORIGIN = "https://study.qiyi71w.com"
PASSWORD = "correct-horse-long-password"


class Backend:
    def __init__(self, username):
        self.username = username
        self.tokens = {}
        self.received = []
        self.redirect_state = False
        self.app = Flask(username)
        app = self.app

        @app.before_request
        def boundary():
            self.received.append((request.path, request.headers.get("Host"), request.headers.get("Origin"),
                                  request.headers.get("X-Account-Id"), request.headers.get("Cookie")))
            if request.host != "study.qiyi71w.com":
                return jsonify(error="host"), 400
            if request.method == "POST" and request.headers.get("Origin") != ORIGIN:
                return jsonify(error="origin"), 403
            if request.method == "POST" and request.path != "/api/login":
                token = request.cookies.get("__Host-coach_session")
                if not token or token not in self.tokens:
                    return jsonify(error="auth"), 401
                if request.headers.get("X-CSRF-Token") != self.tokens[token]:
                    return jsonify(error="csrf"), 403

        @app.post("/api/login")
        def login():
            body = request.get_json()
            if body.get("username") != username or body.get("password") != PASSWORD:
                return jsonify(error="wrong"), 401
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
            self.tokens[token] = csrf
            response = jsonify(username=username, csrf=csrf)
            response.set_cookie("__Host-coach_session", token, secure=True, httponly=True, path="/")
            return response

        @app.get("/api/session")
        def session():
            csrf = self.tokens.get(request.cookies.get("__Host-coach_session"))
            return jsonify(authenticated=True, username=username, csrf=csrf) if csrf else jsonify(authenticated=False)

        @app.get("/api/state")
        def state():
            if self.redirect_state:
                return "", 302, {"Location": "https://evil.example/api/state"}
            if request.cookies.get("__Host-coach_session") not in self.tokens:
                return jsonify(error="auth"), 401
            return jsonify(owner=username)

        @app.post("/api/action")
        def action():
            return jsonify(owner=username)

        @app.post("/api/logout")
        @app.post("/api/password")
        def revoke():
            self.tokens.clear()
            response = jsonify(ok=True)
            response.delete_cookie("__Host-coach_session", path="/")
            return response

        @app.get("/api/ai-progress/<request_id>")
        def progress(request_id):
            if request.cookies.get("__Host-coach_session") not in self.tokens:
                return jsonify(error="auth"), 401
            if request.accept_mimetypes.best != "text/event-stream":
                return jsonify(owner=username)

            def events():
                yield b"event: progress\ndata: first\n\n"
                time.sleep(0.6)
                yield b"event: progress\ndata: second\n\n"
            return Response(events(), content_type="text/event-stream")

        self.server = make_server("127.0.0.1", 0, app, threaded=True)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.thread.join(timeout=3)


@unittest.skipUnless(os.name == "posix", "requires POSIX secret permissions")
class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.backends = [Backend("alice"), Backend("bob")]
        self.addCleanup(lambda: [backend.close() for backend in self.backends])
        root = Path(self.temp.name)
        self.registry_path = root / "registry.json"
        self.key_path = root / "key"
        self.key_path.write_bytes(secrets.token_bytes(32))
        self.key_path.chmod(0o600)
        self.accounts = [dict(username=b.username, account_id="account-" + b.username, port=b.port)
                         for b in self.backends]
        self.save_registry()
        self.app = create_app(self.registry_path, self.key_path)
        self.client = self.app.test_client()

    def save_registry(self, accounts=None):
        self.registry_path.write_text(json.dumps({"origin": ORIGIN, "accounts": self.accounts if accounts is None else accounts}))
        self.registry_path.chmod(0o644)

    def get(self, path, **kwargs):
        return self.client.get(path, base_url=ORIGIN, **kwargs)

    def post(self, path, body=None, csrf=None, **kwargs):
        headers = {"Origin": ORIGIN}
        if csrf is not None:
            headers["X-CSRF-Token"] = csrf
        headers.update(kwargs.pop("headers", {}))
        return self.client.post(path, base_url=ORIGIN, headers=headers, json=body or {}, **kwargs)

    def login(self, name, password=PASSWORD):
        return self.post("/api/login", {"username": name, "password": password})

    def test_accounts_are_isolated_and_failed_login_does_not_retarget(self):
        first = self.login("alice")
        self.assertEqual(first.status_code, 200)
        self.assertIn(COOKIE, first.headers["Set-Cookie"])
        self.assertNotIn("coach_session=", first.headers["Set-Cookie"])
        self.assertNotIn(next(iter(self.backends[0].tokens)), first.headers["Set-Cookie"])
        self.assertEqual(self.get("/api/state").json["owner"], "alice")
        csrf = self.get("/api/session").json["csrf"]
        self.assertEqual(self.post("/api/action", csrf=csrf,
                                   headers={"X-Account-Id": "account-bob", "X-Backend-Port": "1"}).json["owner"], "alice")
        self.assertEqual(self.post("/api/action", csrf="wrong").status_code, 403)
        self.assertEqual(self.login("bob", "wrong-password").status_code, 401)
        self.assertEqual(self.login("nobody").json, self.login("bob", "wrong-password").json)
        self.assertEqual(self.get("/api/state").json["owner"], "alice")
        second = self.login("bob")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(self.get("/api/state").json["owner"], "bob")
        self.assertEqual(len(self.backends[0].tokens), 1)
        self.assertEqual(len(self.backends[1].tokens), 1)
        for backend in self.backends:
            self.assertTrue(all(host == "study.qiyi71w.com" for _, host, _, _, _ in backend.received))
            self.assertTrue(all(account is None for _, _, _, account, _ in backend.received))
        self.assertEqual(self.backends[0].received[-1][1], "study.qiyi71w.com")

    def test_tamper_reassignment_and_backend_revocation(self):
        self.assertFalse(self.get("/api/session").json["authenticated"])
        self.assertEqual(self.get("/api/state").status_code, 401)
        self.login("alice")
        original = self.client.get_cookie(COOKIE, domain="study.qiyi71w.com").value
        self.client.set_cookie(COOKIE, original[:20] + ("A" if original[20] != "A" else "B") + original[21:],
                               domain="study.qiyi71w.com", secure=True)
        self.assertFalse(self.get("/api/session").json["authenticated"])
        self.assertEqual(self.get("/api/state").status_code, 401)
        self.client.set_cookie(COOKIE, original, domain="study.qiyi71w.com", secure=True)
        self.backends[0].tokens.clear()
        self.assertEqual(self.get("/api/state").status_code, 401)
        self.assertFalse(self.get("/api/session").json["authenticated"])
        self.login("alice")
        self.save_registry(self.accounts[1:])
        self.assertFalse(self.get("/api/session").json["authenticated"])
        self.assertEqual(self.get("/api/state").status_code, 401)

    def test_logout_and_password_clear_gateway_cookie(self):
        for route in ("/api/logout", "/api/password"):
            self.login("alice")
            csrf = self.get("/api/session").json["csrf"]
            cookie = self.client.get_cookie(COOKIE, domain="study.qiyi71w.com").value
            res = self.post(route, csrf=csrf)
            self.assertEqual(res.status_code, 200)
            self.assertIn("Max-Age=0", res.headers["Set-Cookie"])
            self.assertEqual(self.get("/api/state").status_code, 401)
            self.client.set_cookie(COOKIE, cookie, domain="study.qiyi71w.com", secure=True)
            self.assertEqual(self.get("/api/state").status_code, 401)

    def test_registry_rejects_duplicates_and_key_permissions(self):
        for field in ("username", "account_id", "port"):
            dupes = [dict(account) for account in self.accounts]
            dupes[1][field] = dupes[0][field]
            self.save_registry(dupes)
            self.assertEqual(self.get("/api/session").status_code, 503)
        self.save_registry()
        self.key_path.chmod(0o644)
        with self.assertRaises(ValueError):
            create_app(self.registry_path, self.key_path)

    def test_host_origin_route_and_body_guards(self):
        self.assertEqual(self.client.get("/health", base_url="https://evil.example").status_code, 400)
        self.assertEqual(self.client.post("/api/login", base_url=ORIGIN, json={},
                                          headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.get("/api/other").status_code, 404)
        self.assertEqual(self.post("/api/login", {"username": "alice", "password": "x" * 33000}).status_code, 413)
        self.assertEqual(self.get("/health").json["status"], "ready")
        self.assertEqual(self.get("/assets/app.js").status_code, 200)
        self.assertEqual(self.get("/").status_code, 200)

    def test_upstream_redirect_is_not_followed(self):
        self.login("alice")
        self.backends[0].redirect_state = True
        response = self.get("/api/state")
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("Location", response.headers)

    def test_sse_first_event_arrives_before_delayed_second(self):
        self.login("alice")
        res = self.get("/api/ai-progress/abcdefghijklmnop", headers={"Accept": "text/event-stream"}, buffered=False)
        try:
            start = time.monotonic()
            first = next(iter(res.response))
            self.assertIn(b"data: first", first)
            self.assertLess(time.monotonic() - start, 0.4)
            self.assertIn(b"data: second", b"".join(res.response))
        finally:
            res.close()


if __name__ == "__main__":
    unittest.main()
