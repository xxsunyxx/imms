import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from imms import cli, db, script_quality


def _onto(script_id, **overrides):
    onto = {
        "script_id": script_id, "title": script_id,
        "characters": [{"id": "a", "name": "甲"}, {"id": "b", "name": "乙"}],
        "clues": [{"id": "c1", "reveals_fact_ids": ["f1"]}],
        "clue_pool": ["c1 线索|logLR:a=1.5"],
        "clue_fact_map": {"c1": ["f1"]},
        "facts": [{"id": "f1", "visibility": "private"}],
        "timeline_true": [{"time": "20:00", "event": "甲在场"}],
        "endings": ["正确", "错误"],
        "evaluation": {
            "killer_id": "a", "required_fact_ids": ["f1"], "motive_fact_id": "f1",
        },
        "source_files": ["examples/scripts/sample.yaml"],
    }
    onto.update(overrides)
    return onto


class TestScriptQuality(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = db.connect(self.root / "q.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_tiers_and_source_labels(self):
        full = script_quality.classify(_onto("full"))
        self.assertEqual(full["tier"], "explicit_full")
        self.assertEqual(full["source_type"], "yaml_explicit")

        partial = _onto("partial")
        partial["evaluation"] = {"killer_id": "a"}
        self.assertEqual(script_quality.classify(partial)["tier"], "explicit_partial")

        minimal = _onto("minimal")
        minimal["timeline_true"] = []
        minimal["evaluation"] = {"killer_id": "a"}
        self.assertEqual(script_quality.classify(minimal)["tier"], "explicit_minimal")

        narrative = _onto("narrative", evaluation={}, clues=[], clue_pool=[], facts=[])
        result = script_quality.classify(narrative)
        self.assertEqual(result["tier"], "narrative_only")
        self.assertIn("no_declared_killer", result["gaps"])

        empty = _onto("empty", evaluation={}, clues=[], clue_pool=[], facts=[],
                      timeline_true=[], endings=[])
        self.assertEqual(script_quality.classify(empty)["tier"], "insufficient")

        boxed = _onto("boxed", source_files=["refer/mystery-scripts-100/盒装本/C01/x.md"])
        self.assertEqual(script_quality.classify(boxed)["source_type"], "boxed_md")

    def test_summary_counts_are_derived_from_stored_scripts(self):
        partial = _onto("partial")
        partial["evaluation"] = {"killer_id": "a"}
        empty = _onto("empty", evaluation={}, clues=[], clue_pool=[], facts=[],
                      timeline_true=[], endings=[])
        for into in (_onto("full"), partial, empty):
            db.save_script(self.conn, into)
        report = script_quality.summarize(self.conn)
        self.assertEqual(report["total"], 3)
        self.assertEqual(report["by_tier"]["explicit_full"], 1)
        self.assertEqual(report["by_tier"]["explicit_partial"], 1)
        self.assertEqual(report["by_tier"]["insufficient"], 1)
        self.assertEqual(report["by_source_type"]["yaml_explicit"], 3)

    def test_derived_manifest_does_not_touch_source(self):
        script = _onto("script_a")
        script_path = self.root / "script_a.json"
        script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
        manifest_path = self.root / "manifest.json"
        manifest = {
            "admission_rules": ["no_invention"],
            "entries": [
                {
                    "decision": "import", "script_file": "script_a.json",
                    "source_kind": "yaml_explicit", "raw_files": [],
                },
                {"decision": "skip", "reasons": [{"code": "no_culprit"}]},
            ],
        }
        original = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
        manifest_path.write_text(original, encoding="utf-8")
        db.save_script(self.conn, script)

        out = self.root / "quality_manifest.json"
        derived = script_quality.build_quality_manifest(self.conn, manifest_path, out)
        self.assertEqual(derived["entries"][0]["quality"]["tier"], "explicit_full")
        self.assertEqual(derived["entries"][1]["quality"]["tier"], "insufficient")
        self.assertEqual(derived["inventory_summary"]["total"], 1)
        self.assertEqual(json.dumps(json.loads(manifest_path.read_text(encoding="utf-8")),
                                    ensure_ascii=False, sort_keys=True), original)
        self.assertTrue(out.exists())

    def test_cli_quality_and_audit_all_by_tier(self):
        db.save_script(self.conn, _onto("full"))
        parser = cli.build_parser()
        args = parser.parse_args(["quality", "summary"])
        self.assertEqual(args.quality_action, "summary")
        args = parser.parse_args(["audit-all", "--by-tier"])
        self.assertTrue(args.by_tier)

        sink = io.StringIO()
        with contextlib.redirect_stdout(sink):
            cli.main(["--db", str(self.root / "q.db"), "audit-all", "--by-tier"])
        report = json.loads(sink.getvalue())
        self.assertIn("by_tier", report)
        self.assertEqual(report["by_tier"]["explicit_full"], 1)
        self.assertIn("admitted", report)

        sink = io.StringIO()
        with contextlib.redirect_stdout(sink):
            cli.main(["--db", str(self.root / "q.db"), "quality", "summary",
                      "--manifest", str(self.root / "missing.json")])
        quality = json.loads(sink.getvalue())
        self.assertEqual(quality["total"], 1)
        self.assertNotIn("manifest", quality)


if __name__ == "__main__":
    unittest.main()
