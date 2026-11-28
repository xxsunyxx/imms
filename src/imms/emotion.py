"""Trauma triggers, PAD updates and discrete emotion mapping (F08/F09).

All triggers come from explicit script fields (``trauma[]``).  Nothing in prose
is treated as a landmine.  The numeric multipliers are implementation choices
documented here: a hit scales the impact by 1.8 and stretches tau by the same
factor, following ORIGINAL_SPEC §4.3.  A hit is always auditable as a GM-only
``trauma_trigger`` event; only the resulting numeric deltas reach L3.
"""

from . import formulas

TRAUMA_MULTIPLIER = 1.8
BASE_TAU = 2.0
AROUSAL_THRESHOLD = 0.6
VALENCE_THRESHOLD = -0.1
DOMINANCE_THRESHOLD = 0.5


def trauma_marker(entry):
    if isinstance(entry, dict):
        return str(entry.get("trigger") or entry.get("marker") or entry.get("term") or "")
    return str(entry or "")


def match_trauma(entries, text):
    """Return the first explicit trauma entry whose marker appears in text."""
    text = str(text or "")
    for entry in entries or []:
        marker = trauma_marker(entry)
        if len(marker) >= 2 and marker in text:
            return entry
    return None


def discrete_from_pad(emo):
    """Map PAD regions onto the four named states in ORIGINAL_SPEC §4.3."""
    emo = emo or {}
    valence = float(emo.get("valence", 0.0))
    arousal = float(emo.get("arousal", 0.0))
    dominance = float(emo.get("dominance", 0.5))
    if arousal >= AROUSAL_THRESHOLD and valence <= VALENCE_THRESHOLD:
        return "愤怒" if dominance >= DOMINANCE_THRESHOLD else "恐惧"
    if valence <= -0.2:
        return "悲伤"
    if valence >= 0.2 and arousal >= 0.5:
        return "兴奋"
    if valence >= 0.2:
        return "愉悦"
    return "平静"


def apply_impact(state, impact, text, round_no, tag="事件评价"):
    """Apply one PAD impact with optional trauma amplification."""
    before = dict(state.get("emotion") or {})
    hit = match_trauma(state.get("trauma") or [], text)
    multiplier = TRAUMA_MULTIPLIER if hit else 1.0
    tau = BASE_TAU * multiplier
    scaled = {
        key: float(value) * multiplier
        for key, value in (impact or {}).items() if key in formulas.PAD_DOMAIN
    }
    after = formulas.pad_update(before, tau, 1.0, scaled)
    after.update({k: v for k, v in before.items() if k not in formulas.PAD_DOMAIN})
    after["discrete"] = discrete_from_pad(after)
    state["emotion"] = after
    report = {
        "round": int(round_no), "tag": tag, "hit": bool(hit),
        "trigger": trauma_marker(hit) if hit else None,
        "multiplier": multiplier, "tau": round(tau, 4),
        "impact": {k: round(v, 4) for k, v in scaled.items()},
        "before": {k: round(float(before.get(k, 0.0)), 4) for k in formulas.PAD_DOMAIN},
        "after": {k: round(float(after.get(k, 0.0)), 4) for k in formulas.PAD_DOMAIN},
        "discrete": after["discrete"],
    }
    if hit:
        state.setdefault("trauma_hits", []).append(report)
    return report


def refresh_discrete(state):
    """Keep the named emotion in sync after external PAD edits."""
    emo = state.setdefault("emotion", {})
    emo["discrete"] = discrete_from_pad(emo)
    return emo["discrete"]
