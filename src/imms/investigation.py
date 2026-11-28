"""Investigation phase: action points, clue cards and private rooms.

Real 剧本杀 gives each player limited action points to search a place/person,
and lets the finder decide whether a clue goes on the table or stays hidden.
This module owns that deterministic part.  The GM agent never decides *what*
a clue says -- clue text comes straight from the stored ontology; the GM only
narrates the phase in wording.

Visibility contract:

* a public clue is appended to ``revealed_clues`` and exempted through
  InfoGuard, so every seat may use the facts it reveals;
* a hidden clue is stored as a private ``clue_found`` event addressed only to
  its finder.  It does NOT enter ``revealed_clues``, so no other seat's prompt,
  the GM narration or the replay transcript can see it;
* private chat reuses the existing room mechanism (``room:<id>`` visibility).
"""

import random

from . import db, stage_gate


def init_investigation(conn, session_id, onto, seats, points=2, seed=42):
    """Initialise per-seat action points and the clue-holder ledger.

    Clues with stage 0 are available in the first investigation phase; later
    stages stay locked until a later phase opens them.  Nothing here reveals
    a clue -- it only records what *could* be found and by whom.
    """
    rng = random.Random(seed)
    clues = list(onto.get("clues") or [])
    cards = []
    for clue in clues:
        if not isinstance(clue, dict):
            continue
        location = str(clue.get("location") or "")
        cards.append({
            "id": clue.get("id"), "title": clue.get("title") or clue.get("id"),
            "stage": int(clue.get("stage") or 0), "location": location,
            "reveals_fact_ids": list(clue.get("reveals_fact_ids") or []),
            "holder": None, "visibility": "undiscovered",
        })
    state = {
        "action_points": {seat: points for seat in seats},
        "cards": cards,
        "seed": seed,
        "log": [],
    }
    db.set_game_state(conn, session_id, "investigation", state)
    return state


def available_targets(onto, seat_id=None):
    """Locations a seat may search.  A seat may never search itself."""
    targets = []
    for clue in onto.get("clues") or []:
        if not isinstance(clue, dict):
            continue
        place = str(clue.get("location") or "").strip()
        if place and place not in targets:
            targets.append(place)
    if seat_id:
        targets = [t for t in targets if t != seat_id]
    return targets


def search(conn, session_id, seat_id, target, onto, make_public=True, seed=None):
    """Spend one action point to search ``target``; return the found card.

    The clue itself is read from the stored ontology.  ``make_public`` is the
    finder's decision (from scripted input or a deterministic policy), not a
    GM invention.  A hidden card is written only to the finder's visibility;
    a public card is also appended to ``revealed_clues`` so InfoGuard unlocks
    the facts it establishes for the whole table.
    """
    state = db.get_game_state(conn, session_id, "investigation", {}) or {}
    points = state.get("action_points") or {}
    if seat_id not in points:
        raise ValueError("ERR_SEAT_NOT_IN_INVESTIGATION")
    if int(points.get(seat_id, 0)) <= 0:
        raise ValueError("ERR_NO_ACTION_POINTS")
    if target not in available_targets(onto, seat_id):
        raise ValueError("ERR_INVALID_SEARCH_TARGET")
    if not stage_gate.private_allowed(conn, session_id):
        raise ValueError("ERR_PRIVATE_WINDOW_CLOSED")
    cards = state.get("cards") or []
    current_round = int(db.get_game_state(conn, session_id, "round", 0) or 0)
    candidates = [
        card for card in cards
        if card.get("holder") is None and card.get("location") == target
        and int(card.get("stage") or 0) <= current_round
    ]
    if not candidates:
        raise ValueError("ERR_NO_CLUE_AT_TARGET")
    rng = random.Random(seed if seed is not None else state.get("seed", 42))
    card = candidates[rng.randrange(len(candidates))]
    card["holder"] = seat_id
    card["visibility"] = "public" if make_public else "hidden"
    points[seat_id] = int(points[seat_id]) - 1
    db.set_game_state(conn, session_id, "investigation", state)

    clue_line = _clue_line(onto, card["id"])
    payload = {
        "seat": seat_id, "target": target, "card_id": card["id"],
        "title": card["title"], "visibility": card["visibility"],
    }
    event_vis = ["public"] if make_public else [seat_id]
    if make_public:
        revealed = list(db.get_game_state(conn, session_id, "revealed_clues", []) or [])
        if clue_line and clue_line not in revealed:
            revealed.append(clue_line)
        db.set_game_state(conn, session_id, "revealed_clues", revealed)
        payload["clue"] = clue_line
        runtime = db.get_game_state(conn, session_id, "runtime", {}) or {}
        for seat_state in runtime.values():
            for fid in card["reveals_fact_ids"]:
                seat_state.setdefault("beliefs", {})[fid] = {
                    "belief": 0.7, "source": "clue", "evidence": [card["id"]],
                }
        db.set_game_state(conn, session_id, "runtime", runtime)
    seq = db.append_game_event(conn, session_id, current_round, seat_id, "clue_found", payload, event_vis)
    db.append_game_event(conn, session_id, current_round, seat_id, "action_point", {
        "resource": "ap", "seat": seat_id, "target": target,
        "before": int(points.get(seat_id, 0)) + 1, "after": int(points.get(seat_id, 0)),
        "card_id": card["id"],
    }, ["gm:host"])
    state["log"].append({"seq": seq, "seat": seat_id, "card_id": card["id"],
                         "visibility": payload["visibility"]})
    db.set_game_state(conn, session_id, "investigation", state)
    return {**payload, "seq": seq}


def _clue_line(onto, clue_id):
    """Resolve the display string used by revealed_clues / clue_fact_map."""
    for line in onto.get("clue_pool") or []:
        if str(line).startswith(f"{clue_id} "):
            return line
    return clue_id


def private_chat(conn, session_doc, room_id, seat_id, target, utterance, claimed_facts=None):
    """Speak inside an existing private room; only members can see it."""
    return db.private_say(conn, session_doc["session_id"], room_id, seat_id, utterance,
                          target=target, claimed_facts=claimed_facts or [])


def open_private_chat(conn, session_doc, round_no, seats):
    if not stage_gate.private_allowed(conn, session_doc["session_id"]):
        raise ValueError("ERR_PRIVATE_WINDOW_CLOSED")
    return db.open_private_room(conn, session_doc, round_no, seats)
