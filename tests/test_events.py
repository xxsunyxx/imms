import tempfile
import unittest
from pathlib import Path

from _fixture import make_session_db
from imms import db, events, retro, runtime


class TestEventInjectionLibrary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn, self.session = make_session_db(Path(self.tmp.name) / "t.db")
        self.sid = self.session["session_id"]
        runtime.init_runtime(self.conn, self.session)
        db.set_game_state(self.conn, self.sid, "total_rounds", 2)
        self.states = db.get_game_state(self.conn, self.sid, "runtime", {})

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _reset(self):
        for state in self.states.values():
            state["emotion"]["arousal"] = 0.1
            state["emotion"]["stress"] = 0.1
            state["emotion"]["exposure_risk"] = 0.1
            state["emotion"]["intoxication"] = 0.0
        db.set_game_state(self.conn, self.sid, "runtime", self.states)

    def test_catalog_has_seven_triggered_events(self):
        ctx = events.build_context(self.conn, self.sid, 2)
        self.assertEqual(set(events.EVENTS), {
            "second_body", "blackout", "npcs_letter", "anonymous_tip",
            "old_flame", "drunken_slip", "weather",
        })
        candidates = events.select(ctx)
        self.assertTrue(candidates)
        self.assertGreaterEqual(candidates[0]["score"], candidates[-1]["score"])
        self.assertIn("variables", candidates[0])

    def test_blackout_is_multi_variable_and_audited(self):
        self._reset()
        applied = events.apply(self.conn, self.sid, "blackout", 1, self.states)
        self.assertEqual(applied["event_id"], "blackout")
        self.assertEqual(db.get_game_state(self.conn, self.sid, "ap_penalty"), -2)
        self.assertGreater(applied["changes"][0]["after"], applied["changes"][0]["before"])
        row = self.conn.execute(
            "SELECT payload_json FROM game_events WHERE session_id=? AND act_type='gm_event_injection'",
            (self.sid,),
        ).fetchone()
        self.assertIsNotNone(row)
        audit = retro.injection_audit(retro._events(self.conn, self.sid))
        self.assertEqual(audit["count"], 1)
        self.assertEqual(audit["by_event"], {"blackout": 1})

    def test_anonymous_tip_targets_highest_exposure(self):
        self._reset()
        seats = list(self.states)
        self.states[seats[0]]["emotion"]["exposure_risk"] = 0.9
        db.set_game_state(self.conn, self.sid, "runtime", self.states)
        applied = events.apply(self.conn, self.sid, "anonymous_tip", 1, self.states)
        self.assertEqual(applied["applied"]["target"], seats[0])
        self.assertGreaterEqual(self.states[seats[0]]["emotion"]["stress"], 0.5)
        self.assertTrue(
            self.conn.execute(
                "SELECT COUNT(*) AS n FROM attribute_events WHERE session_id=? AND reason='匿名举报'",
                (self.sid,),
            ).fetchone()["n"] >= 2
        )

    def test_drunken_slip_leaks_only_canonical_knowledge(self):
        self._reset()
        seats = list(self.states)
        speaker = self.states[seats[0]]
        speaker["emotion"]["intoxication"] = 0.8
        speaker["beliefs"] = {"t_secret": {"belief": 0.9, "source": "canon", "evidence": []}}
        db.set_game_state(self.conn, self.sid, "runtime", self.states)
        applied = events.apply(self.conn, self.sid, "drunken_slip", 1, self.states)
        self.assertEqual(applied["applied"]["fact_id"], "t_secret")
        for other in seats[1:]:
            self.assertIn("t_secret", self.states[other]["beliefs"])

    def test_no_candidate_raises(self):
        ctx = events.build_context(self.conn, self.sid, 1)
        with self.assertRaises(ValueError):
            events.apply(self.conn, self.sid, "second_body", 1, self.states)


if __name__ == "__main__":
    unittest.main()
