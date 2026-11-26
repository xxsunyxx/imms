"""Performance guides: state -> wording only, no facts, numbers or LLM."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from imms import llm, ontology, normalize, performance_guide, prompt_engine, runtime  # noqa: E402
from imms import assemble, db  # noqa: E402

CFG = str(Path(__file__).resolve().parents[1] / "config")


def _state(**overrides):
    base = {
        "emotion": {"arousal": 0.3, "valence": -0.29, "stress": 0.4,
                    "morale": 0.8, "exposure_risk": 0.5, "cognitive_load": 0.1},
        "suspicion": {"ai:c2": 0.63},
        "delivery": {"item": "改口你为您", "hit": True},
    }
    base.update(overrides)
    return base


class TestWordingOnlyContract(unittest.TestCase):
    def test_intent_guides_are_distinct_and_worded(self):
        a = performance_guide.build(_state(), "defend", "ai:c2", CFG)
        b = performance_guide.build(_state(), "accuse", "ai:c2", CFG)
        self.assertNotEqual(a, b)
        self.assertTrue(any("转折" in line for line in a))
        self.assertTrue(any("加压" in line for line in b))

    def test_suspicion_buckets_change_wording(self):
        high = performance_guide.build(_state(), "ask", "ai:c2", CFG)
        low = performance_guide.build(
            _state(suspicion={"ai:c2": 0.05}), "ask", "ai:c2", CFG)
        self.assertTrue(any("明显起疑" in line for line in high))
        self.assertTrue(any("暂无明显怀疑" in line for line in low))

    def test_no_fact_id_number_assignment_or_quote(self):
        for intent in ("assert", "ask", "accuse", "defend", "ally",
                       "deflect", "confess", "provoke"):
            guides = performance_guide.build(_state(), intent, "ai:c2", CFG)
            self.assertTrue(guides)
            for line in guides:
                self.assertEqual(performance_guide.sanitize(line), line)
                self.assertNotIn("=", line)
                self.assertFalse(any(ch.isdigit() for ch in line))
                self.assertNotIn("「", line)
                self.assertNotIn("→", line)

    def test_emotion_guides_reuse_lexicon_without_numbers(self):
        guides = performance_guide.build(_state(), "defend", "ai:c2", CFG)
        self.assertIn("用词偏冷", guides)
        self.assertIn("措辞收窄", guides)
        self.assertFalse(any("(" in line for line in guides))

    def test_delivery_item_only_as_timing_cue(self):
        guides = performance_guide.build(_state(), "defend", "ai:c2", CFG)
        self.assertTrue(any("改口你为您" in line and "不得当作事实" in line
                            for line in guides))
        none = performance_guide.build(
            _state(delivery={"item": "改口你为您", "hit": False}),
            "defend", "ai:c2", CFG)
        self.assertFalse(any("改口你为您" in line for line in none))

    def test_no_llm_call(self):
        with mock.patch("imms.llm.build_provider") as provider:
            performance_guide.build(_state(), "defend", "ai:c2", CFG)
        provider.assert_not_called()

    def test_guide_block_renders_verbatim(self):
        guides = performance_guide.build(_state(), "defend", "ai:c2", CFG)
        view, ctx = {}, {"guides": guides}
        block = prompt_engine.block_guides(view, ctx)
        self.assertTrue(block)
        for line in guides:
            self.assertTrue(any(line in rendered for rendered in block))


class TestRuntimeWiring(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        onto = ontology.parse_script_dir(
            Path(__file__).resolve().parents[1] / "examples" / "scripts" / "snow-night-villa.yaml")
        self.conn = db.connect(str(root / "r.db"))
        db.save_script(self.conn, onto)
        personae, _ = normalize.normalize_persona(
            onto, Path(__file__).resolve().parents[1] / "config")
        db.save_persona_l1(self.conn, onto["script_id"], personae)
        chars = [c["id"] for c in onto["characters"] if not c.get("is_victim")]
        self.session = assemble.assemble(self.conn, onto["script_id"], chars[0])
        self.scripted = {"utterances": ["我没什么要补充的。"] * 5, "vote": chars[1]}

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_performance_guides_reach_generation_prompt(self):
        calls = []

        class Stub:
            name = "http"

            def generate(self, prompt):
                calls.append(prompt)
                if "你是说话风格编译器" in prompt:
                    req = prompt[prompt.rindex("\n{\n") + 1:]
                    import json
                    data = json.loads(req)
                    return json.dumps(
                        {k: data[k] for k in ("anchor", "drift", "boundary")},
                        ensure_ascii=False)
                return ('{"utterance":"我先听你说完。","emotion_shown":"平静",'
                        '"claimed_facts":[]}')

        with mock.patch("imms.llm.build_provider", return_value=Stub()):
            runtime.run_session(self.conn, self.session["session_id"], 1,
                                self.scripted, 42, "http")
        gen = [p for p in calls
               if "你是说话风格编译器" not in p and "信息安全裁判" not in p]
        self.assertTrue(gen)
        self.assertTrue(any("# 表演指引" in p for p in gen))
        self.assertTrue(any("把本轮意图" in p for p in gen))

    def test_template_path_has_no_guide_block(self):
        calls = []
        with mock.patch.object(runtime.llm, "build_prompt", wraps=runtime.llm.build_prompt) as bp:
            runtime.run_session(self.conn, self.session["session_id"], 1,
                                self.scripted, 42, "template")
        for call in bp.call_args_list:
            self.assertIsNone(call.kwargs.get("guides") or None)


if __name__ == "__main__":
    unittest.main()
