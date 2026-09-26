"""Unit tests for structured AI analysis parsing and Telegram rendering.

Tests boundary conditions:
- JSON parsing with/without code fences.
- Schema, field type, and length constraints.
- Rejection of unknown, missing, duplicate announcement IDs.
- Rejection of model-forged source, title, or unexpected keys.
- Action extraction validation: 0..3 actions, strict fields, real evidence substring, timezone offset.
- Clean cutover rejection of legacy announcements without actions.
- Output enrichment with trusted input metadata and validated actions.
- Telegram rendering without empty announcement sections.
"""
import io
import json
import os
import sys
import unittest
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from cc_ai import analyze, render_analysis
from cc_service_security import SecureCanvas


class _FakeHTTPResponse:
    def __init__(self, data: bytes, content_type="application/json", chunk_size=None):
        self._data = io.BytesIO(data)
        self.headers = {"Content-Type": content_type}
        self.chunk_size = chunk_size

    def read(self, n=None):
        return self._data.read(min(n, self.chunk_size) if self.chunk_size and n else n)

    def read1(self, n=None):
        return self.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _mock_open_with_content(content_str: str):
    payload = {
        "choices": [
            {
                "message": {
                    "content": content_str
                }
            }
        ]
    }
    raw_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return _FakeHTTPResponse(raw_bytes)


def _sse(*events, chunk_size=1):
    wire = b"".join(b"data: " + (event if isinstance(event, bytes) else json.dumps(event, ensure_ascii=False).encode("utf-8")) + b"\r\n\r\n" for event in events)
    return _FakeHTTPResponse(wire, "text/event-stream; charset=utf-8", chunk_size)


def _chunk(content, reason=None):
    return {"choices": [{"delta": {"content": content}, "finish_reason": reason}]}

class TestStructuredAI(unittest.TestCase):
    def setUp(self):
        self.endpoint = "https://api.openai.com"
        self.key = "test-key"
        self.model = "test-model"
        self.snapshot = {
            "as_of": "2026-09-23T10:00:00Z",
            "timezone": "America/New_York",
            "assignments": [
                {
                    "course": "MATH101",
                    "name": "Homework 1",
                    "due_at": "2026-09-25T23:59:00Z",
                    "sub_state": "unsubmitted",
                    "html_url": "https://canvas.example.edu/courses/101/assignments/1",
                    "completed": False,
                    "reminder_stopped": False,
                }
            ],
        }

    def test_valid_json_response_without_announcements(self):
        ai_resp = json.dumps({
            "summary": "近期有数学作业待提交，建议优先完成。",
            "announcements": [],
            "next_step": "今晚开始做 Homework 1 第一题。"
        }, ensure_ascii=False)

        with patch("urllib.request.OpenerDirector.open", return_value=_mock_open_with_content(ai_resp)):
            result = analyze(self.endpoint, self.key, self.model, self.snapshot)

        self.assertIsInstance(result, dict)
        self.assertEqual("近期有数学作业待提交，建议优先完成。", result["summary"])
        self.assertEqual([], result["announcements"])
        self.assertEqual("今晚开始做 Homework 1 第一题。", result["next_step"])

        rendered = render_analysis(result)
        self.assertIn(result["summary"], rendered)
        self.assertIn(result["next_step"], rendered)
        self.assertNotIn("公告", rendered)
        self.assertTrue(rendered.endswith(result["next_step"]))

    def test_valid_json_response_with_announcements_and_enrichment(self):
        announcements_input = [
            {
                "id": "101:ann1",
                "course": "MATH101",
                "title": "Quiz Update",
                "text": "Quiz 1 is scheduled for Friday.",
                "source": "https://canvas.example.edu/courses/101/announcements/1",
            },
            {
                "id": "102:ann2",
                "course": "CS102",
                "title": "Lab Guidelines",
                "text": "Please bring your laptop.",
                "source": "https://canvas.example.edu/courses/102/announcements/2",
            }
        ]
        ai_resp = json.dumps({
            "summary": "数学测验与计算机实验公告已发布。",
            "announcements": [
                {
                    "id": "101:ann1",
                    "analysis": "周五进行测验，日期待确认，需准备计算器。",
                    "actions": [
                        {
                            "title": "准备 Quiz 1",
                            "first_step": "查阅 Quiz 1 复习大纲并准备计算器",
                            "due_at": "2026-09-25T15:00:00+08:00",
                            "uncertainty": "具体时刻与地点待确认",
                            "evidence": "Quiz 1 is scheduled for Friday.",
                        }
                    ]
                },
                {
                    "id": "102:ann2",
                    "analysis": "实验课要求自带笔记本电脑。",
                    "actions": [
                        {
                            "title": "准备自带笔记本电脑",
                            "first_step": "检查笔记本电脑电量及实验软件",
                            "due_at": None,
                            "uncertainty": "具体截止时间待确认",
                            "evidence": "Please bring your laptop.",
                        }
                    ]
                }
            ],
            "next_step": "查阅 Quiz 1 复习大纲并准备计算器。"
        }, ensure_ascii=False)

        with patch("urllib.request.OpenerDirector.open", return_value=_mock_open_with_content(ai_resp)):
            result = analyze(self.endpoint, self.key, self.model, self.snapshot, announcements=announcements_input)

        self.assertEqual(2, len(result["announcements"]))
        first_ann = result["announcements"][0]
        self.assertEqual("101:ann1", first_ann["id"])
        self.assertEqual("MATH101", first_ann["course"])
        self.assertEqual("Quiz Update", first_ann["title"])
        self.assertEqual("https://canvas.example.edu/courses/101/announcements/1", first_ann["source"])
        self.assertEqual("周五进行测验，日期待确认，需准备计算器。", first_ann["analysis"])
        self.assertEqual(1, len(first_ann["actions"]))
        self.assertEqual("准备 Quiz 1", first_ann["actions"][0]["title"])
        self.assertEqual("查阅 Quiz 1 复习大纲并准备计算器", first_ann["actions"][0]["first_step"])
        self.assertEqual("2026-09-25T15:00:00+08:00", first_ann["actions"][0]["due_at"])
        self.assertEqual("具体时刻与地点待确认", first_ann["actions"][0]["uncertainty"])
        self.assertEqual("Quiz 1 is scheduled for Friday.", first_ann["actions"][0]["evidence"])

        second_ann = result["announcements"][1]
        self.assertEqual(1, len(second_ann["actions"]))
        self.assertIsNone(second_ann["actions"][0]["due_at"])
        self.assertEqual("Please bring your laptop.", second_ann["actions"][0]["evidence"])
        rendered = render_analysis(result)
        self.assertIn("MATH101 · Quiz Update", rendered)
        self.assertIn("来源：https://canvas.example.edu/courses/101/announcements/1", rendered)
        self.assertIn("CS102 · Lab Guidelines", rendered)
        self.assertIn("来源：https://canvas.example.edu/courses/102/announcements/2", rendered)
        self.assertTrue(rendered.endswith(result["next_step"]))

    def test_json_wrapped_in_markdown_code_fence(self):
        inner_json = json.dumps({
            "summary": "代码块包裹的有效摘要。",
            "announcements": [],
            "next_step": "按计划执行。"
        }, ensure_ascii=False)
        ai_resp = f"```json\n{inner_json}\n```"

        with patch("urllib.request.OpenerDirector.open", return_value=_mock_open_with_content(ai_resp)):
            result = analyze(self.endpoint, self.key, self.model, self.snapshot)
        self.assertEqual("代码块包裹的有效摘要。", result["summary"])

    def test_reject_unknown_announcement_id(self):
        announcements_input = [
            {"id": "101:ann1", "course": "MATH101", "title": "A1", "text": "T1", "source": ""}
        ]
        ai_resp = json.dumps({
            "summary": "摘要",
            "announcements": [{"id": "999:fake_id", "analysis": "伪造解析", "actions": []}],
            "next_step": "建议"
        }, ensure_ascii=False)

        with patch("urllib.request.OpenerDirector.open", return_value=_mock_open_with_content(ai_resp)):
            with self.assertRaises(ValueError):
                analyze(self.endpoint, self.key, self.model, self.snapshot, announcements=announcements_input)

    def test_reject_missing_announcement_id(self):
        announcements_input = [
            {"id": "101:ann1", "course": "MATH101", "title": "A1", "text": "T1", "source": ""},
            {"id": "102:ann2", "course": "CS102", "title": "A2", "text": "T2", "source": ""},
        ]
        ai_resp = json.dumps({
            "summary": "摘要",
            "announcements": [{"id": "101:ann1", "analysis": "只有一条", "actions": []}],
            "next_step": "建议"
        }, ensure_ascii=False)

        with patch("urllib.request.OpenerDirector.open", return_value=_mock_open_with_content(ai_resp)):
            with self.assertRaises(ValueError):
                analyze(self.endpoint, self.key, self.model, self.snapshot, announcements=announcements_input)

    def test_reject_duplicate_announcement_id_in_response(self):
        announcements_input = [
            {"id": "101:ann1", "course": "MATH101", "title": "A1", "text": "T1", "source": ""}
        ]
        ai_resp = json.dumps({
            "summary": "摘要",
            "announcements": [
                {"id": "101:ann1", "analysis": "第一条解析", "actions": []},
                {"id": "101:ann1", "analysis": "重复条目", "actions": []}
            ],
            "next_step": "建议"
        }, ensure_ascii=False)

        with patch("urllib.request.OpenerDirector.open", return_value=_mock_open_with_content(ai_resp)):
            with self.assertRaises(ValueError):
                analyze(self.endpoint, self.key, self.model, self.snapshot, announcements=announcements_input)

    def test_reject_forged_source_or_extra_keys_in_announcement(self):
        announcements_input = [
            {"id": "101:ann1", "course": "MATH101", "title": "A1", "text": "T1", "source": "https://canvas.edu/safe"}
        ]
        ai_resp = json.dumps({
            "summary": "摘要",
            "announcements": [
                {
                    "id": "101:ann1",
                    "analysis": "解析",
                    "actions": [],
                    "source": "https://evil.com/phishing"
                }
            ],
            "next_step": "建议"
        }, ensure_ascii=False)

        with patch("urllib.request.OpenerDirector.open", return_value=_mock_open_with_content(ai_resp)):
            with self.assertRaises(ValueError):
                analyze(self.endpoint, self.key, self.model, self.snapshot, announcements=announcements_input)

    def test_reject_extra_root_keys(self):
        ai_resp = json.dumps({
            "summary": "摘要",
            "announcements": [],
            "next_step": "建议",
            "unexpected_key": "bad"
        }, ensure_ascii=False)

        with patch("urllib.request.OpenerDirector.open", return_value=_mock_open_with_content(ai_resp)):
            with self.assertRaises(ValueError):
                analyze(self.endpoint, self.key, self.model, self.snapshot)

    def test_reject_oversized_fields(self):
        oversized_summary = "长" * 5001
        ai_resp = json.dumps({
            "summary": oversized_summary,
            "announcements": [],
            "next_step": "建议"
        }, ensure_ascii=False)

        with patch("urllib.request.OpenerDirector.open", return_value=_mock_open_with_content(ai_resp)):
            with self.assertRaises(ValueError):
                analyze(self.endpoint, self.key, self.model, self.snapshot)

    def test_reject_empty_fields(self):
        ai_resp = json.dumps({
            "summary": "   ",
            "announcements": [],
            "next_step": "建议"
        }, ensure_ascii=False)

        with patch("urllib.request.OpenerDirector.open", return_value=_mock_open_with_content(ai_resp)):
            with self.assertRaises(ValueError):
                analyze(self.endpoint, self.key, self.model, self.snapshot)

    def test_reject_malformed_json_and_non_object(self):
        with patch("urllib.request.OpenerDirector.open", return_value=_mock_open_with_content("Not a json at all")):
            with self.assertRaises(ValueError):
                analyze(self.endpoint, self.key, self.model, self.snapshot)

        with patch("urllib.request.OpenerDirector.open", return_value=_mock_open_with_content("[1, 2, 3]")):
            with self.assertRaises(ValueError):
                analyze(self.endpoint, self.key, self.model, self.snapshot)

    def test_reject_duplicate_id_in_input_announcements(self):
        duplicate_input = [
            {"id": "same_id", "course": "C1", "title": "T1", "text": "M1", "source": ""},
            {"id": "same_id", "course": "C2", "title": "T2", "text": "M2", "source": ""},
        ]
        with self.assertRaises(ValueError):
            analyze(self.endpoint, self.key, self.model, self.snapshot, announcements=duplicate_input)


    def test_reject_legacy_announcement_without_actions(self):
        announcements_input = [
            {"id": "101:ann1", "course": "MATH101", "title": "A1", "text": "T1", "source": ""}
        ]
        ai_resp = json.dumps({
            "summary": "摘要",
            "announcements": [{"id": "101:ann1", "analysis": "旧格式无 actions 字段"}],
            "next_step": "建议"
        }, ensure_ascii=False)

        with patch("urllib.request.OpenerDirector.open", return_value=_mock_open_with_content(ai_resp)):
            with self.assertRaises(ValueError):
                analyze(self.endpoint, self.key, self.model, self.snapshot, announcements=announcements_input)

    def test_announcement_empty_actions_is_valid(self):
        announcements_input = [
            {"id": "101:ann1", "course": "MATH101", "title": "Notice", "text": "No action required.", "source": "https://canvas.edu/a1"}
        ]
        ai_resp = json.dumps({
            "summary": "纯背景通知，无可行动项。",
            "announcements": [
                {
                    "id": "101:ann1",
                    "analysis": "该公告仅告知背景信息，无作业或任务要求。",
                    "actions": []
                }
            ],
            "next_step": "按原定计划复习。"
        }, ensure_ascii=False)

        with patch("urllib.request.OpenerDirector.open", return_value=_mock_open_with_content(ai_resp)):
            result = analyze(self.endpoint, self.key, self.model, self.snapshot, announcements=announcements_input)

        self.assertEqual(1, len(result["announcements"]))
        self.assertEqual([], result["announcements"][0]["actions"])

    def test_announcement_real_evidence_and_offset_due_at_valid(self):
        announcements_input = [
            {
                "id": "101:ann1",
                "course": "MATH101",
                "title": "Quiz Notice",
                "text": "Quiz 1 is scheduled\n   for Friday at 3pm.",
                "source": "https://canvas.edu/a1"
            }
        ]
        ai_resp = json.dumps({
            "summary": "周五有测验。",
            "announcements": [
                {
                    "id": "101:ann1",
                    "analysis": "周五下午3点进行 Quiz 1。",
                    "actions": [
                        {
                            "title": "参加 Quiz 1",
                            "first_step": "复习第1至3章内容",
                            "due_at": "2026-09-25T15:00:00-04:00",
                            "uncertainty": "教室具体地点待确认",
                            "evidence": "Quiz 1 is scheduled for Friday at 3pm.",
                        }
                    ]
                }
            ],
            "next_step": "复习讲义。"
        }, ensure_ascii=False)

        with patch("urllib.request.OpenerDirector.open", return_value=_mock_open_with_content(ai_resp)):
            result = analyze(self.endpoint, self.key, self.model, self.snapshot, announcements=announcements_input)

        self.assertEqual(1, len(result["announcements"]))
        ann = result["announcements"][0]
        self.assertEqual(1, len(ann["actions"]))
        self.assertEqual("2026-09-25T15:00:00-04:00", ann["actions"][0]["due_at"])
        self.assertEqual("Quiz 1 is scheduled for Friday at 3pm.", ann["actions"][0]["evidence"])

    def test_reject_fake_evidence(self):
        announcements_input = [
            {"id": "101:ann1", "course": "MATH101", "title": "Notice", "text": "Quiz 1 is scheduled for Friday.", "source": ""}
        ]
        ai_resp = json.dumps({
            "summary": "摘要",
            "announcements": [
                {
                    "id": "101:ann1",
                    "analysis": "解析",
                    "actions": [
                        {
                            "title": "任务",
                            "first_step": "第一步",
                            "due_at": None,
                            "uncertainty": "无",
                            "evidence": "Final Exam is scheduled for next Monday.",  # 伪造引用
                        }
                    ]
                }
            ],
            "next_step": "建议"
        }, ensure_ascii=False)

        with patch("urllib.request.OpenerDirector.open", return_value=_mock_open_with_content(ai_resp)):
            with self.assertRaises(ValueError):
                analyze(self.endpoint, self.key, self.model, self.snapshot, announcements=announcements_input)

    def test_reject_due_at_without_timezone_offset(self):
        announcements_input = [
            {"id": "101:ann1", "course": "MATH101", "title": "A1", "text": "Quiz is on Friday at 3pm.", "source": ""}
        ]
        invalid_due_dates = [
            "2026-09-25",               # 日期 only
            "2026-09-25T15:00:00",      # 无 offset (naive)
            "2026-09-25 15:00:00",      # 无 offset (naive)
            "2026-09-25+08:00",         # 日期 only 带 offset
            1234567890,                 # 非字符串
            True,                       # 非字符串
            "",                         # 空字符串
        ]
        for bad_due in invalid_due_dates:
            ai_resp = json.dumps({
                "summary": "摘要",
                "announcements": [
                    {
                        "id": "101:ann1",
                        "analysis": "解析",
                        "actions": [
                            {
                                "title": "任务",
                                "first_step": "第一步",
                                "due_at": bad_due,
                                "uncertainty": "无",
                                "evidence": "Quiz is on Friday at 3pm.",
                            }
                        ]
                    }
                ],
                "next_step": "建议"
            }, ensure_ascii=False)

            with patch("urllib.request.OpenerDirector.open", return_value=_mock_open_with_content(ai_resp)):
                with self.assertRaises(ValueError):
                    analyze(self.endpoint, self.key, self.model, self.snapshot, announcements=announcements_input)

    def test_reject_extra_source_field_in_action(self):
        announcements_input = [
            {"id": "101:ann1", "course": "MATH101", "title": "A1", "text": "Quiz is on Friday.", "source": ""}
        ]
        ai_resp = json.dumps({
            "summary": "摘要",
            "announcements": [
                {
                    "id": "101:ann1",
                    "analysis": "解析",
                    "actions": [
                        {
                            "title": "任务",
                            "first_step": "第一步",
                            "due_at": None,
                            "uncertainty": "无",
                            "evidence": "Quiz is on Friday.",
                            "source": "https://evil.com/fake",  # 额外 source 字段
                        }
                    ]
                }
            ],
            "next_step": "建议"
        }, ensure_ascii=False)

        with patch("urllib.request.OpenerDirector.open", return_value=_mock_open_with_content(ai_resp)):
            with self.assertRaises(ValueError):
                analyze(self.endpoint, self.key, self.model, self.snapshot, announcements=announcements_input)

    def test_reject_more_than_three_actions(self):
        announcements_input = [
            {"id": "101:ann1", "course": "MATH101", "title": "A1", "text": "Part 1 Part 2 Part 3 Part 4", "source": ""}
        ]
        four_actions = [
            {"title": f"Task {i}", "first_step": "Step", "due_at": None, "uncertainty": "None", "evidence": f"Part {i}"}
            for i in range(1, 5)
        ]
        ai_resp = json.dumps({
            "summary": "摘要",
            "announcements": [
                {
                    "id": "101:ann1",
                    "analysis": "解析",
                    "actions": four_actions,
                }
            ],
            "next_step": "建议"
        }, ensure_ascii=False)

        with patch("urllib.request.OpenerDirector.open", return_value=_mock_open_with_content(ai_resp)):
            with self.assertRaises(ValueError):
                analyze(self.endpoint, self.key, self.model, self.snapshot, announcements=announcements_input)

    def test_reject_oversized_action_fields(self):
        announcements_input = [
            {"id": "101:ann1", "course": "MATH101", "title": "A1", "text": "Valid text quote.", "source": ""}
        ]
        # title > 200
        ai_resp = json.dumps({
            "summary": "摘要",
            "announcements": [
                {
                    "id": "101:ann1",
                    "analysis": "解析",
                    "actions": [
                        {
                            "title": "长" * 201,
                            "first_step": "第一步",
                            "due_at": None,
                            "uncertainty": "无",
                            "evidence": "Valid text quote.",
                        }
                    ],
                }
            ],
            "next_step": "建议"
        }, ensure_ascii=False)

        with patch("urllib.request.OpenerDirector.open", return_value=_mock_open_with_content(ai_resp)):
            with self.assertRaises(ValueError):
                analyze(self.endpoint, self.key, self.model, self.snapshot, announcements=announcements_input)

    def test_render_analysis_missing_source(self):
        result = {
            "summary": "摘要",
            "announcements": [
                {
                    "id": "1",
                    "course": "MATH101",
                    "title": "无链接公告",
                    "analysis": "测试解析",
                    "source": "",
                }
            ],
            "next_step": "行动建议"
        }
        rendered = render_analysis(result)
        self.assertIn("来源：未提供", rendered)
        self.assertIn("MATH101 · 无链接公告", rendered)
        self.assertIn("行动建议", rendered)
    def test_streamed_preview_precedes_validated_result_and_excludes_untrusted_fields(self):
        announcement = {"id": "a", "course": "MATH101", "title": "Quiz", "text": "Friday quiz", "source": "https://canvas.edu/a"}
        content = json.dumps({"summary": "中文\\换行", "announcements": [{"id": "a", "analysis": "周五测验", "actions": []}],
                              "next_step": "复习"}, ensure_ascii=False)
        split = content.index("周五") + 1
        updates = []
        stream = _sse(_chunk(content[:split]), _chunk(content[split:]), _chunk("", "stop"),
                      {"choices": [], "usage": {"completion_tokens": 20}}, b"[DONE]")
        early = []
        def track(update):
            updates.append(update)
            if "preview" in update:
                early.append(stream._data.tell() < len(stream._data.getbuffer()))
        with patch("urllib.request.OpenerDirector.open", return_value=stream) as opened:
            result = analyze(self.endpoint, self.key, self.model, self.snapshot,
                             announcements=[announcement], progress=track)
        sent = json.loads(opened.call_args.args[0].data)
        self.assertTrue(sent["stream"])
        self.assertEqual({"include_usage": True}, sent["stream_options"])
        previews = [update["preview"] for update in updates if "preview" in update]
        self.assertTrue(any(early))
        self.assertTrue(previews)
        self.assertIn("摘要：中文\\换行", previews[-1])
        self.assertIn("公告分析：周五测验", previews[-1])
        self.assertIn("下一步：复习", previews[-1])
        self.assertNotIn("actions", previews[-1])
        self.assertEqual("周五测验", result["announcements"][0]["analysis"])
        self.assertEqual("validating", next(u["status"] for u in updates if u.get("status") == "validating"))
        self.assertEqual(20, next(u["output_tokens"] for u in updates if "output_tokens" in u))

    def test_stream_preview_preserves_split_escapes_and_supported_code_fence(self):
        summary = "路径 C:\\draft\\，字面 \\u1234，中文与\U0001D11E，结尾\\"
        content = "```json\n" + json.dumps({"summary": summary, "announcements": [], "next_step": "核对"}, ensure_ascii=True) + "\n```"
        updates = []
        response = _sse(*[_chunk(char) for char in content], _chunk("", "stop"), b"[DONE]", chunk_size=7)
        with patch("urllib.request.OpenerDirector.open", return_value=response):
            result = analyze(self.endpoint, self.key, self.model, self.snapshot, progress=updates.append)
        previews = [update["preview"] for update in updates if "preview" in update]
        self.assertEqual(summary, result["summary"])
        self.assertIn("摘要：" + summary, previews[-1])
        self.assertIn("下一步：核对", previews[-1])

    def test_preview_does_not_read_escaped_field_names_or_evidence(self):
        updates = []
        text = '{"summary":"safe \\u4e2d","bait":"\\\"next_step\\\":\\\"forged\\\"",' \
               '"announcements":[],"next_step":"real"}'
        with patch("urllib.request.OpenerDirector.open", return_value=_sse(_chunk(text), _chunk("", "stop"), b"[DONE]")):
            with self.assertRaises(ValueError):
                analyze(self.endpoint, self.key, self.model, self.snapshot, progress=updates.append)
        preview = "\n".join(u["preview"] for u in updates if "preview" in u)
        self.assertIn("safe 中", preview)
        self.assertNotIn("forged", preview)

    def test_stream_rejects_missing_done_truncation_refusal_and_transport_overflow(self):
        good = '{"summary":"好","announcements":[],"next_step":"做"}'
        cases = (
            _sse(_chunk(good), _chunk("", "stop")),
            _sse(_chunk(good), _chunk("", "length"), b"[DONE]"),
            _sse({"choices": [{"delta": {"refusal": "no"}, "finish_reason": "stop"}]}, b"[DONE]"),
            _sse(_chunk(good), {"error": {"message": "secret"}}, _chunk("", "stop"), b"[DONE]"),
            _sse(_chunk(good), b"[DONE]"),
            _FakeHTTPResponse(b":" + b"a" * (1024 * 1024 + 1) + b"\n\n", "text/event-stream"),
        )
        for response in cases:
            with self.subTest(response=response), patch("urllib.request.OpenerDirector.open", return_value=response):
                with self.assertRaises(ValueError):
                    analyze(self.endpoint, self.key, self.model, self.snapshot)
    def test_stream_rejects_incomplete_json_and_content_limit(self):
        cases = (
            _sse(_chunk('{"summary":"未完'), _chunk("", "stop"), b"[DONE]"),
            _sse(_chunk('{"summary":"' + "长" * 16001), _chunk("", "stop"), b"[DONE]", chunk_size=4096),
            _FakeHTTPResponse(b"data: \xff\n\n", "text/event-stream"),
        )
        for response in cases:
            with self.subTest(response=response), patch("urllib.request.OpenerDirector.open", return_value=response):
                with self.assertRaises(ValueError):
                    analyze(self.endpoint, self.key, self.model, self.snapshot)


    def test_revoke_during_stream_stops_before_more_preview(self):
        updates = []
        permit_valid = [True]
        first = _chunk('{"summary":"先到')
        second = _chunk('后到","announcements":[],"next_step":"行动"}')
        def progress(update):
            updates.append(update)
            if update.get("preview"):
                permit_valid[0] = False
        with patch("urllib.request.OpenerDirector.open", return_value=_sse(first, second, _chunk("", "stop"), b"[DONE]")):
            with self.assertRaisesRegex(ValueError, "stale"):
                analyze(self.endpoint, self.key, self.model, self.snapshot,
                        permit=lambda: permit_valid[0], progress=progress)
        self.assertEqual(1, len([u for u in updates if u.get("preview")]))

    def test_json_response_rejects_overflow_deadline_and_revocation(self):
        good = json.dumps({'choices': [{'message': {'content': json.dumps({
            'summary': 'safe', 'announcements': [], 'next_step': 'check'})}}]}).encode()
        with patch('urllib.request.OpenerDirector.open', return_value=_FakeHTTPResponse(b' ' * (1024 * 1024 + 1))):
            with self.assertRaisesRegex(ValueError, 'size limit'):
                analyze(self.endpoint, self.key, self.model, self.snapshot)
        now = [0]
        response = _FakeHTTPResponse(good)
        read = response.read1
        def slow_read(n):
            now[0] = 121
            return read(n)
        with patch.object(response, 'read1', side_effect=slow_read), \
                patch('time.perf_counter', side_effect=lambda: now[0]), \
                patch('urllib.request.OpenerDirector.open', return_value=response):
            with self.assertRaisesRegex(ValueError, 'deadline'):
                analyze(self.endpoint, self.key, self.model, self.snapshot)
        allowed = [True]
        response = _FakeHTTPResponse(good, chunk_size=1)
        read = response.read1
        def revoke(n):
            allowed[0] = False
            return read(n)
        with patch.object(response, 'read1', side_effect=revoke), \
                patch('urllib.request.OpenerDirector.open', return_value=response):
            with self.assertRaisesRegex(ValueError, 'stale'):
                analyze(self.endpoint, self.key, self.model, self.snapshot, permit=lambda: allowed[0])

    def test_canvas_rejects_oversize_page_and_pagination_total(self):
        canvas = SecureCanvas('https://canvas.example.edu', 'synthetic')
        with patch('cc_service_security._MAX_CANVAS_PAGE_BYTES', 8), \
                patch.object(canvas.opener, 'open', return_value=_FakeHTTPResponse(b' ' * 9)):
            with self.assertRaisesRegex(ValueError, 'size limit'):
                canvas.get('/api/v1/courses')
        pages = [_FakeHTTPResponse(b'[{"id":1}]'), _FakeHTTPResponse(b'[{"id":2}]')]
        pages[0].headers['Link'] = '<https://canvas.example.edu/api/v1/courses?page=2>; rel="next"'
        with patch('cc_service_security._MAX_CANVAS_TOTAL_BYTES', 15), \
                patch.object(canvas.opener, 'open', side_effect=pages):
            with self.assertRaisesRegex(ValueError, 'pagination exceeds size'):
                canvas.get('/api/v1/courses')

    def test_canvas_slow_response_does_not_retry_past_deadline(self):
        canvas = SecureCanvas('https://canvas.example.edu', 'synthetic')
        now = [0]
        response = _FakeHTTPResponse(b'[]')
        read = response.read1
        def slow_read(n):
            now[0] = 121
            return read(n)
        with patch.object(response, 'read1', side_effect=slow_read), \
                patch('time.perf_counter', side_effect=lambda: now[0]), \
                patch.object(canvas.opener, 'open', return_value=response) as opened:
            with self.assertRaisesRegex(ValueError, 'deadline'):
                canvas.get('/api/v1/courses')
        self.assertEqual(1, opened.call_count)



if __name__ == "__main__":
    unittest.main()
