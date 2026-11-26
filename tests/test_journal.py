"""G28: one traceable gateway for session state and event history."""

import tempfile
import unittest
from pathlib import Path

from imms import branch, db, journal

from _fixture import make_session_db


class JournalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.conn, self.session = make_session_db(self.tmp.name)
        self.sid = self.session["session_id"]
        self.seats = [p["id"] for p in self.session["participants"] if p["kind"] != "gm"]
        self.speaker, self.listener = self.seats[0], self.seats[1]

    def tearDown(self):
        self.conn.close()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_state_writes_are_audited_with_reason_and_actor(self):
        journal.set_state(self.conn, self.sid, "round", 2, reason="round_advance", actor="gm:host")
        journal.set_state(self.conn, self.sid, "round", 3, reason="round_advance", actor="gm:host")
        self.assertEqual(journal.get_state(self.conn, self.sid, "round"), 3)
        trail = journal.state_audit(self.conn, self.sid)
        self.assertEqual([a["reason"] for a in trail], ["round_advance", "round_advance"])
        self.assertEqual([a["actor"] for a in trail], ["gm:host", "gm:host"])
        self.assertEqual([a["before_json"] for a in trail], [None, 2])
        self.assertEqual([a["after_json"] for a in trail], [2, 3])
        self.assertEqual([a["seq"] for a in trail], sorted(a["seq"] for a in trail))

    def test_state_snapshot_is_deterministic(self):
        journal.set_state(self.conn, self.sid, "b", 1)
        journal.set_state(self.conn, self.sid, "a", 2)
        snap = journal.state_snapshot(self.conn, self.sid)
        self.assertEqual(list(snap), sorted(snap))
        self.assertEqual(snap["a"], 2)
        self.assertEqual(journal.state_keys(self.conn, self.sid), sorted(snap))
        self.assertEqual(len(journal.state_audit(self.conn, self.sid, since=2)), 1)

    def test_db_wrappers_delegate_to_journal(self):
        db.set_game_state(self.conn, self.sid, "custom", {"x": 1}, reason="legacy_call")
        self.assertEqual(db.get_game_state(self.conn, self.sid, "custom"), {"x": 1})
        trail = journal.state_audit(self.conn, self.sid)
        self.assertEqual(trail[-1]["reason"], "legacy_call")
        self.assertEqual(trail[-1]["after_json"], {"x": 1})

    def test_visibility_and_redaction_share_one_rule(self):
        payload = {
            "utterance": "我什么都没看见。",
            "inner_intent": "我看见了凶器。",
            "lie_id": "lie_speaker_r1_001",
        }
        journal.append_event(self.conn, self.sid, 1, self.speaker, "deflect", payload, ["public"])
        other = journal.visible_events(self.conn, self.sid, self.listener)
        self.assertEqual(len(other), 1)
        self.assertEqual(
            journal_mod_payload(other[0]),
            {"utterance": "我什么都没看见。"},
        )
        own = journal.visible_events(self.conn, self.sid, self.speaker)
        self.assertEqual(journal_mod_payload(own[0])["lie_id"], "lie_speaker_r1_001")
        full = journal.audit_events(self.conn, self.sid)
        self.assertEqual(full[0]["payload"]["inner_intent"], "我看见了凶器。")
        self.assertEqual(full[0]["visibility"], ["public"])
        self.assertEqual(
            journal_mod_payload(db.visible_game_events(self.conn, self.sid, self.listener)[0]),
            {"utterance": "我什么都没看见。"},
        )

    def test_audit_events_filters_and_orders(self):
        journal.append_event(self.conn, self.sid, 1, self.speaker, "assert", {"utterance": "a"}, ["public"])
        journal.append_event(self.conn, self.sid, 2, self.listener, "assert", {"utterance": "b"}, ["public"])
        journal.append_event(self.conn, self.sid, 3, self.speaker, "private_say", {"utterance": "c"}, ["room:x"])
        rows = journal.audit_events(self.conn, self.sid, round_no=2)
        self.assertEqual([r["round"] for r in rows], [1, 2])
        self.assertEqual(
            [r["payload"]["utterance"] for r in journal.audit_events(self.conn, self.sid, actor=self.speaker)],
            ["a", "c"],
        )
        self.assertEqual(
            [r["payload"]["utterance"] for r in journal.audit_events(self.conn, self.sid, act_type="private_say")],
            ["c"],
        )
        self.assertEqual(
            [r["payload"]["utterance"] for r in journal.audit_events(self.conn, self.sid)],
            ["a", "b", "c"],
        )

    def test_branches_clone_the_state_audit_trail(self):
        journal.set_state(self.conn, self.sid, "round", 1, reason="before_fork")
        mem = db.connect(":memory:")
        try:
            branch._clone(
                self.conn, mem, "state_change_audit", "WHERE session_id=?", (self.sid,),
                branch._rekey(self.sid, "fork-1"),
            )
            trail = journal.state_audit(mem, "fork-1")
            self.assertEqual(len(trail), 1)
            self.assertEqual(trail[0]["reason"], "before_fork")
            self.assertEqual(trail[0]["session_id"], "fork-1")
        finally:
            mem.close()


def journal_mod_payload(row):
    import json
    return json.loads(row["payload_json"])


if __name__ == "__main__":
    unittest.main()
