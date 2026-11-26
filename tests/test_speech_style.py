"""Speech-style engine: budget caps, no fabrication, three gates, ledger."""

import tempfile
import unittest
from pathlib import Path

from imms import db, prompt_engine, speech_style


def _view(**overrides):
    base = {
        "character_id": "c1", "name": "测试角色", "public_identity": "身份A",
        "l1": {
            "OCEAN.O": 0.7, "OCEAN.C": 0.9, "OCEAN.E": 0.5,
            "OCEAN.A": 0.6, "OCEAN.N": 0.3, "values.honesty": 0.4,
            "personality": "温和表态",
            "speaking_style": "先听大家讲",
        },
        "l0": {"mask": None},
        "emotion": {"arousal": 0.3, "valence": -0.29, "stress": 0.4},
        "relations": {"c2": {"like": -1.0, "trust": -1.0, "suspicion": 0.63}},
        "accumulator": {"A": 1.37},
        "lie_ledger": [],
        "facets": {},
    }
    base.update(overrides)
    return base


def _ctx(**overrides):
    base = {"session_id": "S", "seat_id": "ai:c1", "round_no": 1,
            "seed": 42, "target_name": "c2", "allowed_facts": ["f1"]}
    base.update(overrides)
    return base


class TestFingerprintNoFabrication(unittest.TestCase):
    def test_missing_fields_stay_empty(self):
        fp = speech_style.extract_fingerprint(_view())
        for key in ("syntax", "rhythm", "catchphrase"):
            self.assertIsNone(fp[key], key)
        self.assertEqual(fp["sample_lines"], [])
        self.assertIn("sample_lines", fp["missing"])

    def test_verbatim_text_and_evidence_quotes(self):
        fp = speech_style.extract_fingerprint(
            _view(), {"evidence_quotes": ["原文证据一", "原文证据二"]}
        )
        self.assertEqual(fp["lexicon"], "先听大家讲")
        self.assertEqual(fp["evidence_quotes"], ["原文证据一", "原文证据二"])

    def test_tone_derived_not_invented(self):
        fp = speech_style.extract_fingerprint(_view())
        self.assertIn("用词", fp["tone_baseline"])
        self.assertIn("结构", fp["tone_baseline"])


class TestDriftBudget(unittest.TestCase):
    def test_caps_and_total_hard_top(self):
        # Maximum signals: every dimension must stay within its own cap and
        # the sum must never exceed 0.20.
        view = _view(
            emotion={"arousal": 1.0, "valence": -1.0, "stress": 1.0},
            accumulator={"A": 10.0},
            relations={"c2": {"like": 1.0, "trust": 1.0, "suspicion": 1.0}},
            l0={"mask": "柔弱"},
        )
        rec, triggers, sig = speech_style.allocate_drift(view, _ctx())
        for key, cap in speech_style.CAPS.items():
            self.assertLessEqual(getattr(rec, key), cap + 1e-9, key)
        self.assertLessEqual(rec.total(), 0.20 + 1e-9)
        self.assertTrue(triggers)

    def test_deterministic_noise_same_seed(self):
        a, _, _ = speech_style.allocate_drift(_view(), _ctx(seed=42))
        b, _, _ = speech_style.allocate_drift(_view(), _ctx(seed=42))
        self.assertEqual(a.noise, b.noise)

    def test_noise_changes_with_seed(self):
        a, _, _ = speech_style.allocate_drift(_view(), _ctx(seed=1))
        b, _, _ = speech_style.allocate_drift(_view(), _ctx(seed=2))
        self.assertNotEqual(a.noise, b.noise)


class TestAssemblyAndAudit(unittest.TestCase):
    def test_three_tagged_blocks(self):
        parts = speech_style.render(_view(), _ctx())
        for tag in ("[ANCHOR]", "[/ANCHOR]", "[DRIFT]", "[/DRIFT]",
                    "[BOUNDARY]", "[/BOUNDARY]"):
            self.assertIn(tag, parts["prompt"])
        self.assertIn("原文证据句", parts["anchor"])

    def test_structure_ratio_is_measured(self):
        parts = speech_style.render(_view(), _ctx())
        self.assertGreater(parts["structure_ratio"], 0)
        self.assertLess(parts["structure_ratio"], 1)
        # No sample_lines in schema -> cannot claim the 0.78-0.82 target.
        self.assertFalse(parts["structure_ok"])

    def test_sample_lines_raise_anchor_share(self):
        # Verbatim lines, when they actually exist, raise the anchor share.
        # The engine never fabricates them to hit 0.78 by force.
        view, ctx = _view(), _ctx()
        fp = speech_style.extract_fingerprint(view, ctx)
        dr, _, _ = speech_style.allocate_drift(view, ctx)
        variants = speech_style.generate_variants(fp, dr, "c2")
        without = speech_style.structure_ratio(
            speech_style.assemble(fp, variants, dr, view, ctx))
        fp["sample_lines"] = ["原文台词甲乙丙丁" * 6 for _ in range(3)]
        with_lines = speech_style.structure_ratio(
            speech_style.assemble(fp, variants, dr, view, ctx))
        self.assertGreater(with_lines, without)

    def test_render_reports_target_miss_honestly(self):
        # Current schema has no verbatim lines: structure_ok must be False,
        # and the gap is listed instead of being silently padded.
        parts = speech_style.render(_view(), _ctx())
        self.assertFalse(parts["structure_ok"])
        self.assertIn("sample_lines", parts["missing"])

    def test_audit_proxy_is_declared(self):
        result = speech_style.audit("测试台词", speech_style.extract_fingerprint(_view()),
                                    speech_style.DriftRecord())
        self.assertEqual(result["cosine_kind"], "character_bigram_proxy")
        self.assertIn("anchor_cos_proxy", result)

    def test_taboo_detection(self):
        fp = speech_style.extract_fingerprint(_view())
        fp["taboo_words"] = ["禁忌"]
        result = speech_style.audit("这是一句禁忌的话", fp, speech_style.DriftRecord())
        self.assertFalse(result["checks"]["taboo_clean"])


class TestThreeGates(unittest.TestCase):
    def test_post_gate_forces_baseline_after_three_over_budget(self):
        self.assertTrue(speech_style.should_force_baseline([0.19, 0.19, 0.19]))
        self.assertFalse(speech_style.should_force_baseline([0.19, 0.19, 0.10]))
        self.assertFalse(speech_style.should_force_baseline([0.19, 0.19]))

    def test_forced_baseline_zeroes_drift(self):
        parts = speech_style.render(
            _view(), _ctx(recent_drift_totals=[0.19, 0.19, 0.19])
        )
        self.assertTrue(parts["forced_baseline"])
        self.assertEqual(parts["drift_record"]["total"], 0.0)
        self.assertTrue(any("post_gate" in t for t in parts["triggers"]))


class TestPromptIntegration(unittest.TestCase):
    def test_style_block_registered(self):
        self.assertIn("style", prompt_engine.BLOCKS)
        self.assertLess(prompt_engine.BLOCK_ORDER.index("style"),
                        prompt_engine.BLOCK_ORDER.index("assigned"))

    def test_block_empty_without_parts(self):
        self.assertEqual(prompt_engine.block_style({}, {}), [])


class TestLedger(unittest.TestCase):
    def test_append_and_read_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            conn = db.connect(Path(d) / "t.db")
            db.append_drift_record(
                conn, "S", 1, "ai:c1", "c1",
                {"emotion": 0.06, "relation": 0.05, "mask": 0, "stress": 0,
                 "noise": 0.02, "total": 0.13},
                target="c2", anchor_cos=0.79, triggers=["relation:x"],
                structure_ratio=0.53, forced_baseline=False,
            )
            rows = db.get_drift_ledger(conn, "S")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["drift_total"], 0.13)
            self.assertEqual(rows[0]["outcome"], "pass")
            self.assertEqual(rows[0]["target"], "c2")
            rows2 = db.get_drift_ledger(conn, "S", "ai:c1")
            self.assertEqual(len(rows2), 1)
            rows3 = db.get_drift_ledger(conn, "S", "ai:other")
            self.assertEqual(len(rows3), 0)
            conn.close()


if __name__ == "__main__":
    unittest.main()
