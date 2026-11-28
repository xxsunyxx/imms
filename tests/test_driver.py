import json
import tempfile
import unittest
from pathlib import Path

from _fixture import make_session_db
from imms import cli, db, driver, journal, replay


class TestHeadlessDriver(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn, self.session = make_session_db(Path(self.tmp.name) / "t.db")
        self.sid = self.session["session_id"]

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _acts(self, act_type):
        return journal.audit_events(self.conn, self.sid, act_type=act_type)

    def test_step_runs_one_round_and_only_finalizes_last(self):
        d = driver.HeadlessDriver(self.conn, self.sid, rounds=2, seed=5)
        first = d.step("第一轮：我知道的只有公开信息。")
        self.assertEqual(first["status"], "advanced")
        self.assertEqual(first["result"], {})
        self.assertEqual(self._acts("vote_cast"), [])
        self.assertIsNone(db.get_game_state(self.conn, self.sid, "result"))
        self.assertEqual(d.status()["cursor"], 1)

        last = d.step("第二轮：我仍然只说公开事实。", vote_target="zhou_muchen")
        self.assertEqual(last["status"], "finalized")
        self.assertTrue(last["result"]["solved"] in (True, False))
        self.assertIn("ending", last["scoring"])
        self.assertEqual(len(self._acts("vote_cast")), 1)
        self.assertTrue(d.status()["finished"])

        text = replay.build_replay(self.conn, self.sid)
        self.assertIn("结局与个人目标结算", text)

    def test_event_stream_emits_only_new_events_as_json(self):
        stream = driver.EventStream()
        d = driver.HeadlessDriver(self.conn, self.sid, rounds=1, stream=stream)
        before = len(self._acts("assert"))
        d.step("只说一句")
        lines = [json.loads(json.dumps(item)) for item in stream.items]
        self.assertTrue(lines)
        self.assertEqual([line["stream_seq"] for line in lines], list(range(1, len(lines) + 1)))
        self.assertTrue(all("session_id" in line and "visibility" in line for line in lines))
        self.assertGreater(len(self._acts("assert")), before)
        self.assertEqual(stream.drain(after=len(lines) - 1), [lines[-1]])

    def test_resume_from_persisted_cursor(self):
        first = driver.HeadlessDriver(self.conn, self.sid, rounds=3, seed=3)
        first.step("第一句")
        resumed = driver.HeadlessDriver(self.conn, self.sid, seed=3)
        self.assertEqual(resumed.status()["cursor"], 1)
        self.assertEqual(resumed.rounds, 3)
        steps = resumed.run(["第二句", {"utterance": "第三句", "vote": "zhou_muchen"}])
        self.assertEqual([step["status"] for step in steps], ["advanced", "finalized"])
        self.assertEqual(len(self._acts("vote_cast")), 1)
        self.assertTrue(resumed.status()["finished"])

    def test_cli_drive_status_step_and_run(self):
        parser = cli.build_parser()
        args = parser.parse_args([
            "--db", str(Path(self.tmp.name) / "cli.db"),
            "drive", "step", "--session", self.sid, "--rounds", "1",
            "--utterance", "cli 发言", "--out", str(Path(self.tmp.name) / "events.jsonl"),
        ])
        self.assertEqual(args.drive_action, "step")
        self.assertEqual(args.utterance, "cli 发言")

        import contextlib
        import io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            cli.main([
                "--db", str(Path(self.tmp.name) / "t.db"),
                "drive", "status", "--session", self.sid,
            ])
        status = json.loads(buffer.getvalue())
        self.assertEqual(status["cursor"], 0)
        self.assertEqual(status["rounds"], 3)

        events_path = Path(self.tmp.name) / "events.jsonl"
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink):
            cli.main([
                "--db", str(Path(self.tmp.name) / "t.db"),
                "drive", "step", "--session", self.sid, "--rounds", "1",
                "--utterance", "cli 发言", "--out", str(events_path),
            ])
        rows = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(rows)
        self.assertTrue(all("payload" in row for row in rows))
        self.assertEqual(len(self._acts("vote_cast")), 1)

    def test_inputs_loader_accepts_array_and_wrapper(self):
        path = Path(self.tmp.name) / "inputs.json"
        path.write_text(json.dumps(["甲", {"utterance": "乙", "vote": "x"}]), encoding="utf-8")
        self.assertEqual(driver.load_inputs(path)[0], "甲")
        path.write_text(json.dumps({"inputs": ["丙"]}), encoding="utf-8")
        self.assertEqual(driver.load_inputs(path), ["丙"])
        path.write_text(json.dumps({"other": []}), encoding="utf-8")
        self.assertEqual(driver.load_inputs(path), [])


if __name__ == "__main__":
    unittest.main()
