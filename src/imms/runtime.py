"""Headless runtime: GM pacing + character agents consuming IMMS runtime formulas."""

import json
import random
import re

from . import aftermath, breakout, contradiction, db, director, emotion, events, faction, facets, formulas, frame, gm_direction, gm_narrator, infoguard, investigation, ips, journal, l4, lie, llm, longterm, memory, performance_guide, phases, scoring, script_locks, speech_style, stage_gate, style_compiler, timeline, vote

REL_DEFAULT = {
    "like": 0.0, "trust": 0.0, "respect": 0.5, "intimacy": 0.0,
    "suspicion": 0.1, "fear": 0.0, "envy": 0.0, "grudge": 0.0,
    "debt": 0.0, "dominance": 0.0, "commitment": 0.0,
}
REL_DOMAIN = {
    "like": (-1, 1), "trust": (-1, 1), "respect": (0, 1), "intimacy": (0, 1),
    "suspicion": (0, 1), "fear": (0, 1), "envy": (0, 1), "grudge": (0, 1),
    "debt": (-1, 1), "dominance": (-1, 1), "commitment": (0, 1),
}
APPRAISAL = {
    "defend": "defend_public", "ally": "defend_public", "share_clue": "share_clue",
    "private_say": "share_clue",
    "accuse": "accuse_unfounded", "lie_exposed": "lie_exposed", "caught_lying": "caught_lying",
    "deflect": "take_credit", "betray": "betray", "rescue": "rescue",
}
INTENTS = ["assert", "ask", "accuse", "defend", "ally", "deflect", "confess", "provoke"]


def _attrs(state):
    return state.get("l1", {})


def _name_map(onto):
    return {c["name"]: c["id"] for c in onto.get("characters", [])}


def init_runtime(conn, session_doc):
    session_id = session_doc["session_id"]
    if db.get_game_state(conn, session_id, "runtime") is not None:
        return
    script_id = session_doc["script_id"]
    onto = db.get_script(conn, script_id)
    # R4: compile explicit script locks onto the session so forks inherit the
    # exact same rules; missing declarations stay empty rather than inferred.
    script_locks.ensure_session(conn, session_id, onto, session_doc)
    persona = db.get_persona_l1(conn, script_id)
    l1map = {p["character_id"]: {a["key"]: a["value"] for a in p["attributes"] if a["value"] is not None} for p in persona["personas"]}
    # L2 assigned values are session-scoped; reload them into the seat view so
    # prompt blocks (public_identity/mask) read assigned data, not canon. Only
    # keys absent from L1 are bridged -- L1 canon stays authoritative.
    l2map = {}
    for row in conn.execute(
        "SELECT character_id, data_json FROM persona_l2 WHERE session_id=?", (session_id,)
    ):
        attr = json.loads(row["data_json"])
        if attr.get("value") is not None and attr.get("key"):
            l2map.setdefault(row["character_id"], {})[f"L2.{attr['key']}"] = attr["value"]
    for cid, assigned in l2map.items():
        l1map.setdefault(cid, {}).update(assigned)

    # G21: dials live on session.l0 and are copied to every seat so downstream
    # rules read the same validated values.  Legacy sessions (no dials) keep
    # neutral multipliers and identical behaviour.
    dials = director.from_session(session_doc)
    if dials and not (session_doc.get("l0") or {}).get("dials"):
        session_doc.setdefault("l0", {})["dials"] = dials
        db.save_session(conn, session_doc)
    seats = [p for p in session_doc["participants"] if p["kind"] != "gm"]
    others = [p["id"] for p in seats]
    facts = {f.get("id"): f for f in onto.get("facts", []) if f.get("id")}
    public_ids = {fid for fid, f in facts.items() if f.get("visibility") == "public"}
    known_by_char = {
        c.get("id"): set(c.get("known_fact_ids") or [])
        for c in onto.get("characters", [])
    }
    char_by_id = {c.get("id"): c for c in onto.get("characters", [])}
    runtime = {}
    for p in seats:
        cid = p["script_character_id"]
        attrs = l1map.get(cid, {})
        N = attrs.get("OCEAN.N", 0.5)
        beliefs = {
            fid: {"belief": 0.8, "source": "public", "evidence": []}
            for fid in sorted(public_ids)
        }
        for fid in sorted(known_by_char.get(cid, set())):
            beliefs[fid] = {"belief": 0.9, "source": "canon", "evidence": []}
        runtime[p["id"]] = {
            "character_id": cid,
            "l1": attrs,
            "l0": {**(p.get("l0") or {}), "dials": dials} if dials else dict(p.get("l0") or {}),
            "relations": {o: dict(REL_DEFAULT) for o in others if o != p["id"]},
            "emotion": {
                "valence": 0.0, "arousal": 0.2, "dominance": 0.5,
                "stress": 0.1, "cognitive_load": 0.1, "intoxication": 0.0,
                "morale": 0.8, "exposure_risk": 0.1, "discrete": "平静",
            },
            "nu": formulas.nu(N),
            "ell": {o: 0.0 for o in others if o != p["id"]},
            "suspicion": {o: 0.1 for o in others if o != p["id"]},
            "faction": None,
            "beliefs": beliefs,
            "lie_ledger": [],
            "memory": [],
            "commitments": {},
            # B3: the human seat relies on its own emotions and stays outside
            # the IPS pipe; AI seats carry a leaky accumulator from canon OCEAN.
            "accumulator": (
                None if session_doc["human_character_id"] in p["id"]
                else director.apply_to_accumulator(ips.new_accumulator(attrs), dials)
            ),
            "delivery": None,
            # G25: L2-stored persona facets; L1 canon is never rewritten.
            "facets": facets.load_registry(conn, session_id, cid),
            "facet_buckets": {},
            "facet_switch_log": [],
            # G09: per-seat lock ledger is part of the snapshot, so a GM branch
            # cannot start from a state with already-satisfied/skipped locks.
            "script_lock_state": script_locks.seat_state(session_doc, p["id"]),
            # G10: explicit trauma triggers and faction bookkeeping.  Both
            # stay empty/unaligned when the script did not author them.
            "trauma": list((char_by_id.get(cid) or {}).get("trauma") or []),
            "faction_state": {"current": None, "cooldown_until": 0},
            "pending_contradictions": [],
        }
    # Clue cards are not knowledge until a clue is actually released/revealed.
    # Every agent starts with only public facts plus its own canon knowledge.

    # G23: prior sessions' non-faded sentiment feeds L3 relation baselines.
    # This is meta state only -- L1 canon is never written back.
    longterm.refresh_faded(conn)
    priors = []
    for cid in sorted({s["character_id"] for s in runtime.values()}):
        priors.extend(longterm.inject_priors_from(conn, runtime, cid))
    if priors:
        db.append_game_event(
            conn, session_id, 0, "gm:host", "longterm_prior",
            {"priors": priors}, ["gm:host"],
        )

    db.set_game_state(conn, session_id, "runtime", runtime)
    db.set_game_state(conn, session_id, "round", 0)
    db.set_game_state(conn, session_id, "revealed_clues", [])
    db.set_game_state(conn, session_id, "user_input_index", 0)
    stage_gate.init_resources(conn, session_id, others)
    timeline.update_session(conn, session_id, onto, 0, {
        p["id"]: p["script_character_id"] for p in seats
    })


def _appraise(state, event, round_no):
    actor = event["actor"]
    act = event["act_type"]
    payload = event.get("payload", {})
    key = APPRAISAL.get(act)
    if not key or actor == state.get("seat_id"):
        return None, []
    target = payload.get("target")
    if target and target != state.get("character_id") and target != state.get("seat_id"):
        return key, []
    beta = formulas.BETA_MATRIX.get(key, {})
    eta = payload.get("eta", 1.0)
    kappa_v = formulas.kappa(state["relations"].get(actor, REL_DEFAULT)["intimacy"])
    changed = []
    for dim, coeff in beta.items():
        if dim not in REL_DEFAULT:
            continue
        lo, hi = REL_DOMAIN[dim]
        before = state["relations"].setdefault(actor, dict(REL_DEFAULT))[dim]
        after = formulas.relation_update(before, REL_DEFAULT[dim], 0.95, coeff, eta, kappa_v, state["nu"], lo, hi)
        state["relations"][actor][dim] = after
        changed.append({"dim": dim, "target": actor, "before": round(before, 4), "after": round(after, 4)})
    impact = {"valence": -0.15 if coeff < 0 else 0.10, "arousal": 0.12, "dominance": 0.05}
    text = " ".join([
        str(payload.get("utterance") or ""),
        " ".join(str(x) for x in (payload.get("claimed_facts") or [])),
    ])
    emo_report = emotion.apply_impact(state, impact, text, round_no)
    for dim, value in emo_report["after"].items():
        changed.append({
            "dim": dim, "target": None, "before": emo_report["before"][dim],
            "after": value, "reason": "F08 trauma" if emo_report["hit"] else "F08 PAD",
        })
    return key, changed


def _update_suspicion(state, events):
    changed = []
    for ev in events:
        payload = ev.get("payload", {})
        for target, log_lr in payload.get("log_lr", {}).items():
            before = state["suspicion"].get(target, formulas.sigmoid(state["ell"].get(target, 0.0)))
            ell = state["ell"].get(target, 0.0) + formulas.evidence_weight(0.8, 1.0, 0.1) * log_lr
            state["ell"][target] = ell
            state["suspicion"][target] = formulas.sigmoid(ell)
            changed.append({"dim": "suspicion", "target": target, "before": before, "after": state["suspicion"][target]})
    return changed


def _log_changes(conn, session_id, round_no, seat_id, character_id, changes):
    """Mirror significant formula-driven L3 deltas into the audit trail."""
    for ch in changes:
        before, after = ch.get("before"), ch.get("after")
        if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
            continue
        before, after = round(before, 4), round(after, 4)
        if abs(after - before) < 0.05:
            continue
        reason = ch.get("reason") or "事件评价 F02/F12"
        data = {
            "key": ch["dim"], "value": after, "layer": "L3",
            "scope": "dyadic", "target": ch.get("target"),
            "visibility": "self", "source": "runtime:formula",
            "locked": False, "reason": reason, "updated_at_round": round_no,
            "updated_by": seat_id,
        }
        conn.execute(
            """INSERT INTO attribute_events(session_id, round, actor, layer, character_id, key, target,
                                            before_json, after_json, reason, created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                session_id, round_no, "runtime:formula", "L3", character_id, ch["dim"],
                ch.get("target") or "", json.dumps({"value": before}, ensure_ascii=False),
                json.dumps(data, ensure_ascii=False), reason, db.now(),
            ),
        )
    conn.commit()


def _fit(intent, attrs):
    O = attrs.get("OCEAN.O", 0.5)
    C = attrs.get("OCEAN.C", 0.5)
    E = attrs.get("OCEAN.E", 0.5)
    A = attrs.get("OCEAN.A", 0.5)
    honesty = attrs.get("values.honesty", 0.5)
    mapping = {
        "assert": 0.5 * O + 0.5 * C,
        "ask": 0.7 * O + 0.3 * E,
        "accuse": 0.6 * (1 - A) + 0.4 * C,
        "defend": 0.6 * C + 0.4 * (1 - A),
        "ally": 0.6 * A + 0.4 * E,
        "deflect": 0.5 * (1 - C) + 0.5 * (1 - A),
        "confess": 0.7 * honesty + 0.3 * (1 - C),
        "provoke": 0.6 * (1 - A) + 0.4 * E,
    }
    return mapping.get(intent, 0.5)


def _choose_intent(state, rng):
    attrs = _attrs(state)
    emo = state["emotion"]
    weights = {"w_p": 1.2, "w_g": 1.0, "w_e": 0.8, "w_s": 0.6, "w_r": 1.0, "w_c": 0.5}
    max_susp = max(state["suspicion"].values()) if state["suspicion"] else 0.1
    intents = list(INTENTS)
    if emo["exposure_risk"] > 0.5:
        intents.append("deflect")
    if emo["morale"] < 0.3:
        intents.append("confess")
    utilities = {}
    for intent in intents:
        fit = _fit(intent, attrs)
        gain = 0.8 if intent == "accuse" and max_susp > 0.5 else 0.3
        impulse = emo["arousal"] * (0.8 if intent in {"accuse", "deflect"} else 0.2)
        social = sum(r.get("like", 0) + r.get("trust", 0) for r in state["relations"].values()) / max(1, len(state["relations"]))
        risk = emo["exposure_risk"] if intent in {"accuse", "confess"} else 0.1
        cost = (1 - attrs.get("values.honesty", 0.5)) if intent == "confess" else 0.1
        utilities[intent] = formulas.utility(weights, fit, gain, impulse, social, risk, cost)
    temp = formulas.decision_temperature(1.0, emo["arousal"], emo["intoxication"])
    probs = formulas.softmax(utilities, temp)
    return rng.choices(list(probs), weights=list(probs.values()), k=1)[0]


def _utter(intent, target_name, clue):
    templates = {
        "assert": f"我认为「{clue}」值得注意。",
        "ask": f"{target_name}，案发时你在哪里？",
        "accuse": f"{target_name}，你的说法和证据对不上。",
        "defend": "我的说法前后一致，没有矛盾。",
        "ally": f"我愿意相信{target_name}。",
        "deflect": "这件事我记不清了。",
        "confess": "我承认，我隐瞒了一件事。",
        "provoke": f"{target_name}，你敢说这件事与你无关？",
    }
    return templates.get(intent, "...")


def _style_for_seat(conn, session_id, seat_id, state, round_no, seed, target_id,
                    target_name, allowed_facts, script_id):
    """Build the speech-style prompt parts and persist one drift ledger row.

    The engine only reads stored state.  Canon evidence quotes are pulled from
    L1 envelopes (read-only) and used as verbatim anchors, never as invented
    dialogue.  A failed/missing field stays empty and is reported in `missing`.
    """
    recent = db.get_drift_ledger(conn, session_id, seat_id)[-speech_style.POST_GATE_ROUNDS:]
    ctx = {
        "session_id": session_id, "seat_id": seat_id, "round_no": round_no,
        "seed": seed, "target_name": target_name, "allowed_facts": list(allowed_facts),
        "recent_drift_totals": [r["drift_total"] for r in recent],
    }
    quotes = []
    for row in conn.execute(
        "SELECT data_json FROM persona_l1 WHERE script_id=? AND character_id=?",
        (script_id, state["character_id"]),
    ):
        try:
            env = json.loads(row["data_json"])
        except (TypeError, ValueError):
            continue
        for ev in (env.get("evidence") or []):
            if ev.get("quote"):
                quotes.append(ev["quote"])
    ctx["evidence_quotes"] = quotes[:5]
    # Relation vectors are keyed by seat id; the prompt must still show the
    # script's character name, never an internal ai:/human: identifier.
    if target_id in state.get("relations", {}):
        ctx["target_id"] = target_id
    view = dict(state)
    onto = db.get_script(conn, script_id)
    names = {c["id"]: c.get("name") for c in onto.get("characters", [])}
    view["name"] = names.get(state["character_id"], state["character_id"])
    view["public_identity"] = state.get("l1", {}).get("public_identity") \
        or state.get("l1", {}).get("L2.public_identity")
    parts = speech_style.render(view, ctx)
    return parts, ctx


def _guard(state, act, public_facts):
    claims = [f for f in act["claimed_facts"] if f in state["beliefs"] or f in public_facts]
    act["claimed_facts"] = claims
    if not state.get("is_human"):
        act = lie.guard_escalation(state, act)
    for entry in state["lie_ledger"]:
        if entry.get("heat", 0) > 0.3 and any(c in entry.get("claimed_facts", []) for c in act["claimed_facts"]):
            if "记不清" not in act["utterance"]:
                act["utterance"] = "这件事我记不清了。"
                act["act_type"] = "deflect"
    return act


def _provocation_from_act(act):
    """G24: derive an ignition impact from an utterance, or None.

    Explicit host/script impact wins; otherwise accusation and alliance are
    the two canonical negative/positive sources.  This applies to the human
    seat as well as AI seats, but never writes IPS fields onto the speaker.
    """
    explicit = act.get("impact") or act.get("provocation")
    if isinstance(explicit, dict):
        kind = explicit.get("kind") or ips.PROVOCATION_KINDS.get(act.get("act_type"), "humiliation")
        if kind not in ips.IMPACT_KINDS:
            return None
        s_k = ips.IMPACT_KINDS[kind]
        try:
            s_k = float(explicit.get("s_k", s_k))
        except (TypeError, ValueError):
            pass
        return kind, round(ips.formulas.clip(s_k, 0.0, 1.0), 4)
    mapped = ips.PROVOCATION_KINDS.get(act.get("act_type"))
    if mapped:
        return mapped, ips.IMPACT_KINDS[mapped]
    if act.get("act_type") == "accuse":
        return "false_accusation", ips.IMPACT_KINDS["false_accusation"]
    if act.get("act_type") == "ally":
        return "warmth", ips.IMPACT_KINDS["warmth"]
    return None


def _user_act(scripted, scripted_index, target_name):
    if scripted is None:
        text = input(f"[用户席位] 第 {scripted_index + 1} 轮发言：")
        return {"act_type": "assert", "utterance": text, "claimed_facts": [], "target": None, "inner_intent": "用户自行决定"}
    lines = scripted.get("utterances", [])
    if scripted_index >= len(lines):
        raise ValueError("ERR_USER_INPUT_EXHAUSTED")
    item = lines[scripted_index]
    if isinstance(item, dict):
        return {
            "act_type": item.get("act_type", "assert"),
            "utterance": item.get("utterance", ""),
            "claimed_facts": item.get("claimed_facts", []),
            "target": item.get("target"),
            "inner_intent": "用户自行决定",
            "impact": item.get("impact"),
            "provocation": item.get("provocation"),
        }
    return {"act_type": "assert", "utterance": item, "claimed_facts": [], "target": None, "inner_intent": "用户自行决定"}


def _audience_for(conn, row, all_seats):
    """Who actually received an event, derived from its visibility tokens."""
    try:
        vis = json.loads(row.get("visibility_json") or "[]")
    except (TypeError, ValueError):
        vis = ["public"]
    actor = row.get("actor")
    if "public" in vis:
        return [s for s in all_seats if s != actor]
    audience = set()
    for token in vis:
        if not isinstance(token, str):
            continue
        if token.startswith("room:"):
            try:
                room = db.get_private_room(conn, token[len("room:"):])
                audience.update(room.get("participants") or [])
            except ValueError:
                continue
        elif token in all_seats:
            audience.add(token)
    audience.discard(actor)
    return sorted(audience)


def _emit_conflict(conn, session_id, round_no, seat_id, conflict):
    """Record a low-false-positive statement conflict for GM audit only."""
    if not conflict:
        return None
    payload = dict(conflict)
    payload["witness"] = seat_id
    seq = db.append_game_event(
        conn, session_id, round_no, "gm:host", "statement_conflict", payload, ["gm:host"]
    )
    return seq


def _recall_payload(items):
    return [{
        "round": item.get("round"), "speaker": item.get("speaker"),
        "utterance": item.get("utterance"), "status": item.get("status"),
        "recall_score": item.get("recall_score"),
    } for item in (items or [])]


def _gm_step(conn, session_id, round_no, onto, runtime, guard=None, seat_map=None, decision=None):
    revealed = db.get_game_state(conn, session_id, "revealed_clues", [])
    events = db.visible_game_events(conn, session_id, "gm")
    n_accuse = sum(1 for e in events if e["round"] == round_no and e["act_type"] == "accuse")
    n_contra = sum(1 for e in events if e["round"] == round_no and e["act_type"] == "contradiction")
    mean_arousal = sum(s["emotion"]["arousal"] for s in runtime.values()) / max(1, len(runtime))
    heat = formulas.actual_heat(n_accuse, n_contra, mean_arousal)
    total_rounds = int(
        db.get_game_state(conn, session_id, "total_rounds", 0)
        or db.get_game_state(conn, session_id, "round", 0)
        or round_no
    )
    target = formulas.heat_target(round_no, max(1, total_rounds), 3.0)
    injected = None
    gm_changes = []
    chosen = None
    stage_advance = None
    private_window = None
    if decision and decision.get("advance_stage"):
        stage_advance = stage_gate.advance(conn, session_id, round_no)
    if decision and decision.get("private_allowed") is not None:
        private_window = stage_gate.set_private_allowed(
            conn, session_id, round_no, decision["private_allowed"], decision,
        )
    if decision and decision.get("inject_event"):
        applied = events.apply(
            conn, session_id, decision["inject_event"], round_no, runtime, guard,
        )
        injected = {
            "type": "gm_event_injection", "event_id": applied["event_id"],
            "seq": applied["seq"], "label": applied["label"],
        }
        return {
            "heat": round(heat, 4), "target": round(target, 4),
            "injected": injected, "changes": [], "event_injection": applied,
            "stage_advance": stage_advance, "private_window": private_window,
        }
    if decision and decision.get("release_clue") and decision["release_clue"] not in revealed:
        if decision["release_clue"] in onto.get("clue_pool", []):
            chosen = decision["release_clue"]
    if chosen is None and heat < target - 0.3:
        pool = [c for c in onto.get("clue_pool", []) if c not in revealed]
        if pool:
            chosen = pool[0]
    if chosen is not None:
        clue = chosen
        revealed.append(clue)
        db.set_game_state(conn, session_id, "revealed_clues", revealed)
        exempted = guard.exempt_clue(clue) if guard else list(onto.get("clue_fact_map", {}).get(clue, []))
        injected = {"type": "clue_release", "clue": clue, "facts_now_public": exempted}
        for state in runtime.values():
            for fid in exempted:
                state["beliefs"][fid] = {"belief": 0.7, "source": "clue", "evidence": [clue]}
        match = re.search(r"logLR:(\w+)=([-\d.]+)", clue)
        if match:
            target_raw = match.group(1)
            target_seat = (seat_map or {}).get(target_raw, f"ai:{target_raw}")
            log_lr = float(match.group(2))
            for state in runtime.values():
                if state.get("seat_id") == target_seat:
                    # The accused does not suspect themselves; they feel exposure.
                    emo = state["emotion"]
                    before_risk = emo.get("exposure_risk", 0.0)
                    emo["exposure_risk"] = min(1.0, before_risk + 0.2)
                    gm_changes.append((state.get("seat_id"), state["character_id"], {
                        "dim": "exposure_risk", "before": before_risk, "after": emo["exposure_risk"],
                    }))
                    continue
                before = state["suspicion"].get(target_seat, formulas.sigmoid(state["ell"].get(target_seat, 0.0)))
                ell = state["ell"].get(target_seat, 0.0) + 0.8 * log_lr
                state["ell"][target_seat] = ell
                state["suspicion"][target_seat] = formulas.sigmoid(ell)
                gm_changes.append((state.get("seat_id"), state["character_id"], {
                    "dim": "suspicion", "target": target_seat,
                    "before": before, "after": state["suspicion"][target_seat],
                }))
        db.set_game_state(conn, session_id, "runtime", runtime)
        db.append_game_event(conn, session_id, round_no, "gm:host", "clue_release", injected, ["public"])
    elif heat > target + 0.3:
        injected = {"type": "rest"}
        db.append_game_event(conn, session_id, round_no, "gm:host", "rest", injected, ["public"])
    return {
        "heat": round(heat, 4), "target": round(target, 4), "injected": injected,
        "changes": gm_changes, "stage_advance": stage_advance,
        "private_window": private_window,
    }


def _vote(conn, session_id, runtime, session_doc, scripted, seed=42):
    result = vote.run_vote(
        conn, session_id, scripted=scripted, runtime=runtime,
        session_doc=session_doc, seed=seed,
    )
    for change in result.pop("_changes", []):
        if change.get("seat_id"):
            _log_changes(
                conn, session_id, db.get_game_state(conn, session_id, "round", 0),
                change["seat_id"], change.get("character_id"), [change],
            )
    return result


def _self_intro_prompt(state, script_title, seat_names, names):
    """First-person self-intro prompt for one seat (public information only)."""
    l1 = state.get("l1", {}) or {}
    cid = state.get("character_id")
    public = l1.get("public_identity") or l1.get("L2.public_identity") or names.get(cid, cid)
    persona = l1.get("personality") or l1.get("L1.personality") or "（未配置）"
    return "\n".join([
        "# 读本与自我介绍",
        f"剧本：{script_title or '本局'}",
        f"你的公开身份：{public}",
        f"你的人设：{persona}",
        "# 玩家规则",
        *[f"- {r}" for r in __import__("imms.prompt_engine", fromlist=["PLAYER_RULES"]).PLAYER_RULES],
        "# 任务",
        "用第一人称做一段自我介绍：姓名、职业、案发时段在做什么、与在座者的明面关系。",
        "只说公开信息，绝不提私有秘密、任务、真凶或作案细节；不照本念。",
        '严格输出 JSON：{"utterance":"...","emotion_shown":"..."}',
    ])


def _run_pre_discussion(conn, session_id, scripted, speaker, gm_agent, gm_voice,
                        seed, seat_names, names):
    """Drive intro -> self-intro -> investigate before the discussion rounds."""
    session_doc = db.get_session(conn, session_id)
    onto = db.get_script(conn, session_doc["script_id"])
    runtime = db.get_game_state(conn, session_id, "runtime") or {}
    seats = list(runtime.keys())
    log = []
    scripted = scripted or {}

    phases.set_phase(conn, session_id, "intro", round_no=0)
    voice = gm_narrator.pick_voice_fp(conn, session_id, runtime, gm_voice, 0, seed=seed)
    report = phases.narrate_phase(conn, session_id, "intro", gm_agent, 0, seat_names, voice)
    db.append_game_event(conn, session_id, 0, "gm:host", "gm_phase_narration", report, ["public"])
    log.append({"round": 0, "actor": "gm:host", "act": "phase:intro",
                "phase": "intro", "status": report["status"]})

    phases.set_phase(conn, session_id, "self_intro", round_no=0)
    intros = scripted.get("self_intros") or {}
    for seat_id in seats:
        state = runtime.get(seat_id, {})
        cid = state.get("character_id")
        label = seat_names.get(seat_id, names.get(cid, cid))
        custom = intros.get(seat_id) or intros.get(label)
        if custom:
            utterance = str(custom)
        else:
            utterance = f"我是{label}。案发那会儿我在场，先后顺序大家可以对。"
            if speaker.name != "template":
                prompt = _self_intro_prompt(state, onto.get("title"), seat_names, names)
                try:
                    if hasattr(speaker, "bind"):
                        speaker.bind(0, seat_id, "self_intro")
                    parsed = llm.parse_speech(speaker.generate(prompt), [], "self_intro")
                    utterance = parsed["utterance"]
                except llm.LLMError:
                    pass
        payload = {"utterance": utterance, "emotion_shown": "平静",
                   "target": None, "claimed_facts": [], "self_intro": True}
        seq = db.append_game_event(conn, session_id, 0, seat_id, "self_intro", payload, ["public"])
        log.append({"round": 0, "actor": seat_id, "act": "self_intro", "seq": seq})
    report = phases.narrate_phase(conn, session_id, "self_intro", gm_agent, 0, seat_names, voice)
    db.append_game_event(conn, session_id, 0, "gm:host", "gm_phase_narration", report, ["public"])
    log.append({"round": 0, "actor": "gm:host", "act": "phase:self_intro",
                "phase": "self_intro", "status": report["status"]})

    phases.set_phase(conn, session_id, "investigate", round_no=0)
    investigation.init_investigation(conn, session_id, onto, seats, seed=seed)
    searches = scripted.get("searches") or []
    if not searches:
        targets = investigation.available_targets(onto)
        searches = [{"seat": s, "target": targets[i % len(targets)] if targets else "现场",
                     "make_public": True} for i, s in enumerate(seats)]
    for item in searches:
        seat_id = item.get("seat") or item.get("actor")
        if seat_id not in runtime:
            continue
        target = item.get("target")
        if not target:
            continue
        try:
            found = investigation.search(
                conn, session_id, seat_id, target, onto,
                make_public=bool(item.get("make_public", True)), seed=seed,
            )
            log.append({"round": 0, "actor": seat_id, "act": "clue_found",
                        "card_id": found["card_id"], "visibility": found["visibility"]})
        except ValueError as exc:
            log.append({"round": 0, "actor": seat_id, "act": "clue_found",
                        "status": "rejected", "reason": str(exc)})
    rooms = scripted.get("private_rooms") or []
    for room in rooms:
        participants = [s for s in room.get("seats", []) if s in runtime]
        if len(participants) < 2:
            continue
        opened = investigation.open_private_chat(conn, session_doc, 0, participants)
        for msg in room.get("utterances") or []:
            actor = msg.get("seat") or msg.get("actor")
            if actor not in participants:
                continue
            try:
                investigation.private_chat(
                    conn, session_doc, opened["room_id"], actor,
                    msg.get("target"), msg.get("utterance", ""),
                    claimed_facts=msg.get("claimed_facts"),
                )
            except ValueError:
                continue
        investigation_done = db.get_private_room(conn, opened["room_id"])
        db.close_private_room(conn, session_id, opened["room_id"])
        log.append({"round": 0, "actor": "gm:host", "act": "private_room",
                    "room_id": opened["room_id"], "participants": participants})
    report = phases.narrate_phase(conn, session_id, "investigate", gm_agent, 0, seat_names, voice)
    db.append_game_event(conn, session_id, 0, "gm:host", "gm_phase_narration", report, ["public"])
    log.append({"round": 0, "actor": "gm:host", "act": "phase:investigate",
                "phase": "investigate", "status": report["status"]})
    return log


def run_phased_session(conn, session_id, rounds=3, scripted=None, seed=42, llm_name=None,
                       start_round=None, gm_policy=None, speaker=None, gm_agent=None,
                       gm_voice=None):
    """Full 剧本杀 flow: intro -> self-intro -> investigate -> discuss -> vote -> reveal.

    The pre-discussion phases are new; discussion/voting reuse the unchanged
    :func:`run_session` loop so all formula, IPS, InfoGuard and replay contracts
    stay intact.  Legacy callers of ``run_session`` see no behaviour change.
    """
    session_doc = db.get_session(conn, session_id)
    init_runtime(conn, session_doc)
    onto = db.get_script(conn, session_doc["script_id"])
    runtime = db.get_game_state(conn, session_id, "runtime") or {}
    names = {v: k for k, v in _name_map(onto).items()}
    seat_names = {p["id"]: p["seat_label"] for p in session_doc["participants"]}
    speaker = speaker if speaker is not None else llm.build_provider(llm_name)
    gm_agent = gm_agent if gm_agent is not None else speaker
    pre_log = _run_pre_discussion(
        conn, session_id, scripted, speaker, gm_agent, gm_voice, seed, seat_names, names,
    )
    phases.set_phase(conn, session_id, "discuss", round_no=1)
    out = run_session(
        conn, session_id, rounds=rounds, scripted=scripted, seed=seed,
        llm_name=None, start_round=start_round if start_round is not None else 1,
        gm_policy=gm_policy, speaker=speaker, gm_agent=gm_agent, gm_voice=gm_voice,
    )
    out["log"] = pre_log + out["log"]
    out["phases"] = phases.phase_log(conn, session_id)
    phases.set_phase(conn, session_id, "vote", status="resolved", round_no=rounds)
    phases.set_phase(conn, session_id, "reveal", status="resolved", round_no=rounds)
    out["phases"] = phases.phase_log(conn, session_id)
    return out


def run_session(conn, session_id, rounds=3, scripted=None, seed=42, llm_name=None,
                start_round=None, gm_policy=None, speaker=None, gm_agent=None,
                gm_voice=None, finalize=True):
    """Run discussion rounds.  ``finalize=False`` skips vote/scoring/settlement.

    Used by the headless driver to advance one round at a time; all per-round
    formulas, IPS, InfoGuard and event writes are unchanged.
    """
    session_doc = db.get_session(conn, session_id)
    init_runtime(conn, session_doc)
    onto = db.get_script(conn, session_doc["script_id"])
    char_by_id = {c.get("id"): c for c in onto.get("characters", [])}
    runtime = db.get_game_state(conn, session_id, "runtime")
    guard_engine = infoguard.build_engine(onto)
    for clue in db.get_game_state(conn, session_id, "revealed_clues", []):
        guard_engine.exempt_clue(clue)
    rng = random.Random(seed)
    human_id = session_doc["human_character_id"]
    frame_seat = frame.killer_seat(session_doc, onto)
    all_seats = list(runtime.keys())
    for seat_id, state in runtime.items():
        state["seat_id"] = seat_id
        state["is_human"] = human_id in seat_id
    db.set_game_state(conn, session_id, "runtime", runtime)

    user_index = db.get_game_state(conn, session_id, "user_input_index", 0)
    public_facts = set(db.get_game_state(conn, session_id, "revealed_clues", []))
    names = {v: k for k, v in _name_map(onto).items()}
    seat_names = {p["id"]: p["seat_label"] for p in session_doc["participants"]}
    # The host reads the whole script: each seat receives its own private
    # goals / hidden conflicts.  These are behavioural directions, never new
    # facts and never part of the public narration.
    directions = gm_direction.private_directions(onto)
    # Optional provider injection: lets an external driver supply an agent-backed
    # provider without registering a new llm_name. Default path is unchanged.
    speaker = speaker if speaker is not None else llm.build_provider(llm_name)
    # GM narrator is optional and independent of the seat provider.  When no
    # agent is supplied, narration falls back to deterministic scene sketches.
    gm_agent = gm_agent if gm_agent is not None else speaker
    judge = guard_engine.make_judge(speaker) if speaker.name != "template" else None
    llm_stats = {"provider": speaker.name, "generated": 0, "fallback": 0}
    guard_stats = {"input_wrapped": 0, "output_rewritten": 0, "output_flagged": 0, "judge_calls": 0}
    log = []

    current_round = db.get_game_state(conn, session_id, "round", 0)
    # F22 needs the planned session length, not the current round; otherwise the
    # target heat is always 3.0 and the pacing controller never rests.
    planned_total = max(1, int(rounds))
    if not finalize:
        # A stepped session passes ``rounds=current_round``; keep the previously
        # declared plan so F22 pacing and driver resume still see the full length.
        planned_total = max(planned_total, int(
            db.get_game_state(conn, session_id, "total_rounds") or planned_total
        ))
    db.set_game_state(conn, session_id, "total_rounds", planned_total)
    # G22: one breakout token per session by default; G21's macro_quota can
    # raise the cap when it is present in the stored session metadata.
    session_dials = director.from_session(session_doc) or {}
    quota = session_dials.get("macro_quota") or (session_doc.get("l0") or {}).get("macro_quota")
    breakout_tokens = db.get_game_state(conn, session_id, "breakout_tokens")
    if not isinstance(breakout_tokens, dict):
        breakout_tokens = breakout.tokens(quota)
        journal.set_state(
            conn, session_id, "breakout_tokens", breakout_tokens,
            reason="g22:init", actor="gm:host",
        )
    first_round = start_round if start_round is not None else current_round + 1
    for round_no in range(first_round, rounds + 1):
        prior_suppressed = [
            e for e in db.visible_game_events(conn, session_id, "gm", round_no - 1)
            if e["act_type"] == "suppressed_burst"
        ]
        suppressed_before = len(prior_suppressed)
        # G05: build the perceiver's L4 view before anyone speaks this round.
        # Only this projection reaches other seats; L1/L3 truth stays private.
        perceived = l4.project_table(runtime, session_id, round_no, seed=seed)
        for audit in l4.guard_audits(perceived):
            db.append_game_event(
                conn, session_id, round_no, "gm:l4", "l4_guard", audit, ["gm"]
            )
        for seat_id, state in runtime.items():
            seen = db.visible_game_events(conn, session_id, seat_id, round_no - 1)
            parsed = []
            for e in seen:
                payload = json.loads(e["payload_json"])
                if e["actor"] != seat_id and payload.get("utterance"):
                    wrapped = guard_engine.guard_input(state["character_id"], payload["utterance"])
                    if wrapped.findings:
                        payload["utterance"] = wrapped.text
                        guard_stats["input_wrapped"] += 1
                parsed.append({
                    "actor": e["actor"], "act_type": e["act_type"], "payload": payload,
                    "seq": e["seq"], "visibility_json": e["visibility_json"],
                })
            round_changes = []
            for ev in parsed:
                _, changes = _appraise(state, ev, round_no)
                round_changes.extend(changes)
            for hit in state.pop("trauma_hits", []):
                db.append_game_event(
                    conn, session_id, round_no, "gm:host", "trauma_trigger",
                    {"seat": seat_id, **hit}, ["gm:host", seat_id],
                )
                log.append({"round": round_no, "actor": "gm:host", "act": "trauma_trigger",
                            "seat": seat_id, "multiplier": hit["multiplier"]})
            round_changes.extend(_update_suspicion(state, parsed))
            ips.accumulate(state, parsed, round_no, session_id=session_id, seed=seed)
            acc = state.get("accumulator")
            current_I = ips.intensity(acc, state, parsed, round_no) if acc else 0.0
            transition = breakout.advance(
                state, round_no, acc, current_I, suppressed_before,
                breakout_tokens, observers=[s for s in all_seats if s != seat_id],
            )
            if transition:
                if transition.get("to") == "frenzy":
                    journal.set_state(
                        conn, session_id, "breakout_tokens", breakout_tokens,
                        reason=f"g22:frenzy@{seat_id}", actor=seat_id,
                    )
                db.append_game_event(
                    conn, session_id, round_no, seat_id, "breakout_state",
                    transition, ["gm"],
                )
                log.append({"round": round_no, "actor": seat_id, "act": "breakout_state",
                            "from": transition.get("from"), "to": transition.get("to")})
                for change in transition.get("changes", []):
                    _log_changes(
                        conn, session_id, round_no, seat_id, state["character_id"], [change]
                    )
            for change in lie.update_from_events(state, parsed):
                log.append({
                    "round": round_no, "actor": seat_id, "act": "lie_revision",
                    "lie_id": change["lie_id"], "trigger": change["trigger"],
                    "status": change["status"], "strategy": change["strategy"],
                    "before": change["before"], "after": change["after"],
                })
            observations = []
            for ev in parsed:
                observation = memory.observe(
                    state, ev, round_no, audience=_audience_for(conn, ev, all_seats)
                )
                if not observation:
                    continue
                observations.append(observation)
                conflict_seq = _emit_conflict(
                    conn, session_id, round_no, seat_id, observation.get("conflict"),
                )
                if conflict_seq:
                    log.append({
                        "round": round_no, "actor": seat_id, "act": "statement_conflict",
                        "detail": observation["conflict"]["detail"],
                        "strength": observation["conflict"]["strength"],
                    })
                for change in observation["commitment_changes"]:
                    log.append({
                        "round": round_no, "actor": seat_id, "act": "commitment",
                        "witness": seat_id, "fact": change["fact"],
                        "from": change["from"], "to": change["to"],
                    })
            conflicts = contradiction.detect(observations, state, round_no)
            for item in conflicts:
                db.append_game_event(
                    conn, session_id, round_no, "gm:host", "contradiction_event",
                    item, ["gm:host", seat_id],
                )
                log.append({"round": round_no, "actor": "gm:host", "act": "contradiction_event",
                            "witness": seat_id, "subject": item.get("subject"),
                            "strength": item["strength"]})
            round_changes.extend(contradiction.apply(state, conflicts, round_no))
            faction_shift = faction.update(
                state, round_no, onto, char_by_id.get(state["character_id"]) or {}
            )
            if faction_shift:
                db.append_game_event(
                    conn, session_id, round_no, "gm:host", "faction_shift",
                    faction_shift, ["gm:host", seat_id],
                )
                log.append({"round": round_no, "actor": "gm:host", "act": "faction_shift",
                            "seat": seat_id, "from": faction_shift["from"],
                            "to": faction_shift["to"]})
            _log_changes(conn, session_id, round_no, seat_id, state["character_id"], round_changes)
            recalled = []
            pending = contradiction.pending_for_round(state, round_no)
            if state["is_human"]:
                raw = _user_act(scripted, user_index, names.get(state["character_id"], state["character_id"]))
                user_index += 1
                if raw["claimed_facts"]:
                    state["beliefs"].update({f: {"belief": 0.8, "source": "user", "evidence": []} for f in raw["claimed_facts"]})
                act = raw
                recalled = memory.recall(state, round_no, cue=act.get("utterance"), limit=3)
            else:
                intent = _choose_intent(state, rng)
                target_id = max(state["suspicion"], key=state["suspicion"].get) if state["suspicion"] else None
                # G10: an unresolved contradiction from an earlier round is
                # raised first.  It only changes which already-known conflict
                # is voiced; it never adds a fact or invents a quote.
                if pending and pending.get("subject") in state["suspicion"]:
                    intent = "accuse"
                    target_id = pending["subject"]
                target_name = seat_names.get(target_id, target_id) or "在座各位"
                # Display text only: strip engine internals such as
                # ``|logLR:x=1.5`` before a clue reaches the seat prompt.  The
                # raw fact ids remain in `allowed` for claim validation.
                clue = infoguard.display_text(next(iter(public_facts), "现有线索"))
                act = {
                    "act_type": intent,
                    "intent": intent,
                    "round": round_no,
                    "utterance": _utter(intent, target_name, clue),
                    "claimed_facts": [clue] if intent == "assert" and clue in state["beliefs"] else [],
                    "target": target_id,
                    "inner_intent": f"意图={intent}；怀疑最高={target_name}",
                    "emotion_shown": "平静",
                    "lie_id": None,
                }
                if pending and pending.get("subject") in state["suspicion"]:
                    act["utterance"] = f"{target_name}，你前后的说法有矛盾。"
                    act["claimed_facts"] = []
                    act["inner_intent"] = "优先抛出已记录的矛盾"
                    contradiction.mark_expressed(state, pending)
                if lie.should_lie(state, intent):
                    entry = lie.create(
                        state, round_no, act["utterance"],
                        audience=[s for s in all_seats if s != seat_id], intent=intent,
                    )
                    act["lie_id"] = entry["lie_id"]
                recalled = memory.recall(
                    state, round_no, cue=f"{intent} {target_name} {clue}", limit=3
                )
                allowed = sorted(set(public_facts) | set(state["beliefs"]))
                # Speech-style engine: read-only persona -> 80/20 style prompt.
                # Runs for every AI seat/round so the drift ledger is complete
                # even when the offline template provider is used.
                style_parts, style_ctx = _style_for_seat(
                    conn, session_id, seat_id, state, round_no, seed,
                    target_id, target_name,
                    [infoguard.display_text(f) for f in allowed],
                    session_doc["script_id"],
                )
                act["style"] = {
                    "drift": style_parts["drift_record"],
                    "structure_ratio": style_parts["structure_ratio"],
                    "structure_ok": style_parts["structure_ok"],
                    "missing": style_parts["missing"],
                    "forced_baseline": style_parts["forced_baseline"],
                    "triggers": style_parts["triggers"],
                }
                if speaker.name != "template":
                    # Compile keyword payload -> coherent style directive.  The
                    # compiler is fail-closed: bad output falls back to the
                    # deterministic prompt; nothing here touches facts/numbers.
                    compiled = style_compiler.compile_prompt(
                        style_parts,
                        style_parts.get("fingerprint")
                        or speech_style.extract_fingerprint(dict(state), style_ctx),
                        style_ctx, speaker, db.StyleCompileCache(conn),
                    )
                    style_parts = compiled["parts"]
                    act["style"]["compile"] = compiled["report"]
                    db.append_style_compile(
                        conn, session_id, round_no, seat_id, state["character_id"],
                        compiled["report"],
                    )
                    db.append_game_event(
                        conn, session_id, round_no, seat_id, "style_compile",
                        compiled["report"], ["gm"],
                    )
                    log.append({
                        "round": round_no, "actor": seat_id, "act": "style_compile",
                        **compiled["report"],
                    })
                # G29: killer-only framing guidance.  The engine computes
                # observable cracks and known fragments; the killer agent
                # authors the actual words.  Never injected into other seats.
                frame_guide = None
                if seat_id == frame_seat:
                    frame_guide = frame.build_guide(
                        conn, session_id, onto, session_doc, state, round_no
                    )
                if frame_guide and not state["is_human"]:
                    state["frame_guide"] = frame_guide["lines"]
                else:
                    state.pop("frame_guide", None)
                if speaker.name != "template":
                    scope = guard_engine.scope_for(state["character_id"])
                    known_lines = (
                        [f"{fid}: {guard_engine.fact_text(fid)}" for fid in sorted(scope.known_fact_ids)]
                        if scope else []
                    )
                    # Performance guides: the bridge from formula-owned intent,
                    # suspicion and emotion to wording only.  Never touches
                    # facts, targets or numbers; sanitised twice (here + block).
                    guides = performance_guide.build(state, intent, target_id)
                    # GM input side: stage/atmosphere guides from the host's
                    # read of the room.  Wording only, never facts or numbers.
                    if gm_agent is not None:
                        guides += gm_narrator.build_stage_guides(
                            conn, session_id, round_no, rounds)
                    act["performance_guides"] = guides
                    prompt = llm.build_prompt(
                        state, intent, target_name, clue, round_no,
                        [infoguard.display_text(f) for f in allowed], seat_names,
                        known_fact_lines=known_lines,
                        recalled_lines=memory.recall_lines(recalled, seat_names),
                        perceived=perceived.get(seat_id, {}),
                        style_parts=style_parts,
                        guides=guides,
                    )
                    dir_lines = directions.get(state["character_id"]) or []
                    prompt = gm_direction.inject_seat(prompt, dir_lines)
                    act["gm_direction"] = list(dir_lines)
                    if dir_lines and round_no == 1:
                        db.append_game_event(
                            conn, session_id, round_no, seat_id, "gm_direction",
                            {"lines": dir_lines}, ["gm"],
                        )
                    try:
                        # Let an injected provider know which seat/round this
                        # speech prompt belongs to (harmless no-op otherwise).
                        if hasattr(speaker, "bind"):
                            speaker.bind(round_no, seat_id, intent)
                        generated = llm.parse_speech(speaker.generate(prompt), allowed, intent)
                        # Mid gate: post-generation cosine proxy audit.
                        audit = speech_style.audit(generated["utterance"],
                                                   speech_style.extract_fingerprint(
                                                       dict(state), style_ctx),
                                                   speech_style.DriftRecord(
                                                       **{k: style_parts["drift_record"][k]
                                                          for k in speech_style.SIGNALS}))
                        act["style"]["audit"] = audit
                        if audit["regenerate_hint"]:
                            log.append({"round": round_no, "actor": seat_id,
                                        "act": "style_regenerate_hint",
                                        "hint": audit["regenerate_hint"]})
                        act["utterance"] = generated["utterance"]
                        act["emotion_shown"] = generated["emotion_shown"]
                        act["claimed_facts"] = generated["claimed_facts"]
                        llm_stats["generated"] += 1
                    except llm.LLMError as exc:
                        llm_stats["fallback"] += 1
                        log.append({"round": round_no, "actor": seat_id, "act": "llm_fallback", "reason": str(exc)})
            act = _guard(state, act, public_facts)
            # The human player may claim anything; guarding their speech would
            # break legitimate gameplay. Only generated AI speech is checked.
            findings = [] if state["is_human"] else guard_engine.check_output(state["character_id"], act["utterance"])
            verdicts = []
            if findings and judge is not None:
                guard_stats["judge_calls"] += 1
                try:
                    verdicts = judge.adjudicate(state["character_id"], act["utterance"], findings)
                except Exception:
                    verdicts = [
                        infoguard.AdjudicatedLeak(f, "confirmed", "judge error (fail-safe)")
                        for f in findings
                    ]
            if verdicts:
                hard = [v for v in verdicts if v.effective_severity == "hard"]
                confirmed = [v for v in verdicts if v.verdict == "confirmed"]
            else:
                hard = [f for f in findings if f.severity == "hard"]
                confirmed = []
            guard_info = None
            if findings:
                ids = sorted({
                    (v.finding.fact_id if hasattr(v, "finding") else v.fact_id)
                    for v in (verdicts or findings)
                })
                leaks = [
                    {
                        "fact_id": v.finding.fact_id if hasattr(v, "finding") else v.fact_id,
                        "severity": v.effective_severity if hasattr(v, "effective_severity") else v.severity,
                    }
                    for v in (verdicts or findings)
                ]
                if hard or confirmed:
                    act["utterance"] = infoguard.REWRITE_UTTERANCE
                    act["act_type"] = "deflect"
                    act["claimed_facts"] = []
                    guard_stats["output_rewritten"] += 1
                    action = "rewrite"
                else:
                    guard_stats["output_flagged"] += 1
                    action = "flag"
                guard_info = {"action": action, "facts": ids, "leaks": leaks}
            stage_report = stage_gate.guard_output(
                conn, session_id, onto, act, round_no, public_facts,
            )
            if stage_report:
                guard_stats["stage_gate_rewritten"] = guard_stats.get("stage_gate_rewritten", 0) + 1
                guard_info = {**(guard_info or {}), "stage_gate": stage_report}
                log.append({"round": round_no, "actor": "gm:host", "act": "stage_gate",
                            "status": "rewrite", "hits": stage_report["hits"]})
            lock_state = state.setdefault(
                "script_lock_state", script_locks.seat_state(session_doc, seat_id)
            )
            lock_report = script_locks.guard_output(lock_state, act, round_no)
            if lock_report["blocked"]:
                guard_stats["script_lock_blocked"] = guard_stats.get("script_lock_blocked", 0) + 1
                guard_info = {**(guard_info or {}), "script_lock": {"status": "blocked"}}
                db.append_game_event(
                    conn, session_id, round_no, "gm:host", "script_lock_violation",
                    {"seat": seat_id, "hits": [
                        {"lock_id": hit["lock_id"]} for hit in lock_report["hits"]
                    ]},
                    ["gm:host", seat_id],
                )
                log.append({"round": round_no, "actor": "gm:host", "act": "script_lock",
                            "status": "blocked", "seat": seat_id})
            satisfied_locks = script_locks.observe_output(lock_state, act, round_no)
            if satisfied_locks:
                log.append({"round": round_no, "actor": "gm:host", "act": "script_lock",
                            "status": "satisfied", "ids": [
                                item["lock_id"] for item in satisfied_locks
                            ]})
            challenge = stage_gate.consume_challenge(
                conn, session_id, round_no, seat_id, act,
            )
            if challenge:
                log.append({"round": round_no, "actor": seat_id, "act": "challenge_token",
                            "action": challenge["action"], "before": challenge["before"],
                            "after": challenge["after"]})
            # IPS P0: expression only.  Delivery never feeds intent selection,
            # claimed facts, beliefs or TruthGraph (IR1).
            delivery = ips.micro_perturb(state, session_id, round_no, seed=seed)
            state["delivery"] = delivery
            payload = {
                "utterance": act["utterance"],
                "inner_intent": act["inner_intent"],
                "claimed_facts": act["claimed_facts"],
                "target": act["target"],
                "emotion_shown": act.get("emotion_shown", "平静"),
                "discrete_emotion": state["emotion"].get("discrete"),
                "faction": state.get("faction"),
                "guard": guard_info,
                "recalled": _recall_payload(recalled),
                "lie_id": act.get("lie_id"),
                "delivery": delivery,
                "style": act.get("style"),
            }
            # Speech-style drift ledger: always written for AI seats, with the
            # final guarded utterance audited against the anchor proxy.
            if "style" in act:
                # The R2 cosine gate applies to LLM-generated text only.  The
                # offline template provider is not a persona attempt, so a
                # zero-overlap score would be noise, not an audit result.
                _audit = act["style"].get("audit")
                db.append_drift_record(
                    conn, session_id, round_no, seat_id, state["character_id"],
                    act["style"]["drift"], target=style_ctx.get("target_name") or "",
                    anchor_cos=(_audit or {}).get("anchor_cos_proxy"),
                    outcome="pass" if (_audit is None or _audit["pass"]) else "regenerate_hint",
                    triggers=act["style"]["triggers"],
                    structure_ratio=act["style"]["structure_ratio"],
                    forced_baseline=act["style"]["forced_baseline"],
                )
                log.append({"round": round_no, "actor": seat_id, "act": "style_drift",
                            "drift_total": act["style"]["drift"]["total"],
                            "anchor_cos": (_audit or {}).get("anchor_cos_proxy"),
                            "cosine_kind": "skipped_template" if _audit is None else "bigram_proxy",
                            "structure_ok": act["style"]["structure_ok"]})
            seq = db.append_game_event(
                conn, session_id, round_no, seat_id, act["act_type"], payload, ["public"]
            )
            # G24: the speaker ignites somebody else; IPS state is written only
            # on the target.  Human seats may ignite but never carry IPS.
            target_seat = act.get("target")
            ignition = _provocation_from_act(act) if target_seat in runtime else None
            if ignition:
                kind, s_k = ignition
                applied = ips.apply_provocation(
                    runtime[target_seat], seat_id, kind, s_k, round_no, seq
                )
                if applied:
                    db.append_game_event(
                        conn, session_id, round_no, seat_id, "provocation",
                        {**applied, "target": target_seat}, ["public"],
                    )
                    log.append({"round": round_no, "actor": seat_id, "act": "provocation",
                                "target": target_seat, "kind": kind, "s_k": s_k})
            own_observation = memory.observe(
                state,
                {
                    "seq": seq, "actor": seat_id, "act_type": act["act_type"],
                    "payload": payload, "is_self": True,
                },
                round_no,
                audience=[s for s in all_seats if s != seat_id],
            )
            if own_observation:
                own_conflict = own_observation.get("conflict")
                if own_conflict and not any(
                    e["payload"].get("witness") == seat_id
                    and e["payload"].get("seq") == own_conflict["seq"]
                    for e in journal.audit_events(
                        conn, session_id, act_type="statement_conflict"
                    ) if isinstance(e.get("payload"), dict)
                ):
                    _emit_conflict(conn, session_id, round_no, seat_id, own_conflict)
                    log.append({
                        "round": round_no, "actor": seat_id, "act": "statement_conflict",
                        "detail": own_conflict["detail"], "strength": own_conflict["strength"],
                    })
                for change in own_observation["commitment_changes"]:
                    log.append({
                        "round": round_no, "actor": seat_id, "act": "commitment",
                        "witness": seat_id, "fact": change["fact"],
                        "from": change["from"], "to": change["to"],
                    })
            log.append({"round": round_no, "actor": seat_id, "act": act["act_type"], "utterance": act["utterance"]})
            if guard_info:
                log.append({"round": round_no, "actor": seat_id, "act": "infoguard", "result": guard_info})
        db.set_game_state(conn, session_id, "user_input_index", user_index)
        # G18: decide overt improvisation once per round.  The highest-scoring
        # eligible seat wins a token; everyone else keeps their charge and is
        # logged.  Round is a proxy for "act" until the stage machine (G06).
        budget = ips.ImprovBudget(db.get_game_state(conn, session_id, "improv_budget", {}))
        budget.macro_in_act = 0
        round_events = []
        for row in db.visible_game_events(conn, session_id, "gm"):
            if row["round"] != round_no:
                continue
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, ValueError):
                continue
            round_events.append({
                "seq": row["seq"], "round": row["round"], "actor": row["actor"],
                "act_type": row["act_type"], "payload": payload,
            })
        n_accuse = sum(1 for e in round_events if e["act_type"] == "accuse")
        n_contra = sum(1 for e in round_events if e["act_type"] == "contradiction")
        mean_arousal = sum(
            s["emotion"]["arousal"] for s in runtime.values()
        ) / max(1, len(runtime))
        burst_heat = formulas.actual_heat(n_accuse, n_contra, mean_arousal)
        # G20: rationalize aftermath raises narrative heat carried across rounds.
        burst_heat += float(db.get_game_state(
            conn, session_id, "ips_heat_adjustment", 0.0
        ) or 0.0)
        burst_target = formulas.heat_target(round_no, max(1, int(rounds)), 3.0)
        bursts = ips.resolve_bursts(
            runtime, round_no, round_events, burst_heat, burst_target, budget,
            public_facts, session_id, seed, onto=onto,
            locks=script_locks.compile_locks(onto),
        )
        for change in bursts["changes"]:
            seat = change.get("seat_id")
            if seat not in runtime:
                continue
            relations = runtime[seat].setdefault("relations", {})
            rel = relations.setdefault(change["target"], dict(REL_DEFAULT))
            rel[change["dim"]] = change["after"]
            _log_changes(conn, session_id, round_no, seat, change.get("character_id"), [change])
        for burst in bursts["bursts"]:
            # G20: settle the aftermath into existing ledgers (lie/memory/relations)
            # before the event is stored so the audit record carries the effects.
            settled = aftermath.resolve(
                runtime, burst, round_no, seed=seed, budget=budget, session_id=session_id
            )
            for change in settled["changes"]:
                seat = change.get("seat_id")
                if seat not in runtime:
                    continue
                _log_changes(conn, session_id, round_no, seat, change.get("character_id"), [change])
            if settled["heat_delta"]:
                heat_adj = float(db.get_game_state(
                    conn, session_id, "ips_heat_adjustment", 0.0
                ) or 0.0) + settled["heat_delta"]
                db.set_game_state(conn, session_id, "ips_heat_adjustment", round(heat_adj, 4))
            db.append_game_event(
                conn, session_id, round_no, burst["seat"], "improv_burst", burst, ["public"]
            )
            log.append({"round": round_no, "actor": burst["seat"], "act": "improv_burst",
                        "tier": burst["tier"], "strategy": burst["aftermath"]["strategy"],
                        "cascade": len(settled["cascade"])})
        for suppressed in bursts["suppressed"]:
            db.append_game_event(
                conn, session_id, round_no, "gm:host", "suppressed_burst", suppressed, ["gm:host"]
            )
        db.set_game_state(conn, session_id, "improv_budget", bursts["budget"])
        seat_map = {
            p["script_character_id"]: p["id"]
            for p in session_doc["participants"] if p.get("script_character_id")
        }
        gm_decision = None
        if gm_policy is not None:
            gm_decision = gm_policy(conn, session_id, round_no, onto, runtime)
            db.append_game_event(
                conn, session_id, round_no, "gm:host", "gm_decision",
                gm_decision, ["gm:host"],
            )
            log.append({"round": round_no, "actor": "gm:host", "act": "gm_decision",
                        "policy": gm_decision.get("policy"), "release_clue": gm_decision.get("release_clue")})
        gm_result = _gm_step(
            conn, session_id, round_no, onto, runtime, guard_engine, seat_map,
            decision=gm_decision,
        )
        for change_seat, change_char, change in gm_result.pop("changes", []):
            if change_seat:
                _log_changes(conn, session_id, round_no, change_seat, change_char, [change])
        log.append({"round": round_no, "actor": "gm:host", "act": "pacing", "result": gm_result})

        # GM narrator (output side): one scene per round.  A detected stampede
        # is wording guidance inside this narration, never a second host
        # utterance or a separate redirect event.
        if gm_agent is not None and getattr(gm_agent, "name", "") != "template":
            voice_fp = gm_narrator.pick_voice_fp(
                conn, session_id, runtime, gm_voice, round_no, seed=seed)
            drift = gm_narrator.detect_drift(conn, session_id, round_no)
            report = gm_narrator.narrate(
                conn, session_id, round_no, gm_agent, rounds, seat_names,
                voice_fp=voice_fp, drift=drift)
            db.append_game_event(
                conn, session_id, round_no, "gm:host", "gm_narration", report, ["public"],
            )
            log.append({"round": round_no, "actor": "gm:host", "act": "gm_narration",
                        "status": report["status"], "narration": report.get("narration"),
                        "drift": sorted(drift)})

        timeline.update_session(
            conn, session_id, onto, round_no,
            {
                p["id"]: p["script_character_id"]
                for p in session_doc["participants"] if p.get("script_character_id")
            },
            runtime_state=runtime,
        )
        for snap_seat, snap_state in runtime.items():
            db.save_snapshot(conn, session_id, round_no, snap_seat, snap_state)
        db.save_snapshot(conn, session_id, round_no, "@global", {
            "revealed_clues": db.get_game_state(conn, session_id, "revealed_clues", []),
            "user_input_index": user_index,
            "round": round_no,
            "total_rounds": db.get_game_state(conn, session_id, "total_rounds", rounds),
        })
        db.set_game_state(conn, session_id, "round", round_no)

    db.set_game_state(conn, session_id, "runtime", runtime)
    db.set_game_state(conn, session_id, "llm_stats", llm_stats)
    db.set_game_state(conn, session_id, "infoguard_stats", guard_stats)
    result = {}
    settled_scoring = {}
    settled_locks = {}
    if finalize:
        result = _vote(conn, session_id, runtime, session_doc, scripted, seed)
        db.set_game_state(conn, session_id, "result", result)
        # GM narrator: host the vote reveal.  The winner/solved flags come from
        # the deterministic tally; the agent only supplies atmosphere and wording.
        if gm_agent is not None and getattr(gm_agent, "name", "") != "template":
            phase = "tie" if result.get("tie") else "reveal"
            voice_fp = gm_narrator.pick_voice_fp(
                conn, session_id, runtime, gm_voice, rounds, seed=seed)
            vote_prompt = gm_narrator.build_vote_prompt(
                conn, session_id, phase, result, seat_names, voice_fp=voice_fp)
            try:
                vote_report = gm_narrator.parse_narration(
                    gm_agent.generate(vote_prompt), vote_prompt,
                    gm_narrator.allowed_quotes(conn, session_id, rounds),
                    forbidden_terms=gm_narrator.hidden_truth_terms(conn, session_id),
                    required_anchors=gm_narrator.case_anchors(conn, session_id))
            except Exception as exc:
                vote_report = {"status": "fallback", "reason": f"ERR_GM_VOTE: {exc}"}
            vote_report["phase"] = phase
            db.append_game_event(
                conn, session_id, rounds, "gm:host", "gm_vote_narration", vote_report, ["public"],
            )
            log.append({"round": rounds, "actor": "gm:host", "act": "gm_vote_narration",
                        "status": vote_report["status"], "narration": vote_report.get("narration")})
        # G08: settle endings, personal goals and F27/player scores from stored
        # events.  No LLM judges a goal and private goals are written only to
        # their own seat plus the host audit view.
        settled_scoring = scoring.settle(conn, session_id)
        log.append({"round": rounds, "actor": "gm:host", "act": "ending_settled",
                    "status": settled_scoring["ending"]["code"]})
        # G09: R4 locks are checked against the full event trail at settlement.
        # Unmet locks stay red; they are never silently promoted to satisfied.
        settled_locks = script_locks.audit(conn, session_id)
        log.append({"round": rounds, "actor": "gm:host", "act": "script_lock_audit",
                    "status": settled_locks["status"], "counts": settled_locks["counts"]})
        # G23: settle cross-session sentiment/outcome after the vote is final.
        settled = longterm.settle_session(
            conn, session_id, player_id=(session_doc.get("l0") or {}).get("player_id")
        )
        db.append_game_event(
            conn, session_id, max(1, int(rounds)), "gm:host", "longterm_settled",
            {"entries": [{
                "owner": e["owner_character_id"], "target": e["target_id"],
                "kind": e["target_kind"], "sentiment": e["sentiment"],
                "outcome": e["outcome"],
            } for e in settled]},
            ["gm:host"],
        )
    return {
        "session_id": session_id, "rounds": rounds, "log": log, "result": result,
        "llm": llm_stats, "infoguard": guard_stats, "scoring": settled_scoring,
        "script_locks": settled_locks,
    }
