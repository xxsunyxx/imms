"""Parse a user-provided script directory into a minimal Script Ontology."""

import re
from pathlib import Path

SECTIONS = {
    "world": "world",
    "世界": "world",
    "truthgraph": "truth_graph",
    "真相": "truth_graph",
    "truthtimeline": "timeline_true",
    "真实时间线": "timeline_true",
    "characters": "characters",
    "人物": "characters",
    "角色": "characters",
    "cluepool": "clue_pool",
    "线索": "clue_pool",
    "plotlocks": "plot_locks",
    "必经桥段": "plot_locks",
    "endings": "endings",
    "结局": "endings",
}


def _section_key(name):
    return SECTIONS.get(name.strip().lower().replace(" ", ""), name.strip().lower().replace(" ", ""))


def _parse_evidence(line):
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < 3:
        return None
    try:
        raw_e = float(parts[1])
    except ValueError:
        return None
    return {
        "key": parts[0],
        "raw_e": raw_e,
        "quote": parts[2],
        "locator": parts[3] if len(parts) > 3 else "",
        "weight": 1.0,
    }


def parse_script_dir(script_dir):
    root = Path(script_dir)
    if not root.is_dir():
        raise ValueError("ERR_SCRIPT_DIR_NOT_FOUND")
    texts = []
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() in {".md", ".txt"}:
            texts.append(p.read_text(encoding="utf-8"))
    if not texts:
        raise ValueError("ERR_NO_SCRIPT_TEXT")
    return parse_script_text("\n\n".join(texts), [str(p) for p in root.rglob("*") if p.suffix.lower() in {".md", ".txt"}])


def parse_script_text(text, source_files=None):
    onto = {
        "schema_version": "0.1",
        "script_id": "untitled",
        "world": {},
        "truth_graph": [],
        "timeline_true": [],
        "characters": [],
        "clue_pool": [],
        "plot_locks": [],
        "endings": [],
        "source_files": source_files or [],
    }
    section = None
    char = None
    in_evidence = False

    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith("#"):
            if s.startswith("### "):
                if _section_key("characters") == "characters" and section == "characters":
                    bits = s[4:].split(None, 1)
                    char = {
                        "id": bits[0],
                        "name": bits[1] if len(bits) > 1 else bits[0],
                        "canon_evidence": [],
                    }
                    onto["characters"].append(char)
                    in_evidence = False
                continue
            if s.startswith("## "):
                section = _section_key(s[3:])
                char = None
                in_evidence = False
                continue
            if s.startswith("# "):
                title = s[2:].strip()
                title = re.sub(r"^剧本[:：]\s*", "", title)
                onto["script_id"] = re.sub(r"[^\w\u4e00-\u9fff]+", "_", title).strip("_") or "untitled"
                continue

        if section == "characters" and char is not None:
            if s.startswith("- ") and in_evidence:
                ev = _parse_evidence(s[2:])
                if ev:
                    char["canon_evidence"].append(ev)
                continue
            if ":" in s or "：" in s:
                key, val = re.split(r"[:：]", s, maxsplit=1)
                key, val = key.strip(), val.strip()
                if key == "canon_evidence":
                    in_evidence = True
                    continue
                in_evidence = False
                char[key] = val
            continue

        if section:
            if s.startswith("- "):
                onto.setdefault(section, []).append(s[2:])
            elif ":" in s or "：" in s:
                key, val = re.split(r"[:：]", s, maxsplit=1)
                if isinstance(onto.get(section), dict):
                    onto[section][key.strip()] = val.strip()
    return onto


def validate_ontology(onto):
    chars = onto.get("characters", [])
    ids = [c.get("id") for c in chars]
    return {
        "char_count": len(chars) >= 3,
        "unique_ids": len(ids) == len(set(ids)),
        "has_truth": len(onto.get("truth_graph", [])) > 0,
        "has_clues": len(onto.get("clue_pool", [])) > 0,
        "has_endings": len(onto.get("endings", [])) > 0,
    }
