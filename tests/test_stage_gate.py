"""Tests for runtime stage gates and scarce-resource audit (#31/G06)."""

import json
import tempfile
import unittest
from pathlib import Path

from _fixture import make_session_db
from imms import db, gm, investigation, journal, phases, runtime, stage_gate


def mini_onto():
    return {
        "script_id": "mini", "title": "mini",
        "facts": [
            {"id": "f_truth", "type": "truth", "content": "完整真相", "visibility": "truth",
             "core": True, "keywords": ["真相"]},
        ],
        "characters": [], "clues": [], "clue_pool": [], "clue_fact_map": {},
        "evaluation": {}, "timeline_true": [],
        "plot_locks": ["第2幕才能提及真相"], "completions": [],
    }


class TestStageGate(unittest.TestCase):
    def test_compile_and_block_before_stage(self):
        onto = mini_onto()
        gates = stage_gate.compile_gates(onto)
        self.assertEqual(len(gates), 1)
        self.assertEqual(gates[0]["stage"], 2)
        self.assertEqual(gates[0]["allowed_stage"], 1)
        self.assertIn("f_truth", gates[0]["fact_ids"])

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn, session = make_session_db(Path(tmp.name) / "t.db")
        self.addCleanup(conn.close)
        sid = session["session_id"]
        stage_gate.init_resources(conn, sid, ["human:a"])

        act = {"utterance": "真相是隔壁做的", "act_type": "assert",
               "claimed_facts": ["f_truth"], "lie_id": "lie_1"}
        report = stage_gate.guard_output(conn, sid, onto, act, 1)
        self.assertTrue(report)
        self.assertEqual(act["act_type"], "deflect")
        self.assertEqual(act["claimed_facts"], [])
        self.assertIsNone(act["lie_id"])
        rows = journal.audit_events(conn, sid, act_type="stage_gate")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["visibility"], ["gm:host"])

        stage_gate.advance(conn, sid, 1)
        allowed = {"utterance": "我现在可以说了", "act_type": "assert",
                   "claimed_facts": ["f_truth"]}
        self.assertIsNone(stage_gate.guard_output(conn, sid, onto, allowed, 2))
        self.assertEqual(stage_gate.current_stage(conn, sid), 1)
        again = stage_gate.advance(conn, sid, 1)
        self.assertTrue(again["already"])

    def test_public_fact_is_exempt(self):
        onto = mini_onto()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn, session = make_session_db(Path(tmp.name) / "t.db")
        self.addCleanup(conn.close)
        sid = session["session_id"]
        stage_gate.init_resources(conn, sid, ["human:a"])
        db.set_game_state(conn, sid, "revealed_clues", ["f_truth 真相|logLR:a=1.0"])
        act = {"utterance": "真相摆着", "act_type": "assert", "claimed_facts": ["f_truth"]}
        self.assertIsNone(stage_gate.guard_output(conn, sid, onto, act, 1))


class TestResources(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn, self.session = make_session_db(Path(self.tmp.name) / "t.db")
        self.sid = self.session["session_id"]

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_challenge_token_spend_and_denial(self):
        stage_gate.init_resources(self.conn, self.sid, ["ai:a"])
        first = {"act_type": "accuse", "utterance": "你说谎", "claimed_facts": []}
        second = {"act_type": "accuse", "utterance": "你说谎", "claimed_facts": []}
        third = {"act_type": "accuse", "utterance": "你说谎", "claimed_facts": []}
        self.assertEqual(stage_gate.consume_challenge(
            self.conn, self.sid, 1, "ai:a", first)["action"], "spent")
        self.assertEqual(stage_gate.consume_challenge(
            self.conn, self.sid, 2, "ai:a", second)["action"], "spent")
        denied = stage_gate.consume_challenge(self.conn, self.sid, 3, "ai:a", third)
        self.assertEqual(denied["action"], "denied")
        self.assertEqual(third["act_type"], "deflect")
        rows = journal.audit_events(self.conn, self.sid, act_type="challenge_token")
        self.assertEqual([r["payload"]["action"] for r in rows], ["spent", "spent", "denied"])
        self.assertEqual(db.get_game_state(self.conn, self.sid, "challenge_tokens")["ai:a"], 0)

    def test_private_window_closes_rooms(self):
        stage_gate.init_resources(self.conn, self.sid, ["ai:a", "ai:b"])
        stage_gate.set_private_allowed(self.conn, self.sid, 1, False)
        self.assertFalse(stage_gate.private_allowed(self.conn, self.sid))
        with self.assertRaises(ValueError):
            investigation.open_private_chat(self.conn, self.session, 1, ["ai:a", "ai:b"])
        rows = journal.audit_events(self.conn, self.sid, act_type="private_window")
        self.assertEqual(rows[0]["payload"]["after"], False)

    def test_phases_reject_skip(self):
        phases.set_phase(self.conn, self.sid, "intro")
        with self.assertRaises(ValueError):
            phases.set_phase(self.conn, self.sid, "vote")
        phases.set_phase(self.conn, self.sid, "self_intro")
        self.assertEqual(phases.phase_of(self.conn, self.sid), "self_intro")

    def test_gm_decision_consumes_stage_flags(self):
        policy = gm.make_policy(overrides={"advance_stage": True, "private_allowed": False})
        runtime.run_session(
            self.conn, self.sid, 1, {"utterances": ["我先说。"], "vote": "ai:lin_wanqiu"},
            42, "template", gm_policy=policy,
        )
        events = journal.audit_events(self.conn, self.sid)
        kinds = [e["act_type"] for e in events]
        self.assertIn("stage_advance", kinds)
        self.assertIn("private_window", kinds)
        self.assertEqual(stage_gate.current_stage(self.conn, self.sid), 1)
        self.assertFalse(stage_gate.private_allowed(self.conn, self.sid))


if __name__ == "__main__":
    unittest.main()
