"""Blind-play oracle, authoring admission and deterministic balance smoke.

G12 / §11.3.  The oracle derives a suspect **only from evidence the table has
actually seen**.  Script truth is consulted later, in a separate ``audit_only``
field, so a wrong vote can still be reported honestly instead of being hidden.

Authoring admission is static and offline: it reuses the solvability audit and
adds checks for ending conditions and machine-readable goal criteria.  The
five-game smoke runs real headless sessions with fixed seeds; it reports what
happened (winner / wrongful accusation / cold rounds) and never turns a small
sample into a statistical claim.
"""

import hashlib
import json
import tempfile
from pathlib import Path

from . import assemble, db, gm_direction, journal, normalize, runtime, scoring, solver, vote

VERSION = "1.0"
GAMES = 5
ROUNDS = 2
BASE_SMOKE_LINES = "我只说公开可得的信息，先听听在座各位的说法。"


def _config_dir():
    return Path(__file__).resolve().parents[2] / "config"


def _supported_condition_keys():
    return {
        "solved", "tie", "motive_correct", "winner",
        "min_clue_coverage", "min_goal_completion", "max_violations",
        "all", "any", "not",
    }


def _walk_conditions(condition):
    if isinstance(condition, dict):
        yield condition
        for value in condition.values():
            yield from _walk_conditions(value)
    elif isinstance(condition, list):
        for item in condition:
            yield from _walk_conditions(item)


def _supported_goal_types():
    return {
        "secret", "hide_fact", "keep_secret", "identify", "solve",
        "identify_culprit", "escape", "evade", "culprit_escape",
        "frame", "framing", "vote_for", "vote",
    }


def authoring_validation(onto):
    """Static admission gate: solvability plus parseable endings/goal checks."""
    baseline = solver.audit_script(onto or {})
    warnings = []
    ending_items = []
    for source in ((onto or {}).get("endings") or [],
                   ((onto or {}).get("evaluation") or {}).get("endings") or []):
        for item in source:
            if isinstance(item, dict):
                ending_items.append(item)
    for item in ending_items:
        condition = item.get("condition") or item.get("when")
        if not condition:
            warnings.append(f"结局 {item.get('id', '?')} 缺少 condition")
            continue
        for block in _walk_conditions(condition):
            unknown = sorted(set(block) - _supported_condition_keys())
            if unknown:
                warnings.append(f"结局 {item.get('id', '?')} 未知条件字段：{'、'.join(unknown)}")
    endings_ok = bool(ending_items) and not any("未知条件字段" in w for w in warnings)
    if not ending_items:
        warnings.append("剧本未提供结构化结局；运行时按 legacy 顺序或派生码结算")

    unsupported = []
    for char in (onto or {}).get("characters") or []:
        if not isinstance(char, dict):
            continue
        for raw in char.get("goals") or []:
            if not isinstance(raw, dict):
                continue
            check = raw.get("check") or raw.get("criteria")
            if not check:
                continue
            if isinstance(check, str):
                check = {"type": check}
            if not isinstance(check, dict) or str(check.get("type") or "") not in _supported_goal_types():
                unsupported.append(str(check.get("type") if isinstance(check, dict) else check))
    goals_ok = not unsupported
    if unsupported:
        warnings.append("不支持的目标判据：" + "、".join(unsupported))

    checks = dict(baseline["checks"])
    checks["endings_parseable"] = endings_ok
    checks["goal_checks_supported"] = goals_ok
    checks["blind_oracle_ready"] = bool(
        checks.get("clue_weights_parseable")
        and checks.get("required_facts_covered")
        and checks.get("unique_solution")
    )
    hard = (
        "has_culprit", "culprit_is_role", "has_clues", "required_facts_covered",
        "clue_weights_parseable", "unique_solution", "solution_matches_truth",
        "endings_parseable", "goal_checks_supported",
    )
    verdict = "pass" if all(checks.get(key) for key in hard) else "needs_work"
    return {
        "script_id": (onto or {}).get("script_id"),
        "version": VERSION,
        "checks": checks,
        "warnings": warnings,
        "verdict": verdict,
        "baseline_verdict": baseline["verdict"],
    }


def _clue_lines(event):
    payload = event.get("payload") or {}
    clues = []
    if event.get("act_type") == "clue_release" and payload.get("clue"):
        clues.append(payload["clue"])
    if event.get("act_type") == "clue_found":
        visibility = event.get("visibility") or []
        if "public" in visibility and payload.get("clue"):
            clues.append(payload["clue"])
    return clues


def blind_play_check(conn, session_id):
    """Derive a suspect from public clues only; truth is audited afterwards."""
    session_doc = db.get_session(conn, session_id)
    onto = db.get_script(conn, session_doc["script_id"])
    result = db.get_game_state(conn, session_id, "result", {}) or {}
    runtime_state = db.get_game_state(conn, session_id, "runtime", {}) or {}
    vote_state = db.get_game_state(conn, session_id, "vote_state", {}) or {}
    to_seat, seat_ids, _ = vote._make_resolver(session_doc, onto, runtime_state)
    people = scoring.resolve_people(session_doc, onto)

    accumulated = []
    checkpoints = {}
    first_unique = None
    for event in journal.audit_events(conn, session_id):
        for clue in _clue_lines(event):
            if clue not in accumulated:
                accumulated.append(clue)
        if not accumulated:
            continue
        scores = vote.evidence_scores(accumulated, to_seat, seat_ids)
        positive = {k: v for k, v in scores.items() if v > 0}
        top = max(positive.values()) if positive else 0.0
        leaders = [k for k, v in positive.items() if v == top] if top else []
        if len(leaders) == 1 and first_unique is None:
            first_unique = {
                "round": event.get("round"),
                "suspect": people["seat_to_name"].get(leaders[0], leaders[0]),
            }
        checkpoints[str(event.get("seq"))] = {
            "round": event.get("round"), "scores": scores, "leaders": leaders,
        }

    final_scores = vote_state.get("evidence_scores") or vote.evidence_scores(
        db.get_game_state(conn, session_id, "revealed_clues", []) or [], to_seat, seat_ids
    )
    positive = {k: v for k, v in final_scores.items() if v > 0}
    ranked = sorted(positive.items(), key=lambda kv: (-kv[1], kv[0]))
    suspect, top = ranked[0] if ranked else (None, 0.0)
    runner, second = ranked[1] if len(ranked) > 1 else (None, 0.0)
    culprit = result.get("culprit")
    return {
        "session_id": session_id,
        "version": VERSION,
        "derived_without_truth": True,
        "suspect": people["seat_to_name"].get(suspect) if suspect else None,
        "runner_up": people["seat_to_name"].get(runner) if runner else None,
        "top_score": top, "margin": round(top - second, 4),
        "unique": bool(suspect and top > 0 and top > second),
        "first_unique_round": first_unique,
        "clues_used": [
            sorted(gm_direction.revealed_ids([item])) or [str(item)]
            for item in accumulated
        ],
        "checkpoints": len(checkpoints),
        "audit_only": {
            "culprit": people["seat_to_name"].get(culprit) if culprit else None,
            "matches_truth": bool(suspect and culprit and suspect == culprit),
            "source": "result.culprit（只在审计对照时使用，不参与推导）",
        },
    }


def oracle_report(conn, session_id):
    session_doc = db.get_session(conn, session_id)
    onto = db.get_script(conn, session_doc["script_id"])
    return {
        "session_id": session_id,
        "version": VERSION,
        "authoring": authoring_validation(onto),
        "blind": blind_play_check(conn, session_id),
        "ending": (db.get_game_state(conn, session_id, "scoring", {}) or {}).get("ending") or {},
    }


def _fingerprint(result, events):
    digest = json.dumps({
        "result": {k: v for k, v in result.items() if not k.startswith("_")},
        "events": [[e.get("round"), e.get("actor"), e.get("act_type")] for e in events],
    }, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(digest.encode("utf-8")).hexdigest()[:16]


def smoke(conn, script_id, games=GAMES, rounds=ROUNDS, seed=42):
    """Run fixed-seed headless games in a scratch DB; never mutate ``conn``."""
    onto = db.get_script(conn, script_id)
    persona_doc, _ = normalize.normalize_persona(onto, _config_dir())
    playable = [c["id"] for c in onto.get("characters", []) if not c.get("is_victim")]
    targets = [cid for cid in playable if cid != (onto.get("evaluation") or {}).get("killer_id")]
    if len(playable) < 2 or not targets:
        return {"status": "insufficient_roles", "games": [], "script_id": script_id}
    results = []
    for index in range(max(1, min(20, int(games)))):
        with tempfile.TemporaryDirectory() as work:
            scratch = db.connect(Path(work) / f"oracle_{index}.db")
            try:
                db.save_script(scratch, onto)
                db.save_persona_l1(scratch, script_id, persona_doc)
                human = playable[index % len(playable)]
                session = assemble.assemble(scratch, script_id, human)
                choices = [cid for cid in targets if cid != human] or targets
                target = choices[index % len(choices)]
                scripted = {
                    "utterances": [BASE_SMOKE_LINES] * max(1, int(rounds)),
                    "vote": target,
                    "reason": f"smoke seed={seed + index} 固定票型",
                }
                out = runtime.run_session(
                    scratch, session["session_id"], rounds=rounds,
                    scripted=scripted, seed=seed + index, llm_name="template",
                )
                events = journal.audit_events(scratch, session["session_id"])
                result = out["result"]
                names = scoring.resolve_people(session, onto)["seat_to_name"]
                cold = not any(
                    e.get("act_type") in {"accuse", "contradiction", "clue_release", "clue_found"}
                    for e in events if e.get("actor") != f"human:{human}"
                )
                results.append({
                    "game": index + 1, "seed": seed + index,
                    "human": names.get(f"human:{human}", human),
                    "scripted_vote": names.get(f"ai:{target}", target),
                    "winner": names.get(result.get("winner"), result.get("winner")),
                    "solved": bool(result.get("solved")),
                    "ending": (db.get_game_state(scratch, session["session_id"], "scoring", {}) or {})
                    .get("ending", {}).get("code"),
                    "events": len(events), "cold": cold,
                    "fingerprint": _fingerprint(result, events),
                })
            finally:
                scratch.close()
    wrongful = sum(1 for row in results if not row["solved"])
    cold_rounds = sum(1 for row in results if row["cold"])
    return {
        "status": "ok", "script_id": script_id, "version": VERSION,
        "config": {"games": len(results), "rounds": rounds, "seed": seed,
                   "vote_policy": "rotating_fixed_targets", "llm": "template"},
        "games": results,
        "summary": {
            "correct": len(results) - wrongful,
            "wrongful": wrongful,
            "cold_rate": round(cold_rounds / max(1, len(results)), 4),
            "unique_fingerprints": len({row["fingerprint"] for row in results}),
            "reproducible_inputs": all(row["fingerprint"] for row in results),
        },
        "note": "小样本观察，不设统计区间硬门槛；同 seed/版本/配置可重跑对照",
    }
