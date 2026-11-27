import json
import tempfile
import unittest
from pathlib import Path

from _fixture import ROOT, make_session_db
from imms import replay, runtime


class TestReplay(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn, self.session = make_session_db(Path(self.tmp.name) / "t.db")
        scripted = json.loads((ROOT / "examples" / "scripts" / "user_script.json").read_text(encoding="utf-8"))
        runtime.run_session(
            self.conn, self.session["session_id"], 2, scripted, 42, "template"
        )
        self.text = replay.build_replay(self.conn, self.session["session_id"])

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_all_five_sections(self):
        for title in replay.SECTIONS:
            self.assertIn(title, self.text)
        self.assertIn("周慕辰", self.text)

    def test_transcript_is_chronological_and_names_every_speaker(self):
        self.assertIn("## 对局实录（按轮）", self.text)
        transcript = self.text.split("## 1.")[0]
        self.assertIn("### 第 1 轮", transcript)
        self.assertIn("**周慕辰", transcript)
        self.assertIn("**林晚秋", transcript)
        if "**GM · 场景叙述**" in transcript:
            self.assertLess(transcript.index("**GM · 场景叙述**"),
                            transcript.index("**周慕辰"))
        self.assertIn("### 投票", transcript)
        self.assertNotIn("（本轮无公开发言）", transcript)

    def test_gm_line_precedes_seat_lines_when_present(self):
        from imms import db
        sid = self.session["session_id"]
        db.append_game_event(self.conn, sid, 1, "gm:host", "gm_narration",
                             {"narration": "灯芯轻轻一晃，桌上没人先开口。",
                              "status": "narrated", "tone": "克制"}, ["public"])
        text = replay.build_replay(self.conn, sid).split("## 1.")[0]
        self.assertLess(text.index("**GM · 场景叙述**"), text.index("**林晚秋"))

    def test_vote_section_lists_voter_to_choice(self):
        transcript = self.text.split("## 对局实录（按轮）")[1].split("## 1.")[0]
        self.assertIn("→", transcript)
        self.assertIn("票数：", transcript)

    def test_dm_debrief_sits_after_transcript_and_is_bounded(self):
        sid = self.session["session_id"]
        text = replay.build_replay(self.conn, sid)
        self.assertIn("## 主持复盘（DM 讲述）", text)
        self.assertLess(text.index("### 投票"), text.index("## 主持复盘（DM 讲述）"))
        self.assertLess(text.index("## 主持复盘（DM 讲述）"), text.index("## 1."))
        block = text.split("## 主持复盘（DM 讲述）")[1].split("## 1.")[0]
        self.assertIn("留给桌上的两个问题", block)
        self.assertIn("**先看人，再看手法。**", block)  # solved branch
        self.assertNotIn("f_", block)

    def test_debrief_wrong_vote_starts_with_method(self):
        from imms import db, gm_debrief
        sid = self.session["session_id"]
        onto = db.get_script(self.conn, self.session["script_id"])
        wrong = {"solved": False, "winner": "someone", "tie": False}
        lines = gm_debrief.debrief(self.conn, sid, onto, wrong)
        self.assertTrue(any("手法和时间" in x for x in lines))
        self.assertFalse(any("先看人" in x for x in lines))

    def test_turning_points_from_formula_audit(self):
        section = self.text.split("## 3.")[1].split("## 4.")[0]
        self.assertIn("suspicion", section)
        self.assertIn("→", section)

    def test_counterfactual_shows_numeric_change(self):
        section = self.text.split("## 5.")[1]
        self.assertIn("S=", section)
        self.assertIn("→", section)


if __name__ == "__main__":
    unittest.main()
