"""G23: cross-session experiential memory with a ~3 day half-life.

This is L0 meta state, *not* L1: it records how a character felt about a
stable player id or another NPC after a session, whether that session was a
win/loss/draw, and lets that sentiment decay unless reinforced.  No Canon
attribute is ever modified and no fact is invented -- evidence always points
at a real session id plus event seqs.

Decay: ``w(t) = exp(-ln2 * (t - last_interacted_at) / half_life)``.  Below
``FADE_THRESHOLD`` a row is marked ``faded`` but never deleted (retro needs
it).  A fresh interaction resets the clock, increments ``sentiment`` by the
new evidence and clears ``faded`` -- the user's "unless re-strengthened".
"""

import json
import math
from datetime import datetime, timedelta, timezone

from . import db

FADE_THRESHOLD = 0.15
DEFAULT_HALF_LIFE = 3.0
SENTIMENT_CAP = 1.0
OUTCOMES = ("win", "loss", "draw")


def _num(value, default=0.0):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def parse_time(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        dt = datetime.now(timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def decay_weight(last_interacted_at, now=None, half_life_days=DEFAULT_HALF_LIFE,
                 at=None):
    """w(t) in [0,1]; ``at`` is an injectable clock for deterministic tests."""
    last = parse_time(last_interacted_at)
    current = parse_time(at) if at is not None else (now or datetime.now(timezone.utc))
    days = max(0.0, (current - last).total_seconds() / 86400.0)
    half = max(0.01, _num(half_life_days, DEFAULT_HALF_LIFE))
    return round(math.exp(-math.log(2.0) * days / half), 6)


def _row(row):
    if row is None:
        return None
    item = dict(row)
    item["evidence"] = json.loads(item.get("evidence_json") or "[]")
    item.pop("evidence_json", None)
    item["faded"] = bool(item.get("faded"))
    return item


def get(conn, owner_character_id, target_kind, target_id):
    row = conn.execute(
        """SELECT * FROM long_term_memory WHERE owner_character_id=?
           AND target_kind=? AND target_id=?""",
        (owner_character_id, target_kind, target_id),
    ).fetchone()
    return _row(row)


def list_for(conn, owner_character_id=None, target_id=None):
    sql = "SELECT * FROM long_term_memory WHERE 1=1"
    params = []
    if owner_character_id:
        sql += " AND owner_character_id=?"
        params.append(owner_character_id)
    if target_id:
        sql += " AND target_id=?"
        params.append(target_id)
    sql += " ORDER BY last_interacted_at DESC, id"
    return [_row(r) for r in conn.execute(sql, params)]


def refresh_faded(conn, at=None):
    """Recompute the faded flag from the stored clock; no deletion."""
    updated = []
    for item in list_for(conn):
        w = decay_weight(item["last_interacted_at"], at=at,
                         half_life_days=item["decay_half_life_days"])
        faded = int(w < FADE_THRESHOLD)
        if faded != int(item["faded"]):
            conn.execute(
                "UPDATE long_term_memory SET faded=? WHERE id=?", (faded, item["id"])
            )
            updated.append({"target_id": item["target_id"], "faded": bool(faded)})
    conn.commit()
    return updated


def reinforce(conn, owner_character_id, target_kind, target_id, sentiment_delta=0.0,
              outcome="draw", evidence=None, session_id="", at=None,
              half_life_days=DEFAULT_HALF_LIFE):
    """Upsert one memory; a fresh interaction revives a faded record."""
    if target_kind not in ("npc", "player"):
        raise ValueError("ERR_LONGTERM_TARGET_KIND")
    outcome = outcome if outcome in OUTCOMES else "draw"
    existing = get(conn, owner_character_id, target_kind, target_id)
    ts = (at or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    evidence = list(evidence or [])
    if existing:
        sentiment = max(-SENTIMENT_CAP, min(SENTIMENT_CAP,
            _num(existing["sentiment"]) + _num(sentiment_delta)
        ))
        merged = _merge_evidence(existing["evidence"], evidence)
        conn.execute(
            """UPDATE long_term_memory SET sentiment=?, outcome=?, last_interacted_at=?,
               decay_half_life_days=?, faded=0, evidence_json=?,
               reinforcement_count=reinforcement_count+1, updated_at=? WHERE id=?""",
            (sentiment, outcome, ts, _num(half_life_days, DEFAULT_HALF_LIFE),
             json.dumps(merged, ensure_ascii=False), ts, existing["id"]),
        )
    else:
        merged = _merge_evidence([], evidence)
        conn.execute(
            """INSERT INTO long_term_memory(owner_character_id, target_kind, target_id,
               player_id, sentiment, outcome, last_interacted_at, decay_half_life_days,
               faded, evidence_json, reinforcement_count, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (owner_character_id, target_kind, target_id,
             target_id if target_kind == "player" else None,
             max(-SENTIMENT_CAP, min(SENTIMENT_CAP, _num(sentiment_delta))),
             outcome, ts, _num(half_life_days, DEFAULT_HALF_LIFE), 0,
             json.dumps(merged, ensure_ascii=False), 0, ts, ts),
        )
    conn.commit()
    return get(conn, owner_character_id, target_kind, target_id)


def _merge_evidence(old, new):
    merged, seen = [], set()
    for item in list(old or []) + list(new or []):
        key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged[:50]


def forget(conn, owner_character_id=None, target_id=None):
    """Explicit operator forget: this one is allowed to delete rows."""
    sql = "DELETE FROM long_term_memory WHERE 1=1"
    params = []
    if owner_character_id:
        sql += " AND owner_character_id=?"
        params.append(owner_character_id)
    if target_id:
        sql += " AND target_id=?"
        params.append(target_id)
    cur = conn.execute(sql, params)
    conn.commit()
    return cur.rowcount


def settle_session(conn, session_id, player_id=None):
    """Summarise a finished session into one sentiment/outcome per character.

    Sentiment comes from stored L3 relations averaged over `like` and `trust`;
    outcome from the vote result.  Seat ids are mapped through the session
    roster so the memory key is a stable character/player id, never a seat id.
    """
    session = db.get_session(conn, session_id)
    runtime_state = db.get_game_state(conn, session_id, "runtime", {}) or {}
    result = db.get_game_state(conn, session_id, "result", {}) or {}
    winner = result.get("winner")
    human_cid = session.get("human_character_id")
    pid = player_id or (session.get("l0") or {}).get("player_id") or human_cid
    written = []
    for seat, state in runtime_state.items():
        if str(seat).startswith("gm:"):
            continue
        owner = state.get("character_id")
        if not owner:
            continue
        outcome = "win" if winner == owner else ("loss" if winner else "draw")
        for other, rel in (state.get("relations") or {}).items():
            other_state = runtime_state.get(other) or {}
            is_human = other_state.get("character_id") == human_cid
            kind = "player" if is_human else "npc"
            tid = (pid or human_cid) if is_human else (other_state.get("character_id") or other)
            sentiment = round(0.6 * _num(rel.get("like")) + 0.4 * _num(rel.get("trust")), 4)
            evidence = [{"session_id": session_id, "seq": None,
                         "basis": "relations(like,trust)+vote.winner"}]
            written.append(reinforce(
                conn, owner, kind, tid, sentiment, outcome, evidence, session_id=session_id
            ))
    return written


def inject_priors_from(conn, runtime_state, owner_character_id, player_id=None, at=None):
    """Inject non-faded sentiment into L3 relations; never touch L1.

    Returns the list of priors actually applied so the runtime can audit them.
    """
    rows = list_for(conn, owner_character_id=owner_character_id)
    applied = []
    for item in rows:
        if item["faded"]:
            continue
        w = decay_weight(item["last_interacted_at"], at=at,
                         half_life_days=item["decay_half_life_days"])
        if w < FADE_THRESHOLD:
            continue
        target = item["target_id"]
        # Map a player id back onto the human seat present in this session.
        seat = next(
            (s for s, st in runtime_state.items()
             if (st.get("character_id") == target
                 or (item["target_kind"] == "player" and st.get("is_human")))),
            None,
        )
        if not seat:
            continue
        for self_seat, state in runtime_state.items():
            if state.get("character_id") != owner_character_id or self_seat == seat:
                continue
            rel = state.setdefault("relations", {}).setdefault(
                seat, {"like": 0.0, "trust": 0.0, "intimacy": 0.0, "suspicion": 0.1}
            )
            delta = round(_num(item["sentiment"]) * w, 4)
            rel["like"] = round(max(-1.0, min(1.0, _num(rel.get("like")) + delta)), 4)
            rel["trust"] = round(max(-1.0, min(1.0, _num(rel.get("trust")) + 0.5 * delta)), 4)
            bias = 0.05 if item["outcome"] == "win" else (-0.05 if item["outcome"] == "loss" else 0.0)
            state.setdefault("long_term_priors", []).append({
                "target": seat, "target_id": target, "kind": item["target_kind"],
                "sentiment": item["sentiment"], "weight": w, "applied_delta": delta,
                "outcome": item["outcome"], "outcome_bias": bias,
                "session_id": next((e.get("session_id") for e in item["evidence"]), None),
            })
            applied.append({"owner": self_seat, "target": seat, "delta": delta, "weight": w})
    return applied
