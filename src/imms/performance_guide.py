"""State -> wording-only performance guides for the speech prompt.

This module is the missing bridge between intent/suspicion/emotion (which IMMS
owns and computes) and the guide block (which only changes *how* something is
said).  It is a pure derivation: every line comes from a stored state value or
the project lexicon; nothing here invents a fact, a number or a dialogue line.

Safety contract, enforced by ``sanitize`` before strings reach the prompt:
  * no fact ids (`f_...`), no `key=value` assignments, no digit-only payloads;
  * no quoted dialogue lines, no character names invented as speakers;
  * guides stay under the 200-char budget of prompt_engine._sanitize_guide.
"""

import json
import os

_LEXICON = None


def lexicon(config_dir=None):
    """Load the wording map (read-only, cached); missing file -> empty map."""
    global _LEXICON
    if _LEXICON is not None:
        return _LEXICON
    path = os.path.join(config_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config"
    ), "performance_guide_lexicon.json")
    try:
        with open(path, encoding="utf-8") as fh:
            _LEXICON = json.load(fh)
    except (OSError, ValueError):
        _LEXICON = {}
    return _LEXICON


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def sanitize(text):
    """Return guide text safe for the prompt, or '' if it smuggles data.

    Mirrors prompt_engine._sanitize_guide but is stricter: spoken quotes, fact
    ids, assignments and digit payloads are all rejected rather than stripped.
    """
    value = str(text or "").replace("\n", " ").strip()[:200]
    if not value:
        return ""
    for bad in ('f_', 'clue_', 'ev_', '=', '「', '」', '『', '』', '"'):
        if bad in value:
            return ""
    if any(ch.isdigit() for ch in value):
        return ""
    return value


def intent_guides(intent):
    """Wording rules for the formula-chosen act type."""
    return list((lexicon().get("intents") or {}).get(intent) or [])


def suspicion_guides(value):
    """Wording rules from the suspicion level toward the current target."""
    v = _num(value)
    bucket = "high" if v > 0.45 else ("mid" if v > 0.2 else "low")
    return list((lexicon().get("suspicion") or {}).get(bucket) or [])


def emotion_guides(state, config_dir=None):
    """Reuse the existing emotion wording map without its numeric prefix.

    ``speech_style.emotion_terms`` renders ``valence(-0.29)→用词偏冷`` for the
    DRIFT block; guides must carry only the wording half, because numbers are
    state data and the guide channel may never carry them.
    """
    from . import speech_style

    emo = (state or {}).get("emotion") or {}
    lib = speech_style.lexicon(config_dir).get("emotion") or {}
    out = []
    for key in ("arousal", "valence", "stress", "morale", "exposure_risk", "cognitive_load"):
        value = emo.get(key)
        if not isinstance(value, (int, float)):
            continue
        threshold = 0.2 if key == "valence" else 0.45
        v = _num(value)
        if key == "valence":
            bucket = "positive" if v > threshold else ("negative" if v < -threshold else None)
        else:
            bucket = "high" if v > threshold else ("low" if v < -threshold else None)
        out.extend((lib.get(key) or {}).get(bucket) or [])
    return out


def delivery_guides(state):
    """The already-sampled IPS performance item, as a timing cue only."""
    delivery = (state or {}).get("delivery") or {}
    item = str(delivery.get("item") or "").strip()
    if not item or not delivery.get("hit"):
        return []
    lib = lexicon().get("delivery") or {}
    prefix = str(lib.get("performance_prefix") or "")
    suffix = str(lib.get("performance_suffix") or "")
    return [f"{prefix}{item}{suffix}"]


def build(state, intent, target_name=None, config_dir=None):
    """Return ordered, sanitised wording guides for this round.

    ``target_name`` is used only to locate the suspicion value; it is never
    printed into a guide (character names are facts, not wording).
    """
    state = state or {}
    lexicon(config_dir)
    lines = []
    framing = str(lexicon().get("framing") or "")
    if sanitize(framing):
        lines.append(framing)
    lines.extend(intent_guides(intent))

    suspicion = state.get("suspicion") or {}
    if target_name:
        value = None
        for key, val in suspicion.items():
            if key == target_name or key.endswith(":" + str(target_name).split(":")[-1]):
                value = val
                break
        lines.extend(suspicion_guides(value if value is not None else 0.0))
    elif suspicion:
        lines.extend(suspicion_guides(max(suspicion.values())))

    lines.extend(emotion_guides(state))
    lines.extend(delivery_guides(state))

    out, seen = [], set()
    for raw in lines:
        text = sanitize(raw)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out
