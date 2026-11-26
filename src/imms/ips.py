"""IPS P0 (G17): leaky accumulator + always-on delivery perturbation.

This module is the first fusion layer of the Improvisational Perturbation
System.  It deliberately stops short of triggering overt improvisation (G18):
events feed a per-seat ``accumulator`` through the original ``s_k`` impact
table, and every AI seat gets a deterministic ``delivery`` descriptor each
round.  ``delivery`` is expression metadata only -- it never changes intent,
claimed facts, beliefs or TruthGraph.
"""

import math

from . import formulas, random_source

PARAMS = {
    "rho0": 0.90,
    "theta0": 0.60,
    "rho_min": 0.50,
    "rho_max": 0.95,
    "theta_min": 0.30,
    "theta_max": 0.95,
    "amax": 1.5,
    "refractory_rounds": 2,
    "refractory_multiplier": 1.4,
    "deliberate_chi": 1.2,
    "passive_chi": 0.5,
    "trauma_chi": 1.8,
    "grudge_gamma": 0.5,
    "intimacy_kappa": 0.6,
    "micro_base": 0.25,
    "micro_arousal": 0.30,
    "micro_intoxication": 0.20,
}

# §4.2 impact table.  Values are positive magnitudes; warmth is a positive
# improvisation source, not a penalty.
IMPACT_KINDS = {
    "humiliation": 0.32,
    "exposure": 0.38,
    "false_accusation": 0.26,
    "betrayal": 0.45,
    "secret_partial": 0.50,
    "ignored": 0.06,
    "credit_steal": 0.14,
    "trauma": 0.28 * 1.8,
    "warmth": 0.20,
    "injustice": 0.18,
    "intoxication": 0.10,
    "death_threat": 0.40,
}

ACT_IMPACT = {
    "accuse": "false_accusation",
    "deflect": "ignored",
    "confess": "secret_partial",
    "ally": "warmth",
}

GM_IMPACT = {
    "second_body": "death_threat",
    "blackout": "injustice",
    "anonymous_tip": "exposure",
    "old_flame": "warmth",
    "drunken_slip": "secret_partial",
}

# §7.5 safe library: zero cost, zero risk, always available.
SAFE_LIBRARY = (
    "凝视不语",
    "缓缓放下茶盏",
    "指尖敲桌三下",
    '改口"你"为"您"',
    "望向窗外",
    "重复对方最后三个字",
    "忽然一笑随即收敛",
)

MAX_APPLIED = 24


def _num(value, default=0.5):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def new_accumulator(attrs, round_no=0):
    """Build a fresh leaky accumulator from canon OCEAN traits.

    High C / low N leaks fast (hard to provoke); low C / high N leaks slowly
    and produces frequent small bursts.  The same formula yields both types.
    """
    C = _num(attrs.get("OCEAN.C"))
    N = _num(attrs.get("OCEAN.N"))
    rho = formulas.clip(
        PARAMS["rho0"] * (1 - 0.30 * C - 0.20 * (1 - N)),
        PARAMS["rho_min"], PARAMS["rho_max"],
    )
    theta = formulas.clip(
        PARAMS["theta0"] * (1 + 0.35 * C - 0.30 * N),
        PARAMS["theta_min"], PARAMS["theta_max"],
    )
    return {
        "A": 0.0,
        "rho": round(rho, 4),
        "theta": round(theta, 4),
        "theta_base": round(theta, 4),
        "refractory_until": int(round_no),
        "applied": [],
    }


def _acc_for(state):
    acc = state.get("accumulator")
    if not isinstance(acc, dict):
        acc = new_accumulator(state.get("l1", {}), state.get("round", 0))
        state["accumulator"] = acc
    return acc


def effective_theta(acc, round_no):
    """Refractory window multiplies the threshold; no state mutation."""
    base = _num(acc.get("theta_base"), _num(acc.get("theta"), 0.6))
    if int(round_no) <= int(acc.get("refractory_until", 0) or 0):
        multiplier = PARAMS["refractory_multiplier"]
    else:
        multiplier = 1.0
    return round(formulas.clip(base * multiplier, PARAMS["theta_min"], PARAMS["theta_max"]), 4)


def classify_impact(event, state):
    """Return ``(kind, s_k, chi, kappa, gamma)`` for one observed event.

    Explicit host-injected ``payload['impact']`` wins; it is validated against
    the known kinds and the [0, 1] magnitude domain.  Unknown or harmless
    events yield ``s_k = 0`` rather than guessing an impact.
    """
    payload = event.get("payload") or {}
    actor = event.get("actor") or ""
    seat_id = state.get("seat_id", "")
    explicit = payload.get("impact")
    kind = None
    if isinstance(explicit, dict):
        candidate = explicit.get("kind")
        if candidate in IMPACT_KINDS:
            kind = candidate
    if kind is None:
        if event.get("act_type") == "gm_event_injection":
            kind = GM_IMPACT.get(payload.get("event_id") or payload.get("label"))
        elif event.get("act_type") == "clue_release":
            clue = str(payload.get("clue") or "")
            if state.get("character_id") and state["character_id"] in clue:
                kind = "exposure"
        else:
            kind = ACT_IMPACT.get(event.get("act_type"))
    if kind is None:
        return None, 0.0, 1.0, 1.0, 0.0

    s_k = IMPACT_KINDS[kind]
    if isinstance(explicit, dict) and "s_k" in explicit:
        override = _num(explicit["s_k"], -1)
        if 0 <= override <= 1:
            s_k = override

    targeted = bool(actor) and (
        payload.get("target") == seat_id
        or payload.get("target") == state.get("character_id")
    )
    chi = PARAMS["deliberate_chi"] if targeted else PARAMS["passive_chi"]
    trauma = (state.get("l1") or {}).get("lexicon.trauma") or []
    utterance = str(payload.get("utterance") or "")
    if isinstance(trauma, str):
        trauma = [trauma]
    if any(str(word) and str(word) in utterance for word in trauma):
        chi *= PARAMS["trauma_chi"]

    relation = (state.get("relations") or {}).get(actor) or {}
    kappa = 1 + PARAMS["intimacy_kappa"] * _num(relation.get("intimacy"), 0.0)
    gamma = _num(relation.get("grudge"), 0.0)
    return kind, round(s_k, 4), round(chi, 4), round(kappa, 4), round(gamma, 4)


def accumulate(state, events, round_no, session_id="", seed=None):
    """Apply observed events to the accumulator.  Pure w.r.t. facts/behaviour.

    Only the ``accumulator`` field is mutated.  G17 never consumes the
    threshold to trigger an overt act -- that gate belongs to G18.
    """
    if state.get("is_human"):
        return None
    acc = _acc_for(state)
    rho = _num(acc.get("rho"), 0.9)
    A = _num(acc.get("A"), 0.0) * rho
    applied = []
    for event in events or []:
        if event.get("actor") == state.get("seat_id"):
            continue
        kind, s_k, chi, kappa, gamma = classify_impact(event, state)
        if s_k <= 0:
            continue
        contribution = s_k * chi * kappa * (1 + PARAMS["grudge_gamma"] * gamma)
        A += contribution
        applied.append({
            "seq": event.get("seq"),
            "round": event.get("round", round_no),
            "actor": event.get("actor"),
            "kind": kind,
            "s_k": s_k,
            "chi": chi,
            "kappa": kappa,
            "gamma": round(gamma, 4),
            "contribution": round(contribution, 4),
        })
    acc["A"] = round(formulas.clip(A, 0.0, PARAMS["amax"]), 4)
    acc["theta"] = effective_theta(acc, round_no)
    acc["applied"] = (acc.get("applied") or []) + applied
    acc["applied"] = acc["applied"][-MAX_APPLIED:]
    return acc


def micro_perturb(state, session_id="", round_no=0, seed=None, base_seed=None, event_seq=None):
    """Build the always-on §9 expression descriptor from explicit fields.

    No unseeded RNG: the draw comes from ``random_source.rng_for`` keyed by
    session/seat/round/seq/purpose.  ``seed`` is accepted as an alias for
    ``base_seed``.  The result is stored only in ``delivery``.
    """
    if base_seed is None:
        base_seed = seed
    if state.get("is_human"):
        return None
    emo = state.get("emotion") or {}
    arousal = _num(emo.get("arousal"), 0.2)
    stress = _num(emo.get("stress"), 0.1)
    load = _num(emo.get("cognitive_load"), 0.1)
    valence = _num(emo.get("valence"), 0.0)
    intox = _num(emo.get("intoxication"), 0.0)
    p = formulas.clip(
        PARAMS["micro_base"] + PARAMS["micro_arousal"] * arousal
        + PARAMS["micro_intoxication"] * intox,
        0.0, 1.0,
    )
    rng = random_source.rng_for(
        session_id, seat_id=state.get("seat_id"), round_no=round_no,
        event_seq=event_seq, purpose="ips.micro_perturb", base_seed=base_seed,
    )
    hit = rng.random() < p
    item = random_source.weighted_choice(
        rng, {name: 1.0 for name in SAFE_LIBRARY}, default=None
    ) if hit else None
    acc = state.get("accumulator") or {}
    overshoot = max(0.0, _num(acc.get("A"), 0.0) - _num(acc.get("theta"), 0.6))
    return {
        "source": "ips:micro",
        "session_id": session_id,
        "round": int(round_no),
        "probability": round(p, 4),
        "hit": bool(hit),
        "item": item,
        "params": {
            "sentence_length_factor": round(1 - 0.40 * arousal, 4),
            "ellipsis_density_factor": round(1 + 1.2 * stress, 4),
            "question_density_factor": round(1 + 0.9 * arousal * (1 if valence < 0 else 0), 4),
            "self_corrections": int(3 * max(0.0, min(1.0, load))),
            "honorific_step": int(min(1.0, overshoot * 2)),
            "pet_phrase_factor": round(1 + 0.6 * arousal, 4),
            "vernacular_probability": round(formulas.clip(0.3 * intox + 0.2 * stress, 0, 1), 4),
            "inversion_probability": round(formulas.clip(0.25 * arousal, 0, 1), 4),
            "pause_marks": int(2 * max(0.0, min(1.0, stress))),
            "verbal_flourish_factor": round(1 - 0.3 * max(0.0, min(1.0, load)), 4),
        },
    }
