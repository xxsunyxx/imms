"""L4 performed-persona projection and the R2 persona-consistency guard.

Original spec §3.4: other seats only ever read L4, never L1/L3 truth.  This
module turns an observed seat's canon (L1), assigned mask (L2) and live state
(L3) into a perceiver-specific *表演投影*.  The projection is deterministic:
the leak noise is drawn from ``random_source`` keyed by session/seat/round and
never from a global RNG.

No embedding service exists in this project (C1 freeze).  The R2 cosine check
therefore uses a rule proxy: OCEAN/values centered at the neutral 0.5 baseline.
It is an auditable consistency check, not a claim about semantic embeddings.
"""

from . import formulas, random_source

PERSONA_KEYS = (
    "OCEAN.O", "OCEAN.C", "OCEAN.E", "OCEAN.A", "OCEAN.N",
    "values.honesty", "values.loyalty", "values.ambition", "values.justice",
)
SIGNED_RELATIONS = ("like", "trust", "dominance", "commitment", "debt")
UNIT_RELATIONS = ("respect", "intimacy", "suspicion", "fear", "envy", "grudge")
COSINE_MIN = 0.72
MAX_RETRIES = 3


def _num(value, default=0.5):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if -1e9 < v < 1e9 else default


def _mask_source(state):
    l1 = state.get("l1") or {}
    for key in ("mask", "L2.mask"):
        if isinstance(l1.get(key), dict):
            return l1[key], f"l1.{key}"
    l0 = state.get("l0") or {}
    if isinstance(l0.get("mask"), dict):
        return l0["mask"], "l0.mask"
    return None, None


def _mask_value(mask, key):
    if not isinstance(mask, dict):
        return None
    if key in mask:
        return mask[key]
    section, _, field = key.partition(".")
    nested = mask.get(section)
    if isinstance(nested, dict) and field in nested:
        return nested[field]
    return None


def canon_vector(state, keys=PERSONA_KEYS):
    """Centered L1 anchor: None components stay neutral and are reported."""
    l1 = state.get("l1") or {}
    return [formulas.clip(_num(l1.get(k), 0.5), 0, 1) - 0.5 for k in keys]


def cosine(a, b):
    """Cosine similarity; returns 0.0 for a zero-norm vector."""
    pairs = [(x, y) for x, y in zip(a or [], b or [])]
    dot = sum(x * y for x, y in pairs)
    na = sum(x * x for x, _ in pairs) ** 0.5
    nb = sum(y * y for _, y in pairs) ** 0.5
    return round(dot / (na * nb), 6) if na and nb else 0.0


def persona_guard(candidate, anchor, threshold=COSINE_MIN):
    """R2: flag a candidate that drifts too far from the L1 anchor."""
    score = round(cosine(candidate, anchor), 4)
    flat = sum(x * x for x in anchor) < 1e-9
    return {
        "cosine": score,
        "threshold": threshold,
        "passed": True if flat else score >= threshold,
        "anchor_flat": flat,
    }


def leak_sigma(state):
    """F18 leak sigma for the observed seat (higher arousal/stress = leakier).

    F01 adds canon-vs-mask tension to the base sigma: the harder a seat has to
    hold its mask, the more it leaks.  A missing tension stays neutral (0.0).
    """
    emo = state.get("emotion") or {}
    acting = _num((state.get("l0") or {}).get("acting_skill"), 0.5)
    tension = max(0.0, _num(state.get("tension"), 0.0))
    return round(formulas.leak_sigma(
        0.05 + 0.4 * tension, _num(emo.get("arousal"), 0.0),
        _num(emo.get("stress"), 0.0), acting
    ), 6)


def mask_delta(state):
    """Mask-minus-canon per persona key; missing mask becomes zero, never a guess."""
    mask, source = _mask_source(state)
    canon = state.get("l1") or {}
    gaps = [] if source else ["mask_not_configured"]
    delta = {}
    for key in PERSONA_KEYS:
        base = _num(canon.get(key), 0.5)
        raw = _mask_value(mask, key)
        delta[key] = 0.0 if raw is None else round(
            formulas.clip(_num(raw, base), 0, 1) - base, 6
        )
        if raw is None and source:
            gaps.append(f"{key}_mask_missing")
    return delta, source, gaps


def _draw(observer_seat, observed_seat, session_id, round_no, seed, salt):
    return random_source.rng_for(
        session_id, seat_id=f"{observer_seat}>{observed_seat}", round_no=round_no,
        purpose=f"l4.project:{salt}", base_seed=seed,
    )


def _apply_retry(candidate, anchor, gamma):
    """Pull a rejected candidate back toward the L1 anchor by ``gamma``."""
    return [
        anchor[i] + gamma * (candidate[i] - anchor[i])
        for i in range(min(len(candidate), len(anchor)))
    ]


def project(observed, observer, session_id="", round_no=0, seed=None):
    """Project one observed seat into one observer's L4 view.

    Mutation-free.  Returns shown persona/attitude plus a guard audit; the
    observer's own L1/L3 values are never copied into the result.
    """
    observer_seat = observer.get("seat_id") or "?"
    observed_seat = observed.get("seat_id") or "?"
    canon = canon_vector(observed)
    delta, mask_source, gaps = mask_delta(observed)
    # Human speech is what it is; it does not get a simulated leak draw.
    sigma = 0.0 if observed.get("is_human") else leak_sigma(observed)

    retries = 0
    clamped = False
    noise = {}
    for salt in range(MAX_RETRIES + 1):
        rng = _draw(observer_seat, observed_seat, session_id, round_no, seed, salt)
        noise = {k: rng.gauss(0.0, sigma) for k in PERSONA_KEYS}
        shown = [
            formulas.clip(canon[i] + delta[PERSONA_KEYS[i]] + noise[PERSONA_KEYS[i]], -0.5, 0.5)
            for i in range(len(PERSONA_KEYS))
        ]
        verdict = persona_guard(shown, canon)
        if verdict["passed"]:
            break
        retries = salt + 1
        clamp_gamma = 0.5 ** retries
        shown = _apply_retry(shown, canon, clamp_gamma)
        clamped = True
        verdict = persona_guard(shown, canon)
        if verdict["passed"]:
            break
    else:
        shown = list(canon)
        clamped = True
        verdict = persona_guard(shown, canon)

    true_rel = (observed.get("relations") or {}).get(observer_seat)
    shown_relation = None
    if isinstance(true_rel, dict):
        rel_mask = _mask_value(_mask_source(observed)[0], "relations") or {}
        rel_mask = rel_mask.get(observer_seat, {}) if isinstance(rel_mask, dict) else {}
        epsilon = _draw(observer_seat, observed_seat, session_id, round_no, seed, "rel").gauss(0.0, sigma)
        shown_relation = {}
        for key in SIGNED_RELATIONS + UNIT_RELATIONS:
            lo, hi = (-1.0, 1.0) if key in SIGNED_RELATIONS else (0.0, 1.0)
            base = _num(true_rel.get(key), 0.5 if lo == 0 else 0.0)
            mask_r = _num(rel_mask.get(key), 0.0) if isinstance(rel_mask, dict) else 0.0
            shown_relation[key] = round(formulas.shown_attitude(base, mask_r, epsilon, lo, hi), 4)
    else:
        gaps.append("relation_not_configured")

    return {
        "observer": observer_seat,
        "observed": observed_seat,
        "character_id": observed.get("character_id"),
        "round": int(round_no),
        "mask_source": mask_source,
        "shown_persona": {
            k: round(v + 0.5, 4) for k, v in zip(PERSONA_KEYS, shown)
        },
        "shown_attitude": shown_relation,
        "leak_sigma": sigma,
        "guard": {
            **verdict,
            "retries": retries,
            "clamped": clamped,
            "action": "clamped" if clamped else ("regenerated" if retries else "pass"),
        },
        "gaps": gaps,
    }


def project_table(states, session_id="", round_no=0, seed=None):
    """Perceiver-indexed projection table: ``{observer: {observed: L4 view}}``."""
    table = {}
    for observer_seat, observer in states.items():
        view = {}
        for observed_seat, observed in states.items():
            if observed_seat == observer_seat:
                continue  # self-knowledge stays L1/L3; no self-projection
            view[observed_seat] = project(
                {**observed, "seat_id": observed_seat},
                {**observer, "seat_id": observer_seat},
                session_id, round_no, seed,
            )
        table[observer_seat] = view
    return table


def guard_audits(table):
    """Flatten only non-passing guard decisions for event logging."""
    audits = []
    for observer, view in sorted(table.items()):
        for observed, item in sorted(view.items()):
            guard = item.get("guard") or {}
            if guard.get("action") == "pass":
                continue
            audits.append({
                "observer": observer, "observed": observed,
                "round": item.get("round"), "guard": guard,
                "mask_source": item.get("mask_source"),
            })
    return audits
