"""GM director's private brief: full-script truth -> per-seat private direction.

The host reads the whole boxed script, so it knows the culprit, the method and
everyone's hidden motive.  This module turns that knowledge into two separate
outputs and keeps them apart:

* :func:`truth_brief` is for the **GM prompt only** -- it lets the host pace
  suspense and choose a focus, but must never be echoed into narration.
* :func:`private_directions` is injected into each seat's own prompt -- goals,
  hidden conflicts, and (for the culprit) conceal/misdirect/framing objectives.

Nothing here mutates L1/L2, widens a fact scope, or changes any formula-owned
number.  It is behavioural wording derived from script text, never a new fact.
"""

import re

_FACT_PREFIX_RE = re.compile(r"^(?:f|clue|ev|t|p|c)_[A-Za-z0-9_-]+\s*[:：]?\s*")
_ASSIGN_RE = re.compile(r"[A-Za-z_][\w.]*\s*=\s*[\w.-]+")
_REVEALED_ID_RE = re.compile(r"^(?:f|clue|ev|t|p|c)_[A-Za-z0-9_-]+")


def revealed_ids(values):
    """Normalise stored revealed clues to bare ids.

    Runtime stores display strings such as ``c_report 急救初步判断记录|logLR:...``
    in ``revealed_clues``.  Comparing those against a script clue id silently
    marks nothing as public, so every consumer must go through this helper.
    """
    out = set()
    for value in values or []:
        if isinstance(value, dict):
            value = value.get("id") or value.get("clue") or value.get("label")
        match = _REVEALED_ID_RE.match(str(value or "").strip())
        if match:
            out.add(match.group())
    return out


def _clean(value, limit=180):
    """Strip fact ids / assignments / quoted wrappers; keep script wording."""
    text = str(value or "").strip()
    text = _FACT_PREFIX_RE.sub("", text)
    text = _ASSIGN_RE.sub("", text)
    text = text.replace("「", "").replace("」", "").replace("『", "").replace("』", "")
    text = text.split("|")[0].strip()
    return text[:limit]


def _chars(onto):
    return {c.get("id"): c for c in (onto or {}).get("characters", [])
            if isinstance(c, dict)}


def _name(char):
    return char.get("name") or char.get("id") or "某人"


def _facts(onto):
    return {f.get("id"): f for f in (onto or {}).get("facts", [])
            if isinstance(f, dict)}


def killer_id(onto):
    """Culprit id from the script's evaluation block; None when absent."""
    evaluation = (onto or {}).get("evaluation") or {}
    kid = evaluation.get("killer_id")
    return kid if kid in _chars(onto) else None


def truth_brief(onto):
    """GM-only description of the whole story.  Never render this publicly."""
    chars, facts = _chars(onto), _facts(onto)
    kid = killer_id(onto)
    out = []
    if not kid:
        return out
    victim = next((c for c in chars.values() if c.get("is_victim")), None)
    victim_name = _name(victim) if victim else "死者"
    out.append(f"- 真凶是 {_name(chars[kid])}；死者是 {victim_name}。")
    for fid in ("t_method", "t_time", "t_access", "t_source", "t_bottle"):
        fact = facts.get(fid)
        if fact and fact.get("visibility") == "private":
            text = _clean(fact.get("content"))
            if text:
                out.append(f"- {text}")
    # Who has a hidden conflict with the victim: the host uses this to place
    # focus and suspense, not to accuse anybody on the record.
    grudges = []
    for cid, char in chars.items():
        if cid == kid or char.get("is_victim"):
            continue
        for fid in char.get("known_fact_ids") or []:
            fact = facts.get(fid)
            if not fact or fact.get("visibility") != "private":
                continue
            if "motive" in fid or "要挟" in str(fact.get("content")):
                grudges.append(f"{_name(char)}（{_clean(fact.get('content'), 60)}）")
                break
    if grudges:
        out.append("- 与死者有私怨的人：" + "、".join(grudges))
    out.append("这些只供你把握节奏和焦点；叙述里一个字都不能透露。")
    return out


def _shared_secret_edges(onto):
    """Characters whose private motives can surface in the same clue."""
    chars, facts = _chars(onto), _facts(onto)
    owner = {}
    for cid, char in chars.items():
        for fid in char.get("known_fact_ids") or []:
            fact = facts.get(fid)
            if fact and fact.get("visibility") == "private" and "motive" in fid:
                owner.setdefault(fid, set()).add(cid)
    edges = {}
    for clue in (onto or {}).get("clues", []):
        if not isinstance(clue, dict):
            continue
        linked = set()
        for fid in clue.get("reveals_fact_ids") or []:
            linked |= owner.get(fid, set())
        for cid in linked:
            for other in linked - {cid}:
                edges.setdefault(cid, set()).add(other)
    return edges


def private_directions(onto):
    """Per-character private direction lines, keyed by script character id."""
    chars = _chars(onto)
    kid = killer_id(onto)
    edges = _shared_secret_edges(onto)
    out = {}

    for cid, char in chars.items():
        if char.get("is_victim"):
            continue
        lines = []
        for goal in char.get("goals") or []:
            text = _clean(goal)
            if text:
                lines.append(f"- 目标：{text}")
        if cid == kid:
            lines.append("- 策略：首要是不被发现；把意外或心衰维持为默认解释，别主动给出手法、时间与入口。")
            lines.append("- 策略：谁提监控或门禁，就把话头引到线上故障与日志校验；答得越像流程越安全。")
            lines.append("- 策略：兜不住时搅浑水，把已有私怨的人推到台前；这是策略，不是你可以凭空声称的事实。")
        for other in sorted(edges.get(cid, set())):
            if other in chars:
                lines.append(
                    f"- 暗线：你和{_name(chars[other])}的把柄可能被同一份材料牵出来；"
                    "彼此都不愿它被摆上桌。")
        if cid == kid:
            for other, other_char in chars.items():
                if other in (cid,) or other_char.get("is_victim"):
                    continue
                has_motive = any("motive" in str(fid)
                                 for fid in other_char.get("known_fact_ids") or [])
                if has_motive:
                    lines.append(
                        f"- 可用挡箭牌：{_name(other_char)}与死者有私怨；必要时把疑点往他身上引"
                        "（这是策略，不是你已核实的事实）。")
        if lines:
            out[cid] = lines
    return out


_SEAT_HEADER = "# 私人目标与暗线（GM 导演·仅你自知，不得原样念出或写进公开发言）"
_GM_HEADER = "# 导演视角（GM 独知；只用来把握节奏与焦点，绝不写进叙述）"


def inject_seat(prompt, lines):
    """Append private direction to a seat prompt; no-op when there is none."""
    lines = [x for x in (lines or []) if x]
    if not lines:
        return prompt
    return "\n".join([str(prompt or ""), "", _SEAT_HEADER, *lines])


def inject_gm(prompt, lines):
    """Append the host-only truth brief to a GM prompt."""
    lines = [x for x in (lines or []) if x]
    if not lines:
        return prompt
    return "\n".join([str(prompt or ""), "", _GM_HEADER, *lines])


def hidden_truth_terms(onto, revealed_clue_ids=()):
    """Distinctive wording from private facts not yet made public.

    Used only by output validation to reject a GM narration that leaks truth.
    Fact ids and numeric parameters are excluded; public wording stays legal.
    """
    facts = _facts(onto)
    revealed = revealed_ids(revealed_clue_ids)
    revealed_facts = set()
    for clue in (onto or {}).get("clues", []):
        if isinstance(clue, dict) and clue.get("id") in revealed:
            revealed_facts.update(clue.get("reveals_fact_ids") or [])
    # A word already in the public record is not a leak, even if a private
    # fact repeats it (e.g. "门禁" also appears in a public fact).
    public_words = set()
    for fact in facts.values():
        if fact.get("visibility") == "public":
            public_words.update(str(k).strip() for k in fact.get("keywords") or [])
    for clue in (onto or {}).get("clues", []):
        if isinstance(clue, dict) and clue.get("id") in revealed:
            public_words.update(re.findall(r"[\u4e00-\u9fff]{2,6}", str(clue.get("description") or "")))
    terms = []
    for fid, fact in facts.items():
        if fid in revealed_facts or fact.get("visibility") != "private":
            continue
        for keyword in fact.get("keywords") or []:
            word = str(keyword).strip()
            if (len(word) >= 2 and not word.isdigit()
                    and not any(word in known for known in public_words)):
                terms.append(word)
    return list(dict.fromkeys(terms))
