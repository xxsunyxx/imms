"""Persona -> speaking-style prompt engine (80% anchor / 20% drift).

The algorithm is written against the data IMMS actually owns, not against the
reference design's field names.  Every prompt slot is filled from a stored
field or a deterministic derivation of one:

  * ``syntax``      <- OCEAN.O/C/E (stable L1 values)
  * ``rhythm``      <- OCEAN.N + L0.play_style (stable)
  * ``catchphrase`` <- verbatim quoted span in ``personality`` / ``speaking_style``
  * ``sample_lines``<- verbatim ``canon_evidence`` quotes (labelled as evidence
                      sentences, never presented as invented dialogue)
  * drift signals   <- emotion (incl. real dominance), relations (incl.
                      grudge/debt), delivery.params, breakout.state, facets,
                      accumulator and the already-sampled delivery.probability
  * variant params  <- IPS micro_perturb's per-round ``delivery.params``

Only genuinely absent fields (e.g. ``taboo_words``, no quoted catchphrase, no
evidence quotes) appear in ``missing``.  Nothing is fabricated to fill a slot.

There is no embedder/numpy in this project: cosine is a declared character-
bigram proxy, never presented as semantic similarity.
"""

from dataclasses import dataclass, asdict
import json
import math
import os
import random
import re

_LEXICON = None


def lexicon(config_dir=None):
    """Load the parameter->wording rule map (read-only, cached).

    These are style rules, never facts about a character: an OCEAN value maps
    to how something is said, not to what is said.  Missing file -> empty map,
    never a fabricated default.
    """
    global _LEXICON
    if _LEXICON is not None:
        return _LEXICON
    path = os.path.join(config_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config"
    ), "speech_style_lexicon.json")
    try:
        with open(path, encoding="utf-8") as fh:
            _LEXICON = json.load(fh)
    except (OSError, ValueError):
        _LEXICON = {}
    return _LEXICON

WEIGHTS = {"emotion": 0.30, "relation": 0.25, "mask": 0.20, "stress": 0.15, "noise": 0.10}
CAPS = {"emotion": 0.06, "relation": 0.05, "mask": 0.04, "stress": 0.03, "noise": 0.02}
TOTAL_BUDGET = 0.20
COS_MIN = 0.72
COS_MAX = 0.88
STRUCTURE_MIN = 0.78
STRUCTURE_MAX = 0.82
POST_GATE_ROUNDS = 3
POST_GATE_THRESHOLD = 0.18


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clip(value, lo=0.0, hi=1.0):
    return max(lo, min(hi, value))


@dataclass
class DriftRecord:
    emotion: float = 0.0
    relation: float = 0.0
    mask: float = 0.0
    stress: float = 0.0
    noise: float = 0.0

    def total(self):
        return round(sum(asdict(self).values()), 6)

    def as_dict(self):
        doc = asdict(self)
        doc["total"] = self.total()
        return doc


# ---------------------------------------------------------------- Step 1
# Fingerprint: every slot is derived from a field we actually store.

def _ocean_terms(key, value):
    """Look up the 5-band wording for an OCEAN dimension from the lexicon."""
    entry = (lexicon().get("ocean") or {}).get(key) or {}
    for band in entry.get("bands") or []:
        if value < _num(band.get("lt"), 1.01):
            return list(band.get("terms") or []), list(band.get("syntax") or [])
    return [], []


def derive_tone(l1):
    """Deterministic tone baseline from stored OCEAN values via the word map."""
    clauses = []
    for key in ("OCEAN.O", "OCEAN.C", "OCEAN.E", "OCEAN.A", "OCEAN.N"):
        value = l1.get(key)
        if isinstance(value, (int, float)):
            terms, _ = _ocean_terms(key, _num(value))
            if terms:
                clauses.append(f"{key.split('.')[-1]}: {'、'.join(terms[:2])}")
    return "；".join(clauses) or "（人格基线未配置，不推导语气）"


def derive_syntax(l1):
    """Sentence preferences from OCEAN (5-band word map, not invented prose)."""
    bits = []
    for key in ("OCEAN.O", "OCEAN.C", "OCEAN.E", "OCEAN.A", "OCEAN.N"):
        value = l1.get(key)
        if isinstance(value, (int, float)):
            _, syntax = _ocean_terms(key, _num(value))
            bits.extend(syntax)
    return "；".join(dict.fromkeys(bits)) or None


def relation_terms(view, audience):
    """Wording rules triggered by the stored relation vector toward audience."""
    rel = (view.get("relations") or {}).get(audience) or {}
    out = []
    lib = lexicon().get("relations") or {}
    for key, value in rel.items():
        entry = lib.get(key) or {}
        if not isinstance(value, (int, float)):
            continue
        bucket = "positive" if _num(value) > 0.15 else (
            "negative" if _num(value) < -0.15 else None)
        if bucket:
            for term in (entry.get(bucket) or [])[:2]:
                out.append(f"{key}({_num(value):+.2f})→{term}")
    return out


def emotion_terms(view):
    """Wording rules from the full emotion vector (not just arousal/valence)."""
    emo = view.get("emotion") or {}
    out = []
    lib = lexicon().get("emotion") or {}
    for key, value in emo.items():
        entry = lib.get(key) or {}
        if not isinstance(value, (int, float)):
            continue
        threshold = 0.2 if key == "valence" else 0.45
        raw = _num(value)
        if key == "valence":
            bucket = "positive" if raw > threshold else (
                "negative" if raw < -threshold else None)
        else:
            bucket = "high" if raw > threshold else ("low" if raw < -threshold else None)
        if bucket:
            for term in (entry.get(bucket) or [])[:2]:
                out.append(f"{key}({raw:+.2f})→{term}")
    return out


def l0_terms(view):
    """Wording rules from acting/lying skill, difficulty and play style."""
    l0 = view.get("l0") or {}
    out = []
    lib = lexicon().get("l0") or {}
    for key in ("acting_skill", "lying_skill", "difficulty", "play_style"):
        value = l0.get(key)
        entry = lib.get(key) or {}
        if isinstance(value, (int, float)) and key != "play_style":
            bucket = "high" if _num(value) >= 0.65 else "low"
            out.extend((entry.get(bucket) or [])[:2])
        elif isinstance(value, str) and value:
            out.extend((entry.get(value) or entry.get("steady") or [])[:2])
    return out


def ips_terms(view):
    """Wording rules from accumulator/theta and breakout state."""
    out = []
    lib = lexicon().get("ips") or {}
    acc = view.get("accumulator") or {}
    A = _num(acc.get("A"))
    theta = _num(acc.get("theta"), 0.6)
    ac = lib.get("accumulator") or {}
    if A >= theta:
        out.extend((ac.get("overshoot") or [])[:2])
    elif theta - A < 0.25:
        out.extend((ac.get("near_theta") or [])[:2])
    elif acc.get("refractory_until") and _num(acc.get("refractory_until")) > 0:
        out.extend((ac.get("refractory") or [])[:2])
    state = (view.get("breakout") or {}).get("state")
    if state:
        out.extend((lib.get("breakout", {}).get(state) or [])[:2])
    return out


def delivery_param_terms(variants):
    """Wording rules for the already-sampled IPS delivery params."""
    out = []
    lib = lexicon().get("delivery_params") or {}
    for key, info in lib.items():
        val = variants.get(key)
        if not isinstance(val, (int, float)):
            continue
        baseline = 1.0 if "factor" in key else 0.0
        if abs(_num(val) - baseline) < 0.05:
            continue
        bucket = "high" if _num(val) > baseline else "low"
        if key == "honorific_step":
            bucket = "on" if _num(val) > 0 else "off"
        elif key in ("pause_marks", "self_corrections"):
            bucket = "on" if _num(val) > 0 else "off"
        term = (info.get(bucket) or "").strip()
        if term:
            out.append(term)
    return out



def derive_rhythm(l1, l0):
    """Speech rhythm from OCEAN.N + L0 play_style via the word map."""
    bits = []
    if isinstance(l1.get("OCEAN.N"), (int, float)):
        _, syntax = _ocean_terms("OCEAN.N", _num(l1["OCEAN.N"]))
        bits.extend(syntax[:2])
    style = (l0 or {}).get("play_style")
    if style:
        terms = (lexicon().get("l0", {}).get("play_style", {}) or {}).get(style) or []
        bits.extend(terms[:2])
        bits.append(f"表演风格={style}")
    return "；".join(dict.fromkeys(bits)) or None


_QUOTE_RE = re.compile(r"[「『“\"]([^「」『』“”\"]{2,40})[」』”\"]")


def extract_catchphrase(*texts):
    """Pull a verbatim quoted span out of stored persona text.

    O09 holds nested quotes (``「我是 … 温和表态「我不动老人，先听大家讲」。」``);
    the innermost span is the actual pet phrase, so choose the longest clean
    match.  Returns None when the script carries no quotation at all.
    """
    for text in texts:
        if not text:
            continue
        hits = [m.strip() for m in _QUOTE_RE.findall(str(text)) if m.strip()]
        if hits:
            return max(hits, key=len)
    return None


def extract_fingerprint(view, ctx=None):
    """Read-only fingerprint built entirely from stored IMMS fields."""
    ctx = ctx or {}
    l1 = dict(view.get("l1") or {})
    l0 = dict(view.get("l0") or {})
    lexicon = l1.get("lexicon") or l1.get("speaking_style")
    ocean = {k: l1.get(k) for k in (
        "OCEAN.O", "OCEAN.C", "OCEAN.E", "OCEAN.A", "OCEAN.N"
    ) if isinstance(l1.get(k), (int, float))}
    honesty = l1.get("values.honesty")
    quotes = [str(q).strip() for q in (
        ctx.get("evidence_quotes") or view.get("evidence_quotes") or []
    ) if str(q or "").strip()]
    # Verbatim evidence sentences are few-shot anchors for wording, not lines
    # the character supposedly spoke before.  The label carries that distinction.
    sample_lines = quotes[:3]
    catchphrase = extract_catchphrase(l1.get("personality"), l1.get("speaking_style"), lexicon)
    missing = []
    if not catchphrase:
        missing.append("catchphrase")
    if not sample_lines:
        missing.append("sample_lines")
    # IMMS schema has no taboo list; keep it empty, never invent prohibitions.
    missing.append("taboo_words")
    return {
        "identity": view.get("name") or view.get("character_id"),
        "public_identity": view.get("public_identity")
        or l1.get("public_identity") or l1.get("L2.public_identity"),
        "lexicon": lexicon,
        "syntax": derive_syntax(l1),
        "rhythm": derive_rhythm(l1, l0),
        "catchphrase": catchphrase,
        "taboo_words": [],
        "values_anchor": {"values.honesty": honesty} if isinstance(honesty, (int, float)) else {},
        "ocean_anchor": ocean,
        "tone_baseline": derive_tone(l1),
        "l0_terms": l0_terms(view),
        "ips_terms": ips_terms(view),
        "sample_lines": sample_lines,
        "evidence_quotes": quotes,
        "missing": missing,
    }


def anchor_text(fp):
    """Text used as the anchor vector source (deterministic for a seat)."""
    bits = [str(fp.get("identity") or ""), str(fp.get("public_identity") or ""),
            str(fp.get("lexicon") or ""), str(fp.get("tone_baseline") or ""),
            str(fp.get("catchphrase") or "")]
    bits.extend(str(fp.get("sample_lines") or []))
    bits.extend(f"{k}={v:.2f}" for k, v in sorted((fp.get("ocean_anchor") or {}).items()))
    for k, v in sorted((fp.get("values_anchor") or {}).items()):
        bits.append(f"{k}={v:.2f}")
    return " ".join(bits)


# ---------------------------------------------------------------- Step 2
# Drift signals: all read real L3/L4/IPS fields.

def _emotion_signal(emotion):
    a = _num(emotion.get("arousal"))
    v = _num(emotion.get("valence"))
    d = _num(emotion.get("dominance"), 0.5)
    # IMMS stores dominance in [-1, 1]; low dominance = loss of control.
    # The design's (1 - dominance) is remapped to that scale, not proxied by
    # (1 - stress) as an earlier revision did.
    low_control = 1.0 - _clip((d + 1.0) / 2.0)
    return _clip(0.5 * abs(v) + 0.3 * a + 0.2 * low_control)


def _relation_signal(view, audience):
    rel = (view.get("relations") or {}).get(audience) or {}
    keys = ("like", "trust", "suspicion", "grudge", "debt")
    parts = [_num(rel.get(k)) for k in keys]
    parts = [p for p in parts if p != 0.0] or parts
    return _clip(sum(abs(p) for p in parts) / max(1, len(parts)))


def _mask_signal(view):
    delivery = view.get("delivery") or {}
    params = delivery.get("params") or {}
    signal = 0.35 * _clip(_num(params.get("honorific_step")))
    # A charged/frenzy breakout is the observable face-slip signal.
    state = (view.get("breakout") or {}).get("state")
    if state in ("charged", "frenzy"):
        signal += 0.35
    facets = view.get("facets") or {}
    active = (facets.get("facets") or {}).get(facets.get("active_facet_id")) or {}
    if str(active.get("memory_scope") or "").startswith("isolated:"):
        signal += 0.20
    if (view.get("l0") or {}).get("mask") or view.get("l1", {}).get("L2.mask"):
        signal += 0.10
    return _clip(signal)


def _stress_signal(view):
    emo = view.get("emotion") or {}
    stress = _num(emo.get("stress"))
    exposure = _num(emo.get("exposure_risk"))
    load = _num(emo.get("cognitive_load"))
    acc = view.get("accumulator") or {}
    overshoot = max(0.0, _num(acc.get("A")) - _num(acc.get("theta"), 0.6))
    return _clip(stress + 0.4 * exposure * load + 0.3 * _clip(overshoot))


def _noise_signal(view, ctx):
    """Use the already-sampled IPS delivery result; seed fallback when absent."""
    delivery = view.get("delivery") or {}
    if delivery:
        return _clip(_num(delivery.get("probability"), 0.5))
    seat_id = ctx.get("seat_id")
    if not seat_id:
        return 0.0
    key = "|".join(str(x) for x in (
        ctx.get("session_id"), seat_id, ctx.get("round_no"), "speech_style:noise"
    ))
    return random.Random(f"{ctx.get('seed') or 0}|{key}").random()


def allocate_drift(view, ctx=None):
    """Allocate the 0.20 budget from stored signals; never fabricate a signal."""
    ctx = ctx or {}
    audience = ctx.get("target_name") or ctx.get("audience") or ""
    sig = {
        "emotion": _emotion_signal(view.get("emotion") or {}),
        "relation": _relation_signal(view, audience) if audience else 0.0,
        "mask": _mask_signal(view),
        "stress": _stress_signal(view),
        "noise": _noise_signal(view, ctx),
    }
    raw = sum(WEIGHTS[k] * sig[k] for k in sig)
    scale = min(TOTAL_BUDGET / raw, 1.0) if raw > 0 else 0.0
    record = DriftRecord(**{
        k: round(min(sig[k] * WEIGHTS[k] * scale, CAPS[k]), 6) for k in sig
    })
    triggers = []
    emo = view.get("emotion") or {}
    if sig["emotion"] > 0.2:
        triggers.append("emotion:arousal=%.2f,valence=%.2f,dominance=%.2f" % (
            _num(emo.get("arousal")), _num(emo.get("valence")),
            _num(emo.get("dominance"), 0.5)))
    if sig["relation"] > 0.2 and audience:
        rel = (view.get("relations") or {}).get(audience) or {}
        triggers.append("relation:%s like=%.2f,trust=%.2f,suspicion=%.2f,grudge=%.2f" % (
            audience, _num(rel.get("like")), _num(rel.get("trust")),
            _num(rel.get("suspicion")), _num(rel.get("grudge"))))
    params = (view.get("delivery") or {}).get("params") or {}
    if sig["mask"] > 0:
        triggers.append("mask:honorific=%s,breakout=%s" % (
            params.get("honorific_step", 0),
            (view.get("breakout") or {}).get("state") or "normal"))
    if sig["stress"] > 0.3:
        triggers.append("stress:exposure=%.2f,load=%.2f,A=%.2f" % (
            _num(emo.get("exposure_risk")), _num(emo.get("cognitive_load")),
            _num((view.get("accumulator") or {}).get("A"))))
    if delivery_hit(view):
        triggers.append("performance:%s,p=%.2f" % (
            (view.get("delivery") or {}).get("item"),
            _num((view.get("delivery") or {}).get("probability"))))
    return record, triggers, sig


def delivery_hit(view):
    delivery = view.get("delivery") or {}
    return bool(delivery.get("hit") and delivery.get("item"))


# ---------------------------------------------------------------- Step 3
# Variants: compile IPS's already-computed delivery.params into style instructions.

def generate_variants(fp, drift, audience=None, view=None):
    params = ((view or {}).get("delivery") or {}).get("params") or {}
    tone = str(fp.get("tone_baseline") or "")
    high_n = _num((fp.get("ocean_anchor") or {}).get("OCEAN.N"), 0.5) >= 0.5
    if drift.stress > 0.02 and high_n:
        tonality = "激烈（应激可短暂破格，但不得破角）"
    elif drift.emotion > 0.04:
        tonality = "比基线稍强"
    elif "留余地" in tone:
        tonality = "温和、留余地"
    else:
        tonality = "平稳"
    # IPS already turned the current emotion into concrete performance factors.
    # The engine reports them verbatim instead of re-deriving competing numbers.
    sent = _num(params.get("sentence_length_factor"), 1.0)
    return {
        "tonality": tonality,
        "address": audience or "在座各位",
        "sentence_bias": round(-drift.emotion * 0.8 + drift.mask * 0.3, 4),
        "sentence_length_factor": round(sent, 4),
        "ellipsis_density_factor": round(_num(params.get("ellipsis_density_factor"), 1.0), 4),
        "question_density_factor": round(_num(params.get("question_density_factor"), 1.0), 4),
        "honorific_step": int(_num(params.get("honorific_step"))),
        "pet_phrase_factor": round(_num(params.get("pet_phrase_factor"), 1.0), 4),
        "pause_marks": int(_num(params.get("pause_marks"))),
        "self_corrections": int(_num(params.get("self_corrections"))),
        "vernacular_probability": round(_num(params.get("vernacular_probability")), 4),
        "inversion_probability": round(_num(params.get("inversion_probability")), 4),
        "verbal_flourish_factor": round(_num(params.get("verbal_flourish_factor"), 1.0), 4),
        "performance_item": ((view or {}).get("delivery") or {}).get("item"),
        "lexical_variety": round(1.0 + drift.noise * 0.5, 4),
        "catchphrase_density": round(max(0.0, 0.5 - drift.stress + drift.mask * 0.3), 4),
        "stress_modifier": "破碎化" if drift.stress > 0.02 else "平稳",
    }


# ---------------------------------------------------------------- Step 4

def _fmt(value, default="（未配置）"):
    return str(value) if value not in (None, "") else default


def assemble(fp, variants, drift, view, ctx=None):
    """Three tagged blocks, each filled from stored data or its derivation."""
    ctx = ctx or {}
    lex = _fmt(fp.get("lexicon"))
    ocean_bits = "；".join(f"{k.split('.')[-1]}={v:.2f}" for k, v in
                          sorted((fp.get("ocean_anchor") or {}).items())) or "（未配置）"
    value_bits = "；".join(f"{k}={v:.2f}" for k, v in
                          sorted((fp.get("values_anchor") or {}).items())) or "（未配置）"
    samples = list(fp.get("sample_lines") or [])
    sample_lines = "\n".join(f"  {i + 1}) 「{line}」" for i, line in enumerate(samples[:5])) \
        or "  （无可引用的证据句，不自造样本）"
    allowed = sorted(set(ctx.get("allowed_facts") or []))
    facts = ", ".join(allowed) if allowed else "（本席位当前无可见事实）"
    has_lies = bool(view.get("lie_ledger"))
    perf = variants.get("performance_item")
    perf_line = f"- 本轮表演项：{perf}（IPS 已采样，仅改措辞）" if perf else ""

    anchor = "\n".join([
        "# ============ [ANCHOR] 说话者核心（逐字复用·不可变）============",
        f"你是 {_fmt(fp.get('identity'))}（{_fmt(fp.get('public_identity'))}）。说话方式必须始终如此：",
        f"- 用词偏好（剧本原文）：{lex}",
        f"- 口癖（原文引号内）：「{_fmt(fp.get('catchphrase'))}」",
        f"- 句式（OCEAN 派生）：{_fmt(fp.get('syntax'))}",
        f"- 节奏（OCEAN.N + play_style 派生）：{_fmt(fp.get('rhythm'))}",
        f"- 表演底色（L0 技能/难度）：{'、'.join(fp.get('l0_terms') or []) or '（未配置）'}",
        f"- 价值观（L1 锚）：{value_bits}（决定语气根基，不可违反）",
        f"- 语气基线（人格五维 5 档映射）：{fp.get('tone_baseline')}",
        f"- 人格基线（L1 只读）：{ocean_bits}",
        "- 原文证据句（逐字锚定措辞，非台词、不得照抄）：",
        sample_lines,
        "# ============ [/ANCHOR] ============",
    ])
    rel_kw = relation_terms(view, ctx.get("target_name") or "")
    emo_kw = emotion_terms(view)
    ips_kw = list(fp.get("ips_terms") or []) + delivery_param_terms(variants)
    params_line = (
        f"句长×{variants['sentence_length_factor']:.2f} 省略×{variants['ellipsis_density_factor']:.2f} "
        f"疑问×{variants['question_density_factor']:.2f} 敬语+{variants['honorific_step']} "
        f"口癖×{variants['pet_phrase_factor']:.2f} 停顿{variants['pause_marks']} "
        f"修正{variants['self_corrections']} 口语{variants['vernacular_probability']:.2f} "
        f"倒装{variants['inversion_probability']:.2f}"
    )
    drift_lines = [
        "# ============ [DRIFT] 本轮 20% 调制（IPS delivery 已采样）============",
        f"语气 {variants['tonality']}（情绪 {drift.emotion:.2f}）/ 对 {variants['address']} 称呼按真实关系（关系 {drift.relation:.2f}）",
        f"{params_line} / 丰富×{variants['lexical_variety']:.2f} / {variants['stress_modifier']}",
        f"关系措辞：{'；'.join(rel_kw[:6]) or '（无显著关系偏移）'}",
        f"情绪措辞：{'；'.join(emo_kw[:6]) or '（无显著情绪偏移）'}",
        f"IPS 措辞：{'；'.join(ips_kw[:6]) or '（无越界/表演偏移）'}",
    ]
    if perf_line:
        drift_lines.append(perf_line)
    drift_lines.append("# ============ [/DRIFT] ============")
    drift_block = "\n".join(drift_lines)
    boundary = "\n".join([
        "# ============ [BOUNDARY] 硬边界（违反即回炉重写）============",
        f"1. 不得超出你的信念图：{facts}",
        "2. 不得与谎言账本冲突（见「你说过什么」段）" if has_lies else "2. 本局尚无已出口的谎言，不得凭空声称",
        "3. 漂移只改怎么说，不改说什么；口癖与证据句不得照抄成新事实",
        f"4. 与本锚点的余弦相似度代理须 ≥ {COS_MIN:.2f}",
        "# ============ [/BOUNDARY] ============",
    ])
    prompt = "\n".join([anchor, drift_block, boundary])
    return {"anchor": anchor, "drift": drift_block, "boundary": boundary, "prompt": prompt}


def structure_ratio(parts):
    """ANCHOR share of the style prompt, measured in characters (CJK proxy)."""
    lengths = {k: len(parts.get(k) or "") for k in ("anchor", "drift", "boundary")}
    total = sum(lengths.values())
    return round(lengths["anchor"] / total, 4) if total else 0.0


# ---------------------------------------------------------------- Step 5

def text_vector(text):
    """Character-bigram multiset proxy.  NOT a semantic embedding."""
    body = "".join(str(text or "").split()).lower()
    if len(body) < 2:
        return {}
    vector = {}
    for i in range(len(body) - 1):
        token = body[i:i + 2]
        vector[token] = vector.get(token, 0) + 1
    return vector


def cosine_proxy(left, right):
    a, b = text_vector(left), text_vector(right)
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    dot = sum(a[k] * b[k] for k in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return round(dot / (na * nb), 4) if na and nb else 0.0


def audit(draft_text, fp, drift):
    """Pre/post gate audit.  Cosine is a declared proxy, not an embedding."""
    cos = cosine_proxy(draft_text, anchor_text(fp))
    used = drift.total()
    checks = {
        "not_broken": cos >= COS_MIN,
        "not_robotic": cos <= COS_MAX,
        "taboo_clean": not any(w and str(w) in str(draft_text or "")
                               for w in (fp.get("taboo_words") or [])),
        "budget_ok": used <= TOTAL_BUDGET * 1.1,
    }
    return {
        "anchor_cos_proxy": cos,
        "cosine_kind": "character_bigram_proxy",
        "checks": checks,
        "pass": all(checks.values()),
        "drift_used": used,
        "drift_budget": TOTAL_BUDGET,
        "regenerate_hint": "回归人设锚点" if not checks["not_broken"] else None,
    }


def should_force_baseline(recent_totals):
    """Post gate: three consecutive over-budget rounds force one calm round."""
    tail = list(recent_totals or [])[-POST_GATE_ROUNDS:]
    return len(tail) == POST_GATE_ROUNDS and all(
        _num(v) > POST_GATE_THRESHOLD for v in tail
    )


def render(view, ctx=None):
    """Full pipeline.  Returns prompt segments, record and audit metadata."""
    ctx = ctx or {}
    fp = extract_fingerprint(view, ctx)
    drift, triggers, signals = allocate_drift(view, ctx)
    forced = bool(ctx.get("force_baseline")) or should_force_baseline(ctx.get("recent_drift_totals"))
    if forced:
        drift = DriftRecord()
        signals = {k: 0.0 for k in SIGNALS}
        triggers = list(triggers or []) + ["post_gate:forced_baseline"]
    variants = generate_variants(fp, drift, ctx.get("target_name"), view)
    parts = assemble(fp, variants, drift, view, ctx)
    parts["structure_ratio"] = structure_ratio(parts)
    parts["structure_ok"] = STRUCTURE_MIN <= parts["structure_ratio"] <= STRUCTURE_MAX
    parts["drift_record"] = drift.as_dict()
    parts["triggers"] = triggers
    parts["signals"] = {k: round(v, 4) for k, v in signals.items()}
    parts["variants"] = variants
    parts["forced_baseline"] = forced
    parts["missing"] = list(fp.get("missing") or [])
    # Carry the exact fingerprint used for this render so downstream compilers
    # validate against the same identity/anchor (not a re-derived variant).
    parts["fingerprint"] = fp
    return parts


SIGNALS = tuple(WEIGHTS)
