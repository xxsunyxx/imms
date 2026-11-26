"""G18/G24: overt improvisation gate, token competition and provocation."""

import json
import tempfile
import unittest
from pathlib import Path

from _fixture import ROOT, make_session_db
from imms import db, ips, journal, runtime


def _seat(name, A=0.0, theta=0.5, arousal=0.2, target=None):
    return {
        "seat_id": name,
        "character_id": name.replace("ai:", "char_"),
        "session_id": "s",
        "is_human": False,
        "l1": {"OCEAN.O": 0.5, "OCEAN.C": 0.5, "OCEAN.N": 0.6,
               "values.honesty": 0.5, "values.loyalty": 0.5, "acting_skill": 0.5},
        "emotion": {"arousal": arousal, "exposure_risk": 0.2, "morale": 0.8},
        "relations": {},
        "suspicion": {target or name: 0.9},
        "beliefs": {"f_known": {"belief": 0.8}},
        "accumulator": {
            "A": A, "rho": 0.9, "theta": theta, "theta_base": theta,
            "refractory_until": 0, "applied": [],
        },
    }


class BurstGateTest(unittest.TestCase):
    def test_intensity_tier_and_amplitude(self):
        acc = {"A": 0.45, "theta": 0.5, "theta_base": 0.5, "refractory_until": 0, "applied": []}
        self.assertEqual(ips.intensity(acc, _seat("a"), round_no=1), 0.0)
        acc["A"] = 0.8
        I = ips.intensity(acc, _seat("a", arousal=0.2), round_no=1)
        self.assertGreater(I, 0.3)
        self.assertEqual(ips.tier_for(I), "meso")
        self.assertEqual(ips.tier_for(0.9), "macro")
        self.assertLess(
            ips.epsilon(0.9, acting_skill=0.9),
            ips.epsilon(0.9, acting_skill=0.1),
        )

    def test_kairos_gate_and_vote_lock(self):
        state = _seat("a", target="a")
        self.assertEqual(ips.kairos(state, 1.0, 1.0, stage="vote"), 0.0)
        quiet = ips.kairos(state, 0.0, 1.0)   # pacing gap: cold room ->放行
        busy = ips.kairos(state, 1.0, 1.0)    # heat already at target
        self.assertGreater(quiet, busy)
        watched = ips.kairos(state, 0.5, 1.0, events=[
            {"actor": "ai:b", "payload": {"target": "ai:a"}}
        ])
        self.assertGreater(watched, busy)

    def test_budget_limits(self):
        budget = ips.ImprovBudget()
        self.assertTrue(budget.available("macro", "ai:a"))
        budget.consume("macro", "ai:a")
        self.assertFalse(budget.available("macro", "ai:b"))  # one macro per act
        for _ in range(2):
            budget.consume("meso", "ai:a")
        self.assertFalse(budget.available("meso", "ai:a"))
        self.assertTrue(budget.available("meso", "ai:b"))
        self.assertTrue(budget.available("micro", "ai:a"))

    def test_token_contention_keeps_charge_and_forces_aftermath(self):
        a, b = _seat("ai:a", A=0.8), _seat("ai:b", A=0.8)
        states = {"ai:a": a, "ai:b": b}
        budget = ips.ImprovBudget()
        result = ips.resolve_bursts(
            states, 1, [], 0.5, 1.0, budget, {"f_known"},
            session_id="s", seed=42,
        )
        self.assertEqual(len(result["bursts"]), 1)
        reasons = [s["reason"] for s in result["suppressed"]]
        self.assertIn("token_contention", reasons)
        burst = result["bursts"][0]
        self.assertEqual(burst["tier"], "meso")
        self.assertEqual(burst["guard"]["IR1"], "pass_claim_only")
        self.assertTrue(burst["speech_act"]["is_unverified_claim"])
        self.assertTrue(set(burst["speech_act"]["claimed_facts"]) <= {"f_known"})
        self.assertTrue(burst["aftermath"]["strategy"] in {"regret", "rationalize", "escalate"})
        self.assertTrue(burst["aftermath"]["observers_delta"])
        self.assertTrue(result["changes"])
        winner = burst["seat"]
        loser = next(s for s in states if s != winner)
        self.assertLess(states[winner]["accumulator"]["A"], 0.8)
        self.assertEqual(states[loser]["accumulator"]["A"], 0.8)
        self.assertGreater(states[winner]["accumulator"]["refractory_until"], 1)
        self.assertTrue(result["budget"]["meso_used"])

    def test_aftermath_strategy_is_deterministic(self):
        def run():
            a = _seat("ai:a", A=0.8)
            observer = _seat("ai:observer", A=0.0)
            return ips.resolve_bursts(
                {"ai:a": a, "ai:observer": observer}, 1, [], 0.5, 1.0,
                ips.ImprovBudget(), {"f_known"}, "s", 42,
            )
        one, two = run(), run()
        self.assertEqual(one["bursts"][0]["aftermath"]["strategy"],
                         two["bursts"][0]["aftermath"]["strategy"])

    def test_ir5_rejects_unobservable_burst(self):
        result = ips.resolve_bursts(
            {"ai:alone": _seat("ai:alone", A=0.8)}, 1, [], 0.5, 1.0,
            ips.ImprovBudget(), {"f_known"}, "s", 42,
        )
        self.assertEqual(result["bursts"], [])
        self.assertEqual(result["suppressed"][0]["reason"], "ir5_no_observer")


class ProvocationTest(unittest.TestCase):
    def test_ignition_only_charges_the_target(self):
        target = _seat("ai:b", A=0.2)
        human = _seat("human:a")
        human["is_human"] = True
        human["accumulator"] = None
        applied = ips.apply_provocation(target, "human:a", "humiliation", 0.32, 1, seq=7)
        self.assertGreater(applied["after"], applied["before"])
        self.assertIsNone(ips.apply_provocation(human, "ai:b", "warmth", 0.2, 1))
        target["accumulator"]["refractory_until"] = 3
        self.assertTrue(ips.apply_provocation(target, "human:a", "humiliation", 0.32, 2)["refractory"])

    def test_runtime_writes_provocation_and_preserves_truth(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn, session = make_session_db(Path(tmp.name) / "t.db")
        self.addCleanup(conn.close)
        sid = session["session_id"]
        truth = db.get_script(conn, session["script_id"])
        target_seat = next(p["id"] for p in session["participants"] if p["id"].startswith("ai:"))
        scripted = {
            "utterances": [
                {"act_type": "accuse", "utterance": "你刚才在说谎。", "target": target_seat},
                {"act_type": "ally", "utterance": "我信你。", "target": target_seat},
            ],
            "vote": "char_02",
        }
        runtime.run_session(conn, sid, 2, scripted, 42, "template")
        self.assertEqual(db.get_script(conn, session["script_id"]), truth)
        rows = [
            {**r, "payload": json.loads(r["payload_json"])}
            for r in db.visible_game_events(conn, sid, "gm")
        ]
        provocations = [r for r in rows if r["act_type"] == "provocation"]
        self.assertTrue(provocations)
        self.assertTrue(all(r["actor"].startswith("human:") for r in provocations))
        self.assertGreater(provocations[0]["payload"]["after"],
                           provocations[0]["payload"]["before"])
        state = db.get_game_state(conn, sid, "runtime", {})
        self.assertIsNone(next(s for s in state.values() if s["is_human"])["accumulator"])
        self.assertTrue(any(s.get("accumulator") for k, s in state.items() if k.startswith("ai:")))
        self.assertIn("improv_budget", [r["key"] for r in conn.execute(
            "SELECT key FROM game_state WHERE session_id=?", (sid,)
        )])

    def test_runtime_emits_burst_and_aftermath(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn, session = make_session_db(Path(tmp.name) / "t.db")
        self.addCleanup(conn.close)
        sid = session["session_id"]
        scripted = json.loads((ROOT / "examples" / "scripts" / "user_script.json").read_text(encoding="utf-8"))
        runtime.run_session(conn, sid, 1, scripted, 42, "template")

        state = db.get_game_state(conn, sid, "runtime", {})
        for seat, item in state.items():
            if not seat.startswith("ai:"):
                continue
            acc = item["accumulator"] or ips.new_accumulator(item["l1"])
            acc.update({"A": 1.2, "theta": 0.5, "theta_base": 0.5, "refractory_until": 0, "applied": []})
            item["accumulator"] = acc
            item["suspicion"] = {next(s for s in state if s.startswith("ai:") and s != seat): 0.9}
        db.set_game_state(conn, sid, "runtime", state)
        runtime.run_session(conn, sid, 2, scripted, 42, "template")

        rows = journal.audit_events(conn, sid)
        bursts = [r for r in rows if r["act_type"] == "improv_burst"]
        suppressed = [r for r in rows if r["act_type"] == "suppressed_burst"]
        self.assertTrue(bursts)
        payload = bursts[-1]["payload"]
        self.assertIn(payload["tier"], {"meso", "macro"})
        self.assertTrue(payload["aftermath"]["observers_delta"])
        self.assertTrue(payload["speech_act"]["is_unverified_claim"])
        self.assertGreaterEqual(len(suppressed), 1)
        self.assertEqual(db.get_script(conn, session["script_id"])["script_id"], session["script_id"])


if __name__ == "__main__":
    unittest.main()
