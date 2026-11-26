"""Agent extraction: rule-driven character profiles read from stored data.

This module is the single entry point for turning a stored script into
per-character data that the runtime and prompt engine consume.  It orchestrates
the existing deterministic chain (Ontology -> L1 envelopes -> derived abilities)
and applies explicit extraction rules.  It invents nothing: any attribute
without evidence stays ``value=None`` with ``missing_evidence``.

Guide prompts drive *variation* at runtime; this module owns the *data* the
guides can talk about.  L1 here is read-only (see persona_l1_no_update trigger).
"""

from . import derive, formulas

CANON_KEYS = (
    "OCEAN.O", "OCEAN.C", "OCEAN.E", "OCEAN.A", "OCEAN.N",
    "values.honesty", "values.loyalty", "values.ambition", "values.justice",
    "health", "physique",
)
DEFAULT_ABILITY = 0.5


def _attrs_from_persona(persona_doc, character_id):
    for p in persona_doc.get("personas", []):
        if p.get("character_id") == character_id:
            return {a["key"]: a for a in p.get("attributes", [])}
    return {}


def _value(envelope):
    if not envelope:
        return None
    return envelope.get("value")


def character_profile(conn, script_id, character_id):
    """Build a complete, auditable profile for one character from the DB.

    Returns canonical values (None when unevidenced), derived abilities, an
    evidence ledger, and the list of gaps.  No random choice, no LLM call.
    """
    from .db import get_persona_l1, get_script

    persona_doc = get_persona_l1(conn, script_id)
    onto = get_script(conn, script_id)
    envelopes = _attrs_from_persona(persona_doc, character_id)

    canon = {k: _value(envelopes.get(k)) for k in CANON_KEYS}
    flat = {k: v for k, v in canon.items() if v is not None}

    abilities = {
        key: round(derive.compute_derived(key, flat, {"acting_skill": DEFAULT_ABILITY}), 4)
        for key in ("observation", "reasoning", "deception", "impulse_control", "social_leverage")
    }
    # Leak sigma is data-driven too: neutral arousal/stress, baseline acting.
    abilities["leak_sigma"] = round(formulas.leak_sigma(
        0.05, 0.0, 0.0, DEFAULT_ABILITY
    ), 4)
    abilities["faction_sensitivity"] = round(flat.get("OCEAN.A", DEFAULT_ABILITY), 4)

    evidence = {}
    gaps = []
    for key, env in envelopes.items():
        if env.get("value") is None or not env.get("evidence"):
            gaps.append({
                "key": key,
                "reason": env.get("reason") or "missing_evidence",
            })
        else:
            evidence[key] = env["evidence"]

    char_meta = {}
    for ch in onto.get("characters", []) if onto else []:
        if ch.get("id") == character_id:
            char_meta = {
                "name": ch.get("name"),
                "public_identity": ch.get("public_identity") or ch.get("role"),
                "known_fact_ids": list(ch.get("known_fact_ids") or []),
            }
            break

    return {
        "script_id": script_id,
        "character_id": character_id,
        "name": char_meta.get("name"),
        "public_identity": char_meta.get("public_identity"),
        "known_fact_ids": char_meta.get("known_fact_ids", []),
        "canon": canon,
        "abilities": abilities,
        "evidence": evidence,
        "gaps": gaps,
        "layer": "L1",
        "read_only": True,
    }


def script_profiles(conn, script_id):
    """Profiles for every character in a stored script, deterministic order."""
    from .db import get_persona_l1

    doc = get_persona_l1(conn, script_id)
    ids = sorted(p["character_id"] for p in doc.get("personas", []))
    return [character_profile(conn, script_id, cid) for cid in ids]


def apply_runtime(state, profile):
    """Overlay runtime L2/L3 state onto a canonical profile for the prompt engine.

    This is a pure view: it does not mutate stored L1.  Canon values missing from
    evidence are filled with runtime-neutral defaults only here, and flagged.
    """
    l1 = dict(profile["canon"])
    neutral = []
    for key, value in list(l1.items()):
        if value is None:
            l1[key] = DEFAULT_ABILITY
            neutral.append(key)
    emotion = dict(state.get("emotion") or {})
    relations = {
        k: dict(v) for k, v in (state.get("relations") or {}).items()
    }
    return {
        "character_id": profile["character_id"],
        "name": profile.get("name"),
        "l1": l1,
        "l0": dict(state.get("l0") or {}),
        "abilities": dict(profile["abilities"]),
        "emotion": emotion,
        "relations": relations,
        "suspicion": dict(state.get("suspicion") or {}),
        "beliefs": dict(state.get("beliefs") or {}),
        "lie_ledger": list(state.get("lie_ledger") or []),
        "memory": list(state.get("memory") or []),
        "neutral_filled": neutral,
        "read_only_canon": True,
    }
