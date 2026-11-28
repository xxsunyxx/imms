"""Contradiction event detection and the 'aha moment' numeric effects.

Conflicts are derived only from already observed statements (the same low
false-positive signal as ``memory.detect_conflict``).  Each event carries its
attribution (witness / subject / source seq), strength and the two numeric
effects required by ORIGINAL_SPEC §5.4: arousal +0.3 and suspicion +0.2.
The next-round priority is a pending pointer to the same source event, never a
new fact or a fabricated quote.
"""

from . import emotion, formulas

MIN_STRENGTH = 0.35
AROUSAL_BUMP = 0.3
SUSPICION_BUMP = 0.2


def detect(observations, state, round_no):
    """Turn observed statement conflicts into attributed contradiction events."""
    out = []
    for observation in observations or []:
        conflict = (observation or {}).get("conflict")
        if not conflict:
            continue
        strength = float(conflict.get("strength") or 0.0)
        if strength < MIN_STRENGTH:
            continue
        fids = [str(f) for f in conflict.get("fact_ids") or []]
        beliefs = state.get("beliefs") or {}
        confidence = max(
            [float((beliefs.get(f) or {}).get("belief", 0.5)) for f in fids] or [0.5]
        )
        out.append({
            "kind": "contradiction_event",
            "round": int(round_no),
            "witness": state.get("seat_id"),
            "subject": conflict.get("speaker"),
            "seq": conflict.get("seq"),
            "prior_seq": conflict.get("prior_seq"),
            "fact_ids": fids,
            "status": conflict.get("status"),
            "strength": round(strength, 4),
            "confidence": round(confidence, 4),
        })
    return out


def apply(state, events, round_no):
    """Apply the numeric effects and queue one conflict for the next round."""
    changes = []
    pending = state.setdefault("pending_contradictions", [])
    for event in events or []:
        emo = state.setdefault("emotion", {})
        before_a = float(emo.get("arousal", 0.0))
        after_a = formulas.clip(before_a + AROUSAL_BUMP, 0.0, 1.0)
        emo["arousal"] = after_a
        emotion.refresh_discrete(state)
        changes.append({
            "dim": "arousal", "before": round(before_a, 4), "after": round(after_a, 4),
            "target": None, "reason": "F17 contradiction_event",
        })
        subject = event.get("subject")
        if subject and subject in state.get("suspicion", {}):
            before_s = float(state["suspicion"].get(subject, 0.1))
            after_s = formulas.clip(before_s + SUSPICION_BUMP, 0.0, 1.0)
            state["suspicion"][subject] = after_s
            changes.append({
                "dim": "suspicion", "target": subject,
                "before": round(before_s, 4), "after": round(after_s, 4),
                "reason": "F17 contradiction_event",
            })
        queued = dict(event)
        queued["round"] = int(round_no)
        if not any(item.get("seq") == queued.get("seq") for item in pending):
            pending.append(queued)
    if len(pending) > 3:
        del pending[:len(pending) - 3]
    return changes


def pending_for_round(state, round_no):
    """Return the oldest still-unexpressed conflict from an earlier round."""
    for item in state.get("pending_contradictions") or []:
        if int(item.get("round", 0)) < int(round_no):
            return item
    return None


def mark_expressed(state, event):
    """Drop a queued conflict once the seat has raised it in speech."""
    pending = state.get("pending_contradictions") or []
    state["pending_contradictions"] = [
        item for item in pending if item.get("seq") != (event or {}).get("seq")
    ]
