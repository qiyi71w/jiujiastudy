"""Authenticated, same-origin HTTP adapter for one Canvas account."""
import hmac
import hashlib
import json
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

from cc_account import StateConflict
from cc_service_security import ServiceSecrets
from cc_web_auth import WebAuth
from cc_web_ui import WebUI


def create_app(service, origin, *, testing=False):
    parsed = urlsplit(origin)
    if testing and parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost") and parsed.path in ("", "/"):
        origin = origin.rstrip("/")
    else:
        origin = ServiceSecrets._canonical_origin(origin)
    app = Flask(__name__, static_folder=None)
    app.config.update(TESTING=testing, MAX_CONTENT_LENGTH=32768)
    auth = WebAuth(service.home, service.account_id)
    cookie = "coach_session" if testing else "__Host-coach_session"
    js = Path(__file__).with_name("web.js").read_text(encoding="utf-8")
    ai_progress = {}
    progress_lock = threading.Lock()
    progress_changed = threading.Condition(progress_lock)

    def progress_view(job):
        return {key: value for key, value in job.items() if key not in ("owner", "started", "finished", "revision", "subscribed")} | {
            "elapsed_seconds": round((job.get("finished") or time.monotonic()) - job["started"], 1)}

    def failure(text, code):
        return jsonify(error=text), code

    def credentials():
        token = request.cookies.get(cookie, "")
        if not token:
            raise PermissionError("请先登录")
        return token, auth.authenticate(token)

    def payload():
        if request.mimetype != "application/json":
            raise ValueError("请求必须为 JSON")
        data = request.get_json()
        if not isinstance(data, dict):
            raise ValueError("请求参数无效")
        return data

    @app.before_request
    def protect():
        if request.host.lower() != urlsplit(origin).netloc.lower():
            return failure("网站地址不匹配", 400)
        if request.method == "POST":
            if request.headers.get("Origin") != origin:
                return failure("请求来源无效，请从网站重新操作", 403)
            if request.path != "/api/login":
                try:
                    _, session = credentials()
                except PermissionError:
                    return failure("登录已过期，请重新登录", 401)
                if not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), session["csrf"]):
                    return failure("请求验证失败，请刷新页面", 403)

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self'; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if not testing:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.errorhandler(PermissionError)
    def unauthorized(error):
        return failure("用户名或密码错误、尝试过于频繁，或登录已过期", 401)

    @app.errorhandler(StateConflict)
    def conflict(error):
        return failure(str(error), 409)

    @app.errorhandler(ValueError)
    def invalid(error):
        return failure(str(error), 400)

    @app.errorhandler(HTTPException)
    def http_error(error):
        return failure("请求无效或内容过大", error.code)

    @app.errorhandler(Exception)
    def unexpected(error):
        return failure("操作未完成，请刷新状态后重试", 503)

    @app.get("/")
    def index():
        return app.response_class(WebUI.page(), mimetype="text/html")

    @app.get("/assets/style.css")
    def style():
        return app.response_class(WebUI.stylesheet(), mimetype="text/css")

    @app.get("/assets/app.js")
    def script():
        return app.response_class(js, mimetype="text/javascript")

    @app.get("/health")
    def health():
        return jsonify(status="ready")

    @app.get("/api/session")
    def session():
        try:
            _, info = credentials()
        except PermissionError:
            return jsonify(authenticated=False)
        return jsonify(authenticated=True, username=info["username"], csrf=info["csrf"])

    @app.post("/api/login")
    def login():
        data = payload()
        info = auth.login(data.get("username"), data.get("password"))
        response = jsonify(username=info["username"], csrf=info["csrf"])
        response.set_cookie(cookie, info["token"], secure=not testing, httponly=True,
                            samesite="Strict", max_age=43200, path="/")
        return response

    @app.post("/api/logout")
    def logout():
        token, _ = credentials()
        auth.logout(token)
        response = jsonify(ok=True)
        response.delete_cookie(cookie, path="/", secure=not testing, httponly=True, samesite="Strict")
        return response

    @app.post("/api/password")
    def password():
        token, _ = credentials()
        data = payload()
        auth.change_password(token, data.get("current_password"), data.get("new_password"))
        response = jsonify(ok=True)
        response.delete_cookie(cookie, path="/", secure=not testing, httponly=True, samesite="Strict")
        return response

    @app.get("/api/state")
    def state():
        token, _ = credentials()
        data = service.portal_state()
        auth.authenticate(token)
        return jsonify(data)

    @app.get("/api/ai-progress/<request_id>")
    def analysis_progress(request_id):
        token, _ = credentials()
        owner = hashlib.sha256(token.encode()).hexdigest()
        streaming = request.accept_mimetypes.best == "text/event-stream"
        with progress_changed:
            # The preview connection can arrive just before its POST registers the job.
            if streaming and request_id not in ai_progress:
                progress_changed.wait_for(lambda: request_id in ai_progress, timeout=2)
            job = ai_progress.get(request_id)
            if not job or job["owner"] != owner:
                return failure("没有这次解读的进度记录", 404)
            if not streaming:
                data = progress_view(job)
            elif job.get("subscribed"):
                return failure("这次解读已有预览连接", 409)
            else:
                job["subscribed"] = True
        if not streaming:
            auth.authenticate(token)
            return jsonify(data)

        def events():
            revision = -1
            try:
                while True:
                    with progress_changed:
                        if job["revision"] == revision and "finished" not in job:
                            progress_changed.wait(timeout=1)
                        data = progress_view(job)
                        revision = job["revision"]
                        finished = "finished" in job
                    # Recheck each emission: an open connection is not a permanent grant.
                    try:
                        auth.authenticate(token)
                    except PermissionError:
                        return
                    yield "event: progress\ndata: " + json.dumps(data, ensure_ascii=False) + "\n\n"
                    if finished:
                        return
            finally:
                with progress_changed:
                    job["subscribed"] = False

        return app.response_class(events(), mimetype="text/event-stream",
                                  headers={"X-Accel-Buffering": "no"})

    @app.post("/api/action")
    def action():
        token, _ = credentials()
        data = payload()
        if set(data) - {"action", "id", "value", "version", "params", "request_id"}:
            raise ValueError("请求包含不支持的参数")
        request_id = data.pop("request_id", None)
        job = None
        if data.get("action") == "ai":
            if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", request_id):
                raise ValueError("解读请求标识无效")
            with progress_lock:
                for old_id in list(ai_progress):
                    if ai_progress[old_id].get("finished", float("inf")) < time.monotonic() - 600:
                        del ai_progress[old_id]
                if request_id in ai_progress:
                    raise StateConflict("这次解读请求已受理，请勿重复提交")
                if len(ai_progress) >= 16:
                    oldest = next((key for key, value in ai_progress.items() if "finished" in value), None)
                    if oldest is None:
                        raise StateConflict("解读请求正在处理，请稍后再试")
                    del ai_progress[oldest]
                job = {"request_id": request_id, "owner": hashlib.sha256(token.encode()).hexdigest(),
                       "model": service.secrets.ai_model or "未配置", "status": "queued",
                       "started": time.monotonic(), "tokens_per_second": None, "output_tokens": None,
                       "revision": 0, "preview": ""}
                ai_progress[request_id] = job
                progress_changed.notify_all()
        elif request_id is not None:
            raise ValueError("仅 AI 解读接受进度标识")

        def update_progress(update):
            with progress_changed:
                job.update(update)
                if job["status"] in ("failed", "cancelled", "skipped"):
                    job["preview"] = ""
                job["revision"] += 1
                progress_changed.notify_all()

        try:
            result = service.portal_action(data, progress=update_progress if job else None)
            auth.authenticate(token)
            if job:
                update_progress({"status": "cancelled" if result.get("stale") else result.get("ai_status", "skipped"),
                                 "finished": time.monotonic()})
            response = {"result": {"text": result["text"]}, "state": service.portal_state()}
            if job:
                with progress_lock:
                    response["progress"] = progress_view(job)
            return jsonify(response)
        except Exception:
            if job:
                update_progress({"status": "failed", "finished": time.monotonic()})
            raise

    return app
