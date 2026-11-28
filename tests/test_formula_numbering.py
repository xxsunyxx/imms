import inspect
import unittest
from pathlib import Path

from imms import cli, derive, llm, memory, prompt_engine, runtime

ROOT = Path(__file__).resolve().parents[1]


class TestFormulaNumbering(unittest.TestCase):
    def test_memory_retrieval_is_f26_everywhere_it_is_exposed(self):
        sources = [
            (ROOT / "src" / "imms" / "memory.py").read_text(encoding="utf-8"),
            (ROOT / "src" / "imms" / "llm.py").read_text(encoding="utf-8"),
            (ROOT / "src" / "imms" / "prompt_engine.py").read_text(encoding="utf-8"),
            (ROOT / "src" / "imms" / "cli.py").read_text(encoding="utf-8"),
        ]
        for text in sources:
            self.assertNotIn("F30", text)
        self.assertIn("F26", inspect.getsource(memory.recall))
        self.assertIn("F26", (ROOT / "src" / "imms" / "prompt_engine.py").read_text(encoding="utf-8"))

    def test_f30_remains_the_derived_ability(self):
        self.assertIn("F30", inspect.getsource(derive).split("\n")[0])
        self.assertIn("F30", (ROOT / "docs" / "FORMULAS.md").read_text(encoding="utf-8"))

    def test_cli_memory_help_says_f26(self):
        parser = cli.build_parser()
        memory_parser = next(
            action for action in parser._actions
            if getattr(action, "choices", None) and "memory" in action.choices
        )
        text = memory_parser.choices["memory"].format_help()
        self.assertIn("F26", text)
        self.assertNotIn("F30", text)


if __name__ == "__main__":
    unittest.main()
