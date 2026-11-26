"""Speech-style engine: data-derived slots, budget caps, three gates, ledger."""

import tempfile
import unittest
from pathlib import Path

from imms import db, prompt_engine, speech_style

STRUCTURE_MIN = speech_style.STRUCTURE_MIN
STRUCTURE_MAX = speech_style.STRUCTURE_MAX


def _view(**overrides):
    base = {
        "character_id": "c1", "name": "测试角色", "public_identity": "身份A",
        "l1": {
            "OCEAN.O": 0.7, "OCEAN.C": 0.9, "OCEAN.E": 0.5,
            "OCEAN.A": 0.6, "OCEAN.N": 0.3, "values.honesty": 0.4,
            "personality": "温和表态「我不动老人，先听大家讲」",
            "speaking_style": "「我是测试角色，先听大家讲。」",
        },
        "l0": {"mask": None, "play_style": "steady", "acting_skill": 0.5,
               "lying_skill": 0.5, "difficulty": 3},
        "emotion": {"arousal": 0.3, "valence": -0.29, "stress": 0.4,
                    "dominance": 0.13, "exposure_risk": 0.5, "cognitive_load": 0.1},
        "relations": {"c2": {"like": -1.0, "trust": -1.0, "suspicion": 0.63,
                             "grudge": 1.0, "debt": -1.0}},
        "accumulator": {"A": 1.37, "theta": 0.7},
        "breakout": {"state": "charged"},
        "delivery": {"probability": 0.34, "hit": True, "item": "改口你为您",
                     "params": {"sentence_length_factor": 0.88,
                                "ellipsis_density_factor": 1.48,
                                "question_density_factor": 1.27,
                                "honorific_step": 1, "pet_phrase_factor": 1.18,
                                "pause_marks": 0, "self_corrections": 0,
                                "vernacular_probability": 0.08,
                                "inversion_probability": 0.08,
                                "verbal_flourish_factor": 0.97}},
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


class TestFingerprintFromExistingData(unittest.TestCase):
    def test_slots_derived_from_stored_fields(self):
        fp = speech_style.extract_fingerprint(_view(), _ctx(
            evidence_quotes=["原文证据一", "原文证据二"]))
        self.assertEqual(fp["catchphrase"], "我不动老人，先听大家讲")
        self.assertIn("条目式", fp["syntax"])
        self.assertIn("节奏稳定", fp["rhythm"])
        self.assertIn("表演风格=steady", fp["rhythm"])
        self.assertEqual(fp["sample_lines"], ["原文证据一", "原文证据二"])
        # Only genuinely absent schema fields are reported as missing.
        self.assertEqual(fp["missing"], ["taboo_words"])

    def test_no_quote_and_no_evidence_reports_only_real_gaps(self):
        view = _view(l1={"OCEAN.C": 0.9, "OCEAN.N": 0.3})
        fp = speech_style.extract_fingerprint(view)
        self.assertIsNone(fp["catchphrase"])
        self.assertEqual(fp["sample_lines"], [])
        self.assertEqual(set(fp["missing"]),
                         {"catchphrase", "sample_lines", "taboo_words"})

    def test_verbatim_text_and_evidence_quotes(self):
        fp = speech_style.extract_fingerprint(
            _view(), {"evidence_quotes": ["原文证据一", "原文证据二"]}
        )
        self.assertEqual(fp["lexicon"], "「我是测试角色，先听大家讲。」")
        self.assertEqual(fp["evidence_quotes"], ["原文证据一", "原文证据二"])

    def test_tone_derived_not_invented(self):
        fp = speech_style.extract_fingerprint(_view())
        self.assertIn("O:", fp["tone_baseline"])
        self.assertIn("C:", fp["tone_baseline"])
        self.assertEqual(len(fp["tone_baseline"].split("；")), 5)

    def test_lexicon_covers_all_relation_and_emotion_dimensions(self):
        lib = speech_style.lexicon()
        self.assertEqual(len(lib["relations"]), 11)
        self.assertGreaterEqual(len(lib["emotion"]), 7)
        for dim in ("OCEAN.O", "OCEAN.C", "OCEAN.E", "OCEAN.A", "OCEAN.N"):
            self.assertEqual(len(lib["ocean"][dim]["bands"]), 5)

    def test_relation_and_emotion_terms_from_stored_fields(self):
        view = _view()
        rel = speech_style.relation_terms(view, "c2")
        emo = speech_style.emotion_terms(view)
        self.assertTrue(any("like" in x for x in rel))
        self.assertTrue(any("grudge" in x for x in rel))
        self.assertTrue(any("exposure_risk" in x or "valence" in x for x in emo))

    def test_l0_and_ips_terms_present(self):
        view = _view()
        self.assertTrue(any("表演" in x or "谎言" in x or "谨慎" in x
                            for x in speech_style.l0_terms(view)))
        self.assertTrue(speech_style.ips_terms(view))

    def test_variants_compile_ips_delivery_params(self):
        view = _view()
        fp = speech_style.extract_fingerprint(view)
        dr, _, _ = speech_style.allocate_drift(view, _ctx())
        v = speech_style.generate_variants(fp, dr, "c2", view)
        self.assertEqual(v["sentence_length_factor"], 0.88)
        self.assertEqual(v["honorific_step"], 1)
        self.assertEqual(v["performance_item"], "改口你为您")

    def test_emotion_signal_uses_real_dominance(self):
        low = speech_style._emotion_signal({"arousal": 0.3, "valence": 0, "dominance": -1.0})
        high = speech_style._emotion_signal({"arousal": 0.3, "valence": 0, "dominance": 1.0})
        self.assertGreater(low, high)

    def test_noise_comes_from_sampled_delivery(self):
        view = _view(delivery={"probability": 0.91, "hit": False, "item": None,
                               "params": {}})
        rec, _, sig = speech_style.allocate_drift(view, _ctx())
        self.assertEqual(sig["noise"], 0.91)


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

    def test_noise_falls_back_to_seed_without_delivery(self):
        # With no IPS delivery sampled, noise comes from the deterministic seed.
        view = _view(delivery=None)
        a, _, _ = speech_style.allocate_drift(view, _ctx(seed=1))
        b, _, _ = speech_style.allocate_drift(view, _ctx(seed=2))
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

    def test_sample_lines_raise_anchor_share(self):
        # Verbatim evidence quotes raise the anchor share; the engine never
        # fabricates lines to hit 0.78 by force.
        view, ctx = _view(), _ctx()
        fp = speech_style.extract_fingerprint(view, ctx)
        dr, _, _ = speech_style.allocate_drift(view, ctx)
        variants = speech_style.generate_variants(fp, dr, "c2", view)
        without = speech_style.structure_ratio(
            speech_style.assemble(fp, variants, dr, view, ctx))
        fp["sample_lines"] = ["原文证据甲乙丙丁" * 6 for _ in range(5)]
        with_lines = speech_style.structure_ratio(
            speech_style.assemble(fp, variants, dr, view, ctx))
        self.assertGreater(with_lines, without)

    def test_render_reports_only_real_gaps(self):
        # With evidence quotes supplied, sample_lines is filled; the only
        # remaining gap is a schema field IMMS genuinely lacks.
        parts = speech_style.render(_view(), _ctx(
            evidence_quotes=["证据句甲乙丙丁" * 5 for _ in range(3)]))
        self.assertEqual(parts["missing"], ["taboo_words"])

    def test_all_stored_slots_present_in_anchor(self):
        parts = speech_style.render(_view(), _ctx(
            evidence_quotes=["原文证据句"]))
        anchor = parts["anchor"]
        for token in ("口癖（原文引号内）", "句式（OCEAN 派生）",
                      "节奏（OCEAN.N + play_style 派生）", "原文证据句"):
            self.assertIn(token, anchor)
        self.assertIn("我不动老人，先听大家讲", anchor)
        self.assertIn("表演风格=steady", anchor)

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
