"""GM event injection library: multi-variable external perturbations (§8.2).

Every candidate is triggered by deterministic variables from the current
session state.  Applying one writes an ``gm_event_injection`` game event plus
L3 audit rows; no LLM decides numbers or truth.
"""

import json
import re

from . import db, formulas, infoguard

LOG_LR_RE = re.compile(r"logLR:\w+=([-\d.]+)")

EVENTS = {
    "second_body": {
        "label": "第二具尸体",
        "effects": ["fear_all+0.3", "stress_all+0.25", "alive_count-1"],
        "trigger": lambda c: c["case_progress"] < 0.4 and c["round_progress"] > 0.5,
        "score": lambda c: round(2.0 - c["case_progress"], 4),
    },
    "blackout": {
        "label": "停电/封锁",
        "effects": ["ap_penalty-2", "arousal_all+0.2"],
        "trigger": lambda c: c["heat"] < c["heat_target"] - 0.3,
        "score": lambda c: round(c["heat_target"] - c["heat"], 4),
    },
    "npcs_letter": {
        "label": "NPC 送来信件",
        "effects": ["release_clue", "belief_update"],
        "trigger": lambda c: bool(c["missing_facts"] and c["unrevealed_clues"]),
        "score": lambda c: round(len(c["missing_facts"]) + 0.5, 4),
    },
    "anonymous_tip": {
        "label": "匿名举报",
        "effects": ["target_stress+0.4", "exposure_risk+0.1"],
        "trigger": lambda c: bool(c["highest_exposure"] and c["highest_exposure"][1] > 0.6),
        "score": lambda c: round(c["highest_exposure"][1] * 2, 4),
    },
    "old_flame": {
        "label": "旧情复燃",
        "effects": ["pair_commitment+0.3"],
        "trigger": lambda c: c["closest_pair"] is not None and c["closest_pair"][2] > 0.7,
        "score": lambda c: round(c["closest_pair"][2], 4),
    },
    "drunken_slip": {
        "label": "酒后失言",
        "effects": ["force_reveal_known_fact"],
        "trigger": lambda c: bool(c["intoxicated"] and c["intoxicated"][1] > 0.6),
        "score": lambda c: round(c["intoxicated"][1] * 2, 4),
    },
    "weather": {
        "label": "天气/时辰",
        "effects": ["clue_decay_multiplier=2", "scheduled"],
        "trigger": lambda c: c["round"] % 3 == 0,
        "score": lambda c: 0.5,
    },
}


def _bump(state, key, delta, lo=0.0, hi=1.0):
    before = float(state.get(key, 0.0))
    after = round(formulas.clip(before + delta, lo, hi), 4)
    state[key] = after
    return round(before, 4), after


def build_context(conn, session_id, round_no=None, runtime=None):
    """Collect the variables used by every trigger.  Pure read except defaults."""
    session = db.get_session(conn, session_id)
    onto = db.get_script(conn, session["script_id"])
    state = runtime if runtime is not None else db.get_game_state(conn, session_id, "runtime", {})
    round_no = int(round_no or db.get_game_state(conn, session_id, "round", 0) or 1)
    total = int(
        db.get_game_state(conn, session_id, "total_rounds", 0)
        or db.get_game_state(conn, session_id, "round", 0)
        or round_no
    )
    revealed = list(db.get_game_state(conn, session_id, "revealed_clues", []))
    pool = list(onto.get("clue_pool", []))

    rows = db.visible_game_events(conn, session_id, "gm")
    n_accuse = sum(1 for e in rows if e["round"] == round_no and e["act_type"] == "accuse")
    n_contra = sum(1 for e in rows if e["round"] == round_no and e["act_type"] == "contradiction")
    arousal = sum(s.get("emotion", {}).get("arousal", 0.0) for s in state.values()) / max(1, len(state))
    heat = formulas.actual_heat(n_accuse, n_contra, arousal)
    target = formulas.heat_target(round_no, max(1, total), 3.0)

    required = set((onto.get("evaluation") or {}).get("required_fact_ids") or [])
    revealed_facts = set()
    clue_map = onto.get("clue_fact_map", {}) or {}
    for clue in revealed:
        ids = clue_map.get(clue, [])
        if isinstance(ids, str):
            ids = [ids]
        revealed_facts.update(ids)
    known_public = {f["id"] for f in onto.get("facts", []) if f.get("visibility") == "public"}
    missing = sorted(required - revealed_facts - known_public)

    exposure = [
        (seat, float(s.get("emotion", {}).get("exposure_risk", 0.0)))
        for seat, s in state.items()
    ]
    highest_exposure = max(exposure, key=lambda x: x[1], default=None)
    intoxicated = max(
        [(seat, float(s.get("emotion", {}).get("intoxication", 0.0))) for seat, s in state.items()],
        key=lambda x: x[1], default=None,
    )
    closest_pair = None
    seats = list(state)
    for i, a in enumerate(seats):
        for b in seats[i + 1:]:
            ab = float(state[a].get("relations", {}).get(b, {}).get("intimacy", 0.0))
            ba = float(state[b].get("relations", {}).get(a, {}).get("intimacy", 0.0))
            if closest_pair is None or max(ab, ba) > closest_pair[2]:
                closest_pair = (a, b, round(max(ab, ba), 4))

    return {
        "session_id": session_id,
        "script_id": session["script_id"],
        "onto": onto,
        "runtime": state,
        "round": round_no,
        "total_rounds": total,
        "round_progress": round(round_no / max(1, total), 4),
        "case_progress": round(len(revealed) / max(1, len(pool)), 4) if pool else 0.0,
        "heat": round(heat, 4),
        "heat_target": round(target, 4),
        "missing_facts": missing,
        "revealed_clues": revealed,
        "unrevealed_clues": [c for c in pool if c not in revealed],
        "highest_exposure": highest_exposure,
        "intoxicated": intoxicated,
        "closest_pair": closest_pair,
    }


def select(ctx, only=None):
    """Return deterministic candidates sorted by trigger strength."""
    candidates = []
    for event_id, spec in EVENTS.items():
        if only and event_id not in only:
            continue
        if not spec["trigger"](ctx):
            continue
        candidates.append({
            "event_id": event_id,
            "label": spec["label"],
            "score": spec["score"](ctx),
            "effects": list(spec["effects"]),
            "variables": {
                "round": ctx["round"], "case_progress": ctx["case_progress"],
                "heat": ctx["heat"], "heat_target": ctx["heat_target"],
                "missing_facts": len(ctx["missing_facts"]),
            },
        })
    return sorted(candidates, key=lambda c: (-c["score"], c["event_id"]))


def _log_l3(conn, session_id, round_no, seat, state, key, before, after, reason):
    if abs(after - before) < 1e-9:
        return
    conn.execute(
        """INSERT INTO attribute_events(session_id, round, actor, layer, character_id, key, target,
                                        before_json, after_json, reason, created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            session_id, round_no, "gm:host", "L3", state.get("character_id", seat), key, "",
            json.dumps({"value": before}, ensure_ascii=False),
            json.dumps({
                "key": key, "value": after, "layer": "L3", "scope": "self",
                "visibility": "self", "source": "gm_event", "evidence": [],
                "updated_at_round": round_no, "updated_by": "gm:host",
            }, ensure_ascii=False),
            reason, db.now(),
        ),
    )


def _release_clue(conn, ctx, guard):
    pool = ctx["unrevealed_clues"]
    if not pool:
        return None, [], []
    def weight(line):
        return sum(float(x) for x in LOG_LR_RE.findall(str(line)))
    clue = max(pool, key=weight)
    revealed = list(ctx["revealed_clues"]) + [clue]
    db.set_game_state(conn, ctx["session_id"], "revealed_clues", revealed)
    ids = (guard.exempt_clue(clue) if guard else ctx["onto"].get("clue_fact_map", {}).get(clue, []))
    if isinstance(ids, str):
        ids = [ids]
    for state in ctx["runtime"].values():
        for fid in ids:
            state.setdefault("beliefs", {})[fid] = {
                "belief": 0.7, "source": "gm_event", "evidence": [clue],
            }
    return clue, list(ids), [{"kind": "clue", "clue": clue, "facts": list(ids)}]


def apply(conn, session_id, event_id=None, round_no=None, runtime=None, guard=None):
    """Apply one perturbation and append its audit event.  Explicit id wins."""
    ctx = build_context(conn, session_id, round_no, runtime)
    candidates = select(ctx, only=[event_id] if event_id else None)
    if not candidates:
        raise ValueError("ERR_NO_EVENT_CANDIDATE")
    chosen = candidates[0]
    event_id = chosen["event_id"]
    state = ctx["runtime"]
    changes = []
    payload_effects = {}

    def bump_all(key, delta, reason):
        for seat, seat_state in state.items():
            emo = seat_state.setdefault("emotion", {})
            before, after = _bump(emo, key, delta)
            _log_l3(conn, session_id, ctx["round"], seat, seat_state, key, before, after, reason)
            changes.append({"seat": seat, "key": key, "before": before, "after": after})

    if event_id == "second_body":
        bump_all("fear", 0.3, "第二具尸体")
        bump_all("stress", 0.25, "第二具尸体")
        alive = int(db.get_game_state(conn, session_id, "alive_count", len(state)))
        db.set_game_state(conn, session_id, "alive_count", max(0, alive - 1))
        payload_effects["alive_count"] = max(0, alive - 1)
    elif event_id == "blackout":
        bump_all("arousal", 0.2, "停电/封锁")
        db.set_game_state(conn, session_id, "ap_penalty", -2)
        payload_effects["ap_penalty"] = -2
    elif event_id == "npcs_letter":
        clue, fact_ids, extra = _release_clue(conn, ctx, guard)
        changes.extend(extra)
        payload_effects.update({"clue": clue, "facts": fact_ids})
    elif event_id == "anonymous_tip":
        seat, exposure = ctx["highest_exposure"]
        emo = state[seat].setdefault("emotion", {})
        before, after = _bump(emo, "stress", 0.4)
        _log_l3(conn, session_id, ctx["round"], seat, state[seat], "stress", before, after, "匿名举报")
        before_risk, after_risk = _bump(emo, "exposure_risk", 0.1)
        _log_l3(conn, session_id, ctx["round"], seat, state[seat], "exposure_risk", before_risk, after_risk, "匿名举报")
        changes.append({"seat": seat, "key": "stress", "before": before, "after": after})
        changes.append({"seat": seat, "key": "exposure_risk", "before": before_risk, "after": after_risk})
        payload_effects["target"] = seat
    elif event_id == "old_flame":
        a, b, _ = ctx["closest_pair"]
        for seat, other in ((a, b), (b, a)):
            rel = state[seat].setdefault("relations", {}).setdefault(other, {})
            before, after = _bump(rel, "commitment", 0.3)
            _log_l3(conn, session_id, ctx["round"], seat, state[seat], "commitment", before, after, "旧情复燃")
            changes.append({"seat": seat, "key": f"commitment->{other}", "before": before, "after": after})
        payload_effects["pair"] = [a, b]
    elif event_id == "drunken_slip":
        seat, _ = ctx["intoxicated"]
        known = [
            fid for fid, info in state[seat].get("beliefs", {}).items()
            if info.get("source") == "canon"
        ]
        leaked = known[0] if known else None
        if leaked:
            for other, seat_state in state.items():
                if other == seat:
                    continue
                seat_state.setdefault("beliefs", {})[leaked] = {
                    "belief": 0.6, "source": "gm_event:drunken_slip", "evidence": [],
                }
        payload_effects.update({"speaker": seat, "fact_id": leaked})
        changes.append({"seat": seat, "key": "force_reveal", "before": 0, "after": 1 if leaked else 0})
    elif event_id == "weather":
        db.set_game_state(conn, session_id, "clue_decay_multiplier", 2.0)
        payload_effects["clue_decay_multiplier"] = 2.0

    db.set_game_state(conn, session_id, "runtime", state)
    conn.commit()
    payload = {
        "type": "gm_event_injection",
        "event_id": event_id,
        "label": chosen["label"],
        "score": chosen["score"],
        "effects": chosen["effects"],
        "applied": payload_effects,
        "changes": changes,
        "variables": chosen["variables"],
    }
    seq = db.append_game_event(conn, session_id, ctx["round"], "gm:host", "gm_event_injection", payload, ["public"])
    return {"event_id": event_id, "seq": seq, "round": ctx["round"], **payload}
