"""Five-section post-game replay generated from the event log (no retelling by LLM)."""

import json
import re

from . import db, formulas, gm_debrief, journal, scoring

SECTIONS = ["真相时间线", "角色内心独白", "关键转折点 Top 5", "谎言全景图",
            "反事实推演", "越界时刻 Top N", "说话风格漂移账"]

INTENT_LABELS = {
    "assert": "主张", "ask": "追问", "accuse": "质疑", "defend": "辩解",
    "ally": "声援", "deflect": "回避", "confess": "承认", "provoke": "挑衅",
}
_GM_ACTS = {
    "gm_narration": "场景叙述", "gm_redirect": "跑偏拉回",
    "gm_vote_narration": "投票揭晓", "gm_phase_narration": "阶段串场",
}
_ID_PREFIX_RE = re.compile(r"^(?:c|f|t|p|clue|ev)_[A-Za-z0-9_-]+\s*")


def _clean_reveal(text):
    """Human-readable clue wording: strip fact ids and logLR parameters."""
    value = str(text or "").strip()
    value = _ID_PREFIX_RE.sub("", value)
    return value.split("|")[0].strip()[:80]


def _transcript_section(conn, session_id, session_doc, names, result):
    """Chronological table talk: host line, every seat's line, then the vote.

    This is the readable replay.  The numbered sections that follow are the
    audit ledger (truth, inner state, deltas) -- they are not a substitute
    for knowing who actually said what, in what order.
    """
    lines = ["## 对局实录（按轮）", ""]
    rows = conn.execute(
        "SELECT round, actor, act_type, payload_json FROM game_events "
        "WHERE session_id=? ORDER BY seq", (session_id,),
    ).fetchall()
    rounds = sorted({int(r["round"]) for r in rows if int(r["round"] or 0) >= 1})
    if not rounds:
        return lines + ["（本局没有产生任何公开事件）", ""]

    kinds = {p["id"]: p.get("kind", "") for p in session_doc["participants"]}
    order = {p["id"]: i for i, p in enumerate(session_doc["participants"])
             if p.get("script_character_id")}
    by_round, gm_lines = {}, {}
    ballots, vote_scenes = [], []
    for row in rows:
        payload = json.loads(row["payload_json"] or "{}")
        rnd = int(row["round"] or 0)
        act = row["act_type"]
        if act == "vote_cast":
            ballots.append(payload)
            continue
        if act == "gm_vote_narration":
            vote_scenes.append(payload)
            continue
        if act in _GM_ACTS:
            gm_lines.setdefault(rnd, []).append((act, payload))
            continue
        if act in ("clue_release", "clue_found"):
            if payload.get("visibility") == "hidden":
                continue  # finder-only clue never enters the public transcript
            label = _clean_reveal(payload.get("clue") or payload.get("label")
                                  or payload.get("title"))
            if label:
                by_round.setdefault(rnd, {}).setdefault("_clues", []).append(label)
            continue
        utterance = str(payload.get("utterance") or "").strip()
        if not utterance or row["actor"] not in names:
            continue
        by_round.setdefault(rnd, {}).setdefault(row["actor"], []).append(
            (row["act_type"], payload))

    if gm_lines.get(0) or by_round.get(0):
        lines.append("### 开场 · 读本 · 搜证")
        for act, payload in gm_lines.get(0, []):
            text = str(payload.get("narration") or "").strip()
            if text:
                lines.append(f"**GM · {_GM_ACTS.get(act, act)}**：{text}")
                lines.append("")
        for actor in sorted((by_round.get(0) or {}).keys()):
            if actor == "_clues":
                continue
            who = names.get(actor, actor)
            for act_type, payload in by_round[0][actor]:
                tag = "人类玩家" if str(kinds.get(actor, "")).startswith("human") \
                    else INTENT_LABELS.get(act_type, "发言")
                if payload.get("utterance"):
                    lines.append(f"**{who}（{tag}）**：{payload['utterance']}")
                    lines.append("")
        for clue in by_round.get(0, {}).get("_clues", []):
            lines.append(f"**公开线索**：{clue}")
            lines.append("")

    for rnd in rounds:
        lines.append(f"### 第 {rnd} 轮")
        round_gm = gm_lines.get(rnd, [])
        narrations = [p for a, p in round_gm if a == "gm_narration"]
        redirects = [p for a, p in round_gm if a == "gm_redirect"]
        if narrations:
            main = narrations[0]
            label = "场景叙述" + ("（含拉回）" if main.get("drift") or redirects else "")
            text = str(main.get("narration") or "（回退，未生成叙述）").strip()
            # Old sessions logged the redirect as its own event.  A pull-back
            # is part of the host's narration, so fold it back into that line.
            for extra in redirects:
                wording = str(extra.get("narration") or "").strip()
                if wording:
                    text += "\n\n" + wording
            lines.append(f"**GM · {label}**：{text}")
            lines.append("")
        for act, payload in round_gm:
            if act in ("gm_narration", "gm_redirect"):
                continue
            label = _GM_ACTS.get(act, act)
            text = str(payload.get("narration") or "（回退，未生成叙述）").strip()
            lines.append(f"**GM · {label}**：{text}")
            lines.append("")
        for clue in by_round.get(rnd, {}).get("_clues", []):
            lines.append(f"**公开线索**：{clue}")
            lines.append("")
        seats = [a for a in by_round.get(rnd, {}) if a != "_clues"]
        seats.sort(key=lambda a: order.get(a, len(order)))
        for actor in seats:
            who = names.get(actor, actor)
            for act_type, payload in by_round[rnd][actor]:
                intent = payload.get("intent") or payload.get("act_type") or act_type
                if str(kinds.get(actor, "")).startswith("human"):
                    tag = "人类玩家"
                else:
                    tag = INTENT_LABELS.get(intent, intent or "发言")
                target = names.get(payload.get("target"))
                suffix = f"（{tag}" + (f"；对 {target}" if target else "") + "）"
                lines.append(f"**{who}{suffix}**：{payload['utterance']}")
                lines.append("")
        if rnd not in gm_lines and rnd not in by_round:
            lines.append("（本轮无公开发言）")
            lines.append("")

    lines.append("### 投票")
    if not ballots:
        lines.append("- 本局未进行投票。")
        lines.append("")
    else:
        for ballot_set in ballots:
            lines.append(f"- 第 {ballot_set.get('ballot_no', '?')} 次投票")
            for entry in ballot_set.get("ballots") or []:
                voter = names.get(entry.get("voter"), entry.get("voter", "?"))
                choice_id = entry.get("choice")
                choice = names.get(choice_id, "弃权") if choice_id else "弃权"
                valid = "" if entry.get("valid", True) else "（无效票）"
                lines.append(f"  - {voter} → {choice}{valid}")
        tally = result.get("tally") or {}
        if tally:
            lines.append("- 票数：" + "、".join(
                f"{names.get(k, k)} {v}" for k, v in tally.items()))
        winner = result.get("winner")
        if winner or result.get("tie"):
            tail = "；平票按证据裁定" if result.get("tie") else ""
            lines.append(f"- 结果：{names.get(winner, winner) if winner else '无'}"
                         f"｜破案：{'是' if result.get('solved') else '否'}{tail}")
        for payload in vote_scenes:
            text = str(payload.get("narration") or "").strip()
            if text:
                lines.append("")
                lines.append(f"**GM · 投票揭晓**：{text}")
        lines.append("")
    return lines


def _events(conn, session_id):
    return journal.audit_events(conn, session_id)


def _seat_names(session_doc):
    return {p["id"]: p.get("seat_label", p["id"]) for p in session_doc["participants"]}


def _culprit_id(onto):
    if onto.get("evaluation", {}).get("killer_id"):
        return onto["evaluation"]["killer_id"]
    names = {c["name"]: c["id"] for c in onto.get("characters", [])}
    for fact in onto.get("truth_graph", []):
        for name, cid in names.items():
            if name in fact:
                return cid
    return None


def _truth_section(onto, session_doc, result, names):
    lines = ["## 1. 真相时间线", ""]
    culprit = _culprit_id(onto)
    culprit_label = next((c["name"] for c in onto.get("characters", []) if c["id"] == culprit), culprit)
    lines.append(f"- 真凶（Ontology 推断）：{culprit_label or '未标注'}")
    winner = result.get("winner")
    lines.append(f"- 本局投票：{names.get(winner, winner)}｜破案：{'是' if result.get('solved') else '否'}")
    lines.append("")
    lines.append("**客观发生（Timeline_True）**")
    lines.append("")
    for item in onto.get("timeline_true", []):
        lines.append(f"- {item}")
    if not onto.get("timeline_true"):
        lines.append("- （剧本未提供真实时间线）")
    lines.append("")
    lines.append("**真相事实（TruthGraph）**")
    lines.append("")
    for fact in onto.get("truth_graph", []):
        lines.append(f"- {fact}")
    return lines


def _inner_section(events, names):
    lines = ["## 2. 角色内心独白", ""]
    grouped = {}
    for e in events:
        inner = e["payload"].get("inner_intent")
        if not inner or e["actor"].startswith("gm:"):
            continue
        grouped.setdefault(e["actor"], []).append((e["round"], inner, e["payload"].get("utterance", "")))
    if not grouped:
        lines.append("（本局无内心记录）")
    for actor, items in grouped.items():
        lines.append(f"### {names.get(actor, actor)}")
        lines.append("")
        for round_no, inner, utterance in items:
            lines.append(f"- 第{round_no}轮 · 说：“{utterance}”")
            lines.append(f"  真实意图：{inner}")
        lines.append("")
    return lines


def _turning_section(conn, session_id, events, names, char_names):
    lines = ["## 3. 关键转折点 Top 5", ""]
    changes = []
    for row in conn.execute(
        "SELECT * FROM attribute_events WHERE session_id=? AND layer='L3'", (session_id,)
    ):
        before, after = row["before_json"], row["after_json"]
        if not before:
            continue
        try:
            b, a = json.loads(before), json.loads(after)
            delta = float(a["value"]) - float(b["value"])
        except (ValueError, KeyError, TypeError):
            continue
        changes.append((abs(delta), row["round"], row["character_id"], row["key"], row["target"], b["value"], a["value"], row["reason"]))
    changes.sort(key=lambda x: -x[0])
    for rank, (_, round_no, char, key, target, before, after, reason) in enumerate(changes[:5], 1):
        subject = names.get(target, target) if target else "自身"
        lines.append(f"{rank}. 第{round_no}轮 · {char_names.get(char, char)} 对 {subject} 的 `{key}`：{before} → {after}（{reason}）")
    if not changes:
        lines.append("（本局未记录数值级转折点；以下列出高影响言语行为）")
        high = [e for e in events if e["act_type"] in {"accuse", "contradiction", "lie_exposed", "caught_lying", "betray"}]
        for rank, e in enumerate(high[:5], 1):
            lines.append(f"{rank}. 第{e['round']}轮 · {e['actor']} · {e['act_type']}：{e['payload'].get('utterance', '')}")
        if not high:
            lines.append("- （无）")
    return lines


def _lie_section(events, runtime):
    lines = ["## 4. 谎言全景图", ""]
    ledger = []
    for state in runtime.values():
        for lie in state.get("lie_ledger", []):
            ledger.append((state.get("seat_id", "?"), lie))
    if ledger:
        for seat, lie in ledger:
            lines.append(
                f"- {seat} 第{lie.get('round_created', '?')}轮对 {','.join(lie.get('audience', []))} "
                f"声称「{lie.get('proposition') or lie.get('claimed_facts', [])}」"
                f"（真实={lie.get('true_value', False)}；策略={lie.get('strategy', 'maintain')}，热度={lie.get('heat', 0)}）"
            )
            lines.append(f"  - 听众反应：{lie.get('reception') or '未知'}")
            for revision in lie.get("revisions") or []:
                lines.append(
                    f"  - 第{revision.get('round', '?')}轮 · {revision.get('status', '?')} · "
                    f"{revision.get('rationale', '')}"
                )
    else:
        lines.append("- 谎言账本为空（本局未产生需持续维护的谎言）。")
    challenges = [e for e in events if e["act_type"] in {"lie_exposed", "caught_lying", "contradiction"}]
    lines.append(f"- 被当场质疑/戳穿事件：{len(challenges)} 次")
    for e in challenges:
        lines.append(f"  - 第{e['round']}轮 · {e['actor']} · {e['act_type']}：{e['payload'].get('utterance', e['payload'].get('type', ''))}")
    rewrites = [e for e in events if e["act_type"] == "llm_fallback"]
    if rewrites:
        lines.append(f"- 台词层回退次数：{len(rewrites)}（不影响数值与剧情）")
    return lines


def _counterfactual_section(events, runtime, names):
    lines = ["## 5. 反事实推演", ""]
    seats = list(runtime)
    found = 0
    for e in events:
        if e["act_type"] != "clue_release":
            continue
        clue = e["payload"].get("clue", "")
        match = re.search(r"logLR:(\w+)=([-\d.]+)", clue)
        if not match:
            continue
        raw_target, lr = match.group(1), float(match.group(2))
        target = next((s for s in seats if raw_target in s), f"ai:{raw_target}")
        effect = 0.8 * lr
        changed = []
        focus = None
        for seat, state in runtime.items():
            ell = {s: state["ell"].get(s, 0.0) for s in seats}
            before = formulas.vote_distribution(ell, 1.0)
            s_before = formulas.sigmoid(ell.get(target, 0.0))
            ell[target] -= effect
            after = formulas.vote_distribution(ell, 1.0)
            s_after = formulas.sigmoid(ell[target])
            if seat != target and focus is None:
                focus = (s_before, s_after)
            b_top, a_top = max(before, key=before.get), max(after, key=after.get)
            if b_top != a_top:
                changed.append(f"{names.get(seat, seat)} 的指认：{names.get(b_top, b_top)} → {names.get(a_top, a_top)}")
        suspicion_note = (
            f"旁观者对被指向者的怀疑度 S={focus[0]:.2f} → {focus[1]:.2f}"
            if focus else "无对应角色视角"
        )
        lines.append(
            f"- 若第{e['round']}轮不释放「{clue.split('|')[0]}」（logLR={lr} → {names.get(target, target)}）："
            + suspicion_note + "；" + ("；".join(changed) if changed else "全场指认众数不变")
        )
        found += 1
    if not found:
        lines.append("（无可解析 logLR 的线索释放事件）")
    lines.append("")
    lines.append("> 推演仅重算怀疑度与投票分布，不重放器物搜证与私聊，故为近似反事实。")
    return lines


def _breakout_section(events, names):
    """G22: breakout moments are audited in their own replay section."""
    lines = ["## 6. 越界时刻 Top N", ""]
    moments = []
    for event in events:
        payload = event.get("payload") or {}
        if event.get("act_type") == "improv_burst" and payload.get("state") == "frenzy":
            moments.append({
                "round": event.get("round"), "seat": event.get("actor"),
                "tier": payload.get("tier"), "epsilon": payload.get("epsilon"),
                "intensity": (payload.get("trigger") or {}).get("intensity"),
                "aftermath": (payload.get("aftermath") or {}).get("strategy"),
                "s_hi": (payload.get("state_thresholds") or {}).get("s_hi"),
                "kind": "frenzy_burst",
            })
        elif event.get("act_type") == "breakout_state" and payload.get("to") == "frenzy":
            moments.append({
                "round": event.get("round"), "seat": event.get("actor"),
                "tier": "-", "epsilon": "-", "intensity": payload.get("intensity"),
                "aftermath": "-", "s_hi": payload.get("s_hi"), "kind": "entry",
            })
    moments.sort(key=lambda m: (m["round"] or 0, m["seat"] or ""))
    if not moments:
        lines.append("- （本局未进入越界态）")
        return lines
    for rank, item in enumerate(moments[:5], 1):
        lines.append(
            f"{rank}. 第{item['round']}轮 · {names.get(item['seat'], item['seat'])} · "
            f"{item['kind']}：强度={item['intensity']}，档位={item['tier']}，"
            f"ε={item['epsilon']}，带通上界={item['s_hi']}，余波={item['aftermath']}"
        )
    lines.append("")
    lines.append("> 越界态记忆作用域为 full（同一人、记忆连续）；IR1/IR2/R4/R5 与真相写入未放宽。")
    return lines


def _style_section(conn, session_id, names):
    """Speech-style drift replay: every modulation must be explainable."""
    lines = ["## 7. 说话风格漂移账", ""]
    try:
        rows = db.get_drift_ledger(conn, session_id)
    except (AttributeError, TypeError):
        rows = []
    if not rows:
        lines.append("- （本局无风格漂移账本记录）")
        return lines
    lines.append("> 风格锚点逐字复用；漂移为 5 类受控信号，账本逐轮可回溯。")
    lines.append("")
    for row in rows[:12]:
        triggers = []
        try:
            triggers = json.loads(row.get("triggers_json") or "[]")
        except (TypeError, ValueError):
            pass
        lines.append(
            f"- 第{row['round']}轮 · {names.get(row['seat_id'], row['seat_id'])} → "
            f"{names.get(row['target'], row['target']) or '在座各位'}："
            f"总漂移={row['drift_total']:.3f}"
            f"（情绪 {row['emotion']:.3f} / 关系 {row['relation']:.3f} / "
            f"面具 {row['mask']:.3f} / 应激 {row['stress']:.3f} / 噪声 {row['noise']:.3f}），"
            f"余弦代理={row['anchor_cos']}，结构比={row['structure_ratio']}，"
            f"结果={row['outcome']}"
            + ("，强制回落基线" if row["forced_baseline"] else "")
        )
        if triggers:
            lines.append(f"    - 触发：{'；'.join(str(t) for t in triggers)}")
    if len(rows) > 12:
        lines.append(f"- （其余 {len(rows) - 12} 条见 `imms style ledger`）")
    lines.append("")
    lines.append("> 余弦为字符 bigram 规则代理，非语义 embedding；漂移只改措辞，不改事实与谎言账本。")
    return lines


def _style_compile_section(conn, session_id, names):
    rows = db.get_style_compile_ledger(conn, session_id)
    lines = ["## 8. 说话风格 LLM 编译"]
    if not rows:
        lines.append("- 本局未启用 LLM 风格编译（离线模板 provider 路径）。")
        return lines
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    lines.append("- 状态：" + "，".join(f"{k}={v}" for k, v in sorted(counts.items())))
    for row in rows[:12]:
        lines.append(
            f"- 第{row['round']}轮 · {names.get(row['seat_id'], row['seat_id'])}："
            f"{row['status']}" + (f"（{row['reason']}）" if row["reason"] else "")
        )
    if len(rows) > 12:
        lines.append(f"- （其余 {len(rows) - 12} 条见 `imms style compiled`）")
    lines.append("")
    lines.append("> 编译器只改措辞；数值、事实与谎言账本不在其输出范围，校验失败即回退确定性 prompt。")
    return lines


def _gm_narration_section(conn, session_id, names):
    """GM narration ledger: what the host said, and why (narrative/vote/drift)."""
    lines = ["## 9. GM 叙事与场面把控", ""]
    rows = conn.execute(
        "SELECT round, act_type, payload_json FROM game_events "
        "WHERE session_id=? AND act_type IN ('gm_narration','gm_redirect','gm_vote_narration') "
        "ORDER BY seq", (session_id,),
    ).fetchall()
    labels = {
        "gm_narration": "场景叙述", "gm_redirect": "跑偏拉回", "gm_vote_narration": "投票揭晓",
    }
    if not rows:
        return lines + ["（未启用 GM 叙事通道）", ""]
    for row in rows:
        payload = json.loads(row["payload_json"] or "{}")
        status = payload.get("status") or "—"
        text = payload.get("narration") or "（回退）"
        lines.append(f"### 第 {row['round']} 轮 · {labels.get(row['act_type'], row['act_type'])} · {status}")
        lines.append(text)
        if row["act_type"] == "gm_redirect" and payload.get("drift"):
            lines.append("- 跑偏信号：" + "、".join(payload["drift"]))
        if payload.get("tone"):
            lines.append(f"- 氛围：{payload['tone']}")
        lines.append("")
    return lines


def _continuity_section(conn, session_id, names):
    """Audit section for stage gates, resource spends and timeline conflicts."""
    lines = ["## 10. 时序门控与供述自洽", ""]
    actions = {
        "stage_gate": "时序门控",
        "stage_advance": "阶段推进",
        "private_window": "私聊窗口",
        "challenge_token": "质询 token",
        "action_point": "行动点",
        "statement_conflict": "供述冲突",
        "timeline_audit": "双轨时间线",
    }
    rows = conn.execute(
        "SELECT round, actor, act_type, payload_json FROM game_events "
        "WHERE session_id=? AND act_type IN ("
        "'stage_gate','stage_advance','private_window','challenge_token',"
        "'action_point','statement_conflict','timeline_audit') ORDER BY seq",
        (session_id,),
    ).fetchall()
    if not rows:
        return lines + ["（本局未记录时序门控或审计事件）", ""]
    for row in rows:
        payload = json.loads(row["payload_json"] or "{}")
        actor = names.get(row["actor"], row["actor"])
        label = actions[row["act_type"]]
        if row["act_type"] == "statement_conflict":
            detail = f"{payload.get('detail', '')}（强度 {payload.get('strength', 0)}）"
        elif row["act_type"] == "timeline_audit":
            gaps = payload.get("gaps") or {}
            detail = "；".join(
                f"{names.get(cid, cid)} 空洞 {v.get('gap', 0)}"
                for cid, v in gaps.items()
            ) or "无角色空洞数据"
            if payload.get("inversions"):
                detail += "；倒挂 " + str(len(payload["inversions"]))
        elif row["act_type"] == "stage_gate":
            detail = "提前披露被拦下：" + "、".join(
                h.get("fact_id", "") for h in payload.get("hits", [])
            )
        else:
            detail = "；".join(
                f"{k}={v}" for k, v in payload.items()
                if k in {"from", "to", "after", "action", "target", "resource", "card_id"}
            )
        lines.append(f"- 第 {row['round']} 轮 · {label} · {actor}：{detail}")
    lines.append("")
    return lines


def _settlement_section(conn, session_id):
    report = db.get_game_state(conn, session_id, "scoring", {}) or scoring.evaluate(conn, session_id)
    rendered = scoring.render(report)
    return rendered.rstrip().splitlines()


def build_replay(conn, session_id):
    session_doc = db.get_session(conn, session_id)
    onto = db.get_script(conn, session_doc["script_id"])
    result = db.get_game_state(conn, session_id, "result", {})
    runtime = db.get_game_state(conn, session_id, "runtime", {})
    names = _seat_names(session_doc)
    char_names = {
        p.get("script_character_id"): p.get("seat_label")
        for p in session_doc["participants"] if p.get("script_character_id")
    }
    for seat, state in runtime.items():
        state["seat_id"] = seat
    events = _events(conn, session_id)
    lines = [
        f"# 剧本杀复盘：{onto.get('title', session_doc['script_id'])}",
        f"会话：{session_id}",
        "",
    ]
    lines += _transcript_section(conn, session_id, session_doc, names, result)
    lines.append("")
    lines += gm_debrief.debrief(conn, session_id, onto, result, names)
    lines.append("> 以下为审计与指标明细（真相、内心、数值、风格账本）。")
    lines.append("")
    lines += _truth_section(onto, session_doc, result, names)
    lines.append("")
    lines += _inner_section(events, names)
    lines.append("")
    lines += _turning_section(conn, session_id, events, names, char_names)
    lines.append("")
    lines += _lie_section(events, runtime)
    lines.append("")
    lines += _counterfactual_section(events, runtime, names)
    lines.append("")
    lines += _breakout_section(events, names)
    lines.append("")
    lines += _style_section(conn, session_id, names)
    lines.append("")
    lines += _style_compile_section(conn, session_id, names)
    lines.append("")
    lines += _gm_narration_section(conn, session_id, names)
    lines.append("")
    lines += _continuity_section(conn, session_id, names)
    lines.append("")
    lines += _settlement_section(conn, session_id)
    return "\n".join(lines) + "\n"
