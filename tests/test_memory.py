import json
import tempfile
import unittest
from pathlib import Path

from _fixture import make_session_db
from imms import db, formulas, gm, llm, memory, runtime


def _event(seq, actor, utterance, facts=None, round_no=1, act_type="assert"):
    return {
        "seq": seq, "actor": actor, "act_type": act_type,
        "payload": {"utterance": utterance, "claimed_facts": facts or [], "target": None},
        "round": round_no,
    }


class TestMemoryLedger(unittest.TestCase):
    def test_observe_records_listener_audience_and_dedupes(self):
        state = {"emotion": {"arousal": 0.2, "valence": 0.0}, "memory": [], "commitments": {}}
        ev = _event(7, "ai:a", "窗台有水渍", ["c_window"], round_no=2)
        result = memory.observe(state, ev, 2, audience=["ai:b", "human:c"])
        self.assertIsNotNone(result)
        record = result["record"]
        self.assertEqual(record["seq"], "7")
        self.assertEqual(record["audience"], ["ai:b", "human:c"])
        self.assertEqual(record["status"], "stated")
        self.assertIn("c_window", state["commitments"])
        self.assertEqual(state["commitments"]["c_window"]["status"], "active")
        self.assertIsNone(memory.observe(state, ev, 2, audience=["ai:b"]))

    def test_commitment_state_machine(self):
        state = {"memory": [], "commitments": {}}
        memory.observe(state, _event(1, "ai:a", "我看见了账本", ["f_ledger"]), 1)
        memory.observe(state, _event(2, "ai:a", "更正，我看见的是药柜", ["f_ledger"],
                                     act_type="revise"), 2)
        self.assertEqual(state["commitments"]["f_ledger"]["status"], "revised")
        memory.observe(state, _event(3, "ai:a", "我撤回刚才那句", ["f_ledger"],
                                     act_type="retract"), 3)
        item = state["commitments"]["f_ledger"]
        self.assertEqual(item["status"], "retracted")
        self.assertEqual([h["status"] for h in item["history"]], ["stated", "revised", "retracted"])
        memory.observe(state, _event(4, "ai:a", "其实不是这样", ["f_ledger"],
                                     act_type="revise"), 4)
        self.assertEqual(state["commitments"]["f_ledger"]["status"], "retracted")

    def test_retraction_without_claims_targets_latest_claim(self):
        state = {"memory": [], "commitments": {}}
        memory.observe(state, _event(1, "ai:a", "我看见账本", ["f_a"]), 1)
        memory.observe(state, _event(2, "ai:a", "我看见药柜", ["f_b"]), 2)
        memory.observe(state, _event(3, "ai:a", "我撤回刚才那句", [], act_type="retract"), 3)
        self.assertEqual(state["commitments"]["f_b"]["status"], "retracted")
        self.assertEqual(state["commitments"]["f_a"]["status"], "active")

    def test_recall_consumes_f30_score(self):
        state = {
            "emotion": {"arousal": 0.4, "valence": 0.0, "dominance": 0.5},
            "memory": [], "commitments": {},
        }
        memory.observe(state, _event(1, "ai:a", "窗台的融雪水渍", ["c_window"], round_no=1), 1)
        memory.observe(state, _event(2, "ai:a", "遗嘱草稿的签字", ["c_will"], round_no=3), 3)
        items = memory.recall(state, 4, cue="窗台 水渍", limit=2)
        self.assertEqual(len(items), 2)
        self.assertGreaterEqual(items[0]["recall_score"], items[1]["recall_score"])
        top = next(i for i in items if i["seq"] == "1")
        expected = formulas.memory_score(
            3, 3.0, top["importance"], memory.similarity("窗台 水渍", "窗台的融雪水渍 c_window"),
            memory.emotion_congruence(state["emotion"], top["emotion_snapshot"]),
        )
        self.assertAlmostEqual(top["recall_score"], round(expected, 4), places=4)

    def test_prompt_includes_recalled_section(self):
        state = {"l1": {}, "emotion": {"arousal": 0.2, "stress": 0.1, "valence": 0.0},
                 "suspicion": {}, "relations": {}, "lie_ledger": [], "beliefs": {}}
        prompt = llm.build_prompt(
            state, "assert", "林晚秋", "c_window", 2, {"c_window"},
            recalled_lines=["- 第1轮 ai:a 对所有人说：「窗台有水渍」（状态=stated，召回分=1.2）"],
        )
        self.assertIn("记忆召回（F26", prompt)
        self.assertIn("第1轮 ai:a 对所有人说", prompt)


class TestRuntimeMemory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn, self.session = make_session_db(Path(self.tmp.name) / "t.db")
        self.sid = self.session["session_id"]

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _scripted(self, utterances=None):
        return {
            "utterances": utterances or [
                {"act_type": "assert", "utterance": "我那晚路过书房。", "claimed_facts": ["c_report"]},
                {"act_type": "assert", "utterance": "我撤回刚才那句。", "claimed_facts": ["c_report"]},
            ],
            "vote": "zhou_muchen",
        }

    def test_runtime_records_audience_commitments_and_recall(self):
        runtime.run_session(self.conn, self.sid, 2, self._scripted(), 42, "template")
        state = db.get_game_state(self.conn, self.sid, "runtime", {})
        human = state["human:lin_wanqiu"]
        records = [m for m in human["memory"] if isinstance(m, dict)]
        self.assertGreaterEqual(len(records), 4)
        self.assertTrue(all(r["audience"] for r in records))
        self.assertEqual(human["commitments"]["c_report"]["status"], "retracted")
        rows = self.conn.execute(
            "SELECT round, payload_json FROM game_events WHERE session_id=? AND round=2 AND actor LIKE 'ai:%'",
            (self.sid,),
        ).fetchall()
        self.assertTrue(rows)
        payload = json.loads(rows[0]["payload_json"])
        self.assertGreaterEqual(len(payload.get("recalled", [])), 1)

    def test_gm_diff_includes_memory_changes(self):
        runtime.run_session(self.conn, self.sid, 2, self._scripted(), 42, "template")
        row = gm.create_branch(
            self.conn, self.sid, 2, label="改口", override_round=2,
            override_utterance="我改主意了，我只相信自己。",
            decision={"policy": "information"},
        )
        diff = gm.branch_diff(self.conn, row["branch_id"])
        self.assertIn("memory_changes", diff)
        self.assertIn("memory_changes", diff["summary"])
        self.assertGreaterEqual(diff["summary"]["memory_changes"], 1)


if __name__ == "__main__":
    unittest.main()
