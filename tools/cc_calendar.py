"""Calendar projection over trusted Canvas facts, announcement actions and syllabus nodes.

Pure functions: no network or model calls. Candidates stay candidates until the
account user confirms them; Canvas due dates are never overwritten.
"""
import re
from zoneinfo import ZoneInfo

from cc_time import parse_ts

SYLLABUS_KEY = "calendar_syllabus"
KINDS = ("exam", "quiz", "assignment", "project", "other")
_CATEGORY = {"exam": "exam", "quiz": "exam", "assignment": "assignment", "project": "other", "other": "other"}


def normalize_title(text):
    return re.sub(r"[\W_]+", " ", str(text or "").lower()).strip()


def _local(value, zone):
    ts = parse_ts(value)
    if not ts:
        return None, None
    local = ts.astimezone(zone)
    return local.date().isoformat(), local.strftime("%H:%M")


def syllabus_store(saved):
    return saved.setdefault(SYLLABUS_KEY, {"sources": {}, "nodes": {}})


def node_version(node):
    return [node.get("revision", 0), node.get("status")]


def events(saved, active, timezone, actions, completed_tasks=None, reminders=None):
    """Return calendar events for monitored courses, sorted by date."""
    zone = ZoneInfo(timezone)
    completed_tasks = completed_tasks or {}
    reminders = reminders or {}
    out = []
    for aid, fact in saved.get("snapshot", {}).get("assignments", {}).items():
        if str(fact.get("course_id")) not in active or not fact.get("due_at"):
            continue
        date, time = _local(fact.get("due_at"), zone)
        done = fact.get("sub_state") in ("submitted", "excused") or bool(fact.get("excused")) or (
            aid in completed_tasks and not completed_tasks[aid].get("needs_review"))
        out.append({"id": "canvas:" + aid, "source": "canvas",
            "category": "exam" if fact.get("is_quiz") else "assignment",
            "course": fact.get("course"), "course_id": str(fact.get("course_id")),
            "title": fact.get("name") or aid, "date": date, "time": time,
            "status": "done" if done else "confirmed",
            "needs_review": bool(completed_tasks.get(aid, {}).get("needs_review")),
            "conflict": None, "url": fact.get("html_url") or "", "evidence": "",
            "sub_state": fact.get("sub_state", "unknown"), "version": None})
    for action in actions:
        if action.get("status") == "dismissed" or action.get("course_id") not in active:
            continue
        date, time = _local(action.get("due_at"), zone)
        status = {"pending": "candidate", "completed": "done"}.get(action["status"], "confirmed")
        out.append({"id": action["id"], "source": "announcement", "category": "announcement",
            "course": action.get("course"), "course_id": action.get("course_id"),
            "title": action.get("title"), "date": date, "time": time, "status": status,
            "needs_review": bool(action.get("needs_review")), "conflict": None,
            "url": action.get("source") or "", "evidence": action.get("evidence") or "",
            "uncertainty": action.get("uncertainty") or "", "version": action.get("action_version")})
    store = saved.get(SYLLABUS_KEY, {})
    announced = [a for a in actions if a.get("status") != "dismissed" and a.get("due_at")]
    for node in store.get("nodes", {}).values():
        if node.get("status") == "dismissed" or node.get("course_id") not in active:
            continue
        conflict = None
        if node.get("status") == "confirmed" and node.get("date"):
            conflict = _announcement_change(node, announced, zone)
        source = store.get("sources", {}).get(node.get("source_id"), {})
        out.append({"id": node["id"], "source": "syllabus", "category": _CATEGORY.get(node.get("kind"), "other"),
            "course": node.get("course"), "course_id": node.get("course_id"), "title": node.get("title"),
            "date": node.get("date"), "time": node.get("time"),
            "status": "candidate" if node.get("status") == "pending" else "confirmed",
            "needs_review": bool(conflict), "conflict": conflict,
            "url": source.get("url") or "", "source_name": source.get("name") or "",
            "evidence": node.get("evidence") or "", "date_text": node.get("date_text") or "",
            "user_edited": bool(node.get("user_edited")), "version": node_version(node)})
    out.sort(key=lambda e: (e["date"] or "9999-99-99", e["time"] or "99:99", e["course"] or "", e["id"]))
    return out


def _announcement_change(node, announced, zone):
    """A unique same-course announcement action with the same title but another date."""
    title = normalize_title(node.get("title"))
    if not title:
        return None
    matches = [a for a in announced if a.get("course_id") == node.get("course_id")
               and normalize_title(a.get("title")) == title]
    if len(matches) != 1:
        return None
    action = matches[0]
    date, time = _local(action["due_at"], zone)
    if date == node.get("date") or [action["id"], action["due_at"]] in node.get("kept", []):
        return None
    return {"other_date": date, "other_time": time, "action_id": action["id"], "due_at": action["due_at"],
            "reason": "公告提示日期变化", "evidence": action.get("evidence") or "", "url": action.get("source") or ""}
