"""GM branch regression: restore an exact round boundary, replay a changed decision,
and regress-check the resulting timeline.

The original session is never mutated: forks run inside an in-memory SQLite copy.
"""

import json

from . import db, formulas, runtime

GLOBAL = "@global"
COPY_TABLES = (
    "scripts", "persona_l1", "attribute_events", "private_rooms",
)


def _clone(conn_src, conn_dst, table, where="", params=(), transform=None):
    cur = conn_src.execute(f"SELECT * FROM {table} {where}", params)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    if not rows:
        return
    if transform:
        rows = [transform(dict(zip(cols, r))) for r in rows]
    sql = f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
    conn_dst.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
    conn_dst.commit()


def _rekey(session_id, fork_id):
    def fn(row):
        row["session_id"] = fork_id
        return row
    return fn


def _rebuild_user_script(conn, session_id, human_seat, total_rounds, override_round, override_utterance, base_vote):
    """Reconstruct the human seat's main-line utterances from the event log."""
    rows = conn.execute(
        """SELECT round, act_type, payload_json FROM game_events
           WHERE session_id=? AND actor=? AND act_type!='private_say' ORDER BY seq""",
        (session_id, human_seat),
    ).fetchall()
    by_round = {}
    for row in rows:
        payload = json.loads(row["payload_json"])
        by_round[row["round"]] = {
            "act_type": row["act_type"],
            "utterance": payload.get("utterance", ""),
            "claimed_facts": payload.get("claimed_facts", []),
            "target": payload.get("target"),
        }
    for round_no in range(1, total_rounds + 1):
        by_round.setdefault(round_no, "（沉默）")
    if override_round is not None and override_utterance is not None:
        item = by_round.get(override_round)
        if isinstance(item, dict):
            item["utterance"] = override_utterance
        else:
            by_round[override_round] = override_utterance
    return {"utterances": [by_round[r] for r in range(1, total_rounds + 1)], "vote": base_vote}


def fork(conn, session_id, at_round, override_round=None, override_utterance=None,
         seed=42, llm_name="template", gm_policy=None):
    """Fork at the START of `at_round`; returns (run_result, in_memory_connection)."""
    session_doc = db.get_session(conn, session_id)
    base_result = db.get_game_state(conn, session_id, "result", {})
    total_rounds = db.get_game_state(conn, session_id, "round", 0)
    if total_rounds < 1:
        raise ValueError("ERR_NO_COMPLETED_ROUNDS")
    if not 1 <= at_round <= total_rounds:
        raise ValueError("ERR_FORK_ROUND_OUT_OF_RANGE")

    count = conn.execute(
        "SELECT COUNT(*) AS n FROM state_snapshots WHERE session_id=? AND agent_id=?",
        (session_id, GLOBAL),
    ).fetchone()["n"]
    fork_id = f"{session_id}-fork{count + 1:03d}"
    mem = db.connect(":memory:")

    for table in COPY_TABLES:
        _clone(conn, mem, table)
    _clone(conn, mem, "sessions", "WHERE session_id=?", (session_id,), _rekey(session_id, fork_id))
    _clone(conn, mem, "game_state", "WHERE session_id=?", (session_id,), _rekey(session_id, fork_id))
    _clone(conn, mem, "game_events", "WHERE session_id=? AND round<?", (session_id, at_round), _rekey(session_id, fork_id))
    _clone(conn, mem, "state_snapshots", "WHERE session_id=? AND round<?", (session_id, at_round), _rekey(session_id, fork_id))

    if at_round > 1:
        snap = db.load_snapshot(conn, session_id, at_round - 1)
        if GLOBAL not in snap:
            mem.close()
            raise ValueError("ERR_SNAPSHOT_NOT_FOUND")
        global_state = snap.pop(GLOBAL)
        db.set_game_state(mem, fork_id, "runtime", snap)
        db.set_game_state(mem, fork_id, "revealed_clues", global_state.get("revealed_clues", []))
        db.set_game_state(mem, fork_id, "user_input_index", global_state.get("user_input_index", at_round - 1))
        db.set_game_state(mem, fork_id, "round", at_round - 1)

    human_seat = next(
        p["id"] for p in session_doc["participants"] if p["kind"] == "human_character"
    )
    scripted = _rebuild_user_script(
        conn, session_id, human_seat, total_rounds, override_round, override_utterance,
        base_result.get("winner"),
    )
    result = runtime.run_session(
        mem, fork_id, rounds=total_rounds, scripted=scripted, seed=seed,
        llm_name=llm_name, start_round=at_round, gm_policy=gm_policy,
    )
    return result, mem, fork_id


def regress(base_result, fork_result, fork_conn, fork_id, at_round):
    """Check that the fork stays inside the same invariants as the base session."""
    checks = {}
    runtime_state = db.get_game_state(fork_conn, fork_id, "runtime", {})

    bounded = True
    for state in runtime_state.values():
        for rel in state.get("relations", {}).values():
            if not isinstance(rel, dict):
                continue
            for dim, val in rel.items():
                lo, hi = runtime.REL_DOMAIN.get(dim, (-1.0, 1.0))
                if not lo <= val <= hi:
                    bounded = False
        for key, val in state.get("emotion", {}).items():
            if key in formulas.PAD_DOMAIN:
                lo, hi = formulas.PAD_DOMAIN[key]
                if not lo <= val <= hi:
                    bounded = False
    checks["bounded"] = bounded

    private_rows = fork_conn.execute(
        "SELECT visibility_json FROM game_events WHERE session_id=? AND act_type='private_say'",
        (fork_id,),
    ).fetchall()
    checks["info_isolation"] = all(
        all(str(token).startswith("room:") for token in json.loads(r["visibility_json"]))
        for r in private_rows
    )

    known = set(db.get_game_state(fork_conn, fork_id, "revealed_clues", []))
    claims_ok = True
    for row in fork_conn.execute(
        "SELECT actor, payload_json FROM game_events WHERE session_id=?", (fork_id,)
    ):
        payload = json.loads(row["payload_json"])
        for fact in payload.get("claimed_facts", []):
            if fact not in known and fact not in runtime_state.get(row["actor"], {}).get("beliefs", {}):
                claims_ok = False
    checks["claims_known"] = claims_ok

    fork_final = fork_result["result"]
    # "Solvable" means the vote mechanism produced legal seats; whether the
    # players actually caught the culprit is an outcome, not an integrity bug.
    checks["solvable"] = (
        fork_final.get("winner") in runtime_state
        and fork_final.get("culprit") in runtime_state
    )

    divergence = {
        "at_round": at_round,
        "base_winner": base_result.get("winner"),
        "fork_winner": fork_final.get("winner"),
        "base_solved": base_result.get("solved"),
        "fork_solved": fork_final.get("solved"),
        "outcome_flipped": base_result.get("solved") != fork_final.get("solved"),
    }
    if all(checks.values()):
        verdict = "changed" if divergence["outcome_flipped"] else "pass"
    else:
        verdict = "fail"
    return {
        "session_id": base_result.get("session_id"),
        "checks": checks,
        "divergence": divergence,
        "verdict": verdict,
        "fork_result": fork_final,
    }


def fork_and_regress(conn, session_id, at_round, override_round=None, override_utterance=None,
                     seed=42, llm_name="template", gm_policy=None):
    base_result = db.get_game_state(conn, session_id, "result", {})
    result, mem, fork_id = fork(
        conn, session_id, at_round, override_round, override_utterance, seed, llm_name, gm_policy
    )
    try:
        report = regress(base_result, result, mem, fork_id, at_round)
    finally:
        mem.close()
    report["session_id"] = session_id
    return report
