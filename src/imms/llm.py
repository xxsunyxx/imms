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
                 known_fact_lines=None, recalled_lines=None, guides=None, perceived=None,
                 style_parts=None):
    """Build a drift-resistant, block-structured persona prompt.

    Compatibility wrapper: rendering now lives in prompt_engine (data blocks
    plus optional guide blocks).  Guides only affect wording, never facts or
    numbers; see prompt_engine for the contract.
    """
    from . import prompt_engine

    return prompt_engine.build_from_state(
        state, intent, target_name, clue, round_no, allowed_facts,
        seat_names=seat_names, known_fact_lines=known_fact_lines,
        recalled_lines=recalled_lines, guides=guides, perceived=perceived,
        style_parts=style_parts,
    )


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
