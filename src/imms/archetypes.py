"""Import anonymized, evidence-backed trait cards into the persona database.

The cards in ``config/archetypes.json`` are distilled from public character
profiles: only behaviour traits and a handful of speech habits survive. Source
character names, work titles and plot facts are deliberately absent.  A digest
guard rejects any name-like token that reappears, so the database can be shared
without carrying the original identities.
"""

import hashlib
import re

from . import db, normalize, profile, registry

# SHA-256 prefixes of the source names and titles.  Digests, not plaintext, so
# the exclusion list itself does not reintroduce the identities.
FORBIDDEN_DIGESTS = frozenset({
    "dc03101373f796ac", "90ca98a8403001bb", "e49e71f0bc2e685d",
    "c589b1ee821ae999", "efd335e6b991e791", "cfcd60302b61b4e7",
    "3faa4fbd811b7b2c", "e9ec9ac18b1b447a", "96b0304f54c71fb3",
    "55ba73f88febb542", "de4186fc908d7585", "543fee2e5fd55b5c",
    "010e3c9b4ccbc68e", "e2ebe51b6d7baa4c", "3eff8c9232a1c552",
    "d1bf3c6462df5167", "b7b9a7a26b872830", "4ec8284239e9a300",
    "8aea39a08bd1fa7e", "79d7bf1b3c62e750", "f5ce489f677577e2",
    "294cfdbab6aad60c", "4f78f03561052085", "6906b5b0ce7bb834",
})

CANON_KEYS = tuple(profile.CANON_KEYS) + (
    "skill_set.observation", "skill_set.reasoning",
)
LIST_KEYS = ("trait.tags", "speech.habit_phrases")
_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+")


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _leaks(text):
    if hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] in FORBIDDEN_DIGESTS:
        return text
    for token in _TOKEN_RE.findall(text):
        if hashlib.sha256(token.encode("utf-8")).hexdigest()[:16] in FORBIDDEN_DIGESTS:
            return token
    return None


def validate_cards(doc):
    """Fail closed on leaked names, duplicate identities or weak evidence."""
    for text in _strings(doc):
        leaked = _leaks(text)
        if leaked:
            raise ValueError("ERR_SOURCE_IDENTITY_LEAK")
    cards = doc.get("cards") or []
    if len(cards) < 1:
        raise ValueError("ERR_NO_CARDS")
    names = [c.get("name") for c in cards]
    ids = [c.get("id") for c in cards]
    if len(set(names)) != len(names) or len(set(ids)) != len(ids):
        raise ValueError("ERR_DUPLICATE_IDENTITY")
    for card in cards:
        if not card.get("speech_habits"):
            raise ValueError("ERR_NO_SPEECH_HABITS")
        for key, spec in (card.get("canon") or {}).items():
            bins, quotes = spec.get("bins") or [], spec.get("quotes") or []
            if key not in CANON_KEYS or len(bins) != 2 or len(quotes) != 2:
                raise ValueError("ERR_WEAK_EVIDENCE")
            if any(float(b) not in {0.1, 0.3, 0.5, 0.7, 0.9} for b in bins):
                raise ValueError("ERR_INVALID_ANCHOR")
    return True


def _canonical_attributes(card):
    out = []
    for key, spec in (card.get("canon") or {}).items():
        domain, _ = normalize.domain_for_key(key)
        items = []
        for idx, (anchor, quote) in enumerate(zip(spec["bins"], spec["quotes"]), 1):
            items.append({
                "source_id": f"ev_{card['id']}_{key}_{idx:02d}",
                "quote": quote,
                "locator": f"web_public_profile:{card['id']}:{idx}",
                "weight": 1.0,
                "anchor_bin": float(anchor),
            })
        score, confidence = normalize.score_evidence(items)
        _, factor = normalize.domain_for_key(key)
        out.append(normalize.envelope(key, score * factor, confidence, items, domain))
    for key in CANON_KEYS:
        if key in (card.get("canon") or {}):
            continue
        domain, _ = normalize.domain_for_key(key)
        out.append(normalize.envelope(key, None, 0.0, [], domain, "missing_evidence"))
    return out


def _list_envelope(card, key, value):
    evidence = [{
        "source_id": f"ev_{card['id']}_{key}_{idx:02d}",
        "quote": item,
        "locator": f"web_public_profile:{card['id']}:habits",
        "weight": 1.0,
    } for idx, item in enumerate(value, 1)]
    return {
        "key": key,
        "value": list(value),
        "layer": "L1",
        "scope": "self",
        "domain": "list",
        "visibility": "self",
        "confidence": 0.7,
        "source": "canon_extract",
        "evidence": evidence,
        "updated_at_round": 0,
        "updated_by": "pipeline:archetypes",
        "locked": True,
        "extraction_version": 1,
    }


def _text_envelope(card, key, value):
    evidence = [{
        "source_id": f"ev_{card['id']}_{key}_01",
        "quote": value,
        "locator": f"archetype_card:{card['id']}",
        "weight": 1.0,
    }]
    return {
        "key": key,
        "value": value,
        "layer": "L1",
        "scope": "self",
        "domain": "text",
        "visibility": "self",
        "confidence": 1.0,
        "source": "canon_extract",
        "evidence": evidence,
        "updated_at_round": 0,
        "updated_by": "pipeline:archetypes",
        "locked": True,
        "extraction_version": 1,
    }


def build_persona_doc(doc):
    """Return (ontology, persona_doc) with no source identities attached."""
    validate_cards(doc)
    characters = []
    personas = []
    for card in doc["cards"]:
        canon = card.get("canon") or {}
        characters.append({
            "id": card["id"],
            "name": card["name"],
            "role": card.get("role", ""),
            "canon_evidence": [
                {"key": key, "raw_e": anchor, "quote": quote, "locator": card["id"]}
                for key, spec in canon.items()
                for anchor, quote in zip(spec["bins"], spec["quotes"])
            ],
            "traits": card.get("traits", []),
            "speech_habits": card["speech_habits"],
        })
        personas.append({
            "character_id": card["id"],
            "name": card["name"],
            "layer": "L1",
            "attributes": (
                _canonical_attributes(card)
                + [
                    _text_envelope(card, "identity.name", card["name"]),
                    _text_envelope(card, "identity.role", card.get("role", "")),
                    _list_envelope(card, "trait.tags", card.get("traits", [])),
                    _list_envelope(card, "speech.habit_phrases", card["speech_habits"]),
                ]
            ),
        })
    onto = {
        "script_id": doc["script_id"],
        "title": doc.get("title", doc["script_id"]),
        "schema_version": "0.1",
        "source_files": ["config/archetypes.json"],
        "characters": characters,
    }
    persona_doc = {
        "schema_version": "0.1",
        "script_id": doc["script_id"],
        "personas": personas,
    }
    return onto, persona_doc


def import_cards(conn, doc, source_kind="trait_cards"):
    """Persist the cards as a script plus read-only L1 personas."""
    onto, persona_doc = build_persona_doc(doc)
    db.save_script(conn, onto)
    version = db.save_persona_l1(conn, onto["script_id"], persona_doc)
    registry.register(
        conn, onto["script_id"], title=onto["title"],
        source_kind=source_kind, canonical_version=onto["schema_version"],
        notes="anonymized trait cards; names regenerated from a fixed seed",
        status="staged",
    )
    return {
        "script_id": onto["script_id"],
        "characters": len(persona_doc["personas"]),
        "extraction_version": version,
        "names": [p["name"] for p in persona_doc["personas"]],
    }
