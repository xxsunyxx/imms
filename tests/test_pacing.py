import json
import tempfile
import unittest
from pathlib import Path

from _fixture import ROOT, make_session_db
from imms import db, gm, runtime


class TestPacingTarget(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn, self.session = make_session_db(Path(self.tmp.name) / "t.db")
        self.sid = self.session["session_id"]
        scripted = json.loads((ROOT / "examples" / "scripts" / "user_script.json").read_text(encoding="utf-8"))
        runtime.run_session(self.conn, self.sid, 2, scripted, 42, "template")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_total_rounds_is_persisted_and_used_by_gm(self):
        self.assertEqual(db.get_game_state(self.conn, self.sid, "total_rounds"), 2)
        onto = db.get_script(self.conn, self.session["script_id"])
        state = db.get_game_state(self.conn, self.sid, "runtime", {})
        early = gm.suggest_decision(self.conn, self.sid, 1, onto, state, "heat")
        late = gm.suggest_decision(self.conn, self.sid, 2, onto, state, "heat")
        self.assertLess(early["heat_target"], late["heat_target"])
        self.assertAlmostEqual(late["heat_target"], 3.0, places=4)
        snap = db.load_snapshot(self.conn, self.sid, 2)
        self.assertEqual(snap["@global"]["total_rounds"], 2)

    def test_high_heat_in_early_round_triggers_rest(self):
        for i in range(4):
            db.append_game_event(self.conn, self.sid, 1, f"ai:x{i}", "accuse", {}, ["public"])
        onto = db.get_script(self.conn, self.session["script_id"])
        rt_state = db.get_game_state(self.conn, self.sid, "runtime", {})
        for state in rt_state.values():
            state["emotion"]["arousal"] = 1.0
        result = runtime._gm_step(self.conn, self.sid, 1, onto, rt_state)
        self.assertEqual(result["injected"]["type"], "rest")
        self.assertGreater(result["heat"], result["target"])


if __name__ == "__main__":
    unittest.main()
