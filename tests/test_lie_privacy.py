"""A lie ledger is owned by its speaker alone.

The stored event keeps the full audit payload (replay/retro need it), but no
other seat may observe ``lie_id``, ``inner_intent``, ``recalled`` or ``guard``.
"""

import tempfile
import unittest
from pathlib import Path

from imms import db

from _fixture import ROOT, make_session_db


class LiePrivacyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.conn, self.session = make_session_db(self.tmp.name)
        self.sid = self.session["session_id"]
        self.seats = [p["id"] for p in self.session["participants"] if p["kind"] != "gm"]
        self.speaker, self.listener = self.seats[0], self.seats[1]
        payload = {
            "utterance": "这件事我记不清了。",
            "inner_intent": "我其实知道钥匙在哪，但我要隐瞒。",
            "claimed_facts": [],
            "target": self.listener,
            "emotion_shown": "平静",
            "guard": {"action": "flag", "facts": ["f_hidden"]},
            "recalled": [{"utterance": "上次你也这么问"}],
            "lie_id": "lie_speaker_r1_001",
        }
        db.append_game_event(self.conn, self.sid, 1, self.speaker, "deflect", payload, ["public"])

    def tearDown(self):
        self.conn.close()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_other_seat_cannot_observe_private_fields(self):
        events = db.visible_game_events(self.conn, self.sid, self.listener)
        own = [e for e in events if e["actor"] == self.speaker]
        self.assertEqual(len(own), 1)
        payload = db.json.loads(own[0]["payload_json"])
        self.assertEqual(payload["utterance"], "这件事我记不清了。")
        for field in db.PRIVATE_PAYLOAD_FIELDS:
            self.assertNotIn(field, payload, f"{field} leaked to {self.listener}")

    def test_speaker_sees_own_private_fields(self):
        events = db.visible_game_events(self.conn, self.sid, self.speaker)
        payload = db.json.loads(events[0]["payload_json"])
        self.assertEqual(payload["lie_id"], "lie_speaker_r1_001")
        self.assertIn("inner_intent", payload)

    def test_gm_keeps_full_audit_view(self):
        events = db.visible_game_events(self.conn, self.sid, "gm")
        payload = db.json.loads(events[0]["payload_json"])
        self.assertEqual(payload["lie_id"], "lie_speaker_r1_001")
        self.assertIn("guard", payload)

    def test_stored_row_keeps_full_payload_for_replay(self):
        rows = self.conn.execute(
            "SELECT payload_json FROM game_events WHERE session_id=? AND actor=?",
            (self.sid, self.speaker),
        ).fetchall()
        self.assertEqual(len(rows), 1)
        payload = db.json.loads(rows[0]["payload_json"])
        self.assertIn("inner_intent", payload)
        self.assertIn("lie_id", payload)


if __name__ == "__main__":
    unittest.main()
