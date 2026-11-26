"""Five-section post-game replay generated from the event log (no retelling by LLM)."""

import json
import re

from . import db, formulas, journal

SECTIONS = ["真相时间线", "角色内心独白", "关键转折点 Top 5", "谎言全景图",
            "反事实推演", "越界时刻 Top N", "说话风格漂移账"]


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
    return "\n".join(lines) + "\n"
