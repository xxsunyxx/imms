"""G22: acute breakout state, token ledger and replay section."""

import copy
import tempfile
import unittest
from pathlib import Path

from _fixture import make_session_db
from imms import breakout, db, ips, journal, replay, runtime


def _seat(name="ai:a", A=1.2, I=0.9, human=False):
    return {
        "seat_id": name, "character_id": name.replace("ai:", "char_"),
        "is_human": human,
        "l1": {"OCEAN.O": 0.6, "OCEAN.C": 0.6, "OCEAN.N": 0.6,
               "values.honesty": 0.5, "values.loyalty": 0.5,
               "mask": {"OCEAN.O": 0.8, "OCEAN.C": 0.8, "OCEAN.N": 0.8}},
        "l0": {"acting_skill": 0.5},
        "emotion": {"arousal": 0.6, "stress": 0.2, "morale": 0.8, "exposure_risk": 0.1},
        "relations": {"ai:b": {"like": 0.4, "trust": 0.5, "respect": 0.6}},
        "beliefs": {"f_known": {"belief": 0.8}},
        "suspicion": {"ai:b": 0.9},
        "memory": [{"text": "上一轮我说了什么"}],
        "accumulator": {"A": A, "rho": 0.9, "theta": 0.5, "theta_base": 0.5,
                        "refractory_until": 0, "applied": []},
    }


class BreakoutTests(unittest.TestCase):
    def test_thresholds_and_state_names(self):
        state = _seat()
        self.assertEqual(breakout.current_state(state), "normal")
        self.assertEqual(breakout.thresholds(state)["s_hi"], 3.2)
        state["breakout"]["state"] = "charged"
        self.assertEqual(breakout.thresholds(state)["f_min"], 0.65)
        state["breakout"]["state"] = "frenzy"
        self.assertEqual(breakout.thresholds(state)["s_hi"], 4.6)

    def test_entry_needs_suppression_and_token(self):
        ledger = breakout.tokens()
        state = _seat()
        before = copy.deepcopy(state)
        blocked = breakout.advance(state, 1, state["accumulator"], 0.9, 0, ledger, ["ai:b"])
        self.assertEqual(breakout.current_state(state), "charged")
        self.assertEqual(blocked["to"], "charged")
        self.assertEqual(ledger["used"], 0)
        self.assertEqual(state["memory"], before["memory"])
        self.assertEqual(state["beliefs"], before["beliefs"])
        self.assertEqual(state["l1"], before["l1"])

        entered = breakout.advance(state, 2, state["accumulator"], 0.9, 1, ledger, ["ai:b"])
        self.assertEqual(entered["to"], "frenzy")
        self.assertEqual(ledger, {"used": 1, "max": 1})
        self.assertEqual(entered["memory_scope"], "full")
        self.assertEqual(state["breakout"]["memory_scope"], "full")
        second = _seat("ai:c")
        breakout.advance(second, 2, second["accumulator"], 0.9, 5, ledger, ["ai:b"])
        blocked2 = breakout.advance(second, 3, second["accumulator"], 0.9, 5, ledger, ["ai:b"])
        self.assertIn("token_unavailable", blocked2["reason"])

    def test_exit_cost_is_applied_once_and_is_irreversible(self):
        ledger = breakout.tokens()
        state = _seat()
        breakout.advance(state, 1, state["accumulator"], 0.9, 1, ledger, ["ai:b"])
        exit_round = state["breakout"]["exit_round"]
        rel_before = state["relations"]["ai:b"]["trust"]
        exited = breakout.advance(state, exit_round, state["accumulator"], 0.9, 1,
                                  ledger, ["ai:b"])
        self.assertEqual(exited["to"], "charged")
        self.assertAlmostEqual(state["emotion"]["exposure_risk"], 0.4)
        self.assertAlmostEqual(state["emotion"]["morale"], 0.6)
        self.assertLess(state["relations"]["ai:b"]["trust"], rel_before)
        self.assertTrue(all(c.get("irreversible") for c in exited["changes"]
                            if c["dim"] in breakout.RELATION_KEYS))
        after = copy.deepcopy(state["relations"]["ai:b"]["trust"])
        breakout.advance(state, exit_round + 1, state["accumulator"], 0.9, 1,
                         ledger, ["ai:b"])
        self.assertEqual(breakout.current_state(state), "charged")
        self.assertEqual(state["relations"]["ai:b"]["trust"], after)
        self.assertTrue(state["breakout"]["after_effect_applied"])

    def test_frenzy_widens_bandpass_without_writing_facts(self):
        states = {"ai:a": _seat(), "ai:b": _seat("ai:b")}
        states["ai:a"]["breakout"] = {**breakout.fresh_state(), "state": "frenzy"}
        states["ai:a"]["l1"]["zeta_f"] = 0.5
        events = [{
            "seq": 1, "round": 1, "actor": "ai:b", "act_type": "accuse",
            "payload": {"target": "ai:a", "impact": {"s_k": 1.0}},
        }]
        result = ips.resolve_bursts(
            states, 1, events, 0.5, 1.0, ips.ImprovBudget(), {"f_known"}, "s", 42
        )
        self.assertTrue(result["bursts"])
        burst = result["bursts"][0]
        self.assertEqual(burst["state"], "frenzy")
        self.assertEqual(burst["state_thresholds"]["s_hi"], 4.6)
        self.assertTrue(burst["speech_act"]["is_unverified_claim"])
        self.assertEqual(burst["speech_act"]["claimed_facts"], ["f_known"])
        self.assertEqual(states["ai:a"]["memory"], [{"text": "上一轮我说了什么"}])

    def test_replay_has_own_breakout_section(self):
        events = [{
            "round": 3, "actor": "ai:a", "act_type": "improv_burst",
            "payload": {"state": "frenzy", "tier": "macro", "epsilon": 0.8,
                        "trigger": {"intensity": 0.91},
                        "aftermath": {"strategy": "escalate"},
                        "state_thresholds": {"s_hi": 4.6}},
        }]
        lines = replay._breakout_section(events, {"ai:a": "甲"})
        text = "\n".join(lines)
        self.assertIn("越界时刻 Top N", text)
        self.assertIn("第3轮", text)
        self.assertIn("记忆作用域为 full", text)

    def test_runtime_emits_breakout_events(self):
        tmp = tempfile.TemporaryDirectory()
        conn, session = make_session_db(Path(tmp.name) / "t.db")
        sid = session["session_id"]
        try:
            runtime.init_runtime(conn, session)
            rt = db.get_game_state(conn, sid, "runtime")
            target = next(s for s in rt if s.startswith("ai:"))
            rt[target]["accumulator"].update({
                "A": 1.5, "rho": 0.99, "theta": 0.5, "theta_base": 0.5,
            })
            rt[target]["emotion"]["arousal"] = 0.9
            db.set_game_state(conn, sid, "runtime", rt)
            db.append_game_event(
                conn, sid, 0, "gm:host", "suppressed_burst", {"seat": target}, ["gm:host"]
            )
            scripted = {
                "utterances": [{"act_type": "accuse", "utterance": "你在说谎。",
                                "target": target}],
                "vote": target,
            }
            runtime.run_session(conn, sid, 1, scripted, 42, "template")
            rows = journal.audit_events(conn, sid)
            entries = [r for r in rows if r["act_type"] == "breakout_state"
                       and r["payload"].get("to") == "frenzy"]
            self.assertTrue(entries)
            self.assertEqual(db.get_game_state(conn, sid, "breakout_tokens")["used"], 1)
            self.assertTrue(any(
                r["act_type"] == "improv_burst" and r["payload"].get("state") == "frenzy"
                for r in rows
            ))
        finally:
            conn.close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
