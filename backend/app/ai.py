"""AI gateway and the mark-suggestion task.

Every external call goes through `call_chat`. Callers must pass text that has
already been de-identified (see deid.py); the API layer enforces this by only
sending payloads that were built, stored and previewed through `build_*`.
Both Together AI and a local Ollama server speak the OpenAI chat API.
"""
from __future__ import annotations

import json
import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

import httpx

from .rubric import Assignment
from .settings import settings

POSTER_TEXT_LIMIT = 14000
TIMEOUT = httpx.Timeout(180.0, connect=15.0)


class AIError(Exception):
    pass


def provider_config(provider: str, kind: str = "text") -> tuple[str, str, str]:
    """(base_url, api_key, model) for a provider."""
    if provider == "together":
        model = settings.together_model_vision if kind == "vision" else settings.together_model_text
        if not settings.together_configured():
            raise AIError("Together API key is not set in .env")
        if not model or model.startswith("__REPLACE"):
            raise AIError(f"Together {kind} model is not set in .env")
        return settings.together_base_url, settings.together_api_key, model
    if provider == "local":
        model = settings.local_llm_model_vision if kind == "vision" else settings.local_llm_model
        if not model or model.startswith("__REPLACE"):
            raise AIError(f"local {kind} model is not set in .env")
        return settings.local_llm_base_url, "ollama", model
    raise AIError(f"AI provider '{provider}' is switched off")


def call_chat(provider: str, model: str, messages: list[dict], schema: Optional[dict] = None,
              max_tokens: int = 2500) -> dict:
    """POST /chat/completions. Returns {"content": str, "usage": dict}."""
    base, key, _ = provider_config(provider)
    body: dict[str, Any] = {"model": model, "messages": messages, "temperature": 0.2,
                            "max_tokens": max_tokens}
    if schema:
        body["response_format"] = {"type": "json_schema",
                                   "json_schema": {"name": "result", "schema": schema}}
    with httpx.Client(timeout=TIMEOUT) as client:
        r = client.post(f"{base.rstrip('/')}/chat/completions", json=body,
                        headers={"Authorization": f"Bearer {key}"})
        if r.status_code == 400 and schema and "response_format" in r.text:
            body.pop("response_format")  # model without structured outputs: rely on the prompt
            r = client.post(f"{base.rstrip('/')}/chat/completions", json=body,
                            headers={"Authorization": f"Bearer {key}"})
    if r.status_code != 200:
        raise AIError(f"{provider} returned {r.status_code}: {r.text[:300]}")
    data = r.json()
    choice = data["choices"][0]
    if choice.get("finish_reason") == "length":
        raise AIError("the model ran out of tokens before finishing; try again")
    return {"content": choice["message"].get("content") or "", "usage": data.get("usage", {})}


def parse_json(content: str) -> dict:
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        raise AIError("the model did not return JSON")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise AIError(f"the model returned malformed JSON: {e}")


# ------------------------------------------------------------- mark suggestion

SUGGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "dimensions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "marks": {"type": ["number", "null"]},
                    "rationale": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "marks", "rationale", "evidence"],
            },
        },
    },
    "required": ["dimensions"],
}

SYSTEM_SUGGEST = """You assist a university lecturer in teacher education who is grading \
academic posters and short in-class poster presentations. You suggest marks; the lecturer \
decides. Be fair, specific and conservative.

Rules:
- Use ONLY the evidence provided. Each dimension lists which sources you may use.
- POSTER TEXT is text extracted from the PDF: layout, images and visual design are NOT visible to you.
- Names have been replaced by placeholders such as [S07]; never guess identities.
- Give marks on the given scale, in multiples of the step, between 0 and the maximum.
  Use the rubric band descriptors: the band's letter range tells you roughly which part of the
  scale to use (A+ to A- = top 15%, B+ to B- = 70-84%, C+ to C = 60-69%, D+ to D = 50-59%, F < 50%).
- If the allowed sources do not give enough evidence for a dimension (for example poster design
  with no instructor comment about design), return marks = null and say what is missing.
- rationale: at most two sentences, referring to concrete features of the work.
- evidence: one to three short quotes or paraphrases from the allowed sources.
- Respond ONLY with JSON matching this schema:
{"dimensions": [{"id": "<dimension id>", "marks": <number or null>, "rationale": "<text>", "evidence": ["<text>"]}]}"""

SOURCE_LABEL = {
    "poster": "POSTER TEXT",
    "poster_image": "POSTER IMAGE (not provided in this request)",
    "presentation_note": "PRESENTATION NOTE",
    "instructor_comment": "INSTRUCTOR COMMENTS",
    "reference_check": "REFERENCE CHECK (not provided in this request)",
}


def build_suggest_messages(a: Assignment, poster_text: str, note: str, comment: str) -> list[dict]:
    step = a.scoring.mark_step
    lines = [f"ASSIGNMENT: {a.assignment.course} — {a.assignment.title}",
             f"Total {a.assignment.max_marks} marks. Mark step: {step}.", "", "RUBRIC DIMENSIONS:"]
    for d in a.dimensions:
        lines.append(f"\n[{d.id}] {d.name} — max {d.max_marks} marks")
        lines.append("  allowed sources: " + ", ".join(SOURCE_LABEL[e] for e in d.evidence))
        if d.guidance:
            lines.append("  guidance: " + " ".join(d.guidance.split()))
        for band, text in d.descriptors.items():
            if text:
                lines.append(f"  band {band}: {text}")
    pt = poster_text.strip()
    if len(pt) > POSTER_TEXT_LIMIT:
        pt = pt[:POSTER_TEXT_LIMIT] + "\n[…truncated]"
    lines += ["", "=== POSTER TEXT ===", pt or "(no extractable text — the poster may be image-only)",
              "", "=== PRESENTATION NOTE (lecturer's live observations) ===", note.strip() or "(none)",
              "", "=== INSTRUCTOR COMMENTS ===", comment.strip() or "(none)", "",
              "Return a suggestion for every dimension id listed above."]
    return [{"role": "system", "content": SYSTEM_SUGGEST},
            {"role": "user", "content": "\n".join(lines)}]


def validate_suggestion(a: Assignment, raw: dict) -> list[dict]:
    """Keep known dimensions; snap marks to the step and clamp to range."""
    step = a.scoring.mark_step
    got = {str(d.get("id")): d for d in raw.get("dimensions", []) if isinstance(d, dict)}
    out = []
    for dim in a.dimensions:
        d = got.get(dim.id, {})
        marks = d.get("marks")
        val: Optional[Decimal] = None
        if isinstance(marks, (int, float)) or (isinstance(marks, str) and re.fullmatch(r"\d+(\.\d+)?", marks)):
            v = Decimal(str(marks))
            v = (v / step).quantize(Decimal(1), rounding=ROUND_HALF_UP) * step
            val = min(max(v, Decimal(0)), dim.max_marks)
        ev = d.get("evidence") or []
        out.append({
            "id": dim.id,
            "marks": format(val.normalize(), "f") if val is not None else None,
            "rationale": str(d.get("rationale") or ("No suggestion returned." if not d else "")),
            "evidence": [str(x) for x in ev][:3] if isinstance(ev, list) else [],
        })
    return out
