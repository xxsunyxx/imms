import copy
import json
import tempfile
import unittest
from pathlib import Path

from _fixture import ROOT, make_session_db
from imms import db, replay, retro, runtime, scoring


def _scripted(**overrides):
    data = json.loads((ROOT / "examples" / "scripts" / "user_script.json").read_text(encoding="utf-8"))
    data.update(overrides)
    return data


class TestScoring(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn, self.session = make_session_db(Path(self.tmp.name) / "t.db")
        self.sid = self.session["session_id"]
        self.onto = db.get_script(self.conn, self.session["script_id"])

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _install_structured_goals(self):
        onto = copy.deepcopy(self.onto)
        onto["endings"] = [
            {"id": "justice", "condition": {"solved": True},
             "text": "凶手落网，雪夜终见天光", "priority": 2},
            {"id": "wrongful", "condition": {"solved": False},
             "text": "真凶脱身，冤案落幕", "priority": 2},
        ]
        for char in onto["characters"]:
            if char["id"] == "zhou_muchen":
                char["goals"] = [
                    {"text": "把用药的事藏住",
                     "check": {"type": "secret", "fact_ids": ["t_drug"]}},
                    {"text": "别被指认出来", "check": {"type": "escape"}},
                ]
            elif char["id"] == "lin_wanqiu":
                char["goals"] = [
                    {"text": "找出真凶", "check": {"type": "identify"}},
                    "守住了自己的秘密",
                ]
        db.save_script(self.conn, onto)

    def test_structured_ending_and_goal_verdicts(self):
        self._install_structured_goals()
        result = runtime.run_session(self.conn, self.sid, 2, _scripted(), 42, "template")
        report = db.get_game_state(self.conn, self.sid, "scoring")
        expected = "justice" if result["result"]["solved"] else "wrongful"
        self.assertEqual(report["ending"]["code"], expected)
        self.assertEqual(report["ending"]["source"], "script_condition")
        self.assertTrue(report["ending"]["text"])

        culprit = report["goals"]["per_seat"]["ai:zhou_muchen"]
        escape = next(g for g in culprit["goals"] if g["id"] == "goal_2")
        self.assertEqual(
            escape["status"], "unmet" if result["result"]["solved"] else "met"
        )
        human = report["goals"]["per_seat"]["human:lin_wanqiu"]
        plain = next(g for g in human["goals"] if g["text"] == "守住了自己的秘密")
        self.assertEqual(plain["status"], "unscored")
        self.assertIn("可机读", plain["reason"])

    def test_public_ending_has_no_internal_ids_and_private_goals_stay_private(self):
        self._install_structured_goals()
        runtime.run_session(self.conn, self.sid, 2, _scripted(), 42, "template")
        public = [e for e in db.visible_game_events(self.conn, self.sid, "public")
                  if e.get("act_type") == "ending_settled"]
        self.assertEqual(len(public), 1)
        raw_public = json.dumps(public[0]["payload_json"], ensure_ascii=False)
        self.assertNotIn("ai:", raw_public)
        self.assertNotIn("human:", raw_public)

        rows = [dict(r) for r in self.conn.execute(
            "SELECT payload_json, visibility_json FROM game_events WHERE session_id=? "
            "AND act_type='goal_settled' ORDER BY seq", (self.sid,),
        )]
        self.assertEqual(len(rows), 5)
        for row in rows:
            payload = json.loads(row["payload_json"])
            visibility = json.loads(row["visibility_json"])
            self.assertIn("gm:host", visibility)
            self.assertIn(payload["seat"], visibility)
            self.assertEqual(len(visibility), 2)

    def test_plain_text_goals_are_never_guessed(self):
        runtime.run_session(self.conn, self.sid, 2, _scripted(), 42, "template")
        report = scoring.evaluate(self.conn, self.sid)
        for seat, item in report["goals"]["per_seat"].items():
            for goal in item["goals"]:
                self.assertEqual(goal["status"], "unscored", (seat, goal))
        for item in report["scores"]["ai"].values():
            self.assertIsNone(item["components"]["goal"])

    def test_condition_priority_and_unknown_key_warning(self):
        people = {
            "seat_ids": ["ai:a"], "char_to_seat": {"a": "ai:a"},
            "seat_to_char": {"ai:a": "a"}, "names": {"a": "甲"},
            "seat_to_name": {"ai:a": "甲"},
        }
        ctx = {
            "result": {"solved": False, "winner": "ai:a", "tie": False},
            "motive_correct": False, "clue_coverage": 0.5,
            "goal_completion": 0.2, "violation_rate": 0.0,
        }
        onto = {"endings": [
            {"id": "low", "condition": {"solved": True}, "text": "低优先级", "priority": 1},
            {"id": "high", "condition": {"solved": False}, "text": "高优先级", "priority": 9},
            {"id": "unknown", "condition": {"nonsense": 1}, "text": "无效", "priority": 99},
        ]}
        ending = scoring.resolve_ending(onto, ctx, people)
        self.assertEqual(ending["code"], "high")
        self.assertTrue(any("未知条件字段" in w for w in ending["warnings"]))

        legacy = scoring.resolve_ending({"endings": ["正确", "错误"]}, ctx, people)
        self.assertEqual(legacy["code"], "wrongful_conviction")
        self.assertEqual(legacy["text"], "错误")

    def test_score_coverage_and_render_sections(self):
        self._install_structured_goals()
        runtime.run_session(self.conn, self.sid, 2, _scripted(), 42, "template")
        report = scoring.evaluate(self.conn, self.sid)
        for item in report["scores"]["ai"].values():
            self.assertGreater(item["coverage"], 0.0)
            if item["score"] is not None:
                self.assertGreaterEqual(item["score"], 0.0)
                self.assertLessEqual(item["score"], 100.0)
        text = scoring.render(report)
        self.assertIn("结局与个人目标结算", text)
        self.assertIn("F27", text)
        self.assertIn("玩家六维评分", text)

        replay_text = replay.build_replay(self.conn, self.sid)
        self.assertIn("## 11. 结局与个人目标结算", replay_text)
        evaluation, md = retro.evaluate_and_render(self.conn, self.sid)
        self.assertIn("scoring", evaluation["retrospective"])
        self.assertIn("结局与个人目标评分", md)
        self.assertEqual(evaluation["metrics"]["score"], retro.compute_metrics(
            self.onto, replay._events(self.conn, self.sid),
            db.get_game_state(self.conn, self.sid, "result", {}),
            retro._human_seats(self.session),
        )["score"])


if __name__ == "__main__":
    unittest.main()
