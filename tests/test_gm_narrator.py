"""Tests for the GM narrator: narrative / vote / drift-correction prompts.

Contract under test: the GM borrows character *wording* only, and never
produces facts, numbers, truth fields, invented quotes or vote outcomes.
"""

import tempfile
import unittest
from pathlib import Path

from imms import db, gm_narrator as gn


class FakeProvider:
    name = "http"

    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.prompts = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else ""


def _conn(td):
    root = td.name if hasattr(td, "name") else td
    conn = db.connect(str(Path(root) / "t.db"))
    return conn


def _view(ocean_c=0.9):
    return {
        "tone_baseline": "O: 联想丰富；C: 收敛",
        "syntax": "流水句、短应答",
        "rhythm": "破碎化、句末带刺",
        "catchphrase": "「请允许我为您补全推演」",
        "ocean_anchor": {"OCEAN.C": ocean_c},
    }


class TestVoice(unittest.TestCase):
    def test_voice_has_wording_only(self):
        lines = gn.voice_from_fingerprint(_view())
        self.assertTrue(any("语气底色" in x for x in lines))
        self.assertTrue(any("句式" in x for x in lines))
        joined = "\n".join(lines)
        self.assertNotIn("0.9", joined)
        self.assertNotIn("OCEAN", joined)

    def test_voice_drops_facts_and_numbers(self):
        fp = _view()
        fp["tone_baseline"] = "f_setup 是关键，heat=0.42"
        lines = gn.voice_from_fingerprint(fp)
        self.assertFalse(any("f_setup" in x or "heat" in x or "0.42" in x for x in lines))

    def test_voice_uses_script_persona(self):
        fp = dict(_view())
        fp["personality"] = "谨慎克制，忠于沈家"
        fp["speaking_style"] = '简短恭敬，多用"是""老爷"'
        joined = "\n".join(gn.voice_from_fingerprint(fp))
        self.assertIn("人设底色：谨慎克制", joined)
        self.assertIn("说话习惯：简短恭敬", joined)
        self.assertNotIn('"', joined)

    def test_inject_voice_has_duty_boundary(self):
        prompt = gn.inject_voice("base", _view(), duty="vote")
        self.assertIn("借用的讲述气质", prompt)
        self.assertIn("绝不宣布票数", prompt)
        self.assertIn("只学怎么说", prompt)


class TestParsing(unittest.TestCase):
    def test_valid_narration(self):
        out = gn.parse_narration(
            '{"narration":"灯下众人各自沉默，话头无人接起。","tone":"克制"}')
        self.assertEqual(out["status"], "narrated")

    def test_plain_prose_accepted_when_clean(self):
        out = gn.parse_narration("桌上安静了一瞬，谁都没先开口。")
        self.assertEqual(out["status"], "narrated")

    def test_fact_id_rejected(self):
        out = gn.parse_narration('{"narration":"f_setup 是关键","tone":"x"}')
        self.assertEqual(out["reason"], "ERR_GM_DATA_LEAK")

    def test_number_rejected(self):
        out = gn.parse_narration('{"narration":"两票对三票 2:3","tone":"x"}')
        self.assertEqual(out["reason"], "ERR_GM_DATA_LEAK")

    def test_truth_field_rejected(self):
        out = gn.parse_narration('{"narration":"真凶已定","tone":"x"}')
        self.assertEqual(out["reason"], "ERR_GM_TRUTH_LEAK")

    def test_invented_quote_rejected(self):
        out = gn.parse_narration('{"narration":"他说「我认罪」","tone":"x"}')
        self.assertEqual(out["reason"], "ERR_GM_INVENTED_QUOTE")

    def test_allowed_quote_passes(self):
        out = gn.parse_narration(
            '{"narration":"他说「我认罪」","tone":"x"}',
            allowed_quotes=["我认罪"])
        self.assertEqual(out["status"], "narrated")

    def test_empty_falls_back(self):
        self.assertEqual(gn.parse_narration("")["status"], "fallback")

    def test_ungrounded_narration_falls_back(self):
        empty = '{"narration":"灯芯晃了一下，谁都没有急着开口。","tone":"克制"}'
        out = gn.parse_narration(empty, required_anchors=["高志远", "会议室"])
        self.assertEqual(out["reason"], "ERR_GM_UNGROUNDED")
        grounded = '{"narration":"会议室里，高志远躺在沙发上。","tone":"克制"}'
        self.assertEqual(
            gn.parse_narration(grounded, required_anchors=["高志远"])["status"],
            "narrated")


class TestPrompts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = _conn(self.tmp)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_narration_prompt_has_craft_and_no_truth(self):
        p = gn.build_narration_prompt(self.conn, "s", 1, 5, voice_fp=_view())
        self.assertIn("像真人讲故事的技法", p)
        self.assertIn("借用的讲述气质", p)
        self.assertIn("责任边界", p)
        self.assertNotIn("TruthGraph", p)
        self.assertNotIn("culprit", p)

    def test_vote_prompt_phases(self):
        for phase in ("before", "tie", "reveal"):
            p = gn.build_vote_prompt(self.conn, "s", phase,
                                     {"solved": True, "tie": phase == "tie"})
            self.assertIn("主持", p)
            self.assertIn("本阶段特别指引", p)
            self.assertNotIn("真凶是", p)

    def test_redirect_prompt_has_craft(self):
        p = gn.build_redirect_prompt(
            self.conn, "s", 2, {"cold": {"desc": "冷场", "evidence": "无人被点名"}})
        self.assertIn("拉回指引", p)
        self.assertIn("真人讲述技法", p)

    def test_stage_guides_are_wording_only(self):
        guides = gn.build_stage_guides(self.conn, "s", 1, 5)
        self.assertTrue(guides)
        for g in guides:
            self.assertFalse(gn._NUM_RE.search(g))
            self.assertFalse(gn._FACT_RE.search(g))

    def test_narrate_empty_reply_falls_back(self):
        out = gn.narrate(self.conn, "s", 1, FakeProvider([]))
        self.assertEqual(out["status"], "fallback")  # empty reply -> validation fallback
        self.assertIn("prompt", out)

    def test_narrate_with_good_reply(self):
        reply = '{"narration":"灯下众人沉默了片刻。","tone":"克制"}'
        out = gn.narrate(self.conn, "s", 1, FakeProvider([reply]))
        self.assertEqual(out["status"], "narrated")
        self.assertIn("像真人讲故事的技法", out["prompt"])

    def test_narrate_template_falls_back(self):
        class T:
            name = "template"
        out = gn.narrate(self.conn, "s", 1, T())
        self.assertEqual(out["status"], "fallback")


class TestPublicCast(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = _conn(self.tmp)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_narration_prompt_carries_public_persona_not_private_facts(self):
        from imms import assemble, normalize, ontology
        root = Path(__file__).resolve().parents[1]
        onto = ontology.parse_script_dir(str(root / "examples" / "scripts"
                                             / "snow-night-villa.yaml"))
        db.save_script(self.conn, onto)
        doc, _ = normalize.normalize_persona(onto, root / "config")
        db.save_persona_l1(self.conn, onto["script_id"], doc)
        chars = [c["id"] for c in onto["characters"] if not c.get("is_victim")]
        session = assemble.assemble(self.conn, onto["script_id"], chars[0])
        prompt = gn.build_narration_prompt(self.conn, session["session_id"], 1)
        public_part = prompt.split(gn.gm_direction._GM_HEADER)[0]
        self.assertIn("# 场上人物（公开人设，仅供你把握说话与节奏）", public_part)
        self.assertIn("谨慎克制", public_part)
        self.assertIn("简短恭敬", public_part)
        self.assertNotIn("胰岛素", public_part)
        self.assertNotIn("私生女", public_part)
        # The host alone knows the whole story; it must never enter the
        # public-facing part of the prompt.
        self.assertIn(gn.gm_direction._GM_HEADER, prompt)
        self.assertIn("真凶是", prompt)


class TestDirectionAndNames(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = _conn(self.tmp)
        root = Path(__file__).resolve().parents[1]
        self.script_dir = root / "examples" / "scripts"
        self.config = root / "config"

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _midnight(self):
        from imms import assemble, normalize, ontology
        onto = ontology.parse_script_dir(str(self.script_dir / "midnight-meeting.yaml"))
        db.save_script(self.conn, onto)
        doc, _ = normalize.normalize_persona(onto, self.config)
        db.save_persona_l1(self.conn, onto["script_id"], doc)
        session = assemble.assemble(self.conn, onto["script_id"], "shen_wei")
        return onto, session

    def test_truth_brief_is_gm_only_and_has_no_fact_ids(self):
        from imms import gm_direction
        onto, _ = self._midnight()
        text = "\n".join(gm_direction.truth_brief(onto))
        self.assertIn("真凶是 韩子昂", text)
        self.assertNotIn("t_source", text)
        self.assertNotIn("killer_id", text)

    def test_private_directions_include_goals_conflicts_and_framing(self):
        from imms import gm_direction
        onto, _ = self._midnight()
        dirs = gm_direction.private_directions(onto)
        killer = "\n".join(dirs["han_ziang"])
        self.assertIn("不被发现", killer)
        self.assertIn("搅浑水", killer)
        self.assertIn("挡箭牌", killer)
        zheng = "\n".join(dirs["zheng_kexin"])
        self.assertIn("目标", zheng)
        self.assertIn("暗线", zheng)
        self.assertNotIn("真凶", zheng)

    def test_hidden_terms_skip_public_and_revealed_wording(self):
        from imms import gm_direction
        onto, _ = self._midnight()
        terms = gm_direction.hidden_truth_terms(onto, [])
        self.assertIn("花生油", terms)
        self.assertNotIn("门禁", terms)  # public fact uses the same word
        revealed = gm_direction.hidden_truth_terms(onto, ["c_report"])
        self.assertNotIn("过敏", revealed)  # t_method revealed by c_report

    def test_stored_revealed_string_normalises_to_clue_id(self):
        from imms import gm_direction
        onto, _ = self._midnight()
        stored = ["c_report 急救初步判断记录|logLR:han_ziang=1.5"]
        self.assertEqual(gm_direction.revealed_ids(stored), {"c_report"})
        self.assertNotIn("过敏", gm_direction.hidden_truth_terms(onto, stored))

    def test_forbidden_term_blocks_narration(self):
        out = gn.parse_narration(
            '{"narration":"他动了花生油瓶","tone":"紧绷"}',
            forbidden_terms=["花生油"])
        self.assertEqual(out["reason"], "ERR_GM_TRUTH_LEAK")

    def test_seat_prompt_uses_character_name_not_seat_id(self):
        from imms import speech_style
        view = {"relations": {"ai:zheng_kexin": {"like": 0.8, "trust": 0.7}}}
        parts = speech_style.render(
            view, {"target_name": "郑可欣", "target_id": "ai:zheng_kexin"})
        self.assertIn("对 郑可欣 称呼按真实关系", parts["prompt"])
        self.assertNotIn("ai:", parts["prompt"])

    def test_gm_event_lines_use_names_and_drop_loglr(self):
        onto, session = self._midnight()
        db.append_game_event(
            self.conn, session["session_id"], 1, "gm:host", "clue_release",
            {"clue": "c_report 急救初步判断记录|logLR:han_ziang=1.5"}, ["public"])
        prompt = gn.build_narration_prompt(
            self.conn, session["session_id"], 1,
            seat_names={"gm:host": "主持人", "ai:han_ziang": "韩子昂"})
        self.assertIn("主持人·公开线索：急救初步判断记录", prompt)
        self.assertNotIn("gm:host", prompt.split(gn.gm_direction._GM_HEADER)[0])
        self.assertNotIn("logLR", prompt)

    def test_redirect_requires_human_mislead_and_followers(self):
        onto, session = self._midnight()
        sid = session["session_id"]
        self.assertEqual(gn.detect_drift(self.conn, sid, 1), {})
        misleading = "最了解咖啡机的人，未必是签字最多的人。"
        db.append_game_event(self.conn, sid, 1, "human:shen_wei", "accuse",
                             {"utterance": misleading, "target": "ai:han_ziang",
                              "claimed_facts": []}, ["public"])
        # One leader alone is not a stampede.
        self.assertEqual(gn.detect_drift(self.conn, sid, 1), {})
        for actor in ("ai:zheng_kexin", "ai:meng_xiaomeng"):
            db.append_game_event(self.conn, sid, 1, actor, "accuse",
                                 {"utterance": "我看也是。", "target": "ai:han_ziang"},
                                 ["public"])
        drift = gn.detect_drift(self.conn, sid, 1)
        self.assertIn("human_mislead", drift)
        self.assertEqual(drift["human_mislead"]["target"], "ai:han_ziang")

    def test_narration_prompt_carries_case_facts_and_revealed_truth(self):
        onto, session = self._midnight()
        sid = session["session_id"]
        prompt = gn.build_narration_prompt(self.conn, sid, 1)
        public = prompt.split(gn.gm_direction._GM_HEADER)[0]
        self.assertIn("# 案情事实", public)
        self.assertIn("客观事实：", public)
        self.assertIn("四十八岁", public)  # stored as 48, spoken in Chinese
        self.assertNotIn("花生油", public)  # still hidden
        # Runtime stores a display string, not a bare clue id.
        db.set_game_state(self.conn, sid, "revealed_clues",
                          ["c_report 急救初步判断记录|logLR:han_ziang=1.5"])
        later = gn.build_narration_prompt(self.conn, sid, 2)
        public_later = later.split(gn.gm_direction._GM_HEADER)[0]
        self.assertIn("发绀", public_later)
        self.assertIn("过敏", public_later)  # t_method now on the table

    def test_fallback_narration_still_carries_a_case_fact(self):
        onto, session = self._midnight()
        out = gn.fallback_narration(self.conn, session["session_id"], 1)
        self.assertIn("高志远", out["narration"])
        self.assertNotIn("48", out["narration"])

    def test_drift_is_written_into_the_one_narration(self):
        onto, session = self._midnight()
        sid = session["session_id"]
        db.append_game_event(self.conn, sid, 1, "human:shen_wei", "accuse",
                             {"utterance": "最了解咖啡机的人，未必是签字最多的人。",
                              "target": "ai:han_ziang", "claimed_facts": []}, ["public"])
        for actor in ("ai:zheng_kexin", "ai:meng_xiaomeng"):
            db.append_game_event(self.conn, sid, 1, actor, "accuse",
                                 {"utterance": "我看也是。", "target": "ai:han_ziang"},
                                 ["public"])
        drift = gn.detect_drift(self.conn, sid, 1)
        self.assertIn("human_mislead", drift)
        prompt = gn.build_narration_prompt(self.conn, sid, 1, drift=drift)
        self.assertIn("# 本轮拉回指引（写进叙述里，不单独输出）", prompt)
        self.assertIn("拉回就写在本轮叙述里", prompt)
        reply = ('{"narration":"会议室里高志远躺在沙发上，表格被人重新翻回桌面。",'
                 '"tone":"转场"}')
        out = gn.narrate(self.conn, sid, 1, FakeProvider([reply]), drift=drift)
        self.assertEqual(out["status"], "narrated")
        self.assertEqual(out["drift"], drift)

    def test_gm_prompt_carries_dm_duties_and_taboos(self):
        onto, session = self._midnight()
        sid = session["session_id"]
        prompt = gn.build_narration_prompt(self.conn, sid, 1)
        self.assertIn("# DM 职责与禁忌（你是主持人，不是玩家）", prompt)
        self.assertIn("控节奏、发线索", prompt)
        self.assertIn("不参与玩家对话", prompt)
        self.assertIn("不剧透", prompt)
        vote = gn.build_vote_prompt(self.conn, sid, "reveal", {"solved": True}, {})
        self.assertIn("# DM 职责与禁忌（你是主持人，不是玩家）", vote)

    def test_supported_human_accusation_is_not_drift(self):
        onto, session = self._midnight()
        sid = session["session_id"]
        db.append_game_event(self.conn, sid, 1, "human:shen_wei", "accuse",
                             {"utterance": "门禁和监控都摆着，先说清楚。",
                              "target": "ai:han_ziang", "claimed_facts": []}, ["public"])
        for actor in ("ai:zheng_kexin", "ai:meng_xiaomeng"):
            db.append_game_event(self.conn, sid, 1, actor, "accuse",
                                 {"utterance": "确实该说清楚。", "target": "ai:han_ziang"},
                                 ["public"])
        self.assertEqual(gn.detect_drift(self.conn, sid, 1), {})


if __name__ == "__main__":
    unittest.main()
