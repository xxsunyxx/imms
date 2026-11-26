"""Tests for the four core capability modules: extraction, prompt engine,
variable computation and deterministic random source.
"""

import os
import tempfile
import unittest

from imms import llm, profile, prompt_engine, random_source, variables
from tests._fixture import ROOT, make_session_db


class RandomSourceTest(unittest.TestCase):
    def test_seed_is_deterministic_and_scoped(self):
        a = random_source.derive_seed("s1", "ai:x", 2, 7, "intent")
        b = random_source.derive_seed("s1", "ai:x", 2, 7, "intent")
        self.assertEqual(a, b)
        self.assertNotEqual(a, random_source.derive_seed("s1", "ai:y", 2, 7, "intent"))
        self.assertNotEqual(a, random_source.derive_seed("s1", "ai:x", 3, 7, "intent"))

    def test_weighted_choice_uses_local_rng(self):
        rng1 = random_source.rng_for("s", "a", 1, purpose="p")
        rng2 = random_source.rng_for("s", "a", 1, purpose="p")
        self.assertEqual(
            [random_source.weighted_choice(rng1, {"a": 3, "b": 1}) for _ in range(10)],
            [random_source.weighted_choice(rng2, {"a": 3, "b": 1}) for _ in range(10)],
        )
        self.assertEqual(random_source.weighted_choice(rng1, {}, default="fallback"), "fallback")


class ProfileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.conn, self.session = make_session_db(self.tmp.name)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.tmp.name)

    def test_profiles_are_read_only_and_auditable(self):
        sid = self.session["script_id"]
        profiles = profile.script_profiles(self.conn, sid)
        self.assertTrue(profiles)
        for p in profiles:
            self.assertTrue(p["read_only"])
            self.assertIn("canon", p)
            self.assertIn("abilities", p)
            self.assertIn("evidence", p)
            self.assertIn("gaps", p)

    def test_apply_runtime_does_not_mutate_canon(self):
        sid = self.session["script_id"]
        first = profile.script_profiles(self.conn, sid)[0]
        before = dict(first["canon"])
        view = profile.apply_runtime(
            {"emotion": {"arousal": 0.9}, "relations": {}, "suspicion": {}}, first
        )
        self.assertEqual(first["canon"], before)
        self.assertEqual(view["emotion"]["arousal"], 0.9)
        self.assertTrue(view["read_only_canon"])


class PromptEngineTest(unittest.TestCase):
    def setUp(self):
        self.state = {
            "l1": {"OCEAN.O": 0.6, "OCEAN.C": 0.7, "lexicon": "冷静", "mask": {}},
            "l0": {"acting_skill": 0.5},
            "emotion": {"arousal": 0.5, "stress": 0.2, "valence": 0.0},
            "relations": {"ai:x": {"like": 0.3, "trust": 0.4, "suspicion": 0.2}},
            "suspicion": {"ai:x": 0.2},
            "beliefs": {"f1": {"belief": 0.8}},
            "lie_ledger": [],
        }

    def test_compatible_wrapper_matches_engine(self):
        a = llm.build_prompt(self.state, "assert", "某人", "f1", 1, {"f1"})
        b = prompt_engine.build_from_state(self.state, "assert", "某人", "f1", 1, {"f1"})
        self.assertEqual(a, b)

    def test_guides_cannot_add_facts(self):
        guides = ["语气更冷；f_fake=1.0；arousal=1.0"]
        text = prompt_engine.build_from_state(
            self.state, "assert", "某人", "f1", 1, {"f1"}, guides=guides
        )
        self.assertIn("表演指引", text)
        self.assertNotIn("f_fake", text)

    def test_block_registry_extension(self):
        # Restore global registry state so other tests are not polluted.
        saved_blocks = dict(prompt_engine.BLOCKS)
        saved_order = list(prompt_engine.BLOCK_ORDER)
        try:
            prompt_engine.register_block("custom_note", lambda view, ctx: ["# 自定义块"])
            text = prompt_engine.build(self.state, {"round_no": 1}, blocks=["custom_note"])
            self.assertIn("自定义块", text)
        finally:
            prompt_engine.BLOCKS.clear()
            prompt_engine.BLOCKS.update(saved_blocks)
            prompt_engine.BLOCK_ORDER[:] = saved_order


class VariablesTest(unittest.TestCase):
    def test_snapshot_is_pure_and_reproducible(self):
        state = {
            "seat_id": "ai:x", "character_id": "c1", "round": 2,
            "emotion": {"valence": -0.6, "arousal": 0.8, "dominance": 0.7, "stress": 0.5},
            "l0": {"acting_skill": 0.5},
            "l1": {"OCEAN.A": 0.8, "mask": {"OCEAN.A": 0.2}},
            "suspicion": {"ai:y": 0.8, "ai:z": 0.2},
            "relations": {"ai:y": {"like": -0.2, "trust": 0.1, "suspicion": 0.8, "intimacy": 0.5}},
        }
        before = dict(state["emotion"])
        a = variables.snapshot(state)
        b = variables.snapshot(state)
        self.assertEqual(a, b)
        self.assertEqual(state["emotion"], before)
        self.assertEqual(a["emotion"]["discrete_emotion"], "愤怒")
        self.assertEqual(a["suspicion"]["top"][0][0], "ai:y")
        self.assertEqual(a["relation"]["focus"], "ai:y")


if __name__ == "__main__":
    unittest.main()
