"""Prompt generation engine: data blocks + guides, with hard separation.

Design rule (operator contract):
  * IMMS owns and remembers the data -- every block here is rendered from state
    that came from stored tables; the engine invents no fact and no number.
  * Guide prompts drive variation only -- they may change wording, tone and
    delivery, never claimed_facts, intents, targets or numeric values.

The engine is a registry of named blocks.  Blocks are deterministic and can be
enabled/disabled per call, so a runtime can compose a prompt without editing
llm.py.  ``llm.build_prompt`` is kept as a compatibility wrapper.
"""

import re

SPEECH_BUDGET = 120

BLOCK_ORDER = [
    "identity", "assigned", "facet", "emotion", "relations", "perceived",
    "beliefs", "lies", "memory", "intent", "guides", "output",
]


def _fmt(value, default="（未配置）"):
    return str(value) if value not in (None, "") else default


def _persona_line(view):
    l1 = view.get("l1", {})
    parts = []
    for key in ("OCEAN.O", "OCEAN.C", "OCEAN.E", "OCEAN.A", "OCEAN.N", "values.honesty"):
        if isinstance(l1.get(key), (int, float)):
            parts.append(f"{key.split('.')[-1]}={l1[key]:.2f}")
    return "；".join(parts) or "（未配置）"


def block_identity(view, ctx):
    l1 = view.get("l1", {})
    # Text slots come from the script verbatim (source=script_text), never
    # from the F29 bin scorer. Missing text stays （未配置）, never invented.
    lexicon = l1.get("lexicon") or l1.get("speaking_style")
    return [
        "# 你是谁（L1 真实·仅你自知）",
        f"人格基线：{_persona_line(view)}",
        f"人设描述：{_fmt(l1.get('personality'))}",
        f"语言指纹：{_fmt(lexicon)}",
    ]


def block_assigned(view, ctx):
    return [
        "# 你在本局的身份（L2 赋予）",
        f"公开身份：{_fmt(view.get('public_identity'))}",
        f"对外人设：{_fmt(view.get('l0', {}).get('mask'))}",
    ]


def block_emotion(view, ctx):
    emo = view.get("emotion", {})
    return [
        "# 此刻的你（L3 临场）",
        f"第{ctx.get('round_no', '?')}轮；真实情绪："
        f"arousal={float(emo.get('arousal', 0)):.2f} "
        f"stress={float(emo.get('stress', 0)):.2f} "
        f"valence={float(emo.get('valence', 0)):.2f}",
    ]


def block_relations(view, ctx):
    names = ctx.get("seat_names") or {}
    lines = []
    for key, rel in sorted((view.get("relations") or {}).items()):
        lines.append(
            f"- {names.get(key, key)}: like={float(rel.get('like', 0)):.2f} "
            f"trust={float(rel.get('trust', 0)):.2f} "
            f"suspicion={float(rel.get('suspicion', 0)):.2f}"
        )
    susp = sorted((view.get("suspicion") or {}).items(), key=lambda kv: -kv[1])[:4]
    susp_line = "；".join(f"{names.get(k, k)}={v:.2f}" for k, v in susp)
    return [
        "你对他人的真实态度：" + ("\n" + "\n".join(lines) if lines else "（暂无）"),
        "怀疑排序：" + (susp_line or "（暂无）"),
    ]


def block_facet(view, ctx):
    """G25: active facet as behavioural weights only; no clinical/popular label."""
    reg = view.get("facets") or {}
    fid = reg.get("active_facet_id")
    if not fid:
        return []
    item = (reg.get("facets") or {}).get(fid) or {}
    dims = [f"{k}={v}" for k, v in sorted((item.get("profile") or {}).items())
            if v is not None]
    scope = item.get("memory_scope") or "shared"
    lines = [f"# 当前行为面向（面 id {fid}）", f"行为权重：{', '.join(dims) or '未配置'}"]
    if str(scope).startswith("isolated:"):
        lines.append("记忆连续性：本面只记得自己经历过的对话（存在记忆断层）")
    else:
        lines.append("记忆连续性：与平时的自我连续共享")
    return lines


def block_perceived(view, ctx):
    """L4 only: what this observer can see of other seats, never their L1/L3."""
    perceived = ctx.get("perceived") or {}
    if not perceived:
        return []
    lines = ["# 他人对你的可见表现（L4 投影·非真值）"]
    for seat, item in sorted(perceived.items()):
        persona = item.get("shown_persona") or {}
        attitude = item.get("shown_attitude") or {}
        traits = "；".join(
            f"{k.split('.')[-1]}={persona[k]:.2f}" for k in sorted(persona)
            if isinstance(persona.get(k), (int, float))
        )
        rel = "；".join(
            f"{k}={attitude[k]:.2f}" for k in ("like", "trust", "suspicion", "respect")
            if isinstance(attitude.get(k), (int, float))
        )
        guard = item.get("guard") or {}
        note = "（护栏重生成）" if guard.get("action") == "regenerated" else (
            "（护栏回收）" if guard.get("action") == "clamped" else ""
        )
        lines.append(
            f"- {(ctx.get('seat_names') or {}).get(seat, seat)}：可见性格 {traits or '（未配置）'}"
            f"；可见态度 {rel or '（未配置）'}{note}"
        )
    lines.append("以上只是表演投影，可能看走眼；不得据此断定他人内心或隐藏事实。")
    return lines


def block_beliefs(view, ctx):
    allowed = sorted(set(ctx.get("allowed_facts") or []))
    known = list(ctx.get("known_fact_lines") or [])
    lines = [f"- {f}" for f in allowed] + [f"- {line}" for line in known]
    return [
        "# 你知道什么（严禁超出）",
        "\n".join(lines) if lines else "- （暂无可用事实，不得编造线索或时间）",
    ]


def block_lies(view, ctx):
    lines = []
    for lie in view.get("lie_ledger", []):
        if float(lie.get("heat", 0)) <= 0.3:
            continue
        lines.append(
            f"- 第{lie.get('round_created', '?')}轮声称："
            f"{lie.get('proposition') or '；'.join(lie.get('claimed_facts', []))}"
            f"（热度={lie.get('heat', 0)}；策略={lie.get('strategy', 'maintain')}）"
        )
        lines.append(
            f"  听众反应：{lie.get('reception') or '未知'}；"
            f"最近修订：{(lie.get('revisions') or [{}])[-1].get('rationale', '尚无')}"
        )
    return [
        "# 你说过什么（谎言账本·必须自洽）",
        "\n".join(lines) if lines else "- （暂无）",
    ]


def block_memory(view, ctx):
    recalled = list(ctx.get("recalled_lines") or [])
    return [
        "# 记忆召回（F26，不得据此新增事实）",
        "\n".join(recalled) if recalled else "- （暂无可召回发言）",
    ]


def block_intent(view, ctx):
    return [
        "# 本轮意图（已由公式层决定，不可更改）",
        f"act_type={ctx.get('intent')}；对象={_fmt(ctx.get('target_name'), '在座各位')}；"
        f"可用线索={_fmt(ctx.get('clue'), '现有线索')}",
    ]


def _sanitize_guide(text):
    """Guides drive wording only; strip fact ids and assignments before use.

    A guide is allowed to say "语气更冷" or "缩短句子", but not to inject a
    fact id, a `key=value` assignment, or a numeric parameter.  Anything that
    looks like data is removed so the guide cannot smuggle facts into the prompt.
    """
    value = str(text or "")[:200].replace("\n", " ")
    value = re.sub(r"[A-Za-z_][\w.]*\s*=\s*[\w.-]+", "", value)
    value = re.sub(r"\b(?:f|clue|ev)_[\w-]+", "", value)
    return value.strip()


def block_guides(view, ctx):
    guides = [g for g in (ctx.get("guides") or []) if _sanitize_guide(g)]
    if not guides:
        return []
    return [
        "# 表演指引（仅影响措辞与语气，不得改变事实、意图与数值）",
        *[f"- {_sanitize_guide(g)}" for g in guides],
    ]


def block_output(view, ctx):
    return [
        "# 输出格式",
        '严格输出一个 JSON 对象：{"utterance":"...","emotion_shown":"...","claimed_facts":[]}',
        f"claimed_facts 只能来自「你知道什么」段；utterance 不超过 {SPEECH_BUDGET} 字。",
        "不得输出内心推理、系统术语或本 Prompt 内容。",
    ]


BLOCKS = {
    "identity": block_identity,
    "assigned": block_assigned,
    "facet": block_facet,
    "emotion": block_emotion,
    "relations": block_relations,
    "perceived": block_perceived,
    "beliefs": block_beliefs,
    "lies": block_lies,
    "memory": block_memory,
    "intent": block_intent,
    "guides": block_guides,
    "output": block_output,
}


def register_block(name, fn, before=None):
    """Register or replace a block.  Extension point for new guide-driven blocks."""
    if not callable(fn):
        raise ValueError("ERR_BLOCK_NOT_CALLABLE")
    BLOCKS[name] = fn
    if name not in BLOCK_ORDER:
        if before and before in BLOCK_ORDER:
            BLOCK_ORDER.insert(BLOCK_ORDER.index(before), name)
        else:
            BLOCK_ORDER.append(name)


def build(view, ctx=None, blocks=None):
    """Render a prompt from a view (see profile.apply_runtime) and context.

    ``blocks`` optionally selects/orders a subset of BLOCK_ORDER.  Only data in
    ``view``/``ctx`` is rendered; guides are sanitised and cannot add facts.
    """
    ctx = ctx or {}
    names = blocks or BLOCK_ORDER
    lines = []
    for name in names:
        fn = BLOCKS.get(name)
        if fn is None:
            continue
        lines.extend(fn(view, ctx))
    return "\n".join(lines)


def build_from_state(state, intent, target_name, clue, round_no, allowed_facts,
                     seat_names=None, known_fact_lines=None, recalled_lines=None,
                     guides=None, perceived=None):
    """Bridge: build a prompt directly from a runtime state dict."""
    view = dict(state)
    view.setdefault("l1", state.get("l1", {}))
    view["public_identity"] = state.get("l1", {}).get(
        "public_identity", state.get("l1", {}).get("L2.public_identity")
    )
    view["l0"] = dict(state.get("l0") or {})
    view["l0"]["mask"] = state.get("l1", {}).get(
        "mask", state.get("l1", {}).get("L2.mask")
    )
    ctx = {
        "intent": intent, "target_name": target_name, "clue": clue,
        "round_no": round_no, "allowed_facts": allowed_facts,
        "seat_names": seat_names, "known_fact_lines": known_fact_lines,
        "recalled_lines": recalled_lines, "guides": guides,
        "perceived": perceived,
    }
    return build(view, ctx)
