import unittest
from pathlib import Path

from imms import assemble, boxed, db, infoguard, ontology, runtime

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
FIXTURE = ROOT / "tests" / "fixtures" / "boxed_mini"


class TestBoxedImport(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.onto = boxed.parse_boxed_dir(FIXTURE)

    def tearDown(self):
        self.conn.close()

    def test_parse_structure(self):
        o = self.onto
        self.assertEqual(o["script_id"], "Z01")
        self.assertEqual(o["world"]["genre"], "搞笑本")
        self.assertEqual(len(o["characters"]), 2)
        self.assertEqual(len(o["clues"]), 2)
        self.assertEqual([c["stage"] for c in o["clues"]], [0, 1])
        self.assertTrue(all("线索卡.png" in c["source_images"] for c in o["clues"]))
        self.assertEqual(len(o["clue_fact_map"]), 4)  # line keys + id keys
        self.assertTrue(ontology.validate_ontology(o)["char_count"])
        self.assertIn(o["evaluation"]["killer_id"], {c["id"] for c in o["characters"]})
        self.assertEqual(o["evaluation"]["culprit_basis"], "truth_text_name_frequency")

    def test_fact_visibility(self):
        vis = {f["id"]: f["visibility"] for f in self.onto["facts"]}
        self.assertEqual(vis["f_z01_pub"], "public")
        self.assertEqual(vis["f_z01_s01"], "private")
        self.assertEqual(vis["f_z01_truth"], "truth")
        scopes = infoguard.build_engine(self.onto).scopes
        self.assertIn("f_z01_s01", scopes["z01_r01"].known_fact_ids)
        self.assertIn("f_z01_s02", scopes["z01_r01"].forbidden_fact_ids)
        self.assertIn("f_z01_truth", scopes["z01_r02"].forbidden_fact_ids)

    def test_import_stores_script_and_l1(self):
        report = boxed.import_boxes(self.conn, FIXTURE.parent, CONFIG, limit=1)
        self.assertEqual(report["imported"], 1)
        self.assertEqual(db.get_script(self.conn, "Z01")["title"], "最小测试本")
        personas = db.get_persona_l1(self.conn, "Z01")["personas"]
        self.assertEqual(len(personas), 2)
        self.assertTrue(all(len(p["attributes"]) >= 10 for p in personas))

    def test_vote_maps_to_human_seat(self):
        boxed.import_boxes(self.conn, FIXTURE.parent, CONFIG, limit=1)
        session = assemble.assemble(self.conn, "Z01", "z01_r01")
        sid = session["session_id"]
        runtime.init_runtime(self.conn, session)
        state = db.get_game_state(self.conn, sid, "runtime")
        result = runtime._vote(self.conn, sid, state, session, {"vote": "z01_r01"})
        self.assertIn("human:z01_r01", result["tally"])


if __name__ == "__main__":
    unittest.main()
