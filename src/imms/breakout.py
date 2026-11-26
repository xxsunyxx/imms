"""G22: acute breakout state (frenzy) -- a controlled 1-2 round deviation.

Frenzy is a *state*, not a new persona and not a new memory scope.  It only
widens the improvisation band-pass; it does not relax IR1/IR2 (no new facts,
no knowledge beyond beliefs/secrets/public), R4 (script locks) or R5 (truth
belongs to the engine).  Memory, beliefs and the lie ledger stay continuous.

Entry requires charge + intensity + at least one previously suppressed burst
+ an explicit per-session token.  Exit is deterministic after 1-2 rounds and
carries an irreversible social/emotional cost.
"""

from . import formulas

STATES = ("normal", "charged", "frenzy")
S_HI = {"normal": 3.2, "charged": 3.6, "frenzy": 4.6}
F_MIN = {"normal": 0.72, "charged": 0.65, "frenzy": 0.55}
ENTER_A = 1.1
ENTER_I = 0.85
TOKEN_PER_SESSION = 1
MIN_DURATION = 1
MAX_DURATION = 2
EXPOSURE_COST = 0.3
MORALE_COST = -0.2
RELATION_LOSS = -0.15
RELATION_KEYS = ("like", "trust", "respect")


def _num(value, default=0.0):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if -1e9 < v < 1e9 else default


def fresh_state():
    return {
        "state": "normal",
        "since": 0,
        "exit_round": None,
        "token_consumed": False,
        "after_effect_applied": False,
        "memory_scope": "full",
    }


def ensure(state):
    """Attach a breakout state without touching any other state field."""
    if not isinstance(state.get("breakout"), dict):
        state["breakout"] = fresh_state()
    slot = state["breakout"]
    slot.setdefault("state", "normal")
    slot.setdefault("memory_scope", "full")
    return slot


def current_state(state):
    return ensure(state)["state"]


def thresholds(state):
    """State-dependent band-pass; IR1/IR2 guards are never in this table."""
    slot = ensure(state)
    name = slot["state"] if slot["state"] in S_HI else "normal"
    return {"s_hi": S_HI[name], "f_min": F_MIN[name], "state": name}


def tokens(max_tokens=None):
    """Fresh token ledger; ``macro_quota`` can raise the default from G21."""
    try:
        cap = int(max_tokens if max_tokens is not None else TOKEN_PER_SESSION)
    except (TypeError, ValueError):
        cap = TOKEN_PER_SESSION
    return {"used": 0, "max": max(0, cap)}


def apply_after_effect(state, observers, round_no):
    """Exit cost: arousal/exposure up, morale down, relation loss not restored."""
    changes = []
    if state.get("is_human"):
        return changes
    emo = state.setdefault("emotion", {})
    before_exposure = _num(emo.get("exposure_risk"), 0.0)
    before_morale = _num(emo.get("morale"), 0.8)
    emo["exposure_risk"] = round(formulas.clip(before_exposure + EXPOSURE_COST, 0, 1), 4)
    emo["morale"] = round(formulas.clip(before_morale + MORALE_COST, 0, 1), 4)
    changes.append({"seat_id": state.get("seat_id"), "dim": "exposure_risk",
                    "before": round(before_exposure, 4), "after": emo["exposure_risk"],
                    "round": round_no})
    changes.append({"seat_id": state.get("seat_id"), "dim": "morale",
                    "before": round(before_morale, 4), "after": emo["morale"],
                    "round": round_no})
    relations = state.setdefault("relations", {})
    for other in (observers or []):
        if other == state.get("seat_id"):
            continue
        rel = relations.setdefault(other, {})
        for key in RELATION_KEYS:
            before = _num(rel.get(key), 0.5 if key == "respect" else 0.0)
            after = round(formulas.clip(before + RELATION_LOSS, -1.0, 1.0), 4)
            rel[key] = after
            changes.append({
                "seat_id": state.get("seat_id"), "target": other, "dim": key,
                "before": round(before, 4), "after": after, "round": round_no,
                "irreversible": True,
            })
    return changes


def advance(state, round_no, acc, intensity, suppressed_count=0, ledger=None,
            observers=None):
    """Move the state machine one round.  Pure w.r.t. facts/memory/L1.

    ``ledger`` is the shared token dict; a token is consumed only on entry to
    frenzy.  Returns a transition descriptor (or ``None``) for event logging.
    """
    if state.get("is_human"):
        return None
    slot = ensure(state)
    acc = acc or {}
    A = _num(acc.get("A"), 0.0)
    theta = _num(acc.get("theta", acc.get("theta_base")), 0.6)
    I = _num(intensity, 0.0)
    before = slot["state"]
    transition = None

    if before == "normal" and A >= theta:
        slot["state"] = "charged"
        slot["since"] = int(round_no)
        transition = {"from": before, "to": "charged", "reason": "accumulator_crossed_theta",
                      "A": round(A, 4), "theta": round(theta, 4), "round": int(round_no)}

    if slot["state"] == "charged":
        available = bool(ledger) and int(ledger.get("used", 0)) < int(ledger.get("max", 0))
        reasons = []
        if A < ENTER_A:
            reasons.append("charge_below_entry")
        if I < ENTER_I:
            reasons.append("intensity_below_entry")
        if int(suppressed_count or 0) < 1:
            reasons.append("no_prior_suppressed_burst")
        if not available:
            reasons.append("token_unavailable")
        if not reasons:
            ledger["used"] = int(ledger.get("used", 0)) + 1
            slot["state"] = "frenzy"
            slot["since"] = int(round_no)
            slot["token_consumed"] = True
            # 1-2 rounds: a stronger charge lasts one extra round.
            slot["exit_round"] = int(round_no) + (
                MAX_DURATION if A >= 1.3 else MIN_DURATION
            )
            transition = {
                "from": before, "to": "frenzy", "round": int(round_no),
                "state": "frenzy", "A": round(A, 4), "intensity": round(I, 4),
                "suppressed_before": int(suppressed_count or 0),
                "token_used": ledger["used"], "token_max": ledger["max"],
                "exit_round": slot["exit_round"],
                "s_hi": S_HI["frenzy"], "f_min": F_MIN["frenzy"],
                "memory_scope": slot["memory_scope"],
                "guards": ["IR1", "IR2", "R4", "R5"],
            }
        elif not transition:
            transition = {
                "from": before, "to": "charged", "round": int(round_no),
                "reason": ",".join(reasons), "state": "charged",
            }

    if slot["state"] == "frenzy" and int(round_no) >= int(slot.get("exit_round") or 0):
        changes = apply_after_effect(state, observers, round_no) if not slot.get("after_effect_applied") else []
        slot["after_effect_applied"] = True
        slot["state"] = "charged"
        slot["exit_round"] = None
        transition = {
            "from": "frenzy", "to": "charged", "round": int(round_no),
            "state": "charged", "after_effect": {
                "exposure_risk": EXPOSURE_COST, "morale": MORALE_COST,
                "relation_loss": RELATION_LOSS,
            },
            "changes": changes,
            "memory_scope": slot["memory_scope"],
        }
    return transition
