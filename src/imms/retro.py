"""Post-game retrospective evaluation.

The five deterministic metrics and the scoring formula follow
lockedroom-ai/backend/app/core/replayeval/metrics.py.  That project does not
implement decision-time information verification, turning-point attribution,
timeline checks or branch counterfactuals; those are added here on top of the
IMMS event log and the solver audit.

All numbers are derived from stored events and the Ontology.  No LLM retells
the story and no session state is mutated.
"""

import json

from . import db, infoguard, replay, solver

WEIGHT_HARD = 1.0
WEIGHT_SOFT = 0.3
SCORE_WEIGHTS = {"coverage": 0.45, "killer": 0.35, "motive": 0.20}
SCORE_PENALTY = 20.0


def _grade(score):
    if score >= 90:
        return "S"
    if score >= 75:
        return "A"
    if score >= 60:
        return "B"
    return "C"


def _events(conn, session_id):
    return replay._events(conn, session_id)


def _human_seats(session_doc):
    return {
        p["id"] for p in session_doc.get("participants", [])
        if p.get("kind") == "human_character"
    }


def _fact_ids_from_clue(onto, clue):
    fmap = onto.get("clue_fact_map") or {}
    ids = fmap.get(clue) or []
    if not ids:
        cid = str(clue).split(" ", 1)[0].split("|", 1)[0]
        ids = fmap.get(cid) or []
    if isinstance(ids, str):
        ids = [ids]
    for structured in onto.get("clues") or []:
        if structured.get("id") == str(clue).split(" ", 1)[0].split("|", 1)[0]:
            ids = ids or list(structured.get("reveals_fact_ids") or [])
    return [fid for fid in ids if fid]


def compute_metrics(onto, events, result, human_seats=None, scripted_motive=None):
    """lockedroom-ai ReplayEval metrics, adapted to IMMS game_events."""
    human_seats = human_seats or set()
    unlocked, hard, soft = set(), 0, 0
    replies = 0
    for event in events:
        payload = event.get("payload") or {}
        actor = event.get("actor")
        if event.get("act_type") == "clue_release" and payload.get("clue"):
            unlocked.add(payload["clue"])
        guard = payload.get("guard")
        if guard:
            leaks = guard.get("leaks")
            if leaks is None:
                leaks = [
                    {"fact_id": fid, "severity": "hard" if fid in guard.get("hard_facts", []) else "soft"}
                    for fid in guard.get("facts", [])
                ]
            for leak in leaks:
                if leak.get("severity") == "hard":
                    hard += 1
                else:
                    soft += 1
        if (
            payload.get("utterance")
            and actor not in human_seats
            and not str(actor).startswith("gm:")
            and event.get("act_type") not in {"private_say", "infoguard", "llm_fallback"}
        ):
            replies += 1

    leak_events = hard + soft
    leak_rate = round((hard * WEIGHT_HARD + soft * WEIGHT_SOFT) / replies, 4) if replies else 0.0

    required = set((onto.get("evaluation") or {}).get("required_fact_ids") or [])
    known_facts = set()
    for clue in unlocked:
        known_facts.update(_fact_ids_from_clue(onto, clue))
    covered = known_facts & required
    clue_coverage = round(len(covered) / len(required), 4) if required else 0.0

    killer_correct = bool(result.get("solved"))
    motive_id = scripted_motive if scripted_motive is not None else result.get("motive_fact_id")
    expected_motive = (onto.get("evaluation") or {}).get("motive_fact_id")
    motive_correct = bool(motive_id and expected_motive and motive_id == expected_motive)

    score = round(max(
        0.0,
        100.0 * (
            SCORE_WEIGHTS["coverage"] * clue_coverage
            + SCORE_WEIGHTS["killer"] * float(killer_correct)
            + SCORE_WEIGHTS["motive"] * float(motive_correct)
        ) - SCORE_PENALTY * leak_rate,
    ), 1)
    characters = onto.get("characters") or []
    talkable = [c for c in characters if not c.get("is_victim")]
    private = [f for f in (onto.get("facts") or []) if f.get("visibility") == "private"]
    difficulty = round(min(10.0, len(required) + len(talkable) * 0.5 + len(private) * 0.2), 1)
    return {
        "leak_rate": leak_rate,
        "clue_coverage": clue_coverage,
        "killer_correct": killer_correct,
        "motive_correct": motive_correct,
        "score": score,
        "grade": _grade(score),
        "difficulty": difficulty,
        "clues_unlocked": len(unlocked),
        "character_replies": replies,
        "leak_events": leak_events,
        "hard_leaks": hard,
        "soft_leaks": soft,
        "covered_required_fact_ids": sorted(covered),
        "unlocked_clues": sorted(unlocked),
    }


def decision_time_info_check(onto, events, human_seats):
    """Offline re-check of every AI line against what it knew at that moment."""
    engine = infoguard.build_engine(onto)
    public_facts = {
        fid for fid, fact in infoguard.fact_map(onto).items()
        if fact.get("visibility") == "public"
    }
    known = set(public_facts)
    findings_by_actor = {}
    safe_lines = 0
    for event in events:
        payload = event.get("payload") or {}
        actor = event.get("actor")
        if event.get("act_type") == "clue_release" and payload.get("clue"):
            engine.exempt_clue(payload["clue"])
            known.update(_fact_ids_from_clue(onto, payload["clue"]))
        if (
            not payload.get("utterance")
            or actor in human_seats
            or str(actor).startswith("gm:")
            or event.get("act_type") != "assert"
        ):
            continue
        findings = engine.check_output(actor, payload["utterance"])
        if findings:
            findings_by_actor.setdefault(actor, []).append({
                "round": event.get("round"),
                "findings": [f.as_dict() for f in findings],
            })
        else:
            safe_lines += 1
    checks = []
    for event in events:
        payload = event.get("payload") or {}
        if not payload.get("claimed_facts"):
            continue
        actor = event.get("actor")
        scope = engine.scope_for(actor)
        scope_known = set(scope.known_fact_ids) if scope else set(public_facts)
        for fid in payload["claimed_facts"]:
            checks.append({
                "round": event.get("round"), "actor": actor, "fact_id": fid,
                "available_at_decision": fid in known or fid in scope_known,
            })
    return {
        "safe_lines": safe_lines,
        "offline_leaks": findings_by_actor,
        "claim_checks": checks,
        "claims_valid": all(c["available_at_decision"] for c in checks),
    }


def turning_points(conn, session_id, limit=5):
    rows = conn.execute(
        "SELECT * FROM attribute_events WHERE session_id=? AND layer='L3'", (session_id,)
    ).fetchall()
    changes = []
    for row in rows:
        try:
            before = json.loads(row["before_json"])["value"]
            after = json.loads(row["after_json"])["value"]
            delta = float(after) - float(before)
        except (ValueError, KeyError, TypeError):
            continue
        changes.append({
            "round": row["round"], "character_id": row["character_id"],
            "key": row["key"], "target": row["target"],
            "before": round(float(before), 4), "after": round(float(after), 4),
            "delta": round(delta, 4), "reason": row["reason"],
        })
    changes.sort(key=lambda item: -abs(item["delta"]))
    return changes[:limit]


def counterfactual_strength(solution):
    """How decisive is the evidence attribution, without actually replaying."""
    scores = solution.get("scores") or {}
    ordered = sorted(scores.values(), reverse=True)
    top = ordered[0] if ordered else 0.0
    second = ordered[1] if len(ordered) > 1 else 0.0
    margin = round(top - second, 4)
    return {
        "top": solution.get("top"), "top_score": top, "runner_up_score": second,
        "margin": margin,
        "decisive": bool(solution.get("unique") and margin >= 0.5),
        "note": "静态证据指向强度；真实分支对照由 GM 决策核的 diff 提供",
    }


def _branch_counterfactual(conn, session_id, solution):
    """Attach real GM branch diffs to the static evidence-strength estimate."""
    report = counterfactual_strength(solution)
    branches = []
    rows = conn.execute(
        """SELECT branch_id, label, status, role, report_json FROM gm_branches
           WHERE session_id=? AND status IN ('active', 'merged')
           ORDER BY created_at, branch_id""",
        (session_id,),
    ).fetchall()
    for row in rows:
        try:
            data = json.loads(row["report_json"]) if row["report_json"] else {}
        except (TypeError, ValueError):
            data = {}
        diff = data.get("diff") or {}
        branches.append({
            "branch_id": row["branch_id"], "label": row["label"],
            "status": row["status"], "role": row["role"],
            "summary": diff.get("summary", {}), "outcome": diff.get("outcome", {}),
        })
    report["branches"] = branches
    return report


def evaluate(conn, session_id, scripted_motive=None):
    session_doc = db.get_session(conn, session_id)
    onto = db.get_script(conn, session_doc["script_id"])
    result = db.get_game_state(conn, session_id, "result", {})
    events = _events(conn, session_id)
    human = _human_seats(session_doc)
    metrics = compute_metrics(onto, events, result, human, scripted_motive)
    audit = solver.audit_script(onto)
    return {
        "session_id": session_id,
        "script_id": onto.get("script_id"),
        "title": onto.get("title"),
        "metrics": metrics,
        "retrospective": {
            "decision_time_info": decision_time_info_check(onto, events, human),
            "turning_points": turning_points(conn, session_id),
            "solvability": {
                "verdict": audit["verdict"],
                "checks": audit["checks"],
                "summary": audit["summary"],
            },
            "counterfactual": _branch_counterfactual(conn, session_id, audit["solution"]),
            "timeline": {
                "order_conflicts": audit["timeline"]["order_conflicts"],
                "warnings": audit["timeline"]["warnings"],
            },
        },
    }


def render_evaluation(report):
    """Markdown section appended to the five-section replay."""
    m = report["metrics"]
    retro = report["retrospective"]
    info = retro["decision_time_info"]
    lines = [
        "## 6. 回溯评估", "",
        f"- 综合评分：**{m['score']}（{m['grade']}）**｜难度 {m['difficulty']}",
        f"- 线索覆盖 {m['clue_coverage']:.0%}｜指认正确 {'是' if m['killer_correct'] else '否'}"
        f"｜动机正确 {'是' if m['motive_correct'] else '否'}",
        f"- 泄露率 {m['leak_rate']}（hard {m['hard_leaks']} / soft {m['soft_leaks']}"
        f"，AI 发言 {m['character_replies']} 条）",
        f"- 剧本可解性：{retro['solvability']['verdict']}",
        "",
        "**决策时点信息边界复核**", "",
        f"- 离线复检安全台词 {info['safe_lines']} 条；"
        f"声称事实可用 {sum(1 for c in info['claim_checks'] if c['available_at_decision'])}"
        f"/{len(info['claim_checks'])}",
    ]
    for actor, hits in info["offline_leaks"].items():
        lines.append(f"- {actor} 有 {len(hits)} 条台词离线复检命中越界风险")
    lines.extend(["", "**证据指向强度**", ""])
    cf = retro["counterfactual"]
    lines.append(
        f"- 最高指向 {cf['top']}：{cf['top_score']}，次高 {cf['runner_up_score']}，"
        f"差 {cf['margin']}｜{'可判定' if cf['decisive'] else '不足以判定'}"
    )
    lines.append("- " + cf["note"])
    for branch in cf.get("branches") or []:
        summary = branch["summary"]
        outcome = branch["outcome"]
        lines.append(
            f"- 分支 {branch['branch_id']}（{branch['label'] or '未命名'}，{branch['status']}）："
            f"线索变化 {summary.get('clue_changes', 0)}、台词变化 {summary.get('utterance_changes', 0)}、"
            f"结局变化 {'是' if summary.get('outcome_changed') else '否'}"
            f"（{outcome.get('base', {}).get('winner')} → {outcome.get('fork', {}).get('winner')}）"
        )
    lines.extend(["", "**转折点 Top 5**", ""])
    if retro["turning_points"]:
        for idx, item in enumerate(retro["turning_points"], 1):
            lines.append(
                f"{idx}. 第{item['round']}轮 · {item['character_id']} · {item['key']}"
                f"（{item['target'] or '自身'}）：{item['before']} → {item['after']}"
            )
    else:
        lines.append("- （无显著数值转折）")
    warnings = retro["timeline"]["order_conflicts"] + retro["timeline"]["warnings"]
    lines.extend(["", "**时间线检查**", ""])
    if warnings:
        for item in warnings:
            lines.append(f"- {item['kind']}：{item['detail']}")
    else:
        lines.append("- 时间线顺序无冲突")
    return "\n".join(lines) + "\n"


def evaluate_and_render(conn, session_id):
    report = evaluate(conn, session_id)
    return report, render_evaluation(report)
