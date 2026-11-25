"""Assemble GM + N-1 AI character agents + one human seat from parsed script and L1 cards."""

from .db import get_persona_l1, get_script, save_session


def assemble(conn, script_id, human_character_id, difficulty=3):
    onto = get_script(conn, script_id)
    personas = get_persona_l1(conn, script_id)
    chars = {c["id"]: c for c in onto.get("characters", [])}

    if human_character_id not in chars:
        raise ValueError("ERR_NO_HUMAN_SEAT")
    selectable = onto.get("world", {}).get("player_selectable", "")
    if selectable and human_character_id not in selectable.split(","):
        raise ValueError("ERR_CHAR_NOT_SELECTABLE")
    playable = {cid: ch for cid, ch in chars.items() if not ch.get("is_victim")}
    if len(playable) < 3:
        raise ValueError("ERR_CHAR_COUNT")
    if not onto.get("truth_graph"):
        raise ValueError("ERR_TRUTH_INCONSISTENT")

    session_id = f"{script_id}-s001"
    participants = [{
        "id": f"gm:{session_id}",
        "kind": "gm",
        "seat_label": "主持人",
        "persona_ref": None,
        "visibility": "all",
    }]

    for cid, ch in playable.items():
        is_human = cid == human_character_id
        participants.append({
            "id": f"{'human' if is_human else 'ai'}:{cid}",
            "kind": "human_character" if is_human else "ai_character",
            "script_character_id": cid,
            "seat_label": ch.get("name", cid),
            "persona_ref": f"persona_l1:{script_id}:{cid}",
            "l0": {
                "difficulty": difficulty,
                "acting_skill": 0.5,
                "lying_skill": 0.5,
                "play_style": "steady",
            },
            "initial_l3": {},
            "script_locks": [],
            "visibility": "self",
        })

    session_doc = {
        "session_id": session_id,
        "script_id": script_id,
        "human_character_id": human_character_id,
        "persona_count": len(personas["personas"]),
        "ontology": f"scripts:{script_id}",
        "participants": participants,
        "visibility_matrix": {
            f"gm:{session_id}": ["all"],
            **{p["id"]: ["self", "public", "faction"] for p in participants if p["kind"] != "gm"},
        },
    }
    save_session(conn, session_doc)
    return session_doc
