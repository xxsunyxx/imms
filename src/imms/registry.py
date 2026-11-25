"""Script management registry: lifecycle, skeleton versions and audit trail.

A script is not just a row in ``scripts``.  It has a managed lifecycle
(staged -> normalized -> ready -> archived, or rejected), a versioned skeleton
produced by the jig, and an event log that records who promoted it and why.
"""

import json

from . import db

STATUSES = ("staged", "normalized", "ready", "archived", "rejected")
ALLOWED = {
    "staged": {"normalized", "rejected"},
    "normalized": {"ready", "staged", "rejected"},
    "ready": {"archived", "normalized", "rejected"},
    "archived": {"ready", "rejected"},
    "rejected": {"staged"},
}


def _row(row):
    return dict(row) if row is not None else None


def get(conn, script_id):
    row = conn.execute(
        "SELECT * FROM script_registry WHERE script_id=?", (script_id,)
    ).fetchone()
    if row is None:
        raise ValueError("ERR_SCRIPT_NOT_REGISTERED")
    return _row(row)


def find(conn, script_id):
    row = conn.execute(
        "SELECT * FROM script_registry WHERE script_id=?", (script_id,)
    ).fetchone()
    return _row(row)


def log(conn, script_id, action, detail=None):
    conn.execute(
        "INSERT INTO script_events(script_id, action, detail_json, created_at) VALUES(?,?,?,?)",
        (script_id, action, json.dumps(detail or {}, ensure_ascii=False), db.now()),
    )
    conn.commit()


def register(conn, script_id, title=None, source_kind="", source_sha256="",
             skeleton_version="", canonical_version="", skeleton_file="",
             script_file="", notes="", status="staged"):
    if status not in STATUSES:
        raise ValueError("ERR_SCRIPT_STATUS_INVALID")
    now = db.now()
    conn.execute(
        """INSERT OR IGNORE INTO script_registry(
               script_id, title, source_kind, source_sha256, status,
               skeleton_version, canonical_version, skeleton_file, script_file,
               notes, created_at, updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (script_id, title or script_id, source_kind, source_sha256, status,
         skeleton_version, canonical_version, skeleton_file, script_file,
         notes, now, now),
    )
    conn.execute(
        """UPDATE script_registry SET title=COALESCE(?, title),
               source_kind=CASE WHEN ?='' THEN source_kind ELSE ? END,
               source_sha256=CASE WHEN ?='' THEN source_sha256 ELSE ? END,
               skeleton_version=CASE WHEN ?='' THEN skeleton_version ELSE ? END,
               canonical_version=CASE WHEN ?='' THEN canonical_version ELSE ? END,
               skeleton_file=CASE WHEN ?='' THEN skeleton_file ELSE ? END,
               script_file=CASE WHEN ?='' THEN script_file ELSE ? END,
               notes=CASE WHEN ?='' THEN notes ELSE ? END,
               updated_at=? WHERE script_id=?""",
        (title, source_kind, source_kind, source_sha256, source_sha256,
         skeleton_version, skeleton_version, canonical_version, canonical_version,
         skeleton_file, skeleton_file, script_file, script_file, notes, notes,
         now, script_id),
    )
    conn.commit()
    row = get(conn, script_id)
    if not conn.execute(
        "SELECT 1 FROM script_events WHERE script_id=? AND action='register'", (script_id,)
    ).fetchone():
        log(conn, script_id, "register", {
            "source_kind": source_kind, "status": status,
        })
    return row


def save_skeleton(conn, script_id, skeleton):
    """Append a skeleton version; older versions are never overwritten."""
    get(conn, script_id)
    version = conn.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 FROM script_skeletons WHERE script_id=?",
        (script_id,),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO script_skeletons(script_id, version, skeleton_json, created_at) VALUES(?,?,?,?)",
        (script_id, version, json.dumps(skeleton, ensure_ascii=False, sort_keys=True), db.now()),
    )
    conn.execute(
        "UPDATE script_registry SET skeleton_version=?, updated_at=? WHERE script_id=?",
        (str(version), db.now(), script_id),
    )
    conn.commit()
    log(conn, script_id, "skeleton_saved", {
        "version": version,
        "gaps": [g.get("slot") for g in skeleton.get("gaps") or []],
        "llm": skeleton.get("llm") or {},
    })
    return version


def skeleton(conn, script_id, version=None):
    get(conn, script_id)
    if version is None:
        row = conn.execute(
            "SELECT * FROM script_skeletons WHERE script_id=? ORDER BY version DESC LIMIT 1",
            (script_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM script_skeletons WHERE script_id=? AND version=?",
            (script_id, version),
        ).fetchone()
    if row is None:
        raise ValueError("ERR_SKELETON_NOT_FOUND")
    data = _row(row)
    data["skeleton"] = json.loads(data.pop("skeleton_json"))
    return data


def set_status(conn, script_id, status, notes=""):
    if status not in STATUSES:
        raise ValueError("ERR_SCRIPT_STATUS_INVALID")
    current = get(conn, script_id)
    if status != current["status"] and status not in ALLOWED[current["status"]]:
        raise ValueError(
            f"ERR_STATUS_TRANSITION: {current['status']}->{status}"
        )
    conn.execute(
        "UPDATE script_registry SET status=?, notes=CASE WHEN ?='' THEN notes ELSE ? END, updated_at=? WHERE script_id=?",
        (status, notes, notes, db.now(), script_id),
    )
    conn.commit()
    log(conn, script_id, "status", {"from": current["status"], "to": status, "notes": notes})
    return get(conn, script_id)


def promote(conn, script_id, status, notes=""):
    return set_status(conn, script_id, status, notes)


def reject(conn, script_id, reason):
    return set_status(conn, script_id, "rejected", reason)


def list_scripts(conn, status=None, limit=None):
    sql = "SELECT * FROM script_registry"
    args = []
    if status:
        sql += " WHERE status=?"
        args.append(status)
    sql += " ORDER BY updated_at DESC, script_id"
    if limit:
        sql += " LIMIT ?"
        args.append(limit)
    return [_row(r) for r in conn.execute(sql, args)]


def history(conn, script_id):
    get(conn, script_id)
    return [
        _row(r) for r in conn.execute(
            "SELECT * FROM script_events WHERE script_id=? ORDER BY id", (script_id,)
        )
    ]
