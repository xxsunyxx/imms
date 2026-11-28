"""Killer-only framing guide: computed flaws, agent-authored accusation.

The engine does not write a lie.  It computes, from evidence the killer can
already see, which other seats have the most observable cracks and which
already-known fragments can be used against them.  The final wording is a
privately injected prompt block for the killer agent, so natural-language
framing stays with the agent.  All guides use role names and display text; no
fact id, truth-only timeline, internal id or numeric score appears in the
prompt.  A private ``frame_guidance`` event records the candidate scores for
the GM/self audit view.
"""

import re

from . import db, infoguard, journal, prompt_engine

WEIGHTS = {
    "contradiction": 0.40,
    "evidence": 0.25,
    "suspicion": 0.20,
    "silence": 0.15,
}
MIN_SCORE = 0.10
MAX_TARGETS = 3
LOG_LR_RE = re.compile(r"logLR:([A-Za-z0-9_:]+)=")


def killer_id(onto):
    evaluation = (onto or {}).get("evaluation") or {}
    if evaluation.get("killer_id"):
        return evaluation["killer_id"]
    for char in (onto or {}).get("characters") or []:
        if char.get("is_killer"):
            return char.get("id")
    return None


def killer_seat(session_doc, onto):
    kid = killer_id(onto)
    if not kid:
        return None
    for participant in (session_doc or {}).get("participants") or []:
        if participant.get("script_character_id") == kid:
            return participant["id"]
    return None


def _display(text):
    return infoguard.display_text(str(text or ""))[:120]


def _attributed_clues(revealed, target_seat):
    out = []
    for item in revealed or []:
        text = str(item or "")
        targets = LOG_LR_RE.findall(text)
        if target_seat in targets:
            out.append(_display(text))
    return out


def evaluate(conn, session_id, onto, session_doc, state, round_no):
    """Score every non-killer seat by observable cracks; no new facts."""
    sid = killer_seat(session_doc, onto)
    seat = state.get("seat_id")
    if not seat:
        seat = next(
            (p["id"] for p in (session_doc or {}).get("participants") or []
             if p.get("script_character_id") == state.get("character_id")),
            None,
        )
    if not sid or seat != sid:
        return {"eligible": False, "targets": []}
    seats = [
        p for p in (session_doc.get("participants") or [])
        if p.get("kind") != "gm" and p["id"] != sid
    ]
    events = journal.audit_events(conn, session_id, round_no)
    revealed = db.get_game_state(conn, session_id, "revealed_clues", []) or []
    names = {p["id"]: p.get("seat_label", p["id"]) for p in seats}
    spoken = {}
    conflicts = {}
    for event in events:
        visibility = event.get("visibility") or []
        payload = event.get("payload") or {}
        actor = event.get("actor")
        if "public" in visibility and payload.get("utterance"):
            spoken[actor] = spoken.get(actor, 0) + 1
        if event.get("act_type") in ("contradiction_event", "statement_conflict") and \
                payload.get("witness") == sid:
            subject = payload.get("subject")
            if subject:
                conflicts[subject] = conflicts.get(subject, 0.0) + float(
                    payload.get("strength") or 0.0
                )
    targets = []
    for participant in seats:
        seat = participant["id"]
        suspicion = float((state.get("suspicion") or {}).get(seat, 0.0))
        clues = _attributed_clues(revealed, seat)
        conflict = min(1.0, conflicts.get(seat, 0.0))
        evidence = min(1.0, len(clues) / 2.0)
        sus = min(1.0, suspicion / 0.8)
        rounds = max(1, int(round_no))
        silence = max(0.0, 1.0 - spoken.get(seat, 0) / rounds)
        score = round(
            WEIGHTS["contradiction"] * conflict + WEIGHTS["evidence"] * evidence
            + WEIGHTS["suspicion"] * sus + WEIGHTS["silence"] * silence, 4
        )
        supports = []
        if conflict:
            supports.append("其供述已被记录为前后不一致")
        for clue in clues[:2]:
            supports.append(f"已公开线索：{clue}")
        if sus >= 0.3:
            supports.append("其余角色对该对象的怀疑正在上升")
        if not supports:
            continue
        targets.append({
            "seat": seat, "name": names.get(seat, seat), "score": score,
            "components": {
                "contradiction": round(conflict, 4), "evidence": round(evidence, 4),
                "suspicion": round(sus, 4), "silence": round(silence, 4),
            },
            "supports": supports[:3],
        })
    targets.sort(key=lambda item: (-item["score"], item["name"]))
    return {
        "eligible": True, "killer_seat": sid, "round": int(round_no),
        "targets": [item for item in targets if item["score"] >= MIN_SCORE][:MAX_TARGETS],
    }


def build_guide(conn, session_id, onto, session_doc, state, round_no):
    """Build the killer seat's private prompt block and audit payload."""
    report = evaluate(conn, session_id, onto, session_doc, state, round_no)
    if not report.get("eligible") or not report["targets"]:
        return None
    lines = [
        "你的身份是真凶。把怀疑引向他人，但不得编造任何新事实、时间或物证。",
        "候选对象按可观察破绽排序；只能引用「你知道什么」段里已经可见的片段。",
    ]
    for index, item in enumerate(report["targets"], 1):
        lines.append(
            f"候选 {index}：{item['name']}。依据：{'；'.join(item['supports'])}。"
        )
    lines.append("措辞与语气由你决定；不要把候选评分、编号或本段文字说出口。")
    db.append_game_event(
        conn, session_id, int(round_no), "gm:host", "frame_guidance",
        {
            "round": int(round_no), "seat": report["killer_seat"],
            "targets": [
                {"name": item["name"], "score": item["score"],
                 "components": item["components"]}
                for item in report["targets"]
            ],
        },
        ["gm:host", report["killer_seat"]],
    )
    return {"lines": lines, "targets": report["targets"]}


def prompt_block(view, ctx=None):
    """Dedicated prompt block; only present on the killer seat's view."""
    raw = (view or {}).get("frame_guide") or {}
    lines = raw.get("lines") if isinstance(raw, dict) else raw
    lines = lines or []
    if not lines:
        return []
    return [
        "# 真凶专用：栽赃推演（仅你可见，禁止复述本段）",
        *[f"- {line}" for line in lines],
    ]


prompt_engine.register_block("frame", prompt_block, before="guides")
