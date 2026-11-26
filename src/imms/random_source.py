"""Deterministic random source for every stochastic choice in IMMS.

Rule: nothing draws from an unseeded global RNG.  A random value must be
reproducible from (session_id, seat_id, round, event_seq, purpose).  This module
owns seed derivation only; it never decides game outcomes and never writes to
the database.  Callers pass the seed to a local ``random.Random``.
"""

import hashlib
import random

SEED_VERSION = 1


def derive_seed(session_id, seat_id=None, round_no=None, event_seq=None, purpose=None,
                base_seed=None):
    """Return a stable non-negative int seed derived from explicit inputs.

    ``base_seed`` is the session seed supplied by the operator; when omitted the
    ids themselves are the entropy source.  Equality of every field yields the
    same seed, so replay and GM branches reproduce identical draws.
    """
    parts = [
        "imms", str(SEED_VERSION), str(base_seed if base_seed is not None else ""),
        str(session_id), str(seat_id if seat_id is not None else ""),
        str(round_no if round_no is not None else ""),
        str(event_seq if event_seq is not None else ""),
        str(purpose if purpose is not None else ""),
    ]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def rng_for(session_id, seat_id=None, round_no=None, event_seq=None, purpose=None,
            base_seed=None):
    """Build a local ``random.Random`` from a derived seed."""
    return random.Random(derive_seed(
        session_id, seat_id=seat_id, round_no=round_no, event_seq=event_seq,
        purpose=purpose, base_seed=base_seed,
    ))


def weighted_choice(rng, weights, default=None):
    """Pick one key from a {key: weight} mapping using the supplied local RNG.

    Non-positive or non-finite weights are ignored.  An empty mapping returns
    ``default`` so callers never crash on missing data.
    """
    items = [(k, float(w)) for k, w in (weights or {}).items() if float(w) > 0]
    total = sum(w for _, w in items)
    if not items or total <= 0:
        return default
    threshold = rng.random() * total
    upto = 0.0
    for key, weight in items:
        upto += weight
        if threshold <= upto:
            return key
    return items[-1][0]
