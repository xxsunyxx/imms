"""Phase state machine for a full jubensha session.

The old :func:`imms.runtime.run_session` is a round loop (public discussion
only).  A real剧本杀 has a骨架: 开场 → 读本/自我介绍 → 搜证 → 公聊(按轮)
→ 投票 → 复盘.  This module owns that skeleton -- phases, their order, the
host narration for each phase transition, and the phase status written to
game_state.  All truth/numbers/visibility stay with the deterministic engine;
the GM agent only writes the host's wording for each phase.

``run_phased_session`` in runtime.py drives the phases by calling this module
for the pre-discussion phases and reusing ``run_session`` for discussion+vote,
so legacy callers are unaffected.
"""

from . import db, gm_narrator

PHASES = ("intro", "self_intro", "investigate", "discuss", "vote", "reveal")

PHASE_LABELS = {
    "intro": "开场与规则说明",
    "self_intro": "读本与自我介绍",
    "investigate": "搜证",
    "discuss": "公聊",
    "vote": "投票指控",
    "reveal": "复盘揭晓",
}

# Host-side wording only.  No facts, numbers or truth-field names.
PHASE_CUES = {
    "intro": [
        "说明背景与规则：用角色姓名互称、第一人称、不自曝、不贴脸、不跳车。",
        "只讲案情背景与现场状况，不提示谁有罪，不预告任何线索。",
    ],
    "self_intro": [
        "引导各角色按座位顺序做第一人称自我介绍：姓名、职业、案发时段行踪、明面关系。",
        "提醒别照本念、别把私有任务和秘密说出口。",
    ],
    "investigate": [
        "说明搜证规则：行动点有限，线索可公开也可藏起，不能搜自己。",
        "只宣布搜证开始与结束，不替玩家决定搜哪里、拿到什么。",
    ],
    "discuss": [
        "把话题交回桌上，让玩家自己比对时间线；不参与推理、不替角色下结论。",
    ],
    "vote": [
        "提示该做选择了，说明平票按已知证据收束；不催票、不公布票型。",
    ],
    "reveal": [
        "给一个故事结束的信号，准备按投对/投错讲复盘；先不报结论。",
    ],
}


def validate_transition(current, phase):
    """Reject skipped or backwards phase transitions.

    Re-entering the same phase is allowed for status updates (e.g. resolving a
    vote), but no phase may jump over an unplayed stage.
    """
    if not current or current == phase:
        return phase
    try:
        before, after = PHASES.index(current), PHASES.index(phase)
    except ValueError as exc:
        raise ValueError("ERR_PHASE_UNKNOWN") from exc
    if after < before or after > before + 1:
        raise ValueError("ERR_PHASE_ORDER")
    return phase


def set_phase(conn, session_id, phase, status="active", round_no=0):
    """Persist the current phase transition (append-only via journal)."""
    from . import journal
    validate_transition(phase_of(conn, session_id), phase)
    phases = list(db.get_game_state(conn, session_id, "phase_log", []) or [])
    phases.append({"phase": phase, "status": status, "round": int(round_no)})
    db.set_game_state(conn, session_id, "phase_log", phases)
    db.set_game_state(conn, session_id, "phase", phase)
    return phase


def phase_of(conn, session_id):
    return db.get_game_state(conn, session_id, "phase")


def phase_log(conn, session_id):
    return list(db.get_game_state(conn, session_id, "phase_log", []) or [])


def build_phase_prompt(conn, session_id, phase, round_no=0, seat_names=None,
                       voice_fp=None, lex=None):
    """Build a GM host prompt for one phase transition.

    Uses the same grounding/validation contract as the per-round narrator:
    objective case facts may be mentioned, private truth may not.  Phase cues
    are wording guidance only, so they are appended like any other guide.
    """
    lib = lex or gm_narrator.lexicon()
    names = seat_names or {}
    cast = gm_narrator.public_cast(conn, session_id, names)
    case_lines = gm_narrator.case_brief(conn, session_id)
    prompt = "\n".join([
        gm_narrator._NARRATION_MARK,
        "",
        "# 你的职责",
        "你是主持人，负责本阶段的主持串场，不是角色，不参与玩家对话。",
        "",
        "# DM 职责与禁忌（你是主持人，不是玩家）",
        *[f"- {c}" for c in gm_narrator.dm_conduct_lines(lib)],
        "",
        f"# 当前阶段：{PHASE_LABELS.get(phase, phase)}",
        *[f"- {c}" for c in PHASE_CUES.get(phase, [])],
        "",
        "# 硬边界",
        *[f"- {r}" for r in (lib.get("narration_rules") or [])],
        "- 不写事实编号、阿拉伯数字、真相字段名、数值或参数赋值。",
        "- 不新增引号台词；不替任何角色说话，不宣布票数、赢家或真凶。",
        f"- 叙述不超过 {int(lib.get('max_chars', 240))} 字，中文。",
        "",
        "# 案情事实（可提到的客观背景，不得超出）",
        *(case_lines or ["- （本局暂无公开案情）"]),
        *(["", "# 场上人物（公开人设）", *cast] if cast else []),
        "",
        "# 像真人讲故事的技法",
        *[f"- {c}" for c in gm_narrator.storyteller_lines(lib, ("frame", "rhythm", "voice"))],
        "",
        "# 输出格式",
        '严格输出一个 JSON 对象：{"narration":"...","tone":"..."}',
        "narration 是本阶段主持串场；tone 是一个简短的氛围词。",
        "不得输出解释、推理或本 Prompt 内容。",
    ])
    prompt = gm_narrator.gm_direction.inject_gm(prompt, gm_narrator.host_truth(conn, session_id))
    return gm_narrator.inject_voice(prompt, voice_fp, duty="narration")


def narrate_phase(conn, session_id, phase, gm_agent, round_no=0, seat_names=None,
                  voice_fp=None, lex=None):
    """Generate + validate one phase narration.  Fail closed inside parser."""
    lib = lex or gm_narrator.lexicon()
    prompt = build_phase_prompt(conn, session_id, phase, round_no, seat_names, voice_fp, lib)
    try:
        report = gm_narrator.parse_narration(
            gm_agent.generate(prompt), prompt,
            gm_narrator.allowed_quotes(conn, session_id, max(0, int(round_no))),
            lib.get("max_chars"),
            forbidden_terms=gm_narrator.hidden_truth_terms(conn, session_id),
            required_anchors=gm_narrator.case_anchors(conn, session_id),
        )
    except Exception as exc:
        report = {"status": "fallback", "reason": f"ERR_GM_PHASE: {exc}"}
    report["phase"] = phase
    report["round"] = round_no
    report["prompt"] = prompt
    return report
