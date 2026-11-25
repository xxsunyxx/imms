import tempfile
import unittest
from pathlib import Path

from _fixture import make_session_db
from imms import db


class TestPrivateRooms(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn, self.session = make_session_db(Path(self.tmp.name) / "t.db")
        self.sid = self.session["session_id"]

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_visibility_and_global_seq(self):
        room = db.open_private_room(self.conn, self.session, 3, ["human:lin_wanqiu", "ai:gu_chengye"])
        say = db.private_say(
            self.conn, self.sid, room["room_id"], "human:lin_wanqiu",
            "账房的灯熄得早。", target="ai:gu_chengye",
        )
        self.assertGreater(say["seq"], room["opened_seq"])
        for seat in ("human:lin_wanqiu", "ai:gu_chengye", "gm:host"):
            seqs = [e["seq"] for e in db.visible_game_events(self.conn, self.sid, seat)]
            self.assertIn(say["seq"], seqs)
        outsider = [e["seq"] for e in db.visible_game_events(self.conn, self.sid, "ai:su_manqing")]
        self.assertNotIn(room["opened_seq"], outsider)
        self.assertNotIn(say["seq"], outsider)

    def test_membership_and_close_guards(self):
        room = db.open_private_room(self.conn, self.session, 3, ["human:lin_wanqiu", "ai:gu_chengye"])
        with self.assertRaises(ValueError) as ctx:
            db.private_say(self.conn, self.sid, room["room_id"], "ai:su_manqing", "让我也听听")
        self.assertEqual(str(ctx.exception), "ERR_NOT_ROOM_MEMBER")
        db.close_private_room(self.conn, self.sid, room["room_id"])
        with self.assertRaises(ValueError) as ctx:
            db.private_say(self.conn, self.sid, room["room_id"], "ai:gu_chengye", "再说一句")
        self.assertEqual(str(ctx.exception), "ERR_ROOM_CLOSED")

    def test_too_small_room_rejected(self):
        with self.assertRaises(ValueError):
            db.open_private_room(self.conn, self.session, 3, ["human:lin_wanqiu"])


if __name__ == "__main__":
    unittest.main()
