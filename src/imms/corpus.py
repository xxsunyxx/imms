"""Self-contained script corpus with admission and completion gates.

The external ``refer/`` directory is disposable.  This module converts the
usable parts of a source repository into project-owned JSON under ``corpus/``,
records provenance hashes, and refuses sources that need mouse-driven scene
search or image-only object recognition.  Missing *logic* is only completed
from evidence already present in the source; anything that still cannot be
derived is skipped rather than invented.
"""

import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .util import read_json, write_json

ROOT = Path(__file__).resolve().parents[2]

# Victims are not seated characters in the source schema.  These annotations
# are taken verbatim from each script's story_background / truth.full_story.
VICTIM_BY_SOURCE_ID = {
    "deep_sea_echo": "钟望",
    "fog_harbor_clocktower": "陆绍庭",
    "last_train": "魏舟",
    "old_house_corridor": "姜慎",
    "rainy_night_letter": "秦重",
    "silent_theater": "范策",
    "mini_case": "秦重",
}

# Soft logic gaps: the evidence chain explicitly asks for material that lives
# only in another character's private memory.  We expose an inquiry prompt,
# never the private content itself.
INQUIRY_GAPS = {
    "fog_harbor_clocktower": [
        {
            "keywords": ["账本照片", "药柜行踪"],
            "prompt": (
                "证据链要求核对“账本照片”与“药柜行踪”。相关细节只存在于个别角色的私有记忆中，"
                "不会自动公开；请通过问询/私聊取得，不要凭臆测补造。"
            ),
        }
    ]
}

MOSSVALE_STORIES = [
    {
        "source_id": "mossvale_fog_lamp",
        "title": "雾灯未眠",
        "source_files": [
            "mossvale/mystery/server/scenario.mjs",
            "mossvale/mystery/shared/layout.mjs",
        ],
    },
    {
        "source_id": "mossvale_suspect7",
        "title": "第7号嫌疑人",
        "source_files": [
            "mossvale/mystery/server/suspect7/catalog.mjs",
            "mossvale/mystery/shared/suspect7/cast.mjs",
        ],
    },
]

SCENE_KEYS = {"image", "photo", "picture", "map", "coord", "x", "y", "asset", "sprite", "region"}
MEDIA_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp3", ".mp4", ".svg")
DUNGEON_KEYS = {"room", "spot", "zone", "location"}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _clean(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _keywords(text, limit=12):
    parts = re.split(r"[，。；：、（）()\s\-|\"'“”《》]", _clean(text))
    out = []
    for part in parts:
        if 2 <= len(part) <= 16 and re.search(r"[\u4e00-\u9fffA-Za-z0-9]", part):
            if part not in out:
                out.append(part)
        if len(out) >= limit:
            break
    return out


def _walk_scene_dependency(node, hits=None):
    """Report fields that cannot be expressed in a text-only runtime."""
    hits = hits if hits is not None else []
    if isinstance(node, dict):
        for key, value in node.items():
            low = str(key).lower()
            if low in SCENE_KEYS and value not in (None, "", [], {}):
                hits.append(f"field:{key}")
            if low in DUNGEON_KEYS and isinstance(value, (str, int)) and str(value):
                hits.append(f"scene_field:{key}")
            if isinstance(value, str) and value.lower().endswith(MEDIA_SUFFIXES):
                hits.append(f"asset:{value}")
            _walk_scene_dependency(value, hits)
    elif isinstance(node, list):
        for item in node:
            _walk_scene_dependency(item, hits)
    return sorted(set(hits))


def admit_rpg(data):
    """Decide whether a RolePlayingGame JSON script can enter the text kernel."""
    reasons = []
    truth = data.get("truth") or {}
    roles = data.get("roles") or []
    stages = data.get("stage_flow") or {}
    targets = (data.get("mechanics_config") or {}).get("search") or {}
    clues = [c for t in targets.get("search_targets", []) for c in t.get("clues", [])]

    scene_hits = _walk_scene_dependency(data)
    if scene_hits:
        reasons.append({
            "code": "requires_mouse_or_image",
            "detail": scene_hits[:8],
        })
    if not truth.get("culprit_role_id"):
        reasons.append({"code": "no_culprit", "detail": "truth.culprit_role_id missing"})
    if not truth.get("evidence_chain"):
        reasons.append({"code": "no_evidence_chain", "detail": "truth.evidence_chain empty"})
    if not stages:
        reasons.append({"code": "no_stage_flow", "detail": "stage_flow missing"})
    else:
        last_key = max(stages, key=lambda k: int(k.split("_")[1]) if k.split("_")[-1].isdigit() else 0)
        if not (stages[last_key].get("resolution") or {}).get("final_decision"):
            reasons.append({"code": "no_final_decision", "detail": f"{last_key}.resolution.final_decision missing"})
    if not clues:
        reasons.append({"code": "no_text_clues", "detail": "search_targets[].clues empty"})
    if len([r for r in roles if r.get("human_playable")]) < 2:
        reasons.append({"code": "too_few_playable_roles", "detail": "fewer than 2 human-playable roles"})

    culprit = truth.get("culprit_role_id")
    if culprit and culprit not in {r.get("role_id") for r in roles}:
        reasons.append({"code": "culprit_not_a_role", "detail": culprit})
    return ("import" if not reasons else "skip"), reasons


def _infer_victim(data):
    sid = (data.get("script_meta") or {}).get("script_id", "")
    if sid in VICTIM_BY_SOURCE_ID:
        return VICTIM_BY_SOURCE_ID[sid], "source_text_annotation"
    text = _clean(data.get("story_background", "")) + " " + _clean(
        (data.get("truth") or {}).get("full_story", "")
    )
    patterns = [
        r"([一-龥]{2,4})(?:在[^。；]{0,24})?(?:被发现|被发现倒在|当场死亡|死亡|遇害)",
        r"(?:发现|指认)([一-龥]{2,4})(?:倒在|死亡)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1), "regex_from_story_background"
    return "未知死者", "not_found"


def _timeline_items(truth):
    timeline = truth.get("complete_timeline") or {}
    if isinstance(timeline, dict):
        return [f"{key} {_clean(value)}" for key, value in sorted(timeline.items())]
    return [_clean(item) for item in timeline if _clean(item)]


def parse_rpg_script(data, source_rel):
    """Convert one source JSON script into the standard IMMS Ontology."""
    meta = data.get("script_meta") or {}
    truth = data.get("truth") or {}
    sid = "rpg_" + re.sub(r"[^a-zA-Z0-9_-]+", "_", meta.get("script_id", "untitled"))
    title = meta.get("title", sid)
    roles = data.get("roles") or []
    culprit = truth.get("culprit_role_id")
    victim, victim_basis = _infer_victim(data)
    completions = []

    facts = []
    public_identity_facts = []

    def add_fact(fid, ftype, content, visibility, owner=None, core=False):
        content = _clean(content)
        if not content:
            return None
        fact = {
            "id": fid,
            "type": ftype,
            "content": content,
            "visibility": visibility,
            "core": core,
            "keywords": _keywords(content),
        }
        if owner:
            fact["owner"] = owner
        facts.append(fact)
        return fact

    add_fact(f"f_{sid}_pub", "background", data.get("story_background", ""), "public", core=True)
    add_fact(
        f"f_{sid}_victim", "victim", f"死者：{victim}。依据：{victim_basis}。",
        "public", core=True,
    )
    completions.append({
        "rule": "R1_victim_annotation",
        "field": "evaluation.victim_name",
        "value": victim,
        "basis": victim_basis,
    })
    truth_fid = f"f_{sid}_truth"
    add_fact(truth_fid, "truth", truth.get("full_story", ""), "truth", core=True)

    timeline = _timeline_items(truth)
    for idx, line in enumerate(timeline, 1):
        add_fact(f"f_{sid}_t{idx:02d}", "timeline", line, "truth")
    if not timeline:
        completions.append({
            "rule": "R4_timeline_placeholder",
            "field": "timeline_true",
            "value": [],
            "basis": "source has no structured timeline; no events invented",
        })

    chain_fids = []
    for idx, chain in enumerate(truth.get("evidence_chain") or [], 1):
        fid = f"f_{sid}_chain{idx:02d}"
        body = f"{_clean(chain.get('conclusion'))} {_clean(chain.get('reasoning'))}".strip()
        add_fact(fid, "evidence_chain", body, "truth", core=True)
        chain_fids.append(fid)

    for idx, constraint in enumerate(data.get("hard_constraints") or [], 1):
        add_fact(f"f_{sid}_lock{idx:02d}", "plot_lock", constraint, "truth")

    private_by_owner = {}
    for role in roles:
        rid = role.get("role_id")
        for stage, body in (role.get("stage_knowledge") or {}).items():
            for block in body.get("blocks") or []:
                bid = block.get("block_id") or f"{rid}_{stage}_{len(private_by_owner.get(rid, [])) + 1}"
                vis = "private" if block.get("visibility") == "self_only" else "public"
                fact = add_fact(
                    bid, block.get("type", "memory"), block.get("content", ""),
                    vis, owner=block.get("owner_role") or rid,
                )
                if fact and vis == "private":
                    private_by_owner.setdefault(rid, []).append(bid)

    for role in roles:
        rid = role.get("role_id")
        public = _clean(role.get("public_identity", ""))
        pfid = f"f_{rid}_identity"
        if public and add_fact(pfid, "identity", public, "public", owner=rid):
            public_identity_facts.append(pfid)

    characters = []
    for role in roles:
        rid = role.get("role_id")
        characters.append({
            "id": rid,
            "name": role.get("role_name", rid),
            "role": role.get("public_identity", ""),
            "public_identity": role.get("public_identity", ""),
            "public_profile": role.get("public_identity", ""),
            "personality": role.get("personality", ""),
            "speaking_style": role.get("personality", ""),
            "lexicon": "",
            "backstory": role.get("hidden_goal") or "",
            "goals": [role["hidden_goal"]] if role.get("hidden_goal") else [],
            "known_fact_ids": list(private_by_owner.get(rid, [])),
            "canon_evidence": [],
            "timeline": [],
            "is_victim": False,
            "is_killer": rid == culprit,
        })

    clues, clue_lines, clue_fact_map = [], [], {}
    clue_by_id = {}
    for target in (data.get("mechanics_config") or {}).get("search", {}).get("search_targets", []):
        raw_stage = target.get("unlock_stage", 1)
        for n, raw in enumerate(target.get("clues") or [], 1):
            cid = raw.get("id") or f"{sid}_c{len(clues) + 1:02d}"
            cid = re.sub(r"[^a-zA-Z0-9_-]+", "_", cid)
            stage = max(0, int(raw_stage or 1) - 1)
            stage_source = f"search_target.unlock_stage={raw_stage}"
            revealed = [
                fid for fid, chain in zip(chain_fids, truth.get("evidence_chain") or [])
                if cid in (chain.get("clue_ids") or [])
            ]
            if not revealed:
                revealed = [truth_fid]
                completions.append({
                    "rule": "R3_clue_truth_fallback",
                    "field": f"clues[{cid}].reveals_fact_ids",
                    "value": revealed,
                    "basis": "clue not named in evidence_chain; linked to full truth fact",
                })
            if cid in clue_by_id:
                completions.append({
                    "rule": "R6_duplicate_clue_id",
                    "field": "clue.id",
                    "value": f"{cid}_{n}",
                    "basis": "source clue id repeated; suffixed to keep references stable",
                })
                cid = f"{cid}_{n}"
            clue_by_id[cid] = raw.get("content", "")
            clues.append({
                "id": cid,
                "title": _clean(target.get("name") or f"线索{n}"),
                "content": _clean(raw.get("content")),
                "type": "physical",
                "truth_value": True,
                "stage": stage,
                "reveals_fact_ids": revealed,
                "visibility": "finder",
                "stage_source": stage_source,
                "source_target": target.get("id", ""),
            })
            weights = f"logLR:{culprit}={1.5 if stage == 0 else 2.0}"
            line = f"{cid} {_clean(raw.get('content'))[:60]}|{weights}"
            clue_lines.append(line)
            clue_fact_map[line] = revealed
            clue_fact_map[cid] = revealed

    # Soft-gap completion: expose an inquiry prompt without leaking the
    # private fact that supplies the missing link.
    for spec in INQUIRY_GAPS.get(meta.get("script_id", ""), []):
        evidence_text = " ".join(
            f"{c.get('conclusion', '')} {c.get('reasoning', '')}"
            for c in truth.get("evidence_chain") or []
        )
        if any(word in evidence_text for word in spec["keywords"]):
            gid = f"{sid}_gap_inquiry"
            clues.append({
                "id": gid,
                "title": "待核对的证词缺口",
                "content": spec["prompt"],
                "type": "inquiry",
                "truth_value": True,
                "stage": max(0, len((data.get("stage_flow") or {})) - 2),
                "reveals_fact_ids": [truth_fid],
                "visibility": "finder",
                "completion": "R5_inquiry_gap",
            })
            clue_lines.append(f"{gid} {spec['prompt'][:60]}|logLR:{culprit}=2.0")
            clue_fact_map[gid] = [truth_fid]
            completions.append({
                "rule": "R5_inquiry_gap",
                "field": "clues[]",
                "value": gid,
                "basis": "evidence chain names material held only in private memory; prompt added, content not leaked",
            })

    stages = data.get("stage_flow") or {}
    last_key = max(stages, key=lambda k: int(k.split("_")[1]) if k.split("_")[-1].isdigit() else 0)
    decision = (stages[last_key].get("resolution") or {}).get("final_decision") or {}
    correct = _clean(decision.get("correct_verdict")) or "指认成立，真相揭晓。"
    incorrect = _clean(decision.get("incorrect_verdict")) or "指认不成立，真凶未被归责。"

    onto = {
        "schema_version": "0.4",
        "script_id": sid,
        "title": title,
        "brief": f"来源：RolePlayingGame/{meta.get('script_id', sid)}；{len(roles)}人；{len(stages)}幕",
        "world": {
            "mode": "truth_reconstruction",
            "stages": len(stages),
            "player_selectable": ",".join(
                r["role_id"] for r in roles if r.get("human_playable")
            ),
            "source_kind": "RolePlayingGame JSON",
            "source_provenance": meta.get("provenance") or {},
        },
        "truth_graph": [f["content"] for f in facts if f["content"]],
        "facts": facts,
        "timeline_true": timeline,
        "characters": characters,
        "clue_pool": clue_lines,
        "clues": clues,
        "clue_fact_map": clue_fact_map,
        "plot_locks": [_clean(c) for c in data.get("hard_constraints") or []],
        "endings": [f"正确指认：{correct}", f"错误指认：{incorrect}"],
        "evaluation": {
            "killer_id": culprit,
            "required_fact_ids": [truth_fid, f"f_{sid}_victim"] + chain_fids,
            "culprit_basis": "truth.culprit_role_id",
            "role_kind": "declared_killer",
            "has_murder": True,
            "victim_name": victim,
            "victim_basis": victim_basis,
            "source_id": meta.get("script_id", sid),
        },
        "completions": completions,
        "source_files": [source_rel],
    }
    return onto, completions


def validate_corpus_onto(onto):
    checks = {
        "char_count": len(onto.get("characters", [])) >= 2,
        "unique_ids": len({c["id"] for c in onto.get("characters", [])}) == len(onto.get("characters", [])),
        "has_truth": bool(onto.get("truth_graph")),
        "has_clues": bool(onto.get("clues")),
        "has_endings": bool(onto.get("endings")),
        "has_timeline": bool(onto.get("timeline_true")),
        "culprit_in_roles": (
            onto.get("evaluation", {}).get("killer_id")
            in {c["id"] for c in onto.get("characters", [])}
        ),
        "victim_named": bool(onto.get("evaluation", {}).get("victim_name")),
        "clue_refs_valid": all(
            fid in {f["id"] for f in onto.get("facts", [])}
            for c in onto.get("clues", [])
            for fid in c.get("reveals_fact_ids", [])
        ),
    }
    return checks


def _mossvale_entries(source_root):
    entries = []
    for story in MOSSVALE_STORIES:
        files = []
        for rel in story["source_files"]:
            path = source_root / rel
            if path.exists():
                files.append({"path": rel, "sha256": sha256_file(path)})
        entries.append({
            "source_id": story["source_id"],
            "title": story["title"],
            "source_kind": "mossvale JavaScript game",
            "decision": "skip",
            "reasons": [{
                "code": "requires_mouse_scene_search",
                "detail": (
                    "剧情推进依赖房间地图、场景搜证点与手绘物件图标（雾灯未眠 51 条 clues 含 room/spot；"
                    "第7号嫌疑人 45 项 evidence + 34 份证词按章节/房间解锁），不是纯文本可玩本"
                ),
            }],
            "source_files": files,
        })
    return entries


def build_corpus(source_root=None, out_dir=None):
    """Build a project-owned corpus from disposable reference repositories."""
    source_root = Path(source_root or (ROOT / "refer"))
    out_dir = Path(out_dir or (ROOT / "corpus"))
    (out_dir / "raw" / "rpg").mkdir(parents=True, exist_ok=True)
    (out_dir / "scripts").mkdir(parents=True, exist_ok=True)

    entries = []
    rpg_dir = source_root / "RolePlayingGame-backup-20261022" / "backend" / "resources" / "scripts"
    if rpg_dir.is_dir():
        for path in sorted(rpg_dir.glob("*.json")):
            data = read_json(path)
            decision, reasons = admit_rpg(data)
            entry = {
                "source_id": (data.get("script_meta") or {}).get("script_id", path.stem),
                "title": (data.get("script_meta") or {}).get("title", path.stem),
                "source_kind": "RolePlayingGame JSON",
                "source_path": str(path.relative_to(source_root)) if source_root in path.parents else str(path),
                "source_sha256": sha256_file(path),
                "decision": decision,
                "reasons": reasons,
            }
            if decision == "import":
                raw_rel = f"raw/rpg/{path.name}"
                shutil.copy2(path, out_dir / raw_rel)
                onto, completions = parse_rpg_script(data, raw_rel)
                checks = validate_corpus_onto(onto)
                if not all(checks.values()):
                    entry["decision"] = "skip"
                    entry["reasons"] = [{
                        "code": "ontology_validation_failed",
                        "detail": {k: v for k, v in checks.items() if not v},
                    }]
                    (out_dir / raw_rel).unlink(missing_ok=True)
                else:
                    script_rel = f"scripts/{onto['script_id']}.json"
                    write_json(out_dir / script_rel, onto)
                    entry.update({
                        "script_id": onto["script_id"],
                        "script_file": script_rel,
                        "characters": len(onto["characters"]),
                        "clues": len(onto["clues"]),
                        "facts": len(onto["facts"]),
                        "victim": onto["evaluation"]["victim_name"],
                        "culprit": onto["evaluation"]["killer_id"],
                        "completions": completions,
                        "checks": checks,
                    })
            entries.append(entry)

    moss_root = source_root / "mossvale"
    if moss_root.is_dir():
        entries.extend(_mossvale_entries(source_root))

    manifest = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_root": str(source_root),
        "storage": "corpus/raw keeps source bytes; corpus/scripts keeps project-owned Ontology JSON",
        "admission_rules": [
            "skip if scene search needs mouse/map/image recognition",
            "skip if no declared culprit, evidence chain, stage flow or final decision",
            "complete only from source evidence; unresolved gaps are recorded, never invented",
        ],
        "entries": entries,
        "summary": {
            "total": len(entries),
            "import": sum(1 for e in entries if e["decision"] == "import"),
            "skip": sum(1 for e in entries if e["decision"] == "skip"),
        },
    }
    write_json(out_dir / "manifest.json", manifest)
    return manifest


def import_corpus(conn, corpus_dir, config_dir):
    """Store accepted corpus scripts and their read-only L1 persona cards."""
    from . import db, normalize, registry

    corpus_dir = Path(corpus_dir)
    manifest = read_json(corpus_dir / "manifest.json")
    imported, skipped = [], []
    for entry in manifest["entries"]:
        if entry["decision"] != "import":
            skipped.append({
                "source_id": entry["source_id"],
                "title": entry.get("title"),
                "reasons": [r["code"] for r in entry.get("reasons", [])],
            })
            continue
        onto = read_json(corpus_dir / entry["script_file"])
        doc, report = normalize.normalize_persona(onto, Path(config_dir))
        db.save_script(conn, onto)
        db.save_persona_l1(conn, onto["script_id"], doc)
        registry.register(
            conn, onto["script_id"], title=onto.get("title"),
            source_kind=entry.get("source_kind", ""),
            source_sha256=entry.get("source_sha256", ""),
            skeleton_version="0", canonical_version=onto.get("schema_version", ""),
            skeleton_file=entry.get("skeleton_file", ""), script_file=entry.get("script_file", ""),
        )
        skeleton_file = entry.get("skeleton_file")
        if skeleton_file and (corpus_dir / skeleton_file).exists():
            registry.save_skeleton(conn, onto["script_id"], read_json(corpus_dir / skeleton_file))
        registry.set_status(conn, onto["script_id"], "normalized", "L1 persona cards generated")
        imported.append({
            "script_id": onto["script_id"],
            "title": onto["title"],
            "characters": len(onto["characters"]),
            "clues": len(onto["clues"]),
            "victim": onto.get("evaluation", {}).get("victim_name"),
            "culprit": onto.get("evaluation", {}).get("killer_id"),
            "completions": len(onto.get("completions", [])),
            "missing_evidence": len(report.get("missing_evidence", [])),
        })
    return {
        "imported": len(imported),
        "skipped": len(skipped),
        "scripts": imported,
        "skipped_detail": skipped,
    }


def corpus_report(corpus_dir):
    manifest = read_json(Path(corpus_dir) / "manifest.json")
    return {
        "summary": manifest["summary"],
        "entries": [
            {
                "source_id": e["source_id"],
                "title": e.get("title"),
                "decision": e["decision"],
                "reasons": [r["code"] for r in e.get("reasons", [])],
                "script_id": e.get("script_id"),
            }
            for e in manifest["entries"]
        ],
    }
