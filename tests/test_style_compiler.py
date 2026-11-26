"""LLM style compiler: coherent directive, hard validation, fail-closed fallback."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from imms import db, llm, ontology, runtime, speech_style, style_compiler

from test_speech_style import _ctx, _view


class FakeProvider:
    name = "http"

    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        return self.reply


def _parts_and_fp():
    view, ctx = _view(), _ctx()
    parts = speech_style.render(view, ctx)
    fp = speech_style.extract_fingerprint(view, ctx)
    return parts, fp, ctx


def _valid_reply(parts):
    # Round-trip the deterministic blocks: wording is "compiled" but every
    # verbatim anchor and number survives, so validation must pass.
    return json.dumps({
        "anchor": parts["anchor"] + "\n风格：先承认再推进，语气有分寸。",
        "drift": parts["drift"],
        "boundary": parts["boundary"],
    }, ensure_ascii=False)


class TestCompilerContract(unittest.TestCase):
    def test_skips_template_provider(self):
        parts, fp, ctx = _parts_and_fp()
        out = style_compiler.compile_prompt(parts, fp, ctx, provider=None)
        self.assertEqual(out["report"]["status"], "skipped_template")
        self.assertIs(out["parts"], parts)

    def test_valid_compiled_prompt_is_used(self):
        parts, fp, ctx = _parts_and_fp()
        provider = FakeProvider(_valid_reply(parts))
        out = style_compiler.compile_prompt(parts, fp, ctx, provider=provider)
        self.assertEqual(out["report"]["status"], "compiled")
        self.assertTrue(out["parts"]["compiled"])
        self.assertIn("风格：先承认再推进", out["parts"]["prompt"])
        self.assertIn("[ANCHOR]", out["parts"]["prompt"])
        self.assertEqual(out["parts"]["drift_record"], parts["drift_record"])

    def test_invalid_json_falls_back(self):
        parts, fp, ctx = _parts_and_fp()
        for reply in ("", "not json", json.dumps({"anchor": "x"})):
            out = style_compiler.compile_prompt(
                parts, fp, ctx, provider=FakeProvider(reply))
            self.assertEqual(out["report"]["status"], "fallback")
            self.assertIs(out["parts"], parts)

    def test_dropped_verbatim_anchor_rejected(self):
        parts, fp, ctx = _parts_and_fp()
        reply = json.dumps({
            "anchor": parts["anchor"].replace("「我是测试角色，先听大家讲。」", ""),
            "drift": parts["drift"], "boundary": parts["boundary"],
        }, ensure_ascii=False)
        out = style_compiler.compile_prompt(parts, fp, ctx, provider=FakeProvider(reply))
        self.assertEqual(out["report"]["status"], "fallback")
        self.assertIn("DROPPED_ANCHOR", out["report"]["reason"])

    def test_changed_number_rejected(self):
        parts, fp, ctx = _parts_and_fp()
        reply = json.dumps({
            "anchor": parts["anchor"], "drift": parts["drift"].replace("0.88", "1.88"),
            "boundary": parts["boundary"],
        }, ensure_ascii=False)
        out = style_compiler.compile_prompt(parts, fp, ctx, provider=FakeProvider(reply))
        self.assertEqual(out["report"]["status"], "fallback")
        self.assertIn("CHANGED_NUMBER", out["report"]["reason"])

    def test_invented_quote_rejected(self):
        parts, fp, ctx = _parts_and_fp()
        reply = json.dumps({
            "anchor": parts["anchor"] + "\n口癖：「我从不说谎，证据在我手里」",
            "drift": parts["drift"], "boundary": parts["boundary"],
        }, ensure_ascii=False)
        out = style_compiler.compile_prompt(parts, fp, ctx, provider=FakeProvider(reply))
        self.assertEqual(out["report"]["status"], "fallback")
        self.assertIn("INVENTED_QUOTE", out["report"]["reason"])

    def test_quotes_already_in_drift_block_are_allowed(self):
        # The lexicon legitimately emits wording quotes like 「您」/「你」 in
        # DRIFT. Echoing them must not be flagged as invented dialogue.
        parts, fp, ctx = _parts_and_fp()
        parts = dict(parts, drift=parts["drift"] + "\n敬语措辞：「您」/「你」")
        reply = json.dumps({
            "anchor": parts["anchor"],
            "drift": parts["drift"],
            "boundary": parts["boundary"],
        }, ensure_ascii=False)
        out = style_compiler.compile_prompt(parts, fp, ctx, provider=FakeProvider(reply))
        self.assertEqual(out["report"]["status"], "compiled")

    def test_unknown_fact_id_rejected(self):
        parts, fp, ctx = _parts_and_fp()
        reply = json.dumps({
            "anchor": parts["anchor"],
            "drift": parts["drift"] + "\n证据：f_secret_truth",
            "boundary": parts["boundary"],
        }, ensure_ascii=False)
        out = style_compiler.compile_prompt(parts, fp, ctx, provider=FakeProvider(reply))
        self.assertEqual(out["report"]["status"], "fallback")
        self.assertIn("UNKNOWN_FACT", out["report"]["reason"])

    def test_llm_error_falls_back(self):
        parts, fp, ctx = _parts_and_fp()
        provider = FakeProvider("{}")
        with mock.patch.object(provider, "generate", side_effect=llm.LLMError("ERR_LLM_REQUEST")):
            out = style_compiler.compile_prompt(parts, fp, ctx, provider=provider)
        self.assertEqual(out["report"]["status"], "fallback")
        self.assertIs(out["parts"], parts)

    def test_cache_hit_skips_second_llm_call(self):
        parts, fp, ctx = _parts_and_fp()
        cache = {}
        provider = FakeProvider(_valid_reply(parts))
        first = style_compiler.compile_prompt(parts, fp, ctx, provider, cache)
        calls = len(provider.prompts)
        second = style_compiler.compile_prompt(parts, fp, ctx, provider, cache)
        self.assertEqual(first["report"]["status"], "compiled")
        self.assertEqual(second["report"]["status"], "cache_hit")
        self.assertEqual(len(provider.prompts), calls)
        self.assertEqual(second["parts"]["prompt"], first["parts"]["prompt"])

    def test_db_backed_cache(self):
        parts, fp, ctx = _parts_and_fp()
        with tempfile.TemporaryDirectory() as td:
            conn = db.connect(str(Path(td) / "c.db"))
            try:
                provider = FakeProvider(_valid_reply(parts))
                first = style_compiler.compile_prompt(
                    parts, fp, ctx, provider, db.StyleCompileCache(conn))
                second = style_compiler.compile_prompt(
                    parts, fp, ctx, provider, db.StyleCompileCache(conn))
                self.assertEqual(first["report"]["status"], "compiled")
                self.assertEqual(second["report"]["status"], "cache_hit")
                self.assertEqual(len(provider.prompts), 1)
            finally:
                conn.close()

    def test_hash_changes_with_anchor(self):
        parts, fp, ctx = _parts_and_fp()
        other = dict(parts, anchor=parts["anchor"] + "变")
        self.assertNotEqual(
            style_compiler.compiled_hash(parts, fp),
            style_compiler.compiled_hash(other, fp),
        )


class TestRuntimeWiring(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        script = Path(__file__).resolve().parents[1] / "examples" / "scripts" / "snow-night-villa.yaml"
        self.onto = ontology.parse_script_dir(script)
        self.conn = db.connect(str(self.root / "r.db"))
        db.save_script(self.conn, self.onto)
        from imms import assemble, normalize
        normalize.normalize_persona(self.onto, Path(__file__).resolve().parents[1] / "config")
        # store the normalized persona doc explicitly
        personae, _ = normalize.normalize_persona(
            self.onto, Path(__file__).resolve().parents[1] / "config")
        db.save_persona_l1(self.conn, self.onto["script_id"], personae)
        chars = [c["id"] for c in self.onto["characters"] if not c.get("is_victim")]
        self.session = assemble.assemble(self.conn, self.onto["script_id"], chars[0])
        self.scripted = {"utterances": ["我没什么要补充的。"] * 20, "vote": chars[1]}

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _http_reply(self):
        return json.dumps({
            "utterance": "听我说完，证据就在那里。",
            "emotion_shown": "平静", "claimed_facts": [],
        }, ensure_ascii=False)

    def test_compile_runs_on_llm_path_and_is_ledgered(self):
        # The reply is a speech JSON, not a compiled style JSON, so validation
        # must fall back; the run still completes and records the outcome.
        with mock.patch("imms.llm.build_provider", return_value=FakeProvider(self._http_reply())):
            out = runtime.run_session(
                self.conn, self.session["session_id"], 1, self.scripted, 42, "http")
        rows = db.get_style_compile_ledger(self.conn, self.session["session_id"])
        self.assertGreater(len(rows), 0)
        self.assertTrue(all(r["status"] in {"fallback", "compiled", "cache_hit"} for r in rows))
        events = [
            e for e in db.visible_game_events(self.conn, self.session["session_id"], "gm:host")
            if e["act_type"] == "style_compile"
        ]
        self.assertGreater(len(events), 0)
        self.assertTrue(any(e.get("act") == "style_compile" for e in out["log"]))

    def test_template_path_has_no_compile_rows(self):
        runtime.run_session(
            self.conn, self.session["session_id"], 1, self.scripted, 42, "template")
        rows = db.get_style_compile_ledger(self.conn, self.session["session_id"])
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
