"""Guide prompt library: managed wording rules that drive variation only.

IMMS owns and remembers the data; guides drive how it is said.  A guide may
change tone, phrasing, rhythm and delivery, never claimed_facts, intents,
targets or numeric values.  Two gates enforce this:

  * import time -- rules carrying fact ids or key=value assignments are
    rejected before they enter the database (with a recorded reason);
  * render time -- ``prompt_engine._sanitize_guide`` strips anything that
    still looks like data.

Sources are YAML files (versionable, human-readable), imported into the
``guide_rules`` table.  Selection is deterministic via ``random_source``, so
replay and GM branches pick the same guides for the same inputs.
"""

import re
from pathlib import Path

import yaml

from . import db, prompt_engine, random_source

DEFAULT_SOURCE = Path(__file__).resolve().parents[2] / "config" / "guides.yaml"

_FACT_ID = re.compile(r"\b(?:f|clue|ev)_[\w-]+")
_ASSIGN = re.compile(r"[A-Za-z_][\w.]*\s*=\s*[\w.-]+")


def validate_text(text):
    """Return a rejection reason, or None when the text is wording-only."""
    value = str(text or "")
    if not value.strip():
        return "empty"
    if _FACT_ID.search(value):
        return "contains_fact_id"
    if _ASSIGN.search(value):
        return "contains_assignment"
    if len(value) > 200:
        return "too_long"
    return None


def parse_file(path=None):
    """Load a YAML guide file into a list of rule dicts."""
    source = Path(path) if path else DEFAULT_SOURCE
    doc = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    rules = doc.get("rules") or []
    if not isinstance(rules, list):
        raise ValueError("ERR_GUIDE_RULES_NOT_LIST")
    return rules, int(doc.get("version") or 1), str(source)


def import_guides(conn, path=None, replace=True):
    """Import rules; rejected rules are returned, never silently dropped."""
    rules, version, source = parse_file(path)
    imported, rejected = [], []
    for item in rules:
        if not isinstance(item, dict):
            rejected.append({"id": "?", "reason": "not_mapping"})
            continue
        gid = str(item.get("id") or "")
        text = str(item.get("text") or "")
        reason = validate_text(text)
        if not gid:
            reason = reason or "missing_id"
        if reason:
            rejected.append({"id": gid, "reason": reason})
            continue
        conn.execute(
            """INSERT OR REPLACE INTO guide_rules
               (guide_id, version, purpose, text, weight, tags_json, enabled, source, created_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                gid, version, str(item.get("purpose") or "tone"), text,
                float(item.get("weight") or 1.0),
                db.json.dumps(sorted(item.get("tags") or [])),
                1 if item.get("enabled", True) else 0,
                source, db.now(),
            ),
        )
        imported.append(gid)
    if replace:
        conn.execute(
            "DELETE FROM guide_rules WHERE version!=?", (version,)
        )
    conn.commit()
    return {"version": version, "source": source, "imported": imported, "rejected": rejected}


def _latest_enabled(conn):
    rows = conn.execute(
        """SELECT * FROM guide_rules g
           WHERE enabled=1 AND version=(SELECT MAX(version) FROM guide_rules)
           ORDER BY guide_id"""
    ).fetchall()
    out = []
    for row in rows:
        d = dict(row)
        d["tags"] = db.json.loads(d.get("tags_json") or "[]")
        out.append(d)
    return out


def list_guides(conn, purpose=None, tag=None):
    """List current-version enabled rules, optionally filtered."""
    rows = _latest_enabled(conn)
    if purpose:
        rows = [r for r in rows if r["purpose"] == purpose]
    if tag:
        rows = [r for r in rows if tag in r["tags"]]
    return rows


def select(conn, purpose=None, tags=None, limit=1, session_id="", seat_id=None,
           round_no=None, event_seq=None, base_seed=None):
    """Deterministically pick wording guides; return sanitised text only."""
    rows = _latest_enabled(conn)
    if purpose:
        wanted = set(purpose if isinstance(purpose, (list, tuple, set)) else [purpose])
        rows = [r for r in rows if r["purpose"] in wanted]
    if tags:
        wanted_tags = set(tags)
        rows = [r for r in rows if wanted_tags.intersection(r["tags"])]
    if not rows:
        return []
    rng = random_source.rng_for(
        session_id, seat_id=seat_id, round_no=round_no, event_seq=event_seq,
        purpose="guide:" + str(purpose or ""), base_seed=base_seed,
    )
    count = min(max(1, int(limit)), len(rows))
    weights = {i: float(r.get("weight") or 1.0) for i, r in enumerate(rows)}
    picked = []
    for _ in range(count):
        idx = random_source.weighted_choice(rng, weights)
        if idx is None:
            break
        picked.append(prompt_engine._sanitize_guide(rows[idx]["text"]))
        weights.pop(idx, None)
    return [p for p in picked if p]


def guides_for_prompt(conn, purpose=None, tags=None, limit=1, session_id="",
                      seat_id=None, round_no=None, event_seq=None, base_seed=None):
    """Bridge to ``prompt_engine``: return guide strings for ctx["guides"]."""
    return select(
        conn, purpose=purpose, tags=tags, limit=limit, session_id=session_id,
        seat_id=seat_id, round_no=round_no, event_seq=event_seq, base_seed=base_seed,
    )
