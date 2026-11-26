"""G20: IPS aftermath consequences, memory imprint and cascade damping (§11–§12).

This module deliberately reuses the ledgers already present in the runtime:

* ``regret``/``rationalize``/``escalate`` consequences are applied to ``relations``
  and ``emotion`` (L3) and audited through ``lie.reasoning_log`` when a lie is
  involved; ``rationalize`` appends to the existing ``lie_ledger``.
* Memory imprint calls ``memory.observe`` and marks the record with a longer
  decay constant and an emotion-congruence tag (trauma re-trigger).
* Self-concept drift is written only to an audit field; L1 Canon is never
  touched and the per-session L1 norm stays within ``MAX_DRIFT``.
* Cascade uses a three-fold damper (depth <= 2, one-tier downgrade, global
  token quota) so a single burst becomes two ripples, not a room-wide frenzy.

Everything is deterministic: draws come from ``random_source.rng_for`` keyed by
session/seat/round/purpose.  No new facts are created (IR1/IR2 unchanged).
"""

import math

from . import formulas, lie, memory, random_source

STRATEGIES = ("regret", "rationalize", "escalate")
KAPPA = 0.05
MAX_DRIFT = 0.08
CASCADE_P0 = 0.35
CASCADE_MAX_DEPTH = 2
SURPRISE_GAIN = 0.5
IMPRINT_TAU_FACTOR = 3.0
DEFAULT_SURPRISE = 1.2

EMO_DOMAIN = {
    "valence": (-1.0, 1.0), "arousal": (0.0, 1.0), "dominance": (-1.0, 1.0),
    "stress": (0.0, 1.0), "cognitive_load": (0.0, 1.0),
    "intoxication": (0.0, 1.0), "morale": (0.0, 1.0), "exposure_risk": (0.0, 1.0),
}
REL_DOMAIN = {
    "like": (-1.0, 1.0), "trust": (-1.0, 1.0), "respect": (0.0, 1.0),
    "intimacy": (0.0, 1.0), "suspicion": (0.0, 1.0), "fear": (0.0, 1.0),
    "envy": (0.0, 1.0), "grudge": (0.0, 1.0), "debt": (-1.0, 1.0),
    "dominance": (-1.0, 1.0), "commitment": (0.0, 1.0),
}
REL_DEFAULT = {
    "like": 0.0, "trust": 0.0, "respect": 0.5, "intimacy": 0.0,
    "suspicion": 0.1, "fear": 0.0, "envy": 0.0, "grudge": 0.0,
    "debt": 0.0, "dominance": 0.0, "commitment": 0.0,
}
TIER_ORDER = ("micro", "meso", "macro")


def _num(value, default=0.0):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def surprise_of(burst):
    """§11.4 surprise s from the G19 direction decision (never fabricated)."""
    chosen = ((burst.get("direction") or {}).get("selected") or {})
    return _num(chosen.get("surprise"), DEFAULT_SURPRISE)


def beta_surprise(burst):
    """Observer impact amplification: beta = 1 + 0.5 * s / 2.0."""
    return round(1.0 + SURPRISE_GAIN * surprise_of(burst) / 2.0, 4)


def _bump(state, container, key, delta, domain, changes, seat_id, target=None,
          reason="", irreversible=False):
    """Apply one clipped L3 delta and append an audit change entry."""
    if container is None or key not in domain:
        return None
    lo, hi = domain[key]
    before = _num(container.get(key), 0.0)
    after = round(formulas.clip(before + delta, lo, hi), 4)
    container[key] = after
    entry = {
        "seat_id": seat_id,
        "character_id": state.get("character_id"),
        "container": container,
        "dim": key,
        "before": round(before, 4),
        "after": after,
        "target": target,
        "reason": reason,
    }
    if irreversible:
        entry["irreversible"] = True
    changes.append(entry)
    return entry


def _observer_seats(burst, states):
    delta = (burst.get("aftermath") or {}).get("observers_delta") or {}
    return [s for s in delta if s in states and not states[s].get("is_human")]


def apply_consequences(states, burst, round_no=0, seed=None):
    """Apply the chosen strategy's numeric consequences in memory (L3 only).

    ``states`` is mutated in place; the returned changes are audit records with
    a ``container`` reference so the caller can mirror them into
    ``attribute_events`` without re-deriving values.
    """
    strategy = (burst.get("aftermath") or {}).get("strategy") or "regret"
    if strategy not in STRATEGIES:
        strategy = "regret"
    seat = burst.get("seat")
    actor = states.get(seat)
    I = _num((burst.get("trigger") or {}).get("intensity"))
    beta = beta_surprise(burst)
    observers = _observer_seats(burst, states)
    claimed = list((burst.get("speech_act") or {}).get("claimed_facts") or [])
    changes = []
    lie_events = []
    heat_delta = 0.0
    irreversible = []

    if strategy == "regret":
        # Target like +.10 (amplified by surprise), self stress +.15.  Any
        # already-claimed fact stays public: an apology cannot un-say it.
        for other in observers:
            rel = states[other].setdefault("relations", {}).setdefault(seat, dict(REL_DEFAULT))
            _bump(states[other], rel, "like", 0.10 * beta, REL_DOMAIN, changes,
                  other, target=seat, reason="ips.aftermath:regret")
        if actor is not None:
            _bump(actor, actor.setdefault("emotion", {}), "stress", 0.15,
                  EMO_DOMAIN, changes, seat, reason="ips.aftermath:regret")
        irreversible = list(claimed)
    elif strategy == "rationalize":
        # Fold the burst into an existing lie: reuse lie.create + reasoning_log.
        if actor is not None:
            action = burst.get("action_key") or "claim_only"
            entry = lie.create(
                actor, round_no,
                f"即兴后圆场（{action}）：我刚才的话有前后一致的说法",
                audience=observers, intent="deflect",
            )
            entry["heat"] = round(_num(entry.get("heat"), 0.0) + 0.25, 4)
            lie._append_reason(
                actor, round_no, entry["lie_id"], "rationalize",
                f"即兴 I={round(I, 3)} 后纳入既有叙事；谎言热度 +0.25",
                source="ips.aftermath",
            )
            lie_events.append({"lie_id": entry["lie_id"], "heat": entry["heat"]})
        heat_delta = 0.25
    else:  # escalate
        # Relationship damage keeps compounding (x1.4 on top of the base hit)
        # and exposure risk rises; this is what can seed a second macro later.
        for other in observers:
            rel = states[other].setdefault("relations", {}).setdefault(seat, dict(REL_DEFAULT))
            _bump(states[other], rel, "trust", -0.12 * 0.4 * beta, REL_DOMAIN,
                  changes, other, target=seat, reason="ips.aftermath:escalate",
                  irreversible=True)
        if actor is not None:
            _bump(actor, actor.setdefault("emotion", {}), "exposure_risk", 0.20,
                  EMO_DOMAIN, changes, seat, reason="ips.aftermath:escalate",
                  irreversible=True)

    return {
        "changes": changes,
        "strategy": strategy,
        "beta": beta,
        "lie_events": lie_events,
        "heat_delta": heat_delta,
        "irreversible": irreversible,
    }


def imprint_memory(state, burst, round_no=0):
    """§11.3 memory imprint with importance 0.6+0.4I and tau x3."""
    if state is None or state.get("is_human"):
        return None
    I = _num((burst.get("trigger") or {}).get("intensity"))
    action = burst.get("action_key") or "claim_only"
    observed = memory.observe(
        state,
        {
            "seq": burst.get("improv_id") or f"imp_r{round_no}",
            "actor": state.get("seat_id"),
            "act_type": "improv_burst",
            "payload": {
                "utterance": f"[即兴·{action}] 我刚才的举动",
                "claimed_facts": list((burst.get("speech_act") or {}).get("claimed_facts") or []),
            },
            "is_self": True,
        },
        round_no,
        audience=_observer_seats(burst, {"x": state}),  # self-record; audience is informational
    )
    if not observed:
        return None
    record = observed["record"]
    record["importance"] = round(0.6 + 0.4 * I, 4)
    record["tau_m_factor"] = IMPRINT_TAU_FACTOR
    emo = state.get("emotion") or {}
    record["emotion_tag"] = {
        key: round(_num(emo.get(key)), 4)
        for key in ("valence", "arousal", "stress", "exposure_risk") if key in emo
    }
    record["imprint"] = True
    return {
        "seq": record["seq"], "importance": record["importance"],
        "tau_m_factor": IMPRINT_TAU_FACTOR, "emotion_tag": record["emotion_tag"],
    }


def drift_self_concept(state, burst, round_no=0):
    """Audit-only self drift with a per-session L1-norm fuse of 0.08.

    The delta direction comes from the G19 action descriptor.  If no direction
    was selected there is no evidence for a drift, so nothing is written.
    """
    if state is None or state.get("is_human"):
        return {"applied": {}, "rejected": "no_state", "l1_norm": 0.0}
    chosen = ((burst.get("direction") or {}).get("selected") or {})
    delta = chosen.get("delta") or {}
    if not delta:
        return {"applied": {}, "rejected": "no_direction", "l1_norm": 0.0}
    I = _num((burst.get("trigger") or {}).get("intensity"))
    ledger = state.setdefault("self_concept_drift", {})
    norm = sum(abs(_num(v)) for v in ledger.values())
    applied, rejected = {}, None
    for key, raw in sorted(delta.items()):
        step = KAPPA * _num(raw) * I
        proposed = ledger.get(key, 0.0) + step
        new_norm = norm - abs(_num(ledger.get(key, 0.0))) + abs(proposed)
        if new_norm > MAX_DRIFT + 1e-9:
            rejected = "fuse_0.08"
            break
        ledger[key] = round(proposed, 6)
        norm = new_norm
        applied[key] = round(step, 6)
    audit = {
        "applied": applied, "rejected": rejected,
        "l1_norm": round(norm, 6), "kappa": KAPPA, "cap": MAX_DRIFT,
    }
    state.setdefault("self_concept_drift_audit", []).append({
        "round": int(round_no), "action": chosen.get("action"), "audit": audit,
    })
    return audit


def cascade_probability(state, source_seat, burst, depth):
    """§12: p0 * (1-C) * arousal * (1+0.6*intimacy) * 0.5^d * beta."""
    if depth < 1 or depth > CASCADE_MAX_DEPTH:
        return 0.0
    l1 = state.get("l1") or {}
    C = formulas.clip(_num(l1.get("OCEAN.C"), 0.5), 0.0, 1.0)
    arousal = formulas.clip(_num((state.get("emotion") or {}).get("arousal")), 0.0, 1.0)
    rel = (state.get("relations") or {}).get(source_seat) or {}
    intimacy = formulas.clip(_num(rel.get("intimacy")), 0.0, 1.0)
    from . import director  # local import avoids a module-load cycle
    p = (
        CASCADE_P0 * (1.0 - C) * arousal * (1.0 + 0.6 * intimacy)
        * (0.5 ** depth) * beta_surprise(burst)
        * director.cascade_multiplier(director.dials_for_state(state))
    )
    return round(formulas.clip(p, 0.0, 1.0), 4)


def _downgrade(tier, depth):
    try:
        idx = TIER_ORDER.index(tier) - depth
    except ValueError:
        return "micro"
    return TIER_ORDER[max(0, idx)]


def maybe_cascade(states, burst, round_no=0, seed=None, budget=None,
                  session_id=""):
    """Two-hop contagion with depth/tier/quota damping and no new facts."""
    from . import ips  # local import: ips -> aftermath is not imported at module load

    seat = burst.get("seat")
    if seat not in states:
        return {"cascade": [], "changes": [], "deferred": []}
    tier = burst.get("tier") or "micro"
    cascade, changes, deferred = [], [], []
    sources = [(seat, 1)]
    seen = {seat}
    while sources:
        source, depth = sources.pop(0)
        for target in sorted(states):
            if target in seen or target == source:
                continue
            target_state = states[target]
            if target_state.get("is_human") or not isinstance(target_state.get("accumulator"), dict):
                continue
            p = cascade_probability(target_state, source, burst, depth)
            if p <= 0:
                continue
            # Depth and source belong in the key: without them the same seat
            # draws the identical number at every hop, making depth-2 nearly
            # impossible whenever depth-1 already failed.
            rng = random_source.rng_for(
                session_id, seat_id=target, round_no=round_no,
                purpose=f"ips.cascade.d{depth}:{source}", base_seed=seed,
            )
            if rng.random() >= p:
                continue
            seen.add(target)
            next_tier = _downgrade(tier, depth)
            available = budget.available(next_tier, target) if budget is not None else True
            kind = "warmth" if (burst.get("aftermath") or {}).get("strategy") == "regret" else "humiliation"
            s_k = round(formulas.clip(ips.IMPACT_KINDS[kind] * beta_surprise(burst), 0.0, 1.0), 4)
            applied = ips.apply_provocation(
                target_state, source, kind, s_k, round_no, seq=burst.get("improv_id")
            )
            entry = {
                "agent": target, "source": source, "depth": depth,
                "tier": next_tier, "probability": p, "impulse": s_k,
                "budget": "available" if available else "deferred",
            }
            cascade.append(entry)
            if not available:
                deferred.append(target)
            else:
                budget.consume(next_tier, target)
            acc = target_state["accumulator"]
            acc["tier_cap"] = next_tier
            rel = target_state.setdefault("relations", {}).setdefault(source, dict(REL_DEFAULT))
            _bump(target_state, rel, "fear", 0.10 / depth, REL_DOMAIN, changes,
                  target, target=source, reason="ips.cascade")
            emo = target_state.setdefault("emotion", {})
            _bump(target_state, emo, "arousal", 0.20, EMO_DOMAIN, changes,
                  target, reason="ips.cascade")
            if depth < CASCADE_MAX_DEPTH:
                sources.append((target, depth + 1))
    return {"cascade": cascade, "changes": changes, "deferred": deferred}


def resolve(states, burst, round_no=0, events=None, seed=None, budget=None,
            session_id=""):
    """One-call aftermath settlement; attaches results back onto ``burst``."""
    consequence = apply_consequences(states, burst, round_no, seed=seed)
    actor = states.get(burst.get("seat"))
    imprint = imprint_memory(actor, burst, round_no)
    drift = drift_self_concept(actor, burst, round_no)
    chain = maybe_cascade(states, burst, round_no, seed=seed, budget=budget,
                          session_id=session_id)
    aft = burst.setdefault("aftermath", {})
    aft["strategy_effects"] = {
        "beta": consequence["beta"],
        "lie_events": consequence["lie_events"],
        "heat_delta": consequence["heat_delta"],
        "irreversible": consequence["irreversible"],
    }
    aft["memory_imprint"] = imprint
    aft["self_concept_delta"]["applied"] = drift["applied"]
    aft["self_concept_delta"]["l1_norm"] = drift["l1_norm"]
    aft["cascade"] = chain["cascade"]
    aft["cascade_deferred"] = chain["deferred"]
    return {
        "changes": consequence["changes"] + chain["changes"],
        "heat_delta": consequence["heat_delta"],
        "imprint": imprint,
        "drift": drift,
        "cascade": chain["cascade"],
        "deferred": chain["deferred"],
    }
