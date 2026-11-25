import copy
import json
import tempfile
import unittest
from pathlib import Path

from _fixture import ROOT, make_session_db
from imms import db, retro, runtime, vote


def _scripted(**overrides):
    data = json.loads((ROOT / "examples" / "scripts" / "user_script.json").read_text(encoding="utf-8"))
    data.update(overrides)
    return data


class TestVotingSystem(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn, self.session = make_session_db(Path(self.tmp.name) / "t.db")
        self.sid = self.session["session_id"]

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_rules_are_merged_and_bounded(self):
        rules = vote.rules_for(
            onto={"evaluation": {"vote_rules": {"rounds": 9, "strategy": "persona"}}},
            scripted={"vote_config": {"temperature": 0.5, "tie_break": "abstain"}},
        )
        self.assertEqual(rules["rounds"], 5)
        self.assertEqual(rules["strategy"], "persona")
        self.assertEqual(rules["temperature"], 0.5)
        self.assertEqual(rules["tie_break"], "abstain")

    def test_resolver_maps_names_and_blocks_self_vote(self):
        onto = db.get_script(self.conn, self.session["script_id"])
        seat_ids = [p["id"] for p in self.session["participants"] if p["kind"] != "gm"]
        runtime.init_runtime(self.conn, self.session)
        state = db.get_game_state(self.conn, self.sid, "runtime")
        to_seat, _, _ = vote._make_resolver(self.session, onto, state)
        rules = vote.normalize_rules()
        mapped = vote.validate_ballot("周慕辰", "human:lin_wanqiu", seat_ids, to_seat, rules)
        self.assertEqual(mapped["choice"], "ai:zhou_muchen")
        blocked = vote.validate_ballot("林晚秋", "human:lin_wanqiu", seat_ids, to_seat, rules)
        self.assertEqual(blocked["code"], "self_vote_blocked")
        self.assertFalse(blocked["valid"])

    def test_tie_break_uses_evidence_not_insertion_order(self):
        seat_ids = ["ai:b", "ai:a"]
        ballots = [
            {"voter": "s1", "choice": "ai:b", "valid": True, "code": "ok"},
            {"voter": "s2", "choice": "ai:a", "valid": True, "code": "ok"},
        ]
        rules = vote.normalize_rules({"tie_break": "evidence"})
        tally = vote.tally_ballots(ballots, seat_ids, rules, {"ai:a": 2.0, "ai:b": 0.0})
        self.assertTrue(tally["tie"])
        self.assertEqual(tally["winner"], "ai:a")
        self.assertEqual(tally["tie_resolved_by"], "evidence")

    def test_persona_strategy_changes_ai_ballot(self):
        seat_ids = ["ai:a", "ai:b", "ai:c"]
        state = {
            "ell": {"ai:a": 0.2, "ai:b": 0.1, "ai:c": 0.0},
            "relations": {"ai:b": {"grudge": 0.9, "like": 0.0, "trust": 0.0, "fear": 0.0}},
            "emotion": {"exposure_risk": 0.0},
        }
        decision = vote.ai_ballot(
            state, "ai:a", seat_ids,
            vote.normalize_rules({"strategy": "persona", "temperature": 0.1}),
        )
        self.assertEqual(decision["choice"], "ai:b")
        self.assertAlmostEqual(sum(decision["distribution"].values()), 1.0, places=6)

    def test_vote_ledger_and_retro_audit(self):
        scripted = _scripted()
        runtime.run_session(self.conn, self.sid, 2, scripted, 42, "template")
        state = db.get_game_state(self.conn, self.sid, "vote_state")
        result = db.get_game_state(self.conn, self.sid, "result")
        self.assertEqual(len(state["rounds"]), 1)
        self.assertEqual(result["ballot_rounds"], 1)
        self.assertIn(result["winner"], result["tally"])
        events = [r["act_type"] for r in self.conn.execute(
            "SELECT act_type FROM game_events WHERE session_id=? ORDER BY seq", (self.sid,)
        )]
        self.assertIn("vote_cast", events)
        self.assertIn("vote_tally", events)
        self.assertIn("vote_result", events)
        report, markdown = retro.evaluate_and_render(self.conn, self.sid)
        self.assertEqual(report["retrospective"]["vote_audit"]["status"], "ok")
        self.assertIn("投票审计", markdown)

    def test_multi_round_replay_and_self_vote_blocked(self):
        scripted = _scripted(
            vote_config={"rounds": 2, "strategy": "persona", "majority_stop": False},
            votes=[
                {"target": "lin_wanqiu", "reason": "先观察票型"},
                {"target": "zhou_muchen", "reason": "改投证据指向者"},
            ],
            motive_fact_id="t_drug",
        )
        runtime.run_session(self.conn, self.sid, 2, scripted, 42, "template")
        result = db.get_game_state(self.conn, self.sid, "result")
        self.assertEqual(result["ballot_rounds"], 2)
        self.assertEqual(result["motive_fact_id"], "t_drug")
        replay = vote.replay_tally(self.conn, self.sid)
        self.assertEqual(replay["ballots"], 5)
        audit = vote.audit(self.conn, self.sid)
        self.assertEqual(audit["ballots"], 10)
        self.assertEqual(audit["self_votes_blocked"], 1)
        self.assertGreaterEqual(audit["vote_reasonableness"], 0.0)
        self.assertLessEqual(audit["vote_reasonableness"], 1.0)

    def test_manual_ballot_is_recorded(self):
        entry = vote.cast_ballot(self.conn, self.sid, "human:lin_wanqiu", "zhou_muchen", "手工")
        self.assertEqual(entry["ballot"]["choice"], "ai:zhou_muchen")
        state = db.get_game_state(self.conn, self.sid, "vote_state")
        self.assertEqual(len(state["manual_ballots"]), 1)
        replay = vote.replay_tally(self.conn, self.sid)
        self.assertEqual(replay["ballots"], 1)


if __name__ == "__main__":
    unittest.main()
