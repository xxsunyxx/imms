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

from . import db, infoguard, journal, replay, scoring, solver, vote

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


def injection_audit(events):
    """Summarise deterministic GM perturbations recorded in game_events."""
    rows = [e for e in events if e.get("act_type") == "gm_event_injection"]
    by_event = {}
    rounds = []
    for row in rows:
        payload = row.get("payload") or {}
        event_id = payload.get("event_id", "unknown")
        by_event[event_id] = by_event.get(event_id, 0) + 1
        rounds.append({
            "round": row.get("round"), "event_id": event_id,
            "score": payload.get("score", 0), "changes": len(payload.get("changes") or []),
        })
    return {
        "status": "ok" if rows else "empty",
        "count": len(rows),
        "by_event": by_event,
        "rounds": rounds,
    }


def improvisation_metrics(events, runtime_state=None, solvability_verdict=None):
    """G21 §15.1: ten deterministic metrics from stored improv_burst events.

    No LLM and no new numeric facts: every value is derived from event payloads
    already written by G17–G20.  ``target`` is the acceptance band from the
    original spec; ``ok`` is a descriptive check, not a gate on gameplay.
    """
    bursts = [e for e in events if e.get("act_type") == "improv_burst"]
    overt = [e for e in bursts if (e.get("payload") or {}).get("tier") in ("meso", "macro")]
    total_rounds = max(1, int((runtime_state or {}).get("round") or len(
        {e.get("round") for e in events}
    ) or 1))
    seats = [k for k in (runtime_state or {}) if not str(k).startswith("gm:")]
    person_rounds = max(1, len(seats) * total_rounds)

    effective = 0
    surprises, fidelities = [], []
    rejected = 0
    candidates = 0
    fallback = 0
    depth_counts = {1: 0, 2: 0}
    aftermath_covered = 0
    max_drift = 0.0
    for event in overt:
        payload = event.get("payload") or {}
        aft = payload.get("aftermath") or {}
        choices = aft.get("observers_delta") or {}
        # Effective = somebody's relationship vector moved > 0.15, or a cascade
        # reached another seat, or a commitment/claim changed.
        moved = any(
            abs(_num((delta or {}).get("trust"))) > 0.15
            or abs(_num((delta or {}).get("like"))) > 0.15
            for delta in choices.values()
        )
        if moved or aft.get("cascade"):
            effective += 1
        if aft.get("strategy"):
            aftermath_covered += 1
        direction = payload.get("direction") or {}
        chosen = direction.get("selected") or {}
        if chosen:
            surprises.append(_num(chosen.get("surprise")))
            fidelities.append(_num(chosen.get("fidelity")))
        for candidate in direction.get("candidates") or []:
            candidates += 1
            if not candidate.get("accepted"):
                rejected += 1
        if (payload.get("action_key") or "") == "safe_library":
            fallback += 1
        for item in aft.get("cascade") or []:
            depth = int(item.get("depth") or 0)
            if depth in depth_counts:
                depth_counts[depth] += 1
        max_drift = max(max_drift, abs(_num(
            (aft.get("self_concept_delta") or {}).get("l1_norm")
        )))

    def band(value, lo, hi):
        return lo <= value <= hi

    rate = round(effective / len(overt), 4) if overt else 0.0
    density = round(len(overt) / person_rounds, 4)
    surprise_mean = round(sum(surprises) / len(surprises), 4) if surprises else None
    fidelity_mean = round(sum(fidelities) / len(fidelities), 4) if fidelities else None
    ooc_rate = round(rejected / candidates, 4) if candidates else 0.0
    fallback_rate = round(fallback / len(bursts), 4) if bursts else 0.0
    total_cascade = depth_counts[1] + depth_counts[2]
    d1_share = round(depth_counts[1] / total_cascade, 4) if total_cascade else 0.0
    coverage = round(aftermath_covered / len(overt), 4) if overt else 1.0
    return {
        "effective_improv_rate": rate,
        "improv_density": density,
        "surprise_mean": surprise_mean,
        "fidelity_mean": fidelity_mean,
        "ooc_reject_rate": ooc_rate,
        "fallback_share": fallback_rate,
        "cascade_depth": {"d1": d1_share, "d2": round(1 - d1_share, 4), "d3plus": 0},
        "aftermath_coverage": coverage,
        "persona_drift": round(max_drift, 6),
        "solvability_preserved": solvability_verdict == "ok",
        "bursts": len(bursts), "overt": len(overt),
        "checks": {
            "effective_improv_rate": band(rate, 0.30, 0.60) if overt else None,
            "improv_density": band(density, 0.15, 0.35) if overt else None,
            "surprise_mean": band(surprise_mean, 1.8, 2.3) if surprise_mean is not None else None,
            "fidelity_mean": (fidelity_mean >= 0.80) if fidelity_mean is not None else None,
            "ooc_reject_rate": band(ooc_rate, 0.10, 0.25) if candidates else None,
            "fallback_share": band(fallback_rate, 0.30, 0.45) if bursts else None,
            "cascade_depth": (total_cascade == 0) or (
                band(d1_share, 0.60, 0.80) and depth_counts[2] > 0
            ),
            "aftermath_coverage": coverage == 1.0,
            "persona_drift": max_drift <= 0.08,
            "solvability_preserved": solvability_verdict == "ok",
        },
    }


def _num(value, default=0.0):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if -1e9 < v < 1e9 else default


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
            "vote_audit": vote.audit(conn, session_id),
            "event_injections": injection_audit(events),
            "improvisation": improvisation_metrics(
                events,
                db.get_game_state(conn, session_id, "runtime", {}),
                audit["verdict"],
            ),
            "timeline": {
                "order_conflicts": audit["timeline"]["order_conflicts"],
                "warnings": audit["timeline"]["warnings"],
            },
            "scoring": db.get_game_state(conn, session_id, "scoring", {})
            or scoring.evaluate(conn, session_id),
            "script_locks": db.get_game_state(conn, session_id, "script_locks", {}),
            "affect": {
                "trauma_triggers": len(journal.audit_events(
                    conn, session_id, act_type="trauma_trigger")),
                "contradiction_events": len(journal.audit_events(
                    conn, session_id, act_type="contradiction_event")),
                "faction_shifts": len(journal.audit_events(
                    conn, session_id, act_type="faction_shift")),
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
    va = retro["vote_audit"]
    lines.extend(["", "**投票审计**", ""])
    if va["status"] == "ok":
        reason = "—" if va["vote_reasonableness"] is None else f"{va['vote_reasonableness']:.0%}"
        lines.append(
            f"- 有效票率 {va['legal_rate']:.0%}｜弃票率 {va['abstention_rate']:.0%}"
            f"｜证据支持率 {reason}（{va['evidence_supported']}/{va['evidence_judged']}）"
        )
        lines.append(
            f"- 自我投票拦截 {va['self_votes_blocked']}｜非法目标 {va['unknown_targets']}"
            f"｜胜者合法 {'是' if va['winner_legal'] else '否'}"
            f"｜平票 {va['tie_resolved_by'] or '无'}"
        )
    else:
        lines.append("- （无投票账本，无法审计；旧会话只保留胜负结果）")
    inj = retro["event_injections"]
    lines.extend(["", "**GM 事件注入审计**", ""])
    if inj["status"] == "ok":
        lines.append(
            f"- 注入 {inj['count']} 次：{inj['by_event']}"
        )
        for item in inj["rounds"]:
            lines.append(
                f"  - 第{item['round']}轮 {item['event_id']}（强度 {item['score']}，变量变更 {item['changes']} 项）"
            )
    else:
        lines.append("- 本局无外部扰动注入")
    lines.extend(["", "**转折点 Top 5**", ""])
    if retro["turning_points"]:
        for idx, item in enumerate(retro["turning_points"], 1):
            lines.append(
                f"{idx}. 第{item['round']}轮 · {item['character_id']} · {item['key']}"
                f"（{item['target'] or '自身'}）：{item['before']} → {item['after']}"
            )
    else:
        lines.append("- （无显著数值转折）")
    ips = retro["improvisation"]
    lines.extend(["", "**IPS 即兴指标看板**", ""])
    lines.append(
        f"- 有效即兴率 {ips['effective_improv_rate']:.0%}（目标 30–60%）"
        f"｜即兴密度 {ips['improv_density']:.2f}/人轮"
        f"｜爆发 {ips['overt']} 次（总计 {ips['bursts']}）"
    )
    lines.append(
        f"- 意外度均值 {ips['surprise_mean']}（1.8–2.3）"
        f"｜保真度均值 {ips['fidelity_mean']}（≥0.80）"
        f"｜OOC 拒绝率 {ips['ooc_reject_rate']:.0%}（10–25%）"
        f"｜兜底占比 {ips['fallback_share']:.0%}（30–45%）"
    )
    depth = ips["cascade_depth"]
    lines.append(
        f"- 级联 d1 {depth['d1']:.0%} / d2 {depth['d2']:.0%} / d≥3 {depth['d3plus']:.0%}"
        f"｜余波覆盖 {ips['aftermath_coverage']:.0%}"
        f"｜人格漂移 {ips['persona_drift']}（≤0.08）"
        f"｜可解性保持 {'是' if ips['solvability_preserved'] else '否'}"
    )
    failed = [k for k, v in ips["checks"].items() if v is False]
    if failed:
        lines.append("- 未落目标区间：" + "、".join(failed))
    else:
        lines.append("- 全部有样本的指标均落在目标区间")
    settled = retro.get("scoring") or {}
    if settled:
        ending = settled.get("ending") or {}
        players = (settled.get("scores") or {}).get("players") or {}
        lines.extend(["", "**结局与个人目标评分**", ""])
        lines.append(
            f"- 结局：{ending.get('code', '—')}（依据 {ending.get('source', '—')}）"
            f"｜玩家评分："
            + "、".join(
                f"{item['name']}={item['score'] if item['score'] is not None else '无法判定'}"
                for item in players.values()
            )
        )
        lines.append("- 纯文本目标未给机读判据时记为 unscored；完整明细见复盘第 11 节")
    warnings = retro["timeline"]["order_conflicts"] + retro["timeline"]["warnings"]
    lines.extend(["", "**时间线检查**", ""])
    if warnings:
        for item in warnings:
            lines.append(f"- {item['kind']}：{item['detail']}")
    else:
        lines.append("- 时间线顺序无冲突")
    locks = retro.get("script_locks") or {}
    if locks:
        lines.extend(["", "**剧本锁（R4）**", ""])
        lines.append(f"- 状态：{locks.get('status')}｜统计：{locks.get('counts')}")
        for item in locks.get("failed") or []:
            lines.append(f"- 未通过：{item.get('owner')}｜{item.get('type')}｜{item.get('status')}")
    affect = retro.get("affect") or {}
    if affect:
        lines.extend(["", "**情绪与阵营**", ""])
        lines.append(
            f"- 创伤触发 {affect['trauma_triggers']}｜矛盾事件 {affect['contradiction_events']}"
            f"｜阵营变更 {affect['faction_shifts']}"
        )
    return "\n".join(lines) + "\n"


def evaluate_and_render(conn, session_id):
    report = evaluate(conn, session_id)
    return report, render_evaluation(report)
