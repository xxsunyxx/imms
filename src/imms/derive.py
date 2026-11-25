"""F30 derived abilities: computed on read, never persisted as independent L1 facts."""

from .db import get_persona_l1


def _attrs_for(persona_doc, character_id):
    for p in persona_doc["personas"]:
        if p["character_id"] == character_id:
            return {a["key"]: a["value"] for a in p["attributes"] if a["value"] is not None}
    raise ValueError("ERR_CHAR_NOT_FOUND")


def compute_derived(key, attrs, l0=None):
    l0 = l0 or {}
    O = attrs.get("OCEAN.O", 0.5)
    C = attrs.get("OCEAN.C", 0.5)
    A = attrs.get("OCEAN.A", 0.5)
    N = attrs.get("OCEAN.N", 0.5)
    skill = lambda k: attrs.get(f"skill_set.{k}", 5) / 10.0

    if key == "observation":
        return 0.6 * O + 0.4 * skill("observation")
    if key == "reasoning":
        return 0.5 * O + 0.3 * C + 0.2 * skill("reasoning")
    if key == "deception":
        return 0.5 * (1 - A) + 0.3 * C + 0.2 * l0.get("acting_skill", 0.5)
    if key == "impulse_control":
        return 0.6 * C + 0.4 * (1 - N)
    if key == "nu":
        return 0.5 + N
    if key == "leak_sigma":
        sigma0 = l0.get("sigma0", 0.05)
        g1 = l0.get("gamma1", 0.5)
        g2 = l0.get("gamma2", 0.5)
        arousal = l0.get("arousal", 0.0)
        stress = l0.get("stress", 0.0)
        acting = l0.get("acting_skill", 0.5)
        return sigma0 * (1 + g1 * arousal) * (1 + g2 * stress) * (1 - acting)
    if key == "T_v":
        T0 = l0.get("T0", 1.0)
        stress = l0.get("stress", 0.0)
        return T0 * (1 + 0.6 * stress) / (1 + 0.5 * C)
    if key == "tech_craft":
        return 0.7 * skill("craft") + 0.3 * O
    if key == "combat_effective":
        physique = attrs.get("physique", 0.5)
        return 0.6 * skill("combat") + 0.2 * physique + 0.2 * (1 - N)
    if key == "social_leverage":
        E = attrs.get("OCEAN.E", 0.5)
        return 0.4 * E + 0.3 * skill("eloquence") + 0.3 * (1 - A)
    raise ValueError("ERR_DERIVATION_NOT_FOUND")


COMPARE_KEYS = [
    "OCEAN.O", "OCEAN.C", "OCEAN.E", "OCEAN.A", "OCEAN.N",
    "values.loyalty", "values.honesty", "values.ambition",
    "skill_set.observation", "skill_set.reasoning", "skill_set.craft",
    "skill_set.combat", "skill_set.eloquence",
    "observation", "reasoning", "deception", "impulse_control", "nu",
    "tech_craft", "combat_effective", "social_leverage",
]


def _value_for(key, attrs, l0):
    if key in attrs:
        return round(attrs[key], 4)
    if key.startswith(("OCEAN.", "values.", "skill_set.")):
        return None
    return round(compute_derived(key, attrs, l0), 4)


def compare_from_db(conn, script_id, char_a, char_b):
    doc = get_persona_l1(conn, script_id)
    attrs_a = _attrs_for(doc, char_a)
    attrs_b = _attrs_for(doc, char_b)
    l0 = {"acting_skill": 0.5}
    return {
        "script_id": script_id,
        "a": char_a,
        "b": char_b,
        "dimensions": [
            {"key": k, "a": _value_for(k, attrs_a, l0), "b": _value_for(k, attrs_b, l0)}
            for k in COMPARE_KEYS
        ],
    }


def derive_from_db(conn, script_id, character_id, key):
    doc = get_persona_l1(conn, script_id)
    return compute_derived(key, _attrs_for(doc, character_id))
