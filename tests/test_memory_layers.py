import json
import tempfile
import unittest
from pathlib import Path

from _fixture import ROOT, make_session_db
from imms import db, journal, memory, replay, retro, runtime


class TestFourLayerMemory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn, self.session = make_session_db(Path(self.tmp.name) / "t.db")
        self.sid = self.session["session_id"]

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_semantic_memory_uses_only_explicit_public_knowledge(self):
        onto = {
            "facts": [
                {"id": "f1", "content": "公开的雨夜", "visibility": "public"},
                {"id": "f2", "content": "只有凶手知道的秘密", "visibility": "private"},
            ],
            "world": {"rules": ["讨论轮不得跳过"]},
        }
        state = {}
        entries = memory.seed_semantic(state, onto, {"goals": ["找到凶手"]})
        texts = " ".join(item["text"] for item in entries)
        self.assertIn("公开的雨夜", texts)
        self.assertIn("讨论轮不得跳过", texts)
        self.assertIn("找到凶手", texts)
        self.assertNotIn("只有凶手知道的秘密", texts)

    def test_reflection_is_sourced_and_weight_doubled(self):
        state = {
            "memory": [{
                "seq": "7", "round": 1, "speaker": "a", "status": "stated",
                "utterance": "我知道雨夜的事", "claimed_facts": ["f1"],
                "importance": 0.6,
            }],
            "suspicion": {"a": 0.5},
        }
        insights = memory.reflect(state, 2, "第二幕", {"a": "甲"})
        self.assertEqual(len(insights), 3)
        self.assertEqual(insights[0]["importance"], 1.0)
        self.assertEqual(insights[0]["weight_factor"], 2.0)
        self.assertIn("7", insights[0]["source_seq"])
        self.assertIn("甲", insights[0]["text"])

    def test_recall_hits_all_three_retrievable_layers(self):
        onto = {"facts": [{"id": "f1", "content": "公开的雨夜", "visibility": "public"}]}
        state = {
            "emotion": {"valence": 0.0, "arousal": 0.2, "dominance": 0.5},
            "memory": [{
                "seq": "7", "round": 1, "speaker": "a", "status": "stated",
                "utterance": "我知道雨夜的事", "claimed_facts": ["f1"],
                "importance": 0.6,
            }],
            "suspicion": {"a": 0.5},
        }
        memory.seed_semantic(state, onto, {})
        memory.reflect(state, 2, "第二幕", {"a": "甲"})
        items = memory.recall(state, 3, cue="雨夜", limit=10)
        sources = {item.get("source") for item in items}
        self.assertIn("semantic", sources)
        self.assertIn("reflection", sources)
        self.assertTrue(any(item.get("source") is None for item in items))
        lines = "\n".join(memory.recall_lines(items, {"a": "甲"}))
        self.assertIn("剧本知识", lines)
        self.assertIn("反思", lines)

    def test_runtime_seeds_and_replays_memory_layers(self):
        scripted = json.loads((ROOT / "examples" / "scripts" / "user_script.json").read_text(encoding="utf-8"))
        runtime.run_session(self.conn, self.sid, 2, scripted, 42, "template")
        runtime_state = db.get_game_state(self.conn, self.sid, "runtime", {})
        self.assertTrue(runtime_state)
        state = next(iter(runtime_state.values()))
        self.assertTrue(any(m.get("source") == "semantic" for m in state.get("semantic_memory") or []))
        self.assertTrue(state.get("reflections"))
        events = journal.audit_events(self.conn, self.sid, act_type="reflection")
        self.assertGreaterEqual(len(events), 1)
        self.assertIn("## 15. 记忆分层", replay.build_replay(self.conn, self.sid))
        report = retro.evaluate(self.conn, self.sid)
        totals = report["retrospective"]["memory_layers"]
        self.assertTrue(any(item["semantic"] > 0 for item in totals.values()))
        self.assertTrue(any(item["reflections"] > 0 for item in totals.values()))


if __name__ == "__main__":
    unittest.main()
