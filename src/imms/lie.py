"""Deterministic LieLedger with a per-agent reasoning loop.

The ledger never invents a fact.  A lie is a claim about the speaker's own
memory/intent, marked false because the speaker already holds relevant
knowledge.  Every lie is later **believed** or **exposed** by someone; both
outcomes force the speaker to revise and to append a reasoning-loop record.
"""

from . import formulas

CHALLENGE_ACTS = {"lie_exposed", "caught_lying"}
CONTRADICTION_ACTS = {"contradiction"}
BELIEF_ACTS = {"ally", "defend"}
EXPOSE_ACTS = CHALLENGE_ACTS | CONTRADICTION_ACTS | {"accuse"}


def _append_reason(state, round_no, lie_id, kind, detail, source=None):
    record = {
        "round": int(round_no),
        "seat": state.get("seat_id"),
        "lie_id": lie_id,
        "kind": kind,
        "detail": detail,
        "source": source,
    }
    state.setdefault("reasoning_log", []).append(record)
    return record


def exposure_probability(state):
    """P_exposed approximation from F20: witnesses' counter-evidence + load."""
    emo = state.get("emotion") or {}
    return round(
        min(1.0, 0.8 * float(emo.get("exposure_risk", 0.0)) + 0.2 * float(emo.get("cognitive_load", 0.0))),
        4,
    )


def lie_gain(state):
    """F20.  Revealing a secret protects it; honesty charges a moral cost."""
    emo = state.get("emotion") or {}
    attrs = state.get("l1") or {}
    honesty = float(attrs.get("values.honesty", 0.5))
    exposure = float(emo.get("exposure_risk", 0.0))
    suspicion = max((state.get("suspicion") or {}).values(), default=0.0)
    return round(
        formulas.lie_gain(
            v_protect=1.2 * exposure,
            v_frame=0.2 if suspicion > 0.5 else 0.0,
            p_exposed=exposure_probability(state),
            c_exposed=1.0,
            honesty_cost=0.6 * honesty,
            mu=honesty,
        ),
        4,
    )


def should_lie(state, intent):
    """Only deflection can become a source-backed lie about one's own memory."""
    return (
        intent == "deflect"
        and bool(state.get("beliefs"))
        and lie_gain(state) > 0
    )


def create(state, round_no, utterance, audience=None, intent="deflect"):
    """Append one lie record and return it.  No fact id is fabricated."""
    ledger = state.setdefault("lie_ledger", [])
    lie_id = f"lie_{state.get('seat_id', '?')}_r{round_no}_{len(ledger) + 1:03d}"
    entry = {
        "lie_id": lie_id,
        "speaker": state.get("seat_id"),
        "character": state.get("character_id"),
        "round_created": int(round_no),
        "audience": sorted(audience or []),
        "proposition": str(utterance or ""),
        "claimed_facts": [],
        "true_value": False,
        "basis": "speaker_knows_relevant_facts",
        "intent": intent,
        "dependencies": [],
        "supporting_props": [],
        "contradicted_by": [],
        "heat": 0.0,
        "strategy": "maintain",
        "reception": {seat: "pending" for seat in sorted(audience or [])},
        "revisions": [],
    }
    ledger.append(entry)
    _append_reason(
        state, round_no, lie_id, "created",
        f"F20={lie_gain(state):+.4f}；依据=已有知识但声称记不清；初始策略=maintain",
    )
    return entry


def update_from_events(state, events):
    """Believed or exposed: each outcome forces a recorded re-justification.

    F21 heat only rises on challenges; being believed does not lower it, but
    both branches append a revision so the speaker's reasoning stays closed.
    """
    changes = []
    own = {state.get("seat_id"), state.get("character_id")}
    ledger = state.get("lie_ledger") or []
    if not ledger:
        return changes
    for event in events or []:
        if not isinstance(event, dict):
            continue
        payload = event.get("payload") or {}
        act = event.get("act_type") or ""
        actor = event.get("actor")
        target = payload.get("target")
        explicit_lie = payload.get("lie_id")
        if target and target not in own:
            continue
        believed = act in BELIEF_ACTS or payload.get("stance") == "believe"
        exposed = act in EXPOSE_ACTS or payload.get("stance") == "expose"
        if not believed and not exposed:
            continue
        for lie in ledger:
            if lie.get("strategy") in ("confess", "abandon"):
                continue
            if explicit_lie and explicit_lie != lie["lie_id"]:
                continue
            before = float(lie.get("heat", 0.0))
            if exposed:
                n_challenge = int(act in CHALLENGE_ACTS or payload.get("stance") == "expose")
                n_contradiction = int(act in CONTRADICTION_ACTS)
                after = formulas.lie_heat(before, n_challenge, n_contradiction)
                lie["heat"] = round(after, 4)
                status = "exposed" if act in CHALLENGE_ACTS else "challenged"
                strategy = "abandon" if after > 1.2 else ("patch" if after > 0.8 else "maintain")
                detail = f"被{status}：热度 {before:.2f}→{after:.2f}，改为 {strategy}"
            else:
                after = before
                status = "believed"
                strategy = "maintain"
                detail = f"被{actor or '他人'}相信：热度不变，维持 {strategy}"
            lie["strategy"] = strategy
            reception = lie.setdefault("reception", {})
            witnesses = [actor] if actor in reception else sorted(reception)
            for seat in witnesses:
                reception[seat] = status
            revision = {
                "round": int(event.get("round") or 0),
                "trigger": act,
                "status": status,
                "heat_before": round(before, 4),
                "heat_after": round(after, 4),
                "strategy": strategy,
                "rationale": detail,
            }
            lie.setdefault("revisions", []).append(revision)
            _append_reason(state, revision["round"], lie["lie_id"], status, detail, event.get("seq"))
            changes.append({
                "lie_id": lie["lie_id"], "trigger": act, "status": status,
                "before": round(before, 4), "after": round(after, 4), "strategy": strategy,
            })
    return changes


def guard_escalation(state, act):
    """heat > 0.8 or a direct exposure forces re-justification before speaking."""
    for lie in state.get("lie_ledger") or []:
        if lie.get("strategy") in ("confess", "abandon"):
            continue
        hot = float(lie.get("heat", 0.0)) > 0.8
        exposed = lie.get("reception") and "exposed" in lie["reception"].values()
        if not (hot or exposed) or act.get("intent") in ("deflect", "confess"):
            continue
        act["utterance"] = lie.get("proposition") or "这件事我记不清了。"
        act["act_type"] = "deflect"
        act["lie_id"] = lie["lie_id"]
        before = lie["strategy"]
        lie["strategy"] = "confess" if act.get("intent") == "confess" else "patch"
        detail = f"发言前重新自圆其说：{before}→{lie['strategy']}，沿用原命题"
        lie.setdefault("revisions", []).append({
            "round": act.get("round", 0), "trigger": "guard", "status": "rejustified",
            "heat_before": lie.get("heat", 0.0), "heat_after": lie.get("heat", 0.0),
            "strategy": lie["strategy"], "rationale": detail,
        })
        _append_reason(state, act.get("round", 0), lie["lie_id"], "rejustified", detail)
    return act
