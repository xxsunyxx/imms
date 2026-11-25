"""F29 evidence-first normalization: user script -> read-only L1 persona cards."""

import statistics

from .util import read_json


def domain_for_key(key):
    if key.startswith("skill_set.") or key == "social_rank":
        return [0, 10], 10.0
    if key.startswith("OCEAN.") or key.startswith("values.") or key in {"health", "physique"}:
        return [0, 1], 1.0
    return [-1, 1], 1.0


def score_evidence(items):
    values = [i["anchor_bin"] for i in items]
    weights = [i.get("weight", 1.0) for i in items]
    score = sum(w * v for w, v in zip(weights, values)) / sum(weights)
    std = statistics.pstdev(values) if len(values) > 1 else 0.0
    consistency = 1 - min(1.0, std / 0.3)
    confidence = (len(items) / (len(items) + 2)) * consistency
    return score, confidence


def envelope(key, value, confidence, evidence, domain, reason=None):
    return {
        "key": key,
        "value": round(value, 4) if value is not None else None,
        "layer": "L1",
        "scope": "self",
        "domain": domain,
        "visibility": "self",
        "confidence": round(confidence, 4),
        "source": "canon_extract",
        "evidence": evidence,
        "updated_at_round": 0,
        "updated_by": "pipeline:v1",
        "locked": True,
        "extraction_version": 1,
        "reason": reason,
    }


STANDARD_KEYS = (
    "OCEAN.O", "OCEAN.C", "OCEAN.E", "OCEAN.A", "OCEAN.N",
    "values.honesty", "health", "physique",
    "skill_set.observation", "skill_set.reasoning",
)


def normalize_persona(onto, config_dir):
    anchors = read_json(config_dir / "anchors.json")
    allowed = set(anchors["levels"])
    report = {"unmapped": [], "invalid_bins": [], "insufficient": [], "missing_evidence": []}
    personas = []

    for ch in onto.get("characters", []):
        grouped = {}
        for ev in ch.get("canon_evidence", []):
            key = ev.get("key")
            raw_e = ev.get("raw_e")
            if raw_e not in allowed:
                report["invalid_bins"].append({"character": ch["id"], "key": key, "raw_e": raw_e})
                continue
            grouped.setdefault(key, []).append({
                "source_id": f"ev_{ch['id']}_{len(grouped.get(key, [])) + 1:03d}",
                "quote": ev.get("quote", ""),
                "locator": ev.get("locator", ""),
                "weight": ev.get("weight", 1.0),
                "anchor_bin": raw_e,
            })

        attrs = []
        if not grouped:
            # A script without behavioural evidence is common. Emit explicit
            # null slots instead of fabricating scores; runtime falls back to
            # neutral values and the report names the gap.
            for key in STANDARD_KEYS:
                domain, _ = domain_for_key(key)
                attrs.append(envelope(key, None, 0.0, [], domain, "missing_evidence"))
            report["missing_evidence"].append(ch["id"])
        for key, items in grouped.items():
            domain, factor = domain_for_key(key)
            if len(items) < 2:
                attrs.append(envelope(key, None, 0.0, items, domain, "insufficient_evidence"))
                report["insufficient"].append({"character": ch["id"], "key": key, "n": len(items)})
                continue
            score, confidence = score_evidence(items)
            attrs.append(envelope(key, score * factor, confidence, items, domain))

        personas.append({
            "character_id": ch["id"],
            "name": ch.get("name", ch["id"]),
            "layer": "L1",
            "attributes": attrs,
        })

    return {"schema_version": "0.1", "script_id": onto["script_id"], "personas": personas}, report
