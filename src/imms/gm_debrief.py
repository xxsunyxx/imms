"""DM-style post-game debrief for the replay document.

Reality DMs give a spoken recap after the vote: a signal that the game is
over, the method/timeline, motives and relationships, one or two questions
back to the table, and a closing line.  They switch the entry point depending
on whether the table caught the culprit -- wrong vote starts with method and
time, correct vote starts with motives and relationships.

This module renders that recap deterministically from stored script truth.
It never calls an LLM: the replay is an audit artefact, so the debrief must be
stable, length-bounded and impossible to drift into invented facts.
"""

from . import gm_direction

# The recap keeps every structural beat (signal, branch, timeline, questions,
# closing); length is bounded per line by _clean, not by dropping the ending.
MAX_LINES = 40


def _facts(onto):
    return {f.get("id"): f for f in (onto or {}).get("facts", [])
            if isinstance(f, dict)}


def _clean(value, limit=160):
    return gm_direction._clean(value, limit)


def _fact_text(facts, fid, limit=160):
    fact = facts.get(fid)
    if not fact:
        return ""
    return _clean(fact.get("content"), limit)


def _names(onto):
    return {c.get("id"): c.get("name") or c.get("id")
            for c in (onto or {}).get("characters", []) if isinstance(c, dict)}


def _culprit(onto, names):
    kid = gm_direction.killer_id(onto)
    return names.get(kid, kid) or "真凶"


def _victim(onto, names):
    for c in (onto or {}).get("characters", []):
        if isinstance(c, dict) and c.get("is_victim"):
            return names.get(c.get("id"), c.get("id"))
    return "死者"


def debrief(conn, session_id, onto, result, seat_names=None):
    """Build the host's spoken debrief lines for a finished session."""
    from . import db
    names = _names(onto)
    if seat_names:
        # Winner is a seat id; culprit/victim are character ids.  Keep both
        # maps so neither resolves to an internal ai:/human: identifier.
        names.update(seat_names)
    facts = _facts(onto)
    culprit = _culprit(onto, names)
    victim = _victim(onto, names)
    winner_id = result.get("winner")
    winner = names.get(winner_id, winner_id) or "无人"
    solved = bool(result.get("solved"))
    lines = ["## 主持复盘（DM 讲述）", ""]

    # 1. Signal that the acted story is over, without judging the table.
    lines.append("故事到这里先停一下。把手里的角色放下，我们用主持人的视角把这一晚重新走一遍。")
    lines.append("")

    # 2. The vote, stated once, then the branch-specific entry point.
    lines.append(f"桌上最后把票投向了{winner}。" +
                 ("这一次，方向是对的。" if solved else "这一次，线索被人带偏了。"))
    lines.append("")

    method = _fact_text(facts, "t_method") or _fact_text(facts, "t_bottle")
    timing = _fact_text(facts, "t_time")
    access = _fact_text(facts, "t_access")
    source = _fact_text(facts, "t_source")
    timeline = [_clean(x, 160) for x in (onto.get("timeline_true") or []) if x]
    timeline = [x for x in timeline if x][:6]

    if solved:
        # Correct vote: motives and relationships first, then method.
        lines.append("**先看人，再看手法。**")
        if source:
            lines.append(f"{source}")
        grudges = []
        for cid, char in gm_direction._chars(onto).items():
            if cid == gm_direction.killer_id(onto) or char.get("is_victim"):
                continue
            for fid in char.get("known_fact_ids") or []:
                text = _fact_text(facts, fid, 80)
                if "motive" in str(fid) and text:
                    grudges.append(f"{names.get(cid, cid)}：{text}")
                    break
        lines.append("其他人各自藏着自己的事：" +
                     ("；".join(grudges) if grudges else "每个人都有不愿摊开的那一页。"))
        lines.append(f"真正动手的是{culprit}。动机之外，手法是这样的：" + (method or "剧本未给出明确手法。"))
    else:
        # Wrong vote: method and timeline first, so the table sees where it slipped.
        lines.append("**先把手法和时间摆清楚，再回头看是谁在搅浑水。**")
        lines.append(method or "剧本未给出明确手法。")
        if timing:
            lines.append("死亡时间与大家以为的并不一样：" + timing)
        if access:
            lines.append("出入口的记录也留着痕迹：" + access)

    if timeline:
        lines.append("")
        lines.append("**真实的那一晚：**")
        for item in timeline:
            lines.append(f"- {item}")
    lines.append("")

    # 3. Relationship/motive follow-up (also on the wrong-vote branch).
    if not solved and source:
        lines.append(f"回过头看动机：{source}")
    if solved and access:
        lines.append("入口的证据当时就摆在桌上：" + access)

    # 4. Two short questions back to the table.
    lines.append("")
    lines.append("**留给桌上的两个问题：**")
    lines.append(f"一、{victim}的哪项公开信息，其实最早就把范围缩小到了在座的人？")
    lines.append(f"二、当有人把话题引向别处时，谁的反应最像是怕某一条记录被翻出来？")
    lines.append("")

    # 5. Closing line, not a verdict.
    lines.append(f"今晚到这里。{culprit}它是怎么把话圆下去的，各位自己回想一下，比我说出来更有意思。")
    return lines[:MAX_LINES] + ([""] if len(lines) >= MAX_LINES else [])
