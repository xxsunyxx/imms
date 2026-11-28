"""Parse a user-provided script directory into a minimal Script Ontology."""

import re
from pathlib import Path

import yaml

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


def parse_yaml_script(path):
    """Parse a structured script (lockedroom-ai format) into our Ontology.

    Keeps the legacy flat fields (truth_graph / clue_pool) for the runtime, and
    carries structured facts / clues / evaluation for InfoGuard and evaluation.
    """
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "facts" not in data:
        raise ValueError("ERR_YAML_NOT_SCRIPT")

    facts = data.get("facts", [])
    characters = data.get("characters", [])
    clues = data.get("clues", [])
    evaluation = data.get("evaluation", {})
    char_by_id = {c["id"]: c for c in characters}

    # Who knows each fact (private) — used to direct each clue's evidentiary weight.
    knowers = {}
    for c in characters:
        for fid in c.get("known_fact_ids", []):
            knowers.setdefault(fid, []).append(c["id"])

    clue_lines = []
    clue_fact_map = {}
    for clue in clues:
        targets = []
        for fid in clue.get("reveals_fact_ids", []):
            for cid in knowers.get(fid, [evaluation.get("killer_id")]):
                if cid and cid not in targets:
                    targets.append(cid)
        if not targets:
            targets = [evaluation.get("killer_id")]
        weight = 1.5 if clue.get("stage", 0) == 0 else 2.0
        suffix = "|".join(f"logLR:{cid}={weight}" for cid in targets if cid)
        line = f"{clue['id']} {clue.get('title', '')}|{suffix}"
        clue_lines.append(line)
        # InfoGuard dynamic exemption: unlocking this clue makes its facts discussable.
        clue_fact_map[line] = list(clue.get("reveals_fact_ids", []) or [])
        clue_fact_map[clue["id"]] = list(clue.get("reveals_fact_ids", []) or [])

    killer_name = char_by_id.get(evaluation.get("killer_id"), {}).get("name", evaluation.get("killer_id"))
    onto = {
        "schema_version": "0.2",
        "script_id": data["id"],
        "title": data.get("title", data["id"]),
        "brief": data.get("brief", ""),
        "world": {
            "mode": data.get("mode", "investigation"),
            "player_selectable": ",".join(data.get("player_selectable", [])),
        },
        "truth_graph": [f["content"] for f in facts],
        "facts": facts,
        "timeline_true": list(char_by_id.get(evaluation.get("killer_id"), {}).get("timeline", [])),
        "characters": [
            {
                "id": c["id"], "name": c["name"], "role": c.get("role", ""),
                "is_victim": c.get("is_victim", False), "is_killer": c.get("is_killer", False),
                "public_identity": c.get("public_profile", ""),
                "personality": c.get("personality", ""),
                "speaking_style": c.get("speaking_style", ""),
                "lexicon": c.get("speaking_style", ""),
                "known_fact_ids": c.get("known_fact_ids", []),
                "backstory": c.get("backstory", ""),
                "timeline": c.get("timeline", []),
                "goals": c.get("goals", []),
                # R4 script locks are only carried when the script declares
                # them explicitly; absence stays absence instead of being
                # inferred from prose.
                "script_locks": c.get("script_locks", []),
                "must_say": c.get("must_say", []),
                "must_trigger": c.get("must_trigger", []),
                "taboo": c.get("taboo", c.get("taboo_topics", [])),
                # G10: emotional triggers and faction inputs are only carried
                # when authored explicitly in the script.
                "trauma": c.get("trauma", []),
                "faction": c.get("faction"),
                "goal_fit": c.get("goal_fit"),
                "goal_fit_by_faction": c.get("goal_fit_by_faction", {}),
                "canon_evidence": [],
            }
            for c in characters
        ],
        "clue_pool": clue_lines,
        "clues": clues,
        "clue_fact_map": clue_fact_map,
        "plot_locks": [],
        "script_locks": data.get("script_locks", []),
        "factions": data.get("factions", []),
        "endings": [
            f"指认{killer_name}则真凶伏法",
            "指认其他人则冤案",
        ],
        "evaluation": evaluation,
        "source_files": [str(path)],
    }
    return onto


def parse_script_dir(script_dir):
    root = Path(script_dir)
    if root.is_dir() and (root / "本盒完整内容.md").exists():
        from . import boxed
        return boxed.parse_boxed_dir(root)
    if root.is_file() and root.suffix.lower() in {".yaml", ".yml"}:
        return parse_yaml_script(root)
    if not root.is_dir():
        raise ValueError("ERR_SCRIPT_DIR_NOT_FOUND")
    yaml_files = sorted([*root.rglob("*.yaml"), *root.rglob("*.yml")])
    if yaml_files:
        return parse_yaml_script(yaml_files[0])
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
        "clue_fact_map": {},
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
        "char_count": len(chars) >= 2,
        "unique_ids": len(ids) == len(set(ids)),
        "has_truth": len(onto.get("truth_graph", [])) > 0,
        "has_clues": len(onto.get("clue_pool", [])) > 0,
        "has_endings": len(onto.get("endings", [])) > 0,
    }
