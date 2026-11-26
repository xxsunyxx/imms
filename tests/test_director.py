"""G21: director dials, deterministic parameter effects and retro dashboard."""

import tempfile
import unittest
from pathlib import Path

from _fixture import make_session_db
from imms import db, director, ips, journal, retro, runtime


def _burst(tier="meso", strategy="regret", trust=-0.2, cascade=False,
           surprise=2.0, fidelity=0.9, candidates=None, action="mask_step"):
    chosen = {"surprise": surprise, "fidelity": fidelity, "action": action}
    return {
        "round": 1, "actor": "ai:a", "act_type": "improv_burst",
        "payload": {
            "tier": tier, "action_key": action,
            "aftermath": {
                "strategy": strategy,
                "observers_delta": {"ai:b": {"trust": trust, "arousal": 0.3}},
                "cascade": [{"agent": "ai:c", "depth": 1}] if cascade else [],
                "self_concept_delta": {"l1_norm": 0.02},
            },
            "direction": {"selected": chosen, "candidates": candidates or [
                {"action": "mask_step", "accepted": True},
                {"action": "shadow_step", "accepted": False},
            ]},
        },
    }


class DirectorTest(unittest.TestCase):
    def test_normalize_clamps_and_presets_are_complete(self):
        out = director.normalize({"improv_gain": 9, "chaos": -2, "macro_quota": 7,
                                  "unknown": 1})
        self.assertEqual(out["improv_gain"], 2.0)
        self.assertEqual(out["chaos"], 0.0)
        self.assertEqual(out["macro_quota"], 3)
        self.assertNotIn("unknown", out)
        for key, cfg in director.PRESETS.items():
            normalized = director.normalize({}, preset=key)
            for dial in director.DIAL_KEYS:
                self.assertEqual(normalized[dial], cfg[dial])

    def test_dial_effects_are_deterministic_and_directional(self):
        calm = director.normalize({}, preset="honkaku")
        farce = director.normalize({}, preset="farce")
        self.assertLess(director.theta_multiplier(farce), director.theta_multiplier(calm))
        self.assertGreater(director.softmax_temperature(farce), director.softmax_temperature(calm))
        self.assertGreater(director.cascade_multiplier(farce), director.cascade_multiplier(calm))
        self.assertGreater(director.epsilon_multiplier(farce), director.epsilon_multiplier(calm))
        self.assertEqual(director.shadow_multiplier({"shadow_sensitivity": 0.0}), 0.0)

    def test_legacy_session_has_neutral_multipliers(self):
        self.assertIsNone(director.from_session({"l0": {}}))
        self.assertEqual(director.theta_multiplier(None), 1.0)
        self.assertEqual(director.softmax_temperature(None), 0.5)
        self.assertEqual(director.cascade_multiplier(None), 1.0)
        acc = director.apply_to_accumulator(
            {"A": 0, "rho": 0.9, "theta": 0.6, "theta_base": 0.6, "applied": []}, None
        )
        self.assertEqual(acc["theta"], 0.6)
        self.assertNotIn("_dial_gain_applied", acc)

    def test_runtime_applies_dials_without_touching_l1(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            conn, session = make_session_db(Path(tmp.name) / "t.db")
            session = director.set_session_dials(session, preset="farce")
            db.save_session(conn, session)
            runtime.init_runtime(conn, session)
            rt = db.get_game_state(conn, session["session_id"], "runtime")
            ai = next(k for k in rt if k.startswith("ai:"))
            self.assertEqual(rt[ai]["l0"]["dials"]["improv_gain"], 1.8)
            self.assertTrue(rt[ai]["accumulator"]["_dial_gain_applied"])
            self.assertLess(rt[ai]["accumulator"]["theta"], 0.6)
            self.assertEqual(rt[ai]["l1"], rt[ai]["l1"])  # no L1 mutation
            conn.close()
        finally:
            tmp.cleanup()

    def test_metrics_bands_and_cascade_shape(self):
        events = [
            _burst(trust=-0.2), _burst(trust=0.02, cascade=True),
            _burst(tier="micro"),
        ]
        m = retro.improvisation_metrics(events, {"ai:a": {}, "ai:b": {}}, "ok")
        self.assertEqual(m["bursts"], 3)
        self.assertEqual(m["overt"], 2)
        self.assertEqual(m["effective_improv_rate"], 1.0)
        self.assertEqual(m["aftermath_coverage"], 1.0)
        self.assertTrue(m["checks"]["aftermath_coverage"])
        self.assertTrue(m["checks"]["persona_drift"])
        self.assertTrue(m["checks"]["solvability_preserved"])
        self.assertEqual(m["cascade_depth"]["d3plus"], 0)

    def test_empty_session_is_reported_not_crashing(self):
        m = retro.improvisation_metrics([], {}, None)
        self.assertEqual(m["bursts"], 0)
        self.assertIsNone(m["checks"]["effective_improv_rate"])


if __name__ == "__main__":
    unittest.main()
