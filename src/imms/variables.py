"""Variable computation: data-driven, deterministic, no side effects.

Runtime state is the single source of truth; this module reads it and computes
derived variables through formulas.py.  It never mutates state and never writes
to the database -- callers decide what to persist via attribute_events/game_state.

Guides drive variation; this module only reports the numbers that variation is
constrained by.  Missing data falls back to neutral and is reported in ``gaps``.
"""

from . import formulas

NEUTRAL = 0.5


def _num(value, default=0.0):
    return value if isinstance(value, (int, float)) else default


def emotion_vector(state):
    """Compute PAD + stress/arousal derived signals from an emotion dict."""
    emo = state.get("emotion") or {}
    arousal = _num(emo.get("arousal"))
    stress = _num(emo.get("stress"))
    l0 = state.get("l0") or {}
    acting = _num(l0.get("acting_skill"), NEUTRAL)
    sigma = formulas.leak_sigma(0.05, arousal, stress, acting)
    temperature = formulas.decision_temperature(1.0, arousal, _num(emo.get("intoxication")))
    return {
        "valence": _num(emo.get("valence")),
        "arousal": arousal,
        "dominance": _num(emo.get("dominance"), NEUTRAL),
        "stress": stress,
        "leak_sigma": round(sigma, 4),
        "decision_temperature": round(temperature, 4),
        "discrete_emotion": discrete_emotion(emo),
    }


def discrete_emotion(emo):
    """PAD -> coarse label (F09).  Positive valence overrides for calm/joy."""
    valence = _num(emo.get("valence"))
    arousal = _num(emo.get("arousal"))
    dominance = _num(emo.get("dominance"), NEUTRAL)
    if arousal >= 0.6 and valence < 0:
        return "愤怒" if dominance >= 0.5 else "恐惧"
    if valence < -0.4:
        return "悲伤"
    if arousal >= 0.6 and valence > 0:
        return "兴奋"
    if valence > 0.4:
        return "平静"
    return "平静"


def suspicion_snapshot(state):
    """Top suspicion targets with probability normalisation (F12/F13/F16)."""
    raw = {k: _num(v) for k, v in (state.get("suspicion") or {}).items()}
    log_odds = {k: __import__("math").log(max(v, 1e-6) / max(1 - v, 1e-6)) for k, v in raw.items()} if raw else {}
    temp = emotion_vector(state)["decision_temperature"]
    probs = formulas.vote_distribution(log_odds, temp) if log_odds else {}
    ranked = sorted(raw.items(), key=lambda kv: -kv[1])
    return {
        "top": ranked[:4],
        "probabilities": {k: round(v, 4) for k, v in probs.items()},
    }


def relation_summary(state):
    """Aggregate directed relations for the currently held suspicion focus."""
    top = suspicion_snapshot(state)["top"]
    focus = top[0][0] if top else None
    rel = (state.get("relations") or {}).get(focus, {}) if focus else {}
    return {
        "focus": focus,
        "like": _num(rel.get("like")),
        "trust": _num(rel.get("trust")),
        "suspicion": _num(rel.get("suspicion")),
        "kappa": round(formulas.kappa(_num(rel.get("intimacy"))), 4),
    }


def tension_snapshot(state, abilities=None):
    """F01 tension from canon vs mask gap weighted by exposure."""
    l1 = state.get("l1") or {}
    mask = l1.get("mask") or l1.get("L2.mask") or {}
    if not isinstance(mask, dict):
        return {"tension": 0.0, "gaps": ["mask_not_configured"]}
    keys = [k for k in l1 if k.startswith("OCEAN.")]
    canon = [_num(l1.get(k), NEUTRAL) for k in keys]
    mask_v = [_num(mask.get(k), NEUTRAL) for k in keys]
    exposure = [emotion_vector(state)["stress"] for _ in keys]
    weight = [1.0 / max(1, len(keys))] * len(keys)
    return {
        "tension": round(formulas.tension(weight, canon, mask_v, exposure), 4),
        "gaps": [] if canon else ["no_ocean_values"],
    }


def snapshot(state, abilities=None):
    """Full variable snapshot for one seat, ready for guides/replay/retro."""
    emo = emotion_vector(state)
    return {
        "seat_id": state.get("seat_id"),
        "character_id": state.get("character_id"),
        "round": state.get("round"),
        "emotion": emo,
        "suspicion": suspicion_snapshot(state),
        "relation": relation_summary(state),
        "tension": tension_snapshot(state, abilities=abilities),
        "gaps": [],
    }
