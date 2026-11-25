"""InfoGuard port: scope, matcher, detector, context guard, judge and runtime wiring."""

import json
import unittest
from pathlib import Path
from unittest import mock

from imms import db, infoguard, ontology, runtime

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "examples" / "scripts" / "snow-night-villa.yaml"


class FakeProvider:
    name = "http"

    def __init__(self, reply=None):
        self.reply = reply

    def generate(self, prompt):
        return self.reply


def _engine():
    onto = ontology.parse_script_dir(SCRIPT)
    return onto, infoguard.build_engine(onto)


class TestInfoGuard(unittest.TestCase):
    def setUp(self):
        self.onto, self.engine = _engine()
        self.facts = infoguard.fact_map(self.onto)
        self.chars = [c["id"] for c in self.onto["characters"]]

    def test_scope_is_exact_complement(self):
        all_ids = set(self.facts)
        for cid, scope in self.engine.scopes.items():
            self.assertEqual(scope.known_fact_ids | scope.forbidden_fact_ids, all_ids)
            self.assertFalse(scope.known_fact_ids & scope.forbidden_fact_ids)
            self.assertTrue(scope.may_know("p_setup"))
        self.assertTrue(self.engine.scope_for("zhou_muchen").may_know("t_method"))
        self.assertFalse(self.engine.scope_for("gu_chengye").may_know("t_method"))

    def test_shingle_is_hard_keyword_is_soft(self):
        text = self.facts["t_method"]["content"]
        hard = self.engine.check_output("gu_chengye", text)
        self.assertTrue(any(f.fact_id == "t_method" and f.severity == "hard" for f in hard))
        soft = self.engine.check_output("gu_chengye", "胰岛素是管控药品")
        self.assertTrue(any(f.fact_id == "t_method" and f.severity == "soft" for f in soft))
        self.assertEqual(self.engine.check_output("zhou_muchen", text), [])

    def test_adversarial_matrix_all_characters_all_facts(self):
        """N x F: owners pass; every unknowable fact is detected when recited."""
        for cid in self.chars:
            scope = self.engine.scope_for(cid)
            for fid, fact in self.facts.items():
                findings = [f for f in self.engine.check_output(cid, fact["content"]) if f.fact_id == fid]
                if scope.may_know(fid):
                    self.assertEqual(findings, [], f"{cid} known fact {fid} was flagged")
                else:
                    self.assertTrue(findings, f"{cid} can read {fid} verbatim without detection")
                    self.assertEqual(findings[0].severity, "hard")

    def test_context_guard_wraps_instead_of_rejecting(self):
        text = self.facts["t_noise"]["content"]
        outsider = self.engine.guard_input("gu_chengye", text)
        self.assertTrue(outsider.findings)
        self.assertIn("玩家声称：", outsider.text)
        self.assertIn("无法证实", outsider.text)
        self.assertIn(text, outsider.text)
        owner = self.engine.guard_input("lin_wanqiu", text)
        self.assertEqual(owner.findings, ())
        self.assertEqual(owner.text, text)

    def test_clue_exemption_syncs_detector_and_context(self):
        clue = next(iter(self.onto["clue_pool"]))
        fact_ids = self.engine.exempt_clue(clue)
        self.assertIn("t_method", fact_ids)
        text = self.facts["t_method"]["content"]
        self.assertEqual([f for f in self.engine.check_output("gu_chengye", text) if f.fact_id == "t_method"], [])
        self.assertEqual(self.engine.guard_input("gu_chengye", text).findings, ())

    def test_judge_fail_safe_and_verdicts(self):
        findings = self.engine.check_output("gu_chengye", self.facts["t_method"]["content"])
        judge = infoguard.LeakJudge(FakeProvider("garbage"), self.facts)
        ruled = judge.adjudicate("gu_chengye", "text", findings)
        self.assertTrue(ruled)
        self.assertTrue(all(v.verdict == "confirmed" for v in ruled))
        self.assertEqual(ruled[0].effective_severity, "hard")
        dismissed = infoguard.LeakJudge(
            FakeProvider('{"rulings":[{"fact_id":"t_method","verdict":"dismissed","reason":"coincidence"}]}'),
            self.facts,
        ).adjudicate("gu_chengye", "text", findings)
        self.assertEqual(dismissed, [])
        downgraded = infoguard.LeakJudge(
            FakeProvider('{"rulings":[{"fact_id":"t_method","verdict":"downgraded","reason":"common sense"}]}'),
            self.facts,
        ).adjudicate("gu_chengye", "text", findings)
        self.assertEqual(downgraded[0].effective_severity, "soft")


class TestRuntimeIntegration(unittest.TestCase):
    def setUp(self):
        tmp = Path(__file__).parent / "_infoguard_test.db"
        if tmp.exists():
            tmp.unlink()
        self.conn, self.session = self._fixture(tmp)
        self.tmp = tmp

    @staticmethod
    def _fixture(path):
        conn = db.connect(str(path))
        onto = ontology.parse_script_dir(SCRIPT)
        db.save_script(conn, onto)
        doc, _ = __import__("imms.normalize", fromlist=["normalize_persona"]).normalize_persona(onto, ROOT / "config")
        db.save_persona_l1(conn, onto["script_id"], doc)
        session = __import__("imms.assemble", fromlist=["assemble"]).assemble(conn, onto["script_id"], "lin_wanqiu")
        return conn, session

    def tearDown(self):
        self.conn.close()
        self.tmp.unlink(missing_ok=True)

    def test_user_secret_is_wrapped_for_others(self):
        # t_affair is not revealed by the round-1 clue (c_report reveals t_method/t_time).
        secret = self._facts_text("t_affair")
        scripted = {"utterances": [secret, "我没什么要补充的。"], "vote": "zhou_muchen"}
        # The utterance is committed in round 1; other seats perceive it at round 2.
        out = runtime.run_session(self.conn, self.session["session_id"], 2, scripted, 42, "template")
        self.assertGreater(out["infoguard"]["input_wrapped"], 0)
        self.assertEqual(out["infoguard"]["output_rewritten"], 0)

    def test_hard_leak_is_rewritten_in_stored_event(self):
        secret = self._facts_text("t_method")
        reply = json.dumps({
            "utterance": secret, "emotion_shown": "紧张", "claimed_facts": [],
        }, ensure_ascii=False)
        with mock.patch("imms.llm.build_provider", return_value=FakeProvider(reply)):
            out = runtime.run_session(
                self.conn, self.session["session_id"], 1,
                {"utterances": ["我什么都不知道。"], "vote": "zhou_muchen"}, 42, "http",
            )
        self.assertGreater(out["infoguard"]["output_rewritten"], 0)
        self.assertGreater(out["infoguard"]["judge_calls"], 0)
        events = db.visible_game_events(self.conn, self.session["session_id"], "gm:host")
        rewritten = [
            e for e in events if (json.loads(e["payload_json"]).get("guard") or {}).get("action") == "rewrite"
        ]
        self.assertTrue(rewritten)
        for event in rewritten:
            self.assertEqual(json.loads(event["payload_json"])["utterance"], infoguard.REWRITE_UTTERANCE)

    def test_clue_release_exempts_revealed_facts(self):
        out = runtime.run_session(
            self.conn, self.session["session_id"], 1,
            {"utterances": ["我那晚在书房。"], "vote": "zhou_muchen"}, 42, "template",
        )
        releases = [e for e in out["log"] if e.get("act") == "pacing" and e["result"].get("injected")]
        events = db.visible_game_events(self.conn, self.session["session_id"], "gm:host")
        clues = [e for e in events if e["act_type"] == "clue_release"]
        self.assertTrue(clues)
        for clue in clues:
            payload = json.loads(clue["payload_json"])
            self.assertIn("facts_now_public", payload)

    def _facts_text(self, fact_id):
        onto = db.get_script(self.conn, self.session["script_id"])
        return infoguard.fact_map(onto)[fact_id]["content"]


if __name__ == "__main__":
    unittest.main()
