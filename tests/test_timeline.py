"""Tests for dual-track timeline and statement conflicts (#31/G07)."""

import json
import tempfile
import unittest
from pathlib import Path

from _fixture import make_session_db
from imms import db, journal, memory, runtime, timeline


def mini_onto():
    return {
        "script_id": "mini", "title": "mini",
        "facts": [], "characters": [
            {"id": "a", "name": "甲", "timeline": ["21:00 事件二", "20:00 事件一"]},
            {"id": "b", "name": "乙", "timeline": ["事件没有时间"]},
        ],
        "clues": [], "clue_pool": [], "clue_fact_map": {},
        "evaluation": {}, "timeline_true": ["20:00 事件一", "21:00 事件二"],
        "plot_locks": [], "completions": [],
    }


class TestTimeline(unittest.TestCase):
    def test_gap_and_real_inversion_only(self):
        tracks = timeline.build_tracks(mini_onto())
        report = timeline.audit(tracks)
        self.assertEqual(report["gaps"]["a"]["covered"], 2)
        self.assertEqual(report["gaps"]["a"]["gap"], 0.0)
        self.assertEqual(report["gaps"]["b"]["gap"], 1.0)
        inversions = [i for i in report["inversions"] if i["character_id"] == "a"]
        self.assertEqual(len(inversions), 1)
        self.assertEqual(inversions[0]["kind"], "narrated_inversion")
        self.assertFalse([i for i in report["inversions"] if i["character_id"] == "b"])

    def test_runtime_writes_gm_only_timeline_report(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn, session = make_session_db(Path(tmp.name) / "t.db")
        self.addCleanup(conn.close)
        sid = session["session_id"]
        report = timeline.update_session(conn, sid, db.get_script(conn, session["script_id"]), 1)
        self.assertIn("gaps", report)
        stored = db.get_game_state(conn, sid, "timeline")
        self.assertEqual(stored["round"], 1)
        rows = journal.audit_events(conn, sid, act_type="timeline_audit")
        self.assertTrue(rows)
        self.assertEqual(rows[-1]["visibility"], ["gm:host"])


class TestStatementConflict(unittest.TestCase):
    def test_conflict_requires_shared_fact_and_evidence(self):
        state = {"memory": [], "commitments": {}}
        memory.observe(state, {
            "seq": 1, "actor": "ai:a", "act_type": "assert",
            "payload": {"utterance": "我看见账本", "claimed_facts": ["f_ledger"]},
        }, 1)
        result = memory.observe(state, {
            "seq": 2, "actor": "ai:a", "act_type": "assert",
            "payload": {"utterance": "我没有看见账本", "claimed_facts": ["f_ledger"]},
        }, 2)
        self.assertIsNotNone(result["conflict"])
        self.assertGreater(result["conflict"]["strength"], 0)
        other = memory.observe(state, {
            "seq": 3, "actor": "ai:a", "act_type": "assert",
            "payload": {"utterance": "我没有看见窗户", "claimed_facts": ["f_window"]},
        }, 3)
        self.assertIsNone(other["conflict"])

    def test_confess_transition_is_recorded(self):
        state = {"memory": [], "commitments": {}}
        memory.observe(state, {
            "seq": 1, "actor": "ai:a", "act_type": "assert",
            "payload": {"utterance": "我没进书房", "claimed_facts": ["f_room"]},
        }, 1)
        memory.observe(state, {
            "seq": 2, "actor": "ai:a", "act_type": "confess",
            "payload": {"utterance": "我承认，我进过书房", "claimed_facts": ["f_room"]},
        }, 2)
        self.assertEqual(state["commitments"]["f_room"]["status"], "confessed")
        self.assertEqual(
            [h["status"] for h in state["commitments"]["f_room"]["history"]],
            ["stated", "confessed"],
        )

    def test_runtime_emits_statement_conflict_gm_only(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn, session = make_session_db(Path(tmp.name) / "t.db")
        self.addCleanup(conn.close)
        sid = session["session_id"]
        scripted = {"utterances": [
            {"utterance": "我看见了报告。", "claimed_facts": ["p_setup"]},
            {"utterance": "我没有看见报告。", "claimed_facts": ["p_setup"]},
        ], "vote": "ai:lin_wanqiu"}
        runtime.run_session(conn, sid, 2, scripted, 7, "template")
        rows = journal.audit_events(conn, sid, act_type="statement_conflict")
        self.assertTrue(rows)
        self.assertEqual(rows[0]["visibility"], ["gm:host"])
        payload = rows[0]["payload"]
        self.assertEqual(payload["speaker"].split(":")[0], "human")
        self.assertIn("p_setup", payload["fact_ids"])


if __name__ == "__main__":
    unittest.main()
