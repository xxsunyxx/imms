"""Anonymized trait-card import: evidence-backed, name-free, replayable."""

import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from imms import archetypes, db
from imms.util import read_json

ROOT = Path(__file__).resolve().parents[1]
CARDS = read_json(ROOT / "config" / "archetypes.json")


class ArchetypeImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_import_creates_nine_evidence_backed_cards(self):
        report = archetypes.import_cards(self.conn, CARDS)
        self.assertEqual(report["characters"], 9)
        self.assertEqual(len(set(report["names"])), 9)
        self.assertEqual(report["script_id"], "trait-archetypes-001")

        doc = db.get_persona_l1(self.conn, CARDS["script_id"])
        self.assertEqual(len(doc["personas"]), 9)
        for card, persona in zip(CARDS["cards"], doc["personas"]):
            attrs = {a["key"]: a for a in persona["attributes"]}
            self.assertEqual(attrs["identity.name"]["value"], card["name"])
            self.assertEqual(attrs["identity.role"]["value"], card["role"])
            self.assertEqual(attrs["trait.tags"]["value"], card["traits"])
            self.assertEqual(attrs["speech.habit_phrases"]["value"], card["speech_habits"])
            self.assertTrue(attrs["speech.habit_phrases"]["evidence"])
            for key, spec in card["canon"].items():
                env = attrs[key]
                self.assertIsNotNone(env["value"])
                self.assertGreater(env["confidence"], 0)
                self.assertEqual(len(env["evidence"]), len(spec["bins"]))
            # No evidence means null, never an invented number.
            self.assertIsNone(attrs["health"]["value"])
            self.assertIsNone(attrs["physique"]["value"])

    def test_source_identity_guard_fails_closed(self):
        self.assertTrue(archetypes.validate_cards(CARDS))
        original = archetypes.FORBIDDEN_DIGESTS
        probe = "测试来源名"
        try:
            archetypes.FORBIDDEN_DIGESTS = original | {
                hashlib.sha256(probe.encode("utf-8")).hexdigest()[:16]
            }
            bad = copy.deepcopy(CARDS)
            bad["cards"][0]["name"] = probe
            with self.assertRaises(ValueError) as ctx:
                archetypes.validate_cards(bad)
            self.assertEqual(str(ctx.exception), "ERR_SOURCE_IDENTITY_LEAK")
        finally:
            archetypes.FORBIDDEN_DIGESTS = original

    def test_l1_rows_are_immutable(self):
        archetypes.import_cards(self.conn, CARDS)
        with self.assertRaises(Exception):
            self.conn.execute(
                "UPDATE persona_l1 SET data_json='{}' WHERE script_id=?",
                (CARDS["script_id"],),
            )

    def test_reimport_creates_a_new_extraction_version(self):
        first = archetypes.import_cards(self.conn, CARDS)
        second = archetypes.import_cards(self.conn, CARDS)
        self.assertEqual(first["extraction_version"], 1)
        self.assertEqual(second["extraction_version"], 2)
        self.assertEqual(first["names"], second["names"])


if __name__ == "__main__":
    unittest.main()
