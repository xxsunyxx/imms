"""Unified read/write gateway for session state and event history.

Every ``game_state`` / ``game_events`` access should go through this module so
that:

* state writes are traceable (before/after + actor + reason in
  ``state_change_audit``);
* event reads share one visibility/redaction rule;
* replay/retro audit views get decoded payloads in deterministic ``seq`` order.

The legacy helpers in :mod:`imms.db` remain as thin delegating wrappers, so
existing CLI behaviour is unchanged.
"""

import json
from datetime import datetime, timezone

PRIVATE_PAYLOAD_FIELDS = ("inner_intent", "lie_id", "recalled", "guard", "memory_gap")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# session state
# ---------------------------------------------------------------------------

def get_state(conn, session_id, key, default=None):
    row = conn.execute(
        "SELECT value_json FROM game_state WHERE session_id=? AND key=?", (session_id, key)
    ).fetchone()
    return json.loads(row["value_json"]) if row else default


def set_state(conn, session_id, key, value, reason="", actor="system"):
    """Write one state key and append an immutable before/after audit entry."""
    before = get_state(conn, session_id, key)
    conn.execute(
        "INSERT OR REPLACE INTO game_state(session_id, key, value_json) VALUES(?,?,?)",
        (session_id, key, json.dumps(value, ensure_ascii=False)),
    )
    conn.execute(
        """INSERT INTO state_change_audit(session_id, key, before_json, after_json, reason, actor, created_at)
           VALUES(?,?,?,?,?,?,?)""",
        (
            session_id,
            key,
            json.dumps(before, ensure_ascii=False) if before is not None else None,
            json.dumps(value, ensure_ascii=False),
            reason or "",
            actor or "system",
            now(),
        ),
    )
    conn.commit()
    return value


def state_keys(conn, session_id):
    rows = conn.execute(
        "SELECT key FROM game_state WHERE session_id=? ORDER BY key", (session_id,)
    ).fetchall()
    return [r["key"] for r in rows]


def state_snapshot(conn, session_id):
    """Deterministic snapshot of all session state keys (key order is stable)."""
    return {key: get_state(conn, session_id, key) for key in state_keys(conn, session_id)}


def state_audit(conn, session_id, since=0):
    rows = conn.execute(
        """SELECT seq, session_id, key, before_json, after_json, reason, actor, created_at
           FROM state_change_audit WHERE session_id=? AND seq>=? ORDER BY seq""",
        (session_id, since),
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        for field in ("before_json", "after_json"):
            raw = item[field]
            item[field] = json.loads(raw) if raw is not None else None
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# game events
# ---------------------------------------------------------------------------

def append_event(conn, session_id, round_no, actor, act_type, payload, visibility):
    conn.execute(
        """INSERT INTO game_events(session_id, round, actor, act_type, payload_json, visibility_json, created_at)
           VALUES(?,?,?,?,?,?,?)""",
        (
            session_id,
            round_no,
            actor,
            act_type,
            json.dumps(payload, ensure_ascii=False),
            json.dumps(visibility, ensure_ascii=False),
            now(),
        ),
    )
    conn.commit()
    return conn.execute("SELECT MAX(seq) AS s FROM game_events WHERE session_id=?", (session_id,)).fetchone()["s"]


def _redact_private(payload, seat_id, actor):
    """Strip speaker-private fields before an event reaches another seat.

    A lie ledger belongs to its speaker alone.  Broadcasting ``lie_id`` would
    mark an utterance as a lie to the whole table, and ``inner_intent`` /
    ``recalled`` would expose thoughts never spoken.  GM and the speaker keep
    the full payload; everyone else sees only what could be observed.  Stored
    rows are untouched, so replay/retro retain the full audit record.
    """
    if seat_id == "gm" or seat_id.startswith("gm:"):
        return payload
    if seat_id == actor:
        return payload
    return {k: v for k, v in payload.items() if k not in PRIVATE_PAYLOAD_FIELDS}


def visible_events(conn, session_id, seat_id, round_no=None):
    """Seat-filtered events, returned in the legacy (JSON-string) row shape."""
    sql = "SELECT * FROM game_events WHERE session_id=?"
    params = [session_id]
    if round_no is not None:
        sql += " AND round<=?"
        params.append(round_no)
    sql += " ORDER BY seq"
    room_cache = {}

    def in_room(token):
        room_id = token[len("room:"):]
        if room_id not in room_cache:
            row = conn.execute(
                "SELECT participants_json FROM private_rooms WHERE room_id=?", (room_id,)
            ).fetchone()
            room_cache[room_id] = json.loads(row["participants_json"]) if row else []
        return seat_id in room_cache[room_id]

    rows = []
    for row in conn.execute(sql, params):
        vis = json.loads(row["visibility_json"])
        if (
            "public" in vis
            or seat_id in vis
            or row["actor"] == seat_id
            or seat_id.startswith("gm:")
            or any(isinstance(t, str) and t.startswith("room:") and in_room(t) for t in vis)
        ):
            item = dict(row)
            payload = json.loads(item["payload_json"])
            if isinstance(payload, dict):
                item["payload_json"] = json.dumps(
                    _redact_private(payload, seat_id, item["actor"]), ensure_ascii=False
                )
            rows.append(item)
    return rows


def audit_events(conn, session_id, round_no=None, act_type=None, actor=None):
    """Full audit view: decoded payload/visibility in deterministic seq order.

    Unlike :func:`visible_events` this does not redact private fields.  It is
    for replay, retro, GM branch comparison and other host-side auditing.
    """
    sql = "SELECT * FROM game_events WHERE session_id=?"
    params = [session_id]
    if round_no is not None:
        sql += " AND round<=?"
        params.append(round_no)
    if act_type is not None:
        sql += " AND act_type=?"
        params.append(act_type)
    if actor is not None:
        sql += " AND actor=?"
        params.append(actor)
    sql += " ORDER BY seq"
    out = []
    for row in conn.execute(sql, params):
        item = dict(row)
        item["payload"] = json.loads(item["payload_json"])
        item["visibility"] = json.loads(item["visibility_json"])
        out.append(item)
    return out
