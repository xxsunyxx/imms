"""Guide library: managed wording rules, with hard data-injection gates."""

import tempfile
import unittest
from pathlib import Path

import yaml

from imms import db, guides, prompt_engine

from _fixture import ROOT, make_session_db


class GuideLibraryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.conn, self.session = make_session_db(self.tmp.name)
        self.sid = self.session["session_id"]
        self.seat = next(p["id"] for p in self.session["participants"] if p["kind"] != "gm")
        self.result = guides.import_guides(self.conn)

    def tearDown(self):
        self.conn.close()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_import_loads_all_default_guides(self):
        self.assertEqual(self.result["rejected"], [])
        self.assertGreaterEqual(len(self.result["imported"]), 10)
        self.assertGreaterEqual(len(guides.list_guides(self.conn)), 10)

    def test_import_rejects_fact_ids_and_assignments(self):
        source = Path(self.tmp.name).with_suffix(".yaml")
        doc = {
            "version": 9,
            "rules": [
                {"id": "ok", "purpose": "tone", "text": "语气更冷。"},
                {"id": "bad_fact", "purpose": "tone", "text": "提到 f_secret_01 即可。"},
                {"id": "bad_assign", "purpose": "tone", "text": "arousal=0.9 时这么说。"},
            ],
        }
        source.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
        result = guides.import_guides(self.conn, source)
        reasons = {r["id"]: r["reason"] for r in result["rejected"]}
        self.assertEqual(reasons["bad_fact"], "contains_fact_id")
        self.assertEqual(reasons["bad_assign"], "contains_assignment")
        self.assertIn("ok", result["imported"])
        self.assertEqual([r["guide_id"] for r in guides.list_guides(self.conn)], ["ok"])

    def test_select_is_deterministic_and_purpose_filtered(self):
        a = guides.select(self.conn, purpose="aftermath", limit=2, session_id=self.sid,
                          seat_id=self.seat, round_no=3)
        b = guides.select(self.conn, purpose="aftermath", limit=2, session_id=self.sid,
                          seat_id=self.seat, round_no=3)
        self.assertEqual(a, b)
        self.assertTrue(all(isinstance(x, str) and x for x in a))
        for purpose in ("aftermath", "tone", "improv", "delivery", "pacing"):
            selected = guides.select(self.conn, purpose=purpose, limit=1,
                                     session_id=self.sid, round_no=1)
            self.assertEqual(len(selected), 1)

    def test_selected_guides_never_inject_facts(self):
        state = {
            "l1": {"OCEAN.O": 0.5, "lexicon": "平静"},
            "emotion": {"arousal": 0.2, "stress": 0.1, "valence": 0.0},
            "relations": {}, "suspicion": {}, "beliefs": {"public_timeline": {}},
            "lie_ledger": [],
        }
        selected = guides.select(self.conn, purpose="tone", limit=3,
                                 session_id=self.sid, round_no=1)
        base = prompt_engine.build_from_state(
            state, "assert", "在座各位", "现有线索", 1,
            sorted(state.get("beliefs", {})),
        )
        guided = prompt_engine.build_from_state(
            state, "assert", "在座各位", "现有线索", 1,
            sorted(state.get("beliefs", {})), guides=selected,
        )
        self.assertIn("表演指引", guided)
        self.assertNotIn("f_", guided)
        self.assertNotIn("clue_", guided)
        # The fact block is identical with or without guides.
        view = dict(state)
        ctx = {"allowed_facts": ["public_timeline"]}
        self.assertEqual(
            prompt_engine.build(view, ctx, blocks=["beliefs"]),
            prompt_engine.build(view, dict(ctx, guides=selected), blocks=["beliefs"]),
        )
        self.assertIn("public_timeline", guided)

    def test_guides_for_prompt_matches_select(self):
        kw = dict(purpose="tone", limit=1, session_id=self.sid, seat_id=self.seat, round_no=2)
        self.assertEqual(
            guides.guides_for_prompt(self.conn, **kw),
            guides.select(self.conn, **kw),
        )


if __name__ == "__main__":
    unittest.main()
