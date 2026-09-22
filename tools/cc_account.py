"""Shared, authenticated account commands and complete-success rule reports."""
import datetime as dt
import os

from cc_collect import refresh_courses, snapshot_from
from cc_config import Ctx, effective, minimal_state, version_of
from cc_courses import course_pairs
from cc_deadlines import SUBMITTED, deadline_rows
from cc_service_security import SecureCanvas, ServiceSecrets
from cc_store import FileLock, jload, jsave
from cc_time import parse_ts


COMMANDS = {
    "help": "显示命令及用途，不访问 Canvas",
    "status": "查看服务及快照状态，不访问 Canvas",
    "report": "查看上次成功日报，不访问 Canvas",
    "refresh": "立即重新采集并生成规则日报",
}


class _CollectionContext(Ctx):
    """Course discovery stages configuration until collection succeeds."""

    def save_config(self):
        self.cfg = effective(self.raw_cfg)


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
            return saved
        return None

    @staticmethod
    def _result(text, saved=None):
        return {"text": text, "collected_at": (saved or {}).get("collected_at"),
                "complete": bool(saved), "actions": []}

    def execute(self, identity, command, params, channel, correlation_id):
        # Recheck administrator configuration before every operation, including reads.
        ctx = _CollectionContext(self.home, quiet=True)
        secrets = ServiceSecrets(ctx.cfg, self.secrets_path)
        if (identity != self.user_id or channel != "telegram"
                or self._binding(secrets) != self.binding):
            raise ValueError("账号绑定已变更，请管理员重启服务")
        if params or command not in COMMANDS:
            return self._result("命令或参数不支持；使用 /canvas help 查看可用操作。")
        saved = self._saved()
        if command == "help":
            return self._result("\n".join(f"/canvas {name} — {description}" for name, description in COMMANDS.items()))
        if command == "status":
            text = "服务：按需刷新；渠道：Telegram 私聊；AI：未启用；计划扫描：未配置。\n"
            text += (f"最近成功采集：{saved['collected_at']}；完整性：完整。" if saved else "尚无成功快照；使用 /canvas refresh。")
            return self._result(text, saved)
        if command == "report":
            return self._result(saved["text"] if saved else "尚无成功快照；使用 /canvas refresh。", saved)
        lock = FileLock(ctx.P("service-refresh.lock"))
        if not lock.acquire(blocking=False):
            return self._result("已有刷新正在进行；请稍后查看快照。", saved)
        try:
            return self._refresh(ctx, secrets, saved)
        except Exception:
            # No raw exception, response, URL, or course content enters diagnostics.
            return self._result("刷新失败，本次未生成完整日报；上次成功快照保留。", saved)
        finally:
            lock.release()

    def _refresh(self, ctx, secrets, previous):
        api = SecureCanvas(secrets.canvas_origin, secrets.canvas_token)
        who = api.get("/api/v1/users/self")
        if not isinstance(who, dict) or not who.get("id"):
            raise ValueError("Canvas 身份验证失败")
        errors = []
        changes = refresh_courses(ctx, api, errors)
        if errors:
            raise ValueError("课程发现失败")
        courses = course_pairs(ctx.cfg, include_inactive=False)
        assignments = {}
        for cid, code in courses:
            values = api.get(f"/api/v1/courses/{cid}/assignments?per_page=100&include[]=submission")
            if not isinstance(values, list):
                raise ValueError("作业采集失败")
            assignments[code] = values
        snap = snapshot_from(courses, assignments, {}, {}, {}, {})
        # Keep exemption and learner-specific due evidence from the actual response.
        for values in assignments.values():
            for assignment in values:
                sub = assignment.get("submission") or {}
                fact = snap["assignments"][str(assignment["id"])]
                fact["excused"] = bool(sub.get("excused"))
                if "cached_due_date" in sub:
                    fact["due_at"] = sub["cached_due_date"]
                for key in ("score", "grade", "graded_at", "posted_at", "attachments", "desc_hash"):
                    fact.pop(key, None)
        now = ctx.clock.now_utc()
        snap.update(collected_at=now.isoformat(), complete=True)
        if previous and previous.get("canvas_user_id") != who["id"]:
            previous = None
        text = self._report(ctx, snap, previous, changes)
        saved = {"binding": self.binding, "canvas_user_id": who["id"],
                 "collected_at": snap["collected_at"], "snapshot": snap, "text": text}
        # Configuration remains the single course-list source. No partial attempt
        # replaces the last successful report, including failures while saving.
        jsave(ctx.P("config.json"), ctx.raw_cfg)
        jsave(self.report_path, saved)
        return self._result(text, saved)

    @staticmethod
    def _report(ctx, snap, previous, course_changes):
        now = ctx.clock.now_utc()
        facts = snap["assignments"]
        active = {"assignments": {aid: a for aid, a in facts.items()
                                  if not a.get("excused") and a.get("sub_state") not in SUBMITTED}}
        # Local learning progress/notes never become Canvas submission evidence.
        ctx.state = {"manual_deadlines": [], "deadline_notes": {}}
        rows = deadline_rows(ctx, active, ctx.clock.today_user(), days=7)
        sections = {"未来七天待交": [], "逾期未交（最近21天）": [], "日期不明任务": [],
                    "新增与改期": [], "最近已提交（七天）": [],
                    "新课程与课程变化": [f"{change}\n{ctx.cfg['canvas_host'].rstrip('/')}/courses" for change in course_changes]}
        for row in rows:
            kind = "日期不明任务" if row["undated"] else ("逾期未交（最近21天）" if row["overdue"] else "未来七天待交")
            status = "；提交状态需确认" if (facts[str(row["id"])].get("sub_state") is None
                    or "on_paper" in row.get("submission_types", [])) else ""
            sections[kind].append(f"{row['course']} · {row['item']} — {row['when']}{status}\n{row.get('url') or '来源链接未提供'}")
        old = (previous or {}).get("snapshot", {}).get("assignments", {})
        for aid, fact in facts.items():
            label = f"{fact['course']} · {fact['name']}\n{fact.get('html_url') or '来源链接未提供'}"
            if previous and aid not in old:
                sections["新增与改期"].append("新增：" + label)
            elif aid in old and fact.get("due_at") != old[aid].get("due_at"):
                sections["新增与改期"].append(f"改期：{label}\n{old[aid].get('due_at')} → {fact.get('due_at')}")
            submitted = parse_ts(fact.get("submitted_at"))
            if fact.get("sub_state") in SUBMITTED and submitted and now - dt.timedelta(days=7) <= submitted <= now:
                sections["最近已提交（七天）"].append(label)
        lines = ["Canvas 规则日报", f"数据截至：{snap['collected_at']}；完整性：完整（监控课程作业）。"]
        if not previous:
            lines.append("首次成功快照作为基线，已有作业不计新增。")
        if not rows:
            lines.append("当前报告范围内没有待交任务。")
        for title, values in sections.items():
            lines.append("\n" + title)
            lines.extend(values or ["无"])
        return "\n".join(lines)
