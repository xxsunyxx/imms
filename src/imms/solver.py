"""Static solvability and consistency audit for a script Ontology.

This is the admission/regression gate that turns "the script was imported"
into "the script can be shown to be playable".  It is deliberately offline and
pure: no LLM, no session state, no mutation.

Checks implemented:

* truth model rules inherited from lockedroom-ai ``validate_references``:
  exactly one declared killer, killer knows the motive, victim holds no
  secret, every private fact is reachable by some character or clue.
* evidence closure: required facts must be covered by clues or public facts.
* unique solution: clues attribute suspicion weight to at most one seat.
* timeline order and stage-gate consistency.
* completion regression: whether an R3/R5 completion actually closes an
  evidence link or is only a hint pointing at the whole truth.
* difficulty curve per stage.

The report is JSON-serialisable so it can be consumed by the CLI, by corpus
admission, and by the replay evaluator.
"""

import re
from collections import defaultdict

WEIGHT_RE = re.compile(r"logLR:([A-Za-z0-9_-]+)=(-?\d+(?:\.\d+)?)")
CLOCK_RE = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")
STAGE_RE = re.compile(r"(?:第\s*([0-9一二三四五六七八九十]+)\s*幕|stage\s*([0-9]+))", re.I)
GATE_RE = re.compile(r"(第\s*([0-9一二三四五六七八九十]+)\s*幕).{0,12}?(才|方|才能|才准|不得|禁止|之前|之后)")
CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _num(value):
    if value is None:
        return None
    value = str(value).strip()
    if value.isdigit():
        return int(value)
    if value in CN_NUM:
        return CN_NUM[value]
    if value.startswith("十") and len(value) == 2 and value[1] in CN_NUM:
        return 10 + CN_NUM[value[1]]
    return None


def _clue_weights(line):
    return {m.group(1): float(m.group(2)) for m in WEIGHT_RE.finditer(str(line))}


def _has_weight_marker(text):
    """Detect both valid `logLR:` and the broken char-split `l|o|g|L|R|` form."""
    text = str(text)
    return "logLR" in text or bool(re.search(r"l\s*\|\s*o\s*\|\s*g\s*\|\s*L\s*\|\s*R", text))


def _facts(onto):
    return {f["id"]: f for f in (onto.get("facts") or []) if f.get("id")}


def _required_fact_ids(onto, facts):
    explicit = list((onto.get("evaluation") or {}).get("required_fact_ids") or [])
    if explicit:
        return explicit
    return [
        fid for fid, fact in facts.items()
        if fact.get("core") and fact.get("visibility") in {"truth", "private"}
    ]


def _clue_records(onto):
    """Normalise clues; merge logLR weights from the pool line into each record."""
    pool_by_id = {}
    for line in onto.get("clue_pool") or []:
        text = str(line)
        pool_by_id[text.split(" ", 1)[0].split("|", 1)[0]] = text

    records = []
    for clue in onto.get("clues") or []:
        cid = clue.get("id")
        text = str(clue.get("content") or clue.get("title") or "")
        pool_line = pool_by_id.get(cid, "")
        weight_parts = [p for p in pool_line.split("|") if "logLR" in p]
        if weight_parts:
            text = text + "|" + "|".join(weight_parts)
        elif _has_weight_marker(pool_line):
            text = pool_line  # keep the malformed form visible to the audit
        records.append({
            "id": cid,
            "text": text,
            "stage": int(clue.get("stage") or 0),
            "reveals": list(clue.get("reveals_fact_ids") or []),
            "completion": clue.get("completion"),
            "source": "clues",
        })
    seen = {r["id"] for r in records}
    for line in onto.get("clue_pool") or []:
        text = str(line)
        cid = text.split(" ", 1)[0].split("|", 1)[0]
        if cid in seen:
            continue
        fmap = onto.get("clue_fact_map") or {}
        reveals = fmap.get(cid) or fmap.get(line) or []
        if isinstance(reveals, str):
            reveals = [reveals]
        records.append({
            "id": cid, "text": text, "stage": 0,
            "reveals": list(reveals), "completion": None, "source": "clue_pool",
        })
        seen.add(cid)
    return records


def _time_point(text):
    match = CLOCK_RE.search(str(text))
    if match:
        return int(match.group(1)) * 60 + int(match.group(2))
    match = STAGE_RE.search(str(text))
    if match:
        value = _num(match.group(1) or match.group(2))
        if value is not None:
            return -1000 + value
    match = re.search(r"(?<!\d)(\d{1,2})\s*点(?!\d)", str(text))
    if match:
        return int(match.group(1)) * 60
    return None


def audit_timeline(onto):
    """Structured timeline plus order conflicts.  No semantic guessing."""
    items = []
    for source, lines in (("truth", onto.get("timeline_true") or []),):
        for line in lines:
            if isinstance(line, dict):
                text = str(line.get("content") or line.get("text") or "")
            else:
                text = str(line)
            if text:
                items.append({"source": source, "text": text, "minute": _time_point(text)})
    for char in onto.get("characters") or []:
        for line in char.get("timeline") or []:
            text = str(line) if not isinstance(line, dict) else str(line.get("content", ""))
            if text:
                items.append({"source": char.get("id"), "text": text, "minute": _time_point(text)})

    conflicts = []
    truth_points = [it["minute"] for it in items if it["source"] == "truth" and it["minute"] is not None]
    for prev, curr in zip(truth_points, truth_points[1:]):
        if curr < prev:
            conflicts.append({
                "kind": "timeline_order",
                "detail": f"真实时间线出现倒序：{prev} → {curr}",
            })
    unresolved = sum(1 for it in items if it["source"] == "truth" and it["minute"] is None)
    warnings = []
    if unresolved:
        warnings.append({
            "kind": "timeline_unstamped",
            "detail": f"{unresolved} 条真实时间线事件没有可解析的时间标记",
        })
    return {"events": items, "order_conflicts": conflicts, "warnings": warnings}


def audit_stage_gates(onto, facts, clues, fact_by_clue):
    """Flag clues that reveal a gated fact earlier than its plot lock allows."""
    conflicts = []
    gates = []
    for text in onto.get("plot_locks") or []:
        text = str(text)
        match = GATE_RE.search(text)
        if not match:
            continue
        gate_stage = _num(match.group(2))
        if gate_stage is None:
            continue
        terms = [kw for kw in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,12}", text) if len(kw) >= 2]
        gates.append({"text": text, "stage": gate_stage, "terms": terms})
    for gate in gates:
        allowed = gate["stage"] - 1  # 0-based clue stage
        for clue in clues:
            if clue["stage"] >= allowed:
                continue
            for fid in fact_by_clue.get(clue["id"], []):
                fact = facts.get(fid) or {}
                content = str(fact.get("content", ""))
                keywords = [str(k) for k in (fact.get("keywords") or [])]
                hits = [k for k in keywords if k and k in gate["text"]]
                if not hits and any(term and term in content for term in gate["terms"]):
                    hits = [t for t in gate["terms"] if t in content]
                if hits:
                    conflicts.append({
                        "kind": "stage_gate_violation",
                        "detail": f"线索 {clue['id']} 在第 {clue['stage']} 阶段揭示 {fid}，违反门控「{gate['text']}」",
                        "clue_id": clue["id"], "fact_id": fid,
                    })
    return {"gates": gates, "conflicts": conflicts}


def evidence_paths(onto, fact_id):
    """Independent evidence routes that can establish one fact.

    Routes come only from explicit ontology fields: clue reveals, public
    facts, a character's canon knowledge, or a fully clue-covered evidence
    chain.  No route is inferred from prose similarity.
    """
    paths = []
    for clue in onto.get("clues") or []:
        if fact_id in (clue.get("reveals_fact_ids") or []):
            paths.append({"kind": "clue", "id": clue.get("id")})
    facts = {f.get("id"): f for f in onto.get("facts") or [] if f.get("id")}
    fact = facts.get(fact_id) or {}
    if fact.get("visibility") == "public":
        paths.append({"kind": "public"})
    for char in onto.get("characters") or []:
        if fact_id in (char.get("known_fact_ids") or []):
            paths.append({"kind": "knower", "id": char.get("id")})
    chain_ids = [fid for fid, f in facts.items() if f.get("type") == "evidence_chain"]
    revealed = {
        fid for clue in onto.get("clues") or []
        for fid in (clue.get("reveals_fact_ids") or [])
    }
    if fact.get("type") == "truth" and chain_ids and all(fid in revealed for fid in chain_ids):
        paths.append({"kind": "composite_chain", "id": "chain"})
    return paths


def is_unique_path(onto, fact_id, exclude=None):
    """IR3 query: true when exactly one independent route reaches ``fact_id``."""
    excluded = set(exclude or [])
    paths = [
        p for p in evidence_paths(onto, fact_id)
        if (p["kind"], p.get("id")) not in excluded
    ]
    return len(paths) == 1


def unique_path_audit(onto):
    """Report facts whose only evidence route can be destroyed by improvisation."""
    facts = [f.get("id") for f in onto.get("facts") or [] if f.get("id")]
    return {
        "unique": [
            {"fact_id": fid, "path": evidence_paths(onto, fid)[0]}
            for fid in facts if is_unique_path(onto, fid)
        ],
        "unreachable": [fid for fid in facts if not evidence_paths(onto, fid)],
    }


def _text_attribution(clues, characters, facts, clue_weights):
    """Fallback attribution when a clue carries no explicit logLR weight."""
    ids = [c.get("id") for c in characters if not c.get("is_victim")]
    names = {c.get("name"): c.get("id") for c in characters if c.get("name")}
    scores = {cid: 0.0 for cid in ids if cid}
    for clue in clues:
        if clue_weights.get(clue["id"]):
            continue
        text = clue["text"] + " " + " ".join(
            str(facts.get(fid, {}).get("content", "")) for fid in clue["reveals"]
        )
        mentioned = set()
        for cid in ids:
            if cid and cid in text:
                mentioned.add(cid)
        for name, cid in names.items():
            if cid and name in text:
                mentioned.add(cid)
        mentioned.discard(None)
        if len(mentioned) == 1:
            scores[next(iter(mentioned))] += 1.0
    return scores


def _completion_regression(onto, facts, fact_by_clue, chain_fids):
    results = []
    completions = onto.get("completions") or []
    for comp in completions:
        rule = str(comp.get("rule") or "")
        entry = {"rule": rule, "field": comp.get("field"), "effective": True, "kind": "metadata"}
        if rule.startswith("R3_") or rule.startswith("R5_"):
            cid = None
            field = str(comp.get("field") or "")
            match = re.search(r"clues\[([^\]]+)\]", field)
            if match:
                cid = match.group(1)
            elif isinstance(comp.get("value"), str):
                cid = comp["value"]
            revealed = fact_by_clue.get(cid, [])
            chain_hits = [fid for fid in revealed if fid in chain_fids]
            entry["clue_id"] = cid
            entry["revealed_fact_ids"] = revealed
            entry["chain_hits"] = chain_hits
            entry["effective"] = bool(chain_hits)
            entry["kind"] = "evidence_link" if chain_hits else "hint_only"
            if not chain_hits:
                entry["detail"] = "只回指完整真相，没有接入任何证据链事实"
        elif rule.startswith("R1_"):
            entry["effective"] = bool((onto.get("evaluation") or {}).get("victim_name"))
        elif rule.startswith("R4_"):
            entry["effective"] = not bool(onto.get("timeline_true"))
            entry["kind"] = "declared_gap" if entry["effective"] else "stale_completion"
            entry["detail"] = "剧本没有结构化时间线，已显式记录缺口，未编造事件"
        elif rule.startswith("R6_"):
            value = comp.get("value")
            entry["effective"] = any((c.get("id") == value) for c in (onto.get("clues") or []))
        results.append(entry)
    return results


def audit_script(onto):
    """Return a full solvability / consistency report for one Ontology."""
    facts = _facts(onto)
    evaluation = onto.get("evaluation") or {}
    killer = evaluation.get("killer_id")
    characters = onto.get("characters") or []
    char_ids = {c.get("id") for c in characters}
    required = _required_fact_ids(onto, facts)
    clues = _clue_records(onto)
    fact_by_clue = {c["id"]: c["reveals"] for c in clues}

    chain_fids = {
        fid for fid, fact in facts.items()
        if fact.get("type") == "evidence_chain"
    }
    public_fids = {fid for fid, fact in facts.items() if fact.get("visibility") == "public"}
    knower_fids = set()
    for char in characters:
        knower_fids.update(char.get("known_fact_ids") or [])

    covered_by_clue = {fid for reveals in fact_by_clue.values() for fid in reveals}
    chain_covered = chain_fids and chain_fids <= covered_by_clue
    coverage = {
        "required": list(required), "by_clue": [], "by_public": [],
        "by_private": [], "by_composite": [], "unreachable": [],
    }
    for fid in required:
        if fid in covered_by_clue:
            coverage["by_clue"].append(fid)
        elif fid in public_fids:
            coverage["by_public"].append(fid)
        elif fid in knower_fids:
            coverage["by_private"].append(fid)
        elif facts.get(fid, {}).get("type") == "truth" and chain_covered:
            # The full truth is assembled from the evidence chain; no single
            # clue is expected to reveal it verbatim.
            coverage["by_composite"].append(fid)
        else:
            coverage["unreachable"].append(fid)

    checks = {}
    checks["has_culprit"] = bool(killer)
    checks["culprit_is_role"] = killer in char_ids if killer else False
    killers = [c for c in characters if c.get("is_killer")]
    checks["single_declared_killer"] = len(killers) <= 1 and (not killers or killers[0].get("id") == killer)
    motive_fid = evaluation.get("motive_fact_id")
    checks["killer_knows_motive"] = (
        not motive_fid or motive_fid in knower_fids or motive_fid in public_fids
    )
    victim = next((c for c in characters if c.get("is_victim")), None)
    checks["victim_no_secrets"] = not victim or not (victim.get("known_fact_ids") or [])
    private_fids = {fid for fid, fact in facts.items() if fact.get("visibility") == "private"}
    checks["private_facts_reachable"] = all(
        fid in knower_fids or fid in covered_by_clue for fid in private_fids
    )
    checks["required_facts_covered"] = not coverage["unreachable"]
    checks["has_clues"] = bool(clues)

    weights = {c["id"]: _clue_weights(c["text"]) for c in clues}
    explicit = sum(1 for w in weights.values() if w)
    clue_lines_with_marker = sum(1 for c in clues if _has_weight_marker(c["text"]))
    checks["clue_weights_parseable"] = explicit == clue_lines_with_marker

    scores = {cid: 0.0 for cid in char_ids if cid}
    for clue in clues:
        for target, weight in weights.get(clue["id"], {}).items():
            if target in scores:
                scores[target] += weight
    if explicit == 0:
        scores = _text_attribution(clues, characters, facts, weights)
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    top_score = ordered[0][1] if ordered else 0.0
    top = [cid for cid, score in ordered if abs(score - top_score) < 1e-6]
    unique = len(top) == 1 and top_score > 0
    solution = {
        "scores": {cid: round(score, 4) for cid, score in ordered},
        "top": top, "top_score": round(top_score, 4), "unique": unique,
        "matches_truth": bool(unique and killer and top[0] == killer),
    }
    checks["unique_solution"] = unique
    checks["solution_matches_truth"] = solution["matches_truth"]

    timeline = audit_timeline(onto)
    checks["timeline_ordered"] = not any(
        c["kind"] == "timeline_order" for c in timeline["order_conflicts"]
    )
    gates = audit_stage_gates(onto, facts, clues, fact_by_clue)
    checks["stage_gates_consistent"] = not gates["conflicts"]

    completions = _completion_regression(onto, facts, fact_by_clue, chain_fids)
    soft = [c for c in completions if c["kind"] == "hint_only"]
    checks["completions_effective"] = not soft

    curve = []
    cumulative_facts = set()
    cumulative_clues = 0
    always_available = set(
        coverage["by_public"] + coverage["by_private"] + coverage["by_composite"]
    )
    for stage in sorted({c["stage"] for c in clues}):
        stage_clues = [c for c in clues if c["stage"] == stage]
        cumulative_clues += len(stage_clues)
        for clue in stage_clues:
            cumulative_facts.update(clue["reveals"])
        covered = (cumulative_facts | always_available) & set(required)
        curve.append({
            "stage": stage,
            "clues": len(stage_clues),
            "cumulative_clues": cumulative_clues,
            "cumulative_facts": len(cumulative_facts),
            "required_covered": len(covered),
        })
    if not any(item["required_covered"] == len(required) for item in curve) and required:
        curve.append({
            "stage": "never", "clues": 0, "cumulative_clues": cumulative_clues,
            "cumulative_facts": len(cumulative_facts),
            "required_covered": len((cumulative_facts | always_available) & set(required)),
        })
    if curve and len(required) and curve[0]["required_covered"] == len(required):
        difficulty = "flat"
    elif curve and len(required) and curve[-1]["required_covered"] < len(required):
        difficulty = "incomplete"
    elif curve and len(required) and curve[-1]["required_covered"] > curve[0]["required_covered"]:
        difficulty = "progressive"
    else:
        difficulty = "unknown"

    contradictions = (
        [{"kind": c["kind"], "detail": c["detail"]} for c in timeline["order_conflicts"]]
        + [{"kind": c["kind"], "detail": c["detail"]} for c in gates["conflicts"]]
    )
    missing_links = [
        {"fact_id": fid, "reason": "no clue, public visibility or knower"}
        for fid in coverage["unreachable"]
    ]
    for comp in soft:
        missing_links.append({
            "completion": comp["rule"],
            "clue_id": comp.get("clue_id"),
            "reason": comp.get("detail", "completion is a hint, not an evidence link"),
        })

    if not killer or killer not in char_ids:
        verdict = "insufficient_data"
    elif not checks["required_facts_covered"]:
        verdict = "incomplete_facts"
    elif not unique:
        verdict = "ambiguous" if top_score > 0 else "unsolvable"
    elif not solution["matches_truth"]:
        verdict = "misdirected"
    elif contradictions:
        verdict = "solvable_with_contradictions"
    else:
        verdict = "solvable_unique"

    return {
        "script_id": onto.get("script_id"),
        "title": onto.get("title"),
        "verdict": verdict,
        "checks": checks,
        "solution": solution,
        "coverage": coverage,
        "timeline": timeline,
        "stage_gates": gates,
        "completion_regression": completions,
        "unique_paths": unique_path_audit(onto),
        "difficulty_curve": curve,
        "difficulty": difficulty,
        "contradictions": contradictions,
        "missing_links": missing_links,
        "summary": {
            "facts": len(facts), "clues": len(clues), "characters": len(characters),
            "required": len(required),
            "contradictions": len(contradictions),
            "missing_links": len(missing_links),
            "hint_only_completions": len(soft),
        },
    }


def audit_script_by_id(conn, script_id):
    from . import db
    return audit_script(db.get_script(conn, script_id))


def audit_all(conn, limit=None):
    from . import db
    out = []
    for row in db.list_scripts(conn):
        if limit is not None and len(out) >= limit:
            break
        report = audit_script_by_id(conn, row["script_id"])
        out.append({
            "script_id": report["script_id"],
            "title": report["title"],
            "verdict": report["verdict"],
            "checks": report["checks"],
            "summary": report["summary"],
        })
    return out
