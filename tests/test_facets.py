"""G25: continuous vs dissociative facets, memory isolation and audit."""

import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from imms import db, facets, prompt_engine


@dataclass(frozen=True)
class _Scope:
    character_id: str
    known_fact_ids: frozenset
    forbidden_fact_ids: frozenset


def _state():
    return {
        "seat_id": "ai:c1", "character_id": "char_c1",
        "l1": {"OCEAN.O": 0.7},
        "beliefs": {"f_public": {"belief": 0.8}, "f_secret": {"belief": 0.9}},
        "memory": [{"seq": "1", "utterance": "我看见烛台"}],
        "lie_ledger": [{"lie_id": "lie_1"}],
        "facets": facets.new_registry(),
    }


def _reg():
    reg = facets.new_registry()
    facets.register(
        reg, "warm_focus", "continuous",
        {"goal_fixation": 0.7, "possessiveness": 0.6, "memory_continuity": 1.0},
        derived_from=[{"l1": "OCEAN.O", "value": 0.7}],
    )
    facets.register(
        reg, "guardian", "dissociative",
        {"hostile_attribution": 0.8, "identity_continuity": 0.2},
        derived_from=[{"event": "e_guardian", "seq": 12}],
    )
    facets.register(
        reg, "cold_watch", "dissociative",
        {"empathy_suppression": 0.9, "memory_continuity": 0.1},
        derived_from=[{"event": "e_cold", "seq": 20}],
    )
    return reg


class FacetTest(unittest.TestCase):
    def test_registry_validation_and_no_diagnosis_labels(self):
        reg = _reg()
        self.assertEqual(facets.validate(reg), [])
        blob = json.dumps(reg, ensure_ascii=False)
        for label in ("BPD", "DID", "疯批", "病娇", "黑化"):
            self.assertNotIn(label, blob)
        reg["facets"]["guardian"]["memory_scope"] = "shared"
        self.assertIn("guardian:bad_memory_scope", facets.validate(reg))

    def test_continuous_facet_keeps_memory_shared_and_weights_l4_only(self):
        state = _state()
        state["facets"] = _reg()
        before = list(state["memory"])
        facets.switch(state, "warm_focus", 3, trigger="climax")
        self.assertEqual(state["memory"], before)
        self.assertIsNone(state["facet_switch_log"][-1]["memory_gap"])
        self.assertGreater(facets.apply_weights(state)["goal_fixation"], 0.6)
        self.assertEqual(state["l1"], {"OCEAN.O": 0.7})

    def test_dissociative_switch_isolates_buckets_and_records_gap(self):
        state = _state()
        state["facets"] = _reg()
        original = list(state["memory"])
        facets.switch(state, "guardian", 4, trigger="trigger_a")
        self.assertEqual(state["memory"], [])
        self.assertEqual(state["beliefs"], {})
        self.assertEqual(state["lie_ledger"], [])
        gap = state["facet_switch_log"][-1]["memory_gap"]
        self.assertIsNotNone(gap)
        self.assertIn("memory", gap["unshared_buckets"])
        # the two dissociative facets never share what either one learned
        state["memory"].append({"seq": "2", "utterance": "守卫面看见钥匙"})
        facets.switch(state, "cold_watch", 5, trigger="trigger_b")
        self.assertEqual(state["memory"], [])
        facets.switch(state, "guardian", 6, trigger="return")
        self.assertEqual(state["memory"], [{"seq": "2", "utterance": "守卫面看见钥匙"}])
        facets.switch(state, None, 7, trigger="base")
        self.assertEqual(state["memory"], original)

    def test_scope_filter_keeps_only_facet_owned_facts(self):
        state = _state()
        state["facets"] = _reg()
        facets.switch(state, "guardian", 4)
        scope = _Scope("char_c1", frozenset({"f_public", "f_secret"}), frozenset())
        filtered = facets.scope_filter(state, scope)
        self.assertEqual(filtered.known_fact_ids, frozenset())
        state["facet_buckets"]["guardian"]["beliefs"]["f_public"] = {"belief": 0.8}
        filtered = facets.scope_filter(state, scope)
        self.assertEqual(filtered.known_fact_ids, frozenset({"f_public"}))

    def test_prompt_block_uses_behaviour_dimensions_not_labels(self):
        state = _state()
        state["facets"] = _reg()
        facets.switch(state, "guardian", 4)
        lines = prompt_engine.block_facet(state, {})
        text = "\n".join(lines)
        self.assertIn("hostile_attribution", text)
        self.assertIn("记忆断层", text)
        for label in ("BPD", "DID", "多重人格"):
            self.assertNotIn(label, text)

    def test_registry_persists_in_l2_and_cli_can_switch(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            conn = db.connect(Path(tmp.name) / "t.db")
            reg = _reg()
            facets.save_registry(conn, "s", "char_c1", reg)
            loaded = facets.load_registry(conn, "s", "char_c1")
            self.assertEqual(loaded, reg)
            state = {"facets": loaded, "beliefs": {}, "memory": [], "lie_ledger": []}
            facets.switch(state, "guardian", 1)
            self.assertEqual(state["facets"]["active_facet_id"], "guardian")
            conn.close()
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
