"""G19: directed improvisation (mask gradient, shadow reversal, band-pass).

The original IPS §5 needs a direction so bursts are not random noise.  There is
no embedding service in this project (C1 freeze), so fidelity uses the same
rule proxy as the R2 guard: OCEAN/values centered at the 0.5 neutral baseline.
When a mask or ζ_f is missing the engine degrades explicitly -- it never fills
in a persona trait or a reversal coefficient it cannot evidence.
"""

from . import formulas, l4

S_MIN = 1.2
S_HI = 3.2
F_MIN = 0.72
ALIGN_MIN = 0.0
CANDIDATE_KEYS = ("canon_hold", "mask_step", "shadow_step")


def _num(value, default=0.0):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if -1e9 < v < 1e9 else default


def mask_gradient(state):
    """canon→mask gradient; empty when no mask is configured (no fabrication)."""
    delta, source, gaps = l4.mask_delta(state)
    if not source:
        return {}, None, ["mask_not_configured"]
    return delta, source, gaps


def shadow_reversal(state, zeta=None):
    """§5 shadow vector: -ζ_f × (canon − neutral).

    ζ_f must come from stored data (``l1.zeta_f`` / ``l0.zeta_f``) or be passed
    explicitly by the operator.  Missing ζ_f disables reversal; it is never
    assumed to be 1.0.
    """
    if zeta is None:
        l1 = state.get("l1") or {}
        l0 = state.get("l0") or {}
        zeta = l1.get("zeta_f", l0.get("zeta_f"))
    if zeta is None:
        return {"enabled": False, "zeta": None, "vector": {}, "reason": "zeta_missing"}
    z = formulas.clip(_num(zeta, 0.0), 0.0, 1.0)
    canon = l4.canon_vector(state)
    vector = {
        key: round(-z * canon[i], 6)
        for i, key in enumerate(l4.PERSONA_KEYS)
    }
    return {"enabled": z > 0, "zeta": z, "vector": vector, "reason": "ok"}


def fidelity(candidate, anchor):
    """Fidelity proxy in [0,1]; None when the anchor carries no information."""
    score = l4.cosine(candidate, anchor)
    if sum(x * x for x in anchor) < 1e-9:
        return None
    return round((score + 1) / 2, 4)


def surprise(events, delta, seat_id="", character_id=""):
    """§5 surprise proxy s from observed impacts plus the candidate's move."""
    peak = 0.0
    for event in events or []:
        payload = event.get("payload") or {}
        target = payload.get("target")
        if target and target not in (seat_id, character_id):
            continue
        impact = payload.get("impact") or {}
        if isinstance(impact, dict):
            peak = max(peak, _num(impact.get("s_k"), 0.0))
        elif event.get("act_type") in {"accuse", "betray", "lie_exposed"}:
            peak = max(peak, 0.45)
    move = sum(abs(_num(v)) for v in (delta or {}).values())
    return round(formulas.clip(S_MIN + 2.2 * peak + 0.8 * move, 0.0, 5.0), 4)


def direction_alignment(delta, gradient, shadow):
    """Dot product of the candidate move with the active direction vector."""
    target = {
        k: _num(gradient.get(k), 0.0) + _num(shadow.get(k), 0.0)
        for k in l4.PERSONA_KEYS
    }
    delta_v = [_num(delta.get(k), 0.0) for k in l4.PERSONA_KEYS]
    target_v = [target[k] for k in l4.PERSONA_KEYS]
    return round(l4.cosine(delta_v, target_v), 4)


def _candidate(action, delta, canon, events, state, gradient, shadow, gaps,
               s_hi, f_min):
    persona = [
        formulas.clip(canon[i] + _num(delta.get(key), 0.0), 0.0, 1.0)
        for i, key in enumerate(l4.PERSONA_KEYS)
    ]
    F = fidelity(persona, canon)
    s = surprise(events, delta, state.get("seat_id", ""), state.get("character_id", ""))
    align = direction_alignment(delta, gradient, shadow.get("vector") or {})
    reasons = []
    if not gradient:
        reasons.append("mask_missing")
    if F is None:
        reasons.append("anchor_flat")
    elif F < f_min:
        reasons.append("fidelity_below_min")
    if s < S_MIN:
        reasons.append("surprise_below_min")
    if s > s_hi:
        reasons.append("surprise_above_max")
    if align <= ALIGN_MIN:
        reasons.append("direction_not_aligned")
    if action == "shadow_step" and not shadow.get("enabled"):
        reasons.append("zeta_missing")
    return {
        "action": action,
        "delta": {k: round(_num(delta.get(k)), 6) for k in l4.PERSONA_KEYS},
        "shown_persona": dict(zip(l4.PERSONA_KEYS, (round(v, 4) for v in persona))),
        "surprise": s,
        "fidelity": F,
        "alignment": align,
        "accepted": not reasons,
        "reject_reasons": reasons,
        "gaps": list(gaps),
    }


def select(state, events=None, round_no=0, seed=None, s_hi=S_HI, f_min=F_MIN,
           zeta=None):
    """§5 candidate generation + band-pass; fully deterministic and pure."""
    gradient, mask_source, gaps = mask_gradient(state)
    shadow = shadow_reversal(state, zeta=zeta)
    if not shadow.get("enabled"):
        gaps.append("zeta_missing")
    canon = l4.canon_vector(state)
    deltas = {
        "canon_hold": {},
        "mask_step": {k: round(0.6 * v, 6) for k, v in gradient.items()},
        "shadow_step": {k: round(0.8 * v, 6) for k, v in shadow["vector"].items()},
    }
    candidates = [
        _candidate(key, deltas[key], canon, events, state, gradient, shadow,
                   gaps, s_hi, f_min)
        for key in CANDIDATE_KEYS
    ]
    accepted = [c for c in candidates if c["accepted"]]
    accepted.sort(key=lambda c: (
        -0.4 * c["fidelity"] - 0.4 * c["alignment"]
        - 0.2 * (c["surprise"] - S_MIN) / max(0.01, s_hi - S_MIN),
        c["action"],
    ))
    return {
        "round": int(round_no),
        "seed": seed,
        "mask_source": mask_source,
        "shadow": shadow,
        "s_range": [S_MIN, s_hi],
        "f_min": f_min,
        "candidates": candidates,
        "selected": accepted[0] if accepted else None,
        "gaps": sorted(set(gaps)),
    }


def apply_to_burst(burst, state, events=None, round_no=0, seed=None,
                   s_hi=S_HI, f_min=F_MIN, zeta=None):
    """Attach a direction decision to an improv burst without changing facts.

    IR1/IR2 remain the caller's responsibility: this only chooses an action
    descriptor, never a new fact, target or numeric claim.
    """
    result = select(state, events, round_no, seed, s_hi=s_hi, f_min=f_min, zeta=zeta)
    burst["direction"] = result
    chosen = result["selected"]
    if chosen:
        burst["action_key"] = chosen["action"]
        burst["speech_act"]["direction_delta"] = chosen["delta"]
    else:
        burst["action_key"] = "safe_library"
    return burst
