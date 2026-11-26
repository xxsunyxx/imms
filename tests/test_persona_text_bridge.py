"""Bridge: script text -> L1 text slots and L2 assigned identity (A fix).

Contract: text slots carry verbatim script text with source=script_text,
are never scored as behavioural evidence, and reach the prompt identity block.
"""

import os
import tempfile
import unittest

from imms import db, normalize, prompt_engine
from tests._fixture import ROOT


class PersonaTextBridgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.conn = db.connect(self.tmp.name)
        from imms import ontology
        self.onto = ontology.parse_script_dir(ROOT / "examples" / "scripts" / "snow-night-villa.yaml")
        db.save_script(self.conn, self.onto)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.tmp.name)

    def test_text_slots_are_verbatim_and_marked_script_text(self):
        doc, _ = normalize.normalize_persona(self.onto, ROOT / "config")
        persona = next(p for p in doc["personas"] if p["character_id"] == "lin_wanqiu")
        slots = {a["key"]: a for a in persona["attributes"]}
        for key in normalize.TEXT_SLOTS:
            self.assertIn(key, slots)
        # When the script has no text, the slot must be an explicit null, not a guess.
        for key, slot in slots.items():
            if slot["source"] == "script_text":
                self.assertIn(key, normalize.TEXT_SLOTS)
                if slot["value"] is None:
                    self.assertEqual(slot["reason"], "missing_evidence")
                else:
                    self.assertEqual(slot["confidence"], 1.0)
                    self.assertTrue(slot["evidence"])

    def test_text_slots_never_get_bin_scores(self):
        # A character with real canon_evidence still keeps text slots out of F29.
        onto = dict(self.onto)
        chars = []
        for ch in onto["characters"]:
            ch = dict(ch)
            ch["canon_evidence"] = [
                {"key": "OCEAN.O", "raw_e": 0.7, "quote": "主动联想、好设问", "locator": "c1"},
                {"key": "OCEAN.O", "raw_e": 0.9, "quote": "抽象推演成习惯", "locator": "c2"},
            ]
            chars.append(ch)
        onto["characters"] = chars
        doc, _ = normalize.normalize_persona(onto, ROOT / "config")
        persona = next(p for p in doc["personas"] if p["character_id"] == "lin_wanqiu")
        for attr in persona["attributes"]:
            if attr["key"] in normalize.TEXT_SLOTS:
                self.assertEqual(attr["source"], "script_text")
                self.assertNotIn("anchor_bin", str(attr.get("evidence", [])))

    def test_prompt_renders_persona_text(self):
        state = {
            "l1": {
                "personality": "温和表态，先听大家讲",
                "speaking_style": "简短克制，少用感叹",
                "lexicon": None,
            },
            "l0": {},
        }
        rendered = prompt_engine.block_identity(
            {"l1": state["l1"], "l0": {}}, {"round_no": 1}
        )
        text = "\n".join(rendered)
        self.assertIn("温和表态，先听大家讲", text)
        self.assertIn("简短克制，少用感叹", text)  # lexicon fallback to speaking_style

    def test_missing_text_stays_unconfigured(self):
        rendered = prompt_engine.block_identity({"l1": {}, "l0": {}}, {"round_no": 1})
        text = "\n".join(rendered)
        self.assertIn("人设描述：（未配置）", text)
        self.assertIn("语言指纹：（未配置）", text)


if __name__ == "__main__":
    unittest.main()
