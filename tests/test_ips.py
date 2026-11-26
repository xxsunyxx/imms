"""G17: leaky accumulator and always-on expression perturbation."""

import json
import tempfile
import unittest
from pathlib import Path

from _fixture import ROOT, make_session_db
from imms import db, ips, runtime


def _state(traits=None, **overrides):
    data = {
        "seat_id": "ai:a",
        "character_id": "char_a",
        "l1": traits if traits is not None else {"OCEAN.C": 0.5, "OCEAN.N": 0.5},
        "emotion": {
            "arousal": 0.2, "stress": 0.1, "cognitive_load": 0.1,
            "valence": 0.0, "intoxication": 0.0,
        },
        "relations": {},
    }
    data.update(overrides)
    return data


class AccumulatorTest(unittest.TestCase):
    def test_traits_change_leak_and_threshold(self):
        calm = ips.new_accumulator({"OCEAN.C": 0.9, "OCEAN.N": 0.1})
        volatile = ips.new_accumulator({"OCEAN.C": 0.1, "OCEAN.N": 0.9})
        self.assertLess(calm["rho"], volatile["rho"])
        self.assertGreater(calm["theta"], volatile["theta"])
        self.assertGreaterEqual(calm["theta"], ips.PARAMS["theta_min"])
        self.assertLessEqual(volatile["theta"], ips.PARAMS["theta_max"])

    def test_impact_table_and_explicit_override(self):
        state = _state(relations={"ai:b": {"intimacy": 0.5, "grudge": 0.4}})
        kind, s, chi, kappa, gamma = ips.classify_impact(
            {"actor": "ai:b", "act_type": "accuse", "payload": {"target": "ai:a"}}, state
        )
        self.assertEqual(kind, "false_accusation")
        self.assertEqual(s, 0.26)
        self.assertEqual(chi, 1.2)
        self.assertAlmostEqual(kappa, 1.3)
        self.assertEqual(gamma, 0.4)
        warm = ips.classify_impact(
            {"actor": "ai:b", "act_type": "ally", "payload": {}}, state
        )
        self.assertEqual(warm[0], "warmth")
        self.assertGreater(warm[1], 0)
        explicit = ips.classify_impact(
            {"actor": "ai:b", "act_type": "assert",
             "payload": {"impact": {"kind": "humiliation", "s_k": 2.0}}}, state
        )
        self.assertEqual(explicit[0], "humiliation")
        self.assertEqual(explicit[1], ips.IMPACT_KINDS["humiliation"])
        harmless = ips.classify_impact(
            {"actor": "ai:b", "act_type": "assert", "payload": {}}, state
        )
        self.assertEqual(harmless[1], 0.0)

    def test_leak_and_cap_with_audit_detail(self):
        state = _state()
        state["accumulator"] = ips.new_accumulator(state["l1"])
        state["accumulator"]["A"] = 0.4
        events = [{
            "seq": 12, "round": 1, "actor": "ai:b", "act_type": "accuse",
            "payload": {"target": "ai:a", "utterance": "你撒谎"},
        }]
        acc = ips.accumulate(state, events, 1, session_id="s")
        self.assertAlmostEqual(acc["A"], round(0.4 * acc["rho"] + 0.26 * 1.2, 4))
        self.assertEqual(acc["applied"][0]["kind"], "false_accusation")
        self.assertEqual(acc["applied"][0]["seq"], 12)
        # 12 repeated hits must saturate at the original [0, 1.5] domain.
        for _ in range(12):
            ips.accumulate(state, events * 4, 1, session_id="s")
        self.assertLessEqual(acc["A"], ips.PARAMS["amax"])

    def test_refractory_raises_threshold_and_human_is_exempt(self):
        state = _state()
        acc = ips.new_accumulator(state["l1"])
        acc["refractory_until"] = 4
        self.assertAlmostEqual(
            ips.effective_theta(acc, 3),
            round(acc["theta_base"] * ips.PARAMS["refractory_multiplier"], 4),
        )
        self.assertEqual(ips.effective_theta(acc, 5), acc["theta_base"])
        human = _state(is_human=True)
        self.assertIsNone(ips.accumulate(human, [{"actor": "ai:b", "act_type": "accuse", "payload": {}}], 1))
        self.assertIsNone(ips.micro_perturb(human, "s", 1))


class DeliveryTest(unittest.TestCase):
    def test_deterministic_and_bounded(self):
        state = _state(emotion={
            "arousal": 0.9, "stress": 0.8, "cognitive_load": 0.9,
            "valence": -0.4, "intoxication": 0.5,
        })
        a = ips.micro_perturb(state, "session-x", 2, seed=42)
        b = ips.micro_perturb(state, "session-x", 2, seed=42)
        self.assertEqual(a, b)
        self.assertNotEqual(a, ips.micro_perturb(state, "session-x", 2, seed=43))
        self.assertGreaterEqual(a["probability"], 0.0)
        self.assertLessEqual(a["probability"], 1.0)
        if a["hit"]:
            self.assertIn(a["item"], ips.SAFE_LIBRARY)
        params = a["params"]
        self.assertEqual(params["self_corrections"], 2)
        self.assertEqual(params["pause_marks"], 1)
        self.assertLess(params["sentence_length_factor"], 1.0)
        self.assertGreater(params["ellipsis_density_factor"], 1.0)

    def test_runtime_puts_delivery_in_events_without_touching_truth(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn, session = make_session_db(Path(tmp.name) / "t.db")
        self.addCleanup(conn.close)
        sid = session["session_id"]
        scripted = json.loads((ROOT / "examples" / "scripts" / "user_script.json").read_text(encoding="utf-8"))
        truth_before = db.get_script(conn, session["script_id"])
        runtime.run_session(conn, sid, 2, scripted, 42, "template")
        self.assertEqual(db.get_script(conn, session["script_id"]), truth_before)

        rows = json.loads(json.dumps(db.visible_game_events(conn, sid, "gm")))
        ai_rows = [r for r in rows if r["actor"].startswith("ai:")]
        human_rows = [r for r in rows if r["actor"].startswith("human:")]
        self.assertTrue(ai_rows)
        for row in ai_rows:
            payload = json.loads(row["payload_json"])
            delivery = payload["delivery"]
            self.assertEqual(delivery["source"], "ips:micro")
            self.assertIn("params", delivery)
            self.assertIsInstance(payload["claimed_facts"], list)
        for row in human_rows:
            self.assertIsNone(json.loads(row["payload_json"])["delivery"])

        state = db.get_game_state(conn, sid, "runtime", {})
        for seat, seat_state in state.items():
            if seat.startswith("ai:"):
                self.assertIn("accumulator", seat_state)
                self.assertIsNotNone(seat_state["delivery"])
            else:
                self.assertIsNone(seat_state["accumulator"])

    def test_two_runs_with_same_seed_reproduce_delivery(self):
        deliveries = []
        for label in ("a", "b"):
            tmp = tempfile.TemporaryDirectory()
            conn, session = make_session_db(Path(tmp.name) / f"{label}.db")
            sid = session["session_id"]
            scripted = json.loads((ROOT / "examples" / "scripts" / "user_script.json").read_text(encoding="utf-8"))
            runtime.run_session(conn, sid, 2, scripted, 42, "template")
            deliveries.append([
                json.loads(row["payload_json"])["delivery"]
                for row in db.visible_game_events(conn, sid, "gm")
                if row["actor"].startswith("ai:")
            ])
            conn.close()
            tmp.cleanup()
        self.assertEqual(deliveries[0], deliveries[1])
        self.assertTrue(deliveries[0])


if __name__ == "__main__":
    unittest.main()
