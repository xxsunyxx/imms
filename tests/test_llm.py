import unittest

from imms import llm


class TestLLM(unittest.TestCase):
    def test_parse_contains_claims_and_intent(self):
        out = llm.parse_speech(
            '```json\n{"utterance":"我昨晚一直在书房。","emotion_shown":"冷峻",'
            '"claimed_facts":["clue_01","truth_secret"]}\n```',
            ["clue_01"], "accuse",
        )
        self.assertEqual(out["claimed_facts"], ["clue_01"])
        self.assertEqual(out["act_type"], "accuse")
        self.assertEqual(out["emotion_shown"], "冷峻")

    def test_parse_rejects_empty_and_bad_json(self):
        for text in ("", "not json", "{}"):
            with self.assertRaises(llm.LLMError):
                llm.parse_speech(text, ["clue_01"], "assert")

    def test_template_provider_is_offline(self):
        self.assertIsNone(llm.TemplateProvider().generate("prompt"))
        self.assertEqual(llm.build_provider("template").name, "template")

    def test_prompt_hides_truth_and_keeps_intent(self):
        state = {
            "l1": {"OCEAN.C": 0.8, "lexicon": "反问句"},
            "emotion": {"arousal": 0.4, "stress": 0.2, "valence": -0.1},
            "relations": {"ai:zhou_muchen": {"like": 0.1, "trust": -0.2, "suspicion": 0.7}},
            "suspicion": {"ai:zhou_muchen": 0.7},
            "lie_ledger": [], "beliefs": {"clue_01": {}},
        }
        prompt = llm.build_prompt(state, "accuse", "赵管事", "clue_01", 3, ["clue_01"])
        self.assertIn("act_type=accuse", prompt)
        self.assertIn("clue_01", prompt)
        self.assertNotIn("is_true", prompt)


if __name__ == "__main__":
    unittest.main()
