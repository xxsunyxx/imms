"""Persona -> speaking-style prompt engine (80% anchor / 20% drift).

Adapted from the supplied reference design to the data IMMS actually owns:

  * Fingerprint reads L1 canon + L2 assigned only.  Fields the scripts do not
    carry (syntax / rhythm / catchphrase / taboo_words / sample_lines) stay
    explicitly empty.  Nothing is invented to fill a prompt slot.
  * Drift signals read L3 emotion / relations + L2 mask + a deterministic seed
    proxy for temperature (IMMS has no sampling temperature).
  * There is no embedder and no numpy dependency, so cosine is a documented
    character-bigram proxy, never presented as semantic similarity.
  * [ANCHOR] / [DRIFT] / [BOUNDARY] are separate blocks.  The structure ratio
    is measured, not asserted: when sample_lines are absent the anchor share
    will be below the 0.78-0.82 target, and the engine reports that honestly.

Every number here is either stored data or a deterministic derivation from
stored data.  Nothing in this module writes back to L1.
"""

from dataclasses import dataclass, asdict
import math
import random

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

def derive_tone(l1):
    """Deterministic tone baseline from stored OCEAN values.

    This is a derivation, not an invention: each clause names the dimension
    and direction that produced it.  Missing dimensions are skipped.
    """
    clauses = []
    for key, label, high, low in (
        ("OCEAN.O", "用词", "较宽、愿意尝试新说法", "偏保守、用熟悉的词"),
        ("OCEAN.C", "结构", "先结论后展开", "随想式展开"),
        ("OCEAN.E", "热度", "不回避表态", "不主动抢话"),
        ("OCEAN.A", "温度", "给对方留余地", "就事论事"),
        ("OCEAN.N", "稳定度", "容易起波澜", "不易激动"),
    ):
        value = l1.get(key)
        if isinstance(value, (int, float)):
            clauses.append(f"{label}{high}" if value >= 0.5 else f"{label}{low}")
    return "；".join(clauses) or "（人格基线未配置，不推导语气）"


def extract_fingerprint(view, ctx=None):
    """Read-only fingerprint from L1/L2 text slots and values.

    Returns keys with ``None`` where the script carries no evidence.  The
    caller renders None as an explicit '（未配置）' placeholder.  Canon evidence
    quotes (verbatim script text) are accepted as anchors but are NOT presented
    as spoken lines -- the schema has no verbatim-dialogue field.
    """
    ctx = ctx or {}
    l1 = dict(view.get("l1") or {})
    lexicon = l1.get("lexicon") or l1.get("speaking_style")
    ocean = {k: l1.get(k) for k in (
        "OCEAN.O", "OCEAN.C", "OCEAN.E", "OCEAN.A", "OCEAN.N"
    ) if isinstance(l1.get(k), (int, float))}
    honesty = l1.get("values.honesty")
    evidence_quotes = [str(q) for q in (
        ctx.get("evidence_quotes") or view.get("evidence_quotes") or []
    ) if str(q or "").strip()][:5]
    return {
        "identity": view.get("name") or view.get("character_id"),
        "public_identity": view.get("public_identity")
        or l1.get("public_identity") or l1.get("L2.public_identity"),
        "lexicon": lexicon,          # verbatim script_text slot, may be None
        "syntax": None,              # no such field in the current schema
        "rhythm": None,
        "catchphrase": None,
        "taboo_words": [],
        "values_anchor": {"values.honesty": honesty} if isinstance(honesty, (int, float)) else {},
        "ocean_anchor": ocean,
        "tone_baseline": derive_tone(l1),
        "sample_lines": [],          # no verbatim lines stored -> never fabricate
        "evidence_quotes": evidence_quotes,
        "missing": ["syntax", "rhythm", "catchphrase", "sample_lines"],
    }


def anchor_text(fp):
    """Text used as the anchor vector source (deterministic for a seat)."""
    bits = [str(fp.get("identity") or ""), str(fp.get("public_identity") or ""),
            str(fp.get("lexicon") or ""), str(fp.get("tone_baseline") or "")]
    bits.extend(f"{k}={v:.2f}" for k, v in sorted((fp.get("ocean_anchor") or {}).items()))
    for k, v in sorted((fp.get("values_anchor") or {}).items()):
        bits.append(f"{k}={v:.2f}")
    return " ".join(bits)


# ---------------------------------------------------------------- Step 2

def _emotion_signal(emotion):
    a = _num(emotion.get("arousal"))
    v = _num(emotion.get("valence"))
    s = _num(emotion.get("stress"))
    # Reference used PAD.  IMMS stores arousal/valence/stress, so dominance is
    # proxied by (1 - stress); the substitution is stated in the ledger.
    return _clip(0.5 * abs(v) + 0.3 * a + 0.2 * (1.0 - s))


def _relation_signal(view, audience):
    rel = (view.get("relations") or {}).get(audience) or {}
    parts = [_num(rel.get(k)) for k in ("like", "trust", "suspicion")]
    return _clip(sum(abs(p) for p in parts) / max(1, len(parts)))


def _mask_signal(view):
    mask = (view.get("l0") or {}).get("mask") or view.get("l1", {}).get("L2.mask")
    signal = 0.5 if mask else 0.0
    facets = view.get("facets") or {}
    item = (facets.get("facets") or {}).get(facets.get("active_facet_id")) or {}
    if str(item.get("memory_scope") or "").startswith("isolated:"):
        signal = _clip(signal + 0.3)
    return signal


def _stress_signal(view):
    stress = _num((view.get("emotion") or {}).get("stress"))
    acc = view.get("accumulator") or {}
    burst = _num(acc.get("A"))
    return _clip(stress + 0.5 * burst)


def _noise_signal(session_id, seat_id, round_no, seed):
    """Deterministic stand-in for sampling temperature."""
    if not seat_id:
        return 0.0
    key = f"{session_id}|{seat_id}|{round_no}|speech_style:noise"
    return random.Random(f"{seed}|{key}").random()


def allocate_drift(view, ctx=None):
    """Allocate the 0.20 drift budget; no signal is ever fabricated."""
    ctx = ctx or {}
    audience = ctx.get("target_name") or ctx.get("audience") or ""
    sig = {
        "emotion": _emotion_signal(view.get("emotion") or {}),
        "relation": _relation_signal(view, audience) if audience else 0.0,
        "mask": _mask_signal(view),
        "stress": _stress_signal(view),
        "noise": _noise_signal(
            ctx.get("session_id") or "", ctx.get("seat_id") or "",
            ctx.get("round_no") or 0, ctx.get("seed") or 0,
        ),
    }
    raw = sum(WEIGHTS[k] * sig[k] for k in sig)
    scale = min(TOTAL_BUDGET / raw, 1.0) if raw > 0 else 0.0
    record = DriftRecord(**{
        k: round(min(sig[k] * WEIGHTS[k] * scale, CAPS[k]), 6) for k in sig
    })
    triggers = []
    if sig["emotion"] > 0.2:
        emo = view.get("emotion") or {}
        triggers.append("emotion:arousal=%.2f,valence=%.2f,stress=%.2f" % (
            _num(emo.get("arousal")), _num(emo.get("valence")), _num(emo.get("stress"))))
    if sig["relation"] > 0.2 and audience:
        rel = (view.get("relations") or {}).get(audience) or {}
        triggers.append("relation:%s like=%.2f,trust=%.2f,suspicion=%.2f" % (
            audience, _num(rel.get("like")), _num(rel.get("trust")),
            _num(rel.get("suspicion"))))
    if sig["mask"] > 0:
        triggers.append("mask:activated")
    if sig["stress"] > 0.3:
        triggers.append("stress:accumulator_A=%.2f" % _num((view.get("accumulator") or {}).get("A")))
    return record, triggers, sig


# ---------------------------------------------------------------- Step 3

def generate_variants(fp, drift, audience=None):
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
    return {
        "tonality": tonality,
        "address": audience or "在座各位",
        "sentence_bias": round(-drift.emotion * 0.8 + drift.mask * 0.3, 4),
        "lexical_variety": round(1.0 + drift.noise * 0.5, 4),
        "catchphrase_density": round(max(0.0, 0.5 - drift.stress + drift.mask * 0.3), 4),
        "stress_modifier": "破碎化" if drift.stress > 0.02 else "平稳",
    }


# ---------------------------------------------------------------- Step 4

def _fmt(value, default="（未配置）"):
    return str(value) if value not in (None, "") else default


def assemble(fp, variants, drift, view, ctx=None):
    """Three tagged blocks.  Only stored data + bounded derivations appear."""
    ctx = ctx or {}
    lex = _fmt(fp.get("lexicon"))
    ocean_bits = "；".join(f"{k.split('.')[-1]}={v:.2f}" for k, v in
                          sorted((fp.get("ocean_anchor") or {}).items())) or "（未配置）"
    value_bits = "；".join(f"{k}={v:.2f}" for k, v in
                          sorted((fp.get("values_anchor") or {}).items())) or "（未配置）"
    samples = list(fp.get("sample_lines") or [])
    sample_lines = "\n".join(f"  {i + 1}) {line}" for i, line in enumerate(samples[:3])) \
        or "  （无原文台词样本，不得自造）"
    quotes = list(fp.get("evidence_quotes") or [])
    quote_lines = "\n".join(f"  - {q}" for q in quotes) \
        or "  （本席位暂无可引用的原文证据句）"
    allowed = sorted(set(ctx.get("allowed_facts") or []))
    facts = ", ".join(allowed) if allowed else "（本席位当前无可见事实）"
    has_lies = bool(view.get("lie_ledger"))

    anchor = "\n".join([
        "# ============ [ANCHOR] 说话者核心（逐字复用·不可变）============",
        f"你是 {_fmt(fp.get('identity'))}（{_fmt(fp.get('public_identity'))}）。说话方式必须始终如此：",
        f"- 用词偏好：{lex}",
        f"- 句式：{_fmt(fp.get('syntax'))}",
        f"- 节奏：{_fmt(fp.get('rhythm'))}",
        f"- 口癖：{_fmt(fp.get('catchphrase'))}",
        f"- 禁用词：{'、'.join(fp.get('taboo_words') or []) or '（未配置）'}（出现即破角）",
        f"- 价值观：{value_bits}（决定语气根基，不可违反）",
        f"- 台词样本（模仿神韵，不得照抄）：",
        sample_lines,
        f"- 原文证据句（锚定用词，非台词、不得照抄）：",
        quote_lines,
        f"- 语气基线（由人格基线派生）：{fp.get('tone_baseline')}",
        f"- 人格基线：{ocean_bits}",
        "# ============ [/ANCHOR] ============",
    ])
    drift_block = "\n".join([
        "# ============ [DRIFT] 本段允许的 20% 调制（受预算约束）============",
        f"- 语气：{variants['tonality']}（漂移 {drift.emotion:.2f}，仅在 [克制, 激烈] 间移动）",
        f"- 对 {variants['address']} 的称呼：按真实关系选择，可向对外人设偏移",
        f"- 句长倾向：{variants['sentence_bias']:+.2f}（负=更短促，正=更绵长）",
        f"- 词汇丰富度：×{variants['lexical_variety']:.2f}（1.0 为基准）",
        f"- 口癖密度：{variants['catchphrase_density']:.2f}（0=压制，1=正常）",
        f"- 应激修饰：{variants['stress_modifier']}（不得破角）",
        "# ============ [/DRIFT] ============",
    ])
    boundary = "\n".join([
        "# ============ [BOUNDARY] 硬边界（违反即回炉重写）============",
        f"1. 不得超出你的信念图：{facts}",
        "2. 不得与谎言账本冲突（见「你说过什么」段）" if has_lies else "2. 本局尚无已出口的谎言，不得凭空声称",
        "3. 禁用词命中即视为破角，回炉重写",
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
    variants = generate_variants(fp, drift, ctx.get("target_name"))
    parts = assemble(fp, variants, drift, view, ctx)
    parts["structure_ratio"] = structure_ratio(parts)
    parts["structure_ok"] = STRUCTURE_MIN <= parts["structure_ratio"] <= STRUCTURE_MAX
    parts["drift_record"] = drift.as_dict()
    parts["triggers"] = triggers
    parts["signals"] = {k: round(v, 4) for k, v in signals.items()}
    parts["variants"] = variants
    parts["forced_baseline"] = forced
    parts["missing"] = list(fp.get("missing") or [])
    if not fp.get("sample_lines"):
        parts["missing"] = parts["missing"] + ["sample_lines"]
    return parts


SIGNALS = tuple(WEIGHTS)
