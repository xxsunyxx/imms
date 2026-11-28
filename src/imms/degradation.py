"""Anti-degradation audit for IMMS sessions (§14.3 of the original spec).

The audit reads stored state, the drift ledger and game events only.  It never
mutates a session and never asks an LLM to judge: each check reports the
observed symptom, the threshold it was compared with, and whether the decision
is evidence-based or unavailable.  Missing data is reported as ``no_data``,
not as a pass -- this keeps the check honest for legacy sessions.
"""

from . import db, journal

# Thresholds are explicit and documented; they are not fitted from a run.
ANCHOR_COS_MIN = 0.85
RELATION_EDGE = 0.95
RELATION_EDGE_RATIO = 0.50
CHATTER_WINDOW = 3
CHATTER_LIMIT = 1
MEMORY_LIMIT = 24
DEADLOCK_STREAK = 3
IMPERFECT_ACTS = {"accuse", "deflect", "confess", "provoke", "lie_exposed",
                  "caught_lying", "improv_burst", "betray", "provoke"}


def _num(value, default=0.0):
    return value if isinstance(value, (int, float)) else default


def _check(risk, status, detail, threshold=None, evidence=None):
    return {
        "risk": risk,
        "status": status,
        "detail": detail,
        "threshold": threshold,
        "evidence": evidence or [],
    }


def _seats(conn, session_id):
    doc = db.get_session(conn, session_id)
    if not doc:
        return {}
    return {
        p["id"]: p.get("script_character_id") or p["id"]
        for p in doc.get("participants") or [] if p.get("kind") != "gm"
    }


def _persona_drift(conn, session_id):
    rows = list(conn.execute(
        "SELECT round, seat_id, anchor_cos, outcome FROM drift_ledger "
        "WHERE session_id=? ORDER BY round, seq", (session_id,),
    ))
    if not rows:
        return _check("人格漂移", "no_data", "无 style drift ledger，无法判定",
                      {"anchor_cos_min": ANCHOR_COS_MIN})
    scored = [(r["round"], r["seat_id"], _num(r["anchor_cos"], 1.0))
              for r in rows if r["anchor_cos"] is not None]
    if not scored:
        return _check("人格漂移", "no_data", "账本无 anchor_cos 采样",
                      {"anchor_cos_min": ANCHOR_COS_MIN})
    low = [item for item in scored if item[2] < ANCHOR_COS_MIN]
    evidence = [{"round": r, "seat": s, "anchor_cos": round(v, 4)}
                for r, s, v in low[:5]]
    return _check(
        "人格漂移",
        "warn" if low else "ok",
        f"人格余弦低于下限 {len(low)} 次" if low else "人格锚采样均在阈值内",
        {"anchor_cos_min": ANCHOR_COS_MIN}, evidence,
    )


def _relation_explosion(runtime):
    pairs, edges = 0, 0
    for state in (runtime or {}).values():
        for rel in (state.get("relations") or {}).values():
            if not isinstance(rel, dict):
                continue
            for dim, value in rel.items():
                pairs += 1
                if abs(_num(value)) >= RELATION_EDGE:
                    edges += 1
    if not pairs:
        return _check("关系爆炸", "no_data", "无关系数值可检查",
                      {"edge": RELATION_EDGE, "edge_ratio": RELATION_EDGE_RATIO})
    ratio = round(edges / pairs, 4)
    return _check(
        "关系爆炸",
        "warn" if ratio > RELATION_EDGE_RATIO else "ok",
        f"贴边关系占比 {ratio}",
        {"edge": RELATION_EDGE, "edge_ratio": RELATION_EDGE_RATIO},
        [{"edge_pairs": edges, "total_pairs": pairs}],
    )


def _value_chatter(events):
    shifts = [e for e in events if e.get("act_type") == "faction_shift"]
    windows = {}
    for ev in shifts:
        r = int(ev.get("round") or 0)
        bucket = r // CHATTER_WINDOW
        windows[bucket] = windows.get(bucket, 0) + 1
    hot = [{"rounds": f"{k * CHATTER_WINDOW}-{k * CHATTER_WINDOW + CHATTER_WINDOW - 1}",
            "shifts": v} for k, v in sorted(windows.items()) if v > CHATTER_LIMIT]
    return _check(
        "数值抖动",
        "warn" if hot else "ok",
        f"{len(hot)} 个 {CHATTER_WINDOW} 轮窗口内阵营变更过密" if hot else "阵营变更未超过冷却窗口上限",
        {"window": CHATTER_WINDOW, "max_shifts": CHATTER_LIMIT}, hot[:5],
    )


def _hallucination(events, stats):
    hits = [e for e in events if e.get("act_type") in
            {"infoguard_rewrite", "script_lock_violation", "stage_gate"}]
    rewritten = int(_num((stats or {}).get("output_rewritten")))
    blocked = int(_num((stats or {}).get("script_lock_blocked")))
    caught = rewritten + blocked + len(hits)
    return _check(
        "幻觉造事实",
        "warn" if caught else "ok",
        f"输出侧拦截或改写 {caught} 次（拦截本身是护栏生效，不是放行）",
        {"claimed_facts_must_bind": True},
        [{"output_rewritten": rewritten, "script_lock_blocked": blocked,
          "audit_events": len(hits)}],
    )


def _memory_bloat(runtime):
    sizes = []
    for seat, state in (runtime or {}).items():
        items = state.get("memory") or []
        reflections = [i for i in items if i.get("kind") == "reflection"]
        sizes.append({"seat": seat, "total": len(items), "reflections": len(reflections)})
    if not sizes:
        return _check("记忆爆炸", "no_data", "无席位记忆快照", {"memory_limit": MEMORY_LIMIT})
    over = [s for s in sizes if s["total"] > MEMORY_LIMIT]
    return _check(
        "记忆爆炸",
        "warn" if over else "ok",
        f"{len(over)} 个席位情节记忆超过上限" if over else "情节记忆未超上限",
        {"memory_limit": MEMORY_LIMIT}, over[:5],
    )


def _logic_deadlock(events, round_no):
    active = {"clue_release", "contradiction_event", "statement_conflict", "accuse"}
    by_round = {}
    for ev in events:
        if ev.get("act_type") in active:
            by_round.setdefault(int(ev.get("round") or 0), 0)
            by_round[int(ev.get("round") or 0)] += 1
    streak, best, start, best_start = 0, 0, 0, 0
    for r in range(1, max(1, int(round_no)) + 1):
        if by_round.get(r, 0) == 0:
            if streak == 0:
                start = r
            streak += 1
            if streak > best:
                best, best_start = streak, start
        else:
            streak = 0
    return _check(
        "逻辑死锁",
        "warn" if best >= DEADLOCK_STREAK else "ok",
        f"最长连续 {best} 轮无推进事件" if best else "每轮均有推进或矛盾事件",
        {"streak": DEADLOCK_STREAK},
        [{"from_round": best_start, "rounds": best}] if best else [],
    )


def _race_condition(events):
    pairs = [(int(e.get("seq") or 0), int(e.get("round") or 0)) for e in events]
    bad = [{"seq": s, "round": r} for (s, r), (ps, pr) in zip(pairs[1:], pairs)
           if s <= ps or r < pr]
    if len(pairs) < 2:
        return _check("并发竞态", "no_data", "事件不足两条，无法检查单调性",
                      {"seq_strictly_increasing": True})
    return _check(
        "并发竞态",
        "warn" if bad else "ok",
        f"发现 {len(bad)} 处序号或轮次回退" if bad else "全局 seq 与轮次单调",
        {"seq_strictly_increasing": True}, bad[:5],
    )


def _all_rational(events, round_no):
    ai_acts = {}
    for ev in events:
        actor = str(ev.get("actor") or "")
        if not actor.startswith("ai:"):
            continue
        r = int(ev.get("round") or 0)
        ai_acts.setdefault(r, set()).add(ev.get("act_type"))
    total_rounds = max(1, int(round_no))
    imperfect = [r for r, acts in ai_acts.items() if acts & IMPERFECT_ACTS]
    if not ai_acts or total_rounds < 3:
        return _check("全员理性", "no_data", "AI 发言轮次不足，无法判断",
                      {"min_rounds": 3})
    if len(imperfect) == 0:
        return _check(
            "全员理性", "warn", f"连续 {total_rounds} 轮无冲动/隐瞒/撒谎行为",
            {"min_rounds": 3}, [],
        )
    return _check(
        "全员理性", "ok", f"{len(imperfect)}/{total_rounds} 轮出现非完全理性行为",
        {"min_rounds": 3}, [{"rounds": sorted(imperfect)[:8]}],
    )


def audit(conn, session_id):
    """Run all §14.3 checks; read-only and deterministic."""
    runtime = db.get_game_state(conn, session_id, "runtime") or {}
    round_no = int(db.get_game_state(conn, session_id, "round", 0) or 0)
    stats = db.get_game_state(conn, session_id, "infoguard_stats") or {}
    events = journal.audit_events(conn, session_id)
    checks = [
        _persona_drift(conn, session_id),
        _relation_explosion(runtime),
        _value_chatter(events),
        _hallucination(events, stats),
        _memory_bloat(runtime),
        _logic_deadlock(events, round_no),
        _race_condition(events),
        _all_rational(events, round_no),
    ]
    warned = [c["risk"] for c in checks if c["status"] == "warn"]
    missing = [c["risk"] for c in checks if c["status"] == "no_data"]
    return {
        "session_id": session_id,
        "round": round_no,
        "checks": checks,
        "warning_risks": warned,
        "no_data_risks": missing,
        "status": "warn" if warned else ("no_data" if missing else "ok"),
    }


def render_markdown(report):
    """Compact host-side view; never exposes a seat's private payload."""
    lines = ["## 防退化清单（§14.3）", ""]
    lines.append(f"- 总览：**{report['status']}**（第 {report['round']} 轮）")
    for item in report["checks"]:
        detail = item["detail"]
        lines.append(f"- {item['risk']}：{item['status']}｜{detail}")
    lines.append("")
    return "\n".join(lines)
