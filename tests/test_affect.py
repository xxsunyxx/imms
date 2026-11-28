import json
import tempfile
import unittest
from pathlib import Path

from _fixture import ROOT, make_session_db
from imms import contradiction, db, emotion, faction, journal, replay, retro, runtime


def _scripted(**overrides):
    data = json.loads((ROOT / "examples" / "scripts" / "user_script.json").read_text(encoding="utf-8"))
    data.update(overrides)
    return data


class TestAffectRuntime(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn, self.session = make_session_db(Path(self.tmp.name) / "t.db")
        self.sid = self.session["session_id"]

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_discrete_emotion_mapping(self):
        self.assertEqual(emotion.discrete_from_pad({
            "valence": -0.4, "arousal": 0.8, "dominance": 0.8,
        }), "愤怒")
        self.assertEqual(emotion.discrete_from_pad({
            "valence": -0.4, "arousal": 0.8, "dominance": 0.2,
        }), "恐惧")
        self.assertEqual(emotion.discrete_from_pad({
            "valence": -0.4, "arousal": 0.2, "dominance": 0.5,
        }), "悲伤")
        self.assertEqual(emotion.discrete_from_pad({
            "valence": 0.0, "arousal": 0.2, "dominance": 0.5,
        }), "平静")

    def test_trauma_amplifies_impact_and_tau(self):
        state = {
            "emotion": {"valence": 0.0, "arousal": 0.2, "dominance": 0.5},
            "trauma": [{"trigger": "被夺权"}],
        }
        report = emotion.apply_impact(
            state, {"valence": -0.15, "arousal": 0.12, "dominance": 0.05},
            "他被夺权之后性情大变", 2,
        )
        self.assertTrue(report["hit"])
        self.assertEqual(report["multiplier"], emotion.TRAUMA_MULTIPLIER)
        self.assertEqual(report["tau"], 3.6)
        self.assertLess(state["emotion"]["valence"], -0.15)
        self.assertTrue(state.get("trauma_hits"))

    def test_contradiction_attribution_and_numeric_effects(self):
        state = {
            "seat_id": "ai:b", "character_id": "b",
            "emotion": {"valence": 0.0, "arousal": 0.2, "dominance": 0.5},
            "suspicion": {"ai:a": 0.1},
            "beliefs": {"f1": {"belief": 0.9}},
            "pending_contradictions": [],
        }
        observations = [{
            "conflict": {
                "kind": "statement_conflict", "speaker": "ai:a", "seq": 7,
                "prior_seq": 3, "fact_ids": ["f1"], "status": "revised",
                "strength": 0.72,
            }
        }]
        events = contradiction.detect(observations, state, 2)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["witness"], "ai:b")
        self.assertEqual(events[0]["subject"], "ai:a")
        self.assertEqual(events[0]["confidence"], 0.9)
        changes = contradiction.apply(state, events, 2)
        self.assertAlmostEqual(state["emotion"]["arousal"], 0.5)
        self.assertAlmostEqual(state["suspicion"]["ai:a"], 0.3)
        self.assertTrue(any(c["dim"] == "suspicion" for c in changes))
        self.assertIsNone(contradiction.pending_for_round(state, 2))
        pending = contradiction.pending_for_round(state, 3)
        self.assertEqual(pending["seq"], 7)
        contradiction.mark_expressed(state, pending)
        self.assertIsNone(contradiction.pending_for_round(state, 3))

    def test_faction_hysteresis_and_cooldown(self):
        onto = {"factions": [{"id": "F1", "name": "甲阵营"}]}
        char = {"faction": "F1", "goal_fit": 0.8}
        state = {
            "seat_id": "ai:a", "character_id": "a", "faction": None,
            "emotion": {"morale": 0.8, "exposure_risk": 0.1},
            "relations": {"ai:b": {"like": 0.8, "trust": 0.8, "respect": 0.8, "commitment": 0.2}},
            "l1": {"values.loyalty": 0.3},
        }
        event = faction.update(state, 1, onto, char)
        self.assertIsNotNone(event)
        self.assertEqual(state["faction"], "F1")
        # The threshold-off band plus a two-round cooldown prevents a flip.
        char["goal_fit"] = -1.0
        self.assertIsNone(faction.update(state, 2, onto, char))
        self.assertEqual(state["faction"], "F1")

    def test_runtime_emits_affect_events_and_replay(self):
        onto = db.get_script(self.conn, self.session["script_id"])
        onto["factions"] = [{"id": "F1", "name": "甲阵营"}]
        human = self.session["human_character_id"]
        for char in onto["characters"]:
            char["faction"] = "F1"
            char["goal_fit"] = 0.8
            if char["id"] == human:
                char["trauma"] = [{"trigger": "被夺权"}]
        db.save_script(self.conn, onto)
        ai_seat = next(p["id"] for p in self.session["participants"] if p["kind"] == "ai_character")
        db.append_game_event(
            self.conn, self.sid, 1, ai_seat, "accuse",
            {"utterance": "他在被夺权之后性情大变", "claimed_facts": [], "target": human},
            ["public"],
        )
        runtime.run_session(self.conn, self.sid, 2, _scripted(), 42, "template")
        trauma = journal.audit_events(self.conn, self.sid, act_type="trauma_trigger")
        shifts = journal.audit_events(self.conn, self.sid, act_type="faction_shift")
        self.assertGreaterEqual(len(trauma), 1)
        self.assertGreaterEqual(len(shifts), 1)
        self.assertEqual(trauma[0]["payload"]["trigger"], "被夺权")
        text = replay.build_replay(self.conn, self.sid)
        self.assertIn("## 13. 情绪、矛盾与阵营", text)
        report = retro.evaluate(self.conn, self.sid)
        self.assertEqual(report["retrospective"]["affect"]["trauma_triggers"], len(trauma))
        self.assertEqual(report["retrospective"]["affect"]["faction_shifts"], len(shifts))


if __name__ == "__main__":
    unittest.main()
