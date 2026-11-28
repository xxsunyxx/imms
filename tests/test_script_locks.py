import json
import tempfile
import unittest
from pathlib import Path

from _fixture import ROOT, make_session_db
from imms import db, gm, journal, replay, retro, runtime, script_locks, solver


def _scripted(**overrides):
    data = json.loads((ROOT / "examples" / "scripts" / "user_script.json").read_text(encoding="utf-8"))
    data.update(overrides)
    return data


class TestScriptLocks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn, self.session = make_session_db(Path(self.tmp.name) / "t.db")
        self.sid = self.session["session_id"]

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _add_locks(self, **kwargs):
        onto = db.get_script(self.conn, self.session["script_id"])
        human = self.session["human_character_id"]
        for char in onto["characters"]:
            if char["id"] == human:
                char["must_say"] = kwargs.get("must_say", ["我有话说"])
                char["taboo"] = kwargs.get("taboo", ["毒药"])
        onto["script_locks"] = kwargs.get("global_locks", [
            {"type": "must_trigger", "value": "烛台", "weight": 0.9},
        ])
        db.save_script(self.conn, onto)
        script_locks.ensure_session(self.conn, self.sid, onto, self.session)
        return onto

    def test_only_explicit_locks_are_compiled(self):
        onto = {
            "characters": [
                {"id": "a", "must_say": ["开场白"], "taboo": ["禁词"]},
                {"id": "b", "script_locks": ["must_trigger:烛台"]},
            ],
            "script_locks": ["没有类型的裸文本"],
        }
        locks = script_locks.compile_locks(onto)
        types = [(x["owner"], x["type"], x["value"]) for x in locks]
        self.assertIn(("a", "must_say", "开场白"), types)
        self.assertIn(("a", "taboo", "禁词"), types)
        self.assertIn(("b", "must_trigger", "烛台"), types)
        self.assertEqual([x["type"] for x in locks if x["type"] == "ignored"], ["ignored"])

    def test_install_assigns_only_own_and_global_locks(self):
        self._add_locks()
        human = next(p for p in self.session["participants"] if p["kind"] == "human_character")
        other = next(p for p in self.session["participants"] if p["kind"] == "ai_character")
        state = script_locks.seat_state(self.session, human["id"])
        self.assertTrue(any(x["type"] == "must_say" for x in state["locks"]))
        self.assertTrue(any(x["type"] == "must_trigger" for x in state["locks"]))
        other_state = script_locks.seat_state(self.session, other["id"])
        self.assertFalse(any(x["type"] == "must_say" for x in other_state["locks"]))
        self.assertTrue(any(x["type"] == "must_trigger" for x in other_state["locks"]))

    def test_guard_blocks_taboo_and_records_must_say(self):
        onto = self._add_locks()
        locks = script_locks.compile_locks(onto)
        state = {"locks": locks, "satisfied": {}, "violations": []}
        act = {"utterance": "这瓶毒药有问题，我有话说。", "claimed_facts": [],
               "act_type": "assert", "intent": "assert", "lie_id": "L1"}
        report = script_locks.guard_output(state, act, 2)
        self.assertTrue(report["blocked"])
        self.assertEqual(act["utterance"], "这件事现在不能说。")
        self.assertEqual(act["claimed_facts"], [])
        self.assertIsNone(act["lie_id"])
        satisfied = script_locks.observe_output(state, act, 2)
        self.assertEqual(satisfied, [])

    def test_runtime_blocks_human_taboo_and_audits(self):
        self._add_locks(global_locks=[])
        scripted = _scripted(utterances=[
            "我有话说，关于毒药", "我有话说", "我有话说", "我有话说", "我有话说",
        ])
        result = runtime.run_session(self.conn, self.sid, 2, scripted, 42, "template")
        rows = journal.audit_events(self.conn, self.sid, act_type="script_lock_violation")
        self.assertGreaterEqual(len(rows), 1)
        self.assertGreaterEqual(result["infoguard"].get("script_lock_blocked", 0), 1)
        human = next(p["id"] for p in self.session["participants"] if p["kind"] == "human_character")
        human_text = " ".join(
            (r["payload"] or {}).get("utterance", "")
            for r in journal.audit_events(self.conn, self.sid, actor=human)
            if (r["payload"] or {}).get("utterance")
        )
        self.assertNotIn("毒药", human_text)
        # The taboo attempt was blocked, but the attempt itself stays red.
        self.assertEqual(result["script_locks"]["status"], "fail")
        self.assertEqual(result["script_locks"]["counts"]["violated"], 1)
        self.assertEqual(result["script_locks"]["counts"]["satisfied"], 1)
        self.assertIn("剧本锁与即兴护栏", replay.build_replay(self.conn, self.sid))
        report = retro.evaluate(self.conn, self.sid)
        self.assertEqual(report["retrospective"]["script_locks"]["status"], "fail")

    def test_untriggered_lock_is_red_and_branch_cannot_bypass(self):
        self._add_locks(global_locks=[{"type": "must_trigger", "value": "从未出现的桥段", "weight": 0.9}])
        result = runtime.run_session(self.conn, self.sid, 2, _scripted(), 42, "template")
        self.assertEqual(result["script_locks"]["status"], "fail")
        failed = [x for x in result["script_locks"]["failed"] if x["status"] == "untriggered"]
        self.assertTrue(failed)
        row = gm.create_branch(
            self.conn, self.sid, 2, override_round=2, override_utterance="我改口了。",
            decision={"policy": "information"},
        )
        checks = row["report_json"]["regress"]["checks"]
        self.assertFalse(checks["script_locks"])

    def test_ir3_unique_path_guard(self):
        onto = {
            "facts": [
                {"id": "f1", "content": "唯一事实"},
                {"id": "f2", "content": "另一事实"},
            ],
            "clues": [{"id": "c1", "reveals_fact_ids": ["f1"]}],
            "clue_fact_map": {"c1": ["f1"]},
            "characters": [
                {"id": "a", "known_fact_ids": ["f2"]},
                {"id": "b", "known_fact_ids": ["f2"]},
            ],
        }
        self.assertTrue(solver.is_unique_path(onto, "f1"))
        self.assertFalse(solver.is_unique_path(onto, "f2"))
        audit = solver.unique_path_audit(onto)
        self.assertEqual([x["fact_id"] for x in audit["unique"]], ["f1"])
        burst = {"evidence_action": {"kind": "destroy_clue", "clue_id": "c1"}}
        report = script_locks.guard_evidence(onto, burst)
        self.assertEqual(report["status"], "downgraded")
        self.assertEqual(report["downgrade"], "damaged_recoverable")

    def test_ir4_guard_downgrades_lock_violating_improv(self):
        locks = [{
            "id": "a:taboo:0", "owner": "a", "type": "taboo", "value": "毒药", "weight": None,
        }, {
            "id": "a:must_trigger:1", "owner": "a", "type": "must_trigger", "value": "主线", "weight": 0.9,
        }]
        burst = {
            "action_key": "mask_step",
            "speech_act": {"claimed_facts": ["毒药的事实"]},
            "evidence_action": {"kind": "destroy_clue", "detail": "主线"},
        }
        report = script_locks.guard_improv(locks, burst, {"character_id": "a"})
        self.assertEqual(report["status"], "downgraded")
        self.assertEqual(burst["action_key"], "safe_library")
        self.assertEqual(burst["speech_act"]["claimed_facts"], [])
        self.assertIn("taboo_claim", report["reasons"])


if __name__ == "__main__":
    unittest.main()
