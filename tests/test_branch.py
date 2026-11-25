import json
import tempfile
import unittest
from pathlib import Path

from _fixture import ROOT, make_session_db
from imms import branch, db, runtime


class TestBranchRegression(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn, self.session = make_session_db(Path(self.tmp.name) / "t.db")
        self.sid = self.session["session_id"]
        scripted = json.loads((ROOT / "examples" / "sample_script" / "user_script.json").read_text(encoding="utf-8"))
        self.base = runtime.run_session(self.conn, self.sid, 2, scripted, 42, "template")
        self.human_seat = next(
            p["id"] for p in self.session["participants"] if p["kind"] == "human_character"
        )

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_round_snapshot_written(self):
        snap = db.load_snapshot(self.conn, self.sid, 1)
        self.assertIn("@global", snap)
        self.assertIn(self.human_seat, snap)
        self.assertEqual(snap["@global"]["round"], 1)

    def test_fork_applies_override_and_regresses(self):
        base_events = self.conn.execute(
            "SELECT COUNT(*) AS n FROM game_events WHERE session_id=?", (self.sid,)
        ).fetchone()["n"]
        report = branch.fork_and_regress(
            self.conn, self.sid, 2, 2, "我相信赵管事，账房的灯熄得早与我无关。"
        )
        self.assertTrue(all(report["checks"].values()), report["checks"])
        self.assertIn(report["verdict"], ("pass", "changed"))
        self.assertEqual(report["divergence"]["at_round"], 2)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS n FROM game_events WHERE session_id=?", (self.sid,)).fetchone()["n"],
            base_events,
        )
        self.assertEqual(
            db.get_game_state(self.conn, self.sid, "result"), self.base["result"]
        )

    def test_fork_event_contains_new_utterance(self):
        result, mem, fork_id = branch.fork(
            self.conn, self.sid, 2, 2, "我改主意了，我只相信自己。"
        )
        try:
            rows = mem.execute(
                "SELECT payload_json FROM game_events WHERE session_id=? AND actor=? AND round=2",
                (fork_id, self.human_seat),
            ).fetchall()
            texts = [json.loads(r[0]).get("utterance", "") for r in rows]
            self.assertIn("我改主意了，我只相信自己。", texts)
            self.assertEqual(result["result"]["solved"] in (True, False), True)
        finally:
            mem.close()

    def test_invalid_fork_round_rejected(self):
        with self.assertRaises(ValueError):
            branch.fork(self.conn, self.sid, 9)


if __name__ == "__main__":
    unittest.main()
