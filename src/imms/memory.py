"""Belief graph and listener-aware memory ledger for the runtime.

The runtime previously stored ``memory`` as ``{round, act, importance}`` and
never consumed F26 (``formulas.memory_score``).  This module turns memory into
an auditable ledger of *who said what to whom, when*, adds a small commitment
state machine (stated -> revised -> retracted), and exposes deterministic
recall that can be injected into prompts and compared across GM branches.

Everything here is a pure function of runtime state: no network, no LLM, no
new numeric facts.  Statements are only what was already written to the event
log; recall never invents facts.
"""

import re

from . import formulas, infoguard

MAX_MEMORY = 200
WORD_RE = re.compile(r"[A-Za-z0-9_]+")
CJK_RE = re.compile(r"[\u4e00-\u9fff]+")

RETRACT_MARKERS = ("我撤回", "收回刚才", "刚才那句作废", "不算我说的", "我说错了", "我改口")
REVISE_MARKERS = ("其实", "更正", "不是这样", "我刚才说错", "准确地说", "换句话说")
CONFESS_MARKERS = ("我承认", "是我做的", "我交代", "我坦白", "坦白说")
WEAKEN_MARKERS = ("记不清", "不确定", "大概是")
HIGH_IMPACT_ACTS = {"accuse", "confess", "contradiction", "lie_exposed", "caught_lying"}
NEGATION_MARKERS = ("不是", "没有", "并未", "没在", "不在", "我没", "我否认")

TRANSITIONS = {
    "active": {"revised", "retracted", "weakened", "confessed"},
    "revised": {"retracted", "weakened", "confessed"},
    "weakened": {"revised", "retracted", "confessed"},
    "confessed": {"retracted"},
    "retracted": set(),
}


def _tokens(text):
    out = set()
    for word in WORD_RE.findall(str(text or "")):
        out.add(word.lower())
    for run in CJK_RE.findall(str(text or "")):
        if len(run) <= 2:
            out.add(run)
        else:
            out.update(run[i:i + 2] for i in range(len(run) - 1))
    return out


def similarity(cue, text):
    """Bounded, deterministic token-overlap score in [0, 1]."""
    a, b = _tokens(cue), _tokens(text)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def emotion_congruence(current, remembered):
    """How close the remembered emotional state is to the current one."""
    cur, mem = current or {}, remembered or {}
    keys = [k for k in formulas.PAD_DOMAIN if k in cur and k in mem]
    if not keys:
        return 0.5
    diffs = [abs(float(cur[k]) - float(mem[k])) / 2.0 for k in keys]
    return round(max(0.0, min(1.0, 1.0 - sum(diffs) / len(diffs))), 4)


def classify_statement(utterance, act_type=""):
    """Classify a (re)statement for the commitment state machine."""
    text = str(utterance or "")
    if any(m in text for m in RETRACT_MARKERS) or act_type == "retract":
        return "retracted"
    if any(m in text for m in CONFESS_MARKERS) or act_type == "confess":
        return "confessed"
    if any(m in text for m in REVISE_MARKERS) or act_type == "revise":
        return "revised"
    if any(m in text for m in WEAKEN_MARKERS) or act_type == "deflect":
        return "weakened"
    return "stated"


def detect_conflict(prior, record):
    """Low-false-positive new-vs-old statement conflict.

    A conflict is reported only when both statements are from the same
    speaker, name the same fact, and the new one contains an explicit
    negation or a status transition.  Mere topical overlap is never enough.
    The numeric strength uses F17 with the two statements' beliefs.
    """
    prior = [p for p in (prior or []) if isinstance(p, dict)]
    current = record or {}
    facts = {str(f) for f in (current.get("claimed_facts") or [])}
    if not facts or current.get("speaker") is None:
        return None
    text = str(current.get("utterance") or "")
    status = current.get("status", "stated")
    negated = any(marker in text for marker in NEGATION_MARKERS)
    if not negated and status not in {"revised", "retracted", "confessed", "weakened"}:
        return None
    for old in reversed(prior):
        if old.get("speaker") != current.get("speaker") or old.get("seq") == current.get("seq"):
            continue
        shared = facts & {str(f) for f in (old.get("claimed_facts") or [])}
        if not shared:
            continue
        if old.get("status") == "retracted" and status == "retracted":
            continue
        belief_a = float(old.get("importance", 0.5))
        belief_b = float(current.get("importance", 0.5))
        # importance is bounded in [0.3, 1]; the formula needs >0.5 beliefs,
        # so use a deterministic floor rather than inventing new evidence.
        strength = formulas.contradiction_strength(
            max(belief_a, 0.6), max(belief_b, 0.6), 0.6,
        )
        return {
            "kind": "statement_conflict",
            "detail": f"同一供述发生变化：{sorted(shared)[0]}",
            "prior_seq": old.get("seq"), "seq": current.get("seq"),
            "speaker": current.get("speaker"), "fact_ids": sorted(shared),
            "status": status, "strength": round(strength, 4),
        }
    return None


def importance_of(act_type, claimed_facts=None, target=None, is_self=False):
    value = 0.30
    if claimed_facts:
        value += min(0.30, 0.12 * len(claimed_facts))
    if act_type in HIGH_IMPACT_ACTS:
        value += 0.25
    if target:
        value += 0.10
    if is_self:
        value += 0.10
    return round(max(0.0, min(1.0, value)), 4)


def _seq_of(event, round_no):
    seq = event.get("seq")
    if seq is not None:
        return str(seq)
    parts = (event.get("actor", "?"), event.get("act_type", "?"),
             str(event.get("payload", {}).get("utterance", ""))[:24])
    return f"r{round_no}:" + ":".join(parts)


def observe(state, event, round_no, audience=None):
    """Record one visible statement into a seat's ledger.

    Returns ``None`` when the event was already recorded (the runtime re-reads
    earlier events each round, so de-duplication by event sequence is required).
    """
    memory = state.setdefault("memory", [])
    prior = [item for item in memory if isinstance(item, dict)]
    seq = _seq_of(event, round_no)
    if any(item.get("seq") == seq for item in memory if isinstance(item, dict)):
        return None
    payload = event.get("payload") or {}
    actor = event.get("actor") or payload.get("speaker")
    is_self = bool(event.get("is_self"))
    record = {
        "seq": seq,
        "round": int(round_no),
        "speaker": actor,
        "audience": sorted(x for x in (audience or []) if x and x != actor),
        "act_type": event.get("act_type", "assert"),
        "utterance": str(payload.get("utterance", ""))[:500],
        "claimed_facts": list(payload.get("claimed_facts") or []),
        "target": payload.get("target"),
        "status": classify_statement(payload.get("utterance"), event.get("act_type", "")),
        "importance": importance_of(
            event.get("act_type", ""), payload.get("claimed_facts"),
            payload.get("target"), is_self,
        ),
        "emotion_snapshot": dict(state.get("emotion") or {}),
        "self": is_self,
    }
    memory.append(record)
    if len(memory) > MAX_MEMORY:
        del memory[:len(memory) - MAX_MEMORY]
    changes = update_commitments(state, record)
    return {
        "record": record,
        "commitment_changes": changes,
        "conflict": detect_conflict(prior, record),
    }


def update_commitments(state, record):
    """Apply one statement to the commitment ledger; return status changes."""
    commitments = state.setdefault("commitments", {})
    status = record.get("status", "stated")
    facts = list(record.get("claimed_facts") or [])
    if status == "retracted" and not facts:
        candidates = [
            fid for fid, item in commitments.items()
            if item.get("speaker") == record.get("speaker")
            and item.get("status") in {"active", "revised", "weakened"}
        ]
        facts = candidates[-1:]  # most recently created claim
    changes = []
    for fid in facts:
        item = commitments.get(fid)
        if item is None:
            if status == "retracted":
                continue
            commitments[fid] = {
                "fact": fid,
                "speaker": record.get("speaker"),
                "round_created": record.get("round"),
                "audience": list(record.get("audience") or []),
                "status": "active" if status == "stated" else status,
                "history": [{"round": record.get("round"), "status": status, "seq": record.get("seq")}],
            }
            changes.append({"fact": fid, "from": None, "to": commitments[fid]["status"]})
            continue
        old = item.get("status", "active")
        target = status if status != "stated" else None
        if target is None or target == old:
            continue
        if target not in TRANSITIONS.get(old, set()):
            continue
        item["status"] = target
        item.setdefault("history", []).append(
            {"round": record.get("round"), "status": target, "seq": record.get("seq")}
        )
        changes.append({"fact": fid, "from": old, "to": target})
    return changes


def seed_semantic(state, onto, char=None):
    """Seed permanent semantic memory from explicit script knowledge only.

    Public facts, authored rules and the character's own goals are the only
    accepted sources.  Private truth facts are never copied into a seat.
    """
    entries, seen = [], set()
    candidates = []
    for fact in (onto or {}).get("facts") or []:
        if fact.get("visibility") == "public":
            candidates.append(("fact", fact.get("content")))
    for rule in ((onto or {}).get("world") or {}).get("rules") or []:
        candidates.append(("rule", rule if isinstance(rule, str) else rule.get("text")))
    for goal in (char or {}).get("goals") or []:
        candidates.append(("goal", goal if isinstance(goal, str) else goal.get("text")))
    for kind, raw in candidates:
        text = infoguard.display_text(str(raw or ""))[:300]
        if not text or text in seen:
            continue
        seen.add(text)
        entries.append({
            "id": f"sem:{kind}:{len(entries)}", "source": "semantic",
            "kind": kind, "text": text, "utterance": text,
            "round": 0, "importance": 0.35, "claimed_facts": [],
        })
    state["semantic_memory"] = entries
    return entries


def reflect(state, round_no, act_label="本幕", seat_names=None):
    """Generate up to three sourced insights at an act boundary (§9.3).

    Insights are deterministic summaries of already-observed material; they
    never introduce a fact.  Weight is doubled relative to an episodic record
    (base 0.5 -> 1.0) and source event seqs are retained.
    """
    since = int(state.get("last_reflected_round") or 0)
    records = [
        m for m in (state.get("memory") or [])
        if isinstance(m, dict) and int(m.get("round") or 0) > since
    ]
    if not records:
        return []
    names = seat_names or {}
    state["last_reflected_round"] = int(round_no)
    top = max(records, key=lambda m: float(m.get("importance") or 0.0))
    speaker = names.get(top.get("speaker"), top.get("speaker"))
    changed = [m for m in records if m.get("status") != "stated"]
    susp = sorted(
        (state.get("suspicion") or {}).items(), key=lambda kv: (-kv[1], kv[0])
    )
    focus = names.get(susp[0][0], susp[0][0]) if susp else "在座众人"
    texts = [
        f"{act_label}最值得保留的发言来自{speaker}：{str(top.get('utterance') or '')[:60]}",
        f"{act_label}共记录 {len(records)} 条发言、{len(changed)} 次供述状态变化。",
        f"{act_label}对{focus}的怀疑最高；后续追问应回到上述已记录片段。",
    ]
    source_seq = [str(top.get("seq"))] + [str(m.get("seq")) for m in changed[:2]]
    out = []
    for index, text in enumerate(texts[:3]):
        out.append({
            "id": f"reflection:{round_no}:{index}",
            "source": "reflection", "text": text, "utterance": text,
            "round": int(round_no), "importance": 1.0,
            "weight_factor": 2.0, "source_seq": sorted(set(source_seq)),
            "claimed_facts": [],
        })
    state.setdefault("reflections", []).extend(out)
    if len(state["reflections"]) > 60:
        del state["reflections"][:len(state["reflections"]) - 60]
    return out


def _recall_items(state):
    items = [dict(m) for m in (state.get("memory") or []) if isinstance(m, dict)]
    for item in state.get("reflections") or []:
        if isinstance(item, dict):
            items.append({**item, "age_override": 0})
    for item in state.get("semantic_memory") or []:
        if isinstance(item, dict):
            items.append({**item, "age_override": 0})
    return items


def recall(state, current_round, cue=None, limit=3, tau_m=3.0):
    """Consume F26: rank episodic, reflective and semantic memory together."""
    tau_m = max(0.1, float(tau_m))
    emo = state.get("emotion") or {}
    cue = cue or ""
    scored = []
    for item in _recall_items(state):
        age = int(item.get("age_override", max(
            0, int(current_round) - int(item.get("round") or 0)
        )))
        text = str(item.get("utterance") or item.get("text") or "")
        cosine = similarity(cue, text + " " + " ".join(item.get("claimed_facts") or []))
        congruence = emotion_congruence(emo, item.get("emotion_snapshot"))
        # G20 memory imprint: an improvised act decays more slowly (tau x3)
        # and carries an emotion tag for trauma-consistent retrieval.
        item_tau = tau_m * max(1.0, float(item.get("tau_m_factor") or 1.0))
        score = formulas.memory_score(age, item_tau, float(item.get("importance") or 0.0), cosine, congruence)
        scored.append({**item, "recall_score": round(score, 4)})
    scored.sort(key=lambda x: (-x["recall_score"], str(x.get("seq") or x.get("id"))))
    return scored[:max(0, int(limit))]


def recall_lines(items, seat_names=None):
    names = seat_names or {}
    lines = []
    for item in items or []:
        source = item.get("source")
        if source == "reflection":
            lines.append(
                f"- 第{item.get('round')}轮 反思（来源事件={item.get('source_seq')}）："
                f"{item.get('text')}（召回分={item.get('recall_score')}）"
            )
            continue
        if source == "semantic":
            lines.append(
                f"- 剧本知识：{item.get('text')}（召回分={item.get('recall_score')}）"
            )
            continue
        speaker = names.get(item.get("speaker"), item.get("speaker"))
        audience = ",".join(names.get(s, s) for s in item.get("audience") or []) or "在座各位"
        status = item.get("status") or "stated"
        lines.append(
            f"- 第{item.get('round')}轮 {speaker} 对 {audience} 说：「{item.get('utterance')}」"
            f"（状态={status}，召回分={item.get('recall_score')}）"
        )
    return lines


def summarize(state):
    memory = [m for m in (state.get("memory") or []) if isinstance(m, dict)]
    return {
        "statements": len(memory),
        "public_statements": sum(1 for m in memory if not m.get("audience")),
        "private_statements": sum(1 for m in memory if m.get("audience")),
        "commitments": len(state.get("commitments") or {}),
        "retracted": sum(
            1 for c in (state.get("commitments") or {}).values() if c.get("status") == "retracted"
        ),
        "semantic": len(state.get("semantic_memory") or []),
        "reflections": len(state.get("reflections") or []),
    }


def diff(base_state, fork_state, current_round=None):
    """Memory-level delta between two snapshots of the same seat."""
    def index(state):
        out = {}
        for item in state.get("memory") or []:
            if isinstance(item, dict):
                out[str(item.get("seq"))] = item
        return out

    base_mem, fork_mem = index(base_state or {}), index(fork_state or {})
    changes = []
    for seq in sorted(set(base_mem) | set(fork_mem)):
        a, b = base_mem.get(seq), fork_mem.get(seq)
        if a is None:
            changes.append({"kind": "added", "seq": seq, "utterance": (b or {}).get("utterance", "")})
        elif b is None:
            changes.append({"kind": "removed", "seq": seq, "utterance": a.get("utterance", "")})
        elif a.get("utterance") != b.get("utterance") or a.get("status") != b.get("status"):
            changes.append({
                "kind": "changed", "seq": seq,
                "base": {"utterance": a.get("utterance"), "status": a.get("status")},
                "fork": {"utterance": b.get("utterance"), "status": b.get("status")},
            })
    base_c = (base_state or {}).get("commitments") or {}
    fork_c = (fork_state or {}).get("commitments") or {}
    for fid in sorted(set(base_c) | set(fork_c)):
        before = (base_c.get(fid) or {}).get("status")
        after = (fork_c.get(fid) or {}).get("status")
        if before != after:
            changes.append({"kind": "commitment", "fact": fid, "base": before, "fork": after})
    if current_round is None:
        rounds = [int(m.get("round") or 0) for m in list(base_mem.values()) + list(fork_mem.values())]
        current_round = max(rounds) if rounds else 0
    base_top = [r.get("seq") for r in recall(base_state or {}, current_round, limit=3)]
    fork_top = [r.get("seq") for r in recall(fork_state or {}, current_round, limit=3)]
    if base_top != fork_top:
        changes.append({"kind": "recall_top", "base": base_top, "fork": fork_top})
    return changes
