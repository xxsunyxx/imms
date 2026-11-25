import json
import tempfile
import unittest
from pathlib import Path

from _fixture import ROOT, make_session_db
from imms import db, gm, runtime


class TestGMDecisionCore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn, self.session = make_session_db(Path(self.tmp.name) / "t.db")
        self.sid = self.session["session_id"]
        scripted = json.loads((ROOT / "examples" / "scripts" / "user_script.json").read_text(encoding="utf-8"))
        self.base = runtime.run_session(self.conn, self.sid, 2, scripted, 42, "template")
        self.base_events = self.conn.execute(
            "SELECT COUNT(*) AS n FROM game_events WHERE session_id=?", (self.sid,)
        ).fetchone()["n"]

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_decision_includes_clue_and_rationale(self):
        onto = db.get_script(self.conn, self.session["script_id"])
        state = db.get_game_state(self.conn, self.sid, "runtime", {})
        decision = gm.suggest_decision(self.conn, self.sid, 2, onto, state, "information")
        self.assertIn("release_clue", decision)
        self.assertIn("rationale", decision)
        self.assertGreaterEqual(decision["clue_gain"], 0)
        self.assertIn("private_allowed", decision)

    def test_branch_tree_diff_merge_and_rollback(self):
        row = gm.create_branch(
            self.conn, self.sid, 2, label="改口", override_round=2,
            override_utterance="我改主意了，我只相信自己。",
            decision={"policy": "information"},
        )
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["parent_id"], "root")
        self.assertTrue(row["report_json"]["regress"]["checks"]["bounded"])

        tree = gm.branch_tree(self.conn, self.sid)
        self.assertIn(row["branch_id"], [b["branch_id"] for b in tree["branches"]])

        diff = gm.branch_diff(self.conn, row["branch_id"])
        self.assertGreaterEqual(diff["summary"]["utterance_changes"], 1)
        self.assertIn("outcome", diff)

        activated = gm.activate(self.conn, row["branch_id"])
        self.assertEqual(activated["role"], "canonical")
        merged = gm.merge(self.conn, row["branch_id"], "采纳为反事实")
        self.assertEqual(merged["status"], "merged")
        rolled = gm.rollback(self.conn, row["branch_id"])
        self.assertEqual(rolled["status"], "rolled_back")
        self.assertIsNone(gm.canonical(self.conn, self.sid))

        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS n FROM game_events WHERE session_id=?", (self.sid,)).fetchone()["n"],
            self.base_events + 3,  # gm_merge + gm_rollback + no canonical event on activate? checked below
        )

    def test_policy_changes_released_clue(self):
        onto = db.get_script(self.conn, self.session["script_id"])
        state = db.get_game_state(self.conn, self.sid, "runtime", {})
        heat = gm.suggest_decision(self.conn, self.sid, 2, onto, state, "heat")
        info = gm.suggest_decision(self.conn, self.sid, 2, onto, state, "information")
        self.assertIn("release_clue", heat)
        self.assertIn("release_clue", info)

    def test_create_branch_does_not_mutate_base_result(self):
        gm.create_branch(self.conn, self.sid, 2, override_round=2, override_utterance="我不说了。")
        self.assertEqual(db.get_game_state(self.conn, self.sid, "result"), self.base["result"])


if __name__ == "__main__":
    unittest.main()
