"""Closure tests for §12.5 situation layer and §14.3 anti-degradation rules."""

import json
import tempfile
import unittest
from pathlib import Path

from imms import db, degradation, formulas, journal, l4, runtime, variables
from imms import runtime as runtime_mod

from _fixture import ROOT, make_session_db


def _state(**overrides):
    base = {
        "seat_id": "ai:a", "character_id": "a",
        "l1": {"OCEAN.N": 0.4, "OCEAN.O": 0.3,
               "mask": {"OCEAN.N": 0.9, "OCEAN.O": 0.8}},
        "l0": {"acting_skill": 0.5},
        "emotion": {"valence": 0.0, "arousal": 0.2, "dominance": 0.5,
                    "stress": 0.4, "intoxication": 0.0, "exposure_risk": 0.1,
                    "morale": 0.8},
        "relations": {}, "nu": 1.0, "suspicion": {"ai:b": 0.7, "ai:c": 0.2},
        "ell": {"ai:b": 0.9, "ai:c": -1.4}, "beliefs": {}, "memory": [],
        "faction": None, "tension": 0.0,
    }
    base.update(overrides)
    return base


class RuleClosureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn, self.session = make_session_db(Path(self.tmp.name) / "t.db")
        self.sid = self.session["session_id"]

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_f06_trust_falls_faster_than_it_recovers(self):
        state = _state()
        state["relations"]["actor"] = dict(runtime_mod.REL_DEFAULT)
        state["relations"]["actor"]["trust"] = 0.8
        bad = {"actor": "actor", "act_type": "accuse",
               "payload": {"target": "a", "utterance": "你不可信", "claimed_facts": []}}
        key, changes = runtime_mod._appraise(state, bad, 1)
        self.assertEqual(key, "accuse_unfounded")
        trust = [c for c in changes if c["dim"] == "trust"][0]
        self.assertLess(trust["after"], 0.8)
        self.assertIn("F06", trust["reason"])

        before = trust["after"]
        good = {"actor": "actor", "act_type": "ally",
                "payload": {"target": "a", "utterance": "我信你", "claimed_facts": []}}
        _, recover = runtime_mod._appraise(state, good, 2)
        gain = [c for c in recover if c["dim"] == "trust"][0]
        self.assertGreater(gain["after"], before)
        self.assertLess(gain["after"] - before, 0.8 - before)
        self.assertIn("F06", gain["reason"])

    def test_f01_tension_raises_temperature_and_leak(self):
        state = _state()
        low = variables.tension_snapshot(state)["tension"]
        self.assertGreater(low, 0.0)
        state["tension"] = low
        base_sigma = l4.leak_sigma(state)
        state["tension"] = 0.9
        self.assertGreater(l4.leak_sigma(state), base_sigma)

        # Same utilities -> higher temperature -> flatter distribution.
        import random
        state_a, state_b = _state(tension=0.0), _state(tension=0.9)
        first = [runtime_mod._choose_intent(state_a, random.Random(1)) for _ in range(40)]
        second = [runtime_mod._choose_intent(state_b, random.Random(1)) for _ in range(40)]
        self.assertGreaterEqual(len(set(second)), len(set(first)) - 1)

    def test_situation_snapshot_is_pure_and_reports_gaps(self):
        states = {
            "ai:a": _state(suspicion={"ai:b": 0.9, "ai:c": 0.05}),
            "ai:c": _state(suspicion={"ai:b": 0.85, "ai:c": 0.1}, faction=True),
            "ai:d": _state(suspicion={"ai:b": 0.8, "ai:c": 0.15}, faction=False),
        }
        before = json.dumps(states, ensure_ascii=False, sort_keys=True)
        snap = variables.situation_snapshot(states, {
            "round": 2, "total_rounds": 5, "revealed_clues": ["c1"],
            "clue_pool": ["c1", "c2"], "alive_count": 4, "heat": 0.6,
        })
        self.assertEqual(json.dumps(states, ensure_ascii=False, sort_keys=True), before)
        self.assertEqual(snap["round_t"], 2)
        self.assertEqual(snap["alive_count"], 4)
        self.assertEqual(snap["clue_revealed_ratio"], 0.5)
        self.assertEqual(snap["consensus_culprit"]["target"], "ai:b")
        self.assertGreater(snap["faction_polarization"], 0.0)
        self.assertLess(snap["info_entropy"], 1.0)
        empty = variables.situation_snapshot({})
        self.assertIn("no_seats", empty["gaps"])

    def test_degradation_checks_are_evidence_based(self):
        report = degradation.audit(self.conn, self.sid)
        self.assertEqual(len(report["checks"]), 8)
        self.assertIn("人格漂移", [c["risk"] for c in report["checks"]])
        self.assertIn("ok", [c["status"] for c in report["checks"]])
        self.assertIn("## 防退化清单", degradation.render_markdown(report))

    def test_degradation_detects_faction_chatter(self):
        for r in (1, 2, 3):
            db.append_game_event(self.conn, self.sid, r, "gm:host", "faction_shift",
                                 {"from": False, "to": True}, ["gm:host"])
        report = degradation.audit(self.conn, self.sid)
        chatter = [c for c in report["checks"] if c["risk"] == "数值抖动"][0]
        self.assertEqual(chatter["status"], "warn")
        self.assertEqual(report["status"], "warn")

    def test_runtime_writes_situation_and_replay_renders_it(self):
        from imms import replay, retro
        scripted = json.loads((ROOT / "examples" / "scripts" / "user_script.json").read_text(encoding="utf-8"))
        runtime.run_session(self.conn, self.sid, 2, scripted, 42, "template")
        situation = db.get_game_state(self.conn, self.sid, "situation")
        self.assertTrue(situation)
        self.assertIn("info_entropy", situation)
        rendered = replay.build_replay(self.conn, self.sid)
        self.assertIn("## 16. 局势层与防退化", rendered)
        report = retro.evaluate(self.conn, self.sid)
        self.assertTrue(report["retrospective"]["degradation"])
        self.assertTrue(report["retrospective"]["situation"])
        self.assertIn("防退化", retro.render_evaluation(report))


if __name__ == "__main__":
    unittest.main()
