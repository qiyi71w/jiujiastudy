"""Authenticated account commands, per-course snapshots and rule reports."""
import datetime as dt
import os
import re
import secrets
import uuid

from cc_collect import refresh_courses, snapshot_from
from cc_config import Ctx, effective, minimal_state, version_of
from cc_courses import course_pairs
from cc_service_security import SecureCanvas, ServiceSecrets
from cc_schedule import ensure_plan, planned, settings
from cc_store import FileLock, jload, jsave
from cc_time import parse_ts


COMMANDS = {
    "help": "显示命令及用途，不访问 Canvas",
    "status": "查看服务及快照状态，不访问 Canvas",
    "report": "查看当前快照日报及数据时间，不访问 Canvas",
    "refresh": "立即重新采集并生成规则日报",
    "tasks": "查看任务、已停止项和确认按钮；/canvas tasks stopped 查看已停止项，不访问 Canvas",
    "schedule": "设置每日基准时间及 IANA 时区：/canvas schedule HH:MM Area/City",

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

    def scheduled_tick(self):
        """Claim a due plan; a persisted report is delivered without Canvas access."""
        ctx = self._authorized(self.user_id)
        now = ctx.clock.now_utc()
        with FileLock(os.path.join(self.home, "service-state.lock")):
            saved = self._plan(ctx, self._saved())
            _, eligible = ensure_plan(saved, ctx.cfg, now)
            for day in eligible:
                plan = saved["daily_plans"][day]
                if plan["state"] == "collecting" and (now - dt.datetime.fromisoformat(plan["claimed_at"])).total_seconds() > 300:
                    lock = FileLock(os.path.join(self.home, "service-refresh.lock"))
                    if lock.acquire(blocking=False):
                        lock.release()
                        plan["state"] = "pending"  # interrupted before a report was committed
                if plan["state"] == "pending" and plan["due"] <= now.isoformat():
                    plan["state"] = "collecting"
                    plan["claimed_at"] = now.isoformat()
                    jsave(self.report_path, saved)
                    break
            else:
                day = None
                jsave(self.report_path, saved)
        if day is not None:
            with FileLock(os.path.join(self.home, "service-state.lock")):
                pending = self._saved()["daily_plans"][day]["state"] == "collecting"
            if pending:
                self._collect(plan_day=day)
        with FileLock(os.path.join(self.home, "service-state.lock")):
            saved = self._saved() or {}
            _, eligible = ensure_plan(saved, ctx.cfg, now)
            for key in eligible:
                plan = saved["daily_plans"][key]
                delivery = plan.get("delivery", {}).get("telegram")
                if plan["state"] == "formed" and delivery and delivery["state"] == "pending" and delivery["attempts"] < 4 and delivery["next_at"] <= now.isoformat():
                    return {"day": key, "text": plan["text"]}
        return None

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


    def scheduled_delivery(self, day, success):
        with FileLock(os.path.join(self.home, "service-state.lock")):
            saved = self._saved()
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
        return (fact if fact and fact.get("course") in
                {code for _, code in course_pairs(ctx.cfg, include_inactive=False)} else None)

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


    def execute(self, identity, command, params, channel, correlation_id):
        # Recheck administrator configuration before every operation, including reads.
        ctx = _CollectionContext(self.home, quiet=True)
        secrets = ServiceSecrets(ctx.cfg, self.secrets_path)
        if (identity != self.user_id or channel != "telegram"
                or self._binding(secrets) != self.binding):
            raise ValueError("账号绑定已变更，请管理员重启服务")
        if command not in COMMANDS or (params and not (command == "tasks" and params == ["stopped"] or command == "schedule" and len(params) == 2)):
            return self._result("命令或参数不支持；使用 /canvas help 查看可用操作。")
        saved = self._saved()
        if command == "help":
            return {**self._result("\n".join(f"/canvas {name} — {description}" for name, description in COMMANDS.items()) + "\n设置入口：/canvas schedule HH:MM Area/City"),
                    "actions": [[{"text": "每日扫描设置", "data": "schedule:help"}]]}
        if command == "status":
            text = "服务：每日扫描；渠道：Telegram 私聊；AI：未启用。\n"
            text += (f"数据截至：{saved.get('collected_at') or '尚无成功数据'}；完整性："
                     + ("完整。" if self._result("", saved)["complete"] else "不完整。")
                     if saved else "尚无成功快照；使用 /canvas refresh。")
            return {**self._result(text + "\n实际下次扫描：" + self.next_scan(ctx) + "\n设置：/canvas schedule HH:MM Area/City", saved),
                    "actions": [[{"text": "每日扫描设置", "data": "schedule:help"}]]}
        if command == "report":
            return self._report_result(ctx, saved)
        if command == "schedule":
            return self.set_schedule(ctx, params)
        if command == "tasks":
            return self.task_list(identity, stopped=bool(params))
        saved = self._collect()
        return self._report_result(_CollectionContext(self.home, quiet=True), saved)

    def _collect(self, plan_day=None):
        """Purpose-neutral collection; only validated commands may join it.

        The persisted attempt id distinguishes joining an in-flight request from
        a later request. Waiting spans processes too; no application cooldown.
        """
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
            if plan_day and saved and saved.get("daily_plans", {}).get(plan_day, {}).get("state") != "collecting":
                return saved
            if joined and saved and saved.get("last_attempt", {}).get("id") != attempt_id:
                return saved
            try:
                return self._refresh(ctx, secrets, saved)
            except Exception:
                # Never persist raw errors, URLs, response content or credentials.
                return self._failed(ctx, self._saved(), [{"course": None, "kind": "collection"}])
        finally:
            lock.release()

    def _failed(self, ctx, previous, failures):
        return self._commit(ctx, previous, None, [], failures)

    def _commit(self, ctx, previous, snap, course_changes, failures):
        # Collection and delivery have separate locks: a late send acknowledgement
        # must not be overwritten by a refresh that was already doing network IO.
        with FileLock(ctx.P("service-state.lock")):
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
            self._record_changes(ctx, saved, snap, course_changes)
            if snap is not None:
                if old.get("complete"):
                    saved["previous_success"] = old
                saved.update(snapshot=snap, collected_at=snap["collected_at"])
            saved["last_attempt"] = {"id": uuid.uuid4().hex, "at": ctx.clock.now_utc().isoformat(),
                                     "complete": snap is not None and not failures,
                                     "promoted": snap is not None, "failures": failures}
            for plan in saved.get("daily_plans", {}).values():
                if plan["state"] == "collecting":
                    events = [e for e in saved["events"] if "scheduled" in e["pending"]]
                    plan.update(state="formed", formed_at=ctx.clock.now_utc().isoformat(),
                                text=self._report(ctx, saved, events),
                                events=[e["id"] for e in events],
                                delivery={"telegram": {"state": "pending", "attempts": 0,
                                                       "next_at": plan["due"]}})
            jsave(self.report_path, saved)
        return saved

    @staticmethod
    def _record_changes(ctx, saved, snap, course_changes):
        old = saved.get("snapshot", {})
        old_facts = old.get("assignments", {})
        now = ctx.clock.now_utc()

        def append(kind, text):
            saved["events"].append({"id": saved["next_event"], "kind": kind, "text": text,
                                    "at": now.isoformat(), "pending": ["manual", "scheduled"]})
            saved["next_event"] += 1

        for change in course_changes:
            append("course", f"{change}\n{ctx.cfg['canvas_host'].rstrip('/')}/courses")
        if snap is None:
            return
        for aid, fact in snap["assignments"].items():
            if snap["course_freshness"].get(fact["course"]) != snap["collected_at"]:
                continue
            before = old_facts.get(aid)
            label = f"{fact['course']} · {fact['name']}\n{fact.get('html_url') or '来源链接未提供'}"
            baseline = fact["course"] not in old.get("course_freshness", {})
            if before is None and not baseline:
                append("added", "新增：" + label)
            elif before is not None and fact.get("due_at") != before.get("due_at"):
                append("due", f"改期：{label}\n{before.get('due_at') or '日期不明'} → {fact.get('due_at') or '日期不明'}")
                if aid in saved.get("reminders", {}):
                    saved["reminders"].pop(aid)
                    append("reminder", ("改期后停止状态解除（已提交，无需催交）：" if fact.get("sub_state") in ("submitted", "excused") or fact.get("excused") else "改期后恢复提醒：") + label)
            if fact.get("sub_state") == "resubmit" and before and before.get("sub_state") != "resubmit":
                append("redo", "Canvas 要求重新提交：" + label)
                if aid in saved.get("reminders", {}):
                    saved["reminders"].pop(aid)
                    append("reminder", "明确要求重新提交后恢复提醒：" + label)
            if fact.get("sub_state") != "submitted":
                continue
            submitted = parse_ts(fact.get("submitted_at"))
            if baseline:
                changed = submitted is not None and now - dt.timedelta(days=7) <= submitted <= now
            else:
                changed = before is None or any(fact.get(key) != before.get(key)
                                               for key in ("sub_state", "submitted_at", "attempt"))
            if changed:
                append("submitted", label)

    def _refresh(self, ctx, secrets, previous):
        api = SecureCanvas(secrets.canvas_origin, secrets.canvas_token)
        who = api.get("/api/v1/users/self")
        if not isinstance(who, dict) or not who.get("id"):
            raise ValueError("Canvas 身份验证失败")
        if previous and previous.get("canvas_user_id") != who["id"]:
            previous = None
        previous = dict(previous or {"binding": self.binding})
        previous["canvas_user_id"] = who["id"]
        errors = []
        changes = refresh_courses(ctx, api, errors)
        if errors:
            return self._failed(ctx, previous, [{"course": None, "kind": "course_list"}])
        courses = course_pairs(ctx.cfg, include_inactive=False)
        assignments, failures = {}, []
        for cid, code in courses:
            try:
                values = api.get(f"/api/v1/courses/{cid}/assignments?per_page=100&include[]=submission")
                if not isinstance(values, list) or any(not isinstance(a, dict) or not a.get("id") for a in values):
                    raise ValueError("作业采集失败")
                assignments[code] = values
            except Exception:
                failures.append({"course": code, "kind": "assignments"})
        if courses and not assignments:
            result = self._commit(ctx, previous, None, changes, failures)
            jsave(ctx.P("config.json"), ctx.raw_cfg)
            return result
        snap = snapshot_from(courses, assignments, {}, {}, {}, {})
        for values in assignments.values():
            for assignment in values:
                sub = assignment.get("submission") or {}
                fact = snap["assignments"][str(assignment["id"])]
                fact["sub_state"] = _submission_state(sub, fact["submission_types"])
                fact["excused"] = sub.get("excused") is True
                for key in ("score", "grade", "graded_at", "posted_at", "attachments", "desc_hash"):
                    fact.pop(key, None)
        now = ctx.clock.now_utc().isoformat()
        old = (previous or {}).get("snapshot", {})
        freshness = dict(old.get("course_freshness", {}))
        for fact in old.get("assignments", {}).values():
            freshness.setdefault(fact["course"], old.get("collected_at"))
        for aid, fact in old.get("assignments", {}).items():
            if fact["course"] not in assignments:
                snap["assignments"][aid] = fact
        freshness.update({code: now for code in assignments})
        snap.update(collected_at=now, complete=not failures, course_freshness=freshness,
                    baseline=not bool(old))
        result = self._commit(ctx, previous, snap, changes, failures)
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
        active_courses = {code for _, code in course_pairs(ctx.cfg, include_inactive=False)}
        sections = {"未来七天待交": [], "逾期未交": [], "日期不明任务": [],
                    "新增与改期": [], "最近已提交（尚未通知）": [], "新课程与课程变化": [],
                    "暂停提醒的旧数据": []}
        for aid, fact in sorted(snap.get("assignments", {}).items(), key=lambda item: (item[1].get("due_at") or "", item[1]["course"], item[1].get("name") or "")):
            state = fact.get("sub_state")
            if aid in saved.get("reminders", {}):
                continue
            if state in ("submitted", "excused") or fact.get("excused"):
                continue
            due = parse_ts(fact.get("due_at"))
            if fact["course"] not in active_courses:
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
                stamp = snap.get("course_freshness", {}).get(fact["course"]) or snap.get("collected_at")
                status += f"；旧数据，截至 {stamp}，需确认"
            when = ctx.clock.fmt(due) if due else "Canvas 没写日期"
            sections[kind].append(f"{fact['course']} · {fact['name']} — {when}{status}\n{fact.get('html_url') or '来源链接未提供'}")
        for event in events:
            section = ("最近已提交（尚未通知）" if event["kind"] == "submitted" else
                       "新课程与课程变化" if event["kind"] == "course" else "新增与改期")
            sections[section].append(event["text"] + f"\n发现于：{event['at']}")
        lines = ["Canvas 规则日报", f"数据截至：{saved.get('collected_at') or '尚无成功数据'}；完整性：{'完整' if complete else '不完整'}（监控课程作业）。"]
        if snap.get("baseline"):
            lines.append("首次成功快照作为基线，已有作业不计新增。")
        if attempt and not attempt.get("promoted", True):
            lines.append("刷新全部失败；本次未更新任务数据，上一成功快照保留。")
        if failures:
            lines.append(f"本次尝试时间：{attempt['at']}")
        for failure in failures:
            course = failure["course"]
            label = course or "账号验证或课程清单"
            stamp = snap.get("course_freshness", {}).get(course) or "尚无成功数据"
            lines.append(f"采集失败：{label}；旧数据截至：{stamp}；无法确认当前提交状态。")
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
