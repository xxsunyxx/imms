"""G23: cross-session memory decay, reinforcement, priors and CLI."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _fixture import make_session_db
from imms import db, journal, longterm, runtime


def _at(days_ago=0):
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


class LongTermTest(unittest.TestCase):
    def test_decay_half_life_and_fade_threshold(self):
        now = _at()
        self.assertAlmostEqual(longterm.decay_weight(now, at=now), 1.0, places=3)
        self.assertAlmostEqual(
            longterm.decay_weight(_at(3), at=now, half_life_days=3), 0.5, places=2
        )
        self.assertLess(longterm.decay_weight(_at(12), at=now), longterm.FADE_THRESHOLD)

    def test_reinforcement_revives_faded_row_and_keeps_evidence(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            conn = db.connect(Path(tmp.name) / "t.db")
            old = _at(20)
            longterm.reinforce(conn, "c1", "player", "p001", -0.6, "loss",
                               [{"session_id": "s1", "seq": 3}], at=old)
            longterm.refresh_faded(conn, at=_at())
            self.assertTrue(longterm.get(conn, "c1", "player", "p001")["faded"])
            row = longterm.reinforce(conn, "c1", "player", "p001", 0.3, "win",
                                     [{"session_id": "s2", "seq": 9}], at=_at())
            self.assertFalse(row["faded"])
            self.assertEqual(row["reinforcement_count"], 1)
            self.assertEqual(row["sentiment"], -0.3)
            self.assertEqual(row["outcome"], "win")
            self.assertEqual(len(row["evidence"]), 2)
            conn.close()
        finally:
            tmp.cleanup()

    def test_forget_is_explicit_and_scoped(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            conn = db.connect(Path(tmp.name) / "t.db")
            for tid in ("p1", "p2"):
                longterm.reinforce(conn, "c1", "player", tid, 0.5, "draw", at=_at())
            self.assertEqual(longterm.forget(conn, "c1", "p1"), 1)
            self.assertEqual(len(longterm.list_for(conn, "c1")), 1)
            conn.close()
        finally:
            tmp.cleanup()

    def test_priors_change_l3_not_l1_and_skip_faded(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            conn = db.connect(Path(tmp.name) / "t.db")
            longterm.reinforce(conn, "c1", "player", "p001", 0.8, "win",
                               [{"session_id": "old", "seq": 1}], at=_at())
            longterm.reinforce(conn, "c1", "npc", "char_02", -0.7, "loss",
                               [{"session_id": "old", "seq": 2}], at=_at(30))
            longterm.refresh_faded(conn, at=_at())
            runtime_state = {
                "ai:c1": {"character_id": "c1", "l1": {"OCEAN.O": 0.5},
                          "relations": {"human:u": {"like": 0.0, "trust": 0.0},
                                        "ai:c2": {"like": 0.0, "trust": 0.0}}},
                "human:u": {"character_id": "u", "is_human": True, "relations": {}},
                "ai:c2": {"character_id": "char_02", "relations": {}},
            }
            applied = longterm.inject_priors_from(
                conn, runtime_state, "c1", at=_at()
            )
            self.assertTrue(applied)
            self.assertGreater(runtime_state["ai:c1"]["relations"]["human:u"]["like"], 0)
            self.assertEqual(runtime_state["ai:c1"]["relations"]["ai:c2"]["like"], 0)
            self.assertEqual(runtime_state["ai:c1"]["l1"], {"OCEAN.O": 0.5})
            self.assertTrue(runtime_state["ai:c1"]["long_term_priors"][0]["session_id"])
            conn.close()
        finally:
            tmp.cleanup()

    def test_runtime_settles_and_injects_across_sessions(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            conn, session = make_session_db(Path(tmp.name) / "t.db")
            sid = session["session_id"]
            runtime.init_runtime(conn, session)
            scripted = {"utterances": [{"act_type": "accuse", "utterance": "你在说谎。",
                                        "target": "ai:char_02"}], "vote": "char_02"}
            runtime.run_session(conn, sid, 1, scripted, 42, "template")
            rows = longterm.list_for(conn)
            self.assertTrue(rows)
            self.assertTrue(all(r["evidence"] for r in rows))
            events = journal.audit_events(conn, sid, act_type="longterm_settled")
            self.assertTrue(events)
            self.assertTrue(all(
                e["outcome"] in ("win", "loss", "draw")
                for e in events[-1]["payload"]["entries"]
            ))
            conn.close()
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
