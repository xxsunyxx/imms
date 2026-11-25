"""Parse the 100 boxed Chinese scripts (盒装本) into the IMMS Ontology.

A box contains 本盒完整内容.md (GM master copy) with five fixed parts:
说明书 / 线索卡清单 / 角色剧本 / 主持人手册 / 结局卡.  Role scripts carry
exactly the private-vs-public split we need for InfoGuard facts, so no
invented attributes are added here: missing behavioural evidence stays null
after L1 normalization.
"""

import re
from pathlib import Path

ROLE_RE = re.compile(r"^③\s*角色剧本\s*[·•:：]\s*(.+?)\s*$")
GM_RE = re.compile(r"^④\s*主持人手册")
END_RE = re.compile(r"^⑤\s*结局卡")
FIELD_RE = re.compile(r"^([一二三四五六七八九十]+)、\s*(.+?)\s*$")
FIELD_KEYS = {
    "一": "identity", "二": "public_background", "三": "stance", "四": "secret",
    "五": "goal", "六": "opening_info", "七": "acts", "八": "hide_hunt",
    "九": "intro", "十": "reminder",
}
SHADED = re.compile(r"<!--\s*学生评审名片\s*(START|END)\s*-->")


def _heads(lines):
    return [(i, s[3:].strip()) for i, s in enumerate(lines) if s.startswith("## ")]


def _clean(lines):
    out = []
    for raw in lines:
        s = raw.strip()
        if not s or s == "---":
            continue
        s = re.sub(r"^>\s?", "", s).replace("**", "")
        out.append(s)
    return "\n".join(out).strip()


def _block(lines, heads, index, stop):
    end = len(lines)
    for idx, _ in heads:
        if idx > index:
            end = min(end, idx)
            break
    return _clean(lines[index + 1:end]), min(end, stop)


def _fields(lines, heads, start, stop):
    result = {}
    for idx, heading in heads:
        if not start < idx < stop:
            continue
        m = FIELD_RE.match(heading)
        if not m:
            continue
        key = FIELD_KEYS.get(m.group(1)) or m.group(2)
        result[key] = _block(lines, heads, idx, stop)[0]
    return result


def _keywords(text, limit=10):
    quoted = re.findall(r"[「『“\"]([^」』”\"]{2,16})[」』”\"]", text)
    segments = re.split(r"[，。；：、（）()\s\-\|]", text)
    plain = [s.strip("># ") for s in segments if 3 <= len(s) <= 16 and re.search(r"[\u4e00-\u9fff]", s)]
    seen, out = set(), []
    for item in quoted + plain:
        if item not in seen:
            seen.add(item)
            out.append(item)
        if len(out) >= limit:
            break
    return out


def _shingles(text, n=6):
    s = re.sub(r"\s+", "", str(text or ""))
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def _header(lines):
    for line in lines[:6]:
        if "编号" in line and "｜" in line:
            parts = [p.strip() for p in line.lstrip("> ").split("｜")]
            sid = re.sub(r"^编号\s*", "", parts[0]).strip()
            writer = re.sub(r"^写手\s*", "", parts[3]).strip() if len(parts) > 3 else ""
            count = re.search(r"(\d+)\s*人", parts[4]).group(1) if len(parts) > 4 else "0"
            return {
                "script_id": sid, "genre": parts[1] if len(parts) > 1 else "",
                "theme": parts[2] if len(parts) > 2 else "", "writer": writer,
                "players": int(count), "duration": parts[5] if len(parts) > 5 else "",
                "difficulty": parts[6] if len(parts) > 6 else "",
            }
    raise ValueError("ERR_BOXED_NO_HEADER")


def _title(lines):
    for line in lines[:3]:
        m = re.search(r"《([^》]+)》", line)
        if m:
            return m.group(1)
    return "untitled"


def _numbered(text):
    out = []
    for line in text.splitlines():
        m = re.match(r"^\s*(\d+)\.\s*(.+)$", line)
        if m:
            out.append((int(m.group(1)), m.group(2).strip()))
    return out


def _stage_lines(text):
    stages = {}
    for line in text.splitlines():
        m = re.search(r"线索\s*(\d+).*?第([一二三])幕", line)
        if m:
            stages[int(m.group(1))] = {"一": 0, "二": 1, "三": 2}[m.group(2)]
    return stages


def _culprit(chars, truth_text, twist_text):
    """Heuristic core-truth bearer — these boxes are not murder cases.

    Counts each character's full name and distinctive suffix in the GM truth
    section.  The basis is recorded in evaluation; it is a playable target for
    the vote, not a claim that the script has a murderer.
    """
    corpus = truth_text + "\n" + twist_text
    counts = []
    for idx, char in enumerate(chars):
        aliases = [char["name"]]
        aliases += [p for p in re.split(r"[·•\s]", char["name"]) if len(p) >= 2]
        counts.append((sum(corpus.count(a) for a in aliases), -idx, char["id"]))
    counts.sort(reverse=True)
    return counts[0][2] if counts else (chars[0]["id"] if chars else None)


def parse_boxed_dir(box_dir):
    """Parse one 盒装本 directory into an Ontology dict."""
    root = Path(box_dir)
    full = root / "本盒完整内容.md"
    if not full.exists():
        raise ValueError("ERR_BOXED_NO_MASTER_FILE")
    text = SHADED.sub("", full.read_text(encoding="utf-8"))
    lines = text.splitlines()
    heads = _heads(lines)
    info = _header(lines)
    sid = info["script_id"]
    low = sid.lower()

    role_idx = [i for i, h in heads if ROLE_RE.match(h)]
    gm_idx = next((i for i, h in heads if GM_RE.match(h)), len(lines))
    end_idx = next((i for i, h in heads if END_RE.match(h)), len(lines))
    if not role_idx:
        raise ValueError("ERR_BOXED_NO_ROLES")

    # Clues + reveal timing (both live in the ② section before the first role).
    clue_text = timing_text = ""
    for i, h in heads:
        if i >= role_idx[0]:
            break
        if "线索卡内容" in h:
            clue_text = _block(lines, heads, i, role_idx[0])[0]
        if "建议揭示时机" in h:
            timing_text = _block(lines, heads, i, role_idx[0])[0]
    clue_rows = _numbered(clue_text)
    if not clue_rows:
        raise ValueError("ERR_BOXED_NO_CLUES")
    stages = _stage_lines(timing_text)

    # Roles and their private facts.
    chars, facts, secret_ids = [], [], []
    public_background = ""
    role_boundaries = role_idx + [gm_idx]
    for n, start in enumerate(role_idx):
        stop = role_boundaries[n + 1]
        name = ROLE_RE.match(next(h for i, h in heads if i == start)).group(1).strip()
        fields = _fields(lines, heads, start, stop)
        cid = f"{low}_r{n + 1:02d}"
        if not public_background:
            public_background = fields.get("public_background", "")
        fid = f"f_{low}_s{n + 1:02d}"
        secret = fields.get("secret", "")
        chars.append({
            "id": cid, "name": name,
            "role": fields.get("identity", ""),
            "public_identity": fields.get("identity", ""),
            "stance": fields.get("stance", ""),
            "secret": secret,
            "goals": [fields.get("goal", "")] if fields.get("goal") else [],
            "acts": fields.get("acts", ""),
            "hide_hunt": fields.get("hide_hunt", ""),
            "intro": fields.get("intro", ""),
            "personality": fields.get("stance", ""),
            "speaking_style": fields.get("intro", ""),
            "lexicon": "", "canon_evidence": [],
            "known_fact_ids": [fid],
            "is_victim": False, "is_killer": False,
        })
        aliases = [p for p in re.split(r"[·•\s]", name) if len(p) >= 2]
        facts.append({
            "id": fid, "type": "secret", "content": secret,
            "visibility": "private", "core": True,
            "keywords": _keywords(secret) + aliases, "owner": cid,
        })
        secret_ids.append(fid)
        facts.append({
            "id": f"f_{low}_role{n + 1:02d}", "type": "identity",
            "content": fields.get("identity", ""), "visibility": "public",
            "keywords": _keywords(fields.get("identity", "")), "owner": cid,
        })

    facts.insert(0, {
        "id": f"f_{low}_pub", "type": "background", "content": public_background,
        "visibility": "public", "core": True, "keywords": _keywords(public_background),
    })

    # GM truth + twist + ending.
    gm_fields = _fields(lines, heads, gm_idx, end_idx)
    truth_text = gm_fields.get("truth", "")
    twist_text = gm_fields.get("twist", "")
    ending_text = ""
    for i, h in heads:
        if i > end_idx and "真相揭晓" in h:
            ending_text = _block(lines, heads, i, len(lines))[0]
            break
    if not ending_text:
        ending_text = truth_text
    for key, label in (("truth", "完整真相"), ("twist", "反转与高潮")):
        body = truth_text if key == "truth" else twist_text
        facts.append({
            "id": f"f_{low}_{key}", "type": key, "content": body,
            "visibility": "truth", "core": True, "keywords": _keywords(body),
        })

    culprit = _culprit(chars, truth_text, twist_text)
    for c in chars:
        c["is_killer"] = c["id"] == culprit

    # Clues: text, stage, and which facts each one makes discussable.
    clues, clue_lines, clue_fact_map = [], [], {}
    revealable = [f for f in facts if f["visibility"] in {"private", "truth"}]
    knowers = {}
    for c in chars:
        for fid in c.get("known_fact_ids", []):
            knowers.setdefault(fid, []).append(c["id"])
    for n, (number, content) in enumerate(clue_rows, 1):
        cid = f"{low}_c{n:02d}"
        revealed = []
        sh = _shingles(content)
        for fact in revealable:
            if any(k in content for k in fact.get("keywords", [])) or (sh & _shingles(fact.get("content", ""))):
                revealed.append(fact["id"])
        if not revealed:
            revealed = [f"f_{low}_truth"]
        stage = stages.get(number, 2)
        targets = []
        for fid in revealed:
            for owner in knowers.get(fid, [culprit]):
                if owner and owner not in targets:
                    targets.append(owner)
        weight = 1.5 if stage == 0 else 2.0
        suffix = "|".join(f"logLR:{t}={weight}" for t in targets if t)
        line = f"{cid} {content[:60]}|{suffix}"
        clue_lines.append(line)
        clue_fact_map[line] = revealed
        clue_fact_map[cid] = revealed
        clues.append({
            "id": cid, "title": f"线索{n}", "content": content,
            "type": "physical", "truth_value": True, "stage": stage,
            "reveals_fact_ids": revealed, "visibility": "finder",
            "source_images": ["线索卡.png"],
        })

    plot_locks = [ln.strip("- ") for c in chars for ln in c["acts"].splitlines() if ln.strip().startswith("- ")]
    return {
        "schema_version": "0.3",
        "script_id": sid,
        "title": _title(lines),
        "brief": f"{info['genre']} · {info['theme']}｜{info['players']}人｜{info['difficulty']}",
        "world": {
            "mode": "truth_reconstruction", "genre": info["genre"],
            "theme": info["theme"], "writer": info["writer"],
            "players": info["players"], "duration": info["duration"],
            "difficulty": info["difficulty"],
            "player_selectable": ",".join(c["id"] for c in chars),
        },
        "truth_graph": [f["content"] for f in facts if f["content"]],
        "facts": facts,
        "timeline_true": [ln.strip("- ") for ln in gm_fields.get("acts", "").splitlines() if ln.strip().startswith("- ")],
        "characters": chars,
        "clue_pool": clue_lines,
        "clues": clues,
        "clue_fact_map": clue_fact_map,
        "plot_locks": plot_locks[:12],
        "endings": [f"主结局：{ending_text[:600]}", "分支结局：按《结局卡》条件念读"],
        "evaluation": {
            "killer_id": culprit,
            "required_fact_ids": secret_ids + [f"f_{low}_truth", f"f_{low}_twist"],
            "culprit_basis": "truth_text_name_frequency",
            "role_kind": "core_truth_bearer",
            "has_murder": False,
        },
        "source_files": [str(full)] + [str(p) for p in sorted(root.rglob("*.png"))],
    }


def list_boxes(root):
    root = Path(root)
    if not root.is_dir():
        raise ValueError("ERR_BOXED_DIR_NOT_FOUND")
    return sorted(p for p in root.iterdir() if p.is_dir() and (p / "本盒完整内容.md").exists())


def import_boxes(conn, root, config_dir, limit=None):
    """Parse and store boxed scripts + read-only L1 cards.

    The printed 线索卡.png was spot-checked against 线索卡清单.md (C01/H01:
    verbatim match), so the markdown list is the clue source of truth.  Image
    paths are kept on each clue as provenance, not re-OCR'd on import.
    """
    from . import db, normalize

    results = []
    for box in list_boxes(root)[:limit]:
        onto = parse_boxed_dir(box)
        doc, report = normalize.normalize_persona(onto, config_dir)
        db.save_script(conn, onto)
        db.save_persona_l1(conn, onto["script_id"], doc)
        results.append({
            "script_id": onto["script_id"],
            "title": onto["title"],
            "genre": onto["world"]["genre"],
            "characters": len(onto["characters"]),
            "clues": len(onto["clues"]),
            "facts": len(onto["facts"]),
            "culprit": onto["evaluation"]["killer_id"],
            "missing_evidence": len(report.get("missing_evidence", [])),
        })
    return {"imported": len(results), "scripts": results}
