"""Script skeleton jig: summarise first, complete from evidence, then normalise.

The jig is a fixed slot template.  An LLM may summarise into the template, but
it may not introduce people, facts, times or clue links that are absent from
the source.  Every accepted string is checked back against the source text;
everything else is recorded as drift and discarded.  The completed skeleton is
projected back into the Ontology before the normal ingest repair stage runs.
"""

import copy
import json
import re

from . import llm

SKELETON_VERSION = "1.0"
SLOTS = (
    "identity", "world", "participants", "truth", "timeline",
    "clues", "locks", "endings", "evaluation",
)
OVERLAY_SLOTS = {
    ("identity", "brief"), ("truth", "summary"),
    ("endings", "correct"), ("endings", "incorrect"),
}


def empty_skeleton():
    skel = {
        "skeleton_version": SKELETON_VERSION,
        "identity": {"script_id": None, "title": None, "brief": None, "source_kind": None},
        "world": {},
        "participants": [],
        "truth": {"summary": None, "facts": []},
        "timeline": {"events": []},
        "clues": [],
        "locks": {"items": []},
        "endings": {"correct": None, "incorrect": None},
        "evaluation": {
            "killer": None, "victim": None,
            "required_facts": [], "role_kind": None,
        },
        "gaps": [],
        "completions": [],
        "llm": {"provider": "template", "accepted": [], "rejected": []},
    }
    return skel


def _text(value):
    return str(value or "").strip()


def _gap(skel, slot, reason, optional=False):
    skel["gaps"].append({"slot": slot, "reason": reason, "optional": optional})


def project(onto, detection=None):
    """Deterministically project an Ontology into the fixed slot template."""
    skel = empty_skeleton()
    detection = detection or {}
    skel["identity"].update({
        "script_id": _text(onto.get("script_id")) or None,
        "title": _text(onto.get("title") or onto.get("script_id")) or None,
        "brief": _text(onto.get("brief")) or None,
        "source_kind": detection.get("kind"),
    })
    skel["world"] = dict(onto.get("world") or {})
    for char in onto.get("characters") or []:
        skel["participants"].append({
            "id": _text(char.get("id")) or None,
            "name": _text(char.get("name")) or None,
            "public_identity": _text(char.get("public_identity") or char.get("role")) or None,
            "is_victim": bool(char.get("is_victim")),
            "is_suspect": not bool(char.get("is_victim")),
        })

    facts = onto.get("facts") or []
    truth_facts = [f for f in facts if f.get("visibility") == "truth" or f.get("type") == "truth"]
    skel["truth"]["facts"] = [
        {"id": f.get("id"), "content": _text(f.get("content"))}
        for f in facts if _text(f.get("content"))
    ][:60]
    truth_lines = [_text(t) for t in (onto.get("truth_graph") or []) if _text(t)]
    skel["truth"]["summary"] = truth_lines[0] if truth_lines else (
        _text(truth_facts[0].get("content")) if truth_facts else None
    )
    skel["timeline"]["events"] = [
        _text(t) for t in (onto.get("timeline_true") or []) if _text(t)
    ]
    skel["clues"] = [
        {
            "id": _text(c.get("id")) or None,
            "content": _text(c.get("content")),
            "stage": int(c.get("stage") or 0),
            "reveals": list(c.get("reveals_fact_ids") or []),
        }
        for c in (onto.get("clues") or [])
    ]
    skel["locks"]["items"] = [_text(x) for x in (onto.get("plot_locks") or []) if _text(x)]
    endings = list(onto.get("endings") or [])
    skel["endings"] = {
        "correct": _text(endings[0]) if endings else None,
        "incorrect": _text(endings[1]) if len(endings) > 1 else None,
    }
    evaluation = onto.get("evaluation") or {}
    skel["evaluation"] = {
        "killer": _text(evaluation.get("killer_id")) or None,
        "victim": _text(evaluation.get("victim_name")) or None,
        "required_facts": list(evaluation.get("required_fact_ids") or []),
        "role_kind": _text(evaluation.get("role_kind")) or None,
    }

    if not skel["truth"]["summary"]:
        _gap(skel, "truth.summary", "no truth text in source")
    if len(skel["participants"]) < 2:
        _gap(skel, "participants", "fewer than two roles in source")
    if not skel["clues"]:
        _gap(skel, "clues", "no clues in source")
    if not skel["endings"]["correct"]:
        _gap(skel, "endings.correct", "no ending in source")
    if not skel["evaluation"]["killer"]:
        _gap(skel, "evaluation.killer", "no declared culprit in source")
    if not skel["timeline"]["events"]:
        _gap(skel, "timeline.events", "no structured timeline in source", optional=True)
    return skel


def source_text(detection, limit=120000):
    """Read the raw source text used for LLM summarisation and drift checks."""
    path = detection.get("file") or detection.get("path")
    if not path:
        return ""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()[:limit]


def _norm(text):
    return re.sub(r"\s+", "", str(text or ""))


def _sentences(text):
    parts = re.split(r"[。！？；\n]+", str(text or ""))
    return [p.strip() for p in parts if len(p.strip()) >= 4]


def _source_backed(text, raw_source):
    sentences = _sentences(text)
    if not sentences:
        return False
    source = _norm(raw_source)
    return all(_norm(s) in source for s in sentences)


def build_prompt(skel, raw_source):
    return "\n".join([
        "# 任务",
        "把下面的剧本原文总结成固定骨架。只允许输出 JSON，不要解释。",
        "# 骨架槽位（不得新增字段）",
        json.dumps({
            "identity": ["brief"],
            "truth": ["summary"],
            "endings": ["correct", "incorrect"],
        }, ensure_ascii=False),
        "# 硬性约束",
        "- summary/brief 必须逐句摘自原文，不得新增人物、时间、事实或结论。",
        "- 原文没有的槽位填 null；不得猜测。",
        "- 不要输出人物、线索或凶手字段，这些由确定性夹具生成。",
        "# 原文",
        raw_source[:60000],
    ])


def _extract_json(raw):
    if not isinstance(raw, str) or not raw.strip():
        return None
    body = raw.strip()
    if body.startswith("```"):
        body = re.sub(r"^```(?:json)?\s*", "", body)
        body = re.sub(r"\s*```$", "", body)
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(body[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def validate(candidate, base, raw_source):
    """Contain LLM drift: unknown slots and unsourced strings are discarded."""
    clean = copy.deepcopy(base)
    drift = []
    if not candidate:
        return clean, drift
    for slot in candidate:
        if slot not in SLOTS and slot not in {"gaps", "completions", "llm"}:
            drift.append({"slot": slot, "reason": "unknown_slot"})
    allowed_ids = {p["id"] for p in base["participants"] if p.get("id")}
    allowed_clues = {c["id"] for c in base["clues"] if c.get("id")}
    allowed_facts = {f["id"] for f in base["truth"]["facts"] if f.get("id")}

    participants = candidate.get("participants")
    if isinstance(participants, list):
        for item in participants:
            if not isinstance(item, dict) or item.get("id") not in allowed_ids:
                drift.append({"slot": "participants", "reason": "unknown_character"})
                continue
            target = next(p for p in clean["participants"] if p["id"] == item["id"])
            for key in ("name", "public_identity"):
                value = _text(item.get(key))
                if value and _source_backed(value, raw_source):
                    target[key] = value
                elif value:
                    drift.append({"slot": f"participants.{key}", "reason": "not_in_source"})

    clues = candidate.get("clues")
    if isinstance(clues, list):
        for item in clues:
            if not isinstance(item, dict) or item.get("id") not in allowed_clues:
                drift.append({"slot": "clues", "reason": "unknown_clue"})
                continue
            target = next(c for c in clean["clues"] if c["id"] == item["id"])
            reveals = [fid for fid in (item.get("reveals") or []) if fid in allowed_facts]
            if len(reveals) != len(item.get("reveals") or []):
                drift.append({"slot": "clues.reveals", "reason": "unknown_fact"})
            if reveals:
                target["reveals"] = reveals

    for section, key in OVERLAY_SLOTS:
        value = _text((candidate.get(section) or {}).get(key)) if isinstance(candidate.get(section), dict) else ""
        if not value:
            continue
        if _source_backed(value, raw_source):
            clean[section][key] = value[:600]
        else:
            drift.append({"slot": f"{section}.{key}", "reason": "not_in_source"})
    return clean, drift


def summarize(skel, raw_source, llm_name=None):
    """LLM summarisation is optional; template/offline keeps the deterministic skeleton."""
    try:
        provider = llm.build_provider(llm_name or "auto")
    except llm.LLMError as exc:
        meta = {"provider": "template", "accepted": [], "rejected": [{"reason": str(exc)}]}
        return skel, meta
    info = {"provider": provider.name, "accepted": [], "rejected": []}
    if isinstance(provider, llm.TemplateProvider):
        return skel, info
    try:
        raw = provider.generate(build_prompt(skel, raw_source))
    except llm.LLMError as exc:
        info["rejected"].append({"reason": str(exc)})
        return skel, info
    candidate = _extract_json(raw)
    if not candidate:
        info["rejected"].append({"reason": "ERR_SKELETON_NOT_JSON"})
        return skel, info
    clean, drift = validate(candidate, skel, raw_source)
    info["accepted"] = sorted(
        f"{section}.{key}" for section, key in OVERLAY_SLOTS
        if _text((candidate.get(section) or {}).get(key))
        and clean[section].get(key) == (candidate.get(section) or {}).get(key)
    )
    info["rejected"] = drift
    return clean, info


def complete(skel, onto):
    """Fill slots only from evidence already present in the source Ontology."""
    skel = copy.deepcopy(skel)
    ledger = []
    chars = onto.get("characters") or []
    if not skel["truth"]["summary"]:
        truth = [f for f in skel["truth"]["facts"] if f.get("content")]
        if truth:
            skel["truth"]["summary"] = truth[0]["content"]
            ledger.append({"rule": "S1_truth_summary_from_fact", "field": "truth.summary",
                           "basis": "first fact from source", "value": truth[0]["id"]})
    if not skel["evaluation"]["killer"]:
        declared = [c["id"] for c in chars if c.get("is_killer") in (True, "true", "True", 1)]
        if declared:
            skel["evaluation"]["killer"] = declared[0]
            ledger.append({"rule": "S2_killer_from_character", "field": "evaluation.killer",
                           "basis": "source character explicitly marked is_killer", "value": declared[0]})
    if not skel["evaluation"]["victim"]:
        victim = next((c.get("name") for c in chars if c.get("is_victim")), None)
        if victim:
            skel["evaluation"]["victim"] = victim
            ledger.append({"rule": "S3_victim_from_character", "field": "evaluation.victim",
                           "basis": "source character explicitly marked is_victim", "value": victim})
    allowed = {f["id"] for f in skel["truth"]["facts"] if f.get("id")}
    primary = next((f["id"] for f in skel["truth"]["facts"]
                    if f.get("content") and _text(f["content"]) == _text(skel["truth"]["summary"])), None)
    if not primary and skel["truth"]["facts"]:
        primary = skel["truth"]["facts"][0]["id"]
    for clue in skel["clues"]:
        links = [fid for fid in clue.get("reveals") or [] if fid in allowed]
        if not links and primary:
            clue["reveals"] = [primary]
            clue["hint_only"] = True
            ledger.append({"rule": "S4_clue_hint_only", "field": f"clues[{clue['id']}].reveals",
                           "basis": "no narrower source evidence; hint only", "value": [primary]})
        else:
            clue["reveals"] = links
    skel["completions"] = ledger
    skel["gaps"] = [
        g for g in skel["gaps"]
        if not (
            (g["slot"] == "truth.summary" and skel["truth"]["summary"])
            or (g["slot"] == "evaluation.killer" and skel["evaluation"]["killer"])
            or (g["slot"] == "evaluation.victim" and skel["evaluation"]["victim"])
        )
    ]
    return skel, ledger


def apply(skel, onto):
    """Project the completed skeleton back into the canonical Ontology."""
    onto = copy.deepcopy(onto)
    for key in ("script_id", "title", "brief"):
        value = skel["identity"].get(key)
        if value:
            onto[key] = value
    for key, value in (skel["world"] or {}).items():
        if value not in (None, "", []):
            onto.setdefault("world", {})[key] = value
    by_id = {c.get("id"): c for c in onto.get("characters") or []}
    for item in skel["participants"]:
        target = by_id.get(item.get("id"))
        if target and item.get("public_identity"):
            target["public_identity"] = item["public_identity"]
    clue_by_id = {c.get("id"): c for c in onto.get("clues") or []}
    for item in skel["clues"]:
        target = clue_by_id.get(item.get("id"))
        if target and item.get("reveals"):
            target["reveals_fact_ids"] = item["reveals"]
    evaluation = onto.setdefault("evaluation", {})
    for src, dst in (("killer", "killer_id"), ("victim", "victim_name"),
                     ("required_facts", "required_fact_ids"), ("role_kind", "role_kind")):
        if skel["evaluation"].get(src):
            evaluation[dst] = skel["evaluation"][src]
    if skel["endings"].get("correct"):
        onto["endings"] = [x for x in (skel["endings"]["correct"], skel["endings"]["incorrect"]) if x]
    return onto
