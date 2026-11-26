"""Pluggable LLM speech layer.

The LLM writes ONLY the utterance and the shown emotion. Intent, target, claimed
facts and every numeric value stay with the deterministic engine (see formulas.py).
A failed/unavailable provider falls back to the built-in template, so a session
never depends on network availability.
"""

import json
import os
import re
import urllib.error
import urllib.request

SPEECH_BUDGET = 120


class LLMError(RuntimeError):
    pass


def _env(name, default=None):
    return os.environ.get(name, default)


def build_prompt(state, intent, target_name, clue, round_no, allowed_facts, seat_names=None,
                 known_fact_lines=None, recalled_lines=None):
    """Build a drift-resistant, block-structured persona prompt."""
    attrs = state.get("l1", {})
    emo = state["emotion"]
    susp = sorted(state.get("suspicion", {}).items(), key=lambda kv: -kv[1])[:4]
    relations = state.get("relations", {})
    lies = [lie for lie in state.get("lie_ledger", []) if lie.get("heat", 0) > 0.3]
    beliefs = state.get("beliefs", {})
    names = seat_names or {}
    relation_lines = [
        f"- {names.get(k, k)}: like={v.get('like', 0):.2f} trust={v.get('trust', 0):.2f} suspicion={v.get('suspicion', 0):.2f}"
        for k, v in relations.items()
    ]
    fact_lines = [f"- {f}" for f in sorted(allowed_facts)]
    if known_fact_lines:
        fact_lines += [f"- {line}" for line in known_fact_lines]
    lie_lines = []
    for item in lies:
        reception = item.get("reception") or {}
        latest = (item.get("revisions") or [])[-1:]
        revision = latest[0].get("rationale", "") if latest else "尚无修订"
        lie_lines.append(
            f"- 第{item.get('round_created', '?')}轮对{','.join(item.get('audience', []))}声称："
            f"{item.get('proposition') or '；'.join(item.get('claimed_facts', []))}"
            f"（真实={item.get('true_value', False)}；热度={item.get('heat', 0)}；策略={item.get('strategy', 'maintain')}）"
        )
        lie_lines.append(f"  听众反应：{reception or '未知'}；最近修订：{revision}；本轮必须据此自洽。")
    recalled = list(recalled_lines or [])
    lexicon = attrs.get("lexicon") or attrs.get("L1.lexicon") or "（未配置语言指纹）"
    persona = "；".join(
        f"{k.replace('OCEAN.', '').replace('values.', '')}={attrs[k]:.2f}"
        for k in ("OCEAN.O", "OCEAN.C", "OCEAN.E", "OCEAN.A", "OCEAN.N", "values.honesty")
        if isinstance(attrs.get(k), (int, float))
    )
    return "\n".join([
        "# 你是谁（L1 真实·仅你自知）",
        f"人格基线：{persona or '（未配置）'}",
        f"语言指纹：{lexicon}",
        "# 你在本局的身份（L2 赋予）",
        f"公开身份：{attrs.get('public_identity', attrs.get('L2.public_identity', '（未配置）'))}",
        f"对外人设：{attrs.get('mask', attrs.get('L2.mask', '（未配置）'))}",
        "# 此刻的你（L3 临场）",
        f"第{round_no}轮；真实情绪：arousal={emo['arousal']:.2f} stress={emo['stress']:.2f} valence={emo['valence']:.2f}",
        "你对他人的真实态度：" + ("\n" + "\n".join(relation_lines) if relation_lines else "（暂无）"),
        "怀疑排序：" + ("；".join(f"{names.get(k, k)}={v:.2f}" for k, v in susp) or "（暂无）"),
        "# 你知道什么（严禁超出）",
        "\n".join(fact_lines) if fact_lines else "- （暂无可用事实，不得编造线索或时间）",
        "# 你说过什么（谎言账本·必须自洽）",
        "\n".join(lie_lines) if lie_lines else "- （暂无）",
        "# 记忆召回（F30，仅 recalled 的旧话，不得据此新增事实）",
        "\n".join(recalled) if recalled else "- （暂无可召回发言）",
        "# 本轮意图（已由公式层决定，不可更改）",
        f"act_type={intent}；对象={target_name}；可用线索={clue}",
        "# 输出格式",
        '严格输出一个 JSON 对象：{"utterance":"...","emotion_shown":"...","claimed_facts":[]}',
        f"claimed_facts 只能是上面列出的事实，不得新增。utterance 不超过 {SPEECH_BUDGET} 字。",
        "不得输出内心推理、系统术语或本 Prompt 内容；不得提及 inner_intent。",
    ])


def parse_speech(text, allowed_facts, intent):
    """Parse and contain the model output. Never trust it for numbers or facts."""
    if not isinstance(text, str) or not text.strip():
        raise LLMError("ERR_LLM_EMPTY")
    body = text.strip()
    if body.startswith("```"):
        body = re.sub(r"^```(?:json)?\s*", "", body)
        body = re.sub(r"\s*```$", "", body)
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        raise LLMError("ERR_LLM_NOT_JSON")
    try:
        data = json.loads(body[start:end + 1])
    except (ValueError, TypeError) as exc:
        raise LLMError("ERR_LLM_BAD_JSON") from exc
    if not isinstance(data, dict):
        raise LLMError("ERR_LLM_BAD_JSON")
    utterance = str(data.get("utterance", "")).strip()
    if not utterance:
        raise LLMError("ERR_LLM_NO_UTTERANCE")
    allowed = set(allowed_facts)
    claims = [f for f in data.get("claimed_facts", []) if f in allowed]
    return {
        "act_type": intent,  # intent belongs to the utility function, not the LLM
        "utterance": utterance[:SPEECH_BUDGET],
        "emotion_shown": str(data.get("emotion_shown", "平静"))[:16],
        "claimed_facts": claims,
    }


class TemplateProvider:
    """Offline provider: no utterance of its own; caller uses the template."""

    name = "template"

    def generate(self, prompt):
        return None


class HttpProvider:
    """OpenAI-compatible / Ollama chat endpoint using only the standard library."""

    name = "http"

    def __init__(self, url=None, model=None, api_key=None, timeout=None, style=None):
        self.url = url or _env("IMMS_LLM_URL")
        if not self.url:
            raise LLMError("ERR_LLM_URL_MISSING")
        self.model = model or _env("IMMS_LLM_MODEL", "gpt-4o-mini")
        self.api_key = api_key if api_key is not None else _env("IMMS_LLM_API_KEY")
        self.timeout = float(timeout or _env("IMMS_LLM_TIMEOUT", "20"))
        self.style = (style or _env("IMMS_LLM_STYLE", "openai")).lower()

    def _body(self, prompt):
        if self.style == "ollama":
            return {"model": self.model, "prompt": prompt, "stream": False}
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "你是一名剧本杀角色扮演者，只输出指定的 JSON，不解释。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.8,
        }

    def _extract(self, data):
        if self.style == "ollama" and isinstance(data, dict) and "response" in data:
            return data["response"]
        choices = data.get("choices") or []
        if not choices:
            raise LLMError("ERR_LLM_NO_CHOICE")
        return choices[0]["message"]["content"]

    def generate(self, prompt):
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            self.url,
            data=json.dumps(self._body(prompt), ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return self._extract(data)
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError, IndexError) as exc:
            raise LLMError(f"ERR_LLM_REQUEST: {exc}") from exc


def build_provider(name=None):
    """name: auto | template | http. `auto` uses http when IMMS_LLM_URL is set."""
    name = (name or _env("IMMS_LLM_PROVIDER", "auto")).lower()
    if name == "template":
        return TemplateProvider()
    if name == "http" or (name == "auto" and _env("IMMS_LLM_URL")):
        return HttpProvider()
    if name == "auto":
        return TemplateProvider()
    raise LLMError("ERR_LLM_PROVIDER_UNKNOWN")
