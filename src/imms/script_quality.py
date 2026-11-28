"""Quality tiers and source labels for the stored script inventory.

G14 / #35.  Tiering is descriptive: every label and gap comes from fields that
actually exist in the stored Ontology or corpus manifest.  Missing evidence is
reported, never filled in, and the original manifest/source bytes are not
modified -- only a derived quality manifest is written.
"""

import json
from collections import Counter
from pathlib import Path

from . import db

TIERS = ("explicit_full", "explicit_partial", "explicit_minimal",
         "narrative_only", "insufficient")
SOURCE_TYPES = ("boxed_md", "yaml_explicit", "rpg_json", "config", "unknown")


def _paths(onto, manifest_entry=None):
    values = list(onto.get("source_files") or [])
    if manifest_entry:
        values.extend(manifest_entry.get("raw_files") or [])
        values.append(manifest_entry.get("source_path") or "")
    return [str(value or "").replace("\\", "/") for value in values if value]


def source_type(onto, manifest_entry=None):
    paths = _paths(onto, manifest_entry)
    joined = " ".join(paths)
    kind = str((manifest_entry or {}).get("source_kind") or "").lower()
    if "盒装本" in joined or "mystery-scripts-100" in joined:
        return "boxed_md"
    if "archetypes" in joined or "config/" in joined:
        return "config"
    if any(path.endswith((".yaml", ".yml")) for path in paths):
        return "yaml_explicit"
    if "rpg" in kind or "corpus/scripts" in joined or "rpg_" in joined:
        return "rpg_json"
    return "unknown"


def classify(onto, manifest_entry=None):
    """Derive a tier, source type and concrete gaps from stored evidence."""
    onto = onto or {}
    evaluation = onto.get("evaluation") or {}
    char_ids = {c.get("id") for c in onto.get("characters", []) if isinstance(c, dict)}
    killer = evaluation.get("killer_id")
    has_killer = bool(killer and killer in char_ids)
    clues = [c for c in onto.get("clues") or [] if isinstance(c, dict)]
    if not clues:
        clues = [line for line in onto.get("clue_pool") or [] if str(line).strip()]
    facts = onto.get("facts") or []
    timeline = onto.get("timeline_true") or []
    endings = onto.get("endings") or []
    clue_map = onto.get("clue_fact_map") or {}

    gaps = []
    if not has_killer:
        gaps.append("no_declared_killer")
    if not clues:
        gaps.append("no_clues")
    if not facts:
        gaps.append("no_structured_facts")
    if not timeline:
        gaps.append("no_timeline_true")
    if not endings:
        gaps.append("no_endings")
    if has_killer and not clue_map and not evaluation.get("required_fact_ids"):
        gaps.append("no_fact_to_clue_map")

    if not has_killer and (timeline or endings):
        tier = "narrative_only"
    elif not has_killer or not clues or not facts:
        tier = "insufficient"
    elif timeline and evaluation.get("required_fact_ids"):
        tier = "explicit_full"
    elif timeline or evaluation.get("required_fact_ids"):
        tier = "explicit_partial"
    else:
        tier = "explicit_minimal"
    return {
        "script_id": onto.get("script_id"),
        "title": onto.get("title"),
        "tier": tier,
        "source_type": source_type(onto, manifest_entry),
        "gaps": gaps,
        "evidence": {
            "killer_id": killer if has_killer else None,
            "clues": len(clues), "facts": len(facts),
            "timeline": len(timeline), "endings": len(endings),
            "required_fact_ids": len(evaluation.get("required_fact_ids") or []),
        },
    }


def summarize(conn, manifest_path=None):
    """Inventory summary plus per-script tiers; no mutation of stored scripts."""
    rows = []
    for item in db.list_scripts(conn):
        onto = db.get_script(conn, item["script_id"])
        rows.append(classify(onto))
    by_tier = Counter(row["tier"] for row in rows)
    by_source = Counter(row["source_type"] for row in rows)
    out = {
        "total": len(rows),
        "by_tier": {tier: by_tier.get(tier, 0) for tier in TIERS},
        "by_source_type": {kind: by_source.get(kind, 0) for kind in SOURCE_TYPES},
        "scripts": rows,
        "note": "tier/source/gaps 全部由已存字段派生；缺失项只标注，不补全、不编造",
    }
    if manifest_path:
        manifest = Path(manifest_path)
        if manifest.exists():
            data = json.loads(manifest.read_text(encoding="utf-8"))
            decisions = Counter(e.get("decision") for e in data.get("entries") or [])
            out["manifest"] = {
                "entries": len(data.get("entries") or []),
                "decisions": dict(decisions),
            }
    return out


def build_quality_manifest(conn, manifest_path, out_path):
    """Write a derived manifest; the source manifest and raw files stay intact."""
    manifest = Path(manifest_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    corpus_dir = manifest.parent
    entries = []
    for item in data.get("entries") or []:
        entry = dict(item)
        script_file = item.get("script_file")
        onto = None
        if script_file:
            path = corpus_dir / script_file
            if path.exists():
                onto = json.loads(path.read_text(encoding="utf-8"))
        if onto is None:
            entry["quality"] = {
                "tier": "insufficient", "source_type": source_type({}, item),
                "gaps": ["no_canonical_script"] + [
                    reason.get("code", "unknown") for reason in item.get("reasons") or []
                    if isinstance(reason, dict)
                ],
            }
        else:
            entry["quality"] = classify(onto, item)
        entries.append(entry)
    inventory = summarize(conn)
    derived = {
        "generated_from": str(manifest),
        "admission_rules": data.get("admission_rules") or [],
        "inventory_summary": {
            "total": inventory["total"],
            "by_tier": inventory["by_tier"],
            "by_source_type": inventory["by_source_type"],
        },
        "entries": entries,
        "summary": {
            "entries": len(entries),
            "by_tier": dict(Counter(e["quality"]["tier"] for e in entries)),
            "by_source_type": dict(Counter(e["quality"]["source_type"] for e in entries)),
        },
        "note": "派生清单：只加质量标签与缺口，不修改 manifest.json 或语料来源",
    }
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(derived, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return derived
