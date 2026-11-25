"""Headless runtime: GM pacing + character agents consuming IMMS runtime formulas."""

import json
import random
import re

from . import db, formulas, llm

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
INTENTS = ["assert", "ask", "accuse", "defend", "ally", "deflect", "confess"]


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
    persona = db.get_persona_l1(conn, script_id)
    l1map = {p["character_id"]: {a["key"]: a["value"] for a in p["attributes"] if a["value"] is not None} for p in persona["personas"]}

    seats = [p for p in session_doc["participants"] if p["kind"] != "gm"]
    others = [p["id"] for p in seats]
    runtime = {}
    for p in seats:
        cid = p["script_character_id"]
        attrs = l1map.get(cid, {})
        N = attrs.get("OCEAN.N", 0.5)
        runtime[p["id"]] = {
            "character_id": cid,
            "l1": attrs,
            "l0": p.get("l0", {}),
            "relations": {o: dict(REL_DEFAULT) for o in others if o != p["id"]},
            "emotion": {
                "valence": 0.0, "arousal": 0.2, "dominance": 0.5,
                "stress": 0.1, "cognitive_load": 0.1, "intoxication": 0.0,
                "morale": 0.8, "exposure_risk": 0.1,
            },
            "nu": formulas.nu(N),
            "ell": {o: 0.0 for o in others if o != p["id"]},
            "suspicion": {o: 0.1 for o in others if o != p["id"]},
            "faction": None,
            "beliefs": {},
            "lie_ledger": [],
            "memory": [],
        }
    for clue in onto.get("clue_pool", []):
        for state in runtime.values():
            state["beliefs"][clue] = {"belief": 0.6, "source": "public", "is_true": True}

    db.set_game_state(conn, session_id, "runtime", runtime)
    db.set_game_state(conn, session_id, "round", 0)
    db.set_game_state(conn, session_id, "revealed_clues", [])
    db.set_game_state(conn, session_id, "user_input_index", 0)


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
    emo = state["emotion"]
    impact = {"valence": -0.15 if coeff < 0 else 0.10, "arousal": 0.12, "dominance": 0.05}
    state["emotion"] = formulas.pad_update(emo, 2.0, 1.0, impact)
    state["emotion"].update({k: v for k, v in emo.items() if k not in formulas.PAD_DOMAIN})
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
        data = {
            "key": ch["dim"], "value": after, "layer": "L3",
            "scope": "dyadic", "target": ch.get("target"),
            "visibility": "self", "source": "runtime:formula",
            "locked": False, "reason": "事件评价 F02/F12", "updated_at_round": round_no,
            "updated_by": seat_id,
        }
        conn.execute(
            """INSERT INTO attribute_events(session_id, round, actor, layer, character_id, key, target,
                                            before_json, after_json, reason, created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                session_id, round_no, "runtime:formula", "L3", character_id, ch["dim"],
                ch.get("target") or "", json.dumps({"value": before}, ensure_ascii=False),
                json.dumps(data, ensure_ascii=False), "事件评价 F02/F12", db.now(),
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
    }
    return templates.get(intent, "...")


def _guard(state, act, public_facts):
    claims = [f for f in act["claimed_facts"] if f in state["beliefs"] or f in public_facts]
    act["claimed_facts"] = claims
    for lie in state["lie_ledger"]:
        if lie.get("heat", 0) > 0.3 and any(c in lie.get("claimed_facts", []) for c in act["claimed_facts"]):
            if "记不清" not in act["utterance"]:
                act["utterance"] = "这件事我记不清了。"
                act["act_type"] = "deflect"
    return act


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
        }
    return {"act_type": "assert", "utterance": item, "claimed_facts": [], "target": None, "inner_intent": "用户自行决定"}


def _gm_step(conn, session_id, round_no, onto, runtime):
    revealed = db.get_game_state(conn, session_id, "revealed_clues", [])
    events = db.visible_game_events(conn, session_id, "gm")
    n_accuse = sum(1 for e in events if e["round"] == round_no and e["act_type"] == "accuse")
    n_contra = sum(1 for e in events if e["round"] == round_no and e["act_type"] == "contradiction")
    mean_arousal = sum(s["emotion"]["arousal"] for s in runtime.values()) / max(1, len(runtime))
    heat = formulas.actual_heat(n_accuse, n_contra, mean_arousal)
    target = formulas.heat_target(round_no, max(1, round_no), 3.0)
    injected = None
    gm_changes = []
    if heat < target - 0.3:
        pool = [c for c in onto.get("clue_pool", []) if c not in revealed]
        if pool:
            clue = pool[0]
            revealed.append(clue)
            db.set_game_state(conn, session_id, "revealed_clues", revealed)
            injected = {"type": "clue_release", "clue": clue}
            match = re.search(r"logLR:(\w+)=([-\d.]+)", clue)
            if match:
                target_seat = f"ai:{match.group(1)}"
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
    return {"heat": round(heat, 4), "target": round(target, 4), "injected": injected, "changes": gm_changes}


def _vote(conn, session_id, runtime, session_doc, scripted):
    onto = db.get_script(conn, session_doc["script_id"])
    name_map = _name_map(onto)
    culprit_id = onto.get("evaluation", {}).get("killer_id")
    if not culprit_id:
        culprit_name = None
        for fact in onto.get("truth_graph", []):
            for name in name_map:
                if name in fact:
                    culprit_name = name
                    break
            if culprit_name:
                break
        culprit_id = name_map.get(culprit_name, culprit_name)
    seat_ids = set(runtime.keys())

    def to_seat(value):
        if value in seat_ids:
            return value
        for prefix in ("ai:", "human:"):
            if prefix + str(value) in seat_ids:
                return prefix + str(value)
        return value

    culprit_id = to_seat(culprit_id)
    tally = {}
    for seat_id, state in runtime.items():
        if state.get("is_human"):
            if scripted and scripted.get("vote"):
                choice = scripted["vote"]
            else:
                choice = input("[用户席位] 投给谁（角色 id）：")
        else:
            dist = formulas.vote_distribution(state["ell"], 1.0)
            choice = max(dist, key=dist.get) if dist else None
        choice = to_seat(choice)
        tally[choice] = tally.get(choice, 0) + 1
    winner = max(tally, key=tally.get) if tally else None
    return {"culprit": culprit_id, "tally": tally, "winner": winner, "solved": winner == culprit_id}


def run_session(conn, session_id, rounds=3, scripted=None, seed=42, llm_name=None, start_round=None):
    session_doc = db.get_session(conn, session_id)
    init_runtime(conn, session_doc)
    onto = db.get_script(conn, session_doc["script_id"])
    runtime = db.get_game_state(conn, session_id, "runtime")
    rng = random.Random(seed)
    human_id = session_doc["human_character_id"]
    for seat_id, state in runtime.items():
        state["seat_id"] = seat_id
        state["is_human"] = human_id in seat_id
    db.set_game_state(conn, session_id, "runtime", runtime)

    user_index = db.get_game_state(conn, session_id, "user_input_index", 0)
    public_facts = set(db.get_game_state(conn, session_id, "revealed_clues", []))
    names = {v: k for k, v in _name_map(onto).items()}
    seat_names = {p["id"]: p["seat_label"] for p in session_doc["participants"]}
    speaker = llm.build_provider(llm_name)
    llm_stats = {"provider": speaker.name, "generated": 0, "fallback": 0}
    log = []

    current_round = db.get_game_state(conn, session_id, "round", 0)
    first_round = start_round if start_round is not None else current_round + 1
    for round_no in range(first_round, rounds + 1):
        for seat_id, state in runtime.items():
            seen = db.visible_game_events(conn, session_id, seat_id, round_no - 1)
            parsed = [
                {"actor": e["actor"], "act_type": e["act_type"], "payload": json.loads(e["payload_json"])}
                for e in seen
            ]
            round_changes = []
            for ev in parsed:
                _, changes = _appraise(state, ev, round_no)
                round_changes.extend(changes)
            round_changes.extend(_update_suspicion(state, parsed))
            _log_changes(conn, session_id, round_no, seat_id, state["character_id"], round_changes)
            if state["is_human"]:
                raw = _user_act(scripted, user_index, names.get(state["character_id"], state["character_id"]))
                user_index += 1
                if raw["claimed_facts"]:
                    state["beliefs"].update({f: {"belief": 0.8, "source": "user", "is_true": True} for f in raw["claimed_facts"]})
                act = raw
            else:
                intent = _choose_intent(state, rng)
                target_id = max(state["suspicion"], key=state["suspicion"].get) if state["suspicion"] else None
                target_name = seat_names.get(target_id, target_id) or "在座各位"
                clue = next(iter(public_facts), "现有线索")
                act = {
                    "act_type": intent,
                    "utterance": _utter(intent, target_name, clue),
                    "claimed_facts": [clue] if intent == "assert" and clue in state["beliefs"] else [],
                    "target": target_id,
                    "inner_intent": f"意图={intent}；怀疑最高={target_name}",
                    "emotion_shown": "平静",
                }
                if speaker.name != "template":
                    allowed = sorted(set(public_facts) | set(state["beliefs"]))
                    prompt = llm.build_prompt(state, intent, target_name, clue, round_no, allowed, seat_names)
                    try:
                        generated = llm.parse_speech(speaker.generate(prompt), allowed, intent)
                        act["utterance"] = generated["utterance"]
                        act["emotion_shown"] = generated["emotion_shown"]
                        act["claimed_facts"] = generated["claimed_facts"]
                        llm_stats["generated"] += 1
                    except llm.LLMError as exc:
                        llm_stats["fallback"] += 1
                        log.append({"round": round_no, "actor": seat_id, "act": "llm_fallback", "reason": str(exc)})
            act = _guard(state, act, public_facts)
            payload = {
                "utterance": act["utterance"],
                "inner_intent": act["inner_intent"],
                "claimed_facts": act["claimed_facts"],
                "target": act["target"],
                "emotion_shown": act.get("emotion_shown", "平静"),
            }
            db.append_game_event(conn, session_id, round_no, seat_id, act["act_type"], payload, ["public"])
            state["memory"].append({"round": round_no, "act": act["act_type"], "importance": 0.5})
            log.append({"round": round_no, "actor": seat_id, "act": act["act_type"], "utterance": act["utterance"]})
        db.set_game_state(conn, session_id, "user_input_index", user_index)
        gm_result = _gm_step(conn, session_id, round_no, onto, runtime)
        for change_seat, change_char, change in gm_result.pop("changes", []):
            if change_seat:
                _log_changes(conn, session_id, round_no, change_seat, change_char, [change])
        log.append({"round": round_no, "actor": "gm:host", "act": "pacing", "result": gm_result})

        for snap_seat, snap_state in runtime.items():
            db.save_snapshot(conn, session_id, round_no, snap_seat, snap_state)
        db.save_snapshot(conn, session_id, round_no, "@global", {
            "revealed_clues": db.get_game_state(conn, session_id, "revealed_clues", []),
            "user_input_index": user_index,
            "round": round_no,
        })
        db.set_game_state(conn, session_id, "round", round_no)

    db.set_game_state(conn, session_id, "runtime", runtime)
    db.set_game_state(conn, session_id, "llm_stats", llm_stats)
    result = _vote(conn, session_id, runtime, session_doc, scripted)
    db.set_game_state(conn, session_id, "result", result)
    return {"session_id": session_id, "rounds": rounds, "log": log, "result": result, "llm": llm_stats}
