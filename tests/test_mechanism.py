"""汎用部品のテスト。ダミーのデータだけで動く（本物のデータ・通信・個人情報は使わない）。"""
import json
import pathlib
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import ai_edit  # noqa: E402
import dayplan  # noqa: E402
import dev_agent  # noqa: E402
import float_geom  # noqa: E402
import my_events  # noqa: E402
import review  # noqa: E402
import tasks_io  # noqa: E402

JST = timezone(timedelta(hours=9))


def task(i, mins):
    return {"id": i, "title": i, "course": "X", "due": "2099-01-01T10:00:00+09:00", "steps": ["x"], "steps_src": {"minutes": [mins]}}


class Plan(unittest.TestCase):
    def test_tasks_go_into_the_gaps_between_fixed_events_on_5_minute_marks(self):
        now = datetime(2026, 10, 1, 9, 0, tzinfo=JST)
        busy = [{"start": now.replace(hour=10), "end": now.replace(hour=11), "title": "固定"}]
        plan = dayplan.make_plan(now, busy, [task("A", 30), task("B", 30)])
        for b in plan["blocks"]:
            self.assertEqual(int(b["start"][3:]) % 5, 0)
            self.assertFalse(b["start"] < "11:00" and b["end"] > "10:00" and b.get("kind") != "fixed")  # 固定予定と重ならない

    def test_a_after_b_is_after_in_time(self):
        now = datetime(2026, 10, 1, 9, 0, tzinfo=JST)
        busy = [{"start": now.replace(hour=10), "end": now.replace(hour=11), "title": "固定"}]
        plan = dayplan.make_plan(now, busy, [task("A", 30), task("B", 120)], overrides={"A": {"after": "B"}})
        by = {b["task"]: b for b in plan["blocks"]}
        self.assertGreaterEqual(by["A"]["start"], by["B"]["end"])


class AiOps(unittest.TestCase):
    def test_validation_is_all_or_nothing(self):
        good = {"op": "replan"}
        self.assertEqual(ai_edit.validate([good])[1], [])
        self.assertTrue(ai_edit.validate([good, {"op": "no_such_op"}])[1])
        self.assertEqual(ai_edit.validate([good, {"op": "no_such_op"}])[0], [])  # 1つでも不正なら全部使わない

    def test_a_question_does_not_change_anything(self):
        self.assertFalse(ai_edit.intent_ok("replan", "今日の予定はどうなってる？"))
        self.assertFalse(ai_edit.intent_ok("replan", "予定は組まないで"))
        self.assertTrue(ai_edit.intent_ok("replan", "予定を組んで"))

    def test_event_kinds_are_checked(self):
        ok = {"op": "event_create", "title": "友達と会う", "start": "2026-10-05T15:00:00+09:00", "end": "2026-10-05T17:00:00+09:00", "kind": "personal"}
        self.assertEqual(ai_edit.validate([ok])[1], [])
        self.assertTrue(ai_edit.validate([{**ok, "kind": "other"}])[1])
        self.assertTrue(ai_edit.validate([{**ok, "start": "2026-10-05T15:00:00"}])[1])  # タイムゾーンが要る


class Review(unittest.TestCase):
    def test_paraphrase_merges_but_numbered_series_do_not(self):
        self.assertTrue(review.similar("第1章の要約を書いて提出する", "第1章について要約を書き提出する"))
        for a, b in (("Journal #3", "Journal #4"), ("第3章を読む", "第5章を読む")):
            self.assertFalse(review.similar(a, b))


class SafeStorage(unittest.TestCase):
    def test_atomic_write_and_locked_json(self):
        d = pathlib.Path(tempfile.mkdtemp())
        tasks_io.write_json_atomic(d / "a.json", {"x": 1})
        self.assertEqual(json.loads((d / "a.json").read_text()), {"x": 1})
        with tasks_io.locked_json(d / "b.json", {"n": 0}) as data:
            data["n"] += 1
        with tasks_io.locked_json(d / "b.json", {"n": 0}) as data:
            self.assertEqual(data["n"], 1)

    def test_a_broken_file_is_not_overwritten(self):
        d = pathlib.Path(tempfile.mkdtemp())
        (d / "c.json").write_text("{broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            with tasks_io.locked_json(d / "c.json", {}):
                pass
        self.assertEqual((d / "c.json").read_text(), "{broken")


class Events(unittest.TestCase):
    def test_events_have_kinds_and_resync_after_restore(self):
        d = pathlib.Path(tempfile.mkdtemp())
        with mock.patch.object(my_events, "FILE", d / "e.json"):
            eid = my_events.add("会う", "2026-10-05T15:00:00+09:00", "2026-10-05T16:00:00+09:00", "personal")
            with self.assertRaises(ValueError):
                my_events.add("x", "2026-10-05T16:00:00+09:00", "2026-10-05T15:00:00+09:00", "personal")  # 終わりが先
            rec = my_events.remove(eid)
            my_events.restore(rec)
            self.assertEqual(my_events.get(eid)["sync"], "local")


class DevGate(unittest.TestCase):
    def test_protected_and_risky_paths(self):
        blocked, risky = dev_agent.classify(["data/x.json", "src/dayplan.py", "tests/_isolate.py", "../etc"])
        self.assertIn("data/x.json", blocked)
        self.assertIn("../etc", blocked)

    def test_dangerous_additions_need_a_reviewer(self):
        self.assertTrue(dev_agent.diff_risk("+import subprocess\n")[0])
        self.assertTrue(dev_agent.diff_risk("-    self.assertTrue(a)\n+    pass\n")[0])
        self.assertFalse(dev_agent.diff_risk("+x = 1\n")[0])


class Geometry(unittest.TestCase):
    def test_touching_the_top_or_bottom_edge_in_the_middle_opens(self):
        box = (0, 25, 1000, 700)
        self.assertEqual(float_geom.edge_zone(300, 25, 400, 70, box, 800), "TC")
        self.assertEqual(float_geom.edge_zone(300, 730, 400, 70, box, 800), "BC")
        self.assertIsNone(float_geom.edge_zone(10, 25, 100, 70, box, 800))  # 左の隅は開かない


if __name__ == "__main__":
    unittest.main()
