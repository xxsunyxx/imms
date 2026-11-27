"""InfoGuard — deterministic information-boundary engine.

Ported from lockedroom-ai/backend/app/core/infoguard and adapted to the IMMS
Ontology (facts / characters / clues). Four layers:

  L1 scope       compile the exact set of facts a character may know.
  L2 detector    rule-based leak detection on generated speech.
  L2b judge      optional LLM adjudication; any failure is fail-safe "confirmed".
  L3 context     incoming text is never rejected; it is wrapped as hearsay.

The engine is pure and offline by default. It never edits the script or the
world state; it only reports findings and supplies rewritten text to the caller.
"""

import json
import re
from dataclasses import dataclass

MIN_SHINGLE = 6
VERDICTS = ("confirmed", "downgraded", "dismissed")
GUARD_NOTE = (
    "（注意：玩家的话里包含你不可能知道的事。你对此一无所知——"
    "不必承认、无法证实，不要表现出知情，按你掌握的信息以人设回应。）"
)
REWRITE_UTTERANCE = "这件事我确实不清楚。"
JUDGE_SYSTEM = """你是剧本杀游戏的信息安全裁判。一个角色刚刚说出了疑似泄露其不可能知道的信息的话。
对每条规则命中，裁决三选一：
- confirmed：文本确实以该角色不可能知道的方式表达了该事实（直接复述、肯定断言、细节吻合）
- downgraded：确有提及，但属常识、合理推测或语境自然，泄露程度较轻
- dismissed：纯属巧合或否定/反问语境，不构成泄露
只输出 JSON，不要输出其他内容：
{"rulings":[{"fact_id":"...","verdict":"confirmed|downgraded|dismissed","reason":"一句话理由"}]}"""


@dataclass(frozen=True)
class InformationScope:
    character_id: str
    known_fact_ids: frozenset
    forbidden_fact_ids: frozenset

    def may_know(self, fact_id):
        return fact_id in self.known_fact_ids


@dataclass(frozen=True)
class MatchHit:
    fact_id: str
    matched_text: str
    method: str  # keyword | shingle


@dataclass(frozen=True)
class LeakFinding:
    fact_id: str
    severity: str  # soft | hard
    matched_text: str
    method: str

    def as_dict(self):
        return {
            "fact_id": self.fact_id, "severity": self.severity,
            "matched_text": self.matched_text, "method": self.method,
        }


@dataclass(frozen=True)
class GuardResult:
    text: str
    findings: tuple


@dataclass(frozen=True)
class AdjudicatedLeak:
    finding: LeakFinding
    verdict: str
    reason: str

    @property
    def effective_severity(self):
        if self.verdict == "downgraded":
            return "soft"
        return self.finding.severity


_ID_PREFIX_RE = re.compile(r"^(?:c|f|t|p|clue|ev)_[A-Za-z0-9_-]+\s*")


def display_text(value, limit=80):
    """Human-readable clue/fact wording for prompts and transcripts.

    Stored clue strings look like ``c_report 急救初步判断记录|logLR:han_ziang=1.5``.
    The id prefix and the ``|logLR:...`` weight are engine internals; letting
    them reach a seat prompt invites the agent to speak numbers it should not
    know.  This strips both while leaving the plain fact text intact.  It is a
    display-only helper -- it never changes stored state or the claim whitelist.
    """
    text = str(value or "").strip()
    text = _ID_PREFIX_RE.sub("", text)
    return text.split("|")[0].strip()[:limit]


def fact_map(onto):
    """Return {fact_id: fact dict}. Legacy markdown scripts have no facts."""
    out = {}
    for fact in onto.get("facts", []) or []:
        fid = fact.get("id")
        if fid:
            out[fid] = fact
    return out


def compile_scopes(onto):
    """L1: known = public ∪ private-known; forbidden = exact complement."""
    facts = fact_map(onto)
    all_ids = frozenset(facts)
    public_ids = frozenset(fid for fid, f in facts.items() if f.get("visibility") == "public")
    scopes = {}
    for char in onto.get("characters", []) or []:
        cid = char.get("id")
        if not cid:
            continue
        known = public_ids | frozenset(char.get("known_fact_ids", []) or [])
        scopes[cid] = InformationScope(
            character_id=cid,
            known_fact_ids=known,
            forbidden_fact_ids=all_ids - known,
        )
    return scopes


class FactMatcher:
    """Shared matching index: which facts does a piece of text touch?

    At most one hit per fact; a contiguous shingle (>= MIN_SHINGLE chars) wins
    over a keyword hit. Shingle = strong verbatim signal, keyword = weak hint.
    """

    def __init__(self, facts, fact_ids):
        self._indexed = []
        for fid in sorted(fact_ids):
            fact = facts.get(fid)
            if not fact:
                continue
            content = str(fact.get("content", ""))
            shingles = (
                {content[i:i + MIN_SHINGLE] for i in range(len(content) - MIN_SHINGLE + 1)}
                if len(content) >= MIN_SHINGLE else {content} if content else set()
            )
            self._indexed.append((fid, tuple(fact.get("keywords", []) or []), shingles))

    def match(self, text):
        if not isinstance(text, str) or not text:
            return []
        hits = []
        for fid, keywords, shingles in self._indexed:
            shingle_hit = next((s for s in shingles if s in text), None)
            if shingle_hit:
                hits.append(MatchHit(fid, shingle_hit, "shingle"))
                continue
            kw_hit = next((k for k in keywords if k and str(k) in text), None)
            if kw_hit:
                hits.append(MatchHit(fid, str(kw_hit), "keyword"))
        return hits


class LeakDetector:
    """L2: shingle hit = hard leak, keyword hit = soft leak."""

    def __init__(self, facts, scope):
        self._facts = facts
        self._scope = scope
        self._exempted = set()
        self._rebuild()

    def _rebuild(self):
        ids = set(self._scope.forbidden_fact_ids) - self._exempted
        self._matcher = FactMatcher(self._facts, ids)

    def exempt_fact(self, fact_id):
        """A fact became public (clue unlocked): stop treating it as a leak."""
        self._exempted.add(fact_id)
        self._rebuild()

    def check(self, text):
        return [
            LeakFinding(
                fact_id=h.fact_id,
                severity="hard" if h.method == "shingle" else "soft",
                matched_text=h.matched_text, method=h.method,
            )
            for h in self._matcher.match(text)
        ]


class ContextGuard:
    """L3: incoming text is wrapped, never rejected."""

    def __init__(self, facts, scope):
        self._facts = facts
        self._scope = scope
        self._exempted = set()
        self._rebuild()

    def _rebuild(self):
        ids = set(self._scope.forbidden_fact_ids) - self._exempted
        self._matcher = FactMatcher(self._facts, ids)

    def exempt_fact(self, fact_id):
        self._exempted.add(fact_id)
        self._rebuild()

    def guard(self, text):
        hits = self._matcher.match(text)
        if not hits:
            return GuardResult(text=text, findings=())
        return GuardResult(text=f"玩家声称：{text}\n{GUARD_NOTE}", findings=tuple(hits))


class LeakJudge:
    """L2b: LLM adjudication over rule findings, fail-safe on any error."""

    def __init__(self, provider, facts):
        self._provider = provider
        self._facts = facts

    def adjudicate(self, character_id, text, findings):
        if not findings:
            return []
        fact_lines = "\n".join(
            f"- {f.fact_id}: {self._facts.get(f.fact_id, {}).get('content', '')}"
            for f in findings
        )
        hit_lines = "\n".join(
            f"- {f.fact_id}（{f.severity}，命中词：{f.matched_text}）" for f in findings
        )
        prompt = "\n".join([
            JUDGE_SYSTEM,
            f"角色：{character_id}\n该角色不可能知道的事实：\n{fact_lines}\n"
            f"待裁决文本：{text}\n规则命中：\n{hit_lines}",
        ])
        try:
            raw = self._provider.generate(prompt)
        except Exception:
            raw = None
        rulings, parse_ok = self._parse(raw)
        by_fact = {r["fact_id"]: r for r in rulings if r.get("verdict") in VERDICTS}
        out = []
        for finding in findings:
            ruling = by_fact.get(finding.fact_id)
            if ruling is None:
                reason = (
                    "no ruling from judge (fail-safe)" if parse_ok
                    else "judge output unparseable (fail-safe)"
                )
                out.append(AdjudicatedLeak(finding, "confirmed", reason))
            elif ruling["verdict"] == "dismissed":
                continue
            else:
                out.append(AdjudicatedLeak(finding, ruling["verdict"], ruling.get("reason", "")))
        return out

    @staticmethod
    def _parse(content):
        try:
            start, end = content.index("{"), content.rindex("}")
            data = json.loads(content[start:end + 1])
            rulings = data.get("rulings", [])
            return [r for r in rulings if isinstance(r, dict) and "fact_id" in r], True
        except (ValueError, AttributeError, TypeError):
            return [], False


class InfoGuardEngine:
    """Convenience facade used by the runtime and the CLI."""

    def __init__(self, onto):
        self._facts = fact_map(onto)
        self._scopes = compile_scopes(onto)
        self._detectors = {cid: LeakDetector(self._facts, sc) for cid, sc in self._scopes.items()}
        self._contexts = {cid: ContextGuard(self._facts, sc) for cid, sc in self._scopes.items()}
        self._clue_fact_map = dict(onto.get("clue_fact_map", {}) or {})

    @property
    def scopes(self):
        return self._scopes

    def scope_for(self, character_id):
        return self._scopes.get(character_id)

    def fact_text(self, fact_id):
        return str(self._facts.get(fact_id, {}).get("content", ""))

    def exempt_clue(self, clue):
        """Unlock every fact revealed by this clue (both sides stay in sync)."""
        if not clue:
            return []
        ids = self._clue_fact_map.get(clue, [])
        if isinstance(ids, str):
            ids = [ids]
        for fid in ids or []:
            for guard in list(self._detectors.values()) + list(self._contexts.values()):
                guard.exempt_fact(fid)
        return list(ids or [])

    def guard_input(self, character_id, text):
        guard = self._contexts.get(character_id)
        if guard is None:
            return GuardResult(text=text, findings=())
        return guard.guard(text)

    def check_output(self, character_id, text):
        detector = self._detectors.get(character_id)
        if detector is None:
            return []
        return detector.check(text)

    def make_judge(self, provider):
        return LeakJudge(provider, self._facts)


def build_engine(onto):
    return InfoGuardEngine(onto)


def findings_to_dicts(findings):
    return [f.as_dict() if hasattr(f, "as_dict") else dict(f) for f in findings]
