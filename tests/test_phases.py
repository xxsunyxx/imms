"""Tests for the phased session skeleton and investigation visibility."""

import tempfile
import unittest
from pathlib import Path

from imms import db, investigation, journal, phases, runtime
from _fixture import make_session_db


class FakeProvider:
    name = "http"

    def __init__(self):
        self.prompts = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        if "你是 IMMS 的 GM 主持人" in prompt:
            return '{"narration":"会议室里灯光还亮着，桌上的记录摊了一角。","tone":"克制"}'
        return '{"utterance":"我先把话说清楚，时间线大家可以对。","emotion_shown":"平静"}'


class TestPhases(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.conn, self.session = make_session_db(Path(self.td.name) / "db")
        self.sid = self.session["session_id"]

    def tearDown(self):
        self.conn.close()
        self.td.cleanup()

    def test_phase_prompt_has_dm_conduct_and_case_facts(self):
        prompt = phases.build_phase_prompt(self.conn, self.sid, "intro")
        self.assertIn("# DM 职责与禁忌", prompt)
        self.assertIn("# 当前阶段：开场与规则说明", prompt)
        self.assertIn("案情背景", prompt)

    def test_phased_session_orders_phases_and_emits_phase_narrations(self):
        provider = FakeProvider()
        out = runtime.run_phased_session(
            self.conn, self.sid, rounds=2, scripted={
                "utterances": ["我先说，门禁记录摆着。", "我也补一句。"],
                "vote": "ai:lin_wanqiu",
            }, seed=7, speaker=provider, gm_agent=provider,
        )
        names = [p["phase"] for p in out["phases"]]
        self.assertIn("intro", names)
        self.assertIn("self_intro", names)
        self.assertIn("investigate", names)
        self.assertIn("discuss", names)
        self.assertIn("vote", names)
        self.assertIn("reveal", names)
        self.assertLess(names.index("intro"), names.index("investigate"))
        self.assertLess(names.index("investigate"), names.index("discuss"))
        acts = [e.get("act") for e in out["log"]]
        self.assertIn("phase:intro", acts)
        self.assertIn("self_intro", acts)
        self.assertIn("clue_found", acts)
        phase_narr = [
            e for e in journal.audit_events(self.conn, self.sid, act_type="gm_phase_narration")
        ]
        self.assertEqual(len(phase_narr), 3)  # intro / self-intro / investigate

    def test_self_intro_does_not_consume_user_utterances(self):
        provider = FakeProvider()
        out = runtime.run_phased_session(
            self.conn, self.sid, rounds=2,
            scripted={"utterances": ["甲", "乙"], "vote": "ai:lin_wanqiu"},
            seed=7, speaker=provider, gm_agent=provider,
        )
        user_lines = [
            e for e in journal.audit_events(self.conn, self.sid)
            if e["actor"].startswith("human:") and e["act_type"] == "assert"
        ]
        self.assertEqual(len(user_lines), 2)


class TestSeatPromptHygiene(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.conn, self.session = make_session_db(Path(self.td.name) / "db")
        self.sid = self.session["session_id"]

    def tearDown(self):
        self.conn.close()
        self.td.cleanup()

    def test_revealed_clue_weight_is_not_spoken_to_seats(self):
        from imms import db as _db
        # Store the same display string runtime uses, weight parameter included.
        _db.set_game_state(self.conn, self.sid, "revealed_clues",
                           ["c_report 初步检验结果|logLR:zhou_muchen=1.5"])
        provider = FakeProvider()
        runtime.run_session(
            self.conn, self.sid, 1,
            {"utterances": ["我先说。"], "vote": "ai:lin_wanqiu"},
            7, "http", speaker=provider, gm_agent=provider,
        )
        seat_prompts = [p for p in provider.prompts if "你是谁（L1 真实" in p]
        self.assertTrue(seat_prompts)
        for prompt in seat_prompts:
            self.assertNotIn("logLR", prompt)
            self.assertNotIn("=1.5", prompt)


class TestInvestigation(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.conn, self.session = make_session_db(Path(self.td.name) / "db")
        self.sid = self.session["session_id"]
        self.onto = db.get_script(self.conn, self.session["script_id"])
        self.seats = [p["id"] for p in self.session["participants"] if p["kind"] != "gm"]
        runtime.init_runtime(self.conn, self.session)
        investigation.init_investigation(self.conn, self.sid, self.onto, self.seats, points=2)

    def tearDown(self):
        self.conn.close()
        self.td.cleanup()

    def test_hidden_clue_is_only_visible_to_finder(self):
        seat = self.seats[0]
        target = investigation.available_targets(self.onto, seat)[0]
        found = investigation.search(self.conn, self.sid, seat, target, self.onto,
                                     make_public=False, seed=3)
        self.assertEqual(found["visibility"], "hidden")
        revealed = db.get_game_state(self.conn, self.sid, "revealed_clues", []) or []
        self.assertFalse(any(line.startswith(found["card_id"]) for line in revealed))
        events = journal.audit_events(self.conn, self.sid, act_type="clue_found")
        self.assertEqual(events[0]["visibility"], [seat])
        for other in [s for s in self.seats if s != seat]:
            visible = db.visible_game_events(self.conn, self.sid, other)
            self.assertFalse(any(e["act_type"] == "clue_found" for e in visible))

    def test_public_clue_unlocks_revealed_clues(self):
        seat = self.seats[0]
        target = investigation.available_targets(self.onto, seat)[0]
        found = investigation.search(self.conn, self.sid, seat, target, self.onto,
                                     make_public=True, seed=3)
        revealed = db.get_game_state(self.conn, self.sid, "revealed_clues", []) or []
        self.assertTrue(any(line.startswith(found["card_id"]) for line in revealed))
        self.assertEqual(db.get_game_state(self.conn, self.sid, "investigation")[
            "action_points"][seat], 1)

    def test_action_points_are_consumed(self):
        seat = self.seats[0]
        targets = investigation.available_targets(self.onto, seat)
        investigation.search(self.conn, self.sid, seat, targets[0], self.onto, seed=1)
        investigation.search(self.conn, self.sid, seat, targets[1], self.onto, seed=1)
        self.assertEqual(db.get_game_state(self.conn, self.sid, "investigation")[
            "action_points"][seat], 0)
        with self.assertRaises(ValueError):
            investigation.search(self.conn, self.sid, seat, targets[0], self.onto, seed=1)

    def test_private_chat_is_room_only(self):
        a, b = self.seats[0], self.seats[1]
        outsider = self.seats[2]
        room = investigation.open_private_chat(self.conn, self.session, 1, [a, b])
        investigation.private_chat(self.conn, self.session, room["room_id"], a, b, "私下说的话")
        insider = db.visible_game_events(self.conn, self.sid, b)
        outside = db.visible_game_events(self.conn, self.sid, outsider)
        self.assertTrue(any(e["act_type"] == "private_say" for e in insider))
        self.assertFalse(any(e["act_type"] == "private_say" for e in outside))


if __name__ == "__main__":
    unittest.main()
