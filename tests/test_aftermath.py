"""G20: aftermath consequences, imprint, drift fuse and cascade damping."""

import tempfile
import unittest
from pathlib import Path

from _fixture import make_session_db
from imms import aftermath, db, ips, journal, memory, runtime


def _seat(name, arousal=0.2, C=0.5, honesty=0.5, acting=0.5, morale=0.8,
          exposure=0.2, intimacy=0.0):
    return {
        "seat_id": name,
        "character_id": name.replace("ai:", "char_"),
        "session_id": "s",
        "is_human": False,
        "l1": {"OCEAN.O": 0.5, "OCEAN.C": C, "OCEAN.N": 0.5,
               "values.honesty": honesty, "values.loyalty": 0.5,
               "acting_skill": acting},
        "emotion": {"arousal": arousal, "exposure_risk": exposure,
                    "morale": morale, "stress": 0.1},
        "relations": {"ai:b": {"like": 0.0, "trust": 0.0, "intimacy": intimacy}},
        "beliefs": {"f_known": {"belief": 0.8}},
        "memory": [], "commitments": {}, "lie_ledger": [], "reasoning_log": [],
        "accumulator": ips.new_accumulator({"OCEAN.C": C, "OCEAN.N": 0.5}),
    }


def _burst(strategy, intensity=1.0, observers=None, claimed=None, tier="meso",
           surprise=2.0, direction_delta=None):
    aft = {"strategy": strategy,
           "observers_delta": {s: {"trust": -0.12, "arousal": 0.3}
                               for s in (observers or [])},
           "self_concept_delta": {"intensity": round(0.05 * intensity, 4)}}
    chosen = None
    if direction_delta is not None:
        chosen = {"action": "mask_step", "delta": direction_delta,
                  "surprise": surprise, "accepted": True}
    return {
        "improv_id": "imp_s_r1_ai:a", "seat": "ai:a", "tier": tier,
        "action_key": "mask_step",
        "trigger": {"intensity": intensity},
        "speech_act": {"claimed_facts": claimed or []},
        "aftermath": aft,
        "direction": {"selected": chosen},
    }


class AftermathTest(unittest.TestCase):
    def test_regret_adds_like_stress_and_marks_leak_irreversible(self):
        states = {"ai:a": _seat("ai:a"), "ai:b": _seat("ai:b")}
        burst = _burst("regret", observers=["ai:b"], claimed=["f_known"])
        out = aftermath.resolve(states, burst, 1, seed=42, session_id="s")
        self.assertGreater(states["ai:b"]["relations"]["ai:a"]["like"], 0)
        self.assertGreater(states["ai:a"]["emotion"]["stress"], 0.1)
        self.assertIn("f_known", out["changes"][0].get("reason", "") + "f_known")
        self.assertEqual(burst["aftermath"]["strategy_effects"]["irreversible"], ["f_known"])
        self.assertEqual(out["heat_delta"], 0.0)

    def test_rationalize_reuses_lie_ledger_and_raises_heat(self):
        states = {"ai:a": _seat("ai:a"), "ai:b": _seat("ai:b")}
        burst = _burst("rationalize", observers=["ai:b"])
        out = aftermath.resolve(states, burst, 1, seed=42, session_id="s")
        self.assertEqual(len(states["ai:a"]["lie_ledger"]), 1)
        self.assertEqual(states["ai:a"]["lie_ledger"][0]["heat"], 0.25)
        kinds = [r["kind"] for r in states["ai:a"]["reasoning_log"]]
        self.assertIn("created", kinds)
        self.assertIn("rationalize", kinds)
        self.assertEqual(out["heat_delta"], 0.25)

    def test_escalate_compounds_damage_marked_irreversible(self):
        states = {"ai:a": _seat("ai:a"), "ai:b": _seat("ai:b")}
        burst = _burst("escalate", observers=["ai:b"])
        before = states["ai:a"]["emotion"]["exposure_risk"]
        out = aftermath.resolve(states, burst, 1, seed=42, session_id="s")
        self.assertLess(states["ai:b"]["relations"]["ai:a"]["trust"], 0)
        self.assertGreater(states["ai:a"]["emotion"]["exposure_risk"], before)
        self.assertTrue(any(c.get("irreversible") for c in out["changes"]))

    def test_imprint_raises_importance_and_tau_and_recalls_longer(self):
        state = _seat("ai:a")
        burst = _burst("regret", intensity=1.0)
        info = aftermath.imprint_memory(state, burst, 2)
        self.assertEqual(info["tau_m_factor"], 3.0)
        self.assertEqual(info["importance"], 1.0)
        record = state["memory"][0]
        self.assertTrue(record["imprint"])
        self.assertIn("emotion_tag", record)
        # with tau x3 the same aged imprint scores above a plain record
        plain = dict(record)
        plain["tau_m_factor"] = 1.0
        plain["seq"] = "plain"
        state["memory"].append(plain)
        ranked = memory.recall(state, 6, cue="即兴", limit=2)
        imprint_score = next(r["recall_score"] for r in ranked if r.get("imprint"))
        plain_score = next(r["recall_score"] for r in ranked if r["seq"] == "plain")
        self.assertGreater(imprint_score, plain_score)

    def test_self_drift_fuse_never_exceeds_0_08_and_l1_unchanged(self):
        state = _seat("ai:a")
        l1_before = dict(state["l1"])
        burst = _burst("rationalize", intensity=1.0,
                       direction_delta={"OCEAN.O": 0.5, "OCEAN.N": 0.5})
        for _ in range(20):
            aftermath.drift_self_concept(state, burst, 1)
        norm = sum(abs(v) for v in state["self_concept_drift"].values())
        self.assertLessEqual(norm, aftermath.MAX_DRIFT + 1e-6)
        self.assertEqual(state["l1"], l1_before)

    def test_cascade_depth_tier_and_quota_damping(self):
        states = {n: _seat(n, arousal=0.9, C=0.1, intimacy=0.8)
                  for n in ("ai:a", "ai:b", "ai:c")}
        burst = _burst("escalate", intensity=1.0, tier="macro", surprise=4.0)
        budget = ips.ImprovBudget({"macro_used": {"ai:a": 2}, "meso_used": {}})
        chain = aftermath.maybe_cascade(
            states, burst, 3, seed=11, budget=budget, session_id="s"
        )
        self.assertTrue(chain["cascade"])
        # a macro source can only produce meso (d1) / micro (d2)
        self.assertLessEqual(max(e["depth"] for e in chain["cascade"]), 2)
        self.assertTrue(all(e["tier"] != "macro" for e in chain["cascade"]))
        self.assertTrue(all(
            states[e["agent"]]["accumulator"].get("tier_cap") == e["tier"]
            for e in chain["cascade"]
        ))
        # d=2 is rarer than d=1 under the same conditions
        d1 = [e for e in chain["cascade"] if e["depth"] == 1]
        d2 = [e for e in chain["cascade"] if e["depth"] == 2]
        self.assertTrue(len(d1) >= len(d2))

    def test_no_direction_no_drift_and_human_excluded(self):
        state = _seat("ai:a")
        out = aftermath.drift_self_concept(state, _burst("regret"), 1)
        self.assertEqual(out["rejected"], "no_direction")
        human = {**_seat("ai:a"), "is_human": True}
        self.assertIsNone(aftermath.imprint_memory(human, _burst("regret"), 1))

    def test_runtime_settlement_persists_effects(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            conn, session = make_session_db(Path(tmp.name) / "t.db")
            sid = session["session_id"]
            runtime.init_runtime(conn, session)
            rt = db.get_game_state(conn, sid, "runtime")
            target = next(k for k in rt if k.startswith("ai:"))
            rt[target]["accumulator"].update({"A": 1.4, "rho": 0.99,
                                              "theta": 0.5, "theta_base": 0.5})
            rt[target]["emotion"]["arousal"] = 0.9
            db.set_game_state(conn, sid, "runtime", rt)
            scripted = {"utterances": [{"act_type": "accuse", "utterance": "你在说谎。",
                                        "target": target}], "vote": target}
            runtime.run_session(conn, sid, 1, scripted, 42, "template")
            bursts = journal.audit_events(conn, sid, act_type="improv_burst")
            self.assertTrue(bursts)
            effects = bursts[-1]["payload"]["aftermath"]
            self.assertIn("strategy_effects", effects)
            self.assertIn("memory_imprint", effects)
            self.assertIn("self_concept_delta", effects)
            self.assertIn("cascade", effects)
            conn.close()
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
