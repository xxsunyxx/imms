"""Dual-track timeline: objective truth vs each character's narrated track.

The truth track is derived only from ``timeline_true``; narrated tracks come
from each character's own ``timeline`` and public time-stamped statements.  A
narrated event is aligned to a truth event only when both carry a parseable
time point close enough together *and* share enough wording.  Therefore:

* `alibi_gap` is a number computed from real coverage, never a guess;
* an inversion is reported only when two aligned events appear in the opposite
  order in a narrated track -- unstamped or merely similar lines are ignored.

Everything in this module is host-side: the report is stored in GM-only state
and events, and never enters a seat prompt.
"""

import json

from . import db, memory, solver

MATCH_TOLERANCE = 30
MIN_TOKEN_OVERLAP = 0.15


def _tokens(text):
    return memory._tokens(text)


def _item(source, text, actor=None, seq=None):
    text = str(text or "").strip()
    return {
        "source": source,
        "actor": actor,
        "seq": seq,
        "text": text,
        "minute": solver._time_point(text),
        "tokens": sorted(_tokens(text)),
    }


def build_tracks(onto, events=None, seat_to_character=None):
    """Build structured truth/narrated tracks from script + public speech."""
    truth = [
        _item("truth", line)
        for line in ((onto or {}).get("timeline_true") or [])
        if str(line or "").strip()
    ]
    narrated = {}
    for char in (onto or {}).get("characters") or []:
        cid = char.get("id")
        if not cid:
            continue
        narrated[cid] = [
            _item("script", line, actor=cid)
            for line in (char.get("timeline") or [])
            if str(line or "").strip()
        ]

    mapping = seat_to_character or {}
    for event in events or []:
        actor = event.get("actor")
        char_id = mapping.get(actor)
        if not char_id:
            continue
        try:
            vis = event.get("visibility")
            if vis is None and "visibility_json" in event:
                vis = json.loads(event["visibility_json"])
            payload = event.get("payload")
            if payload is None and "payload_json" in event:
                payload = json.loads(event["payload_json"])
        except (TypeError, ValueError):
            continue
        if vis is not None and "public" not in (vis or []):
            continue
        utterance = str((payload or {}).get("utterance") or "").strip()
        if not utterance:
            continue
        narrated.setdefault(char_id, []).append(
            _item("speech", utterance, actor=actor, seq=event.get("seq"))
        )
    return {"truth": truth, "narrated": narrated}


def _overlap(a, b):
    left, right = set(a), set(b)
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def _match(truth, item):
    if item["minute"] is None:
        return None, 0.0
    best, best_score = None, 0.0
    for index, event in enumerate(truth):
        if event["minute"] is None:
            continue
        distance = abs(event["minute"] - item["minute"])
        if distance > MATCH_TOLERANCE:
            continue
        similarity = _overlap(event["tokens"], item["tokens"])
        if similarity < MIN_TOKEN_OVERLAP and distance != 0:
            continue
        score = similarity + 1.0 / (1.0 + distance)
        if score > best_score:
            best, best_score = index, score
    return best, round(best_score, 4)


def audit(tracks):
    """Return coverage gaps and only time-backed, aligned inversions."""
    truth = tracks.get("truth") or []
    stamped = [i for i, event in enumerate(truth) if event["minute"] is not None]
    matches = {}
    inversions = []
    gaps = {}
    for char_id, items in (tracks.get("narrated") or {}).items():
        char_matches = []
        for index, item in enumerate(items):
            truth_index, score = _match(truth, item)
            if truth_index is not None:
                char_matches.append({
                    "narrated_index": index, "truth_index": truth_index,
                    "score": score, "text": item["text"],
                })
        matches[char_id] = char_matches
        covered = {m["truth_index"] for m in char_matches}
        total = len(stamped)
        missing = [i for i in stamped if i not in covered]
        gaps[char_id] = {
            "gap": round(1.0 - len(covered) / total, 4) if total else 0.0,
            "covered": len(covered), "total": total,
            "missing_truth_indexes": missing,
            "basis": "timeline_true" if total else "no_stamped_truth",
        }
        ordered = sorted(char_matches, key=lambda m: m["narrated_index"])
        for earlier, later in zip(ordered, ordered[1:]):
            t1, t2 = earlier["truth_index"], later["truth_index"]
            if t1 > t2:
                inversions.append({
                    "kind": "narrated_inversion", "character_id": char_id,
                    "detail": "证词中的两个时间点与真相顺序相反",
                    "truth_indexes": [t1, t2],
                    "texts": [earlier["text"], later["text"]],
                    "strength": round(min(earlier["score"], later["score"]) / 2.0, 4),
                })
    return {"matches": matches, "inversions": inversions, "gaps": gaps}


def update_session(conn, session_id, onto, round_no=0, seat_to_character=None,
                   runtime_state=None):
    """Recompute GM-only timeline report and expose gaps as exposure risk."""
    rows = db.visible_game_events(conn, session_id, "gm")
    events = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            continue
        events.append({
            "seq": row["seq"], "actor": row["actor"],
            "payload": payload, "visibility": json.loads(row["visibility_json"]),
        })
    tracks = build_tracks(onto, events, seat_to_character)
    report = audit(tracks)
    report["round"] = int(round_no)
    report["truth_events"] = len(tracks["truth"])
    db.set_game_state(
        conn, session_id, "timeline", report,
        reason="g07:timeline_update", actor="gm:host",
    )
    db.append_game_event(
        conn, session_id, int(round_no), "gm:host", "timeline_audit",
        {
            "round": int(round_no), "truth_events": len(tracks["truth"]),
            "inversions": report["inversions"],
            "gaps": {
                cid: {"gap": item["gap"], "covered": item["covered"], "total": item["total"]}
                for cid, item in report["gaps"].items()
            },
        },
        ["gm:host"],
    )
    runtime = runtime_state if runtime_state is not None else (
        db.get_game_state(conn, session_id, "runtime", {}) or {}
    )
    changed = False
    for seat_id, state in runtime.items():
        char_id = state.get("character_id")
        gap = (report["gaps"].get(char_id) or {}).get("gap")
        if gap is None:
            continue
        emotion = state.setdefault("emotion", {})
        before = float(emotion.get("exposure_risk", 0.0))
        after = round(max(before, 0.1 + 0.3 * float(gap)), 4)
        if after != before:
            emotion["exposure_risk"] = after
            changed = True
    if changed:
        db.set_game_state(
            conn, session_id, "runtime", runtime,
            reason="g07:alibi_gap", actor="gm:host",
        )
    return report
