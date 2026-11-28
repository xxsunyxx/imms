"""Faction drift with hysteresis, cooldown and a loyalty gate (F11/F17).

Alignment uses ``formulas.alignment`` and ``formulas.faction_decision``.  The
four inputs are either already-runtime values (relation mean, morale/exposure,
commitment) or explicit script fields (``goal_fit``, ``goal_fit_by_faction``).
Missing goal fields are treated as neutral zero and recorded in ``gaps`` rather
than being invented as evidence.  Without declared factions the state records
why it is unaligned instead of pretending a faction exists.
"""

from . import formulas

THETA_ON = 0.70
THETA_OFF = 0.50
COOLDOWN_ROUNDS = 2
LOYO_GATE = 0.70
MORALE_ESCAPE = 0.20


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def candidates(onto, char):
    """Declared factions only; no faction is inferred from goals or prose."""
    out = []
    for item in (onto or {}).get("factions") or []:
        if isinstance(item, dict):
            fid = item.get("id")
            if not fid:
                continue
            out.append({"id": fid, "name": item.get("name", fid)})
        elif str(item).strip():
            out.append({"id": str(item), "name": str(item)})
    own = (char or {}).get("faction")
    if own and not any(item["id"] == own for item in out):
        out.append({"id": own, "name": (char or {}).get("faction_name", own)})
    return out


def inputs(state, char):
    """Compute alignment inputs from runtime values plus explicit goal fields."""
    rels = [
        r for r in (state.get("relations") or {}).values() if isinstance(r, dict)
    ]
    rel_mean = (
        sum(_number(r.get("like")) + _number(r.get("trust")) + _number(r.get("respect"))
            for r in rels) / (3 * len(rels)) if rels else 0.0
    )
    commitment = sum(_number(r.get("commitment")) for r in rels) / len(rels) if rels else 0.0
    emo = state.get("emotion") or {}
    safety = formulas.clip(
        _number(emo.get("morale"), 0.8) - _number(emo.get("exposure_risk"), 0.1), -1.0, 1.0
    )
    loyalty = _number((state.get("l1") or {}).get("values.loyalty"), 0.5)
    betray_cost = loyalty * commitment
    goal_fit = (char or {}).get("goal_fit")
    gaps = []
    if goal_fit is None:
        goal_fit = 0.0
        gaps.append("goal_fit_missing_neutral")
    return {
        "rel_mean": round(rel_mean, 4),
        "goal_fit": round(_number(goal_fit), 4),
        "safety": round(safety, 4),
        "betray_cost": round(betray_cost, 4),
        "loyalty": round(loyalty, 4),
        "gaps": gaps,
    }


def update(state, round_no, onto, char):
    """Recompute alignment once per round; emit a shift only on real changes."""
    current = state.get("faction")
    found = candidates(onto, char)
    fs = state.setdefault("faction_state", {"current": current, "cooldown_until": 0})
    if not found:
        fs.update({
            "current": current, "candidate": None, "alignment": None,
            "round": int(round_no), "gaps": ["no_declared_faction"],
        })
        return None
    base = inputs(state, char)
    by_faction = (char or {}).get("goal_fit_by_faction") or {}
    scored = []
    for faction in found:
        fit = by_faction.get(faction["id"], base["goal_fit"])
        align = formulas.alignment(
            base["rel_mean"], _number(fit), base["safety"], base["betray_cost"]
        )
        scored.append({**faction, "alignment": round(align, 4)})
    scored.sort(key=lambda item: (-item["alignment"], item["id"]))
    best = scored[0]
    decision = formulas.faction_decision(best["alignment"], current, THETA_ON, THETA_OFF)
    target = best["id"] if decision else None
    gate = None
    if current and target != current and base["loyalty"] >= LOYO_GATE and \
            _number((state.get("emotion") or {}).get("morale"), 0.8) >= MORALE_ESCAPE:
        gate = "loyalty_gate"
        target = current
    if int(round_no) < int(fs.get("cooldown_until", 0)):
        gate = "cooldown"
        target = current
    changed = target != current
    fs.update({
        "current": target, "candidate": best["id"], "alignment": best["alignment"],
        "round": int(round_no), "inputs": base, "thresholds": {
            "on": THETA_ON, "off": THETA_OFF,
        },
        "gaps": base["gaps"], "gate": gate,
    })
    state["faction"] = target
    if not changed:
        return None
    fs["cooldown_until"] = int(round_no) + COOLDOWN_ROUNDS
    return {
        "round": int(round_no), "seat": state.get("seat_id"),
        "character": state.get("character_id"),
        "from": current, "to": target, "alignment": best["alignment"],
        "cooldown_until": fs["cooldown_until"],
        "basis": {"inputs": base, "candidates": scored},
    }
