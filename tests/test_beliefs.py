import tempfile
import unittest
from pathlib import Path

from _fixture import make_session_db
from imms import db, infoguard, runtime


class TestBeliefInitialisation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn, self.session = make_session_db(Path(self.tmp.name) / "t.db")
        self.sid = self.session["session_id"]

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_each_seat_only_starts_with_public_and_own_facts(self):
        onto = db.get_script(self.conn, self.session["script_id"])
        public = {f["id"] for f in onto.get("facts", []) if f.get("visibility") == "public"}
        known = {c["id"]: set(c.get("known_fact_ids") or []) for c in onto["characters"]}

        runtime.init_runtime(self.conn, self.session)
        states = db.get_game_state(self.conn, self.sid, "runtime", {})
        for seat in states.values():
            cid = seat["character_id"]
            expected = public | known.get(cid, set())
            self.assertEqual(set(seat["beliefs"]), expected)
            for entry in seat["beliefs"].values():
                self.assertNotIn("is_true", entry)
        # No unreleased clue card is knowledge at build time.
        for clue in onto.get("clue_pool", []):
            for seat in states.values():
                self.assertNotIn(clue, seat["beliefs"])

    def test_released_clue_unlocks_only_its_revealed_facts(self):
        onto = db.get_script(self.conn, self.session["script_id"])
        runtime.init_runtime(self.conn, self.session)
        states = db.get_game_state(self.conn, self.sid, "runtime", {})
        clue = onto["clue_pool"][0]
        fact_ids = onto.get("clue_fact_map", {}).get(clue, [])
        result = runtime._gm_step(
            self.conn, self.sid, 1, onto, states, guard=infoguard.build_engine(onto),
            decision={"release_clue": clue},
        )
        self.assertEqual(result["injected"]["type"], "clue_release")
        for seat in states.values():
            for fid in fact_ids:
                self.assertIn(fid, seat["beliefs"])
                self.assertEqual(seat["beliefs"][fid]["source"], "clue")


if __name__ == "__main__":
    unittest.main()
