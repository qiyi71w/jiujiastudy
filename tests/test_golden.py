"""Four-school CLI contracts: exits, artifacts, calendar boundaries and Canvas reads."""
import difflib
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness  # noqa: E402


def _diff(expected, actual, label):
    a, b = harness.as_text(expected), harness.as_text(actual)
    lines = list(difflib.unified_diff(a.splitlines(), b.splitlines(), "golden/" + label, "actual/" + label,
                                      lineterm="", n=2))
    return "\n".join(lines[:80]) + ("\n..." if len(lines) > 80 else "")


class _Golden:
    scenario = None

    @classmethod
    def setUpClass(cls):
        cls.golden = harness.load_golden(cls.scenario)
        cls.actual = harness.run_scenario(cls.scenario)

    def test_steps(self):
        got = {s["id"]: s for s in self.actual["steps"]}
        for exp in self.golden["steps"]:
            with self.subTest(step=exp["id"]):
                act = got.get(exp["id"])
                self.assertIsNotNone(act, "step did not run")
                self.assertEqual(exp["exit"], act["exit"], "exit code; stderr:\n" + act["stderr"][-1500:])
        self.assertEqual([s["id"] for s in self.golden["steps"]], [s["id"] for s in self.actual["steps"]])

    def test_artifacts(self):
        act = self.actual["artifacts"]
        self.assertEqual(sorted(self.golden["artifacts"]), sorted(act), "set of written files changed")
        plan = act["plan.json"]
        generated = dt.date.fromisoformat(plan["generated"])
        monday = generated - dt.timedelta(days=generated.weekday())
        self.assertEqual([(monday + dt.timedelta(days=i)).isoformat() for i in range(7)],
                         [day["date"] for day in plan["days"]])
        for day in plan["days"]:
            with self.subTest(date=day["date"]):
                self.assertLessEqual(len(day["should"]), 2)
                if dt.date.fromisoformat(day["date"]) < generated:
                    self.assertIsNone(day["must_item_id"])
                    self.assertEqual([], day["should"])

    def test_requests(self):
        act = [f"{s['id']} {q}" for s in self.actual["steps"] for q in s["requests"]]
        if act != self.golden["requests"]:
            self.fail("Canvas requests changed\n" + _diff("\n".join(self.golden["requests"]), "\n".join(act), "requests.txt"))

    def test_only_known_endpoints(self):
        self.assertEqual([], [(s["id"], p) for s in self.actual["steps"] for p in s["unmatched"]])


for _name in harness.SCENARIOS:
    _cls = type(f"Golden_{_name}", (_Golden, unittest.TestCase), {"scenario": _name})
    globals()[_cls.__name__] = _cls
del _cls, _name

if __name__ == "__main__":
    unittest.main()
