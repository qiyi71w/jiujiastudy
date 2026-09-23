"""Authenticated account commands, per-course snapshots and rule reports."""
import datetime as dt
import os
import re
import secrets
from zoneinfo import ZoneInfo
from urllib.parse import urlencode, urlsplit
import uuid

from cc_collect import refresh_courses, snapshot_from, strip_html
from cc_config import Ctx, effective, minimal_state, version_of
from cc_courses import course_pairs
from cc_service_security import SecureCanvas, ServiceSecrets
from cc_schedule import ensure_plan, planned, settings
from cc_store import FileLock, jload, jsave
from cc_time import parse_ts
from cc_ai import analyze

class _StaleOperation(Exception):
    """The account changed service generation during this operation."""


class _SwitchFailure(Exception):
    def __init__(self, generation):
        self.generation = generation



COMMANDS = {
    "help": "显示全部命令及可用条件；关闭时也可用，不访问 Canvas",
    "status": "查看服务及快照状态；关闭时也可用，不访问 Canvas",
    "report": "查看当前快照日报及数据时间；关闭时也可用，不访问 Canvas",
    "refresh": "仅服务开启时立即重新采集并生成规则日报；关闭时拒绝",
    "ai": "完整即时采集后生成摘要或回答：/canvas ai [问题]；须开启 AI 且有剩余额度；例：/canvas ai 未来七天有哪些待交作业？",
    "courses": "查看监控与已停止课程，通过按钮停止或重新加入；关闭时只保存选择",
    "tasks": "查看任务、已停止项和确认按钮；/canvas tasks stopped 查看已停止项；关闭时也可用，不访问 Canvas",
    "schedule": "设置每日基准时间及 IANA 时区：/canvas schedule HH:MM Area/City；关闭时也可用",
    "on": "开启服务，立即采集并恢复当日计划；已开启时不重复采集",
    "off": "暂停采集、AI 和日报；仍可查看旧报告及管理设置",
    "settings": "查看或调整 AI、自动摘要及公告正文授权；关闭时可用，不访问 Canvas",

}


class _CollectionContext(Ctx):
    """Course discovery stages configuration until collection succeeds."""

    def save_config(self):
        self.cfg = effective(self.raw_cfg)


def _submission_state(submission, submission_types):
    """Grades alone cannot establish a submission; redo_request is explicit."""
    if submission.get("excused") is True:
        return "excused"
    if submission.get("redo_request") is True:
        return "resubmit"
    workflow = submission.get("workflow_state")
    if workflow in ("submitted", "pending_review") or parse_ts(submission.get("submitted_at")):
        return "submitted"
    online = {"online_upload", "online_text_entry", "online_url", "online_quiz",
              "media_recording", "student_annotation"}
    if workflow == "unsubmitted" and set(submission_types) & online:
        return "unsubmitted"
    return "unknown"


class AccountService:
    def __init__(self, home, secrets_path):
        self.home = os.path.abspath(home)
        self.secrets_path = secrets_path
        config = jload(os.path.join(self.home, "config.json"))
        self.secrets = ServiceSecrets(config, secrets_path)
        if version_of(config) < 3:
            raise ValueError("服务需要当前版本的独立档案")
        state_path = os.path.join(self.home, "state.json")
        if not os.path.exists(state_path):
            jsave(state_path, minimal_state())
        ctx = Ctx(self.home, quiet=True)
        if ctx.is_v1:
            raise ValueError("服务需要当前版本的独立档案")
        self.user_id = self.secrets.user_id
        self.binding = self._binding(self.secrets)
        self.report_path = ctx.P("service-report.json")
        with FileLock(os.path.join(self.home, "service-state.lock")):
            saved = self._saved() or {"binding": self.binding}
            if "ai_quota_zone" not in saved:
                saved["ai_quota_zone"] = settings(ctx.cfg)[1]
                jsave(self.report_path, saved)

    @staticmethod
    def _binding(secrets):
        return {"origin": secrets.canvas_origin, "telegram_user_id": secrets.user_id,
                "bot_id": secrets.telegram_bot_token.split(":", 1)[0]}

    def _saved(self):
        saved = jload(self.report_path)
        if saved and saved.get("binding") == self.binding:
            # Ticket 01 reports have no journal; establish a conservative baseline.
            if saved.get("format") != 2 and saved.get("snapshot"):
                snap = saved["snapshot"]
                freshness = snap.setdefault("course_freshness", {})
                for fact in snap.get("assignments", {}).values():
                    freshness.setdefault(fact["course"], saved.get("collected_at"))
                    fact["sub_state"] = _submission_state(
                        {"workflow_state": fact.get("sub_state"), "submitted_at": fact.get("submitted_at"),
                         "excused": fact.get("excused")}, fact.get("submission_types", []))
            return saved
        return None

    def generation(self):
        saved = self._saved() or {}
        return saved.get("service_generation", 0)

    def permitted(self, generation):
        saved = self._saved() or {}
        return saved.get("service_enabled", True) and saved.get("service_generation", 0) == generation

    def _require(self, generation):
        if not self.permitted(generation):
            raise _StaleOperation()

    def _ai_state(self, ctx, saved):
        ai = (saved or {}).get("ai", {})
        zone = (saved or {}).get("ai_quota_zone", settings(ctx.cfg)[1])
        day = ctx.clock.now_utc().astimezone(ZoneInfo(zone)).date().isoformat()
        used = (saved or {}).get("ai_usage", {}).get(day, 0)
        available = bool(self.secrets.ai_origin and self.secrets.ai_daily_limit)
        return ai, day, used, available

    def ai_settings(self, identity, action=None):
        ctx = self._authorized(identity)
        if action == "ai:settings":
            action = None
        with FileLock(os.path.join(self.home, "service-state.lock")):
            saved = self._saved() or {"binding": self.binding, "ai_quota_zone": settings(ctx.cfg)[1]}
            ai = saved.setdefault("ai", {"enabled": False, "summary": False, "announcements": False})
            if action is not None:
                token = action[3:] if isinstance(action, str) and action.startswith("ai:") else ""
                button = saved.get("ai_buttons", {}).pop(token, None)
                if (not button or ctx.clock.now_utc().timestamp() - button["at"] > 900
                        or bool(ai.get(button["key"])) != button["before"]):
                    jsave(self.report_path, saved)
                    return self._result("设置按钮已过期，请重新打开设置。")
                ai[button["key"]] = not button["before"]
                saved["ai_revision"] = saved.get("ai_revision", 0) + 1
                for plan in saved.get("daily_plans", {}).values():
                    if plan.get("delivery", {}).get("telegram", {}).get("state") == "pending":
                        plan.pop("ai_text", None)
                        if plan.get("ai_state") in ("pending", "running"):
                            plan["ai_state"] = "done"
                            plan.pop("ai_snapshot", None)
                if button["key"] == "announcements" and not ai["announcements"]:
                    for snapshot in (saved.get("snapshot"), saved.get("previous_success")):
                        if snapshot:
                            snapshot.pop("ai_announcements", None)
            ai, day, used, available = self._ai_state(ctx, saved)
            actions = []
            for key, label in (("enabled", "AI 主开关"), ("summary", "每日自动摘要"),
                               ("announcements", "公告正文授权")):
                token = uuid.uuid4().hex
                saved.setdefault("ai_buttons", {})[token] = {"key": key, "before": bool(ai.get(key)),
                                                              "at": ctx.clock.now_utc().timestamp()}
                actions.append([{"text": label + ("：关" if ai.get(key) else "：开"), "data": "ai:" + token}])
            saved["ai_buttons"] = dict(list(saved["ai_buttons"].items())[-300:])
            jsave(self.report_path, saved)
        limit = self.secrets.ai_daily_limit
        text = ("AI 设置（仅绑定私聊）：\n"
                f"主开关：{'开' if ai.get('enabled') else '关'}；每日自动摘要：{'开' if ai.get('summary') else '关'}；"
                f"公告正文授权：{'开' if ai.get('announcements') else '关'}。\n"
                f"管理员额度日：{day}（{saved['ai_quota_zone']}）；剩余：{max(0, limit - used)}/{limit}；"
                f"{'可用' if available and used < limit else '未配置或额度不足'}。")
        return {**self._result(text, saved), "actions": actions}

    @staticmethod
    def _ai_unavailable(ai, used, available, limit):
        if not ai.get("enabled"):
            return "AI 未启用；可在设置中开启。"
        if not available:
            return "管理员尚未配置 AI 接口或每日额度。"
        if used >= limit:
            return "AI 今日额度已用尽。"
        return None

    def _reserve_ai(self, ctx, generation):
        with FileLock(os.path.join(self.home, "service-state.lock")):
            self._require(generation)
            saved = self._saved()
            ai, day, used, available = self._ai_state(ctx, saved)
            reason = self._ai_unavailable(ai, used, available, self.secrets.ai_daily_limit)
            if reason:
                return reason
            saved.setdefault("ai_usage", {})[day] = used + 1
            saved["ai_usage"] = {key: value for key, value in saved["ai_usage"].items() if key >= day}
            jsave(self.report_path, saved)
        return None
    def _require_ai(self, generation, revision):
        self._require(generation)
        saved = self._saved() or {}
        if saved.get("ai_revision", 0) != revision or not saved.get("ai", {}).get("enabled"):
            raise _StaleOperation()

    def _ai_input(self, ctx, saved, snapshot):
        active = {str(cid) for cid, _ in self._monitored(ctx, saved)}
        def safe_url(value):
            if not isinstance(value, str):
                return None
            try:
                url = urlsplit(value)
            except ValueError:
                return None
            if (url.scheme != "https" or url.username or url.password
                    or f"https://{url.netloc.lower()}" != self.secrets.canvas_origin):
                return None
            return url._replace(query="", fragment="").geturl()
        assignments = {}
        for aid, fact in snapshot.get("assignments", {}).items():
            if fact.get("course_id") in active:
                assignments[aid] = {key: fact.get(key) for key in ("course", "name", "due_at", "sub_state")}
                assignments[aid]["html_url"] = safe_url(fact.get("html_url"))
        announcements = [{"source": safe_url(ann.get("source")), "text": ann.get("text")}
                         for ann in snapshot.get("ai_announcements", [])]
        return {"assignments": assignments}, [ann for ann in announcements if ann["source"]]

    @staticmethod
    def _result(text, saved=None):
        saved = saved or {}
        attempt = saved.get("last_attempt", {})
        return {"text": text, "collected_at": saved.get("collected_at"),
                "complete": attempt.get("complete", bool(saved.get("snapshot"))),
                "failures": attempt.get("failures", []), "actions": []}

    def _report_result(self, ctx, saved):
        if not saved:
            return self._result("尚无成功快照；使用 /canvas refresh。")
        events = [e for e in saved.get("events", []) if "manual" in e["pending"]]
        result = self._result(self._report(ctx, saved, events), saved)
        result["delivery"] = {"epoch": saved.get("epoch"), "events": [e["id"] for e in events]}
        return result

    def _plan(self, ctx, saved):
        saved = dict(saved or {"binding": self.binding})
        ensure_plan(saved, ctx.cfg, ctx.clock.now_utc())
        return saved

    def next_scan(self, ctx):
        with FileLock(os.path.join(self.home, "service-state.lock")):
            if not (self._saved() or {}).get("service_enabled", True):
                return "已暂停"
            saved = self._plan(ctx, self._saved())
            clock, zone, tz = settings(ctx.cfg)
            now = ctx.clock.now_utc()
            _, eligible = ensure_plan(saved, ctx.cfg, now)
            candidates = [saved["daily_plans"][key]["due"] for key in eligible
                          if saved["daily_plans"][key]["state"] in ("pending", "collecting")]
            if not candidates:
                tomorrow = now.astimezone(tz).date() + dt.timedelta(days=1)
                key = tomorrow.isoformat()
                if key not in saved["daily_plans"]:
                    delay = secrets.randbelow(901)
                    saved["daily_plans"][key] = {"due": planned(tomorrow, clock, tz, delay),
                        "delay": delay, "timezone": zone, "time": clock,
                        "state": "pending", "delivery": {}}
                candidates = [saved["daily_plans"][tomorrow.isoformat()]["due"]]
            jsave(self.report_path, saved)
        return dt.datetime.fromisoformat(min(candidates)).astimezone(tz).isoformat() + " (" + zone + ")"

    def set_schedule(self, ctx, params):
        if len(params) != 2 or not re.fullmatch(r"\d{2}:\d{2}", params[0]):
            return self._result("用法：/canvas schedule HH:MM Area/City")
        try:
            settings({"service": {"daily_time": params[0], "daily_timezone": params[1]}})
        except ValueError:
            return self._result("需要有效的 HH:MM 和 IANA 时区。")
        # Refresh also writes config.json: serialize settings against that writer.
        with FileLock(os.path.join(self.home, "service-refresh.lock")):
            with FileLock(os.path.join(self.home, "service-state.lock")):
                current = jload(os.path.join(self.home, "config.json"))
                current.setdefault("service", {}).update(daily_time=params[0], daily_timezone=params[1])
                jsave(os.path.join(self.home, "config.json"), current)
                saved = self._saved()
                if saved:
                    self._plan(_CollectionContext(self.home, quiet=True), saved)
                    jsave(self.report_path, saved)
        return self._result("每日计划已更新；实际下一次：" + self.next_scan(_CollectionContext(self.home, quiet=True)))

    def switch(self, ctx, enabled, expected_generation=None):
        """Persist the generation before acknowledging a transition."""
        now = ctx.clock.now_utc()
        day = None
        with FileLock(os.path.join(self.home, "service-state.lock")):
            saved = self._saved() or {"binding": self.binding}
            if enabled and expected_generation is not None and saved.get("service_generation", 0) != expected_generation:
                return {**self._result("本次开启已失效。"), "stale": True}
            if saved.get("service_enabled", True) == enabled:
                result = self._result("服务已开启。" if enabled else "服务已关闭。", saved)
                if enabled:
                    result["generation"] = saved.get("service_generation", 0)
                return result
            saved["service_enabled"] = enabled
            saved["service_generation"] = uuid.uuid4().hex
            generation = saved["service_generation"]
            if enabled:
                saved = self._plan(ctx, saved)
                _, eligible = ensure_plan(saved, ctx.cfg, now)
                tz = settings(ctx.cfg)[2]
                formed_today = any(p.get("state") == "formed" and p.get("formed_at") and
                                   dt.datetime.fromisoformat(p["formed_at"]).astimezone(tz).date() == now.astimezone(tz).date()
                                   for p in saved["daily_plans"].values())
                for key in eligible:
                    plan = saved["daily_plans"][key]
                    if plan["state"] == "formed":
                        for receipt in plan.get("delivery", {}).values():
                            if receipt["state"] == "pending":
                                receipt["next_at"] = now.isoformat()
                                receipt["attempts"] = 0
                    elif not formed_today and day is None and plan["state"] in ("pending", "cancelled", "collecting"):
                        plan["state"] = "collecting"
                        plan["claimed_at"] = now.isoformat()
                        day = key
            else:
                for plan in saved.get("daily_plans", {}).values():
                    if plan["state"] == "collecting":
                        plan["state"] = "cancelled"
                    if plan.get("ai_state") in ("pending", "running"):
                        plan["ai_state"] = "done"
                        plan.pop("ai_snapshot", None)
            jsave(self.report_path, saved)
        try:
            collected = self._collect(plan_day=day, generation=generation) if enabled else saved
            if enabled and not self.permitted(generation):
                return {**self._result("本次开启已失效。"), "stale": True}
            if enabled:
                if day is not None:
                    with FileLock(os.path.join(self.home, "service-state.lock")):
                        latest = self._saved()
                        if not self.permitted(generation):
                            return {**self._result("本次开启已失效。"), "stale": True}
                        plan = latest["daily_plans"][day]
                        if plan["state"] == "formed":
                            for receipt in plan.get("delivery", {}).values():
                                if receipt["state"] == "pending":
                                    receipt["next_at"] = now.isoformat()
                            jsave(self.report_path, latest)
                            collected = latest
                result = self._report_result(_CollectionContext(self.home, quiet=True), collected)
                if day is None:
                    result["text"] = "服务已开启；已立即采集。\n" + result["text"]
                if day is not None and collected.get("daily_plans", {}).get(day, {}).get("state") == "formed":
                    result["text"] = ("服务已开启；立即采集已形成当日计划日报，正在投递。数据截至：" +
                                      str(collected.get("collected_at") or "尚无成功数据"))
                result["generation"] = generation
                result["resume_delivery"] = True
                return result
            return self._result("服务已关闭；已暂停后续采集及日报。", saved)
        except Exception:
            if enabled:
                raise _SwitchFailure(generation) from None
            raise

    def scheduled_tick(self):
        """Claim a due plan; formed reports remain deliverable without another scan."""
        ctx = self._authorized(self.user_id)
        now = ctx.clock.now_utc()
        with FileLock(os.path.join(self.home, "service-state.lock")):
            saved = self._saved() or {}
            if not saved.get("service_enabled", True):
                return None
            generation = saved.get("service_generation", 0)
            saved = self._plan(ctx, saved)
            _, eligible = ensure_plan(saved, ctx.cfg, now)
            for day in eligible:
                plan = saved["daily_plans"][day]
                if plan["state"] == "collecting" and (now - dt.datetime.fromisoformat(plan["claimed_at"])).total_seconds() > 300:
                    lock = FileLock(os.path.join(self.home, "service-refresh.lock"))
                    if lock.acquire(blocking=False):
                        lock.release()
                        plan["state"] = "pending"
                if plan["state"] == "pending" and plan["due"] <= now.isoformat():
                    plan["state"] = "collecting"
                    plan["claimed_at"] = now.isoformat()
                    jsave(self.report_path, saved)
                    break
            else:
                day = None
                jsave(self.report_path, saved)
        if day is not None and self.permitted(generation):
            self._collect(plan_day=day, generation=generation)
        with FileLock(os.path.join(self.home, "service-state.lock")):
            saved = self._saved() or {}
            _, eligible = ensure_plan(saved, ctx.cfg, now)
            for key in eligible:
                plan = saved["daily_plans"][key]
                started = plan.get("ai_started_at")
                if (plan.get("ai_state") == "running" and started
                        and (now - dt.datetime.fromisoformat(started)).total_seconds() > 60):
                    plan["ai_state"] = "done"
                    plan.pop("ai_snapshot", None)
                    plan["ai_text"] = "AI 自动摘要未完成；规则日报仍可查看。"
                    jsave(self.report_path, saved)
            summaries = [key for key in eligible if saved["daily_plans"][key].get("state") == "formed"
                         and saved["daily_plans"][key].get("ai_state") == "pending"]
        for key in summaries:
            self._auto_summary(key, generation)
        with FileLock(os.path.join(self.home, "service-state.lock")):
            saved = self._saved() or {}
            if not self.permitted(generation):
                return None
            _, eligible = ensure_plan(saved, ctx.cfg, now)
            for key in eligible:
                plan = saved["daily_plans"][key]
                delivery = plan.get("delivery", {}).get("telegram")
                if plan["state"] == "formed" and delivery and delivery["state"] == "pending" and delivery["attempts"] < 4 and delivery["next_at"] <= now.isoformat():
                    if plan.get("ai_state") in ("pending", "running"):
                        return None
                    ai = saved.get("ai", {})
                    text = plan["text"]
                    if ai.get("enabled") and ai.get("summary") and plan.get("ai_text"):
                        text += "\n\n" + plan["ai_text"]
                    return {"day": key, "text": text, "generation": generation,
                            "ai_revision": saved.get("ai_revision", 0) if plan.get("ai_text") and ai.get("enabled") and ai.get("summary") else None}
        return None
    def delivery_permitted(self, due):
        saved = self._saved() or {}
        return (self.permitted(due["generation"]) and
                (due.get("ai_revision") is None or saved.get("ai_revision", 0) == due["ai_revision"]))
    def _auto_summary(self, day, generation):
        ctx = _CollectionContext(self.home, quiet=True)
        with FileLock(os.path.join(self.home, "service-state.lock")):
            if not self.permitted(generation):
                return
            saved = self._saved()
            plan = saved.get("daily_plans", {}).get(day)
            if not plan or plan.get("ai_state") != "pending":
                return
            if not saved.get("ai", {}).get("enabled") or not saved["ai"].get("summary"):
                plan["ai_state"] = "done"
                plan.pop("ai_snapshot", None)
                jsave(self.report_path, saved)
                return
            plan["ai_state"] = "running"
            plan["ai_started_at"] = ctx.clock.now_utc().isoformat()
            revision = saved.get("ai_revision", 0)
            snapshot, allowed_announcements = self._ai_input(ctx, saved, plan.get("ai_snapshot") or {})
            announcements = allowed_announcements if saved["ai"].get("announcements") else None
            jsave(self.report_path, saved)
        try:
            reason = self._reserve_ai(ctx, generation)
            if reason:
                text = "AI 自动摘要未执行：" + reason
            else:
                text = "AI 自动摘要（非 Canvas 事实）：\n" + analyze(
                    self.secrets.ai_origin, self.secrets.ai_api_key, self.secrets.ai_model,
                    snapshot, announcements=announcements, permit=lambda: self._require_ai(generation, revision))
                if announcements:
                    text += "\n公告涉及的日期仅供参考，待确认；来源：" + "、".join(a["source"] for a in announcements)
        except Exception:
            text = "AI 自动摘要失败或超时；规则日报仍可查看。"
        with FileLock(os.path.join(self.home, "service-state.lock")):
            saved = self._saved()
            if not self.permitted(generation):
                return
            plan = saved.get("daily_plans", {}).get(day)
            if plan and plan.get("ai_state") == "running":
                plan["ai_state"] = "done"
                plan.pop("ai_snapshot", None)
                if saved.get("ai_revision", 0) == revision:
                    plan["ai_text"] = text
                jsave(self.report_path, saved)

    def cancel_scheduled(self):
        """Cancel unformed plans (used by the account switch before delivery)."""
        with FileLock(os.path.join(self.home, "service-state.lock")):
            saved = self._saved()
            if not saved:
                return
            changed = False
            for plan in saved.get("daily_plans", {}).values():
                if plan["state"] in ("pending", "collecting"):
                    plan["state"] = "cancelled"
                    changed = True
            if changed:
                jsave(self.report_path, saved)


    def scheduled_delivery(self, day, success, generation=None):
        with FileLock(os.path.join(self.home, "service-state.lock")):
            saved = self._saved()
            if generation is not None and not self.permitted(generation):
                return
            plan = (saved or {}).get("daily_plans", {}).get(day)
            if not plan or plan["state"] != "formed":
                return
            receipt = plan["delivery"]["telegram"]
            if receipt["state"] != "pending":
                return
            if success:
                receipt["state"] = "sent"
                ids = set(plan.get("events", []))
                for event in saved.get("events", []):
                    if event["id"] in ids and "scheduled" in event["pending"]:
                        event["pending"].remove("scheduled")
                saved["events"] = [event for event in saved.get("events", []) if event["pending"]]
            else:
                receipt["attempts"] += 1
                delay = min(3600, 30 * 2 ** (receipt["attempts"] - 1))
                receipt["next_at"] = (_CollectionContext(self.home, quiet=True).clock.now_utc() + dt.timedelta(seconds=delay)).isoformat()
            jsave(self.report_path, saved)

    def acknowledge_delivery(self, result):
        """Called only after all message segments reached the initiating channel."""
        receipt = result.get("delivery")
        if not receipt or not receipt["events"]:
            return
        with FileLock(os.path.join(self.home, "service-state.lock")):
            saved = self._saved()
            if not saved or saved.get("epoch") != receipt["epoch"]:
                return
            delivered = set(receipt["events"])
            for event in saved.get("events", []):
                if event["id"] in delivered and "manual" in event["pending"]:
                    event["pending"].remove("manual")
            saved["events"] = [event for event in saved.get("events", []) if event["pending"]]
            jsave(self.report_path, saved)

    def _authorized(self, identity):
        ctx = _CollectionContext(self.home, quiet=True)
        secrets = ServiceSecrets(ctx.cfg, self.secrets_path)
        if identity != self.user_id or self._binding(secrets) != self.binding:
            raise ValueError("账号绑定已变更，请管理员重启服务")
        return ctx

    @staticmethod
    def _task_version(saved, aid):
        fact = saved.get("snapshot", {}).get("assignments", {}).get(aid)
        if not fact:
            return None
        return [fact.get("course"), fact.get("due_at"), fact.get("sub_state"),
                fact.get("excused"), saved.get("last_attempt", {}).get("id"),
                saved.get("reminders", {}).get(aid)]
    @staticmethod
    def _task_valid(ctx, saved, aid):
        fact = saved.get("snapshot", {}).get("assignments", {}).get(aid)
        return (fact if fact and (str(fact.get("course_id")) in {str(cid) for cid, _ in AccountService._monitored(ctx, saved)}
                if fact.get("course_id") else fact.get("course") in
                {code for _, code in AccountService._monitored(ctx, saved)}) else None)
    @staticmethod
    def _monitored(ctx, saved):
        stopped = set((saved or {}).get("stopped_courses", []))
        return [(cid, code) for cid, code in course_pairs(ctx.cfg, include_inactive=False)
                if str(cid) not in stopped]

    def course_list(self, identity):
        ctx = self._authorized(identity)
        with FileLock(os.path.join(self.home, "service-state.lock")):
            saved = self._saved() or {"binding": self.binding}
            stopped = set(saved.get("stopped_courses", []))
            lines = ["监控课程与已停止监控课程"]
            actions = []
            for course in ctx.cfg.get("courses") or []:
                cid = str(course["id"])
                inactive = bool(course.get("inactive"))
                paused = cid in stopped
                state = "已停止监控" if paused else "已结束或失去访问，旧任务暂停提醒" if inactive else "监控中"
                lines.append(f"{course['code']} · {course.get('name') or course['code']} — {state}")
                token = uuid.uuid4().hex
                saved.setdefault("course_buttons", {})[token] = {
                    "cid": cid, "stopped": paused, "inactive": inactive,
                    "at": dt.datetime.now(dt.timezone.utc).timestamp()}
                actions.append([{"text": ("重新加入" if paused else "停止监控") + " " + course["code"],
                                 "data": "course:" + token}])
            if not actions:
                lines.append("尚无课程；开启后使用 /canvas refresh 发现新课。")
            saved["course_buttons"] = dict(list(saved.get("course_buttons", {}).items())[-300:])
            jsave(self.report_path, saved)
        return {**self._result("\n".join(lines), saved), "actions": actions}

    def course_action(self, identity, data):
        ctx = self._authorized(identity)
        if not isinstance(data, str) or not data.startswith("course:") or len(data) != 39:
            return self._result("按钮无效，请重新打开课程列表。")
        with FileLock(os.path.join(self.home, "service-state.lock")):
            saved = self._saved() or {"binding": self.binding}
            button = saved.get("course_buttons", {}).pop(data[7:], None)
            courses = {str(c["id"]): c for c in ctx.cfg.get("courses") or []}
            course = courses.get(button["cid"]) if button else None
            stopped = set(saved.get("stopped_courses", []))
            if (not button or not course or dt.datetime.now(dt.timezone.utc).timestamp() - button["at"] > 900
                    or (button["cid"] in stopped) != button["stopped"]
                    or bool(course.get("inactive")) != button["inactive"]):
                jsave(self.report_path, saved)
                return self._result("按钮已过期，请重新打开课程列表。")
            cid = button["cid"]
            if cid in stopped:
                stopped.remove(cid)
            else:
                stopped.add(cid)
            saved["stopped_courses"] = sorted(stopped)
            jsave(self.report_path, saved)
            generation = saved.get("service_generation", 0)
            enabled = saved.get("service_enabled", True)
        if not button["stopped"]:
            return self._result("已停止监控 " + course["code"] + "；旧任务保留但不再催交。")
        if not enabled:
            return self._result("已重新加入 " + course["code"] + "；开启服务后采集。")
        collected = self._collect(generation=generation, required_course=cid)
        if not self.permitted(generation):
            return {**self._result("本次采集已失效。"), "stale": True}
        return {**self._result("已重新加入 " + course["code"] + "；" + self._report(_CollectionContext(self.home, quiet=True), collected, []), collected), "generation": generation}

    @staticmethod
    def _button(saved, aid, phase, label, version):
        token = uuid.uuid4().hex
        saved.setdefault("task_buttons", {})[token] = {"aid": aid, "phase": phase,
                                                       "version": version,
                                                       "at": dt.datetime.now(dt.timezone.utc).timestamp()}
        return {"text": label, "data": "task:" + token}

    def task_list(self, identity, stopped=False, page=0):
        ctx = self._authorized(identity)
        with FileLock(os.path.join(self.home, "service-state.lock")):
            saved = self._saved()
            if not saved or not saved.get("snapshot"):
                return self._result("尚无任务快照；使用 /canvas refresh。")
            entries = [(aid, fact) for aid, fact in saved["snapshot"]["assignments"].items()
                       if self._task_valid(ctx, saved, aid) and
                       (aid in saved.get("reminders", {})) == stopped]
            entries.sort(key=lambda item: (item[1].get("due_at") or "", item[1].get("course") or "", item[0]))
            if page < 0 or page > max(0, (len(entries) - 1) // 8):
                return self._result("任务列表已过期，请重新打开。")
            selected = entries[page * 8:(page + 1) * 8]
            lines = ["已停止提醒" if stopped else "任务列表", f"数据截至：{saved.get('collected_at')}"]
            actions = []
            for index, (aid, fact) in enumerate(selected, page * 8 + 1):
                state = fact.get("sub_state")
                status = "Canvas 已提交/豁免" if state in ("submitted", "excused") or fact.get("excused") else ("需确认" if state == "unknown" else "待交")
                lines.append(f"{index}. {fact['course']} · {fact.get('name') or aid} — {fact.get('due_at') or '日期不明'}；{status}\n{fact.get('html_url') or '来源链接未提供'}")
                if stopped:
                    lines.append("停止原因：" + (saved["reminders"][aid].get("reason") or "未填写"))
                actions.append([self._button(saved, aid, "select", f"{index}. " + ("恢复" if stopped else "停止"), self._task_version(saved, aid))])
            if not selected:
                lines.append("无")
            navigation = []
            for target, label in ((page - 1, "上一页"), (page + 1, "下一页")):
                if 0 <= target <= (len(entries) - 1) // 8:
                    navigation.append(self._button(saved, "", "page:" + str(int(stopped)) + ":" + str(target), label, None))
            navigation.append(self._button(saved, "", "page:" + str(int(not stopped)) + ":0", "查看任务" if stopped else "查看已停止" , None))
            actions.append(navigation)
            saved["task_buttons"] = dict(list(saved.get("task_buttons", {}).items())[-300:])
            jsave(self.report_path, saved)
        return {**self._result("\n".join(lines), saved), "actions": actions}

    def task_action(self, identity, data):
        ctx = self._authorized(identity)
        if not isinstance(data, str) or not data.startswith("task:") or len(data) != 37:
            return self._result("按钮无效，请重新打开任务列表。")
        page_request = None
        with FileLock(os.path.join(self.home, "service-state.lock")):
            saved = self._saved()
            if not saved:
                return self._result("按钮已过期，请重新打开任务列表。")
            button = saved.get("task_buttons", {}).pop(data[5:], None)
            if not button:
                return self._result("按钮已过期，请重新打开任务列表。")
            if dt.datetime.now(dt.timezone.utc).timestamp() - button["at"] > 900:
                jsave(self.report_path, saved)
                return self._result("按钮已过期，请重新打开任务列表。")
            phase, aid = button["phase"], button["aid"]
            if phase.startswith("page:"):
                _, stopped, page = phase.split(":")
                page_request = bool(int(stopped)), int(page)
                jsave(self.report_path, saved)
            else:
                fact = self._task_valid(ctx, saved, aid)
                if not fact or button["version"] != self._task_version(saved, aid):
                    jsave(self.report_path, saved)
                    return self._result("任务状态已变化，请重新打开任务列表。")
                if phase == "cancel":
                    jsave(self.report_path, saved)
                    return self._result("已取消，提醒状态未变。")
                stopping = aid not in saved.get("reminders", {})
                if phase == "select":
                    actions = [[self._button(saved, aid, "confirm", "确认停止" if stopping else "确认恢复", button["version"]),
                                self._button(saved, aid, "cancel", "取消", button["version"])]]
                    if stopping:
                        actions.append([self._button(saved, aid, "reason:线下完成", "线下完成并停止", button["version"])])
                    jsave(self.report_path, saved)
                    return {**self._result(("确认停止提醒" if stopping else "确认恢复提醒") +
                                           f"：{fact['course']} · {fact.get('name') or aid}\n{fact.get('html_url') or '来源链接未提供'}"),
                            "actions": actions}
                if phase not in ("confirm", "reason:线下完成") or (phase.startswith("reason:") and not stopping):
                    jsave(self.report_path, saved)
                    return self._result("按钮已过期，请重新打开任务列表。")
                if stopping:
                    saved.setdefault("reminders", {})[aid] = {"reason": phase.partition(":")[2], "at": ctx.clock.now_utc().isoformat()}
                else:
                    del saved["reminders"][aid]
                jsave(self.report_path, saved)
                return self._result("已停止提醒。" if stopping else "已恢复提醒。", saved)
        return self.task_list(identity, *page_request)


    def execute(self, identity, command, params, channel, correlation_id, expected_generation=None):
        # Recheck administrator configuration before every operation, including reads.
        ctx = _CollectionContext(self.home, quiet=True)
        secrets = ServiceSecrets(ctx.cfg, self.secrets_path)
        if (identity != self.user_id or channel != "telegram"
                or self._binding(secrets) != self.binding):
            raise ValueError("账号绑定已变更，请管理员重启服务")
        if command not in COMMANDS or (params and not (command == "tasks" and params == ["stopped"] or command == "schedule" and len(params) == 2 or command == "ai")):
            return self._result("命令或参数不支持；使用 /canvas help 查看可用操作。")
        saved = self._saved()
        ai_revision = (saved or {}).get("ai_revision", 0)
        if command == "help":
            return {**self._result("\n".join(f"/canvas {name}{' [问题]' if name == 'ai' else ''} — {description}" for name, description in COMMANDS.items()) + "\n设置入口：/canvas settings；/canvas schedule HH:MM Area/City"),
                    "actions": [[{"text": "每日扫描设置", "data": "schedule:help"}, {"text": "AI 设置", "data": "ai:settings"}]]}
        if command == "status":
            ai, day, used, available = self._ai_state(ctx, saved)
            text = (("服务：每日扫描；" if (saved or {}).get("service_enabled", True) else "服务：已关闭；")
                    + "渠道：Telegram 私聊；AI：" + ("开启" if ai.get("enabled") else "关闭")
                    + f"；自动摘要：{'开' if ai.get('summary') else '关'}；公告授权：{'开' if ai.get('announcements') else '关'}；"
                    + f"额度日 {day}（{(saved or {}).get('ai_quota_zone', settings(ctx.cfg)[1])}）剩余 {max(0, self.secrets.ai_daily_limit - used)}/{self.secrets.ai_daily_limit}"
                    + ("；接口可用。\n" if available and used < self.secrets.ai_daily_limit else "；接口未配置或额度不足。\n"))
            text += (f"数据截至：{saved.get('collected_at') or '尚无成功数据'}；完整性："
                     + ("完整。" if self._result("", saved)["complete"] else "不完整。")
                     if saved else "尚无成功快照；使用 /canvas refresh。")
            return {**self._result(text + "\n实际下次扫描：" + self.next_scan(ctx) + "\n设置：/canvas settings；/canvas schedule HH:MM Area/City", saved),
                    "actions": [[{"text": "每日扫描设置", "data": "schedule:help"}, {"text": "AI 设置", "data": "ai:settings"}]]}
        if command == "report":
            return self._report_result(ctx, saved)
        if command == "schedule":
            return self.set_schedule(ctx, params)
        if command == "settings":
            return self.ai_settings(identity)
        if command == "courses":
            return self.course_list(identity)
        if command == "tasks":
            return self.task_list(identity, stopped=bool(params))
        if command == "off":
            return self.switch(ctx, False)
        if command == "on":
            return self.switch(ctx, True, expected_generation)
        generation = self.generation() if expected_generation is None else expected_generation
        if not self.permitted(generation):
            return self._result("服务已关闭；/canvas refresh 和 AI 不可用，请使用 /canvas on 开启。", saved)
        if command == "ai":
            latest = self._saved()
            ai, _, used, available = self._ai_state(ctx, latest)
            reason = self._ai_unavailable(ai, used, available, self.secrets.ai_daily_limit)
            if reason:
                result = self._report_result(ctx, latest)
                result["text"] += "\nAI 解读未执行：" + reason
                result["generation"] = generation
                return result
        collected = self._collect(generation=generation)
        if not self.permitted(generation):
            return {**self._result("本次采集已失效；服务状态已变化。"), "stale": True}
        result = self._report_result(_CollectionContext(self.home, quiet=True), collected)
        if command == "ai" and (self._saved() or {}).get("ai_revision", 0) != ai_revision:
            return {**self._result("本次 AI 解读已失效。"), "stale": True}
        if command != "ai":
            return result
        if not result["complete"] or collected.get("last_attempt", {}).get("ai_failure"):
            result["text"] += ("\nAI 解读未执行：公告采集失败。" if collected.get("last_attempt", {}).get("ai_failure")
                               else "\nAI 解读未执行：本次采集不完整。")
            result["generation"] = generation
            return result
        reason = self._reserve_ai(ctx, generation)
        if reason:
            result["text"] += "\nAI 解读未执行：" + reason
        else:
            try:
                snapshot, allowed_announcements = self._ai_input(ctx, collected, collected["snapshot"])
                announcements = allowed_announcements if collected.get("ai", {}).get("announcements") else None
                model_text = analyze(self.secrets.ai_origin, self.secrets.ai_api_key,
                                     self.secrets.ai_model, snapshot,
                                     " ".join(params) if params else None, announcements,
                                     permit=lambda: self._require_ai(generation, ai_revision))
                result["text"] += "\n\nAI 解读（非 Canvas 事实）：\n" + model_text
                if announcements:
                    result["text"] += "\n公告涉及的日期仅供参考，待确认；来源：" + "、".join(
                        a["source"] for a in announcements)
                result["ai_revision"] = ai_revision
            except Exception:
                result["text"] += "\nAI 解读失败或超时；规则日报不受影响。"
        result["generation"] = generation
        if not self.permitted(generation) or (self._saved() or {}).get("ai_revision", 0) != ai_revision:
            return {**self._result("本次 AI 解读已失效。"), "stale": True}
        return result

    def _collect(self, plan_day=None, generation=None, required_course=None):
        """Purpose-neutral collection; only validated commands may join it.

        The persisted attempt id distinguishes joining an in-flight request from
        a later request. Waiting spans processes too; no application cooldown.
        """
        if generation is None:
            generation = self.generation()
        self._require(generation)
        before = self._saved() or {}
        attempt_id = before.get("last_attempt", {}).get("id")
        lock = FileLock(os.path.join(self.home, "service-refresh.lock"))
        joined = not lock.acquire(blocking=False)
        if joined:
            lock.acquire()
        try:
            ctx = _CollectionContext(self.home, quiet=True)
            secrets = ServiceSecrets(ctx.cfg, self.secrets_path)
            if self._binding(secrets) != self.binding:
                raise ValueError("账号绑定已变更，请管理员重启服务")
            saved = self._saved()
            self._require(generation)
            if plan_day and saved and saved.get("daily_plans", {}).get(plan_day, {}).get("state") != "collecting":
                return saved
            if joined and saved and saved.get("last_attempt", {}).get("id") != attempt_id:
                if required_course is None or str(required_course) in saved["last_attempt"].get("collected_courses", []):
                    return saved
            try:
                return self._refresh(ctx, secrets, saved, generation, scheduled=plan_day is not None)
            except _StaleOperation:
                return self._saved()
            except Exception:
                self._require(generation)
                return self._failed(ctx, self._saved(), [{"course": None, "kind": "collection"}], generation)
        finally:
            lock.release()

    def _failed(self, ctx, previous, failures, generation):
        return self._commit(ctx, previous, None, [], failures, generation)

    def _commit(self, ctx, previous, snap, course_changes, failures, generation, collected_courses=(), scheduled=False, ai_failure=False):
        # Collection and delivery have separate locks: a late send acknowledgement
        # must not be overwritten by a refresh that was already doing network IO.
        with FileLock(ctx.P("service-state.lock")):
            self._require(generation)
            latest = self._saved()
            if latest and previous and (not latest.get("canvas_user_id") or latest.get("canvas_user_id") == previous.get("canvas_user_id")):
                saved = dict(latest)
                saved["canvas_user_id"] = previous.get("canvas_user_id")
            else:
                saved = dict(previous or {"binding": self.binding})
            saved.pop("text", None)
            saved.update(format=2)
            saved.setdefault("epoch", uuid.uuid4().hex)
            saved.setdefault("events", [])
            saved.setdefault("next_event", 1)
            old = saved.get("snapshot", {})
            self._record_changes(ctx, saved, snap, course_changes, scheduled)
            if snap is not None:
                if old.get("complete"):
                    saved["previous_success"] = old
                saved.update(snapshot=snap, collected_at=snap["collected_at"])
            saved["last_attempt"] = {"id": uuid.uuid4().hex, "at": ctx.clock.now_utc().isoformat(),
                                     "complete": snap is not None and not failures,
                                     "promoted": snap is not None, "failures": failures,
                                     "ai_complete": snap is not None and not failures and not ai_failure,
                                     "ai_failure": ai_failure,
                                     "collected_courses": [str(c["id"]) for c in ctx.cfg.get("courses") or []
                                                           if str(c["id"]) in collected_courses
                                                           and str(c["id"]) not in saved.get("stopped_courses", [])]}
            ai = saved.get("ai", {})
            for plan in saved.get("daily_plans", {}).values():
                if plan["state"] == "collecting":
                    events = [e for e in saved["events"] if "scheduled" in e["pending"]]
                    plan.update(state="formed", formed_at=ctx.clock.now_utc().isoformat(),
                                text=self._report(ctx, saved, events),
                                events=[e["id"] for e in events],
                                delivery={"telegram": {"state": "pending", "attempts": 0,
                                                       "next_at": plan["due"]}})
                    plan["ai_state"] = ("pending" if saved["last_attempt"]["ai_complete"]
                                        and ai.get("enabled") and ai.get("summary") else "done")
                    if plan["ai_state"] == "pending":
                        plan["ai_snapshot"] = snap
                    elif ai_failure and ai.get("enabled") and ai.get("summary"):
                        plan["ai_text"] = "AI 自动摘要未执行：公告采集失败；规则日报仍可查看。"
            jsave(self.report_path, saved)
        return saved

    @staticmethod
    def _record_changes(ctx, saved, snap, course_changes, scheduled=False):
        old = saved.get("snapshot", {})
        old_facts = old.get("assignments", {})
        now = ctx.clock.now_utc()

        def append(kind, text, course=None, course_id=None):
            saved["events"].append({"id": saved["next_event"], "kind": kind, "text": text,
                                    "course": course, "course_id": course_id,
                                    "at": now.isoformat(), "pending": ["manual", "scheduled"]})
            saved["next_event"] += 1

        for change in course_changes:
            code = change.split("：", 1)[1].split(" ", 1)[0] if change.startswith("新课程：") else change.split(" ", 1)[0]
            append("course", f"{change}{'；请确认课程状态' if '在 Canvas 上看不到了' in change else ''}\n{ctx.cfg['canvas_host'].rstrip('/')}/courses", code)
            if change.startswith("新课程："):
                saved["events"][-1]["pending"] = ["scheduled" if scheduled else "manual"]
        active = {code for _, code in AccountService._monitored(ctx, saved)}
        active_ids = {str(cid) for cid, _ in AccountService._monitored(ctx, saved)}
        if snap is None:
            return
        for aid, fact in snap["assignments"].items():
            if (fact.get("course_id") and fact["course_id"] not in active_ids or
                    not fact.get("course_id") and fact["course"] not in active or
                    snap.get("course_freshness_by_id", {}).get(fact.get("course_id"),
                        snap["course_freshness"].get(fact["course"])) != snap["collected_at"]):
                continue
            before = old_facts.get(aid)
            label = f"{fact['course']} · {fact['name']}\n{fact.get('html_url') or '来源链接未提供'}"
            def append_fact(kind, text):
                append(kind, text, fact["course"], fact.get("course_id"))
            baseline = (fact["course_id"] not in old.get("course_freshness_by_id", {}) if fact.get("course_id") and old.get("course_freshness_by_id")
                        else fact["course"] not in old.get("course_freshness", {}))
            if before is None and not baseline:
                append_fact("added", "新增：" + label)
            elif before is not None and fact.get("due_at") != before.get("due_at"):
                append_fact("due", f"改期：{label}\n{before.get('due_at') or '日期不明'} → {fact.get('due_at') or '日期不明'}")
                if aid in saved.get("reminders", {}):
                    saved["reminders"].pop(aid)
                    append_fact("reminder", ("改期后停止状态解除（已提交，无需催交）：" if fact.get("sub_state") in ("submitted", "excused") or fact.get("excused") else "改期后恢复提醒：") + label)
            if fact.get("sub_state") == "resubmit" and before and before.get("sub_state") != "resubmit":
                append_fact("redo", "Canvas 要求重新提交：" + label)
                if aid in saved.get("reminders", {}):
                    saved["reminders"].pop(aid)
                    append_fact("reminder", "明确要求重新提交后恢复提醒：" + label)
            if fact.get("sub_state") != "submitted":
                continue
            submitted = parse_ts(fact.get("submitted_at"))
            if baseline:
                changed = submitted is not None and now - dt.timedelta(days=7) <= submitted <= now
            else:
                changed = before is None or any(fact.get(key) != before.get(key)
                                               for key in ("sub_state", "submitted_at", "attempt"))
            if changed:
                append_fact("submitted", label)

    def _refresh(self, ctx, secrets, previous, generation, scheduled=False):
        api = SecureCanvas(secrets.canvas_origin, secrets.canvas_token, permit=lambda: self._require(generation))
        who = api.get("/api/v1/users/self")
        if not isinstance(who, dict) or not who.get("id"):
            raise ValueError("Canvas 身份验证失败")
        if previous and previous.get("canvas_user_id") != who["id"]:
            previous = None
        previous = dict(previous or {"binding": self.binding})
        previous["canvas_user_id"] = who["id"]
        errors = []
        changes = refresh_courses(ctx, api, errors, allow_empty=True)
        self._require(generation)
        if errors:
            return self._failed(ctx, previous, [{"course": None, "kind": "course_list"}], generation)
        courses = self._monitored(ctx, self._saved()) + [
            (c["id"], c["code"]) for c in ctx.cfg.get("courses") or []
            if c.get("access_lost") and str(c["id"]) not in (self._saved() or {}).get("stopped_courses", [])]
        assignments, failures = {}, []
        for cid, code in courses:
            if str(cid) in (self._saved() or {}).get("stopped_courses", []):
                continue
            try:
                values = api.get(f"/api/v1/courses/{cid}/assignments?per_page=100&include[]=submission")
                if not isinstance(values, list) or any(not isinstance(a, dict) or not a.get("id") for a in values):
                    raise ValueError("作业采集失败")
                assignments[str(cid)] = values
                course = next(c for c in ctx.raw_cfg["courses"] if str(c["id"]) == str(cid))
                if course.pop("access_lost", None):
                    course.pop("inactive", None)
                    changes.append(f"{code} 又能看到了")
            except _StaleOperation:
                raise
            except ValueError as exc:
                if str(exc) in ("Canvas API request failed with HTTP 403", "Canvas API request failed with HTTP 404"):
                    try:
                        api.get(f"/api/v1/courses/{cid}")
                    except ValueError as confirmation:
                        if str(confirmation) in ("Canvas API request failed with HTTP 403", "Canvas API request failed with HTTP 404"):
                            course = next(c for c in ctx.raw_cfg["courses"] if str(c["id"]) == str(cid))
                            if not course.get("inactive"):
                                course["inactive"] = True
                                changes.append(f"{code} 已失去访问，旧任务已暂停提醒；请确认课程状态")
                            course["access_lost"] = True
                            continue
                    except _StaleOperation:
                        raise
                failures.append({"course": code, "kind": "assignments"})
            except Exception:
                failures.append({"course": code, "kind": "assignments"})
        announcements = []
        announcement_error = False
        if not failures and (self._saved() or {}).get("ai", {}).get("enabled") and (self._saved() or {}).get("ai", {}).get("announcements") and assignments:
            try:
                query = urlencode([("context_codes[]", "course_" + str(cid)) for cid in assignments])
                values = api.get("/api/v1/announcements?" + query + "&per_page=50")
                if not isinstance(values, list):
                    raise ValueError("公告采集失败")
                codes = {"course_" + str(cid): code for cid, code in courses if str(cid) in assignments}
                for value in values[:50]:
                    if not isinstance(value, dict):
                        raise ValueError("公告采集失败")
                    code = codes.get(value.get("context_code"))
                    if code and isinstance(value.get("html_url"), str):
                        announcements.append({"course": code, "text": strip_html(value.get("message"), 1500),
                                              "source": value["html_url"], "title": value.get("title")})
            except _StaleOperation:
                raise
            except Exception:
                announcement_error = True
        ctx.cfg = effective(ctx.raw_cfg)
        if failures and not assignments:
            self._require(generation)
            result = self._commit(ctx, previous, None, changes, failures, generation, scheduled=scheduled)
            self._require(generation)
            jsave(ctx.P("config.json"), ctx.raw_cfg)
            return result
        snap = snapshot_from([], {}, {}, {}, {}, {})
        for cid, code in courses:
            bundle = snapshot_from([(cid, code)], {code: assignments.get(str(cid), [])}, {}, {}, {}, {})
            for assignment in assignments.get(str(cid), []):
                sub = assignment.get("submission") or {}
                fact = bundle["assignments"][str(assignment["id"])]
                fact["course_id"] = str(cid)
                fact["sub_state"] = _submission_state(sub, fact["submission_types"])
                fact["excused"] = sub.get("excused") is True
                for key in ("score", "grade", "graded_at", "posted_at", "attachments", "desc_hash"):
                    fact.pop(key, None)
                snap["assignments"][str(assignment["id"])] = fact
        now = ctx.clock.now_utc().isoformat()
        old = (previous or {}).get("snapshot", {})
        freshness = dict(old.get("course_freshness", {}))
        by_id = dict(old.get("course_freshness_by_id", {}))
        for fact in old.get("assignments", {}).values():
            freshness.setdefault(fact["course"], old.get("collected_at"))
        for aid, fact in old.get("assignments", {}).items():
            if (fact.get("course_id") or next((str(cid) for cid, code in courses if code == fact["course"]), None)) not in assignments:
                snap["assignments"][aid] = fact
        for cid, code in courses:
            if str(cid) in assignments:
                freshness[code] = now
                by_id[str(cid)] = now
        if announcements:
            snap["ai_announcements"] = announcements
        snap.update(collected_at=now, complete=not failures, course_freshness=freshness,
                    course_freshness_by_id=by_id, baseline=not bool(old))
        self._require(generation)
        result = self._commit(ctx, previous, snap, changes, failures, generation, assignments, scheduled,
                              ai_failure=announcement_error)
        self._require(generation)
        jsave(ctx.P("config.json"), ctx.raw_cfg)
        return result

    @staticmethod
    def _report(ctx, saved, events):
        snap = saved.get("snapshot", {})
        attempt = saved.get("last_attempt", {})
        failures = attempt.get("failures", [])
        complete = attempt.get("complete", snap.get("complete", False))
        now = ctx.clock.now_utc()
        end = ctx.clock.course_date(now) + dt.timedelta(days=7)
        active_courses = {code for _, code in AccountService._monitored(ctx, saved)}
        active_ids = {str(cid) for cid, _ in AccountService._monitored(ctx, saved)}
        stopped_ids = set(saved.get("stopped_courses", []))
        stopped_codes = {c["code"] for c in ctx.cfg.get("courses") or []
                         if str(c["id"]) in stopped_ids}
        sections = {"未来七天待交": [], "逾期未交": [], "日期不明任务": [],
                    "新增与改期": [], "最近已提交（尚未通知）": [], "新课程与课程变化": [],
                    "暂停提醒的旧数据": []}
        for aid, fact in sorted(snap.get("assignments", {}).items(), key=lambda item: (item[1].get("due_at") or "", item[1]["course"], item[1].get("name") or "")):
            state = fact.get("sub_state")
            if aid in saved.get("reminders", {}) or (fact.get("course_id") in stopped_ids if fact.get("course_id") else fact["course"] in stopped_codes):
                continue
            if state in ("submitted", "excused") or fact.get("excused"):
                continue
            due = parse_ts(fact.get("due_at"))
            if (fact.get("course_id") not in active_ids if fact.get("course_id") else fact["course"] not in active_courses):
                kind = "暂停提醒的旧数据"
            elif not due:
                kind = "日期不明任务"
            elif due <= now:
                kind = "逾期未交"
            elif ctx.clock.course_date(due) <= end:
                kind = "未来七天待交"
            else:
                continue
            status = "；提交状态需确认" if state == "unknown" else ""
            if state == "resubmit":
                status = "；Canvas 要求重新提交"
            if any(f["course"] in (None, fact["course"]) for f in failures) or kind == "暂停提醒的旧数据":
                stamp = (snap.get("course_freshness_by_id", {}).get(fact.get("course_id")) or
                         snap.get("course_freshness", {}).get(fact["course"]) or snap.get("collected_at"))
                status += f"；旧数据，截至 {stamp}，需确认"
            when = ctx.clock.fmt(due) if due else "Canvas 没写日期"
            sections[kind].append(f"{fact['course']} · {fact['name']} — {when}{status}\n{fact.get('html_url') or '来源链接未提供'}")
        for event in events:
            if event.get("kind") != "course" and (event.get("course_id") not in active_ids if event.get("course_id") else event.get("course") and event["course"] not in active_courses):
                continue
            section = ("最近已提交（尚未通知）" if event["kind"] == "submitted" else
                       "新课程与课程变化" if event["kind"] == "course" else "新增与改期")
            sections[section].append(event["text"] + f"\n发现于：{event['at']}")
        lines = ["Canvas 规则日报", f"数据截至：{saved.get('collected_at') or '尚无成功数据'}；完整性：{'完整' if complete else '不完整'}（监控课程作业）。"]
        if snap.get("baseline"):
            lines.append("首次成功快照作为基线，已有作业不计新增。")
        if attempt and not attempt.get("promoted", True):
            lines.append("刷新全部失败；本次未更新任务数据，上一成功快照保留。")
        if failures or attempt.get("ai_failure"):
            lines.append(f"本次尝试时间：{attempt['at']}")
        for failure in failures:
            course = failure["course"]
            label = course or "账号验证或课程清单"
            stamp = snap.get("course_freshness", {}).get(course) or "尚无成功数据"
            lines.append(f"采集失败：{label}；旧数据截至：{stamp}；无法确认当前提交状态。")
        if attempt.get("ai_failure"):
            lines.append("公告采集失败：公告正文不可用；监控课程作业和提交状态仍按本次数据时间显示。")
        for code, stamp in sorted(snap.get("course_freshness", {}).items()):
            lines.append(f"课程数据：{code}，截至 {stamp}")
        if not snap:
            lines.append("尚无成功快照；使用 /canvas refresh。")
        if not any(sections[kind] for kind in ("未来七天待交", "逾期未交", "日期不明任务")) and complete:
            lines.append("当前报告范围内没有待交任务。")
        for title, values in sections.items():
            lines.append("\n" + title)
            lines.extend(values or ["无"])
        return "\n".join(lines)
