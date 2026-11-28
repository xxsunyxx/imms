"""Unified ingestion pipeline for arbitrary script sources.

Every source -- structured YAML, boxed-script markdown, RolePlayingGame JSON,
legacy section text, an already canonical Ontology, or an interactive scene
game that must be rejected -- goes through the same chain:

    detect -> parse -> admit -> repair -> canonicalize -> store

The canonical product is one deterministic JSON Ontology per script plus a
manifest with source fingerprints, admission reasons and a completion ledger.
Once ingested, the project never needs the original source directory again.
"""

import copy
import hashlib
import json
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .util import read_json

ROOT = Path(__file__).resolve().parents[2]
CANONICAL_VERSION = "1.0"
WEIGHT_RE = re.compile(r"logLR:([^\s|=]+)=(-?\d+(?:\.\d+)?)")
TEXT_SUFFIXES = {".yaml", ".yml", ".md", ".txt"}
SOURCE_SUFFIXES = TEXT_SUFFIXES | {".json", ".mjs", ".js"}
RAW_DIR_BY_KIND = {
    "rpg_json": "rpg", "imms_ontology": "ontology", "structured_yaml": "yaml",
    "boxed_dir": "boxed", "boxed_md": "boxed", "section_text": "text",
}
SCENE_WORDS = ("room", "spot", "zone", "map", "image", "photo", "sprite", "asset", "click", "scene")
CANONICAL_FIELDS = (
    "schema_version", "script_id", "title", "brief", "world", "truth_graph",
    "timeline_true", "characters", "clue_pool", "clues", "clue_fact_map",
    "plot_locks", "endings", "evaluation", "completions", "source_files",
)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _clean(text, limit=None):
    out = re.sub(r"\s+", " ", str(text or "")).strip()
    return out[:limit] if limit else out


def _safe_id(value, fallback="untitled"):
    text = re.sub(r"[^\w\-]+", "_", str(value or ""), flags=re.UNICODE).strip("_")
    return text or fallback


def _keywords(text, limit=8):
    parts = re.split(r"[，。；：、（）()\s\-|\"'“”《》]+", _clean(text))
    return [p for p in parts if 2 <= len(p) <= 16][:limit]


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 1. Format detection
# ---------------------------------------------------------------------------

def detect(path):
    """Identify the source format without mutating anything."""
    path = Path(path)
    if not path.exists():
        raise ValueError("ERR_INGEST_PATH_NOT_FOUND")
    if path.is_dir():
        master = path / "本盒完整内容.md"
        if master.exists():
            return {
                "kind": "boxed_dir", "file": str(master), "path": str(path),
                "confidence": "high", "evidence": ["本盒完整内容.md"],
            }
        # Preserve the historical `parse --script <dir>` behaviour: a single
        # structured script inside a directory is still a valid source.
        candidates = sorted([*path.rglob("*.yaml"), *path.rglob("*.yml")])
        if candidates:
            return detect(candidates[0])
        return {
            "kind": "unknown", "file": None, "path": str(path),
            "confidence": "none", "evidence": ["directory has no 本盒完整内容.md; use batch on its files"],
        }

    suffix = path.suffix.lower()
    if suffix in {".mjs", ".js", ".ts"}:
        text = path.read_text(encoding="utf-8", errors="replace")
        low = text.lower()
        hits = sorted({word for word in SCENE_WORDS if word in low})
        kind = "interactive_scene_game" if hits else "unknown"
        return {
            "kind": kind, "file": str(path), "path": str(path),
            "confidence": "high" if hits else "none", "evidence": hits,
        }
    if suffix == ".json":
        try:
            data = read_json(path)
        except (ValueError, UnicodeDecodeError) as exc:
            return {"kind": "unknown", "file": str(path), "path": str(path),
                    "confidence": "none", "evidence": [f"invalid JSON: {exc}"]}
        if not isinstance(data, dict):
            return {"kind": "unknown", "file": str(path), "path": str(path),
                    "confidence": "none", "evidence": ["JSON root is not an object"]}
        if {"script_id", "characters", "truth_graph", "clue_pool"} <= set(data):
            kind, evidence = "imms_ontology", ["script_id", "characters", "truth_graph", "clue_pool"]
        elif {"script_meta", "roles", "truth"} <= set(data):
            kind, evidence = "rpg_json", ["script_meta", "roles", "truth"]
        else:
            kind, evidence = "unknown", list(data.keys())[:8]
        return {"kind": kind, "file": str(path), "path": str(path),
                "confidence": "high" if kind != "unknown" else "none", "evidence": evidence}
    if suffix in {".yaml", ".yml"}:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, UnicodeDecodeError) as exc:
            return {"kind": "unknown", "file": str(path), "path": str(path),
                    "confidence": "none", "evidence": [f"invalid YAML: {exc}"]}
        if isinstance(data, dict) and {"facts", "characters"} <= set(data):
            kind, evidence = "structured_yaml", ["facts", "characters"]
        else:
            kind, evidence = "unknown", [str(k) for k in data][:8] if isinstance(data, dict) else []
        return {"kind": kind, "file": str(path), "path": str(path),
                "confidence": "high" if kind != "unknown" else "none", "evidence": evidence}
    if suffix in TEXT_SUFFIXES:
        text = path.read_text(encoding="utf-8", errors="replace")
        if "本盒完整内容" in "\n".join(text.splitlines()[:8]) or re.search(r"③\s*角色剧本", text):
            kind, evidence = "boxed_md", ["本盒完整内容", "③ 角色剧本"]
        else:
            from .ontology import SECTIONS
            evidence = sorted({f"## {h}" for h in SECTIONS if re.search(rf"^#{{1,3}}\s*{h}", text, re.M | re.I)})
            kind = "section_text" if evidence else "unknown"
        return {"kind": kind, "file": str(path), "path": str(path),
                "confidence": "high" if evidence else "none", "evidence": evidence[:8]}
    return {"kind": "unknown", "file": str(path), "path": str(path),
            "confidence": "none", "evidence": [f"unsupported suffix {suffix or '(none)' }"]}


# ---------------------------------------------------------------------------
# 2. Adapters: all existing parsers are reused, not duplicated
# ---------------------------------------------------------------------------

def _parse_ontology_file(path):
    onto = read_json(path)
    if not isinstance(onto, dict):
        raise ValueError("ERR_ONTOLOGY_NOT_OBJECT")
    return {"onto": onto, "completions": list(onto.get("completions") or []), "reasons": []}


def _parse_rpg_file(path):
    from . import corpus
    data = read_json(path)
    decision, reasons = corpus.admit_rpg(data)
    if decision != "import":
        # Do not let a downstream parser exception mask the real gate result.
        return {"onto": None, "completions": [], "reasons": reasons,
                "raw_data": data, "admission": decision}
    onto, completions = corpus.parse_rpg_script(data, str(path))
    return {"onto": onto, "completions": completions, "reasons": reasons,
            "raw_data": data, "admission": decision}


def _parse_yaml_file(path):
    from .ontology import parse_yaml_script
    onto = parse_yaml_script(path)
    return {"onto": onto, "completions": [], "reasons": []}


def _parse_boxed(path):
    from . import boxed
    if path.is_dir():
        onto = boxed.parse_boxed_dir(path)
    else:
        # A single master markdown is accepted by staging it under its
        # expected file name; images stay provenance-only and are not copied.
        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp) / "本盒完整内容.md"
            shutil.copy2(path, staged)
            onto = boxed.parse_boxed_dir(staged.parent)
    onto["source_files"] = [str(path)]
    return {"onto": onto, "completions": [], "reasons": []}


def _parse_section_text(path):
    from .ontology import parse_script_text
    text = path.read_text(encoding="utf-8", errors="replace")
    onto = parse_script_text(text, [str(path)])
    return {"onto": onto, "completions": [], "reasons": []}


def _parse_interactive(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    hits = sorted({w for w in SCENE_WORDS if w in text.lower()})
    return {
        "onto": None, "completions": [],
        "reasons": [{"code": "requires_mouse_or_image", "detail": hits[:8]}],
    }


ADAPTERS = {
    "imms_ontology": _parse_ontology_file,
    "rpg_json": _parse_rpg_file,
    "structured_yaml": _parse_yaml_file,
    "boxed_dir": _parse_boxed,
    "boxed_md": _parse_boxed,
    "section_text": _parse_section_text,
    "interactive_scene_game": _parse_interactive,
}


# ---------------------------------------------------------------------------
# 3. Admission gate
# ---------------------------------------------------------------------------

def _truthy(value):
    return value is True or (isinstance(value, str) and value.lower() in {"true", "yes", "1"})


def admit_ontology(parsed, detection):
    """Decide whether a parsed Ontology is playable in the text-only kernel."""
    reasons = list(parsed.get("reasons") or [])
    onto = parsed.get("onto")
    if onto is None:
        return "skip", reasons or [{"code": "no_ontology", "detail": detection["kind"]}]
    chars = onto.get("characters") or []
    ids = [c.get("id") for c in chars]
    facts = onto.get("facts") or []
    truth_facts = [f for f in facts if f.get("visibility") == "truth" or f.get("type") == "truth"]
    checks = {
        "char_count": len(chars) >= 2,
        "unique_ids": all(ids) and len(ids) == len(set(ids)),
        "has_truth": bool(onto.get("truth_graph") or facts),
        "has_clues": bool(onto.get("clues") or onto.get("clue_pool")),
        "has_endings": bool(onto.get("endings")),
        "has_solution": bool(
            (onto.get("evaluation") or {}).get("killer_id")
            or any(_truthy(c.get("is_killer")) for c in chars)
        ),
    }
    codes = {
        "char_count": "too_few_characters",
        "unique_ids": "duplicate_or_missing_character_ids",
        "has_truth": "no_truth",
        "has_clues": "no_clues",
        "has_endings": "no_endings",
        "has_solution": "no_declared_solution",
    }
    reasons.extend({"code": codes[k], "detail": k} for k, ok in checks.items() if not ok)
    killer = (onto.get("evaluation") or {}).get("killer_id")
    if killer and ids and killer not in ids:
        reasons.append({"code": "killer_not_a_character", "detail": killer})
    if truth_facts:
        valid = {f["id"] for f in facts}
        dangling = sorted({
            fid for c in (onto.get("clues") or [])
            for fid in (c.get("reveals_fact_ids") or []) if fid not in valid
        })
        if dangling:
            reasons.append({"code": "dangling_clue_fact_refs", "detail": dangling[:8]})
    return ("import" if not reasons else "skip"), reasons


# ---------------------------------------------------------------------------
# 4. Repair: only evidence already present in the source is used
# ---------------------------------------------------------------------------

def repair(onto, completions=None):
    """Fill structural gaps from source evidence; never invent plot facts."""
    onto = copy.deepcopy(onto)
    ledger = [dict(c) for c in (completions or onto.get("completions") or [])]
    sid = _safe_id(onto.get("script_id"), "untitled")
    onto["script_id"] = sid
    for key, default in (
        ("title", sid), ("brief", ""), ("world", {}), ("truth_graph", []),
        ("timeline_true", []), ("clue_pool", []), ("clues", []),
        ("clue_fact_map", {}), ("plot_locks", []), ("endings", []),
    ):
        onto.setdefault(key, default if not isinstance(default, (list, dict)) else copy.deepcopy(default))

    chars = onto.setdefault("characters", [])
    facts = onto.setdefault("facts", [])
    known_facts = {f.get("id") for f in facts}

    # G4: the legacy section format stores truth as plain strings only.
    if not facts and onto["truth_graph"]:
        for n, line in enumerate(onto["truth_graph"], 1):
            fid = f"f_{sid}_truth{n:02d}"
            facts.append({
                "id": fid, "type": "truth", "content": str(line),
                "visibility": "truth", "core": True, "keywords": _keywords(line),
            })
        known_facts = {f["id"] for f in facts}
        ledger.append({
            "rule": "G4_fact_projection", "field": "facts", "value": len(facts),
            "basis": "projected existing truth_graph lines; no new fact text added",
        })

    truth_ids = [
        f["id"] for f in facts
        if f.get("visibility") == "truth" or f.get("type") == "truth"
    ]
    primary_truth = truth_ids[0] if truth_ids else None

    # G1: clue_pool is the runtime representation; derive structured clues
    # from it when a source only provides plain lines.
    if not onto["clues"] and onto["clue_pool"]:
        for n, line in enumerate(onto["clue_pool"], 1):
            head = str(line).split("|", 1)[0].strip()
            parts = head.split(None, 1)
            cid = _safe_id(parts[0], f"{sid}_c{n:02d}") if parts else f"{sid}_c{n:02d}"
            content = parts[1].strip() if len(parts) > 1 else head
            onto["clues"].append({
                "id": cid, "title": content[:24], "content": content,
                "type": "physical", "truth_value": True, "stage": 0,
                "reveals_fact_ids": [primary_truth] if primary_truth else [],
                "completion": "G1_clue_projection",
            })
        ledger.append({
            "rule": "G1_clue_projection", "field": "clues", "value": len(onto["clues"]),
            "basis": "clue_pool lines project back into structured clue records",
        })

    # G5: adopt an explicitly declared killer when evaluation is absent.
    evaluation = onto.setdefault("evaluation", {})
    if not evaluation.get("killer_id"):
        declared = [c["id"] for c in chars if _truthy(c.get("is_killer")) and c.get("id")]
        if declared:
            evaluation["killer_id"] = declared[0]
            ledger.append({
                "rule": "G5_killer_from_character", "field": "evaluation.killer_id",
                "value": declared[0], "basis": "source character is explicitly marked is_killer",
            })

    # G3: a clue with no fact link can only point back to the full truth.
    for clue in onto["clues"]:
        links = [fid for fid in (clue.get("reveals_fact_ids") or []) if fid in known_facts]
        if not links and primary_truth:
            links = [primary_truth]
            clue["completion"] = "G3_clue_truth_fallback"
            ledger.append({
                "rule": "G3_clue_truth_fallback",
                "field": f"clues[{clue.get('id')}].reveals_fact_ids",
                "value": links, "basis": "no source evidence names a narrower fact; hint only, not a proof",
            })
        clue["reveals_fact_ids"] = links
        for fid in links:
            onto["clue_fact_map"].setdefault(clue["id"], [])
            if fid not in onto["clue_fact_map"][clue["id"]]:
                onto["clue_fact_map"][clue["id"]].append(fid)

    # G2: normalise every clue_pool weight so runtime + solver can parse it.
    owner_by_fact = {}
    for char in chars:
        for fid in char.get("known_fact_ids") or []:
            owner_by_fact.setdefault(fid, char.get("id"))
    culprit = evaluation.get("killer_id")
    clue_by_id = {c.get("id"): c for c in onto["clues"]}
    pool = []
    fixed = 0
    for n, line in enumerate(onto["clue_pool"], 1):
        raw = str(line)
        if not WEIGHT_RE.search(raw):
            head = raw.split("|", 1)[0].strip()
            parts = head.split(None, 1)
            cid = parts[0] if parts else f"{sid}_c{n:02d}"
            clue = clue_by_id.get(cid) or {}
            targets = []
            for fid in clue.get("reveals_fact_ids") or []:
                owner = owner_by_fact.get(fid) or culprit
                if owner and owner not in targets:
                    targets.append(owner)
            if not targets and culprit:
                targets = [culprit]
            weight = 1.5 if int(clue.get("stage") or 0) == 0 else 2.0
            suffix = "|".join(f"logLR:{t}={weight}" for t in targets)
            raw = f"{head}|{suffix}" if suffix else head
            fixed += 1
        pool.append(raw)
        head = raw.split("|", 1)[0].strip()
        cid = head.split(None, 1)[0] if head else f"{sid}_c{n:02d}"
        onto["clue_fact_map"].setdefault(cid, (clue_by_id.get(cid) or {}).get("reveals_fact_ids") or [])
    if fixed:
        onto["clue_pool"] = pool
        ledger.append({
            "rule": "G2_clue_weight_normalization", "field": "clue_pool", "value": fixed,
            "basis": "rewrote unparseable logLR weights using source fact ownership (fallback: declared culprit)",
        })

    onto["completions"] = ledger
    onto["schema_version"] = CANONICAL_VERSION
    onto["source_files"] = list(onto.get("source_files") or [])
    return onto, ledger


# ---------------------------------------------------------------------------
# 5. Canonicalisation + storage
# ---------------------------------------------------------------------------

def canonicalize(path, detection=None, llm=None):
    """Detect -> parse -> admit -> skeleton -> complete -> repair. No writes."""
    detection = detection or detect(path)
    result = {
        "source_path": detection["path"],
        "source_file": detection["file"],
        "detected": detection,
        "decision": "skip",
        "reasons": [],
        "onto": None,
        "skeleton": None,
        "llm": None,
        "completions": [],
        "checks": {},
    }
    adapter = ADAPTERS.get(detection["kind"])
    if adapter is None:
        result["reasons"] = [{"code": "unsupported_format", "detail": detection["evidence"]}]
        return result
    try:
        source_file = Path(detection["file"] or detection["path"])
        result["source_sha256"] = sha256_file(source_file)
        parsed = adapter(Path(detection["path"]))
    except Exception as exc:  # a broken source is a skip, not a crash of the batch
        result["reasons"] = [{"code": "parse_error", "detail": f"{type(exc).__name__}: {exc}"}]
        return result
    decision, reasons = admit_ontology(parsed, detection)
    result.update({
        "decision": decision, "reasons": reasons,
        "completions": list(parsed.get("completions") or []),
    })
    if decision != "import":
        return result
    from . import skeleton as skeleton_mod

    skel = skeleton_mod.project(parsed["onto"], detection)
    skel, llm_info = skeleton_mod.summarize(
        skel, skeleton_mod.source_text(detection), llm
    )
    skel, skel_completions = skeleton_mod.complete(skel, parsed["onto"])
    onto, ledger = repair(
        skeleton_mod.apply(skel, parsed["onto"]),
        list(parsed.get("completions") or []) + skel_completions,
    )
    result.update({
        "onto": onto, "skeleton": skel, "llm": llm_info, "completions": ledger,
        "checks": {
            "canonical_fields": all(k in onto for k in CANONICAL_FIELDS),
            "clue_weights_parseable": all(WEIGHT_RE.search(line) for line in onto["clue_pool"]),
            "clue_refs_valid": all(
                fid in {f["id"] for f in onto["facts"]}
                for c in onto["clues"] for fid in c["reveals_fact_ids"]
            ),
        },
    })
    return result


def validate_canonical(path):
    onto = read_json(path)
    chars = onto.get("characters") or []
    ids = [c.get("id") for c in chars]
    facts = {f.get("id") for f in onto.get("facts") or []}
    return {
        "is_canonical": onto.get("schema_version") == CANONICAL_VERSION,
        "required_fields": all(k in onto for k in CANONICAL_FIELDS),
        "unique_character_ids": len(ids) == len(set(ids)) and all(ids),
        "clue_weights_parseable": all(WEIGHT_RE.search(str(l)) for l in onto.get("clue_pool") or []),
        "clue_refs_valid": all(
            fid in facts for c in onto.get("clues") or []
            for fid in c.get("reveals_fact_ids") or []
        ),
        "has_provenance": bool((onto.get("ingest") or {}).get("source_sha256")),
    }


def write_canonical(path, onto):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(onto, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _load_manifest(root):
    path = Path(root) / "manifest.json"
    if not path.exists():
        return {
            "schema_version": "1.1", "pipeline": "ingest", "generated_at": _now(),
            "storage": "corpus/raw keeps source bytes; corpus/scripts keeps canonical Ontology JSON",
            "entries": [],
        }
    manifest = read_json(path)
    manifest.setdefault("entries", [])
    manifest["pipeline"] = "ingest"
    return manifest


def _write_manifest(root, manifest):
    manifest["generated_at"] = _now()
    manifest["summary"] = {
        "total": len(manifest["entries"]),
        "import": sum(1 for e in manifest["entries"] if e["decision"] == "import"),
        "skip": sum(1 for e in manifest["entries"] if e["decision"] == "skip"),
    }
    write_canonical(Path(root) / "manifest.json", manifest)
    return manifest


def _upsert(manifest, entry, force):
    for idx, old in enumerate(manifest["entries"]):
        same_hash = old.get("source_sha256") and old["source_sha256"] == entry.get("source_sha256")
        same_source = old.get("source_path") == entry.get("source_path") and old.get("source_id") == entry.get("source_id")
        # Skipped sources may share identical bytes; keep one entry per path
        # so their individual rejection reasons remain visible.
        same_import = same_hash and old.get("decision") == "import" and entry.get("decision") == "import"
        if same_source or same_import:
            if force or old.get("decision") != "import":
                manifest["entries"][idx] = entry
            else:
                entry["script_id"] = old.get("script_id", entry.get("script_id"))
                entry["script_file"] = old.get("script_file")
                manifest["entries"][idx] = entry
            return
    manifest["entries"].append(entry)


def ingest_source(path, root=None, force=False, llm=None):
    """Convert one source and store its raw bytes, skeleton, canonical JSON and manifest entry."""
    root = Path(root or (ROOT / "corpus"))
    manifest = _load_manifest(root)
    result = canonicalize(path, llm=llm)
    detection = result["detected"]
    source_id = detection["kind"] + ":" + Path(detection["path"]).name
    source_sha = result.get("source_sha256")
    if result["decision"] != "import":
        entry = {
            "source_id": source_id, "title": Path(detection["path"]).name,
            "source_kind": detection["kind"], "source_path": detection["path"],
            "source_sha256": source_sha, "decision": "skip", "reasons": result["reasons"],
        }
        _upsert(manifest, entry, force)
        _write_manifest(root, manifest)
        result["entry"] = entry
        return result

    onto = result["onto"]
    candidate_sid = onto["script_id"]
    used = {
        e.get("script_id") for e in manifest["entries"]
        if e.get("decision") == "import" and e.get("source_sha256") != source_sha
    }
    script_id = candidate_sid
    suffix = 2
    while script_id in used:
        script_id = f"{candidate_sid}_{suffix}"
        suffix += 1
    if script_id != candidate_sid:
        onto["script_id"] = script_id
        result["completions"].append({
            "rule": "G6_script_id_collision", "field": "script_id",
            "value": script_id, "basis": f"{candidate_sid} already owned by another source",
        })
        onto["completions"] = result["completions"]

    raw_dir = Path("raw") / RAW_DIR_BY_KIND.get(detection["kind"], detection["kind"])
    if detection["kind"] == "boxed_dir":
        raw_rel = str(raw_dir / script_id / Path(detection["file"]).name).replace("\\", "/")
    else:
        raw_rel = str(raw_dir / Path(detection["file"]).name).replace("\\", "/")
    raw_path = root / raw_rel
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    if Path(detection["file"]).resolve() != raw_path.resolve():
        shutil.copy2(detection["file"], raw_path)

    script_rel = f"scripts/{script_id}.json"
    # Byte-level idempotence: re-importing an unchanged source must not move
    # ingested_at.  Otherwise a second ingest differs by one second and breaks
    # reproducibility checks on the canonical file.
    prior_ingest = {}
    if (root / script_rel).exists():
        try:
            prior_ingest = (read_json(root / script_rel).get("ingest") or {})
        except (OSError, ValueError):
            prior_ingest = {}
    ingested_at = prior_ingest.get("ingested_at") \
        if prior_ingest.get("source_sha256") == source_sha else None
    onto["source_files"] = [raw_rel]
    onto["ingest"] = {
        "adapter": detection["kind"],
        "detected_evidence": detection["evidence"],
        "source_path": detection["path"],
        "source_sha256": source_sha,
        "ingested_at": ingested_at or _now(),
        "checks": result["checks"],
    }
    write_canonical(root / script_rel, onto)
    skeleton_rel = f"skeletons/{script_id}.json"
    result["skeleton"]["source_sha256"] = source_sha
    write_canonical(root / skeleton_rel, result["skeleton"])

    entry = {
        "source_id": source_id, "title": onto.get("title", script_id),
        "source_kind": detection["kind"], "source_path": detection["path"],
        "source_sha256": source_sha, "decision": "import", "reasons": [],
        "script_id": script_id, "script_file": script_rel,
        "skeleton_file": skeleton_rel,
        "characters": len(onto["characters"]), "clues": len(onto["clues"]),
        "facts": len(onto["facts"]), "completions": len(result["completions"]),
        "checks": result["checks"], "raw_files": [raw_rel],
    }
    _upsert(manifest, entry, force)
    _write_manifest(root, manifest)
    result["entry"] = entry
    return result


def ingest_tree(path, root=None, limit=None, force=False, llm=None):
    """Ingest every recognisable source under a file or directory."""
    path = Path(path)
    root = Path(root or (ROOT / "corpus"))
    if path.is_file():
        paths = [path]
    else:
        boxed_dirs = sorted(d for d in path.rglob("*")
                            if d.is_dir() and (d / "本盒完整内容.md").exists())
        masters = {d / "本盒完整内容.md" for d in boxed_dirs}
        paths = list(boxed_dirs)
        for candidate in sorted(path.rglob("*")):
            if not candidate.is_file() or candidate.suffix.lower() not in SOURCE_SUFFIXES:
                continue
            if candidate in masters or candidate.name == "manifest.json":
                continue
            if candidate.parent.name == "scripts" and root in candidate.parents:
                continue
            paths.append(candidate)
    entries = []
    for candidate in paths[:limit] if limit else paths:
        result = ingest_source(candidate, root=root, force=force, llm=llm)
        entries.append({
            "source_path": result["source_path"],
            "detected": result["detected"]["kind"],
            "decision": result["decision"],
            "script_id": (result.get("entry") or {}).get("script_id"),
            "reasons": [r["code"] for r in result["reasons"]],
            "skeleton_gaps": len((result.get("skeleton") or {}).get("gaps") or []),
        })
    return {"processed": len(entries), "entries": entries}


def ingest_report(root=None):
    root = Path(root or (ROOT / "corpus"))
    manifest = _load_manifest(root)
    by_kind = {}
    for entry in manifest["entries"]:
        key = f"{entry.get('source_kind')}/{entry['decision']}"
        by_kind[key] = by_kind.get(key, 0) + 1
    return {
        "summary": {
            "total": len(manifest["entries"]),
            "import": sum(1 for e in manifest["entries"] if e["decision"] == "import"),
            "skip": sum(1 for e in manifest["entries"] if e["decision"] == "skip"),
        },
        "by_kind": by_kind,
        "entries": [
            {
                "source_id": e.get("source_id"), "title": e.get("title"),
                "decision": e["decision"], "script_id": e.get("script_id"),
                "reasons": [r["code"] for r in e.get("reasons") or []],
            }
            for e in manifest["entries"]
        ],
    }
