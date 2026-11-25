"""SQLite persistence for scripts, L1/L2/L3 attributes, sessions and audit events."""

import json
import sqlite3
from datetime import datetime, timezone

from .util import write_json

SCHEMA = """
CREATE TABLE IF NOT EXISTS scripts (
    script_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    ontology_json TEXT NOT NULL,
    source_files_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS persona_l1 (
    script_id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    key TEXT NOT NULL,
    extraction_version INTEGER NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (script_id, character_id, key, extraction_version)
);

CREATE TRIGGER IF NOT EXISTS persona_l1_no_update
BEFORE UPDATE ON persona_l1
BEGIN
    SELECT RAISE(ABORT, 'ERR_L1_IMMUTABLE');
END;

CREATE TRIGGER IF NOT EXISTS persona_l1_no_delete
BEFORE DELETE ON persona_l1
BEGIN
    SELECT RAISE(ABORT, 'ERR_L1_IMMUTABLE');
END;

CREATE TABLE IF NOT EXISTS persona_l2 (
    session_id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    key TEXT NOT NULL,
    data_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (session_id, character_id, key)
);

CREATE TABLE IF NOT EXISTS persona_l3 (
    session_id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    key TEXT NOT NULL,
    data_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (session_id, character_id, scope, target, key)
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    script_id TEXT NOT NULL,
    human_character_id TEXT NOT NULL,
    roster_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attribute_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    round INT NOT NULL,
    actor TEXT NOT NULL,
    layer TEXT NOT NULL,
    character_id TEXT NOT NULL,
    key TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    before_json TEXT,
    after_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS state_snapshots (
    session_id TEXT NOT NULL,
    round INT NOT NULL,
    agent_id TEXT NOT NULL,
    data_json TEXT NOT NULL,
    PRIMARY KEY (session_id, round, agent_id)
);

CREATE TABLE IF NOT EXISTS private_rooms (
    room_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    round INT NOT NULL,
    participants_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    opened_seq INT,
    closed_seq INT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS game_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    round INT NOT NULL,
    actor TEXT NOT NULL,
    act_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    visibility_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS game_state (
    session_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    PRIMARY KEY (session_id, key)
);

CREATE TABLE IF NOT EXISTS gm_branches (
    branch_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    parent_id TEXT NOT NULL DEFAULT 'root',
    fork_round INT NOT NULL,
    role TEXT NOT NULL DEFAULT 'branch',
    status TEXT NOT NULL DEFAULT 'active',
    label TEXT NOT NULL DEFAULT '',
    decision_json TEXT NOT NULL DEFAULT '{}',
    report_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_gm_branches_session ON gm_branches(session_id);

CREATE INDEX IF NOT EXISTS idx_l1_lookup ON persona_l1(script_id, character_id, extraction_version);
CREATE INDEX IF NOT EXISTS idx_events_session ON attribute_events(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_game_events_session ON game_events(session_id, seq);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def save_script(conn, onto):
    conn.execute(
        "INSERT OR REPLACE INTO scripts(script_id, title, ontology_json, source_files_json, created_at) VALUES(?,?,?,?,?)",
        (
            onto["script_id"],
            onto.get("title", onto["script_id"]),
            json.dumps(onto, ensure_ascii=False),
            json.dumps(onto.get("source_files", []), ensure_ascii=False),
            now(),
        ),
    )
    conn.commit()


def get_script(conn, script_id):
    row = conn.execute("SELECT ontology_json FROM scripts WHERE script_id=?", (script_id,)).fetchone()
    if row is None:
        raise ValueError("ERR_SCRIPT_NOT_FOUND")
    return json.loads(row["ontology_json"])


def list_scripts(conn):
    return [dict(r) for r in conn.execute("SELECT script_id, title, created_at FROM scripts ORDER BY created_at")]


def latest_version(conn, script_id):
    row = conn.execute(
        "SELECT MAX(extraction_version) AS v FROM persona_l1 WHERE script_id=?", (script_id,)
    ).fetchone()
    return (row["v"] or 0) + 1


def save_persona_l1(conn, script_id, persona_doc):
    version = latest_version(conn, script_id)
    created = now()
    for persona in persona_doc["personas"]:
        for attr in persona["attributes"]:
            conn.execute(
                "INSERT INTO persona_l1(script_id, character_id, key, extraction_version, data_json, created_at) VALUES(?,?,?,?,?,?)",
                (
                    script_id,
                    persona["character_id"],
                    attr["key"],
                    version,
                    json.dumps(attr, ensure_ascii=False),
                    created,
                ),
            )
    conn.commit()
    return version


def get_persona_l1(conn, script_id):
    rows = conn.execute(
        """
        SELECT p.character_id, p.data_json
        FROM persona_l1 p
        JOIN (SELECT script_id, character_id, MAX(extraction_version) AS v
              FROM persona_l1 WHERE script_id=? GROUP BY script_id, character_id) m
          ON p.script_id=m.script_id AND p.character_id=m.character_id AND p.extraction_version=m.v
        WHERE p.script_id=?
        """,
        (script_id, script_id),
    ).fetchall()
    grouped = {}
    for row in rows:
        grouped.setdefault(row["character_id"], []).append(json.loads(row["data_json"]))
    if not grouped:
        raise ValueError("ERR_PERSONA_NOT_FOUND")
    return {"script_id": script_id, "personas": [
        {"character_id": cid, "name": cid, "layer": "L1", "attributes": attrs}
        for cid, attrs in grouped.items()
    ]}


def save_session(conn, session_doc):
    conn.execute(
        "INSERT OR REPLACE INTO sessions(session_id, script_id, human_character_id, roster_json, created_at) VALUES(?,?,?,?,?)",
        (
            session_doc["session_id"],
            session_doc["script_id"],
            session_doc["human_character_id"],
            json.dumps(session_doc, ensure_ascii=False),
            now(),
        ),
    )
    conn.commit()


def get_session(conn, session_id):
    row = conn.execute("SELECT roster_json FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    if row is None:
        raise ValueError("ERR_SESSION_NOT_FOUND")
    return json.loads(row["roster_json"])


def is_l1_key(conn, script_id, key):
    row = conn.execute(
        "SELECT 1 FROM persona_l1 WHERE script_id=? AND key=? LIMIT 1", (script_id, key)
    ).fetchone()
    return row is not None


def set_attribute(conn, session_doc, layer, character_id, key, value, reason, round_no, actor="gm_cli:host", target="", lock=False):
    if layer not in {"L2", "L3"}:
        raise ValueError("ERR_LAYER_NOT_MUTABLE")
    if not reason:
        raise ValueError("ERR_REASON_REQUIRED")
    script_id = session_doc["script_id"]
    if is_l1_key(conn, script_id, key):
        raise ValueError("ERR_L1_IMMUTABLE")

    session_id = session_doc["session_id"]
    table = "persona_l2" if layer == "L2" else "persona_l3"
    if layer == "L2":
        where = "session_id=? AND character_id=? AND key=?"
        params = (session_id, character_id, key)
    else:
        scope = "dyadic" if target else "self"
        where = "session_id=? AND character_id=? AND scope=? AND target=? AND key=?"
        params = (session_id, character_id, scope, target or "", key)

    old = conn.execute(f"SELECT data_json FROM {table} WHERE {where}", params).fetchone()
    before = json.loads(old["data_json"]) if old else None
    after = {
        "key": key,
        "value": value,
        "layer": layer,
        "scope": "dyadic" if target else "self",
        "target": target or None,
        "visibility": "self",
        "source": "gm_cli",
        "locked": bool(lock),
        "reason": reason,
        "updated_at_round": round_no,
        "updated_by": actor,
    }
    data_json = json.dumps(after, ensure_ascii=False)
    if old:
        conn.execute(f"UPDATE {table} SET data_json=?, updated_at=? WHERE {where}", (data_json, now(), *params))
    else:
        if layer == "L2":
            conn.execute(
                "INSERT INTO persona_l2(session_id, character_id, key, data_json, updated_at) VALUES(?,?,?,?,?)",
                (session_id, character_id, key, data_json, now()),
            )
        else:
            conn.execute(
                "INSERT INTO persona_l3(session_id, character_id, scope, target, key, data_json, updated_at) VALUES(?,?,?,?,?,?,?)",
                (session_id, character_id, "dyadic" if target else "self", target or "", key, data_json, now()),
            )
    conn.execute(
        """INSERT INTO attribute_events(session_id, round, actor, layer, character_id, key, target,
                                        before_json, after_json, reason, created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            session_id,
            round_no,
            actor,
            layer,
            character_id,
            key,
            target or "",
            json.dumps(before, ensure_ascii=False) if before else None,
            data_json,
            reason,
            now(),
        ),
    )
    conn.commit()
    return after


def get_attributes(conn, session_id, layer, character_id=None):
    table = "persona_l2" if layer == "L2" else "persona_l3"
    sql = f"SELECT data_json FROM {table} WHERE session_id=?"
    params = [session_id]
    if character_id:
        sql += " AND character_id=?"
        params.append(character_id)
    return [json.loads(r["data_json"]) for r in conn.execute(sql, params)]


def audit(conn, session_id, character_id=None, since=0):
    sql = "SELECT * FROM attribute_events WHERE session_id=? AND seq>=?"
    params = [session_id, since]
    if character_id:
        sql += " AND character_id=?"
        params.append(character_id)
    sql += " ORDER BY seq"
    return [dict(r) for r in conn.execute(sql, params)]


def export_db(conn, out_path):
    payload = {
        "scripts": list_scripts(conn),
        "persona_l2": [dict(r) for r in conn.execute("SELECT * FROM persona_l2")],
        "persona_l3": [dict(r) for r in conn.execute("SELECT * FROM persona_l3")],
        "sessions": [dict(r) for r in conn.execute("SELECT * FROM sessions")],
        "attribute_events": [dict(r) for r in conn.execute("SELECT * FROM attribute_events")],
        "game_events": [dict(r) for r in conn.execute("SELECT * FROM game_events")],
        "state_snapshots": [dict(r) for r in conn.execute("SELECT * FROM state_snapshots")],
        "private_rooms": [dict(r) for r in conn.execute("SELECT * FROM private_rooms")],
        "game_state": [dict(r) for r in conn.execute("SELECT * FROM game_state")],
    }
    write_json(out_path, payload)
    return payload


def save_snapshot(conn, session_id, round_no, agent_id, data):
    conn.execute(
        "INSERT OR REPLACE INTO state_snapshots(session_id, round, agent_id, data_json) VALUES(?,?,?,?)",
        (session_id, round_no, agent_id, json.dumps(data, ensure_ascii=False)),
    )
    conn.commit()


def load_snapshot(conn, session_id, round_no):
    rows = conn.execute(
        "SELECT agent_id, data_json FROM state_snapshots WHERE session_id=? AND round=?",
        (session_id, round_no),
    ).fetchall()
    return {r["agent_id"]: json.loads(r["data_json"]) for r in rows}


def set_game_state(conn, session_id, key, value):
    conn.execute(
        "INSERT OR REPLACE INTO game_state(session_id, key, value_json) VALUES(?,?,?)",
        (session_id, key, json.dumps(value, ensure_ascii=False)),
    )
    conn.commit()


def get_game_state(conn, session_id, key, default=None):
    row = conn.execute(
        "SELECT value_json FROM game_state WHERE session_id=? AND key=?", (session_id, key)
    ).fetchone()
    return json.loads(row["value_json"]) if row else default


def append_game_event(conn, session_id, round_no, actor, act_type, payload, visibility):
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


def visible_game_events(conn, session_id, seat_id, round_no=None):
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
            rows.append(dict(row))
    return rows


def open_private_room(conn, session_doc, round_no, participants):
    session_id = session_doc["session_id"]
    valid = {p["id"] for p in session_doc["participants"] if p["kind"] != "gm"}
    seats = list(dict.fromkeys(participants))
    if len(seats) < 2:
        raise ValueError("ERR_ROOM_TOO_SMALL")
    unknown = [s for s in seats if s not in valid]
    if unknown:
        raise ValueError("ERR_ROOM_SEAT_NOT_FOUND")
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM private_rooms WHERE session_id=?", (session_id,)
    ).fetchone()["n"]
    room_id = f"{session_id}-p{count + 1:03d}"
    conn.execute(
        """INSERT INTO private_rooms(room_id, session_id, round, participants_json, status, created_at)
           VALUES(?,?,?,?,?,?)""",
        (room_id, session_id, round_no, json.dumps(seats, ensure_ascii=False), "open", now()),
    )
    seq = append_game_event(
        conn, session_id, round_no, "gm:host", "room_open",
        {"room_id": room_id, "participants": seats}, [f"room:{room_id}"],
    )
    conn.execute("UPDATE private_rooms SET opened_seq=? WHERE room_id=?", (seq, room_id))
    conn.commit()
    return {"room_id": room_id, "participants": seats, "round": round_no, "opened_seq": seq}


def get_private_room(conn, room_id):
    row = conn.execute("SELECT * FROM private_rooms WHERE room_id=?", (room_id,)).fetchone()
    if row is None:
        raise ValueError("ERR_ROOM_NOT_FOUND")
    data = dict(row)
    data["participants"] = json.loads(data.pop("participants_json"))
    return data


def list_private_rooms(conn, session_id):
    rows = conn.execute(
        "SELECT room_id, round, status, participants_json, opened_seq, closed_seq FROM private_rooms WHERE session_id=? ORDER BY room_id",
        (session_id,),
    ).fetchall()
    return [
        {**dict(r), "participants": json.loads(r["participants_json"]), "participants_json": None}
        for r in rows
    ]


def close_private_room(conn, session_id, room_id):
    room = get_private_room(conn, room_id)
    if room["session_id"] != session_id:
        raise ValueError("ERR_ROOM_SESSION_MISMATCH")
    if room["status"] != "open":
        raise ValueError("ERR_ROOM_ALREADY_CLOSED")
    seq = append_game_event(conn, session_id, room["round"], "gm:host", "room_close", {"room_id": room_id}, [f"room:{room_id}"])
    conn.execute("UPDATE private_rooms SET status='closed', closed_seq=? WHERE room_id=?", (seq, room_id))
    conn.commit()
    return {"room_id": room_id, "status": "closed", "closed_seq": seq}


def private_say(conn, session_id, room_id, actor, utterance, target=None, claimed_facts=None, inner_intent=None):
    room = get_private_room(conn, room_id)
    if room["session_id"] != session_id:
        raise ValueError("ERR_ROOM_SESSION_MISMATCH")
    if room["status"] != "open":
        raise ValueError("ERR_ROOM_CLOSED")
    if actor not in room["participants"]:
        raise ValueError("ERR_NOT_ROOM_MEMBER")
    if not utterance or not utterance.strip():
        raise ValueError("ERR_EMPTY_UTTERANCE")
    runtime = get_game_state(conn, session_id, "runtime", {})
    known = set(get_game_state(conn, session_id, "revealed_clues", []))
    state = runtime.get(actor, {})
    known |= set(state.get("beliefs", {}))
    claims = [f for f in (claimed_facts or []) if f in known]
    payload = {
        "room_id": room_id,
        "utterance": utterance.strip()[:500],
        "claimed_facts": claims,
        "target": target if target in room["participants"] else None,
        "inner_intent": inner_intent if inner_intent is not None else "用户自行决定",
        "emotion_shown": "平静",
    }
    seq = append_game_event(conn, session_id, room["round"], actor, "private_say", payload, [f"room:{room_id}"])
    return {"room_id": room_id, "seq": seq, "actor": actor, "claimed_facts": claims}
