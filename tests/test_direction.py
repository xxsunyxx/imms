"""G19: mask gradient, shadow reversal and band-pass direction."""

import copy
import unittest

from imms import direction, ips


def _state(mask=True, zeta=None):
    l1 = {
        "OCEAN.O": 0.6, "OCEAN.C": 0.6, "OCEAN.E": 0.6,
        "OCEAN.A": 0.6, "OCEAN.N": 0.6,
        "values.honesty": 0.5, "values.loyalty": 0.5,
        "values.ambition": 0.5, "values.justice": 0.5,
    }
    if mask:
        l1["mask"] = {
            "OCEAN.O": 0.8, "OCEAN.C": 0.8, "OCEAN.E": 0.8,
            "OCEAN.A": 0.8, "OCEAN.N": 0.8,
        }
    if zeta is not None:
        l1["zeta_f"] = zeta
    return {
        "seat_id": "ai:a", "character_id": "char_a", "is_human": False,
        "l1": l1, "l0": {"acting_skill": 0.5},
        "emotion": {"arousal": 0.2, "stress": 0.1},
        "relations": {}, "beliefs": {"f_known": {"belief": 0.8}},
        "suspicion": {"ai:b": 0.8},
        "accumulator": {"A": 0.8, "theta": 0.5, "theta_base": 0.5,
                        "refractory_until": 0, "applied": []},
    }


class DirectionTests(unittest.TestCase):
    def test_gradient_requires_mask(self):
        gradient, source, gaps = direction.mask_gradient(_state())
        self.assertEqual(source, "l1.mask")
        self.assertAlmostEqual(gradient["OCEAN.O"], 0.2)
        missing, source, gaps = direction.mask_gradient(_state(mask=False))
        self.assertEqual(missing, {})
        self.assertIsNone(source)
        self.assertIn("mask_not_configured", gaps)

    def test_shadow_never_invents_zeta(self):
        result = direction.shadow_reversal(_state())
        self.assertFalse(result["enabled"])
        self.assertIsNone(result["zeta"])
        self.assertEqual(result["reason"], "zeta_missing")
        explicit = direction.shadow_reversal(_state(), zeta=0.4)
        self.assertTrue(explicit["enabled"])
        self.assertLess(explicit["vector"]["OCEAN.O"], 0)

    def test_fidelity_and_surprise_proxy(self):
        anchor = [0.2, 0.2, 0.2]
        self.assertAlmostEqual(direction.fidelity(anchor, anchor), 1.0)
        self.assertIsNone(direction.fidelity(anchor, [0.0, 0.0, 0.0]))
        self.assertLess(direction.fidelity([-0.2, -0.2, -0.2], anchor), 0.72)
        events = [{"act_type": "accuse", "payload": {"target": "ai:a", "impact": {"s_k": 1.0}}}]
        loud = direction.surprise(events, {}, "ai:a", "char_a")
        quiet = direction.surprise([], {}, "ai:a", "char_a")
        self.assertGreater(loud, quiet)
        self.assertGreater(loud, direction.S_HI)

    def test_band_pass_selects_mask_step_and_rejects_noise(self):
        state = _state()
        before = copy.deepcopy(state["l1"])
        chosen = direction.select(state, [], 2, seed=42)
        self.assertIsNotNone(chosen["selected"])
        self.assertEqual(chosen["selected"]["action"], "mask_step")
        reasons = {c["action"]: c["reject_reasons"] for c in chosen["candidates"]}
        self.assertIn("direction_not_aligned", reasons["canon_hold"])
        self.assertIn("zeta_missing", reasons["shadow_step"])
        self.assertEqual(state["l1"], before)

        loud = [{"act_type": "accuse", "payload": {"target": "ai:a", "impact": {"s_k": 1.0}}}]
        rejected = direction.select(state, loud, 2, seed=42)
        self.assertIsNone(rejected["selected"])
        self.assertTrue(all(not c["accepted"] for c in rejected["candidates"]))

    def test_zeta_enables_shadow_candidate(self):
        state = _state(zeta=0.5)
        result = direction.select(state, [], 3, seed=7)
        shadow = next(c for c in result["candidates"] if c["action"] == "shadow_step")
        self.assertNotIn("zeta_missing", shadow["reject_reasons"])
        self.assertLess(shadow["alignment"], 0)

    def test_burst_carries_direction_without_rewriting_facts(self):
        states = {"ai:a": _state(), "ai:b": _state(mask=False)}
        states["ai:b"]["seat_id"] = "ai:b"
        states["ai:b"]["character_id"] = "char_b"
        before_l1 = copy.deepcopy(states["ai:a"]["l1"])
        result = ips.resolve_bursts(
            states, 1, [], 0.5, 1.0, ips.ImprovBudget(), {"f_known"}, "s", 42
        )
        self.assertEqual(len(result["bursts"]), 1)
        burst = result["bursts"][0]
        self.assertIn("direction", burst)
        self.assertEqual(burst["speech_act"]["claimed_facts"], ["f_known"])
        self.assertTrue(burst["speech_act"]["is_unverified_claim"])
        self.assertEqual(states["ai:a"]["l1"], before_l1)


if __name__ == "__main__":
    unittest.main()
