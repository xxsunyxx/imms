"""G25: long-term persona facets (IPS §4.3/§4.4).

A *facet* is not a rewrite of L1 Canon.  It is a derivative profile that the
runtime points at via ``active_facet_id``.  Two families are modelled, and the
difference between them is memory scope rather than behaviour intensity:

* ``continuous``   - same self, reweighted goal/attitude profile; memory stays
  fully shared (``memory_scope='shared'``).
* ``dissociative`` - separate ``beliefs`` / ``memory`` / ``lie_ledger`` buckets
  per facet (``memory_scope='isolated:<facet_id>'``), with an explicit memory
  gap on switch.  Neither family may create facts (IR1 still applies).

Only behavioural dimension names are stored.  Clinical/popular labels are a
design-side semantic reference in ``docs/`` and never appear in a card, a
prompt or a player-facing replay.
"""

import json

from . import db

KINDS = ("continuous", "dissociative")
BEHAVIOUR_KEYS = (
    "goal_fixation", "possessiveness", "abandonment_fear",
    "empathy_suppression", "manipulation", "remorse_deficit",
    "hostile_attribution", "suspicion_baseline", "affect_instability",
    "attention_seeking", "identity_continuity", "memory_continuity",
)
BUCKETS = ("beliefs", "memory", "lie_ledger")


def _num(value, default=0.0):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if -1e9 < v < 1e9 else default


def new_registry():
    return {"active_facet_id": None, "facets": {}}


def register(reg, facet_id, kind, profile, derived_from=None, entry=None,
             exit_rule=None, memory_scope=None):
    """Register a derivative facet; missing evidence stays None, never guessed."""
    if kind not in KINDS:
        raise ValueError("ERR_FACET_KIND")
    facets = reg.setdefault("facets", {})
    scope = memory_scope or ("shared" if kind == "continuous" else f"isolated:{facet_id}")
    facets[facet_id] = {
        "facet_id": facet_id,
        "kind": kind,
        "derived_from": derived_from or [],
        "profile": {
            key: (_num(profile.get(key), None) if key in profile else None)
            for key in BEHAVIOUR_KEYS
        },
        "memory_scope": scope,
        "entry": entry or {},
        "exit": exit_rule or {},
        "audit": [],
    }
    return facets[facet_id]


def active(reg):
    fid = reg.get("active_facet_id")
    return reg.get("facets", {}).get(fid) if fid else None


def validate(reg):
    """Structural checks for persistence and replay; no diagnosis labels."""
    problems = []
    for fid, item in (reg.get("facets") or {}).items():
        if item.get("kind") not in KINDS:
            problems.append(f"{fid}:bad_kind")
        expected = "shared" if item.get("kind") == "continuous" else f"isolated:{fid}"
        if item.get("memory_scope") != expected:
            problems.append(f"{fid}:bad_memory_scope")
        if not item.get("derived_from"):
            problems.append(f"{fid}:missing_evidence")
        extra = set(item.get("profile") or {}) - set(BEHAVIOUR_KEYS)
        if extra:
            problems.append(f"{fid}:unknown_dimensions")
    return problems


def _ensure_bucket(state, bucket):
    """Isolated facets get their own bucket; continuous facets share the base."""
    reg = state.get("facets") or new_registry()
    facet = active(reg)
    if not facet or facet["memory_scope"] == "shared":
        return state.setdefault(bucket, {} if bucket == "beliefs" else [] if bucket in ("memory", "lie_ledger") else {})
    scope = facet["memory_scope"]
    buckets = state.setdefault("facet_buckets", {}).setdefault(facet["facet_id"], {})
    default = {} if bucket == "beliefs" else []
    return buckets.setdefault(bucket, default)


def view(state, bucket):
    """Read the active facet's view of one memory bucket."""
    return _ensure_bucket(state, bucket)


def switch(state, facet_id, round_no=0, trigger="", seed=None):
    """Move ``active_facet_id``; isolated switches create a memory gap.

    The switch itself never copies or deletes memory: a dissociative facet
    simply starts with empty buckets that fill only from its own observations.
    """
    reg = state.setdefault("facets", new_registry())
    returning_to_base = facet_id is None
    if not returning_to_base and facet_id not in reg.get("facets", {}):
        raise ValueError("ERR_FACET_NOT_FOUND")
    previous = reg.get("active_facet_id")
    if previous == facet_id:
        return None
    target = reg["facets"][facet_id] if not returning_to_base else {
        "kind": "base", "memory_scope": "shared", "audit": []
    }
    entry = {
        "round": int(round_no), "from": previous, "to": facet_id or "base",
        "kind": target["kind"], "trigger": trigger, "seed": seed,
        "memory_scope": target["memory_scope"],
    }
    outgoing_was_isolated = bool(previous and (reg.get("facets", {}).get(previous) or {}).get("memory_scope", "shared").startswith("isolated:"))
    incoming_isolated = target["memory_scope"].startswith("isolated:")
    if incoming_isolated or outgoing_was_isolated or returning_to_base:
        entry["memory_gap"] = {
            "from": previous or "base", "to": facet_id or "base",
            "unshared_buckets": list(BUCKETS),
            "note": "分离面之间不共享 beliefs/memory/lie_ledger",
        }
        # Park the outgoing facet's live buckets, then load the incoming one.
        # Both sides of a dissociative switch therefore see only their own
        # beliefs/memory/lies; the gap is explicit and nothing is deleted.
        store = state.setdefault("facet_buckets", {})
        out_key = previous or "base"
        in_key = facet_id or "base"
        store.setdefault(out_key, {})
        for bucket in BUCKETS:
            store[out_key][bucket] = state.get(bucket)
            state[bucket] = store.setdefault(in_key, {}).get(bucket)
        for bucket in BUCKETS:
            if state.get(bucket) is None:
                state[bucket] = {} if bucket == "beliefs" else []
    else:
        entry["memory_gap"] = None
    reg["active_facet_id"] = facet_id
    if not returning_to_base:
        target.setdefault("audit", []).append(entry)
    state.setdefault("facet_switch_log", []).append(entry)
    return entry


def apply_weights(state):
    """Continuous facets reweight the L4 profile, never L1."""
    reg = state.get("facets") or {}
    facet = active(reg)
    if not facet or facet["kind"] != "continuous":
        return {}
    return {
        key: round(_num(value), 4)
        for key, value in (facet.get("profile") or {}).items()
        if value is not None
    }


def audience_for(state, base_audience=None):
    """Add a facet filter for InfoGuard scope consumers.

    Continuous facets keep the original audience; isolated facets can only be
    seen by seats sharing the same facet id.
    """
    reg = state.get("facets") or {}
    facet = active(reg)
    audience = sorted(set(base_audience or []))
    if not facet or facet["memory_scope"] == "shared":
        return audience
    return [a for a in audience if a == state.get("seat_id")]


def scope_filter(state, scope):
    """Apply facet isolation to an InformationScope-like object (G25 + §4.3)."""
    reg = state.get("facets") or {}
    facet = active(reg)
    if not facet or facet["memory_scope"] == "shared":
        return scope
    if scope is None:
        return None
    known = frozenset(scope.known_fact_ids) if scope.known_fact_ids else frozenset()
    # A dissociative facet does not inherit facts learned by another facet;
    # only public facts plus this facet's own bucket remain visible.
    own = set((_ensure_bucket(state, "beliefs") or {}).keys())
    try:
        from dataclasses import replace
        return replace(scope, known_fact_ids=frozenset(known & own))
    except (TypeError, ValueError):
        return scope


def save_registry(conn, session_id, character_id, reg):
    """Persist to L2 (mutable); L1 stays read-only."""
    conn.execute(
        """INSERT OR REPLACE INTO persona_l2(session_id, character_id, key, data_json, updated_at)
           VALUES(?,?,?,?,?)""",
        (session_id, character_id, "facets.active",
         json.dumps(reg, ensure_ascii=False), db.now()),
    )
    conn.commit()


def load_registry(conn, session_id, character_id):
    row = conn.execute(
        """SELECT data_json FROM persona_l2 WHERE session_id=? AND character_id=? AND key=?""",
        (session_id, character_id, "facets.active"),
    ).fetchone()
    return json.loads(row["data_json"]) if row else new_registry()


def audit_lines(reg, names=None):
    """Player-facing audit text: only behavioural descriptions, no labels."""
    names = names or {}
    lines = []
    for fid, item in (reg.get("facets") or {}).items():
        dims = [k for k, v in (item.get("profile") or {}).items() if v is not None]
        for record in item.get("audit") or []:
            gap = "有记忆断层" if record.get("memory_gap") else "记忆连续"
            lines.append(
                f"- 第{record.get('round')}轮 {item['kind']} 面切换至 {fid}："
                f"{gap}，可见行为维度 {', '.join(dims) or '未配置'}"
            )
    return lines
