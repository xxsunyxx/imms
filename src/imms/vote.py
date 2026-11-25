"""Voting system: rules, ballots, multi-round deliberation and audit.

The runtime used to keep a one-shot tally inline.  This module owns the whole
voting phase while keeping the old result keys (tally/winner/solved) so older
scripts, replay and branch regression keep working.

No LLM is asked to decide a vote.  AI ballots are derived from the stored
log-odds plus persona/relation/emotion terms, and every ballot is recorded.
"""

import random
import re

from . import db, formulas

ABSTAIN = "__abstain__"
WEIGHT_RE = re.compile(r"logLR:([^|=]+)=([-+]?(?:\d+(?:\.\d*)?|\.\d+))")

DEFAULT_RULES = {
    "rounds": 1,
    "strategy": "argmax",
    "temperature": 1.0,
    "allow_self": False,
    "allow_abstain": True,
    "invalid_as": "abstain",
    "tie_break": "evidence",
    "majority_stop": True,
    "open_ballot": False,
    "bandwagon": 0.25,
    "ally_weight": 0.35,
    "grudge_weight": 0.50,
    "fear_weight": 0.30,
    "exposure_deflect": 0.40,
}


def normalize_rules(overrides=None):
    rules = dict(DEFAULT_RULES)
    sources = [overrides] if isinstance(overrides, dict) else (overrides or [])
    for source in sources:
        if isinstance(source, dict):
            rules.update({k: v for k, v in source.items() if k in DEFAULT_RULES})
    rules["rounds"] = max(1, min(5, int(rules["rounds"])))
    rules["temperature"] = max(1e-6, float(rules["temperature"]))
    rules["strategy"] = str(rules["strategy"])
    rules["tie_break"] = str(rules["tie_break"])
    for key in ("bandwagon", "ally_weight", "grudge_weight", "fear_weight", "exposure_deflect"):
        rules[key] = float(rules[key])
    return rules


def rules_for(onto=None, scripted=None, overrides=None):
    """Merge script defaults, per-session scripted config and explicit overrides."""
    script_rules = ((onto or {}).get("evaluation") or {}).get("vote_rules") or {}
    session_rules = (scripted or {}).get("vote_config") or {}
    return normalize_rules([script_rules, session_rules, overrides])


def _culprit_id(onto, to_seat):
    culprit = (onto.get("evaluation") or {}).get("killer_id")
    if culprit:
        return to_seat(culprit)
    names = {c["name"]: c["id"] for c in onto.get("characters", [])}
    for fact in onto.get("truth_graph", []):
        for name, cid in names.items():
            if name in fact:
                return to_seat(cid)
    return None


def _make_resolver(session_doc, onto, runtime):
    seat_ids = list(runtime.keys()) if runtime else [
        p["id"] for p in session_doc.get("participants", []) if p.get("kind") != "gm"
    ]
    char_to_seat = {
        p["script_character_id"]: p["id"]
        for p in session_doc.get("participants", []) if p.get("script_character_id")
    }
    name_to_seat = {
        c["name"]: char_to_seat.get(c["id"], c["id"])
        for c in (onto or {}).get("characters", [])
    }
    seat_set = set(seat_ids)

    def to_seat(value):
        if value is None:
            return ABSTAIN
        text = str(value).strip()
        if not text or text.lower() in {"abstain", "none", "null", "弃票", "弃权"}:
            return ABSTAIN
        if text in seat_set:
            return text
        if text in char_to_seat:
            return char_to_seat[text]
        if text in name_to_seat:
            return name_to_seat[text]
        for prefix in ("ai:", "human:"):
            if prefix + text in seat_set:
                return prefix + text
        return text

    return to_seat, seat_ids, char_to_seat


def validate_ballot(raw, voter, seat_ids, to_seat, rules):
    choice = to_seat(raw)
    if choice == ABSTAIN:
        if rules["allow_abstain"]:
            return {"choice": ABSTAIN, "valid": True, "code": "abstain"}
        return {"choice": ABSTAIN, "valid": False, "code": "abstain_blocked"}
    if choice not in seat_ids:
        return {"choice": ABSTAIN, "valid": False, "code": "unknown_target"}
    if choice == voter and not rules["allow_self"]:
        return {"choice": ABSTAIN, "valid": False, "code": "self_vote_blocked"}
    return {"choice": choice, "valid": True, "code": "ok"}


def evidence_scores(revealed_clues, to_seat, seat_ids):
    scores = {sid: 0.0 for sid in seat_ids}
    for line in revealed_clues or []:
        for target, weight in WEIGHT_RE.findall(str(line)):
            seat = to_seat(target)
            if seat in scores:
                scores[seat] += float(weight)
    return {k: round(v, 4) for k, v in scores.items()}


def ai_ballot(state, seat_id, seat_ids, rules, rng=None, evidence=None):
    """Choose an AI target from log-odds plus persona/relation/emotion terms."""
    rng = rng or random.Random(42)
    candidates = [s for s in seat_ids if s != seat_id or rules["allow_self"]]
    if not candidates:
        candidates = list(seat_ids)
    if not candidates:
        return {"choice": ABSTAIN, "valid": True, "code": "abstain", "distribution": {}}

    scores = {}
    for target in candidates:
        base = float(state.get("ell", {}).get(target, 0.0))
        if rules["strategy"] == "argmax":
            scores[target] = base
            continue
        rel = state.get("relations", {}).get(target, {})
        score = base
        score -= rules["ally_weight"] * 0.5 * (float(rel.get("like", 0.0)) + float(rel.get("trust", 0.0)))
        score += rules["grudge_weight"] * float(rel.get("grudge", 0.0))
        score += rules["fear_weight"] * float(rel.get("fear", 0.0))
        emo = state.get("emotion", {})
        if emo.get("exposure_risk", 0.0) > 0.5 and evidence:
            top = max(evidence, key=evidence.get) if evidence else None
            if top == target:
                score += rules["exposure_deflect"]
        scores[target] = score

    if rules["strategy"] == "argmax":
        top = max(scores.values()) if scores else float("-inf")
        winners = [sid for sid, val in scores.items() if val == top]
        choice = sorted(winners)[0] if winners else ABSTAIN
        return {"choice": choice, "valid": True, "code": "ok", "distribution": scores}

    probs = formulas.softmax(scores, rules["temperature"])
    keys = list(probs)
    choice = rng.choices(keys, weights=[probs[k] for k in keys], k=1)[0]
    return {"choice": choice, "valid": True, "code": "ok", "distribution": probs}


def tally_ballots(ballots, seat_ids, rules, evidence=None):
    """Count valid ballots and resolve ties without relying on dict order."""
    counts = {sid: 0 for sid in seat_ids}
    valid_votes = 0
    abstentions = 0
    invalid = []
    for ballot in ballots:
        if not ballot.get("valid"):
            invalid.append({"voter": ballot.get("voter"), "code": ballot.get("code")})
            continue
        choice = ballot.get("choice")
        if choice == ABSTAIN:
            abstentions += 1
            continue
        if choice in counts:
            counts[choice] += 1
            valid_votes += 1
        else:
            invalid.append({"voter": ballot.get("voter"), "code": "unknown_target"})

    top_count = max(counts.values()) if counts else 0
    top = [sid for sid, n in counts.items() if n == top_count] if top_count else []
    winner, tie, resolved_by = None, False, None
    if len(top) == 1:
        winner = top[0]
    elif len(top) > 1:
        tie = True
        mode = rules["tie_break"]
        if mode == "abstain":
            resolved_by = "abstain"
        elif mode == "deterministic":
            winner = sorted(top)[0]
            resolved_by = "seat_id"
        else:
            scores = evidence or {}
            best = max(scores.get(sid, 0.0) for sid in top) if scores else 0.0
            tied = [sid for sid in top if scores.get(sid, 0.0) == best]
            winner = sorted(tied)[0] if tied else None
            resolved_by = "evidence" if len(tied) == 1 else "seat_id"
    majority = bool(winner and valid_votes and counts[winner] > valid_votes / 2.0)
    return {
        "counts": {k: v for k, v in counts.items() if v or k == winner},
        "winner": winner, "tie": tie, "tie_resolved_by": resolved_by,
        "valid_votes": valid_votes, "abstentions": abstentions,
        "invalid": invalid, "majority": majority,
    }


def _human_ballots(scripted, round_no, seat_id, runtime, to_seat, seat_ids, rules):
    raw = None
    reason = ""
    votes = (scripted or {}).get("votes")
    if votes is not None:
        if isinstance(votes, dict):
            item = votes.get(str(round_no), votes.get(round_no, votes.get("all")))
        elif isinstance(votes, list):
            item = votes[round_no - 1] if round_no - 1 < len(votes) else None
        else:
            item = votes
        if isinstance(item, dict):
            raw = item.get("target", item.get("vote"))
            reason = str(item.get("reason") or "")
        else:
            raw = item
    elif scripted and "vote" in scripted:
        raw = scripted["vote"]
    else:
        raw = input(f"[用户席位] 第 {round_no} 次投票（角色 id，回车弃票）：")
    verdict = validate_ballot(raw, seat_id, seat_ids, to_seat, rules)
    return [{
        "voter": seat_id,
        "character_id": (runtime.get(seat_id) or {}).get("character_id"),
        "kind": "human", "raw": raw, "choice": verdict["choice"],
        "valid": verdict["valid"], "code": verdict["code"], "reason": reason,
    }]


def _deliberate(runtime, tally, rules, seat_ids):
    """Apply a small public-vote influence before the next ballot."""
    changes = []
    if not tally.get("winner") or tally.get("valid_votes") == 0:
        return changes
    for seat_id, state in runtime.items():
        for target, count in tally["counts"].items():
            if target == seat_id or count == 0:
                continue
            delta = 0.0
            if target == tally["winner"]:
                delta = rules["bandwagon"] * count / max(1, tally["valid_votes"])
            before = float(state.get("ell", {}).get(target, 0.0))
            after = max(-8.0, min(8.0, before + delta))
            state.setdefault("ell", {})[target] = after
            suspicion = float(state.get("suspicion", {}).get(target, formulas.sigmoid(after)))
            state.setdefault("suspicion", {})[target] = round(formulas.sigmoid(after), 4)
            if abs(after - before) > 1e-9:
                changes.append({
                    "seat_id": seat_id, "character_id": state.get("character_id"),
                    "dim": "suspicion", "target": target,
                    "before": round(suspicion, 4), "after": round(formulas.sigmoid(after), 4),
                })
    return changes


def run_vote(conn, session_id, scripted=None, runtime=None, session_doc=None,
             onto=None, seed=42, rules=None, persist=True):
    """Run the complete voting phase and persist a ballot ledger."""
    session_doc = session_doc or db.get_session(conn, session_id)
    onto = onto or db.get_script(conn, session_doc["script_id"])
    runtime = runtime if runtime is not None else db.get_game_state(conn, session_id, "runtime", {})
    runtime = runtime or {}
    resolved = normalize_rules(rules) if rules is not None else rules_for(onto, scripted)
    to_seat, seat_ids, _ = _make_resolver(session_doc, onto, runtime)
    culprit = _culprit_id(onto, to_seat)
    rng = random.Random(seed)
    human_id = session_doc.get("human_character_id")
    for seat_id, state in runtime.items():
        state["seat_id"] = seat_id
        state["is_human"] = bool(state.get("is_human") or (human_id and human_id in seat_id))

    revealed = db.get_game_state(conn, session_id, "revealed_clues", [])
    scores = evidence_scores(revealed, to_seat, seat_ids)
    base_round = int(db.get_game_state(conn, session_id, "round", 0))
    ledger = []
    changes = []
    final = None
    for ballot_no in range(1, resolved["rounds"] + 1):
        ballots = []
        for seat_id, state in runtime.items():
            if state.get("is_human"):
                ballots.extend(_human_ballots(
                    scripted, ballot_no, seat_id, runtime, to_seat, seat_ids, resolved
                ))
            else:
                decision = ai_ballot(state, seat_id, seat_ids, resolved, rng, scores)
                ballots.append({
                    "voter": seat_id,
                    "character_id": state.get("character_id"),
                    "kind": "ai", "raw": None, "choice": decision["choice"],
                    "valid": decision["valid"], "code": decision["code"],
                    "strategy": resolved["strategy"],
                    "distribution": {
                        k: round(float(v), 4) for k, v in decision["distribution"].items()
                    },
                    "reason": "",
                })
        round_tally = tally_ballots(ballots, seat_ids, resolved, scores)
        entry = {
            "ballot_no": ballot_no, "round": base_round,
            "ballots": ballots, "tally": round_tally, "evidence_scores": scores,
        }
        ledger.append(entry)
        if persist:
            visibility = ["public"] if resolved["open_ballot"] else ["gm:host"]
            db.append_game_event(
                conn, session_id, base_round, "vote:table", "vote_cast",
                {"ballot_no": ballot_no, "ballots": ballots}, visibility,
            )
            db.append_game_event(
                conn, session_id, base_round, "gm:host", "vote_tally",
                {"ballot_no": ballot_no, **round_tally}, ["public"],
            )
        final = round_tally
        if resolved["majority_stop"] and round_tally["majority"]:
            break
        if ballot_no < resolved["rounds"]:
            changes.extend(_deliberate(runtime, round_tally, resolved, seat_ids))
            if persist:
                db.append_game_event(
                    conn, session_id, base_round, "gm:host", "vote_deliberation",
                    {"after_ballot": ballot_no, "winner": round_tally["winner"]}, ["public"],
                )

    final = final or tally_ballots([], seat_ids, resolved, scores)
    motive = None
    if scripted:
        motive = scripted.get("motive_fact_id")
        votes = scripted.get("votes")
        if isinstance(votes, dict) and votes.get("motive_fact_id"):
            motive = votes["motive_fact_id"]
    result = {
        "culprit": culprit,
        "tally": final["counts"],
        "winner": final["winner"],
        "solved": bool(final["winner"] and final["winner"] == culprit),
        "motive_fact_id": motive,
        "rules": resolved,
        "valid_votes": final["valid_votes"],
        "abstentions": final["abstentions"],
        "invalid_ballots": final["invalid"],
        "tie": final["tie"],
        "tie_resolved_by": final["tie_resolved_by"],
        "majority": final["majority"],
        "ballot_rounds": len(ledger),
        "last_ballot": len(ledger),
    }
    if persist:
        db.set_game_state(conn, session_id, "vote_state", {
            "rules": resolved, "rounds": ledger, "result": result,
            "evidence_scores": scores, "culprit": culprit,
        })
        db.append_game_event(
            conn, session_id, base_round, "gm:host", "vote_result", result, ["public"],
        )
    result["_changes"] = changes
    return result


def cast_ballot(conn, session_id, voter, target, reason="", round_no=None,
                rules=None, persist=True):
    """Record a manual ballot in the ledger without running AI voters."""
    session_doc = db.get_session(conn, session_id)
    onto = db.get_script(conn, session_doc["script_id"])
    runtime = db.get_game_state(conn, session_id, "runtime", {})
    resolved = normalize_rules(rules) if rules is not None else rules_for(onto)
    to_seat, seat_ids, _ = _make_resolver(session_doc, onto, runtime)
    voter_seat = to_seat(voter)
    verdict = validate_ballot(target, voter_seat, seat_ids, to_seat, resolved)
    ballot = {
        "voter": voter_seat,
        "character_id": (runtime.get(voter_seat) or {}).get("character_id"),
        "kind": "manual", "raw": target, "choice": verdict["choice"],
        "valid": verdict["valid"], "code": verdict["code"],
        "reason": str(reason or ""),
    }
    vote_state = db.get_game_state(conn, session_id, "vote_state", {}) or {}
    vote_state.setdefault("rules", resolved)
    vote_state.setdefault("rounds", [])
    vote_state.setdefault("manual_ballots", []).append(ballot)
    scores = vote_state.get("evidence_scores") or evidence_scores(
        db.get_game_state(conn, session_id, "revealed_clues", []), to_seat, seat_ids
    )
    tally = tally_ballots(vote_state["manual_ballots"], seat_ids, resolved, scores)
    vote_state["manual_tally"] = tally
    if persist:
        db.set_game_state(conn, session_id, "vote_state", vote_state)
        db.append_game_event(
            conn, session_id, round_no or int(db.get_game_state(conn, session_id, "round", 0)),
            voter_seat, "vote_cast", {"ballot": ballot}, ["gm:host"],
        )
    return {"ballot": ballot, "tally": tally, "rules": resolved}


def replay_tally(conn, session_id):
    """Recompute the last tally from the stored ballot ledger."""
    vote_state = db.get_game_state(conn, session_id, "vote_state", {}) or {}
    if not vote_state:
        return {"status": "no_vote_state"}
    session_doc = db.get_session(conn, session_id)
    onto = db.get_script(conn, session_doc["script_id"])
    runtime = db.get_game_state(conn, session_id, "runtime", {})
    _, seat_ids, _ = _make_resolver(session_doc, onto, runtime)
    rules = normalize_rules(vote_state.get("rules"))
    ballots = []
    for entry in vote_state.get("rounds", []):
        ballot_no = entry.get("ballot_no")
        if ballot_no == vote_state.get("result", {}).get("last_ballot", ballot_no):
            ballots = entry.get("ballots", [])
    if not ballots and vote_state.get("rounds"):
        ballots = vote_state["rounds"][-1].get("ballots", [])
    ballots += vote_state.get("manual_ballots", [])
    scores = vote_state.get("evidence_scores") or {}
    tally = tally_ballots(ballots, seat_ids, rules, scores)
    return {
        "status": "ok", "rules": rules, "ballots": len(ballots),
        "tally": tally, "result": vote_state.get("result", {}),
    }


def audit(conn, session_id):
    """Offline audit of ballot legality and evidence support."""
    vote_state = db.get_game_state(conn, session_id, "vote_state", {}) or {}
    result = db.get_game_state(conn, session_id, "result", {}) or {}
    if not vote_state:
        return {
            "status": "no_vote_state",
            "killer_correct": bool(result.get("solved")),
            "winner": result.get("winner"),
        }
    ballots = []
    for entry in vote_state.get("rounds", []):
        ballots.extend(entry.get("ballots", []))
    ballots.extend(vote_state.get("manual_ballots", []))
    legal = 0
    abstentions = 0
    blocked_self = 0
    unknown = 0
    supported = 0
    judged = 0
    for ballot in ballots:
        if ballot.get("valid"):
            legal += 1
        if ballot.get("code") == "self_vote_blocked":
            blocked_self += 1
        if ballot.get("code") == "unknown_target":
            unknown += 1
        if ballot.get("choice") == ABSTAIN:
            abstentions += 1
        scores = vote_state.get("evidence_scores") or {}
        if ballot.get("valid") and scores and ballot.get("choice") != ABSTAIN:
            top = max(scores, key=scores.get) if scores else None
            best = max(scores.values()) if scores else 0.0
            judged += 1
            if scores.get(ballot.get("choice"), 0.0) == best and scores.get(top, 0.0) > 0:
                supported += 1
    total = max(1, len(ballots))
    report = vote_state.get("result", {})
    return {
        "status": "ok",
        "ballots": len(ballots),
        "legal_rate": round(legal / total, 4),
        "abstention_rate": round(abstentions / total, 4),
        "self_votes_blocked": blocked_self,
        "unknown_targets": unknown,
        "evidence_supported": supported,
        "evidence_judged": judged,
        "vote_reasonableness": round(supported / max(1, judged), 4) if judged else None,
        "winner": report.get("winner"),
        "winner_legal": bool(report.get("winner") and report["winner"] != ABSTAIN),
        "tie": bool(report.get("tie")),
        "tie_resolved_by": report.get("tie_resolved_by"),
        "majority": bool(report.get("majority")),
        "killer_correct": bool(report.get("solved")),
        "motive_recorded": bool(report.get("motive_fact_id")),
    }
