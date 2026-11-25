import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from imms import cli, db, ingest
from imms.util import read_json

ROOT = Path(__file__).resolve().parents[1]
BOXED = ROOT / "tests" / "fixtures" / "boxed_mini"
CORPUS_MINI = ROOT / "tests" / "fixtures" / "corpus_mini"
MESSY = ROOT / "tests" / "fixtures" / "ingest_mini" / "messy_script.md"


class TestIngest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "corpus"

    def tearDown(self):
        self.tmp.cleanup()

    def test_detection_covers_all_source_kinds(self):
        cases = [
            (BOXED, "boxed_dir"),
            (BOXED / "本盒完整内容.md", "boxed_md"),
            (CORPUS_MINI / "RolePlayingGame-backup-20261022/backend/resources/scripts/mini_case.json", "rpg_json"),
            (ROOT / "examples/scripts/snow-night-villa.yaml", "structured_yaml"),
            (MESSY, "section_text"),
            (CORPUS_MINI / "mossvale/mystery/server/scenario.mjs", "interactive_scene_game"),
        ]
        for path, expected in cases:
            with self.subTest(path=path):
                self.assertEqual(ingest.detect(path)["kind"], expected)

    def test_rpg_conversion_and_completion_ledger(self):
        path = CORPUS_MINI / "RolePlayingGame-backup-20261022/backend/resources/scripts/mini_case.json"
        result = ingest.canonicalize(path)
        self.assertEqual(result["decision"], "import")
        onto = result["onto"]
        self.assertEqual(onto["schema_version"], "1.0")
        self.assertEqual(onto["script_id"], "rpg_mini_case")
        self.assertEqual(onto["evaluation"]["victim_name"], "秦重")
        rules = {c["rule"] for c in onto["completions"]}
        self.assertIn("R1_victim_annotation", rules)
        self.assertTrue(rules <= {"R1_victim_annotation", "R3_clue_truth_fallback"})
        self.assertTrue(all(ingest.WEIGHT_RE.search(line) for line in onto["clue_pool"]))

    def test_boxed_dir_and_single_file(self):
        for path in (BOXED, BOXED / "本盒完整内容.md"):
            with self.subTest(path=path):
                result = ingest.canonicalize(path)
                self.assertEqual(result["decision"], "import")
                self.assertEqual(result["onto"]["script_id"], "Z01")
                self.assertGreaterEqual(len(result["onto"]["characters"]), 2)
                self.assertTrue(result["onto"]["truth_graph"])

    def test_messy_markdown_is_repaired_from_source_only(self):
        result = ingest.canonicalize(MESSY)
        self.assertEqual(result["decision"], "import")
        onto = result["onto"]
        self.assertEqual(onto["schema_version"], "1.0")
        self.assertEqual(onto["evaluation"]["killer_id"], "c1")
        rules = {c["rule"] for c in onto["completions"]}
        self.assertTrue({
            "S2_killer_from_character", "G4_fact_projection",
            "G1_clue_projection", "G2_clue_weight_normalization",
        } <= rules)
        self.assertTrue(all(f["content"] for f in onto["facts"]))
        self.assertEqual(len(onto["clues"]), 2)
        self.assertTrue(all(ingest.WEIGHT_RE.search(line) for line in onto["clue_pool"]))
        self.assertTrue(all(c["reveals_fact_ids"] for c in onto["clues"]))

    def test_scene_game_is_rejected(self):
        path = CORPUS_MINI / "mossvale/mystery/server/scenario.mjs"
        result = ingest.canonicalize(path)
        self.assertEqual(result["decision"], "skip")
        self.assertEqual(result["reasons"][0]["code"], "requires_mouse_or_image")
        self.assertIsNone(result["onto"])

    def test_broken_script_keeps_gate_reasons(self):
        path = CORPUS_MINI / "RolePlayingGame-backup-20261022/backend/resources/scripts/no_case.json"
        result = ingest.canonicalize(path)
        self.assertEqual(result["decision"], "skip")
        codes = {r["code"] for r in result["reasons"]}
        self.assertTrue({"no_culprit", "no_evidence_chain"} <= codes)
        self.assertNotIn("parse_error", codes)

    def test_storage_is_self_contained_and_idempotent(self):
        path = CORPUS_MINI / "RolePlayingGame-backup-20261022/backend/resources/scripts/mini_case.json"
        first = ingest.ingest_source(path, self.root)
        script_file = self.root / first["entry"]["script_file"]
        raw_rel = first["entry"]["raw_files"][0]
        self.assertEqual((self.root / raw_rel).read_bytes(), path.read_bytes())
        payload = script_file.read_bytes()
        checks = ingest.validate_canonical(script_file)
        self.assertTrue(all(checks.values()))
        canonical = read_json(script_file)
        self.assertEqual(canonical["source_files"], [raw_rel])
        self.assertEqual(canonical["ingest"]["source_sha256"], first["source_sha256"])

        second = ingest.ingest_source(path, self.root)
        self.assertEqual(second["entry"]["script_id"], first["entry"]["script_id"])
        self.assertEqual(script_file.read_bytes(), payload)
        self.assertEqual(len(read_json(self.root / "manifest.json")["entries"]), 1)

        # Re-ingesting from the stored raw copy must not copy a file onto itself.
        again = ingest.ingest_source(self.root / raw_rel, self.root)
        self.assertEqual(again["decision"], "import")
        self.assertEqual(again["entry"]["script_id"], first["entry"]["script_id"])
        before_json, after_json = json.loads(payload), read_json(script_file)
        self.assertEqual(after_json["clue_pool"], before_json["clue_pool"])
        self.assertEqual(after_json["characters"], before_json["characters"])

    def test_batch_and_report(self):
        report = ingest.ingest_tree(CORPUS_MINI, self.root)
        self.assertEqual(report["processed"], 4)
        summary = ingest.ingest_report(self.root)["summary"]
        self.assertEqual(summary["import"], 1)
        self.assertEqual(summary["skip"], 3)
        by_kind = ingest.ingest_report(self.root)["by_kind"]
        self.assertEqual(by_kind["interactive_scene_game/skip"], 2)

    def test_cli_wiring(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(["--db", ":memory:", "ingest", "detect", "--path", str(MESSY)])
        self.assertEqual(json.loads(out.getvalue())["kind"], "section_text")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(["--db", ":memory:", "parse", "--script", str(MESSY)])
        parsed = json.loads(out.getvalue())
        self.assertEqual(parsed["script_id"], "账房疑云")
        self.assertTrue(parsed["stored"])


if __name__ == "__main__":
    unittest.main()
