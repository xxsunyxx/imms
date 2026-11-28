import tempfile
import unittest
from pathlib import Path

from _fixture import make_session_db
from imms import db, frame, journal, llm, replay, retro, runtime


class TestKillerFramingGuide(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn, self.session = make_session_db(Path(self.tmp.name) / "t.db")
        self.sid = self.session["session_id"]
        self.onto = db.get_script(self.conn, self.session["script_id"])
        self.killer = frame.killer_seat(self.session, self.onto)
        self.other = next(
            p["id"] for p in self.session["participants"]
            if p["kind"] != "gm" and p["id"] != self.killer
        )

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_guide_is_killer_only_and_uses_known_fragments(self):
        runtime.init_runtime(self.conn, self.session)
        state = db.get_game_state(self.conn, self.sid, "runtime")[self.killer]
        state["suspicion"][self.other] = 0.5
        db.set_game_state(self.conn, self.sid, "revealed_clues", [
            f"c_report 现场有可疑痕迹|logLR:{self.other}=1.5"
        ])
        db.append_game_event(
            self.conn, self.sid, 1, "gm:host", "contradiction_event",
            {
                "witness": self.killer, "subject": self.other,
                "strength": 0.7, "seq": 12,
            },
            ["gm:host", self.killer],
        )
        report = frame.evaluate(
            self.conn, self.sid, self.onto, self.session, state, 2
        )
        self.assertTrue(report["eligible"])
        self.assertEqual(report["targets"][0]["seat"], self.other)
        self.assertTrue(any("不一致" in s for s in report["targets"][0]["supports"]))

        other_state = db.get_game_state(self.conn, self.sid, "runtime")[self.other]
        self.assertFalse(frame.evaluate(
            self.conn, self.sid, self.onto, self.session, other_state, 2
        )["eligible"])

        guide = frame.build_guide(
            self.conn, self.sid, self.onto, self.session, state, 2
        )
        self.assertIsNotNone(guide)
        rows = journal.audit_events(self.conn, self.sid, act_type="frame_guidance")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["visibility"], ["gm:host", self.killer])
        self.assertNotIn(self.other, " ".join(guide["lines"]))
        self.assertNotIn("logLR", " ".join(guide["lines"]))

    def test_prompt_block_is_private_and_has_no_internal_ids(self):
        runtime.init_runtime(self.conn, self.session)
        state = db.get_game_state(self.conn, self.sid, "runtime")[self.killer]
        guide = {
            "lines": [
                "你的身份是真凶。把怀疑引向他人，但不得编造任何新事实、时间或物证。",
                "候选 1：林晚秋。依据：其供述已被记录为前后不一致。",
            ]
        }
        prompt = llm.build_prompt(
            {**state, "frame_guide": guide["lines"]}, "accuse", "林晚秋",
            "现有线索", 2, ["现有线索"],
        )
        self.assertIn("真凶专用", prompt)
        self.assertIn("林晚秋", prompt)
        self.assertNotIn(self.killer, prompt)
        self.assertNotIn("logLR", prompt)

        plain = llm.build_prompt(
            state, "accuse", "林晚秋", "现有线索", 2, ["现有线索"]
        )
        self.assertNotIn("真凶专用", plain)

    def test_runtime_only_writes_guidance_for_killer(self):
        import json
        from _fixture import ROOT
        scripted = json.loads((ROOT / "examples" / "scripts" / "user_script.json").read_text(encoding="utf-8"))
        runtime.init_runtime(self.conn, self.session)
        db.set_game_state(self.conn, self.sid, "revealed_clues", [
            f"c_report 现场有可疑痕迹|logLR:{self.other}=1.5"
        ])
        runtime.run_session(self.conn, self.sid, 2, scripted, 42, "template")
        rows = journal.audit_events(self.conn, self.sid, act_type="frame_guidance")
        self.assertTrue(rows)
        self.assertTrue(all(row["payload"]["seat"] == self.killer for row in rows))
        public = [
            row for row in journal.audit_events(self.conn, self.sid)
            if "public" in (row["visibility"] or [])
        ]
        for row in public:
            self.assertNotIn("真凶专用", str(row["payload"]))
        self.assertIn("## 14. 真凶栽赃推演", replay.build_replay(self.conn, self.sid))
        report = retro.evaluate(self.conn, self.sid)
        self.assertEqual(
            report["retrospective"]["framing"]["guidance_rounds"], len(rows)
        )


if __name__ == "__main__":
    unittest.main()
