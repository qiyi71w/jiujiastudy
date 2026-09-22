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
        self.server.force_status(r"/courses/\d+/assignments", 403)
        failed = self.command("refresh")
        self.assertIn("失败", failed["text"])
        report = self.command("report", restarted)
        self.assertFalse(report["complete"])
        self.assertEqual(first["collected_at"], report["collected_at"])
        self.assertIn("ACCT1101", report["text"])
        self.assertIn("失败", report["text"])

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
        self.service.acknowledge_delivery(first)
        self.sc.course[1104]["assignments"][0]["due_at"] = "2026-03-28T10:00:00Z"
        second = self.command("refresh")
        self.assertIn("改期：STAT2011", second["text"])
        self.assertNotIn("新课程：STAT2011", second["text"])
        self.assertEqual(2, sum(r["path"] == "/api/v1/courses/1104/assignments" for r in self.server.requests()))

    def test_learner_due_date_and_undated_tasks_use_assignment_evidence(self):
        task = self.sc.course[1101]["assignments"][0]
        task.update(name="Personal extension", has_overrides=True, due_at="2026-03-27T10:00:00Z",
                    submission={"workflow_state": "unsubmitted", "cached_due_date": "2026-03-20T10:00:00Z"})
        text = self.command("refresh")["text"]
        self.assertIn("Personal extension", text.split("逾期未交")[0])
        task.update(name="Undated zero-point task", due_at=None, lock_at="2026-03-27T10:00:00Z", points_possible=0)
        text = self.command("refresh")["text"]
        self.assertIn("Undated zero-point task", text.split("日期不明任务")[1].split("新增与改期")[0])

    def test_submission_evidence_not_grades_controls_reminders(self):
        def assignment(aid, name, submission, **extra):
            return dict(id=aid, name=name, due_at="2026-03-27T10:00:00Z", points_possible=10,
                        html_url=f"{{{{BASE}}}}/courses/1101/assignments/{aid}",
                        submission_types=["online_upload"], submission=submission, **extra)
        values = [assignment(901, "Upload", {"workflow_state": "unsubmitted"}),
                  assignment(902, "ScoreOnly", {"workflow_state": "graded", "score": 0}),
                  assignment(903, "Excused", {"workflow_state": "unsubmitted", "excused": True}),
                  assignment(904, "Paper", {"workflow_state": "graded", "score": 50}),
                  assignment(905, "Group", {"workflow_state": "submitted"}, group_category_id=12),
                  assignment(906, "Quiz", {"workflow_state": "pending_review"}, is_quiz_assignment=True)]
        values[3]["submission_types"] = ["on_paper"]
        self.sc.course[1101]["assignments"] = values
        first = self.command("refresh")
        pending = first["text"].split("新增与改期")[0]
        self.assertIn("Upload", pending)
        self.assertIn("ScoreOnly", pending)
        self.assertIn("Paper", pending)
        self.assertIn("提交状态需确认", pending)
        for name in ("Excused", "Group", "Quiz"):
            self.assertNotIn(name, pending)
        values[0]["submission"] = {"workflow_state": "submitted", "submitted_at": "2026-03-24T22:00:00Z", "attempt": 1}
        values[0]["due_at"] = "2026-03-30T10:00:00Z"
        submitted = self.command("refresh")["text"]
        self.assertNotIn("Upload", submitted.split("新增与改期")[0])
        values[0]["submission"].update(workflow_state="graded", score=0, grade="F")
        values[0]["due_at"] = "2026-03-31T10:00:00Z"
        graded = self.command("refresh")["text"]
        self.assertNotIn("Upload", graded.split("新增与改期")[0])
        values[0]["submission"]["redo_request"] = True
        redone = self.command("refresh")["text"]
        self.assertIn("Upload", redone.split("新增与改期")[0])
        self.assertIn("要求重新提交", redone)

    def test_partial_refresh_updates_other_courses_and_marks_stale_data(self):
        self.sc.course[1101]["assignments"][0].update(name="Preserved old task", due_at="2026-03-27T10:00:00Z",
                                                    submission={"workflow_state": "unsubmitted"})
        first = self.command("refresh")
        pin_now("2026-03-25T01:00:00Z")
        self.server.force_status(r"/courses/1101/assignments", 403)
        self.sc.course[1102]["assignments"][0].update(name="Fresh task", due_at="2026-03-28T10:00:00Z",
                                                    submission={"workflow_state": "unsubmitted"})
        partial = self.command("refresh")
        self.assertFalse(partial["complete"])
        self.assertIn("Fresh task", partial["text"])
        self.assertIn("Preserved old task", partial["text"])
        self.assertIn("ACCT1101", partial["text"])
        self.assertIn(first["collected_at"], partial["text"])
        self.assertIn("2026-03-25T01:00:00", partial["text"])
        restarted = AccountService(self.home.archive, self.secrets_path)
        self.assertEqual(partial, self.command("report", restarted))
        self.server.force_status(r"/courses/\d+/assignments", 403)
        pin_now("2026-03-25T02:00:00Z")
        failed = self.command("refresh", restarted)
        self.assertFalse(failed["complete"])
        self.assertEqual(partial["collected_at"], failed["collected_at"])
        self.assertIn("全部", failed["text"])
        self.assertIn("Fresh task", failed["text"])
        self.assertNotIn("没有待交", failed["text"])

    def test_delivered_changes_do_not_repeat_or_consume_scheduled_changes(self):
        task = self.sc.course[1101]["assignments"][0]
        task.update(name="Changing task", due_at="2026-03-27T10:00:00Z", submission={"workflow_state": "unsubmitted"})
        first = self.command("refresh")
        self.service.acknowledge_delivery(first)
        task["submission"].update(workflow_state="submitted", submitted_at="2026-03-24T22:00:00Z", attempt=1)
        changed = self.command("refresh")
        self.assertIn("Changing task", changed["text"].split("最近已提交")[1])
        # Until the channel confirms delivery, restart and another scan must retain it.
        restarted = AccountService(self.home.archive, self.secrets_path)
        retried = self.command("refresh", restarted)
        self.assertIn("Changing task", retried["text"].split("最近已提交")[1])
        restarted.acknowledge_delivery(retried)
        task["submission"].update(workflow_state="graded", score=0)
        again = self.command("refresh", restarted)
        self.assertNotIn("Changing task", again["text"].split("最近已提交")[1])
        self.assertNotIn("Changing task", self.command("report", restarted)["text"].split("最近已提交")[1])
        # The persisted notification obligation is independent of manual viewing.
        journal = jload(Path(self.home.archive, "service-report.json"))["events"]
        submissions = [event for event in journal if event["kind"] == "submitted" and "Changing task" in event["text"]]
        self.assertEqual(1, len(submissions))
        self.assertEqual(["scheduled"], submissions[0]["pending"])

    def test_new_course_notice_survives_total_assignment_failure(self):
        first = self.command("refresh")
        self.service.acknowledge_delivery(first)
        self.sc.courses.append({"id": 1104, "name": "STAT2011 Probability", "course_code": "STAT2011", "_active": True})
        self.sc.course[1104] = {"assignments": []}
        self.server.force_status(r"/courses/\d+/assignments", 403)
        failed = self.command("refresh")
        self.assertFalse(failed["complete"])
        self.assertIn("新课程：STAT2011", failed["text"])
        restarted = AccountService(self.home.archive, self.secrets_path)
        self.assertIn("新课程：STAT2011", self.command("report", restarted)["text"])
        restarted.acknowledge_delivery(failed)
        again = self.command("refresh", restarted)
        self.assertNotIn("新课程：STAT2011", again["text"])
        self.assertIn("STAT2011", again["text"])

    def test_overlapping_refreshes_share_collection_and_next_request_is_immediate(self):
        from concurrent.futures import ThreadPoolExecutor, TimeoutError
        import threading
        entered, release = threading.Event(), threading.Event()
        original = mockcanvas._Handler.r_user_self
        def hold_request(handler):
            entered.set()
            if not release.wait(5):
                return handler._send(500, {})
            return original(handler)
        other = AccountService(self.home.archive, self.secrets_path)
        with patch.object(mockcanvas._Handler, "r_user_self", hold_request), ThreadPoolExecutor(2) as pool:
            first = pool.submit(self.command, "refresh")
            try:
                self.assertTrue(entered.wait(5))
                second = pool.submit(self.command, "refresh", other)
                with self.assertRaises(TimeoutError):
                    second.result(timeout=0.15)
            finally:
                release.set()
            one, two = first.result(timeout=5), second.result(timeout=5)
        self.assertTrue(one["complete"], one)
        self.assertEqual(one, two)
        self.assertEqual(1, sum(r["path"] == "/api/v1/users/self" for r in self.server.requests()))
        next_result = self.command("refresh", other)
        self.assertTrue(next_result["complete"])
        self.assertEqual(2, sum(r["path"] == "/api/v1/users/self" for r in self.server.requests()))

    def test_delivery_acknowledgement_during_collection_is_not_overwritten(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        task = self.sc.course[1101]["assignments"][0]
        task.update(name="Delivered during refresh", submission={"workflow_state": "submitted", "submitted_at": "2026-03-24T22:00:00Z"})
        pending = self.command("refresh")
        entered, release = threading.Event(), threading.Event()
        original = mockcanvas._Handler.r_user_self
        def hold_request(handler):
            entered.set()
            release.wait(5)
            return original(handler)
        with patch.object(mockcanvas._Handler, "r_user_self", hold_request), ThreadPoolExecutor(1) as pool:
            refreshed = pool.submit(self.command, "refresh")
            try:
                self.assertTrue(entered.wait(5))
                self.service.acknowledge_delivery(pending)
            finally:
                release.set()
            result = refreshed.result(timeout=5)
        self.assertNotIn("Delivered during refresh", result["text"].split("最近已提交")[1])

    def test_failed_telegram_delivery_keeps_change_until_report_succeeds(self):
        import io
        import urllib.request
        from cc_telegram import TelegramBot
        task = self.sc.course[1101]["assignments"][0]
        task.update(name="Retry submission notice", submission={"workflow_state": "submitted", "submitted_at": "2026-03-24T22:00:00Z"})
        self.command("refresh")
        bot = TelegramBot(self.service, self.service.secrets.telegram_bot_token)
        update = {"update_id": 90, "message": {"from": {"id": 1234}, "chat": {"id": 1234, "type": "private"}, "text": "/report"}}
        sent, fail = [], [True]
        def boundary(opener, request, *args, **kwargs):
            self.assertTrue(request.full_url.startswith("https://api.telegram.org/"))
            payload = json.loads(request.data)
            sent.append(payload["text"])
            if fail[0]:
                raise OSError("simulated delivery failure")
            return io.BytesIO(json.dumps({"ok": True, "result": {"message_id": 1}}).encode())
        with patch.object(urllib.request.OpenerDirector, "open", boundary):
            bot.handle_update(update)
            sent.clear()
            fail[0] = False
            bot.service = AccountService(self.home.archive, self.secrets_path)
            bot.handle_update(update)
            self.assertIn("Retry submission notice", "".join(sent))
            sent.clear()
            bot.handle_update(update)
            self.assertNotIn("Retry submission notice", "".join(sent))

    def test_task_buttons_persist_cancel_replay_rearm_and_submission_priority(self):
        task = self.sc.course[1101]["assignments"][0]
        for course in self.sc.course.values():
            course["assignments"] = []
        self.sc.course[1101]["assignments"] = [task]
        task.update(name="Task decision", due_at="2026-03-27T10:00:00Z",
                    submission={"workflow_state": "unsubmitted"})
        self.command("refresh")
        self.server.requests(clear=True)
        listing = self.command("tasks")
        self.assertIn("Task decision", listing["text"])
        selection = listing["actions"][0][0]["data"]
        with self.assertRaises(ValueError):
            self.service.task_action(4321, selection)
        confirm = self.service.task_action(1234, selection)
        self.service.task_action(1234, confirm["actions"][0][1]["data"])
        self.assertIn("Task decision", self.command("report")["text"])
        selection = self.command("tasks")["actions"][0][0]["data"]
        options = self.service.task_action(1234, selection)
        stop = options["actions"][1][0]["data"]
        self.service.task_action(1234, stop)
        self.assertNotIn("Task decision", self.command("report")["text"])
        restarted = AccountService(self.home.archive, self.secrets_path)
        self.assertIn("Task decision", restarted.execute(1234, "tasks", ["stopped"], "telegram", "test")["text"])
        self.service.task_action(1234, options["actions"][0][0]["data"])
        self.assertIn("Task decision", restarted.execute(1234, "tasks", ["stopped"], "telegram", "test")["text"])
        self.assertEqual([], self.server.requests())
        task["due_at"] = "2026-03-28T10:00:00Z"
        refreshed = self.command("refresh")
        self.assertIn("Task decision", refreshed["text"])
        self.assertNotIn("Task decision", self.service.execute(1234, "tasks", ["stopped"], "telegram", "test")["text"])
        notices = [event for event in jload(Path(self.home.archive, "service-report.json"))["events"]
                   if event["kind"] == "reminder"]
        self.assertEqual(1, len(notices))
        self.service.acknowledge_delivery(refreshed)
        self.command("refresh")
        self.assertEqual(1, sum(event["kind"] == "reminder" for event in
                                jload(Path(self.home.archive, "service-report.json"))["events"]))
        selection = self.command("tasks")["actions"][0][0]["data"]
        pending = self.service.task_action(1234, selection)["actions"][0][0]["data"]
        task["due_at"] = "2026-03-29T10:00:00Z"
        self.command("refresh")
        self.service.task_action(1234, pending)
        self.assertNotIn("Task decision", self.service.execute(1234, "tasks", ["stopped"], "telegram", "test")["text"])

    def test_reminder_rearms_only_on_explicit_change_and_keeps_submitted_filtered(self):
        task = self.sc.course[1101]["assignments"][0]
        for course in self.sc.course.values():
            course["assignments"] = []
        self.sc.course[1101]["assignments"] = [task]
        task.update(name="Submission priority", due_at="2026-03-27T10:00:00Z",
                    submission={"workflow_state": "unsubmitted"})
        self.command("refresh")
        def stop():
            selected = self.command("tasks")["actions"][0][0]["data"]
            confirmed = self.service.task_action(1234, selected)["actions"][0][0]["data"]
            self.service.task_action(1234, confirmed)
        stop()
        task["submission"] = {"workflow_state": "graded", "score": 0, "grade": "F", "comment": "Revise?"}
        self.command("refresh")
        self.assertIn("Submission priority", self.service.execute(1234, "tasks", ["stopped"], "telegram", "test")["text"])
        task["submission"]["redo_request"] = True
        redo = self.command("refresh")
        self.assertIn("Submission priority", redo["text"])
        self.service.acknowledge_delivery(redo)
        self.command("refresh")
        self.assertEqual(1, sum(event["kind"] == "reminder" for event in
                                jload(Path(self.home.archive, "service-report.json"))["events"]))
        task["submission"] = {"workflow_state": "submitted", "submitted_at": "2026-03-24T22:00:00Z"}
        self.command("refresh")
        stop()
        task["due_at"] = "2026-03-30T10:00:00Z"
        changed = self.command("refresh")
        self.service.acknowledge_delivery(changed)
        self.assertNotIn("Submission priority", self.command("report")["text"])
        self.assertNotIn("Submission priority", self.service.execute(1234, "tasks", ["stopped"], "telegram", "test")["text"])

    def test_telegram_task_callbacks_are_private_and_paginate(self):
        from cc_telegram import TelegramBot
        assignments = self.sc.course[1101]["assignments"]
        sample = dict(assignments[0])
        sample.update(due_at="2026-03-27T10:00:00Z", submission={"workflow_state": "unsubmitted"})
        assignments[:] = [dict(sample, id=10000 + n, name=f"Task {n}") for n in range(20)]
        self.command("refresh")
        self.server.requests(clear=True)
        bot = TelegramBot(self.service, self.service.secrets.telegram_bot_token)
        calls = []
        bot.request = lambda method, payload=None: calls.append((method, payload))
        message = lambda uid, data: {"callback_query": {"id": "cb1", "from": {"id": uid},
                   "message": {"chat": {"id": 1234, "type": "private"}}, "data": data}}
        bot.handle_update({"message": {"from": {"id": 1234}, "chat": {"id": 1234, "type": "private"}, "text": "/canvas tasks"}})
        first = calls[-1][1]
        self.assertEqual(9, len(first["reply_markup"]["inline_keyboard"]))
        page = first["reply_markup"]["inline_keyboard"][-1][-2]["callback_data"]
        calls.clear()
        bot.handle_update({"callback_query": {"id": "cb-group", "from": {"id": 1234},
                          "message": {"chat": {"id": -1234, "type": "group"}}, "data": page}})
        self.assertEqual([], calls)
        bot.handle_update(message(4321, page))
        self.assertEqual([], calls)
        bot.handle_update(message(1234, "task:" + "f" * 32))
        self.assertEqual(["answerCallbackQuery", "sendMessage"], [op for op, _ in calls])
        calls.clear()
        bot.handle_update(message(1234, page))
        self.assertEqual(["answerCallbackQuery", "sendMessage"], [op for op, _ in calls])
        self.assertIn("9.", calls[-1][1]["text"])
        self.assertEqual([], self.server.requests())

    def test_concurrent_task_decisions_preserve_both(self):
        from concurrent.futures import ThreadPoolExecutor
        sample = dict(self.sc.course[1101]["assignments"][0])
        sample.update(due_at="2026-03-27T10:00:00Z", submission={"workflow_state": "unsubmitted"})
        for course in self.sc.course.values():
            course["assignments"] = []
        self.sc.course[1101]["assignments"] = [dict(sample, id=901, name="One"), dict(sample, id=902, name="Two")]
        self.command("refresh")
        controls = self.command("tasks")["actions"][:2]
        previews = [self.service.task_action(1234, row[0]["data"]) for row in controls]
        self.assertIn("One", previews[0]["text"])
        self.assertIn("Two", previews[1]["text"])
        confirmations = [preview["actions"][0][0]["data"] for preview in previews]
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(lambda token: self.service.task_action(1234, token), confirmations))
        report = self.command("report")["text"]
        self.assertNotIn("One", report)
        self.assertNotIn("Two", report)
        self.assertIn("One", self.service.execute(1234, "tasks", ["stopped"], "telegram", "test")["text"])
        self.assertIn("Two", self.service.execute(1234, "tasks", ["stopped"], "telegram", "test")["text"])

    def test_expired_task_confirmation_cannot_change_state(self):
        sample = self.sc.course[1101]["assignments"][0]
        sample.update(due_at="2026-03-27T10:00:00Z", submission={"workflow_state": "unsubmitted"})
        self.command("refresh")
        selection = self.command("tasks")["actions"][0][0]["data"]
        confirm = self.service.task_action(1234, selection)["actions"][0][0]["data"]
        path = Path(self.home.archive, "service-report.json")
        saved = jload(path)
        saved["task_buttons"][confirm[5:]]["at"] = 0
        jsave(path, saved)
        self.service.task_action(1234, confirm)
        self.assertNotIn(confirm[5:], jload(path).get("task_buttons", {}))
        self.assertEqual({}, jload(path).get("reminders", {}))

    def test_task_button_from_other_account_cannot_read_or_change_state(self):
        self.sc.course[1101]["assignments"][0].update(
            name="Private task", due_at="2026-03-27T10:00:00Z",
            submission={"workflow_state": "unsubmitted"})
        target = self.sc.course[1101]["assignments"][0]
        for course in self.sc.course.values():
            course["assignments"] = []
        self.sc.course[1101]["assignments"] = [target]
        self.command("refresh")
        token = self.command("tasks")["actions"][0][0]["data"]
        other_home = harness.FakeHome("other-account")
        self.addCleanup(other_home.cleanup)
        other_home.seed(self.sc, self.origin)
        cfg_path = Path(other_home.archive, "config.json")
        cfg = jload(cfg_path)
        cfg["service"] = {"telegram_user_id": 5678}
        jsave(cfg_path, cfg)
        other_secrets = Path(other_home.tmp, "service.json")
        jsave(other_secrets, {"canvas_origin": self.origin, "canvas_token": self.sc.token,
                              "telegram_bot_token": "2000:" + "y" * 35})
        os.chmod(other_secrets, 0o600)
        other = AccountService(other_home.archive, str(other_secrets))
        other.execute(5678, "refresh", [], "telegram", "other-refresh")
        before = jload(other.report_path)
        rejected = other.task_action(5678, token)
        self.assertNotIn("Private task", rejected["text"])
        self.assertEqual(before, jload(other.report_path))
        with self.assertRaises(ValueError):
            other.task_action(1234, token)
        self.assertIn("Private task", self.service.task_action(1234, token)["text"])

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
        menus = {}
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
            elif method in ("setMyCommands", "setChatMenuButton"):
                menus[method] = payload
                result = True
            elif method == "getUpdates":
                self.assertEqual({"type": "chat", "chat_id": 1234}, menus["setMyCommands"]["scope"])
                self.assertEqual({"chat_id": 1234, "menu_button": {"type": "commands"}}, menus["setChatMenuButton"])
                commands = menus["setMyCommands"]["commands"]
                self.assertEqual({"help", "status", "report", "refresh", "tasks", "schedule", "on", "off"}, {c["command"] for c in commands})
                for command in commands:
                    self.assertEqual((command["command"], [], None), bot.parse_command("/" + command["command"]))
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
        with patch.object(bot, "_scheduled_once", return_value=None), patch.object(urllib.request.OpenerDirector, "open", boundary):
            bot.run()
        self.assertEqual(5, polls[1]["offset"])
        self.assertEqual(30, polls[0]["timeout"])
        self.assertTrue(all(item["chat_id"] == 1234 for item in sent))
        reconstructed = "".join(html.unescape(item["text"][5:-6]) for item in sent[1:])
        self.assertIn("\U0001f600" * 4000, reconstructed)
        self.assertIn("＠everyone", reconstructed)
        for item in sent:
            if "设置：/canvas schedule" in item["text"]:
                self.assertEqual("schedule:help", item["reply_markup"]["inline_keyboard"][0][0]["callback_data"])
            else:
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
                report = self.command("report")
                self.assertFalse(report["complete"])
                self.assertEqual(first["collected_at"], report["collected_at"])
                self.assertIn("ACCT1101", report["text"])
                self.assertEqual(config_before, Path(self.home.archive, "config.json").read_bytes())


    def test_daily_plan_reuses_attempt_and_retries_delivery_without_canvas(self):
        from unittest.mock import patch
        with patch("cc_schedule.secrets.randbelow", return_value=0):
            self.assertIn("2026-03-24", self.service.execute(1234, "schedule", ["22:45", "UTC"], "telegram", "setting")["text"])
        self.server.requests(clear=True)
        first = self.service.scheduled_tick()
        self.assertEqual("2026-03-24", first["day"])
        self.assertIn("Canvas 规则日报", first["text"])
        count = len(self.server.requests())
        self.assertGreater(count, 0)
        self.service.scheduled_delivery(first["day"], False)
        self.assertIsNone(self.service.scheduled_tick())
        pin_now("2026-03-24T23:01:00Z")
        restarted = AccountService(self.home.archive, self.secrets_path)
        self.assertEqual(first, restarted.scheduled_tick())
        self.assertEqual(count, len(self.server.requests()))
        restarted.scheduled_delivery(first["day"], True)
        self.assertIsNone(restarted.scheduled_tick())
        self.assertEqual(count, len(self.server.requests()))
        plan = jload(self.service.report_path)["daily_plans"][first["day"]]
        self.assertEqual("sent", plan["delivery"]["telegram"]["state"])

    def test_daily_plan_fold_gap_midnight_and_timezone_change(self):
        import datetime as dt
        from zoneinfo import ZoneInfo
        from cc_schedule import planned
        tz = ZoneInfo("America/New_York")
        self.assertEqual("2026-11-01T05:30:00+00:00", planned(dt.date(2026, 11, 1), "01:30", tz, 0))
        self.assertEqual("2026-03-08T07:00:00+00:00", planned(dt.date(2026, 3, 8), "02:30", tz, 0))
        with patch("cc_schedule.secrets.randbelow", return_value=900):
            self.service.execute(1234, "schedule", ["23:55", "UTC"], "telegram", "setting")
        plan = jload(self.service.report_path)["daily_plans"]["2026-03-24"]
        self.assertEqual("2026-03-25T00:10:00+00:00", plan["due"])
        self.service.execute(1234, "schedule", ["22:00", "UTC"], "telegram", "setting")
        changed = jload(self.service.report_path)["daily_plans"]["2026-03-24"]
        self.assertEqual(900, changed["delay"])
        self.assertEqual("2026-03-24T22:15:00+00:00", changed["due"])

    def test_daily_report_partial_failure_is_formed_and_not_recollected(self):
        with patch("cc_schedule.secrets.randbelow", return_value=0):
            self.service.execute(1234, "schedule", ["22:00", "UTC"], "telegram", "setting")
        self.command("refresh")
        self.server.force_status(r"/courses/1101/assignments", 403)
        self.server.requests(clear=True)
        due = self.service.scheduled_tick()
        self.assertIn("采集失败：ACCT1101", due["text"])
        self.assertIn("不完整", due["text"])
        count = len(self.server.requests())
        self.assertEqual(due, AccountService(self.home.archive, self.secrets_path).scheduled_tick())
        self.assertEqual(count, len(self.server.requests()))
        self.assertEqual("formed", jload(self.service.report_path)["daily_plans"][due["day"]]["state"])

    def test_unformed_plan_cancel_and_timezone_edit_does_not_double_scan(self):
        with patch("cc_schedule.secrets.randbelow", return_value=0):
            self.service.execute(1234, "schedule", ["22:00", "UTC"], "telegram", "setting")
        self.service.cancel_scheduled()
        self.assertEqual("cancelled", jload(self.service.report_path)["daily_plans"]["2026-03-24"]["state"])
        self.assertIsNone(self.service.scheduled_tick())
        self.assertEqual([], self.server.requests())
        pin_now("2026-03-25T23:00:00Z")
        with patch("cc_schedule.secrets.randbelow", return_value=0):
            self.service.scheduled_tick()
        count = len(self.server.requests())
        self.service.execute(1234, "schedule", ["08:00", "America/Los_Angeles"], "telegram", "setting")
        self.assertEqual("2026-03-25", self.service.scheduled_tick()["day"])
        self.assertEqual(count, len(self.server.requests()))


    def test_midnight_due_executes_once_and_skips_old_history(self):
        with patch("cc_schedule.secrets.randbelow", return_value=900):
            self.service.execute(1234, "schedule", ["23:55", "UTC"], "telegram", "setting")
        pin_now("2026-03-25T00:11:00Z")
        due = self.service.scheduled_tick()
        self.assertEqual("2026-03-24", due["day"])
        count = len(self.server.requests())
        self.service.scheduled_delivery(due["day"], True)
        self.assertEqual(count, len(self.server.requests()))
        pin_now("2026-03-28T23:59:00Z")
        with patch("cc_schedule.secrets.randbelow", return_value=0):
            current = AccountService(self.home.archive, self.secrets_path).scheduled_tick()
        self.assertEqual("2026-03-28", current["day"])
        plans = jload(self.service.report_path)["daily_plans"]
        self.assertEqual("formed", plans["2026-03-24"]["state"])
        self.assertNotIn("2026-03-26", plans)
        self.assertNotIn("2026-03-27", plans)

    def test_scheduled_first_snapshot_retains_failed_course_on_later_refresh(self):
        task = self.sc.course[1101]["assignments"][0]
        task.update(name="Stale scheduled task", due_at="2026-03-27T10:00:00Z",
                    submission={"workflow_state": "unsubmitted"})
        with patch("cc_schedule.secrets.randbelow", return_value=0):
            self.service.execute(1234, "schedule", ["22:00", "UTC"], "telegram", "setting")
        first = self.service.scheduled_tick()
        self.assertIn("Stale scheduled task", first["text"])
        stamp = self.command("report")["collected_at"]
        self.server.force_status(r"/courses/1101/assignments", 403)
        self.sc.course[1102]["assignments"][0].update(name="Updated course task",
            due_at="2026-03-27T10:00:00Z", submission={"workflow_state": "unsubmitted"})
        pin_now("2026-03-25T01:00:00Z")
        partial = self.command("refresh")
        self.assertIn("Stale scheduled task", partial["text"])
        self.assertIn(stamp, partial["text"])
        self.assertIn("Updated course task", partial["text"])

    def test_overlapping_manual_refresh_forms_plan_without_second_collection(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        with patch("cc_schedule.secrets.randbelow", return_value=0):
            self.service.execute(1234, "schedule", ["22:00", "UTC"], "telegram", "setting")
        manual_started, manual_release = threading.Event(), threading.Event()
        scheduled_ready, scheduled_release = threading.Event(), threading.Event()
        original_get = SecureCanvas.get
        def hold_manual(api, path):
            if path == "/api/v1/users/self" and not manual_started.is_set():
                manual_started.set()
                self.assertTrue(manual_release.wait(5))
            return original_get(api, path)
        scheduled = AccountService(self.home.archive, self.secrets_path)
        original_collect = scheduled._collect
        def hold_scheduled(*args, **kwargs):
            scheduled_ready.set()
            self.assertTrue(scheduled_release.wait(5))
            return original_collect(*args, **kwargs)
        with patch.object(SecureCanvas, "get", hold_manual), patch.object(scheduled, "_collect", hold_scheduled):
            with ThreadPoolExecutor(max_workers=2) as pool:
                manual = pool.submit(self.command, "refresh")
                self.assertTrue(manual_started.wait(5))
                daily = pool.submit(scheduled.scheduled_tick)
                self.assertTrue(scheduled_ready.wait(5))
                manual_release.set()
                self.assertIn("Canvas 规则日报", manual.result(timeout=5)["text"])
                scheduled_release.set()
                self.assertEqual("2026-03-24", daily.result(timeout=5)["day"])
        requests = [r for r in self.server.requests() if r["path"] == "/api/v1/users/self"]
        self.assertEqual(1, len(requests))

    def test_off_retains_management_and_prevents_collection_across_restart(self):
        self.command("refresh")
        task = self.service.task_list(1234)["actions"][0][0]["data"]
        self.assertIn("已关闭", self.command("off")["text"])
        restarted = AccountService(self.home.archive, self.secrets_path)
        self.server.requests(clear=True)
        self.assertIn("已关闭", self.command("status", restarted)["text"])
        self.assertIn("已关闭", self.command("refresh", restarted)["text"])
        self.assertIn("数据截至", self.command("report", restarted)["text"])
        confirmation = restarted.task_action(1234, task)
        self.assertTrue(confirmation["actions"])
        confirmed = restarted.task_action(1234, confirmation["actions"][0][0]["data"])
        self.assertIn("已停止提醒", confirmed["text"])
        self.assertIn("每日计划", restarted.execute(1234, "schedule", ["08:30", "UTC"], "telegram", "change")["text"])
        self.assertIsNone(restarted.scheduled_tick())
        self.assertEqual([], self.server.requests())
        self.assertIn("已关闭", self.command("off", restarted)["text"])

    def test_off_during_canvas_pagination_and_fast_on_discards_old_result(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        self.sc.opts["max_per_page"] = 1
        entered, release = threading.Event(), threading.Event()
        original = SecureCanvas.fetch
        def pause_after_first_page(api, url, accept="application/json"):
            result = original(api, url, accept)
            if "/api/v1/courses?" in url and "&page=" not in url and not entered.is_set():
                entered.set()
                self.assertTrue(release.wait(5))
            return result
        with patch.object(SecureCanvas, "fetch", pause_after_first_page), ThreadPoolExecutor(max_workers=2) as pool:
            old = pool.submit(self.command, "refresh")
            self.assertTrue(entered.wait(5))
            self.command("off")
            self.assertEqual(1, sum(r["path"] == "/api/v1/courses" for r in self.server.requests()))
            new = pool.submit(self.command, "on")
            release.set()
            old_result = old.result(timeout=5)
            self.assertNotIn("Canvas 规则日报", old_result["text"])
            self.assertIn("形成当日计划日报", new.result(timeout=5)["text"])
        self.assertEqual(2, sum(r["path"] == "/api/v1/users/self" for r in self.server.requests()))
        self.assertEqual(len(self.sc.courses) + 1, sum(r["path"] == "/api/v1/courses" for r in self.server.requests()))


    def test_on_forms_unformed_day_but_resumes_formed_delivery_without_recollect(self):
        with patch("cc_schedule.secrets.randbelow", return_value=0):
            self.service.execute(1234, "schedule", ["22:00", "UTC"], "telegram", "setting")
        self.command("off")
        self.server.requests(clear=True)
        first = self.command("on")
        self.assertIn("形成当日计划日报", first["text"])
        self.assertGreater(len(self.server.requests()), 0)
        due = self.service.scheduled_tick()
        self.assertIsNotNone(due)
        self.assertEqual("formed", jload(self.service.report_path)["daily_plans"][due["day"]]["state"])
        self.service.scheduled_delivery(due["day"], True, due["generation"])
        self.command("off")
        self.server.requests(clear=True)
        second = self.command("on")
        self.assertGreater(len(self.server.requests()), 0, "on refreshes even after today's report was formed")
        self.assertIsNone(self.service.scheduled_tick(), "already sent channel is not redelivered")
        self.server.requests(clear=True)
        self.assertIn("已开启", self.command("on")["text"])
        self.assertEqual([], self.server.requests())

    def test_on_refreshes_and_delivers_only_pending_formed_report(self):
        from cc_telegram import TelegramBot
        with patch("cc_schedule.secrets.randbelow", return_value=0):
            self.service.execute(1234, "schedule", ["22:00", "UTC"], "telegram", "setting")
        formed = self.service.scheduled_tick()
        self.assertIsNotNone(formed)
        self.command("off")
        sent = []
        bot = TelegramBot(self.service, self.service.secrets.telegram_bot_token)
        with patch.object(bot, "request", side_effect=lambda method, payload: sent.append((method, payload))):
            bot._execute_command(1234, "on", [], "switch")
        texts = [payload["text"] for method, payload in sent if method == "sendMessage"]
        self.assertTrue(any("立即采集" in text for text in texts))
        self.assertTrue(any("Canvas 规则日报" in text for text in texts))
        self.assertEqual("sent", jload(self.service.report_path)["daily_plans"][formed["day"]]["delivery"]["telegram"]["state"])
        self.server.requests(clear=True)
        bot._scheduled_once()
        self.assertIsNone(self.service.scheduled_tick())
        self.assertEqual([], self.server.requests())

    def test_on_before_due_delivers_daily_report_immediately(self):
        from cc_telegram import TelegramBot
        with patch("cc_schedule.secrets.randbelow", return_value=0):
            self.service.execute(1234, "schedule", ["23:55", "UTC"], "telegram", "setting")
        self.command("off")
        sent = []
        bot = TelegramBot(self.service, self.service.secrets.telegram_bot_token)
        with patch.object(bot, "request", side_effect=lambda method, payload: sent.append((method, payload))):
            bot._execute_command(1234, "on", [], "early")
        self.assertTrue(any("Canvas 规则日报" in p["text"] for m, p in sent if m == "sendMessage"))
        plan = jload(self.service.report_path)["daily_plans"]["2026-03-24"]
        self.assertEqual("sent", plan["delivery"]["telegram"]["state"])
        self.assertIsNone(self.service.scheduled_tick())

    def test_old_on_worker_cannot_reply_after_off(self):
        from concurrent.futures import ThreadPoolExecutor
        from cc_telegram import TelegramBot
        import threading
        self.command("off")
        entered, release = threading.Event(), threading.Event()
        original = SecureCanvas.fetch
        def pause(api, url, accept="application/json"):
            result = original(api, url, accept)
            if "/api/v1/users/self" in url and not entered.is_set():
                entered.set()
                self.assertTrue(release.wait(5))
            return result
        sent = []
        bot = TelegramBot(self.service, self.service.secrets.telegram_bot_token)
        with patch.object(bot, "request", side_effect=lambda method, payload: sent.append((method, payload))), patch.object(SecureCanvas, "fetch", pause), ThreadPoolExecutor(2) as pool:
            old = pool.submit(bot._execute_command, 1234, "on", [], "old")
            self.assertTrue(entered.wait(5))
            bot._execute_command(1234, "off", [], "off")
            count = len(sent)
            release.set()
            old.result(timeout=5)
        self.assertEqual(count, len(sent))
        self.assertIn("服务：已关闭", self.command("status")["text"])

    def test_help_states_refresh_availability_when_off(self):
        self.command("off")
        help_text = self.command("help")["text"]
        refresh = next(line for line in help_text.splitlines() if line.startswith("/canvas refresh"))
        self.assertIn("开启", refresh)
        self.assertIn("关闭", next(line for line in help_text.splitlines() if line.startswith("/canvas report")))

    def test_queued_on_before_off_does_not_reopen_or_send(self):
        from concurrent.futures import ThreadPoolExecutor
        from cc_telegram import TelegramBot
        import threading
        entered, release = threading.Event(), threading.Event()
        def block():
            entered.set()
            self.assertTrue(release.wait(5))
        sent = []
        bot = TelegramBot(self.service, self.service.secrets.telegram_bot_token)
        update = {"update_id": 41, "message": {"from": {"id": 1234}, "chat": {"id": 1234, "type": "private"}, "text": "/canvas on"}}
        with patch.object(bot, "request", side_effect=lambda method, payload: sent.append((method, payload))), ThreadPoolExecutor(1) as pool:
            blocked = pool.submit(block)
            self.assertTrue(entered.wait(5))
            bot.handle_update(update, pool)
            bot._execute_command(1234, "off", [], "off")
            count = len(sent)
            release.set()
            blocked.result(timeout=5)
        self.assertEqual(count, len(sent))
        self.assertIn("服务：已关闭", self.command("status")["text"])
        self.assertEqual([], self.server.requests())

    def test_duplicate_on_result_waiting_to_send_is_invalidated(self):
        from concurrent.futures import ThreadPoolExecutor
        from cc_telegram import TelegramBot
        import threading
        from cc_store import FileLock
        returned = threading.Event()
        original = self.service.execute
        def observe(*args, **kwargs):
            result = original(*args, **kwargs)
            if kwargs.get("command") == "on":
                returned.set()
            return result
        sent = []
        bot = TelegramBot(self.service, self.service.secrets.telegram_bot_token)
        lock = FileLock(os.path.join(self.home.archive, "service-telegram-delivery.lock"))
        lock.acquire()
        try:
            with patch.object(bot, "request", side_effect=lambda method, payload: sent.append((method, payload))), patch.object(self.service, "execute", observe), ThreadPoolExecutor(1) as pool:
                old = pool.submit(bot._execute_command, 1234, "on", [], "duplicate")
                self.assertTrue(returned.wait(5))
                self.command("off")
                lock.release()
                old.result(timeout=5)
        finally:
            lock.release()
        self.assertEqual([], sent)

    def test_current_on_failure_replies_but_superseded_failure_does_not(self):
        from cc_telegram import TelegramBot
        self.command("off")
        sent = []
        bot = TelegramBot(self.service, self.service.secrets.telegram_bot_token)
        with patch.object(bot, "request", side_effect=lambda method, payload: sent.append((method, payload))), patch.object(self.service, "_collect", side_effect=RuntimeError("synthetic failure")):
            bot._execute_command(1234, "on", [], "failure")
        self.assertTrue(sent, "current failed on must reply")

if __name__ == "__main__":
    unittest.main()
