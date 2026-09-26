"""Tests for cc_study.schedule_days structured scheduling and boundary rules.
"""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import cc_study
import render_week


class DummyClock:
    def course_date(self, t):
        if hasattr(t, "date"):
            return t.date()
        return t


class DummyCtx:
    def __init__(self, cfg=None):
        self.cfg = cfg or {}
        self.clock = DummyClock()


class TestStudySchedule(unittest.TestCase):
    def setUp(self):
        self.today = dt.date(2026, 9, 21)  # Monday
        self.monday = dt.date(2026, 9, 21)
        self.ctx = DummyCtx()

    def test_exam_reviews_three_days_not_suppressed_by_due_work(self):
        exam_item = {
            "id": "MATH-1",
            "course": "MATH",
            "title": "Final Exam",
            "kind": "Deadline",
            "due_at": "2026-09-25T14:00:00Z",  # Friday
            "exam": True,
            "weight": "20%",
            "when": "09-25 周五 14:00",
        }
        courses_out = [{
            "code": "MATH",
            "deadline_related": [exam_item],
            "before_class": [],
            "todo": [],
        }]

        res = cc_study.schedule_days(self.ctx, self.today, self.monday, courses_out, [], structured=True)
        days = res["days"]

        reviews = [(d["date"], e) for d in days for e in d["entries"] if e.get("activity") == "review"]
        work_items = [(d["date"], e) for d in days for e in d["entries"] if e.get("activity") == "work"]

        # Exam is Friday 09-25; 3 days before are Thu 09-24, Wed 09-23, Tue 09-22
        self.assertEqual(len(reviews), 3)
        review_dates = {date for date, _ in reviews}
        self.assertEqual(review_dates, {"2026-09-22", "2026-09-23", "2026-09-24"})

        for date, rev in reviews:
            self.assertEqual(rev["origin_id"], "MATH-1")
            self.assertEqual(rev["id"], f"MATH-1:review:{date}")
            self.assertIn(rev["slot"], ("must", "should"))

        # Due work item is placed on Wednesday (heavy lead=2 -> 09-23)
        self.assertEqual(len(work_items), 1)
        self.assertEqual(work_items[0][1]["id"], "MATH-1")
        self.assertEqual(work_items[0][1]["slot"], "must")
        self.assertEqual(work_items[0][0], "2026-09-23")

        # On Wednesday, work item takes must and review takes should
        wed = next(d for d in days if d["date"] == "2026-09-23")
        self.assertEqual(len(wed["entries"]), 2)
        self.assertEqual(wed["entries"][0]["slot"], "must")
        self.assertEqual(wed["entries"][0]["activity"], "work")
        self.assertEqual(wed["entries"][1]["slot"], "should")
        self.assertEqual(wed["entries"][1]["activity"], "review")

    def test_same_day_overload_retained_in_parking(self):
        # 4 items all targeting Wednesday 09-23
        items = [
            {
                "id": f"TASK-{i}",
                "course": "CS",
                "title": f"Task {i}",
                "kind": "Deadline",
                "due_at": "2026-09-24T14:00:00Z",  # Thursday, light lead 1 -> Wed 09-23
                "weight": "5%",
                "when": "09-24 周四 14:00",
            }
            for i in range(1, 5)
        ]
        courses_out = [{
            "code": "CS",
            "deadline_related": items,
            "before_class": [],
            "todo": [],
        }]

        res = cc_study.schedule_days(self.ctx, self.today, self.monday, courses_out, [], structured=True)
        wed = next(d for d in res["days"] if d["date"] == "2026-09-23")

        # Wednesday has 1 must + 2 should = 3 entries
        self.assertEqual(len(wed["entries"]), 3)
        self.assertEqual(len(wed["should"]), 2)

        # 4th item was overloaded on Wednesday and retained in parking
        self.assertEqual(len(res["parking"]), 1)
        parked = res["parking"][0]
        self.assertEqual(parked["id"], "TASK-4")
        self.assertEqual(parked["origin_id"], "TASK-4")
        self.assertEqual(parked["date"], "2026-09-23")
        self.assertEqual(parked["activity"], "work")
        self.assertIn("Task 4", parked["text"])

    def test_max_should_bounds_and_zero(self):
        items = [
            {
                "id": f"TASK-{i}",
                "course": "CS",
                "title": f"Task {i}",
                "kind": "Deadline",
                "due_at": "2026-09-24T14:00:00Z",
                "weight": "5%",
                "when": "09-24 周四 14:00",
            }
            for i in range(1, 5)
        ]
        courses_out = [{"code": "CS", "deadline_related": items, "before_class": [], "todo": []}]

        res_0 = cc_study.schedule_days(DummyCtx({"study": {"max_should": 0}}), self.today, self.monday, courses_out, [], structured=True)
        wed_0 = next(d for d in res_0["days"] if d["date"] == "2026-09-23")
        self.assertEqual(len(wed_0["should"]), 0)

        # Capped at 2 when given > 2
        res_5 = cc_study.schedule_days(DummyCtx({"study": {"max_should": 5}}), self.today, self.monday, courses_out, [], structured=True)
        wed_5 = next(d for d in res_5["days"] if d["date"] == "2026-09-23")
        self.assertEqual(len(wed_5["should"]), 2)

        # Explicit 1 works
        res_1 = cc_study.schedule_days(DummyCtx({"study": {"max_should": 1}}), self.today, self.monday, courses_out, [], structured=True)
        wed_1 = next(d for d in res_1["days"] if d["date"] == "2026-09-23")
        self.assertEqual(len(wed_1["should"]), 1)

    def test_legacy_call_and_render_compatibility(self):
        items = [
            {"id": "T1", "course": "CS", "title": "HW1", "kind": "Deadline", "due_at": "2026-09-24T14:00:00Z", "when": "09-24 周四 14:00"},
            {"id": "T2", "course": "CS", "title": "HW2", "kind": "Deadline", "due_at": "2026-09-24T14:00:00Z", "when": "09-24 周四 14:00"},
            {"id": "T3", "course": "CS", "title": "HW3", "kind": "Deadline", "due_at": "2026-09-24T14:00:00Z", "when": "09-24 周四 14:00"},
            {"id": "T4", "course": "CS", "title": "HW4", "kind": "Deadline", "due_at": "2026-09-24T14:00:00Z", "when": "09-24 周四 14:00"},
        ]
        courses_out = [{"code": "CS", "deadline_related": items, "before_class": [], "todo": []}]

        days_list = cc_study.schedule_days(self.ctx, self.today, self.monday, courses_out, [])

        plan_dict = {
            "days": days_list,
            "parking": cc_study.schedule_days.parking,
            "generated": self.today.isoformat(),
            "title": "测试周计划",
            "range": "09-21 至 09-27",
            "study": {"courses": courses_out},
            "deadlines": [],
        }

        html = render_week.render(plan_dict)
        md = render_week.to_markdown(plan_dict)
        self.assertIn("HW4", html)
        self.assertIn("HW4", md)

    def test_structured_return_and_no_sourceless_entry_for_fallback(self):
        res = cc_study.schedule_days(self.ctx, self.today, self.monday, [], [], structured=True)
        for d in res["days"]:
            self.assertEqual(len(d["entries"]), 0)

    def test_overdue_scheduled_today(self):
        overdue_item = {
            "id": "OD-1",
            "course": "PHY",
            "title": "Lab Report",
            "kind": "Overdue",
            "overdue": True,
            "when": "09-18 周五 23:59",
        }
        courses_out = [{"code": "PHY", "deadline_related": [overdue_item], "before_class": [], "todo": []}]

        res = cc_study.schedule_days(self.ctx, self.today, self.monday, courses_out, [], structured=True)
        today_day = next(d for d in res["days"] if d["date"] == self.today.isoformat())
        self.assertEqual(len(today_day["entries"]), 1)
        self.assertEqual(today_day["entries"][0]["id"], "OD-1")
        self.assertEqual(today_day["entries"][0]["origin_id"], "OD-1")
        self.assertEqual(today_day["entries"][0]["slot"], "must")
        self.assertEqual(today_day["entries"][0]["activity"], "work")


if __name__ == "__main__":
    unittest.main()
