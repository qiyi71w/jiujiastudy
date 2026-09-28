"""Calendar projection and light syllabus import guards."""
import json
import os
import sys
import unittest
from unittest.mock import patch

TOOLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import cc_calendar
import cc_syllabus
from test_ai_output import _mock_open_with_content


def saved_with(nodes=None):
    return {"snapshot": {"assignments": {
        "a1": {"course_id": 11, "course": "MATH", "name": "HW 1", "due_at": "2026-10-02T15:59:00Z",
               "sub_state": "unsubmitted", "html_url": "https://canvas.example/a1"},
        "q1": {"course_id": 11, "course": "MATH", "name": "Quiz 1", "due_at": "2026-10-03T15:59:00Z",
               "sub_state": "submitted", "is_quiz": True},
        "x1": {"course_id": 99, "course": "STOPPED", "name": "Hidden", "due_at": "2026-10-03T15:59:00Z"},
        "n1": {"course_id": 11, "course": "MATH", "name": "No due", "due_at": None}}},
        cc_calendar.SYLLABUS_KEY: {"sources": {"s": {"course_id": "11", "name": "syllabus.pdf"}},
                                   "nodes": nodes or {}}}


def action(status="pending", due="2026-10-20T01:00:00Z", title="Midterm Exam"):
    return {"id": "act1", "course_id": "11", "course": "MATH", "title": title, "due_at": due,
            "status": status, "needs_review": False, "evidence": "Midterm moved", "source": "",
            "action_version": [0, 1]}


def node(**extra):
    return {"id": "syllabus:s:1", "source_id": "s", "course_id": "11", "course": "MATH", "title": "Midterm exam",
            "kind": "exam", "date": "2026-10-15", "time": None, "evidence": "Midterm Oct 15",
            "status": "confirmed", "revision": 0, **extra}


class Projection(unittest.TestCase):
    def test_canvas_facts_are_local_dated_and_scoped_to_monitored_courses(self):
        events = cc_calendar.events(saved_with(), {"11"}, "Asia/Shanghai", [])
        by_id = {e["id"]: e for e in events}
        self.assertEqual({"canvas:a1", "canvas:q1"}, set(by_id))
        self.assertEqual(("2026-10-02", "23:59", "assignment", "confirmed"),
                         tuple(by_id["canvas:a1"][k] for k in ("date", "time", "category", "status")))
        self.assertEqual(("exam", "done"), (by_id["canvas:q1"]["category"], by_id["canvas:q1"]["status"]))

    def test_announcement_candidates_are_hollow_and_dismissed_hidden(self):
        events = cc_calendar.events(saved_with(), {"11"}, "UTC", [action()])
        ann = next(e for e in events if e["source"] == "announcement")
        self.assertEqual(("candidate", "announcement"), (ann["status"], ann["category"]))
        events = cc_calendar.events(saved_with(), {"11"}, "UTC", [action("dismissed")])
        self.assertFalse([e for e in events if e["source"] == "announcement"])

    def test_announced_date_change_flags_confirmed_syllabus_node_until_kept(self):
        saved = saved_with({"syllabus:s:1": node()})
        ev = next(e for e in cc_calendar.events(saved, {"11"}, "UTC", [action()]) if e["source"] == "syllabus")
        self.assertTrue(ev["needs_review"])
        self.assertEqual("2026-10-20", ev["conflict"]["other_date"])
        self.assertEqual("2026-10-15", ev["date"])
        saved = saved_with({"syllabus:s:1": node(kept=[["act1", "2026-10-20T01:00:00Z"]])})
        ev = next(e for e in cc_calendar.events(saved, {"11"}, "UTC", [action()]) if e["source"] == "syllabus")
        self.assertIsNone(ev["conflict"])

    def test_pending_node_and_ambiguous_matches_raise_no_conflict(self):
        saved = saved_with({"syllabus:s:1": node(status="pending")})
        ev = next(e for e in cc_calendar.events(saved, {"11"}, "UTC", [action()]) if e["source"] == "syllabus")
        self.assertEqual(("candidate", None), (ev["status"], ev["conflict"]))
        second = {**action(), "id": "act2"}
        saved = saved_with({"syllabus:s:1": node()})
        ev = next(e for e in cc_calendar.events(saved, {"11"}, "UTC", [action(), second]) if e["source"] == "syllabus")
        self.assertIsNone(ev["conflict"])


class SyllabusGuards(unittest.TestCase):
    TEXT = "Course schedule\nMidterm exam: October 15, 2026 in class\nFinal project due Week 14\nLecture 3: limits"

    def test_evidence_must_be_verbatim_and_week_numbers_stay_undated(self):
        items = cc_syllabus.validate_items({"items": [
            {"title": "Midterm", "kind": "exam", "date": "2026-10-15", "time": "25:00",
             "date_text": "October 15", "evidence": "Midterm exam: October 15, 2026"},
            {"title": "Final project", "kind": "project", "date": None, "time": None,
             "date_text": "Week 14", "evidence": "Final project due Week 14"},
            {"title": "Invented", "kind": "exam", "date": "2026-11-01", "time": None,
             "date_text": "", "evidence": "Quiz on November 1"}]}, self.TEXT)
        self.assertEqual(["Midterm", "Final project"], [i["title"] for i in items])
        self.assertEqual((("2026-10-15", None)), (items[0]["date"], items[0]["time"]))
        self.assertIsNone(items[1]["date"])
        with self.assertRaises(ValueError):
            cc_syllabus.validate_items({"items": [], "extra": 1}, self.TEXT)

    def test_empty_or_unsupported_text_never_reaches_model(self):
        with self.assertRaisesRegex(ValueError, cc_syllabus.NO_TEXT):
            cc_syllabus.extract_text(b"   ", "text/plain", "a.txt")
        with self.assertRaises(ValueError):
            cc_syllabus.extract_text(b"\x89PNG....", "image/png", "scan.png")
        with self.assertRaisesRegex(ValueError, "PDF"):
            cc_syllabus.extract_text(b"%PDF-1.4 broken", "application/pdf", "s.pdf")

    def test_private_and_non_https_links_are_rejected(self):
        for url in ("http://example.com/s.pdf", "https://127.0.0.1/s.pdf", "https://169.254.169.254/latest",
                    "https://user:pw@example.com/", "https://example.com:8443/s.pdf"):
            with self.assertRaises(ValueError, msg=url):
                cc_syllabus.check_url(url)
        with patch("cc_syllabus.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.5", 443))]):
            with self.assertRaisesRegex(ValueError, "内部网络"):
                cc_syllabus.check_url("https://intranet.example/s.pdf")

    def test_each_chunk_reserves_quota_before_model_call(self):
        reply = json.dumps({"items": [{"title": "Midterm", "kind": "exam", "date": "2026-10-15", "time": None,
                                       "date_text": "", "evidence": "Midterm exam: October 15, 2026"}]})
        calls = []
        text = (self.TEXT + "\n") + ("filler line\n" * 1200)
        with patch("urllib.request.OpenerDirector.open", side_effect=lambda *a, **k: _mock_open_with_content(reply)):
            items = cc_syllabus.extract_nodes("https://api.example", "k", "m", text, "MATH", 2026,
                                              before_chunk=lambda: calls.append(1))
        self.assertEqual(len(cc_syllabus.chunks(text)), len(calls))
        self.assertGreater(len(calls), 1)
        self.assertEqual(1, len(items))

    def test_login_redirects_explain_download_instead_of_redirect_count(self):
        import email.message
        import urllib.error

        def redirect_to(location, code=302):
            headers = email.message.Message()
            headers["Location"] = location
            return urllib.error.HTTPError("https://uni.example/s", code, "Found", headers, None)

        class Opener:
            def __init__(self, errors):
                self.errors = list(errors)

            def open(self, request, timeout=None):
                raise self.errors.pop(0)

        public = [(2, 1, 6, "", ("93.184.216.34", 443))]
        cases = [
            [redirect_to("https://idp.uni.example/login?next=/s")],
            [redirect_to("/a"), redirect_to("/s")],
            [urllib.error.HTTPError("https://uni.example/s", 403, "Forbidden", email.message.Message(), None)],
        ]
        with patch("cc_syllabus.socket.getaddrinfo", return_value=public):
            for errors in cases:
                with self.assertRaisesRegex(ValueError, "需要登录"):
                    cc_syllabus.fetch("https://uni.example/s", opener=Opener(errors))

    def test_rejected_ai_key_is_reported_as_key_problem(self):
        with patch("cc_syllabus.cc_ai._chat_json",
                   side_effect=ValueError("AI service request failed with HTTP 401")):
            with self.assertRaisesRegex(ValueError, "AI 服务拒绝了当前密钥"):
                cc_syllabus.extract_nodes("https://api.example", "k", "m", self.TEXT, "MATH", 2026)


    def test_canvas_links_are_classified_only_on_bound_origin(self):
        origin = "https://canvas.uni.example"
        self.assertEqual(("syllabus", "123", None),
                         cc_syllabus.canvas_target(origin + "/courses/123/assignments/syllabus", origin))
        self.assertEqual(("home", "123", None), cc_syllabus.canvas_target(origin + "/courses/123", origin))
        self.assertEqual(("file", "123", "456"),
                         cc_syllabus.canvas_target(origin + "/courses/123/files/456/download?wrap=1", origin))
        self.assertEqual(("page", "123", "course-outline"),
                         cc_syllabus.canvas_target(origin + "/courses/123/pages/course-outline", origin))
        self.assertIsNone(cc_syllabus.canvas_target("https://other.example/courses/123/files/456", origin))
        with self.assertRaisesRegex(ValueError, "只支持 Canvas"):
            cc_syllabus.canvas_target(origin + "/courses/123/discussion_topics/9", origin)

    def test_canvas_reads_use_api_and_file_download_skips_token(self):
        class Api:
            def __init__(self, replies):
                self.replies, self.paths = replies, []

            def get(self, path):
                self.paths.append(path)
                reply = self.replies[path]
                if isinstance(reply, Exception):
                    raise reply
                return reply

        long_body = "<p>Midterm exam: October 15, 2026.</p>" + "<p>Course policy and grading details.</p>" * 10
        course = "/api/v1/courses/1?include[]=syllabus_body"
        front = "/api/v1/courses/1/front_page"
        api = Api({course: {"syllabus_body": long_body, "default_view": "modules"}})
        data, kind, _ = cc_syllabus.canvas_fetch(api, ("syllabus", "1", None))
        self.assertEqual((long_body.encode(), "text/html"), (data, kind))

        # Home page is the syllabus: empty syllabus section falls back to the course front page.
        api = Api({course: {"syllabus_body": "", "default_view": "wiki"},
                   front: {"title": "Course 101 Syllabus", "body": long_body}})
        for target in (("syllabus", "1", None), ("home", "1", None)):
            data, _, name = cc_syllabus.canvas_fetch(api, target)
            self.assertEqual((long_body.encode(), "Course 101 Syllabus"), (data, name))

        # A near-empty syllabus section that links to an outline page reads that page.
        api = Api({course: {"syllabus_body": '<a href="/courses/1/pages/outline">Outline</a>'},
                   "/api/v1/courses/1/pages/outline": {"title": "Outline", "body": long_body}})
        self.assertEqual("Outline", cc_syllabus.canvas_fetch(api, ("syllabus", "1", None))[2])

        api = Api({course: {"syllabus_body": ""},
                   front: ValueError("Canvas API request failed with HTTP 404")})
        with self.assertRaisesRegex(ValueError, "主页都没有可识别的文字"):
            cc_syllabus.canvas_fetch(api, ("syllabus", "1", None))

        api = Api({"/api/v1/courses/1/files/2": {"url": "https://files.example/x?verifier=v", "size": 10,
                                                 "display_name": "syllabus.pdf"}})
        with patch("cc_syllabus.fetch", return_value=(b"%PDF", "application/pdf", "u")) as plain:
            self.assertEqual((b"%PDF", "application/pdf", "syllabus.pdf"),
                             cc_syllabus.canvas_fetch(api, ("file", "1", "2")))
        plain.assert_called_once()

        api = Api({"/api/v1/courses/1/files/3": {"locked_for_user": True}})
        with self.assertRaisesRegex(ValueError, "锁定"):
            cc_syllabus.canvas_fetch(api, ("file", "1", "3"))
        api = Api({"/api/v1/courses/1/pages/x": ValueError("Canvas API request failed with HTTP 404")})
        with self.assertRaisesRegex(ValueError, "找不到"):
            cc_syllabus.canvas_fetch(api, ("page", "1", "x"))

if __name__ == "__main__":
    unittest.main()
