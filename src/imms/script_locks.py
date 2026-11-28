"""R4 script locks: must-say / must-trigger / taboo hard checks.

Locks are compiled only from explicit script fields (``script_locks``,
``must_say``, ``must_trigger``, ``taboo`` on a character or at script level).
Prose such as ``plot_locks`` stays a stage gate and is never reinterpreted here.
The compiled rules live on the session document so forks inherit exactly the
same rules; no branch-specific bypass is exposed.

The output guard blocks taboo speech before it is written to the event log.
Unmet must-say / must-trigger locks are reported red at settlement instead of
being silently treated as satisfied.  Personal lock text is never written to
public events or to the public replay section.
"""

import re

from . import db, journal, solver

LOCK_TYPES = ("must_say", "must_trigger", "taboo")
ALIASES = {
    "必须说": "must_say", "必说": "must_say",
    "必须触发": "must_trigger", "必经桥段": "must_trigger",
    "禁止说": "taboo", "禁忌": "taboo", "禁词": "taboo",
}
KEY_SPLIT = re.compile(r"[:：]")
TEXT_KEYS = ("utterance", "claimed_facts", "fact_id", "clue_id", "title", "detail", "trigger")


def _as_list(value):
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _normalize(raw, owner, index):
    """Return one explicit lock, or a diagnostic ``ignored`` entry."""
    typ, value, weight, stage = "", "", None, None
    if isinstance(raw, dict):
        for key in LOCK_TYPES:
            if key in raw:
                typ, value = key, raw[key]
                break
        if not typ:
            typ = str(raw.get("type") or "").strip()
            value = raw.get("value", raw.get("phrase", raw.get("text", "")))
        weight = raw.get("weight")
        stage = raw.get("stage")
    elif isinstance(raw, str):
        text = raw.strip()
        if KEY_SPLIT.search(text):
            head, value = KEY_SPLIT.split(text, maxsplit=1)
            typ = ALIASES.get(head.strip(), head.strip())
            value = value.strip()
        # A bare string in ``script_locks`` is ambiguous: do not guess its type.
    if typ in ALIASES:
        typ = ALIASES[typ]
    if typ not in LOCK_TYPES or not str(value or "").strip():
        return {
            "id": f"{owner}:ignored:{index}", "type": "ignored", "value": "",
            "owner": owner, "reason": "missing_type_or_value",
        }
    entry = {
        "id": f"{owner}:{typ}:{index}",
        "type": typ, "value": str(value).strip(), "owner": owner,
        "weight": float(weight) if weight not in (None, "") else None,
        "stage": int(stage) if stage not in (None, "") else None,
    }
    return entry


def compile_locks(onto):
    """Compile declared locks; absent fields produce no locks."""
    locks, seen = [], set()
    raw_global = onto.get("script_locks") or []
    raws = [("*", item) for item in _as_list(raw_global)]
    for char in onto.get("characters") or []:
        owner = char.get("id")
        if not owner:
            continue
        raws.append((owner, char.get("script_locks")))
        for key in LOCK_TYPES:
            for item in _as_list(char.get(key)):
                raws.append((owner, {key: item}))
    for owner, raw in raws:
        for index, item in enumerate(_as_list(raw)):
            lock = _normalize(item, owner or "*", index)
            signature = (lock["owner"], lock["type"], lock["value"], lock["id"])
            if signature in seen:
                continue
            seen.add(signature)
            locks.append(lock)
    return locks


def install(conn, session_doc, onto):
    """Write compiled rules to each seat (self + GM visibility only)."""
    locks = compile_locks(onto)
    changed = False
    for participant in session_doc.get("participants") or []:
        if participant.get("kind") == "gm":
            continue
        cid = participant.get("script_character_id")
        assigned = [dict(item) for item in locks if item["owner"] in ("*", cid)]
        if participant.get("script_locks") != assigned:
            participant["script_locks"] = assigned
            changed = True
    if changed:
        db.save_session(conn, session_doc)
    return locks


def ensure_session(conn, session_id, onto, session_doc=None):
    """Idempotent install for older/forked sessions assembled before G09."""
    session_doc = session_doc or db.get_session(conn, session_id)
    if not session_doc:
        return []
    return install(conn, session_doc, onto)


def seat_state(session_doc, seat_id):
    """Per-seat runtime lock state; stored inside snapshots for forks."""
    locks = []
    for participant in session_doc.get("participants") or []:
        if participant.get("id") == seat_id:
            locks = participant.get("script_locks") or []
            break
    return {"locks": locks, "satisfied": {}, "violations": []}


def _matches(term, text):
    term = str(term or "").strip()
    return len(term) >= 2 and term in str(text or "")


def guard_output(state, act, round_no):
    """Block taboo content before it reaches the event log (G3 / IR4)."""
    hits = []
    text = " ".join([
        str(act.get("utterance") or ""),
        " ".join(str(x) for x in (act.get("claimed_facts") or [])),
    ])
    for lock in state.get("locks") or []:
        if lock.get("type") == "taboo" and _matches(lock.get("value"), text):
            hits.append({"lock_id": lock["id"], "value": lock["value"]})
    if not hits:
        return {"blocked": False, "hits": []}
    act["utterance"] = "这件事现在不能说。"
    act["act_type"] = "deflect"
    act["intent"] = "deflect"
    act["claimed_facts"] = []
    act["lie_id"] = None
    state.setdefault("violations", []).append({
        "round": int(round_no), "hits": hits, "act_type": "deflect",
    })
    return {"blocked": True, "hits": hits}


def observe_output(state, act, round_no):
    """Mark explicit must-say / must-trigger locks satisfied by one act."""
    text = " ".join([
        str(act.get("utterance") or ""),
        " ".join(str(x) for x in (act.get("claimed_facts") or [])),
    ])
    newly = []
    for lock in state.get("locks") or []:
        if lock.get("type") not in ("must_say", "must_trigger"):
            continue
        if lock["id"] in state.setdefault("satisfied", {}):
            continue
        if _matches(lock.get("value"), text):
            state["satisfied"][lock["id"]] = int(round_no)
            newly.append({"lock_id": lock["id"], "type": lock["type"], "round": int(round_no)})
    return newly


def _payload_text(payload):
    bits = []
    for key in TEXT_KEYS:
        value = payload.get(key)
        if isinstance(value, (list, tuple)):
            bits.extend(str(v) for v in value)
        elif value is not None:
            bits.append(str(value))
    return " ".join(bits)


def audit(conn, session_id):
    """Final R4 audit.  Returns counts and red flags, never lock text."""
    session_doc = db.get_session(conn, session_id) or {}
    onto = db.get_script(conn, session_doc.get("script_id")) or {}
    locks = compile_locks(onto)
    participants = [p for p in session_doc.get("participants") or [] if p.get("kind") != "gm"]
    seats = {
        p.get("script_character_id"): p["id"] for p in participants
        if p.get("script_character_id")
    }
    names = {c.get("id"): c.get("name", c.get("id")) for c in onto.get("characters") or []}
    events = journal.audit_events(conn, session_id)
    revealed = " ".join(str(x) for x in (
        db.get_game_state(conn, session_id, "revealed_clues", []) or []
    ))
    results = []
    for lock in locks:
        owner = lock["owner"]
        target_seats = [seats[owner]] if owner in seats else (
            [p["id"] for p in participants] if owner == "*" else []
        )
        seen = False
        violated = False
        for event in events:
            payload = event.get("payload") or {}
            if lock["type"] == "taboo":
                if event.get("act_type") == "script_lock_violation" and \
                        any(h.get("lock_id") == lock["id"] for h in payload.get("hits") or []):
                    violated = True
                elif not target_seats or event.get("actor") in target_seats:
                    if _matches(lock["value"], _payload_text(payload)):
                        violated = True
                continue
            if not target_seats or event.get("actor") in target_seats:
                text = _payload_text(payload)
                if lock["type"] == "must_say":
                    text = str(payload.get("utterance") or "")
                if _matches(lock["value"], text):
                    seen = True
                elif _matches(lock["value"], revealed):
                    seen = True
        if lock["type"] == "taboo":
            status = "violated" if violated else "pass"
        else:
            status = "satisfied" if seen else "untriggered"
        results.append({
            "lock_id": lock["id"], "type": lock["type"],
            "owner": names.get(owner, "全体角色" if owner == "*" else owner),
            "status": status, "weight": lock.get("weight"),
        })
    failed = [item for item in results if item["status"] in ("violated", "untriggered")]
    report = {
        "status": "fail" if failed else "pass",
        "counts": {
            "total": len(results),
            "satisfied": sum(1 for x in results if x["status"] == "satisfied"),
            "untriggered": sum(1 for x in results if x["status"] == "untriggered"),
            "violated": sum(1 for x in results if x["status"] == "violated"),
        },
        "failed": failed,
        "locks": results,
    }
    db.set_game_state(conn, session_id, "script_locks", report,
                      reason="R4 final audit", actor="gm:host")
    return report


def guard_evidence(onto, burst):
    """IR3: downgrade destruction/forgery of a unique evidence route."""
    action = burst.get("evidence_action") or {}
    kind = str(action.get("kind") or "")
    if kind not in ("destroy_clue", "forge_clue"):
        return {"status": "n/a_no_evidence_action"}
    clue_id = action.get("clue_id")
    clues = [c for c in onto.get("clues") or [] if c.get("id") == clue_id]
    fids = []
    for clue in clues:
        fids.extend(clue.get("reveals_fact_ids") or [])
    fids.extend((onto.get("clue_fact_map") or {}).get(clue_id) or [])
    unique = [fid for fid in sorted(set(fids)) if solver.is_unique_path(onto, fid)]
    if not unique:
        return {"status": "pass", "checked_facts": sorted(set(fids))}
    downgrade = "damaged_recoverable" if kind == "destroy_clue" else "forgery_failed"
    return {
        "status": "downgraded", "downgrade": downgrade,
        "unique_facts": unique, "checked_facts": sorted(set(fids)),
    }


def guard_improv(locks, burst, state=None):
    """IR4: no improv may break an explicit lock or a >0.8 core goal."""
    locks = locks or []
    state = state or {}
    owner = state.get("character_id")
    if owner:
        locks = [lock for lock in locks if lock.get("owner") in ("*", owner)]
    claims = (burst.get("speech_act") or {}).get("claimed_facts") or []
    text = " ".join(str(x) for x in claims)
    reasons = []
    for lock in locks:
        if lock.get("type") == "taboo" and _matches(lock.get("value"), text):
            reasons.append("taboo_claim")
        weight = lock.get("weight")
        if lock.get("type") == "must_trigger" and weight is not None and float(weight) > 0.8:
            action = burst.get("evidence_action") or {}
            if action.get("kind") in ("destroy_clue", "forge_clue") and \
                    _matches(lock.get("value"), _payload_text(action)):
                reasons.append("core_goal_destroyed")
    morale = float((state.get("emotion") or {}).get("morale", 1.0))
    has_token = bool((burst.get("guard") or {}).get("gm_token")) or \
        bool(state.get("gm_improv_token"))
    if not reasons or (morale < 0.15 and has_token):
        return {"status": "pass", "reasons": reasons}
    burst["action_key"] = "safe_library"
    burst["speech_act"]["claimed_facts"] = []
    return {"status": "downgraded", "downgrade": "substitute_safe_improv", "reasons": reasons}
