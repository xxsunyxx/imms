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
