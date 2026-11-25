import json
import tempfile
import unittest
from pathlib import Path

from _fixture import ROOT, make_session_db
from imms import db, gm, retro, runtime


class TestRetroEvaluation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn, self.session = make_session_db(Path(self.tmp.name) / "t.db")
        self.sid = self.session["session_id"]
        scripted = json.loads((ROOT / "examples" / "scripts" / "user_script.json").read_text(encoding="utf-8"))
        runtime.run_session(self.conn, self.sid, 2, scripted, 42, "template")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_metrics_follow_lockedroom_formula(self):
        report = retro.evaluate(self.conn, self.sid)
        m = report["metrics"]
        for key in ("leak_rate", "clue_coverage", "killer_correct", "motive_correct",
                    "score", "grade", "difficulty", "character_replies"):
            self.assertIn(key, m)
        expected = round(max(
            0.0,
            100.0 * (0.45 * m["clue_coverage"] + 0.35 * float(m["killer_correct"])
                     + 0.20 * float(m["motive_correct"])) - 20.0 * m["leak_rate"],
        ), 1)
        self.assertEqual(m["score"], expected)
        self.assertEqual(report["retrospective"]["solvability"]["verdict"], "solvable_unique")

    def test_render_contains_evaluation_section(self):
        report, markdown = retro.evaluate_and_render(self.conn, self.sid)
        self.assertIn("## 6. 回溯评估", markdown)
        self.assertIn("决策时点信息边界复核", markdown)
        self.assertIn("证据指向强度", markdown)
        self.assertIn("转折点 Top 5", markdown)
        self.assertIn("时间线检查", markdown)

    def test_branch_diff_attached_to_counterfactual(self):
        gm.create_branch(
            self.conn, self.sid, 2, label="分支",
            override_round=2, override_utterance="我只相信自己。",
            decision={"policy": "information"},
        )
        report = retro.evaluate(self.conn, self.sid)
        branches = report["retrospective"]["counterfactual"]["branches"]
        self.assertEqual(len(branches), 1)
        self.assertIn("outcome_changed", branches[0]["summary"])
        _, markdown = retro.evaluate_and_render(self.conn, self.sid)
        self.assertIn("分支 ", markdown)

    def test_motive_override_is_scored(self):
        motive = "t_drug"
        report = retro.evaluate(self.conn, self.sid, scripted_motive=motive)
        self.assertTrue(report["metrics"]["motive_correct"])
        self.assertGreaterEqual(report["metrics"]["score"], 0.0)


if __name__ == "__main__":
    unittest.main()
