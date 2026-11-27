"""GM narrator: the language half of the host agent.

The GM decision core (:mod:`imms.gm`) owns truth, clue release, pacing and
branch control -- all deterministic.  This module owns only *how the host
tells it*: it turns already-decided state into (a) stage guides fed into each
seat's prompt before they speak, and (b) a narration prompt that asks the GM
agent to weave the round's public events into a vivid scene.

Hard contract (same golden split as the rest of IMMS):

* numbers, facts, intents, targets and truth stay with the formula/engine;
* the narrator may only change wording, atmosphere and pacing of language;
* every generated line is validated -- fact ids, key=value assignments,
  numeric parameters, truth-field names and invented quoted dialogue are
  rejected, and the call falls back to a deterministic scene sketch.

Stage guides are deterministic lookups (zero LLM).  Narration is the only
place a GM agent is actually asked to produce prose.
"""

import json
import re
from pathlib import Path

from . import gm_direction

DEFAULT_LEXICON = Path(__file__).resolve().parents[2] / "config" / "gm_narration_lexicon.json"

_FACT_RE = re.compile(r"\b(?:f|clue|ev|t|p)_[A-Za-z0-9_-]+")
_ASSIGN_RE = re.compile(r"[A-Za-z_][\w.]*\s*=\s*[\w.-]+")
_NUM_RE = re.compile(r"\d")
_TRUTH_RE = re.compile(r"is_true|culprit|真凶|真相|truth_graph|Timeline_True", re.I)
_QUOTE_RE = re.compile(r"[「『\"]([^」』\"]{1,120})[」』\"]")
_NARRATION_MARK = "你是 IMMS 的 GM 主持人"


def lexicon(path=None):
    """Load the narration lexicon; never raises on a malformed file."""
    target = Path(path) if path else DEFAULT_LEXICON
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _band(value, bands):
    """Pick a band key by ascending threshold (bands: {key: min_value})."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    chosen = None
    for key in sorted(bands, key=lambda k: float(bands[k])):
        if number >= float(bands[key]):
            chosen = key
    return chosen


def _clean_public_text(value, limit=120):
    """Keep script-text wording only: no digits, quotes, fact ids or assignments."""
    text = str(value or "")
    text = re.sub(r"[「『\"'”‘]+|[」』\"'”’]+", "", text)
    text = _ASSIGN_RE.sub("", text)
    if _FACT_RE.search(text) or _NUM_RE.search(text) or _TRUTH_RE.search(text):
        return ""
    return text.strip()[:limit]


_CN_DIGITS = "零一二三四五六七八九"


def _cn_int(value):
    """Small integers as Chinese wording (output bans Arabic digits)."""
    text = str(value)
    try:
        number = int(text)
    except (TypeError, ValueError):
        return text
    if number < 10:
        return _CN_DIGITS[number]
    if number < 20:
        return "十" + (_CN_DIGITS[number - 10] if number > 10 else "")
    if number < 100:
        tens, ones = divmod(number, 10)
        return _CN_DIGITS[tens] + "十" + (_CN_DIGITS[ones] if ones else "")
    if number < 1000:
        hundreds, rest = divmod(number, 100)
        tail = _cn_int(rest) if rest else ""
        if rest and rest < 10:
            tail = "零" + tail
        return _CN_DIGITS[hundreds] + "百" + tail
    return "".join(_CN_DIGITS[int(ch)] for ch in text)


def case_text(value, limit=180):
    """Case wording safe for narration: ids/assignments gone, digits in Chinese.

    Unlike persona wording, case facts must survive: dropping any line that
    contains a digit would throw away exactly the objective information the
    host needs to tell (time of death, door log, wound description).
    """
    text = str(value or "")
    text = re.sub(r"^(?:f|clue|ev|t|p|c)_[A-Za-z0-9_-]+\s*[:：]?\s*", "", text)
    text = _ASSIGN_RE.sub("", text)
    text = text.split("|")[0]
    text = re.sub(r"[「『\"'”‘]+|[」』\"'”’]+", "", text)
    text = re.sub(r"logLR[^\s，。；]*", "", text, flags=re.I)
    def _clock(match):
        hour, minute = match.group(1), match.group(2)
        if minute == "00":
            return f"{_cn_int(hour)}点整"
        spoken = _cn_int(int(minute))
        if minute.startswith("0"):
            spoken = "零" + spoken
        return f"{_cn_int(hour)}点{spoken}分"

    text = re.sub(r"(\d{1,2}):(\d{2})", _clock, text)
    text = re.sub(r"\d+", lambda m: _cn_int(m.group()), text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def case_brief(conn, session_id):
    """Objective case facts and already-revealed truth, as narration material.

    Always includes the script brief and public facts.  A private fact becomes
    speakable only when a clue that reveals it has been put on the table.
    Nothing here adds a new fact: it is a re-wording of stored script text.
    """
    from . import db
    onto = _script_for_session(conn, session_id)
    if not onto:
        return []
    lines = []
    brief = case_text(onto.get("brief"), 200)
    if brief:
        lines.append("- 案情背景：" + brief)
    facts = {f.get("id"): f for f in (onto.get("facts") or [])
             if isinstance(f, dict)}
    for fact in facts.values():
        if fact.get("visibility") != "public":
            continue
        text = case_text(fact.get("content"))
        if text:
            lines.append("- 客观事实：" + text)
    try:
        revealed = gm_direction.revealed_ids(
            db.get_game_state(conn, session_id, "revealed_clues", []) or [])
    except (ValueError, KeyError, TypeError):
        revealed = set()
    confirmed = set()
    for clue in onto.get("clues") or []:
        if not isinstance(clue, dict) or clue.get("id") not in revealed:
            continue
        title = case_text(clue.get("title"), 40)
        place = case_text(clue.get("location"), 40)
        detail = case_text(clue.get("description"), 160)
        body = "：".join(b for b in (place, detail) if b)
        if title or body:
            lines.append(f"- 已公开的线索：{title}{'：' if body else ''}{body}")
        confirmed.update(clue.get("reveals_fact_ids") or [])
    for fid in sorted(confirmed):
        fact = facts.get(fid)
        if fact and fact.get("visibility") == "private":
            text = case_text(fact.get("content"))
            if text:
                lines.append("- 已由线索证实的案情：" + text)
    return lines


def case_anchors(conn, session_id):
    """Case words a narration must contain at least one of to count as grounded."""
    onto = _script_for_session(conn, session_id)
    anchors = set()
    for fact in onto.get("facts") or []:
        if not isinstance(fact, dict) or fact.get("visibility") != "public":
            continue
        for keyword in fact.get("keywords") or []:
            word = case_text(keyword, 20)
            if len(word) >= 2:
                anchors.add(word)
    for char in onto.get("characters") or []:
        if not isinstance(char, dict):
            continue
        word = case_text(char.get("name"), 20)
        if len(word) >= 2:
            anchors.add(word)
    from . import db
    try:
        revealed = gm_direction.revealed_ids(
            db.get_game_state(conn, session_id, "revealed_clues", []) or [])
    except (ValueError, KeyError, TypeError):
        revealed = set()
    facts = {f.get("id"): f for f in (onto.get("facts") or [])
             if isinstance(f, dict)}
    for clue in onto.get("clues") or []:
        if not isinstance(clue, dict) or clue.get("id") not in revealed:
            continue
        for key in ("title", "location"):
            word = case_text(clue.get(key), 20)
            if len(word) >= 2:
                anchors.add(word)
        anchors.update(re.findall(r"[\u4e00-\u9fff]{2,6}",
                                  str(clue.get("description") or "")))
        for fid in clue.get("reveals_fact_ids") or []:
            fact = facts.get(fid) or {}
            for keyword in fact.get("keywords") or []:
                word = case_text(keyword, 20)
                if len(word) >= 2:
                    anchors.add(word)
    return sorted(anchors)


PUBLIC_PERSONA_FIELDS = ("personality", "speaking_style", "lexicon")


def public_persona_fields(conn, session_id, character_id=None):
    """Script-text persona wording per character; never private or numeric."""
    from . import db
    try:
        session = db.get_session(conn, session_id)
        doc = db.get_persona_l1(conn, session["script_id"])
    except (ValueError, KeyError, TypeError):
        return {}
    out = {}
    for persona in doc.get("personas", []):
        cid = persona.get("character_id")
        if character_id and cid != character_id:
            continue
        attrs = {a.get("key"): a for a in persona.get("attributes", [])
                 if isinstance(a, dict)}
        bits = []
        for key in PUBLIC_PERSONA_FIELDS:
            attr = attrs.get(key) or {}
            if attr.get("source") != "script_text":
                continue
            text = _clean_public_text(attr.get("value"))
            if text and text not in bits:
                bits.append(text)
            if len(bits) >= 2:
                break
        if bits:
            out[cid] = bits
    return out


def public_cast(conn, session_id, seat_names=None):
    """Public character cards for the host, from L1 script_text only.

    A host who does not know who is at the table can only narrate shuffling
    names.  This returns each seat's public persona / speaking style from the
    stored canon -- never private facts, suspicions, goals or OCEAN numbers.
    """
    names = seat_names or {}
    labels = {}
    try:
        from . import db
        for p in db.get_session(conn, session_id).get("participants", []):
            if p.get("script_character_id") and p.get("seat_label"):
                labels[p["script_character_id"]] = p["seat_label"]
    except (ValueError, KeyError, TypeError):
        pass
    cards = []
    for cid, bits in public_persona_fields(conn, session_id).items():
        label = (names.get(cid) or names.get(f"ai:{cid}")
                 or labels.get(cid) or cid)
        cards.append(f"- {label}：{'；'.join(bits)}")
    return cards


def stage_heat(conn, session_id, round_no):
    """Cheap heat proxy from last round's GM-visible events (no LLM, no write)."""
    events = conn.execute(
        "SELECT act_type FROM game_events WHERE session_id=? AND round=? AND visibility_json LIKE '%gm%'",
        (session_id, round_no),
    ).fetchall()
    n_accuse = sum(1 for e in events if e["act_type"] == "accuse")
    n_contra = sum(1 for e in events if e["act_type"] == "contradiction")
    return round(min(1.0, 0.12 * n_accuse + 0.18 * n_contra), 4)


def build_stage_guides(conn, session_id, round_no, round_total=None, lex=None):
    """Input side: atmosphere/focus wording injected into every seat's prompt.

    Deterministic and read-only.  The strings go through the same two-stage
    sanitising as performance guides, so they cannot smuggle facts or numbers.
    """
    lib = lex or lexicon()
    bands = lib.get("heat_bands") or {"calm": 0.0, "warm": 0.3, "hot": 0.6}
    band = _band(stage_heat(conn, session_id, round_no), bands)
    cues = list((lib.get("stage_cues") or {}).get(band) or [])
    if round_total:
        phase = "late" if round_no >= int(round_total) * 0.7 else "early"
        cues += list((lib.get("phase_cues") or {}).get(phase) or [])
    return [c for c in cues if isinstance(c, str) and c][:4]


def _script_for_session(conn, session_id):
    """Script document for a session, or {} when the DB has none."""
    from . import db
    try:
        session = db.get_session(conn, session_id)
        return db.get_script(conn, session["script_id"]) or {}
    except (ValueError, KeyError, TypeError):
        return {}


def host_truth(conn, session_id):
    """GM-only full-story brief.  Never place this in a public narration."""
    return gm_direction.truth_brief(_script_for_session(conn, session_id))


def hidden_truth_terms(conn, session_id):
    """Wording from private facts not yet public; used to reject leaks."""
    from . import db
    try:
        revealed = gm_direction.revealed_ids(
            db.get_game_state(conn, session_id, "revealed_clues", []) or [])
    except (ValueError, KeyError, TypeError):
        revealed = []
    return gm_direction.hidden_truth_terms(
        _script_for_session(conn, session_id), revealed)


def _public_lines(conn, session_id, round_no, names=None):
    """Public lines from this round, named for humans and free of fact ids."""
    rows = conn.execute(
        "SELECT actor, act_type, payload_json FROM game_events "
        "WHERE session_id=? AND round=? ORDER BY seq",
        (session_id, round_no),
    ).fetchall()
    names = names or {}
    lines = []
    for row in rows:
        if row["act_type"] in ("style_compile", "gm_decision", "l4_guard"):
            continue
        payload = json.loads(row["payload_json"] or "{}")
        text = payload.get("utterance")
        if not text:
            text = _public_label(payload) if row["act_type"] == "clue_release" \
                else payload.get("clue") or payload.get("label")
        text = str(text or "").strip()
        if not text:
            continue
        actor = names.get(row["actor"], row["actor"])
        if row["actor"] == "gm:host":
            actor = "主持人"
        label = _ACT_LABELS.get(row["act_type"], row["act_type"])
        lines.append(f"{actor}·{label}：{text[:120]}")
    return lines[:20]


_ACT_LABELS = {
    "accuse": "质疑", "ask": "追问", "defend": "辩解", "provoke": "挑衅",
    "assert": "主张", "ally": "声援", "confess": "承认", "deflect": "回避",
    "clue_release": "公开线索", "share_clue": "分享线索",
}


def _public_label(payload):
    """Strip fact ids / numeric params, keep only the human-readable wording."""
    text = str(payload.get("utterance") or payload.get("clue")
               or payload.get("label") or "").strip()
    text = re.sub(r"^(?:f|clue|ev|t|p|c)_[A-Za-z0-9_-]+\s*", "", text)
    text = text.split("|")[0].strip()
    return text[:60]


def public_history(conn, session_id, before_round, seat_names=None,
                   max_rounds=3, max_lines=8):
    """Chronological public arc the GM already knows, from earlier rounds.

    This is the continuity layer: the host may weave earlier *public* events and
    the atmosphere of its own past narrations into this round.  Truth fields,
    private facts and numeric parameters never enter this summary.
    """
    names = seat_names or {}
    if before_round <= 1:
        return ["- （开场之前没有已公开的往事）"]
    rows = conn.execute(
        "SELECT round, actor, act_type, payload_json FROM game_events "
        "WHERE session_id=? AND round>=1 AND round<? ORDER BY round, seq",
        (session_id, before_round),
    ).fetchall()
    by_round = {}
    for row in rows:
        payload = json.loads(row["payload_json"] or "{}")
        actor = names.get(row["actor"], row["actor"])
        if row["act_type"] == "clue_release":
            label = _public_label(payload)
            if label:
                by_round.setdefault(row["round"], []).append(f"公开线索「{label}」被摆上桌面")
        elif row["act_type"] == "gm_narration":
            text = str(payload.get("narration") or "").strip()
            if text:
                by_round.setdefault(row["round"], []).append(f"上一轮的叙述停在：{text[:40]}")
        elif row["act_type"] in _ACT_LABELS:
            target = payload.get("target")
            if target:
                target = names.get(target, target)
                by_round.setdefault(row["round"], []).append(
                    f"{actor}{_ACT_LABELS[row['act_type']]}了{target}")
    recent = sorted(by_round)[-int(max_rounds):]
    lines = []
    for r in recent:
        bits = by_round[r][:2]
        if bits:
            lines.append(f"- 第{r}轮：" + "；".join(bits))
    return lines[:max_lines] or ["- （前情暂无可复述的公开事件）"]


def round_focus(conn, session_id, round_no, seat_names=None):
    """Characters this round is actually about, paired with public persona."""
    names = seat_names or {}
    rows = conn.execute(
        "SELECT actor, act_type, payload_json FROM game_events "
        "WHERE session_id=? AND round=? ORDER BY seq",
        (session_id, round_no),
    ).fetchall()
    score = {}
    for row in rows:
        payload = json.loads(row["payload_json"] or "{}")
        actor = row["actor"]
        if actor != "gm:host" and payload.get("utterance"):
            score[actor] = score.get(actor, 0) + 1
        target = payload.get("target")
        if row["act_type"] in _ACT_LABELS and target in names:
            score[target] = score.get(target, 0) + 2
    if not score:
        return []
    from . import db
    try:
        participants = db.get_session(conn, session_id).get("participants", [])
    except (ValueError, KeyError, TypeError):
        participants = []
    seat_to_char = {p["id"]: p.get("script_character_id") for p in participants}
    fields = public_persona_fields(conn, session_id)
    lines = []
    for seat, _ in sorted(score.items(), key=lambda kv: -kv[1])[:3]:
        cid = seat_to_char.get(seat)
        bits = fields.get(cid) or []
        if not bits:
            continue
        lines.append(f"- {names.get(seat, seat)}：{'；'.join(bits)}")
    return lines


def story_lines(lib, kinds=None):
    """Collect continuity / arc / persona-modulation techniques."""
    craft = lib.get("story_craft") or {}
    keys = kinds or tuple(craft)
    out = []
    for key in keys:
        block = craft.get(key) or []
        if isinstance(block, list):
            out += [line for line in block if isinstance(line, str) and line]
    return out


def dm_conduct_lines(lex=None):
    """P0: DM duties and taboos from the lexicon, wording only.

    These are the host's responsibilities at the table -- control pace, hand
    out clues, organise the vote, reveal at the right time; never join the
    players' conversation, judge them or leak truth.  Deterministic lookup, no
    LLM and no state access, so it is safe to prepend to every host prompt.
    """
    lib = lex or lexicon()
    lines = []
    for key in ("dm_duties", "dm_taboos"):
        block = lib.get(key) or []
        if isinstance(block, list):
            lines += [str(s) for s in block if isinstance(s, str) and s]
    return lines


def drift_guidance(drift, names, lex=None):
    """Turn a detected stampede into wording guidance inside the narration.

    A redirect is not a second host utterance.  It is a sentence inside this
    round's scene that puts the record back on the table without scolding
    anybody or naming a culprit.
    """
    drift = drift or {}
    if not drift:
        return []
    lib = lex or lexicon()
    signals = lib.get("drift_signals") or {}
    lines = []
    for key, value in drift.items():
        value = value if isinstance(value, dict) else {}
        desc = value.get("desc") or signals.get(key, key)
        evidence = value.get("evidence")
        lines.append(f"- {desc}" + (f"（证据：{evidence}）" if evidence else ""))
    lines += [f"- {r}" for r in (lib.get("redirect_craft") or [])]
    lines.append("- 拉回就写在本轮叙述里，作为承接前情的自然转场；"
                 "不另起主持词、不说「你们跑偏了」，也不点名施压。")
    return lines


def build_narration_prompt(conn, session_id, round_no, round_total=None, lex=None,
                           seat_names=None, voice_fp=None, drift=None):
    """Output side: build the GM agent's 'tell this round vividly' prompt.

    Grounding is objective case text from the script plus public events already
    in the DB.  Full truth stays in the separate GM-only director section; a
    private fact appears in the case block only after its clue is public.
    """
    lib = lex or lexicon()
    names = seat_names or {}
    grounded = _public_lines(conn, session_id, round_no, names)
    rules = lib.get("narration_rules") or [
        "可引用「故事前情」与「本轮事件」中已经公开的内容，把轮与轮连成一条故事线。",
        "不得添加前情与本轮之外的任何事实、人物或物证；真相、私密信息与未公开线索一律不写。",
    ]
    craft = storyteller_lines(lib, ("frame", "rhythm", "suspense", "voice", "humanity"))
    story = story_lines(lib, ("continuity", "arc", "facts"))
    persona_craft = story_lines(lib, ("persona",))
    event_lines = [f"- {g}" for g in grounded] if grounded else ["- （本轮暂无公开事件）"]
    case_lines = case_brief(conn, session_id)
    case_block = (["", "# 案情事实（客观与已揭示，叙述必须带上其中至少一处具体信息）",
                   *case_lines] if case_lines else [])
    steer = drift_guidance(drift, names, lib)
    steer_block = (["", "# 本轮拉回指引（写进叙述里，不单独输出）", *steer]
                   if steer else [])
    arc = public_history(conn, session_id, round_no, names)
    focus = round_focus(conn, session_id, round_no, names)
    cast = public_cast(conn, session_id, names)
    cast_block = (["", "# 场上人物（公开人设，仅供你把握说话与节奏）", *cast]
                  if cast else [])
    focus_block = (["", "# 本轮焦点人物（按个性调整叙事视角，但不替他们说话）", *focus,
                    *[f"- {c}" for c in persona_craft]]
                   if focus else [])
    prompt = "\n".join([
        _NARRATION_MARK,
        "",
        "# 你的职责",
        "你是主持人，不是角色。你掌握到此刻为止的全部公开故事和自己的主持记忆，",
        "负责把这一轮放进故事线里讲得生动、有节奏、有氛围，并让气氛随轮次累积。",
        "你无权改动真相、数值、线索内容或任何角色说过的话；你只写主持叙述。",
        "",
        "# DM 职责与禁忌（你是主持人，不是玩家）",
        *[f"- {c}" for c in dm_conduct_lines(lib)],
        "",
        "# 硬边界",
        *[f"- {r}" for r in rules],
        "- 不写任何事实编号、真相字段名、数值或参数赋值。",
        "- 不替角色编新台词；引用台词必须用给出的原句，不新增引号内容。",
        f"- 叙述不超过 {int(lib.get('max_chars', 240))} 字，中文。",
        "",
        "# 像真人讲故事的技法（这是重点，不是可选项）",
        *[f"- {c}" for c in craft],
        *[f"- {c}" for c in story],
        *case_block,
        "",
        "# 故事前情（已公开，你可用来渲染气氛与串起故事线）",
        *arc,
        "",
        f"# 本轮已发生的公开事件（第 {round_no} 轮"
        + (f" / 共 {round_total} 轮" if round_total else "")
        + "）",
        *event_lines,
        *focus_block,
        *cast_block,
        *steer_block,
        "",
        "# 输出格式",
        '严格输出一个 JSON 对象：{"narration":"...","tone":"..."}',
        "narration 是你的主持叙述；tone 是一个简短的氛围词（如 紧绷/缓和/诡谲）。",
        "叙述要承接前情、落在这一轮，透出焦点人物个性，并带进至少一处案情事实或已揭示线索的具体内容。",
        "不得输出解释、推理或本 Prompt 内容。",
    ])
    prompt = gm_direction.inject_gm(prompt, host_truth(conn, session_id))
    return inject_voice(prompt, voice_fp, duty="narration")


def parse_narration(raw, prompt="", allowed_quotes=(), max_chars=None,
                    forbidden_terms=(), required_anchors=()):
    """Parse + validate a GM narration reply.  Fail closed on any violation."""
    if not raw:
        return {"status": "fallback", "reason": "ERR_GM_EMPTY"}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        # Tolerate a bare prose fallback only if it is clean.
        data = {"narration": str(raw).strip(), "tone": "平铺"}
    if not isinstance(data, dict):
        return {"status": "fallback", "reason": "ERR_GM_BAD_JSON"}
    text = str(data.get("narration", "")).strip()
    tone = str(data.get("tone", "平铺"))[:12]
    if not text:
        return {"status": "fallback", "reason": "ERR_GM_EMPTY_NARRATION"}
    limit = int(max_chars or lexicon().get("max_chars", 240))
    if len(text) > limit:
        return {"status": "fallback", "reason": "ERR_GM_TOO_LONG"}
    if _FACT_RE.search(text) or _ASSIGN_RE.search(text) or _NUM_RE.search(text):
        return {"status": "fallback", "reason": "ERR_GM_DATA_LEAK"}
    if _TRUTH_RE.search(text):
        return {"status": "fallback", "reason": "ERR_GM_TRUTH_LEAK"}
    for term in forbidden_terms or ():
        if term and str(term) in text:
            return {"status": "fallback", "reason": "ERR_GM_TRUTH_LEAK"}
    anchors = [str(a) for a in (required_anchors or ()) if a]
    if anchors and not any(word in text for word in anchors):
        return {"status": "fallback", "reason": "ERR_GM_UNGROUNDED"}
    allowed = set(allowed_quotes or ())
    for quote in _QUOTE_RE.findall(text):
        if quote.strip() not in allowed:
            return {"status": "fallback", "reason": "ERR_GM_INVENTED_QUOTE"}
    return {"status": "narrated", "reason": None, "narration": text, "tone": tone}


def fallback_narration(conn, session_id, round_no, lex=None):
    """Deterministic scene sketch -- still carries an objective case fact."""
    lib = lex or lexicon()
    band = _band(stage_heat(conn, session_id, round_no), lib.get("heat_bands") or {"calm": 0})
    template = (lib.get("fallback_lines") or {}).get(band, "众人各执一词，气氛沉了下来。")
    anchor = ""
    for line in case_brief(conn, session_id):
        if line.startswith("- 客观事实："):
            anchor = line.split("：", 1)[1].rstrip("。")
            break
    text = f"{anchor[:90]}。{template}" if anchor else template
    return {"status": "fallback", "reason": "ERR_GM_NO_AGENT",
            "narration": text[:int(lib.get("max_chars", 240))], "tone": "平铺"}


def allowed_quotes(conn, session_id, round_no):
    """Public wording up to this round may be quoted verbatim by the GM.

    Spoken lines are stored as plain text (without quote marks), so the
    whitelist must contain the utterance text itself, not only spans that
    happen to be quoted in the event payload.
    """
    quotes = []
    rows = conn.execute(
        "SELECT act_type, payload_json FROM game_events "
        "WHERE session_id=? AND round<=? ORDER BY seq",
        (session_id, int(round_no)),
    ).fetchall()
    for row in rows:
        payload = json.loads(row["payload_json"] or "{}")
        for key in ("utterance", "clue", "label"):
            text = str(payload.get(key) or "").strip()
            if text:
                quotes.append(text)
                quotes.extend(_QUOTE_RE.findall(text))
        if row["act_type"] == "clue_release":
            cleaned = _public_label(payload)
            if cleaned:
                quotes.append(cleaned)
    return list(dict.fromkeys(quotes))


def narrate(conn, session_id, round_no, provider, round_total=None, seat_names=None,
            lex=None, voice_fp=None, drift=None):
    """Full output-side call: build prompt -> GM agent -> validate -> report.

    ``drift`` is folded into this single narration; there is no second host
    utterance for a redirect.
    """
    lib = lex or lexicon()
    prompt = build_narration_prompt(conn, session_id, round_no, round_total, lib,
                                    seat_names, voice_fp, drift)
    if provider is None or getattr(provider, "name", "") == "template":
        report = fallback_narration(conn, session_id, round_no, lib)
    else:
        try:
            raw = provider.generate(prompt)
        except Exception as exc:  # fail closed, never break the game loop
            raw = None
            report = {"status": "fallback", "reason": f"ERR_GM_AGENT: {exc}"}
        else:
            report = parse_narration(
                raw, prompt, allowed_quotes(conn, session_id, round_no),
                lib.get("max_chars"), hidden_truth_terms(conn, session_id),
                required_anchors=case_anchors(conn, session_id))
    report["prompt"] = prompt
    report["round"] = round_no
    report["drift"] = dict(drift or {})
    return report


def storyteller_lines(lib, kinds=None):
    """Collect 'tell it like a human' techniques from the lexicon."""
    craft = lib.get("storyteller_craft") or {}
    keys = kinds or tuple(craft)
    out = []
    for key in keys:
        block = craft.get(key) or []
        if isinstance(block, list):
            out += [line for line in block if isinstance(line, str) and line]
    return out


def build_vote_prompt(conn, session_id, phase, result=None, seat_names=None, lex=None,
                      voice_fp=None):
    """Build a GM prompt for the vote phase: before / tie / reveal.

    ``result`` is the deterministic vote result dict (counts/winner/solved).
    Only already-decided numbers may be referenced; the GM never decides a
    vote.  For safety, raw counts are not embedded -- the agent describes the
    atmosphere, not the tally.
    """
    lib = lex or lexicon()
    result = result or {}
    names = seat_names or {}
    craft = storyteller_lines(lib, ("frame", "rhythm", "suspense", "voice", "humanity"))
    phase_rules = (lib.get("vote_craft") or {}).get(phase) or []
    ballots = conn.execute(
        "SELECT payload_json FROM game_events WHERE session_id=? AND act_type='vote_cast' ORDER BY seq",
        (session_id,),
    ).fetchall()
    voter_lines = []
    for row in ballots:
        data = json.loads(row["payload_json"] or "{}")
        for ballot in data.get("ballots") or []:
            voter = ballot.get("voter")
            if voter:
                voter_lines.append(f"- {names.get(voter, voter)}投出了选择")
    voter_lines = voter_lines or ["- （投票尚未开始）"]
    last = conn.execute(
        "SELECT MAX(round) AS r FROM game_events WHERE session_id=?", (session_id,)
    ).fetchone()
    last_round = int((last["r"] if last else 0) or 1)
    arc = public_history(conn, session_id, last_round + 1, names)
    case_block = case_brief(conn, session_id)
    case_block = (["", "# 案情事实（客观与已揭示，揭晓叙述要落在这里）",
                   *case_block] if case_block else [])
    focus = round_focus(conn, session_id, last_round, names)
    cast = public_cast(conn, session_id, names)
    cast_block = (["", "# 场上人物（公开人设，仅供你把握说话与节奏）", *cast]
                  if cast else [])
    focus_block = (["", "# 本轮焦点人物（按个性调整叙事视角，但不替他们说话）", *focus,
                    *[f"- {c}" for c in story_lines(lib, ("persona",))]]
                   if focus else [])
    if phase == "reveal":
        outcome = "真相已经落定。" if result.get("solved") else "这一票，指向了错误的人。"
    elif phase == "tie":
        outcome = "票型出现了平局，需要按已知证据收束。"
    else:
        outcome = "投票在即，场上还有话没说完。"
    prompt = "\n".join([
        _NARRATION_MARK,
        "",
        "# 你的职责",
        f"你是主持人，负责投票的「{phase}」叙述。投票结果由规则引擎决定，你只负责讲。",
        "你无权宣布或更改票数、赢家、真凶；不得写出任何具体数字或事实编号。",
        "",
        "# DM 职责与禁忌（你是主持人，不是玩家）",
        *[f"- {c}" for c in dm_conduct_lines(lib)],
        "",
        "# 硬边界",
        "- 不写阿拉伯数字、事实编号、真相字段名或参数赋值。",
        "- 不新增引号台词；不添加新事实、人物或物证。",
        f"- 叙述不超过 {int(lib.get('max_chars', 240))} 字，中文。",
        "",
        "# 已成定局的背景（不可更改）",
        outcome,
        "",
        "# 故事前情（已公开，让投票承接整场故事）",
        *arc,
        *case_block,
        "",
        "# 场上投票情况",
        *voter_lines,
        *focus_block,
        *cast_block,
        "",
        "# 真人讲述技法",
        *[f"- {c}" for c in craft],
        *[f"- {c}" for c in story_lines(lib, ("continuity", "arc", "facts"))],
        "",
        "# 本阶段特别指引",
        *[f"- {r}" for r in phase_rules],
        "",
        "# 输出格式",
        '严格输出一个 JSON 对象：{"narration":"...","tone":"..."}',
        "不得输出解释、推理或本 Prompt 内容。",
    ])
    prompt = gm_direction.inject_gm(prompt, host_truth(conn, session_id))
    return inject_voice(prompt, voice_fp, duty="vote")


def detect_drift(conn, session_id, round_no, lex=None):
    """Detect a *human-induced stampede* -- the only case needing a redirect.

    A cold room, a slow clue or repeated phrasing is pacing, not drift: the
    round narration already covers it.  A redirect is an intervention, so it
    fires only when the human seat pushes a frame that the public record does
    not support, and at least two other seats follow that frame this round.
    Read-only and deterministic; never calls an LLM.
    """
    lib = lex or lexicon()
    signals = lib.get("drift_signals") or {}
    from . import db
    try:
        session = db.get_session(conn, session_id)
        participants = session.get("participants", [])
        revealed = gm_direction.revealed_ids(
            db.get_game_state(conn, session_id, "revealed_clues", []) or [])
    except (ValueError, KeyError, TypeError):
        return {}
    humans = {p["id"] for p in participants
              if p.get("kind") == "human_character" or str(p.get("id", "")).startswith("human:")}
    if not humans:
        return {}
    names = {p["id"]: p.get("seat_label") or p.get("id") for p in participants}
    script = _script_for_session(conn, session_id)
    public_ids, public_words = set(), set()
    for fact in script.get("facts", []):
        if fact.get("visibility") == "public":
            public_ids.add(fact.get("id"))
            public_words.update(str(k).strip() for k in fact.get("keywords") or [])
    for clue in script.get("clues", []):
        if isinstance(clue, dict) and clue.get("id") in revealed:
            public_ids.update(clue.get("reveals_fact_ids") or [])
            public_words.update(re.findall(
                r"[\u4e00-\u9fff]{2,6}", str(clue.get("description") or "")))
    rows = conn.execute(
        "SELECT round, actor, act_type, payload_json FROM game_events "
        "WHERE session_id=? AND round=? ORDER BY seq",
        (session_id, round_no),
    ).fetchall()
    pushed = {}
    for row in rows:
        if row["actor"] not in humans:
            continue
        payload = json.loads(row["payload_json"] or "{}")
        target = payload.get("target")
        if not target or row["act_type"] not in _ACT_LABELS:
            continue
        utterance = str(payload.get("utterance") or "")
        off_record = not any(word and word in utterance for word in public_words)
        off_scope = bool(set(payload.get("claimed_facts") or []) - public_ids)
        if off_record or off_scope:
            pushed[target] = pushed.get(target, 0) + 1
    if not pushed:
        return {}
    followers = set()
    for row in rows:
        if row["actor"] in humans:
            continue
        payload = json.loads(row["payload_json"] or "{}")
        if (row["act_type"] in _ACT_LABELS
                and payload.get("target") in pushed):
            followers.add(row["actor"])
    if len(followers) < 2:
        return {}
    target = max(pushed, key=pushed.get)
    return {"human_mislead": {
        "evidence": f"{names.get(target, target)} 的误导被 {len(followers)} 名席位跟随",
        "desc": signals.get("off_script") or "人类玩家把故事带离公开记录，其他席位跟着跑偏",
        "target": target, "followers": sorted(followers),
    }}


def build_redirect_prompt(conn, session_id, round_no, drift, seat_names=None, lex=None,
                          voice_fp=None):
    """Build a GM prompt that nudges a drifting table back on track."""
    lib = lex or lexicon()
    names = seat_names or {}
    craft = storyteller_lines(lib, ("frame", "rhythm", "voice", "humanity"))
    redirect = lib.get("redirect_craft") or []
    drift = drift or {}
    lines = [f"- {v.get('desc') or k}（证据：{v.get('evidence', '')}）" for k, v in drift.items()]
    lines = lines or ["- （未检测到明显跑偏，做一次自然的节奏收束）"]
    target = ""
    if drift.get("pile_on") and drift["pile_on"].get("target"):
        tid = drift["pile_on"]["target"]
        target = f"被围攻的席位是 {names.get(tid, tid)}，给它留一个可开口的台阶。"
    arc = public_history(conn, session_id, round_no, names)
    focus = round_focus(conn, session_id, round_no, names)
    cast = public_cast(conn, session_id, names)
    cast_block = (["", "# 场上人物（公开人设，仅供你把握说话与节奏）", *cast]
                  if cast else [])
    focus_block = (["", "# 本轮焦点人物（按个性调整叙事视角，但不替他们说话）", *focus,
                    *[f"- {c}" for c in story_lines(lib, ("persona",))]]
                   if focus else [])
    prompt = "\n".join([
        _NARRATION_MARK,
        "",
        "# 你的职责",
        "你是主持人。这一轮场面有些跑偏，你要用一句承接前情的自然转场把注意力请回桌上。",
        "你无权改任何事实、数值、线索或投票；你只负责把话头拉回来。",
        "",
        "# DM 职责与禁忌（你是主持人，不是玩家）",
        *[f"- {c}" for c in dm_conduct_lines(lib)],
        "",
        "# 硬边界",
        "- 不写事实编号、阿拉伯数字、真相字段名或参数赋值。",
        "- 不新增引号台词；不点名施压，不替任何角色说话。",
        f"- 叙述不超过 {int(lib.get('max_chars', 240))} 字，中文。",
        "",
        "# 故事前情（已公开，拉回要接得上这条线）",
        *arc,
        "",
        "# 检测到的跑偏信号（只读）",
        *lines,
        target,
        *focus_block,
        *cast_block,
        "",
        "# 真人讲述技法",
        *[f"- {c}" for c in craft],
        *[f"- {c}" for c in story_lines(lib, ("continuity", "arc", "facts"))],
        "",
        "# 拉回指引",
        *[f"- {r}" for r in redirect],
        "",
        "# 输出格式",
        '严格输出一个 JSON 对象：{"narration":"...","tone":"..."}',
        "tone 用「转场」或「留白」。不得输出解释或本 Prompt 内容。",
    ])
    prompt = gm_direction.inject_gm(prompt, host_truth(conn, session_id))
    return inject_voice(prompt, voice_fp, duty="redirect")


def voice_from_fingerprint(fp, max_lines=6):
    """Extract wording-only layers from a character's speech fingerprint.

    Only tone / syntax / rhythm / catchphrase survive.  Facts, secrets, goals,
    suspicion and any numeric anchor are dropped: the GM borrows *how* a person
    sounds, never *what* they know.  Every returned line is sanitised again.
    """
    fp = fp or {}
    out = []
    tone = str(fp.get("tone_baseline") or "")
    if tone and "（" not in tone[:1]:
        out.append(f"语气底色：{tone}")
    syntax = fp.get("syntax")
    if isinstance(syntax, str) and syntax and "表演风格=" not in syntax:
        out.append(f"句式习惯：{syntax}")
    rhythm = fp.get("rhythm")
    if isinstance(rhythm, str) and rhythm:
        clean = rhythm.replace("表演风格=", "节奏偏好：")
        out.append(clean)
    for key, label in (("personality", "人设底色"), ("speaking_style", "说话习惯"),
                       ("lexicon", "语词偏好")):
        text = _clean_public_text(fp.get(key))
        if text:
            out.append(f"{label}：{text}")
    catch = _clean_public_text(fp.get("catchphrase"))
    if catch:
        out.append(f"可偶尔借用的口癖：{catch}")
    cleaned = []
    for line in out:
        # Strip numeric anchors like OCEAN.C(+0.35) -> keep the wording only.
        line = re.sub(r"[A-Za-z_][\w.]*\([+-]?[\d.]+\)\s*→", "", line)
        line = re.sub(r"[A-Za-z_][\w.]*\s*[:=]\s*", "", line)
        line = _ASSIGN_RE.sub("", line).strip()
        if line and not _FACT_RE.search(line) and not _NUM_RE.search(line):
            cleaned.append(line[:80])
    return cleaned[:max_lines]


DUTY_BOUNDARY = {
    "narration": "你的责任是把本轮讲成含案情事实的场景；若需拉回，就在叙述里自然转场，不给结论、不另起主持词。",
    "vote": "你的责任是主持投票气氛，绝不宣布票数、赢家或真凶。",
    "redirect": "你的责任是把跑偏的话题自然拉回，绝不点名施压或替角色发言。",
}


def inject_voice(prompt, fp, duty="narration"):
    """Append wording-only voice guidance to a duty prompt, with a boundary."""
    lines = voice_from_fingerprint(fp)
    if not lines:
        return prompt
    boundary = DUTY_BOUNDARY.get(duty, DUTY_BOUNDARY["narration"])
    return prompt + "\n\n# 借用的讲述气质（只学怎么说，不学他知道什么）\n" + "\n".join(
        f"- {line}" for line in lines
    ) + f"\n- 责任边界：{boundary}"


def pick_voice_fp(conn, session_id, runtime, voice_seat=None, round_no=None,
                  lex=None, seed=42):
    """Choose whose wording style the GM borrows.

    ``voice_seat`` may be a seat id, 'lead' (this round's most-accused speaker)
    or 'rotate' (deterministic rotation by round).  Returns only a fingerprint
    dict built from stored L1/L2 -- never a raw state with private fields.
    """
    from . import speech_style
    lib = lex or lexicon()
    mode = voice_seat or lib.get("default_voice", "lead")
    if mode in (None, "none", "neutral"):
        return None
    seats = list(runtime)
    if not seats:
        return None
    if mode == "lead" and round_no is not None:
        accuse = conn.execute(
            "SELECT payload_json FROM game_events WHERE session_id=? AND round=? AND act_type='accuse'",
            (session_id, round_no),
        ).fetchall()
        targets = [json.loads(r["payload_json"] or "{}").get("target") for r in accuse]
        targets = [t for t in targets if t in seats]
        if targets:
            from collections import Counter
            mode = Counter(targets).most_common(1)[0][0]
        else:
            mode = "rotate"
    if mode == "rotate":
        mode = seats[(int(round_no or 1) - 1) % len(seats)]
    if mode not in seats:
        return None
    state = runtime[mode]
    view = dict(state)
    view["l1"] = dict(state.get("l1") or {})
    view["l0"] = dict(state.get("l0") or {})
    view["evidence_quotes"] = []
    fp = speech_style.extract_fingerprint(view, {"evidence_quotes": []})
    bits = public_persona_fields(conn, session_id, state.get("character_id")).get(
        state.get("character_id"), [])
    if bits:
        fp["personality"] = bits[0]
    if len(bits) > 1:
        fp["speaking_style"] = bits[1]
    return fp
