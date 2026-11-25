import unittest

from _fixture import ROOT
from imms import ontology, solver


def mini_onto():
    return {
        "script_id": "mini", "title": "mini",
        "facts": [
            {"id": "f1", "type": "truth", "content": "完整真相", "visibility": "truth", "core": True, "keywords": []},
            {"id": "f2", "type": "evidence_chain", "content": "证据", "visibility": "truth", "core": True, "keywords": []},
            {"id": "f3", "type": "private", "content": "秘密", "visibility": "private", "core": False, "keywords": []},
        ],
        "characters": [
            {"id": "a", "name": "甲", "is_victim": False, "is_killer": True, "known_fact_ids": ["f2"]},
            {"id": "b", "name": "乙", "is_victim": False, "is_killer": False, "known_fact_ids": ["f3"]},
        ],
        "clues": [{"id": "c1", "content": "线索", "stage": 0, "reveals_fact_ids": ["f2"]}],
        "clue_pool": ["c1 线索|logLR:a=2.0"],
        "clue_fact_map": {"c1": ["f2"]},
        "evaluation": {"killer_id": "a", "required_fact_ids": ["f1", "f2"], "motive_fact_id": "f2"},
        "timeline_true": ["20:00 事件一", "20:30 事件二"],
        "plot_locks": [], "completions": [],
    }


class TestSolver(unittest.TestCase):
    def test_real_yaml_script_is_unique(self):
        onto = ontology.parse_script_dir(ROOT / "examples" / "scripts" / "snow-night-villa.yaml")
        report = solver.audit_script(onto)
        self.assertEqual(report["verdict"], "solvable_unique")
        self.assertTrue(report["solution"]["matches_truth"])
        self.assertTrue(report["checks"]["required_facts_covered"])
        self.assertTrue(report["checks"]["clue_weights_parseable"])

    def test_truth_fact_is_composite_from_chain(self):
        report = solver.audit_script(mini_onto())
        self.assertIn("f1", report["coverage"]["by_composite"])
        self.assertEqual(report["verdict"], "solvable_unique")
        self.assertEqual(report["difficulty"], "flat")

    def test_timeline_order_conflict(self):
        onto = mini_onto()
        onto["timeline_true"] = ["21:00 事件一", "20:00 事件二"]
        report = solver.audit_script(onto)
        self.assertFalse(report["checks"]["timeline_ordered"])
        self.assertIn("timeline_order", [c["kind"] for c in report["contradictions"]])
        self.assertEqual(report["verdict"], "solvable_with_contradictions")

    def test_unstamped_timeline_is_warning_only(self):
        onto = mini_onto()
        onto["timeline_true"] = ["事件一", "事件二"]
        report = solver.audit_script(onto)
        self.assertTrue(report["checks"]["timeline_ordered"])
        self.assertEqual(report["verdict"], "solvable_unique")
        self.assertTrue(report["timeline"]["warnings"])

    def test_broken_clue_weight_detected(self):
        onto = mini_onto()
        onto["clue_pool"] = ["c1 线索|l|o|g|L|R|:|a|=|2|.|0"]
        report = solver.audit_script(onto)
        self.assertFalse(report["checks"]["clue_weights_parseable"])

    def test_stage_gate_violation(self):
        onto = mini_onto()
        onto["facts"][1]["keywords"] = ["真相"]
        onto["plot_locks"] = ["第2幕才能提及真相"]
        report = solver.audit_script(onto)
        self.assertFalse(report["checks"]["stage_gates_consistent"])
        self.assertIn("stage_gate_violation", [c["kind"] for c in report["contradictions"]])

    def test_hint_only_completion_is_flagged(self):
        onto = mini_onto()
        onto["clues"][0]["reveals_fact_ids"] = ["f1"]
        onto["clue_fact_map"] = {"c1": ["f1"]}
        onto["evaluation"]["required_fact_ids"] = ["f2"]
        onto["completions"] = [{
            "rule": "R3_clue_truth_fallback",
            "field": "clues[c1].reveals_fact_ids",
            "value": ["f1"],
        }]
        report = solver.audit_script(onto)
        self.assertFalse(report["checks"]["completions_effective"])
        self.assertEqual(report["completion_regression"][0]["kind"], "hint_only")
        self.assertTrue(report["missing_links"])

    def test_unreachable_required_fact_blocks_verdict(self):
        onto = mini_onto()
        onto["evaluation"]["required_fact_ids"].append("f_missing")
        report = solver.audit_script(onto)
        self.assertEqual(report["verdict"], "incomplete_facts")
        self.assertEqual(report["coverage"]["unreachable"], ["f_missing"])


if __name__ == "__main__":
    unittest.main()
