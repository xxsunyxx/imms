import json
import tempfile
import unittest
from pathlib import Path

from _fixture import ROOT, make_session_db
from imms import db, lie, replay, runtime


class TestLieLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn, self.session = make_session_db(Path(self.tmp.name) / "t.db")
        self.sid = self.session["session_id"]
        runtime.init_runtime(self.conn, self.session)
        states = db.get_game_state(self.conn, self.sid, "runtime", {})
        self.state = next(s for s in states.values() if not s.get("is_human", False))
        self.state["seat_id"] = next(k for k, v in states.items() if v is self.state)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _raise_exposure(self):
        self.state["emotion"]["exposure_risk"] = 0.9
        self.state["l1"]["values.honesty"] = 0.05

    def test_gain_decision_and_source_backed_record(self):
        self._raise_exposure()
        self.assertGreater(lie.lie_gain(self.state), 0)
        self.assertTrue(lie.should_lie(self.state, "deflect"))
        self.assertFalse(lie.should_lie(self.state, "assert"))
        entry = lie.create(self.state, 1, "我记不清了。", ["human:lin_wanqiu"])
        self.assertEqual(entry["claimed_facts"], [])
        self.assertIs(entry["true_value"], False)
        self.assertEqual(entry["basis"], "speaker_knows_relevant_facts")
        self.assertEqual(entry["reception"], {"human:lin_wanqiu": "pending"})
        self.assertEqual([r["kind"] for r in self.state["reasoning_log"]], ["created"])

    def test_believed_and_exposed_both_force_rejustification(self):
        entry = lie.create(self.state, 1, "我记不清了。", ["human:lin_wanqiu"])
        believed = lie.update_from_events(self.state, [{
            "seq": 11, "round": 2, "actor": "human:lin_wanqiu", "act_type": "ally",
            "payload": {"target": self.state["seat_id"]},
        }])
        self.assertEqual(believed[0]["status"], "believed")
        self.assertEqual(entry["reception"]["human:lin_wanqiu"], "believed")
        self.assertEqual(entry["strategy"], "maintain")

        exposed = lie.update_from_events(self.state, [{
            "seq": 12, "round": 3, "actor": "human:lin_wanqiu", "act_type": "caught_lying",
            "payload": {"target": self.state["seat_id"]},
        }])
        self.assertEqual(exposed[0]["status"], "exposed")
        self.assertGreater(entry["heat"], 0)
        self.assertEqual(len(entry["revisions"]), 2)
        self.assertEqual(
            [r["kind"] for r in self.state["reasoning_log"]],
            ["created", "believed", "exposed"],
        )

    def test_heat_escalation_forces_maintenance(self):
        entry = lie.create(self.state, 1, "我记不清了。")
        for _ in range(3):
            lie.update_from_events(self.state, [{
                "act_type": "caught_lying",
                "payload": {"target": self.state["seat_id"]},
            }])
        self.assertGreater(entry["heat"], 0.8)
        self.assertEqual(entry["strategy"], "patch")
        guarded = lie.guard_escalation(self.state, {
            "act_type": "accuse", "intent": "accuse", "utterance": "你根本在撒谎。",
        })
        self.assertEqual(guarded["act_type"], "deflect")
        self.assertEqual(guarded["lie_id"], entry["lie_id"])

    def test_runtime_records_lies_and_replay_renders_them(self):
        states = db.get_game_state(self.conn, self.sid, "runtime", {})
        for seat, state in states.items():
            state["seat_id"] = seat
            if "human:" not in seat:
                state["emotion"]["exposure_risk"] = 0.9
                state["l1"]["values.honesty"] = 0.05
        db.set_game_state(self.conn, self.sid, "runtime", states)
        scripted = json.loads((ROOT / "examples" / "scripts" / "user_script.json").read_text(encoding="utf-8"))
        runtime.run_session(self.conn, self.sid, 2, scripted, 42, "template")
        final = db.get_game_state(self.conn, self.sid, "runtime", {})
        self.assertTrue(any(s.get("lie_ledger") for s in final.values()))
        self.assertTrue(any(s.get("reasoning_log") for s in final.values()))
        rows = self.conn.execute(
            "SELECT payload_json FROM game_events WHERE session_id=? AND act_type='deflect'", (self.sid,)
        ).fetchall()
        self.assertTrue(any(json.loads(r["payload_json"]).get("lie_id") for r in rows))
        self.assertIn("谎言全景图", replay.build_replay(self.conn, self.sid))


if __name__ == "__main__":
    unittest.main()
