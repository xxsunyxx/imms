"""LLM compiler for the speech-style prompt (keyword payload -> style directive).

The deterministic engine (``speech_style.render``) produces keywords, numeric
drift and verbatim anchors.  This stage asks an LLM to turn that payload into a
coherent, first-person speaking-style prompt.  It is a *wording compiler*, not
an author: the output is accepted only if every verbatim anchor, every numeric
parameter and the three-block contract survive intact.  Any failure falls back
to the deterministic prompt so a bad model output can never change facts,
intent or numbers.
"""

import hashlib
import json
import re

from . import llm, speech_style

VERSION = "style-compiler-v1"
MAX_CHARS = 2400
MIN_CHARS = 80

# A compiled directive is only prose.  These patterns indicate the model tried
# to smuggle in a fact id or a quoted line it invented.
_FACT_ID_RE = re.compile(r"\bf_[A-Za-z0-9_-]+\b")
_QUOTE_RE = re.compile(r"[「『\"]([^」』\"]{1,120})[」』\"]")


class CompileError(RuntimeError):
    pass


def build_request(parts, fp, ctx=None):
    """Return the compiler prompt.  Only the deterministic payload is sent."""
    ctx = ctx or {}
    body = json.dumps({
        "anchor": parts.get("anchor"),
        "drift": parts.get("drift"),
        "boundary": parts.get("boundary"),
        "verbatim_anchors": {
            "identity": fp.get("identity"),
            "public_identity": fp.get("public_identity"),
            "lexicon": fp.get("lexicon"),
            "catchphrase": fp.get("catchphrase"),
            "sample_lines": fp.get("sample_lines") or [],
        },
        "numeric_anchors": fp.get("ocean_anchor") or {},
        "missing": fp.get("missing") or [],
    }, ensure_ascii=False, indent=2)
    return "\n".join([
        "你是说话风格编译器。把下面的关键词载荷改写为一段连贯的第二人称说话风格指令。",
        "硬性要求：",
        "1. 保留 [ANCHOR] / [DRIFT] / [BOUNDARY] 三个标签及其顺序，每块可重排措辞但不删标签。",
        "2. verbatim_anchors 里的 identity、public_identity、lexicon、catchphrase、sample_lines 必须逐字出现，不得改写或新增引号台词。",
        "3. numeric_anchors 的数值与 [DRIFT] 的全部系数必须原样保留，不得增删数字。",
        "4. 不得出现 f_ 开头的事实编号；不得新增人物、事件、时间或证据；missing 字段缺什么就保持缺，不得补写。",
        "5. 只输出 JSON：{\"anchor\": \"...\", \"drift\": \"...\", \"boundary\": \"...\"}，不要解释、不要代码块。",
        "",
        body,
    ])


def parse_compiled(text):
    """Parse the strict JSON returned by the compiler."""
    if not isinstance(text, str) or not text.strip():
        raise CompileError("ERR_COMPILE_EMPTY")
    body = text.strip()
    if body.startswith("```"):
        body = re.sub(r"^```(?:json)?\s*", "", body)
        body = re.sub(r"\s*```$", "", body)
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        raise CompileError("ERR_COMPILE_NOT_JSON")
    try:
        data = json.loads(body[start:end + 1])
    except (ValueError, TypeError) as exc:
        raise CompileError("ERR_COMPILE_BAD_JSON") from exc
    if not isinstance(data, dict):
        raise CompileError("ERR_COMPILE_BAD_JSON")
    for key in ("anchor", "drift", "boundary"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise CompileError(f"ERR_COMPILE_MISSING_{key.upper()}")
    return {k: data[k].strip() for k in ("anchor", "drift", "boundary")}


def _tokens(parts, fp):
    """Verbatim strings and numbers that must survive compilation."""
    verbatim = []
    for key in ("identity", "public_identity", "catchphrase"):
        value = fp.get(key)
        if value:
            verbatim.append(str(value))
    for line in fp.get("sample_lines") or []:
        if line:
            verbatim.append(str(line))
    lex = fp.get("lexicon")
    if lex:
        verbatim.append(str(lex))
    numbers = []
    for block in ("anchor", "drift", "boundary"):
        numbers.extend(re.findall(r"\d+\.\d+|\d+", parts.get(block) or ""))
    allowed_quotes = set()
    # Every deterministic block is a source of legitimate wording quotes
    # (e.g. the lexicon outputs 「您」/「你」 inside DRIFT).  Only quotes the
    # compiler *adds* beyond these blocks count as invented dialogue.
    for text in (*verbatim, *(parts.get(k) or "" for k in ("anchor", "drift", "boundary"))):
        allowed_quotes.update(m.strip() for m in _QUOTE_RE.findall(str(text)))
    return verbatim, numbers, allowed_quotes


def validate(compiled, parts, fp, ctx=None):
    """Fail closed when the compiler drops anchors, edits numbers or invents text."""
    ctx = ctx or {}
    text = "\n".join(compiled.values())
    if not (MIN_CHARS <= len(text) <= MAX_CHARS):
        raise CompileError("ERR_COMPILE_LENGTH")
    for tag in ("[ANCHOR]", "[/ANCHOR]", "[DRIFT]", "[/DRIFT]", "[BOUNDARY]", "[/BOUNDARY]"):
        if tag not in text:
            raise CompileError("ERR_COMPILE_BLOCK")
    verbatim, numbers, allowed_quotes = _tokens(parts, fp)
    for token in verbatim:
        if token not in text:
            raise CompileError("ERR_COMPILE_DROPPED_ANCHOR")
    for number in numbers:
        if number not in text:
            raise CompileError("ERR_COMPILE_CHANGED_NUMBER")
    for quote in _QUOTE_RE.findall(text):
        if quote.strip() and quote.strip() not in allowed_quotes:
            raise CompileError("ERR_COMPILE_INVENTED_QUOTE")
    allowed_facts = set(ctx.get("allowed_facts") or [])
    if any(fid not in allowed_facts for fid in _FACT_ID_RE.findall(text)):
        raise CompileError("ERR_COMPILE_UNKNOWN_FACT")
    return True


def compiled_hash(parts, fp):
    raw = json.dumps(
        {"parts": parts, "fp": fp, "version": VERSION}, ensure_ascii=False, sort_keys=True
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def compile_prompt(parts, fp, ctx=None, provider=None, cache=None):
    """Compile the keyword payload into a style prompt, with a hard fallback.

    ``cache`` is an optional dict-like object keyed by ``compiled_hash``; callers
    that want persistence pass a DB-backed mapping (see db.style_compile_*).
    Returns a dict with ``parts`` (compiled or fallback) and a ``report``.
    """
    ctx = ctx or {}
    digest = compiled_hash(parts, fp)
    report = {
        "status": "skipped_template", "version": VERSION, "hash": digest,
        "provider": getattr(provider, "name", None), "reason": None,
    }
    if provider is None or getattr(provider, "name", None) == "template":
        report["reason"] = "template provider does not consume prompts"
        return {"parts": parts, "report": report}

    if cache is not None:
        hit = cache.get(digest)
        if hit:
            compiled = parse_compiled(hit)
            try:
                validate(compiled, parts, fp, ctx)
                report.update({"status": "cache_hit", "reason": None})
                return {"parts": _merge(parts, compiled), "report": report}
            except CompileError as exc:
                report.update({"status": "cache_invalid", "reason": str(exc)})
                cache.pop(digest, None)

    try:
        raw = provider.generate(build_request(parts, fp, ctx))
        compiled = parse_compiled(raw)
        validate(compiled, parts, fp, ctx)
    except (llm.LLMError, CompileError) as exc:
        report.update({"status": "fallback", "reason": str(exc)})
        return {"parts": parts, "report": report}

    if cache is not None:
        cache[digest] = json.dumps(compiled, ensure_ascii=False)
    report.update({"status": "compiled", "reason": None})
    return {"parts": _merge(parts, compiled), "report": report}


def _merge(parts, compiled):
    """Return a copy of parts with compiled prose replacing the keyword dump."""
    out = dict(parts)
    out.update({k: compiled[k] for k in ("anchor", "drift", "boundary")})
    out["prompt"] = "\n".join([out["anchor"], "", out["drift"], "", out["boundary"]])
    out["compiled"] = True
    out["structure_ratio"] = speech_style.structure_ratio(out)
    return out
