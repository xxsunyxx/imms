"""G05: L4 projection and R2 persona-consistency guard."""

import tempfile
import unittest
from pathlib import Path

from imms import db, l4, llm, runtime
from _fixture import make_session_db


def state(seat, l1=None, relations=None, emotion=None, l0=None, human=False):
    return {
        "seat_id": seat,
        "character_id": seat.replace(":", "_"),
        "l1": l1 or {},
        "l0": l0 or {"acting_skill": 0.5},
        "relations": relations or {},
        "emotion": emotion or {"arousal": 0.2, "stress": 0.1},
        "is_human": human,
    }


class L4Tests(unittest.TestCase):
    def test_guard_anchor_and_cosine(self):
        anchor = [0.4, 0.3, -0.2]
        self.assertEqual(l4.cosine(anchor, anchor), 1.0)
        self.assertTrue(l4.persona_guard(anchor, anchor)["passed"])
        self.assertTrue(l4.persona_guard([0.0] * 3, [0.0] * 3)["anchor_flat"])
        bad = l4.persona_guard([-0.4, -0.3, 0.2], anchor)
        self.assertFalse(bad["passed"])
        self.assertLess(bad["cosine"], 0)

    def test_projection_is_deterministic_and_masked(self):
        l1 = {"OCEAN.O": 0.7, "OCEAN.C": 0.4, "mask": {"OCEAN.O": 0.9}}
        observed = state("ai:b", l1, relations={"ai:a": {"like": -0.8, "trust": -0.9,
                                                        "suspicion": 0.8}},
                         l0={"acting_skill": 1.0})
        observer = state("ai:a")
        one = l4.project(observed, observer, "s", 2, seed=42)
        two = l4.project({**observed, "seat_id": "ai:b"}, observer, "s", 2, seed=42)
        unmasked = l4.project({**observed, "l1": {"OCEAN.O": 0.7, "OCEAN.C": 0.4}},
                              observer, "s", 2, seed=42)
        self.assertEqual(one, two)
        self.assertGreater(one["shown_persona"]["OCEAN.O"],
                           unmasked["shown_persona"]["OCEAN.O"])
        self.assertGreaterEqual(one["shown_attitude"]["trust"], -0.9)
        self.assertAlmostEqual(one["shown_attitude"]["like"], -0.8)
        self.assertEqual(one["leak_sigma"], 0.0)
        self.assertEqual(one["mask_source"], "l1.mask")
        delta, source, _ = l4.mask_delta(observed)
        self.assertEqual(source, "l1.mask")
        self.assertAlmostEqual(delta["OCEAN.O"], 0.2)

    def test_missing_mask_degrades_without_invention(self):
        observed = state("ai:b", {"OCEAN.N": 0.6},
                         relations={"ai:a": {"trust": 0.3}})
        item = l4.project(observed, state("ai:a"), "s", 1)
        self.assertIsNone(item["mask_source"])
        self.assertIn("mask_not_configured", item["gaps"])
        self.assertLess(abs(item["shown_persona"]["OCEAN.O"] - 0.5), 0.1)

    def test_guard_retries_and_clamps_extreme_mask(self):
        l1 = {
            "OCEAN.O": 0.6, "OCEAN.C": 0.6, "OCEAN.E": 0.6,
            "OCEAN.A": 0.6, "OCEAN.N": 0.6,
            "mask": {"OCEAN.O": 0.0, "OCEAN.C": 0.0, "OCEAN.E": 0.0,
                     "OCEAN.A": 0.0, "OCEAN.N": 0.0},
        }
        observed = state("ai:b", l1, relations={"ai:a": {"trust": 0.2}},
                         emotion={"arousal": 2.0, "stress": 2.0},
                         l0={"acting_skill": -1.0})
        item = l4.project(observed, state("ai:a"), "s", 3, seed=7)
        self.assertNotEqual(item["guard"]["action"], "pass")
        self.assertGreaterEqual(item["guard"]["cosine"], item["guard"]["threshold"] - 1e-6)

        table = {"ai:a": {"ai:b": item}}
        self.assertTrue(l4.guard_audits(table))

    def test_prompt_contains_l4_only(self):
        observed = state("ai:b", {"OCEAN.O": 0.8, "mask": {"OCEAN.O": 0.1}},
                         relations={"ai:a": {"like": -0.91, "trust": -0.88}},
                         emotion={"arousal": 1.0, "stress": 1.0},
                         l0={"acting_skill": 0.0})
        item = l4.project(observed, state("ai:a"), "s", 4, seed=11)
        prompt = llm.build_prompt(
            state("ai:a"), "accuse", "乙", "线索", 4, ["f_public"],
            perceived={"ai:b": item},
        )
        self.assertIn("L4 投影", prompt)
        self.assertIn("可能看走眼", prompt)
        self.assertNotIn("-0.88", prompt)
        self.assertNotIn("inner_intent", prompt)

    def test_projection_table_excludes_self_and_runtime_runs(self):
        states = {
            "ai:a": state("ai:a", {"OCEAN.O": 0.5}, human=False),
            "human:b": state("human:b", {"OCEAN.O": 0.5}, human=True),
        }
        table = l4.project_table(states, "s", 1, seed=3)
        self.assertEqual(set(table["ai:a"]), {"human:b"})
        self.assertEqual(table["human:b"]["ai:a"]["leak_sigma"] > 0, True)

        tmp = tempfile.TemporaryDirectory()
        conn, session = make_session_db(Path(tmp.name) / "t.db")
        try:
            runtime.init_runtime(conn, session)
            scripted = {
                "utterances": [
                    {"act_type": "assert", "utterance": "我昨晚在客厅。", "target": "ai:gu_chengye"}
                ],
                "vote": "gu_chengye",
            }
            result = runtime.run_session(conn, session["session_id"], 1, scripted, 42, "template")
            self.assertTrue(result["log"])
            events = db.visible_game_events(conn, session["session_id"], "gm")
            self.assertTrue(all(isinstance(e["seq"], int) for e in events))
        finally:
            conn.close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
