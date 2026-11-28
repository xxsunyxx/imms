"""Ending resolution, goal settlement and multi-objective scoring.

G08 / F27 / player six-dimension scoring.  Everything here is recomputed from
the stored ontology, game_state and game_events; no LLM is asked to judge a
goal and no private fact is inferred from prose.

Evidence policy:

* an ending is only attached to script text when the script supplies either a
  structured ``condition`` or the legacy "correct first, incorrect second"
  ordering.  Otherwise the ending code is derived and the text is empty;
* a personal goal is only scored when the script supplies a machine-checkable
  ``check`` (secret facts / identify / escape / frame / vote_for).  Plain-text
  goals stay ``unscored`` instead of being guessed from wording;
* score components with no evidence are ``None``.  The aggregate is the
  weighted mean over the components that do exist, with ``coverage`` showing
  how much of the rubric was actually grounded.
"""

import json

from . import db, gm_direction, infoguard, journal

# F27: Score_AI = w1 Goal + w2 Persona + w3 Drama + w4 Consistency - w5 Violations
AI_WEIGHTS = {
    "goal": 0.30, "persona": 0.25, "drama": 0.20,
    "consistency": 0.15, "violations": 0.10,
}
# §14.1 player dimensions
PLAYER_WEIGHTS = {
    "reasoning": 0.25, "evidence": 0.15, "persona": 0.10,
    "social": 0.15, "hidden_goal": 0.20, "violations": 0.15,
}
DRAMA_ACTS = {"assert", "accuse", "contradiction", "clue_found", "improv_burst"}
RELATION_KEYS = ("like", "trust", "intimacy", "grudge", "fear", "suspicion", "commitment")


def _num(value, default=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if -1e9 < number < 1e9 else default


def _clean(value, limit=160):
    text = str(value or "").strip()
    return text[:limit]


def resolve_people(session_doc, onto):
    """Map seats/characters without leaking internal ids into public payloads."""
    participants = session_doc.get("participants", []) if session_doc else []
    seat_ids = [p["id"] for p in participants if p.get("kind") != "gm"]
    char_to_seat = {
        p["script_character_id"]: p["id"]
        for p in participants if p.get("script_character_id")
    }
    seat_to_char = {seat: char for char, seat in char_to_seat.items()}
    names = {c.get("id"): c.get("name") or c.get("id")
             for c in (onto or {}).get("characters", []) if isinstance(c, dict)}
    seat_to_name = {}
    for participant in participants:
        seat = participant.get("id")
        char = participant.get("script_character_id")
        if seat and seat in seat_ids:
            seat_to_name[seat] = names.get(char) or participant.get("seat_label") or seat
    return {
        "seat_ids": seat_ids,
        "char_to_seat": char_to_seat,
        "seat_to_char": seat_to_char,
        "names": names,
        "seat_to_name": seat_to_name,
    }


def _to_seat(value, people):
    text = str(value or "").strip()
    if not text:
        return None
    if text in people["seat_ids"]:
        return text
    if text in people["char_to_seat"]:
        return people["char_to_seat"][text]
    if text in people["names"].values():
        for cid, name in people["names"].items():
            if name == text:
                return people["char_to_seat"].get(cid)
    return None


def public_fact_ids(onto, events, revealed=None):
    """Facts the whole table can legitimately use at the end of the game."""
    out = {
        fid for fid, fact in infoguard.fact_map(onto).items()
        if fact.get("visibility") == "public"
    }
    fmap = onto.get("clue_fact_map") or {}
    for clue in gm_direction.revealed_ids(revealed):
        out.update(fmap.get(clue) or [])
    for clue in (onto.get("clues") or []):
        if not isinstance(clue, dict):
            continue
        fid = str(clue.get("id") or "")
        if fid in gm_direction.revealed_ids(revealed):
            out.update(clue.get("reveals_fact_ids") or [])
    for event in events or []:
        visibility = event.get("visibility") or []
        if "public" not in visibility:
            continue
        payload = event.get("payload") or {}
        out.update(str(f) for f in (payload.get("claimed_facts") or []) if f)
        if payload.get("clue"):
            out.update(fmap.get(payload["clue"]) or [])
    return out


def _last_ballots(vote_state, people):
    choices = {}
    for entry in (vote_state or {}).get("rounds") or []:
        for ballot in entry.get("ballots") or []:
            if ballot.get("voter"):
                choices[ballot["voter"]] = ballot.get("choice")
    for ballot in (vote_state or {}).get("manual_ballots") or []:
        if ballot.get("voter"):
            choices[ballot["voter"]] = ballot.get("choice")
    return {
        voter: (_to_seat(target, people) or target)
        for voter, target in choices.items()
    }


def _relation_influence(conn, session_id, char_to_seat):
    totals = {}
    rows = conn.execute(
        "SELECT character_id, key, before_json, after_json FROM attribute_events "
        "WHERE session_id=? AND layer='L3'", (session_id,),
    ).fetchall()
    for row in rows:
        key = str(row["key"] or "")
        if not any(word in key for word in ("relation", "suspicion", *RELATION_KEYS)):
            continue
        try:
            before = _num(json.loads(row["before_json"] or "{}").get("value"))
            after = _num(json.loads(row["after_json"] or "{}").get("value"))
        except (TypeError, ValueError):
            continue
        seat = char_to_seat.get(row["character_id"])
        if seat:
            totals[seat] = totals.get(seat, 0.0) + abs(after - before)
    return {seat: round(value, 4) for seat, value in totals.items()}


def _persona_from_rows(rows, seats):
    buckets = {seat: [] for seat in seats}
    for row in rows:
        seat = row["seat_id"]
        if seat in buckets and row["anchor_cos"] is not None:
            buckets[seat].append(_num(row["anchor_cos"]))
    return {
        seat: round(sum(values) / len(values), 4)
        for seat, values in buckets.items() if values
    }


def _seat_signals(events, seats, culprit_seat):
    signals = {
        seat: {"speech": 0, "drama": 0, "conflicts": 0, "violations": 0,
               "clues": 0, "deflects": 0, "accusations": 0, "lies": 0}
        for seat in seats
    }
    for event in events:
        actor = event.get("actor")
        if actor not in signals:
            continue
        act = str(event.get("act_type") or "")
        payload = event.get("payload") or {}
        if payload.get("utterance"):
            signals[actor]["speech"] += 1
        if act in DRAMA_ACTS or payload.get("inner_intent"):
            signals[actor]["drama"] += 1
        if act == "clue_found":
            signals[actor]["clues"] += 1
        if act == "deflect":
            signals[actor]["deflects"] += 1
        if act == "accuse":
            signals[actor]["accusations"] += 1
        if act == "statement_conflict" and payload.get("witness") == actor:
            signals[actor]["conflicts"] += 1
        if payload.get("lie_id") or act == "lie_exposed":
            signals[actor]["lies"] += 1
        guard = payload.get("guard") or {}
        if guard.get("leaks") or guard.get("stage_gate") or guard.get("action") == "rewrite":
            signals[actor]["violations"] += 1
    out = {}
    for seat, data in signals.items():
        rows = data["speech"] or 1
        out[seat] = {
            **data,
            "consistency": round(1.0 - min(1.0, data["conflicts"] / rows), 4),
            "violation_rate": round(min(1.0, data["violations"] / rows), 4),
        }
    return out


def _normalised_goal(raw):
    if isinstance(raw, dict):
        text = _clean(raw.get("text") or raw.get("goal") or raw.get("description"))
        check = raw.get("check") or raw.get("criteria")
        if isinstance(check, str):
            check = {"type": check}
        return {
            "id": _clean(raw.get("id")) or None,
            "text": text,
            "check": dict(check) if isinstance(check, dict) else None,
        }
    return {"id": None, "text": _clean(raw), "check": None}


def _check_goal(goal, ctx, people, seat):
    check = goal["check"]
    if not check:
        return {"status": "unscored", "reason": "剧本未给出可机读判定条件",
                "evidence": []}
    kind = str(check.get("type") or "").strip().lower()
    result = ctx["result"]
    if kind in {"secret", "hide_fact", "keep_secret"}:
        fids = [str(f) for f in (check.get("fact_ids") or check.get("facts") or []) if f]
        if not fids:
            return {"status": "unscored", "reason": "secret 判据缺少 fact_ids", "evidence": []}
        leaked = sorted(set(fids) & ctx["public_facts"])
        return {
            "status": "met" if not leaked else "unmet",
            "reason": "秘密事实未进入公开视野" if not leaked else "秘密事实已进入公开视野",
            "evidence": leaked,
        }
    if kind in {"identify", "solve", "identify_culprit"}:
        voter = check.get("voter")
        if voter is not None and _to_seat(voter, people) != seat:
            return {"status": "unscored", "reason": "判据指定了其他投票人", "evidence": []}
        return {
            "status": "met" if result.get("solved") else "unmet",
            "reason": "投票指认真凶" if result.get("solved") else "未指认真凶",
            "evidence": [str(result.get("winner"))] if result.get("winner") else [],
        }
    if kind in {"escape", "evade", "culprit_escape"}:
        if seat != ctx["culprit_seat"]:
            return {"status": "unscored", "reason": "逃脱判据只适用于真凶席位", "evidence": []}
        return {
            "status": "met" if not result.get("solved") else "unmet",
            "reason": "真凶未被指认" if not result.get("solved") else "真凶被指认",
            "evidence": [str(result.get("winner"))] if result.get("winner") else [],
        }
    if kind in {"frame", "framing"}:
        target = _to_seat(check.get("target"), people)
        if not target:
            return {"status": "unscored", "reason": "栽赃判据缺少有效 target", "evidence": []}
        framed = result.get("winner") == target and target != ctx["culprit_seat"]
        return {
            "status": "met" if framed else "unmet",
            "reason": "被栽赃者成为最终指认对象" if framed else "栽赃未成功",
            "evidence": [people["seat_to_name"].get(target, target)],
        }
    if kind in {"vote_for", "vote"}:
        target = _to_seat(check.get("target"), people)
        if not target:
            return {"status": "unscored", "reason": "投票判据缺少有效 target", "evidence": []}
        voted = ctx["last_votes"].get(seat) == target
        return {
            "status": "met" if voted else "unmet",
            "reason": "末轮投票指向目标" if voted else "末轮未投目标",
            "evidence": [people["seat_to_name"].get(target, target)],
        }
    return {"status": "unscored", "reason": f"不支持的判据类型：{kind}", "evidence": []}


def settle_goals(ctx, people):
    """Per-seat goal outcomes; plain-text goals are never scored by guesswork."""
    per_seat = {}
    for char, raw_goals in ((c.get("id"), c.get("goals") or [])
                            for c in (ctx["onto"].get("characters") or [])
                            if isinstance(c, dict)):
        seat = people["char_to_seat"].get(char)
        if not seat:
            continue
        rows = []
        for index, raw in enumerate(raw_goals, 1):
            goal = _normalised_goal(raw)
            verdict = _check_goal(goal, ctx, people, seat)
            rows.append({
                "id": goal["id"] or f"goal_{index}",
                "text": goal["text"],
                "status": verdict["status"],
                "reason": verdict["reason"],
                "evidence": verdict["evidence"],
            })
        scored = [row for row in rows if row["status"] in {"met", "unmet"}]
        met = sum(1 for row in scored if row["status"] == "met")
        per_seat[seat] = {
            "character_id": char,
            "name": people["names"].get(char, char),
            "goals": rows,
            "scored": len(scored),
            "met": met,
            "completion": round(met / len(scored), 4) if scored else None,
            "unscored": len(rows) - len(scored),
        }

    culprit_seat = ctx["culprit_seat"]
    signals = ctx["signals"].get(culprit_seat, {}) if culprit_seat else {}
    votes = ctx["last_votes"]
    culprit_vote = votes.get(culprit_seat) if culprit_seat else None
    strategy = {
        "culprit": people["seat_to_name"].get(culprit_seat) if culprit_seat else None,
        "deflects": signals.get("deflects", 0),
        "accusations": signals.get("accusations", 0),
        "lies": signals.get("lies", 0),
        "framing_ballots": int(bool(
            culprit_vote and culprit_vote != culprit_seat
            and culprit_vote != ctx["result"].get("winner")
        )),
        "basis": "game_events / vote_state；只记可观察行为，不推断目标是否达成",
    }
    return {"per_seat": per_seat, "culprit_strategy": strategy}


def _condition_hit(condition, ctx, people):
    """Evaluate a script condition without eval(); unknown keys are warnings."""
    if not isinstance(condition, dict):
        return False, ["condition 不是对象"]
    warnings = []
    checks = []
    for key, expected in condition.items():
        if key in {"all", "any", "not"}:
            if key == "not":
                ok, warn = _condition_hit(expected, ctx, people)
                checks.append(not ok)
                warnings.extend(warn)
                continue
            items = expected if isinstance(expected, list) else [expected]
            results = [_condition_hit(item, ctx, people) for item in items]
            for _, warn in results:
                warnings.extend(warn)
            checks.append(all(ok for ok, _ in results) if key == "all"
                          else any(ok for ok, _ in results))
            continue
        if key == "solved":
            actual = bool(ctx["result"].get("solved"))
        elif key == "tie":
            actual = bool(ctx["result"].get("tie"))
        elif key == "motive_correct":
            actual = bool(ctx["motive_correct"])
        elif key == "winner":
            actual = ctx["result"].get("winner") == _to_seat(expected, people)
        elif key == "min_clue_coverage":
            actual = ctx["clue_coverage"] >= _num(expected)
        elif key == "min_goal_completion":
            actual = ctx["goal_completion"] is not None and ctx["goal_completion"] >= _num(expected)
        elif key == "max_violations":
            actual = ctx["violation_rate"] <= _num(expected)
        else:
            warnings.append(f"未知条件字段：{key}")
            continue
        checks.append(actual == expected if isinstance(expected, bool) else actual)
    return all(checks) and not warnings, warnings


def resolve_ending(onto, ctx, people):
    """Structured conditions first; legacy ending order as a documented fallback."""
    structured = []
    for source in (onto.get("endings") or [], (onto.get("evaluation") or {}).get("endings") or []):
        for item in source:
            if isinstance(item, dict):
                structured.append(item)
    matched = []
    warnings = []
    for index, item in enumerate(structured):
        condition = item.get("condition") or item.get("when")
        if not condition:
            continue
        ok, warn = _condition_hit(condition, ctx, people)
        warnings.extend(warn)
        if ok:
            matched.append({
                "id": item.get("id") or f"ending_{index + 1}",
                "text": _clean(item.get("text") or item.get("narration")),
                "priority": _num(item.get("priority"), index),
                "order": index,
            })
    if matched:
        matched.sort(key=lambda row: (-row["priority"], row["order"]))
        winner = matched[0]
        return {
            "code": winner["id"], "text": winner["text"],
            "source": "script_condition", "warnings": warnings,
        }

    texts = [x for x in (onto.get("endings") or []) if isinstance(x, str) and x.strip()]
    solved = bool(ctx["result"].get("solved"))
    if solved and texts:
        text, code, source = texts[0], "culprit_caught", "script_order"
    elif texts and len(texts) > 1:
        text, code, source = texts[1], "wrongful_conviction", "script_order"
    elif texts and not solved:
        text, code, source = "", "wrongful_conviction", "no_script_text"
    else:
        text, code, source = "", ("culprit_caught" if solved else "no_verdict"), "derived"
    return {"code": code, "text": text, "source": source, "warnings": warnings}


def _weighted(components, weights):
    available = {key: value for key, value in components.items() if value is not None}
    if len(available) < 2:
        return None, round(sum(weights[k] for k in available), 4)
    total = sum(weights[key] for key in available)
    score = sum(weights[key] * value for key, value in available.items()) / total
    return round(score, 4), round(total, 4)


def score_sessions(ctx, people, goals):
    """F27 AI scores and §14.1 player six-dimension scores from stored signals."""
    seats = people["seat_ids"]
    signals = ctx["signals"]
    persona = ctx["persona"]
    social = ctx["social"]
    drama_max = max(1, max((signals.get(s, {}).get("drama", 0) for s in seats), default=0))
    ai, players = {}, {}
    for seat in seats:
        row = signals.get(seat) or {}
        goal = goals["per_seat"].get(seat) or {}
        ai_components = {
            "goal": goal.get("completion"),
            "persona": persona.get(seat),
            "drama": round(min(1.0, row.get("drama", 0) / max(3, drama_max)), 4)
            if row.get("drama") else None,
            "consistency": row.get("consistency") if row.get("speech") else None,
            "violations": row.get("violation_rate") if row.get("speech") else None,
        }
        value, coverage = _weighted(ai_components, AI_WEIGHTS)
        ai[seat] = {
            "name": people["seat_to_name"].get(seat, seat),
            "components": ai_components,
            "score": round(max(0.0, min(1.0, value)) * 100, 1) if value is not None else None,
            "coverage": coverage,
        }

        required = ctx["required_facts"]
        known = ctx["public_facts"]
        coverage_ratio = round(len(required & known) / len(required), 4) if required else None
        reasoning = None
        if ctx["result"].get("culprit") or ctx["result"].get("winner"):
            reasoning = 0.5 * float(bool(ctx["result"].get("solved")))
            if coverage_ratio is not None:
                reasoning += 0.5 * coverage_ratio
        player_components = {
            "reasoning": round(reasoning, 4) if reasoning is not None else None,
            "evidence": round(min(1.0, row.get("clues", 0) / max(1, ctx["clue_total"])), 4)
            if ctx["clue_total"] else None,
            "persona": persona.get(seat),
            "social": round(min(1.0, social.get(seat, 0.0) / 2.0), 4)
            if social.get(seat) is not None else None,
            "hidden_goal": goal.get("completion"),
            "violations": row.get("violation_rate") if row.get("speech") else None,
        }
        value, coverage = _weighted(player_components, PLAYER_WEIGHTS)
        players[seat] = {
            "name": people["seat_to_name"].get(seat, seat),
            "dimensions": player_components,
            "score": round(max(0.0, min(1.0, value)) * 100, 1) if value is not None else None,
            "coverage": coverage,
        }
    return {"ai": ai, "players": players}


def evaluate(conn, session_id):
    """Full deterministic report; never mutates session state."""
    session_doc = db.get_session(conn, session_id)
    onto = db.get_script(conn, session_doc["script_id"])
    result = db.get_game_state(conn, session_id, "result", {}) or {}
    vote_state = db.get_game_state(conn, session_id, "vote_state", {}) or {}
    events = journal.audit_events(conn, session_id)
    people = resolve_people(session_doc, onto)
    revealed = db.get_game_state(conn, session_id, "revealed_clues", []) or []
    public = public_fact_ids(onto, events, revealed)
    required = set((onto.get("evaluation") or {}).get("required_fact_ids") or [])
    coverage = round(len(required & public) / len(required), 4) if required else None
    expected_motive = (onto.get("evaluation") or {}).get("motive_fact_id")
    motive = result.get("motive_fact_id") or expected_motive
    signals = _seat_signals(events, people["seat_ids"],
                            _to_seat((onto.get("evaluation") or {}).get("killer_id"), people))
    persona_rows = conn.execute(
        "SELECT seat_id, anchor_cos FROM drift_ledger WHERE session_id=?", (session_id,),
    ).fetchall()
    ctx = {
        "onto": onto, "result": result, "public_facts": public,
        "required_facts": required, "clue_coverage": coverage or 0.0,
        "clue_total": len(onto.get("clues") or []),
        "motive_correct": bool(motive and expected_motive and motive == expected_motive),
        "culprit_seat": _to_seat((onto.get("evaluation") or {}).get("killer_id"), people),
        "last_votes": _last_ballots(vote_state, people),
        "signals": signals,
        "persona": _persona_from_rows(persona_rows, people["seat_ids"]),
        "social": _relation_influence(conn, session_id, people["char_to_seat"]),
        "goal_completion": None,
        "violation_rate": round(sum(
            v.get("violation_rate", 0.0) for v in signals.values()
        ) / max(1, len(signals)), 4),
    }
    goals = settle_goals(ctx, people)
    completed = [g["completion"] for g in goals["per_seat"].values()
                 if g["completion"] is not None]
    ctx["goal_completion"] = round(sum(completed) / len(completed), 4) if completed else None
    ending = resolve_ending(onto, ctx, people)
    scores = score_sessions(ctx, people, goals)
    return {
        "session_id": session_id,
        "ending": ending, "goals": goals, "scores": scores,
        "basis": {
            "required_fact_ids": sorted(required),
            "public_fact_ids": sorted(public),
            "clue_coverage": coverage,
            "motive_correct": ctx["motive_correct"],
            "goal_scoring": "仅显式 check 判据；纯文本目标不评分",
        },
    }


def settle(conn, session_id):
    """Persist the report: one public ending, one private settlement per seat."""
    report = evaluate(conn, session_id)
    existing = db.get_game_state(conn, session_id, "scoring", {}) or {}
    fingerprint = json.dumps(
        db.get_game_state(conn, session_id, "result", {}) or {},
        sort_keys=True, default=str, ensure_ascii=False,
    )
    if existing.get("_fingerprint") == fingerprint:
        return existing
    report["_fingerprint"] = fingerprint
    session_doc = db.get_session(conn, session_id)
    people = resolve_people(session_doc, db.get_script(conn, session_doc["script_id"]))
    round_no = int(db.get_game_state(conn, session_id, "round", 0) or 0)
    db.set_game_state(conn, session_id, "scoring", report)
    db.append_game_event(
        conn, session_id, round_no, "gm:host", "ending_settled",
        {
            "code": report["ending"]["code"], "text": report["ending"]["text"],
            "source": report["ending"]["source"],
            "players": {
                item["name"]: {
                    "score": item["score"], "coverage": item["coverage"],
                }
                for item in report["scores"]["players"].values()
            },
        }, ["public"],
    )
    for seat, payload in report["goals"]["per_seat"].items():
        db.append_game_event(
            conn, session_id, round_no, "gm:host", "goal_settled",
            {"seat": seat, "goals": payload}, ["gm:host", seat],
        )
    db.append_game_event(
        conn, session_id, round_no, "gm:host", "culprit_strategy",
        report["goals"]["culprit_strategy"], ["gm:host"],
    )
    return report


def render(report):
    """Markdown section for replay/retro; names only, no internal seat ids."""
    ending = report["ending"]
    lines = ["## 11. 结局与个人目标结算", "",
             f"- 结局：{ending['code']}（依据：{ending['source']}）"]
    if ending["text"]:
        lines.append(f"- 结局文本：{ending['text']}")
    for warning in ending["warnings"]:
        lines.append(f"- 条件告警：{warning}")
    strategy = report["goals"]["culprit_strategy"]
    lines.append(
        f"- 真凶行为记录：{strategy['culprit'] or '未登记'}；"
        f"回避 {strategy['deflects']}、指控 {strategy['accusations']}、"
        f"谎言 {strategy['lies']}、栽赃票 {strategy['framing_ballots']}"
    )
    lines.extend(["", "**AI 多目标评分（F27）**", ""])
    for item in report["scores"]["ai"].values():
        parts = "、".join(
            f"{key}={value if value is not None else '—'}"
            for key, value in item["components"].items()
        )
        lines.append(f"- {item['name']}：{item['score'] if item['score'] is not None else '无法判定'}（{parts}）")
    lines.extend(["", "**玩家六维评分（§14.1）**", ""])
    for item in report["scores"]["players"].values():
        parts = "、".join(
            f"{key}={value if value is not None else '—'}"
            for key, value in item["dimensions"].items()
        )
        lines.append(f"- {item['name']}：{item['score'] if item['score'] is not None else '无法判定'}（{parts}）")
    lines.extend(["", "**个人目标判定**", ""])
    for item in report["goals"]["per_seat"].values():
        if not item["goals"]:
            continue
        detail = "；".join(
            f"{goal['text'] or goal['id']}：{goal['status']}"
            for goal in item["goals"]
        )
        lines.append(f"- {item['name']}：{detail}")
    lines.append("> 纯文本目标无机器可判定条件时记为「unscored」，不按措辞猜测完成度。")
    return "\n".join(lines) + "\n"
