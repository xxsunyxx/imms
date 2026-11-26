"""GM decision core: policy, branch tree, merge/rollback and narrative diff.

The old ``branch`` module gives a one-shot fork with four formal checks.  This
module keeps that machinery but adds the missing GM layer:

* a policy that chooses which clue to release and whether to open private
  rooms, based on heat and information gain;
* a persistent parent/child branch tree with an explicit canonical branch;
* merge / rollback that are recorded as events instead of rewriting history;
* a narrative diff over clues, utterances, suspicion and outcome.

Branch sessions themselves stay in memory (event sourcing): a branch is a
decision specification plus a replay, not a second copy of the world.
"""

import json
from datetime import datetime, timezone

from . import branch, db, formulas, journal, memory, runtime, solver

WEIGHT_RE = solver.WEIGHT_RE


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _revealed_facts(onto, clues):
    fmap = onto.get("clue_fact_map") or {}
    out = set()
    for clue in clues:
        ids = fmap.get(clue) or []
        if isinstance(ids, str):
            ids = [ids]
        out.update(ids)
    return out


def _clue_gain(onto, clue, required):
    weights = {m.group(1): float(m.group(2)) for m in WEIGHT_RE.finditer(str(clue))}
    fmap = onto.get("clue_fact_map") or {}
    ids = fmap.get(clue) or []
    if isinstance(ids, str):
        ids = [ids]
    coverage = len(set(ids) & required) / len(required) if required else 0.0
    return round(sum(weights.values()) + coverage * 2.0, 4), weights


def suggest_decision(conn, session_id, round_no, onto, runtime_state,
                     policy="adaptive", overrides=None):
    """Pure GM decision.  Signature is compatible with runtime's gm_policy hook."""
    overrides = overrides or {}
    revealed = list(db.get_game_state(conn, session_id, "revealed_clues", []))
    pool = [c for c in onto.get("clue_pool", []) if c not in revealed]
    required = set((onto.get("evaluation") or {}).get("required_fact_ids") or [])
    known = _revealed_facts(onto, revealed)
    gains = {c: _clue_gain(onto, c, required) for c in pool}

    events = db.visible_game_events(conn, session_id, "gm")
    n_accuse = sum(1 for e in events if e["round"] == round_no and e["act_type"] == "accuse")
    n_contra = sum(1 for e in events if e["round"] == round_no and e["act_type"] == "contradiction")
    mean_arousal = sum(s["emotion"]["arousal"] for s in runtime_state.values()) / max(1, len(runtime_state))
    heat = formulas.actual_heat(n_accuse, n_contra, mean_arousal)
    total_rounds = int(
        db.get_game_state(conn, session_id, "total_rounds", 0)
        or db.get_game_state(conn, session_id, "round", 0)
        or round_no
    )
    target = formulas.heat_target(round_no, max(1, total_rounds), 3.0)

    rationale = [f"policy={policy}，热度 {heat:.3f} / 目标 {target:.3f}"]
    chosen = None
    if policy == "information":
        if pool:
            chosen = max(pool, key=lambda c: gains[c][0])
            rationale.append(f"信息增益优先：{gains[chosen][0]}")
    elif heat < target - 0.3:
        if pool:
            ranked = sorted(pool, key=lambda c: -gains[c][0])
            chosen = ranked[0] if policy == "adaptive" else pool[0]
            rationale.append(
                f"热度不足，按{('信息增益' if policy == 'adaptive' else '顺序')}释放线索"
            )
    else:
        rationale.append("热度达标，暂不释放线索")

    advance_stage = bool(required and known >= required)
    if advance_stage:
        rationale.append("关键事实已齐，可推进下一幕")
    private_allowed = bool(revealed) and heat <= target + 0.3
    rationale.append("允许私聊" if private_allowed else "暂不开放私聊")

    decision = {
        "policy": policy,
        "round": round_no,
        "release_clue": chosen,
        "clue_gain": gains.get(chosen, (0, {}))[0] if chosen else 0,
        "clue_weights": gains.get(chosen, (0, {}))[1] if chosen else {},
        "heat": round(heat, 4),
        "heat_target": round(target, 4),
        "advance_stage": advance_stage,
        "private_allowed": private_allowed,
        "rationale": rationale,
    }
    decision.update(overrides)
    return decision


def make_policy(policy="adaptive", overrides=None):
    """Build a callable for runtime.run_session(gm_policy=...)."""
    def _policy(conn, session_id, round_no, onto, runtime_state):
        return suggest_decision(
            conn, session_id, round_no, onto, runtime_state, policy, overrides
        )
    return _policy


def _branch_row(conn, branch_id):
    row = conn.execute("SELECT * FROM gm_branches WHERE branch_id=?", (branch_id,)).fetchone()
    if row is None:
        raise ValueError("ERR_BRANCH_NOT_FOUND")
    return _row_dict(row)


def _row_dict(row):
    data = dict(row)
    for key in ("decision_json", "report_json"):
        if data.get(key):
            data[key] = json.loads(data[key])
    return data


def canonical(conn, session_id):
    row = conn.execute(
        "SELECT * FROM gm_branches WHERE session_id=? AND role='canonical' AND status!='rolled_back'",
        (session_id,),
    ).fetchone()
    return _row_dict(row) if row else None


def branch_tree(conn, session_id):
    rows = conn.execute(
        "SELECT * FROM gm_branches WHERE session_id=? ORDER BY created_at, branch_id", (session_id,)
    ).fetchall()
    nodes = [_row_dict(r) for r in rows]
    return {
        "session_id": session_id,
        "canonical": next((n["branch_id"] for n in nodes if n["role"] == "canonical"), None),
        "branches": nodes,
    }


def narrative_diff(base_conn, fork_conn, base_id, fork_id):
    """Compare two branch sessions without mutating either."""
    def events(conn, sid):
        return [
            {
                "round": row["round"],
                "actor": row["actor"],
                "act_type": row["act_type"],
                "payload_json": row["payload_json"],
                "payload": row["payload"],
            }
            for row in journal.audit_events(conn, sid)
        ]

    base_events, fork_events = events(base_conn, base_id), events(fork_conn, fork_id)

    def clue_order(rows):
        return [
            json.loads(r["payload_json"]).get("clue")
            for r in rows if r["act_type"] == "clue_release"
        ]

    def utterances(rows):
        return {
            (r["round"], r["actor"]): json.loads(r["payload_json"]).get("utterance", "")
            for r in rows if json.loads(r["payload_json"]).get("utterance")
        }

    b_clues, f_clues = clue_order(base_events), clue_order(fork_events)
    b_lines, f_lines = utterances(base_events), utterances(fork_events)
    clue_changes = [
        {"kind": "added" if clue not in b_clues else "reordered", "clue": clue}
        for clue in f_clues if clue not in b_clues or f_clues.index(clue) != b_clues.index(clue)
    ] + [
        {"kind": "removed", "clue": clue}
        for clue in b_clues if clue not in f_clues
    ]
    utterance_changes = [
        {"round": key[0], "actor": key[1], "base": b_lines.get(key, ""), "fork": f_lines.get(key, "")}
        for key in sorted(set(b_lines) | set(f_lines))
        if b_lines.get(key) != f_lines.get(key)
    ]

    suspicion_changes = []
    memory_changes = {}
    rounds = {
        r[0] for r in base_conn.execute(
            "SELECT DISTINCT round FROM state_snapshots WHERE session_id=?", (base_id,)
        )
    } | {
        r[0] for r in fork_conn.execute(
            "SELECT DISTINCT round FROM state_snapshots WHERE session_id=?", (fork_id,)
        )
    }
    for round_no in sorted(rounds):
        b_snap, f_snap = db.load_snapshot(base_conn, base_id, round_no), db.load_snapshot(fork_conn, fork_id, round_no)
        for seat in sorted(set(b_snap) | set(f_snap)):
            b_state, f_state = b_snap.get(seat, {}), f_snap.get(seat, {})
            if not isinstance(b_state, dict) or not isinstance(f_state, dict):
                continue
            b_susp = b_state.get("suspicion") or {}
            f_susp = f_state.get("suspicion") or {}
            if not isinstance(b_susp, dict) or not isinstance(f_susp, dict):
                continue
            b_top = max(b_susp, key=b_susp.get, default=None)
            f_top = max(f_susp, key=f_susp.get, default=None)
            if b_top != f_top:
                suspicion_changes.append({
                    "round": round_no, "seat": seat, "base_top": b_top, "fork_top": f_top,
                })
            for change in memory.diff(b_state, f_state, round_no):
                if change.get("kind") == "recall_top":
                    key = (seat, "recall_top", tuple(change.get("base") or []))
                else:
                    key = (seat, change.get("kind"), change.get("seq") or change.get("fact"))
                if key not in memory_changes:
                    memory_changes[key] = {"seat": seat, "round": round_no, **change}

    b_result = db.get_game_state(base_conn, base_id, "result", {})
    f_result = db.get_game_state(fork_conn, fork_id, "result", {})
    outcome = {
        "base": {"winner": b_result.get("winner"), "solved": b_result.get("solved")},
        "fork": {"winner": f_result.get("winner"), "solved": f_result.get("solved")},
        "changed": b_result.get("solved") != f_result.get("solved") or b_result.get("winner") != f_result.get("winner"),
    }
    ordered_memory = [memory_changes[k] for k in sorted(memory_changes, key=str)]
    return {
        "clue_changes": clue_changes,
        "utterance_changes": utterance_changes,
        "suspicion_changes": suspicion_changes,
        "memory_changes": ordered_memory,
        "outcome": outcome,
        "summary": {
            "clue_changes": len(clue_changes),
            "utterance_changes": len(utterance_changes),
            "suspicion_changes": len(suspicion_changes),
            "memory_changes": len(ordered_memory),
            "outcome_changed": outcome["changed"],
        },
    }


def create_branch(conn, session_id, at_round, label="", override_round=None,
                  override_utterance=None, decision=None, seed=42, llm_name="template"):
    """Fork, replay with a GM policy, regress, and record it in the branch tree."""
    decision = decision or {}
    parent = canonical(conn, session_id)
    parent_id = parent["branch_id"] if parent else "root"
    policy = make_policy(decision.get("policy", "adaptive"), decision.get("overrides"))
    result, mem, fork_id = branch.fork(
        conn, session_id, at_round, override_round, override_utterance,
        seed, llm_name, gm_policy=policy,
    )
    try:
        base_result = db.get_game_state(conn, session_id, "result", {})
        report = branch.regress(base_result, result, mem, fork_id, at_round)
        report["session_id"] = session_id
        diff = narrative_diff(conn, mem, session_id, fork_id)
    finally:
        mem.close()

    count = conn.execute(
        "SELECT COUNT(*) AS n FROM gm_branches WHERE session_id=?", (session_id,)
    ).fetchone()["n"]
    branch_id = f"{session_id}-gm{count + 1:03d}"
    conn.execute(
        """INSERT INTO gm_branches(branch_id, session_id, parent_id, fork_round, role, status,
                                   label, decision_json, report_json, created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            branch_id, session_id, parent_id, at_round, "branch", "active", label,
            json.dumps(decision, ensure_ascii=False),
            json.dumps({"regress": report, "diff": diff}, ensure_ascii=False),
            _now(),
        ),
    )
    conn.commit()
    return _branch_row(conn, branch_id)


def _set_canonical(conn, branch_id, role, status, event_type):
    row = _branch_row(conn, branch_id)
    conn.execute(
        "UPDATE gm_branches SET role='branch' WHERE session_id=? AND role='canonical'",
        (row["session_id"],),
    )
    conn.execute(
        "UPDATE gm_branches SET role=?, status=? WHERE branch_id=?", (role, status, branch_id)
    )
    db.append_game_event(
        conn, row["session_id"], row["fork_round"], "gm:host", event_type,
        {"branch_id": branch_id, "role": role, "status": status}, ["gm:host"],
    )
    conn.commit()
    return _branch_row(conn, branch_id)


def activate(conn, branch_id):
    return _set_canonical(conn, branch_id, "canonical", "active", "gm_activate")


def merge(conn, branch_id, rationale=""):
    row = _branch_row(conn, branch_id)
    payload = row["report_json"].get("diff") or {}
    activated = _set_canonical(conn, branch_id, "canonical", "merged", "gm_merge")
    activated["merge_rationale"] = rationale
    activated["diff_summary"] = payload.get("summary", {})
    return activated


def rollback(conn, branch_id):
    row = _branch_row(conn, branch_id)
    parent_id = row["parent_id"]
    if parent_id != "root":
        activate(conn, parent_id)
    else:
        conn.execute(
            "UPDATE gm_branches SET role='branch' WHERE session_id=? AND role='canonical'",
            (row["session_id"],),
        )
        conn.commit()
    conn.execute("UPDATE gm_branches SET status='rolled_back' WHERE branch_id=?", (branch_id,))
    db.append_game_event(
        conn, row["session_id"], row["fork_round"], "gm:host", "gm_rollback",
        {"branch_id": branch_id, "parent_id": parent_id}, ["gm:host"],
    )
    conn.commit()
    return _branch_row(conn, branch_id)


def branch_diff(conn, branch_id):
    row = _branch_row(conn, branch_id)
    return row["report_json"].get("diff") or {}
