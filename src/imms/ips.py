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
    "eta": 0.60,
    "k_min": {"micro": 0.15, "meso": 0.40, "macro": 0.60},
    "tier_min": {"meso": 0.35, "macro": 0.70},
    "macro_per_act": 1,
    "meso_per_agent": 2,
    "macro_per_agent": 2,
    "cascade_depth": 2,
    "refractory_rounds": 2,
}

TIER_RANK = {"micro": 0, "meso": 1, "macro": 2}

PROVOCATION_KINDS = {
    "provoke": "humiliation",
    "provoke_warmth": "warmth",
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


# ---------------------------------------------------------------------------
# G18: overt improvisation gate, budget and aftermath
# ---------------------------------------------------------------------------

def intensity(acc, state, events=None, round_no=0):
    """§4.3 burst intensity.  Zero when the accumulator has not crossed theta."""
    A = _num(acc.get("A"), 0.0)
    theta = effective_theta(acc, round_no)
    if A < theta:
        return 0.0
    peak = 0.0
    for item in acc.get("applied") or []:
        if round_no is None or item.get("round", round_no) == round_no:
            peak = max(peak, _num(item.get("s_k"), 0.0))
    for event in events or []:
        kind, s_k, _, _, _ = classify_impact(event, state)
        peak = max(peak, s_k)
    arousal = _num((state.get("emotion") or {}).get("arousal"), 0.0)
    return round(formulas.clip(
        0.7 * (A - theta) / max(0.05, 1 - theta) + 0.6 * peak + 0.25 * arousal,
        0.0, 1.0,
    ), 4)


def tier_for(I):
    if I >= PARAMS["tier_min"]["macro"]:
        return "macro"
    if I >= PARAMS["tier_min"]["meso"]:
        return "meso"
    return "micro"


def epsilon(I, acting_skill=0.5):
    """§8.1 amplitude; good actors leak less."""
    base = formulas.clip(0.15 + 0.85 * _num(I), 0.1, 1.0)
    return round(base * (1 - 0.3 * _num(acting_skill, 0.5)), 4)


def kairos(state, heat=0.0, target=0.0, events=None, stage="roundtable"):
    """§8.3 timing window, with a rule-proxy for semantic relevance (B2)."""
    suspicion = (state.get("suspicion") or {}).values()
    attention = max(suspicion, default=0.1)
    seat = state.get("seat_id")
    character = state.get("character_id")
    for event in events or []:
        payload = event.get("payload") or {}
        if payload.get("target") in (seat, character):
            attention = 1.0
    rel = 0.8  # no embedder: deterministic rule proxy, never fabricated evidence
    gap = formulas.clip(1 - _num(heat) / max(0.001, _num(target)), 0.0, 1.0)
    insertable = 0.0 if stage == "vote" else 1.0
    return round(formulas.clip(attention * rel * (1 + gap) * insertable, 0.0, 1.0), 4)


class ImprovBudget:
    """§8.2 token pool.  Serializable so it can live in game_state."""

    def __init__(self, data=None):
        data = data or {}
        self.macro_in_act = int(data.get("macro_in_act", 0) or 0)
        self.meso_used = dict(data.get("meso_used") or {})
        self.macro_used = dict(data.get("macro_used") or {})

    def available(self, tier, seat):
        if tier == "micro":
            return True
        if tier == "meso":
            return int(self.meso_used.get(seat, 0)) < PARAMS["meso_per_agent"]
        if tier == "macro":
            return (
                int(self.macro_in_act) < PARAMS["macro_per_act"]
                and int(self.macro_used.get(seat, 0)) < PARAMS["macro_per_agent"]
            )
        return False

    def consume(self, tier, seat):
        if tier == "meso":
            self.meso_used[seat] = int(self.meso_used.get(seat, 0)) + 1
        elif tier == "macro":
            self.macro_used[seat] = int(self.macro_used.get(seat, 0)) + 1
            self.macro_in_act += 1

    def to_dict(self):
        return {
            "macro_in_act": self.macro_in_act,
            "meso_used": self.meso_used,
            "macro_used": self.macro_used,
        }


def _aftermath_strategy(state, seed, round_no):
    """§11.2 three-way softmax, deterministic and L1-read-only."""
    attrs = state.get("l1") or {}
    emo = state.get("emotion") or {}
    honesty = _num(attrs.get("values.honesty"), 0.5)
    loyalty = _num(attrs.get("values.loyalty"), 0.5)
    acting = _num(attrs.get("acting_skill"), 0.5)
    z = {
        "regret": 1.2 * _num(attrs.get("OCEAN.N"), 0.5) + 0.8 * _num(attrs.get("OCEAN.C"), 0.5)
                  + 1.0 * honesty - 0.6 * _num(emo.get("arousal"), 0.0),
        "rationalize": 1.0 * _num(attrs.get("OCEAN.O"), 0.5) + 1.2 * acting
                       + 0.6 * (1 - _num(attrs.get("OCEAN.N"), 0.5)),
        "escalate": 1.3 * _num(emo.get("exposure_risk"), 0.0)
                    + 1.0 * (1 - _num(emo.get("morale"), 0.8))
                    + 0.9 * _num(emo.get("arousal"), 0.0) - 0.8 * loyalty,
    }
    probs = formulas.softmax(z, 0.5)
    rng = random_source.rng_for(
        state.get("session_id", ""), seat_id=state.get("seat_id"), round_no=round_no,
        purpose="ips.aftermath", base_seed=seed,
    )
    return random_source.weighted_choice(rng, probs, default="regret")


def resolve_bursts(states, round_no, events, heat, target, budget, public_facts,
                   session_id="", seed=None, stage="roundtable"):
    """§8 token competition + §10 IR1/IR2/IR5 guard + §11 aftermath.

    Only the highest-scoring eligible seat bursts this round; everyone else
    keeps their accumulator and gets a ``suppressed_burst`` audit entry.
    Overt improvisation is a separate event: it never rewrites a speech act.
    """
    candidates = []
    suppressed = []
    for seat, state in states.items():
        if state.get("is_human"):
            continue
        acc = state.get("accumulator")
        if not isinstance(acc, dict):
            continue
        I = intensity(acc, state, events, round_no)
        if I <= 0:
            continue
        tier = tier_for(I)
        K = kairos(state, heat, target, events, stage)
        if K <= PARAMS["k_min"][tier]:
            suppressed.append({"seat": seat, "reason": "kairos_below_min", "tier": tier,
                               "kairos": K, "intensity": I, "accumulator": acc["A"]})
            continue
        if not budget.available(tier, seat):
            suppressed.append({"seat": seat, "reason": "token_unavailable", "tier": tier,
                               "kairos": K, "intensity": I, "accumulator": acc["A"]})
            continue
        score = round(I * 0.6 + _num(acc.get("A"), 0) * 0.4 + K * 0.2, 4)
        candidates.append({"seat": seat, "state": state, "acc": acc, "I": I,
                           "tier": tier, "K": K, "score": score})
    if not candidates:
        return {"bursts": [], "suppressed": suppressed, "changes": [], "budget": budget.to_dict()}

    candidates.sort(key=lambda c: (-c["score"], c["seat"]))
    winner = candidates[0]
    for item in candidates[1:]:
        suppressed.append({"seat": item["seat"], "reason": "token_contention",
                           "tier": item["tier"], "kairos": item["K"],
                           "intensity": item["I"], "accumulator": item["acc"]["A"]})

    state = winner["state"]
    acc = winner["acc"]
    seat = winner["seat"]
    I, tier, K = winner["I"], winner["tier"], winner["K"]
    # IR5: an overt act with nobody to observe it cannot have an aftermath,
    # so it is not allowed to consume a token.
    if tier != "micro" and not any(
        s != seat and not states[s].get("is_human") for s in states
    ):
        suppressed.append({"seat": seat, "reason": "ir5_no_observer", "tier": tier,
                           "kairos": K, "intensity": I, "accumulator": acc["A"]})
        return {"bursts": [], "suppressed": suppressed, "changes": [],
                "budget": budget.to_dict()}
    eps = epsilon(I, (state.get("l1") or {}).get("acting_skill", 0.5))
    # IR1/IR2: claim-only intersection with known facts; no TruthGraph write.
    known = set(state.get("beliefs") or {}) | set(public_facts or [])
    claimed = sorted(f for f in known)[:1] if tier != "micro" else []
    strategy = _aftermath_strategy(state, seed, round_no)
    observers = [s for s in states if s != seat and not states[s].get("is_human")]
    observer_delta = {}
    changes = []
    for other in observers[:3]:
        delta = {
            "trust": -0.12 if strategy == "escalate" else 0.05,
            "arousal": round(0.1 + 0.2 * I, 4),
        }
        observer_delta[other] = delta
        changes.append({"seat_id": other, "character_id": states[other].get("character_id"),
                        "dim": "trust", "before": _num((states[other].get("relations") or {}).get(seat, {}).get("trust"), 0.0),
                        "after": round(_num((states[other].get("relations") or {}).get(seat, {}).get("trust"), 0.0) + delta["trust"], 4),
                        "target": seat})
    before_A = _num(acc.get("A"), 0.0)
    acc["A"] = round(before_A * (1 - PARAMS["eta"]), 4)
    acc["refractory_until"] = int(round_no) + PARAMS["refractory_rounds"]
    budget.consume(tier, seat)
    burst = {
        "improv_id": f"imp_{session_id}_{round_no}_{seat}",
        "session_id": session_id,
        "round": int(round_no),
        "seat": seat,
        "tier": tier,
        "action_key": "claim_only" if tier != "micro" else "safe_library",
        "trigger": {
            "accumulator_before": round(before_A, 4),
            "theta": acc["theta"],
            "intensity": I,
            "focus_target": next(iter(observer_delta), None),
        },
        "epsilon": eps,
        "timing": {"kairos": K},
        "speech_act": {
            "claimed_facts": claimed,
            "is_unverified_claim": bool(claimed),
        },
        "guard": {"IR1": "pass_claim_only", "IR2": "pass_known_subset", "IR5": "pass"},
        "aftermath": {
            "strategy": strategy,
            "self_surprise": round(_num(I) * (1 - _num((state.get("l1") or {}).get("acting_skill"), 0.5)), 4),
            "memory_importance": round(0.6 + 0.4 * I, 4),
            # B1: audit only, never written back into L1 canon.
            "self_concept_delta": {"intensity": round(0.05 * I, 4)},
            "observers_delta": observer_delta,
            "cascade": [],
        },
        "seed": seed,
    }
    return {"bursts": [burst], "suppressed": suppressed, "changes": changes,
            "budget": budget.to_dict()}


def apply_provocation(target_state, source, kind, s_k, round_no, seq=None):
    """G24: inject a shock into the target's accumulator, never the speaker's.

    The human seat stays outside IPS; a human utterance can still ignite an AI
    by writing the impact into the target's event view.
    """
    if target_state.get("is_human") or target_state.get("accumulator") is None:
        return None
    acc = target_state["accumulator"]
    before = _num(acc.get("A"), 0.0)
    event = {
        "seq": seq, "round": round_no, "actor": source,
        "act_type": "provoke",
        "payload": {"target": target_state.get("seat_id"),
                    "impact": {"kind": kind, "s_k": s_k}},
    }
    accumulate(target_state, [event], round_no)
    return {"seat": target_state.get("seat_id"), "source": source, "kind": kind,
            "s_k": s_k, "before": round(before, 4), "after": acc["A"],
            "refractory": int(round_no) <= int(acc.get("refractory_until", 0) or 0)}
