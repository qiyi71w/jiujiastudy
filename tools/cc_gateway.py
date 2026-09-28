"""Same-origin reverse gateway for independent single-account Canvas backends."""

import argparse
import base64
import hmac
import http.client
import json
import os
from pathlib import Path
import re
import stat
import time
from urllib.parse import urlsplit
from http.cookies import SimpleCookie

from flask import Flask, Response, jsonify, request
from werkzeug.exceptions import HTTPException
from waitress import serve

from cc_web_ui import WebUI

COOKIE = "__Host-coach_gateway"
BACKEND_COOKIE = "__Host-coach_session"
MAX_REQUEST = 32768
MAX_UPLOAD = 5 * 1024 * 1024 + 65536  # Only /api/syllabus; the backend re-checks the 5MB file cap.
UPLOAD_SECONDS = 1020  # Up to 8 syllabus chunks x 120s model deadline, plus fetch time.
MAX_RESPONSE = 2 * 1024 * 1024
SESSION_TTL = 43200
USERNAME = re.compile(r"[A-Za-z0-9._-]{3,64}\Z")
ACCOUNT_ID = re.compile(r"[A-Za-z0-9_-]{8,128}\Z")
REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{16,64}\Z")
LOGIN_ERROR = "用户名或密码错误"


def _read_regular(path, *, private=False):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or (info.st_mode & (0o077 if private else 0o022)):
            raise ValueError("unsafe gateway file permissions")
        if info.st_size > 1024 * 1024:
            raise ValueError("gateway file too large")
        return stream.read(1024 * 1024 + 1)


def _registry(path):
    data = json.loads(_read_regular(path))
    if not isinstance(data, dict) or set(data) != {"origin", "accounts"} or not isinstance(data["origin"], str):
        raise ValueError("invalid gateway registry")
    origin = urlsplit(data["origin"])
    if (origin.scheme != "https" or not origin.hostname or origin.username or origin.password
            or origin.port or origin.path or origin.query or origin.fragment
            or data["origin"] != f"https://{origin.netloc}"):
        raise ValueError("invalid gateway origin")
    accounts = data["accounts"]
    if not isinstance(accounts, list):
        raise ValueError("invalid gateway accounts")
    usernames, ids, ports = set(), set(), set()
    for entry in accounts:
        if not isinstance(entry, dict) or set(entry) != {"username", "account_id", "port"}:
            raise ValueError("invalid gateway account")
        username, account_id, port = entry["username"], entry["account_id"], entry["port"]
        if (not isinstance(username, str) or not USERNAME.fullmatch(username)
                or not isinstance(account_id, str) or not ACCOUNT_ID.fullmatch(account_id)
                or type(port) is not int or not 1 <= port <= 65535
                or username in usernames or account_id in ids or port in ports):
            raise ValueError("invalid or duplicate gateway account")
        usernames.add(username)
        ids.add(account_id)
        ports.add(port)
    return data


def _encode(account_id, token, key):
    payload = json.dumps([account_id, token, int(time.time()) + SESSION_TTL], separators=(",", ":")).encode()
    signature = hmac.digest(key, payload, "sha256")
    return base64.urlsafe_b64encode(payload + signature).rstrip(b"=").decode("ascii")


def _decode(value, key):
    if not value or len(value) > 2048 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        return None
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        payload, signature = raw[:-32], raw[-32:]
        expected = hmac.digest(key, payload, "sha256")
        if not payload or not hmac.compare_digest(expected, signature):
            return None
        account_id, token, expiry = json.loads(payload)
        if (not isinstance(account_id, str) or not ACCOUNT_ID.fullmatch(account_id)
                or not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,256}", token)
                or type(expiry) is not int or not time.time() < expiry <= time.time() + SESSION_TTL):
            return None
        return account_id, token
    except (ValueError, TypeError, UnicodeError):
        return None


def create_app(registry_path, key_path, *, testing=False):
    key = _read_regular(key_path, private=True)
    if len(key) < 32:
        raise ValueError("gateway signing key must contain at least 32 bytes")
    _registry(registry_path)
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST
    backend_cookie = "coach_session" if testing else BACKEND_COOKIE
    js = Path(__file__).with_name("web.js").read_text(encoding="utf-8")

    def fail(status, text="请求未完成"):
        return jsonify(error=text), status

    @app.before_request
    def protect():
        limit = MAX_UPLOAD if request.path == "/api/syllabus" else MAX_REQUEST
        request.max_content_length = limit
        if request.content_length and request.content_length > limit:
            return fail(413)
        try:
            registry = _registry(registry_path)
        except (OSError, ValueError, TypeError, UnicodeError):
            return fail(503)
        request.environ["gateway.registry"] = registry
        if request.host.lower() != urlsplit(registry["origin"]).netloc.lower():
            return fail(400, "网站地址不匹配")
        if request.method == "POST" and request.headers.get("Origin") != registry["origin"]:
            return fail(403, "请求来源无效，请从网站重新操作")

    @app.after_request
    def secure(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self'; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.errorhandler(HTTPException)
    def http_error(error):
        return fail(error.code if error.code in (400, 404, 405, 413) else 400)

    @app.get("/")
    def index():
        return Response(WebUI.page(), mimetype="text/html")

    @app.get("/assets/style.css")
    def stylesheet():
        return Response(WebUI.stylesheet(), mimetype="text/css")

    @app.get("/assets/app.js")
    def script():
        return Response(js, mimetype="text/javascript")

    @app.get("/health")
    def health():
        return jsonify(status="ready")

    def upstream_error(status):
        response, _ = fail(status)
        response.status_code = status
        return response, []

    def upstream(port, path, method="GET", body=None, token=None, stream=False):
        connection = http.client.HTTPConnection("127.0.0.1", port,
            timeout=65 if stream else UPLOAD_SECONDS if path == "/api/syllabus" else 30)
        headers = {"Host": request.host, "Accept": "text/event-stream" if stream else "application/json",
                   "Accept-Encoding": "identity"}
        if request.method == "POST":
            headers["Origin"] = request.headers["Origin"]
            headers["Content-Type"] = request.headers.get("Content-Type", "application/json")
            headers["X-CSRF-Token"] = request.headers.get("X-CSRF-Token", "")
        if token:
            headers["Cookie"] = f"{backend_cookie}={token}"
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            if 300 <= response.status < 400:
                return upstream_error(502)
            if stream and response.status == 200 and response.getheader("Content-Type", "").split(";", 1)[0].strip() == "text/event-stream":
                def events():
                    try:
                        while chunk := response.read1(4096):
                            yield chunk
                    except (OSError, http.client.HTTPException):
                        return
                    finally:
                        connection.close()
                streamed = Response(events(), status=200, mimetype="text/event-stream",
                                    headers={"X-Accel-Buffering": "no"})
                streamed.call_on_close(connection.close)
                return streamed, []
            length = response.getheader("Content-Length")
            if length is not None and (not length.isdecimal() or int(length) > MAX_RESPONSE):
                return upstream_error(502)
            content = response.read(MAX_RESPONSE + 1)
            if len(content) > MAX_RESPONSE:
                return upstream_error(502)
            result = Response(content, status=response.status,
                              content_type=response.getheader("Content-Type", "application/json"))
            return result, response.getheaders()
        except (OSError, http.client.HTTPException, ValueError):
            return upstream_error(502)
        finally:
            if not (stream and 'response' in locals() and response.status == 200
                    and response.getheader("Content-Type", "").split(";", 1)[0].strip() == "text/event-stream"):
                connection.close()

    def backend_route(path, *, method="GET", body=None, token=None, stream=False):
        account = next((a for a in request.environ["gateway.registry"]["accounts"]
                        if a["account_id"] == token[0]), None) if token else None
        if account is None:
            return None
        return upstream(account["port"], path, method=method, body=body,
                        token=token[1], stream=stream)

    def credentials():
        cookie = _decode(request.cookies.get(COOKIE), key)
        if cookie is None:
            return None
        return cookie if any(a["account_id"] == cookie[0] for a in request.environ["gateway.registry"]["accounts"]) else None

    def protected(path, *, post=False, stream=False):
        token = credentials()
        if token is None:
            return fail(401)
        body = request.get_data(cache=False) if post else None
        result = backend_route(path, method="POST" if post else "GET", body=body, token=token, stream=stream)
        return result[0]

    @app.post("/api/login")
    def login():
        if request.mimetype != "application/json":
            return fail(400)
        body = request.get_data(cache=False)
        try:
            payload = json.loads(body)
        except (UnicodeError, ValueError):
            return fail(400)
        if not isinstance(payload, dict) or not isinstance(payload.get("username"), str):
            return fail(400)
        account = next((a for a in request.environ["gateway.registry"]["accounts"]
                        if a["username"] == payload["username"]), None)
        if account is None:
            return fail(401, LOGIN_ERROR)
        result = upstream(account["port"], "/api/login", "POST", body=body)
        if not isinstance(result, tuple) or not isinstance(result[0], Response):
            return fail(503)
        response, headers = result
        if response.status_code != 200:
            if response.status_code == 401:
                return fail(401, LOGIN_ERROR)
            return response
        cookies = SimpleCookie()
        try:
            for name, value in headers:
                if name.lower() == "set-cookie":
                    cookies.load(value)
            backend_token = cookies[backend_cookie].value
            identity = response.get_json()
            if (not re.fullmatch(r"[A-Za-z0-9_-]{20,256}", backend_token)
                    or not isinstance(identity, dict) or identity.get("username") != account["username"]):
                raise ValueError("invalid backend login")
        except (KeyError, ValueError, TypeError):
            return fail(502)
        response.set_cookie(COOKIE, _encode(account["account_id"], backend_token, key),
                            secure=True, httponly=True, samesite="Strict", max_age=SESSION_TTL, path="/")
        return response

    @app.get("/api/session")
    def session():
        if credentials() is None:
            return jsonify(authenticated=False)
        return protected("/api/session")

    @app.get("/api/state")
    def state():
        return protected("/api/state")

    @app.get("/api/ai-progress/<request_id>")
    def progress(request_id):
        if not REQUEST_ID.fullmatch(request_id):
            return fail(404)
        return protected("/api/ai-progress/" + request_id,
                         stream=request.accept_mimetypes.best == "text/event-stream")

    @app.post("/api/action")
    def action():
        return protected("/api/action", post=True)

    @app.post("/api/syllabus")
    def syllabus():
        return protected("/api/syllabus", post=True)

    @app.post("/api/logout")
    def logout():
        result = protected("/api/logout", post=True)
        if isinstance(result, Response) and result.status_code == 200:
            result.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="Strict")
        return result

    @app.post("/api/password")
    def password():
        result = protected("/api/password", post=True)
        if isinstance(result, Response) and result.status_code == 200:
            result.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="Strict")
        return result

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local account gateway")
    parser.add_argument("--registry", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--listen", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    args = parser.parse_args(argv)
    if args.listen != "127.0.0.1" or not 1 <= args.port <= 65535:
        parser.error("gateway must bind IPv4 localhost on a valid port")
    serve(create_app(args.registry, args.key), host=args.listen, port=args.port, threads=16)


if __name__ == "__main__":
    main()
