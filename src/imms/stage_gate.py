"""Runtime-side plot locks, challenge tokens and private-window gating.

``solver`` audits stage gates offline: a lock such as「第2幕才能提及真相」is
checked against clue stages before a script is admitted.  This module reuses
the same lock grammar at runtime, so a completed gate cannot be bypassed by a
generated line that claims a gated fact too early.

The module is deterministic and never invents facts:

* locks come only from ``plot_locks`` and ``facts`` of type ``plot_lock``;
* gated facts are resolved through the lock's own terms / fact keywords;
* an already public fact is exempt, because the table legitimately knows it;
* resource spends are written as GM-only events and state audits.
"""

import re

from . import db, gm_direction, solver


def _terms(text):
    return [
        token for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,12}", str(text or ""))
        if len(token) >= 2
    ]


def compile_gates(onto):
    """Compile stage gates shared by the runtime and the admission solver."""
    facts = [f for f in (onto or {}).get("facts") or [] if isinstance(f, dict)]
    texts = [str(x) for x in (onto or {}).get("plot_locks") or [] if str(x).strip()]
    texts.extend(
        str(f.get("content") or "")
        for f in facts
        if f.get("type") == "plot_lock" and str(f.get("content") or "").strip()
    )
    gates = []
    seen = set()
    for text in texts:
        match = solver.GATE_RE.search(text) or solver.STAGE_RE.search(text)
        if not match:
            continue
        raw = match.group(2) or match.group(1)
        stage = solver._num(raw)
        if stage is None or stage < 1 or text in seen:
            continue
        seen.add(text)
        terms = _terms(text)
        fact_ids = []
        for fact in facts:
            fid = str(fact.get("id") or "")
            keywords = [str(k) for k in (fact.get("keywords") or []) if len(str(k)) >= 2]
            content = str(fact.get("content") or "")
            if (
                (fid and fid in text)
                or any(keyword in text for keyword in keywords)
                or any(term in content for term in terms)
            ):
                fact_ids.append(fid)
        gates.append({
            "text": text,
            "stage": stage,
            "allowed_stage": stage - 1,
            "terms": terms,
            "fact_ids": [fid for fid in fact_ids if fid],
        })
    return gates


def stage_state(conn, session_id):
    """Return ``{level, last_round}``; legacy integer states are upgraded."""
    state = db.get_game_state(conn, session_id, "stage", None)
    if isinstance(state, dict):
        return {"level": int(state.get("level", 0) or 0),
                "last_round": int(state.get("last_round", 0) or 0)}
    return {"level": int(state or 0), "last_round": 0}


def current_stage(conn, session_id):
    return stage_state(conn, session_id)["level"]


def advance(conn, session_id, round_no):
    """Consume GM ``advance_stage`` once per round; stage numbers are 0-based."""
    state = stage_state(conn, session_id)
    if int(round_no) <= state["last_round"]:
        return {"from": state["level"], "to": state["level"], "already": True}
    before = state["level"]
    state["level"] = before + 1
    state["last_round"] = int(round_no)
    db.set_game_state(
        conn, session_id, "stage", state,
        reason="g06:advance_stage", actor="gm:host",
    )
    report = {"from": before, "to": state["level"], "round": int(round_no), "already": False}
    db.append_game_event(
        conn, session_id, int(round_no), "gm:host", "stage_advance", report, ["gm:host"]
    )
    return report


def set_private_allowed(conn, session_id, round_no, allowed, decision=None):
    """Consume GM ``private_allowed``; later private rooms obey this window."""
    value = bool(allowed)
    before = db.get_game_state(conn, session_id, "private_allowed", True)
    db.set_game_state(
        conn, session_id, "private_allowed", value,
        reason="g06:private_allowed", actor="gm:host",
    )
    report = {"round": int(round_no), "before": bool(before), "after": value}
    if decision:
        report["policy"] = decision.get("policy")
        report["heat"] = decision.get("heat")
    db.append_game_event(
        conn, session_id, int(round_no), "gm:host", "private_window", report, ["gm:host"]
    )
    return report


def private_allowed(conn, session_id):
    """Rooms open by default; an explicit GM decision can close them."""
    value = db.get_game_state(conn, session_id, "private_allowed", True)
    return True if value is None else bool(value)


def public_fact_ids(onto, revealed):
    """Facts the table may mention: public canon plus revealed clue payloads."""
    facts = {str(f.get("id")): f for f in (onto or {}).get("facts") or [] if f.get("id")}
    out = {
        fid for fid, fact in facts.items()
        if fact.get("visibility") == "public"
    }
    fmap = (onto or {}).get("clue_fact_map") or {}
    for value in gm_direction.revealed_ids(revealed):
        if value in facts:
            out.add(value)
        ids = fmap.get(value) or []
        if isinstance(ids, str):
            ids = [ids]
        out.update(str(fid) for fid in ids)
    return out


def guard_output(conn, session_id, onto, act, round_no, public_facts=None):
    """Block a line that reveals a gated fact before its stage opens.

    Returns an audit report and rewrites ``act`` in place on a hit.  The GM-only
    report is the only place gate text / fact ids are recorded; the public line
    keeps the player's perspective intact.
    """
    gates = compile_gates(onto)
    if not gates:
        return None
    stage = current_stage(conn, session_id)
    active = [gate for gate in gates if stage < int(gate["allowed_stage"])]
    if not active:
        return None
    revealed = db.get_game_state(conn, session_id, "revealed_clues", []) or []
    exempt = public_fact_ids(onto, revealed) | set(public_facts or [])
    facts = {str(f.get("id")): f for f in (onto or {}).get("facts") or [] if f.get("id")}
    claims = {str(f) for f in (act.get("claimed_facts") or [])}
    text = str(act.get("utterance") or "")
    hits = []
    for gate in active:
        for fid in gate["fact_ids"]:
            if fid in exempt:
                continue
            fact = facts.get(fid) or {}
            keywords = [str(k) for k in (fact.get("keywords") or []) if len(str(k)) >= 2]
            via = "claim" if fid in claims else (
                "keyword" if any(keyword in text for keyword in keywords) else None
            )
            if via:
                hits.append({
                    "fact_id": fid, "gate_stage": gate["stage"],
                    "current_stage": stage, "via": via,
                })
    if not hits:
        return None
    act["utterance"] = "这件事现在还不能说。"
    act["act_type"] = "deflect"
    act["claimed_facts"] = []
    # A stage block is a hard leak gate, so clear any derived lie that may
    # have been created for the original line (the proposition never aired).
    act["lie_id"] = None
    report = {
        "action": "rewrite", "stage": stage, "round": int(round_no),
        "gates": len(active), "hits": hits,
    }
    db.append_game_event(
        conn, session_id, int(round_no), "gm:host", "stage_gate", report, ["gm:host"]
    )
    return report


def init_resources(conn, session_id, seats, points=None):
    """Initialise stage/lock state and scarce resources for a new session."""
    db.set_game_state(
        conn, session_id, "stage", {"level": 0, "last_round": 0},
        reason="g06:init", actor="gm:host",
    )
    db.set_game_state(
        conn, session_id, "challenge_tokens", {seat: int(points or 2) for seat in seats},
        reason="g06:init", actor="gm:host",
    )
    db.set_game_state(
        conn, session_id, "private_allowed", True,
        reason="g06:init", actor="gm:host",
    )


CHALLENGE_ACTS = {"accuse", "contradiction"}


def consume_challenge(conn, session_id, round_no, seat_id, act):
    """Spend one challenge token; deny and deflect when the budget is empty."""
    act_type = str(act.get("act_type") or "")
    if act_type not in CHALLENGE_ACTS:
        return None
    tokens = dict(db.get_game_state(conn, session_id, "challenge_tokens", {}) or {})
    before = int(tokens.get(seat_id, 2))
    denied = before <= 0
    if not denied:
        tokens[seat_id] = before - 1
        db.set_game_state(
            conn, session_id, "challenge_tokens", tokens,
            reason="g06:challenge_token", actor=seat_id,
        )
    else:
        act["utterance"] = "我先按下不表。"
        act["act_type"] = "deflect"
        act["claimed_facts"] = []
        act["lie_id"] = None
    report = {
        "seat": seat_id, "round": int(round_no), "act_type": act_type,
        "resource": "challenge_token", "before": before,
        "after": max(0, before - 1), "action": "denied" if denied else "spent",
    }
    db.append_game_event(
        conn, session_id, int(round_no), "gm:host", "challenge_token", report, ["gm:host"]
    )
    return report
