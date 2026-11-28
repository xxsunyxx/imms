import copy
import json
import tempfile
import unittest
from pathlib import Path

from _fixture import ROOT, make_session_db
from imms import cli, db, oracle, runtime


def _scripted(**overrides):
    data = json.loads((ROOT / "examples" / "scripts" / "user_script.json").read_text(encoding="utf-8"))
    data.update(overrides)
    return data


class TestOracle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn, self.session = make_session_db(Path(self.tmp.name) / "t.db")
        self.sid = self.session["session_id"]
        self.onto = db.get_script(self.conn, self.session["script_id"])

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _structured_script(self):
        onto = copy.deepcopy(self.onto)
        onto["endings"] = [
            {"id": "justice", "condition": {"solved": True}, "text": "真凶伏法"},
            {"id": "wrongful", "condition": {"solved": False}, "text": "冤案"},
        ]
        db.save_script(self.conn, onto)

    def test_authoring_gate_distinguishes_legacy_and_structured_endings(self):
        legacy = oracle.authoring_validation(self.onto)
        self.assertEqual(legacy["verdict"], "needs_work")
        self.assertTrue(legacy["checks"]["blind_oracle_ready"])
        self.assertTrue(any("结构化结局" in w for w in legacy["warnings"]))

        self._structured_script()
        current = oracle.authoring_validation(db.get_script(self.conn, self.session["script_id"]))
        self.assertEqual(current["verdict"], "pass")
        self.assertTrue(current["checks"]["endings_parseable"])

    def test_blind_check_uses_public_evidence_only(self):
        runtime.run_session(self.conn, self.sid, 2, _scripted(), 42, "template")
        report = oracle.blind_play_check(self.conn, self.sid)
        self.assertTrue(report["derived_without_truth"])
        self.assertTrue(report["unique"])
        self.assertNotIn("ai:", report["suspect"] or "")
        self.assertNotIn("human:", str(report["first_unique_round"]))
        self.assertEqual(report["suspect"], "周慕辰")
        self.assertTrue(report["audit_only"]["matches_truth"])
        self.assertNotIn("top_score", report["audit_only"])

        stored = db.get_game_state(self.conn, self.sid, "result")
        stored["culprit"] = "ai:gu_chengye"
        db.set_game_state(self.conn, self.sid, "result", stored)
        changed = oracle.blind_play_check(self.conn, self.sid)
        self.assertEqual(changed["suspect"], report["suspect"])
        self.assertFalse(changed["audit_only"]["matches_truth"])

    def test_oracle_report_combines_authoring_blind_and_ending(self):
        self._structured_script()
        runtime.run_session(self.conn, self.sid, 2, _scripted(), 42, "template")
        report = oracle.oracle_report(self.conn, self.sid)
        self.assertEqual(report["version"], oracle.VERSION)
        self.assertEqual(report["authoring"]["verdict"], "pass")
        self.assertEqual(report["blind"]["session_id"], self.sid)
        self.assertIn("code", report["ending"])

    def test_five_game_smoke_is_reproducible_and_scratch_only(self):
        before = db.list_scripts(self.conn)
        first = oracle.smoke(self.conn, self.session["script_id"], games=5, rounds=2, seed=7)
        second = oracle.smoke(self.conn, self.session["script_id"], games=5, rounds=2, seed=7)
        self.assertEqual(first["status"], "ok")
        self.assertEqual(len(first["games"]), 5)
        self.assertEqual(first["config"]["games"], 5)
        self.assertEqual(
            [row["fingerprint"] for row in first["games"]],
            [row["fingerprint"] for row in second["games"]],
        )
        self.assertTrue(first["summary"]["reproducible_inputs"])
        self.assertEqual(before, db.list_scripts(self.conn))
        for row in first["games"]:
            self.assertNotIn("ai:", row["winner"] or "")
            self.assertIn("ending", row)

    def test_cli_exposes_oracle_and_audit_all_gate(self):
        parser = cli.build_parser()
        args = parser.parse_args(["oracle", "check", "--session", "S"])
        self.assertEqual(args.oracle_action, "check")
        self.assertEqual(args.session, "S")
        args = parser.parse_args(["oracle", "smoke", "--script-id", "X", "--games", "3"])
        self.assertEqual(args.oracle_action, "smoke")
        self.assertEqual(args.games, 3)

        rows = cli.audit_all_with_gate(self.conn)
        for row in rows:
            self.assertIn("authoring", row)
            self.assertIn("admitted", row)

    def test_unsupported_goal_check_is_flagged(self):
        onto = copy.deepcopy(self.onto)
        for char in onto["characters"]:
            if char["id"] == "lin_wanqiu":
                char["goals"] = [{"text": "藏秘密", "check": {"type": "telepathy"}}]
        db.save_script(self.conn, onto)
        report = oracle.authoring_validation(onto)
        self.assertFalse(report["checks"]["goal_checks_supported"])
        self.assertEqual(report["verdict"], "needs_work")
        self.assertTrue(any("telepathy" in w for w in report["warnings"]))


if __name__ == "__main__":
    unittest.main()
