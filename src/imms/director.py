"""G21: director dials (IPS §14.2) and default style presets.

Five operator knobs are stored on ``session.l0['dials']`` and copied into each
seat's ``l0`` at runtime so every downstream rule reads the same value.  Dial
effects are deterministic multipliers on existing parameters -- no new random
source, no truth-graph write, no L1 mutation:

* ``improv_gain``  -> theta0 is scaled inversely (more gain, lower threshold)
* ``chaos``        -> aftermath softmax temperature and cascade base p0 rise
* ``shadow_sensitivity`` -> global multiplier on zeta_f in shadow reversal
* ``leak_rate``    -> multiplier on epsilon (mask-slip amplitude)
* ``macro_quota``  -> token pool ceiling (also consumed by G22 breakout)

The section-level effects are applied in their own modules; this module is the
single source of validated values so replay always knows what was in force.
"""

import math

# (knob, min, max, default)
RANGES = {
    "improv_gain": (0.5, 2.0, 1.0),
    "chaos": (0.0, 1.0, 0.4),
    "shadow_sensitivity": (0.0, 1.0, 0.6),
    "leak_rate": (0.0, 1.0, 0.5),
    "macro_quota": (0, 3, 1),
}

PRESETS = {
    "honkaku": {
        "name": "本格冷推理", "improv_gain": 0.6, "chaos": 0.2,
        "shadow_sensitivity": 0.3, "leak_rate": 0.4, "macro_quota": 1,
    },
    "shakaiha": {
        "name": "社会派人性剧", "improv_gain": 1.2, "chaos": 0.4,
        "shadow_sensitivity": 0.8, "leak_rate": 0.7, "macro_quota": 1,
    },
    "guofeng": {
        "name": "国风宅斗", "improv_gain": 1.4, "chaos": 0.3,
        "shadow_sensitivity": 0.9, "leak_rate": 0.9, "macro_quota": 1,
    },
    "farce": {
        "name": "闹剧本", "improv_gain": 1.8, "chaos": 0.8,
        "shadow_sensitivity": 0.7, "leak_rate": 0.8, "macro_quota": 2,
    },
    "emotional": {
        "name": "情感本", "improv_gain": 1.3, "chaos": 0.5,
        "shadow_sensitivity": 0.6, "leak_rate": 1.0, "macro_quota": 1,
    },
}
DEFAULT = {k: v[2] for k, v in RANGES.items()}
# Neutral multipliers used when a session has no dials at all.  Legacy
# sessions must keep their pre-G21 behaviour, so "absent" is not the same as
# "the default style preset".
NEUTRAL = {
    "improv_gain": 1.0, "chaos": 0.0,
    "shadow_sensitivity": 1.0, "leak_rate": 1.0, "macro_quota": 1,
}
DIAL_KEYS = tuple(RANGES)


def _num(value, default):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def normalize(data=None, preset=None):
    """Return a complete, range-clamped dial dict (never raises on bad input)."""
    values = dict(DEFAULT)
    if preset:
        cfg = PRESETS.get(preset)
        if cfg:
            for key in DIAL_KEYS:
                values[key] = cfg[key]
    for key, value in (data or {}).items():
        if key not in RANGES:
            continue
        lo, hi, default = RANGES[key]
        number = _num(value, default)
        number = max(lo, min(hi, number))
        values[key] = int(round(number)) if key == "macro_quota" else round(number, 4)
    return values


def from_session(session_doc):
    """Read explicit dials; ``None`` for a legacy session without any."""
    l0 = (session_doc or {}).get("l0") or {}
    data = l0.get("dials")
    return normalize(data) if data else None


def set_session_dials(session_doc, data=None, preset=None):
    """Mutate and return the session doc with validated dials in ``l0``."""
    doc = session_doc or {}
    l0 = doc.setdefault("l0", {})
    l0["dials"] = normalize(data, preset=preset)
    if preset and preset in PRESETS:
        l0["style"] = preset
    # Keep the legacy top-level quota in sync for older modules.
    l0["macro_quota"] = l0["dials"]["macro_quota"]
    return doc


def theta_multiplier(dials):
    """improv_gain scales theta0 inversely; gain 2.0 halves the threshold."""
    if not dials:
        return 1.0
    return round(1.0 / max(0.1, _num(dials.get("improv_gain"), 1.0)), 4)


def epsilon_multiplier(dials):
    """leak_rate scales the mask-slip amplitude epsilon."""
    if not dials:
        return 1.0
    return round(max(0.0, _num(dials.get("leak_rate"), 1.0)), 4)


def cascade_multiplier(dials):
    """chaos scales the cascade base probability p0."""
    if not dials:
        return 1.0
    return round(1.0 + 0.5 * max(0.0, _num(dials.get("chaos"), 0.0)), 4)


def softmax_temperature(dials, base=0.5):
    """chaos widens the aftermath strategy draw without unseeding it."""
    if not dials:
        return float(base)
    chaos = max(0.0, _num(dials.get("chaos"), 0.0))
    return round(max(0.05, float(base) * (1.0 + chaos)), 4)


def shadow_multiplier(dials):
    """shadow_sensitivity scales zeta_f; zero disables reversal entirely."""
    if not dials:
        return 1.0
    return round(max(0.0, _num(dials.get("shadow_sensitivity"), 1.0)), 4)


def apply_to_accumulator(acc, dials):
    """Scale theta/theta_base once at accumulator creation (idempotent marker)."""
    if not isinstance(acc, dict) or not dials or acc.get("_dial_gain_applied"):
        return acc
    mult = theta_multiplier(dials)
    for key in ("theta", "theta_base"):
        if key in acc:
            acc[key] = round(max(0.3, min(0.95, float(acc[key]) * mult)), 4)
    acc["_dial_gain_applied"] = True
    return acc


def dials_for_state(state):
    """Explicit dials only; ``None`` means a legacy session with no dials."""
    data = (state.get("l0") or {}).get("dials")
    return normalize(data) if data else None
