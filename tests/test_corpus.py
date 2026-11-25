import tempfile
import unittest
from pathlib import Path

from imms import assemble, corpus, db, runtime
from imms.util import read_json

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "corpus_mini"
CONFIG = ROOT / "config"


class TestCorpus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "corpus"
        self.manifest = corpus.build_corpus(FIXTURE, self.out)

    def tearDown(self):
        self.tmp.cleanup()

    def test_admission_decisions(self):
        by_id = {e["source_id"]: e for e in self.manifest["entries"]}
        self.assertEqual(self.manifest["summary"]["import"], 1)
        self.assertEqual(self.manifest["summary"]["skip"], 3)
        self.assertEqual(by_id["mini_case"]["decision"], "import")
        self.assertEqual(by_id["no_case"]["decision"], "skip")
        self.assertIn("no_culprit", [r["code"] for r in by_id["no_case"]["reasons"]])
        for key in ("mossvale_fog_lamp", "mossvale_suspect7"):
            self.assertIn("requires_mouse_scene_search", [r["code"] for r in by_id[key]["reasons"]])

    def test_ontology_and_completion(self):
        onto = read_json(self.out / "scripts" / "rpg_mini_case.json")
        self.assertEqual(onto["evaluation"]["victim_name"], "秦重")
        self.assertEqual(onto["evaluation"]["killer_id"], "mini_b")
        self.assertTrue(all(c["id"] in onto["world"]["player_selectable"].split(",") for c in onto["characters"]))
        self.assertEqual(onto["clues"][0]["stage"], 0)
        self.assertTrue(onto["clues"][0]["reveals_fact_ids"])
        self.assertTrue(all(not p.startswith("refer/") for p in onto["source_files"]))
        self.assertTrue(all(c["rule"] in {"R1_victim_annotation", "R3_clue_truth_fallback"}
                            for c in onto["completions"]))
        self.assertTrue(corpus.validate_corpus_onto(onto)["clue_refs_valid"])

    def test_import_and_play(self):
        conn = db.connect(":memory:")
        try:
            report = corpus.import_corpus(conn, self.out, CONFIG)
            self.assertEqual(report["imported"], 1)
            self.assertEqual(report["skipped"], 3)
            session = assemble.assemble(conn, "rpg_mini_case", "mini_a")
            runtime.init_runtime(conn, session)
            state = db.get_game_state(conn, session["session_id"], "runtime")
            result = runtime._vote(conn, session["session_id"], state, session, {"vote": "mini_b"})
            self.assertEqual(result["tally"]["human:mini_a"], 1)
            self.assertIn("ai:mini_b", result["tally"])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
