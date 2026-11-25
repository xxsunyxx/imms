import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from imms import cli, db, ingest, registry, skeleton

ROOT = Path(__file__).resolve().parents[1]
YAML = ROOT / "examples/scripts/snow-night-villa.yaml"
MESSY = ROOT / "tests/fixtures/ingest_mini/messy_script.md"


class TestSkeleton(unittest.TestCase):
    def test_projection_has_fixed_slots_and_gaps(self):
        result = ingest.canonicalize(YAML, llm="template")
        skel = result["skeleton"]
        self.assertEqual(skel["skeleton_version"], skeleton.SKELETON_VERSION)
        self.assertTrue(all(k in skel for k in skeleton.SLOTS))
        self.assertEqual(skel["identity"]["script_id"], "snow-night-villa")
        self.assertEqual(len(skel["participants"]), len(result["onto"]["characters"]))
        self.assertTrue(skel["truth"]["facts"])
        self.assertTrue(skel["clues"])
        self.assertEqual(skel["llm"]["provider"], "template")

    def test_llm_drift_is_contained(self):
        result = ingest.canonicalize(YAML, llm="template")
        base = result["skeleton"]
        candidate = {
            "participants": [
                {"id": "new_guy", "name": "编造的人"},
                {"id": base["participants"][0]["id"], "name": "编造的改名"},
            ],
            "clues": [{"id": base["clues"][0]["id"], "reveals": ["fake_fact"]}],
            "truth": {"summary": "这句完全不存在于原文。"},
            "evil_slot": "x",
        }
        clean, drift = skeleton.validate(candidate, base, "原文里没有任何这些句子")
        codes = {d["reason"] for d in drift}
        self.assertTrue({
            "unknown_slot", "unknown_character", "unknown_fact", "not_in_source"
        } <= codes)
        self.assertEqual(clean["participants"], base["participants"])
        self.assertEqual(clean["clues"][0]["reveals"], base["clues"][0]["reveals"])
        self.assertEqual(clean["truth"]["summary"], base["truth"]["summary"])

    def test_completion_uses_only_source_marks(self):
        result = ingest.canonicalize(MESSY, llm="template")
        skel = result["skeleton"]
        self.assertEqual(skel["evaluation"]["killer"], "c1")
        self.assertTrue(skel["truth"]["summary"])
        rules = {c["rule"] for c in skel["completions"]}
        self.assertIn("S2_killer_from_character", rules)
        self.assertNotIn("evaluation.killer", [g["slot"] for g in skel["gaps"]])

    def test_storage_writes_skeleton_alongside_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "corpus"
            result = ingest.ingest_source(MESSY, root, llm="template")
            rel = result["entry"]["skeleton_file"]
            stored = json.loads((root / rel).read_text(encoding="utf-8"))
            self.assertEqual(stored["skeleton_version"], skeleton.SKELETON_VERSION)
            self.assertEqual(stored["source_sha256"], result["source_sha256"])

    def test_cli_skeleton_command(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(["--db", ":memory:", "ingest", "skeleton", "--path", str(MESSY),
                      "--llm", "template"])
        data = json.loads(out.getvalue())
        self.assertEqual(data["decision"], "import")
        self.assertTrue(data["skeleton"]["truth"]["summary"])


class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")

    def tearDown(self):
        self.conn.close()

    def test_lifecycle_versions_and_history(self):
        registry.register(self.conn, "demo", title="演示本", source_kind="section_text",
                          canonical_version="1.0")
        version = registry.save_skeleton(self.conn, "demo", {
            "skeleton_version": "1.0", "gaps": [{"slot": "timeline.events"}],
        })
        self.assertEqual(version, 1)
        self.assertEqual(registry.get(self.conn, "demo")["status"], "staged")
        registry.promote(self.conn, "demo", "normalized", "L1 generated")
        registry.promote(self.conn, "demo", "ready", "audit passed")
        with self.assertRaises(ValueError):
            registry.promote(self.conn, "demo", "staged")
        actions = [e["action"] for e in registry.history(self.conn, "demo")]
        self.assertEqual(actions[:3], ["register", "skeleton_saved", "status"])
        self.assertEqual(registry.skeleton(self.conn, "demo")["version"], 1)

    def test_import_registers_ingested_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "corpus"
            ingest.ingest_source(YAML, root, llm="template")
            from imms import corpus
            corpus.import_corpus(self.conn, root, ROOT / "config")
            entry = registry.get(self.conn, "snow-night-villa")
            self.assertEqual(entry["status"], "normalized")
            self.assertEqual(entry["source_kind"], "structured_yaml")
            self.assertEqual(registry.skeleton(self.conn, "snow-night-villa")["version"], 1)


if __name__ == "__main__":
    unittest.main()
